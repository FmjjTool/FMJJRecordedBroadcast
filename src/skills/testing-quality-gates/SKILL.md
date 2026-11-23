---
name: testing-quality-gates
description: "测试与质量门禁（任何代码修改、重构、Bug 修复、数据迁移或 UI 改动时必读）。规定 Domain/L3 单测、Adapter 契约测试、L5 集成测试、L2 offscreen 冒烟与手工验证的层级；测试数据必须隔离，不碰真实 Cookie、真实网络或生产数据库；Bug 修复必须补回归测试。"
---

# 测试与质量门禁（Testing & Quality Gates）

先阅读 [convention-skill](../convention-skill/SKILL.md)、[layering-contract](../layering-contract/SKILL.md) 与 [security-baseline](../security-baseline/SKILL.md)。涉及发布时再读 [summarize-update](../summarize-update/SKILL.md)；涉及 Cookie/登录时按 [cookie-data-protection](../cookie-data-protection/SKILL.md) 与 [cookie-manager](../cookie-manager/SKILL.md) 执行。

本 skill 规定“什么该测、在哪一层测、用什么数据测、什么时候算通过”。它不替代 `summarize-update` 的开发阶段冒烟/报告/提交闭环，也不授权任何真实账户或真实凭据验证。

## 基本原则

1. **测行为，不测实现**：断言用户/调用方能看到的结果，不断言内部私有调用顺序。
2. **先证明失败，再修复**：Bug 修复先补一个能复现的失败测试，再改代码。
3. **测试数据必须隔离**：临时目录、虚构凭据、fake 适配器；不复制真实 `config/`、`download/`、数据库或 Cookie。
4. **自动化测试不碰真实网络/账户**：网络在 Adapter 边界 mock；真实账号实测只在用户明确要求时手工执行。
5. **测试失败不得靠放宽断言、删测试或跳过测试来“通过”**；先定位根因。
6. **没跑的测试如实说明**：只跑 offscreen 不等于真实 UI 通过；只跑单测不等于端到端通过。

## 测试层级

| 层级 | 测什么 | 放在哪 | 是否碰真实环境 |
| --- | --- | --- | --- |
| **Domain / L3 单测** | 去重键、日期过滤、匹配/分组、重试/回退决策、格式化、状态机、任务编排 | `tests/`，纯函数/服务，fake 适配器 | 否 |
| **Adapter 契约测试** | 平台响应解析、字段映射、分页、错误分类、回退顺序 | `tests/`，固定 fixture / 录制的最小响应 | 否 |
| **L5 集成测试** | SQLite 迁移、事务、WAL、并发写、索引、备份/恢复 | 临时目录中的真实 SQLite | 否（临时库） |
| **L2 offscreen 冒烟** | 页面可构造、关键按钮/状态、无异常、无网络/凭据副作用 | `QT_QPA_PLATFORM=offscreen`，fake app | 否 |
| **手工 UI/真实账号验证** | 布局、交互、真实平台行为、长任务、录制/上传 | 用户明确要求时 | 是，按用户授权 |

## 必须补测试的改动

- 新增或修改**去重规则、日期范围、匹配/分组、排序、分页**：必须有边界用例。
- 新增或修改**重试、超时、限流、回退顺序、取消**：必须有成功/失败/取消用例。
- 新增或修改**数据库表结构、迁移、写入方**：必须有迁移测试 + 旧数据兼容测试 + 失败回滚测试。
- 新增或修改**平台适配器**：必须有解析 fixture、错误响应、分页/空结果、回退路径测试。
- 修复 Bug：必须有回归测试，且在修复前能失败。
- 修改 UI：必须有 offscreen 冒烟；涉及视觉的再按 `ui-visual-spec` 做浅/深主题手工核对。
- 修改录播八模块：先按 `recording-toolbox-core` 取得授权，再补对应回归测试，不能只跑无关页面。
- 修改 Cookie/登录：只用虚构凭据和临时目录；真实凭据不进入测试。

## 测试数据与隔离

1. 所有文件写入使用 `tempfile.TemporaryDirectory()` 或项目约定的临时根；测试结束清理。
2. 数据库测试使用临时 SQLite 文件，不连接 `config/` 下的真实库；需要旧数据时手工构造最小 fixture。
3. 凭据使用虚构值（如 `test-token`）并注入 fake 管理层；不读取 `cookies.json`、浏览器 profile 或 storage_state。
4. 网络测试在 Adapter 边界替换为 fake/httpx MockTransport；不在测试中访问真实平台。
5. 时间、随机数、机器 GUID 等不稳定输入要注入或固定；避免依赖当前日期。
6. 不共享可变全局状态；测试可单独运行、可重复运行、顺序无关。
7. 不使用 `sleep` 等待异步结果；用事件、回调、超时断言或可控时钟。

