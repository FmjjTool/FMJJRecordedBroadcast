"""五个音乐平台账号/歌手数据采集器：游客HTTP → 游客Playwright → Cookie 三级回退。

平台与目标：
- 网易云 netease：用户(user) / 歌手(artist)
- QQ音乐 qq：歌手(singer mid)
- 酷狗 kugou：歌手(singerid)
- 酷我 kuwo：歌手(rid)
- 5sing fivesing：用户(uid)

统一 record 字段：platform, kind, name, fans, following, listens, songs,
albums, mvs, works, sign, region, birthday, extra, avatar, url, error。

Cookie 是账号凭据，能不用就不用（降低风控/封号风险）；
只有游客方式实在连不上才接入，Cookie 固定放第三级。
"""

import hashlib
import json
import random
import re
import time

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
             "Mobile/15E148 Safari/604.1")

# 酷狗 openapi 签名盐（抓包自 m.kugou.com，2026-08 验证有效）
KG_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"

# 网易云省份代码（GB/T 2260 前两位；7 位自定义码如 1000000 不在表内 → 留空）
NE_PROVINCES = {
    "11": "北京", "12": "天津", "13": "河北", "14": "山西", "15": "内蒙古",
    "21": "辽宁", "22": "吉林", "23": "黑龙江", "31": "上海", "32": "江苏",
    "33": "浙江", "34": "安徽", "35": "福建", "36": "江西", "37": "山东",
    "41": "河南", "42": "湖北", "43": "湖南", "44": "广东", "45": "广西",
    "46": "海南", "50": "重庆", "51": "四川", "52": "贵州", "53": "云南",
    "54": "西藏", "61": "陕西", "62": "甘肃", "63": "青海", "64": "宁夏",
    "65": "新疆", "71": "台湾", "81": "香港", "82": "澳门",
}

PLATFORM_NAMES = {"netease": "网易云", "qq": "QQ音乐", "kugou": "酷狗",
                  "kuwo": "酷我", "fivesing": "5sing"}


def _client(referer, cookie="", ua=UA, timeout=15):
    headers = {"User-Agent": ua, "Referer": referer}
    if cookie:
        headers["Cookie"] = cookie
    return httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)


