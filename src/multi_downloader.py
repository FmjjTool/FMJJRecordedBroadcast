"""
多平台视频解析下载（从 video-parser 项目迁移）。

支持平台：抖音 / 小红书 / 快手 / 好看视频 / 微视 / 梨视频 / 皮皮搞笑（B站走独立 playurl 模块）。

核心思路：各平台返回统一结构 (title, video_url, cover_url)，下载时去水印/取最高清流。
抖音需要 a_bogus 签名（node 执行 toolbox/a_bogus.js），其余平台纯 HTTP + 正则解析页面内嵌 JSON。
"""

import json
import os
import re
import random
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx

# 域名 → 平台名
DOMAIN_TO_NAME = {
    "www.xiaohongshu.com": "小红书",
    "www.douyin.com": "抖音",
    "www.iesdouyin.com": "抖音",
    "v.douyin.com": "抖音",
    "www.kuaishou.com": "快手",
    "haokan.baidu.com": "好看视频",
    "haokan.hao123.com": "好看视频",
    "isee.weishi.qq.com": "微视",
    "www.pearvideo.com": "梨视频",
    "h5.pipigx.com": "皮皮搞笑",
}

UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
UA_M = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


def _find_node() -> str:
    """查找 node 可执行文件路径。

    优先级：PATH → Playwright 自带 node（Windows 分发，项目已依赖，
    桌面环境 PATH 无 node 时的关键兜底）→ 常规安装位 → TRAE 内置。
    """
    node = shutil.which("node")
    if node:
        return node
    cands = []
    # Playwright driver 自带 node.exe（playwright 已是项目依赖）
    try:
        import playwright as _pw
        cands.append(str(Path(_pw.__file__).parent / "driver" / "node.exe"))
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    for extra in (
        r"C:\Program Files\nodejs\node.exe",
        rf"{local}\Programs\nodejs\node.exe",
        rf"{appdata}\TRAE SOLO CN\ModularData\ai-agent\vm\tools\node\node.exe",
        rf"{appdata}\TRAE\ModularData\ai-agent\vm\tools\node\node.exe",
        r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe",
    ):
        cands.append(extra)
    for cand in cands:
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("未找到 node 环境（抖音 a_bogus 签名需要）")


def _douyin_abogus(query: str, user_agent: str = UA_PC) -> str:
    """调用 node 执行 a_bogus.js 生成 a_bogus 签名。

    node 缺失/调用失败一律返回空串（调用方自行降级），不抛异常——
    否则会击穿抖音采集的三级回退链（游客 HTTP → Playwright → Cookie）。
    """
    cli = Path(__file__).parent / "toolbox" / "abogus_cli.js"
    try:
        node = _find_node()
        out = subprocess.run(
            [node, str(cli), query, user_agent],
            capture_output=True, timeout=15, encoding="utf-8", errors="replace",
        )
        return out.stdout.strip()
    except Exception as e:
        print(f"[抖音] a_bogus 签名失败: {e}")
        return ""


_DY_DETAIL_PARAMS = ("device_platform=webapp&aid=6383&channel=channel_pc_web"
                     "&pc_client_type=1&version_code=190500&version_name=19.5.0"
                     "&cookie_enabled=true&platform=PC")

# 本次进程内 aweme/detail 是否已被 Argus 风控拦死（返回过 403）。
# 一旦确认，后续条目直接走浏览器兜底，不再白打两次 403 请求（既省 2s/条，
# 也避免持续给风控计数加码）。
_DY_API_BLOCKED = False


