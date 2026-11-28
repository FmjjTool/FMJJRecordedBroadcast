# 项目级强制入口（所有 Agent 必读）

操作前完整阅读本文件、[总约定](src/skills/convention-skill/SKILL.md)、[真实 Cookie 绝对保护](src/skills/cookie-data-protection/SKILL.md)；任何更新前再读 [核心八模块保护](src/skills/recording-toolbox-core/SKILL.md)。然后按任务加载下表主入口及其相关 references，已读且未变化的不重复读取。

本文件只维护入口与不可忽略边界；17 条约定的现行正文及唯一细则在 convention-skill，不再复制整套流程。用户明确要求规则修订时可在指定范围合并、更新，不能把技能整理视为应用改动或线上写入授权。

## 主技能入口

| 主入口 | 触发与内容 |
| --- | --- |
| [convention-skill](src/skills/convention-skill/SKILL.md) | 所有操作；Git / 报告 / 入口约定；并行时读 coordination |
| [cookie-data-protection](src/skills/cookie-data-protection/SKILL.md) | 所有操作前必读；真实 Cookie 无例外保护 |
| [cookie-manager](src/skills/cookie-manager/SKILL.md) | 应用账户、认证接口、登录能力与隔离验证 |
| [testing-quality-gates](src/skills/testing-quality-gates/SKILL.md) | 任何更新前核对实例保护；代码改动 / 测试 / 清理按分支阅读 |
| [layering-contract](src/skills/layering-contract/SKILL.md) | 新增 / 重构 / 跨层；数据、平台、桌面→WebUI 同步按分支阅读 |
| [security-baseline](src/skills/security-baseline/SKILL.md) | 非 Cookie 密钥、日志、文件、ZIP、subprocess、网络、依赖、打包 |
| [ui-visual-spec](src/skills/ui-visual-spec/SKILL.md) | UI 视觉、交互 / 文案、程序导出图按分支阅读，使用现有共享组件与令牌 |
| [fengming-livestream-summary](src/skills/fengming-livestream-summary/SKILL.md) | 封茗囧菌转录 / 文件总结；纠错、分类、数据库与纯文本模板 |
| [summarize-update](src/skills/summarize-update/SKILL.md) | 明确推送 / 发布，或构建、内置清单、依赖解析与缺依赖排查；双版本按分支阅读 |
| [software-feature-guide](src/skills/software-feature-guide/SKILL.md) | 页面功能、入口、路径与限制，按页面读 references |
| [tcdoc-sync-scope](src/skills/tcdoc-sync-scope/SKILL.md) | 腾讯文档同步 / 写回 / 表格修改 / 导出覆盖前必读 |
| [recording-toolbox-core](src/skills/recording-toolbox-core/SKILL.md) | 更新前核对八模块直接 / 间接影响与具体授权 |
| [quick-commands](src/skills/quick-commands/SKILL.md) | /短语、要点或技能路由查询，仅读原文生成摘要 |

项目只保留上述 13 个主技能入口。专项细则集中在所属主目录的 references/，快捷指令直接指向现行正文，不保留旧名转交目录。技能发现按实际目录枚举；单目录导出只携带该目录，跨技能依赖仍须按链接同时提供。

## 开工与收尾

仓库更新前完整读 [Git / Markdown / 日报细则](src/skills/convention-skill/references/git-workflow.md)：status、备份本任务稳定 WIP、核对 origin、按直连→代理→其它逐档成功 fetch、祖先关系及左右独有提交比较。完成前置状态、备份与分支映射核对后，优先用根目录 check_git_sync.py 一次获取检查事实；用法与失败回退集中在该细则。远端领先只快进，本地领先保留，分叉展示差异后询问，不强推。非 main / worktree 先确认基线与目标映射。

并行工作读 [协作细则](src/skills/convention-skill/references/coordination.md)：默认直接在当前 main 修改，不因多聊天创建支线；一个文件一个 writer，共享文件串行更新。目标文件与其他改动完全无关时，按路径直接改并提交自己的文件，无需询问，保留他人 WIP；同文件或无法安全保留时才用独立 worktree。完成后先合并北京时间唯一日报 Progress/YYYY-MM-DD_当日总结.md，再按明确路径本地提交并报告哈希；GitHub 多档不可达且用户明确本地最新时，记录远端未验证后继续本地提交，不自动推送。

