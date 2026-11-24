"""WebUI 公网隧道管理（L3）：客户端检测与 Cloudflare 快速隧道启停。

Cloudflare 快速隧道提供临时 HTTPS 公网地址，不需要账号；Tailscale / frp
目前只做客户端检测与配置占位，启动能力后续补。只停本模块启动的进程。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from toolbox.config_manager import (get_app_root, load_convert_config,
                                    save_convert_config)

TUNNELS = {
    "cloudflare": {"name": "Cloudflare 快速隧道", "exe": "cloudflared",
                   "note": "临时公网 HTTPS 地址，无需账号"},
    "tailscale": {"name": "Tailscale", "exe": "tailscale",
                  "note": "设备组私有网络，需要登录"},
    "frp": {"name": "frp", "exe": "frpc",
            "note": "自建服务器中转，需要 frpc 配置"},
}
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_START_TIMEOUT = 30.0
_log = logging.getLogger(__name__)


class WebUITunnel:
    def __init__(self):
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._mode = ""
        self._public_url = ""
        self._last_error = ""

    def detect(self) -> dict:
        return {key: _find_exe(item["exe"]) for key, item in TUNNELS.items()}

    def config(self) -> dict:
        cfg = load_convert_config()
        mode = str(cfg.get("webui_tunnel") or "none")
        if mode not in TUNNELS:
            mode = "none"
        return {"mode": mode,
                "frp_config": str(cfg.get("webui_tunnel_frp_config") or "")}

    def save_config(self, mode: str | None = None,
                    frp_config: str | None = None) -> dict:
        cfg = load_convert_config()
        if mode is not None:
            cfg["webui_tunnel"] = str(mode)
        if frp_config is not None:
            cfg["webui_tunnel_frp_config"] = str(frp_config)
        save_convert_config(cfg)
        return self.config()

    def status(self) -> dict:
        if self._proc is not None and self._proc.poll() is not None:
            self._last_error = (
                f"隧道进程已退出（代码 {self._proc.returncode}），"
                "详见 logs/webui_tunnel.log")
            self._proc = None
            self._public_url = ""
        return {"running": self._proc is not None,
                "mode": self._mode,
                "public_url": self._public_url,
                "clients": self.detect(),
                "error": self._last_error}

    def start(self, mode: str, local_port: int) -> dict:
        with self._lock:
            current = self.status()
            if current["running"]:
                return current
            if mode == "cloudflare":
                return self._start_cloudflare(local_port)
            if mode in TUNNELS:
                raise ValueError(
                    f"{TUNNELS[mode]['name']} 启动尚未接入，请先使用 "
                    "Cloudflare 快速隧道")
            raise ValueError("未知的隧道类型")

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None:
                return {"stopped": False, "message": "隧道未运行"}
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            self._proc = None
            self._public_url = ""
            self._last_error = ""
            return {"stopped": True}

    def _start_cloudflare(self, local_port: int) -> dict:
        exe = _find_exe("cloudflared")
        if not exe:
            raise ValueError("未检测到 cloudflared，请先在全局设置安装")
        if not 1 <= int(local_port) <= 65535:
            raise ValueError("WebUI 端口无效")
        root = get_app_root()
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "webui_tunnel.log"
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW
        handle = open(log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [exe, "tunnel", "--url", f"http://127.0.0.1:{local_port}",
                 "--no-autoupdate", "--loglevel", "info"],
                cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=handle, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True)
        finally:
            handle.close()
        self._proc = proc
        self._mode = "cloudflare"
        self._public_url = ""
        self._last_error = ""
        deadline = time.time() + _START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                self._last_error = (
                    f"Cloudflare 隧道启动失败（代码 {proc.returncode}），"
                    "详见 logs/webui_tunnel.log")
                self._proc = None
                self._mode = ""
                raise RuntimeError(self._last_error)
            match = _URL_RE.search(_read_tail(log_path))
            if match:
                self._public_url = match.group(0)
                return self.status()
            time.sleep(0.3)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._proc = None
        self._mode = ""
        self._last_error = "Cloudflare 隧道启动超时，详见 logs/webui_tunnel.log"
        raise RuntimeError(self._last_error)


def _find_exe(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    root = get_app_root()
    for folder in (root, root / "bin", root / "tools"):
        candidate = folder / (name + ".exe" if os.name == "nt" else name)
        if candidate.is_file():
            return str(candidate)
    return ""


def _read_tail(path: Path, limit: int = 64 * 1024) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


_tunnel: WebUITunnel | None = None


def get_tunnel() -> WebUITunnel:
    global _tunnel
    if _tunnel is None:
        _tunnel = WebUITunnel()
    return _tunnel