"""
B站 (Bilibili) 视频信息解析器
支持: 公开API + WBI签名 + Playwright保底 + 限流重试 + 多P检测
"""

from typing import Optional
import re
import time
import hashlib
from urllib.parse import urlencode
import httpx
from .base import PlatformBase, VideoInfo, now_str, normalize_datetime

# B站 WBI 签名固定置换表（用于混淆 img_key+sub_key 得到 mixin_key）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 20, 62, 57, 36, 34, 44, 11, 52,
]


def _build_video_url(v: dict) -> str:
    bvid = v.get("bvid", "")
    if bvid: return f"https://www.bilibili.com/video/{bvid}"
    aid = v.get("aid", "")
    if aid: return f"https://www.bilibili.com/video/av{aid}"
    return ""


def get_wbi_keys(headers: dict) -> tuple[str, str]:
    """获取 WBI 签名的 img_key / sub_key（nav 接口）。"""
    resp = httpx.get("https://api.bilibili.com/x/web-interface/nav",
                     headers=headers, timeout=15, follow_redirects=True)
    data = resp.json()
    wbi_img = (data.get("data") or {}).get("wbi_img")
    if not wbi_img:
        raise RuntimeError(f"nav 接口返回异常: {data}")
    img_key = wbi_img["img_url"].split("/")[-1].split(".")[0]
    sub_key = wbi_img["sub_url"].split("/")[-1].split(".")[0]
    return img_key, sub_key


def get_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB if i < len(raw))[:32]


def sign_params(params: dict, mixin_key: str) -> dict:
    """对请求参数做 WBI 签名（追加 wts + w_rid）。"""
    params["wts"] = int(time.time())
    sorted_items = sorted(params.items(), key=lambda x: x[0])
    query = urlencode(sorted_items)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


