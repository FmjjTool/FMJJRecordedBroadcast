# -*- coding: utf-8 -*-
"""腾讯文档智能表格 → 本地 SQLite 同步原型（实验版，不接入主程序）。

用法：
    python test/tcsync.py dump   <文档URL>        # 抓一次，落 test/_out/tables.db + Excel
    python test/tcsync.py diff   <文档URL>        # 再抓一次，打印与上次的差异
    python test/tcsync.py show   <文档URL>        # 只打印本次解析结果，不落库
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys
import time
import zlib
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "_out")

API_BASE = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
            "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
            "&id={key}&normal=1&outformat=1&wb=1&nowb=0")

FIELD_TYPES = {1: "文本", 2: "数字", 3: "复选框", 4: "日期", 5: "图片", 6: "附件",
               7: "电话", 8: "链接", 9: "单选", 10: "创建人", 11: "成员",
               12: "创建时间", 13: "最后编辑时间", 16: "邮箱", 17: "多选",
               18: "关联", 19: "公式", 21: "关联", 25: "自动编号"}
LINK_TAG = "\x00link:"       # 关联字段先落 recordId，等全部表解析完再换成可读文本
CST = timezone(timedelta(hours=8))


# ----------------------------------------------------------------------------- 解码

def strip_jsonp(text):
    s = text.strip()
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


def blocks_of(chunk):
    """把 chunk 里的 smartsheet 载荷解成 block 列表。"""
    blob = (chunk or {}).get("smartsheet")
    if not blob:
        return []
    data = unblob(blob)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data


def deep_unkey(node):
    """腾讯有两套键名：{"1":..} 与 {"k1":..}。统一成纯数字键。"""
    if isinstance(node, list):
        return [deep_unkey(x) for x in node]
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            if isinstance(key, str) and len(key) > 1 and key[0] == "k" and key[1:].isdigit():
                out[key[1:]] = deep_unkey(val)
            else:
                out[key] = deep_unkey(val)
        return out
    return node


# ----------------------------------------------------------------------------- 解析

def _get(node, *keys):
    """按任意层级的数字键取值，兼容 "1" / "k1" 两种写法。"""
    cur = node
    for k in keys:
        if not isinstance(cur, dict):
            return None
        if k in cur:
            cur = cur[k]
            continue
        alt = f"k{k}"
        if alt in cur:
            cur = cur[alt]
            continue
        return None
    return cur


def parse_meta_block(blk):
    """t=3005：字段定义 / 用户 / 视图。"""
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
        fields[fid] = {"id": fid, "name": meta.get("30") or fid,
                       "type": ftype, "typeName": FIELD_TYPES.get(ftype, str(ftype)),
                       "options": opts}
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
    """t=3028：记录本体 + 记录元信息（修改时间/修改人）。"""
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


def render_cell(cell, field, users):
    """把单元格拍成可读文本。cell 形如 {"30": type, "31": uid, "32": ts, "<type>": value}。"""
    if not isinstance(cell, dict):
        return ""
    ftype = field["type"]
    val = cell.get(str(ftype))
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
    if ftype == 3:  # 复选框
        return "✓" if val in (True, "true", 1, "1") else ""
    if ftype == 10:  # 创建人
        return users.get(val, str(val)) if val else ""
    if ftype == 25:  # 自动编号
        if isinstance(val, dict):
            return str(val.get("2") or val.get("1") or "")
        return str(val)
    if ftype == 2:  # 数字
        if isinstance(val, dict):
            return str(val.get("1", ""))
        return str(val)
    if ftype == 4:  # 日期：毫秒时间戳
        try:
            return datetime.fromtimestamp(int(val) / 1000, CST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return str(val)
    if ftype == 5:  # 图片
        return " ".join(item.get("3") or item.get("1") or "" for item in val if isinstance(item, dict))
    if ftype == 6:  # 附件
        return " ".join(item.get("1") or "" for item in val if isinstance(item, dict))
    if ftype == 8:  # 链接
        out = []
        for item in val if isinstance(val, list) else [val]:
            if isinstance(item, dict):
                out.append(item.get("3") or item.get("2") or "")
            elif item:
                out.append(str(item))
        return " ".join(x for x in out if x)
    if ftype in (9, 17):  # 单选 / 多选：id → 选项名
        ids = val if isinstance(val, list) else [val]
        names = [field["options"].get(i, i) for i in ids if i]
        return "、".join(names)
    if ftype in (12, 13):  # 创建时间 / 最后编辑时间
        try:
            return datetime.fromtimestamp(int(val) / 1000, CST).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(val)
    if ftype == 11:  # 成员
        ids = val if isinstance(val, list) else [val]
        return "、".join(users.get(i, i) for i in ids if i)
    if ftype in (18, 21):  # 关联其它表：先落 recordId，全部表解析完再翻译
        return "\x01".join(LINK_TAG + i for i in _ids(val))
    if ftype == 19:  # 公式：服务端不返回计算值
        return ""
    if isinstance(val, list):
        return "、".join(str(x) for x in val if isinstance(x, (str, int, float)))
    return str(val)


def parse_chunk(chunk, tab):
    blocks = blocks_of(chunk)
    fields, users, views = {}, {}, []
    records, rec_meta = {}, {}
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        kind = str(blk.get("t"))
        body = deep_unkey(blk.get("c"))
        if not isinstance(body, dict):
            continue
        blk = {"t": kind, "c": body}
        if kind == "3005":
            fields, users, views = parse_meta_block(blk)
        elif kind == "3028":
            records, rec_meta = parse_records_block(blk)

    order = next((v["fieldOrder"] for v in views if v["fieldOrder"]), []) or list(fields.keys())
    order = [fid for fid in order if fid in fields]
    for fid in fields:
        if fid not in order:
            order.append(fid)
    rec_order = next((v["recordOrder"] for v in views if v["recordOrder"]), []) or list(records.keys())
    rec_order = [rid for rid in rec_order if rid in records] + \
                [rid for rid in records if rid not in rec_order]

    columns = [dict(fields[fid], index=i) for i, fid in enumerate(order)]
    rows = []
    for pos, rid in enumerate(rec_order, start=1):
        cells = records[rid]
        values = {fields[fid]["name"]: render_cell(cells.get(fid), fields[fid], users)
                  for fid in order}
        rows.append({"record_id": rid, "row_no": pos, "values": values,
                     "updated_ts": (rec_meta.get(rid) or {}).get("updated_ts")})
    return {"tab_id": tab["id"], "tab_name": tab.get("name") or tab["id"],
            "kind": tab.get("type"), "columns": columns, "rows": rows,
            "max_row": (chunk or {}).get("max_row"),
            "max_col": (chunk or {}).get("max_col")}


# ----------------------------------------------------------------------------- 抓取

def doc_key_of(url):
    """从 URL 取文档 key：去掉 ?tab=xxx / #anchor 之类的尾巴。"""
    path = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return path.split("/")[-1]


