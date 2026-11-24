---
name: layering-contract
description: "分层约束（新增/重构/跨层调用前必读）。定义入口/任务编排、UI 表现、应用服务/用例、平台适配/领域引擎、基础设施/数据五层及横切层的名称、职责、允许依赖与禁止模式；禁止 UI 直接承载业务、引擎反向依赖 UI、数据层双写与第二套凭据/定时入口。"
---

# 分层约束（Layering Contract）

先阅读 [convention-skill](../convention-skill/SKILL.md)（拉取、备份、提交、报告及约定 7/9/10）。涉及 Cookie/登录、录播八模块、腾讯文档写入或 UI 视觉时，再分别阅读 [cookie-data-protection](../cookie-data-protection/SKILL.md)、[cookie-manager](../cookie-manager/SKILL.md)、[recording-toolbox-core](../recording-toolbox-core/SKILL.md)、[tcdoc-sync-scope](../tcdoc-sync-scope/SKILL.md)、[ui-visual-spec](../ui-visual-spec/SKILL.md)。

本 skill 只约束分层与依赖方向；与其他 skill 冲突时，真实 Cookie 保护、录播核心保护与腾讯文档写入范围优先。

## 定位与适用

**适用场景**：新增、移动或重构模块；新增页面、服务、平台适配器或数据存储；跨层调用；修复“页面太重 / 引擎依赖 UI / 数据双写”等问题。

**不适用场景**：纯文案、纯视觉调整（按 `ui-visual-spec`）、纯业务参数修正。但不得借本 skill 绕过核心八模块保护、Cookie 禁令或腾讯文档白名单。

本 skill 是纯 Markdown 约束，不安装运行时拦截器；由 Agent 阅读后逐项人工核对。目录映射只是当前代码的快照，判断归属以职责为准，不以文件夹名为准。

## 五层 + 横切层

| 层 | 名称 | 职责 | 典型位置 |
| --- | --- | --- | --- |
| **L1** | 入口 / 任务编排层 | 启动与关闭、命令行参数分发、无窗口任务入口、配置/日志/进程生命周期初始化、把任务交给 L2 或 L3 | `main.py`；`toolbox/tcdoc/job.py` 的无窗口入口、`toolbox/account_tracker_job.py`、`toolbox/quark/job.py`、`toolbox/webwiki/analytics_job.py` |
| **L2** | UI 表现层 | 主窗口、导航、页面、对话框、浮层、主题应用、输入校验、结果渲染、调用 L3；只持有界面状态 | `src/gui_qt/main_window.py`、`src/gui_qt/pages/`、`src/gui_qt/theme.py`、`src/gui_qt/navigation.py`、`src/gui_qt/toast.py` |
| **L3** | 应用服务 / 用例层 | 采集、同步、备份、追踪、导出、资产、统计、报告等用例编排；事务边界、去重、重试、限流、进度事件、账号解析、失败回退策略 | `toolbox/tcdoc/job.py`、`toolbox/quark/backup.py`、`toolbox/account_tracker_job.py`、`tracker/accounts.py` 的服务部分、`music/downloader.py`、`exporters/`、`toolbox/livestream_summary/pipeline.py` |
| **L4** | 平台适配 / 领域引擎层 | 平台协议、签名、解析、格式转换、媒体/ASR/ffmpeg 引擎、网盘 API、平台采集器；返回领域对象或 DTO | `platforms/`、`bili/`、`netease/`、`music/scrapers/`、`tracker/collectors/`、`toolbox/*_engine.py`、`toolbox/tcdoc/http_api.py`、`toolbox/quark/api.py`、`baidu/`、`yun139/` |
| **L5** | 基础设施 / 数据层 | 配置、日志、SQLite 存储与索引、文件 IO、浏览器、进程树、Windows 计划任务适配、加密原语、通用工具 | `toolbox/config_manager.py`、`app_logger.py`、`toolbox/durable_io.py`、`toolbox/increment_db.py`、`toolbox/asset_index.py`、`toolbox/browser.py`、`toolbox/*_task.py`、`toolbox/process_tree.py` |
| **X** | 横切层 | 主题、账户/Cookie 管理、定时任务中心、线程桥、日志、toast 等跨层能力 | `src/gui_qt/theme.py`、`src/auth.py`、`toolbox/task_center.py`、`src/gui_qt/common.py` 的 `run_bg/post_to_main`、`src/gui_qt/toast.py` |

## 每层必须与禁止

### L1 入口 / 任务编排层

- 必须：解析参数、初始化配置与日志、选择 GUI 或无窗口路径、处理进程退出兜底。
- 禁止：写采集/解析/同步规则；直接访问平台接口或业务数据库；把业务分支塞进入口函数。

### L2 UI 表现层

- 必须：构建控件、绑定交互、校验用户输入、调用 L3、渲染 DTO/事件、维护界面状态。
- 禁止：直接写 SQL；直接调用平台 HTTP；直接读写凭据文件；直接安装计划任务；在页面内实现线程池、限流、去重、重试、格式匹配等业务编排；直接批量改文件。
- 图片预览等展示型网络 I/O 也应由 L3/L5 的统一下载/缓存接口提供，不在页面重复造请求。

