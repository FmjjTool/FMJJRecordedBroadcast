"""酷狗 App 登录态（L4）：用网页登录票据换取 App token。

酷狗云歌单（创建 / 加歌）只认 App token，网页 token 会返回 20017。
这里不改动统一登录里已保存的网页登录态，只在内存里用它的 `t` 票据
向 `login_by_token` 换一个新的 App token，并缓存到独立的会话文件
`config/kugou_app_session.json`（唯一写入方就是本模块，不属于 Cookie 存储）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import string
import time
from pathlib import Path

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .playlist.base import AuthRequiredError

SALT = "OIlwieks28dk2k092lksi2UIkp"
APPID = 1005
CLIENTVER = 20489
UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDIAG7QOELSYoIJvTFJhMpe1s/gbjDJX51HBNnEl5HXqTW6lQ7LC8jr9fWZTwusknp+sVGzwd40MwP6U5yDE27M/X1+UR4tvOGOqp94TJtQ1EPnWGWXngpeIW5GxoQGao1rmYWAu6oi1z9XkChrsUdC6DJE5E221wf/4WLFxwAtRQIDAQAB
-----END PUBLIC KEY-----"""
TOKEN_KEYS = {"key": "90b8382a1bb4ccdcf063102053fd75b8",
              "iv": "f063102053fd75b8"}
LOGIN_ROUTER = "login.user.kugou.com"
CACHE_TTL_SECONDS = 7 * 24 * 3600

_public = load_pem_public_key(PUBLIC_KEY.encode()).public_numbers()
_KEY_BYTES = (_public.n.bit_length() + 7) // 8


def session_path() -> Path:
    from toolbox.config_manager import get_config_dir

    return get_config_dir() / "kugou_app_session.json"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _aes_hex(data: str, key: str, iv: str) -> str:
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    return cipher.encrypt(pad(data.encode(), 16)).hex()


def _aes_hex_random_key(data: str) -> tuple[str, str]:
    alphabet = string.ascii_lowercase + string.digits
    temp = "".join(secrets.choice(alphabet) for _ in range(16))
    key = _md5(temp)
    return _aes_hex(data, key[:32], key[-16:]), temp


def _rsa_raw(data: str) -> str:
    number = int.from_bytes(data.encode(), "big")
    encrypted = pow(number, _public.e, _public.n)
    return encrypted.to_bytes(_KEY_BYTES, "big").hex()


def _sign(params: dict, body: str) -> str:
    joined = "".join(f"{key}={params[key]}" for key in sorted(params))
    return _md5(f"{SALT}{joined}{body}{SALT}")


def _load_cache() -> dict:
    path = session_path()
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(userid: str, token: str, nickname: str = "") -> None:
    try:
        from toolbox.durable_io import atomic_write_json

        atomic_write_json(session_path(), {
            "userid": str(userid),
            "token": token,
            "nickname": nickname,
            "saved_at": int(time.time()),
        })
        try:
            os.chmod(session_path(), 0o600)
        except OSError:
            pass
    except OSError:
        pass


# ==================== 主版 App 扫码登录 ====================

LOGIN_APPID = 1005
QR_APPID = 1001
SRC_APPID = 2919
WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
QR_TXT = "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=1005&"


def _web_sign(params: dict) -> str:
    joined = "".join(f"{key}={value}" for key, value in sorted(params.items()))
    return _md5(f"{WEB_SALT}{joined}{WEB_SALT}")


def _qr_mid() -> str:
    return str(int(_md5(secrets.token_hex(16)), 16))


def _qr_get(path: str, extra: dict) -> dict:
    params = {
        "dfid": "-", "mid": _qr_mid(), "uuid": "-",
        "appid": QR_APPID, "clientver": 11440, "clienttime": int(time.time()),
    }
    params.update(extra)
    params["signature"] = _web_sign(params)
    try:
        resp = httpx.get("https://login-user.kugou.com" + path, params=params,
                         headers={"User-Agent": UA}, timeout=20.0,
                         follow_redirects=True)
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def qr_create() -> tuple[str, bytes]:
    """生成主版 App 登录二维码，返回 (qrcode_key, png_bytes)。"""
    data = _qr_get("/v2/qrcode", {
        "type": 1, "plat": 4, "appid": LOGIN_APPID,
        "qrcode_txt": QR_TXT, "srcappid": SRC_APPID,
    })
    payload = data.get("data") or {}
    key = str(payload.get("qrcode") or payload.get("key") or "")
    if not key:
        raise AuthRequiredError(
            f"酷狗扫码初始化失败（错误码 {data.get('error_code') or '未知'}）",
            platform="kugou", phase="qr",
        )
    image = str(payload.get("qrcode_img") or "")
    if image.startswith("data:image"):
        import base64

        return key, base64.b64decode(image.split(",", 1)[1])
    import io

    import qrcode

    buffer = io.BytesIO()
    qrcode.make(f"https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={key}"
                ).save(buffer, format="PNG")
    return key, buffer.getvalue()


