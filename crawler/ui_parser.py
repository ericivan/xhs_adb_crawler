"""
ui_parser.py — 解析 uiautomator dump 出的 XML，
提取小红书笔记详情页与评论区的结构化数据。

小红书 UI 特征（可能随版本变化）：
  - 正文 TextView：resource-id 含 "desc" / "content" / 包名前缀
  - 作者昵称：resource-id 含 "name" / "nick"
  - 互动数字：紧邻 ImageView（点赞/收藏/评论图标）的 TextView
  - 话题标签：文本以 "#" 开头
  - 评论列表：RecyclerView 下的每个 FrameLayout/LinearLayout
"""

import re
import logging
import xml.etree.ElementTree as ET
from typing import Optional, List, Tuple

from models.note import Note
from models.comment import Comment, SubComment

logger = logging.getLogger(__name__)

# ── 关键词正则 ─────────────────────────────────────────────────────────────
_RE_NUMBER = re.compile(r"^\d+(\.\d+)?[万千百]?$")
_RE_TOPIC  = re.compile(r"^#.+")
_RE_TIME   = re.compile(
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})|(\d+小时前)|(\d+分钟前)|昨天|前天|刚刚"
)

# XHS 包名前缀（resource-id 去掉 "com.xingin.xhs:" 前缀后的 id 名）
_XHS_RES_PREFIX = "com.xingin.xhs:id/"

# ── 已知的 resource-id 关键词（根据逆向/测试积累，可按需扩充）──────────────
_RES_AUTHOR   = {"name", "nick_name", "nickname", "author_name", "user_name",
                 "nicknametv"}          # 实测：com.xingin.xhs:id/nickNameTV
_RES_TITLE    = {"title", "note_title"}
_RES_CONTENT  = {"desc", "content", "note_content", "body"}
_RES_LIKE     = {"like_count", "likes", "like_text"}
_RES_COLLECT  = {"collect_count", "bookmark_count", "favorite_count"}
_RES_COMMENT  = {"comment_count", "comments"}
_RES_TIME     = {"time", "publish_time", "create_time"}

# 小红书混淆后所有内容字段共用的 resource-id
_OBFUSCATED_RES = "0_resource_name_obfuscated"
# 评论输入框占位文字（需跳过）
_COMMENT_PLACEHOLDER = {"说点什么", "写评论", "说点什么..."}


def _strip_prefix(res_id: str) -> str:
    """去掉 resource-id 中的包名前缀，只保留 id 名。"""
    if ":" in res_id:
        return res_id.split(":")[-1].split("/")[-1]
    return res_id.split("/")[-1]


def _text(node: ET.Element) -> str:
    return (node.get("text") or "").strip()


def _res(node: ET.Element) -> str:
    return _strip_prefix(node.get("resource-id") or "")


def _all_texts(root: ET.Element) -> List[Tuple[str, ET.Element]]:
    """返回所有有文本内容的节点 [(text, node), ...]。"""
    results = []
    for n in root.iter("node"):
        t = _text(n)
        if t:
            results.append((t, n))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 笔记详情页解析
# ─────────────────────────────────────────────────────────────────────────────

def parse_note_detail(root: ET.Element, note: Optional[Note] = None) -> Note:
    """
    从笔记详情页的 UI XML 中提取笔记信息，填充并返回 Note 对象。

    小红书现版本特征：
      - 作者：resource-id = nickNameTV
      - 标题/正文/互动数：resource-id 全被混淆为 0_resource_name_obfuscated
        → 按出现顺序 + 内容长度区分
    """
    if note is None:
        note = Note()

    all_nodes = list(root.iter("node"))

    # ── 1. 精确匹配已知 resource-id ──────────────────────────────────────
    for node in all_nodes:
        rid = _res(node).lower()
        t   = _text(node)
        if not t:
            continue
        if rid in _RES_AUTHOR and not note.author_name:
            note.author_name = t
        elif rid in _RES_TITLE and not note.title:
            note.title = t
        elif rid in _RES_CONTENT and not note.content:
            note.content = t
        elif rid in _RES_LIKE and not note.like_count:
            note.like_count = t
        elif rid in _RES_COLLECT and not note.collect_count:
            note.collect_count = t
        elif rid in _RES_COMMENT and not note.comment_count:
            note.comment_count = t
        elif rid in _RES_TIME and not note.publish_time:
            note.publish_time = t

    # ── 2. 处理混淆字段（0_resource_name_obfuscated）────────────────────
    obfuscated_texts = []
    for node in all_nodes:
        raw_rid = (node.get("resource-id") or "")
        if _OBFUSCATED_RES in raw_rid:
            t = _text(node)
            if t and t not in _COMMENT_PLACEHOLDER:
                obfuscated_texts.append(t)

    # 从混淆文本中按顺序拆分：标题(短) → 正文(长) → 互动数(纯数字)
    counts = [t for t in obfuscated_texts if _is_count(t)]
    content_texts = [t for t in obfuscated_texts
                     if not _is_count(t) and not _RE_TIME.search(t)
                     and not _RE_TOPIC.match(t)]

    if content_texts:
        if not note.title:
            # 第一条非数字文本通常是标题（相对较短）
            note.title = content_texts[0]
        if not note.content and len(content_texts) > 1:
            # 第二条通常是正文（更长）
            note.content = content_texts[1]
        elif not note.content:
            # 只有一条时，若超过 15 字则当正文，否则当标题
            if len(content_texts[0]) > 15 and not note.content:
                note.content = content_texts[0]

    if len(counts) >= 3:
        if not note.like_count:    note.like_count    = counts[0]
        if not note.collect_count: note.collect_count = counts[1]
        if not note.comment_count: note.comment_count = counts[2]

    # ── 3. 话题 & 标签、发布时间 ─────────────────────────────────────────
    text_nodes = _all_texts(root)
    for t, _ in text_nodes:
        if _RE_TOPIC.match(t) and t not in note.topics:
            note.topics.append(t)
    if not note.publish_time:
        for t, _ in text_nodes:
            if _RE_TIME.search(t):
                note.publish_time = t
                break

    return note


