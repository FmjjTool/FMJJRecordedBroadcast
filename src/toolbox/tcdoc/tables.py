"""腾讯文档监控 · 智能表格数据同步到本地。

链路：dop-api/opendoc 取工作表清单（纯 HTTP，只需 Referer）→ 逐表取数 →
解码结构化 → 落本地 SQLite（增量对比新增/修改/删除）→ 每个文档一份 Excel；
图片字段的 URL 同步登记到 images 表，由 images 模块增量下载到 图片/。

协议要点（都是实测踩出来的，别改）：
1. 接口返回 JSONP，要剥壳；HTTP 直连只校验 Referer，浏览器版靠页面上下文带 credentials。
2. 载荷两种形态：小响应 base64+zlib（腾讯不给 `=` padding），大响应直接是 JSON 文本。
3. 键名两套：`{"1":..}` 与 `{"k1":..}`，统一用 deep_unkey 归一化。
4. 列顺序在 `view["3"]["2"]`，行顺序在 `view["3"]["1"]`。
5. 关联字段（类型 18/21）值是对方 recordId，要全部表解析完再翻译（resolve_links）。
6. 大文档（几百 MB）首次 meta 可能空 header，必须重试几轮。

已知限制：
- 类型 19 公式列服务端不返回计算结果，只能留空。
- grid（普通表格，protobuf）、dashboard、smartcanvas（新版文章页）不是这套协议，跳过。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import zlib
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import config as cfg_mod

API_BASE = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
            "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
            "&id={key}&normal=1&outformat=1&wb=1&nowb=0")
PAGE_TIMEOUT = 90_000
BATCH = 5000          # 单次请求行数上限，超大表靠分段拉全（截断会导致假删除）
SYNC_KINDS = ("sheet", "smartsheet")
CST = timezone(timedelta(hours=8))

FIELD_TYPES = {1: "文本", 2: "数字", 3: "复选框", 4: "日期", 5: "图片", 6: "附件",
               7: "电话", 8: "链接", 9: "单选", 10: "创建人", 11: "成员",
               12: "创建时间", 13: "最后编辑时间", 16: "邮箱", 17: "多选",
               18: "关联", 19: "公式", 21: "关联", 25: "自动编号"}

# 关联字段先落 recordId，等全部表解析完再换成可读文本（分隔符不能是逗号，
# 关联值可能是带逗号的 dict repr）
LINK_TAG = "\x00link:"
LINK_SEP = "\x01"
_ID_RE = re.compile(r"^r[0-9A-Za-z]{4,12}$")   # 腾讯记录 id 形如 r83LFJ

_TAG_RE = re.compile(r"^k(\d+)$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sheets (
    doc_key TEXT, tab_id TEXT, tab_name TEXT, kind TEXT, cols_json TEXT,
    n_rows INTEGER, skipped INTEGER DEFAULT 0, fetched_at TEXT,
    PRIMARY KEY (doc_key, tab_id));
CREATE TABLE IF NOT EXISTS rows (
    doc_key TEXT, tab_id TEXT, record_id TEXT, row_no INTEGER,
    data_json TEXT, row_hash TEXT, updated_ts TEXT, synced_at TEXT,
    PRIMARY KEY (doc_key, tab_id, record_id));
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
    doc_key TEXT, tab_id TEXT, tab_name TEXT,
    kind TEXT, record_id TEXT, summary TEXT, before_json TEXT, after_json TEXT);
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_key TEXT PRIMARY KEY, rev TEXT DEFAULT '', updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_rows_page ON rows (doc_key, tab_id, row_no);
CREATE INDEX IF NOT EXISTS idx_changes_doc ON changes (doc_key, ts);
"""


# ==================== 解码 ====================

