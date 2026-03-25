"""
mysql_storage.py — 将笔记与评论数据存入 MySQL 数据库。

依赖：pymysql（pip install pymysql）

对应表：
  xhs_note          — 笔记主表
  xhs_note_comment  — 评论表
"""

import json
import logging
import time
import re
from typing import List, Optional

import pymysql
import pymysql.cursors

import config
from models.note import Note
from models.comment import Comment

logger = logging.getLogger(__name__)


# ── 中文数字互转（"1.2万" → 12000）──────────────────────────────────────────
def _parse_count(text: str) -> int:
    """将点赞数/收藏数等文本转为整数。"""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        if "千" in text:
            return int(float(text.replace("千", "")) * 1000)
        return int(float(text))
    except (ValueError, TypeError):
        return 0


# ── SQL ────────────────────────────────────────────────────────────────────

_UPSERT_NOTE = """
INSERT INTO xhs_note
  (user_id, nickname, avatar, ip_location,
   add_ts, last_modify_ts,
   note_id, type, title, `desc`, video_url,
   `time`, last_update_time,
   liked_count, liked_count_int,
   collected_count, collected_count_int,
   comment_count, comment_count_int,
   share_count, share_count_int,
   image_list, tag_list, note_url,
   source_keyword, xsec_token)
VALUES
  (%(user_id)s, %(nickname)s, %(avatar)s, %(ip_location)s,
   %(add_ts)s, %(last_modify_ts)s,
   %(note_id)s, %(type)s, %(title)s, %(desc)s, %(video_url)s,
   %(time)s, %(last_update_time)s,
   %(liked_count)s, %(liked_count_int)s,
   %(collected_count)s, %(collected_count_int)s,
   %(comment_count)s, %(comment_count_int)s,
   %(share_count)s, %(share_count_int)s,
   %(image_list)s, %(tag_list)s, %(note_url)s,
   %(source_keyword)s, %(xsec_token)s)
ON DUPLICATE KEY UPDATE
  nickname         = VALUES(nickname),
  title            = VALUES(title),
  `desc`           = VALUES(`desc`),
  liked_count      = VALUES(liked_count),
  liked_count_int  = VALUES(liked_count_int),
  collected_count  = VALUES(collected_count),
  collected_count_int = VALUES(collected_count_int),
  comment_count    = VALUES(comment_count),
  comment_count_int = VALUES(comment_count_int),
  tag_list         = VALUES(tag_list),
  note_url         = VALUES(note_url),
  last_modify_ts   = VALUES(last_modify_ts)
"""

_INSERT_COMMENT = """
INSERT IGNORE INTO xhs_note_comment
  (user_id, nickname, avatar, ip_location,
   add_ts, last_modify_ts,
   comment_id, create_time, note_id,
   content, sub_comment_count, pictures,
   parent_comment_id,
   like_count, like_count_int)
VALUES
  (%(user_id)s, %(nickname)s, %(avatar)s, %(ip_location)s,
   %(add_ts)s, %(last_modify_ts)s,
   %(comment_id)s, %(create_time)s, %(note_id)s,
   %(content)s, %(sub_comment_count)s, %(pictures)s,
   %(parent_comment_id)s,
   %(like_count)s, %(like_count_int)s)
"""


class MysqlStorage:
    def __init__(self):
        self._conn: Optional[pymysql.connections.Connection] = None
        self._connect()

    def _connect(self):
        self._conn = pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            charset=config.MYSQL_CHARSET,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        logger.info("MySQL 已连接: %s:%s/%s",
                    config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DATABASE)

    def _cursor(self):
        """获取 cursor，断线自动重连。"""
        try:
            self._conn.ping(reconnect=True)
        except Exception:
            self._connect()
        return self._conn.cursor()

    # ──────────────────────────────────────────────────────────────────────
    # 笔记
    # ──────────────────────────────────────────────────────────────────────

    def save_note(self, note: Note):
        now_ms = int(time.time() * 1000)
        tag_list = json.dumps(
            note.topics + note.tags, ensure_ascii=False
        ) if (note.topics or note.tags) else None

        row = {
            "user_id":            None,
            "nickname":           note.author_name or None,
            "avatar":             None,
            "ip_location":        None,
            "add_ts":             now_ms,
            "last_modify_ts":     now_ms,
            "note_id":            note.note_id or None,
            "type":               note.note_type or None,
            "title":              note.title or None,
            "desc":               note.content or None,
            "video_url":          None,
            "time":               None,          # 无法从 ADB 获取精确时间戳
            "last_update_time":   None,
            "liked_count":        note.like_count or None,
            "liked_count_int":    _parse_count(note.like_count),
            "collected_count":    note.collect_count or None,
            "collected_count_int": _parse_count(note.collect_count),
            "comment_count":      note.comment_count or None,
            "comment_count_int":  _parse_count(note.comment_count),
            "share_count":        None,
            "share_count_int":    0,
            "image_list":         None,
            "tag_list":           tag_list,
            "note_url":           note.note_url or None,
            "source_keyword":     note.source_keyword or None,
            "xsec_token":         None,
        }
        with self._cursor() as cur:
            cur.execute(_UPSERT_NOTE, row)
        self._conn.commit()
        logger.debug("MySQL 保存笔记: %s", note.note_id or note.title)

    def save_notes(self, notes: List[Note]):
        for note in notes:
            self.save_note(note)

    # ──────────────────────────────────────────────────────────────────────
    # 评论
    # ──────────────────────────────────────────────────────────────────────

    def save_comments(self, note_id: str, comments: List[Comment]):
        now_ms = int(time.time() * 1000)
        rows = []
        for idx, c in enumerate(comments):
            rows.append({
                "user_id":           None,
                "nickname":          c.author_name or None,
                "avatar":            None,
                "ip_location":       None,
                "add_ts":            now_ms,
                "last_modify_ts":    now_ms,
                "comment_id":        None,       # ADB 无法获取
                "create_time":       None,       # 无法转精确时间戳
                "note_id":           note_id or None,
                "content":           c.content or None,
                "sub_comment_count": len(c.sub_comments),
                "pictures":          None,
                "parent_comment_id": None,
                "like_count":        c.like_count or None,
                "like_count_int":    _parse_count(c.like_count),
            })

        if not rows:
            return
        with self._cursor() as cur:
            cur.executemany(_INSERT_COMMENT, rows)
        self._conn.commit()
        logger.info("MySQL 保存 %d 条评论 (note_id=%s)", len(rows), note_id)

    # ──────────────────────────────────────────────────────────────────────

    def close(self):
        if self._conn:
            self._conn.close()
