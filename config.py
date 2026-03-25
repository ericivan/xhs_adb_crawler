# =============================================================================
# 小红书 ADB 爬虫 — 全局配置
# =============================================================================

# ── 设备 & ADB ─────────────────────────────────────────────────────────────
ADB_PATH = "adb"                     # adb 可执行文件路径
DEVICE_SERIAL = None                 # None = 自动选第一台设备; 多设备时填 serial

# ── 小红书 App ──────────────────────────────────────────────────────────────
XHS_PACKAGE = "com.xingin.xhs"
XHS_MAIN_ACTIVITY = "com.xingin.xhs/.activity.SplashActivity"
# 笔记详情 deep-link 模板 (通过 note_id 直跳)
XHS_NOTE_DEEPLINK = "xhsdiscover://item/detail?id={note_id}"

# ── 临时文件路径（设备端）─────────────────────────────────────────────────
DEVICE_SCREENSHOT_PATH = "/sdcard/xhs_screenshot.png"
DEVICE_UI_DUMP_PATH    = "/sdcard/xhs_ui.xml"

# ── 爬取行为控制 ───────────────────────────────────────────────────────────
SCROLL_DURATION_MS   = 400          # 每次滑动持续时间(ms)
SCROLL_STEP_PX       = 600          # 每次滑动像素距离
WAIT_SHORT           = 0.8          # 短等待(s)
WAIT_MEDIUM          = 1.5          # 中等等待(s)
WAIT_LONG            = 3.0          # 长等待(s)
MAX_SEARCH_RESULTS   = 20           # 每次搜索最多抓取笔记数
MAX_COMMENT_SCROLLS  = 15           # 评论区最多滚动次数
MAX_FEED_NOTES       = 10           # 首页信息流最多抓取笔记数
RETRY_TIMES          = 3            # 操作失败重试次数

# ── 输出 ───────────────────────────────────────────────────────────────────
OUTPUT_DIR      = "data"
JSON_OUTPUT     = True
SQLITE_OUTPUT   = True
SQLITE_DB_NAME  = "xhs_data.db"
SCREENSHOT_SAVE = False             # 是否保存调试截图
