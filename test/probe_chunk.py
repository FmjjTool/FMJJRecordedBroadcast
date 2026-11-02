# -*- coding: utf-8 -*-
"""看 initialAttributedText.text 的 chunk 结构（isChunked 时字段可能变形）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
TAB = sys.argv[2] if len(sys.argv) > 2 else None


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

        base = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
                "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
                f"&id={key}&normal=1&outformat=1&wb=1&nowb=0")
        st, txt = get(base)
        meta = json.loads(txt)
        cc = meta.get("clientVars", {}).get("collab_client_vars", {})
        tabs = [t for h in cc.get("header", []) for t in h.get("d", [])]
        print("tabs:", [(t.get("id"), t.get("type"), t.get("name")) for t in tabs])
        target = TAB or next((t["id"] for t in tabs if t.get("type") == "smartsheet"), None)
        print("target tab:", target)

        u = f"{base}&tab={target}&startrow=0&endrow=5000"
        st2, txt2 = get(u)
        data = json.loads(txt2)
        c2 = data.get("clientVars", {}).get("collab_client_vars", {})
        iat = c2.get("initialAttributedText") or {}
        print("status:", st2, "len:", len(txt2), "isChunked:", c2.get("isChunked"),
              "iat keys:", list(iat.keys()))
        texts = iat.get("text") or []
        print("text 数量:", len(texts))
        for i, chunk in enumerate(texts):
            print(f"--- chunk[{i}] keys:")
            for k, v in chunk.items():
                if isinstance(v, str):
                    print(f"    {k}: str len={len(v)} head={v[:60]!r}")
                else:
                    print(f"    {k}: {type(v).__name__} = {str(v)[:160]}")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump", f"chunk_{target}.json")
        open(out, "w", encoding="utf-8").write(txt2)
        print("已存:", out)
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
