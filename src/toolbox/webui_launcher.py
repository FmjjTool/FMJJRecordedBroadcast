"""WebUI 独立服务进程管理（L3）：启动/停止子进程、配置与状态探测。

桌面设置页只调用本模块，不直接拼命令行或探测端口。WebUI 本身仍是
`toolbox/webui` 的 L2 显示层，由 `main.py --webui` 以独立进程启动。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from toolbox.config_manager import (get_app_root, get_config_dir,
                                    load_convert_config, save_convert_config)

_DEFAULT_PORT = 8765
_START_TIMEOUT = 12.0
_STOP_TIMEOUT = 4.0
_health_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_log = logging.getLogger(__name__)


class WebUILauncher:
    def __init__(self):
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._token = ""
        self._host = ""
        self._last_error = ""

    def config(self) -> dict:
        cfg = load_convert_config()
        try:
            port = int(cfg.get("webui_port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            port = _DEFAULT_PORT
        if not 1024 <= port <= 65535:
            port = _DEFAULT_PORT
        return {"port": port,
                "auto_start": bool(cfg.get("webui_auto_start", False)),
                "lan": bool(cfg.get("webui_lan", False))}

    def save_config(self, port: int | None = None,
                    auto_start: bool | None = None,
                    lan: bool | None = None) -> dict:
        cfg = load_convert_config()
        if port is not None:
            cfg["webui_port"] = int(port)
        if auto_start is not None:
            cfg["webui_auto_start"] = bool(auto_start)
        if lan is not None:
            cfg["webui_lan"] = bool(lan)
        save_convert_config(cfg)
        return self.config()

    def status(self) -> dict:
        if self._proc is not None and self._proc.poll() is not None:
            self._last_error = (
                f"WebUI 服务已退出（代码 {self._proc.returncode}），"
                "详见 logs/webui.log")
            self._proc = None
            self._token = ""
            self._port = 0
            self._host = ""
        cfg = self.config()
        port = self._port or cfg["port"]
        owned = self._proc is not None
        running = owned or _health(port)
        if running and not owned:
            self._port = port
        lan_enabled = self._host == "0.0.0.0" or (not owned and cfg["lan"])
        lan_ip = _lan_ip() if running and lan_enabled else ""
        return {"running": bool(running), "owned": owned,
                "port": self._port or port, "host": self._host,
                "url": self.url() if running else "",
                "lan_ip": lan_ip,
                "lan_url": self.lan_url() if lan_ip else "",
                "pid": self._proc.pid if owned else 0,
                "error": self._last_error}

    def url(self) -> str:
        local = self._url_for("127.0.0.1")
        if local:
            return local
        path = get_config_dir() / "webui_url.txt"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def lan_url(self) -> str:
        ip = _lan_ip()
        if not ip:
            return ""
        direct = self._url_for(ip)
        if direct:
            return direct
        source = self.url()
        if not source:
            return ""
        parts = urllib.parse.urlsplit(source)
        netloc = f"{ip}:{parts.port or self._port or _DEFAULT_PORT}"
        return urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def phone_url(self) -> str:
        return self.lan_url() or self.url()

    def _url_for(self, host: str) -> str:
        if host and self._port and self._token:
            return f"http://{host}:{self._port}/?token={self._token}"
        return ""

    def start(self, port: int | None = None,
              lan: bool | None = None) -> dict:
        with self._lock:
            current = self.status()
            if current["running"]:
                return current
            cfg = self.config()
            try:
                target = int(port if port is not None else cfg["port"])
            except (TypeError, ValueError) as exc:
                raise ValueError("端口必须是数字") from exc
            if not 1024 <= target <= 65535:
                raise ValueError("端口必须在 1024-65535 之间")
            if _port_in_use(target):
                raise ValueError(f"端口 {target} 已被其他程序占用")
            if lan is None:
                lan = cfg["lan"]
            host = "0.0.0.0" if lan else "127.0.0.1"
            token = secrets.token_urlsafe(24)
            proc = self._spawn(target, token, host)
            self._proc, self._port, self._token = proc, target, token
            self._host = host
            self._last_error = ""
            deadline = time.time() + _START_TIMEOUT
            while time.time() < deadline:
                if proc.poll() is not None:
                    self._last_error = (
                        f"WebUI 启动失败（代码 {proc.returncode}），"
                        "详见 logs/webui.log")
                    self._proc = None
                    self._token = ""
                    self._port = 0
                    self._host = ""
                    raise RuntimeError(self._last_error)
                if _health(target):
                    return self.status()
                time.sleep(0.25)
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._proc = None
            self._token = ""
            self._host = ""
            self._last_error = "WebUI 启动超时，详见 logs/webui.log"
            raise RuntimeError(self._last_error)

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None:
                return {"stopped": False,
                        "message": "该 WebUI 不是由本软件启动，请在对应窗口停止"}
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=_STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            self._proc = None
            self._port = 0
            self._token = ""
            self._host = ""
            self._last_error = ""
            return {"stopped": True}

    def start_if_enabled(self) -> None:
        cfg = self.config()
        if not cfg["auto_start"]:
            return
        try:
            self.start(cfg["port"])
        except Exception as exc:
            _log.warning("WebUI 自动启动失败：%s", exc)

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception as exc:
            _log.warning("WebUI 停止失败：%s", exc)

    def open_browser(self) -> str:
        status = self.status()
        if not status["running"]:
            raise RuntimeError("WebUI 未运行，请先启动")
        url = self.url()
        if not url:
            raise RuntimeError("未找到 WebUI 访问地址，请重新启动服务")
        webbrowser.open(url)
        return url

    def _spawn(self, port: int, token: str, host: str) -> subprocess.Popen:
        root = get_app_root()
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(root / "main.py"))
        command += ["--webui", "--webui-port", str(port),
                    f"--webui-host={host}", f"--webui-token={token}"]
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "webui.log"
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        handle = open(log_path, "ab", buffering=0)
        try:
            # 临时阅读会话仍由桌面内存唯一持有；子进程只得到只读本机能力。
            child_env = os.environ.copy()
            child_env.pop("FMJJ_WECHAT_SESSION_ENDPOINT", None)
            child_env.pop("FMJJ_WECHAT_SESSION_CAPABILITY", None)
            try:
                from wechat_session import webui_environment
                child_env.update(webui_environment())
            except Exception:
                # 微信阅读为可选能力，本机临时通道异常不阻断既有 WebUI。
                _log.warning("临时阅读共享通道不可用，WebUI 将以无阅读会话模式启动")
            return subprocess.Popen(
                command, cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=handle, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True, env=child_env)
        finally:
            handle.close()


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        return "" if ip.startswith("127.") else ip
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            return "" if ip.startswith("127.") else ip
        except OSError:
            return ""


def _port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return True


def _health(port: int) -> bool:
    try:
        with _health_opener.open(
                f"http://127.0.0.1:{port}/api/health", timeout=1.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except Exception:
        return False


_launcher: WebUILauncher | None = None


def get_launcher() -> WebUILauncher:
    global _launcher
    if _launcher is None:
        _launcher = WebUILauncher()
    return _launcher
