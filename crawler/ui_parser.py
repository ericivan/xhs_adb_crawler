"""
ui_parser.py — 解析 uiautomator dump 出的 XML，
提取小红书笔记详情页与评论区的结构化数据。

小红书 UI 特征（实测）：
  图文笔记：
    - 作者：resource-id = nickNameTV
    - 正文/互动数：resource-id = 0_resource_name_obfuscated（混淆）
  视频笔记：
    - 作者：resource-id = matrixNickNameView
    - 正文：resource-id = noteContentText（未混淆，含内嵌 #tag）
  评论区（图文/视频均适用）：
    - 全部 0_resource_name_obfuscated
    - 固定三元组：作者名 → 评论内容 → "X分钟前 地区 回复"
    - 开始标记：「共 X 条评论」
    - 结束标记：「- 到底了 -」
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
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    r"|(\d+小时前)|(\d+分钟前)|(\d+天前)"
    r"|昨天|前天|刚刚"
)
# 「2分钟前 广东 回复」「47分钟前 中国香港 回复」——评论三元组最后一行
_RE_COMMENT_META = re.compile(
    r"(?:\d+(?:分钟|小时|天|周|月|年)前|刚刚|昨天|前天)"
)
# 「共 X 条评论」—— 评论区开始标记
_RE_CMT_HEADER = re.compile(r"^共\s*\d+\s*条评论")
# 从正文中提取 #话题 标签
_RE_TAG_IN_CONTENT = re.compile(r"#[\w\u4e00-\u9fff·]+")

# XHS 包名前缀
_XHS_RES_PREFIX = "com.xingin.xhs:id/"

# ── 已知 resource-id 关键词 ─────────────────────────────────────────────────
_RES_AUTHOR   = {"name", "nick_name", "nickname", "author_name", "user_name",
                 "nicknametv",          # 图文笔记：com.xingin.xhs:id/nickNameTV
                 "matrixnicknameview"}  # 视频笔记：com.xingin.xhs:id/matrixNickNameView
_RES_TITLE    = {"title", "note_title"}
_RES_CONTENT  = {"desc", "content", "note_content", "body",
                 "notecontenttext"}    # 视频笔记：com.xingin.xhs:id/noteContentText
_RES_LIKE     = {"like_count", "likes", "like_text"}
_RES_COLLECT  = {"collect_count", "bookmark_count", "favorite_count"}
_RES_COMMENT  = {"comment_count", "comments"}
_RES_TIME     = {"time", "publish_time", "create_time"}

# 混淆后所有内容字段共用的 resource-id
_OBFUSCATED_RES = "0_resource_name_obfuscated"

# ── 需要跳过的 UI 噪音文字 ──────────────────────────────────────────────────
_UI_NOISE = {
    # 评论输入 & 按钮
    "说点什么", "写评论", "说点什么...", "留下你的想法吧",
    "回复", "赞", "踩", "首评",
    # 功能按钮
    "问一问", "关注", "发消息", "私信", "举报",
    "更多", "收藏", "分享", "点赞", "评论",
    # 展开/收起
    "查看更多", "展开", "收起", "全文",
    # 底部 Tab
    "笔记", "用户", "话题", "全部", "最新", "最热",
    "发现", "首页", "消息", "我",
    # section 标题（视频/搜索/推荐）
    "相关搜索", "相关笔记", "热门搜索", "更多推荐",
    "猜你喜欢", "为你推荐", "猜你想搜",
    "精选评论", "全部评论", "添加评论",
    # 笔记元数据标签
    "地点",
    # 评论区底部
    "- 到底了 -",
}


def _strip_prefix(res_id: str) -> str:
    """去掉 resource-id 中的包名前缀，只保留 id 名（小写）。"""
    if ":" in res_id:
        return res_id.split(":")[-1].split("/")[-1].lower()
    return res_id.split("/")[-1].lower()


def _text(node: ET.Element) -> str:
    return (node.get("text") or "").strip()


def _res(node: ET.Element) -> str:
    return _strip_prefix(node.get("resource-id") or "")


def _all_texts(root: ET.Element) -> List[Tuple[str, ET.Element]]:
    """返回所有有文本内容的节点 [(text, node), ...]。"""
    return [(t, n) for n in root.iter("node")
            if (t := _text(n))]


def _is_comment_meta(text: str) -> bool:
    """
    判断文本是否是评论三元组最后一行（时间+地区+回复）。
    例：「2分钟前 广东 回复」「47分钟前 中国香港 回复」
    """
    return bool(_RE_COMMENT_META.search(text)) and text.strip().endswith("回复")


# ─────────────────────────────────────────────────────────────────────────────
# 笔记详情页解析
# ─────────────────────────────────────────────────────────────────────────────

def parse_note_detail(root: ET.Element, note: Optional[Note] = None) -> Note:
    """
    从笔记详情页的 UI XML 中提取笔记信息，填充并返回 Note 对象。

    已知 resource-id：
      图文：nickNameTV（作者）
      视频：matrixNickNameView（作者）、noteContentText（正文+#tag）
      其余字段：0_resource_name_obfuscated（混淆，按位置区分）
    """
    if note is None:
        note = Note()

    all_nodes = list(root.iter("node"))

    # ── 1. 精确匹配已知 resource-id ──────────────────────────────────────
    for node in all_nodes:
        rid = _res(node)
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

    # ── 2. 从正文（noteContentText 或已解析 content）中提取内嵌 #tag ─────
    if note.content:
        for tag in _RE_TAG_IN_CONTENT.findall(note.content):
            if tag not in note.topics:
                note.topics.append(tag)

    # ── 3. 处理混淆字段（0_resource_name_obfuscated）────────────────────
    obfuscated_texts = []
    for node in all_nodes:
        raw_rid = (node.get("resource-id") or "")
        if _OBFUSCATED_RES in raw_rid:
            t = _text(node)
            if t and t not in _UI_NOISE and not _is_comment_meta(t):
                obfuscated_texts.append(t)

    # 从混淆文本按顺序拆分：互动数(纯数字) / 时间 / 话题 / 正文
    counts = [t for t in obfuscated_texts if _is_count(t)]
    content_texts = [t for t in obfuscated_texts
                     if not _is_count(t)
                     and not _RE_TIME.search(t)
                     and not _RE_TOPIC.match(t)
                     and not _RE_CMT_HEADER.match(t)]

    if content_texts:
        if not note.title:
            note.title = content_texts[0]
        if not note.content and len(content_texts) > 1:
            note.content = content_texts[1]
        elif not note.content and len(content_texts[0]) > 15:
            note.content = content_texts[0]

    if len(counts) >= 3:
        if not note.like_count:    note.like_count    = counts[0]
        if not note.collect_count: note.collect_count = counts[1]
        if not note.comment_count: note.comment_count = counts[2]

    # ── 4. 话题标签 & 发布时间 ────────────────────────────────────────────
    for t, _ in _all_texts(root):
        if _RE_TOPIC.match(t) and t not in note.topics:
            note.topics.append(t)
    if not note.publish_time:
        for t, _ in _all_texts(root):
            if _RE_TIME.search(t) and not _is_comment_meta(t):
                note.publish_time = t
                break

    # ── 5. 再次从 content 提取 #tag（content 可能由混淆段补充）─────────────
    if note.content:
        for tag in _RE_TAG_IN_CONTENT.findall(note.content):
            if tag not in note.topics:
                note.topics.append(tag)

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

    策略 1：resource-id 精确匹配 comment_item 容器
    策略 2：基于「共X条评论」开始标记 + 三元组启发式
    """
    # ── 策略 1：resource-id 定位 ─────────────────────────────────────────
    containers = _find_comment_containers(root)
    if containers:
        comments = []
        for idx, container in enumerate(containers):
            c = _extract_comment_from_container(container)
            if c.is_valid():
                c.note_id = note_id
                c.comment_index = start_index + idx
                comments.append(c)
        if comments:
            return comments

    # ── 策略 2：启发式三元组 ─────────────────────────────────────────────
    return _heuristic_parse_comments(root, note_id, start_index)


