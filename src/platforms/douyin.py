"""
抖音 (Douyin) 视频信息解析器
支持: 页面解析 / API 调用 / Cookie 认证 / Playwright
"""

from typing import Optional
import re
import json
from urllib.parse import unquote

import httpx
from .base import PlatformBase, VideoInfo, now_str
from toolbox.browser import launch as pw_launch


def _deep_find(d, key, depth=10):
    """递归查找嵌套 dict/list 中的 key（去重页面解析里重复的 deep_find）。"""
    if depth <= 0:
        return None
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _deep_find(v, key, depth - 1)
            if r is not None:
                return r
    elif isinstance(d, list):
        for item in d:
            r = _deep_find(item, key, depth - 1)
            if r is not None:
                return r
    return None


class DouyinParser(PlatformBase):
    name = "douyin"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/",
    }

    VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")
    NOTE_ID_PATTERN = re.compile(r"/note/(\d+)")
    MODAL_ID_PATTERN = re.compile(r"[?&]modal_id=(\d+)")
    USER_PATTERN = re.compile(r"douyin\.com/user/([a-zA-Z0-9_-]+)")


    def extract_id(self, url: str) -> str:
        for p in [self.VIDEO_ID_PATTERN, self.NOTE_ID_PATTERN, self.MODAL_ID_PATTERN]:
            m = p.search(url)
            if m: return m.group(1)
        return ""

    def extract_user_id(self, url: str) -> str:
        m = self.USER_PATTERN.search(url)
        return m.group(1) if m else ""

    def _normalize_url(self, url: str) -> str:
        if "/user/" in url:
            m = self.MODAL_ID_PATTERN.search(url)
            if m: return f"https://www.douyin.com/video/{m.group(1)}"
        return url

    def _resolve_short_url(self, url: str, client: httpx.Client) -> str:
        try:
            resp = client.get(url, follow_redirects=True)
            return str(resp.url)
        except Exception:
            return url

    # ========== API ==========

    def _try_api(self, video_id: str, cookie_str: str, client: httpx.Client) -> Optional[VideoInfo]:
        api_headers = dict(self.HEADERS)
        if cookie_str: api_headers["Cookie"] = cookie_str
        try:
            resp = client.get(f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}", headers=api_headers)
            data = resp.json()
            if data.get("status_code") == 0:
                items = data.get("item_list", [])
                if items: return self._build_from_aweme(items[0], video_id)
            else:
                print(f"[抖音] API 返回: {data.get('status_msg', '')}（可能需要登录 Cookie）")
        except Exception as e:
            print(f"[抖音] API 请求失败: {e}")
        return None

    def _build_from_aweme(self, item: dict, video_id: str) -> VideoInfo:
        author = item.get("author", {})
        stats = item.get("statistics", {})
        video = item.get("video", {})
        # 安全提取 cover_url
        cover_url = ""
        if isinstance(video, dict):
            cover = video.get("cover")
            if isinstance(cover, dict):
                ul = cover.get("url_list", [])
                if ul: cover_url = ul[0]
            if not cover_url:
                origin = video.get("origin_cover")
                if isinstance(origin, dict):
                    ol = origin.get("url_list", [])
                    if ol: cover_url = ol[0]
        duration = int(video.get("duration", 0) or 0) // 1000 if isinstance(video, dict) else 0
        ct = item.get("create_time", 0)
        pub = ""
        if ct:
            from datetime import datetime
            try: pub = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M")
            except: pub = str(ct)
        return VideoInfo(
            platform="douyin", video_id=video_id, title=item.get("desc", ""),
            author=author.get("nickname", ""), author_id=str(author.get("uid", "")),
            play_count=int(stats.get("play_count", 0) or 0),
            like_count=int(stats.get("digg_count", 0) or 0),
            comment_count=int(stats.get("comment_count", 0) or 0),
            share_count=int(stats.get("share_count", 0) or 0),
            favorite_count=int(stats.get("collect_count", 0) or 0),
            duration=duration, cover_url=cover_url,
            video_url=self._extract_video_dl_url(item),
            publish_time=pub, tags=[t.get("title", "") for t in item.get("text_extra", [])],
            raw_url=f"https://www.douyin.com/video/{video_id}", raw_data=item, fetched_at=now_str(),
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
                pa = bit_rate[0].get("play_addr", {})
                urls = pa.get("url_list", [])
                if urls:
                    return urls[0]
        return f"https://www.douyin.com/video/{item.get('aweme_id', '')}"

    # ========== 页面解析 ==========

    def _parse_from_page(self, url: str, client: httpx.Client) -> Optional[VideoInfo]:
        try:
            resp = client.get(url, follow_redirects=True); resp.raise_for_status()
            html = resp.text; final_url = str(resp.url)
            video_id = self.extract_id(final_url) or self.extract_id(url)
            if len(html) < 1000 or '<body></body>' in html[:500]:
                return None
            data = {}
            for pat in [r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>',
                         r'window\._ROUTER_DATA\s*=\s*(\{.*?\});',
                         r'window\.__NUXT__\s*=\s*(\{.*?\});']:
                m = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        raw = m.group(1).strip()
                        if 'RENDER_DATA' in pat:
                            raw = unquote(raw)
                        data = json.loads(raw)
                        if data: break
                    except: pass
            video_info = {}
            if isinstance(data, dict):
                vd = _deep_find(data, "aweme_detail") or _deep_find(data, "item_list") or {}
                if isinstance(vd, list) and vd: vd = vd[0]
                if isinstance(vd, dict): video_info = vd
            title = ""
            ogt = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
            if ogt: title = ogt.group(1)
            if not title: title = video_info.get("desc", "")
            author = ""
            am = re.search(r'"nickname"\s*:\s*"([^"]*)"', html)
            if am: author = am.group(1)
            stats = video_info.get("statistics", video_info)
            # publish_time
            pub = ""
            ct = video_info.get("create_time", 0)
            if ct:
                from datetime import datetime
                try: pub = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M")
                except: pub = str(ct)
            video_url_real = final_url
            if video_info and isinstance(video_info, dict) and video_info.get("video"):
                video_url_real = self._extract_video_dl_url(video_info)
            info = VideoInfo(
                platform="douyin", video_id=video_id, title=title or video_info.get("desc", ""),
                author=author or str(video_info.get("author", {}).get("nickname", "") if isinstance(video_info.get("author"), dict) else ""),
                author_id=str(video_info.get("author_user_id", "")),
                play_count=int(stats.get("play_count", 0) or 0),
                like_count=int(stats.get("digg_count", 0) or 0),
                comment_count=int(stats.get("comment_count", 0) or 0),
                share_count=int(stats.get("share_count", 0) or 0),
                favorite_count=int(stats.get("collect_count", 0) or 0),
                cover_url=video_info.get("video", {}).get("cover", {}).get("url_list", [""])[0] if isinstance(video_info.get("video"), dict) else "",
                video_url=video_url_real, raw_url=url, raw_data=video_info, fetched_at=now_str(),
                publish_time=pub,
            )
            if not info.title and not info.author:
                tr = re.search(r"<title>(.*?)</title>", html)
                if tr: info.title = tr.group(1).strip()
            if not video_info and "验证码" in (info.title or ""):
                print("[抖音] 页面命中验证码中间页，视为失败")
                return None
            return info
        except Exception as e:
            print(f"[抖音] 解析失败: {e}")
            return None

    # ========== 用户时间线（Playwright） ==========

    def fetch_user_posts(self, url_or_id: str, count: int = 10) -> list:
        """有 Cookie 直接 Cookie 态抓，失败回退游客态。

        抖音用户时间线无免登录 HTTP API，浏览器是直连手段。
        实测（2026-08-31）：同一主页游客 aweme/post 列表会漏掉最新若干作品
        （缺 08-29/30/31 三条），Cookie 态完整且为游客态超集——用户时间线
        属「必须用 Cookie 的接口」，与全链路游客优先原则的例外条款一致。
        """
        sec_uid = self.extract_user_id(url_or_id)
        if not sec_uid:
            print(f"[抖音] 无法提取用户ID: {url_or_id}")
            return []

        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            print("[抖音] 未安装 playwright")
            return []

        from auth import get_cookie
        cookie_str = get_cookie("douyin")

        if cookie_str:
            results = self._fetch_pw_once(sec_uid, count, cookie_str=cookie_str)
            if results:
                return results
            print("[抖音] Cookie 态未获取到数据（可能失效/触发风控），回退游客态...")

        # 无 Cookie 或 Cookie 态失败：游客浏览器兜底
        return self._fetch_pw_once(sec_uid, count, cookie_str="")

    def _fetch_pw_once(self, sec_uid: str, count: int, cookie_str: str = "") -> list:
        """单次 Playwright 抓取用户作品列表（cookie_str 为空即游客态）。"""
        from playwright.sync_api import sync_playwright

        results = []

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
            # 注入隐身脚本
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
                window.chrome = { runtime: {} };
            """)
            if cookie_str:
                for item in cookie_str.split("; "):
                    if "=" in item:
                        name, value = item.split("=", 1)
                        try:
                            context.add_cookies([{"name": name, "value": value, "domain": ".douyin.com", "path": "/"}])
                        except Exception:
                            pass

            page = context.new_page()
            collected = []

            def on_response(response):
                if response.status != 200:
                    return
                url = response.url
                # 匹配多种用户作品列表 API 端点
                if any(p in url for p in ["aweme/v1/web/aweme/post", "/aweme/post/", "user/profile"]):
                    try:
                        data = response.json()
                        aweme_list = data.get("aweme_list", [])
                        for a in aweme_list:
                            if a not in collected:
                                collected.append(a)
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                user_url = f"https://www.douyin.com/user/{sec_uid}"
                print(f"[抖音-Playwright] 加载: {user_url[:60]}...")
                # 抖音主页资源重、加载慢，20s 实测会超时（2026-09-18），放宽到 60s
                page.goto(user_url, wait_until="domcontentloaded", timeout=60000)

                # 页面内嵌数据（RENDER_DATA/SSR 首屏）合并进 collected（与 API 拦截去重）
                def _merge_html_items():
                    html = page.content()
                    for item in self._extract_aweme_list_from_html(html):
                        if item not in collected:
                            collected.append(item)

                _merge_html_items()
                if collected:
                    print(f"[抖音-Playwright] 首屏获取到 {len(collected)} 条")

                # 滚动触发 API 翻页加载，凑够 count 条即停
                for i in range(5):
                    if len(collected) >= count:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                    _merge_html_items()

                if not collected:
                    page.wait_for_timeout(5000)
                    _merge_html_items()

                for item in collected[:count]:
                    info = self._build_from_aweme(item, str(item.get("aweme_id", "")))
                    info.raw_url = user_url
                    results.append(info)

            except Exception as e:
                print(f"[抖音-Playwright] 页面加载失败: {e}")
            finally:
                browser.close()

        return results

    def _extract_aweme_list_from_html(self, html: str) -> list:
        """从页面 HTML 中提取 aweme_list（RENDER_DATA / SSR 数据）"""
        import json
        from urllib.parse import unquote
        result = []
        # 尝试 RENDER_DATA
        m = re.search(r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                raw = unquote(m.group(1).strip())
                data = json.loads(raw)
                aweme_list = _deep_find(data, "aweme_list", depth=12)
                if isinstance(aweme_list, list):
                    result = aweme_list
            except Exception:
                pass
        # 也尝试从 window.__INITIAL_STATE__ 或 __NUXT__ 提取（如果有）
        if not result:
            for pat in [r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
                         r'window\.__NUXT__\s*=\s*(\{.*?\});',
                         r'self\.__next_f\.push\(\[1,"(.*?)"\]\)']:
                m = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        raw = m.group(1).strip()
                        # 处理转义
                        raw = raw.replace('\\"', '"').replace('\\\\', '\\')
                        data = json.loads(raw)
                        aweme_list = _deep_find(data, "aweme_list", depth=12)
                        if isinstance(aweme_list, list):
                            result = aweme_list
                            break
                    except Exception:
                        pass
        return result

    # ========== 主入口 ==========

    def parse(self, url: str, client: Optional[httpx.Client] = None) -> Optional[VideoInfo]:
        """三级回退：① 游客直连（页面 + 游客 Playwright）→ ② 接入 Cookie 重试。

        Cookie 固定放第三级：游客方式拿不到数据才带 Cookie 走 _try_api + 页面解析。
        """
        # 一级：无 Cookie（页面解析 + 游客 Playwright 兜底）
        result = self._parse_once(url, client, cookie="")
        if result is None and client is None:
            from auth import get_cookie
            ck = get_cookie("douyin")
            if ck:
                print("[抖音] 游客解析失败，接入 Cookie 重试...")
                # Playwright 游客已在一级试过，Cookie 重试只走 API + 页面
                result = self._parse_once(url, None, cookie=ck, allow_playwright=False)
        return result

    def _parse_once(self, url: str, client: Optional[httpx.Client],
                    cookie: str = "", allow_playwright: bool = True) -> Optional[VideoInfo]:
        close_client = False
        if client is None:
            headers = dict(self.HEADERS)
            if cookie: headers["Cookie"] = cookie
            client = httpx.Client(headers=headers, timeout=20, follow_redirects=True)
            close_client = True
        try:
            # 图文 note：iteminfo 接口对图文返回 11110 风控，直接委托 multi_downloader
            # 的 aweme/detail + a_bogus 签名解析（能拿到 images 和完整元数据；
            # 其内部同样是游客优先、失败才接入 Cookie）
            if "/note/" in url:
                try:
                    from multi_downloader import parse_video
                    d = parse_video(url)
                    if d.get("title") or d.get("images"):
                        info = VideoInfo(
                            platform="douyin",
                            kind="video",
                            video_id=d.get("video_id") or self.extract_id(url),
                            title=d.get("title", ""),
                            description=d.get("title", ""),
                            author=d.get("author", ""),
                            author_id=d.get("author_id", ""),
                            play_count=d.get("play_count", 0),
                            like_count=d.get("like_count", 0),
                            comment_count=d.get("comment_count", 0),
                            cover_url=d.get("cover_url", ""),
                            video_url="",  # 图文无视频
                            publish_time=d.get("publish_time", ""),
                            raw_url=url,
                            fetched_at=now_str(),
                        )
                        info.images = d.get("images", [])
                        return info
                except Exception as e:
                    print(f"[抖音] note 图文解析失败: {e}")
                return None

            if "v.douyin.com" in url:
                url = self._resolve_short_url(url, client)
            url = self._normalize_url(url)
            video_id = self.extract_id(url)
            if not video_id:
                print(f"[抖音] 无法提取视频ID: {url}")
                return None
            cookie_str = getattr(client, 'headers', {}).get('Cookie', '')
            if cookie_str:
                result = self._try_api(video_id, cookie_str, client)
                if result: return result
            print("[抖音] 尝试页面解析...")
            result = self._parse_from_page(url, client)
            if result and (result.title or result.author):
                return result
            if not allow_playwright:
                return None
            print("[抖音] HTTP 方式未能获取有效数据，尝试 Playwright...")
            try:
                from .douyin_playwright import DouyinPlaywrightParser
                pw = DouyinPlaywrightParser()
                pw_result = pw.parse(url)
                if pw_result and (pw_result.title or pw_result.author):
                    print("[抖音] Playwright 获取成功")
                    return pw_result
            except ImportError:
                print("[抖音] Playwright 未安装")
            except Exception as e:
                print(f"[抖音] Playwright 失败: {e}")
            return None
        finally:
            if close_client:
                client.close()
