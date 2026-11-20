---
name: "summarize-update"
description: "开发阶段质量闭环与版本发布流程：编辑后适用测试、唯一当日报告和本地提交；明确请求推送/发布时按用户范围处理。总结文章、工作总结、直播总结及仅要求「总结此次更新」排除自动推送和发布；提及/否定推送不授权执行。未命中排除项的发布工作按全文流程执行。"
---

# 更新总结与版本发布流程

发布相关触发词：用户明确要求「**开始推送**」「**远端推送**」「**更新推送**」「**推送**」或发布新版本。仅要求「**总结此次更新**」按下面的总结排除项处理。

## 总结工作排除项（先判断，优先适用）

- 用户仅要求总结文章、直播总结、工作总结、更新内容汇总或「总结此次更新」时，只完成总结及项目要求的报告/必要本地提交，**不执行本 skill 第1—6步，不 push、不打包、不改版本、不创建 tag/Release**。
- 引用、解释、检查推送流程，或说「不需要 push」「不推送」，都不是执行授权；创建/修改本 skill 也不触发发布。
- 用户另行明确要求推送/发布时，才按其具体范围进入相应流程，用户的限制优先于默认流程。总结工作与发布工作分别判断，本地提交不自动升级为推送。

本流程 = 把本次累积的提交发布为新版本并上传安装包到 GitHub Releases。本 skill 同时固化开发阶段约定（每部分编辑质量闭环），发布流程须待大任务结束、离屏测试全过后执行。

## 执行要求（强制，不可跳过）

1. 先核对总结排除项；仅在明确请求的推送/发布任务中，必须**完整阅读本 skill 全文**（含硬性前提、版本号规则、第 1-6 步、安全红线），理解每一步的输入、产物与失败处理。
2. 按第 1 步到第 6 步**顺序完整执行**，不得跳步、合并步骤或提前结束；任何一步失败立即停止并报告，不得续跑。
3. 未命中总结排除项、且用户没有另外限定范围时，触发词「推送 / 开始推送 / 远端推送 / 更新推送」按既有完整发布流程处理；总结请求、引用词汇和否定推送不构成该授权。用户明确只推送或不打包等限制时，以用户具体范围为准。
4. 每一步完成后核对产物：提交已推送、版本号已改、安装包存在且可启动、README 已更新、Release 资产大小与本地一致。
5. 全流程结束后按「验证与汇报」向用户汇报版本号、Release 链接、更新摘要与提交哈希。

## 开发阶段约定：每部分编辑质量闭环（必须执行）

任何代码更新/修改任务，**每完成一个部分**（一个页面/模块/功能的一次编辑批次）后，依次执行：

1. **冒烟测试**：对本部分改动涉及的模块做导入/实例化级验证（页面实例化冒烟需 `QT_QPA_PLATFORM=offscreen` 下建 QApplication，且 app 实例需补 `work_dir`/`toast`/`show_page` 属性）；导入错误、启动即崩必须当场暴露。冒烟失败先修复到通过。
2. **更新当日报告**：按 `convention-skill` 约定 4，在项目根目录写（或更新）`Progress/YYYY-MM-DD_当日总结.md`，日期取北京时间的完成日期；内容含：问题/需求 → 根因/方案 → 改动文件清单 → 验证结果 → 待处理问题 → Git 提交信息。同一天的多个任务、多部分修改和后续验证都汇入同一文档；更新前读取已有内容，保留其他任务的记录，不另开主题报告。文档与代码一起提交进库。
3. **传本地 git 一次**：先清理测试脚本/临时文件/无用代码（项目规则：垃圾不进库），再连同 Progress 文档一起提交本部分改动。
4. **提交结果**：向用户汇报本部分改动内容、冒烟测试结果、提交哈希。

**离屏测试是最后的测试**：不在每部分后运行；待**所有部分编辑完成、当前大任务结束后**统一执行一次完整离屏测试——发现问题修复→重测直至通过，通过后做收尾 git 提交并向用户汇报最终结果。

上述闭环走完、工作区干净后，方可触发下方发布流程。

## 硬性前提

1. **工作区必须干净**（`git status` 无未提交修改）。有未提交内容先按项目规范提交（测试脚本/临时文件必须先清理再提交）。
2. 仅明确的发布请求且未附“不打包”等限制时，才适用打包前提；总结排除项或对本 skill 的维护/阅读不授权打包。

