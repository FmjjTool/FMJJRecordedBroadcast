"""Cookie 身份验证：用已保存 Cookie 拉取当前登录用户名（12 平台）。

probe(platform) 返回：
    {"ok": True,  "username": "昵称"}  Cookie 有效且拿到用户名
    {"ok": True,  "username": ""}      Cookie 有效但拿不到用户名
    {"ok": None,  "username": "用户ID xxx"} 无法远程验证，展示 Cookie 中的 ID
    {"ok": None,  "username": ""}      该平台暂不支持自动验证
    {"ok": False, "username": ""}      Cookie 已失效/未登录

verify(platform, cookie) 是同一套判定，但用传入的 Cookie 串（Cookie 管理弹窗
捕获过程中即时校验用，不落盘）。
"""

from __future__ import annotations

from urllib.parse import urlencode

try:
    from . import nick_cache
except ImportError:  # 直接以顶层模块方式导入（个别脚本 sys.path 指到 tracker/）
    import nick_cache  # type: ignore[no-redef]


def _cookie_field(cookie: str, name: str) -> str:
    for part in (cookie or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _headers(cookie: str) -> dict:
    from multi_downloader import UA_PC

    return {"User-Agent": UA_PC, "Cookie": cookie}


def probe(platform: str) -> dict:
    import auth

    # whoami 仅服务于 Cookie 管理弹窗的"状态展示/验证"——读已存储 Cookie，
    # 不受总开关限制（总开关只控制采集功能是否用 Cookie）
    if platform == "netease":
        cookie = (auth.get_netease_cookie_stored() or "").strip()
    else:
        cookie = (auth.load_cookies().get(platform, "") or "").strip()
    return verify(platform, cookie)


def verify(platform: str, cookie: str) -> dict:
    """用指定 Cookie 串验证登录态（不读存储；弹窗捕获中即时校验用）。"""
    cookie = (cookie or "").strip()
    if not cookie:
        return {"ok": False, "username": ""}
    fn = _VERIFIERS.get(platform)
    if fn is None:
        return {"ok": None, "username": ""}  # 该平台暂不支持自动验证
    try:
        return fn(cookie)
    except Exception:
        return {"ok": None, "username": ""}


def _tb_nick(cookie: str) -> str:
    """淘宝 Cookie 内的昵称字段（tracknick/dnk/_nk_ 为 URL 编码昵称）。"""
    from urllib.parse import unquote

    for name in ("tracknick", "dnk", "_nk_"):
        v = _cookie_field(cookie, name)
        if v:
            v = unquote(v).strip()
            if v:
                return v
    return ""


def _taobao(cookie: str) -> dict:
    # 远程真实验证：复用商品采集同款 mtop 接口（wandering.get，需登录会话）——
    #   ret 含 SUCCESS → 登录有效（商品采集可用）；FAIL_SYS_SESSION_EXPIRED → 登录失效；
    #   RGV587 等风控假响应 → 无法判定（ok=None，可稍后重试）。
    # 注意淘宝会话分层：网页层（i.taobao.com 跳登录）与 mtop 层独立，
    # 商品采集只依赖 mtop 层，故以 mtop 接口为准。
    unb = _cookie_field(cookie, "unb")
    nick = _tb_nick(cookie)
    id_text = nick or (f"淘宝ID {unb}" if unb else "")
    fallback = {"ok": None, "username": id_text}
    try:
        from shopping import _taobao_get_token, _taobao_mtop, get_verify_seller_id
        sid = get_verify_seller_id()
        if not sid:
            return fallback
        tk, cd = _taobao_get_token(cookie)
        data = {"direction": 1, "userId": sid, "pageSize": 20,
                "needPreNew": True, "id": 0, "curPage": 1, "preItemId": 0}
        resp = _taobao_mtop("mtop.taobao.wireless.shop.index.wandering.get",
                            "1.0", data, cd, tk)
        ret = ";".join(str(x) for x in (resp.get("ret") or []))
        if "SUCCESS" in ret:
            return {"ok": True, "username": id_text}
        if "SESSION_EXPIRED" in ret or "NEED_LOGIN" in ret:
            return {"ok": False, "username": ""}
        return fallback
    except Exception:
        return fallback


def _xianyu(cookie: str) -> dict:
    from platforms.xianyu import verify_login
    return verify_login(cookie)


def _xhs_state(html: str) -> dict:
    """取页面内嵌的 window.__INITIAL_STATE__（复用笔记解析的花括号配平逻辑）。"""
    try:
        from platforms.xiaohongshu import _balanced_json
        d = _balanced_json(html, r"window\.__INITIAL_STATE__")
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _xiaohongshu(cookie: str) -> dict:
    # 登录态在探索页 SSR 的 user.loggedIn / user.userInfo 里；
    # 首页（www.xiaohongshu.com/）是 JS 壳，没有 __INITIAL_STATE__，
    # 旧实现只抓首页再正则，永远解析不到 → 恒显示「无法验证」
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://www.xiaohongshu.com/"
    for url in ("https://www.xiaohongshu.com/explore",
                "https://www.xiaohongshu.com/"):
        try:
            html = requests.get(url, headers=h, timeout=15).text
        except Exception:
            continue
        st = _xhs_state(html)
        if not st:
            continue
        user = st.get("user") or {}
        info = user.get("userInfo") or {}
        nick = (info.get("nickname") or info.get("nickName")
                or info.get("nick_name") or "").strip()
        guest = info.get("guest")
        if user.get("loggedIn") is True and guest is not True:
            return {"ok": True, "username": nick}
        if user.get("loggedIn") is False:
            return {"ok": False, "username": ""}
        if nick and guest is False:
            return {"ok": True, "username": nick}
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}  # 页面没拿到 state：无法判定


