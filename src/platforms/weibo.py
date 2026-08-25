"""
微博 (Weibo) 视频信息解析器
优先使用 m.weibo.cn JSON API，失败时回退到页面解析
"""

from typing import Optional
import re
import json
import httpx
from .base import PlatformBase, VideoInfo, now_str, normalize_datetime


class WeiboParser(PlatformBase):
    name = "weibo"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://m.weibo.cn/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": "",
    }

    WEIBO_MID_PATTERN = re.compile(r"weibo\.com/(\d+)/([a-zA-Z0-9]+)")
    WEIBO_CN_PATTERN = re.compile(r"weibo\.cn/([a-zA-Z0-9]+)")
    WEIBO_DETAIL_PATTERN = re.compile(r"m\.weibo\.cn/detail/([a-zA-Z0-9]+)")
    WEIBO_STATUS_PATTERN = re.compile(r"m\.weibo\.cn/status/([a-zA-Z0-9]+)")
    STATUS_API = "https://m.weibo.cn/api/statuses/show"
    LONGTEXT_API = "https://m.weibo.cn/statuses/extend"


    @staticmethod
    def _extract_livephoto_url(raw: str) -> str:
        """从 live 图视频源解析出真实 .mov 直链。

        m.weibo.cn 的 pics[].videoSrc 是播放器页 URL，形如：
          https://video.weibo.com/media/play?livephoto=<urlencoded>.mov
        真正的视频在 livephoto 参数里；若已是直链则原样返回。
        """
        if not raw:
            return ""
        raw = raw.strip()
        if "livephoto=" in raw:
            from urllib.parse import urlparse, parse_qs, unquote
            q = parse_qs(urlparse(raw).query)
            lp = q.get("livephoto", [])
            if lp:
                return unquote(lp[0])
        return raw


    def extract_id(self, url: str) -> str:
        for p in [self.WEIBO_DETAIL_PATTERN, self.WEIBO_STATUS_PATTERN]:
            m = p.search(url); 
            if m: return m.group(1)
        m = self.WEIBO_CN_PATTERN.search(url)
        if m: return m.group(1)
        m = self.WEIBO_MID_PATTERN.search(url)
        if m: return m.group(2)
        return ""

    def _parse_from_api(self, weibo_id: str, original_url: str, client: httpx.Client) -> Optional[VideoInfo]:
        try:
            resp = client.get(f"{self.STATUS_API}?id={weibo_id}", follow_redirects=True); resp.raise_for_status()
            if "passport" in str(resp.url): return None
            data = resp.json()
            if "id" not in data or "user" not in data: return None
            return self._build_info(data, weibo_id, original_url, client)
        except Exception as e:
            print(f"[微博] API失败: {e}")
            return None

    def _parse_from_mobile(self, url: str, client: httpx.Client) -> Optional[VideoInfo]:
        try:
            weibo_id = self.extract_id(url)
            resp = client.get(f"https://m.weibo.cn/detail/{weibo_id}" if weibo_id else url, follow_redirects=True)
            html = resp.text
            if "Sina Visitor System" in html: return None
            # 复用括号计数提取 $render_data（兼容 var $render_data / window.$render_data）
            mblogs = self._extract_mblogs_from_html(html)
            if mblogs:
                return self._build_info(mblogs[0], weibo_id, url, client)
            return None
        except Exception as e:
            print(f"[微博] 页面失败: {e}")
            return None

    def _fill_long_text(self, status: dict, client: httpx.Client) -> None:
        """长微博展开：时间轴/详情接口的 mblog 常只有截断 text（尾部"...全文"），
        isLongText=true 时需单独请求 extend 接口取全文，填回 longText 字段。"""
        mid = str(status.get("mid") or status.get("id") or "")
        if not mid:
            return
        try:
            resp = client.get(f"{self.LONGTEXT_API}?id={mid}")
            data = resp.json()
            if data.get("ok") == 1:
                lt = (data.get("data") or {}).get("longTextContent", "")
                if lt:
                    status["longText"] = {"longTextContent": lt}
        except Exception as e:
            print(f"[微博] 长文展开失败({mid}): {e}")

    @staticmethod
    def _clean_rich(text: str) -> str:
        """HTML 正文清洗：<br> 保留换行，其余标签去除。"""
        if not text:
            return ""
        t = re.sub(r"<br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
        return re.sub(r"<[^>]+>", "", t).strip()

    def _build_info(self, status: dict, weibo_id: str, original_url: str,
                    client: Optional[httpx.Client] = None) -> Optional[VideoInfo]:
        # 长微博：接口未附带 longText 时现场请求 extend 接口展开全文
        if status.get("isLongText") and not status.get("longText"):
            if client is not None:
                self._fill_long_text(status, client)
            else:
                tmp = httpx.Client(headers=self.HEADERS, timeout=15, follow_redirects=True)
                try:
                    self._fill_long_text(status, tmp)
                finally:
                    tmp.close()
        text = status.get("text", "") or status.get("raw_text", "")
        clean_text = self._clean_rich(text)
        if status.get("isLongText") and status.get("longText"):
            lt = status["longText"].get("longTextContent", "")
            if lt: clean_text = self._clean_rich(lt)

        title = status.get("status_title") or (clean_text.split("\n")[0].strip() if "\n" in clean_text else clean_text)
        description = clean_text

        # 发布设备：从 annotations.client_mblogid 提取
        device = ""
        for ann in (status.get("annotations") or []):
            cid = ann.get("client_mblogid", "")
            if cid and "-" in cid:
                device = cid.split("-")[0]
                break
        # 备选：source 字段（如果不是话题链接）
        if not device:
            raw_source = re.sub(r'<[^>]+>', '', status.get("source", ""))
            if raw_source and "超话" not in raw_source and len(raw_source) < 20:
                device = raw_source
        if device:
            description = description + f"\n\n—— {device}"

        user_info = status.get("user", {}) or {}
        author = user_info.get("screen_name", "") or user_info.get("name", "")
        author_id = str(user_info.get("id", ""))
        author_face = user_info.get("avatar_hd", "") or user_info.get("profile_image_url", "")
        attitudes = int(status.get("attitudes_count", 0))
        comments = int(status.get("comments_count", 0))
        reposts = int(status.get("reposts_count", 0))
        favorites = int(status.get("favorites_count", 0) or 0)
        publish_time = normalize_datetime(status.get("created_at", ""))

        video_url = ""; cover_url = ""; images = []; live_photos = []
        page_info = status.get("page_info", {}) or {}
        if isinstance(page_info, dict):
            mi = page_info.get("media_info", {}) or {}
            if isinstance(mi, dict):
                video_url = (mi.get("stream_url_hd", "")
                             or mi.get("stream_url", "")
                             or mi.get("mp4_720p_mp4", "")
                             or mi.get("mp4_hd_mp4", "")
                             or mi.get("mp4_sd_mp4", "")
                             or mi.get("mp4_url", "")
                             or mi.get("h5_url", ""))
                cover_url = mi.get("pic_url", "") or page_info.get("page_pic", {}).get("url", "")
            # 兜底：page_info.urls（dict: 清晰度 -> mp4 直链）
            if not video_url:
                urls = page_info.get("urls", {})
                if isinstance(urls, dict) and urls:
                    video_url = (urls.get("mp4_720p_mp4", "")
                                 or urls.get("mp4_hd_mp4", "")
                                 or urls.get("mp4_sd_mp4", "")
                                 or next(iter(urls.values()), ""))
            if not video_url: video_url = page_info.get("url", "")

        # 提取所有图片 URL（优先 original 原图 → large → middle → url）
        pics = status.get("pics", [])
        pic_infos = status.get("pic_infos", {}) or {}
        for pic in pics:
            pid = pic.get("pid", "")
            # live photo 检测
            ptype = pic.get("type", "")
            if ptype == "livephoto" or (pid and pic_infos.get(pid, {}).get("type") == "livephoto"):
                live_video = ""
                if pid and pic_infos.get(pid):
                    live_video = pic_infos[pid].get("video", "")
                if not live_video:
                    # m.weibo.cn 的 pics 里字段是 videoSrc（播放器页 URL），非 video
                    live_video = pic.get("video", "") or pic.get("videoSrc", "")
                if live_video:
                    live_video = self._extract_livephoto_url(live_video)
                    if live_video:
                        live_photos.append(live_video)
            # 普通图片 — original 优先
            img_url = ""
            if pid and pic_infos.get(pid):
                img_url = pic_infos[pid].get("original", {}).get("url", "")
            if not img_url:
                img_url = (pic.get("largest", {}).get("url", "")
                           or pic.get("large", {}).get("url", ""))
            if not img_url:
                img_url = pic.get("url", "")
            if not img_url:
                img_url = pic.get("middle", {}).get("url", "")
            if img_url:
                images.append(img_url)
        # 如果 pics 为空，尝试从 pic_infos 提取
        if not images and pic_infos:
            for pid, pdata in pic_infos.items():
                ptype = pdata.get("type", "")
                if ptype == "livephoto":
                    lv = pdata.get("video", "") or pdata.get("videoSrc", "")
                    lv = self._extract_livephoto_url(lv)
                    if lv: live_photos.append(lv)
                img_url = (pdata.get("original", {}).get("url", "")
                           or pdata.get("large", {}).get("url", "")
                           or pdata.get("middle", {}).get("url", ""))
                if img_url: images.append(img_url)

        if not cover_url and images:
            cover_url = images[0]
        if not cover_url: cover_url = status.get("original_pic", "")
        tags = list(set(re.findall(r'#([^#]+)#', text)))
        play_count = int(page_info.get("play_count", 0)) if isinstance(page_info, dict) else 0
        duration = int(page_info.get("duration", 0) or 0) if isinstance(page_info, dict) else 0
        region = (status.get("region_name") or "").replace("发布于 ", "")
        info = VideoInfo(
            platform="weibo", video_id=weibo_id or str(status.get("id","")),
            title=title, description=description,
            author=author, author_id=author_id, author_face=author_face,
            play_count=play_count, like_count=attitudes,
            comment_count=comments, share_count=reposts,
            favorite_count=favorites, duration=duration,
            cover_url=cover_url, video_url=video_url or f"https://m.weibo.cn/detail/{weibo_id}",
            publish_time=publish_time, tags=tags, category=region,
            raw_url=original_url, raw_data=status, fetched_at=now_str(),
            images=images, live_photos=live_photos,
        )
        return info if info.title or info.author else None

    # ========== 主入口 ==========

    def parse(self, url: str, client: Optional[httpx.Client] = None) -> Optional[VideoInfo]:
        """三级回退：① 游客直连 → ② 接入 Cookie 重试（Cookie 固定放第三级）。"""
        # 一级：无 Cookie 直连
        result = self._parse_once(url, client, cookie="")
        if result is None and client is None:
            from auth import get_cookie
            ck = get_cookie("weibo")
            if ck:
                print("[微博] 游客解析失败，接入 Cookie 重试...")
                result = self._parse_once(url, None, cookie=ck)
        return result

    def _parse_once(self, url: str, client: Optional[httpx.Client], cookie: str = "") -> Optional[VideoInfo]:
        close_client = False
        if client is None:
            headers = dict(self.HEADERS)
            if cookie: headers["Cookie"] = cookie
            client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
            close_client = True
        try:
            weibo_id = self.extract_id(url)
            if not weibo_id: return None
            api_result = self._parse_from_api(weibo_id, url, client)
            # bid 短链（如 weibo.com/uid/RbICzlIaB）时 statuses/show 返回的数据常缺 pics，
            # 此时回退到详情页 render_data（数据最全，含 pics/pic_infos）
            if api_result and (api_result.images or api_result.live_photos):
                return api_result
            mobile_result = self._parse_from_mobile(url, client)
            return mobile_result or api_result
        finally:
            if close_client: client.close()

    # ========== 用户时间线 ==========

    USER_UID_PATTERN = re.compile(r"weibo\.com/(?:u/)?(\d{5,15})")
    USER_API = "https://m.weibo.cn/api/container/getIndex"

    def extract_user_id(self, url: str) -> str:
        m = self.USER_UID_PATTERN.search(url)
        return m.group(1) if m else ""

    def fetch_user_posts(self, url_or_id: str, count: int = 10) -> list:
        """三级回退：① 访客通行证 API → ② HTTP 页面 → ③ 接入用户 Cookie → ④ Playwright 兜底。

        Cookie 是账号凭据，能不用就不用（降低风控/封号风险）；
        游客方式实在连不上才接入，Cookie 固定放第三级。
        """
        uid = self.extract_user_id(url_or_id)
        if not uid: return []
        from auth import get_cookie

        # 层级 1: 访客通行证 API（自动获取，无需登录）
        vck = self._get_visitor_cookie()
        if vck:
            visitor_headers = dict(self.HEADERS)
            visitor_headers["Cookie"] = vck
            api_results = self._try_api_fetch(uid, count, visitor_headers)
            if api_results:
                print(f"[微博-访客通行证] API 获取到 {len(api_results)} 条")
                return api_results

        # 层级 2: HTTP 页面解析（游客）
        print("[微博] 尝试 HTTP 页面解析...")
        page_headers = {"Cookie": vck} if vck else {}
        page_results = self._fetch_http_page(uid, count, page_headers)
        if page_results:
            return page_results

        # 层级 3: 接入用户 Cookie（API + 页面各试一次）
        ck = get_cookie("weibo")
        if ck:
            print("[微博] 游客方式未获取到数据，接入 Cookie 重试...")
            cookie_headers = dict(self.HEADERS)
            cookie_headers["Cookie"] = ck
            api_results = self._try_api_fetch(uid, count, cookie_headers)
            if api_results:
                return api_results
            page_results = self._fetch_http_page(uid, count, cookie_headers)
            if page_results:
                return page_results

        # 层级 4: Playwright 浏览器（最后兜底）
        print("[微博] 尝试 Playwright...")
        return self._fetch_pw(uid, count)

    def _try_api_fetch(self, uid: str, count: int, headers: dict) -> list:
        """使用指定 headers 尝试 API 分页获取"""
        results = []
        remaining = count; page = 1
        client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
        try:
            while remaining > 0:
                params = {"type":"uid","value":uid,"containerid":f"107603{uid}","page":page}
                resp = client.get(self.USER_API, params=params)
                if resp.status_code != 200 or not resp.text.strip():
                    break
                data = resp.json()
                if data.get("ok") != 1:
                    break
                cards = data.get("data",{}).get("cards",[])
                if not cards: break
                taken = 0
                for card in cards:
                    if taken >= remaining: break
                    mblog = card.get("mblog")
                    if not mblog: continue
                    info = self._build_info(mblog, mblog.get("mid",""), f"https://weibo.com/{uid}", client)
                    if info: results.append(info); taken += 1
                remaining -= taken; page += 1
                if taken == 0: break
        except Exception:
            pass
        finally:
            client.close()
        return results

    def _get_visitor_cookie(self) -> str:
        """自动获取微博访客通行证（SUB/SUBP），无需用户登录"""
        try:
            resp = httpx.get(
                "https://visitor.passport.weibo.cn/visitor/genvisitor2",
                params={"cb": "visitor_callback", "from": "weibo", "tid": "",
                        "return_url": "https://m.weibo.cn/"},
                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"},
                timeout=10, follow_redirects=True,
            )
            text = resp.text
            m = re.search(r'visitor_callback\((.*)\)', text, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                sub = data.get("data", {}).get("sub", "")
                subp = data.get("data", {}).get("subp", "")
                if sub:
                    print("[微博] 访客通行证已获取")
                    return f"SUB={sub}; SUBP={subp}"
        except Exception as e:
            print(f"[微博] 访客通行证获取失败: {e}")
        return ""

    def _fetch_http_page(self, uid: str, count: int = 10, extra_headers: dict = None) -> list:
        """通过 HTTP 直接抓取 H5 页面提取帖子（无需浏览器）"""
        results = []
        h5_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://m.weibo.cn/",
        }
        if extra_headers:
            if extra_headers.get("Cookie"):
                h5_headers["Cookie"] = extra_headers["Cookie"]
        try:
            resp = httpx.get(
                f"https://m.weibo.cn/u/{uid}?launchid=10000360-page_H5&luicode=10000011&lfid=102803",
                headers=h5_headers, timeout=15, follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
            if "Sina Visitor System" in html:
                print("[微博-HTTP] 遇到验证页")
                return results
            mblogs = self._extract_mblogs_from_html(html)
            if mblogs:
                # 复用带 Cookie 的会话做长文展开（extend 接口）
                ext_client = httpx.Client(headers=h5_headers, timeout=15, follow_redirects=True)
                try:
                    for mblog in mblogs[:count]:
                        info = self._build_info(mblog, mblog.get("mid",""), f"https://weibo.com/{uid}", ext_client)
                        if info: results.append(info)
                finally:
                    ext_client.close()
            if results:
                print(f"[微博-HTTP] 页面解析获取到 {len(results)} 条")
        except Exception as e:
            print(f"[微博-HTTP] 页面请求失败: {e}")
        return results

    def _fetch_pw(self, uid: str, count: int = 10) -> list:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[微博-Playwright] 未安装 playwright")
            return []
        from auth import get_cookie
        ck = get_cookie("weibo")
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                locale="zh-CN",
            )
            # 注入隐身脚本：隐藏 webdriver 标记
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
                window.chrome = { runtime: {} };
            """)
            if ck:
                for domain in (".m.weibo.cn", ".weibo.cn", ".weibo.com"):
                    for item in ck.split("; "):
                        if "=" in item:
                            n, v = item.split("=", 1)
                            try: ctx.add_cookies([{"name":n,"value":v,"domain":domain,"path":"/"}])
                            except: pass
            page = ctx.new_page()
            collected = []
            api_urls_seen = []
            # 长文展开会话（extend 接口，游客可用；有 Cookie 时带上）
            ext_headers = dict(self.HEADERS)
            ext_headers["Cookie"] = ck or ""
            ext_client = httpx.Client(headers=ext_headers, timeout=15, follow_redirects=True)

            def on_resp(r):
                if r.status != 200:
                    return
                url = r.url
                if "container/getIndex" in url:
                    api_urls_seen.append(url)
                    try:
                        d = r.json()
                        if d.get("ok") == 1:
                            for c in d.get("data",{}).get("cards",[]):
                                if c.get("mblog"): collected.append(c["mblog"])
                        else:
                            print(f"[微博-Playwright] API 返回 ok={d.get('ok')}, msg={d.get('msg','')}")
                    except Exception as e:
                        print(f"[微博-Playwright] API 响应解析失败: {e}")
                if "statuses/" in url and "container" not in url:
                    api_urls_seen.append(url)

            page.on("response", on_resp)

            try:
                print("[微博-Playwright] 启动隐身浏览器...")
                page.goto(f"https://m.weibo.cn/u/{uid}?launchid=10000360-page_H5&luicode=10000011&lfid=102803", wait_until="domcontentloaded", timeout=30000)

                # 等待 "Sina Visitor System" 验证通过（如果有）
                title = page.title()
                if "Sina Visitor" in title or "Visitor System" in title:
                    print("[微博-Playwright] 检测到验证页面，等待通过...")
                    try:
                        page.wait_for_function(
                            "document.title.indexOf('Sina Visitor') === -1",
                            timeout=15000,
                        )
                        print("[微博-Playwright] 验证已通过")
                    except Exception:
                        print("[微博-Playwright] 验证等待超时，尝试继续...")

                current_url = page.url
                if "passport" in current_url or "login" in current_url:
                    print("[微博-Playwright] 页面跳转到登录页（Cookie 可能已过期）")

                # 等待页面完全加载
                page.wait_for_timeout(3000)

                # 尝试从页面 HTML 提取内嵌数据
                html = page.content()
                posts_from_html = self._extract_mblogs_from_html(html)
                if posts_from_html:
                    print(f"[微博-Playwright] 从页面内嵌数据提取到 {len(posts_from_html)} 条")
                    for mblog in posts_from_html[:count]:
                        info = self._build_info(mblog, mblog.get("mid",""), f"https://weibo.com/{uid}", ext_client)
                        if info: results.append(info)
                    if results:
                        browser.close()
                        return results

                # 滚动触发 API
                for i in range(8):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                    if len(collected) >= count:
                        break

                if not collected:
                    page.wait_for_timeout(3000)
                    # 最后再检查一次页面数据
                    html = page.content()
                    more = self._extract_mblogs_from_html(html)
                    for mblog in more:
                        if mblog not in collected:
                            collected.append({"mblog": mblog})

                for item in collected:
                    if isinstance(item, dict) and item.get("id"):
                        # 直接是 mblog 对象
                        mblog = item
                    elif isinstance(item, dict) and item.get("mblog"):
                        mblog = item["mblog"]
                    else:
                        continue
                    if len(results) >= count:
                        break
                    info = self._build_info(mblog, mblog.get("mid",""), f"https://weibo.com/{uid}", ext_client)
                    if info: results.append(info)

                if not results:
                    if api_urls_seen:
                        print(f"[微博-Playwright] 拦截到 API 但无数据，URL 示例: {api_urls_seen[0][:80]}...")
                    else:
                        print(f"[微博-Playwright] 未拦截到 API 请求（页面标题: {page.title()}）")
            except Exception as e:
                print(f"[微博-Playwright] 失败: {e}")
            finally:
                ext_client.close()
                browser.close()
        return results

    def _extract_mblogs_from_html(self, html: str) -> list:
        """从页面 HTML 中提取 mblog 数据（SSR 内嵌）"""
        import json
        result = []
        # 尝试 $render_data（使用括号计数防止嵌套截断）
        # 兼容 var $render_data 与 window.$render_data 两种写法
        m = re.search(r'(?:window\.|var\s+)?\$render_data\s*=\s*\[', html)
        if m:
            start = m.end() - 1  # 指向 [
            depth = 0; end = start
            for i in range(start, len(html)):
                if html[i] == '[': depth += 1
                elif html[i] == ']':
                    depth -= 1
                    if depth == 0: end = i + 1; break
            try:
                data = json.loads(html[start:end]) if end > start else []
                if isinstance(data, list):
                    status = data[0].get("status", data[0])
                    if status.get("id"):
                        result.append(status)
                # 可能是多条
                for item in data:
                    if isinstance(item, dict):
                        s = item.get("status", item)
                        if s.get("id") and s not in result:
                            result.append(s)
            except Exception:
                pass
        if not result:
            # 尝试 __INITIAL_STATE__
            init_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
            if init_match:
                try:
                    start = init_match.end()
                    if start < len(html) and html[start] == '{':
                        depth = 0; end = start
                        for i in range(start, len(html)):
                            if html[i] == '{': depth += 1
                            elif html[i] == '}':
                                depth -= 1
                                if depth == 0: end = i + 1; break
                        data = json.loads(html[start:end]) if end > start else {}
                        # 深度查找 cards / statuses
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
                        cards = deep_find(data, "cards")
                        if isinstance(cards, list):
                            for card in cards:
                                mblog = card.get("mblog")
                                if mblog: result.append(mblog)
                except Exception:
                    pass
        return result
