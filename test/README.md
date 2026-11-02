# 腾讯文档表格数据 → 本地同步（实验）

实验目标：把腾讯文档里的**智能表格数据**同步到本地，能识别新增 / 修改 / 删除。
本目录全部是实验代码，**未接入主程序**。

---

## 一、结论

**可以直接做，且不依赖登录态。**

| 项 | 结果 |
|---|---|
| 文档 | 3 个（封茗囧菌数据汇总库 / 百科用户反馈与收集 / 「封茗囧菌」无料周边汇总） |
| 能取到的表 | 19 张（9 + 4 + 6） |
| 累计行数 | 7110 行 |
| 全量耗时 | 19.6s + 9.6s + 10.7s ≈ 40s |
| 增量识别 | 新增 / 修改 / 删除 三类全部命中，未改动的表不产生记录 |
| 免登录 | 公开文档不注入登录态也能读全量（`isLogin=False` 仍拿到 43 列 691 行） |

按文档拆分：

```
DRnd3bkhkQUR1aEJr  封茗囧菌数据汇总库     9 表 / 7033 行
DRndLZVRmVW1GZExW  百科用户反馈与收集     4 表 /   51 行
DRkZIbkZtQ0taTHd4  「封茗囧菌」无料周边汇总  6 表 /   26 行
```

> ⚠️ **文档 key 区分大小写**。「无料周边汇总」的 key 是 `DRkZIbkZtQ0taTHd4`
> （`Q0` 后面是小写 `ta`）。写成 `Q0TaTHd4` 会直接显示「文档不存在」，
> 看起来像文档被删了，其实是 key 打错了。URL 带 `?tab=xxx` 时记得把 query 剥掉再取 key。

---

## 二、取数原理

1. 用 Playwright 打开文档页（复用 `toolbox.browser.launch`，Edge → Chrome → 内核）。
2. 页内 `fetch` 调接口：
   `https://docs.qq.com/dop-api/opendoc?...&id=<key>&normal=1&outformat=1&wb=1&nowb=0`
   - 不带 `tab` → 返回文档骨架（`collab_client_vars.header[].d[]` = 工作表清单）
   - 带 `&tab=<tabId>&startrow=0&endrow=10000` → 返回该表数据
3. 响应是 **JSONP**（`cl({...})`）→ 剥壳成 JSON。
4. 数据在 `clientVars.collab_client_vars.initialAttributedText.text[0].smartsheet`，**两种载荷形态**：
   - 小响应：base64 + zlib（腾讯不给 `=` padding，需自己补）
   - 大响应（>约 1MB，如完整歌曲 4.7MB）：**直接内联 JSON 文本**
5. 解码后是 `[ [元数据块 t=3005, 记录块 t=3028] ]`。
6. 两套键名：数据汇总库用 `{"1":..}`，反馈库用 `{"k1":..}` → 统一 `deep_unkey()` 把 `k1` 归一化为 `1`，之后同一套代码解析。

结构（归一化后）：

```
block t=3005                          # 元数据
  c.3.3  = { fieldId: {"30": 列名, "31": 类型, "9"/"17": {选项表}} }
  c.3.4  = [ 视图 ]                   # 视图["3"]["1"]=行顺序，["3"]["2"]=列顺序
  c.3.5  = { userId: {"2": 昵称} }     # 人员映射
block t=3028                          # 记录
  c.2.1  = { recordId: {"1": { fieldId: cell }} }
  c.2.2  = { recordId: {"1": 修改时间戳, "2": 修改人} }
cell     = { "30": 类型, "31": 创建人, "32": 时间, "<类型码>": 值 }
```

## 三、字段类型映射（已实现）

| 类型码 | 含义 | 取值方式 |
|---|---|---|
| 1 | 文本 | 段落数组 `[{1:"text",2:内容}]` 拼接（兼容单 dict 形态） |
| 2 | 数字 | 直接取 |
| 3 | 复选框 | `True` → ✓ |
| 4 | 日期 | 毫秒时间戳 → `YYYY-MM-DD`（UTC+8） |
| 5 / 6 | 图片 / 附件 | 取 URL 或文件名 |
| 8 | 链接 | `{1:"url",2:显示,3:跳转}` → 取跳转地址 |
| 9 / 17 | 单选 / 多选 | 选项 id → 选项名（来自字段定义的选项表） |
| 10 / 11 | 创建人 / 成员 | userId → 昵称 |
| 12 / 13 | 创建时间 / 最后编辑时间 | 毫秒时间戳 → `YYYY-MM-DD HH:MM` |
| 16 | 邮箱/电话 | 直接取 |
| **18 / 21** | **关联其它表** | **跨表翻译**：先落 recordId，全部表解析完后用 `resolve_links()` 换成目标行主字段文本（如「场次」`rCTJxM` → `长沙ACC`） |
| 19 | 公式 | **拿不到值**（见限制） |
| 25 | 自动编号 | `{1:序号,2:完整编号}` → 取完整编号 |

---

## 四、增量机制

SQLite `test/_out/tables.db`：

- `sheets(doc_key, tab_id, tab_name, cols_json, n_rows, fetched_at)`
- `rows(doc_key, tab_id, record_id, row_no, data_json, row_hash, updated_ts, synced_at)`
- `changes(id, ts, doc_key, tab_id, tab_name, kind, record_id, summary, before_json, after_json)`

