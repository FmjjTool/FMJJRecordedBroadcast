# -*- coding: utf-8 -*-
"""解码 + 列/行结构检查。

用法：python test/inspect_tab.py <文档URL> [tabId]
"""
import base64
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
TAB = sys.argv[2] if len(sys.argv) > 2 else None

BASE = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
        "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
        "&id={key}&normal=1&outformat=1&wb=1&nowb=0")


def strip_jsonp(t):
    s = t.strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


def unblob(v):
    """腾讯的 value 有两种形态：内联 JSON 文本，或 base64+zlib。"""
    if not isinstance(v, str):
        return v
    s = v.lstrip()
    if s[:1] in "[{":
        return json.loads(s)
    raw = base64.b64decode(v + "=" * (-len(v) % 4))
    return json.loads(zlib.decompress(raw).decode("utf-8"))


def decode_blocks(iat):
    blocks = []
    for chunk in (iat or {}).get("text") or []:
        for key in ("smartsheet", "workbook"):
            blob = chunk.get(key)
            if not blob:
                continue
            try:
                data = unblob(blob)
            except Exception as exc:
                print(f"  !! {key} 解码失败: {exc}")
                continue
            blocks.append((key, data))
    return blocks


def parse_sheet(data):
    """返回 (columns[{id,name,type}], rows[{recordId: {fieldId: value}}])."""
    cols, rows = {}, {}
    root = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data
    for blk in root:
        if not isinstance(blk, dict):
            continue
        t, c = blk.get("t"), blk.get("c") or {}
        if t == 3005:  # 字段定义
            for holder in c.values():
                if not isinstance(holder, dict):
                    continue
                for fid, meta in holder.items():
                    if isinstance(meta, dict) and ("k30" in meta or "k31" in meta):
                        cols[fid] = {"id": fid, "name": meta.get("k30"), "type": meta.get("k31")}
        elif t == 3028:  # 记录
            for lvl in c.values():
                if not isinstance(lvl, dict):
                    continue
                for lvl2 in lvl.values():
                    if not isinstance(lvl2, dict):
                        continue
                    for lvl3 in lvl2.values():
                        if not isinstance(lvl3, dict):
                            continue
                        for rid, rec in lvl3.items():
                            if isinstance(rec, dict):
                                rows[rid] = rec
    return cols, rows


def cell_text(v):
    """把单元格原始值拍成人类可读文本。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        return " / ".join(str(cell_text(x)) for x in v if cell_text(x) != "")
    if isinstance(v, dict):
        for key in ("k1", "k2", "k9", "k4", "k25", "k26", "k27", "k28"):
            if key in v:
                return cell_text(v[key])
        return json.dumps(v, ensure_ascii=False)[:60]
    return str(v)


def main():
    key = URL.rstrip("/").split("/")[-1]
    state = os.path.join(ROOT, "config", "tcdoc_state.json")
    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch

    with sync_playwright() as p:
        browser = launch(p, headless=True)
        kw = {"locale": "zh-CN", "viewport": {"width": 1500, "height": 950}}
        if os.path.exists(state):
            kw["storage_state"] = state
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)

        def get(u):
            js = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
                  " return [r.status, await r.text()]; }")
            st, text = page.evaluate(js, u)
            return st, strip_jsonp(text)

        base = BASE.format(key=key)
        st, txt = get(base)
        meta = json.loads(txt)
        cc = meta.get("clientVars", {}).get("collab_client_vars", {})
        tabs = [t for h in cc.get("header", []) for t in h.get("d", [])]
        print("tabs:", [(t.get("id"), t.get("type"), t.get("name")) for t in tabs])
        target = TAB or next((t["id"] for t in tabs if t.get("type") == "smartsheet"), None)
        print("target:", target)

        st2, txt2 = get(f"{base}&tab={target}&startrow=0&endrow=5000")
        data = json.loads(txt2)
        c2 = data.get("clientVars", {}).get("collab_client_vars", {})
        iat = c2.get("initialAttributedText") or {}
        for key_, blk in decode_blocks(iat):
            print(f"block[{key_}] type={type(blk).__name__} len={len(blk)}")
            if key_ != "smartsheet":
                continue
            cols, rows = parse_sheet(blk)
            order = [c["name"] or c["id"] for c in cols.values()]
            print(f"列数={len(cols)} 行数={len(rows)}")
            print("列:", order)
            for i, (rid, rec) in enumerate(list(rows.items())[:3]):
                print(f"\n--- row[{i}] {rid}")
                for fid, meta in cols.items():
                    raw = rec.get(fid)
                    print(f"    {meta['name']:<12} type={meta['type']:<4} raw={str(raw)[:70]:<72} -> {cell_text(raw)!r}")
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
