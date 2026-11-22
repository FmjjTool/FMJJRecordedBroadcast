"""程序内代理设置（唯一写入方：config/proxy/settings.json）。

只保存「怎么启动内核」与「用哪个提供商」，不保存节点凭据本身：
提供商内容落在 config/proxy/provider.yaml，订阅链接由内核自己下载到
config/proxy/providers/。所有文件都在 config/（gitignore）下，不进仓库。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from toolbox.config_manager import get_config_dir
from toolbox.durable_io import atomic_write_json

SCHEMA_VERSION = 1
DEFAULT_MIXED_PORT = 7897
DEFAULT_API_PORT = 9097
DEFAULT_TEST_URL = "http://www.gstatic.com/generate_204"
GROUP_NAME = "程序代理"
MODES = ("rule", "global", "direct")
MODE_LABELS = {"rule": "规则", "global": "全局", "direct": "直连"}

# 提供商来源：file=用户自己写的本地 YAML；url=提供商订阅链接
PROVIDER_KINDS = ("file", "url")


def proxy_dir() -> Path:
    path = get_config_dir() / "proxy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bin_dir() -> Path:
    path = proxy_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def providers_dir() -> Path:
    path = proxy_dir() / "providers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return proxy_dir() / "settings.json"


def runtime_path() -> Path:
    return proxy_dir() / "runtime.yaml"


def provider_path() -> Path:
    return proxy_dir() / "provider.yaml"


def log_path() -> Path:
    return proxy_dir() / "mihomo.log"


def defaults() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "enabled": False,          # 程序级代理是否启用（影响进程环境变量）
        "core_path": "",           # 手动指定的内核文件；空=自动探测
        "core_mirror": "",         # 内核下载加速前缀，{url} 占位；空=用内置镜像表
        "mixed_port": DEFAULT_MIXED_PORT,
        "api_port": DEFAULT_API_PORT,
        "mode": "rule",
        "provider_kind": "file",
        "provider_url": "",
        "group": GROUP_NAME,
        "secret": "",
        "updated_at": "",
    }


def _to_port(value, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1024 <= port <= 65535 else fallback


def normalize(raw: dict | None) -> dict:
    """把磁盘内容收敛成合法设置：端口越界回默认，未知模式回规则。"""
    data = defaults()
    if isinstance(raw, dict):
        data.update({k: v for k, v in raw.items() if k in data})
    data["version"] = SCHEMA_VERSION
    data["enabled"] = bool(data.get("enabled"))
    data["mixed_port"] = _to_port(data.get("mixed_port"), DEFAULT_MIXED_PORT)
    data["api_port"] = _to_port(data.get("api_port"), DEFAULT_API_PORT)
    if data["api_port"] == data["mixed_port"]:
        data["api_port"] = DEFAULT_API_PORT
    if str(data.get("mode")) not in MODES:
        data["mode"] = "rule"
    if str(data.get("provider_kind")) not in PROVIDER_KINDS:
        data["provider_kind"] = "file"
    data["core_path"] = str(data.get("core_path") or "")
    data["core_mirror"] = str(data.get("core_mirror") or "")
    data["provider_url"] = str(data.get("provider_url") or "")
    data["group"] = str(data.get("group") or GROUP_NAME)
    data["secret"] = str(data.get("secret") or "")
    return data


def load() -> dict:
    import json

    path = settings_path()
    raw = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
    data = normalize(raw)
    if not data["secret"]:
        data["secret"] = secrets.token_hex(16)
        save(data)
    return data


def save(data: dict) -> dict:
    data = normalize(data)
    atomic_write_json(settings_path(), data)
    return data


def update(**changes) -> dict:
    data = load()
    data.update(changes)
    return save(data)