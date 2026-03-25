"""
json_storage.py — 将笔记与评论数据以 JSON 格式持久化到本地文件。

目录结构：
  data/
    notes.json          ← 所有笔记列表（追加写入）
    comments/
      <note_id>.json    ← 每篇笔记对应的评论列表
"""

import os
import json
import time
import logging
from typing import List

import config
from models.note import Note
from models.comment import Comment

logger = logging.getLogger(__name__)


class JsonStorage:
    def __init__(self, output_dir: str = config.OUTPUT_DIR):
        self.output_dir = output_dir
        self.notes_file = os.path.join(output_dir, "notes.json")
        self.comments_dir = os.path.join(output_dir, "comments")
        os.makedirs(self.comments_dir, exist_ok=True)

    # ──────────────────────────────────────────────────────────────────────
    # 笔记
    # ──────────────────────────────────────────────────────────────────────

    def save_note(self, note: Note):
        """将笔记追加写入 notes.json。"""
        data = self._load_json_list(self.notes_file)
        # 如果同 ID 已存在则更新，否则追加
        existing_ids = {d.get("note_id") for d in data}
        d = note.to_dict()
        if note.note_id and note.note_id in existing_ids:
            data = [d if item.get("note_id") == note.note_id else item
                    for item in data]
            logger.debug("更新笔记: %s", note.note_id)
        else:
            data.append(d)
            logger.debug("新增笔记: %s", note.note_id or note.title[:20])
        self._save_json_list(self.notes_file, data)

    def save_notes(self, notes: List[Note]):
        for note in notes:
            self.save_note(note)

    # ──────────────────────────────────────────────────────────────────────
    # 评论
    # ──────────────────────────────────────────────────────────────────────

    def save_comments(self, note_id: str, comments: List[Comment]):
        """将评论列表写入 comments/<note_id>.json。"""
        fname = os.path.join(self.comments_dir,
                             f"{note_id or int(time.time())}.json")
        existing = self._load_json_list(fname)
        existing_keys = {
            f"{d.get('author_name')}||{d.get('content')}" for d in existing
        }
        new = [c.to_dict() for c in comments
               if f"{c.author_name}||{c.content}" not in existing_keys]
        all_data = existing + new
        self._save_json_list(fname, all_data)
        logger.info("评论已写入 %s（新增 %d 条）", fname, len(new))

    # ──────────────────────────────────────────────────────────────────────
    # 内部
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_json_list(path: str) -> list:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return []
        return []

    @staticmethod
    def _save_json_list(path: str, data: list):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
