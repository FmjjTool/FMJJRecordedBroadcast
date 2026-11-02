# -*- coding: utf-8 -*-
"""探 grid 普通表格 tab，以及 meta 返回 0 tab 的文档。
用法：python test/probe_grid.py [URL] [tabId]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
TAB = sys.argv[2] if len(sys.argv) > 2 else "33mt1g"

BASE = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
        "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
        "&id={key}&normal=1&outformat=1&wb=1&nowb=0")


def strip_jsonp(t):
    s = t.strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


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
        for tab in (TAB, None):
            u = base if tab is None else f"{base}&tab={tab}&startrow=0&endrow=200"
            st, txt = get(u)
            label = tab or "META"
            print(f"\n===== {label} status={st} len={len(txt)}")
            if st != 200 or not txt.strip().startswith("{"):
                print("   非 JSON 响应:", txt[:200])
                continue
            data = json.loads(txt)
            cv = data.get("clientVars") or {}
            cc = cv.get("collab_client_vars") or {}
            print("   cv keys:", list(cv.keys()))
            print("   cc keys:", list(cc.keys()))
            iat = cc.get("initialAttributedText") or {}
            print("   iat keys:", list(iat.keys()))
            texts = iat.get("text") or []
            print("   text chunk 数:", len(texts))
            for i, ch in enumerate(texts):
                info = {}
                for k, v in ch.items():
                    info[k] = (f"str({len(v)})" if isinstance(v, str) else
                               f"{type(v).__name__}({len(v) if hasattr(v, '__len__') else v})")
                print(f"   chunk[{i}]: {info}")
            if texts:
                out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump",
                                   f"grid_{label}.json")
                open(out, "w", encoding="utf-8").write(txt)
                print("   已存:", out)
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
