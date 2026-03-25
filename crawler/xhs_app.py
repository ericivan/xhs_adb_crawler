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
        """启动小红书。"""
        if force_restart:
            self.device.stop_app(config.XHS_PACKAGE)
            time.sleep(1)
        if not self.device.is_app_foreground(config.XHS_PACKAGE):
            logger.info("启动小红书 App ...")
            self.device.launch_app(config.XHS_PACKAGE, config.XHS_MAIN_ACTIVITY)
            self._dismiss_splash()
        else:
            logger.info("小红书已在前台")

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

    def get_note_items_on_screen(self, root: ET.Element) -> List[ET.Element]:
        """
        获取当前屏幕上所有笔记卡片/条目节点。
        返回可点击的容器节点列表。
        """
        items = []

        # ── 策略 1：resource-id 匹配 ───────────────────────────────────
        for node in root.iter("node"):
            if _res_contains(node, *_RES_NOTE_ITEM):
                if node.get("clickable") == "true":
                    items.append(node)

        if items:
            return items

        # ── 策略 2：寻找可点击的 FrameLayout / LinearLayout 且含图片+文字
        for node in root.iter("node"):
            cls = node.get("class", "")
            if node.get("clickable") == "true" and (
                "FrameLayout" in cls or "LinearLayout" in cls or "CardView" in cls
            ):
                # 子节点里有 ImageView 且有 TextView
                children_cls = [c.get("class", "") for c in node]
                has_img  = any("ImageView" in c for c in children_cls)
                has_text = any("TextView" in c for c in children_cls)
                if has_img and has_text:
                    items.append(node)

        return items

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
