#!/usr/bin/env python3
"""
main.py — 小红书 ADB 爬虫入口

用法示例:

  # 搜索关键词，抓取笔记 + 评论
  python main.py search --keyword "美食探店" --notes 10 --comments

  # 通过 Note ID 直接抓取（支持多个）
  python main.py note --ids 64a1b2c3d4e5f6g7,64a1b2c3d4e5f6g8

  # 抓取首页信息流
  python main.py feed --notes 5 --comments

  # 无线 ADB 连接后再抓取
  python main.py search --keyword "护肤" --connect 192.168.1.100:5555

全局选项:
  --device SERIAL   指定设备 serial（多设备时使用）
  --no-comments     只抓笔记，不抓评论
  --max-scrolls N   评论区最多滚动 N 次（默认 15）
  --output DIR      输出目录（默认 data/）
  --json            输出 JSON（默认开启）
  --sqlite          输出 SQLite（默认开启）
  --debug           开启 DEBUG 日志
"""

import sys
import os
import time
import logging
import argparse
from typing import List

import config
from crawler.adb_device import ADBDevice, ADBError
from crawler.xhs_app import XHSApp
from crawler.note_crawler import NoteCrawler
from crawler.comment_crawler import CommentCrawler
from storage.json_storage import JsonStorage
from storage.sqlite_storage import SqliteStorage
from storage.mysql_storage import MysqlStorage
from models.note import Note
from models.comment import Comment