## 版本号规则（重要）

- **默认补丁位 +0.0.1**（如 2.8.4 → 2.8.5），**除非用户明确要求**其他递增幅度（如 minor/major）。
- **补丁位到 9 后必须进位**：不存在 2.8.10 这类两位补丁号——2.8.9 + 0.0.1 = **2.9.0**（次版本 +1、补丁归 0）。历史 tag 全部符合此规（2.8.8 → 2.8.9 → 下一个就是 2.9.0）。
- 版本号唯一来源：`src/version.py` 的 `APP_VERSION`，改这里且只改这里。
- 上一版本 tag = 当前 git 最新 tag（`git tag -l` 取最大值），用于提交范围与统计。

## 流程（顺序执行，不可跳步）

### 第 1 步：推送本地提交

```
git push origin main
```

先把手头累积的全部提交同步到远端，再做后续动作。

### 第 2 步：递增版本号（打包前置）

改写 `src/version.py` 的 `APP_VERSION` 为新版本号（规则见上）。
必须在打包**前**完成——安装器文件名内嵌版本号。

### 第 3 步：打包

```
python build.py
```

- **bash 里跑必须加 `PYTHONPATH=` 前缀**（如 `PYTHONPATH= python build.py`）：WorkBuddy 沙箱的 safe-delete shim 会拦截 dist 清理（文件数超阈值直接 SystemExit），清空 PYTHONPATH 等价于卸掉 shim
- 产物：`dist/installer/FengMingToolbox_Setup_{新版本}.exe`（约 92MB，**文件名无 v 前缀**，轻量版定版后体积从 275MB 降至此；PyInstaller + Inno Setup）
- 耗时 3 分钟级：后台运行 + 等待完成通知，不要前台死等
- build.py 内置隐私扫描（Cookie/配置/账号进包自动中止）——扫描失败立即**中止整个流程**并报告，严禁绕过
- **打包后必须真的跑一次 exe**（隐私扫描 + 体积检查发现不了「能启动但切页就炸」）：
  `cd dist/FengMingToolbox && QT_QPA_PLATFORM=offscreen timeout 12 ./FengMingToolbox.exe`，
  退出码 124（跑到超时）= 正常；stderr 出现 Traceback 即中止流程。跑完删掉它生成的
  `config/ download/ logs/` 再压缩/上传（否则把本机数据打进包）。
  实测坑：页面是懒加载的（`importlib` 按字符串导入），PyInstaller 静态分析看不到
  `gui_qt.pages.*`，`--collect-submodules` 在 spec 阶段 sys.path 无 src 时收集为空——
  必须在 build.py 里按 `src/gui_qt/pages/*.py` 目录生成 hidden-import。
- 打包失败：中止流程并报告错误，不执行后续步骤

### 第 4 步：更新 README

- README.md 第 7 行附近「当前版本下载」链接改为 `releases/tag/{新版本}`
- 「### 更新日志」标题下、最新条目之上插入新条目：

```
#### v{新版本}（YYYY-MM-DD 当天日期）

- **分组标题**：改动归纳……
```

- 内容来源：`git log {上一版本tag}..HEAD --oneline`，按主题分组归纳（如 新功能 / 修复 / 重构），语气与历史条目一致
- 与 `src/version.py` 一起提交：

```
git add src/version.py README.md
git commit -m "docs: v{新版本} 版本发布（更新日志 + 版本号）"
```

### 第 5 步：推送 GitHub

```
git push origin main
```

### 第 6 步：创建 Release 并上传安装包

**凭据获取**（GCM，token 仅内存传递，严禁打印/落盘/写日志/提交进 git）：

