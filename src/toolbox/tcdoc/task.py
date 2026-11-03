"""腾讯文档监控 · Windows 计划任务（每天 / 按间隔定时采集）。

任务名固定 FengMingTCDocMonitor，与其他工具的定时任务互不影响。
命令：
- 开发模式：pythonw.exe  <src>/toolbox/tcdoc/run_job.py
- 打包模式：FengMingToolbox.exe --tcdoc-collect
两者最终都走 job.main()（无界面采集 + 写日志）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "FengMingTCDocMonitor"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """调用 schtasks；被系统策略/权限拦住时返回失败结果而不是抛异常。"""
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="gbk", errors="ignore")
    except (OSError, ValueError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"无法调用系统计划任务：{exc}")


def _runner_command() -> str:
    """计划任务要执行的完整命令行（带引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tcdoc-collect'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent / "run_job.py"
    return f'"{exe}" "{script}"'


def install_daily(time_str: str) -> tuple[bool, str]:
    """安装 / 更新每天定时采集任务，返回 (是否成功, 提示)。"""
    command = _runner_command()
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, f"每天 {time_str} 自动采集"


def install_interval(minutes: int) -> tuple[bool, str]:
    """安装 / 更新按分钟间隔重复的采集任务（60 的倍数会显示成小时）。"""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False, "间隔分钟数无效"
    if not 1 <= minutes <= 1439:
        return False, "间隔需在 1 ~ 1439 分钟之间（24 小时请用每天模式）"
    command = _runner_command()
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
                   "/SC", "MINUTE", "/MO", str(minutes), "/ST", "00:00", "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    desc = f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"
    return True, f"{desc}自动采集"


def uninstall() -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, "定时任务已删除"


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


def next_run_text() -> str:
    data = info()
    if not data:
        return "定时任务未安装"
    nxt = data.get("Next Run Time") or data.get("下次运行时间") or "-"
    return f"定时任务已安装，下次运行：{nxt}"
