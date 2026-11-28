# 标准版与 FULL 内置版

本文件是双版本发布的唯一正文，入口为 [summarize-update](../SKILL.md)；旧名 [release-editions](../../release-editions/SKILL.md) 仅作兼容跳转。修改 build.py、内置清单、依赖解析或排查“装了仍缺依赖”，以及构建/发布 FULL 时完整读取。总约定、真实凭据与实例保护仍适用；读取本文件不授权构建、发布或接入真实数据。

## 当前状态与两形态

**FULL 已实现**：build.py 支持 --prepare-full 与 --edition standard|full；运行时由 `src/toolbox/edition.py` 判断形态，updater.py 已分流安装器。下文区分当前源码事实、必须遵守的契约和仍需实际验收的能力，不把历史版本测量当成本轮结果。

| 项 | 标准版 | FULL 内置版 |
| --- | --- | --- |
| 定位 | 保持既有轻量形态，外部组件按需准备 | 内置下表指定组件；可选本地总结组件除外 |
| 默认安装器名 | `FengMingToolbox_Setup_<版本>.exe` | `FengMingToolbox_Full_Setup_<版本>.exe` |
| 版本/源码 | 同一 APP_VERSION 与同一发布源码 | 同左，不独立编号 |
| 更新源 | 同一 Release，严格匹配标准前缀 | 同一 Release，严格匹配 FULL 前缀 |
| 安装身份 | 同一 AppId、默认安装目录及配置 | 同左，覆盖安装路径，不做第二套用户数据 |
| 实际体积 | 以本次文件字节数为准 | 以本次文件字节数为准，不承诺固定 MB |

现有 AppId 为 `8F6A2C71-9D4E-4B57-A3C2-7E9F01B5D384`；构建产物名默认取 APP_NAME，使用 --name 时核对更新器前缀，不可因更名导致更新不可达。不要把标准版“不改变既有行为”误写为跨依赖/工具版本“逐字节相同”。

## 形态与资源契约

- edition() 的实际顺序：开发/测试环境变量 `FMJJ_EDITION`（standard/full）→ 程序根 `FULL_EDITION` 标记 → standard。APP_VERSION 只在 `src/version.py` 维护，形态不写成第二版本号。正式验收排除意外环境变量覆盖。
- 标准版与 FULL 共用代码、配置与 AppId，不增加第二更新地址、账户库、配置目录或依赖安装入口。运行数据不可写时的用户目录回退按当前实现核对，不借切换形态迁移/清空真实数据。
- 全局设置是安装/下载/卸载唯一入口；FULL 仅对实际存在并通过检测的内置项显示“已内置”。不能因标记存在便把所有组件判为可用；Git、node+ npm、本地总结等可选项不因此变为内置。

| 组件 | 包内位置 / 当前查找 | 构建与验收要求 |
| --- | --- | --- |
| FFmpeg / ffprobe | `bin/`；utils.py 当前 PATH → 程序目录（包根、bin、ffmpeg 等候选）→ 用户自定义 | 复制干净、许可证合规的 exe；干净环境确认无需系统 PATH |
| Playwright headless shell | `browsers/chromium_headless_shell-<rev>/`；frozen 且包内目录存在时 browsers_dir() 选包内，否则用户 LocalAppData | 只放 headless shell，不放完整 Chromium 或浏览器 profile；启动前设置 PLAYWRIGHT_BROWSERS_PATH |
| 本地转写程序 | `models/whisper-faster.exe` | 从可信的纯程序文件准备，不整目录复制用户 models |
| 转写模型 | `models/faster-whisper-base/`；build.py 的 `WHISPER_MODELS` 当前仅 base | 仅打指定 base；small/medium/large 按需，不因本机已下载而带入 |
| CLIP 图识别 | `models/clip/*.onnx`；image_embedding.py 配置路径 → 包内模型 → `config/models` | 隔离且无自定义路径时证明命中包内模型 |
| 抖音签名 node | Playwright driver 自带 node.exe；multi_downloader._find_node() 为 PATH → driver → 常规/宿主安装位 | 复用已有 driver node，不额外塞 `bin/node/node.exe`；内置 node 不含 npm，不能当作资料站前端构建环境 |
| 7-Zip | 既有 `_internal/toolbox/bin` 资源 | 保持既有 ADD_DATA 与第三方声明 |

