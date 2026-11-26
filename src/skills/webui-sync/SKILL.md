---
name: webui-sync
description: "桌面端 → WebUI 同步（桌面端新增或改动功能后强制核对）。定义必须同批接入 WebUI 的能力、四层接入顺序（toolbox/webui_*.py → webui/services/*.py → webui/app.py 路由 → static/app.js 页面与 index.html 导航）、表单参数的声明式字段表（一份字段表三处消费，避免逐层手抄与空壳占位）、死控件识别（控件在但没绑事件、主功能藏在折叠块）、桌面专属白名单、同批提交与三层验收口径。触发词：同步到 WebUI、WebUI 没有这个功能、Web 端少了按钮、页面接入、四层同步、桌面端更新、按钮点了没反应、参数不生效。"
---

# 桌面端 → WebUI 同步

先阅读 [convention-skill](../convention-skill/SKILL.md)（约定 2/3/4/9/12/16）与 [layering-contract](../layering-contract/SKILL.md)（五层依赖 + 「WebUI 中间层隔离」）。涉及界面时再读 [ui-visual-spec](../ui-visual-spec/SKILL.md)、[ui-interaction-spec](../ui-interaction-spec/SKILL.md)。

## 定位与铁律

桌面端是功能源头，WebUI 是同一能力的第二个入口。桌面端的新增或改动完成后，**必须同一批把它接到 WebUI**，不允许「先做桌面，Web 以后再说」。

1. **同一份实现**：业务规则、常量、命名、去重、回写逻辑复用 L3/L4 共享模块，不在 Web 侧另写一套。共享纯函数放 `src/toolbox/`（如 `rename_rules.py`、`bili_assets.py`），桌面页与 Web 中间层都调用它。
2. **同一份数据**：`config/`、`download/`、SQLite、账号文件与桌面共用，保持唯一写入方，不建 Web 专用副本。
3. **同一批提交**：四层改动进同一个 commit，不拆成「后端先上、前端下次补」。
4. **不降级为只读**：只读页与置灰按钮只是过渡态，验收标准是功能真能执行。

## 触发条件

命中任一即按本 skill 执行：

- 桌面端新增页面、页签、按钮、菜单项、表格操作或导出入口。
- 新增或修改 L3 用例的签名、参数、返回结构、错误文案、默认值。
- 新增配置键、落盘文件、数据库表/字段（Web 读写同源）。
- 新增写操作：移动/改名/删除/回写/上传/批处理。
- 修复改变行为的 bug，或调整校验、排序、分页口径。
- 新增依赖、登录或凭据要求（Web 端给等价提示，文案遵守约定 9，不出现凭据字样）。
- 用户反馈「WebUI 没有这个功能 / 按钮少了 / 逻辑和桌面差太多」。

不适用：纯桌面进程内能力（见白名单）；只改桌面外观且无功能语义的视觉调整。

## 桌面专属白名单

只有同时满足「依赖桌面进程内状态 / 自绘画布 / 本机原生窗口或进程启停 / 需要系统级权限」之一，才允许不同步。当前涉及图片裁切、封面制作的自绘排版、证书制作、网络代理。封面制作的目录扫描、标题日期识别与模板批量补全已拆为 Qt-free 用例并同步 Web，不能把整页都降为说明页。

1. 新增专属页必须在 `software-feature-guide` 与当日报告写明判定依据（为何不能 Web 化）。
2. `app.js` 的 `routes` 中保留 `renderDesktopOnly` 说明页，文案与桌面能力一致；不放半残控件、不放点了没反应的按钮。
3. 专属能力若可拆分（如「取数 / 导出」可 Web 化，「编辑」留桌面），先拆出可执行部分接入 Web。
4. 隧道、录制弹窗、弹幕悬浮窗等「本机进程内」能力，Web 端只读展示状态或说明，不伪造「运行中」。

## 四层接入顺序（缺一层视为未完成）

### 1. L3 Web 用例 `src/toolbox/webui_<域>.py`