def doc_key_of(url: str) -> str:
    """从 URL 取文档 key：必须剥掉 ?tab=xxx / #anchor，否则接口拿不到数据。"""
    path = str(url or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return path.split("/")[-1]


def strip_jsonp(text: str) -> str:
    s = (text or "").strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


def unblob(value):
    """腾讯的载荷有两种形态：大响应内联 JSON 文本，小响应 base64 + zlib。"""
    if not isinstance(value, str):
        return value
    s = value.lstrip()
    if s[:1] in "[{":
        return json.loads(s)
    raw = base64.b64decode(value + "=" * (-len(value) % 4))
    return json.loads(zlib.decompress(raw).decode("utf-8"))


def deep_unkey(node):
    """键名归一化：{"k1":..} → {"1":..}（两套协议键名统一）。"""
    if isinstance(node, list):
        return [deep_unkey(x) for x in node]
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            hit = _TAG_RE.match(key) if isinstance(key, str) else None
            out[hit.group(1) if hit else key] = deep_unkey(val)
        return out
    return node


def blocks_of(chunk) -> list:
    """chunk 里的 smartsheet 载荷 → block 列表。"""
    blob = (chunk or {}).get("smartsheet")
    if not blob:
        return []
    data = unblob(blob)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data


def _get(node, *keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict):
            return None
        if k in cur:
            cur = cur[k]
        elif f"k{k}" in cur:
            cur = cur[f"k{k}"]
        else:
            return None
    return cur


# ==================== 解析 ====================

def parse_meta_block(blk):
    """t=3005：字段定义 / 人员映射 / 视图。"""
    fields, users, views = {}, {}, []
    holder = _get(blk, "c", "3") or {}
    for fid, meta in (holder.get("3") or {}).items():
        if not isinstance(meta, dict):
            continue
        ftype = meta.get("31")
        opts = {}
        for container in (meta.get("9"), meta.get("17")):
            if isinstance(container, dict):
                for opt in container.get("3") or []:
                    if isinstance(opt, dict) and "1" in opt:
                        opts[opt["1"]] = opt.get("2") or opt["1"]
        fields[fid] = {"id": fid, "name": meta.get("30") or fid, "type": ftype,
                       "typeName": FIELD_TYPES.get(ftype, str(ftype)), "options": opts}
    for uid, info in (holder.get("5") or {}).items():
        if isinstance(info, dict):
            users[uid] = info.get("2") or uid
    for view in holder.get("4") or []:
        if isinstance(view, dict):
            views.append({"name": view.get("31"),
                          "recordOrder": _get(view, "3", "1") or [],
                          "fieldOrder": _get(view, "3", "2") or []})
    return fields, users, views


def parse_records_block(blk):
    """t=3028：记录本体 + 记录元信息（修改时间 / 修改人）。"""
    records, meta = {}, {}
    container = _get(blk, "c", "2") or {}
    for rid, wrap in (container.get("1") or {}).items():
        cell_map = wrap.get("1") if isinstance(wrap, dict) else None
        if isinstance(cell_map, dict):
            records[rid] = cell_map
    for rid, info in (container.get("2") or {}).items():
        if isinstance(info, dict):
            meta[rid] = {"updated_ts": info.get("1"), "editor": info.get("2")}
    return records, meta


def _ids(val) -> list[str]:
    """抽出关联字段引用的 recordId。

    关联值形态很不统一，实测见过三种：
      - 纯 id：`"rAbCd1"`
      - id 列表：`["rAbCd1", "rXyZ23"]`
      - 包装结构：`{"1": [{"1":1, "3":"r83LFJ", "2":0}]}`（外层 1 = 记录数组，
        内层 3 = recordId、2 = 序号）
    **绝不能 str(dict) 当 id**：腾讯返回的 dict 键顺序每次响应都不同，
    repr 一变就会被判成「修改」（实测一次误报 300 行）。
    """
    out: list[str] = []

    def walk(node):
        if isinstance(node, str):
            if _ID_RE.match(node):
                out.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(val)
    if not out and isinstance(val, str) and val:
        out.append(val)
    return out


def _to_date(ms, with_time=False) -> str:
    fmt = "%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d"
    try:
        return datetime.fromtimestamp(int(ms) / 1000, CST).strftime(fmt)
    except (TypeError, ValueError):
        return str(ms)


def render_cell(cell, field, users) -> str:
    """单元格 → 可读文本。cell = {30:类型, 31:创建人, 32:时间, <类型码>:值}。"""
    if not isinstance(cell, dict):
        return ""
    ftype = field["type"]
    val = cell.get(str(ftype)) if ftype is not None else None
    if val is None and ftype is not None:
        val = cell.get(ftype)
    if val is None or val == "" or val == []:
        return ""

    if ftype in (1, 16):  # 文本 / 邮箱电话
        if isinstance(val, list):
            return "".join(seg.get("2", "") for seg in val if isinstance(seg, dict)).strip()
        if isinstance(val, dict):
            return str(val.get("2") or val.get("1") or "").strip()
        return str(val)
    if ftype == 2:  # 数字
        return str(val.get("1", "")) if isinstance(val, dict) else str(val)
    if ftype == 3:  # 复选框
        return "✓" if val in (True, "true", 1, "1") else ""
    if ftype == 4:  # 日期
        return _to_date(val)
    if ftype in (12, 13):  # 创建时间 / 最后编辑时间
        return _to_date(val, with_time=True)
    if ftype == 5:  # 图片
        return " ".join(item.get("3") or item.get("1") or ""
                        for item in val if isinstance(item, dict))
    if ftype == 6:  # 附件
        return " ".join(item.get("1") or "" for item in val if isinstance(item, dict))
    if ftype == 8:  # 链接
        out = []
        for item in (val if isinstance(val, list) else [val]):
            if isinstance(item, dict):
                out.append(item.get("3") or item.get("2") or "")
            elif item:
                out.append(str(item))
        return " ".join(x for x in out if x)
    if ftype in (9, 17):  # 单选 / 多选
        ids = val if isinstance(val, list) else [val]
        return "、".join(field["options"].get(i, i) for i in ids if i)
    if ftype == 10:  # 创建人
        return users.get(val, str(val)) if val else ""
    if ftype == 11:  # 成员
        ids = val if isinstance(val, list) else [val]
        return "、".join(users.get(i, i) for i in ids if i)
    if ftype in (18, 21):  # 关联其它表（两阶段）
        return LINK_SEP.join(LINK_TAG + i for i in _ids(val))
    if ftype == 25:  # 自动编号
        return str(val.get("2") or val.get("1") or "") if isinstance(val, dict) else str(val)
    if ftype == 19:  # 公式：服务端不给计算值
        return ""
    if isinstance(val, list):
        return "、".join(str(x) for x in val if isinstance(x, (str, int, float)))
    return str(val)


def parse_chunk(chunk, tab) -> dict:
    """一页数据 chunk → {tab_id, tab_name, columns[], rows[]}。"""
    fields, users, views = {}, {}, []
    records, rec_meta = {}, {}
    for blk in blocks_of(chunk):
        if not isinstance(blk, dict):
            continue
        kind = str(blk.get("t"))
        body = deep_unkey(blk.get("c"))
        if not isinstance(body, dict):
            continue
        if kind == "3005":
            fields, users, views = parse_meta_block({"c": body})
        elif kind == "3028":
            records, rec_meta = parse_records_block({"c": body})

    order = next((v["fieldOrder"] for v in views if v["fieldOrder"]), []) or list(fields)
    order = [fid for fid in order if fid in fields]
    for fid in fields:
        if fid not in order:
            order.append(fid)
    rec_order = next((v["recordOrder"] for v in views if v["recordOrder"]), []) or list(records)
    rec_order = [rid for rid in rec_order if rid in records] + \
                [rid for rid in records if rid not in rec_order]

    columns = [dict(fields[fid], index=i) for i, fid in enumerate(order)]
    rows = []
    for pos, rid in enumerate(rec_order, start=1):
        cells = records[rid]
        rows.append({
            "record_id": rid,
            "row_no": pos,
            "values": {fields[fid]["name"]: render_cell(cells.get(fid), fields[fid], users)
                       for fid in order},
            "updated_ts": (rec_meta.get(rid) or {}).get("updated_ts"),
        })
    return {"tab_id": tab["id"], "tab_name": tab.get("name") or tab["id"],
            "kind": tab.get("type"),
            "columns": [{k: c[k] for k in ("id", "name", "type", "typeName")} for c in columns],
            "rows": rows}


def resolve_links(sheets) -> list:
    """全部表解析完后，把关联字段的 recordId 换成目标行的主字段文本。"""
    prefer = ("演出名称", "名称", "标题", "歌曲名称", "无料名", "奖项", "活动名称", "直播标题")
    index = {}
    for sheet in sheets:
        for row in sheet["rows"]:
            values = row["values"]
            text = next((values[n] for n in prefer
                         if values.get(n) and LINK_TAG not in str(values[n])), "")
            if not text:
                text = next((str(v) for v in values.values()
                             if v and LINK_TAG not in str(v)), "")
            index[row["record_id"]] = text or row["record_id"]
    for sheet in sheets:
        for row in sheet["rows"]:
            for name, value in list(row["values"].items()):
                if isinstance(value, str) and LINK_TAG in value:
                    names = [index.get(part[len(LINK_TAG):], part[len(LINK_TAG):])
                             for part in value.split(LINK_SEP) if part.startswith(LINK_TAG)]
                    row["values"][name] = "、".join(names)
    return sheets


# ==================== 抓取 ====================

def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _fetch_json(page, url: str) -> dict:
    js = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
          " return [r.status, await r.text()]; }")
    status, text = page.evaluate(js, url)
    if status != 200:
        raise RuntimeError(f"接口返回 HTTP {status}")
    return json.loads(strip_jsonp(text))


