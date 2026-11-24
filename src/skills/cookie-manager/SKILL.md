---
name: cookie-manager
description: "统一账户登录与认证管理。涉及登录界面、Cookie/Token 接口、认证业务或旧格式适配时必读；页面不得自建 Cookie 管理器、不得出现 Cookie 字样，登录只走统一入口；先遵守真实 Cookie 绝对保护禁令，业务只读使用已有有效凭据，写入/清除/迁移能力仅用隔离虚构数据验证。"
---

# 统一账户登录与 Cookie 管理

先阅读 [convention-skill](../convention-skill/SKILL.md)。本 skill 规定应用账户管理与业务认证的统一方式；按平台原生认证协议处理 Cookie、Token、会话和浏览器登录快照。

## 最高优先级：真实 Cookie 绝对不能改动

必须先完整阅读 [cookie-data-protection](../cookie-data-protection/SKILL.md)。本 skill 的登录、保存、清除、换号、刷新与迁移均是接口能力说明，不授权 Agent 操作真实已存 Cookie。真实数据不设确认后覆盖或其他例外；下述能力只能在独立临时目录使用虚构数据实现验证，业务请求仅通过已确认无写入副作用的入口只读使用现有有效凭据。

## 必须遵守的统一规则

1. 所有需要登录的账户统一放在「Cookie 管理」中管理，覆盖现有平台、新增平台和 GitHub 等 Token 账户。登录、重新登录、账号选择/切换、验证和清除只使用该管理入口。任何页面、对话框、浮层、子窗口不得另行创建 Cookie 管理器、登录窗口、凭据输入/保存控件或第二套凭据列表；页面文案不得出现「Cookie」「cookie」及同类凭据字样，也不得放置「打开 Cookie 管理」「去登录」等引导入口，用户自行前往总管理器（项目约定 9）。
2. 需要登录的功能统一调用认证管理层的有效凭据接口。业务层不另做登录窗口、不直接读取凭据文件、不保存另一份长期登录态、不硬编码 Cookie/Token。
3. 缺少平台或凭据类型时，先增加管理入口、管理层接口与平台适配，再让业务使用该接口。统一的是账户管理与调用入口，各平台仍使用自己的 Cookie、Bearer Token 或会话协议。
4. 业务使用当前账号的有效登录态，遵守总开关、过期、清除和账号变化。请求所需的 CSRF/签名字段从同一次有效凭据快照派生；任务启动/重试前重新确认账号与状态，不能混用两次登录的身份。
5. 未登录、关闭或失效时，按功能需要进入公开/游客流程，或报告认证不可用；Agent 不代为重新登录、清除、刷新或回写真实 Cookie。不能偷偷使用旧文件、浏览器 profile 或快照恢复登录。
6. 登录票据只传给对应平台的认证目标；上传/CDN 的临时授权由平台适配处理，不能附带转发账户 Cookie。Cookie、Token、登录快照等敏感内容不进入源码、skill、报告、日志、Git 提交或发行包。

## 当前代码接口与边界

以下路径与函数已在本次编写时核对；修改业务前仍应读取当前实现。表内接口不是“全部迁移完成”的声明。

| 位置 | 已有入口 | 使用规则 |
| --- | --- | --- |
| `src/gui_qt/cookie_manager.py` | `CookieManagerDialog`、`COOKIE_PLATFORMS`、浏览器/扫码/手工输入入口 | 统一登录 UI；新增账户在此接入管理入口 |
| `src/gui_qt/cookie_manager.py` | `_TcdocOpenApiDialog` | 腾讯文档 OpenAPI 三字段（Client-Id / Access-Token / Open-Id）管理；存储沿用 `auth` 加密文件的 `tcdoc_openapi` 键，页面不再提供输入控件 |
| `src/auth.py` | `get_cookie(platform)` | 普通业务获取有效 Cookie，受总开关和本地有效期规则控制；服务端仍可能判定失效 |
| `src/auth.py` | `set_cookie(platform, text)`、`clear_cookie(platform)` | 现有写入/清除能力；Agent 禁止对真实 Cookie 调用，只能验证隔离虚构数据 |
| `src/auth.py` | `is_cookie_enabled()`、`set_cookie_enabled(enabled)` | 读取须无写入副作用；写总开关会改同一凭据文件，禁止在真实环境调用 |
| `src/auth.py` | `get_netease_cookie()` | 只读统一加密存储；旧文件隐式迁移已移除 |
| `src/auth.py` | `load_cookies()`、`get_netease_cookie_stored()` | `load_cookies()` 只用于必要的管理展示，不能绕过有效性规则；后者只读统一存储，不再触发旧文件迁移 |
| `src/toolbox/tcdoc/session.py` | `cookie_string()`、`apply_to_context()`、`save_state_snapshot()`、`clear()` | 读取/注入须确认不会回写真实状态；快照保存和清除只在隔离虚构环境验证 |
| `src/toolbox/webwiki/credentials.py`、`auth.py`，`src/gui_qt/cookie_manager.py` | GitHub Token 统一凭据适配与 `_GitHubTokenDialog` | 已迁入统一管理；新文件 `config/github_auth.json`，旧 `config/webwiki_auth.json` 只读兼容；业务不得直接读写凭据文件 |