def _douyin_detail_by_browser(aweme_id: str, timeout_ms: int = 30000) -> dict:
    """Playwright 兜底：在浏览器上下文内 fetch aweme/detail，返回 aweme_detail。

    2026-10 起抖音该接口对纯 HTTP 客户端（游客 / 带 Cookie / 带 a_bogus 签名）
    一律 403「Blocked by ArgusSecurityPlugin Uifid Not Found」——Argus 校验的是
    浏览器指纹与 secsdk 票据（TLS 指纹、sec-ch-ua、x-secsdk-csrf-token 等），
    httpx 无法伪造，a_bogus 再正确也过不去。而在页面上下文里 fetch 会自带
    全套指纹与票据，实测 200 返回完整 aweme_detail（含 bit_rate 各画质直链）。

    失败返回空 dict，不抛异常（调用方按"解析失败"处理）。
    """
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch as pw_launch
    except Exception as e:  # noqa: BLE001 - 未安装/打包缺失都按不可用处理
        print(f"[抖音] Playwright 不可用，跳过浏览器兜底: {e}")
        return {}
    api = (f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{_DY_DETAIL_PARAMS}"
           f"&aweme_id={aweme_id}")
    try:
        with sync_playwright() as p:
            browser = pw_launch(
                p, headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                ctx = browser.new_context(
                    user_agent=UA_PC, viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                )
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>false});")
                page = ctx.new_page()
                # 先开首页：跑完 __ac_nonce JS 挑战、下发 ttwid / UIFID 等票据，
                # 没有这一步直接 fetch 同样是 403
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                page.wait_for_timeout(2500)
                res = [0, ""]
                # 票据落地有快有慢：403 时再等一会儿重试一次，别一次抖动就放弃
                for attempt in range(2):
                    res = page.evaluate(
                        """async (u) => {
                            const r = await fetch(u, {credentials: 'include'});
                            const t = await r.text();
                            return [r.status, t];
                        }""", api)
                    if res and res[0] == 200:
                        return (json.loads(res[1]) or {}).get("aweme_detail") or {}
                    if attempt == 0:
                        page.wait_for_timeout(2000)
                print(f"[抖音] 浏览器接口返回 HTTP {res[0] if res else '?'}")
                return {}
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 - 浏览器异常一律降级
        print(f"[抖音] 浏览器兜底失败: {e}")
        return {}


def recognize_platform(url: str) -> str | None:
    """根据 URL 识别平台名。"""
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return None
    return DOMAIN_TO_NAME.get(domain)


def _get_video_id(url: str) -> str:
    """从 URL 提取视频 ID（优先 query 参数，其次路径末段）。

    modal_id：搜索页/主页弹窗链接（/search/xxx?...&modal_id=123 或
    /user/xxx/search/yyy?...&modal_id=123）的作品 ID 在 query 里——按路径
    末段取会拿到搜索关键词（如"三角洲表情包"），当 aweme_id 发请求必然
    返回空详情，还白打风控计数。"""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    for key in ("vid", "id", "modal_id"):
        if q.get(key):
            return q[key][0]
    seg = parsed.path.strip("/").split("/")
    return seg[-1] if seg else ""


def _extract_json(html: str, pattern: str) -> dict | None:
    """从页面 script 里提取内嵌 JSON 对象。"""
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        return None
    raw = m.group(1).rstrip(";").replace("undefined", "null")
    try:
        return json.loads(raw)
    except Exception:
        return None


# ==================== 各平台解析 ====================

def _pick_douyin_quality(bit_rate: list, quality: str | None) -> str:
    """从抖音 bit_rate 列表按画质选档，返回 play_addr.url_list[0] 直链。

    quality: "最高画质" / "1080P" / "720P" / "540P" / None。
    无该画质时逐级降（选码率最高档）。
    """
    if not bit_rate:
        return ""

    def _url(b):
        ul = (b.get("play_addr") or {}).get("url_list") or []
        return ul[0] if ul else ""

    if not quality or quality == "最高画质":
        best = max(bit_rate, key=lambda b: int(b.get("bit_rate") or 0))
        return _url(best)
    # 按分辨率关键词匹配 gear_name（如 normal_1080_0 / normal_720_0）
    kw = quality.replace("P", "").replace("p", "")
    cands = [b for b in bit_rate if kw in (b.get("gear_name") or "")]
    if not cands:
        cands = bit_rate  # 无该画质，逐级降
    best = max(cands, key=lambda b: int(b.get("bit_rate") or 0))
    return _url(best)