def _list_tabs(page, base: str, tag: str, on_status=None) -> list[dict]:
    """文档骨架 → 工作表清单；大文档首次可能为空，重试 8 轮。"""
    tabs: list[dict] = []
    for attempt in range(8):
        if attempt:
            _emit(on_status, "等待文档初始化……")
            page.wait_for_timeout(2500)
        data = _fetch_json(page, base)
        cc = (data.get("clientVars") or {}).get("collab_client_vars") or {}
        tabs = [tab for head in cc.get("header") or [] for tab in head.get("d") or []]
        if tabs:
            return tabs
    raise RuntimeError(f"文档「{tag}」没有返回任何工作表（可能已删除 / 无权限 / 是旧版文档）")


def collect_in_page(page, doc: dict, on_status=None) -> dict:
    """在一个已打开的文档页上抓取全部智能表格；page 必须已停在本文档。"""
    key = doc.get("key") or doc_key_of(doc.get("url"))
    base = API_BASE.format(key=key)
    _emit(on_status, f"读取表格清单：{doc['name']}")
    tabs = _list_tabs(page, base, doc["name"], on_status)

    sheets, skipped = [], []
    total = len(tabs)
    for index, tab in enumerate(tabs, start=1):
        if tab.get("type") != "smartsheet":
            skipped.append({"tab_id": tab.get("id"), "name": tab.get("name"),
                            "kind": tab.get("type")})
            continue
        _emit(on_status, f"读取表格 {tab.get('name')}（{index}/{total}）")
        sheet = _fetch_sheet(page, base, tab)
        sheet["doc_key"] = key
        sheets.append(sheet)
    resolve_links(sheets)
    return {"doc_key": key, "name": doc["name"], "sheets": sheets, "skipped": skipped}