def _int(v):
    """宽容整数化：'1.2万'/12300.0/'12,300' → int，失败返回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _ne_region(province, city=""):
    """网易云 province/city 代码 → 省名（仅标准 6 位 GB 码）。"""
    if not province:
        return ""
    p = str(province)
    if len(p) == 6 and p[:2] in NE_PROVINCES:
        return NE_PROVINCES[p[:2]]
    return ""


# ==================== 网易云 ====================

def _netease_http(pid, kind, cookie=""):
    with _client("https://music.163.com/", cookie) as c:
        if kind == "artist":
            r = c.get(f"https://music.163.com/api/v1/artist/{pid}")
            try:
                d = r.json()
            except ValueError:
                return {"platform": "netease", "error": "接口返回异常（非 JSON）"}
            artist = d.get("artist") or {}
            if not artist:
                return {"platform": "netease", "error": f"接口无歌手数据（code={d.get('code')}）"}
            rec = {
                "platform": "netease", "kind": "artist", "pid": pid,
                "name": artist.get("name") or "",
                "songs": _int(artist.get("musicSize")),
                "albums": _int(artist.get("albumSize")),
                "mvs": _int(artist.get("mvSize")),
                "sign": artist.get("briefDesc") or "",
                "avatar": artist.get("picUrl") or artist.get("img1v1Url") or "",
                "url": f"https://music.163.com/#/artist?id={pid}",
            }
            alias = artist.get("alias") or []
            if alias:
                rec["extra"] = "别名：" + "、".join(str(a) for a in alias[:3])
            # 歌手页粉丝数需登录，游客不可得（fansSize 恒空）
            return rec

        r = c.get(f"https://music.163.com/api/v1/user/detail/{pid}")
        try:
            d = r.json()
        except ValueError:
            return {"platform": "netease", "error": "接口返回异常（非 JSON）"}
        prof = d.get("profile") or {}
        if not prof:
            return {"platform": "netease", "error": f"接口无用户数据（code={d.get('code')}）"}
        rec = {
            "platform": "netease", "kind": "user", "pid": pid,
            "name": prof.get("nickname") or "",
            "fans": _int(prof.get("followeds")),
            "following": _int(prof.get("follows")),
            "works": _int(prof.get("sCount")),
            "sign": prof.get("signature") or "",
            "region": _ne_region(prof.get("province"), prof.get("city")),
            "level": _int(d.get("level")),
            "avatar": prof.get("avatarUrl") or "",
            "url": f"https://music.163.com/#/user/home?id={pid}",
        }
        extras = []
        if prof.get("artistName"):
            extras.append(f"音乐人：{prof['artistName']}")
        if _int(d.get("listenSongs")):
            extras.append(f"累计听歌 {d['listenSongs']:,}")
        if extras:
            rec["extra"] = "；".join(extras)
        return rec


def _netease_playwright(pid, kind):
    """游客 Playwright 兜底：渲染用户/歌手主页提取文本指标。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "netease", "error": "未安装 Playwright 组件"}

    if kind == "artist":
        url = f"https://music.163.com/artist?id={pid}"
    else:
        url = f"https://music.163.com/user/home?id={pid}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                                      locale="zh-CN")
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            text = ""
            for f in page.frames:
                try:
                    t = f.evaluate("document.body.innerText")
                except Exception:
                    continue
                if t and len(t) > len(text):
                    text = t
            if not text or len(text) < 50:
                return {"platform": "netease", "error": "页面未渲染出内容"}
            rec = {"platform": "netease", "kind": kind or "user", "pid": pid, "url": url}
            m = re.search(r"粉丝[^\d\n]{0,10}([\d,]+)", text)
            if m:
                rec["fans"] = _int(m.group(1))
            m = re.search(r"关注[^\d\n]{0,10}([\d,]+)", text)
            if m:
                rec["following"] = _int(m.group(1))
            m = re.search(r"(?:歌曲|单曲)[^\d\n]{0,8}([\d,]+)", text)
            if m:
                rec["songs"] = _int(m.group(1))
            m = re.search(r"专辑[^\d\n]{0,8}([\d,]+)", text)
            if m:
                rec["albums"] = _int(m.group(1))
            m = re.search(r"MV[^\d\n]{0,8}([\d,]+)", text)
            if m:
                rec["mvs"] = _int(m.group(1))
            m = re.search(r"地区[：:\s]*([^\s\n]{2,12})", text)
            if m:
                rec["region"] = m.group(1)
            if not (rec.get("fans") or rec.get("songs") or rec.get("following")):
                return {"platform": "netease", "error": "页面未解析到有效数据"}
            return rec
        finally:
            browser.close()


def _collect_netease(pid, kind):
    cookie = ""
    try:
        from auth import get_cookie
        cookie = get_cookie("netease")
    except Exception:
        pass

    r = _netease_http(pid, kind)
    if not r.get("error"):
        return r
    r = _netease_playwright(pid, kind)
    if not r.get("error"):
        return r
    if cookie:
        r = _netease_http(pid, kind, cookie=cookie)
        if not r.get("error"):
            return r
    return {"platform": "netease", "error": "游客与 Cookie 方式均失败，请稍后重试"}


# ==================== QQ音乐 ====================

