"""
xhs_app.py — 小红书 App 高层操作封装：
  - 启动 / 确保在前台
  - 搜索关键词
  - 浏览首页信息流
  - 打开笔记详情
  - 返回上一页
  - 等待界面加载
"""

import time
import logging
import re
from typing import List, Optional, Tuple
import xml.etree.ElementTree as ET

import config
from crawler.adb_device import ADBDevice, ADBError

logger = logging.getLogger(__name__)

# ── 已知 resource-id 片段（跨版本兼容匹配）──────────────────────────────
_RES_SEARCH_BAR   = ("search_bar", "search_input", "searchbar", "search_edit",
                     "search_text", "et_search")
_RES_SEARCH_BTN   = ("search_btn", "search_button", "iv_search", "btn_search",
                     "search_icon")
_RES_NOTE_ITEM    = ("note_item", "feed_item", "card_item", "explore_item",
                     "note_card")
_RES_COMMENT_AREA = ("comment", "review", "comment_list", "rv_comment")


def _res_contains(node: ET.Element, *keywords) -> bool:
    rid = (node.get("resource-id") or "").lower()
    return any(k in rid for k in keywords)


def _text(node: ET.Element) -> str:
    return (node.get("text") or "").strip()


class XHSApp:
    """小红书 App 自动化操作封装。"""

    def __init__(self, device: ADBDevice):
        self.device = device
        self._screen_w, self._screen_h = 1080, 1920  # 默认值，连接后更新
        self._prev_ime: str = ""                      # 启动前的输入法，退出时恢复

    # ──────────────────────────────────────────────────────────────────────
    # 初始化 & 启动
    # ──────────────────────────────────────────────────────────────────────

    def setup(self):
        """初始化设备信息（需在 device.auto_select_device() 之后调用）。"""
        try:
            self._screen_w, self._screen_h = self.device.get_screen_size()
        except ADBError as e:
            logger.warning("获取屏幕尺寸失败: %s，使用默认值", e)

    def launch(self, force_restart: bool = False):
        """启动小红书，并自动切换到 ADBKeyboard。"""
        # 切换输入法（记录原输入法，退出时可恢复）
        self._prev_ime = self.device.switch_to_adb_keyboard()

        if force_restart:
            self.device.stop_app(config.XHS_PACKAGE)
            time.sleep(1)
        if not self.device.is_app_foreground(config.XHS_PACKAGE):
            logger.info("启动小红书 App ...")
            self.device.launch_app(config.XHS_PACKAGE, config.XHS_MAIN_ACTIVITY)
            self._dismiss_splash()
        else:
            logger.info("小红书已在前台")

    def quit(self):
        """退出爬虫，恢复原输入法。"""
        self.device.restore_ime(self._prev_ime)

    def _dismiss_splash(self):
        """尝试关闭启动广告 / 权限弹窗。"""
        time.sleep(config.WAIT_LONG)
        for _ in range(3):
            root = self.device.dump_ui()
            # 常见按钮文本
            for btn_text in ("跳过", "我知道了", "同意", "允许", "知道了", "×"):
                if self.device.tap_by_text(root, btn_text, exact=True):
                    logger.debug("关闭弹窗: %s", btn_text)
                    time.sleep(0.8)
                    break
            else:
                break  # 没有弹窗，退出循环

    # ──────────────────────────────────────────────────────────────────────
    # 搜索
    # ──────────────────────────────────────────────────────────────────────

    def search(self, keyword: str) -> bool:
        """
        在小红书中搜索关键词，进入搜索结果页。
        返回 True 表示成功进入搜索结果。
        """
        logger.info("搜索关键词: %s", keyword)
        root = self.device.dump_ui()

        # ── 1. 尝试点击搜索入口 ──────────────────────────────────────────
        if not self._tap_search_entry(root):
            logger.warning("未找到搜索入口，尝试点击顶部搜索区域")
            # fallback：点击屏幕顶部中间区域
            self.device.tap(self._screen_w // 2, int(self._screen_h * 0.06))
            time.sleep(config.WAIT_MEDIUM)

        # ── 2. 输入关键词 ────────────────────────────────────────────────
        root = self.device.dump_ui()
        search_box = self._find_search_input(root)
        if search_box is not None:
            self.device.tap_node(search_box)
            time.sleep(0.5)

        # 清空已有内容
        self.device.clear_text()
        self.device.input_text(keyword)
        time.sleep(0.5)

        # ── 3. 点击搜索/确认 ─────────────────────────────────────────────
        self.device.key_event("KEYCODE_ENTER")
        time.sleep(config.WAIT_MEDIUM)

        # ── 4. 确认进入搜索结果页 ────────────────────────────────────────
        root = self.device.dump_ui()
        return self._is_search_result_page(root)

    def _tap_search_entry(self, root: ET.Element) -> bool:
        """点击首页搜索入口（放大镜图标或搜索框）。"""
        for node in root.iter("node"):
            if _res_contains(node, *_RES_SEARCH_BTN):
                self.device.tap_node(node)
                time.sleep(config.WAIT_MEDIUM)
                return True
        # 通过描述 / 文本匹配
        for node in root.iter("node"):
            desc = (node.get("content-desc") or "").lower()
            if "搜索" in desc or "search" in desc:
                self.device.tap_node(node)
                time.sleep(config.WAIT_MEDIUM)
                return True
        return False

    def _find_search_input(self, root: ET.Element) -> Optional[ET.Element]:
        """找到搜索输入框节点。"""
        for node in root.iter("node"):
            cls = node.get("class", "")
            if "EditText" in cls:
                return node
            if _res_contains(node, *_RES_SEARCH_BAR):
                return node
        return None

    def _is_search_result_page(self, root: ET.Element) -> bool:
        """判断当前是否是搜索结果页。"""
        texts = [n.get("text", "") for n in root.iter("node")]
        keywords = ("笔记", "用户", "话题", "全部", "最新", "最热")
        return any(k in texts for k in keywords)

    # ──────────────────────────────────────────────────────────────────────
    # 浏览搜索结果 / 首页信息流
    # ──────────────────────────────────────────────────────────────────────

    # 点击笔记卡片时需要跳过的文字（这些是 UI 功能按钮，不是笔记）
    _NOTE_ITEM_NOISE = {
        "问一问", "关注", "发消息", "私信", "举报",
        "更多", "分享", "收藏", "点赞",
    }

    def get_note_items_on_screen(self, root: ET.Element) -> List[ET.Element]:
        """
        获取当前屏幕上所有笔记卡片/条目节点。
        返回可点击的容器节点列表。

        过滤规则：
          1. 节点自身文本不能是已知 UI 按钮
          2. 节点尺寸要足够大（笔记卡片至少占屏幕宽度 1/3）
        """
        min_w = self._screen_w // 3   # 笔记卡片最小宽度
        min_h = 80                    # 笔记卡片最小高度(px)

        items = []

        # ── 策略 1：resource-id 匹配 ───────────────────────────────────
        for node in root.iter("node"):
            if _res_contains(node, *_RES_NOTE_ITEM):
                if node.get("clickable") == "true":
                    if self._is_valid_note_card(node, min_w, min_h):
                        items.append(node)
        if items:
            return items

        # ── 策略 2：可点击容器 + 含图片文字 + 尺寸过滤 ────────────────
        for node in root.iter("node"):
            cls = node.get("class", "")
            if node.get("clickable") != "true":
                continue
            if not ("FrameLayout" in cls or "LinearLayout" in cls
                    or "CardView" in cls or "RelativeLayout" in cls):
                continue
            # 自身文本 OR 任意子节点文本是已知按钮则跳过
            if self._subtree_has_noise(node, self._NOTE_ITEM_NOISE):
                continue
            # 尺寸过滤
            if not self._is_valid_note_card(node, min_w, min_h):
                continue
            # 子节点里有 ImageView 且有 TextView
            all_cls = [c.get("class", "") for c in node.iter()]
            has_img  = any("ImageView" in c for c in all_cls)
            has_text = any("TextView"  in c for c in all_cls)
            if has_img and has_text:
                items.append(node)

        return items

    @staticmethod
    def _subtree_has_noise(node: ET.Element, noise_set: set) -> bool:
        """检查节点及所有后代节点中是否有噪音文本（问一问 等 UI 按钮）。"""
        for child in node.iter():
            t = (child.get("text") or "").strip()
            if t in noise_set:
                return True
        return False

    @staticmethod
    def _is_valid_note_card(node: ET.Element,
                             min_w: int, min_h: int) -> bool:
        """通过 bounds 判断节点是否足够大（排除小按钮）。"""
        from crawler.adb_device import ADBDevice
        bounds = ADBDevice.get_node_bounds(node)
        if not bounds:
            return False
        x1, y1, x2, y2 = bounds
        return (x2 - x1) >= min_w and (y2 - y1) >= min_h

    def get_note_titles_on_screen(self, root: ET.Element) -> List[str]:
        """提取当前屏幕所有笔记标题文本（用于去重）。"""
        titles = []
        for node in root.iter("node"):
            rid = (node.get("resource-id") or "").lower()
            if any(k in rid for k in ("title", "note_title")):
                t = _text(node)
                if t:
                    titles.append(t)
        return titles

    def open_note_by_index(self, index: int) -> bool:
        """
        点击搜索结果/信息流中第 index 条笔记（从 0 开始）。
        返回是否成功进入详情页。
        """
        root = self.device.dump_ui()
        items = self.get_note_items_on_screen(root)
        if index >= len(items):
            logger.warning("当前屏幕仅 %d 条笔记，无法打开第 %d 条", len(items), index)
            return False
        self.device.tap_node(items[index])
        time.sleep(config.WAIT_LONG)
        return self._is_note_detail_page(self.device.dump_ui())

    def open_note_by_deeplink(self, note_id: str) -> bool:
        """通过深链接直接打开笔记详情页。"""
        url = config.XHS_NOTE_DEEPLINK.format(note_id=note_id)
        logger.info("深链接打开笔记: %s", url)
        self.device.open_deeplink(url)
        return self._is_note_detail_page(self.device.dump_ui())

    def _is_note_detail_page(self, root: ET.Element) -> bool:
        """判断当前是否在笔记详情页。"""
        for node in root.iter("node"):
            rid = (node.get("resource-id") or "").lower()
            if any(k in rid for k in ("desc", "content", "note_desc",
                                       "comment_input", "comment_bar")):
                return True
        return False

    # ──────────────────────────────────────────────────────────────────────
    # 笔记详情页操作
    # ──────────────────────────────────────────────────────────────────────

    def scroll_to_comments(self) -> bool:
        """
        在笔记详情页向下滚动，直到进入评论区（出现"评论"相关文字）。
        """
        for _ in range(5):
            root = self.device.dump_ui()
            if self._is_comment_area_visible(root):
                return True
            self.device.scroll_down(400)
            time.sleep(config.WAIT_SHORT)
        return False

    def _is_comment_area_visible(self, root: ET.Element) -> bool:
        """判断评论区是否在当前视图中。"""
        for node in root.iter("node"):
            t   = _text(node)
            rid = (node.get("resource-id") or "").lower()
            if "评论" in t or any(k in rid for k in _RES_COMMENT_AREA):
                return True
        return False

    def scroll_comments(self):
        """在评论区向下翻页。"""
        self.device.scroll_down(config.SCROLL_STEP_PX)
        time.sleep(config.WAIT_SHORT)

    def go_back(self):
        """返回上一页。"""
        self.device.back()
        time.sleep(config.WAIT_MEDIUM)

    # ──────────────────────────────────────────────────────────────────────
    # 首页 / Tab 导航
    # ──────────────────────────────────────────────────────────────────────

    def go_home(self):
        """返回小红书首页。"""
        self.device.home()
        time.sleep(0.5)
        self.launch()

    def switch_to_tab(self, tab_name: str):
        """
        切换到指定底部 Tab（如 "首页", "发现", "消息", "我"）。
        """
        root = self.device.dump_ui()
        for node in root.iter("node"):
            desc = (node.get("content-desc") or "").strip()
            t    = _text(node)
            if tab_name in desc or tab_name in t:
                cls = node.get("class", "")
                if node.get("clickable") == "true" or "Tab" in cls:
                    self.device.tap_node(node)
                    time.sleep(config.WAIT_MEDIUM)
                    return
        logger.warning("未找到 Tab: %s", tab_name)

    # ──────────────────────────────────────────────────────────────────────
    # URL & note_id 提取
    # ──────────────────────────────────────────────────────────────────────

    def extract_note_url(self) -> tuple:
        """
        在笔记详情页尝试获取 note_url 和 note_id。

        方式 1（快速）：从 dumpsys activity top 提取当前 intent URI
        方式 2（可靠）：点击分享按钮 → 在分享面板找 URL 文本 / 复制链接 → 读 toast
        返回 (note_id, note_url)
        """
        # ── 方式 1：activity intent ──────────────────────────────────────
        note_id = self._note_id_from_activity()
        if note_id:
            url = f"https://www.xiaohongshu.com/explore/{note_id}"
            logger.info("activity 提取 note_id: %s", note_id)
            return note_id, url

        # ── 方式 2：分享面板 ─────────────────────────────────────────────
        note_id, url = self._note_url_via_share()
        if url:
            logger.info("分享面板提取 url: %s", url)
        return note_id, url

    def _note_id_from_activity(self) -> str:
        """从 dumpsys activity top 解析当前笔记 ID。"""
        try:
            output = self.device.shell("dumpsys activity top")
            # 深链接格式：xhsdiscover://item/detail?id=<note_id>
            m = re.search(r'detail\?id=([0-9a-f]{16,})', output, re.IGNORECASE)
            if m:
                return m.group(1)
            # URL 格式：/explore/<note_id>
            m = re.search(r'/explore/([0-9a-f]{16,})', output, re.IGNORECASE)
            if m:
                return m.group(1)
        except ADBError as e:
            logger.debug("dumpsys 提取失败: %s", e)
        return ""

    def _note_url_via_share(self) -> tuple:
        """
        点击分享按钮，在分享面板中寻找 URL：
          1. 分享面板打开后直接扫描是否有 URL 文本
          2. 若无，点击「复制链接」，再扫 toast
        最后关闭分享面板（back），保持在笔记详情页。
        """
        # ── 1. 找到并点击分享按钮 ────────────────────────────────────────
        root = self.device.dump_ui()
        if not self._tap_share_button(root):
            logger.debug("未找到分享按钮")
            return "", ""
        time.sleep(config.WAIT_MEDIUM)

        # ── 2. 扫描分享面板内容 ──────────────────────────────────────────
        root = self.device.dump_ui()
        url = self._find_url_in_ui(root)

        if not url:
            # ── 3. 点击「复制链接」──────────────────────────────────────
            tapped = False
            for kw in ("复制链接", "复制", "Copy Link"):
                if self.device.tap_by_text(root, kw, exact=True):
                    tapped = True
                    break
            if tapped:
                time.sleep(config.WAIT_SHORT)
                root = self.device.dump_ui()
                url = self._find_url_in_ui(root)

        # ── 4. 关闭分享面板，回到笔记详情 ───────────────────────────────
        self.device.back()
        time.sleep(config.WAIT_SHORT)

        if not url:
            return "", ""

        note_id = self._parse_note_id(url)
        return note_id, url

    def _tap_share_button(self, root: ET.Element) -> bool:
        """找到分享按钮并点击。"""
        # content-desc 或 resource-id 含 share / 分享
        for node in root.iter("node"):
            desc = (node.get("content-desc") or "").lower()
            rid  = (node.get("resource-id") or "").lower()
            if any(k in desc for k in ("分享", "share")) or \
               any(k in rid  for k in ("share", "forward")):
                self.device.tap_node(node)
                return True
        return False

    @staticmethod
    def _find_url_in_ui(root: ET.Element) -> str:
        """在 UI 树中寻找小红书 URL 文本。"""
        for node in root.iter("node"):
            t = (node.get("text") or "").strip()
            if any(domain in t for domain in
                   ("xiaohongshu.com", "xhslink.com", "xhsdiscover://")):
                return t
        return ""

    @staticmethod
    def _parse_note_id(url: str) -> str:
        """从 URL 中解析 note_id。"""
        m = re.search(r'/explore/([0-9a-f]{16,})', url, re.IGNORECASE) or \
            re.search(r'[?&]id=([0-9a-f]{16,})', url, re.IGNORECASE)
        return m.group(1) if m else ""

    # ──────────────────────────────────────────────────────────────────────
    # 辅助
    # ──────────────────────────────────────────────────────────────────────

    def wait_loading(self, timeout: float = 5.0):
        """等待加载动画消失（检测进度条/加载中文字）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            root = self.device.dump_ui()
            loading = False
            for node in root.iter("node"):
                desc = (node.get("content-desc") or "").lower()
                t    = _text(node).lower()
                if "加载" in t or "loading" in desc:
                    loading = True
                    break
            if not loading:
                return
            time.sleep(0.5)