def _bili(cookie: str) -> dict:
    import requests

    d = requests.get("https://api.bilibili.com/x/web-interface/nav",
                     headers=_headers(cookie), timeout=10).json()
    data = d.get("data") or {}
    if d.get("code") == 0 and data.get("isLogin"):
        return {"ok": True, "username": data.get("uname") or ""}
    return {"ok": False, "username": ""}


def _douyin(cookie: str) -> dict:
    import requests

    from multi_downloader import _douyin_abogus

    query = urlencode({"device_platform": "webapp", "aid": "6383", "version_code": "170808"})
    a_bogus = _douyin_abogus(query)
    if not a_bogus:
        return {"ok": None, "username": ""}
    url = f"https://www.douyin.com/aweme/v1/web/user/profile/self/?{query}&a_bogus={a_bogus}"
    h = _headers(cookie)
    h["Referer"] = "https://www.douyin.com/"
    d = requests.get(url, headers=h, timeout=15).json()
    user = d.get("user")
    if d.get("status_code") == 0 and user:
        return {"ok": True, "username": user.get("nickname") or ""}
    if d.get("status_code") in (8, "8"):
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}


def _weibo(cookie: str) -> dict:
    import requests

    d = requests.get("https://m.weibo.cn/api/config",
                     headers=_headers(cookie), timeout=10).json()
    data = d.get("data") or {}
    if not data.get("login"):
        return {"ok": False, "username": ""}
    user = data.get("user") or {}
    name = (user.get("screen_name") or "").strip()
    uid = data.get("uid") or user.get("id")
    if not name and uid:
        # 2026-10 实测 config 里 user 恒为 null（只剩 uid），昵称改问 users/show
        try:
            u = requests.get(f"https://m.weibo.cn/api/users/show?uid={uid}",
                             headers=_headers(cookie), timeout=10).json()
            name = ((u.get("data") or {}).get("screen_name") or "").strip()
        except Exception:
            name = ""
    return {"ok": True, "username": name}


def _netease(cookie: str) -> dict:
    import requests

    d = requests.post("https://music.163.com/api/nuser/account/get",
                      headers=_headers(cookie), data={}, timeout=10).json()
    prof = d.get("profile") or {}
    if d.get("code") == 200 and prof.get("nickname"):
        return {"ok": True, "username": prof["nickname"]}
    return {"ok": False, "username": ""}