def _fetch_sheet(page, base: str, tab: dict) -> dict:
    """分段拉完整张表：表可能超过单批上限，截断会导致本地误判「删除」。"""
    columns: list[dict] | None = None
    merged: dict[str, dict] = {}
    expected = None
    start = 0
    for _ in range(60):  # 60 批 × BATCH 行，足够兜住超大表
        url = f"{base}&tab={tab['id']}&startrow={start}&endrow={start + BATCH}"
        data = _fetch_json(page, url)
        cc = (data.get("clientVars") or {}).get("collab_client_vars") or {}
        chunk = (cc.get("initialAttributedText") or {}).get("text", [{}])[0]
        part = parse_chunk(chunk, tab)
        if columns is None:
            columns = part["columns"]
        for pos, row in enumerate(part["rows"]):
            row["row_no"] = start + pos + 1     # 分段后行号要接上
            merged[row["record_id"]] = row
        max_row = chunk.get("max_row")
        last = chunk.get("end_row_index")
        expected = int(max_row) if max_row is not None else None
        if not part["rows"] or last is None:
            break
        if expected is not None and int(last) + 1 >= expected:
            break
        start = int(last) + 1
    rows = sorted(merged.values(), key=lambda r: r["row_no"])
    return {"tab_id": tab["id"], "tab_name": tab.get("name") or tab["id"],
            "kind": tab.get("type"), "columns": columns or [], "rows": rows,
            "expected_rows": expected}