def _qq_http(mid, cookie=""):
    payload = {
        "comm": {"ct": 19, "cv": 0},
        "singer": {
            "module": "music.web_singer_info_svr",
            "method": "get_singer_detail_info",
            "param": {"sort": 5, "singermid": mid, "sin": 0, "num": 1},
        },
    }
    with _client(f"https://y.qq.com/n/ryqq/singer/{mid}", cookie) as c:
        r = c.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=payload,
                   headers={"Origin": "https://y.qq.com"})
        try:
            d = r.json()
        except ValueError:
            return {"platform": "qq", "error": "接口返回异常（非 JSON）"}

    singer = d.get("singer") or {}
    data = singer.get("data") or {}
    info = data.get("singer_info") or {}
    if not info:
        return {"platform": "qq", "error": f"接口无歌手数据（code={d.get('code')}）"}

    extras = data.get("extras") or []
    listen_count = 0
    for it in extras:
        if isinstance(it, dict):
            listen_count = max(listen_count, _int(it.get("listen_count")) or 0)
    rec = {
        "platform": "qq", "kind": "singer", "pid": mid,
        "name": info.get("name") or "",
        "fans": _int(info.get("fans")),
        "songs": _int(data.get("total_song")),
        "albums": _int(data.get("total_album")),
        "mvs": _int(data.get("total_mv")),
        "listens": listen_count or None,
        "sign": data.get("singer_brief") or "",
        "avatar": f"https://y.gtimg.cn/music/photo_new/T001R300x300M000{mid}.jpg",
        "url": f"https://y.qq.com/n/ryqq/singer/{mid}",
    }
    return rec


def _collect_qq(mid, kind):
    cookie = ""
    try:
        from auth import get_cookie
        cookie = get_cookie("qq")
    except Exception:
        pass

    r = _qq_http(mid)
    if not r.get("error"):
        return r
    # QQ 歌手页对游客基本不渲染（SPA 需登录态），跳过 Playwright 直接 Cookie 重试
    if cookie:
        r = _qq_http(mid, cookie=cookie)
        if not r.get("error"):
            return r
    return {"platform": "qq", "error": "游客与 Cookie 方式均失败，请稍后重试"}


# ==================== 酷狗 ====================

def _kg_sign(params, body=None):
    """酷狗 openapi 签名：SALT + sorted('k=v') [+ body JSON] + SALT → md5。"""
    parts = [f"{k}={params[k]}" for k in sorted(params) if k != "signature"]
    plain = KG_SALT + "".join(sorted(parts))
    if body is not None:
        plain += json.dumps(body, separators=(",", ":"))
    plain += KG_SALT
    return hashlib.md5(plain.encode()).hexdigest()


def _kugou_http(sid, cookie=""):
    rec = {"platform": "kugou", "kind": "singer", "pid": str(sid),
           "url": f"https://www.kugou.com/yy/singer/home/{sid}.html"}
    with _client("https://m.kugou.com/", cookie) as c:
        # ① 歌手主页信息（名字/简介/歌曲数/专辑数/MV数/头像）
        try:
            r = c.get("https://gateway.kugou.com/mobilecdn/api/v3/singer/info",
                      params={"singerid": sid})
            d = (r.json().get("data") or {})
            if d.get("singername"):
                rec["name"] = d.get("singername")
                rec["songs"] = _int(d.get("songcount"))
                rec["albums"] = _int(d.get("albumcount"))
                rec["mvs"] = _int(d.get("mvcount"))
                rec["sign"] = d.get("profile") or d.get("intro") or ""
                img = d.get("imgurl") or ""
                rec["avatar"] = img.replace("{size}", "240") if img else ""
        except Exception:
            pass

        # ② 粉丝数（打卡榜接口，游客免签名）
        try:
            r = c.get("https://singerdiscuss.kugou.com/v3/discuss/get_fans_attendance_rank",
                      params={"singer_id": sid, "rank_type": 1})
            m = re.search(r'"fans_count"\s*:\s*(\d+)', r.text)
            if m:
                rec["fans"] = int(m.group(1))
        except Exception:
            pass

        # ③ openapi 作者信息（月听众/年听众/地区/生日/短简介，带签名）
        try:
            mid = "".join(random.choices("0123456789abcdef", k=32))
            params = {"appid": "1058", "mid": mid, "uuid": mid, "dfid": "-",
                      "clientver": "1000", "clienttime": str(int(time.time() * 1000)),
                      "srcappid": "2919"}
            body = {"author_id": int(sid), "need_song_list": 0, "page": 1, "pagesize": 1}
            params["signature"] = _kg_sign(params, body)
            r = c.post("https://gateway.kugou.com/openapi/v2/union/author/audios",
                       params=params,
                       content=json.dumps(body, separators=(",", ":")),
                       headers={"Content-Type": "application/json"})
            data = r.json().get("data") or {}
            ai = data.get("author_info") or {}
            base = ai.get("base") or {}
            extra = ai.get("extra") or {}
            fans_info = data.get("fans_info") or {}
            if base.get("author_name"):
                rec.setdefault("name", base["author_name"])
            if fans_info.get("fans") is not None:
                rec["fans"] = _int(fans_info.get("fans"))
            month = None
            year = None
            for it in (ai.get("listener_list") or []):
                if it.get("type") == "month":
                    month = _int(it.get("listener"))
                elif it.get("type") == "year":
                    year = _int(it.get("listener"))
            if month is None and ai.get("listeners"):
                for it in ai.get("listeners") or []:
                    if isinstance(it, dict) and it.get("type") == "month":
                        month = _int(it.get("listener"))
            listens = month or year
            if listens:
                rec["listens"] = listens
            region = " ".join(x for x in (base.get("country"), extra.get("areaname")) if x)
            if region:
                rec["region"] = region
            if base.get("birthday"):
                rec["birthday"] = str(base.get("birthday"))
            if extra.get("short_intro"):
                rec["sign"] = extra["short_intro"]
        except Exception:
            pass

    if not rec.get("name"):
        return {"platform": "kugou", "error": "接口无歌手数据（可能歌手ID无效）"}
    return rec