def _find_nick(obj, depth=0):
    """QQ 音乐用户接口字段名不定，浅层深度搜索昵称。"""
    if depth > 3:
        return ""
    if isinstance(obj, dict):
        for k in ("nick", "nickname", "name", "uname"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            r = _find_nick(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_nick(v, depth + 1)
            if r:
                return r
    return ""


def _qq_hash33(skey: str) -> int:
    """QQ 音乐的 g_tk（网页内嵌页公参）：h = h*33 + ord(ch)，初值 5381。"""
    h = 5381
    for ch in (skey or ""):
        h = (h + (h << 5) + ord(ch)) & 0xFFFFFFFF
    return h & 0x7FFFFFFF


def _qq_account(cookie: str) -> tuple[str, str, int]:
    """从 Cookie 解出 (musicid, musickey, tmeLoginType)。

    QQ 登录是 QQ 号，微信登录是 0 开头的 musicid（可能带 o 前缀），两种都要认。
    """
    uid = (_cookie_field(cookie, "uin") or _cookie_field(cookie, "qqmusic_uin")
           or _cookie_field(cookie, "wxuin") or "").strip()
    if uid[:1] in ("o", "O"):
        uid = uid[1:]
    uid = uid.lstrip("0") or uid
    key = ""
    for name in ("qm_keyst", "qqmusic_key", "music_key", "musickey"):
        key = _cookie_field(cookie, name).strip()
        if key:
            break
    login_type = 1 if (_cookie_field(cookie, "tmeLoginType").strip() == "1"
                       or key.startswith("W_X")) else 2
    return uid, key, login_type


def _qq_profile(cookie: str, uid: str, key: str) -> dict | None:
    """主页资料 CGI：未登录恒回 code=1000，带登录态返回 creator（含昵称）→ 可判登录。

    比 musicu 用户接口轻，且微信/QQ 两种登录都认（musicu 对微信票常回 500003）。
    """
    import requests

    g_tk = _qq_hash33(key)
    params = {"format": "json", "cid": "205360838", "userid": uid, "uin": uid,
              "reqfrom": "1", "reqtype": "0", "ct": "20", "cv": "2276",
              "platform": "yqq.json",
              "g_tk": g_tk, "g_tk_new_20200303": g_tk}
    h = _headers(cookie)
    h["Referer"] = f"https://y.qq.com/n/ryqq/profile?uin={uid}"
    d = requests.get("https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg",
                     params=params, headers=h, timeout=10).json()
    if not isinstance(d, dict):
        return None
    if d.get("code") == 0:
        creator = (d.get("data") or {}).get("creator") or {}
        return {"ok": True, "username": str(creator.get("nick") or "").strip()}
    if d.get("code") in (1000, -1000) or str(d.get("subcode")) in ("1000", "-1000"):
        return {"ok": False, "username": ""}
    return None


def _qq_musicu(cookie: str, uid: str, key: str, login_type: int) -> dict | None:
    """musicu 用户信息接口：comm 必须带 uin/authst/tmeLoginType，否则恒 500003。"""
    import requests

    comm = {"ct": 24, "cv": 0, "format": "json", "tmeAppID": "qqmusic",
            "tmeLoginType": login_type, "uin": uid, "qq": uid, "authst": key}
    payload = {"comm": comm,
               "req_0": {"module": "music.UserInfoCgiSvr",
                         "method": "GetUserInfo", "param": {}}}
    h = _headers(cookie)
    h["Referer"] = "https://y.qq.com/"
    d = requests.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=payload,
                      headers=h, timeout=10).json()
    req = d.get("req_0") or {}
    if req.get("code") == 0:
        return {"ok": True, "username": _find_nick(req.get("data") or {})}
    if req.get("code") in (500003, 1000, -1000) or d.get("code") in (1000, -1000):
        return {"ok": False, "username": ""}
    return None


def _qq(cookie: str) -> dict:
    uid, key, login_type = _qq_account(cookie)
    # 扫码登录时换票接口给的昵称（微信票问不到用户信息，只有这一份）
    cached = nick_cache.get("qq", uid) if uid else ""
    if not uid or not key:
        # 只有 uin、没有通行令牌：凭证不完整，远程一定问不出登录态
        shown = cached or (f"QQ {uid}" if uid else "")
        return {"ok": None, "username": shown}

    def _run(probe):
        try:
            return probe()
        except Exception:
            return None

    def _fill(info):
        if not info.get("username"):
            info["username"] = cached
        return info

    profile = _run(lambda: _qq_profile(cookie, uid, key))
    if profile is not None and profile.get("ok") is True:
        return _fill(profile)
    musicu = _run(lambda: _qq_musicu(cookie, uid, key, login_type))
    if musicu is not None and musicu.get("ok") is True:
        return _fill(musicu)
    # 主页资料接口是真正的登录门禁（未登录恒回 1000），它的否定结论可信；
    # musicu 用户接口对新版/微信登录的票常回 500003，单独否定时只报「无法验证」，
    # 免得把好好的绿标误判成红标。
    if profile is not None:
        return profile
    return {"ok": None, "username": cached or f"QQ {uid}"}


def _kuaishou(cookie: str) -> dict:
    # graphql userInfo 已失效（2026-10 实测登录态也返回 null）——改为 Cookie
    # 票据判定：web_st(30天)/webday7_st(7天) 存在即登录态；QQ 授权残留的
    # ptnick_XXX 可提昵称；userId 游客也有不能单独作准
    import urllib.parse

    for key in ("kuaishou.server.web_st", "kuaishou.server.webday7_st"):
        if _cookie_field(cookie, key):
            import re
            m = re.search(r"ptnick_\d+=([^;]+)", cookie)
            if m:
                raw = m.group(1).strip()
                nick = ""
                # 实测 ptnick 是纯 hex 的 UTF-8（如 e6ad86e68595），也可能是 URL 编码
                try:
                    if re.fullmatch(r"[0-9a-fA-F]+", raw) and len(raw) % 6 == 0:
                        nick = bytes.fromhex(raw).decode("utf-8", "ignore")
                    elif raw.startswith("%") or raw.startswith("%25"):
                        nick = urllib.parse.unquote(raw)
                except Exception:
                    nick = ""
                if nick:
                    return {"ok": True, "username": nick}
            uid = _cookie_field(cookie, "userId")
            cached = nick_cache.get("kuaishou", uid) if uid else ""
            if cached:
                return {"ok": True, "username": cached}
            return {"ok": True, "username": f"快手用户 {uid}" if uid else "已登录"}
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://www.kuaishou.com/"
    q = {"query": "query { userInfo { id name } }", "variables": {}}
    try:
        d = requests.post("https://www.kuaishou.com/graphql", json=q, headers=h, timeout=10).json()
    except Exception:
        d = {}
    user = ((d.get("data") or {}).get("userInfo")) or {}
    if user.get("name"):
        return {"ok": True, "username": user["name"]}
    if user.get("id"):
        return {"ok": True, "username": f"ID {user['id']}"}
    return {"ok": False, "username": ""}


def _kuwo(cookie: str) -> dict:
    import requests

    # 远程接口需 Cookie 内的 kw_token 作 csrf——登录 Cookie 不一定带（浏览器
    # 捕获时可能未存），此时接口恒回 "The request is illegal"，不能当未登录
    token = _cookie_field(cookie, "kw_token")
    if token:
        h = _headers(cookie)
        h["csrf"] = token
        h["Referer"] = "https://www.kuwo.cn/"
        try:
            d = requests.get("https://www.kuwo.cn/api/www/user/account/info?httpsStatus=1",
                             headers=h, timeout=10).json()
            data = d.get("data") or {}
            name = data.get("username") or data.get("name") or data.get("nickName")
            if d.get("success") and name:
                return {"ok": True, "username": str(name)}
        except Exception:
            pass
    # Cookie 内登录凭据直接判定：uname3 为 JS escape 编码的昵称（可能再被
    # URL 编码一层，如 %25u6B46%25u6155 → %u6B46%u6155 → 汉字）
    uid = _cookie_field(cookie, "userid")
    nick = _kw_uname3(cookie)
    if uid and nick:
        return {"ok": True, "username": nick}
    account = _cookie_field(cookie, "username")
    if uid and account:
        return {"ok": True, "username": account}
    if uid:
        return {"ok": None, "username": f"用户ID {uid}"}
    return {"ok": None, "username": ""}


def _kw_uname3(cookie: str) -> str:
    """解码酷我 uname3（JS escape 汉字昵称）；无该字段或解不出返回空。"""
    import re
    from urllib.parse import unquote

    v = _cookie_field(cookie, "uname3")
    if not v:
        return ""
    for _ in range(2):  # 至多剥两层 URL 编码（%25u… → %u… → 汉字由正则接手）
        d = unquote(v)
        if d == v:
            break
        v = d
    m = re.findall(r"%u([0-9a-fA-F]{4})", v)
    if m:
        return "".join(chr(int(x, 16)) for x in m)
    return v if v and "%" not in v else ""


def _kugou_nick(v: str) -> str:
    """解码酷狗 NickName（URL 百分号编码；老账号可能是 %uXXXX 或 GBK）。"""
    import re
    from urllib.parse import unquote

    v = (v or "").strip()
    if not v:
        return ""
    m = re.findall(r"%u([0-9a-fA-F]{4})", v)  # JS escape 编码（酷狗老账号）
    if m:
        return "".join(chr(int(x, 16)) for x in m)
    for _ in range(2):  # 至多剥两层（%25E6… → %E6… → 汉字）
        if "%" not in v:
            break
        for enc in ("utf-8", "gbk"):
            try:
                d = unquote(v, encoding=enc, errors="strict")
            except Exception:
                continue
            if d and d != v:
                v = d
                break
        else:
            break
    return v


def _kugoo_fields(cookie: str) -> dict:
    """解析酷狗登录态 Cookie `KuGoo`（形如 KugooID=123&NickName=%E6%AD%8C&Pic=…）。

    酷狗网页端 cmhead.min.js 的 islogined() 就是读这个 Cookie，登录态和昵称
    全在里面，并不存在独立的 KugooID Cookie——旧实现只找 KugooID 字段，导致
    明明登录了也判成"无法验证"。
    """
    raw = _cookie_field(cookie, "KuGoo")
    if not raw:
        return {}
    out = {}
    for part in raw.split("&"):
        k, _, v = part.partition("=")
        k = k.strip()
        if k:
            out[k] = v
    return out


def _kugou(cookie: str) -> dict:
    # 远程用户接口全被拦（userservice 404/passport 403/mobilecdn Access Deny），
    # 只能解析 Cookie：KuGoo（登录态本体，含 KugooID/NickName）→ 老式 KugooID+t
    kg = _kugoo_fields(cookie)
    uid = (kg.get("KugooID") or kg.get("KugouID")
           or _cookie_field(cookie, "KugooID")
           or _cookie_field(cookie, "KugouID")
           or _cookie_field(cookie, "userid"))
    nick = (_kugou_nick(kg.get("NickName") or kg.get("UserName") or "")
            or _kugou_nick(_cookie_field(cookie, "UserName")))
    if nick and nick != f"kgopen{uid}":
        return {"ok": True, "username": nick}
    if uid and (kg or _cookie_field(cookie, "t")):
        return {"ok": True, "username": f"用户ID {uid}"}
    if uid:
        return {"ok": None, "username": f"用户ID {uid}"}
    # 连 uid / KuGoo 都没有 = 明确的游客态（不是"无法验证"）
    return {"ok": False, "username": ""}


def _five_checkstatus(cookie: str) -> dict | None:
    """5sing 站内登录态接口（页面自己用的那个）。不可达返回 None。"""
    import requests

    h = _headers(cookie)
    h["Referer"] = "http://5sing.kugou.com/"
    for url in ("http://service.5sing.kugou.com/user/checkstatus",
                "https://5sservice.kugou.com/user/checkstatus"):
        try:
            d = requests.get(url, headers=h, timeout=12).json()
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if not d.get("isLogin"):
            return {"ok": False, "username": ""}
        nick = ""
        for k in ("NickName", "nickName", "nickname", "UserName", "userName",
                  "username", "name"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                nick = v.strip()
                break
        if not nick:
            uid = d.get("UserId") or d.get("userid")
            if uid:
                nick = f"用户ID {uid}"
        if not nick:
            nick = _find_nick(d)  # 返回体可能把用户信息套在子对象里
        return {"ok": True, "username": nick}
    return None


def _fivesing(cookie: str) -> dict:
    """5sing 登录态：只能问站内 user/checkstatus 接口。

    5sing 的登录态在服务端会话里（sl-session 这类 HttpOnly 会话 Cookie 游客也有），
    Cookie 名上没有可靠的"登录特征"；个人中心页 /my/ 的 HTML 是 JS 渲染的空壳，
    游客标记恒在，不能用它判断——只有 checkstatus 是页面自己用的登录态接口。
    """
    r = _five_checkstatus(cookie)
    if r is not None:
        return r
    return {"ok": None, "username": ""}  # 接口不可达：无法判定（不当成未登录）


def _tcdoc(cookie: str) -> dict:
    """腾讯文档：user_info 接口的 login 字段（游客态也可能带 uid Cookie，不能只看名字）。"""
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://docs.qq.com/desktop"
    d = requests.get("https://docs.qq.com/cgi-bin/online_docs/user_info?u=",
                     headers=h, timeout=10).json()
    if not isinstance(d, dict):
        return {"ok": None, "username": ""}
    if d.get("login") is True:
        nick = (d.get("nick") or d.get("wx_nick") or d.get("qq_nick") or "").strip()
        return {"ok": True, "username": nick}
    # 票失效时接口不带 login 字段，只回 12100 /「尚未登录」
    if d.get("login") is False or d.get("retcode") == 12100 or d.get("cgicode") == 12100:
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}


def _quark(cookie: str) -> dict:
    """夸克网盘：account/info 返回昵称即已登录；票失效时 code 非 OK 且无昵称。"""
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://pan.quark.cn/"
    d = requests.get("https://pan.quark.cn/account/info?fr=pc&platform=pc",
                     headers=h, timeout=10).json()
    if not isinstance(d, dict):
        return {"ok": None, "username": ""}
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    nick = str((data or {}).get("nickname") or "").strip()
    if nick:
        return {"ok": True, "username": nick}
    # 未登录时接口 code 为 "KO"/非 OK，data 里没有 nickname
    if str(d.get("code") or "").upper() in ("KO", "ERROR", "401") or d.get("code") == 401:
        return {"ok": False, "username": ""}
    return {"ok": False, "username": ""}


def _yun139(cookie: str) -> dict:
    """移动云盘：用签名接口列一次根目录，能列通就算登录有效。

    它的用户信息接口同样要 mcloud-sign，所以拿「读一次目录」当探活；
    展示名用 Cookie 里已打码的账号（ORCHES-I-ACCOUNT-SIMPLIFY，形如 138****8113）。
    """
    from urllib.parse import unquote

    account = ""
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == "ORCHES-I-ACCOUNT-SIMPLIFY":
            account = unquote(value.strip())
            break
    try:
        from toolbox.yun139.api import Yun139Client, Yun139Error
    except Exception:
        return {"ok": None, "username": account}
    try:
        Yun139Client(cookie).list_dir("/")
        return {"ok": True, "username": account}
    except Yun139Error as exc:
        return {"ok": False, "username": ""} if exc.is_auth else {"ok": None, "username": account}
    except Exception:
        return {"ok": None, "username": account}


def _baidu(cookie: str) -> dict:
    """百度网盘：gettemplatevariable 能拿到 bdstoken 即已登录（顺带取用户名）。

    抓不到 bdstoken 但也没明确报错时返回 ok=None，让界面显示「无法验证」而不是误判失败。
    """
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://pan.baidu.com/disk/main"
    d = requests.get(
        "https://pan.baidu.com/api/gettemplatevariable",
        params={"clienttype": 0, "app_id": 250528, "web": 1,
                "fields": '["bdstoken","username"]'},
        headers=h, timeout=10).json()
    if not isinstance(d, dict):
        return {"ok": None, "username": ""}
    result = d.get("result") if isinstance(d.get("result"), dict) else {}
    if d.get("errno") == 0 and result.get("bdstoken"):
        return {"ok": True, "username": str(result.get("username") or "")}
    if d.get("errno") in (-6, 2, 111, 116, -3):
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}


