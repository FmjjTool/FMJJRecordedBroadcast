# 封茗囧菌数据汇总库 · 统一维护工具箱（PySide6 版）

> **FengMingToolbox v2.8.8** · 制作：歆慕
>
> 录播工具箱一体化工具，封茗囧菌维护组专用软件程序
>
> 项目地址：<https://github.com/FmjjTool/FMJJRecordedBroadcast> · 当前版本下载：<https://github.com/FmjjTool/FMJJRecordedBroadcast/releases/tag/2.8.8>

一套面向「封茗囧菌数据汇总库」的**桌面一体化工具箱**，把内容采集、录播后期、音乐服务、直播录制、账号追踪、票务/购物解析等散落能力整合进同一个窗口。

- **定位**：多平台内容下载器 + 录播后期工具箱 + 数据追踪台
- **GUI 框架**：PySide6（Qt6）
- **设计原则**：引擎层与 GUI 层完全解耦（引擎无反向依赖 GUI），页面**懒加载**（点开才实例化）
- **前身**：`../平台信息获取/`（customtkinter 版）。本项目完整复用其引擎层，GUI 层用 Qt6 全部重写

---

## 目录

1. [核心功能](#一核心功能)
2. [架构设计](#二架构设计)
3. [目录结构](#三目录结构)
4. [环境要求](#四环境要求)
5. [安装步骤](#五安装步骤)
6. [配置说明](#六配置说明)
7. [启动与运行](#七启动与运行)
8. [使用示例](#八使用示例)
9. [打包发布](#九打包发布)
10. [常见问题](#十常见问题)
11. [维护信息](#十一维护信息)

---

## 一、核心功能

侧栏共 **9 个分组、33 个页面**（均以 `BasePage` 为基类）。

### 1. 📊 信息获取

| 页面 | 说明 |
|---|---|
| 🔍 单个解析 | 单 URL 解析 + 就地下载。支持 B站/抖音/微博（完整解析器）+ 小红书/快手/好看/微视/梨视频/皮皮搞笑 |
| 📋 批量导入 | 多行 URL 批量解析（每行一条，`Ctrl+Enter` 触发），支持暂停、从文件导入、勾选批量下载 |
| 👥 账号获取 | 按账号批量采集最新内容。支持全部/选中/按组采集、条数限制（0=无上限）、定时获取、增量模式、限流 |
| 📦 已获取 | 四栏分平台（抖音/B站/微博/其他）浏览已采集数据，默认按发布日期倒序，支持勾选导出/下载 |
| 🎁 B站资源 | 8 个平级功能 tab：🗃 表情包搜索（需登录）/ 🔍 装扮搜索 / 🆔 装扮表情包 / 📺 直播表情 / 👕 装扮ID详情 / 🎁 收藏集 / 👤 空间 / 🔍 综合搜索（视频结果一键跳「单个解析」） |

- 已获取数据**持久化到 SQLite**（`config/collected.db`），重启自动恢复，跨页面共享
- 导出格式：JSON / XLSX / MD（可多选，默认全开）

### 2. 🗂 资产管理

三 Tab 页面：

- **已获取资源**：扫 `download/已获取数据/` 历史导出，平台/关键词/状态筛选、时间排序、分页渲染、二级详情窗、导出预览图
- **数据库浏览**：浏览增量采集库（`increment.db`），按平台/作者/关键词查询，勾选导出 JSON、删除
- **本地资源**：目录扫描 + 缩略图懒加载，支持扫描上限、分批间隔、增量模式

### 3. 🎬 录播工具箱

| 页面 | 说明 |
|---|---|
| 📂 自动整理 | 按文件名日期把散落文件归入 `YYYYMMDD` 文件夹，支持扫描预览 |
| 🔄 视频转换 | 批量转封装/转码，无损优先（`ffmpeg -c copy`），失败可回退 `libx264` 转码；含缓存、并发、重试、保留时间戳 |
| ✏️ 重命名 | 三栏交互重命名（日期文件夹 / 账户+类型 / 视频信息+标题），`Enter` 确认、`Esc` 跳过，可写入版权元数据 |
| 🎵 音频提取 | 视频批量提取 `.m4a`（`-acodec copy` 无损），默认勾选当天（北京时区） |
| 📝 字幕转换 | `.bcc/.json/.ass/.srt/.txt` → 纯文字 TXT / 带时间轴 TXT / LRC / ASS / SRT |
| 🚀 继续操作 | 外部软件/链接启动器（4×3 分页面板、滚轮翻页、图标管理、进程树跟随） |
| 📅 日历浏览 | 录播文件 年 → 月 → 日 三级日历浏览 |
| 🚚 文件迁移 | 日期文件夹迁移到目标目录，支持剪切/复制、文件级合并、重复项逐项处理 |

### 4. 🎫 票务购物

| 页面 | 说明 |
|---|---|
| 🎫 票务解析 | 大麦 / 秀动 / B站会员购 演出搜索与详情抓取，含票档、场次、场馆、宣传图下载 |
| 🛒 购物解析 | 淘宝店铺商品列表 + 单商品详情抓取，支持 SKU、主图下载、单商品 Excel 五 Sheet 导出 |

### 5. 🎵 音乐服务

| 页面 | 说明 |
|---|---|
| 🎵 音乐信息获取 | 酷狗 / QQ音乐 / 网易云 / 酷我 / 5sing 五平台并行对比搜索，勾选导出详情 |
| 🎧 音乐下载 | 五平台下载工具箱（网易云支持 search/song/playlist/**album** 四种模式，7 档音质；其余三模式） |
| 🔓 音乐解密 | 加密音乐解密，支持 `ncm / qmc0-8 / mflac / mgg / kgm / vpr / kwm / xm / tm0-6 / x2m / x3m` 等 30+ 种格式 |

### 6. 👥 账户追踪

| 页面 | 说明 |
|---|---|
| 🎼 音乐账号追踪 | 音乐平台账号长期指标追踪（网易云/QQ音乐/酷狗/酷我/5sing），每账号一卡片，变化提示**涨绿跌红**，支持定时采集、按组导出 PNG |
| 📈 网媒账号追踪 | 单账号长期指标追踪（粉丝/关注/获赞/内容数/简介/认证/等级），每账号一卡片，变化提示**涨绿跌红**，支持定时采集、按组导出 PNG |

### 7. 🔴 直播弹幕录制

| 页面 | 说明 |
|---|---|
| 📺 抖音弹幕姬 | 抖音直播间实时接入：弹幕/礼物/进场/点赞/关注抓取、四种模式（正常/仅视频/仅弹幕/回放）、直播预览、录制、回放包导出/导入/合并、开播监控、打赏榜 |
| 📺 B站弹幕姬 | B站直播实时接入：弹幕/礼物/SC/舰长等抓取，**纯 HTTP FLV 流式录制（不依赖 ffmpeg）**，可同步录制弹幕为 BililiveRecorder 同款 XML，断流自动重连续录 |

### 8. 🧰 实用工具

| 页面 | 说明 |
|---|---|
| 🖼 图片裁切 | 四边参考线裁图（移植自 imgcut）：多图槽位导入、精细拖线与边界吸附、放大镜、撤销/重做，单张/批量导出 |
| 🎨 套版封面制作 | 底图 + 三字段文字（标题/日期/备注）封面生成：字体/字号/颜色/对齐独立控制、预览拖动定位、模板记忆、导出 PNG/JPG |
| 🎞 媒体筛选分类 | 图片/视频三栏分类工作台（移植自 image-sorter）：键盘工作流、MD5 四层去重、视频/LIVP 播放、撤销/复检、页内重命名 |
| 🗜 媒体压缩 | 图片/视频批量压缩：两套预设四档 + 自定义九项参数、实时大小预估、后台队列、产物自动标记已分类 |
| ✏️ 批量重命名 | zRenamer 风格，8 类规则叠加（整体/替换/添加/删除/序号/模板/文本模式/选项）+ 双列预览 + 撤销 |
| 🕓 文件时间修改 | 批量改创建/修改/访问时间，支持指定时间、偏移量（`+2d` `-210m`）、按文件名提取时间 |
| 🏷 音乐标签 | 原生音频标签编辑器（16 种格式），支持网络匹配、批量匹配、XLSX 表格匹配、封面与歌词编辑 |
| 🎤 歌词转换 | `.lrc/.krc/.ksc/.qrc/.trc/.elrc` → 纯文字 / LRC / SRT / ASS / KSC 逐字，支持直接粘贴歌词文本；附 KRCD 工具（酷狗 KRC 加密 ↔ KRCD 明文互转） |

### 9. ⚙️ 全局

- **全局设置**：默认工作文件夹、统一导出目录、**主题自动切换**（跟随系统深浅色 / 定时黑白，二选一）、配置与依赖检查（清理 `__pycache__`、**FFmpeg 自定义位置**——系统 PATH 优先、未找到时回退自定义路径、依赖缺失标红 + 一键安装）、关于

### 全局特性

- **主题**：浅色/深色手动切换（侧栏底部主题方块，深色显 🌙 / 浅色显 ☀️） + **跟随系统深浅色**（Windows 深浅色变化每 10 秒同步）或 **定时自动切换**（默认 07:00 浅色 / 19:00 深色，到点即变，支持跨午夜），两种自动模式互斥
- **Cookie 管理**：11 平台 Cookie 统一存储，**Fernet 加密**（密钥派生自机器 GUID）。扫码登录 3 家（B站 / 网易云 / QQ音乐），内嵌浏览器登录 10 家（B站、抖音、微博、QQ音乐、酷狗、酷我、5sing、小红书、快手、淘宝；系统 Edge/Chrome 嵌入软件窗口，自动捕获含 HttpOnly 的全量 Cookie）
- **快捷键**：`Enter` 统一分发到当前页主操作，`Esc` 分发到当前页取消操作
- **全局拖放**：拖入文件夹=切换工作目录（字幕/歌词/音乐标签页则加入文件列表；弹幕姬页拖入 `.zip`=进入回放模式）
- **Toast 提示**：右下角淡入淡出提示
- **隐私保护**：`config/`、`download/`、`logs/`、追踪账号数据均 gitignore，不进仓库、不进安装包

---

## 二、架构设计

```
┌─────────────────────────────────────────────────────────────┐
│  main.py    入口：无控制台守卫 / 子进程 CREATE_NO_WINDOW /    │
│             Qt 降噪 / 配置初始化 / 日志初始化 / ffmpeg 检测    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  GUI 层  src/gui_qt/                                         │
│    main_window.py   侧栏导航 + 懒加载 + 页面切换/目录广播      │
│    theme.py         浅色/深色 QSS + 内联样式自动重映射 + 定时  │
│    common.py        线程桥(run_bg/post_to_main) + 共享状态    │
│    pages/*.py       33 个功能页（BasePage 子类）              │
└──────────────────────────┬──────────────────────────────────┘
                           │ 单向依赖（引擎不反向依赖 GUI）
┌──────────────────────────▼──────────────────────────────────┐
│  引擎层  src/                                                 │
│    toolbox/     录播工具箱引擎（转换/音频/整理/重命名/字幕/歌词/ │
│                 直播录制/增量库/配置管理）                     │
│    platforms/   平台解析（B站/抖音/微博/快手/小红书）           │
│    tracker/     账号追踪采集（5 平台采集器 + 存档 + PNG 导出）  │
│    music/       音乐信息聚合（5 源搜索/下载/导出/标签读写）      │
│    netease/     网易云下载（eapi 加密 + 扫码登录）             │
│    bili/        B站底层（WBI 签名 / 资源 API / 链接解析 / 下载）│
│    exporters/   视频数据导出（JSON / XLSX / MD）              │
│    auth.py      Cookie 仓库（11 平台，Fernet 加密）            │
│    media_downloader.py / multi_downloader.py /               │
│    bilibili_download.py / shopping.py / tickets.py           │
└─────────────────────────────────────────────────────────────┘
```

### 关键设计约定

1. **分层解耦**：`toolbox/` 只依赖标准库 + 系统 ffmpeg/ffprobe，不依赖 GUI、不依赖网络框架
2. **懒加载**：页面在首次点击时才 `PAGE_CLASSES[key](app)` 实例化；页面内重依赖（PIL、openpyxl 等）在函数内延迟 import，控制启动开销
3. **线程桥**：后台任务统一走 `common.run_bg(fn, on_done)`，`on_done(result, err)` 自动排回主线程；轻量进度用 `post_to_main`
4. **页面生命周期钩子**（`BasePage` 可选实现，由主窗口反射调用）：

   | 钩子 | 触发时机 |
   |---|---|
   | `on_show()` | 每次切换到该页（刷新数据） |
   | `on_hide()` | 每次切走（停后台任务） |
   | `shutdown()` | 软件退出（优先于 `on_hide`） |
   | `on_return_press()` | `Enter` 键 |
   | `on_escape_press()` | `Esc` 键 |
   | `on_dir_changed(path)` | 全局工作目录变更广播 |
   | `refresh_count()` | 已获取数据条数变更广播 |
   | `add_dropped_files/files/folder()` | 拖放路由 |

5. **新增页面**：在 `gui_qt/pages/` 写一个 `BasePage` 子类 → 在 `main_window.py` 的 `PAGE_CLASSES` 注册 → 在 `NAV_GROUPS` 加导航项

### 平台的三级/四级回退策略

解析器普遍采用「游客 → 兜底 → Cookie」的降级链，最大限度免登录：

| 平台 | 单作品 | 用户作品列表 | 加密/风控参数 |
|---|---|---|---|
| **B站** | 游客 HTTP API（**WBI 签名**）→ Cookie | 游客 → Playwright → Cookie | WBI（`w_rid` + `wts`） |
| **抖音** | 页面解析 → Cookie | **必须 Playwright** → Cookie | `a_bogus`（走 Node.js） |
| **微博** | m.weibo.cn API → 页面 → Cookie | 访客通行证 → 页面 → Cookie → Playwright | 无（自动获取访客通行证） |
| **快手** | 游客票据（可用） | **必须 Cookie** | 无 |
| **小红书** | 游客（可用） | **必须 Cookie** | `xsec_token`（会失效） |

---

## 三、目录结构

```
PySide6/
├── main.py                       # PySide6 入口
├── build.py                     # 一键打包（PyInstaller + Inno Setup + 隐私扫描）
├── check_deps.py                # 依赖自检 / 自动安装
├── requirements.txt
├── FengMingToolbox.spec         # PyInstaller spec（未被 git 跟踪，*.spec 已忽略）
├── 启动工具箱(无窗口).vbs        # 开发模式无黑窗启动
│
├── src/                         # 源码主目录
│   ├── version.py               # 版本号唯一来源（APP_NAME / APP_VERSION / APP_PUBLISHER）
│   ├── auth.py                  # Cookie 仓库（11 平台，Fernet 加密 + 机器 GUID 派生密钥）
│   ├── accounts_manager.py      # 「账号获取」页账号/分组管理（config/accounts.txt）
│   ├── app_logger.py            # 日志体系（logs/app_*.log，Stdout Tee，自动轮转）
│   ├── media_downloader.py      # 媒体批量落盘（图片/live图/视频，反爬限速）
│   ├── multi_downloader.py      # 7 平台视频解析 + a_bogus 签名入口（调 Node）
│   ├── bilibili_download.py     # B站视频下载（DASH 流 + ffmpeg 合并）
│   ├── shopping.py              # 淘宝店铺/商品解析
│   ├── tickets.py               # 大麦/秀动/B站会员购 演出解析
│   ├── music_unlock.py          # 加密音乐解密（30+ 格式）
│   │
│   ├── gui_qt/                  # ★ PySide6 GUI 层
│   │   ├── main_window.py       #   主窗口：导航 + 懒加载 + 31 页注册 + 主题定时器
│   │   ├── theme.py             #   浅色/深色主题 + 内联样式自动重映射 + 定时切换
│   │   ├── common.py            #   线程桥 + 共享状态 + 已获取持久化(SQLite)
│   │   ├── download_ops.py      #   下载共享操作（解析/分组/画质映射/B站下载主流程）
│   │   ├── cookie_manager.py    #   Cookie 管理对话框（扫码 / 内嵌浏览器）
│   │   ├── row_widgets.py       #   整行可点击复选组件 RowCheckList
│   │   ├── process_tree.py      #   Windows Job Object 进程树（父关子关）
│   │   ├── toast.py             #   右下角 toast
│   │   ├── toolbox_common.py    #   日期文件夹常量与工具
│   │   └── pages/               #   31 个页面模块（30 个挂侧栏 + 1 个孤儿页）
│   │
│   ├── toolbox/                 # 录播工具箱引擎（仅依赖标准库 + ffmpeg）
│   │   ├── config_manager.py    #   配置中枢（config.json / cache.json / 导出目录）
│   │   ├── utils.py             #   文件名清洗 / ffmpeg 错误分类 / 视频信息探测
│   │   ├── convert_engine.py    #   视频转换（无损封装 → 转码回退 + 缓存）
│   │   ├── audio_engine.py      #   音频提取 + 安全删除（回收站）
│   │   ├── organize_engine.py   #   按日期自动整理
│   │   ├── rename_engine.py     #   重命名解析辅助（标题/时间/日期文件夹扫描）
│   │   ├── subtitle_engine.py   #   字幕格式互转
│   │   ├── lyrics_engine.py     #   歌词格式互转 + 酷狗 KRC 加解密
│   │   ├── live_video.py        #   ffmpeg 拉流预览 + FLV 录制
│   │   ├── bili_live.py         #   B站直播录制（纯 HTTP FLV + WS 弹幕 → XML）
│   │   ├── douyin_live.py       #   抖音直播抓取（Playwright 签名 + WS protobuf）
│   │   ├── increment_db.py      #   增量采集 SQLite 库（分平台分表 + 两层去重）
│   │   ├── a_bogus.js           #   抖音 a_bogus 签名算法（JS）
│   │   ├── abogus_cli.js        #   签名 CLI 包装（Python subprocess 调用）
│   │   └── _unlock_keys.json    #   音乐解锁密钥表
│   │
│   ├── platforms/               # 平台解析层
│   │   ├── base.py              #   VideoInfo 数据结构 + PlatformBase + URLRecognizer
│   │   └── bilibili.py / douyin.py / douyin_playwright.py /
│   │       weibo.py / kuaishou.py / xiaohongshu.py
│   │
│   ├── tracker/                 # 账号追踪
│   │   ├── config.py  storage.py  accounts.py  whoami.py  export_png.py
│   │   ├── collectors/         # 5 个采集器（bilibili/douyin/weibo/kuaishou/xiaohongshu）
│   │   └── data/               # 按平台存档 records.{json,xlsx}（gitignore）
│   │
│   ├── music/                   # 音乐信息聚合
│   │   ├── base_scraper.py  models.py  utils.py  downloader.py  exporter.py  tags_io.py
│   │   └── scrapers/           # fivesing / kugou / kuwo / netease / qq_music
│   │
│   ├── netease/                 # 网易云（eapi 加密 + 扫码登录 + 下载器）
│   │   └── cookie_manager.py  music_api.py  music_downloader.py
│   │
│   ├── bili/                    # B站底层（wbi / api / links / download）
│   └── exporters/               # 视频数据导出（JSON / XLSX / MD）
│
├── config/                      # 运行时配置（gitignore，不进仓库/安装包）
├── download/                    # 运行时产出（gitignore）
├── logs/                        # 运行日志（gitignore，上限 50 个自动轮转）
└── dist/  build/                # 打包产物（gitignore）
```

---

## 四、环境要求

### 必需

| 项 | 要求 | 说明 |
|---|---|---|
| **操作系统** | Windows 10/11（x64） | 部分功能用了 Win32 API（Job Object、创建时间、注册表 MachineGuid） |
| **Python** | 3.9+（推荐 **3.10 ~ 3.12**，实测 3.12） | 用到了 `zoneinfo`、内置泛型 `list[]`/`dict[]` |
| **PySide6** | ≥ 6.5.0 | GUI 框架（Essentials 即可，浏览器登录走系统 Edge/Chrome，无需 Addons 的 WebEngine） |

Python 依赖见 `requirements.txt`：

```
PySide6>=6.5.0
httpx>=0.27.0      rich>=13.0.0      openpyxl>=3.1.0
pydantic>=2.0.0    pydantic-core>=2.0.0,<2.47
requests>=2.28.0   cryptography>=40.0.0
pycryptodome       qrcode>=8.0       mutagen>=1.46.0
playwright>=1.40.0          # 可选，缺失时相关功能自动降级
```

> `pydantic-core` 锁 `<2.47`：pydantic 2.13.x 要求 `pydantic-core==2.46.x`，装高了会 `SystemError`。

### 可选（缺失时相关功能降级或不可用）

| 依赖 | 影响的功能 | 安装方式 |
|---|---|---|
| **ffmpeg / ffprobe** | 视频转换、音频提取、B站视频下载、直播预览与录制、视频抽帧 | 下载 https://ffmpeg.org/download.html 并加入 PATH |
| **Node.js v18+** | 抖音 `a_bogus` 签名（抖音解析/采集/Cookie 验证） | 需 `node` 在 PATH 或常见安装路径 |
| **Playwright** | 抖音直播抓取、抖音用户作品列表、大麦关键词搜索、Cookie 浏览器登录、部分兜底解析 | `pip install playwright`；浏览器优先用**系统 Edge/Chrome**（Win10/11 自带，无需打包），都没有时可在 Cookie 管理页按需下载采集引擎（约 91 MB） |
| **websocket-client** | B站弹幕录制、抖音直播 WebSocket | `pip install websocket-client` |
| **Pillow / openpyxl / send2trash** | 缩略图、PNG 导出、Excel 导出、回收站删除 | 通常随依赖链安装 |
| **pywin32** | 精确设置文件创建时间（缺失时回退 ctypes） | `pip install pywin32` |
| **Inno Setup 6** | 仅**打包安装器**时需要 | `winget install JRSoftware.InnoSetup` |

---

## 五、安装步骤

### 1. 获取代码

```bash
cd D:\PythonApps\all
git clone https://github.com/FmjjTool/FMJJRecordedBroadcast.git PySide6      # 或解压已有源码
cd PySide6
```

### 2. 创建并激活虚拟环境（推荐）

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

国内网络可加源：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 依赖自检

```bash
python check_deps.py           # 检查 + 自动安装缺失/版本不符的依赖
python check_deps.py --check   # 只报告，不安装
```

自检分两阶段：**先校验版本 → 后 import**。原因是 `pydantic_core` 这类 C 扩展一旦被 import 进内存，pip 替换磁盘文件也不生效，必须在 import 前把版本修对。

### 5. 安装可选依赖

```bash
# ffmpeg：下载后解压，把 bin 目录加入 PATH
ffmpeg -version                # 验证

# Playwright（抖音直播、部分解析兜底、浏览器登录必需）
pip install playwright
# 浏览器内核：优先系统 Edge/Chrome（无需下载）；无系统浏览器时按需下载采集引擎
playwright install chromium-headless-shell

# websocket-client（B站弹幕录制、抖音直播）
pip install websocket-client
```

---

## 六、配置说明

配置目录由 `get_config_dir()` 决定：优先 `<应用根>/config`，**不可写时（如装在 Program Files）自动回退 `%APPDATA%\FengMingToolbox\config`**。

首次运行由 `init_all_configs()` 自动创建默认配置，并迁移旧版散落配置文件。

| 文件 | 内容 |
|---|---|
| `config.json` | 4 个顶层键：<br>• `convert`（25 键）——输入/输出扩展名、输出目录、转码后删源、重试、编码回退（`libx264` / CRF 18 / preset fast）、并发、保留元数据与时间戳、缓存开关、`work_dir`、`subtitle_formats`、启动器项、迁移目标目录、`max_log_files`(50)、`theme`、`theme_schedule_*`（定时主题）、`remark_info`（弹幕姬备注）<br>• `rename`（16 键）——账户列表、直播类型、视频/字幕后缀、跳过子文件夹、字幕匹配阈值(0.6)、`extra_info`、`platform_list`<br>• `work_dir`（顶层）<br>• `subtitle_formats`（顶层，默认 `["text","timed_text","lrc"]`） |
| `cache.json` | `convert_cache`（转换缓存，按 size+mtime 判定命中）/ `rename_cache` / `rename_history` |
| `cookies.json` | Cookie 仓库（Fernet 加密）。4 键：`cookies`（`{平台: "enc:密文"}`）/ `cookie_times` / `updated_at` / `cookie_enabled`（总开关） |
| `douyin_live.json` | 抖音直播页设置：监控间隔(30s)、统计间隔(1s)、轮询间隔(60s)、保存开关、上次房间号、模式等 |
| `douyin_live_rooms.json` | 房间号 → roomId 缓存（避免重复解析） |
| `accounts.txt` | 「账号获取」页账号列表，每行一条，pipe 分隔 |
| `groups.json` | 账号分组，扁平映射 `{"11": "默认分组"}`。**组号必须是重复双位数**（`11/22/.../99`） |
| `collected.db` | **SQLite**。已获取数据持久化，单表 `videos(dedup_key PK, platform, created_at, data)`，WAL 模式 |
| `increment.db` | **SQLite**。增量采集库，**每平台一张表**（23 列）+ 内部表 `_content_keys` / `_meta`。两层去重：表内 `dedup_key` 唯一 + 跨表 `content_key` 全局唯一（同内容跨平台只留最早一条） |
| `rename_log.log` | 重命名操作日志 |

**账号行格式**（`accounts.txt`）：

```
# 注释行
https://example.com                            # 纯 URL（兼容旧格式）
账号名|https://url                              # 2 段
账号名|https://url|备注                          # 3 段
11,22|组名|账号名|https://url                    # 4 段（多组用逗号）
11,22|组名|账号名|https://url|备注                # 5 段
... |video | dynamic | all                      # 尾段为采集类型标记
```

> ⚠️ **注意重名文件**：`src/tracker/groups.json` 是**账号追踪页**的分组，与 `config/groups.json`（账号获取页）是**两个不同的文件**。打包后追踪页数据落在 `config/`，源码运行时落在 `src/tracker/`。

### 导出目录

默认 `<应用根>/download`，可在「⚙️ 全局设置」修改。子目录分类：

| 类别 | 子文件夹 |
|---|---|
| `subtitle` | 字幕文件 |
| `music` | 歌曲文件 |
| `music_info` | 音乐信息 |
| `tracker` | 账号追踪数据 |
| `video_data` | 已获取数据 |
| `danmaku` | 弹幕文件 |

---

## 七、启动与运行

### 开发模式

```bash
python main.py
```

**无 CMD 黑窗**：双击 `启动工具箱(无窗口).vbs`（优先用 `pythonw` 启动）。

打包后的 exe 本身已 `--windowed`，无黑窗。

### 窗口规格

- 默认 1120×700；屏幕可用区域不足时自动收缩
- 最小尺寸 880×560
- 左侧 210px 导航（可滚动，「⚙️ 全局」固定在底部）+ 右侧内容区

### 启动流程（`main.py`）

1. `_headless_io()` —— 无控制台时把 `stdout/stderr` 补空流，避免 `print`/`logging` 崩溃
2. `_suppress_child_consoles()` —— 给 `subprocess.Popen` 补 `CREATE_NO_WINDOW`，ffmpeg/node 不弹黑窗
3. `_minimize_console()` —— 开发模式自动最小化 cmd（ConPTY 伪控制台用 `SW_HIDE` 整体隐藏）
4. `init_all_configs()` —— 初始化/迁移配置
5. `setup_logging(console=False)` —— 日志只进 `logs/app_*.log`，不刷控制台
6. 检测浏览器环境（注册表读 Edge/Chrome 版本记日志；Edge/Chrome/采集引擎全缺时提示一次）
7. 安装 Qt 降噪过滤器（吞掉字体回退等已知无害警告）
8. `theme.init_theme() → install_auto_remap() → apply_theme()`
9. 创建 `MainWindow` 并 show
10. ffmpeg 检测（缺失弹警告，不阻断启动）

### 停止

直接关闭窗口。`closeEvent` 会对**所有已加载页面**调用 `shutdown()`（优先）或 `on_hide()` 全停。

---

## 八、使用示例

### 示例 1：解析并下载一批链接

1. 「📋 批量导入」→ 粘贴多个 URL（每行一条）
2. `Ctrl+Enter` 或点「批量解析」
3. 勾选需要的条目 → 选择画质 → 「⬇ 下载勾选」
4. 去「📦 已获取」查看/导出（JSON / XLSX / MD）

### 示例 2：录播文件后期流水线

1. 「⚙️ 全局设置」设置「默认工作文件夹」为录播根目录（含 `YYYYMMDD` 子文件夹）
2. 「📂 自动整理」——把散落文件归入日期文件夹
3. 「🔄 视频转换」——批量 FLV → MP4（无损优先）
4. 「✏️ 重命名」——三栏交互命名，写入版权元数据
5. 「🎵 音频提取」——按需提取 `.m4a`
6. 「📝 字幕转换」——`.bcc` → SRT / LRC
7. 「🚚 文件迁移」——迁移到归档目录

> 也可直接把文件夹**拖到窗口**上切换工作目录。

### 示例 3：直播间弹幕抓取与录制

1. 左下角「🍪 管理 Cookie」→ 登录抖音（**礼物消息必须登录**，游客只有弹幕）
2. 「📺 弹幕姬」→ 输入房间号 → ▶ 连接
   - 需要 Playwright + 浏览器内核（签名用，系统 Edge/Chrome 即可）
   - 遇到验证码会弹窗，手动过验证后点「继续」
3. 需要录像：点「⏺ 自动录播」，或去「🔴 抖音录播姬」独立录制
4. 结束后「💾 保存弹幕」/「📦 导出回放包」

### 示例 4：账号长期追踪

1. 「📈 账号追踪」→「设置」Tab 添加账号（名字 + 平台 + 链接 + 组号）
2. 回「功能」Tab → 点账号卡片「采集」
3. 开启「⏰ 定时采集」可长期无人值守
4. 采集满两次后可「📷 导出变化对比 PNG」（涨绿跌红）；「按组导出」把整组账号堆成一张长图

### 示例 5：音乐五平台搜刮与下载

1. 「🎵 音乐信息获取」→ 输入歌手/歌曲名 →「🔍 搜索五平台」
2. 勾选想要的歌曲 →「📥 获取选中歌曲详情并导出」（JSON + XLSX + MD）
3. 需要音频文件：去「🎧 音乐下载」选择平台与模式搜索下载
   - 可下载：酷狗（128k）、酷我（320k）、5sing（hq 192k）、网易云（需登录）
   - QQ音乐**仅解析**，直链接口需 VIP
   - 下载失败会自动**智能换源**（酷我 → 酷狗 → 网易云）
4. 手上有加密文件：去「🔓 音乐解密」

### 示例 6：Cookie 登录

1. 左下角「🍪 管理 Cookie」
2. 选平台 → **扫码**（B站/网易云/QQ音乐）或**浏览器登录**（其余）
3. 登录完成后点「✅ 已获取（保存 Cookie）」
4. 状态显示 `🟢 已登录 · 用户名` 即成功

> 浏览器登录用**平台级共享无痕 profile**，已存 Cookie 会回种实现自动登录。

---

## 九、打包发布

### 前置

```bash
pip install pyinstaller
winget install JRSoftware.InnoSetup      # 仅生成安装器时需要
```

### 命令

```bash
python build.py                    # 完整：PyInstaller 绿色版 → 隐私扫描 → Inno Setup 安装器
python build.py --no-installer     # 只出绿色版
python build.py --skip-build       # 复用 dist/，仅重编安装器
python build.py --name 自定义名称   # 自定义产物目录 / exe 名
```

### 产物

| 产物 | 路径 |
|---|---|
| 绿色版 | `dist/<名称>/<名称>.exe`（无 CMD 窗口，双击即用） |
| 安装器 | `dist/installer/<名称>_Setup_<版本>.exe` |

### PyInstaller 关键配置

```
--onedir --windowed --paths src
--exclude-module tkinter PyQt5 PyQt5.sip matplotlib IPython  # 死重排除
--hidden-import qrcode                                        # 函数内动态 import
--add-data src/toolbox/a_bogus.js;toolbox
--add-data src/toolbox/abogus_cli.js;toolbox
--add-data src/toolbox/_unlock_keys.json;toolbox
打包后 prune()：剥离 WebEngine 调试资源 / 未用 Qt DLL（白名单）/
QML 插件树 / Playwright 内置浏览器 / 53 种语言文件（只留中英）
```

### 安装器特性

- 自选安装位置（默认 `{autopf}\FengMingToolbox`）
- 写注册表 `HKLM\Software\FengMingToolbox`（`InstallPath`/`Version`/`Publisher`）+ 控制面板卸载条目
- `[Dirs]` 预建 `logs`/`download` 并 `Permissions: users-modify`（解决 Program Files 只读）
- 开始菜单/桌面图标（桌面图标默认勾选）
- 完整卸载**默认保留用户数据**

### 隐私扫描（打包后自动执行）

扫描 `dist/<名称>/`，命中即中止打包：

- **文件黑名单**：`cookies.json`、`config.json`、`cache.json`、`accounts.json`、`groups.json`、`accounts.txt`、`rename_log.log`、`cookies.json.bak`
- **文件名正则**：`^app_\d{8}_\d{6}.*\.log$`
- **目录黑名单**：`config`、`logs`、`download`、`__pycache__`

这样保证包内只有代码模块 + 引擎资源，首次运行由 `init_all_configs()` 重建默认配置。

### 打包后行为差异

- `get_app_root()` 返回 **exe 所在目录**（源码模式返回项目根）
- 追踪账号数据自动重定向到 exe 旁 `config/`（规避 Program Files 只读）
- `check_deps.py` 检测到 `sys.frozen` 直接放行

---

## 十、常见问题

### 启动类

**Q：`python check_deps.py` 报「缺失 PySide6」，但程序能正常启动？**
A：已知误报。部分安装方式（如 6.11.x）把 Qt 拆成 `PySide6_Essentials` + `PySide6_Addons` 两个发行包，**没有名为 `PySide6` 的元数据条目**，而 `check_deps.py` 阶段一用 `importlib.metadata.version()` 查版本，查不到就判为缺失。校验真实可用性请用：

```bash
python -c "import PySide6; print(PySide6.__version__)"
```

只要能打印出版本号就不影响运行。

**Q：提示「未检测到 ffmpeg」？**
A：视频转换、音频提取、B站下载、直播预览/录制都依赖 ffmpeg。下载 https://ffmpeg.org/download.html 解压后把 `bin` 加入 PATH，重启软件。查找顺序：`PATH` → `D:/Programs/ffmpeg/bin/ffmpeg.exe` → `C:/ffmpeg/bin/ffmpeg.exe`，**优先选带 `rawvideo` 封装的完整版**。

**Q：`pip install` 报 `SystemError` 提到 pydantic-core？**
A：pydantic 与 pydantic-core 版本不匹配。执行 `python check_deps.py`，它会自动从报错文本提取目标版本并精确安装（兜底 `pydantic-core==2.46.4`）。装完重启终端。

**Q：Cookie 浏览器登录窗口打不开？**
A：内嵌登录需要系统 **Edge 或 Chrome**（Win10/11 自带）。两者都没有时可在 Cookie 管理页按需下载采集引擎；也可改用「输入」手动粘贴 Cookie。

**Q：控制台一堆 `OpenType support missing` 警告？**
A：已默认静默（Qt 日志过滤规则 + 消息处理器双重拦截）。这是中文系统字体链不含印度系文字导致的无害警告。

**Q：启动时弹出浏览器/登录窗口？**
A：正常的 Cookie 浏览器登录流程，登录完点「✅ 已获取」即可。

### 功能类

**Q：抖音解析/采集失败，提示「未找到 node 环境」？**
A：抖音 `a_bogus` 签名需要 Node.js。安装 Node 18+ 并确保 `node` 在 PATH。

**Q：抖音直播连不上/礼物数为 0？**
A：① 礼物消息**必须登录抖音 Cookie**（游客只有弹幕）；② 需要 Playwright + 浏览器内核（系统 Edge/Chrome 即可）；③ 遇到验证码会弹窗，手动过验证后点「继续」。

**Q：快手/小红书主页作品列表抓不到？**
A：这两个平台的**主页/列表必须 Cookie**（游客被登录墙或频控拦）。单作品/笔记详情游客可用。

**Q：B站采集返回 -352 / -403？**
A：WBI 签名问题。代码会自动 `refresh_wbi()` 重试一次；仍失败则去 Cookie 管理登录 B站后重试。

**Q：下载很慢，像卡住了？**
A：B站下载前会**随机等待 3~60 秒**（防反爬硬性条件），属正常行为。GUI 手动下载抖音等平台已改用 0.5~1.5 秒小区间。

**Q：转换后的视频没有声音/音画不同步？**
A：无损封装会自动加 `-bsf:a aac_adtstoasc` 处理 AAC。若源文件时间戳异常（直播录屏常见 `non-monotonous DTS`），可尝试勾选「无损失败转码」走转码路径。

**Q：换电脑后 Cookie 全失效？**
A：Cookie 用 Fernet 加密，密钥派生自**本机 MachineGuid**。换机后密文无法解开，需重新登录（不会报错，只是读为空）。

**Q：追踪数据有两个 `groups.json`？**
A：是的。`config/groups.json` 是「账号获取」页的分组，`src/tracker/groups.json` 是「账号追踪」页的分组，两者独立。打包后后者也落到 `config/`。

### 数据类

**Q：数据存在哪？能备份吗？**
A：`config/`（配置 + Cookie + 数据库）和 `download/`（产出）。备份这两个目录即可。它们都 gitignore，不进仓库也不进安装包。

**Q：日志文件太多？**
A：默认上限 50 个（`config.json` 的 `convert.max_log_files`），超出自动删最旧。可在设置页调整，或点「🧹 清理 Python 缓存」。

**Q：从老项目迁移数据会覆盖新数据吗？**
A：不会。普通文件遇到同名则**跳过**（新项目数据优先）；xlsx 是**按行合并去重**，且合并前自动备份为 `.xlsx.bak`。

---

## 十一、维护信息

### 版本

版本号唯一来源：`src/version.py`

```python
APP_NAME         = "FengMingToolbox"
APP_DISPLAY_NAME = "封茗囧菌数据汇总库 统一维护工具箱"
APP_VERSION      = "2.8.4"
APP_PUBLISHER    = "歆慕"
```

窗口标题、打包产物名、Inno Setup 安装器、注册表均从这里读取。**发版只需改这一个文件**。

### 日志

- 位置：`<应用根>/logs/app_YYYYMMDD_HHMMSS_mmm.log`
- 每次运行一个独立文件，上限 50 个自动轮转
- `sys.stdout`/`sys.stderr` 被 Tee 到文件，自动剥离 ANSI 颜色码，逐行 flush
- 未处理异常由 `sys.excepthook` 写入日志（GUI 模式控制台静默，查日志定位问题）

### 隐私与安全

- Cookie 用 **Fernet 加密**（密钥 = SHA256(`fmjj-cookie-v1:` + 本机 MachineGuid)）
- `config/`、`download/`、`logs/`、追踪账号数据、B站/网易云 Cookie 文件均 gitignore
- 打包后自动隐私扫描，Cookie/配置/账号进包即中止
- Cookie 有效期：微博 30 天，其余平台默认永不过期（`auth.py` 的 `COOKIE_TTL_DAYS`）
- Cookie 总开关可一键停用（`cookie_enabled`），停用后所有采集按游客态走

### 更新日志

#### v2.8.8（2026-09-05）

- **轻量版正式发布（定版）**：v2.8.7 晚间曾以资产替换形式临时上线轻量安装器，本次定版为正式版本线——浏览器架构统一走系统浏览器（Edge → Chrome → 按需下载采集引擎三级降级），安装包不再携带任何浏览器内核，**安装器体积 285.6MB → 90.7MB（缩减 68%）**；启动时自动检测浏览器环境（记日志），无 Edge/Chrome 的机器可在 Cookie 管理页一键下载采集引擎（国内镜像）。
- **定版伴随更新**：应用图标换为小呆形象；工作文件夹/导出文件夹支持锁定（锁定后拖入不改变）；移除外部 dycast-main 启动器页（功能已由内置弹幕姬页替代）。
- **工程**：修复打包白名单漏删 QtNetwork（QtMultimedia 的内部依赖）致成品启动即崩的问题。

#### v2.8.7（2026-09-05）

- **媒体筛选分类页（新功能）**：「🧰 实用工具」组新增图片/视频分类工作台（移植自 image-sorter）——三栏布局（源文件网格 + 文件夹面板 + 文件信息/重命名标签页）、缩略图懒加载、目录扫描/MD5 四层去重管线、分类键盘工作流、撤销/复检、页内设置；QMediaPlayer 视频播放 + LIVP 实况图播放；文件列表两列自适应（窄面板不退化单列）、文件名两行换行 + 路径中段省略号 tooltip、深色模式主题映射适配；缓存清理入口迁移到全局设置（扫描/缩略图/MD5 三缓存一键清理，已分类 MD5 记录保留）。
- **媒体压缩页（新功能）**：压缩功能从筛选页独立成页——批量文件列表（添加/拖放导入）+ 图片/视频两套预设（高质量/均衡/极限/无损 + 自定义）九项参数 + 实时大小预估 + 后台队列；压缩产物 MD5 标记已分类（不再回到筛选列表）、输出目录自动记忆。
- **套版封面制作页（新功能）**：底图 + 三字段文字（标题/日期/备注）封面生成器——字体/字号/颜色/粗斜体/对齐独立控制、同步样式单项锁定、全参数比例化存储（1080p/2K/4K 布局一致）、QTextLayout 自动换行 + 溢出缩字号、预览区鼠标拖动定位、导出 PNG/JPG 到 `download/套版封面/`、模板 JSON 记忆样式与底图；底图选择器 168px 方块平铺（后台解码 + 实时筛选 + 双击指定）、预览 10ms 节流实时渲染（防连续输入饿死）、滚轮不改值专用于滚动、字体下拉输入识别、48 色主题色板替代原生 QColorDialog；修复底图缩略图解码必败与颜色弹窗黑底。
- **图片裁切修复**：对齐 imgcut 原版三处差距——悬停显示放大镜、`Ctrl+Shift+Z` 重做、饱和度公式改 `(max-min)/max`。
- **浏览器架构与安装包体积（重磅）**：Playwright 采集统一走系统浏览器（Edge → Chrome → 按需下载采集引擎三级降级），安装包不再携带任何浏览器内核，**安装器体积 285.6MB → 90.6MB（缩减 68%）**；内嵌浏览器登录改为系统浏览器窗口嵌入对话框（保留地址栏，防钓鱼）；启动时自动检测浏览器环境（记日志），无 Edge/Chrome 的机器可在 Cookie 管理页一键下载采集引擎（国内镜像）；QtWebEngine 依赖全链路移除。
- **应用图标**：小呆形象作为应用与安装器图标。
- **设置页**：工作文件夹/导出文件夹支持锁定——锁定后拖入文件夹不再改变，解锁后恢复。
- **弹幕姬**：移除外部 dycast-main 启动器页（功能已由内置弹幕姬页替代）。
- **工程**：新增「总结此次更新」版本发布流程 skill（本次发布由该流程驱动）；修复拖放时 `QWindow` 对象触发 `isAncestorOf` 的 TypeError；修复打包白名单漏删 QtNetwork 致成品启动崩溃。

#### v2.8.6（2026-09-04）

- **图片裁切页（新功能）**：「🧰 实用工具」组新增四边参考线裁图工具（移植自 imgcut）——多图槽位导入（拖拽 / 文件 / 文件夹）、四线拖动（精细模式 20px→1px、Shift 加速）、边界吸附（numpy 边缘画像后台计算）、内容边界自动对齐、像素放大镜（2–64x）、精确坐标输入、撤销 / 重做、右键删除，单张 / 批量导出到 `download/图片裁切/`。
- **图片裁切修复**：放大镜定位规则重做——源窗口不再夹取图片边界（线到边缘显示越界黑作为到边指示）、放大镜移入裁切区内侧不再遮挡遮罩、窄 / 扁选区放不下时整体移到裁切区外保证选区可见；修复画布参考线渲染中断与放大镜黑块。
- **设置页**：内置「封茗囧菌直播总结」AI Skill（设置页可下载导出）；页面改骨架屏加载，打开不再卡顿。
- **抖音解析修复**：分享口令整段粘贴（口令+说明文字一起复制）解析失败；验证码中间页污染解析结果。
- **工程**：发布流程 skill 沉淀首次实战经验（GCM 直调取凭据 / ps1 纯 ASCII / Release 正文 UTF-8 读入）；删除老项目迁移脚本 `migrate_old_data.py` 及 README 相关说明、`check_deps.py` 取消 git 追踪等死代码清理。

#### v2.8.5（2026-09-03）

- **微博视频解析**：新增 `video.weibo.com/show?fid=` 播放器页链接解析（fid 是媒体对象 ID 而非微博 mid，原正则全部不匹配）——h5 播放器接口游客可用，一次拿全 1080P/720P/480P 直链、作者、封面、播放量等完整信息；同帖 `m.weibo.cn/detail` 链接的视频流兜底修复（url_objects 数据源，此前 page_info 为空拿不到视频）。
- **资产/已获取页微博视频下载修复**：三处根因——平台路由把微博条目误入图片下载分支（只下封面不下视频）、下载构造缺 raw_data（挖不到清晰度直链）、weibocdn 直链带 Expires 签名过期。新增视频条目判定（微博/小红书/快手等统一路由视频下载）+ 下载前重解析（raw_url 不可用时 `detail/{mid}` 兜底），已获取页下载链路接入 `refresh_weibo_urls`。
- **媒体删除与补下**：删除图片/视频后条目保留、可正常补下（视频完整判定覆盖微博等平台，此前被判"完整"遭批量跳过）；删除对话框修复独立媒体条目 file_path 误归"文本"类别，新增删光媒体后条目消失的警告。
- **预览图导出修复**：正文行行重叠错位（`\n` 换行 + 行高度量）与 ▶/emoji 豆腐方框（emoji 正则扩展、混合字体绘制、getlength 排版步进、基线对齐）全面修复；底部水印改为「封茗囧菌数据汇总库 · 维护工具」。
- **工程**：新增「总结此次更新」发布流程 skill（`src/skills/summarize-update`）：推送本地提交 → 版本号 +0.0.1 → 打包 → 更新 README → 推送 GitHub → 上传安装包到 Releases 一键完成。

#### v2.8.4（2026-09-02）

- **主题系统**：新增「跟随系统深浅色」自动模式（注册表检测，与定时切换互斥）；侧栏底部改版——🌙/☀️ 主题方块（30×30）+ ⚙️ 全局设置同排居中，全局设置按钮移至主题下方。
- **全应用小尺寸按钮统一图标化**：修复 emoji 图标看不清问题（全局 QSS 内边距挤压图标绘制区 + 微软雅黑缺 emoji 字形），沉淀 `icon_btn_style` 辅助函数覆盖 10 个文件——批量导入/已获取的全选/反选/清除/导出/下载、启动器、资产页 ↻📥🗑、账号/追踪页 ↑↓✕、弹幕测试页 ▶■⏸⚙🧹；固定宽度文字按钮同步修复。
- **B站资源页重构**：表情包/收藏集/空间综合搜索三页合并为「🎁 B站资源」聚合页并入信息获取组，嵌套 tab 拍平为 8 个平级功能 tab（表情包搜索/装扮搜索/装扮表情包/直播表情/装扮ID详情/收藏集/空间/综合搜索）；剥离与单个解析重复的「视频信息/动态详情」，api 死代码清理。
- **FFmpeg 自定义位置**：全局设置新增「FFmpeg 位置」（支持目录或 exe 全路径），查找顺序系统 PATH → 自定义位置 → 命令名，全部引擎调用点接入（转换/音频/版权写入/B站合并/抽帧）。
- **已获取列表删除功能**：右键「🗑 删除该条目…」单条删除 + 工具栏「🗑 删除所选」批量删除（确认框列明条目数和文件数，数据 + 媒体文件全清）。
- **账号获取/网媒追踪/音乐追踪**：分组列表独立滚动 + ⬆️⬇️ 排序（重排 groups.json 键序）；账号获取保存补 toast 提示、添加按钮改 ➕ 图标。
- **批量导入页**：⏸️ 暂停左侧新增 ❌ 取消按钮（未开始 URL 跳过）；导入/全选/反选/清除/下载全部图标化。
- **功能介绍浮层**：14 页浮层式功能介绍（不推动布局），支持点击空白区域关闭（浮层内部、教程滚动、锚点点击不受影响）；交互重命名页新增「❓ 使用教程」浮层。
- **解析增强**：B站纯 BV/AV 号可直接解析（无需完整 URL）；资产页右键可打开 MD/媒体文件。
- **购物页**：移除右上角「淘宝 Cookie」按钮（Cookie 统一由侧栏管理维护）；解析失败改为状态栏显示完整原因 + Cookie 刷新指引。
- **修复**：恢复「清理 Python 缓存」按钮（曾误下线）；删除旧缓存迁移功能全链路；依赖版本显示双重截断改正则提取完整版本号；深色模式依赖区配色（底色/红字/绿字走主题映射表）；音乐下载/文件迁移页内容区白底+边框（深浅色均正常）。
- **其他**：删除零引用的 `_ref` 参考源码目录（3.4MB）；资产页两处刷新按钮改 🔄 图标。

#### v2.8.3（2026-09-01）

- **抖音 / B 站弹幕姬（test_page / bili_danmaku）**：删除「🚀 启动弹幕姬」按钮（dycast 外部 Node 项目仅保留作可选启动器，UI 不再展示入口）。顶栏「🔁 轮询 / ⚙ 设置 / 🗑 清空内容 / ■ 断开 / ▶ 连接」统一改为**仅图标**（保留 tooltip 文字说明）。工具栏（保存弹幕 / 导出 / 导入 / 合并 / 统计 / 录播 / 监控）保留文字便于辨识。
- **文件迁移页（migrate）**：中间文件列表行从「📁 YYYYMMDD (N 项)」升级为「📁 2026-09-01 (今天) + 原始名 + 文件数 + 总大小（B/KB/MB/GB）+ 最后修改时间」。新增 `_format_folder_date` / `_format_size` / `_folder_stats` 三个辅助函数。
- **重命名设置页（rename）**：`_mini_btn` 尺寸 28×24 → 36×30，× 按钮用 `×` (U+00D7) 替代原 `✕` (U+2715)，字号 16px，确保 × 图标清晰可辨（原 28×24 下 × 几乎不可见）。所有方向按钮（⬆ / ⬇ / ×）保持**仅图标、无文字**（tooltip 提供上移 / 下移 / 删除说明）。删除按钮加红色危险色（红底 + 红字 + 加粗）。

#### v2.8.2（2026-08-30）

- 略（前序版本）

### 已知问题

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `main_window.py` vs `cookie_manager.py` | 两处 `COOKIE_PLATFORMS` 不一致（10 vs 11，后者多 `taobao`） | 侧栏显示「已登录 N/10」，不统计淘宝 |
| 2 | `pages/calendar.py` | 面包屑写「双击日期打开文件夹」，实现是**单击** | 仅文案不符 |
| 3 | `main_window.py` | 状态栏只在初始化时设一次「就绪」，之后无更新 | 形同虚设的占位 |
| 4 | `pages/test_page.py` | 文件名/key 为历史遗留的 `test_page`，实际是抖音弹幕姬页 | 命名易混淆 |
| 5 | `pages/douyin_recorder.py` | 有一处中文注释被误编码为 UTF-8 乱码 | 不影响运行 |
| 6 | `platforms/bilibili.py` vs `bili/wbi.py` | WBI 签名有两套实现，`MIXIN_KEY_ENC_TAB` 置换表**尾部不完全一致** | 二者签名结果可能不同，但目前各自链路均可用 |
| 7 | `tracker/config.py` | `OUTPUT_PATHS` 只有 3 个平台 | 快手/小红书记录不写入 `tracker/data/`（仍写入按账号的 xlsx） |
| 8 | `src/music/scrapers/fivesing.py` | `get_play_url()` 在文件中定义了两次（实现相同） | 冗余代码，后者生效 |
| 9 | `check_deps.py` | docstring 写「被 app 启动时自动调用」，实际**无调用点** | 需手动执行 `python check_deps.py` |

### 开发约定

1. **改引擎前先查现有代码**，避免重复造轮子（引擎层工具函数已高度复用，如 `toolbox/utils.sanitize_filename`）
2. **新增页面**：`pages/` 写 `BasePage` 子类 → `PAGE_CLASSES` 注册 → `NAV_GROUPS` 加导航项
3. **后台任务**统一走 `common.run_bg(fn, on_done)`，**不要跨线程访问 QWidget**
4. **重依赖在函数内延迟 import**，控制启动开销
5. **引擎层不要反向 import GUI**（`toolbox/live_video.py` 是唯一例外，用了 `QTimer` 做渲染定时）
6. **发版流程**：改 `src/version.py` → `git commit` → `python build.py`

### 参考来源

- 录播录制设计参考 [BililiveRecorder](https://github.com/Bililive/BililiveRecorder)（FLV 流式转存、弹幕 XML 格式）
- 加密音乐解密算法移植自 [Unlock Music](https://github.com/unlock-music/unlock-music)
- 多平台视频解析思路来自 video-parser 项目
- 抖音弹幕姬参考 [dycast](https://github.com/skmcj/dycast)（直播协议与弹幕分类格式的原始参考项目）

---

**制作：歆慕** | 数据汇总库：<https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr>