def _parse_douyin(url: str, quality: str | None = None) -> dict:
    """抖音：aweme/detail 接口 + a_bogus 签名。quality 选画质（默认最高）。

    支持视频（/video/）和图文（/note/）：图文 aweme_type=68，只有 images 无视频，
    此时 video_url 置空（play_addr 指向 douyinstatic 封面图 CDN，不是真视频流）。
    """
    global _DY_API_BLOCKED
    aweme_id = _get_video_id(url)
    if not aweme_id or not aweme_id.isdigit():
        # 抖音作品 ID 纯数字；拿不到说明链接不是作品页（如搜索页没带
        # modal_id、或路径被解析成了关键词）——直接报错，不发无效请求
        raise ValueError(f"无法从链接提取抖音作品ID：{url[:80]}")
    base = ("https://www.douyin.com/aweme/v1/web/aweme/detail/?"
            "device_platform=webapp&aid=6383&channel=channel_pc_web"
            f"&aweme_id={aweme_id}&update_version_code=170400&pc_client_type=1"
            "&version_code=190500&version_name=19.5.0&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN"
            "&browser_platform=Win32&browser_name=Chrome&browser_version=127.0.0.0"
            "&browser_online=true&engine_name=Blink&engine_version=127.0.0.0"
            "&os_name=Windows&os_version=10&cpu_core_num=8&device_memory=8"
            "&platform=PC&downlink=1.25&effective_type=4g&round_trip_time=50")
    abogus = _douyin_abogus(base.split("?")[1], UA_PC)
    full_url = f"{base}&a_bogus={abogus}"
    # Referer 按链接类型（note 图文 / video 视频）动态设置
    ref_path = "note" if "/note/" in url else "video"
    headers = {
        "User-Agent": UA_PC,
        "Referer": f"https://www.douyin.com/{ref_path}/{aweme_id}",
    }

    def _detail(cookie: str) -> dict:
        if cookie:
            h = dict(headers)
            h["Cookie"] = cookie
            try:
                r = httpx.get(full_url, headers=h, timeout=15)
                if r.status_code == 403:
                    blocked[0] = True
                return r.json()
            except Exception:
                return {}
        # 游客：先访问首页拿 ttwid 等游客票据，否则接口直接拒绝
        with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as c:
            try:
                c.get("https://www.douyin.com/")
            except Exception:
                pass
            try:
                r = c.get(full_url)
                if r.status_code == 403:
                    blocked[0] = True
                return r.json()
            except Exception:
                return {}

    blocked = [False]
    # 游客优先；拿不到详情才接入登录 Cookie（三级兜底）
    data = {} if _DY_API_BLOCKED else _detail("")
    if not (data.get("aweme_detail") or {}):
        try:
            from auth import get_cookie
            ck = get_cookie("douyin")
        except Exception:
            ck = ""
        if ck and not _DY_API_BLOCKED:
            print("[抖音下载] 游客解析失败，接入Cookie重试...")
            data = _detail(ck)
    detail = data.get("aweme_detail", {}) or {}
    if not detail:
        if blocked[0]:
            # Argus 已确认拦截：本次运行后续条目直接走浏览器，省掉两次 403 往返
            _DY_API_BLOCKED = True
        # 三级兜底：浏览器内 fetch（Argus 风控下唯一稳定的纯解析通道，
        # 见 _douyin_detail_by_browser 注释）
        print("[抖音下载] 接口 403/无数据，改用浏览器接口兜底...")
        detail = _douyin_detail_by_browser(aweme_id)
    video = detail.get("video", {}) or {}

    # 图文判定：aweme_type=68（图集），或 images 非空且无视频码率档
    is_note = int(detail.get("aweme_type", 0) or 0) == 68 or (
        bool(detail.get("images")) and not (video.get("bit_rate") or [])
    )

    video_url = ""
    if not is_note:
        bit_rate = video.get("bit_rate") or []
        if bit_rate:
            video_url = _pick_douyin_quality(bit_rate, quality)
        if not video_url:
            url_list = (video.get("play_addr") or {}).get("url_list") or []
            video_url = url_list[0] if url_list else ""

    images = []
    for img in (detail.get("images") or []):
        url_list = (img.get("url_list") or [])
        if url_list:
            images.append(url_list[0])

    cover = ""
    for key in ("cover_original_scale", "cover"):
        cl = (video.get(key) or {}).get("url_list") or []
        if cl:
            cover = cl[0]
            break
    if not cover and images:
        cover = images[0]

    # 补充元数据（作者 / 数据 / 时间）
    author = (detail.get("author") or {}).get("nickname", "")
    stats = detail.get("statistics") or {}
    publish_time = ""
    ct = detail.get("create_time", 0)
    if ct:
        from datetime import datetime
        try:
            publish_time = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M")
        except Exception:
            publish_time = ""

    return {
        "platform": "抖音",
        "video_id": aweme_id,
        "title": detail.get("desc") or "",
        "video_url": video_url,
        "cover_url": cover,
        "images": images,
        "author": author,
        "author_id": str((detail.get("author") or {}).get("uid", "") or ""),
        "play_count": int(stats.get("play_count", 0) or 0),
        "like_count": int(stats.get("digg_count", 0) or 0),
        "comment_count": int(stats.get("comment_count", 0) or 0),
        "publish_time": publish_time,
        "is_note": is_note,
        "raw_data": detail,
    }