def _115(cookie: str) -> dict:
    """115 网盘：my.115.com 的 nav 接口能返回 uname 即已登录。

    未登录时接口回 state=false（errno 40101017 这类）且没有 data；
    接口不可达或结构不认识时返回 ok=None，让界面显示「无法验证」而不是误判。
    """
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://115.com/"
    try:
        d = requests.get("https://my.115.com/?ct=ajax&ac=nav",
                         headers=h, timeout=10).json()
    except Exception:
        return {"ok": None, "username": ""}
    if not isinstance(d, dict):
        return {"ok": None, "username": ""}
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    if d.get("state") is True:
        nick = str((data or {}).get("uname") or (data or {}).get("user_name")
                   or "").strip()
        return {"ok": True, "username": nick}
    if d.get("state") is False:
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}


def kugou_nick_for(userid) -> str:
    """同 userid 的酷狗昵称（酷狗概念版没有可用的用户信息接口，借主账号 Cookie）。"""
    import auth

    try:
        kg = _kugoo_fields(auth.load_cookies().get("kugou", ""))
        if userid and str(kg.get("KugooID") or "") == str(userid):
            return _kugou_nick(kg.get("NickName") or "")
    except Exception:
        pass
    return ""


def _acfun(cookie: str) -> dict:
    """AcFun：personalInfo 返回 result=0 即已登录，昵称从 info 里浅层找。

    游客态接口回 401 / {"result":-401,"error_msg":"user not login"}，
    这属于明确的未登录；接口异常或返回不认识的结构则给 None（不当成未登录）。
    """
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://www.acfun.cn/"
    try:
        d = requests.get("https://www.acfun.cn/rest/pc-direct/user/personalInfo",
                         headers=h, timeout=10).json()
    except Exception:
        return {"ok": None, "username": ""}
    if not isinstance(d, dict):
        return {"ok": None, "username": ""}
    result = d.get("result")
    if result == 0:
        info = d.get("info") or d.get("userInfo") or {}
        nick = ""
        if isinstance(info, dict):
            nick = str(info.get("userName") or info.get("name") or "").strip()
            user = info.get("user") if isinstance(info.get("user"), dict) else {}
            nick = nick or str((user or {}).get("name") or "").strip()
        return {"ok": True, "username": nick}
    msg = str(d.get("error_msg") or "").lower()
    if result in (-401, 401) or "not login" in msg or "未登录" in msg:
        return {"ok": False, "username": ""}
    return {"ok": None, "username": ""}


def _qqgroup(cookie: str) -> dict:
    """QQ群：get_group_list 回 ec=0 即登录态有效（同一接口也是人数订阅的取数通道）。

    昵称取 Cookie 里的 uin（QQ 号，形如 o0123456789）；接口不可达时给 None，
    界面显示「无法验证」而不是误判成未登录。
    """
    try:
        from platforms.fan_groups import qq_login_probe
    except Exception:
        return {"ok": None, "username": ""}
    info = qq_login_probe(cookie) or {}
    return {"ok": info.get("ok"), "username": str(info.get("username") or "")}


_VERIFIERS = {
    "bilibili": _bili, "douyin": _douyin, "weibo": _weibo, "acfun": _acfun,
    "netease": _netease, "netease_playlist": _netease, "qq": _qq,
    "kuaishou": _kuaishou,
    "kuwo": _kuwo, "kugou": _kugou, "xiaohongshu": _xiaohongshu,
    "taobao": _taobao, "5sing": _fivesing, "tcdoc": _tcdoc,
    "xianyu": _xianyu,
    "quark": _quark, "baidu": _baidu, "yun139": _yun139,
    "115": _115, "qqgroup": _qqgroup,
}
