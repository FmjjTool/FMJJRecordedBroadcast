"""网媒/音乐账号追踪 · 无界面定时采集（Windows 计划任务调用）。

与 GUI「⬇ 获取全部」同一条采集链路，只是不带界面：
- 网媒：逐账号采集指标并追加 xlsx 历史缓存，抖音账号顺带抓直播历史
- 音乐：逐账号采集指标并追加 xlsx 历史缓存
结果写 config/account_tracker_task.log，退出码 0 = 全部成功。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

LOG_MAX_BYTES = 2 * 1024 * 1024
PARALLEL_WORKERS = 4   # 与 GUI 批量采集同款并发波次


# ==================== 日志 ====================

def _log_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "account_tracker_task.log"


def log(message: str) -> None:
    """追加一行采集日志（pythonw / 无控制台环境下也能留痕）。"""
    try:
        path = _log_path()
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            backup = path.with_suffix(".1.log")
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
            except OSError:
                pass
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 - 日志失败不能打断采集
        pass


# ==================== 单账号采集 ====================

def _collect_tracker_one(acc: dict) -> tuple[bool, str]:
    from tracker.accounts import collect_account, save_history, parse_url
    from tracker.douyin_live import collect_and_save
    name = acc.get("name", "未命名")
    try:
        record = collect_account(acc)
        if record.get("error"):
            return False, f"[{name}] 采集失败：{record['error']}"
        save_history(name, record)
        note = ""
        platform = (acc.get("platform") or "").strip() or parse_url(acc.get("url", ""))[0]
        if platform == "douyin":
            try:
                live = collect_and_save(acc, record.get("uid"))
                if live.get("error"):
                    note = f"（直播历史失败：{live['error']}）"
                else:
                    note = f"（直播历史 {live['total']} 场，新增 {live['new']}）"
            except Exception as exc:  # noqa: BLE001 - 直播失败不影响指标采集
                note = f"（直播历史失败：{exc}）"
        return True, f"[{name}] 采集成功{note}"
    except Exception as exc:  # noqa: BLE001 - 单账号失败不影响其它账号
        return False, f"[{name}] 采集异常：{exc}"


def _collect_music_one(acc: dict) -> tuple[bool, str]:
    from music_account.accounts import collect_account, save_history
    name = acc.get("name", "未命名")
    try:
        record = collect_account(acc)
        if record.get("error"):
            return False, f"[{name}] 采集失败：{record['error']}"
        save_history(name, record)
        return True, f"[{name}] 采集成功"
    except Exception as exc:  # noqa: BLE001 - 单账号失败不影响其它账号
        return False, f"[{name}] 采集异常：{exc}"


# ==================== 入口 ====================

def run(kind: str) -> int:
    kind_name = "网媒账号追踪" if kind == "tracker" else "音乐账号追踪"
    log(f"===== {kind_name}定时任务开始 =====")
    try:
        from toolbox.config_manager import init_all_configs
        init_all_configs()
        if kind == "tracker":
            from tracker.accounts import load_accounts
            worker = _collect_tracker_one
        else:
            from music_account.accounts import load_accounts
            worker = _collect_music_one
        accounts = load_accounts()
        if not accounts:
            log(f"[{kind_name}] 尚未配置任何账号，跳过")
            return 1
        ok = 0
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = [pool.submit(worker, acc) for acc in accounts]
            for fut in as_completed(futures):
                try:
                    good, msg = fut.result()
                except Exception as exc:  # noqa: BLE001 - 正常已在 worker 内捕获
                    good, msg = False, f"采集异常：{exc}"
                ok += 1 if good else 0
                log(msg)
        log(f"===== {kind_name}定时任务结束：成功 {ok}/{len(accounts)} =====")
        return 0 if ok == len(accounts) else 1
    except Exception as exc:  # noqa: BLE001
        log(f"定时任务失败：{exc}")
        return 1


def main(kind: str) -> int:
    return run(kind)
