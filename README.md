# 小红书 ADB 爬虫

通过 **ADB（Android Debug Bridge）** 自动化操作小红书 App，抓取笔记信息与评论数据。

## 功能

| 功能 | 说明 |
|------|------|
| 搜索抓取 | 输入关键词，自动搜索并批量抓取笔记 |
| ID 直跳 | 通过笔记 ID 深链接直接打开并抓取 |
| 信息流 | 抓取首页推荐信息流中的笔记 |
| 评论抓取 | 自动滚动评论区，抓取一级评论及互动数据 |
| 双格式输出 | 同时写入 **JSON 文件** 和 **SQLite 数据库** |

## 环境要求

- Python 3.11.14（开发环境版本，3.8+ 均可运行）
- ADB 已安装并加入 PATH（`adb version` 可正常执行）
- 安卓手机已开启「USB 调试」或「无线调试」
- 小红书 App 已安装（包名 `com.xingin.xhs`）

### 中文输入（推荐）

为了正确输入中文搜索词，建议在手机上安装 [ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)，并在输入法设置中切换到 ADBKeyBoard。

## 安装

```bash
git clone <repo_url>
cd xhs_adb_crawler
pip install -r requirements.txt
```

## 快速上手

### 1. 连接设备

**USB 连接：**
```bash
adb devices   # 确认设备已识别
```

**无线 ADB（Android 11+）：**
```bash
# 在手机「开发者选项」中启用「无线调试」，获取 IP:端口
python main.py search --keyword "美食" --connect 192.168.1.100:5555
```

### 2. 搜索关键词抓取

```bash
# 搜索「美食探店」，抓取 10 篇笔记 + 评论
python main.py search --keyword "美食探店" --notes 10

# 只抓笔记，不抓评论
python main.py search --keyword "护肤" --notes 20 --no-comments

# 限制评论区滚动次数
python main.py search --keyword "旅行" --notes 5 --max-scrolls 5
```

### 3. 按笔记 ID 抓取

```bash
# 单篇
python main.py note --ids 64a1b2c3d4e5f6g7

# 多篇（逗号分隔）
python main.py note --ids 64a1b2c3,64d4e5f6,64g7h8i9
```

### 4. 首页信息流

```bash
python main.py feed --notes 10
```

### 5. 其他选项

```bash
# 禁用 SQLite 输出（只保存 JSON）
python main.py search --keyword "穿搭" --no-sqlite

# 禁用 JSON 输出（只保存 SQLite）
python main.py search --keyword "穿搭" --no-json

# 指定输出目录
python main.py search --keyword "穿搭" --output my_data/

# 开启调试日志
python main.py search --keyword "穿搭" --debug

# 多设备时指定 serial
python main.py search --keyword "穿搭" --device emulator-5554
```

## 输出结构

```
data/
├── notes.json              # 所有笔记列表
├── comments/
│   ├── <note_id>.json      # 每篇笔记的评论
│   └── ...
└── xhs_data.db             # SQLite 数据库
```

### notes.json 字段

```json
{
  "note_id": "",
  "title": "笔记标题",
  "content": "正文内容",
  "note_type": "图文/视频",
  "author_name": "作者昵称",
  "author_id": "",
  "like_count": "1.2万",
  "collect_count": "3456",
  "comment_count": "89",
  "tags": ["标签1"],
  "topics": ["#话题"],
  "publish_time": "2024-01-01",
  "crawled_at": 1700000000.0
}
```

### 评论字段

```json
{
  "note_id": "...",
  "comment_index": 0,
  "author_name": "用户昵称",
  "content": "评论内容",
  "like_count": "12",
  "publish_time": "3小时前",
  "sub_comments": []
}
```

## 项目结构

```
xhs_adb_crawler/
├── main.py               # CLI 入口
├── config.py             # 全局配置
├── requirements.txt
├── crawler/
│   ├── adb_device.py     # ADB 基础操作封装
│   ├── xhs_app.py        # 小红书 App 自动化操作
│   ├── note_crawler.py   # 笔记详情抓取
│   ├── comment_crawler.py# 评论区抓取
│   └── ui_parser.py      # UI XML 解析
├── models/
│   ├── note.py           # 笔记数据模型
│   └── comment.py        # 评论数据模型
├── storage/
│   ├── json_storage.py   # JSON 持久化
│   └── sqlite_storage.py # SQLite 持久化
└── data/                 # 输出目录（自动创建）
```

## 常见问题

**Q: 无法识别设备？**
检查 `adb devices` 输出，确认手机已授权 USB 调试。

**Q: 中文无法输入？**
安装 ADBKeyBoard 并切换为默认输入法；或修改 `config.py` 中的 `input_text` 备用方案。

**Q: 解析不到笔记内容？**
小红书版本更新后 resource-id 可能变化。运行 `adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml` 查看实际 UI 结构，按需更新 `crawler/ui_parser.py` 中的 resource-id 关键词列表。

**Q: 评论抓取不完整？**
增大 `--max-scrolls` 参数值；或在 `config.py` 中调整 `WAIT_SHORT` / `WAIT_MEDIUM` 延时以适配网络较慢的情况。

## 注意事项

- 本工具仅用于个人学习与研究，请勿用于商业用途。
- 频繁抓取可能触发小红书风控，建议控制速度（默认延时已较保守）。
- 部分内容需要登录才能查看，请确保手机上已登录小红书账号。
