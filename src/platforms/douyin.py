"""
抖音 (Douyin) 视频信息解析器
支持: 页面解析 / API 调用 / Cookie 认证 / Playwright
"""

from typing import Optional
import atexit
import queue
import re
import json
import threading
import time
from concurrent.futures import Future
from urllib.parse import unquote

import httpx
from .base import PlatformBase, VideoInfo, now_str
from toolbox.browser import launch as pw_launch

# aweme/post 直调被 Argus 风控拦死后进入冷却，避免每个账号都白打请求；
# 浏览器兜底仍可取到登录态下的完整列表。
_POST_HTTP_COOLDOWN = 600.0
_post_http_blocked_until = 0.0
_post_http_blocked_lock = threading.Lock()


def _post_http_blocked_for() -> float:
    """HTTP 直调冷却剩余秒数（0 = 可用）。"""
    with _post_http_blocked_lock:
        return max(0.0, _post_http_blocked_until - time.time())


def _mark_post_http_blocked() -> None:
    global _post_http_blocked_until
    with _post_http_blocked_lock:
        _post_http_blocked_until = time.time() + _POST_HTTP_COOLDOWN


def _classify_post_response(status_code: int, text: str) -> str:
    """aweme/post 响应分类：ok / blocked / non_json / transient。

    抖音对非浏览器客户端常回 403「Blocked by ArgusSecurityPlugin」，
    或回验证页 HTML；两者都应停止直调而不是反复 JSON 解析。
    5xx 只算临时故障，不进入风控冷却。
    """
    body = (text or "").strip()
    if status_code >= 500:
        return "transient"
    if status_code == 403 or "ArgusSecurityPlugin" in body:
        return "blocked"
    if not body:
        return "blocked"
    if not body.startswith(("{", "[")):
        return "non_json"
    return "ok"


# ---------- Playwright 浏览器会话池 ----------
# 复用浏览器与登录/游客上下文，避免每个账号重复启动；sync API 不允许跨线程，
# 因此每个池线程独占一个 Playwright 实例，调用线程只等待 Future。
_PW_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
_PW_ARGS = ["--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage"]
_PW_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
    window.chrome = { runtime: {} };
