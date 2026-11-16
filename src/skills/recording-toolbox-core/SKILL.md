---
name: recording-toolbox-core
description: "录播工具箱核心保护，任何项目更新前必读。自动整理、交互重命名/投稿、视频操作、音频提取、字幕转换、继续操作、日历浏览、文件迁移为核心逻辑处理中心；未获用户对本次具体模块与改动范围的明确授权，不得直接或间接修改。"
---

# 录播工具箱核心保护

先阅读 [convention-skill](../convention-skill/SKILL.md)。用户指定下面八个录播工具箱模块为核心逻辑处理中心，默认禁止改动。每次更新都应先核对本 skill，包含修改其他模块、共享基础设施和文档的任务。

## 唯一指定的八个核心模块

表中路径均相对项目根目录。保护的是模块功能及其调用链，不能只保护截图的文字或图标，也不能只按文件名判断。

| 截图中的模块 | 主路由 / 页面类 | 页面与主要处理位置 |
| --- | --- | --- |
| 自动整理 | `toolbox_organize` / `OrganizePage` | `src/gui_qt/pages/organize.py`、`src/toolbox/organize_engine.py` |
| 交互重命名 / 投稿 | `toolbox_rename` / `RenamePage` | `src/gui_qt/pages/rename.py`、`src/toolbox/rename_engine.py`、`src/toolbox/bili_upload.py`、`src/gui_qt/bili_upload_dialog.py`、`src/gui_qt/video_selection_dialog.py` |
| 视频操作 | `toolbox_editor` / `EditorPage` | `src/gui_qt/pages/editor.py`、`src/toolbox/edit_engine.py`、`src/toolbox/convert_engine.py`、`src/toolbox/concat_engine.py` |
| 音频提取 | `toolbox_audio` / `AudioPage` | `src/gui_qt/pages/audio.py`、`src/toolbox/audio_engine.py`、`src/toolbox/asr_engine.py`、`src/gui_qt/asr_settings_dialog.py` |
| 字幕转换 | `toolbox_subtitle` / `SubtitlePage` | `src/gui_qt/pages/subtitle.py`、`src/toolbox/subtitle_engine.py`；内嵌歌词页 `src/gui_qt/pages/lyrics_tool.py`、`src/toolbox/lyrics_engine.py` |
| 继续操作 | `toolbox_launcher` / `LauncherPage` | `src/gui_qt/pages/launcher.py`；外部程序启动与关联进程管理 `src/gui_qt/process_tree.py` |
| 日历浏览 | `toolbox_calendar` / `CalendarPage` | `src/gui_qt/pages/calendar.py`；日期目录读取复用 `src/toolbox/rename_engine.py` |
| 文件迁移 | `toolbox_migrate` / `MigratePage` | `src/gui_qt/pages/migrate.py` 内的扫描、复制/剪切、重复项处理及任务逻辑 |

同样保护视频转换旧入口 `toolbox_convert`、兼容页面 `src/gui_qt/pages/convert.py`，以及这些核心页面已有或后续拆出的页签、对话框与处理组件。文件映射不是放行清单：发现核心逻辑在其他文件中时，也按本 skill 保护，不因改名、拆分或移动文件失去保护。

## 禁止直接或间接改动

未经本次具体授权，不得执行：

- 删除、合并、改名、重排截图中的入口，改变路由/别名或把入口替换成简化页面；移除已有控件、参数设置、快捷键、预览、页签或功能。
- 修改各模块的算法、流程、参数默认值、FFmpeg/转录调用、输出命名、日期识别、文件过滤、元数据、覆写/删除策略、错误处理、线程任务及取消行为。
- 调整自动整理、交互重命名与投稿之间的衔接，绕过重命名/版权处理等已有投稿前置条件，或改变投稿账号、文案、分P、上传和发布行为。
- 借全局美化、性能优化、清理死代码、统一登录、重构或“顺手修复”更改核心；把核心迁到新文件或新入口后再修改以绕开限制。
- 修改共享代码而改变核心入口、交互、配置、生命周期、数据或输出行为，即使变更文件不在上表中。

共享影响尤其应核对：`src/gui_qt/main_window.py` 的录播工具箱菜单/路由/别名与工作目录广播，`src/gui_qt/toolbox_common.py`、`src/gui_qt/pages/base.py`、公共线程/控件/导航/主题，`src/toolbox/config_manager.py`、`src/toolbox/utils.py`、认证管理，以及视频页使用的 `src/image_sorter/core/media_compress_thread.py`。这些共享文件的其他独立功能不因此整文件冻结，但涉及核心的变更必须先确认授权与影响。

## 可以执行的工作与授权判断

允许只读查看源码、调用关系与差异，记录问题和建议，维护保护 skill/报告，并按总约定保存既有改动的本地备份。备份不修改已有源码内容，也不等于批准已有代码变更；新增保护规则不自动回滚历史或并行任务的改动。

用户要求处理其他模块时，优先完成不影响上述核心的部分；不能为了连带优化自行打开保护区。只读检查发现缺陷时先给出具体影响与方案，不能自动动手修复。

若本次需求确实需要核心改动，在修改前列出具体模块、文件、拟改内容及影响。用户尚未明确授权这些具体改动时，说明本 skill 的禁止条款并询问；用户已经明确要求修改该模块及具体功能时，在该范围内执行即可，不重复请求同一授权。笼统的“全局优化”“美化整个软件”“统一登录”“更新所有页面”不视为授权核心变更。

获准后按确认范围保留其余功能与参数，完成适用验证、当日报告和本地提交；授权仅针对本次具体变更，不永久解除保护。新的模块或超出范围的取舍需要另行明确，不自动扩大授权。

## 执行与收尾核对

1. 确认已读总约定与本 skill；逐项对照八个模块、旧入口、子组件及共享调用链。
2. 将拟改范围标记为保护区外、直接影响核心或间接影响核心；后两者必须有对应的用户具体授权。
3. 编辑前保留原有改动并按版本领先规则确定基础。核心未获授权时只分析和记录，继续可以独立完成的保护区外工作。
4. 收尾检查实际差异与授权范围，不能为让测试通过删除核心功能、放宽原约束或改写既有预期。发现意外改动时停止扩大范围，列出证据后按用户指示处理，不能覆盖其他任务的改动。
5. 将需求、修改范围、验证、未完成项及提交信息汇入唯一当日报告。本次只写保护文档时，检查 Markdown、引用、映射与实际提交文件即可，不为验证运行会改写用户文件、迁移录播或上传投稿的流程。

本 skill 仅包含 Markdown，由 Agent 阅读后人工核对执行；目录发现或导出不等于已安装运行时拦截器。涉及登录仍遵守 [cookie-manager](../cookie-manager/SKILL.md)，涉及腾讯文档数据写入仍遵守 [tcdoc-sync-scope](../tcdoc-sync-scope/SKILL.md)；这些其他任务不自动放宽录播核心保护。
