# -*- coding: utf-8 -*-
"""直播总结 · 配置与 API Key 本机加密存储。

普通设置写进 `config.json` 的 `convert` 段（与项目其他模块一致）；
API Key 单独写 `config/livestream_summary.json`，用 MachineGuid 派生密钥加密，
与 Cookie 存储完全隔离，不读取、不改写任何已有登录凭据。
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from toolbox.config_manager import (
    get_config_dir, load_convert_config, save_convert_config,
)

CFG_BASE_URL = "livestream_summary_base_url"
CFG_MODEL = "livestream_summary_model"
CFG_CHUNK_CHARS = "livestream_summary_chunk_chars"
CFG_OUTPUT_DIR = "livestream_summary_output_dir"
CFG_MODE = "livestream_summary_mode"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CHUNK_CHARS = 6000
MODES = {"ai": "大模型总结", "rule": "本地规则保底"}


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    output_dir: str = ""
    mode: str = "ai"


def load_settings() -> Settings:
    cfg = load_convert_config()
    try:
        chunk_chars = int(cfg.get(CFG_CHUNK_CHARS) or DEFAULT_CHUNK_CHARS)
    except (TypeError, ValueError):
        chunk_chars = DEFAULT_CHUNK_CHARS
    mode = str(cfg.get(CFG_MODE) or "ai").strip()
    return Settings(
        base_url=str(cfg.get(CFG_BASE_URL) or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        model=str(cfg.get(CFG_MODEL) or "").strip(),
        chunk_chars=max(2000, min(20000, chunk_chars)),
        output_dir=str(cfg.get(CFG_OUTPUT_DIR) or "").strip(),
        mode=mode if mode in MODES else "ai",
    )


def save_settings(settings: Settings) -> None:
    cfg = load_convert_config()
    cfg[CFG_BASE_URL] = settings.base_url.strip() or DEFAULT_BASE_URL
    cfg[CFG_MODEL] = settings.model.strip()
    cfg[CFG_CHUNK_CHARS] = max(2000, min(20000, int(settings.chunk_chars)))
    cfg[CFG_OUTPUT_DIR] = settings.output_dir.strip()
    cfg[CFG_MODE] = settings.mode if settings.mode in MODES else "ai"
    save_convert_config(cfg)


# ==================== API Key（本机加密，独立于 Cookie） ====================

def secret_path() -> Path:
    return get_config_dir() / "livestream_summary.json"


def _machine_seed() -> str:
    """本机标识；取不到时退回网卡节点号，与 webwiki 统计密码方案一致。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
    except Exception:  # noqa: BLE001 - 非 Windows / 无权限
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
    """独立盐值：与 Cookie 加密、站点统计密码互不影响。"""
    seed = f"fmjj-livestream-summary-v1:{_machine_seed()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _encrypt(text: str) -> str:
    if not text:
        return ""
    try:
        from cryptography.fernet import Fernet

        return "enc:" + Fernet(_fernet_key()).encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:  # noqa: BLE001 - 加密库缺失时降级明文
        return text


def _decrypt(text: str) -> str:
    if not text:
        return ""
    if not text.startswith("enc:"):
        return text
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(text[4:].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - 换机 / 重装后密钥不匹配
        return ""


def load_api_key() -> str:
    try:
        data = json.loads(secret_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return _decrypt(str(data.get("api_key") or ""))


def has_api_key() -> bool:
    return bool(load_api_key())


def save_api_key(api_key: str) -> bool:
    """保存 API Key；传空串等于清除。返回是否已加密。"""
    api_key = str(api_key or "").strip()
    path = secret_path()
    if not api_key:
        try:
            path.unlink()
        except OSError:
            pass
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _encrypt(api_key)
    payload = {
        "api_key": encrypted,
        "encrypted": encrypted.startswith("enc:"),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return bool(payload["encrypted"])
