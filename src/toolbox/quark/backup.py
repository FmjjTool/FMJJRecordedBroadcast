# -*- coding: utf-8 -*-
"""夸克网盘备份 · 备份引擎（扫描 → 比对 → 上传 → 记录）。

GUI「立即备份」和 Windows 计划任务（`main.py --quark-backup`）走的是同一份实现，
区别只在有没有进度回调。

增量口径（增量追加，云端多余文件不删）：
1. 状态库无记录 → 传；
2. size 或 mtime 变化 → 传；
3. 状态库有记录且未变 → 跳过；
4. 状态库无记录但云端同名同大小 → 直接补记录跳过（防止状态库被删后整盘重传）。
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import state
from .config import Mapping, load_mappings


class QuarkAuthError(RuntimeError):
    """未登录 / Cookie 总开关关闭 / Cookie 已失效。"""


@dataclass
class BackupResult:
    mapping_id: str = ""
    local_dir: str = ""
    remote_dir: str = ""
    scanned: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    started_at: str = ""
    finished_at: str = ""
    errors: list[str] = field(default_factory=list)
    message: str = ""
    ok: bool = True

    def summary(self) -> str:
        parts = [f"扫描 {self.scanned}", f"上传 {self.uploaded}",
                 f"跳过 {self.skipped}"]
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.bytes:
            parts.append(human_size(self.bytes))
        return " · ".join(parts)


def human_size(num: float) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 客户端 ====================

def build_client():
    """按 Cookie 管理的当前登录态构造客户端（未登录时抛 QuarkAuthError）。"""
    from auth import get_cookie

    from .api import QuarkClient

    cookie = get_cookie("quark")
    if not cookie:
        raise QuarkAuthError(
            "夸克网盘未登录或 Cookie 总开关已关闭，请到「Cookie 管理 → 夸克网盘」登录后再试")
    return QuarkClient(cookie)


def client_hashes(path: Path) -> tuple[str, str]:
    """算 (md5, sha1)；单独抽出来是为了让备份引擎不直接依赖 api 模块的其余部分。"""
    from .api import file_hashes
    return file_hashes(path)


# ==================== 扫描 ====================

def _excluded(rel_path: str, patterns: list[str]) -> bool:
    """按相对路径匹配排除规则（同时看 basename 和前缀目录）。"""
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    name = rel_path.rsplit("/", 1)[-1]
    for pattern in patterns:
        pat = pattern.strip().lstrip("./")
        if not pat:
            continue
        if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat):
            return True
        if pat.endswith("/*") and rel_path.startswith(pat[:-1]):
            return True
        if rel_path == pat or rel_path.startswith(pat + "/"):
            return True
    return False


def iter_files(mapping: Mapping):
    """遍历映射的本地目录，产出 (绝对路径, 相对路径)。目录级排除会整体跳过。"""
    root = Path(mapping.local_dir)
    if not root.is_dir():
        return
    patterns = mapping.exclude_patterns()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for name in dirnames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if not _excluded(rel, patterns):
                keep.append(name)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _excluded(rel, patterns):
                continue
            yield Path(dirpath) / name, rel


def _remote_path(mapping: Mapping, rel_path: str) -> str:
    base = mapping.remote_dir.strip("/")
    parent = rel_path.replace("\\", "/").rsplit("/", 1)[0] if "/" in rel_path else ""
    if base and parent:
        return f"{base}/{parent}"
    return base or parent


def _emit(progress, **payload):
    if progress is None:
        return
    try:
        progress(payload)
    except Exception:  # noqa: BLE001 - 界面回调异常不能打断备份
        pass


def _say(log, message: str):
    if log is not None:
        try:
            log(message)
        except Exception:  # noqa: BLE001
            pass


# ==================== 单条映射 ====================

def run_mapping(mapping: Mapping, client=None, progress=None, log=None,
                stop_event=None, force: bool = False) -> BackupResult:
    """执行一条映射的备份，返回结果（不抛业务异常，鉴权失败写入 errors）。"""
    result = BackupResult(mapping_id=mapping.id, local_dir=mapping.local_dir,
                          remote_dir=mapping.remote_dir, started_at=_now())

    if not mapping.local_dir or not Path(mapping.local_dir).is_dir():
        result.ok = False
        result.message = "本地目录不存在"
        result.errors.append(f"本地目录不存在：{mapping.local_dir or '(空)'}")
        result.finished_at = _now()
        return result
    if not mapping.remote_dir:
        result.ok = False
        result.message = "未设置云端目录"
        result.errors.append("未设置云端目标文件夹")
        result.finished_at = _now()
        return result

    files = list(iter_files(mapping))
    result.scanned = len(files)
    if not files:
        result.finished_at = _now()
        result.message = "本地目录没有可备份的文件"
        return result

    _say(log, f"[{mapping.remote_dir}] 待处理 {len(files)} 个文件")
    try:
        if client is None:
            client = build_client()
        folder_fid = client.ensure_dir(mapping.remote_dir)
    except QuarkAuthError as exc:
        result.ok = False
        result.message = str(exc)
        result.errors.append(str(exc))
        result.finished_at = _now()
        return result
    except Exception as exc:  # noqa: BLE001 - 网络 / 接口错误
        result.ok = False
        result.message = f"连接夸克网盘失败：{exc}"
        result.errors.append(result.message)
        result.finished_at = _now()
        return result

    remote_cache: dict[str, dict] = {}
    index_cache: dict[str, dict] = {}
    total = len(files)
    # 总字节数用来算全局进度：大文件（几 GB 的视频）靠文件计数看不出进展
    total_bytes = 0
    for path, _rel in files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
    done_bytes = 0

    def _percent(sent_extra: int = 0) -> int:
        if total_bytes <= 0:
            return 0
        return int(min(100, (done_bytes + sent_extra) * 100 / total_bytes))

    def _cloud_index(remote_dir: str) -> dict:
        if remote_dir not in index_cache:
            index_cache[remote_dir] = client.list_index(remote_dir)
        return index_cache[remote_dir]

    for index, (path, rel) in enumerate(files, start=1):
        if stop_event is not None and stop_event.is_set():
            result.message = "已手动停止"
            break
        _emit(progress, phase="run", current=index - 1, total=total,
              percent=_percent(), name=rel)
        try:
            stat = path.stat()
        except OSError as exc:
            result.failed += 1
            result.errors.append(f"{rel}：读取失败 {exc}")
            continue

        if mapping.max_file_mb and stat.st_size > mapping.max_file_mb * 1024 * 1024:
            result.skipped += 1
            done_bytes += stat.st_size
            _say(log, f"跳过（超过 {mapping.max_file_mb} MB）：{rel}")
            continue

        remote_dir = _remote_path(mapping, rel)
        try:
            cloud_index = _cloud_index(remote_dir)
        except Exception as exc:  # noqa: BLE001 - 列目录失败按「云端没有」处理
            cloud_index = {}
            remote_cache.pop(remote_dir, None)
            _say(log, f"读取云端目录失败，按新增处理：{remote_dir or '/'}（{exc}）")

        cloud = cloud_index.get(path.name) or {}
        cloud_size = cloud.get("size")
        record = state.get_record(mapping.id, str(path))
        same_meta = bool(record) and int(record["size"]) == stat.st_size \
            and abs(float(record["mtime"]) - stat.st_mtime) < 1e-6

        # 快路径：本地没动过 + 云端大小一致 → 不算 md5 直接跳过（3 GB 视频也秒过）
        if not force and same_meta and cloud_size == stat.st_size:
            result.skipped += 1
            done_bytes += stat.st_size
            continue

        # 慢路径：算 md5 判内容。只有内容（或云端那一份）真的变了才重传
        _emit(progress, phase="hash", name=rel, size=stat.st_size,
              current=index - 1, total=total, percent=_percent())
        try:
            md5, sha1 = client_hashes(path)
        except OSError as exc:
            result.failed += 1
            done_bytes += stat.st_size
            result.errors.append(f"{rel}：读取失败 {exc}")
            _say(log, f"校验失败：{rel}（{exc}）")
            continue

        if not force and cloud_size == stat.st_size and (
                (record and record.get("md5") == md5) or not record):
            # 内容一致（或云端同名同大小、本机无记录）：不覆盖，只刷新记录
            state.put_record(mapping.id, str(path), remote_dir, stat.st_size,
                             stat.st_mtime, md5, (record or {}).get("fid") or cloud.get("fid", ""))
            result.skipped += 1
            done_bytes += stat.st_size
            _say(log, f"内容一致，跳过：{rel}")
            continue

        try:
            _emit(progress, phase="file", name=rel, sent=0, size=stat.st_size,
                  current=index - 1, total=total, percent=_percent())
            if stat.st_size >= 100 * 1024 * 1024:
                _say(log, f"开始上传：{rel}  {human_size(stat.st_size)}（分片上传，进度见进度条）")
            # 覆盖前先删掉云端同名的那一份（只删同名同路径的，不碰其他文件）
            if mapping.replace_changed:
                old_fids = []
                if cloud.get("fid"):
                    old_fids.append(cloud["fid"])
                if record and record.get("fid") and record["fid"] not in old_fids:
                    old_fids.append(record["fid"])
                if old_fids:
                    if client.delete_files(old_fids):
                        _say(log, f"已删除云端旧版本：{rel}")
                    else:
                        _say(log, f"旧版本删除失败，将保留副本：{rel}")
            info = client.upload_file(
                path, remote_dir, hashes=(md5, sha1),
                on_stage=lambda stage, _name, _size, _rel=rel: _emit(
                    progress, phase=("hash" if stage == "hash" else "file"),
                    name=_rel, size=_size, sent=0, current=index - 1,
                    total=total, percent=_percent()),
                on_progress=lambda sent, size, _rel=rel: _emit(
                    progress, phase="file", name=_rel, sent=sent, size=size,
                    current=index - 1, total=total, percent=_percent(sent)),
            )
            state.put_record(mapping.id, str(path), remote_dir, stat.st_size,
                             stat.st_mtime, info.get("md5", md5), info.get("fid", ""))
            cloud_index[path.name] = {"fid": info.get("fid", ""), "size": stat.st_size}
            done_bytes += stat.st_size
            result.uploaded += 1
            result.bytes += stat.st_size
            note = "（秒传）" if info.get("instant") else ""
            _say(log, f"已上传{note}：{rel}  {human_size(stat.st_size)}")
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不打断整批
            result.failed += 1
            done_bytes += stat.st_size
            message = f"{rel}：{exc}"
            result.errors.append(message)
            _say(log, f"上传失败：{message}")
            index_cache.pop(remote_dir, None)

    result.finished_at = _now()
    if result.failed:
        result.ok = False
        result.message = result.message or f"{result.failed} 个文件失败"
    elif not result.message:
        result.message = "完成"
    _emit(progress, phase="done", current=total, total=total, percent=100, name="")
    try:
        state.add_run(result)
    except Exception:  # noqa: BLE001 - 记录失败不影响备份结果
        pass
    return result


# ==================== 全部映射 ====================

def run_all(mappings: list[Mapping] | None = None, progress=None, log=None,
            stop_event=None, force: bool = False) -> list[BackupResult]:
    """按顺序跑全部启用的映射；鉴权失败时只报一次错，不重复请求。"""
    items = [m for m in (mappings if mappings is not None else load_mappings())
             if m.enabled and m.local_dir]
    results: list[BackupResult] = []
    if not items:
        return results

    client = None
    auth_error: str = ""
    total = len(items)
    for index, mapping in enumerate(items):
        if stop_event is not None and stop_event.is_set():
            break
        _emit(progress, phase="mapping", index=index, total=total,
              name=mapping.title())
        if auth_error:
            failed = BackupResult(mapping_id=mapping.id, local_dir=mapping.local_dir,
                                  remote_dir=mapping.remote_dir, started_at=_now(),
                                  finished_at=_now(), ok=False,
                                  message=auth_error, errors=[auth_error])
            results.append(failed)
            continue
        try:
            if client is None:
                client = build_client()
        except QuarkAuthError as exc:
            auth_error = str(exc)
            failed = BackupResult(mapping_id=mapping.id, local_dir=mapping.local_dir,
                                  remote_dir=mapping.remote_dir, started_at=_now(),
                                  finished_at=_now(), ok=False,
                                  message=auth_error, errors=[auth_error])
            results.append(failed)
            _say(log, auth_error)
            continue
        result = run_mapping(mapping, client=client, progress=progress, log=log,
                             stop_event=stop_event, force=force)
        results.append(result)
        _say(log, f"[{mapping.remote_dir}] {result.summary()}")
    return results
