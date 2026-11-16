"""自动整理引擎 - 按日期整理文件到文件夹"""
import os
import hashlib
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Tuple

from .utils import extract_date_from_filename


VIDEO_EXTS = {".mp4", ".flv", ".mkv", ".ts", ".mov", ".avi", ".webm",
              ".m4v", ".mpeg", ".mpg", ".mts", ".m2ts", ".wmv", ".ogv"}


def _snapshot(path):
    stat = os.stat(path)
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "inode": stat.st_ino, "device": stat.st_dev}


def scan_sources(sources, work_dir, recursive=False, stable_seconds=30):
    """多来源视频迁移计划；工作目录子树、链接、近期仍在写入的文件不迁移。"""
    target = Path(work_dir).resolve()
    seen, plan, skipped = set(), [], []
    for directory in sources:
        source = Path(directory).resolve()
        if not source.is_dir():
            skipped.append(f"来源目录不存在：{source}")
            continue
        if source == target or source.is_relative_to(target):
            skipped.append(f"来源已在工作目录：{source}")
            continue
        for folder, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs
                       if not (Path(folder) / name).is_symlink()
                       and not getattr(Path(folder) / name, "is_junction", lambda: False)()
                       and not (Path(folder) / name).resolve().is_relative_to(target)] if recursive else []
            for name in sorted(names):
                path = Path(folder) / name
                if path.suffix.lower() not in VIDEO_EXTS or path.is_symlink():
                    continue
                resolved = path.resolve()
                if resolved in seen or not resolved.is_relative_to(source) or resolved.is_relative_to(target):
                    continue
                seen.add(resolved)
                try:
                    snapshot = _snapshot(resolved)
                    if not snapshot["size"] or time.time_ns() - snapshot["mtime_ns"] < stable_seconds * 10**9:
                        skipped.append(f"暂缓（空文件或最近仍有写入）：{path}")
                        continue
                    date = extract_date_from_filename(name) or ""
                    plan.append({"name": name, "path": str(resolved), "source_root": str(source),
                                 "date": date, "ext": path.suffix.lower(), **snapshot})
                except OSError as error:
                    skipped.append(f"无法读取：{path}（{type(error).__name__}）")
    return sorted(plan, key=lambda item: (item["date"], item["name"], item["path"])), skipped


@contextmanager
def _locked_reader(path):
    if os.name != "nt":
        with open(path, "rb") as stream:
            yield stream
        return
    # Windows 拒绝共享写入：录制器仍持有写句柄时无法打开，整个复制期间也禁止新增写句柄。
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 1 | 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "文件正被使用或没有读取权限", str(path))
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


def move_video(item, work_dir, date=None):
    """先复制到目标磁盘的临时文件、校验，再无覆盖发布并删除来源。"""
    source = Path(item["path"])
    root = Path(work_dir).resolve()
    if source.suffix.lower() not in VIDEO_EXTS or source.is_symlink():
        raise ValueError("仅允许普通视频文件")
    allowed = Path(item.get("source_root") or source.parent).resolve()
    if not source.resolve().is_relative_to(allowed):
        raise ValueError("文件已离开所选来源目录")
    target_dir = (root / (date if date is not None else item.get("date", ""))).resolve()
    if not target_dir.is_relative_to(root):
        raise ValueError("目标不在工作目录")
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary = target_dir / (".fmjj-" + uuid.uuid4().hex + ".part")
    expected = {key: item[key] for key in ("size", "mtime_ns", "inode", "device") if key in item}
    try:
        with _locked_reader(source) as reader:
            before = _snapshot(source)
            if any(before[key] != value for key, value in expected.items()):
                raise ValueError("文件已变化，请重新扫描")
            if time.time_ns() - before["mtime_ns"] < 30 * 10**9:
                raise ValueError("文件近期仍有写入，稍后再试")
            digest = hashlib.sha256()
            with open(temporary, "xb") as writer:
                while chunk := reader.read(4 * 1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            with open(temporary, "rb") as check:
                verified = hashlib.file_digest(check, "sha256").digest()
            if temporary.stat().st_size != before["size"] or verified != digest.digest():
                raise OSError("目标视频校验失败，来源已保留")
            os.utime(temporary, ns=(os.stat(source).st_atime_ns, before["mtime_ns"]))
            if _snapshot(source) != before:
                raise ValueError("复制期间来源变化，来源已保留")
            number = 0
            while True:
                name = source.name if not number else f"{source.stem}_{number}{source.suffix}"
                destination = target_dir / name
                if destination.resolve() == source.resolve():
                    raise ValueError("来源和目标相同")
                try:
                    # 同一目标磁盘的硬链接保证原子且不覆盖；Windows 非 NTFS 用无覆盖 rename。
                    try:
                        os.link(temporary, destination)
                    except FileExistsError:
                        raise
                    except OSError:
                        if os.name != "nt":
                            raise
                        os.rename(temporary, destination)
                    break
                except FileExistsError:
                    number += 1
            try:
                source.unlink()
            except OSError as error:
                raise OSError(f"已复制到 {destination}，来源删除失败，请核对后手动处理") from error
            return str(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def execute_migration(work_dir, plan, on_progress=None):
    results = []
    for index, item in enumerate(plan, 1):
        try:
            target = move_video(item, work_dir)
            result = {"source": item["path"], "target": target, "ok": True}
        except (OSError, ValueError) as error:
            result = {"source": item["path"], "ok": False, "error": str(error)}
        results.append(result)
        if on_progress:
            on_progress(index, len(plan), result)
    return results


def scan_files(root_path: str) -> Tuple[Dict[str, List[Dict]], List[str]]:
    """扫描目录下的文件，按日期分组"""
    groups = {}
    no_date_files = []
    
    if not os.path.isdir(root_path):
        return groups, no_date_files
    
    try:
        for name in os.listdir(root_path):
            full_path = os.path.join(root_path, name)
            if not os.path.isfile(full_path):
                continue
            if name.startswith('.'):
                continue
            
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS or os.path.islink(full_path):
                continue
            
            date_str = extract_date_from_filename(name)
            
            if date_str:
                if date_str not in groups:
                    groups[date_str] = []
                groups[date_str].append({
                    "name": name,
                    "path": full_path,
                    **_snapshot(full_path),
                    "ext": ext
                })
            else:
                no_date_files.append(name)
    except Exception:
        pass
    
    sorted_groups = dict(sorted(groups.items()))
    return sorted_groups, no_date_files


def execute_organize(root_path: str, groups: Dict[str, List[Dict]]) -> Tuple[int, int, List[str]]:
    """执行整理操作"""
    success_count = 0
    fail_count = 0
    failed_files = []
    
    for date_str, files in groups.items():
        folder_path = os.path.join(root_path, date_str)
        try:
            os.makedirs(folder_path, exist_ok=True)
        except Exception:
            fail_count += len(files)
            failed_files.extend([f["name"] for f in files])
            continue
        
        for f in files:
            try:
                move_video(f, root_path, date=date_str)
                success_count += 1
            except Exception:
                fail_count += 1
                failed_files.append(f["name"])
    
    return success_count, fail_count, failed_files
