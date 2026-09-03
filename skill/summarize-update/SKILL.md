---
name: "summarize-update"
description: "版本发布流程：推送本地提交 → 版本号递增（默认 +0.0.1）→ 打包安装器 → 更新 README 更新日志 → 推送 GitHub → 创建 Release 并上传安装包。当用户说「总结此次更新」或要求发布新版本时调用。"
---

# 总结此次更新（版本发布流程）

触发词：用户说「**总结此次更新**」。本流程 = 把本次累积的提交发布为新版本并上传安装包到 GitHub Releases。

## 硬性前提

1. **工作区必须干净**（`git status` 无未提交修改）。有未提交内容先按项目规范提交（测试脚本/临时文件必须先清理再提交）。
2. 本 skill 被触发 = 用户对打包的**明确授权**（项目规则「仅在明确指示时打包」的唯一放行条件）。

## 版本号规则（重要）

- **默认补丁位 +0.0.1**（如 2.8.4 → 2.8.5），**除非用户明确要求**其他递增幅度（如 minor/major）。
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

- 产物：`dist/installer/FengMingToolbox_Setup_v{新版本}.exe`（约 275MB，PyInstaller + Inno Setup）
- 耗时 10 分钟级：后台运行 + 轮询等待完成，不要前台死等
- build.py 内置隐私扫描（Cookie/配置/账号进包自动中止）——扫描失败立即**中止整个流程**并报告，严禁绕过
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
$tok = ""
foreach ($l in (($in | git credential fill) -split "`n")) {
  if ($l -match "^password=(.+)$") { $tok = $Matches[1] }
}
$H = @{ Authorization = "Bearer $tok"; Accept = "application/vnd.github+json" }
```

**a) 建本地 tag 并推送**：

```powershell
git tag {新版本}
git push origin {新版本}
```

**b) 创建 Release**（非 draft、非 prerelease，name = tag，与历史格式一致）：

```powershell
$notes = @"
## 更新说明

> 对比 v{上一版本}：共 N 个提交、N 个文件变更（+N / −N 行）

### 分组标题
- 条目……（与 README 更新日志同内容）
"@
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
- 中文正文必须走 `UTF8.GetBytes`（PS5.1 直接传字符串会乱码）

**c) 上传安装包资产**：

```powershell
$exe = "dist/installer/FengMingToolbox_Setup_v{新版本}.exe"
$uri = "https://uploads.github.com/repos/FmjjTool/FMJJRecordedBroadcast/releases/$($rel.id)/assets?name=$(Split-Path $exe -Leaf)"
Invoke-RestMethod -Method Post -Uri $uri `
  -Headers @{ Authorization = "Bearer $tok" } `
  -InFile $exe -ContentType "application/octet-stream" -TimeoutSec 1800
```

- 275MB 大文件上传按分钟计，超时给足（1800s）
- 未安装 gh CLI，一律走 REST API，不要尝试 gh 命令

### 验证与汇报

- `GET /repos/FmjjTool/FMJJRecordedBroadcast/releases/tags/{新版本}` 确认：非 draft、assets=1、大小 ≈275MB
- 向用户汇报：新版本号、Release 链接（`https://github.com/FmjjTool/FMJJRecordedBroadcast/releases/tag/{新版本}`）、本次更新内容摘要

## 安全红线

- token（gho_ 开头）只允许在内存变量中传递，任何情况下不得打印、写入文件、出现在提交或日志里
- 隐私扫描中止 = 流程中止，不得以任何方式绕过
- 全流程任何一步失败：报告用户并停止，不跳步续跑
