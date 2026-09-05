"""
抖音 Playwright 解析器（可选）
需要: pip install playwright && playwright install chromium
"""

import re, json
from typing import Optional
from .base import PlatformBase, VideoInfo, now_str
from toolbox.browser import launch as pw_launch


class DouyinPlaywrightParser(PlatformBase):
    name = "douyin_playwright"


    def extract_id(self, url: str) -> str:
        for p in [r"/video/(\d+)", r"[?&]modal_id=(\d+)"]:
            m = re.search(p, url)
            if m: return m.group(1)
        return ""

    def parse(self, url: str, client=None) -> Optional[VideoInfo]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[抖音-Playwright] 需要: pip install playwright && playwright install chromium")
            return None

        video_id = self.extract_id(url) or "unknown"

        with sync_playwright() as p:
            browser = pw_launch(
                p, headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
                window.chrome = { runtime: {} };
            """)
            page = context.new_page()
            api_data = {}

            def handle_response(response):
                if response.status != 200: return
                try:
                    data = response.json()
                    if "aweme_detail" in data:
                        api_data["detail"] = data["aweme_detail"]
                except: pass

            page.on("response", handle_response)

            try:
                video_url = f"https://www.douyin.com/video/{video_id}" if video_id != "unknown" else url
                page.goto(video_url, wait_until="domcontentloaded", timeout=15000)

                # 轮询等待 API（用 page.wait_for_timeout 不阻塞事件循环）
                for _ in range(10):
                    page.wait_for_timeout(1500)
                    aweme = api_data.get("detail")
                    if isinstance(aweme, dict) and aweme.get("desc"):
                        return self._build_from_aweme(aweme, video_id, url)

                # 备选: RENDER_DATA
                html = page.content()
                render_match = re.search(r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.DOTALL)
                if render_match:
                    from urllib.parse import unquote
                    try:
                        raw = unquote(render_match.group(1).strip())
                        data = json.loads(raw)
                        def deep_find(d, key, depth=10):
                            if depth <= 0: return None
                            if isinstance(d, dict):
                                if key in d: return d[key]
                                for v in d.values():
                                    r = deep_find(v, key, depth-1)
                                    if r is not None: return r
                            elif isinstance(d, list):
                                for item in d:
                                    r = deep_find(item, key, depth-1)
                                    if r is not None: return r
                            return None
                        aweme = deep_find(data, "aweme_detail")
                        if isinstance(aweme, dict) and aweme.get("desc"):
                            return self._build_from_aweme(aweme, video_id, url)
                    except: pass

                print("[抖音-Playwright] 未能提取到视频数据")
                return None

            except Exception as e:
                print(f"[抖音-Playwright] 页面加载失败: {e}")
                return None
            finally:
                browser.close()

    def _build_from_aweme(self, aweme: dict, video_id: str, url: str) -> VideoInfo:
        author_info = aweme.get("author", {})
        stats = aweme.get("statistics", {})
        video_info = aweme.get("video", {})

        cover_url = ""
        cover = video_info.get("cover", {})
        if isinstance(cover, dict):
            ul = cover.get("url_list", [])
            if ul: cover_url = ul[0]
        if not cover_url:
            origin = video_info.get("origin_cover", {})
            if isinstance(origin, dict):
                ul = origin.get("url_list", [])
                if ul: cover_url = ul[0]

        duration = int(video_info.get("duration", 0) or 0) // 1000

        publish_time = ""
        ct = aweme.get("create_time", 0)
        if ct:
            from datetime import datetime
            try: publish_time = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M")
            except: publish_time = str(ct)

        music = ""
        m = aweme.get("music", {})
        if isinstance(m, dict) and m.get("title"):
            music = m["title"]
            if m.get("author"): music += f" - {m['author']}"

        return VideoInfo(
            platform="douyin", video_id=video_id,
            title=aweme.get("desc", ""), description="",
            author=author_info.get("nickname", ""),
            author_id=str(author_info.get("uid", "")),
            play_count=int(stats.get("play_count", 0) or 0),
            like_count=int(stats.get("digg_count", 0) or 0),
            comment_count=int(stats.get("comment_count", 0) or 0),
            share_count=int(stats.get("share_count", 0) or 0),
            favorite_count=int(stats.get("collect_count", 0) or 0),
            duration=duration, cover_url=cover_url,
            video_url=self._extract_video_dl_url(aweme),
            publish_time=publish_time,
            tags=[t.get("tag_name", "") for t in aweme.get("text_extra", [])],
            music=music,
            raw_url=url, raw_data=aweme, fetched_at=now_str(),
        )

    def _extract_video_dl_url(self, item: dict) -> str:
        """提取真实视频下载 URL（无水印优先）"""
        video = item.get("video", {})
        if isinstance(video, dict):
            for key in ("download_addr", "play_addr_h264", "play_addr"):
                addr = video.get(key, {})
                if isinstance(addr, dict):
                    urls = addr.get("url_list", [])
                    if urls:
                        return urls[0]
            bit_rate = video.get("bit_rate", [])
            if bit_rate and isinstance(bit_rate[0], dict):
                urls = bit_rate[0].get("play_addr", {}).get("url_list", [])
                if urls:
                    return urls[0]
        return f"https://www.douyin.com/video/{item.get('aweme_id', '')}"
