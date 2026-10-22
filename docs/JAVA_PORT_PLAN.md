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
| **M3 网络与账号体系** | HttpClient 封装（UA/JSON/重试/表单）、WBI 网络层（nav 取 key、buvid、bili_ticket、cookie header）、Cookie 仓库（Fernet 语义逐字节兼容 Python，旧 cookies.json 直接可用）、B站扫码登录、12 平台管理界面 | ✅ 完成 |
| **M4 录播工具箱** | 音频提取、视频转换（ffmpeg 子进程：`-c copy` 无损优先 + libx264 回退）、文件迁移、日历浏览、继续操作启动器 | ✅ 完成 |
| **M5 解析与下载** | ✅ B站/微博/**抖音（SSR 分享页）**解析下载、多线程分段、批量导入、已获取、SQLite；⬜ 剩余：快手/小红书（需签名或登录，见 3.15） | ✅ 基本完成 |
| **M6 信息/资产/追踪页** | 🚧 已完成：单个/批量解析、已获取、酷我搜索下载、**资产管理**；⬜ 剩余：账号获取、抖音指数、B站资源、网易云/QQ/酷狗/5sing、账号追踪卡片 + PNG 导出、主题自动切换 | 🚧 进行中 |
| **M7 音乐高级** | ✅ 完成：NCM/QMC(v1+v2)/KGM/KWM/XM/TM/X2M/X3M 解密 + 标签读写（JAudiotagger）；⬜ 剩余：网络匹配/封面补全（随 M6 音乐搜索） | ✅ 完成 |
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

## 3.5 M2/M3 已知怪癖与差距（bug 级一致的保留项）

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

## 3.6 M3 决策与差距

1. **WebView 不可用**：本机 JDK 27 移除了 `jdk.jsobject`（66 个 jmod 里没有），
   JavaFX 21/27 的 `javafx.web` 都起不来（`Module jdk.jsobject not found`）。
   因此浏览器登录退化为「系统浏览器登录 + 粘贴 Cookie」（带登录特征校验），
   **M5 引入 playwright-java 后恢复自动捕获**（与原版一致）。
2. **扫码登录仅 B站**：完整链路（generate → 2 秒轮询 → Set-Cookie 收集 → 落盘）
   已落地；网易云/QQ/酷狗概念版扫码依赖各自加密 API，随 M6/M7 补齐。
3. **Fernet 完全互通**：密钥 = `urlsafe_b64encode(sha256("fmjj-cookie-v1:"+MachineGuid))`，
   token 为 0x80+ts+IV+AES128CBC+HMAC-SHA256。Java 用 FFM 直调 advapi32 读
   MachineGuid（不依赖 reg.exe，避免被安全软件拦）。测试含 Python 生成的 5 条
   token 双向互通，**原版 cookies.json 可直接复用，登录态零迁移成本**。
4. **JDK 要求提升到 27**：FFM 正式 API（22+）+ JavaFX 27（配 JDK 27）。
   若将来要支持 JDK 21，需要把 FernetCompat 的注册表读取换成 JNA 并回退 JavaFX 21。

## 3.7 M4 决策与差距

1. **ffmpeg 仍是外部依赖**：定位顺序 = PATH → 全局设置自定义路径 → `bin/` 内置 →
   裸 `ffmpeg`（与原版一致）。`Ffmpeg.available()` 供 UI 提示；内置分发留 M9。
2. **文件迁移勾选方式**：原版是每行 CheckBox；Java 版用多选行 + 全选/反选
   （交互差异，功能等价）。目标已存在时逐文件合并、同名加 `_N`。
3. **继续操作页**：先给「系统目录 + 最近 8 个日期文件夹」的快捷卡片；
   原版的自定义条目（launcher_items 持久化）随 M6 全局设置补。
4. **回收站**：Python 用 send2trash；Java 用 `Desktop.moveToTrash`
   （不可用时退化永久删除，页面会提示）。

## 3.8 M5 第一刀（B站链路）说明

1. **已验证真实链路**：`BiliParser.videoInfo/playInfo` 对线上 API 调通
   （游客身份：5 档画质 / 6 条视频流 / 3 条音频流，AVC 优先命中 480P；
   登录 Cookie 后可得 1080P+，与 B站权限模型一致）。
2. **下载器**：镜像回退 + `.part` + Range 断点续传已实现；
   IDM 风格多线程分段（Python `media_downloader._download_ranged`）留第二刀。
3. **链接类型**：只处理 video/user/liveroom/dynamic 四类；
   装扮/收藏集/漫展（lottery/suit/blackboard）属 M6「B站资源」页。
4. **入库**：`config/collected.db` 的 media 表（platform+media_id 唯一，UPSERT）。
   与 Python 的增量库表结构不同（原版按平台分表），M6 做「已获取」页时统一对齐。

## 3.9 M5 第二刀（分段下载 + 微博）说明

1. **分段下载已对齐原版**：Range 试探（`bytes=0-0`）→ 均分 N 段 → 各段独立句柄按偏移写
   `.idmpart` → 全成后原子改名；失败/取消删残片（分段中间有洞，不能走单流续传）。
   段级重试 3 次，从段内断点续。UI 提供 单线程/4/8 线程三档。
2. **微博只走 statuses/show 主路径**：Python 还有「移动页 HTML 解析」「视频播放器页 fid →
   component API」两级兜底，Java 版暂未移植（失败会明确报错，不会静默）。
3. **抖音仅链接识别**：`v.douyin.com` 短链、`/video/`、`/note/`、`modal_id`、`/user/` 都能识别，
   解析需浏览器上下文（Argus 风控），与 Python 版一致——留 M5 第三刀接 playwright-java。
4. **快手/小红书**：需各自签名/Cookie 体系，随第三刀处理。

## 3.10 M5 第三刀-A（批量导入 + 已获取）说明

1. **LinkRouter 统一入口**：五平台（B站/微博/抖音/快手/小红书）单条 + 多行批量识别，
   返回 `Target(platform, id, kind)`；`parsable()` 标明当前可解析平台（B站/微博），
   其余在 UI 上明确标注「待支持」而不是静默失败。
2. **批量导入页**：识别（不触网）→ 逐条串行解析+下载 → 行内状态实时刷新 → 自动入库。
   B站取最高画质（DASH 合并，4 线程），微博下载全部图片/视频。
3. **已获取页**：`collected.db` 的 media 表读取，平台筛选、打开文件/目录、删除记录、
   导出 CSV（UTF-8 BOM，Excel 直接打开不乱码）。
4. **仍待办**：抖音解析（需 playwright-java + a_bogus，见难点清单）、
   快手/小红书解析（签名/Cookie 体系）、已获取页的分页与批量导出。

## 3.11 M7 第一刀（音乐解密）说明

1. **已移植 4 族算法**（逐行对照 Python music_unlock.py）：
   - NCM：AES-128-ECB keybox（`hzHRAmso5kInbaxW`）+ 256 字节 RC4 变体密钥流 + meta（`163 key(Don't modify):` 外层 / `music163:` 内层）
   - QMC：v1 静态表（`offset²+27`）、v2 Map 密码（`offset²+71214` 旋转）、v2 RC4 分段（首段 0x80 / 段 5120）
   - KGM/KGMA/VPR：动态 mask（272 表 + `T(x)=x^((x&0xF)<<4)` 变换，T 是自逆）
   - KWM：`yeelion-kuwo-*` 头 + 文件密钥 XOR（0x400 偏移）
   - TEA（TarsCpp TCTEA CBC）用于 QMC EncV2 主密钥
2. **密钥表**：`_unlock_keys.json` 原样放进 resources（QMC/KGM 共 11 张表）。
3. **跨语言验证**：`tools/gen_music_samples.py` 用 Python + pycryptodome 按原版算法**加密**生成
   sample.ncm / sample.kgm / sample.kwm + QMC mask 向量，Java 解密后与 `.expected` 逐字节比对
   （8 个测试全绿）。这比"能解一个真实文件"更能证明算法一致。
4. **未移植**：XM（虾米）/ TM（喜马拉雅）/ XMLY（x2m/x3m 索引重排）—— 算法独立，随 M7 第二刀。
5. **已知差异**：QMC 的 `STag`（未写密钥）与 `QTag`（KeySize BE）分支已实现但无样本验证；
   真实 QTag 文件需人工验证一次。

## 3.11b M7 第二刀（补齐 3 种格式 + 标签编辑）

1. **XM**：`ifmt` 头 + `0xFE×4` 标记 + 第 4-8 字节类型（` WAV`/`FLAC`/` MP3`/` A4M`）+
   `((b - key) & 0xFF) ^ 0xFF`（offset 之后）。**注意 Java 运算符优先级**：`&` 低于 `^`，
   必须写成 `((b - key) & 0xFF) ^ 0xFF` 加全括号。
2. **TM**：只把前 8 字节改成 `\x00\x00\x00\x20ftyp`，输出 m4a。
3. **x2m/x3m**：前 min(1024, len) 字节按 1024 长索引表重排 + XOR（x2m 用 `xmly`，
   x3m 用 32 位十六进制串）。
4. **标签编辑**：`net.jthink:jaudiotagger:3.0.1`（中央仓库坐标是 net.jthink，不是 org.jaudiotagger）。
   API 差异：3.0 用 `tag.deleteField(FieldKey)`（没有 `removeFields`）。
   测试用 ffmpeg 现场生成 1 秒静音 mp3/flac，写入→读回断言（含封面 1x1 PNG）。
5. **未做**：xlsx 批量匹配、网络补全封面（原版右侧功能），随 M6 音乐搜索一起。

## 3.13 M6 第一刀（酷我搜索下载）说明

1. **真实链路已验证**（`FMJJ_NET_TEST=1 mvn test -Dtest=KuwoNetworkIT`）：
   `searchSong("起风了 周深")` → 5 首周深；`getPlayUrl` → `https://kw-bj.kuwo.cn/...mp3`；
   `getLyric` → 1247 字符 LRC。
2. **直链关键技巧**：`antiserver.kuwo.cn` 返回 302，**不跟随重定向**直接读 `Location`
   就是 mp3 地址。这样避开了目标 CDN 的弱证书（Python urllib 会报
   `CERTIFICATE_VERIFY_FAILED: EE certificate key too weak`）。
   为此给 HttpClient 加了 `getRedirect()`（`Redirect.NEVER`）。
3. **歌词接口要 `m.kuwo.cn` 的 Referer**（用 www 的 Referer 返回空）。
4. **`_match_any` 语义**：最长片段必须命中；其余片段「至少命中一个」即通过
   （不是全部命中）—— 一开始写错，用 Python 向量才发现。
5. **注册表校验**：`MainWindow.validateRegistry()` 在启动时比对 pageFactories 与 NAV_GROUPS，
   不一致就 WARN。起因是踩了 `music_unlock` vs `unlock_music` 的键名错，页面静默失效。
6. **未移植**：网易云（eapi/weapi 加密）、QQ（musicu sign）、酷狗（签名）、5sing；
   抖音指数/B站资源/资产管理/账号追踪。

## 3.14 M6 第二刀（资产管理 + 5sing 探测结论）

1. **资产管理已落地**：`AssetScanner` 统计日期文件夹的图片/视频/音频/体积/最后修改，
   页面支持汇总行、双击打开、导出 CSV。测试用临时目录构造结构（含子目录、大小写扩展名、根目录散落文件）。
2. **5sing 直链已失效**（重要发现）：
   - 搜索走酷狗 `mobilecdn.kugou.com/api/v3/search/song` —— **仍可用**（返回 status=1，
     含 isoriginal/Accompany 字段，能区分原唱/翻唱/伴奏）。
   - 但原版直链 `m.kugou.com/api/v1/wechat/index?cmd=101&hash=xxx` 现在返回
     `{"errcode":2,"error":"Empty key"}` —— **接口加了 key 校验，原版这条链路在本机也不通**。
   - 结论：5sing 下载需要重新逆向（找带 key 的接口或走网页解析），不是移植工作量问题。
     暂缓，先做能验证的功能。
3. 原版 asset 页的「原始数据」列依赖 `asset_index.db` 缓存（扫描 json + 媒体目录），
   Java 版暂未移植该缓存层；当前统计口径只按扩展名分类。

## 3.15 抖音 SSR 路线 + 快手/小红书探测结论

**抖音（已实现，不需要 playwright）**：
- 实测 `https://www.iesdouyin.com/share/video/{id}/` 返回 200，页面内嵌
  `window._ROUTER_DATA = {...}`，`loaderData["video_(id)/page"].videoInfoRes.item_list[0]`
  就是完整 aweme 对象（desc/author/video/statistics/images）。
- 这比 Python 版的 Playwright 路线**更轻**（原版走浏览器是因为 `aweme/detail` 被 Argus 拦，
  但分享页 SSR 是另一条路）。
- 实现：`DouyinParser`（平衡括号提取 JSON + 字段映射 + `playwm`→`play` 去水印）；
  短链由 `DouyinLink.resolve` 展开。图集与视频都支持下载。
- **未验证项**：本机拿不到真实 aweme_id（热榜 aweme 接口返回空、搜索接口 `Url doesn't match`），
  解析器只用离线 fixture 验证过结构；真实链接请在页面上粘一条试。

**快手（暂缓）**：`v.m.chenzhongtech.com/fw/photo/{id}` 返回 200 但页面只有
`__CDN_DISPATCH__`/`__KCONF__` 空壳，数据在 `kuaishou.com/graphql`（需 cookie + 签名）。

**小红书（暂缓）**：`xiaohongshu.com/explore/{id}` 无 xsec_token 时 302 跳登录，
数据需登录态 + `__INITIAL_STATE__`（登录页不返回）。

## 3.12 桌面启动器与 exe 打包

- `jpackage --type app-image` 生成 `dist/FengMingToolboxJava/`（含精简 JRE，148MB，目标机**不需要装 Java**）。
- 桌面放 `封茗工具箱 Java版.vbs`（双击启动，无控制台窗口）。**不能只复制 exe**：
  app-image 的 exe 依赖同目录 `runtime/` 与 `app/`。
- 重新打包：`package_exe.bat`（或手动 `mvn package` → 复制 jar 到 target/lib → jpackage）。
  注意 .bat 必须是 CRLF 换行，否则 cmd 解析错乱。
- 主类用 `cn.fmjj.toolbox.Launcher`（非 Application 子类），规避 classpath 启动的 JavaFX 模块检查。

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