## 本地命令与门禁

**基础检查**

```bash
python -m compileall -q src
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
```

**分部分编辑时**

- 先跑与改动最相关的测试文件；修复失败后再跑全量。
- 每个编辑部分完成后按 `summarize-update` 做导入/实例化冒烟、更新当日报告、本地提交。
- 所有部分完成后跑一次完整离屏测试；通过后再收尾提交。

**发布前**

- 全量测试通过；隐私扫描通过；工作区干净。
- 未运行的测试、未验证的真实平台行为在报告中明确标注。

## 测试质量

1. 一个测试只验证一个行为；名称写清“场景 + 预期”。
2. 使用 Arrange–Act–Assert；失败信息包含输入与期望值。
3. 避免大面积快照；只对稳定、可读的输出做 golden/snapshot（如图片尺寸/布局、导出字段）。
4. 不 mock 被测对象自身；只 mock 边界（网络、时间、文件系统、凭据、外部进程）。
5. 对异常路径也要断言：错误类型、错误码、用户可见提示、是否重试。
6. 覆盖率只作参考；优先覆盖关键路径：去重、存储写入、同步匹配、任务调度、认证闸门、导出格式。

## 烟测文件目录（固定）

- 手动冒烟 / 联调脚本、harness、临时数据、日志与过程截图统一放在项目根目录 `_smoking/`；不得散落到根目录、`tests/`、`src/` 或 `Progress/`。
- `_smoking/` 已由 `.gitignore` 忽略：不 `git add -f`、不提交、不推送；提交前确认 `git ls-files _smoking` 为空。
- 只有需要长期作为报告证据的最终截图才另存到 `Progress/ui/<日期>/`（PNG 已被忽略）；过程截图与 harness 留在 `_smoking/`。
- `_smoking/` 是可复用的本地临时工作区，清理按 [test-cleanup](../test-cleanup/SKILL.md)：保留可复用脚本，只清理可再生的临时输出。

## 报告与收尾

- 报告写明：跑了哪些测试、结果、未跑哪些、为什么；失败是否已修复。
- 不用“应该没问题”“理论上通过”代替实际结果。
- 测试产生的临时文件、截图、日志不得提交；烟测脚本与过程截图放 `_smoking/`，仅最终证据截图放 `Progress/ui/<日期>/`。
- `tests/` 已被 `.gitignore` 忽略，测试文件不入库；本次验证完成后按 [test-cleanup](../test-cleanup/SKILL.md) 删除测试文件与测试缓存。测试夹具不含真实个人数据/凭据。

## 检查清单

- [ ] 本次改动属于哪一层？对应层级的测试是什么？
- [ ] Bug 修复是否有先失败、后通过的回归测试？
- [ ] 去重/日期/分页/重试/取消/迁移是否有边界用例？
- [ ] Adapter 是否有 fixture、错误响应、空结果、回退测试？
- [ ] 测试是否只用临时目录/虚构凭据/fake 网络，未碰真实环境？
- [ ] UI 改动是否做了 offscreen 冒烟？视觉改动是否做了浅/深主题手工核对？
- [ ] 全量测试是否通过？未跑的测试是否如实报告？
- [ ] 烟测脚本、harness、临时数据与过程截图是否都在 `_smoking/`，且未被提交/推送？
- [ ] 验证完成后是否已按 `test-cleanup` 删除 `tests/` 与测试缓存？
- [ ] 录播八模块/Cookie/腾讯文档写入是否先核对了授权边界？
- [ ] 是否更新当日报告并本地提交？

## 与其他 skill 的关系

- `summarize-update`：开发阶段冒烟、报告、提交与发布流程；本 skill 补测试分层与门禁。
- `test-cleanup`：验证完成后的测试文件与缓存清理；先测后删，`tests/` 不入库。
- `layering-contract`：测试按层放置；L3 纯逻辑必须可脱离 Qt 测试。
- `data-lifecycle`：迁移/备份/恢复测试的具体要求。
- `security-baseline`：安全输入、路径、ZIP、subprocess、日志的测试用例。
- `platform-adapter-policy`：Adapter 契约测试与 fixture 规范。
