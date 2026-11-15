# 项目级强制约定（所有 Agent 必读）

> 本文件是项目级的必读入口。任何 Agent（AI 编程助手、子 Agent、自动化任务）在执行任何操作前，必须先完整阅读本文件及所引用的约定 skill，阅读后方可操作。

## 约定 skill

完整内容见 [src/skills/convention-skill/SKILL.md](src/skills/convention-skill/SKILL.md)。

## 约定 1：更新前必须拉取最新代码

- 任何更新动作前，先确认 `origin` 为 `https://github.com/FmjjTool/FMJJRecordedBroadcast.git`，再执行 `git pull origin main`（或先 `git fetch origin` 再合并）。
- 拉取失败（网络不可达、认证失败、合并冲突、本地未提交改动阻塞、非 git 仓库、远端不匹配等）时：立即停止，禁止在旧代码上直接更新，必须向用户提问或按其他相关 skill 处理。
- 禁止未拉取就更新、禁止拉取失败后强行更新、禁止 `git push --force` 等破坏远端的操作。
