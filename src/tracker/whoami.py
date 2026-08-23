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
        "kuwo": _kuwo, "kugou": _kugou,
    }.get(platform)
    if fn is None:
        return {"ok": None, "username": ""}  # 5sing / 小红书 暂不支持
    try:
        return fn(cookie)
    except Exception:
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
    import requests

    h = _headers(cookie)
    h["Referer"] = "https://www.kuaishou.com/"
    q = {"query": "query { userInfo { id name } }", "variables": {}}
    d = requests.post("https://www.kuaishou.com/graphql", json=q, headers=h, timeout=10).json()
    user = ((d.get("data") or {}).get("userInfo")) or {}
    if user.get("name"):
        return {"ok": True, "username": user["name"]}
    if user.get("id"):
        return {"ok": True, "username": f"ID {user['id']}"}
    return {"ok": False, "username": ""}


def _kuwo(cookie: str) -> dict:
    import requests

    token = _cookie_field(cookie, "kw_token")
    h = _headers(cookie)
    h["csrf"] = token or "x"
    h["Referer"] = "https://www.kuwo.cn/"
    try:
        d = requests.get("https://www.kuwo.cn/api/www/user/account/info?httpsStatus=1",
                         headers=h, timeout=10).json()
    except ValueError:
        d = {}
    data = d.get("data") or {}
    name = data.get("username") or data.get("name") or data.get("nickName")
    if d.get("success") and name:
        return {"ok": True, "username": str(name)}
    uid = _cookie_field(cookie, "userid")
    if uid:
        return {"ok": None, "username": f"用户ID {uid}"}
    return {"ok": None, "username": ""}


def _kugou(cookie: str) -> dict:
    uid = _cookie_field(cookie, "userid") or _cookie_field(cookie, "KugouID")
    if uid:
        return {"ok": None, "username": f"用户ID {uid}"}
    return {"ok": None, "username": ""}
