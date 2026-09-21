# skill 固化 Progress 进度记录约定（2026-09-21）

## 需求
用户在此前会话中确立新约定：每次修复都要在项目根目录 `Progress/` 写进度记录。将该约定固化进 `src/skills/summarize-update/SKILL.md` 的开发阶段约定，保证后续每次任务自动执行。

## 改动
`src/skills/summarize-update/SKILL.md`「开发阶段约定：每部分编辑质量闭环」新增第 2 步「写进度记录」：
- 路径：项目根目录 `Progress/`，命名 `YYYY-MM-DD_主题.md`
- 内容：问题/需求 → 根因/方案 → 改动文件清单 → 验证结果 → Git 提交信息
- 同一任务多部分修改追加到同一文档；文档与代码一起提交进库
- 原 2/3 步顺延为 3/4 步；frontmatter description 同步更新

## Git
- 提交：`docs: skill 固化 Progress 进度记录约定（每部分改动写 Progress/ 文档）`
