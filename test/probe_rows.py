# -*- coding: utf-8 -*-
"""探测单个 tab 的数据响应结构（同步器写解析前先看清楚）。

用法：python test/probe_rows.py [文档URL] [startrow] [endrow]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump")
URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/smartsheet/DRndLZVRmVW1GZExW"
START = int(sys.argv[2]) if len(sys.argv) > 2 else 0
END = int(sys.argv[3]) if len(sys.argv) > 3 else 200


def strip_jsonp(text):
    s = text.strip()
    if s[:1] in "{[":
        return s
    i, j = s.find("("), s.rfind(")")
    return s[i + 1:j] if i != -1 and j > i else s


def main():
    key = URL.rstrip("/").split("/")[-1]
    state = os.path.join(ROOT, "config", "tcdoc_state.json")
    os.makedirs(DUMP, exist_ok=True)
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
            js = """async (u) => { const r = await fetch(u, {credentials:'include'});
                                 return [r.status, await r.text()]; }"""
            st, text = page.evaluate(js, u)
            return st, strip_jsonp(text)

        base = ("https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
                "&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
                f"&id={key}&normal=1&outformat=1&wb=1&nowb=0")

        st, meta_text = get(base)
        meta = json.loads(meta_text)
        cv = meta.get("clientVars", {})
        tabs = []
        for h in cv.get("collab_client_vars", {}).get("header", []):
            for t in h.get("d", []):
                tabs.append(t)
        view_id = cv.get("viewId", "")
        print(f"status={st} 共 {len(tabs)} 个 tab，默认 viewId={view_id}")
        for t in tabs:
            print(f"  · {t.get('type'):<12} {t.get('id'):<12} {t.get('name')}"
                  f"{'  (隐藏)' if t.get('hidden') else ''}")

        sheets = [t for t in tabs if t.get("type") == "smartsheet" and not t.get("hidden")]
        if not sheets:
            print("没有可同步的智能表格 tab")
            ctx.close()
            browser.close()
            return 1
        tab = sheets[0]
        url = f"{base}&tab={tab['id']}&viewId={view_id}&startrow={START}&endrow={END}"
        st, text = get(url)
        print(f"\n取 tab={tab['id']} {tab['name']} [{START},{END}) → status={st} 长度={len(text)}")
        with open(os.path.join(DUMP, f"rows_{tab['id']}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(text[:800])
            return 1

        def walk(node, path="", depth=0, out=None):
            out = out if out is not None else []
            if depth > 2:
                return out
            if isinstance(node, dict):
                for k, v in node.items():
                    kind = type(v).__name__
                    size = len(v) if isinstance(v, (list, dict, str)) else v
                    out.append((f"{path}.{k}", kind, size))
                    if isinstance(v, (dict, list)):
                        walk(v, f"{path}.{k}", depth + 1, out)
            elif isinstance(node, list) and node:
                out.append((f"{path}[0]", type(node[0]).__name__, len(node)))
                walk(node[0], f"{path}[0]", depth + 1, out)
            return out

        print("\n== 结构（前 60 条）==")
        for row in walk(data)[:60]:
            print("  ", row)
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
