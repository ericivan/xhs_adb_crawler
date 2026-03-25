"""
comment.py — 小红书评论数据模型
"""

from dataclasses import dataclass, field, asdict
from typing import List
import time


@dataclass
class SubComment:
    """二级评论（回复）。"""
    author_name: str = ""
    author_id: str = ""
    content: str = ""
    like_count: str = ""
    publish_time: str = ""
    crawled_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comment:
    """一级评论。"""
    note_id: str = ""                               # 所属笔记 ID
    comment_index: int = 0                          # 顺序编号

    author_name: str = ""
    author_id: str = ""
    content: str = ""
    like_count: str = ""
    publish_time: str = ""

    sub_comments: List[SubComment] = field(default_factory=list)
    crawled_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sub_comments"] = [s.to_dict() for s in self.sub_comments]
        return d

    def is_valid(self) -> bool:
        return bool(self.content)

    def __repr__(self):
        return (f"<Comment #{self.comment_index} author={self.author_name!r} "
                f"content={self.content[:30]!r} subs={len(self.sub_comments)}>")
