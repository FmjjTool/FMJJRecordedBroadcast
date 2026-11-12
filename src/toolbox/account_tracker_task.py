"""网媒/音乐账号追踪 · Windows 计划任务（每天 / 按间隔定时采集）。

两个页面各一个独立任务，与其他工具的定时任务互不影响：
- 网媒账号追踪：FengMingTrackerMonitor
- 音乐账号追踪：FengMingMusicTrackerMonitor
命令：
- 开发模式：pythonw.exe  <项目>/main.py --tracker-collect / --music-collect
- 打包模式：FengMingToolbox.exe --tracker-collect / --music-collect
两者最终都走 toolbox.account_tracker_job.main()（无界面采集 + 写日志）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAMES = {
    "tracker": "FengMingTrackerMonitor",
    "music": "FengMingMusicTrackerMonitor",
}
KIND_NAMES = {"tracker": "网媒账号追踪", "music": "音乐账号追踪"}
_CLI_FLAGS = {"tracker": "--tracker-collect", "music": "--music-collect"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """调用 schtasks；被系统策略/权限拦住时返回失败结果而不是抛异常。"""
    # pythonw / 无控制台 exe 里直接启动 schtasks 会闪一个控制台窗口
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="gbk", errors="ignore", **kwargs)
    except (OSError, ValueError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"无法调用系统计划任务：{exc}")


def _task_name(kind: str) -> str:
    if kind not in TASK_NAMES:
        raise ValueError(f"未知的追踪类型：{kind}")
    return TASK_NAMES[kind]


def _runner_command(kind: str) -> str:
    """计划任务要执行的完整命令行（带引号）。"""
    flag = _CLI_FLAGS[kind]
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {flag}'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{exe}" "{main_py}" {flag}'


def install_daily(kind: str, time_str: str) -> tuple[bool, str]:
    """安装 / 更新每天定时采集任务，返回 (是否成功, 提示)。"""
    command = _runner_command(kind)
    result = _run(["schtasks", "/Create", "/TN", _task_name(kind), "/TR", command,
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, f"每天 {time_str} 自动采集"


def install_interval(kind: str, minutes: int) -> tuple[bool, str]:
    """安装 / 更新按分钟间隔重复的采集任务（60 的倍数会显示成小时）。"""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False, "间隔分钟数无效"
    if not 1 <= minutes <= 1439:
        return False, "间隔需在 1 ~ 1439 分钟之间（24 小时请用每天模式）"
    command = _runner_command(kind)
    result = _run(["schtasks", "/Create", "/TN", _task_name(kind), "/TR", command,
                   "/SC", "MINUTE", "/MO", str(minutes), "/ST", "00:00", "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    desc = f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"
    return True, f"{desc}自动采集"


def uninstall(kind: str) -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", _task_name(kind), "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, "定时任务已删除，关闭软件后不再采集"


def info(kind: str) -> dict:
    """解析 schtasks 输出（兼容中英文系统），未安装 / 查询失败返回空 dict。"""
    try:
        result = _run(["schtasks", "/Query", "/TN", _task_name(kind), "/V", "/FO", "LIST"])
    except Exception:  # noqa: BLE001 - 任何异常都按「查不到」处理
        return {}
    if result.returncode != 0:
        return {}
    data: dict = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def status(kind: str) -> tuple[bool, str]:
    """(是否已安装, 给界面显示的说明文本)。"""
    data = info(kind)
    if not data:
        return False, "定时任务未安装（关闭软件不会采集）"
    nxt = data.get("Next Run Time") or data.get("下次运行时间") or "-"
    sched = data.get("Schedule Type") or data.get("计划类型") or ""
    prefix = f"定时任务已安装（{sched}）" if sched else "定时任务已安装"
    return True, f"{prefix}，下次运行：{nxt}"