运行时浏览器启动顺序仍为系统 Edge → Chrome → 默认本地内核；“包内路径优先”只描述 headless 资源目录，不能声称 FULL 改成每次只用内置浏览器。任何改查找顺序仍需核对录播核心等共享影响授权。

## 当前实现的验收风险

以下是静态源码核对可见的限制，文档更新不等于已修复代码：

- prepare_full_vendor() 找不到 headless shell 时只打印警告；stage_full_bundle() 的 missing 清单检查 FFmpeg、ASR 与 CLIP，**没有强制确认 browsers 内核完整**。因此构建命令成功不等于离线浏览器依赖齐全，发布前必须额外核对目录、实际可执行文件和干净环境启动。
- asr_engine.get_models_dir() 会尝试在程序 models 下写探针，不可写时退 APPDATA。不能仅凭包里有转写文件便声称 Program Files 安装下会命中它；须在真实的只读安装权限条件下使用隔离测试环境验收，未做则标未验证。
- 全局设置的 `_FULL_BUNDLED` 当前覆盖 ffmpeg、ffprobe、pw_shell、asr_program、asr_model、clip_model，且检测 ok 才标“已内置”。不要写成“所有检测项均已内置、无条件零缺失”。

本轮若只是技能维护，只记录这些边界，不为文档整理自动修改 build.py、运行时、用户安装或真实配置。

## 构建与隐私

1. 完整发布按主入口授权与版本流程。准备独立构建目录，使用已确认的本次发布源码（含待提交的版本号修改），不引用用户运行目录。最终 tag 内容必须与构建源码对应。标准版先构建并验收，再构建 FULL；后一次构建会清理共享 `dist/<name>` 与 `build/`，先保存前一形态的验证结论和安装器。
2. python build.py --prepare-full 将指定资源准备到 `build_full_vendor/`；当前会复制 PATH 中 FFmpeg、`models/whisper-faster.exe` 和已安装的 headless shell，并下载指定模型。因此执行前核对来源纯净、许可和输出范围，不把整个本机 `models/` 或浏览器目录无筛选复制。供应商目录不能含账户、profile、日志或历史输出。
3. 标准版使用 python build.py --edition standard；FULL 使用 python build.py --edition full。FULL 装配把 `bin/`、`models/`、`browsers/` 追加到 `dist/<name>/` 并写 `FULL_EDITION`。标准版产物中不得有该标记或残留 FULL 组件。
4. 不使用 --skip-build 把残留 FULL 目录直接重新封成标准版；若显式跳过构建，必须先验证目录正是本次指定源码与形态。失败停止，不能绕过隐私扫描或删除保护。
5. build.py 隐私扫描和可选 LLM 排除必须通过。`config/`、`logs/`、`download/`、真实 Cookie/Token、账号数据、浏览器 profile、用户缓存不得入包。模型本身的 `models/**/config.json` 是参数文件，不等于真实应用配置；仅按当前扫描的明确例外核对，不扩成通用 config 白名单。
6. 对每个实际声明发布的安装器记录名称、字节数、形态与 SHA-256。build.py 按本版本磁盘存在的标准/FULL 安装器写 `SHA256SUMS.txt`，每包一行；清单中的每包都必须来自本次已验证产物，不能悄悄混入旧文件。
7. 同一个 Release 上传安装器与一份校验文件：标准单包通常 2 个必需资产，标准+FULL 通常 3 个；有额外获准资产时按实际清单核对，不固化资产数。