class BilibiliParser(PlatformBase):
    name = "bilibili"

    VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
    VIDEO_STAT_API = "https://api.bilibili.com/x/web-interface/archive/stat"
    USER_API = "https://api.bilibili.com/x/space/arc/search"
    USER_WBI_API = "https://api.bilibili.com/x/space/wbi/arc/search"
    DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
    DYNAMIC_DETAIL_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com",
    }

    BV_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10}")
    AV_PATTERN = re.compile(r"av(\d+)", re.IGNORECASE)
    USER_SPACE_PATTERN = re.compile(r"space\.bilibili\.com/(\d+)")
    DYNAMIC_ID_PATTERN = re.compile(r"(?:t\.bilibili\.com|bilibili\.com/opus)/(\d+)")


    def extract_id(self, url: str) -> tuple[str, str]:
        bv = self.BV_PATTERN.search(url)
        if bv: return ("bvid", bv.group(0))
        av = self.AV_PATTERN.search(url)
        if av: return ("aid", av.group(1))
        return ("", "")

    def extract_user_id(self, url: str) -> str:
        m = self.USER_SPACE_PATTERN.search(url)
        if m: return m.group(1)
        m = re.search(r'(\d{5,15})', url)
        if m: return m.group(1)
        return ""

    def _resolve_short_url(self, url: str, client: httpx.Client) -> str:
        try:
            resp = client.head(url, follow_redirects=False)
            if resp.headers.get("location"): return resp.headers["location"]
        except: pass
        try:
            resp = client.get(url, follow_redirects=True)
            return str(resp.url)
        except: return url

    def parse(self, url: str, client: Optional[httpx.Client] = None) -> Optional[VideoInfo]:
        """两级回退：① 游客直连 → ② 接入Cookie重试（Cookie固定放第三级）。"""
        # 动态链接走动态详情解析
        if self.DYNAMIC_ID_PATTERN.search(url):
            return self.parse_dynamic(url, client)
        result = self._parse_once(url, client, cookie="")
        if result is None and client is None:
            from auth import get_cookie
            ck = get_cookie("bilibili")
            if ck:
                print("[B站] 游客解析失败，接入Cookie重试...")
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
            original_url = url
            if "b23.tv" in url or re.search(r'https?://bili\d+\.cn/', url):
                url = self._resolve_short_url(url, client)
            id_type, id_value = self.extract_id(url)
            if not id_value:
                print(f"[B站] 无法提取视频ID: {original_url}")
                return None
            params = {"bvid": id_value} if id_type == "bvid" else {"aid": id_value}

            # 带重试的 API 调用
            v, stat_data = self._api_call_with_retry(client, params)
            if not v:
                return None

            # 构建多P描述
            pages = v.get("pages", [])
            multi_p = ""
            if len(pages) > 1:
                multi_p = f" [共{len(pages)}P"
                ugc = v.get("ugc_season")
                if ugc and ugc.get("title"):
                    multi_p += f" | 合集: {ugc['title'][:30]}"
                multi_p += "]"
                part_titles = " | ".join([f"P{p.get('page','?')}:{p.get('part','')[:20]}" for p in pages[:8]])
                if len(pages) > 8: part_titles += f" ...共{len(pages)}P"
            else:
                part_titles = ""

            desc = v.get("desc", "")
            if multi_p:
                desc = multi_p + "\n" + part_titles + ("\n" + desc if desc else "")

            return VideoInfo(
                platform="bilibili", video_id=v.get('bvid','') or f"av{v.get('aid','')}",
                title=v.get("title",""), description=desc,
                author=v.get("owner",{}).get("name",""),
                author_id=str(v.get("owner",{}).get("mid","")),
                author_face=v.get("owner",{}).get("face",""),
                play_count=stat_data.get("view",v.get("stat",{}).get("view",0)),
                like_count=stat_data.get("like",v.get("stat",{}).get("like",0)),
                comment_count=stat_data.get("reply",v.get("stat",{}).get("reply",0)),
                share_count=stat_data.get("share",v.get("stat",{}).get("share",0)),
                favorite_count=stat_data.get("favorite",v.get("stat",{}).get("favorite",0)),
                danmaku_count=stat_data.get("danmaku",v.get("stat",{}).get("danmaku",0)),
                coin_count=stat_data.get("coin",v.get("stat",{}).get("coin",0)),
                duration=v.get("duration",0), cover_url=v.get("pic",""),
                video_url=_build_video_url(v),
                publish_time=normalize_datetime(v.get("pubdate","")),
                tags=[t.get("tag_name","") for t in v.get("tag",[])],
                category=v.get("tname",""),
                dimension=f"{pages[0].get('dimension',{}).get('width','?')}x{pages[0].get('dimension',{}).get('height','?')}" if pages else "",
                copyright="自制" if v.get("copyright") == 1 else "转载" if v.get("copyright") == 2 else "",
                raw_url=original_url,
                raw_data={"video":v,"stat":stat_data},
                fetched_at=now_str(),
            )
        except Exception as e:
            print(f"[B站] 解析失败: {e}")
            return None
        finally:
            if close_client: client.close()

    def parse_dynamic(self, url: str, client: Optional[httpx.Client] = None) -> Optional[VideoInfo]:
        """解析单条 B站动态（t.bilibili.com/{id} 或 bilibili.com/opus/{id}）。两级回退：游客优先。"""
        result = self._parse_dynamic_once(url, client, cookie="")
        if result is None and client is None:
            from auth import get_cookie
            ck = get_cookie("bilibili")
            if ck:
                print("[B站] 游客动态解析失败，接入Cookie重试...")
                result = self._parse_dynamic_once(url, None, cookie=ck)
        return result

    def _parse_dynamic_once(self, url: str, client: Optional[httpx.Client], cookie: str = "") -> Optional[VideoInfo]:
        m = self.DYNAMIC_ID_PATTERN.search(url)
        if not m:
            print(f"[B站] 无法提取动态ID: {url}")
            return None
        dyn_id = m.group(1)
        close_client = False
        if client is None:
            headers = dict(self.HEADERS)
            headers["Referer"] = f"https://t.bilibili.com/{dyn_id}"
            if cookie: headers["Cookie"] = cookie
            client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
            close_client = True
        try:
            resp = client.get(self.DYNAMIC_DETAIL_API, params={
                "id": dyn_id, "timezone_offset": "-480", "features": "itemOpusStyle",
            })
            data = resp.json()
            if data.get("code") != 0:
                print(f"[B站] 动态详情接口错误: {data.get('message')}")
                return None
            item = (data.get("data") or {}).get("item") or {}
            if not item:
                print("[B站] 动态不存在或已删除")
                return None
            info = self._build_dynamic_info(item)
            info.raw_url = url
            return info
        except Exception as e:
            print(f"[B站] 动态解析失败: {e}")
            return None
        finally:
            if close_client: client.close()

    @staticmethod
    def _build_dynamic_info(item: dict) -> "VideoInfo":
        """从动态 item（feed/space 与 detail 接口返回结构一致）构建 VideoInfo。"""
        mod = item.get("modules", {})
        author_mod = mod.get("module_author", {})
        dyn_mod = mod.get("module_dynamic", {})
        stat_mod = mod.get("module_stat", {})

        author = author_mod.get("name", "")
        author_id = str(author_mod.get("mid", ""))
        author_face = author_mod.get("face", "")

        desc = dyn_mod.get("desc") or {}
        text = desc.get("text", "") if isinstance(desc, dict) else str(desc)
        text = re.sub(r'<[^>]+>', '', text)

        major = dyn_mod.get("major") or {}
        mtype = major.get("type", "")
        dyn_id = item.get("id_str", "")

        publish_ts = int(author_mod.get("pub_ts", 0) or 0)
        publish_time = normalize_datetime(publish_ts) if publish_ts else ""
        if not publish_time:
            # 兜底：动态 id 前10位编码了发布时间戳
            if dyn_id and len(dyn_id) >= 10 and dyn_id[:10].isdigit():
                try:
                    publish_time = normalize_datetime(int(dyn_id[:10]))
                except (ValueError, TypeError, OSError):
                    pass

        def _cnt(name):
            v = stat_mod.get(name)
            return int(v.get("count", 0)) if isinstance(v, dict) else 0
        like_count = _cnt("like")
        comment_count = _cnt("comment")
        share_count = _cnt("forward")
        view_count = _cnt("view")

        images = []
        cover_url = ""
        video_url = ""
        title = text[:80] if text else f"B站动态 {dyn_id[:8]}"

        if mtype == "MAJOR_TYPE_DRAW":
            # 图片动态
            draw = major.get("draw", {})
            for img_item in draw.get("items", []):
                src = img_item.get("src", "")
                if src:
                    images.append(src)
            if images:
                cover_url = images[0]
        elif mtype == "MAJOR_TYPE_ARCHIVE":
            # 转发视频：不污染 video_url，避免和原视频去重冲突；仅参考封面/标题
            archive = major.get("archive", {})
            cover_url = archive.get("cover", "")
            archive_title = archive.get("title", "")
            if archive_title and not title:
                title = archive_title
        elif mtype == "MAJOR_TYPE_OPUS":
            # 纯文本/图文短帖（desc 常为 null，正文在 summary）
            opus = major.get("opus", {})
            for pic in opus.get("pics", []):
                src = pic.get("url", "") or pic.get("src", "")
                if src:
                    images.append(src)
            if images:
                cover_url = images[0]
            if not text:
                summary = opus.get("summary") or {}
                if isinstance(summary, dict):
                    nodes = summary.get("rich_text_nodes") or []
                    parts = []
                    for node in nodes:
                        t = node.get("text") or node.get("orig_text") or ""
                        if t:
                            parts.append(t)
                    if parts:
                        text = "".join(parts)
                    elif isinstance(summary.get("text"), str):
                        text = summary.get("text")
            opus_title = opus.get("title", "") or ""
            if opus_title:
                title = opus_title
            elif text:
                title = text[:80]

        return VideoInfo(
            platform="bilibili",
            kind="dynamic",
            video_id=f"dyn_{dyn_id}",
            title=title,
            description=text,
            author=author,
            author_id=author_id,
            author_face=author_face,
            play_count=int(view_count),
            like_count=int(like_count),
            comment_count=int(comment_count),
            share_count=int(share_count),
            cover_url=cover_url,
            video_url=video_url or f"https://t.bilibili.com/{dyn_id}",
            publish_time=publish_time,
            images=images,
            raw_url=f"https://t.bilibili.com/{dyn_id}",
            raw_data=item,
            fetched_at=now_str(),
            category="动态",
        )

    def _api_call_with_retry(self, client, params, max_retries=3) -> tuple:
        """API调用带限流重试，显示冷却倒计时"""
        for attempt in range(max_retries):
            resp = client.get(self.VIDEO_INFO_API, params=params)
            data = resp.json()
            code = data.get("code", 0)

            if code == 0:
                v = data["data"]
                stat_data = {}
                stat_resp = client.get(self.VIDEO_STAT_API, params=params)
                if stat_resp.status_code == 200:
                    sj = stat_resp.json()
                    if sj.get("code") == 0: stat_data = sj["data"]
                return v, stat_data

            msg = data.get("message", "")
            if code == -799 or "频繁" in str(msg):
                wait = (attempt + 1) * 5  # 5s, 10s, 15s
                print(f"[B站] 请求频繁，冷却 {wait}s...", end="", flush=True)
                for _ in range(wait):
                    time.sleep(1)
                    print(".", end="", flush=True)
                print(" 重试")
            else:
                print(f"[B站] API错误: {msg}")
                break
        return None, {}

    # ========== 用户时间线 (HTTP + Playwright) ==========

    def fetch_user_posts(self, url_or_id: str, count: int = 10, kind: str = "all") -> list:
        """获取用户最新内容。kind: "video"=只视频、"dynamic"=只动态、"all"=全部"""
        mid = self.extract_user_id(url_or_id)
        if not mid:
            print(f"[B站] 无法提取用户ID: {url_or_id}")
            return []
        results = []
        # 按 kind 决定只拉视频 / 只拉动态 / 混合
        if kind in ("all", "video"):
            n = count if kind == "video" else (count // 2 + 1)
            results.extend(self._fetch_user_videos(mid, n))
        if kind in ("all", "dynamic"):
            if kind == "dynamic":
                n = count
            else:
                # 已抓的视频数（按非 dynamic 计）保证合计接近 count
                existing_videos = sum(1 for r in results if getattr(r, "kind", "") != "dynamic")
                n = count - existing_videos + 2
            results.extend(self._fetch_dynamics(mid, n))
        # 去重并限制数量
        seen = set()
        unique = []
        for r in results:
            if r.video_id not in seen:
                seen.add(r.video_id)
                unique.append(r)
        # 按发布时间倒序
        unique.sort(key=lambda x: x.publish_time or "", reverse=True)
        return unique[:count]

    def _fetch_user_videos(self, mid: str, count: int = 10) -> list:
        """三级回退：① 游客HTTP（内部失败切游客Playwright）→ ② 接入Cookie重试。"""
        results = self._fetch_user_videos_once(mid, count, cookie="")
        if results:
            return results
        from auth import get_cookie
        ck = get_cookie("bilibili")
        if ck:
            print("[B站] 游客方式未获取到视频，接入Cookie重试...")
            results = self._fetch_user_videos_once(mid, count, cookie=ck)
        return results

    def _fetch_user_videos_once(self, mid: str, count: int = 10, cookie: str = "") -> list:
        results = []
        page = 1; remaining = count
        headers = dict(self.HEADERS)
        if cookie: headers["Cookie"] = cookie
        client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
        # 尝试获取 WBI 签名密钥（nav 接口），失败则回退旧接口
        mixin_key = None
        try:
            _img_key, _sub_key = get_wbi_keys(headers)
            mixin_key = get_mixin_key(_img_key, _sub_key)
        except Exception:
            mixin_key = None
        try:
            while remaining > 0:
                ps = min(remaining, 30)
                params = {"mid":mid,"ps":ps,"pn":page,"order":"pubdate","tid":"0","keyword":""}
                if mixin_key:
                    resp = client.get(self.USER_WBI_API, params=sign_params(dict(params), mixin_key))
                else:
                    resp = client.get(self.USER_API, params=params)
                data = resp.json()
                code = data.get("code", 0)
                if code != 0:
                    msg = data.get("message","")
                    if mixin_key:
                        # WBI 失败，回退旧接口试一次
                        mixin_key = None
                        resp = client.get(self.USER_API, params=params)
                        data = resp.json()
                        code = data.get("code", 0)
                        if code != 0:
                            print(f"[B站] API错误: {msg}，切 Playwright...")
                            pw = self._fetch_user_posts_playwright(mid, count)
                            results.extend(pw)
                            client.close()
                            return results
                    elif code == -799 or "频繁" in str(msg):
                        wait = 5
                        print(f"[B站] 请求频繁，冷却 {wait}s...", end="", flush=True)
                        for _ in range(wait):
                            time.sleep(1)
                            print(".", end="", flush=True)
                        print(" 重试")
                        resp = client.get(self.USER_API, params=params)
                        data = resp.json()
                        if data.get("code") != 0:
                            print("[B站] 重试仍失败，切 Playwright...")
                            pw = self._fetch_user_posts_playwright(mid, count)
                            results.extend(pw)
                            client.close()
                            return results
                    else:
                        print(f"[B站] API错误: {msg}，切 Playwright...")
                        pw = self._fetch_user_posts_playwright(mid, count)
                        results.extend(pw)
                        client.close()
                        return results
                vlist = data.get("data",{}).get("list",{}).get("vlist",[])
                if not vlist: break
                for v in vlist:
                    results.append(VideoInfo(
                        platform="bilibili",
                        video_id=v.get('bvid','') or f"av{v.get('aid','')}",
                        title=v.get("title",""), description=v.get("description",""),
                        author=v.get("author",""), author_id=str(v.get("mid","")),
                        play_count=int(v.get("play",0) or 0), comment_count=int(v.get("comment",0) or 0),
                        duration=self._parse_duration(v.get("length","")),
                        cover_url=v.get("pic",""), video_url=_build_video_url(v),
                        publish_time=normalize_datetime(int(v.get("created",0))),
                        raw_url=f"https://space.bilibili.com/{mid}", raw_data=v, fetched_at=now_str(),
                        category="视频",
                    ))
                remaining -= ps; page += 1
                if len(vlist) < ps: break
            return results
        except Exception as e:
            print(f"[B站] 异常: {e}，切 Playwright...")
            pw = self._fetch_user_posts_playwright(mid, count)
            results.extend(pw)
            return results
        finally:
            client.close()

    def _fetch_user_posts_playwright(self, mid: str, count: int = 10) -> list:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[B站] Playwright 未安装")
            return []
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=self.HEADERS["User-Agent"])
            page = ctx.new_page()
            collected = []
            def on_resp(r):
                if "arc/search" in r.url and r.status == 200:
                    try:
                        d = r.json()
                        if d.get("code") == 0:
                            for v in d.get("data",{}).get("list",{}).get("vlist",[]):
                                collected.append(v)
                    except: pass
            page.on("response", on_resp)
            try:
                page.goto(f"https://space.bilibili.com/{mid}/video", wait_until="networkidle", timeout=30000)
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                if not collected:
                    page.wait_for_timeout(5000)
                for v in collected[:count]:
                    results.append(VideoInfo(
                        platform="bilibili", video_id=v.get('bvid','') or f"av{v.get('aid','')}",
                        title=v.get("title",""), description=v.get("description",""),
                        author=v.get("author",""), author_id=str(v.get("mid","")),
                        play_count=int(v.get("play",0) or 0), comment_count=int(v.get("comment",0) or 0),
                        duration=self._parse_duration(v.get("length","")),
                        cover_url=v.get("pic",""), video_url=_build_video_url(v),
                        publish_time=normalize_datetime(int(v.get("created",0))),
                        raw_url=f"https://space.bilibili.com/{mid}", raw_data=v, fetched_at=now_str(),
                        category="视频",
                    ))
            except Exception as e:
                print(f"[B站-Playwright] 失败: {e}")
            finally:
                browser.close()
        if not results:
            print("[B站] Playwright 也未获取到数据")
        return results

    # ========== 动态 (含图文) ==========

    def _fetch_dynamics(self, mid: str, count: int = 10) -> list:
        """获取用户动态（支持图文、转发视频等）。游客优先，失败接入Cookie。"""
        results = self._fetch_dynamics_once(mid, count, cookie="")
        if results:
            return results
        from auth import get_cookie
        ck = get_cookie("bilibili")
        if ck:
            print("[B站] 游客方式未获取到动态，接入Cookie重试...")
            results = self._fetch_dynamics_once(mid, count, cookie=ck)
        return results

    def _fetch_dynamics_once(self, mid: str, count: int = 10, cookie: str = "") -> list:
        results = []
        headers = dict(self.HEADERS)
        headers["Referer"] = f"https://space.bilibili.com/{mid}/dynamic"
        if cookie: headers["Cookie"] = cookie
        client = httpx.Client(headers=headers, timeout=15, follow_redirects=True)
        offset = ""
        try:
            while len(results) < count + 5:
                params = {
                    "host_mid": mid,
                    "timezone_offset": "-480",
                    "features": "itemOpusStyle",
                    "offset": offset or "",
                }
                resp = client.get(self.DYNAMIC_API, params=params)
                data = resp.json()
                if data.get("code") != 0:
                    break
                items = data.get("data", {}).get("items", [])
                if not items:
                    break
                for item in items:
                    results.append(self._build_dynamic_info(item))

                offset = data.get("data", {}).get("offset", "")
                has_more = data.get("data", {}).get("has_more", False)
                if not has_more or not offset:
                    break
            return results
        except Exception as e:
            print(f"[B站] 动态获取异常: {e}")
            return results
        finally:
            client.close()

    @staticmethod
    def _parse_duration(s: str) -> int:
        if not s: return 0
        parts = s.split(":")
        if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        try: return int(s)
        except ValueError: return 0
