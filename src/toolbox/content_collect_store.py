"""定时内容采集的唯一配置写入方与本地目录读取设施（L5）。

primary: config/content_collect.json，schema_version=1，只存显式订阅 ID 和参数；
readers: content_collect / task_center。按 kind + 规范 ID 去重，不迁移旧配置，
不读取或写入任何登录存储；删除计划不会删除订阅或采集历史。
本模块原子更新配置，进程/线程锁保护更新与每类任务防重入。
config/content_collect_*.log 为可轮转运行日志，最多保留当前与一份 1 MiB 旧日志。
超话库与指数快照仅读元数据，历史仍由各自原有存储负责。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from toolbox.durable_io import atomic_write_json

KINDS = ("chaohua", "douyin_index")
SCHEMA_VERSION = 1
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


class SettingsError(ValueError):
    """设置无法安全读取或写入；不包含路径或文件内容。"""


class LockBusy(SettingsError):
    """另一个进程或线程持有同一操作的锁。"""


def _config_dir() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir()


def settings_path() -> Path:
    return _config_dir() / "content_collect.json"


def load() -> dict:
    """缺文件返回空配置；损坏/未知版本明确报错，绝不自动覆盖。"""
    path = settings_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "collectors": {}}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise SettingsError("采集订阅设置过大，未作修改")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if (not isinstance(data, dict)
                or type(data.get("schema_version")) is not int
                or data["schema_version"] != SCHEMA_VERSION
                or not isinstance(data.get("collectors"), dict)
                or any(not isinstance(value, dict)
                       for value in data["collectors"].values())):
            raise SettingsError("采集订阅设置版本或格式不支持，未作修改")
        return data
    except SettingsError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise SettingsError("采集订阅设置无法读取，未作修改") from exc


@contextmanager
def exclusive_lock(name: str, *, wait: float = 0):
    """系统文件锁随进程退出释放，不用 PID/超时删除锁文件判断运行状态。"""
    if name not in ("settings", "log-chaohua", "log-douyin_index",
                    "run-chaohua", "run-douyin_index"):
        raise ValueError("未知的采集操作")
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(name, threading.Lock())
    if not thread_lock.acquire(timeout=max(0, wait)):
        raise LockBusy("另一项采集操作正在进行，请稍后再试")
    handle = None
    locked = False
    try:
        try:
            handle = open(_config_dir() / ("content_collect_" + name + ".lock"), "a+b")
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + max(0, wait)
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LockBusy("另一项采集操作正在进行，请稍后再试") from exc
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        except LockBusy:
            raise
        except OSError as exc:
            raise SettingsError("采集操作锁无法建立，请检查写入权限") from exc
        yield
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
        thread_lock.release()


def save(kind: str, values: dict) -> None:
    if kind not in KINDS:
        raise ValueError("未知的内容采集任务")
    with exclusive_lock("settings", wait=5):
        data = load()
        data["collectors"][kind] = values
        try:
            atomic_write_json(settings_path(), data)
        except OSError as exc:
            raise SettingsError("采集订阅设置无法保存，请检查写入权限") from exc


def log(kind: str, message: str) -> None:
    """调用方只传已脱敏的状态文本；日志失败不影响已保存的采集结果。"""
    if kind not in KINDS:
        return
    try:
        with exclusive_lock("log-" + kind, wait=1):
            path = _config_dir() / ("content_collect_" + kind + ".log")
            if path.exists() and path.stat().st_size > 1024 * 1024:
                os.replace(path, path.with_suffix(".1.log"))
            now = datetime.now(timezone(timedelta(hours=8)))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{now:%Y-%m-%d %H:%M:%S}] {message[:500]}\n")
    except (OSError, SettingsError):
        pass


def topic_choices() -> list[dict]:
    """只读现有 topics，不初始化超话库，不把已有主题视为订阅。"""
    path = _config_dir() / "weibo_chaohua.db"
    if not path.is_file():
        return []
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        rows = conn.execute("SELECT topic_id, nick FROM topics ORDER BY last_seen DESC LIMIT 500").fetchall()
        return [{"value": str(row[0] or ""), "label": str(row[1] or row[0] or "")}
                for row in rows]
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()


def index_choices() -> list[dict]:
    """只读已有 latest.json 的 UID/昵称，不创建指数输出目录。"""
    from toolbox.config_manager import EXPORT_CATEGORIES, get_export_dir
    root = Path(get_export_dir()) / EXPORT_CATEGORIES["douyin_index"]
    if not root.is_dir():
        return []
    rows = []
    try:
        folders = sorted(root.iterdir())[:500]
    except OSError:
        return []
    for folder in folders:
        latest = folder / "latest.json"
        try:
            if not latest.is_file() or latest.stat().st_size > 10 * 1024 * 1024:
                continue
            data = json.loads(latest.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                continue
            meta, author = data.get("meta") or {}, data.get("author") or {}
            uid = str(meta.get("uid") or author.get("aweme_id") or "")
            rows.append({"value": uid,
                         "label": str(author.get("user_name") or folder.name)})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return rows
