"""
comment_crawler.py — 评论区抓取逻辑：
  1. 在笔记详情页滚动到评论区
  2. 分页滚动收集评论
  3. 去重（相同内容的评论不重复记录）
  4. 支持抓取二级回复（展开回复）
"""

import time
import logging
from typing import List, Set

import config
from crawler.adb_device import ADBDevice
from crawler.xhs_app import XHSApp
from crawler.ui_parser import parse_comments
from models.comment import Comment

logger = logging.getLogger(__name__)


class CommentCrawler:
    """抓取笔记评论区数据。"""

    def __init__(self, device: ADBDevice, app: XHSApp):
        self.device = device
        self.app = app

    def crawl_comments(self, note_id: str = "",
                        max_scrolls: int = config.MAX_COMMENT_SCROLLS
                        ) -> List[Comment]:
        """
        在当前笔记详情页抓取所有评论。
        调用前需确保已进入笔记详情页。

        Args:
            note_id:     所属笔记 ID
            max_scrolls: 最多滚动次数
        Returns:
            Comment 对象列表
        """
        all_comments: List[Comment] = []
        seen: Set[str] = set()          # 已见评论内容去重键
        start_index = 0

        # ── 1. 滚动到评论区 ──────────────────────────────────────────────
        logger.info("滚动到评论区 ...")
        if not self.app.scroll_to_comments():
            logger.warning("未找到评论区，直接尝试抓取")

        # ── 2. 循环翻页抓取 ──────────────────────────────────────────────
        for scroll_idx in range(max_scrolls):
            root = self.device.dump_ui()
            batch = parse_comments(root, note_id=note_id, start_index=start_index)

            # 去重并累积
            new_count = 0
            for c in batch:
                key = f"{c.author_name}||{c.content}"
                if key not in seen and c.is_valid():
                    seen.add(key)
                    all_comments.append(c)
                    new_count += 1

            logger.info("第 %d 次滚动，新增 %d 条，累计 %d 条",
                        scroll_idx + 1, new_count, len(all_comments))

            start_index += new_count

            # ── 检查是否到达底部 ─────────────────────────────────────────
            if self._is_comment_end(root):
                logger.info("已到达评论底部")
                break

            # ── 无新内容时提前停止 ───────────────────────────────────────
            if new_count == 0 and scroll_idx > 2:
                logger.info("连续无新评论，停止翻页")
                break

            self.app.scroll_comments()

        logger.info("评论抓取完成，共 %d 条", len(all_comments))
        return all_comments

    # ──────────────────────────────────────────────────────────────────────
    # 辅助
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_comment_end(root) -> bool:
        """检测是否出现「没有更多评论」等底部提示。"""
        end_texts = ("没有更多评论", "已经到底了", "暂无评论",
                     "还没有评论", "成为第一个评论的人", "查看全部评论")
        for node in root.iter("node"):
            t = (node.get("text") or "").strip()
            if any(kw in t for kw in end_texts):
                return True
        return False

    def try_expand_replies(self, comment: Comment) -> List[Comment]:
        """
        尝试展开某条评论的回复（点击"查看X条回复"按钮）。
        返回带有 sub_comments 填充的 comment 列表（通常只有1条）。
        """
        root = self.device.dump_ui()
        for node in root.iter("node"):
            t = (node.get("text") or "").strip()
            if "条回复" in t or "查看回复" in t:
                self.device.tap_node(node)
                time.sleep(config.WAIT_MEDIUM)
                # 展开后重新 dump 解析子评论
                sub_root = self.device.dump_ui()
                sub_comments_raw = parse_comments(sub_root, note_id=comment.note_id)
                # 跳过第一条（即父评论本身），其余视为子评论
                from models.comment import SubComment
                for sc in sub_comments_raw[1:]:
                    sub = SubComment(
                        author_name=sc.author_name,
                        content=sc.content,
                        like_count=sc.like_count,
                        publish_time=sc.publish_time,
                    )
                    comment.sub_comments.append(sub)
                break
        return [comment]
