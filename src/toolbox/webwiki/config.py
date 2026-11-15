"""本地资料站 · 路径、配置、代理与解释器探测。

仓库默认落在「统一导出目录/本地资料站」下（download/ 已在 .gitignore 里），
GitHub 登录态单独放 config/webwiki_auth.json，不进导出目录、不进仓库。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

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


# ==================== GitHub 登录态 ====================

def auth_path() -> Path:
    return get_config_dir() / "webwiki_auth.json"


def load_auth() -> dict:
    try:
        data = json.loads(auth_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def token() -> str:
    return str(load_auth().get("token") or "")


def save_auth(access_token: str, user: dict | None = None) -> dict:
    user = user or {}
    data = {
        "token": str(access_token or ""),
        "login": str(user.get("login") or ""),
        "name": str(user.get("name") or ""),
        "avatar": str(user.get("avatar_url") or ""),
    }
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def clear_auth() -> None:
    try:
        auth_path().unlink()
    except OSError:
        pass


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


def child_env() -> dict:
    """子进程环境：补系统代理 + 强制 UTF-8 输出（避免 emoji 在 GBK 管道里崩）。"""
    env = os.environ.copy()
    proxy = system_proxy()
    if proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.setdefault(name, proxy)
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