def _is_count(text: str) -> bool:
    """判断文本是否是互动数字（如 '123'、'1.2万'、'999+'）。"""
    cleaned = text.replace("+", "").strip()
    return bool(_RE_NUMBER.match(cleaned)) or cleaned == "0"


# ─────────────────────────────────────────────────────────────────────────────
# 评论区解析
# ─────────────────────────────────────────────────────────────────────────────

def parse_comments(root: ET.Element, note_id: str = "",
                   start_index: int = 0) -> List[Comment]:
    """
    从当前界面的 UI XML 中解析评论列表，返回 Comment 对象列表。

    策略：
      1. 找到所有 resource-id 含 "comment" 的容器节点
      2. 若找不到，退化为启发式：按行分析 TextView 序列
    """
    comments: List[Comment] = []

    # ── 策略 1：按 resource-id 定位评论条目容器 ───────────────────────────
    comment_containers = _find_comment_containers(root)
    if comment_containers:
        for idx, container in enumerate(comment_containers):
            c = _extract_comment_from_container(container)
            if c.is_valid():
                c.note_id = note_id
                c.comment_index = start_index + idx
                comments.append(c)
        if comments:
            return comments

    # ── 策略 2：启发式行分析 ─────────────────────────────────────────────
    comments = _heuristic_parse_comments(root, note_id, start_index)
    return comments


def _find_comment_containers(root: ET.Element) -> List[ET.Element]:
    """寻找评论条目的容器节点。"""
    candidates = []
    for node in root.iter("node"):
        rid = _res(node)
        cls = node.get("class", "")
        # XHS 评论 item 常见 resource-id 含 "comment_item" / "review_item"
        if any(k in rid for k in ("comment_item", "review_item", "comment_list_item")):
            candidates.append(node)
        # 退而求其次：RecyclerView 的直接子 FrameLayout
        # (在无 resource-id 时通过父节点为 RecyclerView 来识别)
    return candidates


def _extract_comment_from_container(container: ET.Element) -> Comment:
    """从单个评论容器节点中提取字段。"""
    c = Comment()
    for node in container.iter("node"):
        rid = _res(node)
        t   = _text(node)
        if not t:
            continue
        if rid in _RES_AUTHOR and not c.author_name:
            c.author_name = t
        elif rid in ("comment_content", "content", "desc") and not c.content:
            c.content = t
        elif rid in _RES_LIKE and not c.like_count:
            c.like_count = t
        elif rid in _RES_TIME and not c.publish_time:
            c.publish_time = t

    # 如果 resource-id 匹配失败，用启发式
    if not c.content:
        texts = [(n.get("text", ""), n) for n in container.iter("node")
                 if n.get("text")]
        c = _heuristic_fill_comment(c, texts)

    return c


def _heuristic_parse_comments(root: ET.Element, note_id: str,
                               start_index: int) -> List[Comment]:
    """
    启发式：
    - 收集所有文本节点，按纵坐标排序
    - 根据「昵称→内容→时间/点赞」的典型排列识别评论块
    """
    text_nodes = _all_texts(root)
    # 按 y 坐标排序
    def y_center(node: ET.Element) -> int:
        from crawler.adb_device import ADBDevice
        b = ADBDevice.get_node_bounds(node)
        return (b[1] + b[3]) // 2 if b else 9999

    text_nodes.sort(key=lambda tn: y_center(tn[1]))

    comments: List[Comment] = []
    i = 0
    while i < len(text_nodes):
        t, n = text_nodes[i]
        # 跳过互动数字、话题、时间等非评论主体
        if _is_count(t) or _RE_TOPIC.match(t):
            i += 1
            continue
        if _RE_TIME.search(t):
            i += 1
            continue

        # 把当前文本当作昵称
        author = t
        content = ""
        like    = ""
        pub_time = ""

        j = i + 1
        # 向后搜索内容（取第一条非数字、非时间的文本）
        while j < len(text_nodes) and not content:
            nxt, _ = text_nodes[j]
            if _is_count(nxt) or _RE_TIME.search(nxt) or _RE_TOPIC.match(nxt):
                j += 1
                break
            content = nxt
            j += 1

        # 继续搜索时间 & 点赞
        while j < len(text_nodes):
            nxt, _ = text_nodes[j]
            if _RE_TIME.search(nxt) and not pub_time:
                pub_time = nxt
                j += 1
            elif _is_count(nxt) and not like:
                like = nxt
                j += 1
            else:
                break

        if content:
            c = Comment(
                note_id=note_id,
                comment_index=start_index + len(comments),
                author_name=author,
                content=content,
                like_count=like,
                publish_time=pub_time,
            )
            comments.append(c)
            i = j
        else:
            i += 1

    return comments


def _heuristic_fill_comment(c: Comment,
                             texts: List[Tuple[str, ET.Element]]) -> Comment:
    """用文本列表启发式填充 Comment 字段。"""
    non_meta = [(t, n) for t, n in texts
                if not _is_count(t) and not _RE_TIME.search(t)
                and not _RE_TOPIC.match(t)]
    if non_meta and not c.author_name:
        c.author_name = non_meta[0][0]
    if len(non_meta) > 1 and not c.content:
        c.content = non_meta[1][0]

    for t, _ in texts:
        if _is_count(t) and not c.like_count:
            c.like_count = t
        if _RE_TIME.search(t) and not c.publish_time:
            c.publish_time = t
    return c
