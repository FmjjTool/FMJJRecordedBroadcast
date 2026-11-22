"""本地资料站 · 路径、配置、代理与解释器探测。

仓库默认落在「统一导出目录/本地资料站」下（download/ 已在 .gitignore 里），
GitHub 登录态由 toolbox.webwiki.credentials 统一管理（新文件
config/github_auth.json）；旧 config/webwiki_auth.json 仅作只读兼容。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from toolbox.config_manager import (
    get_config_dir,
    get_export_dir,
    load_convert_config,
    save_convert_config,
)

REPO_URLS = {
    "webwiki": "https://github.com/FmjjTool/webwiki.git",
    "webwiki-api": "https://github.com/FmjjTool/webwiki-api.git",
}

# ==================== 目录 ====================

def root_dir() -> Path:
    """资料站根目录（配置为空时用 统一导出目录/本地资料站）。"""
    raw = str(load_convert_config().get("webwiki_dir") or "").strip()
    root = Path(raw).expanduser() if raw else (get_export_dir() / "本地资料站")
    root.mkdir(parents=True, exist_ok=True)
    return root


def set_root_dir(path: str) -> None:
    cfg = load_convert_config()
    cfg["webwiki_dir"] = str(path or "").strip()
    save_convert_config(cfg)


def web_dir(root: Path | None = None) -> Path:
    return Path(root or root_dir()) / "webwiki"


def api_dir(root: Path | None = None) -> Path:
    return Path(root or root_dir()) / "webwiki-api"


def venv_dir(root: Path | None = None) -> Path:
    return Path(root or root_dir()) / ".venv"


def venv_python(root: Path | None = None) -> Path:
    base = venv_dir(root)
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def dist_dir(root: Path | None = None) -> Path:
    return web_dir(root) / "dist"


def logs_dir(root: Path | None = None) -> Path:
    path = Path(root or root_dir()) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ==================== 端口 / 开关 ====================

def ports() -> tuple[int, int]:
    cfg = load_convert_config()
    return (int(cfg.get("webwiki_backend_port") or 8000),
            int(cfg.get("webwiki_site_port") or 8090))


def set_ports(backend: int, site: int) -> None:
    cfg = load_convert_config()
    cfg["webwiki_backend_port"] = int(backend)
    cfg["webwiki_site_port"] = int(site)
    save_convert_config(cfg)


def auto_open_browser() -> bool:
    return bool(load_convert_config().get("webwiki_open_browser", True))


def set_auto_open_browser(enabled: bool) -> None:
    cfg = load_convert_config()
    cfg["webwiki_open_browser"] = bool(enabled)
    save_convert_config(cfg)


# ==================== 站点访问统计 ====================

def analytics_scope() -> str:
    """统计口径：human（严格验证）/ qualified（加行为合格）/ all（全部分类）。"""
    scope = str(load_convert_config().get("webwiki_analytics_scope") or "human")
    return scope if scope in ("human", "qualified", "all") else "human"


def set_analytics_scope(scope: str) -> None:
    cfg = load_convert_config()
    cfg["webwiki_analytics_scope"] = str(scope or "human")
    save_convert_config(cfg)


def analytics_time() -> str:
    return str(load_convert_config().get("webwiki_analytics_time") or "09:10")


def set_analytics_time(text: str) -> None:
    cfg = load_convert_config()
    cfg["webwiki_analytics_time"] = str(text or "09:10").strip()
    save_convert_config(cfg)


def set_analytics_api(url: str) -> None:
    """留空表示用内置的官方接口地址。"""
    cfg = load_convert_config()
    cfg["webwiki_analytics_api"] = str(url or "").strip()
    save_convert_config(cfg)


# ==================== GitHub 旧登录态（只读兼容） ====================

def auth_path() -> Path:
    return get_config_dir() / "webwiki_auth.json"


def load_auth() -> dict:
    try:
        data = json.loads(auth_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ==================== 系统代理 ====================

def system_proxy() -> str:
    """读取系统代理（Windows 注册表），没有则回退环境变量；返回 http://host:port。"""
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if enabled and server:
                raw = str(server)
                if "=" in raw:
                    entries = {}
                    for part in raw.split(";"):
                        if "=" in part:
                            name, value = part.split("=", 1)
                            entries[name.strip().lower()] = value.strip()
                    raw = entries.get("https") or entries.get("http") or ""
                if raw:
                    return raw if "://" in raw else f"http://{raw}"
        except Exception:  # noqa: BLE001 - 代理只是加速，读不到就直连
            pass
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            return value if "://" in value else f"http://{value}"
    return ""


# 本机常见代理端口：Clash 7890、v2rayN 10809、旧配置 12450（常已失效）
LOCAL_PROXY_PORTS = (7890, 10809, 12450)
_PROXY_PREFIXES = ("http://", "https://", "socks5://", "socks5h://")


