# Java 全栈移植路线图

> 目标：把 `FengMingToolbox v2.9.1`（Python/PySide6，约 7.6 万行）整体移植到 Java 21 + JavaFX。
> 本文档是唯一进度事实源。每完成一个模块：更新状态勾选 + 在 `README.md` 进度表同步一行。

## 0. 策略与铁律

**策略：纵切优先，分页替换。** 先搭「构建 → 主窗口导航 → 引擎 → 页面 → 测试」完整纵切
（M1 已完成），后续每个功能按「引擎先行、页面跟进、测试对齐」的节奏插入，
主窗口与 35 页导航全部预注册，替换占位页即可，架构零改动。

**铁律**（与 README「移植约定」一致）：

1. `engine/` 零 GUI 依赖；
2. 移植期不做行为修正，与原版 bug 级一致（可疑处注释标注）；
3. 引擎测试期望值必须来自 Python 原版运行时；
4. 页面必须接真实引擎（不允许长期空壳）。

## 1. 资产盘点（Python 版实测）

| 层 | 规模 | 说明 |
|---|---|---|
| 引擎层 | ~3.3 万行，约 120 个模块 | `toolbox/ platforms/ tracker/ music/ netease/ bili/ exporters/` + 根级大文件（shopping 1480 行、tickets 1358 行、music_unlock 626 行） |
| GUI 层 | ~4.1 万行，43 个页面模块 | PySide6；35 页挂侧栏，其余为页面内嵌 tab/共享组件 |
| 外部依赖 | — | playwright(16 文件)、ffmpeg 子进程(14 文件)、sqlite3(5 文件)、subprocess 调用点 74 处 |

## 2. 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M1 骨架 + 纵切** | Maven/JavaFX 构建、主窗口 9 组 35 页导航、懒加载、生命周期钩子、双主题、Toast、字幕引擎+页、歌词引擎+页、WBI 签名、跨语言测试 | ✅ 完成 |
| **M2 配置与基础工具** | settings(JSON) 持久化、文件时间修改、批量重命名（8 类规则）、自动整理（按日期归档） | ✅ 完成 |
| **M3 网络与账号体系** | HttpClient 封装（UA/cookie/重试/限流）、WBI 网络层（nav 取 key、buvid、bili_ticket）、Cookie 仓库（JCE 移植 Fernet 语义：AES-CBC + HMAC + 机器 GUID 派生密钥，sqlite 存储）、扫码/浏览器登录入口 | ⬜ |
| **M4 录播工具箱** | 音频提取、视频转换（ffmpeg 子进程：`-c copy` 无损优先 + libx264 回退）、文件迁移、日历浏览、继续操作启动器 | ⬜ |
| **M5 解析与下载** | B站/抖音/微博/快手/小红书 解析器（游客→兜底→Cookie 降级链）、媒体批量落盘、DASH 下载 + ffmpeg 合并、a_bogus（见难点）、已获取数据 SQLite 持久化（xerial sqlite-jdbc） | ⬜ |
| **M6 信息/资产/追踪页** | 单个/批量解析、账号获取、已获取、抖音指数、B站资源、资产管理；音乐 5 平台搜索/下载；账号追踪卡片 + PNG 导出；主题自动切换（跟随系统/定时） | ⬜ |
| **M7 音乐高级** | 30+ 加密音乐解密（ncm/qmc/mgg/kgm/kwm…逐一移植）、音乐标签编辑（JAudiotagger）、网络匹配 | ⬜ |
| **M8 票务购物与直播** | 大麦/秀动/会员购、淘宝商品、抖音弹幕姬（WebSocket）、B站弹幕姬（纯 HTTP FLV 录制 + 弹幕 XML）、图片裁切、封面制作、媒体筛选（MD5 去重）、媒体压缩 | ⬜ |
| **M9 打包分发** | jpackage 打包（Windows msi/exe）、FFmpeg 检测与自定义路径、check_deps 自检、隐私扫描（config/download/logs 不入包） | ⬜ |

建议每 1-2 个里程碑发一次内部测试版；M5 之后可考虑对原版做双写灰度。

## 3. 35 页映射（key → 引擎 → 里程碑 → 难度）

