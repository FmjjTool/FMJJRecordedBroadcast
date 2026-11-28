---
name: ui-visual-spec
description: "项目界面与图片输出规范的统一入口。修改 PySide6 界面外观、布局、交互、表格、表单、反馈文案，或程序生成的报表图、预览图、封面默认模板时使用；按任务读取对应参考，屏幕 UI 与导出图片的尺寸、字体和版式分开适用。"
---

# 界面与图片输出规范

先遵守 [convention-skill](../convention-skill/SKILL.md)、[cookie-data-protection](../cookie-data-protection/SKILL.md)与[核心模块保护](../recording-toolbox-core/SKILL.md)。本入口统一管理屏幕视觉、交互与图片输出，详细规范按下表分支维护。技能只使用 Markdown。

## 先判断输出对象，再按需阅读

先读完本入口，再完整阅读与本次改动有关的参考章节；不因某个分支触发就加载其余全部规范。一个任务同时修改屏幕预览和导出图片时，两条分支分别适用。

| 本次工作 | 需要阅读的详细规范 |
| --- | --- |
| 屏幕颜色、主题、字体、背景、自绘、图标或主题切换 | [屏幕视觉](references/screen-visual.md)第一至三节、第七节；图标还读第四节 |
| 页面边距、页签、工具栏、按钮、下拉、组件外观 | [屏幕视觉](references/screen-visual.md)第四至六节；涉及的颜色与字号查第二、三节 |
| 弹窗、表格、排序与分页、状态反馈、长任务、表单、按钮或错误文案 | [交互与反馈](references/interaction.md)对应章节及第六节检查清单；外观同时按屏幕视觉相关章 |
| 账号对比图、资产预览拼贴、报表图、封面默认模板、内部认证证书 | [图片输出](references/generated-images.md)的适用范围、共享实现、对应版式、测试与验收；证书另读 A4 例外 |
| 屏幕 UI 改动后的验收 | [屏幕视觉](references/screen-visual.md)第八、九节；历史偏差只在修改相关内容时查第十、十一节 |
| 需要历史样张的实现线索 | [界面规范样张](references/界面规范样张.md)，只作历史参考，现行规则以前述参考为准 |

## 共同边界

1. 屏幕色板唯一来源是 src/gui_qt/theme.py；生成图片的共享渲染与 token 由 src/toolbox/image_render.py 提供。两者共享颜色语义，各自的画布、字体大小和版式规则不能混用；页面不得另建色板或第二套渲染实现。
2. 屏幕 UI 不新增页面标题与说明头部，不恢复功能介绍、使用教程或帮助浮层；功能说明维护在 [software-feature-guide](../software-feature-guide/SKILL.md)。结构性图标使用 navigation.line_icon；用户生成正文按原有例外保留。
3. 表格统一使用 gui_qt/table_sort.py 的三态排序和 gui_qt/table_columns.py 的列宽自适应 / 重置。实现、状态与按钮位置按交互参考，不在页面复制排序或列宽逻辑。
4. 业务页面不新增登录窗口、凭据控件或登录引导入口，用户可见文字不出现凭据名词；未登录时给出无入口指引的「需要登录」或「登录已失效」提示。认证能力仍只读使用统一管理接口，真实已存凭据禁令不变。
5. 屏幕普通文字与图片中的普通文字目标对比度至少 4.5:1；大字至少 3:1，大字指至少 18pt（约 24px），或至少 14pt 粗体（约 18.67px）。辅助说明、placeholder、hover、focus 文字没有普遍豁免；例外与历史未达标项见屏幕视觉第二节。
6. 用户自定义封面、裁切、截图 / 抽帧、下载媒体和内部缩略图不强套报表画布或品牌页头页脚，仍遵守各自既有行为与数据保护。内部认证证书使用明确的 A4 例外。
7. 规范只约束本次已授权的改动，不自动授权修改业务规则、录播八模块或真实数据，不以统一风格为由批量重排其他页面。桌面用户可用能力变化时另按 [桌面到 WebUI 同步](../layering-contract/references/webui-sync.md)处理。

## 验证与截图

程序、界面、渲染和截图验证统一遵守 [testing-quality-gates](../testing-quality-gates/SKILL.md)、[隔离测试](../testing-quality-gates/references/isolated-testing.md)与[运行中实例保护](../testing-quality-gates/references/running-instance-guard.md)。使用隔离程序副本和虚构数据；屏幕视觉参考中的「真实窗口」指隔离副本使用 Windows 平台插件显示的窗口，不是接入用户当前实例。切主题、改定时或重启恢复的验收也仅操作隔离副本，不能关闭用户实例。

临时 harness、数据、日志和过程截图放根目录 _smoking/；只有最终报告证据截图放 Progress/ui/日期/，清理按 [测试清理](../testing-quality-gates/references/test-cleanup.md)。接入真实实例、真实数据或真实平台的申请、快照与还原只遵守隔离测试主规则，不在视觉规范另设放行路径；真实已存 Cookie 禁令始终适用。

纯 Markdown 维护只需检查内容、引用、链接、主入口与分支路由和差异范围；不为验证文档而启动应用、登录或生成真实业务数据。实际检查结果、未验证项及提交由主任务按总约定记录。