def collect_in_context(context, docs: list[dict], on_status=None,
                       on_doc=None) -> list[dict]:
    """在一个已有浏览器 context 里抓多个文档（复用会话，省一次浏览器启动）。"""
    results = []
    for doc in docs:
        page = None
        try:
            page = context.new_page()
            page.set_default_timeout(20_000)
            page.goto(doc["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            # 页面初始化（大文档要 8~10s）后再打接口
            page.wait_for_timeout(4000)
            item = collect_in_page(page, doc, on_status)
            item["ok"] = True
        except Exception as exc:  # noqa: BLE001 - 单文档失败不影响其它文档
            item = {"doc_key": doc.get("key"), "name": doc.get("name"),
                    "sheets": [], "skipped": [], "ok": False, "error": str(exc)}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        if on_doc:
            try:
                on_doc(item)
            except Exception:  # noqa: BLE001
                pass
        results.append(item)
    return results


def collect_docs(docs: list[dict], headless: bool = True, on_status=None,
                 state=None) -> list[dict]:
    """独立浏览器会话抓表格（公开文档不需要登录态）。"""
    from playwright.sync_api import sync_playwright

    from toolbox.browser import launch as pw_launch

    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档")
    with sync_playwright() as p:
        browser = pw_launch(p, headless=headless,
                            args=["--disable-blink-features=AutomationControlled",
                                  "--disable-notifications"])
        kwargs = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai",
                  "viewport": {"width": 1500, "height": 950}}
        if state and Path(state).exists():
            kwargs["storage_state"] = str(state)
        context = browser.new_context(**kwargs)
        # 登录 Cookie 来自 Cookie 管理器，覆盖快照里的旧票
        try:
            from . import session as _session
            _session.apply_to_context(context)
        except Exception:  # noqa: BLE001 - 公开文档不带票也能读
            pass
        try:
            return collect_in_context(context, docs, on_status)
        finally:
            try:
                context.close()
            finally:
                browser.close()


# ==================== 存储 ====================

def db_path() -> Path:
    return cfg_mod.tables_db_path()


def init_db(path=None) -> Path:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(_SCHEMA)
    return path


def get_rev(doc_key: str, path=None) -> str:
    """该文档上次成功同步的版本号（没有则空串）。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        row = conn.execute("SELECT rev FROM doc_meta WHERE doc_key=?",
                           (str(doc_key),)).fetchone()
    return str(row[0] or "") if row else ""


def set_rev(doc_key: str, rev: str, path=None) -> None:
    path = init_db(path)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO doc_meta (doc_key, rev, updated_at) "
                     "VALUES (?,?,?)", (str(doc_key), str(rev or ""), now))
        conn.commit()


def has_data(doc_key: str, path=None) -> bool:
    """本地是否已有该文档的表格数据（rev 跳过必须以此为前提）。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(
            "SELECT 1 FROM sheets WHERE doc_key=? AND skipped=0 LIMIT 1",
            (str(doc_key),)).fetchone() is not None


def _row_hash(values: dict) -> str:
    return hashlib.md5(json.dumps(values, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()


def row_summary(values: dict) -> str:
    for key in ("歌曲名称", "演出名称", "无料名", "标题", "奖项", "活动名称", "直播标题", "名称"):
        if values.get(key):
            return str(values[key])[:60]
    for value in values.values():
        if value:
            return str(value)[:60]
    return ""


def save_doc(result: dict, path=None) -> dict:
    """落库并返回增量统计 {tab_name: (rows, added, updated, removed)}。"""
    path = init_db(path)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    doc_key = result["doc_key"]
    stats = []
    with closing(sqlite3.connect(path)) as conn, conn:
        for sheet in result.get("sheets", []):
            tab_id, tab_name = sheet["tab_id"], sheet["tab_name"]
            old = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT record_id, row_hash, data_json FROM rows "
                "WHERE doc_key=? AND tab_id=?", (doc_key, tab_id))}
            added = updated = removed = pending = 0
            expected = sheet.get("expected_rows")
            complete = expected is None or len(sheet["rows"]) == int(expected)
            seen = set()
            for row in sheet["rows"]:
                rid, h = row["record_id"], _row_hash(row["values"])
                seen.add(rid)
                prev = old.get(rid)
                payload = json.dumps(row["values"], ensure_ascii=False)
                if prev is None:
                    added += 1
                    conn.execute(
                        "INSERT OR REPLACE INTO rows VALUES (?,?,?,?,?,?,?,?)",
                        (doc_key, tab_id, rid, row["row_no"], payload, h,
                         row.get("updated_ts"), now))
                    conn.execute(
                        "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,"
                        "summary,before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                        (now, doc_key, tab_id, tab_name, "新增", rid,
                         row_summary(row["values"]), "", payload))
                elif prev[0] != h:
                    updated += 1
                    changed = [k for k in row["values"]
                               if json.loads(prev[1]).get(k) != row["values"][k]]
                    conn.execute(
                        "UPDATE rows SET row_no=?, data_json=?, row_hash=?, updated_ts=?, "
                        "synced_at=? WHERE doc_key=? AND tab_id=? AND record_id=?",
                        (row["row_no"], payload, h, row.get("updated_ts"), now,
                         doc_key, tab_id, rid))
                    conn.execute(
                        "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,"
                        "summary,before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                        (now, doc_key, tab_id, tab_name, "修改", rid,
                         "、".join(changed[:4]) + ("…" if len(changed) > 4 else ""),
                         prev[1], payload))
                else:
                    conn.execute(
                        "UPDATE rows SET row_no=?, synced_at=? "
                        "WHERE doc_key=? AND tab_id=? AND record_id=?",
                        (row["row_no"], now, doc_key, tab_id, rid))
            for rid in old:
                if rid in seen:
                    continue
                if not complete:
                    # 这次没拉全（分段失败 / 接口截断），绝不删除本地数据
                    pending += 1
                    continue
                removed += 1
                before = old[rid][1]
                conn.execute("DELETE FROM rows WHERE doc_key=? AND tab_id=? AND record_id=?",
                             (doc_key, tab_id, rid))
                conn.execute(
                    "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,"
                    "summary,before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, doc_key, tab_id, tab_name, "删除", rid,
                     row_summary(json.loads(before)), before, ""))
            conn.execute(
                "INSERT OR REPLACE INTO sheets VALUES (?,?,?,?,?,?,?,?)",
                (doc_key, tab_id, tab_name, sheet.get("kind") or "smartsheet",
                 json.dumps(sheet["columns"], ensure_ascii=False),
                 len(sheet["rows"]), 0, now))
            stats.append({"tab_id": tab_id, "tab_name": tab_name,
                          "rows": len(sheet["rows"]), "cols": len(sheet["columns"]),
                          "added": added, "updated": updated, "removed": removed,
                          "pending": pending, "complete": complete})

        # 记录被跳过的非表格页（grid / dashboard / smartcanvas），界面上给原因
        for item in result.get("skipped", []):
            conn.execute(
                "INSERT OR REPLACE INTO sheets VALUES (?,?,?,?,?,?,?,?)",
                (doc_key, item["tab_id"], item.get("name") or item["tab_id"],
                 item.get("kind") or "", "[]", 0, 1, now))
        conn.commit()
    try:
        from . import images
    except Exception:  # noqa: BLE001 - 图片登记失败不影响表格数据落库
        return {"doc_key": doc_key, "name": result.get("name"), "tabs": stats}
    for sheet, stat in zip(result.get("sheets", []), stats):
        try:
            images.register_sheet(
                doc_key, sheet, path,
                prune=bool(stat.get("complete", True) and sheet.get("columns")))
        except Exception:  # noqa: BLE001 - 单表登记失败不影响其它表
            pass
    return {"doc_key": doc_key, "name": result.get("name"), "tabs": stats}


