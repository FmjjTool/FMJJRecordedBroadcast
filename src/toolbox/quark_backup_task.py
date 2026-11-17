"""夸克网盘备份 · Windows 计划任务（每天 / 按间隔自动备份）。

命令：
- 开发模式：pythonw.exe  <项目>/main.py --quark-backup
- 打包模式：FengMingToolbox.exe --quark-backup
两者最终都走 toolbox.quark.job.main()（无界面备份 + 写日志）。

与腾讯文档监控、账号追踪的计划任务互不影响，任务名独立：
- FengMingQuarkBackup
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "FengMingQuarkBackup"
CLI_FLAG = "--quark-backup"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """调用 schtasks；被系统策略/权限拦住时返回失败结果而不是抛异常。"""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="gbk", errors="ignore", **kwargs)
    except (OSError, ValueError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"无法调用系统计划任务：{exc}")


def _runner_command() -> str:
    """计划任务要执行的完整命令行（带引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {CLI_FLAG}'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{exe}" "{main_py}" {CLI_FLAG}'


def install_daily(time_str: str) -> tuple[bool, str]:
    """安装 / 更新每天自动备份任务。"""
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _runner_command(),
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, f"每天 {time_str} 自动备份"


def install_interval(minutes: int) -> tuple[bool, str]:
    """安装 / 更新按分钟间隔重复的备份任务（60 的倍数显示成小时）。"""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False, "间隔分钟数无效"
    if not 1 <= minutes <= 1439:
        return False, "间隔需在 1 ~ 1439 分钟之间（24 小时请用每天模式）"
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _runner_command(),
                   "/SC", "MINUTE", "/MO", str(minutes), "/ST", "00:00", "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    desc = f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"
    return True, f"{desc}自动备份"


def uninstall() -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, "定时任务已删除，关闭软件后不再自动备份"


def info() -> dict:
    """解析 schtasks 输出（兼容中英文系统），未安装 / 查询失败返回空 dict。"""
    try:
        result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
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


def status() -> tuple[bool, str]:
    """(是否已安装, 给界面显示的说明文本)。"""
    data = info()
    if not data:
        return False, "定时任务未安装（关闭软件不会自动备份）"
    nxt = data.get("Next Run Time") or data.get("下次运行时间") or "-"
    sched = data.get("Schedule Type") or data.get("计划类型") or ""
    prefix = f"定时任务已安装（{sched}）" if sched else "定时任务已安装"
    return True, f"{prefix}，下次运行：{nxt}"


def apply_schedule(mode: str, time_str: str, minutes: int) -> tuple[bool, str]:
    """按计划设置安装任务（daily / interval）。"""
    if str(mode) == "interval":
        return install_interval(minutes)
    return install_daily(time_str)
