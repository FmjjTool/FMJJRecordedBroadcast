"""Qt-free use cases for the global-settings Python dependency installer.

Only the fixed dependency identifiers below are accepted. Installs and fresh
import checks use the running application's interpreter; no credentials, config
or component files are read or written. Packaged applications cannot use pip.
Process output is translated to safe stages/errors rather than exposing paths,
download URLs or environment details to either desktop or Web clients.
"""

from __future__ import annotations

import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


# key: (display name, fixed pip requirements, import checks, distribution name)
_DEPENDENCIES = {
    "PySide6": ("PySide6", ("PySide6",), ("PySide6",), "PySide6"),
    "requests": ("requests", ("requests",), ("requests",), "requests"),
    "PIL": ("Pillow", ("Pillow",), ("PIL",), "Pillow"),
    "bs4": ("BeautifulSoup4", ("beautifulsoup4",), ("bs4",), "beautifulsoup4"),
    "lxml": ("lxml", ("lxml",), ("lxml", "lxml.etree"), "lxml"),
    "httpx": ("httpx", ("httpx",), ("httpx",), "httpx"),
    "openpyxl": ("openpyxl", ("openpyxl",), ("openpyxl",), "openpyxl"),
    "mutagen": ("mutagen", ("mutagen",), ("mutagen",), "mutagen"),
    "pyzipper": ("证据加密组件", ("pyzipper==0.4.0", "pycryptodomex==3.24.2"),
                  ("pyzipper", "Cryptodome.Cipher.AES"), "pyzipper"),
    "websocket": ("websocket-client", ("websocket-client",),
                   ("websocket",), "websocket-client"),
    "playwright": ("playwright", ("playwright",),
                    ("playwright.sync_api",), "playwright"),
}
_INSTALL_LOCK = threading.Lock()
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DependencyInstallError(RuntimeError):
    """A safe, actionable message for the settings UI."""


class DependencyInstallCancelled(DependencyInstallError):
    """The caller cancelled this install/check."""


def _spec(key):
    if not isinstance(key, str) or key not in _DEPENDENCIES:
        raise ValueError("请选择依赖列表中的安装项")
    return _DEPENDENCIES[key]


def _python_executable() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
        if not executable.is_file():
            raise DependencyInstallError("当前运行环境缺少安装程序，请修复 Python 环境后重试")
    return str(executable)


def _cancelled(cancel) -> bool:
    return bool(cancel is not None and
                (cancel.is_set() if hasattr(cancel, "is_set") else cancel()))


def _check_cancel(cancel):
    if _cancelled(cancel):
        raise DependencyInstallCancelled("已取消依赖安装；已安装的部分保留，可重新安装")


def _report(progress, percent, message):
    if progress is not None:
        progress(percent, message)


def _own_process(process):
    """Use the existing Job Object helper only for this newly created child."""
    from toolbox.process_tree import assign_job, create_kill_job, terminate_job
    job = create_kill_job()
    if job is not None and not assign_job(job, process):
        terminate_job(job)
        return None
    return job


def _stop_process(process, job):
    if job is not None:
        from toolbox.process_tree import terminate_job
        terminate_job(job)
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                raise DependencyInstallError("安装子任务未能停止，请稍后检查安装状态") from None


def _stage(line):
    line = line.lower()
    if "installing collected" in line or "attempting uninstall" in line:
        return "正在安装依赖包…"
    if "downloading" in line:
        return "正在下载依赖包…"
    if "collecting" in line or "looking in indexes" in line:
        return "正在检查安装来源与依赖…"
    if "already satisfied" in line:
        return "正在校验已安装的依赖…"
    if "retrying" in line:
        return "下载连接正在重试，可停止后检查网络…"
    if "building wheel" in line or "preparing metadata" in line:
        return "正在准备安装文件…"
    return None


def _error_reason(output):
    text = str(output or "").lower()
    if "no module named pip" in text:
        return "当前 Python 环境缺少安装器，请修复此环境后重试"
    if any(word in text for word in ("permission denied", "access is denied", "winerror 5", "not writeable")):
        return "当前运行环境没有写入权限，请检查权限后重试"
    if "no matching distribution" in text or "could not find a version" in text:
        return "没有适合当前 Python 版本的安装包，请检查运行环境后重试"
    if any(word in text for word in ("sslerror", "certificate verify", "tls")):
        return "下载连接验证失败，请检查网络证书后重试"
    if any(word in text for word in ("connection", "timed out", "proxy", "network", "name resolution")):
        return "下载连接失败，请检查网络连接或代理设置后重试"
    return "安装程序返回错误，请检查网络连接和环境权限后重试"


