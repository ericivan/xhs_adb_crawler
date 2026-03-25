"""
note_crawler.py — 笔记详情抓取逻辑：
  1. 进入笔记详情页
  2. 多次 dump UI 以获取完整正文（长文需滚动）
  3. 提取笔记元数据
  4. 保存可选截图
"""

import os
import time
import logging
from typing import Optional

import config
from crawler.adb_device import ADBDevice
from crawler.xhs_app import XHSApp
from crawler.ui_parser import parse_note_detail
from models.note import Note

logger = logging.getLogger(__name__)


class NoteCrawler:
    """抓取单篇笔记的元数据。"""

    def __init__(self, device: ADBDevice, app: XHSApp):
        self.device = device
        self.app = app

    def crawl_current_note(self, note_id: str = "") -> Note:
        """
        抓取当前詳情页的笔记数据。
        调用前需确保已进入笔记详情页。
        """
        note = Note(note_id=note_id)

        # ── 第一屏：获取标题、作者、正文开头 ────────────────────────────
        root = self.device.dump_ui()
        note = parse_note_detail(root, note)
        logger.debug("第一屏解析: title=%r author=%r", note.title, note.author_name)

        # ── 适当向下滚动获取更多正文（最多 2 次）────────────────────────
        if not note.content or len(note.content) < 20:
            for _ in range(2):
                self.device.scroll_down(300)
                time.sleep(config.WAIT_SHORT)
                root = self.device.dump_ui()
                note = parse_note_detail(root, note)
                if note.content:
                    break

        # ── 截图（可选）──────────────────────────────────────────────────
        if config.SCREENSHOT_SAVE:
            screenshot_dir = os.path.join(config.OUTPUT_DIR, "screenshots")
            os.makedirs(screenshot_dir, exist_ok=True)
            fname = f"note_{note_id or int(time.time())}.png"
            local_path = os.path.join(screenshot_dir, fname)
            self.device.screenshot(local_path)
            note.screenshot_path = local_path
            logger.debug("截图保存: %s", local_path)

        # ── 记录互动数据（有时需要滚回顶部）────────────────────────────
        if not note.like_count:
            self.device.scroll_up(1000)
            time.sleep(config.WAIT_SHORT)
            root = self.device.dump_ui()
            note = parse_note_detail(root, note)

        logger.info("笔记抓取完成: %s", note)
        return note

    def crawl_note_by_deeplink(self, note_id: str) -> Optional[Note]:
        """通过深链接打开并抓取笔记。"""
        if not self.app.open_note_by_deeplink(note_id):
            logger.warning("无法通过深链接打开笔记: %s", note_id)
            return None
        note = self.crawl_current_note(note_id=note_id)
        return note
