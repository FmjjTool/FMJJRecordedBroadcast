# 项目级强制约定（所有 Agent 必读）

> 本文件是项目级的必读入口。任何 Agent（AI 编程助手、子 Agent、自动化任务）在执行任何操作前，必须先完整阅读本文件及所引用的约定 skill，阅读后方可操作。

## 约定 skill

完整内容见 [src/skills/convention-skill/SKILL.md](src/skills/convention-skill/SKILL.md)。

## 约定 1：更新前必须拉取最新代码

- 任何更新动作前，先确认 `origin` 为 `https://github.com/FmjjTool/FMJJRecordedBroadcast.git`，再执行 `git pull origin main`（或先 `git fetch origin` 再合并）。
- 拉取失败（网络不可达、认证失败、合并冲突、本地未提交改动阻塞、非 git 仓库、远端不匹配等）时：立即停止，禁止在旧代码上直接更新，必须向用户提问或按其他相关 skill 处理。
- 禁止未拉取就更新、禁止拉取失败后强行更新、禁止 `git push --force` 等破坏远端的操作。

## 约定 2：改动前先备份，完成后必须本地提交

- 执行任何更改操作前，先执行 `git status` 检查当前代码是否有备份（已保存为本地 commit）。
- 存在已修改/未跟踪文件（未备份）时，先本地 `git commit` 完成备份，再开始操作；提交前清理临时文件与测试产物。
- 无法提交（非 git 仓库、合并冲突、钩子失败等）时立即停止，向用户提问，禁止直接改动。
- 每次任务或编辑部分完成后，必须本地 `git commit` 一次，并按需向用户汇报提交哈希。
- 本地提交不等于推送；是否推送按用户指示或相关 skill（如 `summarize-update`）执行。