def _kugou_playwright(sid):
    """游客 Playwright 兜底：渲染 m.kugou.com 歌手页提取粉丝/月听众。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "kugou", "error": "未安装 Playwright 组件"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=MOBILE_UA, viewport={"width": 390, "height": 844},
                                      locale="zh-CN", is_mobile=True, has_touch=True)
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(f"https://m.kugou.com/singer/{sid}",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            try:
                text = page.evaluate("document.body.innerText")
            except Exception:
                text = ""
            if not text or len(text) < 50:
                return {"platform": "kugou", "error": "页面未渲染出内容"}
            rec = {"platform": "kugou", "kind": "singer", "pid": str(sid),
                   "url": f"https://www.kugou.com/yy/singer/home/{sid}.html"}
            for pat, key in [(r"粉丝[^\d\n]{0,8}([\d.万亿]+)", "fans"),
                             (r"月听众[^\d\n]{0,8}([\d.万亿]+)", "listens"),
                             (r"歌曲[^\d\n]{0,8}([\d.万亿]+)", "songs"),
                             (r"专辑[^\d\n]{0,8}([\d.万亿]+)", "albums"),
                             (r"MV[^\d\n]{0,8}([\d.万亿]+)", "mvs")]:
                m = re.search(pat, text)
                if m:
                    rec[key] = _num_cn(m.group(1))
            if not (rec.get("fans") or rec.get("listens") or rec.get("songs")):
                return {"platform": "kugou", "error": "页面未解析到有效数据"}
            return rec
        finally:
            browser.close()


def _num_cn(s):
    """'1.2万'/'3.4亿' → int。"""
    s = str(s).strip().replace(",", "")
    try:
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _collect_kugou(sid, kind):
    cookie = ""
    try:
        from auth import get_cookie
        cookie = get_cookie("kugou")
    except Exception:
        pass

    r = _kugou_http(sid)
    if not r.get("error"):
        # HTTP 无粉丝/月听众（粉丝接口已下线）→ Playwright 渲染补齐缺失字段
        if not (r.get("fans") or r.get("listens")):
            pw_rec = _kugou_playwright(sid)
            for k, v in pw_rec.items():
                if v and not r.get(k):
                    r[k] = v
        return r
    r = _kugou_playwright(sid)
    if not r.get("error"):
        return r
    if cookie:
        r = _kugou_http(sid, cookie=cookie)
        if not r.get("error"):
            return r
    return {"platform": "kugou", "error": "游客与 Cookie 方式均失败，请稍后重试"}


# ==================== 酷我 ====================

def _kuwo_http(rid, cookie=""):
    with _client("https://www.kuwo.cn/", cookie) as c:
        r = c.get(f"https://www.kuwo.cn/singer_detail/{rid}")
    html = r.text or ""
    idx = html.find("singerInfo:")
    if idx < 0:
        return {"platform": "kuwo", "error": "页面无歌手数据（可能歌手ID无效）"}
    start = html.find("{", idx)
    depth, end = 0, -1
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= start:
        return {"platform": "kuwo", "error": "页面数据解析失败"}
    raw = html[start:end]
    # NUXT 压缩产物里部分值是函数参数引用（pic:d / isStar:b）——
    # 单字符裸标识符按空串处理，否则 json.loads 失败
    raw = re.sub(r':\s*[A-Za-z_$]\s*(?=[,}])', ': ""', raw)
    try:
        fixed = re.sub(r'([{,])(\w+)\s*:', r'\1"\2":', raw)
        info = json.loads(fixed)
    except json.JSONDecodeError:
        return {"platform": "kuwo", "error": "页面数据解析失败"}

    def gv(*keys):
        for k in keys:
            v = info.get(k)
            if v not in (None, "", "-", '""'):
                return v
        return None

    name = gv("name", "artistName")
    if not name:
        return {"platform": "kuwo", "error": "页面无歌手名（可能歌手ID无效）"}
    import html as _html
    desc = ""
    if gv("info"):
        desc = _html.unescape(str(gv("info"))).replace("\xa0", " ").strip()
    rec = {
        "platform": "kuwo", "kind": "singer", "pid": str(rid),
        "name": str(name),
        "fans": _num_cn(gv("artistFans", "fansNum")),
        "songs": _num_cn(gv("musicNum", "songNum")),
        "albums": _num_cn(gv("albumNum")),
        "mvs": _num_cn(gv("mvNum")),
        "sign": desc or str(gv("gener") or ""),
        "avatar": gv("pic300") or gv("pic70") or "",
        "url": f"https://www.kuwo.cn/singer_detail/{rid}",
    }
    region = " ".join(str(x) for x in (gv("country"), gv("birthplace")) if x)
    if region:
        rec["region"] = region
    if gv("birthday"):
        rec["birthday"] = str(gv("birthday"))
    extras = []
    for label, key in [("性别", "gener"), ("星座", "constellation"), ("身高", "tall"),
                       ("体重", "weight"), ("语言", "language"), ("别名", "aartist")]:
        v = gv(key)
        if v:
            extras.append(f"{label}：{v}")
    if extras:
        rec["extra"] = "；".join(extras)
    return rec


def _kuwo_playwright(rid):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "kuwo", "error": "未安装 Playwright 组件"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                                      locale="zh-CN")
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(f"https://www.kuwo.cn/singer_detail/{rid}",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            try:
                text = page.evaluate("document.body.innerText")
            except Exception:
                text = ""
            if not text or len(text) < 50:
                return {"platform": "kuwo", "error": "页面未渲染出内容"}
            rec = {"platform": "kuwo", "kind": "singer", "pid": str(rid),
                   "url": f"https://www.kuwo.cn/singer_detail/{rid}"}
            for pat, key in [(r"粉丝[^\d\n]{0,8}([\d.万亿]+)", "fans"),
                             (r"(?:歌曲|单曲)[^\d\n]{0,8}([\d.万亿]+)", "songs"),
                             (r"专辑[^\d\n]{0,8}([\d.万亿]+)", "albums"),
                             (r"MV[^\d\n]{0,8}([\d.万亿]+)", "mvs")]:
                m = re.search(pat, text)
                if m:
                    rec[key] = _num_cn(m.group(1))
            if not (rec.get("fans") or rec.get("songs")):
                return {"platform": "kuwo", "error": "页面未解析到有效数据"}
            return rec
        finally:
            browser.close()


def _collect_kuwo(rid, kind):
    cookie = ""
    try:
        from auth import get_cookie
        cookie = get_cookie("kuwo")
    except Exception:
        pass

    r = _kuwo_http(rid)
    if not r.get("error"):
        return r
    r = _kuwo_playwright(rid)
    if not r.get("error"):
        return r
    if cookie:
        r = _kuwo_http(rid, cookie=cookie)
        if not r.get("error"):
            return r
    return {"platform": "kuwo", "error": "游客与 Cookie 方式均失败，请稍后重试"}


# ==================== 5sing ====================

def _fivesing_http(uid, cookie=""):
    with _client("https://5sing.kugou.com/", cookie) as c:
        r = c.get(f"http://5sing.kugou.com/{uid}/default.html")
    html = r.text or ""

    # 数字统计在 <div class="number" id="totalfans"><a href="/{uid}/fans/1.html">1394</a>，
    # 必须锚定 id 后第一个数字（直接全局找 key 会误配 href 里的 uid）
    def num(key):
        m = re.search(rf'id="{key}"[^>]*>\s*(?:<a[^>]*>)?\s*([\d,]+)', html)
        return int(m.group(1).replace(",", "")) if m else None

    name = None
    m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r'class="nick"[^>]*>([^<]+)<', html)
        if m:
            name = m.group(1).strip()
    if not name:
        t = re.search(r"<title>([^<]+)</title>", html)
        if t:
            title = t.group(1).strip().split("_")[0].split("-")[0].strip()
            if title and title not in ("5sing", "中国原创音乐基地"):
                name = title
    if not name and "totalfans" not in html:
        return {"platform": "fivesing", "error": "页面无用户数据（可能用户ID无效）"}

    rec = {
        "platform": "fivesing", "kind": "user", "pid": str(uid),
        "name": name or "",
        "fans": num("totalfans"),
        "following": num("totalfriend"),
        "listens": num("totalrq"),
        "url": f"https://5sing.kugou.com/{uid}/default.html",
    }
    # 个人介绍：<h3>…个人介绍</h3><p>&nbsp;&nbsp; 文本</p>
    m = re.search(r'个人介绍</h3>\s*<p>([^<]{2,300})</p>', html)
    if m:
        import html as _html
        rec["sign"] = _html.unescape(m.group(1)).replace("\xa0", " ").strip()
    return rec


def _fivesing_playwright(uid):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"platform": "fivesing", "error": "未安装 Playwright 组件"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                                      locale="zh-CN")
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(f"https://5sing.kugou.com/{uid}/default.html",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            try:
                text = page.evaluate("document.body.innerText")
            except Exception:
                text = ""
            if not text or len(text) < 30:
                return {"platform": "fivesing", "error": "页面未渲染出内容"}
            rec = {"platform": "fivesing", "kind": "user", "pid": str(uid),
                   "url": f"https://5sing.kugou.com/{uid}/default.html"}
            for pat, key in [(r"粉丝[^\d\n]{0,8}([\d,]+)", "fans"),
                             (r"关注[^\d\n]{0,8}([\d,]+)", "following"),
                             (r"人气[^\d\n]{0,8}([\d,]+)", "listens")]:
                m = re.search(pat, text)
                if m:
                    rec[key] = _int(m.group(1))
            t = re.search(r"^([^\n]{1,30})的", text)
            if t:
                rec["name"] = t.group(1).strip()
            if not (rec.get("fans") or rec.get("listens")):
                return {"platform": "fivesing", "error": "页面未解析到有效数据"}
            return rec
        finally:
            browser.close()


def _collect_fivesing(uid, kind):
    r = _fivesing_http(uid)
    if not r.get("error"):
        return r
    r = _fivesing_playwright(uid)
    if not r.get("error"):
        return r
    # 5sing 无 Cookie 登录通道，游客即全部能力
    return {"platform": "fivesing", "error": "游客方式失败，请稍后重试"}


# ==================== 统一入口 ====================

_COLLECTORS = {
    "netease": _collect_netease,
    "qq": _collect_qq,
    "kugou": _collect_kugou,
    "kuwo": _collect_kuwo,
    "fivesing": _collect_fivesing,
}


def collect(platform, pid, kind=""):
    """采集一个音乐平台账号/歌手。platform ∈ 五平台，pid 为平台内 ID，
    kind ∈ {"user", "artist", "singer", ""}（空=平台只有一种目标）。"""
    fn = _COLLECTORS.get(platform)
    if not fn:
        return {"platform": platform or "unknown", "error": f"未知平台 {platform}"}
    try:
        return fn(str(pid).strip(), (kind or "").strip())
    except Exception as e:
        return {"platform": platform, "error": f"采集异常：{e}"}


# ==================== 名称搜索（设置页“搜索账号”用） ====================

def search_account(platform, keyword):
    """按名称搜索平台账号/歌手，返回 [{name, id, kind, url, fans}]。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    try:
        if platform == "netease":
            return _search_netease(kw)
        if platform == "qq":
            return _search_qq(kw)
        if platform == "kugou":
            return _search_kugou(kw)
        if platform == "kuwo":
            return _search_kuwo(kw)
    except Exception:
        pass
    return []