def sheets_of(doc_key: str, path=None) -> list[dict]:
    """该文档的全部工作表（含被跳过的），按界面顺序返回。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        rows = conn.execute(
            "SELECT tab_id, tab_name, kind, cols_json, n_rows, skipped, fetched_at "
            "FROM sheets WHERE doc_key=? ORDER BY rowid", (doc_key,)).fetchall()
    out = []
    for tab_id, name, kind, cols, n_rows, skipped, fetched in rows:
        try:
            columns = json.loads(cols or "[]")
        except json.JSONDecodeError:
            columns = []
        out.append({"tab_id": tab_id, "tab_name": name, "kind": kind,
                    "columns": columns, "n_rows": n_rows, "skipped": bool(skipped),
                    "fetched_at": fetched})
    return out


def rows_page(doc_key: str, tab_id: str, offset: int = 0, limit: int = 200, path=None):
    """分页取行，返回 (rows[{row_no, values}], 总数)。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM rows WHERE doc_key=? AND tab_id=?",
            (doc_key, tab_id)).fetchone()[0]
        data = conn.execute(
            "SELECT row_no, data_json FROM rows WHERE doc_key=? AND tab_id=? "
            "ORDER BY row_no LIMIT ? OFFSET ?", (doc_key, tab_id, limit, offset)).fetchall()
    rows = []
    for row_no, payload in data:
        try:
            values = json.loads(payload)
        except json.JSONDecodeError:
            values = {}
        rows.append({"row_no": row_no, "values": values})
    return rows, total