- 必须 Qt-free：只 import L4/L5，禁止 `PySide6`、`gui_qt`、`QTimer`。
- 先查共享模块：命名、B站分区、片段 CSV/EDL、改名规则、封面模板等已有纯函数就 import；桌面页里内嵌的同类逻辑搬进共享模块（行为不变），桌面页改为调用，禁止两套。
- 路径参数做穿越校验，只允许受控根目录内；返回 DTO 不含绝对路径。
- 长任务返回可提交的 job 工厂，不在用例里阻塞或 sleep；失败返回明确异常/结果，不吞错。

### 2. L2 服务 `src/toolbox/webui/services/<域>.py`

- 收 `JobManager`、进度、取消、错误分类，组装 DTO 给前端。
- 不写 SQL、不直接拼页面文案、不持有桌面页面对象或单例。

### 3. 路由 `src/toolbox/webui/app.py`

- `server.get("/api/<域>/<动作>", handler)` / `server.post(...)`；参数用 `req.q`、`req.q_int`、`req.json` 读取并校验范围。
- 长任务 `jobs.submit("<kind>", fn)` 返回 `{"job": id}`，前端用 `watchJob` 跟进度。
- 文件下载用 `BytesResponse` / `AttachmentResponse` / `FileResponse` / `RangeResponse`，不手写响应头。
- 鉴权与错误信封由 `webui/server.py` 统一处理，不在 handler 里自建 token 校验。

### 4. 前端 `src/toolbox/webui/static/app.js` + `index.html`

- `routes` 表新增一项：`routeKey: { title: "<PAGE_INDEX 名>", load: renderXxx }`，未知路由落「工作台」。
- `index.html` 侧栏加 `<button class="nav-item" data-route="routeKey">名称</button>`，分组、命名、顺序照桌面 `src/gui_qt/page_index.py` 的 `PAGE_INDEX` / `NAV_LAYOUT`；Web 端不新增桌面没有的入口。
- 每个页面脚本是独立作用域：**不要调用别的页面的 helper**（会抛 ReferenceError 导致整页空白），需要的函数就地构造；`navigate()` 已包 try/catch，但不要依赖它兜底。
- `?token=` 深链逻辑不要重排：先取 hash 再 `replaceState`，否则会落默认页。
- 写操作三件套：禁用态防连点、不可逆/批量操作二次确认、长任务可见进度条（不能只有百分比文字）。
- 表格排序/分页按接口下推（`ORDER BY` 白名单 + 重查第 1 页），不只在当前页排。
- 视觉只用 `static/style.css` 的 CSS 变量，**不改 QSS、不改 `theme.py`、不改全局配色**。

## 表单类接入：一份字段表，三处消费

四层是「位置」，不是「写法」。一个参数在四层里被手抄三次，是这类功能反复返工的主因：任何一处漏写都不报错，只是「点了没反应」。

**做法**：每类参数只在 L3 维护一张声明式字段表（`toolbox/webui_<域>.py` 里，纯数据）：

```python
FIELDS = {
    "del_types": {"type": "strlist", "default": [], "allow": {...}},
    "del_pos":   {"type": "int", "default": 0, "min": 0},
    "del_kw":    {"type": "str", "default": ""},
}
```

- **L3**：由表生成 config，不再手写 `"del": {"types": []}` 这类占位。
- **L2 服务**：按表 `payload.get(name, default)` 透传，不逐个点名。
- **前端**：按表生成控件（`type` 决定 checkbox / number / text / select），控件 `name` 即字段名。

新增一个开关 = 表里加一行；漏写不可能，因为三处都从同一张表读。

**三个反模式（都真实踩过）**：

| 反模式 | 症状 | 识别方法 |
| --- | --- | --- |
| 占位字典 | `"del": {"types": []}` 恒为空，前端传什么都丢 | 搜 `{"types": []}`、`{"": ""}` 这类空壳，或 `TODO` / 占位注释 |
| 控件在、线未接 | 勾选后预览不变，要点「扫描」才动 | 检查每个输入是否在 `addEventListener` 列表里 |
| 主功能藏在 `<details>` | 用户以为「这功能没做」 | 搜 `details` / `summary` 里是否含业务控件 |

**接入前先验货三问**，任一项不成立就不是「已接入」：

1. 引擎真消费这个字段吗？（`rename_rules.apply_rules` 里有没有对应分支）
2. 中间层真透传吗？（不是空壳默认值）
3. 控件绑了事件、默认可见吗？