def qr_poll(qrcode_key: str) -> dict:
    """轮询扫码状态：1 等扫码 / 2 待确认 / 4 成功 / 0 过期。"""
    data = _qr_get("/v2/get_userinfo_qrcode", {
        "plat": 4, "appid": LOGIN_APPID, "srcappid": SRC_APPID,
        "qrcode": qrcode_key,
    })
    payload = data.get("data") or {}
    status = payload.get("status")
    if status == 4:
        token = str(payload.get("token") or "")
        userid = str(payload.get("userid") or "")
        info = payload.get("userinfo") or payload.get("user_info") or {}
        nickname = str(payload.get("nickname") or payload.get("NickName")
                       or info.get("nickname") or info.get("user_name") or "")
        if token:
            _save_cache(userid, token, nickname)
            return {"status": 4, "logged_in": True, "userid": userid,
                    "nickname": nickname}
        return {"status": 4, "logged_in": False, "message": "已确认但未返回令牌"}
    if status == 0:
        return {"status": 0, "logged_in": False, "message": "二维码已过期，请重新扫码"}
    if status == 2:
        return {"status": 2, "logged_in": False, "message": "已扫码，请在手机上确认"}
    return {"status": status or 1, "logged_in": False, "message": "等待扫码"}


def cached_token(userid: str) -> str:
    data = _load_cache()
    if str(data.get("userid") or "") != str(userid):
        return ""
    token = str(data.get("token") or "")
    saved_at = int(data.get("saved_at") or 0)
    if not token or time.time() - saved_at > CACHE_TTL_SECONDS:
        return ""
    return token


def clear_cache() -> None:
    """退出登录/换号时清掉 App 会话；只删本模块自己的文件。"""
    try:
        session_path().unlink(missing_ok=True)
    except OSError:
        pass


APP_LOGIN_HINT = (
    "请到「账号与登录 → 登录状态 → 酷狗」点「App扫码」，"
    "用酷狗 App 扫码登录后再试"
)


def get_app_token(cookie: dict[str, str], *, force: bool = False) -> str:
    """用网页登录票据换取 App token；失败抛 AuthRequiredError（不含凭据）。"""
    userid = str(cookie.get("userid") or cookie.get("KugooID") or "").strip()
    if not userid:
        raise AuthRequiredError(f"未登录酷狗。{APP_LOGIN_HINT}",
                                platform="kugou", phase="auth")
    if not force:
        token = cached_token(userid)
        if token:
            return token
    web_token = str(cookie.get("t") or cookie.get("token") or "").strip()
    if not web_token:
        raise AuthRequiredError(f"酷狗登录状态不完整。{APP_LOGIN_HINT}",
                                platform="kugou", phase="auth")
    token = _exchange(userid, web_token, cookie)
    if not token:
        raise AuthRequiredError(f"酷狗云歌单需要登录酷狗 App。{APP_LOGIN_HINT}",
                                platform="kugou", phase="auth")
    _save_cache(userid, token)
    return token


def _exchange(userid: str, web_token: str, cookie: dict[str, str]) -> str:
    now_ms = int(time.time() * 1000)
    mid = str(cookie.get("kg_mid") or cookie.get("mid") or "")
    dfid = str(cookie.get("kg_dfid") or cookie.get("dfid") or "")

    p3 = _aes_hex(json.dumps({"clienttime": now_ms // 1000, "token": web_token},
                             separators=(",", ":")),
                  TOKEN_KEYS["key"], TOKEN_KEYS["iv"])
    enc_params, temp_key = _aes_hex_random_key("{}")
    pk = _rsa_raw(json.dumps({"clienttime_ms": now_ms, "key": temp_key},
                             separators=(",", ":")))
    body = json.dumps({
        "dfid": dfid, "p3": p3, "plat": 1, "t1": 0, "t2": 0,
        "t3": "MCwwLDAsMCwwLDAsMCwwLDA=", "pk": pk, "params": enc_params,
        "userid": userid, "clienttime_ms": now_ms,
    }, separators=(",", ":"))
    data = _post("/v5/login_by_token", body, web_token, userid, mid, dfid)
    if data.get("status") != 1:
        code = data.get("error_code") or data.get("err_code")
        raise AuthRequiredError(
            f"酷狗云歌单需要登录酷狗 App。{APP_LOGIN_HINT}"
            + (f"（换取失败码 {code}）" if code else ""),
            platform="kugou", phase="auth",
        )
    payload = data.get("data") or {}
    secu = payload.get("secu_params")
    token = ""
    if secu:
        key = _md5(temp_key)
        try:
            decrypted = AES.new(key[:32].encode(), AES.MODE_CBC,
                                key[-16:].encode()).decrypt(bytes.fromhex(secu))
            decrypted = decrypted[:-decrypted[-1]].decode()
            info = json.loads(decrypted)
            token = str(info.get("token") or "")
        except (ValueError, KeyError, json.JSONDecodeError):
            token = ""
    return token or str(payload.get("token") or "")


def _post(path: str, body: str, token: str, userid: str, mid: str,
          dfid: str) -> dict:
    clienttime = int(time.time())
    params = {
        "dfid": dfid or "-", "mid": mid or "-", "uuid": "-",
        "appid": APPID, "clientver": CLIENTVER, "clienttime": clienttime,
        "token": token, "userid": userid,
    }
    params["signature"] = _sign(params, body)
    headers = {
        "User-Agent": UA,
        "dfid": dfid or "-",
        "mid": mid or "-",
        "clienttime": str(clienttime),
        "Content-Type": "application/json;charset=utf-8",
        "x-router": LOGIN_ROUTER,
    }
    try:
        resp = httpx.post("https://gateway.kugou.com" + path, params=params,
                          content=body.encode("utf-8"), headers=headers,
                          timeout=20.0, follow_redirects=True)
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
