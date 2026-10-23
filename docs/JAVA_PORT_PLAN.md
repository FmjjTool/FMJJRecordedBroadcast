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
| **M5 解析与下载** | ✅ B站/微博/抖音/快手 解析下载、多线程分段、批量导入、已获取、SQLite；⚠️ 小红书：能读探索流元信息，**媒体下载需 x-s 签名**（见 3.18） | ✅ 完成（小红书受限） |
| **M6 信息/资产/追踪页** | 🚧 已完成：单个/批量解析、已获取、酷我搜索下载、资产管理、B站资源（空间+搜索）、**公众号解析**；⬜ 剩余：账号获取、抖音指数、B站表情包/装扮、音乐信息、账号追踪×2、视频评论区 | 🚧 进行中 |
| **M7 音乐高级** | ✅ 完成：NCM/QMC(v1+v2)/KGM/KWM/XM/TM/X2M/X3M 解密 + 标签读写（JAudiotagger）；⬜ 剩余：网络匹配/封面补全（随 M6 音乐搜索） | ✅ 完成 |
| **M8 票务购物与直播** | ✅ 媒体工具 4 页；🚧 B站弹幕：协议已实现但 JDK WebSocket 被拒（见 3.19，需换库）；⬜ 剩余：大麦/秀动/会员购、淘宝商品、抖音弹幕姬 | 🚧 进行中 |
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
搜索模式：B站走 `BiliResource.searchAll`，结果弹窗 7 列（平台/标题/作者/播放/时长/发布时间/链接），
双击回填链接并解析；抖音/微博搜索未实现（明确提示，不假装）。

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
**未实现**：网络匹配 / 批量匹配 / 表格匹配（需五大音源与 XLSX 解析），点击明确提示。

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

**已完成 24 / 35**：
解析类 info_parse / info_batch / info_results / bili_resource / wechat_article；
音乐类 netease / unlock_music / music_tag；
工具类 toolbox_convert / toolbox_audio / toolbox_migrate / toolbox_calendar / toolbox_launcher /
toolbox_organize / toolbox_rename / rename_batch / file_time / lyrics_tool / toolbox_subtitle；
媒体类 image_crop / cover_maker / image_sorter / media_compress；资产 asset；设置 global_settings。

**剩余 11 页与预估**：

| 页面 | 原版规模 | 依赖 | 难度 |
|---|---|---|---|
| music_info | 231 行 | 多平台音乐接口（网易云/QQ/酷狗） | 中（接口已有部分） |
| video_comments | 467 行 | B站评论（reply/wbi） | 低（WBI 现成） |
| music_tracker | 1155 行 | 本地统计 + 平台数据 | 中（统计逻辑多） |
| tracker | 1114 行 | 本地统计 + PNG 卡片导出 | 中（要 Graphics2D 画卡片） |
| info_accounts | 1769 行 | 12 平台账号信息接口 | 高（页面最大） |
| wechat_article | ✅ 已完成（306 行） | — | — |
| douyin_index | 大 | 抖音接口（风控） | 高 |
| tickets | 大 | 大麦/秀动/会员购（签名） | 高 |
| shopping | 大 | 淘宝（签名） | 高 |
| bili_danmaku | — | JDK WebSocket 被拒（见 3.19） | 需换库 |
| douyin_danmaku | — | 抖音 WS + 签名 | 高 |

**建议顺序**：video_comments（WBI 现成）→ music_info（复用酷我/网易云）→ music_tracker/tracker（纯本地）
→ info_accounts（多平台，量大）→ 票务/淘宝/弹幕（签名与 WS，最后）

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

**当前实现**：解析页识别小红书链接 → 展示 noteId / 是否有 xsec_token → 提示「媒体下载未实现」，
不假装能下载。探索流列表能力已进引擎，可随时接到 UI。

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
