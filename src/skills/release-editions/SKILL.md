---
name: "release-editions"
description: "标准版与 FULL 内置版的双形态打包与发布规则：FULL 版把 FFmpeg、Playwright 内核、本地转写程序与模型、图识别模型、node 等外部依赖全部内置，做到装完即用。新增/修改发布形态、调整内置清单、构建 FULL 包或排查「某功能仍提示缺依赖」时必读。"
---

# 双版本发布（标准版 / FULL 内置版）

本 skill 定义同一套代码产出两种安装包的规则。当前发布流程（`summarize-update`）只出标准版；FULL 版尚未实现，落地时按本文档执行。

## 触发场景

- 用户提到「FULL 版」「内置版」「完整版」「开箱即用」「离线可用」「两版本」「双版本打包」。
- 修改 `build.py` 的 `ADD_DATA`、Inno 模板、依赖查找顺序，或调整内置资源清单。
- 排查「FULL 版装完仍提示安装 ffmpeg / node / 模型」这类问题。

## 一、两版本定义

| 项 | 标准版（现有形态，不可改变） | FULL 内置版 |
| --- | --- | --- |
| 定位 | 体积小，外部依赖按需下载 | 一次安装、断网可用，装完无「缺失」提示 |
| 安装包名 | `FengMingToolbox_Setup_<ver>.exe` | `FengMingToolbox_Full_Setup_<ver>.exe` |
| 版本号 | `src/version.py` 的 `APP_VERSION` | **同一个** `APP_VERSION`，不单独编号 |
| 更新源 | 同一 GitHub Release | 同一 Release；资产按版本筛选，互不串包 |
| 依赖面板 | 现状：检测 + 下载按钮 | 全部显示「已内置」，只留「打开所在目录」，不再给下载按钮 |
| 安装目录 | `{autopf}\FengMingToolbox` | 同一 AppId、同一默认目录（覆盖安装，不并存） |
| 预估体积 | 127.9 MB（v3.0.2 实测） | 约 550–650 MB（以 base 模型计，见体积预算） |

**红线**：不允许给 FULL 版单独的第二套版本号、第二个更新地址、第二套配置目录或第二份依赖清单。两种形态共用一份代码、一份 `APP_VERSION`、一个 AppId、一个 Release。

## 二、FULL 版内置清单

