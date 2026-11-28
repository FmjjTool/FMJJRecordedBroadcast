---
name: quick-commands
description: "项目 /短语与 skill 要点查询。只按现行原文生成简短摘要或输出路由表，不执行外部操作；实际总结任务仍按主入口文件与授权要求处理。"
---

# 快捷指令路由

用户发送 /短语、以“快捷指令”开头查询某项，或索要快捷列表时使用。精确匹配下表短语；消息带具体工作任务时仍按主入口的触发规则执行，不能把“查询总结规则”误当成实际文章总结。

## 输出规则

1. 先读目标主入口与本次选中的 reference，直接从当前原文提炼，最多 5 条要点；本文件不保存规则摘要副本或固定条数 / 旧版本状态。
2. 首行标明“/短语 → 实际文档路径”，后附最多五条核心限制；末尾提醒“要点不替代执行任务前的完整阅读”。用户要全文时给实际目标文档全文，不凭旧摘要补写。
3. 只要列表时输出下表；多个短语按出现顺序处理。未登记则说明，并提供最接近的已登记项，不擅自新增同义词。
4. 快捷查询不触发 push、打包、改版本、tag / Release、任务安装、登录、数据写入或修改技能；携带具体行动请求时，其授权范围与保护规则仍逐项判断。
5. 优先级按真实 Cookie 保护 → 总约定 → 任务主规则处理，实际用户授权不能由历史记录或摘要推定；冲突需要报告，不用快捷词绕开。

## 唯一路由表

旧名仍可跳转，维护位置已经合并到目标主目录；实际执行时先读对应 SKILL.md 再读分支 reference。单独导出 / 复制兼容旧目录时须同时携带对应主目录和依赖，不能只带转交页。

| 快捷短语 | 当前维护位置（src/skills/ 下） | 定位 |
| --- | --- | --- |
| `/约定` | [convention-skill/SKILL.md](../convention-skill/SKILL.md) | 现行 17 条约定与任务路由 |
| `/网络` | [convention-skill/references/git-workflow.md](../convention-skill/references/git-workflow.md) | 约定 15；逐档网络与远端保护 |
| `/管理入口` | [convention-skill/references/product-boundaries.md](../convention-skill/references/product-boundaries.md) | 约定 9—12、17；账户 / 任务 / 安装 / 备份入口 |
| `/Cookie保护` | [cookie-data-protection/SKILL.md](../cookie-data-protection/SKILL.md) | 真实 Cookie 绝对保护 |
| `/登录` | [cookie-manager/SKILL.md](../cookie-manager/SKILL.md) | 统一认证接口与已有实现边界 |
| `/核心模块` | [recording-toolbox-core/SKILL.md](../recording-toolbox-core/SKILL.md) | 八模块具体授权与影响范围 |
| `/分层` | [layering-contract/SKILL.md](../layering-contract/SKILL.md) | 五层与横切，按需读架构参考 |
| `/平台适配` | [layering-contract/references/platform-adapter-policy.md](../layering-contract/references/platform-adapter-policy.md) | 平台契约、回退与错误分类 |
| `/安全` | [security-baseline/SKILL.md](../security-baseline/SKILL.md) | 密钥 / 文件 / 网络 / 依赖 / 打包 |
| `/运行中` | [testing-quality-gates/references/running-instance-guard.md](../testing-quality-gates/references/running-instance-guard.md) | 用户实例保护与隔离进程收尾 |
| `/数据` | [layering-contract/references/data-lifecycle.md](../layering-contract/references/data-lifecycle.md) | 数据 owner / primary / derived / 迁移 |
| `/出图` | [ui-visual-spec/references/generated-images.md](../ui-visual-spec/references/generated-images.md) | 程序输出图，非 AI 图像生成 |
| `/视觉` | [ui-visual-spec/references/screen-visual.md](../ui-visual-spec/references/screen-visual.md) | 主题 / 排版 / 布局 / 组件 |
| `/交互` | [ui-visual-spec/references/interaction.md](../ui-visual-spec/references/interaction.md) | 弹窗 / 表格 / 四态 / 表单 / 文案 |
| `/腾讯文档` | [tcdoc-sync-scope/SKILL.md](../tcdoc-sync-scope/SKILL.md) | 现行两表白名单与写入门禁 |
| `/总结文章` | [fengming-livestream-summary/SKILL.md](../fengming-livestream-summary/SKILL.md) | 文件门槛、全文纠错与固定模板 |
| `/直播总结` | [fengming-livestream-summary/SKILL.md](../fengming-livestream-summary/SKILL.md) | 直播输入与领域规范 |
| `/更新发布` | [summarize-update/SKILL.md](../summarize-update/SKILL.md) | 总结 / 推送 / 完整发布动作区分 |
| `/双版本` | [summarize-update/references/release-editions.md](../summarize-update/references/release-editions.md) | 标准 / FULL 产物与验收 |
| `/并行` | [convention-skill/references/coordination.md](../convention-skill/references/coordination.md) | 文件所有权 / WIP / 合回 |
| `/测试` | [testing-quality-gates/SKILL.md](../testing-quality-gates/SKILL.md) | 风险对应的质量门禁与验证 |
| `/测试清理` | [testing-quality-gates/references/test-cleanup.md](../testing-quality-gates/references/test-cleanup.md) | 先测后清，本任务与并行边界 |
| `/隔离测试` | [testing-quality-gates/references/isolated-testing.md](../testing-quality-gates/references/isolated-testing.md) | 独立程序副本与受控真实接入 |
| `/功能说明` | [software-feature-guide/SKILL.md](../software-feature-guide/SKILL.md) | 按页面读取使用说明 |
| `/WebUI同步` | [layering-contract/references/webui-sync.md](../layering-contract/references/webui-sync.md) | 桌面变化的四层接入和三层验收 |
| `/shaman` | [convention-skill/references/craftsmanship.md](../convention-skill/references/craftsmanship.md) | 默认不读的可选风格 |
| `/快捷指令` | [quick-commands/SKILL.md](../quick-commands/SKILL.md) | 本映射表 |

应用内技能发现仍会显示 25 个旧 ID；这表示 13 个主入口 + 12 个兼容入口，不等于维护 25 套正文。新增技能类型或路由时同步 AGENTS.md、主技能描述与本表，不再复制长规则摘要。