def _find_comment_containers(root: ET.Element) -> List[ET.Element]:
    candidates = []
    for node in root.iter("node"):
        rid = _res(node)
        if any(k in rid for k in ("comment_item", "review_item",
                                  "comment_list_item")):
            candidates.append(node)
    return candidates


def _extract_comment_from_container(container: ET.Element) -> Comment:
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
    if not c.content:
        texts = [(n.get("text", ""), n) for n in container.iter("node")
                 if n.get("text")]
        c = _heuristic_fill_comment(c, texts)
    return c


def _heuristic_parse_comments(root: ET.Element, note_id: str,
                               start_index: int) -> List[Comment]:
    """
    启发式评论解析（适配小红书 0_resource_name_obfuscated 全混淆结构）。

    核心规则：
      1. 找到「共 X 条评论」标记，仅解析其后的节点
      2. 按「作者 → 内容 → 时间+地区+回复」三元组提取
      3. 遇到「- 到底了 -」停止
    """
    text_nodes = _all_texts(root)

    # 按 y 坐标排序
    def _y(node: ET.Element) -> int:
        from crawler.adb_device import ADBDevice
        b = ADBDevice.get_node_bounds(node)
        return (b[1] + b[3]) // 2 if b else 9999

    text_nodes.sort(key=lambda tn: _y(tn[1]))

    # 找到评论区开始位置（「共 X 条评论」之后）
    start_pos = 0
    for idx, (t, _) in enumerate(text_nodes):
        if _RE_CMT_HEADER.match(t):
            start_pos = idx + 1
            break

    comments: List[Comment] = []
    i = start_pos

    while i < len(text_nodes):
        t, _ = text_nodes[i]

        # 结束标记
        if "到底了" in t:
            break

        # 跳过所有噪音、互动数、话题、评论元信息行
        if (t in _UI_NOISE
                or _is_count(t)
                or _RE_TOPIC.match(t)
                or _is_comment_meta(t)
                or _RE_CMT_HEADER.match(t)):
            i += 1
            continue

        # 当前节点视为「作者名」
        author = t
        content = ""
        pub_time = ""

        j = i + 1

        # 找评论内容（跳过噪音，遇到元信息行则此条评论无正文）
        while j < len(text_nodes):
            nxt, _ = text_nodes[j]
            if "到底了" in nxt:
                break
            if (nxt in _UI_NOISE or _is_count(nxt)
                    or _RE_TOPIC.match(nxt) or _RE_CMT_HEADER.match(nxt)):
                j += 1
                continue
            if _is_comment_meta(nxt):
                # 时间行直接跟在作者后（无正文），结束本条
                pub_time = nxt
                j += 1
                break
            # 第一条非噪音非元信息文本 = 评论内容
            content = nxt
            j += 1
            break

        # 找时间元信息行（紧跟在内容后）
        if content:
            while j < len(text_nodes):
                nxt, _ = text_nodes[j]
                if "到底了" in nxt:
                    break
                if _is_comment_meta(nxt):
                    pub_time = nxt
                    j += 1
                    break
                if _is_count(nxt) or nxt in _UI_NOISE:
                    j += 1
                    break
                # 不是元信息 → 下一条评论开始，停止
                break

            comments.append(Comment(
                note_id=note_id,
                comment_index=start_index + len(comments),
                author_name=author,
                content=content,
                publish_time=pub_time,
            ))
            i = j
        else:
            # 没有找到内容（可能是作者名误识别），向前推进
            i = j if j > i + 1 else i + 1

    return comments


def _heuristic_fill_comment(c: Comment,
                             texts: List[Tuple[str, ET.Element]]) -> Comment:
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
