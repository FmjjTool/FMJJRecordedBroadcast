---
name: layering-contract
description: "项目架构与工程边界总入口。新增/重构/跨层调用、持久化/迁移/去重、平台接口/回退、桌面能力同步 WebUI 时使用；按任务读取对应参考规则。业务页面经 L3 用例访问引擎与存储，禁止反向依赖、数据双写及第二套凭据或定时入口。"
---

# 分层、数据与平台工程约束

先阅读 [convention-skill](../convention-skill/SKILL.md)。真实凭据、录播核心和腾讯文档写入分别服从 [cookie-data-protection](../cookie-data-protection/SKILL.md)、[recording-toolbox-core](../recording-toolbox-core/SKILL.md)、[tcdoc-sync-scope](../tcdoc-sync-scope/SKILL.md)；本 skill 不提供额外操作授权。

## 按任务读取

只读取本轮命中的参考文档，命中多个任务时组合读取；不因进入本 skill 就通读全部专项规则。参考文档是相应规则的规范正文，旧 skill 名只保留兼容入口。

| 触发 | 必读参考 | 内容 |
| --- | --- | --- |
| 新增、移动、重构模块或跨层调用 | [分层详细规则](references/architecture.md) | 五层职责、横切能力、依赖方向、代码放置、历史偏差与 WebUI 隔离 |
| 新增/修改数据库、存储、去重、迁移、备份或保留清理 | [数据生命周期](references/data-lifecycle.md) | 唯一写入方、schema、primary/derived、事务、迁移、备份、导入导出 |
| 新增/修改平台接口、登录适配、下载或回退策略 | [平台适配](references/platform-adapter-policy.md) | Adapter 契约、能力矩阵、网络/签名、错误分类、契约测试 |
| 桌面端新增/改动用户能力，或排查 WebUI 缺失/不一致 | [桌面到 WebUI 同步](references/webui-sync.md) | 四层同批接入、字段表、桌面专属边界、三层验收 |

纯文案、视觉或业务参数调整不自动触发全部架构参考；涉及界面时另按 [ui-visual-spec](../ui-visual-spec/SKILL.md) 与 [ui-interaction-spec](../ui-interaction-spec/SKILL.md)。

## 共用边界

1. 跨层业务调用只向下：**L1 入口 → L2 表现/L3 用例，L2 → L3，L3 → L4 引擎/L5 设施，L4 → L5**。L3 内可无环复用共享用例；L2 页面和中心页不得直连业务存储或平台协议。
2. L2 可直接使用主题、导航、toast 与线程桥等纯表现横切能力；这不允许直接处理业务网络、SQL、文件批量操作、凭据或计划任务。认证与任务管理的业务能力经 L3 管理接口调用。
3. L3/L4/L5 不依赖 Qt、页面或桌面单例；跨层传 DTO、事件和明确错误，不传可变页面对象。
4. 一个存储只有一个规范写入方；页面经 L3 访问，去重键与常量集中，primary 与 derived 明确，不新增双写或 Web 专属数据副本。
5. 桌面与 Web 复用业务规则和同源数据，保持进程与页面状态隔离；桌面能力变更按参考文档同批接入 WebUI。
6. 验证默认使用隔离副本和虚构数据；未验证项如实报告。报告与提交统一按总约定，本 skill 只使用 Markdown，不安装运行时拦截器。

## 兼容入口

[data-lifecycle](../data-lifecycle/SKILL.md)、[platform-adapter-policy](../platform-adapter-policy/SKILL.md)、[webui-sync](../webui-sync/SKILL.md) 的目录与 `SKILL.md` 保留，供既有引用、快捷指令和下载 ID 使用；其规范正文统一维护在本目录的 `references/`，不另存第二套规则。