def _parse_xiaohongshu(url: str) -> dict:
    # 复用 platforms 解析器：2026-09 后 SSR 的 consumer.originVideoKey 已消失、
    # stream 键名混淆为 EF*，且游客态常无 note（需 Cookie 兜底）
    from platforms.xiaohongshu import XiaohongshuParser

    info = XiaohongshuParser().parse(url)
    if not info:
        raise ValueError("小红书页面解析失败")
    return {
        "platform": "小红书",
        "title": (info.title or "") + (info.description or ""),
        "video_url": info.video_url or "",
        "cover_url": info.cover_url or "",
        "images": list(info.images or []),
    }


def _parse_kuaishou(url: str) -> dict:
    headers = {"User-Agent": UA_PC, "Referer": "https://www.kuaishou.com/",
               "Cookie": "kpf=PC_WEB; clientid=3; did=web_66ce2b981cc6326ce81c6593ec91501c"}
    resp = httpx.get(url, headers=headers, timeout=15)
    data = _extract_json(resp.text, r"window\.__APOLLO_STATE__\s*=\s*(\{.*\};)")
    if not data:
        raise ValueError("快手页面解析失败")
    vid = _get_video_id(url)
    dc = data.get("defaultClient", {})
    video_url = (dc.get("VisionVideoSetRepresentation:1") or {}).get("url", "").replace("\\u002F", "/")
    cover = (dc.get(f"VisionVideoDetailPhoto:{vid}") or {}).get("coverUrl", "")
    title = (dc.get(f"VisionVideoDetailPhoto:{vid}") or {}).get("caption", "")
    return {"platform": "快手", "title": title, "video_url": video_url, "cover_url": cover, "images": []}


def _parse_haokan(url: str) -> dict:
    headers = {"User-Agent": UA_M, "Referer": "https://haokan.baidu.com/v"}
    resp = httpx.get(url, headers=headers, timeout=15)
    data = _extract_json(resp.text, r"window\.__PRELOADED_STATE__\s*=\s*(\{.*\};)")
    if not data:
        raise ValueError("好看视频页面解析失败")
    meta = data.get("curVideoMeta", {})
    clarity = meta.get("clarityUrl") or []
    from urllib.parse import unquote
    video_url = unquote(clarity[-1].get("url", "")) if clarity else ""
    return {
        "platform": "好看视频",
        "title": meta.get("title", ""),
        "video_url": video_url.replace("\\/", "/"),
        "cover_url": (meta.get("poster") or "").replace("\\/", "/"),
        "images": [],
    }