def recent_changes(doc_key: str, limit: int = 100, tab_id: str | None = None,
                   path=None) -> list[dict]:
    """最近的变更记录（新增 / 修改 / 删除）。"""
    path = init_db(path)
    sql = ("SELECT ts, tab_name, kind, record_id, summary FROM changes WHERE doc_key=?")
    args: list = [doc_key]
    if tab_id:
        sql += " AND tab_id=?"
        args.append(tab_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with closing(sqlite3.connect(path)) as conn, conn:
        rows = conn.execute(sql, args).fetchall()
    return [{"ts": r[0], "tab_name": r[1], "kind": r[2], "record_id": r[3], "summary": r[4]}
            for r in rows]


def stats(doc_key: str, path=None) -> dict:
    """该文档的表格概览：表数 / 行数 / 最近同步时间。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        total_sheets = conn.execute(
            "SELECT COUNT(*) FROM sheets WHERE doc_key=? AND skipped=0", (doc_key,)).fetchone()[0]
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM rows WHERE doc_key=?", (doc_key,)).fetchone()[0]
        last = conn.execute(
            "SELECT MAX(fetched_at) FROM sheets WHERE doc_key=?", (doc_key,)).fetchone()[0]
    return {"sheets": total_sheets, "rows": total_rows, "last": last or ""}


def clear_doc(doc_key: str, path=None) -> int:
    """删除该文档的全部表格数据与变更记录，返回删除的行数。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        n = conn.execute("SELECT COUNT(*) FROM rows WHERE doc_key=?", (doc_key,)).fetchone()[0]
        conn.execute("DELETE FROM rows WHERE doc_key=?", (doc_key,))
        conn.execute("DELETE FROM sheets WHERE doc_key=?", (doc_key,))
        conn.execute("DELETE FROM changes WHERE doc_key=?", (doc_key,))
        conn.execute("DELETE FROM doc_meta WHERE doc_key=?", (doc_key,))
        conn.commit()
    try:
        from . import images
        images.clear_doc(doc_key, path)   # 图片文件保留，只清登记避免再次误下载
    except Exception:  # noqa: BLE001 - 图片登记清理失败不影响表格数据清理
        pass
    return n


# ==================== 导出 ====================

def export_excel(doc_key: str, doc_name: str, path=None, target=None) -> Path:
    """把该文档全部工作表导出成一个 Excel（每表一个 sheet）。"""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill

    def safe(v):
        if not isinstance(v, str):
            return v
        return ILLEGAL_CHARACTERS_RE.sub("", v)[:32000]

    target = Path(target or cfg_mod.table_file({"key": doc_key, "name": doc_name}))
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True)
    used = set()
    for sheet in sheets_of(doc_key, path):
        if sheet["skipped"]:
            continue
        title = re.sub(r"[\\/*?:\[\]]", "_", sheet["tab_name"])[:31] or sheet["tab_id"][:31]
        while title in used:
            title = (title[:28] + "_x")[:31]
        used.add(title)
        ws = wb.create_sheet(title)
        names = [c["name"] for c in sheet["columns"]]
        ws.append([safe(n) for n in names])
        for cell in ws[1]:
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center")
        offset = 0
        while True:
            rows, total = rows_page(doc_key, sheet["tab_id"], offset, 500, path)
            for row in rows:
                ws.append([safe(row["values"].get(n, "")) for n in names])
            offset += len(rows)
            if not rows or offset >= total:
                break
        for col in ws.columns:
            width = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 42)
        ws.freeze_panes = "A2"
    if not wb.sheetnames:
        # 空 workbook 保存会抛 IndexError，这里补一个占位表说明原因
        ws = wb.create_sheet("(空)")
        ws.append(["该文档暂无本地表格数据，请先执行一次「同步表格数据」"])
    wb.save(target)
    return target