### L3 应用服务 / 用例层

- 必须：以可脱离 Qt 的函数/类提供用例；接收 DTO/参数，返回结果、进度事件或错误；拥有事务、去重、重试、限流、并发上限与失败回退策略。
- 禁止：`import PySide6`、`import gui_qt`；依赖控件、页面、主题或 `QTimer`；承担渲染与用户提示。

### L4 平台适配 / 领域引擎层

- 必须：封装协议、签名、解析、分页、格式转换、媒体处理；错误以明确异常/结果返回。
- 禁止：依赖 UI、页面状态或 L3 用例；直接写页面数据、凭据或界面配置；把“该不该重试/该用哪个账号”等业务决策藏在适配器里。

### L5 基础设施 / 数据层

- 必须：提供通用、可复用的能力；每个存储声明唯一写入方；路径、连接、锁与事务实现集中。
- 禁止：业务决策、页面专属文案/路径、跨层反向依赖；同一数据仓库出现多个 writer。

### X 横切层

- `theme`：只负责视觉；页面不得自带第二套色板（见 `ui-visual-spec`）。
- `auth` / Cookie 管理：凭据只走统一管理层；页面只调用管理接口，不读取凭据文件，不显示凭据字样（见约定 5/7/9 与 `cookie-manager`）。
- `task_center`：Windows 计划任务的唯一注册与分发中心；功能页不得内嵌定时设置（见约定 10）。
- `run_bg/post_to_main`：只做线程桥；不得承载业务状态或数据持久化。
- `toast` / 日志：只做反馈与记录；不得作为业务状态存储。

## 依赖方向铁律

1. 只允许向下调用：**L1 → L2/L3，L2 → L3，L3 → L4/L5，L4 → L5**。
2. L2 不得为了业务直接 import L4/L5；需要网络、数据库或文件处理时，先在 L3 增加用例接口。
3. L3、L4、L5 不得 import `PySide6`、`gui_qt`。现有反向依赖必须登记为已知偏差，改动相关链路时消除，不得作为新代码范例。
4. L4 不得 import L3；L5 不得 import L2/L3/L4；禁止任何循环依赖。
5. 横切层可被各层使用，但不得反向依赖调用方，也不得变成业务状态仓库。
6. 跨层数据只能通过明确的 DTO、事件或返回值传递，不得共享可变页面对象。

```
L1 入口/任务编排
 ├─ L2 UI 表现 ──→ L3 应用服务 ──→ L4 平台适配 ──→ L5 基础设施/数据
 └─ L1 无窗口任务 ─→ L3 应用服务 ──→ L4 平台适配 ──→ L5 基础设施/数据
                                    X 横切层：theme / auth / task_center / run_bg / toast
```

## 数据与状态规则

1. **一个存储一个规范写入方**：在模块 docstring 声明 owner、schema 版本和派生关系；禁止两个模块同时写同一张表/同一个文件。
2. **去重规则集中**：存储去重与展示去重必须分开命名（如 `storage_dedup` / `display_dedup`），写清作用域；禁止在多处复制去重键逻辑。
3. **禁止双写**：历史遗留的 `collected.db` / `increment.db` 等双轨存储，先声明 primary 与 derived，再逐步收敛；新功能不得再新增双写。
4. **账号模型统一**：新增账号字段先改规范模型与适配器，不在页面里做双向同步、合并或补字段。
5. **常量集中**：平台名称、日期文件夹正则、导出子目录等放到 L4/L5 的单一来源；页面不得复制一份。
6. **凭据零扩散**：真实 Cookie/Token 不进入源码、日志、报告、导出、测试或 Git；页面只使用管理层接口。
7. **计划任务单一入口**：任务注册、计划读写、安装/卸载只走 `task_center` 与「任务」页；功能页只保留说明文案。
8. **中心页只做聚合**：跨页「中心」（如「账号名单」）属于 L2 入口，只调用既有 L3/L5 服务与数据；不得在中心页另写备份、迁移、去重或定时逻辑，也不得持有第二份数据。

## 新增代码放置规则

- 新增页面/对话框 → L2；新增用例/流程 → L3；新增平台协议/格式处理 → L4；新增通用能力/存储 → L5；新增启动方式/无窗口入口 → L1。
- 新增 SQLite 表或写入 → L5 存储 + L3 用例；禁止在 L2 写 SQL。
- 新增网络请求 → L4 适配器 + L3 编排；禁止在 L2 直接 `requests/httpx` 做业务。
- 新增导出/资产/报告 → L3 服务；禁止在 L2 实现列映射、分组、去重、文件命名。
- 新增定时任务 → L1 无窗口入口 + L3 `task_center` 注册 + L2「任务」页；禁止功能页加定时控件或弹窗（约定 10）。
- 新增登录/凭据 → 统一认证管理层与平台适配；禁止页面自建登录窗口、凭据输入或第二套存储（约定 9）。
- 新增跨页中心/管理器 → 先按 `convention-skill` 约定 12 判定层级并登记；L2 只聚合，不承载第二套数据或业务逻辑。
- 触及录播八模块 → 先读 `recording-toolbox-core`；本 skill 不构成授权。
- 触及腾讯文档写入 → 先读 `tcdoc-sync-scope` 并核对白名单。