"""
_PW_IDLE_SECONDS = 120.0
_PW_MAX_WORKERS = 2

_log_local = threading.local()


def _log(msg: str) -> None:
    """浏览器线程内先写本地 sink，由调用线程回放，保持账号日志前缀。"""
    sink = getattr(_log_local, "sink", None)
    if sink is None:
        print(msg)
    else:
        sink.append(msg)


class _DouyinPwPool:
    """上限固定的浏览器会话池；每个线程独占 browser + 登录/游客 context。"""

    def __init__(self, max_workers: int = _PW_MAX_WORKERS,
                 idle_seconds: float = _PW_IDLE_SECONDS):
        self._max = max(1, int(max_workers))
        self._idle = float(idle_seconds)
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._closed = False

    def submit(self, fn) -> Future:
        fut: Future = Future()
        with self._lock:
            if self._closed:
                fut.set_exception(RuntimeError("浏览器会话已关闭"))
                return fut
            self._queue.put((fut, fn))
            self._ensure_worker_locked()
        return fut

    def _ensure_worker_locked(self) -> None:
        self._workers = [t for t in self._workers if t.is_alive()]
        if len(self._workers) >= self._max:
            return
        if self._queue.qsize() > len(self._workers):
            t = threading.Thread(target=self._run, name="douyin-pw", daemon=True)
            t.start()
            self._workers.append(t)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = pw_launch(p, headless=True, args=list(_PW_ARGS))
                contexts = {}
                try:
                    while True:
                        try:
                            fut, fn = self._queue.get(timeout=self._idle)
                        except queue.Empty:
                            with self._lock:
                                if not self._queue.empty():
                                    continue
                                self._workers = [
                                    t for t in self._workers
                                    if t is not threading.current_thread()]
                                return
                        if fut is None:
                            return
                        if not fut.set_running_or_notify_cancel():
                            continue
                        sink = []
                        _log_local.sink = sink
                        try:
                            result = fn(browser, contexts)
                        except BaseException as exc:  # noqa: BLE001
                            fut.set_exception(exc)
                            try:
                                if not browser.is_connected():
                                    break
                            except Exception:
                                break
                        else:
                            fut.set_result((result, sink))
                        finally:
                            _log_local.sink = None
                finally:
                    for cookie, ctx in list(contexts.values()):
                        try:
                            ctx.close()
                        except Exception:
                            pass
                    try:
                        browser.close()
                    except Exception:
                        pass
        except BaseException as exc:  # noqa: BLE001 - 启动失败要唤醒排队任务
            with self._lock:
                others = [t for t in self._workers
                          if t.is_alive() and t is not threading.current_thread()]
            if not others:
                while True:
                    try:
                        fut, _fn = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if fut is not None and not fut.done():
                        fut.set_exception(exc)
        finally:
            with self._lock:
                self._workers = [t for t in self._workers
                                 if t.is_alive() and t is not threading.current_thread()]
                # 浏览器崩溃后仍有排队任务时补一个线程，避免 Future 永远等待
                if (not self._closed and not self._queue.empty()
                        and len(self._workers) < self._max):
                    t = threading.Thread(target=self._run, name="douyin-pw", daemon=True)
                    t.start()
                    self._workers.append(t)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            workers = list(self._workers)
        for _ in workers:
            self._queue.put((None, None))
        for t in workers:
            t.join(timeout=5)
        while True:
            try:
                fut, _fn = self._queue.get_nowait()
            except queue.Empty:
                break
            if fut is not None and not fut.done():
                fut.set_exception(RuntimeError("浏览器会话已关闭"))
        with self._lock:
            self._workers = []


_pw_pool = _DouyinPwPool()
atexit.register(_pw_pool.close)


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
        """弹窗链接规范化成标准作品页。

        主页搜索弹窗（/user/xxx/search/关键词?...&modal_id=）和全站搜索弹窗
        （/search/关键词?...&modal_id=）的作品 ID 都在 modal_id query 里——
        两类都必须重写成 /video/{id}，否则页面解析对搜索页 HTML 无从下手。"""
        if "/user/" in url or "/search/" in url:
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
                print(f"[抖音] API 返回: {data.get('status_msg', '')}（可能需要登录）")
        except Exception as e:
            print(f"[抖音] API 请求失败: {e}")
        return None

    @staticmethod
    def _note_images(item: dict) -> tuple:
        """图集判定与图片直链提取。返回 (is_note, images)。

        判定同 multi_downloader._parse_douyin：aweme_type=68，或 images 非空
        且无视频码率档（图文的 video.play_addr 是背景音乐 mp3，不是视频流，
        绝不能当 video_url 返回——否则下载器只下到封面）。"""
        images = []
        for img in (item.get("images") or []):
            ul = img.get("url_list") or []
            if ul:
                images.append(ul[0])
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        is_note = (int(item.get("aweme_type", 0) or 0) == 68
                   or (bool(images) and not (video.get("bit_rate") or [])))
        return is_note, images

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
        is_note, note_images = self._note_images(item)
        info = VideoInfo(
            platform="douyin", video_id=video_id, title=item.get("desc", ""),
            author=author.get("nickname", ""), author_id=str(author.get("uid", "")),
            play_count=int(stats.get("play_count", 0) or 0),
            like_count=int(stats.get("digg_count", 0) or 0),
            comment_count=int(stats.get("comment_count", 0) or 0),
            share_count=int(stats.get("share_count", 0) or 0),
            favorite_count=int(stats.get("collect_count", 0) or 0),
            duration=duration, cover_url=cover_url,
            video_url="" if is_note else self._extract_video_dl_url(item),
            publish_time=pub, tags=[t.get("title", "") for t in item.get("text_extra", [])],
            raw_url=f"https://www.douyin.com/video/{video_id}", raw_data=item, fetched_at=now_str(),
        )
        if is_note:
            info.kind = "note"
            info.images = note_images
        return info

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
            is_note, note_images = (self._note_images(video_info)
                                    if isinstance(video_info, dict) else (False, []))
            if not video_info:
                # 页面无作品数据（风控中间页/搜索页/纯壳页面）：og:title 与
                # <title> 只是页面标题，据此构造的"空壳"（无作者无链接全 0）
                # 会被上游当成功——必须返回 None，走 aweme/detail/Playwright 兜底
                if "验证码" in html[:2000]:
                    print("[抖音] 页面命中验证码中间页，视为失败")
                else:
                    print("[抖音] 页面无作品数据（风控/非作品页），视为失败")
                return None
            video_url_real = "" if is_note else final_url
            if not is_note and video_info and isinstance(video_info, dict) and video_info.get("video"):
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
            if is_note:
                info.kind = "note"
                info.images = note_images
            return info
        except Exception as e:
            print(f"[抖音] 解析失败: {e}")
            return None

    # ========== 用户时间线（HTTP 直调 + Playwright 兜底） ==========

    POST_API = "https://www.douyin.com/aweme/v1/web/aweme/post/"

    @staticmethod
    def _aweme_url(item: dict) -> str:
        """aweme 条目 → 真实作品页链接（图文 note / 视频 video）。"""
        vid = str(item.get("aweme_id") or "")
        if item.get("images"):
            return f"https://www.douyin.com/note/{vid}"
        return f"https://www.douyin.com/video/{vid}"

    def fetch_user_posts(self, url_or_id: str, count: int = 10,
                         pause_every: int = 0, pause_secs: float = 0.0,
                         stop_check=None, on_items=None) -> list:
        """用户作品列表。已启用的 Cookie 优先，游客 HTTP 与浏览器依次兜底。

        pause_every/pause_secs: 账号内部翻页限流（每抓 pause_every 条停 pause_secs 秒），
        0 = 不限流（仅保留翻页基础间隔）。
        stop_check: 返回 True 时停止翻页并保留已获取的部分（取消时断点保留）。

        层级（2026-09-21 Playwright 页面数据层被安全 SDK 拦死后改为 HTTP 直调优先）：
        ① HTTP 直调 aweme/post + a_bogus 签名（Cookie 优先，游客也试一次；
           被 Argus 风控拦截后进入冷却，后续账号直接走浏览器）
        ② Playwright 浏览器兜底（Cookie → 游客；游客态可能缺少最新作品）
        """
        sec_uid = self.extract_user_id(url_or_id)
        if not sec_uid:
            print(f"[抖音] 无法提取用户ID: {url_or_id}")
            return []

        def _stopped():
            return stop_check is not None and stop_check()

        if _stopped():
            return []
        try:
            from auth import get_cookie
            cookie_str = get_cookie("douyin") or ""
        except Exception:
            cookie_str = ""
        cookie_attempts = (cookie_str, "") if cookie_str else ("",)
        partial = []
        partial_keys = set()

        def _remember(items):
            for item in items:
                key = (getattr(item, "video_id", "") or getattr(item, "raw_url", "")
                       or getattr(item, "video_url", "") or id(item))
                if key not in partial_keys:
                    partial_keys.add(key)
                    partial.append(item)

        def _partial_result():
            return partial[:count] if count > 0 else list(partial)

        def _run_stage(fetch, cookie):
            # 失败仍尝试下一层补足目标；全部失败/取消时再用已经获取的作品兜底。
            def _on_items(items):
                _remember(items)
                if on_items is not None:
                    on_items(items)

            try:
                result = fetch(sec_uid, count, cookie_str=cookie,
                               pause_every=pause_every, pause_secs=pause_secs,
                               stop_check=stop_check, on_items=_on_items)
            except Exception as exc:
                print(f"[抖音] 当前采集方式失败: {exc}")
                result = []
            if result:
                _remember(result)
            return result

        # 一级：各登录态 HTTP 只试一次，Cookie 不可用时仍尝试游客 HTTP。
        # 接口被 Argus 拦死后冷却期内跳过整级，避免每个账号都白打两轮请求。
        # 冷却期直接走浏览器（首个账号被拦时已提示，这里不再逐账号刷日志）。
        if _post_http_blocked_for() <= 0:
            for cookie in cookie_attempts:
                if _stopped():
                    return _partial_result()
                if _post_http_blocked_for() > 0:
                    break
                results = _run_stage(self._fetch_user_posts_http, cookie)
                if _stopped():
                    return _partial_result()
                if results:
                    return results

        # 二级：Playwright 兜底
        if _stopped():
            return _partial_result()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except Exception as exc:
            print(f"[抖音] Playwright 不可用: {exc}")
            return _partial_result()
        for cookie in cookie_attempts:
            if _stopped():
                return _partial_result()
            results = _run_stage(self._fetch_pw_once, cookie)
            if _stopped():
                return _partial_result()
            if results:
                return results
            if cookie:
                print("[抖音] 登录态未获取到数据（可能失效或触发风控），"
                      "回退游客态；游客态可能缺少最新作品")
        return _partial_result()

    def _fetch_user_posts_http(self, sec_uid: str, count: int, cookie_str: str = "",
                               pause_every: int = 0, pause_secs: float = 0.0,
                               stop_check=None, on_items=None) -> list:
        """HTTP 直调 aweme/post（a_bogus 签名），max_cursor 链式翻页到 count 条。

        空响应或签名失败时返回空，让上层继续尝试；已回调作品由上层留作最终兜底。
        风控拦截（403 Argus / 验证页 HTML / 空响应）会标记冷却，后续账号跳过直调。
        """
        if stop_check is not None and stop_check():
            return []
        if _post_http_blocked_for() > 0:
            return []
        import requests
        from multi_downloader import UA_PC, _douyin_abogus

        results = []
        failed = False
        cursor = 0
        seen = set()
        label = "抖音-登录态" if cookie_str else "抖音"
        # 有限目标 50 页护栏；0=无上限（count≥10^12）翻到底（has_more=False 自然停）
        max_pages = 2500 if count >= 10**12 else 50
        with requests.Session() as s:
            s.headers.update({"User-Agent": UA_PC, "Referer": "https://www.douyin.com/"})
            if cookie_str:
                s.headers["Cookie"] = cookie_str
            else:
                try:
                    s.get("https://www.douyin.com/", timeout=15)
                except Exception:
                    pass
            for _page in range(max_pages):
                if stop_check is not None and stop_check():
                    if results:
                        print(f"[{label}] 收到停止信号，保留已获取 {len(results)} 条")
                    break
                params = {
                    "device_platform": "webapp", "aid": "6383", "sec_user_id": sec_uid,
                    "max_cursor": str(cursor), "locate_query": "false",
                    "show_live_replay_strategy": "1", "need_time_list": "1",
                    "time_list_query": "0", "whale_cut_token": "", "cut_version": "1",
                    "count": "20",
                }
                query = "&".join(f"{k}={v}" for k, v in params.items())
                d = None
                # 截断/风控偶发：同参数最多两试（第二试换新签名），两败则停止本层
                for _attempt in range(2):
                    if stop_check is not None and stop_check():
                        break
                    a_bogus = _douyin_abogus(query, UA_PC)
                    if not a_bogus:
                        print("[抖音] a_bogus 签名失败，放弃 HTTP 直调")
                        failed = True
                        break
                    if stop_check is not None and stop_check():
                        break
                    try:
                        r = s.get(f"{self.POST_API}?{query}&a_bogus={a_bogus}", timeout=20)
                    except Exception as e:
                        print(f"[抖音] HTTP 直调第 {_attempt + 1} 次失败（{type(e).__name__}）")
                        if stop_check is None or not stop_check():
                            time.sleep(1.0)
                        continue
                    kind = _classify_post_response(r.status_code, r.text)
                    if kind == "transient":
                        print(f"[抖音] 接口暂时不可用（HTTP {r.status_code}），稍后重试")
                        if stop_check is None or not stop_check():
                            time.sleep(1.0)
                        continue
                    if kind == "blocked":
                        _mark_post_http_blocked()
                        print(f"[抖音] 接口被平台拦截（HTTP {r.status_code}），"
                              "本次跳过直调并改用浏览器")
                        failed = True
                        break
                    if kind == "non_json":
                        _mark_post_http_blocked()
                        print(f"[抖音] 接口返回非 JSON（HTTP {r.status_code}），改用浏览器兜底")
                        failed = True
                        break
                    try:
                        d = r.json()
                    except ValueError:
                        print(f"[抖音] 接口响应解析失败（HTTP {r.status_code}），改用浏览器兜底")
                        failed = True
                        break
                    break
                if d is None:
                    if stop_check is None or not stop_check():
                        failed = True
                    break
                if d.get("status_code") != 0:
                    print(f"[抖音] aweme/post status_code={d.get('status_code')}")
                    failed = True
                    break
                page_infos = []
                for a in d.get("aweme_list") or []:
                    try:
                        vid = str(a.get("aweme_id") or "")
                        if not vid or vid in seen:
                            continue
                        info = self._build_from_aweme(a, vid)
                        info.raw_url = self._aweme_url(a)
                    except Exception as exc:
                        print(f"[{label}] 跳过无法解析的作品: {exc}")
                        continue
                    seen.add(vid)
                    results.append(info)
                    page_infos.append(info)
                # 有上限时只回调本次目标数量，避免一页 20 条超出用户填的 10 条
                if page_infos and on_items is not None:
                    callback_items = page_infos
                    if count > 0:
                        remain = count - (len(results) - len(page_infos))
                        callback_items = page_infos[:max(0, remain)]
                    if callback_items:
                        try:
                            on_items(callback_items)
                        except Exception:
                            pass
                if len(results) >= count or not d.get("has_more"):
                    break
                if stop_check is not None and stop_check():
                    break
                cursor = int(d.get("max_cursor") or 0)
                if (_page + 1) % 5 == 0:
                    target = "无上限" if count >= 10**12 else f"目标 {count}"
                    print(f"[{label}] 已翻 {_page + 1} 页 · 累计 {len(results)} 条（{target}），继续...")
                # 翻页间隔：限流开启时按「每 pause_every 条停 pause_secs 秒」折算
                # （一页约 20 条 = 2×10 条 → 停 2×10s）；未开启仅保留基础间隔
                page_new = len(d.get("aweme_list") or [])
                if pause_every > 0 and pause_secs > 0 and page_new:
                    time.sleep(pause_secs * page_new / pause_every)
                else:
                    time.sleep(0.6)
        if failed:
            print("[抖音] HTTP 直调未取得数据，转浏览器兜底")
            return []
        print(f"[抖音] HTTP 直调获取到 {len(results)} 条")
        return results[:count] if count > 0 else results

    def _fetch_pw_once(self, sec_uid: str, count: int, cookie_str: str = "",
                       pause_every: int = 0, pause_secs: float = 0.0,
                       stop_check=None, on_items=None) -> list:
        """提交到浏览器会话池；调用线程回放浏览器线程日志。"""
        if stop_check is not None and stop_check():
            return []
        fut = _pw_pool.submit(
            lambda browser, contexts: self._fetch_pw_in_browser(
                browser, contexts, sec_uid, count, cookie_str,
                pause_every=pause_every, pause_secs=pause_secs,
                stop_check=stop_check, on_items=on_items))
        result, logs = fut.result()
        for line in logs:
            print(line)
        return result

    def _pw_context(self, browser, contexts, cookie_str: str):
        """复用登录/游客上下文；Cookie 变化时重建，避免沿用旧登录态。"""
        state = "login" if cookie_str else "guest"
        cached = contexts.get(state)
        if cached is not None:
            cached_cookie, ctx = cached
            try:
                if cached_cookie == cookie_str and not ctx.is_closed():
                    return ctx
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
            contexts.pop(state, None)
        ctx = browser.new_context(
            user_agent=_PW_UA,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        ctx.add_init_script(_PW_STEALTH_JS)
        if cookie_str:
            for item in cookie_str.split("; "):
                if "=" in item:
                    name, value = item.split("=", 1)
                    try:
                        ctx.add_cookies([{"name": name, "value": value,
                                          "domain": ".douyin.com", "path": "/"}])
                    except Exception:
                        pass
        contexts[state] = (cookie_str, ctx)
        return ctx

    def _fetch_pw_in_browser(self, browser, contexts, sec_uid: str, count: int,
                             cookie_str: str = "", pause_every: int = 0,
                             pause_secs: float = 0.0, stop_check=None,
                             on_items=None) -> list:
        """在池线程内抓取单个账号（cookie_str 为空即游客态）。"""
        if stop_check is not None and stop_check():
            return []
        context = self._pw_context(browser, contexts, cookie_str)
        results = []
        page = context.new_page()
        collected = []
        revealed = 0
        first_timeline = threading.Event()
        login_state = "unknown"

        def _reveal(raw_items):
            """把新解析到的作品立即回调给采集层（主线程按账号节奏揭示）。"""
            nonlocal revealed
            if on_items is None:
                return
            items = []
            for a in raw_items:
                if count > 0 and revealed + len(items) >= count:
                    break
                try:
                    info = self._build_from_aweme(a, str(a.get("aweme_id", "")))
                    info.raw_url = self._aweme_url(a)
                    items.append(info)
                except Exception:
                    pass
            if not items:
                return
            revealed += len(items)
            try:
                on_items(items)
            except Exception:
                pass

        def on_response(response):
            nonlocal login_state
            if response.status != 200:
                return
            url = response.url
            if "user/profile/self" in url and login_state == "unknown":
                try:
                    login_state = "ok" if (response.json() or {}).get("status_code") == 0 else "expired"
                except Exception:
                    login_state = "expired"
            # 匹配多种用户作品列表 API 端点
            if any(p in url for p in ["aweme/v1/web/aweme/post", "/aweme/post/", "user/profile"]):
                try:
                    data = response.json()
                    aweme_list = data.get("aweme_list", [])
                    # 只把「有作品」的列表响应当作首屏就绪；空响应多为预取/风控，
                    # 不能因此提前结束等待
                    if aweme_list and ("aweme/v1/web/aweme/post" in url or "/aweme/post/" in url):
                        first_timeline.set()
                    new_raw = []
                    for a in aweme_list:
                        if a not in collected:
                            collected.append(a)
                            new_raw.append(a)
                    if new_raw:
                        _reveal(new_raw)
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            user_url = f"https://www.douyin.com/user/{sec_uid}"
            _log(f"[抖音-Playwright] 加载: {user_url[:60]}...")
            # 抖音主页资源重、加载慢，20s 实测会超时（2026-09-18），放宽到 60s
            page.goto(user_url, wait_until="domcontentloaded", timeout=60000)

            # 页面内嵌数据（RENDER_DATA/SSR 首屏）合并进 collected（与 API 拦截去重）
            def _merge_html_items():
                html = page.content()
                new_raw = []
                for item in self._extract_aweme_list_from_html(html):
                    if item not in collected:
                        collected.append(item)
                        new_raw.append(item)
                if new_raw:
                    _reveal(new_raw)

            def _wait_first_timeline():
                # 首个作品列表响应可能晚于 DOM 就绪，最多等 12s，
                # 避免慢响应被后面的停滞检测误判成空结果
                for _ in range(24):
                    if collected or first_timeline.is_set():
                        return
                    if stop_check is not None and stop_check():
                        return
                    page.wait_for_timeout(500)

            _wait_first_timeline()
            if not collected:
                # 只有 API 没数据时才解析页面 HTML（page.content 在大页面上开销明显）
                _merge_html_items()
            if collected:
                _log(f"[抖音-Playwright] 首屏获取到 {len(collected)} 条")
            elif not first_timeline.is_set() and (stop_check is None or not stop_check()):
                # 首屏可能遇到验证页/空白页：重载一次再等首个列表响应
                _log("[抖音-Playwright] 首屏未获取到作品，重载一次...")
                first_timeline.clear()
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    _wait_first_timeline()
                    if not collected:
                        _merge_html_items()
                except Exception as e:
                    _log(f"[抖音-Playwright] 重载失败: {e}")

            # 滚动触发 API 翻页加载，凑够 count 条即停；0=无上限（count≥10^12）
            # 放开轮数护栏，靠停滞检测收尾（连续 3 轮无新数据 = 已到底）
            rounds = 400 if count >= 10**12 else 5
            prev, stalls = -1, 0
            for i in range(rounds):
                if len(collected) >= count:
                    break
                if stop_check is not None and stop_check():
                    _log(f"[抖音-Playwright] 收到停止信号，保留已获取 {len(collected)} 条")
                    break
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                if len(collected) == prev:
                    stalls += 1
                    if stalls >= 3:
                        break
                else:
                    stalls = 0
                prev = len(collected)
                if (i + 1) % 10 == 0:
                    _log(f"[抖音-Playwright] 已滚动 {i + 1} 轮 · 累计 {len(collected)} 条，继续...")
                # 限流：每次滚动加载约一页（20 条），按「每 pause_every 条停 pause_secs 秒」折算
                if pause_every > 0 and pause_secs > 0:
                    page.wait_for_timeout(int(pause_secs * 20 / pause_every * 1000))

            if not collected and (stop_check is None or not stop_check()):
                page.wait_for_timeout(5000)
                _merge_html_items()

            if collected and cookie_str and login_state == "expired":
                _log("[抖音] 登录状态已失效，本次仅获取公开作品，可能缺少最新内容")

            for item in collected[:count]:
                info = self._build_from_aweme(item, str(item.get("aweme_id", "")))
                info.raw_url = self._aweme_url(item)
                results.append(info)

        except Exception as e:
            _log(f"[抖音-Playwright] 页面加载失败: {e}")
        finally:
            try:
                page.close()
            except Exception:
                pass

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
                print("[抖音] 游客解析失败，接入登录态重试...")
                # Playwright 游客已在一级试过，Cookie 重试只走 API + 页面
                result = self._parse_once(url, None, cookie=ck, allow_playwright=False)
        return result

    def _build_from_parsed_dict(self, d: dict, url: str) -> VideoInfo:
        """multi_downloader._parse_douyin（aweme/detail）结果 → VideoInfo。

        图文 note：kind=note、video_url 置空（其 play_addr 是背景音乐 mp3）、
        images 带全部图集直链；视频则带所选画质直链。"""
        is_note = bool(d.get("is_note"))
        info = VideoInfo(
            platform="douyin",
            kind="note" if is_note else "video",
            video_id=str(d.get("video_id") or self.extract_id(url) or ""),
            title=d.get("title", ""),
            description=d.get("title", ""),
            author=d.get("author", ""),
            author_id=str(d.get("author_id", "") or ""),
            play_count=int(d.get("play_count", 0) or 0),
            like_count=int(d.get("like_count", 0) or 0),
            comment_count=int(d.get("comment_count", 0) or 0),
            cover_url=d.get("cover_url", ""),
            video_url="" if is_note else (d.get("video_url") or ""),
            publish_time=d.get("publish_time", ""),
            raw_url=url, fetched_at=now_str(),
        )
        if d.get("raw_data"):
            info.raw_data = d["raw_data"]
        if is_note or (d.get("images") and not info.video_url):
            info.images = d.get("images") or []
        return info

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
                        return self._build_from_parsed_dict(d, url)
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
            # 页面解析失败 → aweme/detail + a_bogus 签名接口（比 Playwright 轻，
            # 游客→Cookie 内部自动兜底；风控期页面/浏览器双挂时实测最可用，
            # 图集 note 也只有这条链拿得到完整 images）
            print("[抖音] 页面解析未取到数据，尝试 aweme/detail 接口...")
            try:
                from multi_downloader import parse_video
                d = parse_video(f"https://www.douyin.com/video/{video_id}")
                if d.get("title") or d.get("images") or d.get("video_url"):
                    print("[抖音] aweme/detail 获取成功")
                    return self._build_from_parsed_dict(d, url)
            except Exception as e:
                print(f"[抖音] aweme/detail 失败: {e}")
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
