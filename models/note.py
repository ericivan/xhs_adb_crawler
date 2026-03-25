"""
note.py — 小红书笔记数据模型
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List
import time


@dataclass
class Note:
    # ── 基础信息 ──────────────────────────────────────────────────────────
    note_id: str = ""                 # 笔记 ID（从 URL 或界面提取）
    title: str = ""                   # 标题
    content: str = ""                 # 正文
    note_type: str = ""               # 图文 / 视频

    # ── 作者信息 ──────────────────────────────────────────────────────────
    author_name: str = ""             # 作者昵称
    author_id: str = ""               # 作者 ID

    # ── 互动数据 ──────────────────────────────────────────────────────────
    like_count: str = ""              # 点赞数（字符串保留"万"等单位）
    collect_count: str = ""           # 收藏数
    comment_count: str = ""           # 评论数

    # ── 标签 & 话题 ──────────────────────────────────────────────────────
    tags: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)

    # ── 发布时间 ──────────────────────────────────────────────────────────
    publish_time: str = ""

    # ── 抓取元数据 ───────────────────────────────────────────────────────
    crawled_at: float = field(default_factory=time.time)
    note_url: str = ""                # 笔记完整 URL
    screenshot_path: str = ""         # 本地截图路径（可选）

    def to_dict(self) -> dict:
        return asdict(self)

    def is_valid(self) -> bool:
        """至少有一个字段有值即认为有效（防止 resource-id 混淆导致漏存）。"""
        return bool(self.title or self.content or self.author_name
                    or self.like_count or self.topics)

    def __repr__(self):
        return (f"<Note id={self.note_id!r} author={self.author_name!r} "
                f"title={self.title[:20]!r} likes={self.like_count}>")
