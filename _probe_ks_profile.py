"""探测快手新版个人主页 DOM/接口里可用的粉丝数据来源。"""
import json
import re
import sys

sys.path.insert(0, "src")

from playwright.sync_api import sync_playwright

sys.path.insert(0, "src")
from toolbox.browser import launch as pw_launch

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

url = "https://www.kuaishou.com/profile/3x2ranvzkq6g6wy"

with sync_playwright() as p:
    browser = pw_launch(p, headless=True,
                        args=["--disable-blink-features=AutomationControlled",
                              "--no-sandbox", "--disable-dev-shm-usage"])
    try:
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                                  locale="zh-CN")
        page = ctx.new_page()
        captured = []

        def on_response(resp):
            try:
                u = resp.url
                if any(k in u for k in ("graphql", "profile/feed", "user")):
                    try:
                        j = resp.json()
                    except Exception:
                        return
                    captured.append((u, j))
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print("goto err:", e)
        page.wait_for_timeout(6000)

        print("=== title:", page.title())
        # 抓页面文本里的粉丝/关注计数区域
        body = page.inner_text("body")
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        print("=== body lines (first 40):")
        for ln in lines[:40]:
            print(repr(ln))
        print("\n=== captured api urls:")
        for u, j in captured:
            print("URL:", u[:120])
            s = json.dumps(j, ensure_ascii=False)
            for kw in ("fan", "fanCount", "userProfile", "ownerCount", "follow"):
                if kw in s:
                    print("  contains:", kw)
            print("  head:", s[:400])
        html = page.content()
        open("_probe_ks_profile.html", "w", encoding="utf-8").write(html)
        print("\nhtml saved, len:", len(html))
        # 常见计数标记
        for pat in ("粉丝", "关注", "获赞", "作品"):
            for m in re.finditer(pat, html):
                s = html[max(0, m.start() - 200):m.start() + 120]
                print(f"\n--- {pat} @ {m.start()} ---")
                print(s.replace("\n", " ")[:320])
                break
    finally:
        browser.close()
