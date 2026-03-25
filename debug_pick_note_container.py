import argparse
import re
from typing import Dict, Optional, Tuple, List
import xml.etree.ElementTree as ET

import config
from crawler.adb_device import ADBDevice, ADBError
from crawler.xhs_app import XHSApp


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(bounds: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Parse Android UI bounds like "[15,330][533,1272]".
    """
    if not bounds:
        return None
    m = _BOUNDS_RE.search(bounds.replace(" ", ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def contains_bounds(container: Tuple[int, int, int, int],
                    inner: Tuple[int, int, int, int]) -> bool:
    cx1, cy1, cx2, cy2 = container
    ix1, iy1, ix2, iy2 = inner
    return (cx1 <= ix1 and cy1 <= iy1 and cx2 >= ix2 and cy2 >= iy2)


def build_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    parent_map: Dict[ET.Element, ET.Element] = {}
    # ElementTree doesn't provide parent pointers; build a map by traversal.
    for parent in root.iter():
        for child in list(parent):
            if isinstance(child, ET.Element):
                parent_map[child] = parent
    return parent_map


def find_node_by_bounds(root: ET.Element, bounds_str: str) -> Optional[ET.Element]:
    target = bounds_str.strip().replace(" ", "")
    for node in root.iter("node"):
        b = (node.get("bounds") or "").strip().replace(" ", "")
        if b == target:
            return node
    return None


def find_node_by_content_desc(root: ET.Element, content_desc_substr: str) -> Optional[ET.Element]:
    needle = content_desc_substr.strip()
    if not needle:
        return None
    for node in root.iter("node"):
        cd = (node.get("content-desc") or "").strip()
        if needle in cd:
            return node
    return None


def node_brief(node: ET.Element) -> str:
    return (
        f"class={node.get('class')}, clickable={node.get('clickable')}, "
        f"resource-id={node.get('resource-id')}, bounds={node.get('bounds')}"
    )


def collect_non_empty_texts(node: ET.Element, limit: int = 8) -> List[str]:
    texts: List[str] = []
    for n in node.iter("node"):
        t = (n.get("text") or "").strip()
        if t:
            texts.append(t)
            if len(texts) >= limit:
                break
    return texts


def main():
    parser = argparse.ArgumentParser(
        description="Debug tool: locate the clickable ancestor and the note card container "
                    "that contains a target node (by bounds / content-desc)."
    )
    parser.add_argument("--bounds", default="", help='Target node bounds, e.g. "[15,330][533,1272]"')
    parser.add_argument("--content-desc", default="", help="Substring to match node content-desc")
    parser.add_argument("--xml-file", default="", help="(Optional) Local UI XML file. If provided, skip adb dump_ui().")
    parser.add_argument("--dump-save", default="", help="(Optional) save dumped ui xml to a local file path")
    args = parser.parse_args()

    root: Optional[ET.Element] = None
    device: Optional[ADBDevice] = None
    app: Optional[XHSApp] = None

    # If the user provides local XML, we don't need adb dump at all.
    if args.xml_file:
        xml_path = args.xml_file
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # For candidate filtering we rely on default screen size; no adb calls needed.
        device = ADBDevice(serial=config.DEVICE_SERIAL)
        app = XHSApp(device)
    else:
        device = ADBDevice(serial=config.DEVICE_SERIAL)
        if not device.auto_select_device():
            raise SystemExit("No ADB device connected.")

        app = XHSApp(device)
        app.setup()

    try:
        if root is None:
            root = device.dump_ui()
    except ADBError as e:
        raise SystemExit(f"dump_ui failed: {e}")

    if args.dump_save and root is not None:
        # best-effort save local xml for manual inspection
        tree = ET.ElementTree(root)
        tree.write(args.dump_save, encoding="utf-8", xml_declaration=True)

    target = None
    if args.bounds:
        target = find_node_by_bounds(root, args.bounds)
    if target is None and args.content_desc:
        target = find_node_by_content_desc(root, args.content_desc)

    if target is None:
        raise SystemExit("Target node not found in dumped UI. "
                         "Try adjusting --bounds or --content-desc, and ensure you are on feed list page.")

    print("[1] Target node:")
    print(node_brief(target))

    parent_map = build_parent_map(root)
    clickable = target
    visited = 0
    while clickable is not None and clickable.get("clickable") != "true":
        clickable = parent_map.get(clickable)
        visited += 1
        if visited > 2000:
            break

    if clickable is None:
        print("[2] Clickable ancestor: not found")
    else:
        print("[2] First clickable ancestor (nearest parent chain):")
        print(node_brief(clickable))

    # Compare against candidate note cards used by get_note_items_on_screen()
    if app is None:
        raise SystemExit("Internal error: app is None")

    candidates = app.get_note_items_on_screen(root)
    print(f"[3] Candidate note containers found by get_note_items_on_screen(): {len(candidates)}")

    target_bounds = parse_bounds(target.get("bounds") or "")
    if target_bounds is None:
        print("[4] Target bounds parse failed; can't compute containment.")
        return

    scored: List[Tuple[int, str]] = []
    for idx, c in enumerate(candidates):
        cb = parse_bounds(c.get("bounds") or "")
        if cb is None:
            continue
        if contains_bounds(cb, target_bounds):
            # smaller area => closer match (prefer the tightest container that still contains the target)
            area = (cb[2] - cb[0]) * (cb[3] - cb[1])
            scored.append((area, f"idx={idx}; {node_brief(c)}; texts={collect_non_empty_texts(c)}"))

    if not scored:
        print("[4] No candidate note container contains the target node bounds.")
        print("    This usually means the UI you dumped is not the feed list page, "
              "or the candidate filter thresholds don't match this layout.")
        return

    scored.sort(key=lambda x: x[0])
    best_area, best_info = scored[0]
    print("[4] Best matching note container (tightest contains target bounds):")
    print(best_info)

    if len(scored) > 1:
        print("[5] Other containing candidates (area ascending, top 5):")
        for area, info in scored[:5]:
            if info == best_info:
                continue
            print(f"    area={area} :: {info}")


if __name__ == "__main__":
    main()