| 导航页 | Python 引擎模块 | 里程碑 | 难度 |
|---|---|---|---|
| 字幕转换 ✅ | toolbox/subtitle_engine | M1 | — |
| 歌词转换 ✅ | toolbox/lyrics_engine | M1 | — |
| 批量重命名 ✅ | rename_batch 规则引擎 | M2 | 低 |
| 自动整理 ✅ | toolbox/organize_engine | M2 | 低 |
| 文件时间修改 ✅ | file_time（含 ctime 限制） | M2 | 中 |
| 全局设置 ✅ | config_manager（子集） | M2 | 低 |
| 音频提取 | toolbox/audio_engine (ffmpeg) | M4 | 中 |
| 视频转换 | toolbox/convert_engine (ffmpeg) | M4 | 中 |
| 文件迁移 / 日历浏览 / 继续操作 | toolbox/migrate, calendar, launcher | M4 | 低-中 |
| 单个解析 / 批量导入 | platforms/*, multi_downloader | M5 | 高 |
| 账号获取 / 已获取 | accounts_manager + SQLite | M5-M6 | 中 |
| 抖音指数 | platforms/douyin_index | M6 | 中 |
| B站资源 | bili/api, bili/links, bili/download | M6 | 高 |
| 资产管理 | exporters, media_downloader | M6 | 中 |
| 音乐信息获取 / 音乐下载 | music/*, netease/* | M6 | 高（eapi） |
| 音乐解密 | music_unlock (30+ 格式) | M7 | 高 |
| 音乐标签 | mutagen → JAudiotagger | M7 | 高 |
| 音乐/网媒账号追踪 | tracker/*, music_account/* | M6 | 中（PNG 导出） |
| 抖音弹幕姬 / B站弹幕姬 | 直播录制（WebSocket / FLV） | M8 | 高 |
| 票务解析 / 购物解析 | tickets, shopping | M8 | 中 |
| 图片裁切 / 封面制作 / 媒体筛选 / 媒体压缩 | imgcut / image-sorter 系移植 | M8 | 高（图像） |

## 3.5 M2 已知怪葩与差距（bug 级一致的保留项）

1. **重命名冲突双加序号**：Python `_refresh` 的跨项撞名按列表索引身份判断，
   两个同名文件会**互相**触发冲突（a.mp4/a.mp4 → a_1.mp4/a_2.mp4），已按原样保留。
2. **规则撞名不去重**：规则把 x1/x2 都改成 x.mp4 时，原版不会加序号（`used` 表
   只在撞「已登记名/已存在原名」时才写入）→ 执行时后写覆盖先写。已保留原行为，
   待与作者确认是否在后续里程碑修正（届时需要同步改两个语言版本）。
3. **文件时间 ctime**：Python 用 win32file/ctypes 写 Windows 创建时间；Java NIO
   大多平台不可写 → 引擎尽力而为，UI 状态标注「创建时间不支持」。
4. **批量重命名文本模式 tab**（逐行直接编辑新名）未做，M3 前补或确认砍掉。
5. **Settings 只移植全局设置页相关键**（目录/主题/ffmpeg），其余 60+ 配置键
   随各自页面里程碑补齐；主题的跟随系统/定时切换 M6 落地。

## 4. 依赖映射（Python → Java）

| 直搬 | httpx/requests → `java.net.http`；sqlite3 → xerial sqlite-jdbc；playwright → playwright-java（同厂，1:1 概念） |
|---|---|
| 重实现 | pydantic → Jackson + record 校验；openpyxl → Apache POI；mutagen → JAudiotagger；qrcode → ZXing；cryptography/pycryptodome → JCA/BouncyCastle；Pillow+numpy → Java 2D（BufferedImage） |
| 无对等 | pillow-heif（HEIC/实况图）→ 外部 ffmpeg 转码或 `jaudiotagger` 不可用，需自研解析；Node a_bogus → 保留 Node 子进程调用 |

## 5. 难点清单（按踩坑概率排序）

1. **a_bogus / X-Bogus 等风控签名**：原版靠内嵌 Node 执行 JS。Java 侧首选仍是
   `ProcessBuilder` 调 Node（把 JS 当资产打包进 jar）；备选是 GraalJS 移植 JS 引擎。
2. **playwright 依赖**：playwright-java + 浏览器二进制分发（~300MB）与原版一致，
   注意无头环境 fallback。
3. **网易云 eapi 加密**：weapi/eapi 的 AES-ECB + MD5 链路用 JCA 逐行移植，
   用原版请求报文做回归。
4. **30+ 音乐解密格式**：ncm/qmc/mgg/kgm 等多为 XOR + 表替换 + RC4/KECCAK 变体，
   逐个格式由原版二进制样本驱动测试。
5. **图像链路**：HEIC/LIVP 解码、边界吸附（numpy 加速）在 Java 里需要重设计算法或
   引入 OpenCV（引入即引入原生库分发问题）。
6. **PNG 卡片导出**（追踪页）：JavaFX `Node.snapshot()` 或 `SwingFXUtils` 转 BufferedImage。
7. **进程树**：Python 用 Windows Job Object 父关子关；Java 侧可用 JNA 或退化为
   `ProcessHandle` 尽力而为（不阻塞主流程，标注差异）。
8. **Cookie Fernet 语义**：密钥由机器 GUID 派生（PBKDF2/固定 salt 需对照原版实现），
   **解密兼容旧数据**是硬性验收点——需用原版 `config/cookie.txt` 真数据做迁移测试。
9. **ffmpeg 外置依赖**：检测 PATH → 自定义路径回退逻辑保持原版行为。

## 6. 验收标准

- 每个引擎模块：JUnit 测试通过（含 Python 向量用例），`engine/` 无 javafx import；
- 每个页面：接真实引擎，核心路径人工走查一遍；
- 每个里程碑：`mvn clean test` 全绿 + 更新本文件状态列。

## 7. 打包发布（M9）

- `jpackage` 生成 Windows msi/exe（捆绑 JRE 或依赖用户 JDK 二选一，需产品决策）；
- FFmpeg 作为外部资产检测，不进安装包（与原版一致）；
- 隐私扫描：`config/ download/ logs/` 与账号数据一律排除。