```powershell
$in = "protocol=https`nhost=github.com`n`n"
$out = $in | & "C:/Users/$env:USERNAME/.workbuddy/binaries/PortableGit/versions/1.3.0/mingw64/bin/git-credential-manager.exe" get
$tok = ""
foreach ($l in ($out -split "`n")) {
  if ($l -match "^password=(.+)$") { $tok = $Matches[1].Trim() }
}
$H = @{ Authorization = "Bearer $tok"; Accept = "application/vnd.github+json" }
```

> PortableGit 版本目录会随客户端升级变化（实测 1.2.0 → 1.3.0）。先
> `ls ~/.workbuddy/binaries/PortableGit/versions/` 取当前版本；找不到时退回
> `D:/Program File/WorkbuddyAI/resources/vendor/PortableGit/mingw64/bin/git-credential-manager.exe`
> （即 `git config credential.helper` 指向的那个）。

**执行方式注意（实测坑）**：
- 必须把 PowerShell 代码写成 `.ps1` 文件后 `powershell -ExecutionPolicy Bypass -File xxx.ps1` 执行——不能 `powershell -Command "..."`（外层双引号会吞掉 `$` 变量），也不能 `cmd /c`（工具宿主禁用）
- 子 PowerShell 里 `git credential fill` 包装层不工作（报 missing protocol field），必须直接调用 GCM exe（上面写法）
- `.ps1` 文件必须**纯 ASCII**：PS 5.1 把无 BOM 的 UTF-8 脚本当 ANSI 读，中文会乱码甚至语法错误——中文内容（Release 正文等）写到单独的 UTF-8 `.txt`，脚本里用 `[IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)` 读入

**a) 建本地 tag 并推送**：

```powershell
git tag {新版本}
git push origin {新版本}
```

**b) 创建 Release**（非 draft、非 prerelease，name = tag，与历史格式一致）：

Release 正文写到 UTF-8 文本文件（如 `_tmp_notes.txt`），格式：

```
## 更新说明

> 对比 v{上一版本}：共 N 个提交、N 个文件变更（+N / −N 行）

### 分组标题
- 条目……（与 README 更新日志同内容）
```

```powershell
$notes = [IO.File]::ReadAllText("<notes.txt 绝对路径>", [Text.Encoding]::UTF8)
$json = @{
  tag_name = "{新版本}"; name = "{新版本}"
  body = $notes
} | ConvertTo-Json
$rel = Invoke-RestMethod -Method Post `
  -Uri "https://api.github.com/repos/FmjjTool/FMJJRecordedBroadcast/releases" `
  -Headers $H -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($json))
```

- 统计头数字来自 `git log {上一tag}..HEAD --oneline | Measure-Object -Line` 和 `git diff --shortstat {上一tag}..HEAD`
- JSON body 必须走 `UTF8.GetBytes`（PS5.1 直接传字符串会乱码）
- release id 存临时文件供上传步骤用：`$rel.id | Out-File -Encoding ascii _tmp_relid.txt`

**c) 上传安装包与校验文件资产**（两个都要传）：

```powershell
$files = @(
  "dist/installer/FengMingToolbox_Setup_{新版本}.exe",
  "dist/installer/SHA256SUMS.txt"
)
foreach ($f in $files) {
  $uri = "https://uploads.github.com/repos/FmjjTool/FMJJRecordedBroadcast/releases/$($rel.id)/assets?name=$(Split-Path $f -Leaf)"
  Invoke-RestMethod -Method Post -Uri $uri `
    -Headers @{ Authorization = "Bearer $tok" } `
    -InFile $f -ContentType "application/octet-stream" -TimeoutSec 1800
}
```

- `SHA256SUMS.txt` 由 `build.py` 自动生成（`<sha256>  <安装包名>`）；客户端的「检查更新」强校验它，**缺这个文件时自动安装会拒绝，只允许手动下载**
- 92MB 上传按分钟计，超时给足（1800s）
- 未安装 gh CLI，一律走 REST API，不要尝试 gh 命令

### 验证与汇报

- `GET /repos/FmjjTool/FMJJRecordedBroadcast/releases/tags/{新版本}` 确认：非 draft、assets=2（安装包 + SHA256SUMS.txt）、安装包大小 ≈92MB（与本地 `dist/installer` 产物字节数一致）
- 再取 `SHA256SUMS.txt` 内容，确认哈希与本地 `Get-FileHash` 结果逐字一致（客户端按它校验，不一致会导致所有用户更新失败）
- **仓库为私有仓库**：验证的 GET 也必须带 Authorization 头（GCM 取 token 同上）——无凭据返回 404，曾被误判为 Release 不存在
- 向用户汇报：新版本号、Release 链接（`https://github.com/FmjjTool/FMJJRecordedBroadcast/releases/tag/{新版本}`）、本次更新内容摘要

## 安全红线

- token（gho_ 开头）只允许在内存变量中传递，任何情况下不得打印、写入文件、出现在提交或日志里
- 隐私扫描中止 = 流程中止，不得以任何方式绕过
- 全流程任何一步失败：报告用户并停止，不跳步续跑
