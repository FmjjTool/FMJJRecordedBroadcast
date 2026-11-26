---
name: data-lifecycle
description: "数据生命周期与迁移约束（新增/修改数据库、存储、去重规则、迁移、备份、保留清理时必读）。规定一个存储一个写入方、schema 版本、迁移前备份、去重键归属、primary/derived 关系、原子导出与回滚；不授权改动真实 Cookie 或腾讯文档白名单。"
---

# 数据生命周期（Data Lifecycle）

先阅读 [convention-skill](../convention-skill/SKILL.md) 与 [layering-contract](../layering-contract/SKILL.md)。涉及 Cookie/登录时按 [cookie-data-protection](../cookie-data-protection/SKILL.md) 与 [cookie-manager](../cookie-manager/SKILL.md)；涉及腾讯文档写入时按 [tcdoc-sync-scope](../tcdoc-sync-scope/SKILL.md)。

本 skill 规定持久化数据的 owner、schema、迁移、去重、保留、备份与恢复。它不改变既有数据，也不授权对真实 Cookie、真实账号数据或腾讯文档做迁移/覆盖/删除。

## 适用范围

- SQLite：`collected.db`、`increment.db`、`asset_index.db`、`tables.db`、`tcdoc.db`、`pages.db`、`revisions.db`、`members.db`、`quark_backup.db` 等。
- 文件型存储：账号 `accounts.json` / `accounts.txt` / `music_accounts.json`、分组、标签历史、导出索引、缓存。
- 配置与运行时数据：`config/`、`download/`、`logs/`、导出产物。
- 备份/恢复、导入/导出、清理/保留、迁移脚本与兼容层。

**不适用**：纯内存状态、一次性临时变量；但临时文件也必须限定目录并在结束时清理。

## 数据清单与 owner

新增或修改存储前，先写清：

| 项 | 必须说明 |
| --- | --- |
| 存储路径 | 绝对/相对位置，是否 gitignore，是否进安装包 |
| owner（唯一写入方） | 哪个模块负责写；其他模块只读 |
| readers | 哪些页面/服务读取；是否允许直接读 |
| schema 版本 | 版本号、迁移入口、兼容策略 |
| primary / derived | 主数据还是派生缓存；能否从其他数据重建 |
| 去重键 | 键定义、作用域、稳定性、生成函数 |
| 保留/清理 | 何时删除、保留多久、是否备份 |
| 备份/恢复 | 是否需要备份、恢复如何校验 |

同一存储禁止两个 writer。新增字段先改 owner 模块和 schema，再让页面/服务读取，不允许页面直接 `sqlite3.connect` 写库。

## Schema 与迁移

1. 每个库有 schema 版本（如 `_meta(schema_version)`）；迁移必须幂等、可重复执行。
2. 迁移前先备份数据库/文件；在副本或临时目录验证迁移，再对真实库执行。
3. 迁移只前进，不依赖“用户会回滚”；必须提供失败处理与恢复路径。
4. 不删除旧列/旧表，除非已确认所有 readers 迁移完成并取得用户明确同意；先保留兼容列，再逐步废弃。
5. 迁移过程中逐条/分批处理，记录新增、修改、删除数量；失败立即停止，不写入半成品。
6. 真实 Cookie/凭据存储的迁移仍按 `cookie-data-protection` 禁止；本 skill 不提供任何迁移授权。

## 去重与 primary/derived

1. 去重键必须由**一个函数/常量**定义，所有写入方共用；禁止在页面、导出、匹配器里各写一份。
2. 明确区分：
   - **storage dedup**：入库去重，决定唯一记录；
   - **display dedup**：列表展示合并，不能反过来影响存储；
   - **cross-store dedup**：跨库/跨平台合并，必须单独命名并说明范围。
3. 稳定 ID 优先：`video_id` / `bvid` / `status_id` 等；不要用会过期的 CDN 直链或每次变化的签名 URL。
4. 如果维护 `content_key` 或全局注册表：每次写入必须同步更新；如果不打算维护，就删除该机制或明确标记为“历史迁移专用”，不能只写不查/只查不写。
5. 双写（如 `collected.db` 与 `increment.db`）必须声明 primary 与 derived；新功能不得再新增双写，逐步收敛。