# ─────────────────────────────────────────────────────────────────────────────
# 日志设置
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    fmt   = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    # 压低第三方库日志
    for lib in ("PIL", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


logger = logging.getLogger("xhs_crawler")


# ─────────────────────────────────────────────────────────────────────────────
# 存储辅助
# ─────────────────────────────────────────────────────────────────────────────

def build_storage(args) -> tuple:
    """返回 (json_storage | None, sqlite_storage | None, mysql_storage | None)。"""
    js    = JsonStorage(args.output)   if args.json        else None
    sql   = SqliteStorage(args.output) if args.sqlite      else None
    mysql = None
    if config.MYSQL_OUTPUT:
        try:
            mysql = MysqlStorage()
        except Exception as e:
            logger.error("MySQL 连接失败: %s，跳过 MySQL 存储", e)
    return js, sql, mysql


def persist(note: Note, comments: List[Comment],
            js, sql, mysql):
    """将笔记和评论写入所有启用的存储后端。"""
    nid = note.note_id or note.title[:20]
    if js:
        js.save_note(note)
        if comments:
            js.save_comments(nid, comments)
    if sql:
        sql.save_note(note)
        if comments:
            sql.save_comments(nid, comments)
    if mysql:
        mysql.save_note(note)
        if comments:
            mysql.save_comments(nid, comments)


# ─────────────────────────────────────────────────────────────────────────────
# 核心爬取流程
# ─────────────────────────────────────────────────────────────────────────────

def crawl_one_note(app: XHSApp, note_crawler: NoteCrawler,
                   comment_crawler: CommentCrawler,
                   note_id: str = "",
                   crawl_comments: bool = True,
                   max_scrolls: int = config.MAX_COMMENT_SCROLLS,
                   ) -> tuple:
    """
    抓取当前笔记详情页，返回 (Note, List[Comment])。
    """
    note = note_crawler.crawl_current_note(note_id=note_id)
    comments: List[Comment] = []
    if crawl_comments:
        comments = comment_crawler.crawl_comments(
            note_id=note.note_id or note_id,
            max_scrolls=max_scrolls,
        )
    return note, comments


# ─────────────────────────────────────────────────────────────────────────────
# 子命令实现
# ─────────────────────────────────────────────────────────────────────────────

def cmd_search(args, app: XHSApp, note_crawler: NoteCrawler,
               comment_crawler: CommentCrawler,
               js: JsonStorage, sql: SqliteStorage):
    """搜索关键词并抓取笔记。"""
    keyword  = args.keyword
    n_notes  = args.notes
    do_cmt   = not args.no_comments
    max_sc   = args.max_scrolls

    logger.info("=== 开始搜索: %s (最多 %d 篇) ===", keyword, n_notes)

    if not app.search(keyword):
        logger.error("进入搜索结果页失败，请检查设备状态")
        return

    time.sleep(config.WAIT_MEDIUM)

    crawled = 0
    screen_offset = 0          # 记录已点击过的条目数，避免重复

    while crawled < n_notes:
        root = app.device.dump_ui()
        items = app.get_note_items_on_screen(root)

        # 过滤掉已经翻过的条目（按屏幕位置偏移）
        remaining = items[screen_offset:]
        if not remaining:
            logger.info("当前屏幕无更多条目，向下滑动")
            app.device.scroll_down()
            time.sleep(config.WAIT_MEDIUM)
            screen_offset = 0
            continue

        for item in remaining:
            if crawled >= n_notes:
                break

            # 计算绝对位置用于点击
            center = app.device.get_node_center(item)
            if not center:
                continue

            logger.info("--- 打开第 %d 篇笔记 ---", crawled + 1)
            app.device.tap(*center)
            time.sleep(config.WAIT_LONG)

            # 确认进入详情页
            detail_root = app.device.dump_ui()
            if not app._is_note_detail_page(detail_root):
                logger.warning("未进入详情页，跳过")
                app.go_back()
                time.sleep(config.WAIT_MEDIUM)
                continue

            try:
                note, comments = crawl_one_note(
                    app, note_crawler, comment_crawler,
                    crawl_comments=do_cmt, max_scrolls=max_sc,
                )
                note.source_keyword = keyword
                if note.is_valid():
                    persist(note, comments, js, sql, mysql)
                    crawled += 1
                    logger.info("进度: %d/%d  %s", crawled, n_notes, note)
            except Exception as e:
                logger.error("抓取失败: %s", e, exc_info=True)

            app.go_back()
            time.sleep(config.WAIT_MEDIUM)
            screen_offset += 1

        # 本屏已处理完毕，下滑加载新内容
        if crawled < n_notes:
            app.device.scroll_down()
            time.sleep(config.WAIT_MEDIUM)
            screen_offset = 0

    logger.info("=== 搜索抓取完成: 共 %d 篇 ===", crawled)


def cmd_note(args, app: XHSApp, note_crawler: NoteCrawler,
             comment_crawler: CommentCrawler,
             js: JsonStorage, sql: SqliteStorage):
    """通过笔记 ID 列表直接抓取。"""
    note_ids = [nid.strip() for nid in args.ids.split(",") if nid.strip()]
    do_cmt   = not args.no_comments
    max_sc   = args.max_scrolls

    logger.info("=== 按 ID 抓取 %d 篇笔记 ===", len(note_ids))
    for note_id in note_ids:
        logger.info("--- 抓取笔记: %s ---", note_id)
        if not app.open_note_by_deeplink(note_id):
            logger.error("无法打开笔记: %s", note_id)
            continue
        try:
            note, comments = crawl_one_note(
                app, note_crawler, comment_crawler,
                note_id=note_id,
                crawl_comments=do_cmt,
                max_scrolls=max_sc,
            )
            if note.is_valid():
                persist(note, comments, js, sql, mysql)
                logger.info("完成: %s", note)
        except Exception as e:
            logger.error("抓取失败 %s: %s", note_id, e, exc_info=True)
        app.go_back()
        time.sleep(config.WAIT_MEDIUM)


def cmd_feed(args, app: XHSApp, note_crawler: NoteCrawler,
             comment_crawler: CommentCrawler,
             js: JsonStorage, sql: SqliteStorage):
    """抓取首页信息流。"""
    n_notes = args.notes
    do_cmt  = not args.no_comments
    max_sc  = args.max_scrolls

    logger.info("=== 抓取首页信息流 (最多 %d 篇) ===", n_notes)
    app.switch_to_tab("首页")
    time.sleep(config.WAIT_MEDIUM)

    crawled = 0
    while crawled < n_notes:
        root  = app.device.dump_ui()
        items = app.get_note_items_on_screen(root)
        if not items:
            app.device.scroll_down()
            time.sleep(config.WAIT_MEDIUM)
            continue

        for item in items:
            if crawled >= n_notes:
                break
            center = app.device.get_node_center(item)
            if not center:
                continue

            app.device.tap(*center)
            time.sleep(config.WAIT_LONG)

            detail_root = app.device.dump_ui()
            if not app._is_note_detail_page(detail_root):
                app.go_back()
                time.sleep(config.WAIT_SHORT)
                continue

            try:
                note, comments = crawl_one_note(
                    app, note_crawler, comment_crawler,
                    crawl_comments=do_cmt, max_scrolls=max_sc,
                )
                if note.is_valid():
                    persist(note, comments, js, sql, mysql)
                    crawled += 1
                    logger.info("进度: %d/%d  %s", crawled, n_notes, note)
            except Exception as e:
                logger.error("抓取失败: %s", e, exc_info=True)

            app.go_back()
            time.sleep(config.WAIT_MEDIUM)

        app.device.scroll_down()
        time.sleep(config.WAIT_MEDIUM)

    logger.info("=== 信息流抓取完成: 共 %d 篇 ===", crawled)


# ─────────────────────────────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs_crawler",
        description="小红书 ADB 爬虫 — 抓取笔记与评论",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--device",      default=None,
                        help="设备 serial（多设备时指定）")
    parser.add_argument("--connect",     default=None,
                        help="通过 TCP/IP 连接设备，如 192.168.1.100:5555")
    parser.add_argument("--output",      default=config.OUTPUT_DIR,
                        help="数据输出目录（默认 data/）")
    parser.add_argument("--no-comments", action="store_true",
                        help="只抓笔记，不抓评论")
    parser.add_argument("--max-scrolls", type=int,
                        default=config.MAX_COMMENT_SCROLLS,
                        help="评论区最多滚动次数（默认 15）")
    parser.add_argument("--json",        action="store_true", default=True,
                        help="输出 JSON（默认开启）")
    parser.add_argument("--no-json",     dest="json", action="store_false")
    parser.add_argument("--sqlite",      action="store_true", default=True,
                        help="输出 SQLite（默认开启）")
    parser.add_argument("--no-sqlite",   dest="sqlite", action="store_false")
    parser.add_argument("--debug",       action="store_true",
                        help="开启 DEBUG 日志")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # search 子命令
    sp_search = subparsers.add_parser("search", help="搜索关键词并抓取笔记")
    sp_search.add_argument("--keyword", "-k", required=True, help="搜索关键词")
    sp_search.add_argument("--notes",   "-n", type=int,
                           default=config.MAX_SEARCH_RESULTS,
                           help="最多抓取笔记数（默认 20）")

    # note 子命令
    sp_note = subparsers.add_parser("note", help="按笔记 ID 直接抓取")
    sp_note.add_argument("--ids", "-i", required=True,
                         help="笔记 ID，多个用逗号分隔")

    # feed 子命令
    sp_feed = subparsers.add_parser("feed", help="抓取首页信息流")
    sp_feed.add_argument("--notes", "-n", type=int,
                         default=config.MAX_FEED_NOTES,
                         help="最多抓取笔记数（默认 10）")

    # dump-ui 子命令（调试用）
    sp_dump = subparsers.add_parser("dump-ui",
                                    help="导出当前界面 UI XML，用于调试 resource-id")
    sp_dump.add_argument("--out", "-o", default="ui_dump.xml",
                         help="保存路径（默认 ui_dump.xml）")

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.debug)
    logger.info("小红书 ADB 爬虫启动")

    # ── 设备初始化 ──────────────────────────────────────────────────────
    device = ADBDevice(serial=args.device)

    if args.connect:
        logger.info("连接设备: %s", args.connect)
        if not device.connect(args.connect):
            logger.error("连接失败: %s", args.connect)
            sys.exit(1)

    if not device.auto_select_device():
        logger.error("未找到在线 ADB 设备，请检查连接")
        sys.exit(1)

    logger.info("使用设备: %s", device.serial)

    # ── dump-ui：只需要设备，不需要启动 App ─────────────────────────────
    if args.command == "dump-ui":
        import xml.etree.ElementTree as ET
        out_path = args.out
        root = device.dump_ui()
        ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
        logger.info("UI XML 已保存: %s", os.path.abspath(out_path))
        # 打印所有有文本且有 resource-id 的节点，方便定位字段
        print("\n=== 有文本的节点（resource-id → text）===")
        for node in root.iter("node"):
            rid = node.get("resource-id", "")
            t   = (node.get("text") or "").strip()
            if rid and t:
                print(f"  {rid:60s}  {t[:60]}")
        return

    # ── App & Crawler 初始化 ─────────────────────────────────────────────
    app              = XHSApp(device)
    app.setup()
    app.launch()

    note_crawler    = NoteCrawler(device, app)
    comment_crawler = CommentCrawler(device, app)
    js, sql, mysql  = build_storage(args)

    # ── 执行子命令 ──────────────────────────────────────────────────────
    try:
        if args.command == "search":
            cmd_search(args, app, note_crawler, comment_crawler, js, sql, mysql)
        elif args.command == "note":
            cmd_note(args, app, note_crawler, comment_crawler, js, sql, mysql)
        elif args.command == "feed":
            cmd_feed(args, app, note_crawler, comment_crawler, js, sql, mysql)
    except KeyboardInterrupt:
        logger.info("用户中断")
    except ADBError as e:
        logger.error("ADB 错误: %s", e)
        sys.exit(1)
    finally:
        app.quit()
        if sql:
            sql.close()
        if mysql:
            mysql.close()

    logger.info("爬虫结束，数据已保存至 %s/", args.output)


if __name__ == "__main__":
    main()
