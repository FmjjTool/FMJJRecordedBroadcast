# -*- coding: utf-8 -*-
"""抓一份 dop-api/opendoc 原始响应，看结构（供写同步器用）。

用法：
    python test/dump_opendoc.py                       # 默认智能表格
    python test/dump_opendoc.py <文档URL> [startrow] [endrow]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump")

DEFAULT_URL = "https://docs.qq.com/smartsheet/DRndLZVRmVW1GZExW"


def _strip_jsonp(text: str) -> str:
    """opendoc 返回 JSONP（cl({...})），剥掉回调包装。"""
    s = text.strip()
    if s.startswith("{") or s.startswith("["):
        return s
    i, j = s.find("("), s.rfind(")")
    if i != -1 and j > i:
        return s[i + 1:j]
    return s


def main(argv):
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    start = int(argv[2]) if len(argv) > 2 else 0
    end = int(argv[3]) if len(argv) > 3 else 60
    key = url.rstrip("/").split("/")[-1]
    state = os.path.join(ROOT, "config", "tcdoc_state.json")
    os.makedirs(DUMP, exist_ok=True)

    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch

    with sync_playwright() as p:
        browser = launch(p, headless=True)
        kwargs = {"locale": "zh-CN", "viewport": {"width": 1500, "height": 950}}
        if os.path.exists(state):
            kwargs["storage_state"] = state
        ctx = browser.new_context(**kwargs)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)

        api = (f"https://docs.qq.com/dop-api/opendoc?u=&noEscape=1&enableSmartsheetSplit=1"
               f"&supportOptimizedVer=4&chunkCellSize=15000&enableChunkRank=1"
               f"&startrow={start}&endrow={end}&id={key}&normal=1&outformat=1&wb=1&nowb=0")
        js = """async (u) => {
            const r = await fetch(u, {credentials: 'include'});
            const t = await r.text();
            return [r.status, r.headers.get('content-type') || '', t];
        }"""
        status, ctype, text = page.evaluate(js, api)
        print("status:", status, "| ctype:", ctype, "| 长度:", len(text))
        body = _strip_jsonp(text)
        with open(os.path.join(DUMP, "opendoc_raw.txt"), "w", encoding="utf-8") as f:
            f.write(body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            print("不是 JSON：", exc)
            print(text[:800])
            ctx.close()
            browser.close()
            return 1

        print("\n顶层键：", list(data.keys()))
        cv = data.get("clientVars") or {}
        print("clientVars 键：", list(cv.keys())[:40])

        def peek(path, node, depth=0):
            if depth > 3:
                return
            if isinstance(node, dict):
                for k, v in list(node.items())[:60]:
                    kind = type(v).__name__
                    extra = ""
                    if isinstance(v, (list, str)):
                        extra = f" len={len(v)}"
                    elif isinstance(v, (int, float, bool)) or v is None:
                        extra = f" = {v}"
                    print("  " * depth + f"{path}.{k} <{kind}>{extra}")
                    if isinstance(v, dict) and depth < 2:
                        peek(path + "." + k, v, depth + 1)
            elif isinstance(node, list) and node:
                print("  " * depth + f"{path}[0] <{type(node[0]).__name__}>")
                if isinstance(node[0], dict):
                    peek(path + "[0]", node[0], depth + 1)

        peek("clientVars", cv)
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