## 评审检查清单

- [ ] 新增/修改的 import 方向符合 L1→L2→L3→L4→L5；没有反向依赖或循环。
- [ ] `rg "PySide6|gui_qt" src/toolbox src/platforms src/tracker src/music src/netease src/bili src/exporters src/auth.py` 只出现已登记的偏差。
- [ ] `rg "requests|httpx|sqlite3|subprocess" src/gui_qt/pages` 没有业务用途；图片预览等展示 I/O 已走统一接口。
- [ ] 页面没有新增数据库写入、平台请求、凭据读写、计划任务安装或线程池编排。
- [ ] 每个存储只有一个写入方；去重键/平台常量/日期规则没有新增副本。
- [ ] 新增凭据只走统一管理层；页面文案不含凭据字样（约定 9）。
- [ ] 新增计划任务只走 `task_center` 与「任务」页（约定 10）。
- [ ] 录播八模块、Cookie、腾讯文档白名单的授权与边界已核对。
- [ ] L3 纯逻辑有测试；L2 有 UI 冒烟；未使用真实 Cookie 做验证。
- [ ] 已更新唯一当日报告并本地提交。
- [ ] skill 保持 Markdown-only，未附带脚本或机器可读配置。

## 已知偏差与迁移

以下是审计中反复出现的模式，不代表已经自动修复；涉及对应模块时逐项确认现状，不要批量返工：

- **L2 承载 L3（剩余）**：`asset` 的目录扫描/平台下载/记录导出/资产索引；采集任务采集、首页统计、回放包、账号 diff、标签匹配已完成服务下沉。
- **已修复（2026-11-17 P4）**：`exporters/wechat_writer.py` 的 PDF 已上移 GUI；`toolbox/live_video.py` 改注入调度；`toolbox/webwiki/server.py` 改用 `toolbox.process_tree`。
- **L5 数据多轨**：`collected.db` 与 `increment.db`、`asset_index.db` 与 `_assets_index.json`、网媒/音乐/信息获取三套账号存储。
- 这些偏差不因本 skill 新增而自动回滚；只在新代码或触及该链路时按授权范围收敛，保留既有行为并补测试。

**建议迁移顺序**：先抽 L3（采集 → 统计 → 资产 → 导出 → 回放包 → 追踪对比 → 标签匹配），再统一 L5 存储与去重，再修反向依赖，最后按页面处理凭据/定时/主题/命名。禁止一次性大重构。

## WebUI 中间层隔离（强制）

WebUI 以独立进程运行，其对主程序而言只是 L2 显示层；所有 Web 业务能力必须经**独立中间层（L3 Web 用例）**提供，且与桌面端严格隔离：

1. **WebUI 中间层独立成模块**：Web 专用用例放 `toolbox/webui_tools.py`、`asset_query.py`、`live_record_status.py`、`webui_modules.py` 等 L3 文件，与桌面页面用例分文件维护，不共用页面级状态。
2. **只依赖引擎，不碰桌面**：中间层只 import L4 引擎与 L5 设施（如 `audio_engine`、`subtitle_engine`、`date_folders`、`collected_store`），禁止 `import PySide6`、`import gui_qt`，禁止依赖桌面窗口、控件、`QTimer` 或桌面页面单例。
3. **不反向入侵桌面**：为 WebUI 新增能力时，默认不改桌面页面与引擎的行为；只有桌面页面内嵌了必须复用的业务逻辑时才把它**搬**到 L3（行为不变），桌面页后续再收敛调用。
4. **故障隔离**：WebUI 子进程崩溃、被停止或令牌失效，不得影响桌面实例与正在进行的桌面任务；WebUI 启动的子进程（隧道、扫描线程等）由 WebUI 侧自行回收。
5. **数据边界**：Web 中间层只做只读或用户明确授权的写入；返回 DTO 不含绝对路径；与桌面共享的存储仍保持唯一写入方。
6. **验证隔离**：WebUI 的测试与验证默认使用隔离副本（独立 config/、download/、logs/、独立端口），不得接入运行中的桌面实例。

任何 WebUI 相关的新文档、skill、页面或用例，都必须写明并遵守本节；与其他 skill 冲突时，真实 Cookie 保护与录播核心保护仍优先。

## 与其他 skill 的关系

- `convention-skill`：负责拉取、备份、提交、报告与总约定；本 skill 只补充分层。
- `cookie-data-protection` / `cookie-manager`：负责真实凭据保护与统一登录；本 skill 不授权任何凭据写入。
- `recording-toolbox-core`：负责八个核心模块保护；本 skill 不授权核心重构。
- `tcdoc-sync-scope`：负责腾讯文档写入白名单；本 skill 不放宽写入范围。
- `ui-visual-spec`：负责视觉与主题；本 skill 只管层间职责。
- 约定 10（任务页）：负责定时任务入口；本 skill 要求定时设置只出现在 L2「任务」页与 L3 `task_center`。