def normalize_proxy(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith(_PROXY_PREFIXES) else f"http://{raw}"


def proxy_alive(proxy: str, timeout: float = 0.4,
                cache: dict[tuple[str, int], bool] | None = None) -> bool:
    """本机代理先探端口：端口不通直接跳过，不算「代理失败」；远端代理不探。"""
    parts = urlsplit(proxy)
    host = parts.hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        return True
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (host, port)
    if cache is not None and key in cache:
        return cache[key]
    try:
        with socket.create_connection((host, port), timeout=timeout):
            alive = True
    except OSError:
        alive = False
    if cache is not None:
        cache[key] = alive
    return alive


def git_config_proxy() -> str:
    """`git config --get http.proxy` 只当候选：本机历史上残留过失效代理。"""
    kwargs = {"capture_output": True, "text": True, "timeout": 5}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([find_git(), "config", "--get", "http.proxy"], **kwargs)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def proxy_candidates() -> list[str]:
    """访问 GitHub 的网络通道，按约定 15 逐档尝试；空串 = 显式直连。

    第一档直连（显式清空 http.proxy）→ 当次指定 / 环境变量 → 系统代理 →
    本机常见端口（先探活）→ `git config http.proxy`（只当候选）。
    """
    channels: list[str] = [""]
    probed: dict[tuple[str, int], bool] = {}

    def add(raw: str) -> None:
        value = normalize_proxy(raw)
        if value and value not in channels and proxy_alive(value, cache=probed):
            channels.append(value)

    for name in ("WEBWIKI_PROXY", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        add(os.environ.get(name, ""))
    add(system_proxy())
    for port in LOCAL_PROXY_PORTS:
        # 混合端口（Clash / mihomo）同时提供 HTTP 与 SOCKS5；本机 SOCKS5 常比
        # HTTP CONNECT 更稳，两个都当候选，探活只按端口算一次
        add(f"http://127.0.0.1:{port}")
        add(f"socks5h://127.0.0.1:{port}")
    add(git_config_proxy())
    return channels


# 图片图床走直连：这些域名都在国内，绕开系统代理反而更快；
# 系统代理通常是给 GitHub 用的，图片再走代理会串行拖慢首屏。
IMAGE_DIRECT_HOSTS = (
    ".docs.qq.com", ".hdslb.com", ".bilibili.com", ".douyinpic.com",
    ".douyincdn.com", ".kwaicdn.com", ".kuaishou.com", ".sinaimg.cn",
    ".weibo.com", ".xiaohongshu.com", ".xhscdn.com", ".qpic.cn",
)


def child_env() -> dict:
    """子进程环境：补系统代理 + 强制 UTF-8 输出（避免 emoji 在 GBK 管道里崩）。

    图片图床加入 NO_PROXY：本地站点经系统代理并发取图时会被串行拖慢
    （实测 10 张缩略图从 3.5s 变成 20s+），直连国内图床反而快。
    """
    env = os.environ.copy()
    proxy = system_proxy()
    if proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.setdefault(name, proxy)
    no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
    parts = [part.strip() for part in no_proxy.split(",") if part.strip()]
    if "*" not in parts:
        for host in IMAGE_DIRECT_HOSTS:
            if host not in parts:
                parts.append(host)
    if parts:
        value = ",".join(parts)
        env["NO_PROXY"] = value
        env["no_proxy"] = value
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ==================== 可执行程序探测 ====================

def _python_candidates() -> list[str]:
    found: list[str] = []
    if not getattr(sys, "frozen", False) and sys.executable:
        exe = Path(sys.executable)
        sibling = exe.with_name("python.exe")
        found.append(str(sibling if sibling.exists() else exe))
    for name in ("python.exe", "python"):
        path = shutil.which(name)
        if path:
            found.append(path)
    if os.name == "nt":
        try:
            import winreg

            best: list[tuple[tuple[int, int], str]] = []
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"Software\Python\PythonCore") as core:
                        index = 0
                        while True:
                            try:
                                version = winreg.EnumKey(core, index)
                            except OSError:
                                break
                            index += 1
                            try:
                                with winreg.OpenKey(core, version + r"\InstallPath") as inst:
                                    folder, _ = winreg.QueryValueEx(inst, "")
                                    exe = Path(folder) / "python.exe"
                                    if exe.is_file():
                                        parts = tuple(int(x) for x in version.split(".")
                                                      if x.isdigit())[:2]
                                        best.append((parts, str(exe)))
                            except (OSError, ValueError):
                                continue
                except OSError:
                    continue
            for _parts, path in sorted(best, reverse=True):
                found.append(path)
        except Exception:  # noqa: BLE001
            pass
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
        try:
            for folder in sorted(local.glob("Python3*"), reverse=True):
                exe = folder / "python.exe"
                if exe.is_file():
                    found.append(str(exe))
        except OSError:
            pass
    seen = set()
    result = []
    for path in found:
        key = os.path.normcase(path)
        if key not in seen and Path(path).is_file():
            seen.add(key)
            result.append(path)
    return result


def find_python() -> str:
    """找系统 Python（用于创建资料站专用虚拟环境）。"""
    candidates = _python_candidates()
    if not candidates:
        raise RuntimeError("未找到 Python 解释器：请先安装 Python 3.10+ 并加入 PATH")
    return candidates[0]


def find_git() -> str:
    path = shutil.which("git")
    if not path and os.name == "nt":
        for candidate in (
                r"C:\Program Files\Git\cmd\git.exe",
                r"C:\Program Files (x86)\Git\cmd\git.exe"):
            if Path(candidate).is_file():
                path = candidate
                break
    if not path:
        raise RuntimeError("未找到 git：请先安装 Git for Windows 并加入 PATH")
    return path


def find_node() -> tuple[str, str]:
    node = shutil.which("node") or shutil.which("node.exe")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("未找到 Node.js / npm：请安装 Node.js 18+ 并加入 PATH")
    return node, npm
