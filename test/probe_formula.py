# -*- coding: utf-8 -*-
"""公式列（type 19）能不能拿到计算值？试几组请求参数。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tcsync  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
TAB = sys.argv[2] if len(sys.argv) > 2 else "ss_ktwk0t"

VARIANTS = {
    "原始": "",
    "calcByFrontend=0": "&calcByFrontend=0",
    "getFormulaResult=1": "&getFormulaResult=1",
    "formulaResult=1": "&formulaResult=1",
    "withFormulaResult=1": "&withFormulaResult=1",
    "type=7": "&type=7",
    "optimizedVer=2": "&optimizedVer=2&supportOptimizedVer=2",
}


def main():
    key = URL.rstrip("/").split("/")[-1]
    base = tcsync.API_BASE.format(key=key)
    state = os.path.join(ROOT, "config", "tcdoc_state.json")
    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch

    js = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
          " return [r.status, await r.text()]; }")
    with sync_playwright() as p:
        browser = launch(p, headless=True)
        kw = {"locale": "zh-CN", "viewport": {"width": 1500, "height": 950}}
        if os.path.exists(state):
            kw["storage_state"] = state
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(5000)

        for name, extra in VARIANTS.items():
            u = f"{base}&tab={TAB}&startrow=0&endrow=100{extra}"
            st, text = page.evaluate(js, u)
            if st != 200:
                print(f"{name:<22} HTTP {st}")
                continue
            try:
                data = json.loads(tcsync.strip_jsonp(text))
            except Exception as exc:
                print(f"{name:<22} 解析失败 {exc}")
                continue
            cv = data.get("clientVars") or {}
            cc = cv.get("collab_client_vars") or {}
            chunk = (cc.get("initialAttributedText") or {}).get("text", [{}])[0]
            tab = {"id": TAB, "name": "t", "type": "smartsheet"}
            sheet = tcsync.parse_chunk(chunk, tab)
            formulas = [c for c in sheet["columns"] if c["type"] == 19]
            filled = {c["name"]: sum(1 for r in sheet["rows"] if r["values"].get(c["name"]))
                      for c in formulas}
            print(f"{name:<22} 响应={len(text):>8}B 记录={len(sheet['rows']):<4} "
                  f"formulaResults={str(cv.get('formulaResults'))[:20]:<22} 公式列非空={filled}")

        ctx.storage_state(path=state)
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