| 组件 | 包内位置 | 现在代码里的查找点 | FULL 要求 |
| --- | --- | --- | --- |
| ffmpeg / ffprobe | `<app>\bin\` | `toolbox/utils.py::_bundled_binary`（root、`bin/`、`ffmpeg/bin/` 已支持） | 直接放入 `bin/`，无需改查找顺序 |
| Playwright 内核 | `<app>\browsers\chromium_headless_shell-<rev>` | `toolbox/browser.py::browsers_dir` / `ensure_env` | frozen 态优先指向包内 `browsers/`，不存在再落 `%LOCALAPPDATA%\FengMingToolbox\browsers` |
| 本地转写程序 | `<app>\models\whisper-faster.exe` | `toolbox/asr_engine.py::get_models_dir` | 已被 `get_models_dir()` 的「程序目录/models」覆盖 |
| 本地转写模型 | `<app>\models\faster-whisper-base\` | 同上 `local_model_dir` | 只内置 `base`（与默认 `asr_local_model` 一致）；其余档位仍按需下载 |
| 图识别模型 | `<app>\models\clip\vision_model_quantized.onnx` | `toolbox/image_embedding.py::default_model_dir`（现为 `config/models`） | 增加「包内 `models/clip` → `config/models`」的查找顺序 |
| node（抖音 a_bogus 签名） | `<app>\bin\node\node.exe` | `multi_downloader.py` 只 `shutil.which("node")`；设置页检测同 | 必须加内置回退：PATH 找不到时用包内 node |
| 7-Zip | `<app>\_internal\toolbox\bin` | 已随包（`ADD_DATA`） | 不变 |

**FULL 版不放**：用户 Cookie、账号、日志、下载缓存、`config/` 下任何文件；也不放本机 `models/` 的整目录副本（见隐私与体积）。

## 三、体积预算（实测基线）

| 项 | 未压缩 | LZMA 后（估算） | 说明 |
| --- | --- | --- | --- |
| 标准版整体 | 435 MB | 127.9 MB | v3.0.2 实测安装包 |
| ffmpeg + ffprobe（gyan release-essentials） | ≈250 MB | ≈75 MB | 只取两个 exe |
| chromium_headless_shell | 277.5 MB | ≈100 MB | 只内置 headless shell，不内置完整 Chromium（436 MB） |
| whisper-faster.exe | 84.3 MB | ≈35 MB | 本地转写程序 |
| faster-whisper-base 模型 | ≈145 MB | ≈140 MB | 权重压缩率低，别按 3:1 估 |
| CLIP 视觉编码器 onnx | ≈90 MB | ≈88 MB | `vision_model_quantized.onnx` |
| node.exe | 89.2 MB | ≈35 MB | 仅签名用，单文件即可 |
| **FULL 合计（估算）** | **≈1.4 GB** | **≈550–650 MB** | 以 base 模型为准 |

- 内置 `small` 模型会再增加约 330 MB（本机实测 463.7 MB 原始），**默认不内置**；`medium` / `large` 一律按需下载。
- GitHub 单个资产上限 2 GB，FULL 包在限内；但 Inno 生成的单 exe 建议控制在 2 GB 以下，超过需分卷，此时必须同步改发布流程。
- 若后续要压体积，优先顺序：模型档位（base → tiny）＞ headless shell 精简 ＞ ffmpeg 换更小构建；node 不建议去掉（抖音签名会退化）。

## 四、代码契约（落地时必须遵守）

1. **版本标记只有一个来源**：包根放 `FULL_EDITION` 标记文件（内容 `full`），运行时 `edition()` 读它；读不到一律视为 `standard`。不要把 edition 写进 `version.py`，避免双份事实。
2. **依赖查找顺序**（标准版行为不得变化）：
   - ffmpeg / ffprobe：PATH → 程序目录（已有）→ 用户自定义位置。
   - node：PATH → `<app>\bin\node\node.exe` → 报错。
   - Playwright：包内 `browsers/`（存在时）→ 用户 LocalAppData。`PLAYWRIGHT_BROWSERS_PATH` 必须在 `sync_playwright()` 之前设置，事后设置无效。
   - CLIP 模型：用户配置路径 → 包内 `models/clip` → `config/models`。
3. **依赖面板**：FULL 版每项显示「已内置」+ 版本号，中性/通过色，不计入缺失数；隐藏下载按钮。标准版保持现状，不因 FULL 改造改动文案与入口分级（约定 11 仍成立：安装入口统一在全局设置）。
4. **更新器**：`toolbox/updater.py` 的资产筛选按 edition 分支——标准版匹配 `FengMingToolbox_Setup_`，FULL 版匹配 `FengMingToolbox_Full_Setup_`；两者的 `startswith` 集合互斥，禁止用模糊匹配把 Full 包发给标准版用户。
5. **安装/升级**：两版本共用 Inno 的 `AppId={{8F6A2C71-9D4E-4B57-A3C2-7E9F01B5D384}`。标准版 → FULL 版可直接覆盖升级；FULL 版 → 标准版属降级，安装器不会删除多余的内置文件，必须提示「先卸载再装，否则残留下载模型与内核」。
6. **配置与数据**：两版本共用同一配置目录（Program Files 不可写时落 `%APPDATA%\FengMingToolbox\config`），切换版本不迁移、不清空数据。

## 五、构建改造（build.py）

1. 新增参数 `--edition standard|full`，默认 `standard`；标准版产物、体积、行为与现在逐字节一致的构建参数不得增删。
2. FULL 分支只做三件事：
   - 追加内置资源到 `dist/<name>/`（`bin/ffmpeg.exe`、`bin/ffprobe.exe`、`bin/node/node.exe`、`browsers/`、`models/`、`FULL_EDITION` 标记）。
   - Inno 模板 `OutputBaseFilename` 加 `_Full` 后缀，`AppName` / `UninstallDisplayName` 标注「FULL 版」，`[Registry]` 追加 `Edition=full`。
   - 隐私扫描范围不变，但额外断言 `dist` 内不得出现 `config/`、`logs/`、`download/`、`cookies.json`、`accounts*`，以及 `models/` 下不得混入转写缓存或历史文件。
3. 内置资源来源必须是**干净下载目录**（例如 `build/vendor/`），不得直接复制本机 `models/`、`%LOCALAPPDATA%\ms-playwright\chromium-*`（完整 Chromium 不内置）或任何用户数据。
4. 构建后生成包含两行的 `SHA256SUMS.txt`：
   ```
   <sha256>  FengMingToolbox_Setup_<ver>.exe
   <sha256>  FengMingToolbox_Full_Setup_<ver>.exe
   ```
5. 发布时 Release 上传 4 个资产之前先核对：两个 exe 体积、两行 SHA、FULL 包内 `FULL_EDITION` 存在、标准版包内不存在该标记。

## 六、发布与更新流程

1. 版本号、tag、Release 仍只有一份，沿用 `summarize-update` 的第 1–6 步；FULL 版作为**额外资产**上传，不新建 Release、不新建 tag。
2. 构建顺序：先标准版（必须与历史产物一致），再 FULL 版；两步都通过隐私扫描与离屏启动烟测才允许上传。
3. 更新器验证：标准版用户检查更新只能看到标准资产；FULL 版用户看到 FULL 资产与约 600 MB 的体积提示；两版本的 `SHA256SUMS.txt` 一致（同一文件两行）。
4. 若某次只发标准版（例如紧急修复），FULL 版沿用上一版资产会与代码不一致——**不允许**。要么同版本补发 FULL，要么在 Release 说明中明确「本版本暂不提供 FULL 版」，由 `updater` 对 FULL 用户提示「该版本暂无 FULL 包，请手动下载标准版或等待下一版」。

## 七、许可与第三方声明

FULL 版把更多第三方二进制随包分发，发布前必须在 `THIRD_PARTY_NOTICES.md` 增补：

- FFmpeg（按所选构建的 GPL/LGPL 版本，附源码获取地址与许可证全文入口）
- Playwright / Chromium headless shell（Apache-2.0 / BSD 类，附 NOTICE）
- Node.js（MIT，附 LICENSE）
- faster-whisper 模型（按模型卡许可，附来源与许可）
- CLIP ONNX（MIT，附来源）
- 7-Zip（LGPL + unRAR 限制，已随包，保持现有声明）

项目本身是 GPL-3.0，可与 GPL/LGPL 组件共存；**禁止**内置带专有编解码器（如 libfdk-aac）的 ffmpeg 构建。

## 八、验收清单（缺一不可）

| 项 | 判定 |
| --- | --- |
| 干净环境 | 全新 Windows 虚拟机，未装 ffmpeg / node / Playwright / 模型 |
| 断网安装 | 安装过程不需要联网；装完首次启动不弹任何下载提示 |
| 依赖面板 | 全部组件显示「已内置」，缺失数为 0 |
| 功能抽测 | FFmpeg 转码、音频提取转写（base）、图识别、抖音签名、浏览器登录、弹幕长连接各跑一次 |
| 更新 | 标准版只看到标准包；FULL 版只看到 FULL 包；SHA 校验通过 |
| 覆盖升级 | 标准版 → FULL 版覆盖安装后配置、Cookie、下载记录不丢 |
| 体积/哈希 | 两个 exe 体积与 `SHA256SUMS.txt`、Release 资产三方一致 |
| 隐私 | 包内无 `config/`、`logs/`、`download/`、Cookie、账号、用户模型缓存 |

## 九、分阶段落地

- **P0（代码契约）**：`edition()` 标记读取、node / CLIP 的内置回退、依赖面板「已内置」文案。此时仍只发标准版，行为不变。
- **P1（构建与发布）**：`build.py --edition full`、Inno 双产物、`SHA256SUMS.txt` 两行、发布流程追加 FULL 资产、FULL 版离屏烟测。
- **P2（体验与优化）**：安装器内选择版本（标准 / FULL 二选一）、跨版本切换提示、体积优化（headless shell 精简、模型档位可选）、`THIRD_PARTY_NOTICES` 完整化。

## 十、常见坑

1. **直接复制本机 `models/`**：会带进 `small`（463.7 MB）与转写缓存，FULL 包体积失控且可能夹带用户数据。只允许从干净目录挑 `base`。
2. **把完整 Chromium 打进包**：完整版 436.4 MB，且大多数场景只用 headless shell；内置 headless shell 即可，登录仍走系统 Edge/Chrome。
3. **忘记 node 回退**：FULL 版仍会提示「未找到 node 环境」，因为现有代码只查 PATH。
4. **CLIP 模型放错目录**：`default_model_dir()` 是 `config/models`，不是包内 `models/`，不加回退会显示「未下载模型」。
5. **Playwright 环境变量设晚了**：必须在 `sync_playwright()` 之前设置，否则仍去用户目录找内核。
6. **AppId 不同**：给 FULL 换新 GUID 会变成两套程序、两个卸载项、两份配置，升级路径断裂。
7. **资产筛选不严**：标准版误匹配到 `FengMingToolbox_Full_Setup_*` 会把 600 MB 包推给只想小体积的用户。
8. **FULL → 标准直接覆盖**：残留的内置内核与模型不会被 Inno 删除，需明确提示卸载重装。
9. **只测标准版**：FULL 的失败点集中在「查找顺序」和「只读目录」，必须在 Program Files 安装路径下测，不要在可写目录里测完就发布。