def fetch_doc(url, state_path, on_status=None):
    """打开文档 → 枚举 tab → 逐个 smartsheet tab 取数。返回 (doc, [sheet...], skipped)."""
    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch

    key = doc_key_of(url)
    base = API_BASE.format(key=key)
    sheets, skipped = [], []

    js = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
          " return [r.status, await r.text()]; }")

    def say(msg):
        if on_status:
            on_status(msg)

    with sync_playwright() as p:
        browser = launch(p, headless=True)
        kw = {"locale": "zh-CN", "viewport": {"width": 1500, "height": 950}}
        if os.path.exists(state_path):
            kw["storage_state"] = state_path
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        say("打开文档…")
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(5000)

        def get(u):
            st, text = page.evaluate(js, u)
            if st != 200:
                raise RuntimeError(f"HTTP {st}: {text[:120]}")
            return json.loads(strip_jsonp(text))

        meta = get(base)
        cc = (meta.get("clientVars") or {}).get("collab_client_vars") or {}
        tabs = []
        for head in cc.get("header") or []:
            tabs.extend(head.get("d") or [])
        # 大文档（几百 MB）首次 meta 可能还没初始化完，header 为空，要重试几轮
        for _ in range(8):
            if tabs:
                break
            say("等待文档初始化…")
            page.wait_for_timeout(2500)
            meta = get(base)
            cc = (meta.get("clientVars") or {}).get("collab_client_vars") or {}
            tabs = []
            for head in cc.get("header") or []:
                tabs.extend(head.get("d") or [])
        if not tabs:
            raise RuntimeError("该文档没有返回任何工作表（可能已删除/无权限/是旧版文档）")
        doc_title = (meta.get("clientVars") or {}).get("title") or key
        say(f"发现 {len(tabs)} 个工作表")

        for idx, tab in enumerate(tabs, start=1):
            if tab.get("type") != "smartsheet":
                skipped.append({"tab_id": tab.get("id"), "name": tab.get("name"),
                                "kind": tab.get("type")})
                say(f"[{idx}/{len(tabs)}] {tab.get('name')} 跳过（{tab.get('type')} 普通表格暂不支持）")
                continue
            say(f"[{idx}/{len(tabs)}] 读取 {tab.get('name')}…")
            data = get(f"{base}&tab={tab['id']}&startrow=0&endrow=10000")
            chunk = ((data.get("clientVars") or {}).get("collab_client_vars") or {}) \
                .get("initialAttributedText", {}).get("text", [{}])[0]
            sheet = parse_chunk(chunk, tab)
            sheet["doc_key"] = key
            sheet["doc_title"] = doc_title
            sheets.append(sheet)
            say(f"      {tab.get('name')}：{len(sheet['columns'])} 列 / {len(sheet['rows'])} 行")

        ctx.storage_state(path=state_path)
        ctx.close()
        browser.close()
    resolve_links(sheets)
    return {"key": key, "title": doc_title, "url": url}, sheets, skipped