**验收用「非默认值」**：传一个非默认值看输出是否随之改变，而不是看控件是否存在。控件存在只能证明第 4 层写了，证明不了整条链路通。

## 导航与命名对齐

- 页面名用 `PAGE_INDEX` 的名称；默认首页是「工作台」。
- 侧栏分组顺序照桌面；桌面改名或移动分组时，同批改 `index.html` 与 `routes` 的 `title`。
- 页面标题由顶栏提供，不在内容区重复大标题（与桌面一致）。

## 同批提交

1. 改前 `git status`，把本次四层文件清单写进当日报告。
2. 一个功能一个 commit，包含全部层；曾出现漏提交 `webui_*.py` 导致远端接口 500 的情况，提交后必须 `git show --stat` 核对文件数与预期一致，缺层立即补提交。
3. 只本地 commit；推送需用户明确指示（约定 2）。

## 验收：三层口径分开报

不要用一句「已接入」概括，按三层分别给结论：

| 层 | 判定 |
| --- | --- |
| 页面接入 | 路由存在、侧栏可见、深链 `#/<route>?token=...` 能直接打开 |
| 功能可执行 | 真实调用接口完成一次「读 → 写 → 回读」，不是只有控件 |
| 布局对齐 | 与桌面同页对照分组、字段、操作顺序、状态文案 |

验证方式（按 [isolated-testing](../isolated-testing/SKILL.md)）：

- 隔离副本 + 独立端口（如 18791/18792）+ 临时 app 根（patch `config_manager.get_app_root`）+ 引擎打桩。
- 宿主机 Edge 无头 Playwright 走一遍页面，截图存 `Progress/ui/<日期>/`。
- 真实 `config/` 与 `download/` 不动，跑完核对 `config/config.json` 前后 SHA-256 一致并在报告写明。
- 临时文件、测试脚本走 `host_safe_delete` 回收站清理，按 [test-cleanup](../test-cleanup/SKILL.md)。

禁止：在真实 `download/` 上做写操作；用真实账号或凭据做验证；把未验证项写成已通过。

## 检查清单

- [ ] 已判定本次桌面改动「需同步 / 桌面专属」；专属已登记理由。
- [ ] 四层齐全（`webui_*.py`、`services/*.py`、`app.py`、`app.js` + `index.html`）且在同一提交。
- [ ] 业务规则复用共享模块，没有第二套实现、第二份配置或第二份数据。
- [ ] L3 无 Qt 依赖、无绝对路径、路径穿越已校验。
- [ ] 路由参数已校验；错误信封统一；长任务走 `jobs`。
- [ ] `routes` 与侧栏已加，名称与分组照 `PAGE_INDEX`。
- [ ] 页面内没有跨页 helper 调用。
- [ ] 参数未在四层手抄三份：表单类已用同一份字段表生成 config / 透传 / 控件，没有 `{"types": []}` 这类空壳占位。
- [ ] 每个新建控件都绑了 change/input 事件，主功能不在默认折叠的 `<details>` 里。
- [ ] 用「非默认值」验过一遍链路（改参数 → 输出随之改变），没有只看控件是否存在。
- [ ] 写操作有禁用态、二次确认与进度显示。
- [ ] 三层验收分别记录，未验证项如实标注。
- [ ] `software-feature-guide` 已同步（功能说明或入口变化时）。
- [ ] 当日报告已更新并随改动本地提交。

## 与其他 skill 的关系

- [convention-skill](../convention-skill/SKILL.md)：提交、报告、隔离测试与凭据禁令；本 skill 不豁免其中任何一条。
- [layering-contract](../layering-contract/SKILL.md)：五层与「WebUI 中间层隔离」的权威定义；本 skill 只负责同步流程，不改变分层。
- [software-feature-guide](../software-feature-guide/SKILL.md)：功能说明唯一维护处；Web 化或新增专属页时同步更新。
- [ui-visual-spec](../ui-visual-spec/SKILL.md) / [ui-interaction-spec](../ui-interaction-spec/SKILL.md)：Web 端只借语义角色与交互范式，不改 QSS 与主题。
- [test-cleanup](../test-cleanup/SKILL.md)：验证完成后清理测试文件与临时产物。
