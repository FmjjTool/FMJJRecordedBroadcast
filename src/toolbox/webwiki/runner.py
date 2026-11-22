"""本地资料站 · 子进程流式执行（日志逐行回调，供页面实时显示）。"""

from __future__ import annotations

import os
import subprocess

from . import config


class RunError(RuntimeError):
    """外部命令执行失败。"""


def run_stream(cmd: list[str], cwd, log, env: dict | None = None,
               title: str = "", check: bool = True) -> tuple[int, list[str]]:
    """执行命令并把 stdout/stderr 逐行交给 log；失败时抛 RunError。"""
    cmd = [str(item) for item in cmd]
    if title:
        log(f"▶ {title}")
    log("$ " + " ".join(cmd))
    kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env or config.child_env(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    lines: list[str] = []
    try:
        for raw in proc.stdout or []:
            line = raw.rstrip()
            if not line:
                continue
            lines.append(line)
            log(line)
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if check and rc:
        tail = lines[-1] if lines else "无输出"
        raise RunError(f"{title or cmd[0]} 失败（退出码 {rc}）：{tail}")
    return rc, lines


def capture(cmd: list[str], cwd, env: dict | None = None, timeout: int = 30,
            merge_stderr: bool = True) -> tuple[int, str]:
    """静默执行命令（用于读取 git 状态），返回 (退出码, 输出)。

    merge_stderr=False 时丢弃 stderr：git 的 warning（如 safe.directory、
    ignore 文件不可读）不能混进 porcelain 输出，否则会被误判成"有本地改动"。
    """
    kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env or config.child_env(),
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([str(item) for item in cmd], **kwargs)
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