def _search_netease(kw):
    out = []
    with _client("https://music.163.com/") as c:
        # 歌手优先（音乐人主要形态）
        r = c.get("https://music.163.com/api/search/get/web",
                  params={"type": 100, "s": kw, "limit": 8, "offset": 0})
        for a in (r.json().get("result") or {}).get("artists") or []:
            out.append({
                "name": a.get("name") or "", "id": a.get("id"), "kind": "artist",
                "url": f"https://music.163.com/#/artist?id={a.get('id')}",
                "fans": _int(a.get("fansSize")),
            })
        # 用户
        r = c.get("https://music.163.com/api/search/get/web",
                  params={"type": 1002, "s": kw, "limit": 8, "offset": 0})
        for u in (r.json().get("result") or {}).get("userprofiles") or []:
            out.append({
                "name": u.get("nickname") or "", "id": u.get("userId"), "kind": "user",
                "url": f"https://music.163.com/#/user/home?id={u.get('userId')}",
                "fans": _int(u.get("followeds")),
            })
    return out[:12]


def _search_qq(kw):
    with _client("https://y.qq.com/") as c:
        r = c.get("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                  params={"w": kw, "format": "json", "p": 1, "n": 10, "type": 1})
        lst = ((r.json().get("data") or {}).get("song") or {}).get("list") or []
    seen, out = set(), []
    for s in lst:
        for si in s.get("singer") or []:
            mid = si.get("mid")
            if mid and mid not in seen:
                seen.add(mid)
                out.append({
                    "name": si.get("name") or "", "id": mid, "kind": "singer",
                    "url": f"https://y.qq.com/n/ryqq/singer/{mid}", "fans": None,
                })
    return out[:12]


def _search_kugou(kw):
    with _client("https://m.kugou.com/") as c:
        # mobilecdn 走 https 证书不匹配（CN 不含该域名），必须 http
        r = c.get("http://mobilecdn.kugou.com/api/v3/search/singer",
                  params={"keyword": kw, "page": 1, "pagesize": 12, "format": "json"})
        lst = r.json().get("data") or []
    return [{
        "name": s.get("singername") or "", "id": s.get("singerid"), "kind": "singer",
        "url": f"https://www.kugou.com/yy/singer/home/{s.get('singerid')}.html",
        "fans": _int(s.get("fanscount")),
    } for s in lst if s.get("singerid")]


def _search_kuwo(kw):
    with _client("https://www.kuwo.cn/") as c:
        r = c.get("https://www.kuwo.cn/search/searchMusicBykeyWord",
                  params={"vipver": 1, "client": "kt", "ft": "music", "cluster": 0,
                          "strategy": 2012, "encoding": "utf8", "rformat": "json",
                          "mobi": 1, "issubtitle": 1, "show_copyright_off": 1,
                          "pn": 0, "rn": 10, "all": kw})
        lst = r.json().get("abslist") or []
    seen, out = set(), []
    for s in lst:
        rid = s.get("ARTISTID")
        if rid and rid not in seen:
            seen.add(rid)
            out.append({
                "name": s.get("ARTIST") or "", "id": rid, "kind": "singer",
                "url": f"https://www.kuwo.cn/singer_detail/{rid}", "fans": None,
            })
    return out[:12]
