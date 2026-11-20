"""软件更新：从 GitHub Releases 检查新版本、下载安装包、校验 SHA256、静默安装。

设计约束：
- 只有「安装版」才允许自动安装：读注册表 HKLM/HKCU\\Software\\FengMingToolbox 的
  InstallPath，与当前 sys.executable 所在目录比对。绿色版 / 源码运行只提示手动下载，
  否则安装器会在 Program Files 里再装出第二套程序。
- 程序常驻托盘，关闭主窗口只是隐藏，exe 仍被占用。因此安装前必须真正退出进程，
  由外部 cmd 等 PID 消失后再运行安装器（/VERYSILENT），安装器 [Run] 段负责拉起新版本。
- 发布流程（build.py）会在 dist/installer 生成 SHA256SUMS.txt，需一并上传到 Release；
  下载后强校验，Release 缺校验文件时不走自动安装。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = "FmjjTool/FMJJRecordedBroadcast"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
INSTALL_REG_KEY = r"Software\FengMingToolbox"
SETUP_PREFIX = "FengMingToolbox_Setup_"
SHA_SUMS_NAME = "SHA256SUMS.txt"
CHECK_INTERVAL = 24 * 3600  # 启动静默检查的最小间隔（秒）


class UpdateError(RuntimeError):
    """检查 / 下载 / 校验失败，message 直接展示给用户。"""


@dataclass
class ReleaseInfo:
    version: str                 # 规范化版本号，如 3.0.2
    tag: str
    name: str
    notes: str
    page_url: str
    asset_name: str              # 安装包文件名
    asset_url: str
    asset_size: int = 0
    sha_name: str = ""           # 校验文件名（空 = 该 Release 未提供）
    sha_url: str = ""
    published_at: str = ""
    assets: list = field(default_factory=list)

    @property
    def size_text(self) -> str:
        if self.asset_size <= 0:
            return "未知大小"
        return f"{self.asset_size / 1024 / 1024:.1f} MB"


# ==================== 版本比较 ====================

def parse_version(text: str) -> tuple:
    """'v3.0.10' → (3, 0, 10)；取数字段，忽略前后缀与预发布标记。"""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) if nums else ()


def is_newer(remote: str, current: str) -> bool:
    """按数字段比较（避免 3.0.10 被判成小于 3.0.9）。"""
    r, c = parse_version(remote), parse_version(current)
    if not r:
        return False
    width = max(len(r), len(c))
    return r + (0,) * (width - len(r)) > c + (0,) * (width - len(c))


# ==================== 查询 Release ====================

def _session_headers(current: str) -> dict:
    return {
        "User-Agent": f"FengMingToolbox/{current} (+{RELEASES_PAGE})",
        "Accept": "application/vnd.github+json",
    }


def _pick_setup_asset(assets: Iterable[dict]) -> Optional[dict]:
    for a in assets:
        name = str(a.get("name") or "")
        if name.lower().endswith(".exe") and name.startswith(SETUP_PREFIX):
            return a
    return None


def _pick_sha_asset(assets: Iterable[dict]) -> Optional[dict]:
    for a in assets:
        name = str(a.get("name") or "")
        if name.lower() == SHA_SUMS_NAME.lower() or name.lower().endswith(".sha256"):
            return a
    return None


def fetch_latest(current: str = "", timeout: float = 6.0,
                 proxies: Optional[dict] = None) -> ReleaseInfo:
    """拉取 latest release；网络/接口错误统一抛 UpdateError（消息可直接展示）。"""
    import requests

    try:
        resp = requests.get(API_LATEST, headers=_session_headers(current or "0"),
                            timeout=(4.0, timeout), proxies=proxies)
    except requests.Timeout:
        raise UpdateError("连接 GitHub 超时，请检查网络或代理设置")
    except requests.RequestException as exc:
        raise UpdateError(f"无法访问 GitHub：{exc.__class__.__name__}")
    if resp.status_code == 403:
        raise UpdateError("GitHub 接口返回 403（请求过于频繁，请稍后再试）")
    if resp.status_code == 404:
        raise UpdateError("仓库暂无 Release 版本")
    if resp.status_code != 200:
        raise UpdateError(f"GitHub 接口返回 HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise UpdateError("GitHub 返回内容无法解析")

    assets = data.get("assets") or []
    setup = _pick_setup_asset(assets)
    if setup is None:
        raise UpdateError("该版本未上传安装包（Setup.exe）")
    sha = _pick_sha_asset(assets)
    tag = str(data.get("tag_name") or "")
    return ReleaseInfo(
        version=".".join(str(n) for n in parse_version(tag)) or tag.lstrip("v"),
        tag=tag,
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        asset_name=str((setup or {}).get("name") or ""),
        asset_url=str((setup or {}).get("browser_download_url") or ""),
        asset_size=int((setup or {}).get("size") or 0),
        sha_name=str((sha or {}).get("name") or ""),
        sha_url=str((sha or {}).get("browser_download_url") or ""),
        published_at=str(data.get("published_at") or ""),
        assets=assets,
    )


def check_for_update(current: str, timeout: float = 6.0,
                     proxies: Optional[dict] = None):
    """返回 (ReleaseInfo | None, 错误消息 | None)；有新版本才返回 info。"""
    info = fetch_latest(current, timeout=timeout, proxies=proxies)
    return (info, None) if is_newer(info.version, current) else (None, None)


# ==================== 下载与校验 ====================

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha_sums(text: str, filename: str) -> str:
    """从 'hash  filename' 列表里取指定文件的哈希（兼容 *filename / 大小写）。"""
    want = filename.strip().lower()
    for line in str(text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            name = parts[-1].lstrip("*").strip().lower()
            if name == want:
                return parts[0].lower()
    return ""


def fetch_expected_sha(info: ReleaseInfo, timeout: float = 10.0,
                       proxies: Optional[dict] = None) -> str:
    import requests

    if not info.sha_url:
        raise UpdateError("该版本未提供 SHA256SUMS.txt，无法校验安装包，请手动下载")
    try:
        resp = requests.get(info.sha_url, headers=_session_headers(info.version),
                            timeout=(4.0, timeout), proxies=proxies)
        resp.raise_for_status()
    except Exception as exc:
        raise UpdateError(f"校验文件下载失败：{exc.__class__.__name__}")
    expected = parse_sha_sums(resp.text, info.asset_name)
    if not expected:
        raise UpdateError("校验文件里没有该安装包的记录，请手动下载")
    return expected


def download(info: ReleaseInfo, dest_dir: Path,
             progress: Optional[Callable[[int, int], None]] = None,
             cancel=None, timeout: float = 30.0,
             proxies: Optional[dict] = None) -> Path:
    """下载安装包并校验 SHA256；progress(已下载字节, 总字节)。"""
    import requests

    if not info.asset_url:
        raise UpdateError("该版本没有可下载的安装包")
    expected = fetch_expected_sha(info, proxies=proxies)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / info.asset_name
    partial = target.with_suffix(target.suffix + ".part")
    h = hashlib.sha256()
    done = 0
    try:
        with requests.get(info.asset_url, headers=_session_headers(info.version),
                          stream=True, timeout=(6.0, timeout), proxies=proxies) as resp:
            if resp.status_code != 200:
                raise UpdateError(f"安装包下载失败：HTTP {resp.status_code}")
            total = int(resp.headers.get("Content-Length") or info.asset_size or 0)
            with open(partial, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("已取消下载")
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        if info.asset_size and done != info.asset_size:
            raise UpdateError(f"安装包大小不符（{done} / {info.asset_size} 字节）")
        if h.hexdigest().lower() != expected:
            raise UpdateError("SHA256 校验失败，安装包可能损坏或被篡改")
        os.replace(partial, target)
        return target
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"安装包下载中断：{exc.__class__.__name__}")
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"安装包写入失败：{exc}")


# ==================== 安装形态判断 ====================

def installed_dir() -> Optional[Path]:
    """安装器写入的安装目录；绿色版/从未安装过返回 None。"""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, INSTALL_REG_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "InstallPath")
        except OSError:
            continue
        if value and os.path.isdir(value):
            return Path(value)
    return None


def app_dir() -> Path:
    """当前运行目录：打包后是 exe 所在目录，源码运行是项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def self_update_supported() -> tuple:
    """(是否可自动安装, 不能时的原因)。"""
    if not getattr(sys, "frozen", False):
        return False, "当前以源码方式运行，只能检查与下载"
    target = installed_dir()
    if target is None:
        return False, "未找到安装记录（绿色版），请手动下载安装包"
    if os.path.normcase(str(target.resolve())) != os.path.normcase(str(app_dir())):
        return False, "当前运行位置与安装记录不一致，请手动下载安装包"
    return True, ""


