"""
adb_device.py — 对 adb 命令的轻量封装，提供：
  - 设备连接与状态检测
  - 截图 / UI 层次树导出
  - 点击、滑动、文本输入等基础操作
  - App 启动 / 停止 / 深链接跳转
"""

import os
import platform
import re
import subprocess
import tempfile
import time
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)


class ADBError(Exception):
    pass


class ADBDevice:
    """封装 adb 操作的核心类。"""

    def __init__(self, serial: Optional[str] = None, adb_path: str = config.ADB_PATH):
        self.adb_path = adb_path
        self.serial = serial or config.DEVICE_SERIAL
        self._screen_size: Optional[Tuple[int, int]] = None

    # ──────────────────────────────────────────────────────────────────────
    # 内部命令执行
    # ──────────────────────────────────────────────────────────────────────

    def _build_cmd(self, *args: str) -> list:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        return cmd

    def run(self, *args: str, timeout: int = 30) -> str:
        """执行 adb 命令并返回 stdout 字符串。"""
        cmd = self._build_cmd(*args)
        logger.debug("ADB: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode != 0:
            raise ADBError(f"adb error (rc={result.returncode}): {stderr or stdout}")
        return stdout

    def shell(self, command: str, timeout: int = 30) -> str:
        """执行 adb shell 命令。"""
        return self.run("shell", command, timeout=timeout)

    # ──────────────────────────────────────────────────────────────────────
    # 设备连接
    # ──────────────────────────────────────────────────────────────────────

    def connect(self, host_port: str) -> bool:
        """通过 TCP/IP 连接设备（无线 ADB）。"""
        output = self.run("connect", host_port)
        return "connected" in output.lower()

    def check_connected(self) -> bool:
        """检查是否有设备在线。"""
        try:
            output = self.run("devices")
            lines = [l for l in output.splitlines() if "\tdevice" in l]
            return len(lines) > 0
        except ADBError:
            return False

    def list_devices(self) -> list:
        """返回所有已连接设备的 serial 列表。"""
        output = self.run("devices")
        devices = []
        for line in output.splitlines():
            if "\tdevice" in line:
                devices.append(line.split("\t")[0].strip())
        return devices

    def auto_select_device(self) -> bool:
        """若未指定 serial，自动选取第一台在线设备。"""
        if self.serial:
            return True
        devices = self.list_devices()
        if not devices:
            return False
        self.serial = devices[0]
        logger.info("自动选择设备: %s", self.serial)
        return True

    # ──────────────────────────────────────────────────────────────────────
    # 屏幕信息
    # ──────────────────────────────────────────────────────────────────────

    def get_screen_size(self) -> Tuple[int, int]:
        """返回 (width, height)。"""
        if self._screen_size:
            return self._screen_size
        output = self.shell("wm size")
        m = re.search(r"(\d+)x(\d+)", output)
        if not m:
            raise ADBError(f"无法解析屏幕尺寸: {output}")
        self._screen_size = (int(m.group(1)), int(m.group(2)))
        return self._screen_size

    def get_screen_center(self) -> Tuple[int, int]:
        w, h = self.get_screen_size()
        return w // 2, h // 2

    # ──────────────────────────────────────────────────────────────────────
    # 截图 & UI Dump
    # ──────────────────────────────────────────────────────────────────────

    def screenshot(self, local_path: Optional[str] = None) -> str:
        """
        截图并拉取到本机。
        返回本机临时文件路径。
        """
        self.shell(f"screencap -p {config.DEVICE_SCREENSHOT_PATH}")
        if local_path is None:
            fd, local_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
        self.run("pull", config.DEVICE_SCREENSHOT_PATH, local_path)
        return local_path

    def dump_ui(self, retries: int = 3, retry_delay: float = 1.5) -> ET.Element:
        """
        导出当前界面 UI XML，返回解析后的根 Element。
        uiautomator dump 在 App 繁忙时可能被 kill（rc=137），自动重试。
        """
        last_err: Exception = ADBError("dump_ui 未执行")
        for attempt in range(retries):
            if attempt > 0:
                logger.debug("dump_ui 重试 %d/%d（%.1fs 后）", attempt, retries - 1, retry_delay)
                time.sleep(retry_delay)
            try:
                self.shell(f"uiautomator dump {config.DEVICE_UI_DUMP_PATH}",
                           timeout=20)
            except ADBError as e:
                last_err = e
                logger.warning("uiautomator dump 失败（attempt %d）: %s", attempt + 1, e)
                continue

            time.sleep(0.3)
            fd, local_path = tempfile.mkstemp(suffix=".xml")
            os.close(fd)
            try:
                self.run("pull", config.DEVICE_UI_DUMP_PATH, local_path)
                tree = ET.parse(local_path)
                root = tree.getroot()
                # 空 XML（无子节点）视为无效，继续重试
                if len(list(root.iter("node"))) == 0:
                    logger.warning("dump_ui 返回空 XML，重试")
                    last_err = ADBError("UI XML 为空")
                    continue
                return root
            except (ET.ParseError, ADBError) as e:
                last_err = e
                logger.warning("dump_ui 解析失败（attempt %d）: %s", attempt + 1, e)
            finally:
                try:
                    os.remove(local_path)
                except OSError:
                    pass

        raise ADBError(f"dump_ui 连续失败 {retries} 次: {last_err}")

    # ──────────────────────────────────────────────────────────────────────
    # 触控操作
    # ──────────────────────────────────────────────────────────────────────

    def tap(self, x: int, y: int):
        """点击坐标。"""
        self.shell(f"input tap {x} {y}")
        time.sleep(config.WAIT_SHORT)

    def long_press(self, x: int, y: int, duration_ms: int = 800):
        """长按坐标。"""
        self.shell(f"input swipe {x} {y} {x} {y} {duration_ms}")
        time.sleep(config.WAIT_SHORT)

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = config.SCROLL_DURATION_MS):
        """滑动。"""
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
        time.sleep(config.WAIT_SHORT)

    def scroll_down(self, step: int = config.SCROLL_STEP_PX):
        """向下滚动（手机屏幕从下往上划）。"""
        w, h = self.get_screen_size()
        cx = w // 2
        y1 = int(h * 0.7)
        y2 = max(y1 - step, 50)
        self.swipe(cx, y1, cx, y2)

    def scroll_up(self, step: int = config.SCROLL_STEP_PX):
        """向上滚动（手机屏幕从上往下划）。"""
        w, h = self.get_screen_size()
        cx = w // 2
        y1 = int(h * 0.3)
        y2 = min(y1 + step, h - 50)
        self.swipe(cx, y1, cx, y2)

    # ──────────────────────────────────────────────────────────────────────
    # 输入法管理
    # ──────────────────────────────────────────────────────────────────────

    _ADB_IME = "com.android.adbkeyboard/.AdbIME"

    def get_current_ime(self) -> str:
        """返回当前默认输入法的 IME ID。"""
        return self.shell("settings get secure default_input_method").strip()

    def set_ime(self, ime_id: str):
        """切换默认输入法。"""
        self.shell(f"ime enable {ime_id}")
        self.shell(f"ime set {ime_id}")
        time.sleep(0.5)
        logger.info("输入法已切换: %s", ime_id)

    def switch_to_adb_keyboard(self) -> str:
        """
        切换到 ADBKeyboard，返回切换前的输入法 ID（用于事后恢复）。
        若 ADBKeyboard 未安装则跳过，返回空字符串。
        """
        prev = self.get_current_ime()
        if prev == self._ADB_IME:
            return prev  # 已经是 ADBKeyboard，无需切换
        try:
            self.set_ime(self._ADB_IME)
            logger.info("已切换到 ADBKeyboard（原输入法: %s）", prev)
            return prev
        except ADBError as e:
            logger.warning("切换 ADBKeyboard 失败（未安装？）: %s", e)
            return ""

    def restore_ime(self, ime_id: str):
        """恢复到指定输入法（一般传 switch_to_adb_keyboard 的返回值）。"""
        if ime_id and ime_id != self._ADB_IME:
            self.set_ime(ime_id)
            logger.info("已恢复输入法: %s", ime_id)

    # ──────────────────────────────────────────────────────────────────────
    # 键盘 & 文本
    # ──────────────────────────────────────────────────────────────────────

    def input_text(self, text: str):
        """
        输入文本，优先级：
          1. ADBKeyboard broadcast（手机端需安装 ADBKeyboard）
          2. 系统剪贴板 + KEYCODE_PASTE（跨平台，支持中文，无需额外 App）
          3. adb input text 兜底（仅 ASCII 可靠）
        """
        # ── 方式 1：ADBKeyboard（最佳，支持中文）─────────────────────────
        escaped = text.replace("'", "\\'")
        try:
            out = self.shell(f"am broadcast -a ADB_INPUT_TEXT --es msg '{escaped}'")
            # ADBKeyboard 成功响应时 result 不为 -1
            if "result=-1" not in out:
                time.sleep(0.5)
                return
        except ADBError:
            pass

        # ── 方式 2：系统剪贴板粘贴（Windows/macOS/Linux 均支持中文）─────
        try:
            self._input_via_clipboard(text)
            return
        except Exception as e:
            logger.debug("剪贴板输入失败: %s，降级到 input text", e)

        # ── 方式 3：adb input text 兜底（中文可能乱码）──────────────────
        safe = text.replace(" ", "%s").replace("'", "")
        self.shell(f"input text '{safe}'")
        time.sleep(0.5)

    def _input_via_clipboard(self, text: str):
        """
        将文本写入本机剪贴板，再让手机执行粘贴动作。
        支持 Windows / macOS / Linux。
        """
        system = platform.system()
        if system == "Windows":
            # clip 命令接受 UTF-16 LE stdin
            subprocess.run(
                "clip", input=text.encode("utf-16-le"),
                shell=True, check=True
            )
        elif system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        else:
            # Linux：优先 xclip，其次 xsel
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"), check=True
                )
            except FileNotFoundError:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode("utf-8"), check=True
                )
        time.sleep(0.3)
        # 手机执行粘贴
        self.shell("input keyevent KEYCODE_PASTE")
        time.sleep(0.5)

    def clear_text(self, length: int = 50):
        """全选并删除输入框内容。"""
        self.key_event("KEYCODE_CTRL_A")
        self.key_event("KEYCODE_DEL")
        time.sleep(0.3)

    def key_event(self, key: str):
        """发送按键事件，key 可以是字符串（如 'KEYCODE_BACK'）或数字。"""
        self.shell(f"input keyevent {key}")
        time.sleep(0.3)

    def back(self):
        self.key_event("KEYCODE_BACK")

    def home(self):
        self.key_event("KEYCODE_HOME")

    # ──────────────────────────────────────────────────────────────────────
    # App 控制
    # ──────────────────────────────────────────────────────────────────────

    def launch_app(self, package: str, activity: str = ""):
        """启动 App。"""
        if activity:
            self.shell(f"am start -n {activity}")
        else:
            self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(config.WAIT_LONG)

    def stop_app(self, package: str):
        self.shell(f"am force-stop {package}")
        time.sleep(0.5)

    def open_deeplink(self, url: str):
        """通过 am start 打开深链接。"""
        self.shell(f'am start -a android.intent.action.VIEW -d "{url}"')
        time.sleep(config.WAIT_LONG)

    def is_app_foreground(self, package: str) -> bool:
        """判断指定 App 是否在前台。"""
        output = self.shell("dumpsys activity activities | grep mResumedActivity")
        return package in output

    # ──────────────────────────────────────────────────────────────────────
    # UI 元素查找（基于 dump_ui）
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def find_elements_by_text(root: ET.Element, text: str,
                               exact: bool = False) -> list:
        results = []
        for node in root.iter("node"):
            node_text = node.get("text", "")
            if exact:
                if node_text == text:
                    results.append(node)
            else:
                if text.lower() in node_text.lower():
                    results.append(node)
        return results

    @staticmethod
    def find_elements_by_resource_id(root: ET.Element, res_id: str) -> list:
        return [n for n in root.iter("node") if n.get("resource-id", "") == res_id]

    @staticmethod
    def find_elements_by_class(root: ET.Element, class_name: str) -> list:
        return [n for n in root.iter("node")
                if class_name in n.get("class", "")]

    @staticmethod
    def get_node_bounds(node: ET.Element) -> Optional[Tuple[int, int, int, int]]:
        """
        解析 bounds="[x1,y1][x2,y2]" 返回 (x1, y1, x2, y2)。
        """
        bounds_str = node.get("bounds", "")
        m = re.findall(r"\d+", bounds_str)
        if len(m) == 4:
            return int(m[0]), int(m[1]), int(m[2]), int(m[3])
        return None

    @staticmethod
    def get_node_center(node: ET.Element) -> Optional[Tuple[int, int]]:
        bounds = ADBDevice.get_node_bounds(node)
        if bounds:
            return (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
        return None

    def tap_node(self, node: ET.Element):
        """点击 UI 节点中心。"""
        center = self.get_node_center(node)
        if center:
            self.tap(*center)
        else:
            raise ADBError("无法获取节点坐标")

    def tap_by_text(self, root: ET.Element, text: str, exact: bool = True) -> bool:
        """在 UI 树中找到文本节点并点击，成功返回 True。"""
        nodes = self.find_elements_by_text(root, text, exact=exact)
        if not nodes:
            return False
        self.tap_node(nodes[0])
        return True