# ----------------------------------------------------------------------------- 存储

SCHEMA = """
CREATE TABLE IF NOT EXISTS sheets (
    doc_key TEXT, tab_id TEXT, tab_name TEXT, cols_json TEXT,
    n_rows INTEGER, fetched_at TEXT,
    PRIMARY KEY (doc_key, tab_id));
CREATE TABLE IF NOT EXISTS rows (
    doc_key TEXT, tab_id TEXT, record_id TEXT, row_no INTEGER,
    data_json TEXT, row_hash TEXT, updated_ts TEXT, synced_at TEXT,
    PRIMARY KEY (doc_key, tab_id, record_id));
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
    doc_key TEXT, tab_id TEXT, tab_name TEXT,
    kind TEXT, record_id TEXT, summary TEXT, before_json TEXT, after_json TEXT);
"""


def db_path():
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, "tables.db")


def row_hash(values):
    return hashlib.md5(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _ids(val):
    """关联字段的值可能是 id 字符串、id 列表，或带目标信息的小 dict。"""
    out = []
    for item in (val if isinstance(val, list) else [val]):
        if isinstance(item, dict):
            item = item.get("1") or item.get("2")
        if item:
            out.append(str(item))
    return out


def resolve_links(sheets):
    """把所有表解析完后，把 type 18/21 关联字段的 recordId 换成目标行的主字段文本。"""
    prefer = ("演出名称", "名称", "标题", "歌曲名称", "无料名", "奖项", "活动名称", "直播标题")
    index = {}
    for sheet in sheets:
        for row in sheet["rows"]:
            values = row["values"]
            text = next((values[n] for n in prefer
                         if n in values and values[n] and LINK_TAG not in str(values[n])), "")
            if not text:
                text = next((str(v) for v in values.values()
                             if v and LINK_TAG not in str(v)), "")
            index[row["record_id"]] = text or row["record_id"]
    for sheet in sheets:
        for row in sheet["rows"]:
            for name, value in list(row["values"].items()):
                if isinstance(value, str) and LINK_TAG in value:
                    names = [index.get(part[len(LINK_TAG):], part[len(LINK_TAG):])
                             for part in value.split("\x01") if part.startswith(LINK_TAG)]
                    row["values"][name] = "、".join(names)
    return sheets


def save(doc, sheets, db=None):
    """写入并返回每张表的差异统计。"""
    db = db or db_path()
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    report = []
    for sheet in sheets:
        key, tab_id, tab_name = doc["key"], sheet["tab_id"], sheet["tab_name"]
        old = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT record_id, row_hash, data_json FROM rows WHERE doc_key=? AND tab_id=?",
            (key, tab_id))}
        added = updated = removed = 0
        seen = set()
        for row in sheet["rows"]:
            rid, h = row["record_id"], row_hash(row["values"])
            seen.add(rid)
            prev = old.get(rid)
            if prev is None:
                added += 1
                conn.execute(
                    "INSERT OR REPLACE INTO rows VALUES (?,?,?,?,?,?,?,?)",
                    (key, tab_id, rid, row["row_no"], json.dumps(row["values"], ensure_ascii=False),
                     h, row["updated_ts"], now))
                conn.execute(
                    "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,summary,"
                    "before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, key, tab_id, tab_name, "新增", rid,
                     _summary(row["values"]), "", json.dumps(row["values"], ensure_ascii=False)))
            elif prev[0] != h:
                updated += 1
                conn.execute(
                    "UPDATE rows SET row_no=?, data_json=?, row_hash=?, updated_ts=?, synced_at=? "
                    "WHERE doc_key=? AND tab_id=? AND record_id=?",
                    (row["row_no"], json.dumps(row["values"], ensure_ascii=False), h,
                     row["updated_ts"], now, key, tab_id, rid))
                before = json.loads(prev[1])
                changed = [k for k in row["values"] if before.get(k) != row["values"][k]]
                conn.execute(
                    "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,summary,"
                    "before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, key, tab_id, tab_name, "修改", rid,
                     "、".join(changed[:4]) + ("…" if len(changed) > 4 else ""),
                     prev[1], json.dumps(row["values"], ensure_ascii=False)))
            else:
                conn.execute("UPDATE rows SET row_no=?, synced_at=? WHERE doc_key=? "
                             "AND tab_id=? AND record_id=?",
                             (row["row_no"], now, key, tab_id, rid))
        for rid in old:
            if rid not in seen:
                removed += 1
                before = old[rid][1]
                conn.execute("DELETE FROM rows WHERE doc_key=? AND tab_id=? AND record_id=?",
                             (key, tab_id, rid))
                conn.execute(
                    "INSERT INTO changes (ts,doc_key,tab_id,tab_name,kind,record_id,summary,"
                    "before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, key, tab_id, tab_name, "删除", rid,
                     _summary(json.loads(before)), before, ""))
        conn.execute(
            "INSERT OR REPLACE INTO sheets VALUES (?,?,?,?,?,?)",
            (key, tab_id, tab_name,
             json.dumps([{k: c[k] for k in ("id", "name", "type", "typeName")}
                         for c in sheet["columns"]], ensure_ascii=False),
             len(sheet["rows"]), now))
        report.append({"tab_id": tab_id, "tab_name": tab_name, "rows": len(sheet["rows"]),
                       "cols": len(sheet["columns"]), "added": added,
                       "updated": updated, "removed": removed})
    conn.commit()
    conn.close()
    return report


