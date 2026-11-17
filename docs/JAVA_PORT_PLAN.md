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
| **M5 解析与下载** | ✅ B站/微博/抖音/快手/小红书 解析下载、多线程分段、批量导入、已获取、SQLite（小红书媒体走 CDP 笔记页，见 3.18） | ✅ 完成 |
| **M6 信息/资产/追踪页** | ✅ 已完成：单个/批量解析、已获取、酷我搜索下载、资产管理、B站资源（8 个 tab 全部实现）、公众号解析、视频评论区、音乐信息、账号追踪×2、抖音指数、账号获取 | ✅ 完成 |
| **M7 音乐高级** | ✅ 完成：NCM/QMC(v1+v2)/KGM/KWM/XM/TM/X2M/X3M 解密 + 标签读写（JAudiotagger）；⬜ 剩余：网络匹配/封面补全（随 M6 音乐搜索） | ✅ 完成 |
| **M8 票务购物与直播** | ✅ 媒体工具 4 页；✅ B站弹幕姬（3.23）；✅ 抖音弹幕姬（3.25，含预览/FLV 录制/回放视频，见 3.31）；✅ 票务解析（3.26）；✅ 购物解析（3.27） | ✅ 完成 |
| **M9 打包分发** | ✅ jpackage app-image（自带 JRE）、FFmpeg 检测+自定义路径、环境自检（7 项）、隐私校验（自用包/分发包）；⬜ msi/exe 安装器需 WiX | ✅ 完成 |

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
3. **继续操作页**：已支持自定义软件/链接条目（`config/launcher.json` 持久化）、
   12 项分页卡片、图标、上下移/删除、「跟随进程树」启动与退出时统一关闭，
   另保留工作/导出/配置/日志四个系统目录快捷入口。
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