def _run_process(args, *, progress=None, cancel=None, timeout=300):
    _check_cancel(cancel)
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    process = None
    job = None
    reader = None
    stopped = threading.Event()
    finished = threading.Event()
    messages = queue.Queue(maxsize=64)
    output = deque(maxlen=64)
    deadline = time.monotonic() + max(1.0, min(float(timeout), 900.0))
    try:
        process = subprocess.Popen(
            list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env, cwd=str(Path(args[0]).parent),
            creationflags=_NO_WINDOW, shell=False)
        job = _own_process(process)

        def read_output():
            try:
                while not stopped.is_set():
                    line = process.stdout.readline(4096)
                    if not line:
                        break
                    while not stopped.is_set():
                        try:
                            messages.put(line, timeout=0.2)
                            break
                        except queue.Full:
                            continue
            except (OSError, ValueError):
                pass
            finally:
                finished.set()

        reader = threading.Thread(target=read_output, name="dependency-output", daemon=True)
        reader.start()
        previous_stage = None
        while True:
            _check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise DependencyInstallError("依赖安装超时，已停止安装；请检查网络后重试")
            try:
                line = messages.get(timeout=0.15)
            except queue.Empty:
                line = None
            if line is not None:
                output.append(line)
                stage = _stage(line)
                if stage and stage != previous_stage:
                    _report(progress, None, stage)
                    previous_stage = stage
            if finished.is_set() and messages.empty() and process.poll() is not None:
                return process.wait(timeout=3), "".join(output)
    except DependencyInstallError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise DependencyInstallError("无法启动依赖安装程序，请检查 Python 环境后重试") from None
    finally:
        stopped.set()
        if process is not None:
            _stop_process(process, job)
            if reader is not None:
                reader.join(timeout=1)
            if process.stdout is not None:
                process.stdout.close()


def _check_script(key):
    _name, _packages, modules, distribution = _spec(key)
    return (
        "import importlib, json\n"
        "from importlib.metadata import version\n"
        f"modules = {list(modules)!r}\n"
        "for name in modules: importlib.import_module(name)\n"
        f"try: value = version({distribution!r})\n"
        "except Exception: value = getattr(importlib.import_module(modules[0]), '__version__', '已安装')\n"
        "print(json.dumps({'installed': True, 'version': value}))\n"
    )


def dependency_status(key="pyzipper", *, cancel=None) -> dict:
    """Verify imports in the same interpreter, independent of cached failures."""
    name, _packages, modules, distribution = _spec(key)
    supported = not getattr(sys, "frozen", False)
    data = {"key": key, "name": name, "supported": supported,
            "installed": False, "version": "", "reason": ""}
    if not supported:
        try:
            from importlib.metadata import version
            for module in modules:
                importlib.import_module(module)
            try:
                value = version(distribution)
            except Exception:
                value = getattr(importlib.import_module(modules[0]), "__version__", "已安装")
            data.update(installed=True, version=str(value)[:80])
        except Exception:
            data["reason"] = "未内置，请重装软件"
        return data
    try:
        code, output = _run_process(
            [_python_executable(), "-c", _check_script(key)],
            timeout=30, cancel=cancel)
        if code == 0:
            for line in reversed(output.splitlines()):
                try:
                    result = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(result, dict) and result.get("installed") is True:
                    data.update(installed=True, version=str(result.get("version") or "已安装")[:80])
                    return data
        data["reason"] = "依赖或运行组件尚未准备好，可点击安装后重试"
    except DependencyInstallCancelled:
        raise
    except DependencyInstallError as exc:
        data["reason"] = str(exc)
    return data


def install_dependency(key, *, progress=None, cancel=None, timeout=300) -> dict:
    """Install one allowlisted dependency, then verify it in a fresh process.

    ``progress(None, message)`` means an indeterminate stage, never a guessed
    percentage. Cancellation only stops child processes created by this call;
    packages already installed are retained and can be checked/reinstalled.
    """
    name, packages, _modules, _distribution = _spec(key)
    if getattr(sys, "frozen", False):
        raise DependencyInstallError("未内置，请重装软件；安装版不支持单独安装 Python 依赖")
    _check_cancel(cancel)
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise DependencyInstallError("已有依赖安装在进行中，请结束后重试")
    try:
        _report(progress, None, "正在启动" + name + "安装…")
        command = [_python_executable(), "-m", "pip", "install", *packages,
                   "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
                   "--timeout", "20", "--retries", "1"]
        code, output = _run_process(command, progress=progress, cancel=cancel, timeout=timeout)
        if code:
            raise DependencyInstallError(name + "安装失败：" + _error_reason(output))
        _check_cancel(cancel)
        importlib.invalidate_caches()
        _report(progress, None, "安装程序已结束，正在校验" + name + "…")
        status = dependency_status(key, cancel=cancel)
        if not status["installed"]:
            raise DependencyInstallError(name + "安装校验未通过：" + status["reason"])
        _report(progress, 100, name + "已安装并通过校验")
        return {**status, "summary": name + "已安装并通过校验"}
    finally:
        _INSTALL_LOCK.release()
