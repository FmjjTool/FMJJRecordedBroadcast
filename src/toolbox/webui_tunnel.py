"""WebUI 公网隧道管理（L3）：客户端检测与隧道启停。

- Cloudflare 快速隧道：临时 HTTPS 公网地址，无需账号（子进程）。
- Tailscale：私有网络；`tailscale up` 为一次性命令（可能需要浏览器登录），
  停止只清除本软件显示状态，不执行 `tailscale down`，避免影响用户的系统
  级 Tailscale 连接。
- frp：读取用户配置的 frpc 配置文件启动子进程；公网地址由用户在 frp
  服务器侧配置。

只停本模块启动的进程；不写 config 以外的数据目录。
"""

from __future__ import annotations

import json
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
                  "note": "设备组私有网络，需要登录；WebUI 需允许局域网"},
    "frp": {"name": "frp", "exe": "frpc",
            "note": "自建服务器中转，需要 frpc 配置文件"},
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
                "frp_config": str(cfg.get("webui_tunnel_frp_config") or ""),
                "frp_public_url": str(
                    cfg.get("webui_tunnel_frp_public_url") or "")}

    def save_config(self, mode: str | None = None,
                    frp_config: str | None = None,
                    frp_public_url: str | None = None) -> dict:
        cfg = load_convert_config()
        if mode is not None:
            cfg["webui_tunnel"] = str(mode)
        if frp_config is not None:
            cfg["webui_tunnel_frp_config"] = str(frp_config)
        if frp_public_url is not None:
            cfg["webui_tunnel_frp_public_url"] = str(frp_public_url)
        save_convert_config(cfg)
        return self.config()

    def status(self) -> dict:
        self._refresh_proc()
        mode = self._mode
        running = self._proc is not None
        if mode == "tailscale":
            running = _tailscale_running(_find_exe("tailscale"))
        return {"running": bool(running), "mode": mode,
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
            if mode == "tailscale":
                return self._start_tailscale(local_port)
            if mode == "frp":
                return self._start_frp(local_port)
            raise ValueError("未知的隧道类型")

    def stop(self) -> dict:
        with self._lock:
            if self._mode == "tailscale":
                self._mode = ""
                self._public_url = ""
                self._last_error = ""
                return {"stopped": False,
                        "message": "Tailscale 为系统服务，未执行关闭；"
                                   "如需断开请在系统中操作"}
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
            self._mode = ""
            self._public_url = ""
            self._last_error = ""
            return {"stopped": True}

    def _refresh_proc(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            self._last_error = (
                f"隧道进程已退出（代码 {self._proc.returncode}），"
                "详见 logs/webui_tunnel.log")
            self._proc = None
            self._public_url = ""

    def _start_cloudflare(self, local_port: int) -> dict:
        exe = _find_exe("cloudflared")
        if not exe:
            raise ValueError("未检测到 cloudflared，请先在全局设置安装")
        _validate_port(local_port)
        log_path = _log_path()
        proc = _spawn([exe, "tunnel", "--url",
                       f"http://127.0.0.1:{local_port}",
                       "--no-autoupdate", "--loglevel", "info"], log_path)
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

    def _start_tailscale(self, local_port: int) -> dict:
        exe = _find_exe("tailscale")
        if not exe:
            raise ValueError("未检测到 Tailscale，请先在全局设置安装")
        _validate_port(local_port)
        try:
            result = subprocess.run(
                [exe, "up"], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            self._last_error = (
                "Tailscale 登录未完成：请在浏览器完成登录后重试")
            raise RuntimeError(self._last_error) from exc
        if result.returncode != 0:
            message = ((result.stderr or result.stdout or "").strip()[:300]
                       or "需要先登录 Tailscale")
            self._last_error = f"Tailscale 启动失败：{message}"
            raise RuntimeError(self._last_error)
        self._mode = "tailscale"
        self._last_error = ""
        ip = _tailscale_ip(exe)
        self._public_url = f"http://{ip}:{local_port}" if ip else ""
        return self.status()

    def _start_frp(self, local_port: int) -> dict:
        exe = _find_exe("frpc")
        if not exe:
            raise ValueError("未检测到 frpc，请先在全局设置安装")
        _validate_port(local_port)
        cfg = self.config()
        config_path = str(cfg["frp_config"] or "").strip()
        if not config_path:
            raise ValueError("请先选择 frpc 配置文件")
        if not Path(config_path).is_file():
            raise ValueError("frpc 配置文件不存在，请重新选择")
        log_path = _log_path()
        proc = _spawn([exe, "-c", config_path], log_path)
        self._proc = proc
        self._mode = "frp"
        self._public_url = str(cfg["frp_public_url"] or "")
        self._last_error = ""
        time.sleep(0.8)
        if proc.poll() is not None:
            self._last_error = (
                f"frp 启动失败（代码 {proc.returncode}），"
                "详见 logs/webui_tunnel.log")
            self._proc = None
            self._mode = ""
            self._public_url = ""
            raise RuntimeError(self._last_error)
        return self.status()


def _validate_port(port: int) -> None:
    if not 1 <= int(port) <= 65535:
        raise ValueError("WebUI 端口无效")


def _log_path() -> Path:
    log_dir = get_app_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "webui_tunnel.log"


def _spawn(command: list, log_path: Path) -> subprocess.Popen:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    handle = open(log_path, "ab", buffering=0)
    try:
        return subprocess.Popen(
            command, cwd=str(get_app_root()), stdin=subprocess.DEVNULL,
            stdout=handle, stderr=subprocess.STDOUT,
            creationflags=flags, close_fds=True)
    finally:
        handle.close()


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


def _tailscale_running(exe: str) -> bool:
    if not exe:
        return False
    try:
        result = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True,
            timeout=10)
        payload = json.loads(result.stdout or "{}")
        return str(payload.get("BackendState") or "") == "Running"
    except Exception:
        return False


def _tailscale_ip(exe: str) -> str:
    try:
        result = subprocess.run(
            [exe, "ip", "-4"], capture_output=True, text=True, timeout=10)
        for line in (result.stdout or "").splitlines():
            text = line.strip()
            if text:
                return text
    except Exception:
        return ""
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