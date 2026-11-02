# -*- coding: utf-8 -*-
"""接口探测：打开腾讯文档表格/智能表格，抓下页面自己发起的请求，找出「表格数据」接口。

用法（项目根目录）：
    python test/probe_api.py            # 默认探测智能表格「百科用户反馈与收集」
    python test/probe_api.py <文档URL>   # 指定任意腾讯文档

产物：
    test/_dump/catalog.txt    所有候选请求/响应清单（按响应体大小排序）
    test/_dump/NNN_*.json     体积较大的 JSON 响应体（便于看结构）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

DEFAULT_URL = "https://docs.qq.com/smartsheet/DRndLZVRmVW1GZExW"
DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump")

# 可能承载表格正文的接口关键字（命中即记录完整响应体）
INTEREST = re.compile(
    r"(smartsheet|sheet|records?|get_?data|get_?sheet|table|rows?|cells?|"
    r"cgi-bin|dop-api|online_docs|opendoc|doc_data|export)", re.I)


def _slug(url: str) -> str:
    path = re.sub(r"[^A-Za-z0-9]+", "_", url.split("?")[0])
    return path[-70:].strip("_")


def main(argv):
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    os.makedirs(DUMP, exist_ok=True)
    state = os.path.join(ROOT, "config", "tcdoc_state.json")

    from playwright.sync_api import sync_playwright
    from toolbox.browser import launch

    rows = []      # dict 列表
    saved = 0

    with sync_playwright() as p:
        browser = launch(p, headless=True,
                         args=["--disable-blink-features=AutomationControlled"])
        ctx_kwargs = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai",
                      "viewport": {"width": 1500, "height": 950}}
        if os.path.exists(state):
            ctx_kwargs["storage_state"] = state
        ctx = browser.new_context(**ctx_kwargs)

        req_info = {}

        def on_request(req):
            if INTEREST.search(req.url):
                req_info[req.url] = (req.method, (req.post_data or "")[:600])

        def on_response(resp):
            nonlocal saved
            if not INTEREST.search(resp.url):
                return
            ctype = resp.headers.get("content-type", "")
            method, post = req_info.get(resp.url, ("", ""))
            item = {"url": resp.url, "status": resp.status, "ctype": ctype.split(";")[0],
                    "size": 0, "method": method, "post": post, "file": ""}
            if "json" in ctype or "text" in ctype:
                try:
                    body = resp.text()
                except Exception:
                    body = ""
                item["size"] = len(body)
                if body and len(body) > 300 and "json" in ctype:
                    saved += 1
                    name = f"{saved:03d}_{_slug(resp.url)[:60]}.json"
                    try:
                        with open(os.path.join(DUMP, name), "w", encoding="utf-8") as f:
                            f.write(body[:2_000_000])
                        item["file"] = name
                    except OSError:
                        pass
            rows.append(item)

        page = ctx.new_page()
        page.on("request", on_request)
        page.on("response", on_response)
        print(f"打开：{url}")
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        for _ in range(24):
            page.wait_for_timeout(500)
        for _ in range(4):          # 滚一遍触发懒加载分页
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
        print("标题：", page.title())
        ctx.storage_state(path=state)
        ctx.close()
        browser.close()

    rows.sort(key=lambda r: -(r["size"] or 0))
    lines = ["大小\t状态\t方法\t落盘\tURL"]
    for r in rows:
        lines.append(f"{r['size']}\t{r['status']}\t{r['method']}\t{r['file']}\t{r['url'][:220]}")
    with open(os.path.join(DUMP, "catalog.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n\n==== 带请求体的接口 ====\n")
        for r in rows:
            if r["post"]:
                f.write(f"[{r['method']}] {r['url']}\n  {r['post']}\n\n")

    print(f"\n捕获 {len(rows)} 条候选请求，落盘 {saved} 个 JSON")
    print("清单：", os.path.join(DUMP, "catalog.txt"))
    print("\n==== 体积 Top 15 ====")
    for line in lines[1:16]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