### 单个解析（info_parse.py，366 行）— 已重排
原版结构：
- `🔍 单个解析`(18px 粗体)
- 行1：模式下拉(104) `🔗 链接模式 / 🔎 搜索模式` + 搜索平台下拉(90，仅搜索模式可见) +
  链接输入(伸展) + `🔍 解析`(绿 #2e7d32)
- 行2：`⬇ 下载当前解析结果`(绿，默认禁用) + 伸展 + `画质:`(110) + `线程:`(64)
- 结果区：只读 QTextEdit（placeholder「解析结果将显示在这里」，伸展）
- 日志区：只读 QTextEdit（固定高 110，placeholder「下载日志」）

Java 侧已对齐：`modeBox` / `searchPlatBox` / `resultArea`（只读 TextArea）/ 固定高 110 的 log；
模式切换改 placeholder 与按钮文案（🔍 解析 ↔ 🔎 搜索）。
搜索模式：B站走 `BiliResource.searchAll`，微博走 `WeiboParser.search`
（m.weibo.cn 容器接口），结果弹窗双击回填链接并解析；「全部平台」会合并两边结果。
抖音搜索在无头浏览器下会落到验证码中间页（CDP 实测），页面明确提示改用作品链接解析。

### 批量导入（info_batch.py，403 行）— 已重排
原版结构：标题(18px) → 输入框(**固定高 150**) → 按钮行(`批量解析` + `❌`取消(40×30,禁用) +
`⏸️`暂停(40×30,禁用) + `📁`导入文件 | 伸展 | `⬇️`下载勾选(40×30,绿,禁用) + `画质:`(110) + `线程:`(64))
→ 计数行(`已解析 N 条 · 勾选 M 条` 蓝字 #1976d2 + 伸展 + `☑️`全选/`🔄`反选/`❌`清除选择 32×24)
→ 可勾选列表(伸展) → 日志(**固定高 110**)。

Java 已对齐，并把流程拆成原版的两阶段：
- **批量解析**：只解析（B站 videoInfo / 微博 parse），填标题与状态，成功后自动勾选；
  支持暂停（⏸️ 轮询等待）与取消（当前条目解析完即停）
- **下载勾选**：只下勾选条目，按所选线程数
- 表格首列 CheckBox（`CheckBoxTableCell`）+ 平台/标题/状态；双击行打开原链接
- `📁` 从 txt 导入追加

### 已获取（info_results.py，665 行）— 已重排
原版结构：标题(18px)`📦 已获取（按平台分栏，按发布日期倒序）` + 伸展 +
`📤 导出所有勾选`(绿) + `📥 下载所有勾选`(绿) + `🗑 删除所有勾选`(红 #c62828) + `🗑 全部清空`(红)
+ `画质:`(110) + `线程:`(64)；第二行 `导出格式:` JSON/XLSX/MD；
主体**四栏**（📕 抖音 | 📺 B站 | 📝 微博 | 📦 其他），每栏白底圆角：
栏头「平台名 + 计数(蓝)」、按钮行「全选/反选/清除(40×22) + 📤 导出(46×22) + 📥 下载(46×22)」、
卡片列表（CheckBox + 标题 + 作者/时间/下载状态，双击打开本地文件）。

Java 已对齐。导出：JSON / MD 已实现，**XLSX 用 CSV（UTF-8 BOM）替代**并在状态栏标注「XLSX 待补」；
下载仅 B站（重新解析后直下），非 B站计入「跳过」并说明。

### 音乐下载（netease.py，1111 行）— 已重排
原版结构：`🎧 音乐下载工具箱`(18px) + 伸展 + `❓ 使用说明`（红框）；平台下拉（6 平台）+
模式按钮组（按平台动态：搜索/单曲/歌单/专辑）；动态输入区（搜索＝歌手150+关键词+🔍获取，
其余＝单链接+🔍解析）；白底带边框详情卡片区（伸展）；底部栏（☑全选/🔄反选/□全不选 +
□自动换源 + 音质 + 导出位置 + 📁(30×26) + `⬇ 下载选中`(绿) + 状态）。

Java 已对齐结构与交互。平台矩阵与原版一致：
网易云(4 模式)/QQ(3)/酷狗(3)/酷我(3)/5sing(3)/酷狗概念版(2)。
**功能现状**：仅酷我可用（搜索→勾选→下载，带 .lrc）；其余平台切过去会明确提示「接口未移植」，
不做假按钮。卡片含「⬇ 立即下载」快捷入口。

### 音乐解密（music_unlock.py，135 行）— 已重排
原版结构极简：`🔓 音乐解密`(18px) + 伸展 + `❓ 帮助` → 工具行（`📁 选择文件` + `🗑 清空` +
伸展 + `🔓 开始解密`(绿, padding 6 16, radius 4)）→ 文件列表（**最大高 200**）→ 日志（只读，伸展）。

Java 已对齐：**改为「选择文件」多选对话框**（原版是选文件，不是选目录），列表最大高 200，
日志伸展；解密结果与失败原因实时写入日志，成功后打开导出目录。

### 音乐标签（music_tag.py，1210 行）— 已重排
原版结构：工具栏（＋添加文件/📂添加文件夹/💾保存选中(绿)/💾全部保存(绿)/🌐网络匹配(蓝)/
⚡批量匹配(蓝)/📥表格匹配(蓝)/✖移除选中/🗑清除标签/🧹清空列表 + 伸展 + ❓）→
**QSplitter 左右分栏**（左 340：表单标题 + QFormLayout 12 字段（9 个单行 + 音轨号/碟号同行 +
歌词 QPlainTextEdit 最大高 140 + 「⛶展开编辑」按钮 52 宽）+ 封面区 140×140 与三按钮；
右 760：11 列表格）→ 状态栏（状态灰 + 伸展 + `过滤：` 220）。

Java 已对齐结构，并扩展引擎：`MusicTagEngine.TagFields`（12 字段）+ `readFields/writeFields`
（JAudiotagger 的 ALBUM_ARTIST/COMPOSER/LYRICIST/COMMENT/DISC_NO/LYRICS）。
功能：添加文件/文件夹、表单编辑、保存选中/全部保存、清除标签、移除、清空、过滤、
歌词弹窗编辑、封面更换/移除（预览 140×140）。
**已实现**：网络匹配（五平台候选 + 歌词/封面）、批量匹配（第一候选）、
XLSX 表格匹配（文件名/标题/艺术家/专辑/年份/音轨/碟/风格/专辑艺术家/歌词列）。

### 图片裁切（image_crop.py，1287 行）— 已重排
原版是**画布交互式裁切**：工具栏顺序为 `📁 导入` / `↩` `↪` / `➖ 3x ➕` / `🎯 自动对齐` /
`✏️ 精确输入` / `□ 精细模式` `□ 边界吸附` / 伸展 / `— × — px` / `💾 导出` / `📦 批量导出` / `🗑️`；
主体：左列表（170-260）+ 右 CropCanvas；底部：操作提示 + 文件名。

Java 已实现画布交互：
- Canvas 绘制图片（等比适配）+ 框外暗化 + 裁切框 + 四角手柄
- 拖动四条参考线或整体移动（8px 命中容差）
- **精细模式**：位移 /20（按住 Shift 临时加速）；**撤销/重做**（Ctrl+Z / Ctrl+Y，栈式）
- **自动对齐**：从四边向内找第一个非纯黑/纯白的行/列
- **精确输入**：上/下/左/右像素坐标（尺寸按钮也打开该对话框）
- 导出（`_cropped` 后缀到 导出目录/图片裁切）/ 批量导出 / 清空
- **坑**：JavaFX `Canvas` 不会跟随容器尺寸，需 `Pane` 包裹并绑定 width/height

**未做**：放大镜浮窗（原版拖拽时右侧显示像素放大镜）、边界吸附的边缘分析。

### 封面制作（cover_maker.py，1884 行）— 已重排（核心子集）
原版结构：`🎨 套版封面制作`(11pt 粗体) + `❓` + 伸展 + `🎲 随机底图` + `🖼 选择底图` +
`📁 上传图片` + 裁剪填充下拉 → SplitPane（左 380 滚动区：🖼 底图组 / 🔗 同步样式 / 各文字图层；
右 760 预览）→ 状态栏。

Java 已对齐骨架：底图组（更换文件夹/重新扫描/计数/提示）、文字组（标题/副标题 + 字号 + 颜色 +
底部渐变条）、🎨 颜色预设（8 色）、分辨率下拉（1280×720…3840×2160）、`💾 导出 PNG`、
右侧 Canvas 实时预览（`render()` 支持裁剪填充/完整包含两种铺法）。

**未做**（原版高级功能）：多文字图层、字体选择器、拖拽定位、分割线、同步样式。
新增 4 个渲染测试（尺寸/裁剪填充铺满/完整包含留黑边/无底图黑底）。

### 媒体筛选（image_sorter.py 等 4 文件，6106 行）— 已重排（核心工作流）
原版结构：顶栏1（📂添加源文件夹 / 🧹清空源 / 视图下拉 / 伸展 / 源路径）→
顶栏2（📁分类目录 + 路径 / 伸展 / 分类方式(复制·移动) / ⬇️保留(S) / ⭐收藏(F) / 🗑️删除(W) /
↩️撤销(Ctrl+Z) / ⚙️设置）→ 管线状态 → **三栏 Splitter（330/440/330）**
（文件列表 / 预览+文件夹面板 / 文件信息）。

Java 已对齐骨架与工作流：
- 三栏：左文件列表（带 ✅⭐🗑 状态前缀）/ 中预览（图片缩略渲染）/ 右信息（大小/路径/类型）
  + 保留/收藏/删除 三色计数
- 视图：📅日期分组（按修改时间排序）/ 📋默认列表 / 🎲随机抽样（前 50）
- 分类：保留→`分类目录/保留`、收藏→`分类目录/收藏`（复制或移动，重名自动加 `_n`）；
  删除→回收站；操作后自动跳到下一个
- 键盘：S 保留 / F 收藏 / W 删除 / Ctrl+Z 撤销（撤销会删除已复制/移动的目标文件）

**未做**：缩略图网格、MD5 去重面板、随机抽样数/自动播放视频/实况、快捷键映射、文件夹面板。

### 媒体压缩（media_compress.py，1172 行）— 已重排
原版结构：顶栏（📁添加文件 / 📂添加文件夹 / ☑️全选 / 🔄反选 / 🗑️清空列表 / 伸展 /
`共 N 个文件` / ▶压缩勾选(蓝 #2563eb) / ⏹停止）→ Splitter（左可勾选文件列表 +
右参数滚动区：选中文件信息 / 🖼图片参数组 / 🎬视频参数组 / ⚙CPU 占用限制滑块 20-100 默认 55）。

Java 已对齐：CheckBoxListCell 可勾选列表、预设（高质量/均衡/小体积/只压 720p）+ 应用预设、
质量滑块 1-100（映射视频 CRF 52-10）、分辨率（原始/1080p/720p/480p）、编码（H.264/H.265）、
清除元数据（`-map_metadata -1`）、CPU 限制（按核数百分比算 `-threads`）、大小预估。

**未做**：图片格式转换（原版有 png/webp 选项）、逐文件进度条、后台缩略图预览。

### B站表情包（bili_emoji.py 的「表情包搜索」）— 新实现
原版 bili_resource 的第 1 个 tab。接口：`x/emote/user/panel/web?business=reply`（需登录 Cookie）。

**实测可用**：返回 **11 个表情包、每包 20 个表情**（含 `[doge]` 等，URL 在 `i0.hdslb.com/bfs/garb/`）。

Java 已实现 `BiliEmote` + 该 tab：
- `parse()` 跳过空包、`//` 补 https、**GIF 优先**（`gif_url` 有值就用动图）
- `flatten()` / `search()`（按表情名或包名匹配）/ `safeName()`（去 `[ ]` 等非法字符）
- 页面：🔄加载表情包 / 包下拉（全部 + 每包计数）/ 搜索框 / ⬇下载当前列表；
  FlowPane 网格（56×56 缩略图异步加载 + 表情名），点单个即下载；
  批量下载按包名分子目录，落 `导出目录/B站资源/表情包/{包名}/`
- 3 个测试（解析/GIF 优先、扁平化与搜索、文件名安全化）

**仍占位**：装扮搜索 / 装扮表情包 / 直播表情 / 装扮ID详情 / 收藏集（5 个，需 bili_garb 623 行 + 表情包详情）

### B站装扮搜索（bili_garb.py 的「装扮搜索」）— 新实现
接口：`x/garb/v2/mall/home/search?key_word=&pn=`（**免签名**，游客可用）。

**关键结构坑**：结果条目在 **`data.list`**，不是 `data.items`（后者恒为空）；
每条含 `name / item_id / group_name / properties.dlc_act_id`；
**收藏集要用 `dlc_act_id` 跳转**（item_id 对收藏集无效）。

Java 已实现 `BiliGarb` + 该 tab：
- `search()`（关键词 + 页码）/ `parseSearch()`（跳过空条目、`jumpKey()` 优先 dlc_act_id）
- `suitDetail()`（`x/garb/v2/user/suit/benefit?item_id=&part=cards`，含表情/头像框/粉丝牌等资源）
- 页面：搜索框 + 🔍搜索 + 4 列表格（名称 / item_id / 分组 / 收藏集 act_id）
- 2 个测试（解析与跳转键、空数据）

**仍占位**：装扮表情包 / 直播表情 / 装扮ID详情 / 收藏集（4 个）

### B站装扮ID详情（bili_garb.py 的「装扮ID详情」）— 新实现
接口：`x/garb/v2/user/suit/benefit?item_id=&part=cards`（免签名）。

**实测**：`item_id=336202401` → 名称「桃味白菜个性装扮」，抽出 **52 个资源**。

**结构**：`data.suit_items` 是**对象**，8 个键各一类，值为数组，元素含 `name` + `properties`；
资源 URL 藏在 `properties` 的各种字段里（`image` / `fans_image` / `image1_landscape` /
`space_1_mp4_horizontal` / `image_ani` / `loading_url` / `image_cover` …）。

8 类：`card` 卡片 / `card_bg` 卡片背景 / `emoji_package` 表情包 / `loading` 加载动画 /
`play_icon` 播放图标 / `skin` 皮肤 / `space_bg` 空间背景 / `thumbup` 点赞动画。

Java 已实现 `BiliGarb.parseResources()` + 该 tab：
- 遍历 8 类 → 每个元素 → properties 里**只收图片/视频 URL**（排除颜色值、md5）
- 页面：输入 item_id + 🔍加载 + ⬇下载全部资源 + 3 列表格（类别 / 名称 / 资源地址）；
  双击单条下载；批量按 `类别_名称_hash` 命名落 `导出目录/B站资源/装扮/{装扮名}/`
- 2 个新增测试（资源抽取与过滤、8 类中文名）

### B站收藏集（bili_garb.py 的「收藏集」）— 新实现
接口：`x/vas/dlc_act/act/basic?act_id=`（免签名）。

**实测**：`act_id=113100` → 「封茗囧菌·不同的你」，**6 个资源**
（活动主图 + 横版卡牌亮/暗 + 竖版卡牌亮/暗 + 卡池图）。

字段：`act_title` / `act_y_img` / `horizontal_card_light_url` / `horizontal_card_shadow_url` /
`vertical_card_light_url` / `vertical_card_shadow_url` / `animation_info{}` /
`lottery_list[].lottery_image`。

Java 已实现 `BiliGarb.parseAct()` + 该 tab：
- 按语义标注资源名（活动主图 / 横版卡牌(亮) / 竖版卡牌(暗) / 动画-xx / 卡池-xx）
- 页面：输入 act_id + 🔍加载 + ⬇下载全部 + 2 列表格；双击单条下载
- 落 `导出目录/B站资源/收藏集/{活动名}/`
- 2 个测试（资源抽取与过滤、空数据）

### B站资源剩下 2 个 tab：接口探明但未通（占位文案已写准）
试了这两条路，都卡在接口层，**保持占位并写清已探明的边界**（不放假控件）：

**🆔 装扮表情包**
- 装扮详情 `suit_items.emoji_package[0].properties` 只给**包封面** `image`，
  表情明细 `item_emoji_list` 在 `part=cards` 和 `part=emote` 下**都为空**
- `x/emote/package?ids=<装扮item_id>&business=reply` 返回 code=0 但 `packages` 为空
  —— `ids` 要的是**表情包 ID**，不是装扮 item_id
- 待办：找到「装扮 → 表情包 ID」的映射，或用正确的 part 参数

**📺 直播表情**
- `x/emote/user/panel/web?business=live` → **code=-400**（参数不被接受）
- 待办：查直播表情的正确接口（可能需要 room_id + 特定 business 值）

**B站资源 8 tab 最终状态：可用 6 / 占位 2**（本轮不再钻这两个）

### B站资源（bili_resource.py 聚合 8 tab）— Tab 结构已对齐
原版是 8 个功能 tab 平铺（顺序交错）：🗃 表情包搜索 / 🔍 装扮搜索 / 🆔 装扮表情包 /
📺 直播表情 / 👕 装扮ID详情 / 🎁 收藏集 / 👤 空间 / 🔍 综合搜索。

Java 已按同顺序建 8 个 tab：
- ✅ 可用 6 个：**🗃 表情包搜索**（11 包 × 20 表情）、**🔍 装扮搜索**、
  **👕 装扮ID详情**（52 个资源）、**🎁 收藏集**（6 个资源）、
  **👤 空间**、**🔍 综合搜索**
- ⚠️ 其余 2 个 tab 是**明确占位**（说明缺哪个模块、原版多少行），不放假控件：
  仅剩 bili_emoji 剩余部分（装扮表情包 / 直播表情）

### 公众号解析（wechat_article.py，306 行）— 已重排
原版自上而下：标题(18px)+帮助 → 输入框（**最大高 110**，placeholder 两行示例）→
`导出格式:` HTML / PDF / Word / 图片文本(MD) → `导出到:` 默认位置/固定位置 + 📁(30×26) + 路径(灰 11px)
→ 进度条 → 状态(灰) → 按钮行（`🔍 解析并导出`(绿) + `⏹ 取消`(红) + `📂 打开导出文件夹`）→ 日志。

Java 已完全对齐该顺序。HTML / MD 导出已实现（图片下载到 `{标题}_files/` 并替换引用）；
**已实现 HTML / MD / Word / PDF 导出**：Word 用 POI XWPF（标题/段落 + 已下载图片），
PDF 用 PDFBox A4 排版（自动加载 Windows 中文字体 msyh/simsun，正文自动换行，
图片追加在正文后）。

### 视频评论区（video_comments.py，467 行）— 新实现
原版结构：平台下拉（抖音/B站/微博）+ 模式下拉 → 输入行（链接/ID + 视频数 + 页数 +
`🔍 抓取评论` + `⏹ 停止`）→ 第二行（视频下拉 + `🖼 导出长图` + `💾 保存原始数据`）→
筛选行（搜索 / 昵称 / 带图 / 最低赞 / 排序）→ 评论表格。

Java 已实现（**B站可用**）：
- `BiliComments`：`x/v2/reply`（**WBI 签名**）+ type=1/oid=aid/pn/ps=20/sort=2（热评），
  自动 BV→aid（view 接口），翻页限速 300ms，`-404`/深翻页无 Cookie 都有可读提示
- 页面：平台/模式下拉、视频数/页数、抓取与停止、视频下拉、筛选（关键词/昵称/带图/最低赞/排序）
- 导出：**评论长图**（Graphics2D 绘制，昵称+点赞+时间+自动折行，中文 1 字宽/英文 0.55）+
  **原始数据**（comments.json + comments.txt，落到 导出目录/评论区/B站/{BV}/）
- 实测：BV1GJ411x7h7 抓 2 页 → 40 条评论（含用户名/点赞/内容）

**抖音/微博评论接口未移植**，切换平台时明确提示；作者模式仅抖音支持（未实现）。

### 交互重命名（rename.py，947 行）— 新实现
原版三栏：📁 日期文件夹 + 🎬 文件列表 / 👤 直播账户 + 🎯 直播类型（多选，按点击顺序）+
🗑 清除所有选择 / 📹 视频信息（版本 有弹幕·无弹幕 / 平台 / 标题 自动·手动·不保留）+
新文件名预览 + 执行重命名。

Java 已实现三栏结构与命名链路：
- `RenameRule`：`日期_时间_账户_类型1+类型2_有弹幕|无弹幕_平台_标题 + 后缀`（空字段跳过、
  类型按点击顺序、标题清非法字符）；`parseDateTime`（紧凑/分隔两种）、`guessAccount`
- 页面：扫描工作目录 `YYYYMMDD` 文件夹 → 视频列表 → 选视频自动解析日期时间并猜账户 →
  账户列表（可手动添加）/ 类型多选 / 版本 / 平台 / 标题三模式 → 实时预览新名 → 执行重命名
  （目标已存在会拒绝，成功后刷新并自动选中下一个）
- 新增 6 个命名规则测试（完整/空字段/类型顺序/非法字符/日期解析/账户猜测）

**未做**：账户/类型/平台配置持久化（原版从 config_manager 读）。
预览截图已实现：选中视频后用 FFmpeg 截第 1 秒帧（320 宽）显示在右栏。

### 音乐信息获取（music_info.py，231 行）— 新实现
原版是**五平台五列对比**：`🎵 音乐信息获取`(18px) → 搜索行（歌手 150 / 歌曲名 伸展 /
`🔍 搜索五平台`）→ 五列（酷狗 / QQ / 网易云 / 酷我 / 5sing，每列：平台名 + 全选/反选/清除 +
可勾选列表）→ 底部（`📥 获取选中歌曲详情并导出` + 状态）。

Java 已实现五列结构与导出：
- 酷我列真实可用（`KuwoScraper.searchSong`，默认歌手「封茗囧菌」）
- 其余四列 placeholder 明确写「XX 接口未移植」，不放假数据
- 导出：勾选歌曲 → JSON + CSV（UTF-8 BOM）落到 导出目录/音乐信息/

### 账号追踪（tracker.py，1114 行）— 新实现
原版顶部：`📈 网媒账号追踪` + `🔄 刷新` + `📷 按组导出` + `○ 定时采集` + `⬇ 获取全部` + 平台下拉；
两个 Tab：追踪账号（卡片网格）/ ⚙️ 追踪账号设置（添加账号 + 分组 + 已配置列表）。

Java 已实现：
- `TrackerStore`：`config/tracker_accounts.json` 读写（名称/平台/链接/分组/粉丝/获赞/采集时间）；
  `biliUid`（空间链接或纯 UID）、`formatCount`（万/亿）、`platformName`
- 页面：卡片网格（👤 名称 / 平台·分组 / 粉丝·获赞 / 采集时间 / ⬇采集 + 📷导出）、
  设置 Tab（添加账号 + 删除 + 已配置列表）、`⬇ 获取全部`（可按平台过滤）、
  `⏰ 定时采集`（30 分钟，Timeline 开关 + 绿点指示）
- 采集：**B站可用**（`card` 接口取粉丝/获赞），其他平台明确提示「接口未移植」
- 导出：单卡片 PNG / **按组导出**（每组一张拼贴，3 列卡片 320×140）
- 4 个测试（UID 解析 / 数字格式化 / 平台名 / 卡片绘制尺寸）

### 音乐账号追踪（music_tracker.py，1155 行）— 新实现
原版：`🎼 音乐账号追踪` + 🔄刷新 + 📷按组导出 + ○定时采集 + ⬇获取全部 + 平台下拉；
双 Tab：功能页（卡片：昵称/平台/粉丝/涨跌/采集·导出·历史）/ ⚙️ 设置（搜索 + 添加账号 + 组号 + 列表）。

Java 已实现纯本地全链路：
- `MusicTrackerStore`：`music_tracker_accounts.json`（账号）+ `music_tracker_history.json`
  （每个账号最多 200 条时间序列）；`delta()` 涨跌对比；`guessPlatform()` 从主页链接识别平台
- 页面：卡片网格（涨绿跌红箭头）+ 设置 Tab（链接自动识别平台 + 组号/组名 + 删除）+
  `⏰ 定时采集`（30 分钟）+ `📷 按组导出`（3 列 320×140 拼贴）+ `📜 历史`弹窗
- **采集接口全部未移植**（网易云/QQ/酷狗/酷我/5sing 歌手主页），点击采集明确提示
- 4 个测试（平台识别 / 历史追加去重与涨跌 / 配置往返 / 卡片尺寸）
- **坑**：`5sing.kugou.com` 必须先于 `kugou.com` 判断，否则被酷狗抢先命中

### 抖音房间解析 + PREP_JS（新实现，差最后一步验证）
新增 `engine/platform/douyin/DouyinLive`：
- `ttwid(webRid)`：访问直播间页，从 **Set-Cookie** 抓 ttwid（HttpOnly，`document.cookie` 读不到），缓存 6h
- `roomInfo(webRid)`：`live.douyin.com/webcast/room/web/enter/` → roomId / title / status / online / cover
- `stubMd5(roomId, uniqueId)`：拼 `live_id=1,aid=6383,...` 串取 md5
- `prepJs(roomId, uniqueId, md5)`：页面内 JS —— frontierSign 取 X-Bogus → fetch `/webcast/im/fetch/`
  → 手写 protobuf reader 解 `cursor`(f2) / `internalExt`(f5) / `pushServer`(f10)

**实测（webRid=582775732015）**：
```
ttwidLen=127
roomId=7698701141241711370   ← 拿到了
title=owo…
status=4（已结束）
md5=caa7082e812006f67ef7adf6f0df904a
acrawler=object
signature=16                 ← X-Bogus 正常
cursor=0 / internalExt=0 / pushServer=（空）
```

**为什么 pushServer 是空**：该房间 `status=4`（未开播），`im/fetch` 不返回游标。
**只差一个在播房间做最终验证**；抖音首页推荐要登录才渲染（无登录时 DOM 里没有房间链接）。

## 3.33 音乐标签页：网络匹配 / 批量匹配 / XLSX 表格匹配

- **网络匹配**：按当前文件「艺术家 + 标题」（缺失时用文件名）搜索，来源可选
  网易云/QQ/酷我/酷狗/5sing，候选表双击或选中应用；填充标题/艺术家/专辑，
  后台拉取歌词与封面（封面在「保存选中」时写入）。
- **批量匹配**：对选中文件逐一搜索（网易云优先，失败依次换源），取第一候选，
  覆盖标题/艺术家/专辑/歌词并标记 ●。
- **表格匹配**：POI 读取 XLSX 首表，按表头识别
  文件名/标题/艺术家/专辑/年份/音轨/碟/风格/专辑艺术家/歌词列，
  用文件名或标题匹配列表内歌曲，合并字段并标记待保存。

**实测**：搜索/标签写入沿用五平台已验证接口；XLSX 解析由 POI 完成。

## 3.32 音乐下载扩展：酷狗 / 5sing / 网易云 / QQ

**新增 `engine/music/KugouScraper`**（移植自 Python `music/scrapers/kugou.py`）：
- 搜索：`mobilecdn.kugou.com/api/v3/search/song`（去掉 `<em>` 高亮、时长秒归一、封面 `{size}`→480）。
- 直链：`m.kugou.com/app/i/getSongInfo.php?cmd=playInfo`（免费歌 128k 全曲；
  VIP/付费/下架给出明确错误）。
- 歌词：`m.kugou.com/app/i/krc.php`（LRC 直出；KRC JSON 做 base64+zlib 解压）。

**新增 `engine/music/FiveSingScraper`**（Python `scrapers/fivesing.py` 搜索部分）：
5sing 主站受 CloudFlare 保护，改用酷狗共享库搜索，按原唱/翻唱/伴奏规则筛选合并，
歌曲 ID 为酷狗 hash（下载走同一游客直链）。

**新增 `engine/music/NeteaseScraper`**（Python `scrapers/netease.py`）：
- 搜索：`music.163.com/api/search/get` 表单（ar/al/dt/fee）。
- 歌词：`/api/song/lyric?lv=-1`。
- 直链：公开 `song/media/outer/url?id=xxx.mp3` 的 302（免费歌可用）。

**新增 `engine/music/QQMusicScraper`**（Python `scrapers/qq_music.py`）：
- 搜索：`c.y.qq.com/soso/fcgi-bin/client_search_cp`（songmid/albummid/interval）。
- 歌词：`fcg_query_lyric_new.fcg`。
- 直链：pyq 分享接口跳转（部分免费歌可用；VIP/受限时明确报错）。

**下载器**：`MusicDownloader` 按 `MusicSong.platform` 路由到五平台，
扩展名取自平台返回，歌词写同名 `.lrc`。

**页面**：音乐下载页五平台均可搜索、勾选下载；解析模式：
- 网易云：单曲 / 歌单 / 专辑；
- QQ：单曲 / 歌单 / 专辑；
- 酷我：单曲 / 歌单（专辑 API 有反爬，UI 未开放专辑模式）；
- 酷狗：单曲 / 歌单；
- 5sing：单曲（`/m/detail/yc-xxx.html` / `yc/xxx`）与歌单页（`{用户}/dj/{id}.html`，
  解析页面内 `yc|fc|bz/数字.html` 链接，兼容用户名/数字 uid）。
酷狗概念版仍提示未移植。

**实测**：酷狗「封茗囧菌」下载 3.95MB；5sing 9 条、下载 3.95MB；
网易云搜索 3 条、直链 376 字符、歌词 1423、下载 3.49MB；单曲详情/专辑 1 首/
歌单 200 首解析正常；QQ 搜索 3 条、直链 217 字符、歌词 821、下载 960KB MP3；
单曲详情、专辑 2 首、歌单 76 首解析正常；酷我歌单 38/218 首、单曲详情正常；
酷狗歌单 96 首、单曲详情正常；5sing 单曲页解析并下载 4.36MB、歌单 8 首解析正常。

## 3.31 抖音弹幕视频引擎：预览 / FLV 录制 / 回放播放

**新增 `engine/media/LiveVideoEngine`**（移植自 Python `toolbox/live_video.py`）：
- `Player`：ffmpeg 将直播流或本地视频解码为固定 640x360 RGB24 rawvideo
  （缩放+黑边填充、15fps、`-fflags nobuffer`），GUI 定时器取最新帧写入
  `WritableImage`；带浏览器 UA + 平台 Referer，支持停止/重开。
- `Recorder`：`ffmpeg -c copy -f flv` 无损录制，stop 时向 stdin 写 `q` 优雅收尾。

**抖音直播流**（`DouyinLive.streams`）：解析 `stream_url.flv_pull_url` +
`live_core_sdk_data.pull_data.stream_data`（原画 `_origin` / 蓝光 `_md`），
页面按清晰度排序展示（原画/蓝光/1080P/720P/540P/360P/流畅）。

**页面**（`DouyinDanmakuPage`）：
- 连接成功后自动拉流并预览；切换清晰度即时生效（录制中仅影响预览）；
- ⏺ 回放包录制：FLV 存 `<导出目录>/直播录制/`，停止/断开自动打包回放包；
  「自动回放包录制」开关持久化，拿到流即开录；录制计时显示；
- 📂 导入回放包：解压 `recording.flv/.mp4` 到临时文件，自动切回放模式；
  「▶ 播放回放 / ⏸ 暂停 / ▶ 继续」+ 进度条拖动跳转 + 0.5x/1x/1.5x/2x 倍速
  （本地文件用 ffmpeg `-readrate` 按真实时间读取，避免 rawvideo 瞬间解码到结尾）；
- 回放弹幕同步：按第一条弹幕与 `recording_meta.startTimestamp` 计算视频零点，
  200ms 心跳按时间戳逐条渲染历史弹幕/礼物/进场/点赞（拖动进度会重建面板，
  补渲染最近 250 条并重建打赏榜）；无视频的回放包也能仅按弹幕时间轴回放；
- 📇 已存账号选择器：汇总最近连接 / accounts.txt / 直播录制文件；
  `douyin.com/user/sec_uid` 主页通过 CDP 页面上下文请求 profile/other 解析 web_rid；
- 软件退出时录制同步打包，避免后台 daemon 线程被进程结束截断。

**实测**：抖音在播房间获取 6 档清晰度；直播预览成功解出 640x360 帧；
6 秒 SD 流录制出 ~984KB FLV；本地视频解码正常。

## 3.30 B站资源补齐：装扮表情包 + 直播表情（8/8 tab）

**接口**（`engine/platform/bili/BiliEmote` 扩展）：
- 表情包详情：`x/emote/package?ids=&business=reply`（普通包，含 gif/webp/png）。
- 装扮表情：`x/garb/v2/user/suit/benefit?item_id=&part=cards` 的
  `suit_items.emoji_package[].properties.item_emoji_list`（实测长度 2481，可正常解析）。
- 直播间：`room/v1/Room/get_info`（真实房号/封面/网页背景/关键帧）+
  `live_user/v1/Master/info`（主播头像）+ App 签名详情（App 背景图）+
  `xlive/web-ucenter/v2/emoticon/GetEmoticons`（需登录；返回专属+通用表情包）。

**页面**（`BiliResourceEmoteTabs`）：
- 装扮表情包：输入普通表情包 ID / 装扮 item_id / 收藏集链接（自动跳「收藏集」），
  自动识别 + 强制装扮模式；格式 GIF/WEBP/PNG/全部；表情文件清单 + 全部下载。
- 直播表情：房间号/链接 → 主播头像/封面/网页+App 背景/关键帧 +
  直播间专属与通用表情（专属在前、通用置底），全部下载到
  `<导出目录>/B站资源/表情包/`。

**实测**：表情包 11 个包（首个 20 表情）、装扮表情清单、直播间 544820
（2 个表情包：通用 38 + UP主大表情 20）均获取成功。

## 3.29 账号获取页：B站/微博/抖音时间线 + 账号分组管理

**引擎**：
- `engine/accounts/AccountStore`：`config/accounts.txt`（组号/组名/账号名/URL/备注/类型）
  + `config/groups.json`，格式与 Python 版逐字段兼容（含账号名/URL 写反自动纠偏）。
- `engine/accounts/AccountFeed`：B站（WBI `arc/search` 投稿 + `feed/space` 动态）、
  微博（`m.weibo.cn/api/container/getIndex` + Cookie）、抖音（CDP 拦截
  `aweme/v1/web/aweme/post/`）、快手（CDP 拦截 `rest/v/profile/feed`）、
  小红书（主页 SSR `__INITIAL_STATE__` 花括号配平解析）；统一 Post 模型 → 中文字段 JSON。
- 日期段过滤：绝对/相对时间解析（刚刚、N 分钟/小时/天/周/月前、昨天、前天），
  解析失败的条目保留（宁多勿漏）。

**页面**：功能页（全部/选中/按组采集、条数 0=无上限、日期段、增量模式、暂停/取消、
进度条、实时日志、30 分钟/1/2 小时定时获取）；设置页（添加账号、编辑/删除/上下移、
分组多选/管理）。增量结果写 `<导出目录>/已获取数据/账号增量/<平台>.jsonl`
（按作品 ID 去重），独立模式每作品一个 JSON。

**实测**：B站空间 2 的投稿与动态、微博时间线、抖音 sec_uid 主页时间线均成功抓取
并完成时间归一化；小红书 SSR 解析有跨语言单测（含 undefined/尾逗号/new Set 容错）。
快手/小红书主页需要登录 Cookie。

**限制**：结果落导出目录，未写入 `collected.db`；定时为固定间隔
（Python 版支持起止时间对话框）。

## 3.28 购物解析：淘宝店铺商品 + 商品详情 + Excel

**引擎**（`engine/platform/shopping/Shopping`）：
- 店铺信息：游客 PC 首页（sellerId/shopId/店铺名）。
- 商品列表：`_m_h5_tk` 令牌 + `mtop.taobao.wireless.shop.index.wandering.get` 分页，
  字段兼容 wandering/wsearch/经典 PC（价格/销量/优惠券/缩略图去后缀）。
- 商品详情：`item.htm` SSR `__ICE_APP_CONTEXT__`（标题/主图/SKU/价格/物流/评分/参数）
  + `mtop.taobao.detail.getdesc` 6.0（参数 + 图文详情长图）；HTTP 失败时 CDP 渲染兜底。
- 图片：主图/SKU/详情三目录并发下载（6 线程）；Excel 由 `TaobaoXlsx` 用 Apache POI
  输出五 sheet 并嵌入已下载图片（JPG/PNG/GIF 原字节，WEBP 转 PNG）。

**页面**：店铺模式（勾选/全选/反选、排序、简介预览、逐件详情+Excel+图片）、
商品直链模式（SKU 表 + 完整详情 + 单商品 Excel）、多选导出全量 JSON（未拉详情的自动补拉）。

**实测**：默认店铺 14 件商品、商品详情（3 主图/SKU/3 参数/6 详情图）、图片下载与
Excel 导出均成功（POI 为新增依赖）。

## 3.27 票务解析：大麦 / 秀动 / B站会员购

**引擎**（`engine/platform/tickets/Tickets`）：
- 大麦：mtop 两步令牌（`_m_h5_tk` + MD5 签名，Cookie 罐自动回传），艺人页动态卡片树
  展开、演出详情（场次/限购/实名/退票规则/嘉宾/介绍 HTML/座位图/FAQ）。
- 秀动：艺人页/事件页/关键词列表 HTML 解析（Nuxt SSR 字段、票种去重）。
- B站会员购：公开搜索/详情 API（场次+票档+公告+嘉宾+头图）。
- 大麦关键词搜索：无损 HTTP 直调被 RGV587 拦 → CDP 移动端页面拦截
  `mtop.damai.wireless.search.search` 响应（带 RGV587 重试）。
- 图片下载：封面/海报/宣传/座位/嘉宾头像/艺人海报/场馆图。

**页面**：三平台链接解析 + 跨平台关键词搜索（留空默认「封茗囧菌」）、勾选批量详情、
表头三态排序、详情文本、单场失败重试、全量 JSON 导出。

**实测**：B站会员购 20 条搜索结果 + 项目详情；大麦艺人页返回演出；秀动关键词接口可用。

## 3.26 抖音指数页：创作者中心达人数据

**引擎**（`engine/platform/douyin/DouyinIndex`）：创作者中心 `api/v2/daren/*`
（搜索/作者信息/核心指标/稿均指标/达人视频/单视频指数/粉丝画像/相似达人），
仅需抖音登录 Cookie + referer + appsource；粉丝画像二次 JSON 字符串解析与性别中文化。
快照保存 `<导出目录>/抖音指数/<达人名>/抖音指数_时间戳.json` + `latest.json`。

**页面**：两步确认搜索（候选表）、统计范围、核心/稿均/每日趋势/达人视频/粉丝画像
（占比+TGI）/相似达人五个 Tab，双击视频打开、双击相似达人直接抓取。

**实测**：达人搜索与全量抓取均成功（示例达人：6 条视频 / 14 个粉丝画像维度 /
20 个相似达人，快照 + latest.json 落盘正常）。

## 3.25 抖音弹幕姬：全链路打通（CDP 签名 → WS → protobuf → 页面）

**状态**：`DouyinDanmakuPage` 已替换占位页并注册导航；`mvn clean test` 241 项全绿，
冒烟自检 35 页全部构造成功。**已用两个在播房间实测收流**（小时榜/直播数据/进场/排行榜等），
`connected` 后正常打印弹幕消息。

**链路**（`engine/platform/douyin/DouyinLiveClient`）：
1. `room/web/enter` 取 roomId/主播/标题/在线/封面；未开播直接回 `not_live`（页面轮询负责重试）
2. 无头 Edge + CDP：**先覆盖 UA（去掉 HeadlessChrome）与视口 1920×1080**，再注入 Cookie 打开直播间
3. **截页面自身 `im/fetch` 响应**（`Network.responseReceived` + `Network.getResponseBody`）→
   cursor/internalExt/pushServer；用 internalExt 里的 `wss_push_did`（退回 URL 的
   `user_unique_id`）重算 X-Bogus，保证签名与 did 同源
4. 自造 `prepJs` 请求仅作兜底（2026-10 起抖音校验 a_bogus/did，自造请求会拿到空体）
5. `Network.getCookies` 取 ttwid（HttpOnly，`document.cookie` 读不到）+ sessionid（礼物需要登录态）
6. Java-WebSocket 连 pushServer（UA/Origin/Cookie）→ gzip 解压 → protobuf PushFrame/Response/Message
7. 10s 心跳（`3a026862`）+ needAck 回执；确认收到推送后才关浏览器释放 Cookie

**踩坑（两个都会导致 200 + 空响应体）**：
- 无头 Edge 默认 UA 含 `HeadlessChrome`、视口 800×600 → 抖音直接返回空体；
  必须 `Emulation.setUserAgentOverride` + `setDeviceMetricsOverride`
- 自己拼 `a_bogus='00000000'` + 固定 `user_unique_id` 的 im/fetch 也会拿到空体 →
  改用截获页面自身请求的响应

**页面能力**（对齐 Python `test_page.py` 抖音形态）：
- 三栏：主播卡（封面/粉丝/在线/累计观看/点赞/备注）；弹幕区（聊天/礼物/点赞/进场开关 +
  关键字 / @昵称搜索）；右侧（房间号 + WS、打赏榜、事件日志）
- 打赏榜按**连击会话**增量结算：`repeatEnd` 收尾、反复送 x1 算新一轮、粉丝团名称/价格兜底
- 实时落盘 `导出目录/弹幕文件/实时记录/{room}_{yyyyMMdd}.jsonl`；连上后回读今日尾巴恢复
- 📊 统计每 10s 采样写 `弹幕文件/{room}_{yyyyMMdd}.csv`（UTF-8 BOM 表头）
- 💾 保存弹幕 / 📦 导出回放包 / 📂 导入 / 🧩 合并（dycast 全量格式：data.json +
  全量弹幕.json + 分类 TXT + cover）
- 🔁 轮询等开播、自动重连、断开自动保存、软件退出时 `shutdown()` 同步打包
- `captcha` 状态明确提示（不弹验证窗口）；视频预览/FLV 录制/回放视频见 3.31。

**测试**：`DouyinLiveProtocolTest` 覆盖 protobuf 解码 / 心跳 / 帧解析 / im/fetch 响应解析 /
did 提取；页面纳入冒烟自检。

## 3.24 抖音弹幕姬：CDP 基础设施打通（页面 JS + X-Bogus 签名）

**为什么需要浏览器**：抖音弹幕不是纯 WebSocket —— 连接参数（cursor/internalExt/pushServer）
必须在**页面上下文**里跑 `fetch('/webcast/im/fetch/')` 才能拿到，签名靠
`window.byted_acrawler.frontierSign({'X-MS-STUB': md5})['X-Bogus']`（纯 HTTP 伪造不了）。

**方案选择**：不加 Playwright（~50MB + 额外下载浏览器），改用**系统 Edge + CDP**：
`msedge --headless=new --remote-debugging-port=<port> --user-data-dir=<tmp>`，
用已有的 Java-WebSocket 连 CDP，`Runtime.evaluate` 跑 JS。

**新增 `engine/browser/CdpClient`**（约 300 行）：
- `findBrowser()`：按顺序找 Edge/Chrome（Program Files (x86) → Program Files → Chrome）
- `launch(port, timeout)`：起进程 → 轮询 `/json/list` → **筛 `type=page` 且非 `chrome-extension://`**
  （踩坑：不筛会连到扩展后台页，JS 全部 undefined）→ 连 CDP → `Page.enable` + `Runtime.enable`
- `navigate(url)`：`Page.navigate` + 轮询 `document.readyState`
- `evalJson/evalString`：`Runtime.evaluate`（awaitPromise + returnByValue，异常带 description）
- `cookies()` / `currentUrl()` / `close()`（销毁进程 + 删临时 profile）

**实测**：
```
browser=C://Program Files (x86)\Microsoft\Edge\Application\msedge.exe
launched OK
url=https://live.douyin.com/
acrawler=object          ← window.byted_acrawler 可用
frontier=function        ← frontierSign 可用
sigLen=16  sig=6sZQqOiiEPAmBnLA   ← X-Bogus 签名拿到了
```

**下一步**：`roomId` 要先用 HTTP `room/web/enter` 拿（页面 DOM/RSC 里取不到），
再执行完整 PREP_JS（fetch im/fetch → 解析 protobuf 的 cursor/internalExt/pushServer），
最后 Java 直连 WS + protobuf 解弹幕。（已完成，见 3.25）

## 3.23 B站弹幕姬：打通了！（根因 = 取 token 与握手必须用同一份完整设备 Cookie）

**根因（对比 Python 逐层排查得出）**：
1. Python `build_cookie_header` 返回 **4 段 cookie（366 字节）**：
   `buvid3` + `buvid4`（都来自 `x/frontend/finger/spi` 的 `b_3`/`b_4`）+ `b_nut`（当前秒）
   + `bili_ticket`（`GenWebTicket`，HMAC-SHA256("XgwSnGZ1p", "ts"+ts) 签名）
2. Java 之前只带 `buvid3`（71 字节）→ 握手 1006
3. **更关键**：`getDanmuInfo` 取 token 时也必须带**同一份**完整 cookie。
   只带 buvid3 拿到的 token，在握手时会被拒（即使握手带了完整 cookie）。

**修复后实测**：
```
devCookieLen=326（buvid3 + buvid4 + b_nut + bili_ticket）
tokenLen=256
[status] 弹幕通道已打开，正在鉴权…
[status] 已发起认证（房间 7734200）
[status] 已连接弹幕通道 ✅
TOTAL=7（收到事件）
[status] 弹幕连接已关闭（1000）
```

**实现**：
- `BiliLiveClient.guestCookie()`：spi 取 b_3/b_4 → b_nut=当前秒 → GenWebTicket 取 ticket →
  拼 4 段 cookie（进程内缓存 1 小时）
- `danmakuAuth(roomId, cookie, buvid, deviceCookie)` 重载：取 token 时带同一份设备 cookie
- `BiliDanmakuPage`：房间号 + 🔌连接/⏹断开 + 实时弹幕列表（最多 500 条）+ 房间信息
  （标题/在线/是否直播中）+ 切页自动断开

**排错历程（供后人少走弯路）**：先怀疑 JDK WebSocket → 换 Java-WebSocket 仍 1006 →
再怀疑 buvid 来源 → 再怀疑握手 Cookie → 最后发现**取 token 的 Cookie 不一致**才是真因。
库从来不是问题。

## 3.22 B站弹幕：换库后仍 1006 —— 结论修正为「认证内容差异」

按计划把 JDK `java.net.http.WebSocket` 换成 **Java-WebSocket 1.5.7**（纯 Java，约 200KB），
`connect()` 重写为 `WebSocketClient` 匿名子类（onOpen 发认证包 + 心跳，onMessage 喂 drain()），
其余（WBI 鉴权、封包、zlib 解压、事件分发）一行未改。

**结果：仍然 `1006` 关闭，收不到 `op=8`。**

| 变量 | 试过的值 | 结果 |
|---|---|---|
| WebSocket 库 | JDK 内置 / Java-WebSocket 1.5.7 | 都是 1006 |
| buvid 来源 | 首页 Set-Cookie / finger-spi b_3 | 都 1006 |
| 握手 Cookie | 不带 / 带设备 Cookie（71 字节） | 都 1006 |
| host | host_list[0] / 默认 broadcastlv | 都 1006 |
| 发送延迟 | 0 / 150ms | 都 1006 |

**结论修正**：之前判断「JDK WebSocket 与该服务端不兼容」是错的 —— **换库同样失败**，
说明问题在**认证内容**（token 或 buvid 与 Python 版存在差异，Python 用同一套协议能收到 `op=8 code=0`）。

**下一步要做的**：抓 Python 版实际发出的 WS 认证帧（原始字节）与 Java 的逐字节对比，
重点看 `token` 字段（Python 侧 256 字符，Java 侧 260-264 字符，长度就对不上）与 `buvid`。

**当前状态**：依赖与实现保留（Java-WebSocket 比 JDK 实现更好用），页面仍未接。

## 3.21 UI 对齐原则（用户实测反馈后确立）

用户实测反馈：「UI 差别还挺大」→ 确立迁移标准：**布局/交互优先贴近原版，功能对齐其次**。

**做法**：先读原版页面的控件树（`QGroupBox`/`QHBoxLayout`/`QTableWidget`/`QLabel` 的文案与顺序、
`setFixedWidth`、样式色值），再在 JavaFX 里按同样结构搭；不再自创布局。

**已按此重做的页面**：

### 资产管理（asset.py，原版 1500+ 行）
原版是「页头 + 三 Tab + 卡片式列表」，之前误做成「单表格统计日期文件夹」。现已重做：
- 页头：`🗂 资产管理`(16px 粗体) + 右侧 `💾 备份`
- Tab 顺序：**已获取资源 | 数据库浏览 | 本地资源**
- 已获取资源：`📦 已获取资源`(18px) + 工具条（`平台:`下拉 120 / 搜索框伸展 / 状态下拉 110 /
  排序 90 / 🔄 / 全选 / `线程:` 70 / `📥 下载所选` 绿底）+ 卡片行（90×60 图标 + 标题 + 详情）
- 数据库浏览：6 列表格（平台/ID/标题/作者/发布/本地路径）
- 本地资源：`📁 本地资源`(18px) + 目录行 + 策略行（`上限:`50 / `(0=全部)` / `分批间隔(秒):`40 /
  `增量模式(写入索引)` / `🔍 扫描` 绿底）+ 缩略图卡片（图片真缩略图 90×60，视频🎬 音频🎵）+ 加载更多
- 💾 备份：导出 导出目录 + collected.db 为 zip

**待按此重做的页面**：info_parse / info_batch / info_results / netease / unlock_music / music_tag /
image_crop / cover_maker / image_sorter / media_compress / bili_resource / wechat_article
（这些当前是「功能对、布局简化」，需逐个对照原版控件树重排）

## 3.20 页面迁移总览（目标：35 页全部真实实现）

**已完成 35 / 35**：
解析类 info_parse / info_batch / info_results / info_accounts / bili_resource / wechat_article /
video_comments；音乐类 netease / unlock_music / music_tag / music_info；
追踪类 music_tracker / tracker；指数类 douyin_index；
工具类 toolbox_convert / toolbox_audio / toolbox_migrate / toolbox_calendar / toolbox_launcher /
toolbox_organize / toolbox_rename / rename_batch / file_time / lyrics_tool / toolbox_subtitle；
媒体类 image_crop / cover_maker / image_sorter / media_compress；资产 asset；设置 global_settings；
直播类 bili_danmaku / douyin_danmaku；票务 tickets；购物 shopping。

**剩余 0 页**。35 个导航页与已知子项（B站资源 8 tab、账号时间线 5 平台、
抖音弹幕视频预览/录制/回放）均已实现。

## 3.19 B站弹幕姬：协议已通，JDK WebSocket 握手被拒（未接页面）

**已实现的（`BiliLiveClient`）**：
- 房间信息 `room/v1/Room/get_info`（实测：房间 6 → real 7734200 / live=1 / 20 万在线）
- 游客 buvid3：优先 B站首页 `Set-Cookie`（与 Python `build_cookie_header` 一致），
  退回 `x/frontend/finger/spi` 的 `b_3`
- 弹幕鉴权 `getDanmuInfo`：**必须 WBI 签名**（不签名 -352），实测能拿到 token（256-264 字符）+ host
- 协议封包/解包：`>IHHII`（总长/16/1/op/1）+ body；op=7 认证 / op=2 心跳 / op=8 回执 / op=5 消息；
  protover=2 用 zlib（`Inflater`）解压后拆内部帧；单测覆盖封包字节布局与解压往返

**卡住的**：JDK `java.net.http.WebSocket` 在 open 后发送认证帧，服务端立即
`1006` / `Connection reset`，收不到 `op=8`。对照实验：
- **Python websocket-client 用同样的 URL、同样的认证体、同样的 token 格式 → 成功**
  （`op=8 {"code":0}` + 4 个 op=5 消息帧）
- 已排除：token 长度（260 字符正常）、buvid 来源（首页 Set-Cookie 与 spi 都试过）、
  代理（显式 `ProxySelector.of(null)`）、发送延迟（150ms）、host_list 与默认地址
- 结论：**JDK WebSocket 实现与该服务端不兼容**，换库（Java-WebSocket / OkHttp / Netty）大概率可解

**替代路线也空**：`xlive/web-room/v1/dM/gethistory` 返回 `code=0` 但 `room_size=0`（该房间无历史缓存）

**当前状态**：页面**未接**（避免用户点了没反应）；引擎与协议单测保留，换库后可直接复用。

## 3.18 M5 收尾：小红书能力边界（实测）

**能做的**（纯 HTTP + Cookie，已实现 `XiaohongshuParser`）：
- `GET /explore`（带 Cookie）→ `__INITIAL_STATE__.feed.feeds`：**推荐流 34-35 条笔记**
  （id / displayTitle / 作者 / type / 时长 / 封面 / xsecToken）
- 登录态：`user.loggedIn` + `userInfo.nickname`
- **坑**：SSR 里含 JS 的 `undefined`，Jackson 直接解析会失败 → 必须 `.replace("undefined","null")`

**做不了的**（硬门槛）：
- 笔记详情页 `/explore/{id}?xsec_token=` 的 SSR **不含笔记数据**（只有 UI 配置 + 搜索提示）
- 媒体（视频直链 / 图片原图）只能走 `edith.xiaohongshu.com/api/sns/web/v1/feed`，
  该接口要 **x-s 签名**（前端 `_webmsxyw` JS 生成，含 x-t/x-s-common）
- headless 浏览器访问 `/explore` 直接被「安全限制」页拦（85KB 验证页）
- 结论：小红书下载 = 逆向 x-s 或真实浏览器（非 headless）+ 登录态，工作量明显大于抖音/快手

**当前实现**：解析页识别小红书链接 → CDP 启动系统 Edge（注入登录 Cookie）打开笔记页，
读取 `og:video`（视频直链）与 `#noteContainer` 内的正文图片（含懒加载、按图片 ID 去重、
排除头像/表情/推荐流封面）→ 下载为 `<noteId>.mp4` / `<noteId>_N.jpg|webp`。

**实测**（`FMJJ_NET_TEST=1 mvn test -Dtest=XiaohongshuProbeIT -Dfmjj.home=<项目根>`）：
- 视频笔记 → 直链 1.89MB 下载成功，正文图片 0（不混入推荐流）
- 多图笔记 → 6 张图，首张 126KB 下载成功
- 无 Cookie 时视频直链仍可读（`og:video` 在 SSR 里），图片笔记需要登录态渲染 `#noteContainer`

**仍未做**：纯 HTTP 的 x-s 签名（`edith.xiaohongshu.com/api/sns/web/v1/feed`），
目前每次解析会起一次无头 Edge（约 10 秒）；探索流列表能力保留在引擎，未接 UI。

## 3.17 M6 第二刀（B站资源：空间 + 综合搜索）

- `BiliResource`：`x/web-interface/card`（免签名）+ `x/web-interface/wbi/search/all/v2`（WBI 签名）
- **WBI keys 自取**：`WbiSigner` 只做签名，keys 要自己从 `x/web-interface/nav` 的
  `data.wbi_img.img_url / sub_url` 取 basename（去扩展名）；`fetchKeys()` 已补上
- 搜索结果是「分组」结构（`result[].result_type` + `data[]`），只保留 `video` / `bili_user`，
  其余类型（番剧/影视/专栏）忽略；标题里的 `<em>` 高亮要剥掉，`//` 开头图片补 `https:`
- 页面 `BiliResourcePage` 用 TabPane 装 2 个 tab（空间 / 综合搜索），原版 8 tab 的
  表情包×3、装扮×3 待补
- **真实验证**（`FMJJ_NET_TEST=1 mvn test -Dtest=BiliResourceNetworkIT`）：
  UID 2 → 碧诗 143.2 万粉 Lv6；WBI keys 正常；搜索「封茗囧菌」→ 21 条（含「【囧菌翻唱】东京不太热」）
- 注意：surefire 用 `fmjj.home=target/test-home`，所以网络 IT 里 `CookieStore.get` 返回空是**预期**
  （游客态也能用 card/search，登录态只在页面运行时生效）

## 3.16 M8 第一刀（媒体工具）+ M9（打包分发）

**M8 媒体工具**（4 个页面，纯本地无接口）：
- `ImageTools`：居中裁切（正方形/16:9/3:4）、强制拉伸、等比缩放（不放大）、
  封面（背景铺满 + 底部渐变条 + 标题/副标题，Graphics2D 渲染）
- `MediaDedup`：先按文件大小预分组，再对同尺寸算 MD5（省哈希开销），按可省空间排序
- 页面：图片裁切 / 封面制作 / 媒体筛选（重复删除进回收站）/ 媒体压缩（libx264 + CRF 四档预设）
- 测试 14 个：裁切比例、不放大、封面尺寸、PNG 往返、JPG 透明压平、MD5 已知向量、大小预筛、扩展名过滤

**M9 打包分发**：
- app-image 自带精简 JRE（148MB），目标机免装 Java
- 环境自检（`EnvironmentCheck`，7 项）：Java 版本 / FFmpeg / FFprobe / 浏览器（抖音）/
  数据目录可写 / 磁盘 / 登录态；入口在「全局设置 → 环境自检」
- 隐私校验：`package_exe.bat` 默认生成**分发包（不含 cookies.json）**；
  自用执行 `package_exe.bat keep` 才带登录态
- **msi/exe 安装器未做**：jpackage 需要 WiX Toolset（本机未装）。当前交付形态是绿色版 app-image。

## 3.15 抖音解析路线（已实现：系统浏览器 dump-dom）

**结论：纯 HTTP 不可行，用系统 Edge/Chrome 的 `--dump-dom` 可行。**

实测数据（2026-10-23，样本 aweme `7415554945589513513`）：

| 路线 | 结果 |
|---|---|
| 分享页 `iesdouyin.com/share/video/{id}/` | 200，32KB，**`videoInfoRes` 缺失**（假 ID 页面反而有骨架，真实 ID 没有数据） |
| PC 页 `douyin.com/video/{id}` | 200，72KB 空壳，无 `RENDER_DATA`/`__INITIAL_STATE__`/`_ROUTER_DATA` |
| 老接口 `iteminfo` / `snssdk` / `m.douyin` | **200 + 空体**（Argus 特征） |
| **Edge `--headless=new --dump-dom`** | ✅ 1.1MB 渲染后 DOM：`<source src>` 直链、`data-e2e-aweme-id`、`<title>`、`data-e2e="user-info"` 内昵称 |

实现要点：
- `DouyinBrowser`：按 Edge(x86/x64) → Chrome 顺序找浏览器，`--virtual-time-budget=5000`，
  临时 profile，读完 stdout 后删目录；可用 `FMJJ_BROWSER` 环境变量覆盖路径。
- `DouyinParser.parse()`：先试 HTTP 分享页（有 SSR 就直接用），失败降级浏览器；`fromDom()` 解析 DOM。
- **提取坑**：`user-info` 区块内昵称藏在多层 `<a><span>` 里，且外层 style 很长，
  用「正则去标签 + split」会残留 `<div`；改为 `firstTextNode()` 逐标签跳过取第一个非空文本节点。
- 耗时：首次约 51s（含 HTTP 尝试 + 冷启动），优化后 **约 14s**（降 virtual-time-budget 到 5000）。
- 代价：需要目标机有 Edge/Chrome（Windows 自带 Edge，满足）；比 Python 版 Playwright 轻（不下载 Chromium）。

**快手（已实现，待真实链接验证）**：
- `v.m.chenzhongtech.com/fw/photo/{id}` 是空壳；但 `www.kuaishou.com/short-video/{id}`
  在**带完整请求头**（`Accept: text/html...` + `Accept-Language: zh-CN`）时返回 75KB SSR 页面，
  内含 `window.__APOLLO_STATE__`（Apollo GraphQL 缓存）—— 纯 HTTP 可读，不需要浏览器。
- 坑：只带 UA + Referer 时返回 `{"result":2,"error_msg":null}`（风控），加 `Accept`/`Accept-Language` 就好。
- 解析策略：Apollo 缓存 key 形态会变（`Photo:xxx` / `VisionVideoDetailPhoto:xxx`），
  所以用 **DFS 找「含 caption 且有视频地址」的对象**，不写死路径；
  视频直链优先 `manifest.adaptationSet[].representation[].url` 里的非 H265。
- **未验证**：本机拿不到真实 photoId（首页/发现页是 CSR，无 ID），需要一条真实快手链接。

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
