"""酷我 App 登录态（L4）：扫码登录获取 uid + sid。

酷我 PC 客户端的歌单写入走明文 HTTP `nplserver.kuwo.cn/pl.svc`，
鉴权是 `uid` + `sid`；网页登录态（Cookie）没有 sid，写不了。
这里用酷我官方扫码登录接口（`i.kuwo.cn/qrlogin/*`）换一份 uid/sid，
存独立会话文件 `config/kuwo_app_session.json`（唯一读写方），
不改动统一登录里已保存的网页 Cookie。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from .playlist.base import AuthRequiredError

BASE = "https://i.kuwo.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HINT = "请到「账号与登录 → 登录状态 → 酷我」点「App扫码」，用酷我 App 扫码登录后再试"


def session_path() -> Path:
    from toolbox.config_manager import get_config_dir

    return get_config_dir() / "kuwo_app_session.json"


def load_session() -> dict:
    try:
        with session_path().open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or not data.get("sid"):
        return {}
    return data


def save_session(uid: str, sid: str) -> None:
    try:
        from toolbox.durable_io import atomic_write_json

        atomic_write_json(session_path(), {
            "uid": str(uid), "sid": str(sid), "saved_at": int(time.time()),
        })
        try:
            os.chmod(session_path(), 0o600)
        except OSError:
            pass
    except OSError:
        pass


def clear_session() -> None:
    try:
        session_path().unlink(missing_ok=True)
    except OSError:
        pass


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA, "Referer": "https://h5app.kuwo.cn/"},
                        timeout=20.0, follow_redirects=True)


def qr_create() -> tuple[str, bytes]:
    """请求扫码 token 与二维码图片（JPEG）。"""
    with _client() as client:
        resp = client.get(f"{BASE}/qrlogin/token", params={
            "devId": 0, "devName": "PC", "src": "mbox", "from": "pc",
            "devType": "PC", "devResolution": "1024*768",
        })
        try:
            data = resp.json()
        except ValueError as exc:
            raise AuthRequiredError(
                f"酷我扫码初始化失败（HTTP {resp.status_code}）",
                platform="kuwo", phase="qr") from exc
        token = str(data.get("token") or "")
        if data.get("ret") != 0 or not token:
            raise AuthRequiredError(
                f"酷我扫码初始化失败（ret={data.get('ret')}）",
                platform="kuwo", phase="qr")
        image = client.get(f"{BASE}/qrlogin/qrcode/unionPC/{token}")
        if image.status_code != 200 or not image.content:
            raise AuthRequiredError("酷我二维码图片获取失败",
                                    platform="kuwo", phase="qr")
        return token, image.content


def qr_poll(token: str) -> dict:
    """轮询扫码结果；成功后把 uid/sid 写入会话文件。"""
    with _client() as client:
        resp = client.get(f"{BASE}/qrlogin/check/{token}")
    try:
        data = resp.json()
    except ValueError:
        return {"status": "waiting", "message": "等待扫码"}
    ret = data.get("ret")
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    uid = str(payload.get("uid") or payload.get("userid") or "")
    sid = str(payload.get("sid") or "")
    if ret == 0 and uid and sid:
        save_session(uid, sid)
        return {"status": "ok", "uid": uid, "logged_in": True}
    if ret in (1001, 1002):
        return {"status": "waiting" if ret == 1001 else "scanned",
                "message": data.get("msg") or "等待扫码"}
    if ret == 0:
        return {"status": "waiting", "message": data.get("msg") or "等待确认"}
    if ret in (-1, 1003, 1004):
        return {"status": "expired", "message": "二维码已过期，正在刷新"}
    return {"status": "failed", "message": f"{data.get('msg') or '扫码失败'}（ret={ret}）"}


def require_session() -> tuple[str, str]:
    data = load_session()
    if not data:
        raise AuthRequiredError(f"未登录酷我 App。{HINT}",
                                platform="kuwo", phase="auth")
    return str(data.get("uid")), str(data.get("sid"))