# ==================== 静默安装 ====================

def build_wait_script(installer: Path, wait_pid: int, log_path: Optional[Path] = None) -> str:
    """生成等本进程退出后再安装的 cmd 脚本内容。"""
    log = f' > "{log_path}" 2>&1' if log_path else ""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        f'rem 等待 PID {wait_pid} 退出后再覆盖安装（程序运行中 exe 被占用）\r\n'
        "set /a tries=0\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {wait_pid}" 2>nul | find "{wait_pid}" >nul\r\n'
        "if errorlevel 1 goto install\r\n"
        "set /a tries+=1\r\n"
        "if %tries% GEQ 300 goto install\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto wait\r\n"
        ":install\r\n"
        f'start "" "{installer}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART{log}\r\n'
        'del "%~f0" >nul 2>&1\r\n'
    )


def launch_installer(installer: Path, wait_pid: int) -> Path:
    """写临时脚本并脱离启动；调用方随后应立即退出进程。"""
    installer = Path(installer)
    if not installer.is_file():
        raise UpdateError("安装包不存在，请重新下载")
    temp = Path(tempfile.gettempdir())
    script = temp / f"FengMingToolbox_update_{wait_pid}.cmd"
    log = temp / "FengMingToolbox_update.log"
    script.write_text(build_wait_script(installer, wait_pid, log), encoding="gbk",
                      errors="replace")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True)
    return script


def download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "FengMingToolbox_update"


def should_check_now(last_check: float, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    try:
        last = float(last_check or 0)
    except (TypeError, ValueError):
        last = 0.0
    return now - last >= CHECK_INTERVAL