判定：以 `record_id` 为键，对 `data_json` 求 md5。
- 库里没有 → **新增**
- 有但 hash 不同 → **修改**（并记录实际变动了哪几列）
- 库里有、这次没抓到 → **删除**

### 实测（人为制造 3 类差异后重跑）

```
完整歌曲   691 行  新增 1   修改 1   删除 1     ← 期望值，完全吻合
其余 8 张表        新增 0   修改 0   删除 0     ← 无变化不误报
```

`changes` 明细：

```
修改 rwXdXJ  歌曲名称          （定位到具体变动列，库里已回写远端真实值 声声慢）
新增 rS6yh9  Shattered Crown  （自动抓主标识字段做摘要）
删除 rFAKE001 这条远端已经删了 （保留删除前快照）
```

再原样重跑一次（3 个文档、19 张表）→ 全部 0/0/0，说明幂等、不会重复刷。

---

## 五、已知限制（需要你决策）

1. **公式列值拿不到**。类型 19 的单元格服务端不返回计算结果，`formulaResults` 恒为 `null`，
   试过 `calcByFrontend=0` / `getFormulaResult=1` / `formulaResult=1` / `withFormulaResult=1`
   等多个参数均无效（看起来是前端算或走 websocket 推送）。
   受影响列（完整歌曲）：作词原创性、作曲原创性、作品性质、发行平台列表、发布天数、线下演唱频次。
   → 可选方案：① 本地用 Python 复算公式（表达式在字段定义里，如
   `IF(ISNUMBER(SEARCH("封茗囧菌",[作词人])),"自作词","非作词")`，需要一个小型求值器）；
   ② 该列留空并标注；③ 不做。

2. **普通表格（grid）、仪表盘（dashboard）、新版的文章页（smartcanvas）读不到**。
   - 「囧菌百科」是 grid，数据是 protobuf 编码，与智能表格不是一套协议；
   - 3 个 dashboard（用户反馈收集预览 / 综合查看表 / 已结束丨双册首次预览 / 数据）是图表视图，本身没有数据行；
   - 「无料周边汇总」里的指南 / 服务 / 消息 3 个 tab 是 **smartcanvas**（新版文章型页面），不属于表格。
   → 目前已跳过并在界面标注。

3. **大文档首次 meta 请求可能返回空 header**。无料周边汇总那份 197MB，页面初始化要 8~10 秒，
   第一次 `opendoc` 拿不到 `header[].d[]`，会误判成「无工作表」。
   已加 8 轮 × 2.5s 重试，实测第 2~3 轮就有数据了。

4. **遍历全量靠 `endrow` 一次性拉**。完整歌曲 691 行 = 4.7MB，官方动态 5000 行 = 约 5MB，
   单表一次请求即可；但如果表格继续膨胀到几万行，需要考虑分段（`startrow/endrow` 已支持）。

5. **筛选视图不影响总量**。已验证同一张表用不同 `viewId`（含带筛选的「有效活动」和
   不带筛选的「管理页」）取数，`total_record_count` 与解出条数完全一致，
   说明接口返回的是**整表**而不是当前视图筛选后的子集。

---

## 六、合并进主程序的方案（待你确认）

1. `src/toolbox/tcdoc/tables.py`：把本目录 `tcsync.py` 的 `unblob / deep_unkey / parse_chunk /
   render_cell / save / export_excel` 搬过去（去掉 CLI 部分），存储路径改用
   `get_export_subdir("tcdoc")/tables.db`，Excel 放 `报表/表格/`。
2. `src/toolbox/tcdoc/job.py`：`collect_all()` 里加一步「同步表格」，与浏览量采集共用同一次
   浏览器会话（省一次启动）；定时任务到点一起跑。
3. `src/gui_qt/pages/tcdoc.py`：新增第 4 个 Tab「表格同步」——左侧表清单（表名 / 行数 / 上次同步时间），
   右侧该表数据表（分页），顶部「🔄 同步表格」「📊 导出 Excel」，底部「变更记录」（新增/修改/删除明细）。
4. 配置键（须登记 `DEFAULT_CONVERT_CONFIG_DICT`）：
   `tcdoc_sync_tables`（是否开启表格同步，默认 True）、`tcdoc_table_rows`（界面每页行数）。
5. 文档清单复用现有 `tcdocs`，按 tab 的 `type` 自动判断：`smartsheet` 同步，
   `grid` / `dashboard` / `smartcanvas` 跳过并在界面列出跳过原因。
   现有 4 个预置文档里：「用户协议与编辑须知」是 `aio`（文章型），不参与表格同步。

## 七、本目录文件

| 文件 | 用途 |
|---|---|
| `tcsync.py` | **核心原型**：抓取 → 解码 → 结构化 → SQLite 增量 → Excel |
| `inspect_tab.py` | 单表结构检查（列 / 首几行 / 原始值） |
| `probe_*.py` / `peek_*.py` | 一次性探索脚本（接口、分页、chunk 形态、公式参数、grid） |
| `_out/tables.db` | 实验库（3 个文档 / 19 张表 / 7110 行） |
| `_out/*.xlsx` | 三份导出结果 |
| `_dump/` | 抓包与原始响应留档 |
