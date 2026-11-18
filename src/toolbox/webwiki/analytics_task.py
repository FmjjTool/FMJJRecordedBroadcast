"""站点访问统计 · Windows 计划任务（每天定时采集一次）。

任务名固定 FengMingWebwikiAnalytics，与其他工具的定时任务互不影响。
命令：
- 开发模式：pythonw.exe  <src>/toolbox/webwiki/analytics_job.py
- 打包模式：FengMingToolbox.exe --webwiki-analytics
两者最终都走 analytics_job.main()（无界面采集 + 写日志）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TASK_NAME = "FengMingWebwikiAnalytics"
TASK_NAMES = (TASK_NAME, TASK_NAME + "_2", TASK_NAME + "_3")


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


def _runner_command() -> str:
    """计划任务要执行的完整命令行（带引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --webwiki-analytics'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent / "analytics_job.py"
    return f'"{exe}" "{script}"'


def _normalize_times(time_str: str, extra_times=()) -> tuple[list[str], str]:
    times: list[str] = []
    for value in [time_str, *list(extra_times or [])]:
        value = str(value or "").strip()
        if not value:
            continue
        try:
            hour, minute = (int(part) for part in value.split(":", 1))
            assert 0 <= hour <= 23 and 0 <= minute <= 59
        except (ValueError, AssertionError):
            return [], f"时间格式应为 24 小时制 HH:MM：{value}"
        if value not in times:
            times.append(value)
    if not times:
        return [], "至少需要一个每天执行时间"
    return times[:len(TASK_NAMES)], ""


def install_daily(time_str: str, extra_times=()) -> tuple[bool, str]:
    """安装 / 更新每天定时采集任务（最多三个时间点），返回 (是否成功, 提示)。"""
    times, error = _normalize_times(time_str, extra_times)
    if error:
        return False, error
    command = _runner_command()
    errors = []
    for index, value in enumerate(times):
        result = _run(["schtasks", "/Create", "/TN", TASK_NAMES[index],
                       "/TR", command, "/SC", "DAILY", "/ST", value, "/F"])
        if result.returncode != 0:
            errors.append((result.stdout or result.stderr or "未知错误").strip())
    # 补采时间减少时，删掉多余的旧任务
    for index in range(len(times), len(TASK_NAMES)):
        _run(["schtasks", "/Delete", "/TN", TASK_NAMES[index], "/F"])
    if errors:
        return False, errors[0]
    return True, "每天 " + " / ".join(sorted(times)) + " 自动采集站点统计"


def uninstall() -> tuple[bool, str]:
    results = [_run(["schtasks", "/Delete", "/TN", name, "/F"])
               for name in TASK_NAMES]
    if all(result.returncode != 0 for result in results):
        return False, (results[0].stdout or results[0].stderr
                       or "未知错误").strip()
    return True, "定时任务已删除"


def _query_task(name: str) -> dict:
    try:
        result = _run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"])
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


def _next_run_dt(text: str):
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def info() -> dict:
    """解析全部时间点的 schtasks 输出；返回主任务数据并带最早下次运行时间。"""
    queried = [(name, _query_task(name)) for name in TASK_NAMES]
    installed = [(name, data) for name, data in queried if data]
    if not installed:
        return {}
    primary = next((data for name, data in installed if name == TASK_NAME),
                   installed[0][1])
    next_values = []
    for _name, data in installed:
        text = (data.get("Next Run Time") or data.get("下次运行时间") or "")
        parsed = _next_run_dt(text)
        if parsed is not None:
            next_values.append((parsed, text))
    if next_values:
        earliest = min(next_values, key=lambda item: item[0])[1]
        primary = dict(primary)
        primary["Next Run Time"] = earliest
        primary["下次运行时间"] = earliest
    return primary


def next_run_text() -> str:
    data = info()
    if not data:
        return "定时任务未安装"
    nxt = (data.get("Next Run Time") or data.get("下次运行时间")
           or data.get("下次运行的时候") or "-")
    state = data.get("Status") or data.get("状态") or ""
    suffix = f"（{state}）" if state else ""
    return f"已安装，下次运行：{nxt}{suffix}"