项目技能只允许 Markdown（SKILL.md + 可选 .md 子文档）；用户对当次脚本 / 结构化配置的明确要求才构成例外，不沉淀为默认。普通文档校验不启动应用，不清理其它代码任务的测试或缓存。

## 必须守住的范围

- **真实 Cookie**：保存值、归属 / 时间、加密表示、真实存储、快照 / 旧副本均禁止 Agent 修改、删除、迁移、刷新或复制到测试环境，无测试 / 修复 / 确认后覆盖例外。整文件仅改总开关也禁止；意外变化停止，不自行回写或回滚。
- **用户运行中实例**：默认不得关闭、结束或重启，不覆盖运行中的 exe / 目录。本次具体授权且已提示录制 / 采集中断风险才可按实例保护执行。Agent 自建且确认完全隔离的进程可正常收尾，不误杀共享进程。
- **测试与真实数据**：默认程序副本 + 独立 config/download/logs/端口 + 虚构数据，本项目无 --work-dir。真实目录默认只读，接入指定非凭据数据或真实实例须本次列范围、快照与 SHA-256、整文件还原方案并获同意；失败或还原不完整停止写入报告。真实 Cookie 永无例外。烟测、harness、临时数据与过程图只放 _smoking/；最终证据才放 Progress/ui/<日期>/。测试后先按质量门禁验证再清理本任务拥有的测试，tests 不入库，不清他人活动文件。
- **核心八模块**：自动整理、视频操作、命名投稿、音频提取、字幕转换、继续操作、日历浏览、文件迁移，含入口 / 路由 / 页面 / 引擎与共享代码的间接影响，须有本次具体模块及范围授权。全局优化、重构、清理、登录统一不构成授权；规则维护只改文档。
- **腾讯文档**：唯一常设写入上限为《封茗囧菌数据汇总库》doc_key DRnd3bkhkQUR1aEJr 的 官方动态 ss_h2uh2s / 直播记录 ss_1xgstm；动作、记录与门禁仍按本次授权核对。完整歌曲、线下活动、歌曲片段、周边、图集数据库及其它表 / 文档均禁写。旧追加授权、正确字段、已有按钮、只新增 / 追加不能扩大范围；越界后停止报告，不自动再次写入或回滚。
- **统一管理入口**：应用账户统一总管理器，业务页无凭据控件 / Cookie 字样 / 登录引导；计划只在任务页经 task_center，依赖安装只在全局设置。配置 / 资产备份恢复只在全局设置，网盘增量上传仍在网络库。宿主开发工具可只读使用既有原生认证，不复制或迁移应用凭据。
- **UI 与跨端**：主题使用 theme.py 唯一色板；视觉 / 交互 / 表格 / 图片各按主入口对应 reference，新增功能说明同步 guide。页面不恢复帮助按钮 / 浮层或顶部标题说明。桌面可用能力变化按 layering 的 Web 同步 reference 判定四层接入或现有例外，三层验收如实报告；技能整并不授权改核心模块。
- **总结与发布**：实际“总结文章”须可读取文件，纠错 / 分类 / 模板完整读取，数据库按索引取用；维护关键词不触发总结。普通总结不 push / 打包 / 改版本 / tag / Release。明确推送仅同步指定代码，明确完整发布才按发布流程，不扩张动作。

## 快捷指令

完整映射只维护在 [quick-commands](src/skills/quick-commands/SKILL.md)。保留 /约定、/网络、/管理入口、/Cookie保护、/登录、/核心模块、/分层、/平台适配、/安全、/运行中、/数据、/出图、/视觉、/交互、/腾讯文档、/总结文章、/直播总结、/更新发布、/双版本、/并行、/测试、/测试清理、/隔离测试、/功能说明、/WebUI同步、/shaman、/快捷指令。摘要不代替实际任务必读规范，也不触发任何外部操作。
