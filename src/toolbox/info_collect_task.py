"""信息获取 · 作品增量 · Windows 计划任务（每天 / 按间隔）。

- 任务名：FengMingInfoCollect
- 命令：pythonw main.py --info-collect（打包后 FengMingToolbox.exe --info-collect）
- 采集对象：信息获取页账号名单（`config/accounts.txt`）里的全部账号
- 采集方式：与「同步前刷新」同一条链路 —— `toolbox.account_collection.collect_accounts`
  增量抓取后由 `toolbox.collected_store.ingest_items` 入库；只入库不下载媒体文件，
  避免后台任务悄悄占满磁盘。
- 单账号条数：COUNT_PER_ACCOUNT（默认 20，够覆盖停更账号的日常增量）
- 日志：config/info_collect_task.log（超过 1 MB 备份为 .1.log）
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TASK_NAME = "FengMingInfoCollect"
FLAG = "--info-collect"
COUNT_PER_ACCOUNT = 20


def _config_dir() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir()


# ==================== 日志 ====================

def log(message: str) -> None:
    try:
        path = _config_dir() / "info_collect_task.log"
        try:
            if path.stat().st_size > 1024 * 1024:
                os.replace(path, path.with_suffix(".1.log"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


# ==================== 采集 ====================

def collect_all(count: int = COUNT_PER_ACCOUNT, progress=None) -> tuple[int, int]:
    """抓取全部信息获取账号的最新作品，返回 (账号数, 入库条数)。"""
    from accounts_manager import load_accounts
    from toolbox.account_collection import collect_accounts
    from toolbox.collected_store import ingest_items

    accounts = [a for a in load_accounts() if getattr(a, "url", "")]
    if not accounts:
        return 0, 0

    def emit(line: str) -> None:
        log(str(line)[:500])
        if progress:
            progress(line)

    def persist(posts, incremental):
        ingest_items(posts, incremental=True)
        return "", {}

    def on_event(event):
        if event.get("type") == "progress":
            emit(f"进度 {event.get('done', 0)}/{event.get('total', 0)} "
                 f"{event.get('label', '')}")
        elif event.get("type") == "log":
            text = str(event.get("text", "")).strip()
            if text:
                emit(text)

    total, _logs, _posts = collect_accounts(
        accounts, int(count or COUNT_PER_ACCOUNT), incremental=True,
        on_event=on_event, persist_batch=persist)
    return len(accounts), int(total or 0)


def main() -> int:
    log("===== 信息获取增量定时任务开始 =====")
    try:
        from toolbox.config_manager import init_all_configs
        init_all_configs()
        n, total = collect_all(progress=None)
        if n == 0:
            log("信息获取里没有可用账号，跳过")
            return 0
        log(f"===== 信息获取增量定时任务结束：{n} 个账号 / 本次入库 {total} 条 =====")
        return 0
    except Exception as exc:  # noqa: BLE001 - 计划任务不能把异常抛到控制台
        log(f"定时任务失败：{type(exc).__name__}: {exc}")
        return 1


# ==================== 计划任务 ====================

def _run(args: list[str]) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="gbk", errors="ignore", **kwargs)
    except (OSError, ValueError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"无法调用系统计划任务：{exc}")


def _runner_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {FLAG}'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{exe}" "{main_py}" {FLAG}'


def install_daily(time_str: str) -> tuple[bool, str]:
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _runner_command(),
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, f"每天 {time_str} 自动增量采集作品"


def install_interval(minutes: int) -> tuple[bool, str]:
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
    return True, f"{desc}自动增量采集作品"


def uninstall() -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, "定时任务已删除，关闭软件后不再采集"


def info() -> dict:
    try:
        result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    except Exception:  # noqa: BLE001 - 查不到按未安装处理
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
    data = info()
    if not data:
        return False, "定时任务未安装（关闭软件不会采集）"
    nxt = data.get("Next Run Time") or data.get("下次运行时间") or "-"
    return True, f"定时任务已安装，下次运行：{nxt}"