## 并发与事务

1. 每次操作短连接（开–关），不在模块级共享 SQLite 连接；跨线程使用各自连接。
2. 启用 WAL 和 busy timeout；写入使用事务；批量写入分批提交，失败可重试。
3. 一个库同一时间只允许一个 writer；需要并发读时使用只读连接。
4. 长事务不阻塞 UI；后台线程写入，主线程只读快照。
5. 取消/超时后保留已提交数据，不留下半写状态。

## 保留、清理与备份

1. 日志、缓存、临时文件按配置上限轮转/清理；用户数据不静默删除。
2. 批量删除/清空前确认；优先移入回收站/备份目录，而不是直接 `unlink`/`rmtree`。
3. 备份包含 manifest（版本、时间、来源、文件列表、校验和），默认排除 Cookie/Token/真实凭据。
4. 恢复前校验版本与完整性；不覆盖比备份更新的数据；恢复失败保留原数据。
5. 备份文件不进入 Git、安装包或导出目录之外的公共位置。
6. 账号主数据迁移前必须生成「只读快照」（原始文件 + 归一化 JSON + manifest 校验和），旧文件保留为 `.migrated.bak`，并提供关闭注册表开关即回退旧读取逻辑；迁移脚本先 dry-run 输出合并/冲突报告，用户确认后才落盘。
7. 主动备份与恢复 UI 只在「全局设置 → 备份与恢复」，聚合配置、资产与网盘备份，业务页不保留按钮、页签或快捷跳转（约定 17）。统一走 `toolbox/backup.py` 与 `toolbox/quark/backup.py`，不复制备份、合并、去重或同步记录（约定 12）；备份计划仍在「任务」页，内部覆盖前安全副本保留原有行为。
8. 腾讯文档中间层缺图补下的图片只落 `数据目录/中间层/图片缓存`（按 URL 哈希命名，可重建、可随时清理），**不写用户「download/已获取数据」媒体库**；中间层页面的「有图片」默认指链接，不是本地文件。
9. 官方动态「评论区图片」用的账号评论索引图只落 `数据目录/中间层/评论区图片/{平台}/{作品ID}`，评论原图落 `中间层/图片缓存`；两者都是可重建派生数据，不写用户媒体库，也不改 `download/评论区` 下的评论导出文件。

## 导出与导入

1. 导出先写临时文件，成功后原子替换目标；不直接覆盖正在被 Excel/WPS 打开的文件。
2. 导出包含 schema/格式版本；导入先校验版本与字段，再在临时目录解析。
3. 导入不直接覆盖真实库；先合并/去重，生成变更报告，再按确认范围写入。
4. 导出/导入过程中的错误按行/记录报告，不静默跳过整批。
5. 文件名、目录、时间戳统一（北京时间），避免同一批数据产生多个近似目录。

## 检查清单

- [ ] 新存储是否声明了路径、owner、readers、schema 版本、primary/derived、去重键、保留策略？
- [ ] 是否只有一个 writer？页面/服务是否只通过 owner 接口访问？
- [ ] 迁移是否幂等、先在副本验证、有备份和失败恢复路径？
- [ ] 去重键是否集中定义？storage/display/cross-store 是否分开命名？
- [ ] 是否存在双写？是否声明了 primary/derived 与收敛计划？
- [ ] SQLite 是否短连接、WAL、事务、单 writer、跨线程独立连接？
- [ ] 删除/清理是否有确认与备份，未静默删除用户数据？
- [ ] 导出是否原子写入、带版本；导入是否先校验再合并？
- [ ] Cookie/腾讯文档写入边界是否按对应 skill 核对，未被本 skill 放宽？

## 与其他 skill 的关系

- `layering-contract`：存储放 L5，用例编排放 L3；本 skill 补迁移/去重/备份细节。
- `testing-quality-gates`：迁移、去重、并发写必须有测试。
- `security-baseline`：备份/导出/日志不含 Secret；导入防路径穿越/ZIP Slip。
- `cookie-data-protection` / `cookie-manager`：凭据存储优先级更高，禁止迁移真实凭据。
- `tcdoc-sync-scope`：腾讯文档写入范围；本 skill 不放宽。
