"""
sqlite_storage.py — 将笔记与评论数据存入 SQLite 数据库。

表结构：
  notes    — 笔记主表
  comments — 评论表（含二级回复以 JSON 字段存储）
"""

import os
import json
import sqlite3
import logging
from typing import List

import config
from models.note import Note
from models.comment import Comment

logger = logging.getLogger(__name__)

_CREATE_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id       TEXT UNIQUE,
    title         TEXT,
    content       TEXT,
    note_type     TEXT,
    author_name   TEXT,
    author_id     TEXT,
    like_count    TEXT,
    collect_count TEXT,
    comment_count TEXT,
    tags          TEXT,       -- JSON array
    topics        TEXT,       -- JSON array
    publish_time  TEXT,
    crawled_at    REAL,
    screenshot_path TEXT
);
"""

_CREATE_COMMENTS = """
CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id       TEXT,
    comment_index INTEGER,
    author_name   TEXT,
    author_id     TEXT,
    content       TEXT,
    like_count    TEXT,
    publish_time  TEXT,
    sub_comments  TEXT,       -- JSON array of sub-comment dicts
    crawled_at    REAL,
    UNIQUE(note_id, author_name, content)
);
"""

_INSERT_NOTE = """
INSERT OR REPLACE INTO notes
  (note_id, title, content, note_type, author_name, author_id,
   like_count, collect_count, comment_count, tags, topics,
   publish_time, crawled_at, screenshot_path)
VALUES
  (:note_id, :title, :content, :note_type, :author_name, :author_id,
   :like_count, :collect_count, :comment_count, :tags, :topics,
   :publish_time, :crawled_at, :screenshot_path)
"""

_INSERT_COMMENT = """
INSERT OR IGNORE INTO comments
  (note_id, comment_index, author_name, author_id, content,
   like_count, publish_time, sub_comments, crawled_at)
VALUES
  (:note_id, :comment_index, :author_name, :author_id, :content,
   :like_count, :publish_time, :sub_comments, :crawled_at)
"""


class SqliteStorage:
    def __init__(self, output_dir: str = config.OUTPUT_DIR,
                 db_name: str = config.SQLITE_DB_NAME):
        os.makedirs(output_dir, exist_ok=True)
        self.db_path = os.path.join(output_dir, db_name)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_tables()
        logger.info("SQLite 数据库: %s", self.db_path)

    def _init_tables(self):
        cur = self._conn.cursor()
        cur.execute(_CREATE_NOTES)
        cur.execute(_CREATE_COMMENTS)
        self._conn.commit()

    # ──────────────────────────────────────────────────────────────────────
    # 笔记
    # ──────────────────────────────────────────────────────────────────────

    def save_note(self, note: Note):
        d = note.to_dict()
        d["tags"]   = json.dumps(d["tags"],   ensure_ascii=False)
        d["topics"] = json.dumps(d["topics"], ensure_ascii=False)
        self._conn.execute(_INSERT_NOTE, d)
        self._conn.commit()
        logger.debug("保存笔记到 SQLite: %s", note.note_id or note.title[:20])

    def save_notes(self, notes: List[Note]):
        for note in notes:
            self.save_note(note)

    # ──────────────────────────────────────────────────────────────────────
    # 评论
    # ──────────────────────────────────────────────────────────────────────

    def save_comments(self, note_id: str, comments: List[Comment]):
        rows = []
        for c in comments:
            d = c.to_dict()
            d["note_id"]      = note_id or d.get("note_id", "")
            d["sub_comments"] = json.dumps(d["sub_comments"], ensure_ascii=False)
            rows.append(d)
        self._conn.executemany(_INSERT_COMMENT, rows)
        self._conn.commit()
        logger.info("保存 %d 条评论到 SQLite (note_id=%s)", len(rows), note_id)

    # ──────────────────────────────────────────────────────────────────────
    # 查询（供调试 / 导出）
    # ──────────────────────────────────────────────────────────────────────

    def query_notes(self, limit: int = 100) -> List[dict]:
        cur = self._conn.execute(f"SELECT * FROM notes LIMIT {limit}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_comments(self, note_id: str = "", limit: int = 500) -> List[dict]:
        if note_id:
            cur = self._conn.execute(
                f"SELECT * FROM comments WHERE note_id=? LIMIT {limit}", (note_id,)
            )
        else:
            cur = self._conn.execute(f"SELECT * FROM comments LIMIT {limit}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self):
        self._conn.close()
