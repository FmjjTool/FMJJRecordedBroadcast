"""GitHub Token 统一凭据适配。

新存储在 ``config/github_auth.json``，使用本机标识派生的独立密钥加密；
旧 ``config/webwiki_auth.json`` 只读兼容，保存/清除只写新文件，不迁移、
不重写旧凭据。业务层只通过本模块读取当前登录态。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import get_config_dir


def auth_path() -> Path:
    return get_config_dir() / "github_auth.json"


def _machine_seed() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
    except Exception:  # noqa: BLE001 - 非 Windows / 无权限时退回节点号
        pass
    try:
        import uuid

        node = uuid.getnode()
        if node:
            return f"node-{node}"
    except Exception:  # noqa: BLE001
        pass
    return "local-default"


def _fernet_key() -> bytes:
    seed = f"fmjj-github-auth-v1:{_machine_seed()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _encrypt(text: str) -> str:
    if not text:
        return ""
    try:
        from cryptography.fernet import Fernet

        return "enc:" + Fernet(_fernet_key()).encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:  # noqa: BLE001 - 加密库缺失时降级，payload 会标记 encrypted=False
        return text


def _decrypt(text: str) -> str:
    if not text:
        return ""
    if not text.startswith("enc:"):
        return text
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(text[4:].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - 换机 / 重装系统后密钥不匹配
        return ""


def _legacy_load() -> dict:
    """旧 webwiki_auth.json 只读兼容；不迁移、不清理。"""
    try:
        from . import config as legacy

        data = legacy.load_auth()
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    token = str(data.get("token") or "")
    if not token:
        return {}
    return {
        "token": token,
        "login": str(data.get("login") or ""),
        "name": str(data.get("name") or ""),
        "avatar": str(data.get("avatar") or ""),
        "cleared": False,
        "source": "legacy",
    }


def load() -> dict:
    """读取当前 GitHub 登录态；新存储优先，缺失时只读回退旧文件。"""
    path = auth_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _legacy_load()
    if not isinstance(payload, dict):
        return _legacy_load()
    if payload.get("cleared"):
        return {"token": "", "login": "", "name": "", "avatar": "",
                "cleared": True, "source": "unified"}
    token = _decrypt(str(payload.get("token") or ""))
    return {
        "token": token,
        "login": str(payload.get("login") or ""),
        "name": str(payload.get("name") or ""),
        "avatar": str(payload.get("avatar") or ""),
        "cleared": False,
        "source": "unified",
    }


def token() -> str:
    return str(load().get("token") or "")


def save(access_token: str, user: dict | None = None) -> dict:
    """保存 GitHub Token（仅由统一管理界面的用户操作调用）。"""
    user = user or {}
    token_value = str(access_token or "").strip()
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _encrypt(token_value)
    payload = {
        "token": encrypted,
        "login": str(user.get("login") or ""),
        "name": str(user.get("name") or ""),
        "avatar": str(user.get("avatar_url") or user.get("avatar") or ""),
        "cleared": False,
        "encrypted": encrypted.startswith("enc:"),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "token": token_value,
        "login": payload["login"],
        "name": payload["name"],
        "avatar": payload["avatar"],
        "cleared": False,
        "source": "unified",
    }


def clear() -> None:
    """清除登录态；写入 cleared 标记以阻止旧文件重新生效。"""
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": "",
        "login": "",
        "name": "",
        "avatar": "",
        "cleared": True,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
