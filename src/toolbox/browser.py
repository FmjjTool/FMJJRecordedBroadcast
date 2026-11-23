"""统一浏览器层：全应用 Playwright launch 的唯一入口。

体积方案（安装包优化 2026-09-05）：
- 安装包不再携带任何浏览器内核（原 QtWebEngine 由内嵌登录页承担、
  Playwright headless shell 226MB 改为按需下载）。
- 启动优先级：系统 Edge → 系统 Chrome → 本地 headless shell（用户目录按需下载）。
- 打包态下 playwright/_impl/_transport.py 会强制 PLAYWRIGHT_BROWSERS_PATH=0
  （只查 exe 旁 driver/package/.local-browsers，见项目记忆 4a48a57），
  本模块在 launch 前统一改为用户目录，headless shell 下载后即可被第 3 级命中。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 降级链：None = 不指定 channel（playwright 默认，即本地 headless shell）
_CHANNELS = ["msedge", "chrome", None]

# 国内镜像优先（Playwright 官方环境变量），失败自动回退官方 CDN
_DOWNLOAD_HOSTS = ["https://npmmirror.com/mirrors/playwright", None]

_APP_DIR_NAME = "FengMingToolbox"


def browsers_dir() -> Path:
    """Playwright 内核目录：FULL 版优先包内 browsers/，否则用户 LocalAppData。

    包内内核只读即可（浏览器用户数据由 Playwright 放临时目录），
    因此安装在 Program Files 下也能直接使用。
    """
    from toolbox import edition

    bundled = edition.bundled_dir("browsers")
    if getattr(sys, "frozen", False) and bundled.is_dir():
        return bundled
    base = Path(os.environ.get("LOCALAPPDATA", "")) or (Path.home() / "AppData" / "Local")
    return base / _APP_DIR_NAME / "browsers"


def ensure_env() -> None:
    """frozen 态把 playwright 浏览器查找目录指向用户目录（幂等）。

    必须在 sync_playwright() 启动前调用；transport 的 env 是启动 driver 进程时
    快照的，事后设置不生效。
    """
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())


def launch(p, headless: bool = True, **kwargs):
    """按 Edge → Chrome → 默认 顺序启动浏览器。

    参数与 playwright.chromium.launch 一致（args/timeout/proxy 等透传）。
    三级全失败时抛出最后一次的异常（调用方原有的 except 分支照常工作）。
    """
    ensure_env()
    last_err: Exception | None = None
    for ch in _CHANNELS:
        try:
            kw = dict(kwargs)
            if ch:
                kw["channel"] = ch
            return p.chromium.launch(headless=headless, **kw)
        except Exception as e:  # noqa: BLE001 - 逐级降级，最终抛出
            last_err = e
    raise last_err  # type: ignore[misc]


def _driver_paths() -> tuple[Path, Path] | None:
    """返回 (node.exe, cli.js)；playwright 打包结构异常时返回 None。"""
    try:
        import playwright
        pkg = Path(playwright.__file__).resolve().parent
    except Exception:
        return None
    node = pkg / "driver" / "node.exe"
    cli = pkg / "driver" / "package" / "cli.js"
    if node.is_file() and cli.is_file():
        return node, cli
    return None


def headless_shell_installed() -> bool:
    """本地 headless shell 是否已下载（降级链第 3 级是否可用）。"""
    lb = browsers_dir()
    return any(lb.glob("chromium_headless_shell-*"))


def headless_shell_dirs() -> list[Path]:
    """本地下载的 Playwright headless shell 目录（不含系统 Edge/Chrome）。"""
    base = browsers_dir()
    if not base.is_dir():
        return []
    return [path for path in base.glob("chromium_headless_shell-*")
            if path.is_dir() and not path.is_symlink()]


def headless_shell_size() -> int:
    total = 0
    for folder in headless_shell_dirs():
        for path in folder.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def uninstall_headless_shell() -> tuple[bool, int, str]:
    """只删除用户目录下下载的采集内核；系统 Edge/Chrome 与 FULL 内置不处理。"""
    from toolbox import edition

    base = browsers_dir().resolve()
    if getattr(sys, "frozen", False):
        try:
            if base == edition.bundled_dir("browsers").resolve():
                return False, 0, "FULL 版内置采集引擎不可卸载"
        except Exception:
            pass
    folders = headless_shell_dirs()
    if not folders:
        return False, 0, "未下载本地采集引擎"
    freed = 0
    failed = ""
    for folder in folders:
        try:
            folder = folder.resolve()
            if folder.parent != base or not folder.name.startswith("chromium_headless_shell-"):
                continue
            size = sum(path.stat().st_size for path in folder.rglob("*")
                       if path.is_file())
            shutil.rmtree(folder)
            freed += size
        except OSError as exc:
            failed = str(exc)
    if failed:
        return False, freed, f"部分内核文件无法删除（可能正在使用）：{failed}"
    return True, freed, "已卸载本地采集引擎，可重新下载"


def download_headless_shell(log=None) -> bool:
    """按需下载 headless shell 到用户目录（国内镜像优先）。

    供设置页手动入口 / 首次使用兜底调用；约 91MB，调用方须先征得用户同意。
    """
    paths = _driver_paths()
    if not paths:
        if log:
            log("未找到 Playwright 驱动，无法下载")
        return False
    node, cli = paths
    ensure_env()
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())
    for host in _DOWNLOAD_HOSTS:
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = host or ""
        if not host:
            env.pop("PLAYWRIGHT_DOWNLOAD_HOST", None)
        try:
            if log:
                log(f"开始下载（{'国内镜像' if host else '官方源'}）…")
            r = subprocess.run(
                [str(node), str(cli), "install", "chromium-headless-shell"],
                env=env, capture_output=True, text=True, timeout=1200,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0 and headless_shell_installed():
                if log:
                    log("下载完成")
                return True
            if log:
                log(f"下载失败：{(r.stderr or r.stdout).strip()[:200]}")
        except Exception as e:  # noqa: BLE001 - 镜像失败换下一个源
            if log:
                log(f"下载异常：{e}")
    return False


def detect() -> dict:
    """检测浏览器环境（设置页「浏览器环境」展示用，不启动浏览器）。"""
    import winreg

    result = {"edge": None, "chrome": None, "shell": headless_shell_installed()}
    # Edge/Chrome 固定注册表位置取版本（不 launch，毫秒级）
    keys = {
        "edge": (r"SOFTWARE\Microsoft\Edge\BLBeacon", "version"),
        "chrome": (r"SOFTWARE\Google\Chrome\BLBeacon", "version"),
    }
    for name, (subkey, value) in keys.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
                result[name] = winreg.QueryValueEx(k, value)[0]
        except OSError:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as k:
                    result[name] = winreg.QueryValueEx(k, value)[0]
            except OSError:
                result[name] = None
    return result