`build_full_vendor/` 是本地构建材料，不提交不推送。体积预算应来自本次组件测量，模型权重压缩率不能沿用可执行文件的估值。若工具或托管限制导致需分卷，先给出安装/更新兼容方案，不擅自变更发行格式。

## 本地总结组件不随 FULL 内置

用户于 2026-11-26 明确指定，优先于“全部外部依赖内置”等概括：

1. CPU 引擎、Vulkan GPU 引擎与 GGUF 大语言模型始终为用户自选可选组件，标准/FULL 均不预装、不自动下载，不因缺少它们计入必需依赖缺失或显示“已内置”。这不改变 ASR、CLIP、FFmpeg 与浏览器原有清单。
2. 安装/下载仍只在全局设置；CPU/GPU 与模型分别选择，总结功能页只消费已配置组件，不新建安装入口。
3. 存放于 `optional_components/local_llm/`，不可写时使用运行时 LocalAppData 后备位置；这些目录和模型不属于 `build_full_vendor`，不得复制进任何发行包。
4. 引擎采用受控官方 llama.cpp 固定版本和下载校验，保留原始许可/第三方声明；GGUF 许可按各自模型卡，不以引擎许可概括权重。不默认引入 llama-cpp-python 或 CUDA 大包。
5. PyInstaller/.spec 排除开发环境 llama_cpp，保留其他功能依赖。FULL 装配前及最终标准/FULL 产物都应阻断 `optional_components/local_llm`、GGUF、llama-server、llama 库误收集；发现时中止，不自动删除用户已下载内容。
6. CPU、Vulkan 真实推理、硬件兼容和实际包体积分别验收；静态排除/夹具扫描不能写成真实构建或推理通过。

## 更新与形态切换

- updater 按互斥前缀选择标准或 FULL，禁止模糊匹配串包；同一 `SHA256SUMS.txt` 为各资产提供对应行。
- FULL 包必须与同版本标准包/发布源码一致，不能拿上一版 FULL 冒充本版。某版仅供标准时，Release 明确说明，FULL 客户端应提示当前无 FULL 包；不假称本版双包齐全。
- 标准→FULL 为相同 AppId 覆盖升级；FULL→标准可能残留内置组件与标记，不宣称简单覆盖就变干净标准版。说明卸载重装或受控切换方案，任何执行仍需用户针对具体安装操作的授权，真实凭据禁令不放宽。
- 不为了切换形态删除真实配置、账户或下载记录；升级数据保留测试只用独立虚构数据和隔离安装。

## 许可与验收

发布前核对 THIRD_PARTY_NOTICES.md 中 FFmpeg 构建对应 GPL/LGPL、Playwright/Chromium NOTICE、Node.js、转写程序/模型、CLIP 与 7-Zip 的实际来源和许可；不内置带不兼容专有组件（如 libfdk-aac）的 FFmpeg。来源或许可不清晰即停止该组件分发，不能用旧版本声明代替本次核对。

| 验收 | 必须证明的结果 |
| --- | --- |
| 干净且隔离的 Windows 环境 | 未靠本机 FFmpeg/node/浏览器/模型 PATH 掩盖缺失 |
| 断网安装与首次启动 | 声明内置组件不需要临时联网下载；可选本地总结仍保持可选 |
| 只读安装权限 | Program Files 场景下浏览器、ASR、CLIP 路径实际有效，用户数据走正常回退 |
| 功能抽测 | FFmpeg、base 转写、图识别、抖音签名、所需浏览器路径及弹幕长连接分别验证，未测明确标注 |
| 形态与更新 | 标记/环境变量解析正确，两形态只挑对应安装器、哈希校验通过 |
| 虚构数据覆盖升级 | 标准→FULL 的假配置与下载记录保留；不接触真实账户 |
| 资产/隐私 | 本地、校验文件与 Release 的名称/字节/哈希一致，无运行数据、凭据或可选 LLM 误打包 |

功能抽测必须先核对录播八模块等授权，不能因发布文档列为验收就自行进入保护区。完整结果与未验证项按主入口和总约定报告。