def _summary(values):
    for key in ("歌曲名称", "名称", "标题", "奖项", "活动名称", "直播标题"):
        if values.get(key):
            return str(values[key])[:60]
    for v in values.values():
        if v:
            return str(v)[:60]
    return ""


# ----------------------------------------------------------------------------- 导出 Excel

def export_excel(doc, sheets, path=None):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    path = path or os.path.join(OUT_DIR, f"{doc['title']}.xlsx")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True)

    def safe(v):
        """Excel 不接受控制字符，单格上限 32767 字符。"""
        if not isinstance(v, str):
            return v
        return ILLEGAL_CHARACTERS_RE.sub("", v)[:32000]

    for sheet in sheets:
        ws = wb.create_sheet(sheet["tab_name"][:31] or sheet["tab_id"][:31])
        names = [c["name"] for c in sheet["columns"]]
        ws.append([safe(n) for n in names])
        for cell in ws[1]:
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center")
        for row in sheet["rows"]:
            ws.append([safe(row["values"].get(n, "")) for n in names])
        for i, col in enumerate(ws.columns, start=1):
            width = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 42)
        ws.freeze_panes = "A2"
    wb.save(path)
    return path


# ----------------------------------------------------------------------------- CLI

def main(argv):
    action = argv[1] if len(argv) > 1 else "dump"
    urls = [a for a in argv[2:] if a.startswith("http")] or \
           ["https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"]
    state = os.path.join(ROOT, "config", "tcdoc_state.json")
    all_report = []
    for url in urls:
        t0 = time.time()
        print(f"\n>>> {url}")
        doc, sheets, skipped = fetch_doc(url, state, on_status=lambda m: print(f"  {m}", flush=True))
        print(f"\n文档：{doc['title']}  用时 {time.time() - t0:.1f}s")
        print(f"工作表 {len(sheets)} 张成功 / {len(skipped)} 张跳过")
        for s in sheets:
            fill = sum(1 for r in s["rows"] for v in r["values"].values() if v)
            print(f"  - {s['tab_name']:<14} {len(s['columns']):>3} 列 {len(s['rows']):>4} 行 "
                  f"非空单元格 {fill}")
        for s in skipped:
            print(f"  x {s['name']}（{s['kind']}）跳过")
        if action == "show":
            continue
        report = save(doc, sheets)
        print("\n增量对比：")
        for r in report:
            print(f"  {r['tab_name']:<14} {r['rows']:>4} 行  新增 {r['added']:<3} "
                  f"修改 {r['updated']:<3} 删除 {r['removed']}")
        xlsx = export_excel(doc, sheets)
        print(f"  Excel：{xlsx}")
        all_report.extend(report)
    if action != "show":
        print(f"\nSQLite：{db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
