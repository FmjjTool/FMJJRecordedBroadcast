"""Cookie 身份验证：用已保存 Cookie 拉取当前登录用户名（10 平台）。

probe(platform) 返回：
    {"ok": True,  "username": "昵称"}  Cookie 有效且拿到用户名
    {"ok": True,  "username": ""}      Cookie 有效但拿不到用户名
    {"ok": None,  "username": "用户ID xxx"} 无法远程验证，展示 Cookie 中的 ID
    {"ok": None,  "username": ""}      该平台暂不支持自动验证
    {"ok": False, "username": ""}      Cookie 已失效/未登录
"""

from __future__ import annotations

from urllib.parse import urlencode


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
    if not cookie:
        return {"ok": False, "username": ""}

    fn = {
        "bilibili": _bili, "douyin": _douyin, "weibo": _weibo,
        "netease": _netease, "qq": _qq, "kuaishou": _kuaishou,
        "kuwo": _kuwo, "kugou": _kugou, "xiaohongshu": _xiaohongshu,
        "taobao": _taobao,
    }.get(platform)
    if fn is None:
        return {"ok": None, "username": ""}  # 5sing 暂不支持
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


def _xiaohongshu(cookie: str) -> dict:
    import requests

    r = requests.get("https://www.xiaohongshu.com/",
                     headers=_headers(cookie), timeout=12)
    import re
    import json
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                  r.text, re.S)
    if not m:
        return {"ok": None, "username": ""}
    try:
        d = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(1)).replace("undefined", "null"))
    except Exception:
        return {"ok": None, "username": ""}
    user = d.get("user") or {}

    def _find_nick(obj, depth=0):
        if depth > 4:
            return ""
        if isinstance(obj, dict):
            for k in ("nickname", "nickName", "nick_name"):
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

    nick = _find_nick(user)
    logged_in = bool(user.get("loggedIn") is True or user.get("loginInfo")
                     or (user.get("userInfo") and nick))
    if logged_in and nick:
        return {"ok": True, "username": nick}
    if nick:
        return {"ok": True, "username": nick}
    return {"ok": None, "username": ""}


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
    if data.get("login"):
        user = data.get("user") or {}
        return {"ok": True, "username": user.get("screen_name") or ""}
    return {"ok": False, "username": ""}


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


def _qq(cookie: str) -> dict:
    import requests

    payload = {"comm": {"ct": 24, "cv": 0},
               "req_0": {"module": "music.UserInfoCgiSvr", "method": "GetUserInfo", "param": {}}}
    d = requests.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=payload,
                      headers=_headers(cookie), timeout=10).json()
    req = d.get("req_0") or {}
    if req.get("code") == 0:
        nick = _find_nick(req.get("data") or {})
        return {"ok": True, "username": nick}
    uin = _cookie_field(cookie, "uin")
    if uin:
        return {"ok": None, "username": f"QQ {uin}"}
    return {"ok": False, "username": ""}


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


def _kugou(cookie: str) -> dict:
    # 远程用户接口全被拦（userservice 404/passport 403/mobilecdn Access Deny），
    # 用 Cookie 内登录凭据判定：KugooID（注意双 o，KugouID 拼法查不到值）+
    # 登录态 Cookie（KuGoo 会话 / t 令牌）同时存在才算有效登录
    uid = (_cookie_field(cookie, "KugooID")
           or _cookie_field(cookie, "KugouID")
           or _cookie_field(cookie, "userid"))
    logged = bool(uid and (_cookie_field(cookie, "KuGoo")
                           or _cookie_field(cookie, "t")))
    name = _cookie_field(cookie, "UserName")
    if logged and name and name != f"kgopen{uid}":
        return {"ok": True, "username": name}
    if logged:
        return {"ok": True, "username": f"用户ID {uid}"}
    if uid:
        return {"ok": None, "username": f"用户ID {uid}"}
    return {"ok": None, "username": ""}
