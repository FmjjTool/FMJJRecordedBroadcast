# 并行协作与文件所有权（Agent Coordination）

先阅读 [convention-skill](../../convention-skill/SKILL.md)。明确推送 / 发布或维护构建、内置清单、依赖解析时再读 [summarize-update](../../summarize-update/SKILL.md)；涉及数据迁移时再读 [数据生命周期](../../layering-contract/references/data-lifecycle.md)；涉及测试时再读 [testing-quality-gates](../../testing-quality-gates/SKILL.md)。

本 skill 只补 **多人/多 Agent/多进程同时改代码** 的协调规则；单 Agent 串行任务的 Git、备份、报告、发布仍按 `convention-skill` 与 `summarize-update` 执行。

## 默认工作基线与并行决策

多个聊天或 Agent 同时推进时，默认继续在当前 `main` 工作区完成本次修改，不因并行本身创建支线、分支或 worktree。先按文件路径核对所有权：若本次目标文件与其他在途改动完全无关，可直接在 `main` 修改，并只按路径暂存、提交自己的文件；保留其他 WIP，不因这些无关改动另行询问。

只有在同一目标文件已有其他写入方、无法安全保留他人 WIP，或任务确实需要隔离高风险过程时，才采用独立支线 / worktree 或暂停冲突部分。该默认不放宽真实 Cookie、核心模块授权、共享文件单 writer、备份失败、提交失败及其他安全边界。

## 开工前

1. 执行 `git status`、`git log --oneline -10`，确认当前工作区是否有他人在途改动。
2. Git 准备只按总约定 Git 细则做一次；先确认目标分支映射，不把非 main 或 detached worktree 与 origin/main 的计数误当同分支状态。
3. 在任务清单/回复中声明：本次要改哪些文件/模块、只读还是写入、预计是否触碰共享文件。
4. 读取目标文件的最新内容，不依赖记忆中的旧版本；文件可能已被其他 Agent 或用户改写。
5. 识别共享文件：`AGENTS.md`、`src/skills/convention-skill/SKILL.md`、`src/gui_qt/main_window.py`、`src/toolbox/config_manager.py`、`src/toolbox/task_center.py`、`src/gui_qt/theme.py`、`src/gui_qt/common.py`、`src/version.py` 等。共享文件默认串行修改，修改前重新读取。

## 文件所有权

1. **一个文件同一时间只允许一个写入方**。两个 Agent 不得同时编辑同一页面、同一服务或同一存储模块。
2. 页面/模块按目录划分所有权；跨模块改动先约定接口，再各自修改自己的文件。
3. 如果必须在同一文件并行，先拆分为独立小改动，或使用独立 worktree/分支；不要在同一工作区互相覆盖。并行聊天本身不构成创建支线的理由。
4. 发现目标文件出现自己没写过的改动：立即停止编辑，重新读取并判断来源；不得直接覆盖、回退或删除他人改动。
5. 长任务（录制、批量下载、迁移、全量测试）开始前告知可能影响的文件/目录；结束或取消后再让其他 Agent 接手。

## WIP 与提交

1. 按总约定处理本任务拥有的稳定 WIP；他人仍在变化的 WIP，若与本次目标文件完全无关，可留在 `main` 并按路径提交自己的文件，不必为此询问；若同文件、共享语义重叠或无法安全保留，再协调或使用独立干净 worktree。任何情况下都不擅自备份提交、暂存、删除或覆盖他人 WIP。不要 git add -A 混入无关文件。
2. 提交前逐项检查 `git diff`，确认没有：
   - 真实 Cookie/Token/密钥/账号数据；
   - `config/`、`download/`、`logs/` 下的运行时数据；
   - 临时脚本、测试截图、缓存、构建产物。
3. 一个提交只表达一个主题；并行任务按文件路径拆开提交，不把不同 Agent 的改动混在一个提交里。
4. 提交说明写清“为什么”和影响范围；提交后执行 `git status` 确认没有遗漏或误提交。
5. 不擅自提交他人的 WIP；若已获明确备份授权且确认不会干扰活动修改，才可按路径备份；提交说明标明 `chore: backup work-in-progress`，并在回复中告知对方。
6. 本地提交不等于推送；推送按用户指示或 `summarize-update` 执行，推送前重新 fetch/比较。

## 禁止的 Git 操作

未经用户明确授权，禁止：

- `git reset --hard`、`git checkout -- <file>`、`git restore <file>` 等会丢弃工作区改动的操作；
- `git clean -fd`、`rm -rf` 删除未跟踪文件/目录；
- 裸 `git stash` / `git stash pop`（多人工作区会互相串 stash）；
- `git push --force` / `--force-with-lease` 改写远端历史；
- `git commit --amend` 修改已存在的提交（除非用户明确要求且该提交未推送）；
- 跳过钩子（`--no-verify`）或绕过签名。

必须暂存时：使用 `git stash push -m "<唯一标签>"`，记录 stash ref；恢复用 `git stash apply <ref>`，只 drop 自己创建的条目，不用 `pop`。

## 冲突与分叉

1. 发现本地/云端分叉：列出共同基线、双方独有提交、逐文件差异和重叠冲突，向用户提问；收到明确选择前不合并、不覆盖、不推送。
2. 文件内冲突：先理解双方意图，保留有效改动；不为了“让冲突消失”整段删除对方内容。
3. 语义冲突（两边都改了同一业务规则）：即使文本能自动合并，也要列出行为差异并询问。
4. 合并后重新读取关键文件、运行相关测试，再提交；提交说明记录取舍。

## 交接与报告

1. 每完成一个部分，在唯一当日报告中记录：改动文件、验证结果、未完成项、阻塞点、提交哈希。
2. 交接时写清：哪些文件已改、哪些还没改、下一步谁接手、有哪些临时状态（后台任务、录制、下载）。
3. 不把“另一个 Agent 正在改”当成不测试/不报告的理由；只读部分也要说明边界。
4. 任务被中断时，先保存可恢复状态（WIP 提交、临时文件说明），不要留下半覆盖的数据。

## 检查清单

- [ ] 已看 `git status` / `git log` / 远端状态（或记录网络不可达且用户确认本地最新），知道是否有并行改动？
- [ ] 已声明本次文件/模块范围，避开他人正在编辑的文件？
- [ ] 共享文件是否串行修改，修改前重新读取？
- [ ] 是否按路径备份 WIP、按主题提交，未混入他人改动/临时产物/真实数据？
- [ ] 是否避免了 reset/clean/checkout/stash pop/force push 等破坏性操作？
- [ ] 分叉/冲突是否先列差异并询问，未自动覆盖？
- [ ] 当日报告是否记录提交哈希、未完成项和交接信息？

## 与其他 skill 的关系

- `convention-skill`：单人 Git/备份/报告/发布的通用流程；本 skill 补并行协调。
- `testing-quality-gates`：并行改动后各自跑相关测试，合并后按受影响范围运行必要验证；纯文档检查不启动应用。
- [数据生命周期](../../layering-contract/references/data-lifecycle.md)：共享数据库/迁移时先约定 writer，避免两个 Agent 同时迁移。
- `security-baseline`：WIP 提交前检查 Secret/真实数据，不把凭据带进 Git。
