"""内容采集 Windows 计划任务适配（L5）；由 task_center 统一分发。

不持有业务订阅或凭据；源码与打包均经 main.py 的无窗口入口执行。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TASK_NAMES = {"chaohua": "FengMingChaohuaCollect", "douyin_index": "FengMingDouyinIndexCollect"}
FLAGS = {"chaohua": "--chaohua-collect", "douyin_index": "--douyin-index-collect"}


def _task_name(kind: str) -> str:
    if kind not in TASK_NAMES:
        raise ValueError("未知的内容采集任务")
    return TASK_NAMES[kind]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30,
                              encoding="gbk", errors="replace", **kwargs)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 1, "", "系统计划任务暂时不可用")


def _runner_command(kind: str) -> str:
    _task_name(kind)
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, FLAGS[kind]])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return subprocess.list2cmdline([str(exe), str(main_py), FLAGS[kind]])


def install_daily(kind: str, time_str: str) -> tuple[bool, str]:
    task_name = _task_name(kind)
    if not isinstance(time_str, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_str):
        return False, "时间格式应为 HH:MM"
    result = _run(["schtasks", "/Create", "/TN", task_name, "/TR", _runner_command(kind),
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    return ((True, f"每天 {time_str} 自动采集") if result.returncode == 0
            else (False, "计划任务安装失败，请检查系统权限"))


def install_interval(kind: str, minutes: int) -> tuple[bool, str]:
    task_name = _task_name(kind)
    if isinstance(minutes, bool) or not isinstance(minutes, (int, str)):
        return False, "间隔分钟数无效"
    try:
        minutes = int(minutes)
    except ValueError:
        return False, "间隔分钟数无效"
    if not 1 <= minutes <= 1439:
        return False, "间隔需在 1–1439 分钟之间"
    result = _run(["schtasks", "/Create", "/TN", task_name, "/TR", _runner_command(kind),
                   "/SC", "MINUTE", "/MO", str(minutes), "/ST", "00:00", "/F"])
    return ((True, f"每 {minutes} 分钟自动采集") if result.returncode == 0
            else (False, "计划任务安装失败，请检查系统权限"))


def uninstall(kind: str) -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", _task_name(kind), "/F"])
    return ((True, "定时任务已删除，订阅和采集历史保留") if result.returncode == 0
            else (False, "计划任务删除失败，请检查安装状态与系统权限"))


def info(kind: str) -> dict:
    result = _run(["schtasks", "/Query", "/TN", _task_name(kind), "/V", "/FO", "LIST"])
    if result.returncode != 0:
        return {}
    data = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            data[key.strip()] = value.strip()
    return data
