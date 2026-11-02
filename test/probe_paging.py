# -*- coding: utf-8 -*-
"""验证：分页参数能否一次/多次取全，各 tab 的 total_record_count。

用法：python test/probe_paging.py
"""
import base64
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

DOCS = [
    ("smartsheet", "https://docs.qq.com/smartsheet/DRndLZVRmVW1GZExW", "百科用户反馈与收集"),
    ("smartsheet", "https://docs.qq.com/smartsheet/DRkZIbkZtQ0TaTHd4", "「封茗囧菌」无料周边汇总"),
    ("sheet", "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr", "封茗囧菌数据汇总库"),
]


def strip_jsonp(text):
    s = text.strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


def decode_chunks(payload):
    """initialAttributedText.text[] → 解码后的 JSON 列表。"""
    out = []
    for chunk in payload.get("text") or []:
        blob = chunk.get("smartsheet")
        if not blob:
            continue
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
        try:
            out.append(json.loads(zlib.decompress(raw).decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            out.append({"__error__": str(exc)})
    return out


def main():
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

        for kind, url, name in DOCS:
            print("=" * 70)
            print(f"{name}  [{kind}]")
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(6000)
            key = url.rstrip("/").split("/")[-1]

            def get(u):
                js = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
                      " return [r.status, await r.text()]; }")
                st, text = page.evaluate(js, u)
                return st, strip_jsonp(text)

            base = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
                    "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
                    f"&id={key}&normal=1&outformat=1&wb=1&nowb=0")
            st, txt = get(base)
            meta = json.loads(txt)
            cv = meta.get("clientVars", {})
            tabs = [t for h in cv.get("collab_client_vars", {}).get("header", [])
                    for t in h.get("d", [])]
            view = cv.get("viewId", "")
            print(f"  meta status={st} tabs={len(tabs)} viewId={view}")
            for t in tabs:
                print(f"    - {t.get('type'):<11} {t.get('id'):<10} {t.get('name')}"
                      f"{' (隐藏)' if t.get('hidden') else ''}")
            if kind != "smartsheet":
                print("  （普通表格：数据格式待单独确认）")
                continue

            sheet = next((t for t in tabs if t.get("type") == "smartsheet"), None)
            if not sheet:
                continue
            for end in (60, 5000):
                u = f"{base}&tab={sheet['id']}&viewId={view}&startrow=0&endrow={end}"
                st, txt = get(u)
                data = json.loads(txt)
                iat = (data.get("clientVars", {}).get("collab_client_vars", {})
                       .get("initialAttributedText") or {})
                chunks = iat.get("text") or []
                head = {k: v for k, v in (chunks[0] if chunks else {}).items()
                        if k != "smartsheet"}
                decoded = decode_chunks(iat)
                rec_n = None
                for d in decoded:
                    if isinstance(d, list) and len(d) > 1 and isinstance(d[0], list):
                        pass
                print(f"  [{0},{end}) status={st} 响应={len(txt)}B chunks={len(chunks)}"
                      f" 头部={ {k: head.get(k) for k in ('max_row','max_col','start_row_index','end_row_index','total_record_count')} }")
                # 数一下记录条数
                for block in decoded:
                    try:
                        recs = block[0][1]["c"]["k2"]["k1"]
                        rec_n = len(recs)
                    except Exception:
                        pass
                print(f"     解码后 record 数={rec_n}")
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
