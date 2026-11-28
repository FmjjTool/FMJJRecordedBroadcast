---
name: software-feature-guide
description: "软件页面功能与使用说明。用于功能介绍、页面说明、使用教程、某页怎么用、软件功能索引；按页面读取 references，不默认加载全量页面正文。"
---

# 软件功能说明（按页面读取）

## 定位

-   软件内的「功能介绍」「使用说明」「使用教程」按钮与浮层已全部移除；页面不再提供介绍入口。
-   本 skill 是功能说明的唯一维护处。新增、改名、调整入口/保存路径/限制时同步更新本文，不重新添加页面内帮助控件。
-   本文只说明用户可见功能，不改变数据结构、同步范围、统一账号管理及录播八模块的既有授权边界。
-   桌面端功能变化后同步到 WebUI 的接入流程、桌面专属判定与验收口径见 [webui-sync](../webui-sync/SKILL.md)；页面功能说明仍只在本 skill 维护。

本入口负责定位与导航，详细业务说明按页保存在 `references/`。这些说明面向用户解释既有能力，不是 Agent 登录、停止实例、写入真实数据或修改受保护模块的授权。当前统一登录入口显示名是「账号与登录」；约定所称「Cookie 管理」指同一个管理器，未因本轮整理改名。

## 读取路由

1. 先用下面索引定位具体页面，只完整读取相应的 Markdown；跨页任务再读取相关页。
2. 涉及页签命名、表格通用操作时读取 [通用页面说明](references/common.md)。
3. 询问 WebUI 能力先读 [WebUI 服务与页面索引](references/webui.md)，再读其列出的对应 Web 页面，不默认展开全部 Web 功能。
4. 腾讯文档写入先完整读 [范围约束](../tcdoc-sync-scope/SKILL.md)。页面内历史实现说明不能扩张当前两表白名单。
5. 本 skill 目录整体导出即可携带全部页面说明；名称、旧入口路径与 skill ID 保持不变。

## 页面索引

> 页面注册（名称、侧栏分组、页面类路径、搜索别名、首页卡片文案）的唯一入口是 `src/gui_qt/page_index.py`：新增/改名只改这一处，`main_window.PAGE_CLASSES` 与侧栏、面包屑、搜索、首页卡片都由它派生；页面类、侧栏、容器页签、首页卡片与索引应保持一一对应；此文不把历史测试文件路径当作常驻实现。本文表格只做功能说明。