def _parse_weishi(url: str) -> dict:
    headers = {"User-Agent": UA_PC, "Referer": "https://isee.weishi.qq.com"}
    resp = httpx.get(url, headers=headers, timeout=15)
    data = _extract_json(resp.text, r"window\.Vise\.initState\s*=\s*(\{.*\};)")
    if not data:
        raise ValueError("微视页面解析失败")
    feeds = data.get("feedsList") or []
    if not feeds:
        raise ValueError("微视未找到视频")
    f0 = feeds[0]
    return {
        "platform": "微视",
        "title": f0.get("feedDesc", ""),
        "video_url": (f0.get("videoUrl") or "").replace("\\u002F", "/"),
        "cover_url": (f0.get("videoCover") or "").replace("\\u002F", "/"),
        "images": [],
    }


def _parse_lishipin(url: str) -> dict:
    vid = _get_video_id(url)
    cont_id = "".join(filter(str.isdigit, vid))
    headers = {"User-Agent": UA_PC, "Referer": url}
    resp = httpx.get("https://www.pearvideo.com/videoStatus.jsp",
                     params={"contId": cont_id, "mrd": random.random()},
                     headers=headers, timeout=15)
    data = resp.json()
    info = data.get("videoInfo", {})
    src = (info.get("videos") or {}).get("srcUrl", "")
    video_url = re.sub(r"(\d+)-(\d+-hd\.mp4)", f"cont-{vid}-\\2", src)
    # 标题从页面摘要提取
    html = httpx.get(url, headers=headers, timeout=15).text
    m = re.search(r'class="summary"[^>]*>([^<]+)', html)
    return {
        "platform": "梨视频",
        "title": m.group(1).strip() if m else "",
        "video_url": video_url,
        "cover_url": info.get("video_image", ""),
        "images": [],
    }


def _parse_pipigaoxiao(url: str) -> dict:
    pid = _get_video_id(url)
    headers = {"User-Agent": UA_PC, "Referer": url}
    resp = httpx.post("https://h5.pipigx.com/ppapi/share/fetch_content",
                      json={"mid": "null", "pid": int(pid), "type": "post"},
                      headers=headers, timeout=15)
    data = resp.json()
    post = (data.get("data") or {}).get("post", {})
    imgs = post.get("imgs") or []
    img_id = imgs[0].get("id") if imgs else ""
    video_url = (post.get("videos") or {}).get(str(img_id), {}).get("url", "")
    return {
        "platform": "皮皮搞笑",
        "title": post.get("content", ""),
        "video_url": video_url,
        "cover_url": f"https://file.ippzone.com/img/view/id/{img_id}" if img_id else "",
        "images": [],
    }


_PARSERS = {
    "抖音": _parse_douyin,
    "小红书": _parse_xiaohongshu,
    "快手": _parse_kuaishou,
    "好看视频": _parse_haokan,
    "微视": _parse_weishi,
    "梨视频": _parse_lishipin,
    "皮皮搞笑": _parse_pipigaoxiao,
}


def parse_video(url: str, quality: str | None = None) -> dict:
    """解析视频链接，返回 {platform, title, video_url, cover_url, images}。

    quality 仅对抖音生效（"最高画质"/"1080P"/"720P"/"540P"）。
    """
    platform = recognize_platform(url)
    if not platform:
        raise ValueError(f"不支持的链接: {url}")
    parser = _PARSERS.get(platform)
    if not parser:
        raise ValueError(f"平台 '{platform}' 解析器未实现")
    if platform == "抖音" and quality:
        return parser(url, quality)
    return parser(url)


def _safe_name(name: str, max_len: int = 60) -> str:
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", str(name)).strip()
    return name[:max_len] or "video"