现有平台 key：`bilibili`、`douyin`、`weibo`、`netease`、`qq`、`kugou`、`kugou_lite`、`kuwo`、`5sing`、`xiaohongshu`、`kuaishou`、`taobao`、`tcdoc`、`qqgroup`。引用现有入口时使用实际 key；新增 GitHub 等账户须补实际注册与凭据类型接口，不能假定已经存在。QQ 群的登录态用于粉丝群页的群号查询兜底（qinfo / 群搜索），业务只读获取，不在页面保存或展示凭据。

GitHub 的 GCM/系统凭据可作为统一管理层的原生后端；业务通过管理层适配获取授权，Token 保持 Token 类型，不拼成 Cookie 请求头。正常 Git 工具认证仍使用其原生凭据机制，不在同步命令、远端 URL 或日志里暴露 Token。

**约定 9 的既有待清理项**（本次只记录，未改代码）：`bili_emoji.py`、`bili_resource.py`、`bili_danmaku.py`、`bili_shop_page.py`、`douyin_index.py`、`info_accounts.py`、`shopping_page.py`、`douyin_danmaku.py`、`video_comments.py`、`livestream_summary.py`、`settings.py`、`music_tracker.py` 等页面仍有 Cookie 字样或登录引导。涉及这些页面改动时按约定 9 清理，不能借新增规则批量修改。`quark_backup.py`、`quark_download.py`、`netease.py`、`tcdoc.py`、`localwiki.py` 与 `bili_upload_dialog.py` 已在 2026-11-17 的凭据红线批次清理；`tcdoc_credentials.py` 已删除，OpenAPI 三个 ID 改由统一管理对话框维护。

当前还需迁移/核对的具体入口：腾讯文档 `has_login()` 仍接受快照文件存在作为登录条件。涉及该链路时，须消除绕过关闭/清除状态的路径并验证，不能直接沿用它证明统一规则已满足。

## 浏览器快照与旧格式迁移

**本节只规定能力设计与隔离验证，不在真实用户环境迁移、保存或清理凭据；真实已存 Cookie 绝对保护优先。**

- 腾讯文档 `storage_state` 包含 Cookie 与 localStorage，不能只靠 Cookie 字符串保证可重建完整登录。快照仅作管理层控制的补充状态，须匹配当前账号；管理层关闭、清除或换号后旧快照不能继续授权业务。
- 酷狗概念版等非 Cookie 会话走统一管理中的平台适配。迁移过程中可以保留必要的底层格式，读取、更新和清除须受同一管理层控制，不能形成两套账户状态。
- 酷狗主版另有「App 扫码」入口（登录状态 → 酷狗行 → App扫码）：主版 App 令牌只用于云歌单写入，存在独立会话文件 `config/kugou_app_session.json`，由 `src/music/kugou_app_auth.py` 唯一读写，**不写入也不改动** `cookies.json` 里的网页登录态；网页登录态仍是业务默认凭据，App 令牌失效时重新扫码即可。酷狗云歌单只认 App 令牌，网页令牌写入会返回 20017。
- 先盘点受影响链路的登录 UI、凭据读写、缓存、profile 和全部业务调用方；读取旧格式与当前管理记录时不输出凭据内容。
- 迁移旧凭据时不覆盖管理层已有的较新账号状态；确认转换与保存成功、调用方已切换后，再停用旧读取路径并按确认范围清理旧状态。迁移失败保留原数据并报告，不写成已统一。
- 明确区分首次迁移与用户主动退出/清除，保存退出状态或移除相应旧来源，避免下一次读取又自动导入旧票据。不能用“文件仍存在”判定当前可登录。
- 长任务不得在换号后悄悄改用另一个账号；按任务身份暂停、取消或重新启动，并在新请求前核对当前状态。公开请求的短期游客票据由平台适配处理，不登记成用户已登录账户。

## 验证与收尾

涉及代码/适配能力修改时，在独立临时目录使用虚构凭据验证受影响业务：管理入口登录后可调用；关闭后认证请求不可用；清除后旧文件/快照不会复活；失效提示一致；重新登录/换号不会混用旧状态；模拟旧用户迁移保留正确账号；多处业务使用同一管理入口。禁止复制真实 Cookie 或真实配置，也不启动可能隐式迁移/回写的真实管理窗口；不为验证发起未授权的发布或数据写回。

只维护 skill 文档时，核对接口、引用、规则的一致性和 Markdown 格式即可；明确实际迁移的未完成项，不声称执行了登录或业务验证。任何腾讯文档数据写入还须遵守 [tcdoc-sync-scope](../tcdoc-sync-scope/SKILL.md) 白名单。

按总约定更新唯一当日报告并本地提交。此 skill 只包含 Markdown，不附带执行脚本或配置文件；被内置 skill 目录发现/可导出，不等于运行时已经执行全部管理约束。