| 页面 | 代码入口 | 主要内容 |
| --- | --- | --- |
| [采集任务](references/collection-tasks.md) | `src/gui_qt/pages/info_accounts.py` | 批量账号追踪、作品抓取、增量入库 |
| [采集结果](references/collection-results.md) | `src/gui_qt/pages/info_results.py` | 分平台分页、跨页勾选、完整导出与下载 |
| [腾讯文档](references/tcdoc.md) | `src/gui_qt/pages/tcdoc.py` | 浏览量/成员数、表格同步、中间层、映射回写、完整歌曲检索 |
| [账号名单](references/accounts.md) | `src/gui_qt/pages/account_registry.py` | 已并入「账号与登录 → 账号配置」，仅保留兼容路由 |
| [资料站](references/localwiki.md) | `src/gui_qt/pages/localwiki.py` | 微信小程序主程序与 Wiki 网站的仓库更新和运行范围选择 |
| [资产库](references/assets.md) | `src/gui_qt/pages/asset.py` | 资源归档、采集记录、本地文件、活动视频；配置与资产备份统一在全局设置 |
| [网络库](references/netdisk.md) | `src/gui_qt/pages/netdisk.py` | 网盘备份 + 网盘下载两个页签（先备份后下载） |
| [视频评论](references/video-comments.md) | `src/gui_qt/pages/video_comments.py` | 抖音 / B站 / 微博评论抓取、作者模式、筛选与长图导出 |
| [B站资源](references/bili-resources.md) | `src/gui_qt/pages/bili_resource.py` | 表情包、装扮/收藏集、用户空间 |
| [抖音指数](references/douyin-index.md) | `src/gui_qt/pages/douyin_index.py` | 达人数据、作品与粉丝分析 |
| [粉丝群](references/fan-groups.md) | `src/gui_qt/pages/fan_groups_page.py` | 抖音/微博/B站公开粉丝群；QQ群分享链接/登录按群号订阅 |
| [微博超话](references/weibo-chaohua.md) | `src/gui_qt/pages/weibo_chaohua.py` | 超话访客态采集与历史分析 |
| [票务解析](references/tickets.md) | `src/gui_qt/pages/tickets_page.py` | 大麦/秀动/B站会员购 |
| [购物解析](references/shopping.md) | `src/gui_qt/pages/shopping_hub.py` | 淘宝 / 抖音商城 / B站小店 / 闲鱼（链接自动识别，闲鱼支持关键词搜索与入库下载） |
| [公众号文章](references/wechat-articles.md) | `src/gui_qt/pages/wechat_article.py` | 公众号资料、文章归档、历史同步与正文图片导出 |
| [直播总结](references/livestream-summary.md) | `src/gui_qt/pages/livestream_summary.py` | 转录总结、模型接口 / 本地 CPU·GPU / 规则模式与模型参数 |
| [抖音表情](references/douyin-emoji.md) | `src/gui_qt/pages/douyin_emoji_page.py` | 会员表情栏、直播收集与弹幕包补收（采集工具第 4 个页签） |
| [歌曲下载](references/music-download.md) | `src/gui_qt/pages/netease.py` | 单页：五平台对比 / 单平台解析 / 下载 / 导出（原「音乐信息获取」已并入） |
| [音乐标签](references/music-tags.md) | `src/gui_qt/pages/music_tag.py` | 音频标签/封面/歌词编辑，四平台网络匹配 |
| [歌单制作](references/music-playlist.md) | `src/gui_qt/pages/music_playlist.py` | 桌面 / WebUI 仅从线下活动曲目编排跨平台歌单，默认最新活动，完整歌单名可编辑，预览后写入并回填活动歌单链接 |
| [歌词转换](references/lyrics-conversion.md) | `src/gui_qt/pages/lyrics_tool.py` | 歌词格式转换、KRC 加解密 |
| [字幕转换](references/subtitle-conversion.md) | `src/gui_qt/pages/subtitle.py` | 字幕格式批量转换与运行日志 |
| [图片裁切](references/image-crop.md) | `src/gui_qt/pages/image_crop.py` | 四边参考线裁图与批量导出 |
| [视频操作](references/video-editor.md) | `src/gui_qt/pages/editor.py` | 视频预览与彩色片段标记、转换、合并、切割、压缩、轨道、截图与元数据 |
| [命名投稿](references/naming-upload.md) | `src/gui_qt/pages/rename.py` | 直播录屏逐条标准命名 |
| [自动整理](references/organize.md) | `src/gui_qt/pages/organize.py` | 来源视频按日期迁移到工作目录 |
| [音频提取](references/audio-extraction.md) | `src/gui_qt/pages/audio.py` | 视频音轨提取、音频转字幕与转录选项 |
| [批量改名](references/batch-rename.md) | `src/gui_qt/pages/rename_batch.py` | 八类规则批量改名 |
| [封面制作](references/cover-maker.md) | `src/gui_qt/pages/cover_maker.py` | 底图 + 三段文字封面、识别本地视频标题日期并批量补封面 |
| [证书制作](references/certificate-maker.md) | `src/gui_qt/pages/certificate_maker.py` | 协议录入、选项复核、自动水印、历史回填、两页认证证书与记录批量加密导出 |
| [弹幕姬](references/danmaku.md) | `src/gui_qt/pages/bili_danmaku.py`、`douyin_danmaku.py`、`gui_qt/danmaku_window.py` | 直播弹幕实时采集/回放（独立窗口，直播录制页「弹幕姬」按钮打开） |
| [直播录制](references/live-recording.md) | `src/gui_qt/live_record_window.py` | 多平台开播监控与录制 |
| [性能诊断](references/performance.md) | `src/gui_qt/pages/performance.py` | 可选启动自动监控、性能采样、页面 / 任务耗时、Python 分配快照与报告导出 |
| [工作台](references/dashboard.md) | `src/gui_qt/pages/home.py` | 本地只读总览、趋势与指标下钻 |
| [账号与登录](references/accounts.md) | `src/gui_qt/cookie_manager.py` | 统一账号配置、登录状态；约定名为「Cookie 管理」 |
| [定时任务](references/scheduled-tasks.md) | `src/gui_qt/pages/tasks.py` | 统一计划任务设置、安装与删除 |
| [全局设置与后台运行](references/global-settings.md) | `src/gui_qt/pages/settings.py` | 目录、备份恢复、主题、依赖组件、更新与后台模式 |
| [WebUI 独立服务](references/webui.md) | `src/toolbox/webui/` | 服务访问与按页面能力索引 |

## 维护约定

-   页面新增或调整功能时，先改代码，再更新本 skill 对应 references 页面；不在页面重新加入功能介绍控件。
-   页面顶端不显示页面标题与说明文字，页面身份由顶栏和侧栏提供；功能说明只在本 skill 维护。
-   新增或调整页面交互（弹窗形态、空态、进度与取消、分页、按钮文案）时，同步核对 `ui-interaction-spec`；外观与布局核对 `ui-visual-spec`。功能说明仍写在本 skill，不把交互规范复制进功能章节。
-   本文档不构成录播八模块、Cookie 数据、腾讯文档写入范围的改动授权。
