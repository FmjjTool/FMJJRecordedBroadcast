# -*- coding: utf-8 -*-
"""逐 tab 试取：不带 viewId 能不能取到数据 + 各 tab 记录数。

用法：python test/probe_tabs.py <文档URL>
"""
import base64
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"


def strip_jsonp(t):
    s = t.strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


def decode(iat):
    blocks = []
    for chunk in (iat or {}).get("text") or []:
        blob = chunk.get("smartsheet")
        if not blob:
            continue
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
        blocks.append(json.loads(zlib.decompress(raw).decode("utf-8")))
    return blocks


def rec_count(blocks):
    for block in blocks:
        try:
            if isinstance(block, list) and block and isinstance(block[0], list):
                return len(block[0][1]["c"]["k2"]["k1"])
        except Exception:
            continue
    return None


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

        base = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
                "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
                f"&id={key}&normal=1&outformat=1&wb=1&nowb=0")
        st, txt = get(base)
        meta = json.loads(txt)
        cv = meta.get("clientVars", {})
        cc = cv.get("collab_client_vars", {})
        print(f"meta status={st} len={len(txt)} collab keys={list(cc.keys())[:12]}")
        print("smartsheetConfig:", str(cc.get("smartsheetConfig"))[:300])
        tabs = [t for h in cc.get("header", []) for t in h.get("d", [])]
        print(f"tabs={len(tabs)}")
        if not tabs:
            raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump", "meta_noluck.txt")
            open(raw_path, "w", encoding="utf-8").write(txt)
            print("tabs 为空，原始响应已存：", raw_path)
        for t in tabs:
            if t.get("type") != "smartsheet":
                print(f"  - {t.get('type'):<11} {t.get('id'):<10} {t.get('name')} (跳过)")
                continue
            u = f"{base}&tab={t['id']}&startrow=0&endrow=5000"
            st2, txt2 = get(u)
            data = json.loads(txt2)
            c2 = data.get("clientVars", {}).get("collab_client_vars", {})
            iat = c2.get("initialAttributedText") or {}
            head = {k: v for k, v in ((iat.get("text") or [{}])[0]).items() if k != "smartsheet"}
            blocks = decode(iat)
            print(f"  - {t['id']:<10} {t['name'][:14]:<16} status={st2} 响应={len(txt2)}B "
                  f"total={head.get('total_record_count')} 解出={rec_count(blocks)} "
                  f"max_row={head.get('max_row')} viewId={c2.get('viewId')}")
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
