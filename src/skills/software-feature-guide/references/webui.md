# WebUI 独立服务

> **当前有效范围优先**：Agent 对腾讯文档的写入仅限《封茗囧菌数据汇总库》（`doc_key=DRnd3bkhkQUR1aEJr`）的「官方动态」`ss_h2uh2s` 与「直播记录」`ss_1xgstm`。白名单只是上限，执行仍需本次任务授权与门禁。下文五表上传、线下活动宣传图追加、活动歌单回填等白名单外按钮和旧授权仅保留为既有实现说明，**不构成现行许可，Agent 不得触发**；详见 [腾讯文档范围](../../tcdoc-sync-scope/SKILL.md)。本轮仅整理文档，未修改应用实现。

> 本页只加载服务启动、访问、架构和共同限制；具体功能按下方页面链接读取。桌面详情另见 [页面索引](../SKILL.md)。

-   票务与购物解析输入卡片均外置「平台范围」复选框，默认全选，平台选项来自共享定义。票务搜索、链接和 JSON 导出透传同一白名单；购物在勾选范围内自动识别，跳过未勾选平台，保留一次解析首个匹配平台的规则。全部取消勾选时显示行内提示并禁用解析/导出，后台任务期间锁定平台复选框；下方商品历史页签仅用于浏览已保存产物。

-   腾讯文档中间层包含「歌曲片段」本地只读快照：选表后点「刷新快照」，显示原列、已有记录与快照行数，可查看 TSV；不按动态的账号或时间范围筛选，不开放上传。接口复用 `/api/tcdoc/bridge` 与 `/api/tcdoc/bridge/preview`，业务实现仍在共享只读服务，WebUI 不持有第二份数据。

- 启动：全局设置「WebUI 独立服务」卡片可启动 / 停止 / 打开浏览器 / 随软件启动；也可源码运行 `python main.py --webui --webui-port 8765`，打包版运行 `FengMingToolbox.exe --webui --webui-port 8765`。可选 `--webui-host`（默认 `127.0.0.1`）、`--webui-token`（不填则生成并写入 `config/webui_url.txt`），也可用环境变量 `FMJJ_WEBUI_TOKEN`。
- 手机访问：设置页勾选「允许局域网访问」并重启 WebUI 后监听 `0.0.0.0`，手机与电脑同一 Wi-Fi 可访问；「手机二维码」生成含访问令牌的局域网链接，首次访问需在 Windows 防火墙提示中允许。公网访问使用「WebUI 公网访问（隧道）」卡片：Cloudflare 快速隧道提供临时 HTTPS 地址（需 cloudflared）；Tailscale 用私有网络（需登录，WebUI 需允许局域网，停止只清显示状态、不执行 `tailscale down`）；frp 需选择 frpc 配置文件（公网地址由服务器侧配置，可在配置里填公网地址用于显示）；不要把端口直接映射到公网。
- 服务完全独立于桌面 Qt：只加载 `toolbox/webui` 的 Qt-free 服务端与原生 Web 前端，不创建窗口、不导出控件树；访问地址在启动输出和 `config/webui_url.txt` 中。深链可直达页面（如 `?token=...#/editor`），登录后会保留 `#/` 路由并移除令牌。Web 前端沿用 `ui-visual-spec` 的字号阶梯、圆角档位与侧栏尺寸（侧栏 228 / 窄屏 200、导航 8×11、按钮 / 导航圆角 8、卡片 12、工具卡 10），颜色取 `theme.py` 语义令牌（只改 WebUI CSS，不动 `theme.py` / QSS）。
- 架构边界（强制隔离）：WebUI 是 L2 显示层，只做参数校验、DTO/HTTP 适配与界面状态；业务查询、扫描、导出等用例统一放在 `toolbox/` 的独立 L3 Web 中间层（`webui_tools.py`、`asset_query.py`、`live_record_status.py`、`webui_modules.py`），与桌面端用例分文件、分进程隔离。中间层主要依赖 L4 引擎与 L5 设施，也可无环复用共享 L3 用例、DTO 与业务服务；禁止 `PySide6`/`gui_qt`，不反向调用桌面页面；为 WebUI 加能力默认不改桌面行为；WebUI 崩溃不影响桌面实例；验证一律用隔离副本。详见 `layering-contract` 的「WebUI 中间层隔离」。

## 按页面读取

- [工作台](webui/dashboard.md)
- [账户追踪](webui/account-tracking.md)
- [粉丝群](webui/fan-groups.md)
- [抖音指数](webui/douyin-index.md)
- [微博超话](webui/weibo-chaohua.md)
- [采集结果](webui/collection-results.md)
- [采集任务](webui/collection-tasks.md)
- [采集工具](webui/collection-tools.md)
- [网络库](webui/netdisk.md)
- [资料站](webui/localwiki.md)
- [直播总结](webui/livestream-summary.md)
- [媒体筛选](webui/media-sorter.md)
- [批量改名](webui/batch-rename.md)
- [票务解析](webui/tickets.md)
- [购物解析](webui/shopping.md)
- [歌单制作](webui/music-playlist.md)
- [歌曲下载](webui/music-download.md)
- [音乐标签](webui/music-tags.md)
- [性能诊断](webui/performance.md)
- [封面制作](webui/cover-maker.md)
- [图片裁切 / 证书制作 / 网络代理](webui/desktop-only.md)
- [资产库](webui/assets.md)
- [录制](webui/live-recording.md)
- [腾讯文档](webui/tcdoc.md)
- [录播工具箱](webui/recording-toolbox.md)
- [任务](webui/scheduled-tasks.md)
- [系统](webui/system.md)

## 登录、会话与限制

- 登录与会话：启动令牌（长期）只在登录时使用——打开带 `?token=` 的地址自动换短期会话票据（12 小时、内存存储、服务重启失效）后清除 URL 参数，浏览器只保存票据不保存长期令牌；会话过期自动回到登录页。启动令牌仍可直接调用 API（兼容脚本与旧地址）。`/api/auth/login`、`/api/auth/logout` 需通过 Origin 校验。
- 服务端只监听指定地址，`/api/*` 需令牌或会话票据；POST 校验 Origin。WebUI 与桌面界面相互独立，页面样式和布局不再依赖桌面控件树。
- 限制：首版不镜像桌面弹窗/原生文件框/自绘图表；本地文件单次最多扫描 50000 个文件（超出截断提示），扫描结果缓存 5 分钟，目录变化或过期后自动后台刷新；完整度匹配不含 B站资源包目录；腾讯文档 Web 端既有单条 / 批量新增、文本修改 / 删除、当次新增选项及新增行评论区图片等能力见 [腾讯文档说明](webui/tcdoc.md)，Agent 始终只可在两表白名单及当次授权内操作；线下活动宣传图追加与活动歌单回填虽有历史实现，当前对 Agent 禁写；迁移的重复项覆盖 / 删除本地处理仅在桌面端；手机端已覆盖全部导航页与录制启停（需桌面运行）；命名投稿的 B站投稿已接入预检 + 确认上传（需 B站登录 Cookie，真实上传请实机验证）；视频操作已接入轨道 / 元数据 / 章节 / 加音轨字幕 / 静音黑帧场景检测 / 片段 CSV·EDL·章节导出与 CSV 导入 / 预览播放；网页播放器受浏览器编解码限制，MKV、部分编码的 AVI / FLV 仍建议用桌面端预览。
