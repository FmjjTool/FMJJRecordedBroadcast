"""WebUI 媒体筛选：扫描源文件夹并列出图片 / 视频，支持预览与分类写操作。

写操作（移动 / 复制 / 重命名 / 删除）与桌面端同语义：目标为桌面「媒体筛选」
的分类工作目录及其子文件夹，「删除」= 移入内置「删除」文件夹（可找回），
不永久删除；批量上限 200 个文件。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from image_sorter import settings as is_settings
from image_sorter.constants import (BUILTIN_FOLDERS, SUPPORTED_IMAGE_FORMATS,
                                    SUPPORTED_VIDEO_FORMATS)
from image_sorter.core.file_processor import FileProcessor

_MAX_SCAN_FILES = 20000
_MAX_PREVIEW = 20 * 1024 * 1024
_MAX_BATCH = 200
_INVALID_NAME = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED_NAMES = ({"CON", "PRN", "AUX", "NUL"}
                   | {f"COM{i}" for i in range(1, 10)}
                   | {f"LPT{i}" for i in range(1, 10)})


def _folders() -> list[Path]:
    try:
        raw = is_settings.get_list("source_folders")
    except Exception:
        raw = []
    folders = []
    for item in raw:
        path = Path(str(item))
        if path.is_dir():
            folders.append(path)
    return folders


def _work_dir() -> Path | None:
    try:
        text = str(is_settings.load_settings().get("work_dir") or "").strip()
    except Exception:
        text = ""
    if not text:
        return None
    path = Path(text)
    return path if path.is_dir() else None


def _targets() -> list[dict]:
    root = _work_dir()
    if root is None:
        return []
    builtin = set(BUILTIN_FOLDERS.values())
    items = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        items.append({"name": entry.name, "builtin": entry.name in builtin})
    order = {name: index for index, name in enumerate(BUILTIN_FOLDERS.values())}
    items.sort(key=lambda item: ((0, order[item["name"]]) if item["builtin"]
                                 else (1, item["name"])))
    return items


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def overview() -> dict:
    folders = _folders()
    root = _work_dir()
    return {
        "folders": [{"index": index, "path": str(path)}
                    for index, path in enumerate(folders)],
        "work_dir": str(root) if root else "",
        "work_dir_name": root.name if root else "",
        "targets": _targets(),
        "can_write": root is not None,
        "note": ("复制 / 移动 / 改名 / 删除已可在网页操作；删除 = 移入分类工作目录的"
                 "「删除」文件夹，可随时找回。去重（MD5）仍在桌面端。")
        if root else ("复制 / 移动 / 改名 / 删除需要先在桌面端「媒体筛选」设置分类工作目录；"
                      "扫描与预览不受影响。"),
    }


def scan(kind: str = "all", keyword: str = "", limit: int = 500) -> dict:
    kind = str(kind or "all")
    if kind not in ("all", "image", "video"):
        raise ValueError("未知类型")
    keyword = str(keyword or "").strip().lower()
    limit = max(1, min(2000, int(limit)))
    folders = _folders()
    items = []
    total_files = 0
    total_images = 0
    total_videos = 0
    total_bytes = 0
    truncated = False
    scanned = 0
    for index, folder in enumerate(folders):
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith("."))
            for name in sorted(filenames):
                suffix = Path(name).suffix.lower()
                if suffix in SUPPORTED_IMAGE_FORMATS:
                    file_kind = "image"
                elif suffix in SUPPORTED_VIDEO_FORMATS:
                    file_kind = "video"
                else:
                    continue
                scanned += 1
                if scanned > _MAX_SCAN_FILES:
                    truncated = True
                    break
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total_files += 1
                total_bytes += int(stat.st_size)
                if file_kind == "image":
                    total_images += 1
                else:
                    total_videos += 1
                if kind != "all" and file_kind != kind:
                    continue
                if keyword and keyword not in name.lower():
                    continue
                if len(items) >= limit:
                    truncated = True
                    continue
                try:
                    rel = path.relative_to(folder).as_posix()
                except ValueError:
                    rel = path.name
                items.append({
                    "ref": f"{index}:{rel}",
                    "name": name,
                    "folder": folder.name,
                    "kind": file_kind,
                    "is_image": file_kind == "image",
                    "size": int(stat.st_size),
                    "mtime": _ts(stat.st_mtime),
                })
            if truncated and scanned > _MAX_SCAN_FILES:
                break
        if truncated and scanned > _MAX_SCAN_FILES:
            break
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {
        "kind": kind,
        "keyword": keyword,
        "items": items,
        "shown": len(items),
        "total_files": total_files,
        "images": total_images,
        "videos": total_videos,
        "bytes": total_bytes,
        "truncated": truncated,
        "limit": limit,
        "note": "扫描结果只读；移动 / 复制 / 改名 / 删除在选中条目后操作。",
    }


def resolve(ref: str, *, preview: bool = False) -> Path:
    ref = str(ref or "").strip()
    if ":" not in ref:
        raise ValueError("文件标识不正确")
    index_text, rel = ref.split(":", 1)
    try:
        index = int(index_text)
    except ValueError as exc:
        raise ValueError("文件标识不正确") from exc
    folders = _folders()
    if index < 0 or index >= len(folders):
        raise ValueError("源文件夹不存在")
    folder = folders[index].resolve()
    target = (folder / rel).resolve()
    try:
        target.relative_to(folder)
    except ValueError as exc:
        raise ValueError("文件不在源文件夹内") from exc
    if not target.is_file():
        raise ValueError("文件不存在")
    if target.suffix.lower() not in SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS:
        raise ValueError("不支持的文件类型")
    if preview:
        if target.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError("只支持预览图片")
        try:
            if target.stat().st_size > _MAX_PREVIEW:
                raise ValueError("图片超过 20MB，请到桌面端查看")
        except OSError as exc:
            raise ValueError("文件读取失败") from exc
    return target


# ==================== 分类写操作（复制 / 移动 / 改名 / 删除） ====================

def _require_confirm(confirmed) -> None:
    if confirmed is not True:
        raise ValueError("请在页面上确认后再执行")


def _target_folder(name: str) -> Path:
    text = str(name or "").strip()
    if not text or text in (".", "..") or "/" in text or "\\" in text:
        raise ValueError("目标文件夹不正确")
    root = _work_dir()
    if root is None:
        raise ValueError("请先在桌面端「媒体筛选」设置分类工作目录")
    folder = (root / text).resolve()
    try:
        folder.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("目标文件夹不正确") from exc
    if not folder.is_dir():
        raise ValueError("目标文件夹不存在，请先在桌面端创建")
    return folder


def _refs(refs) -> list[str]:
    if not isinstance(refs, list) or not refs:
        raise ValueError("请先选择文件")
    if len(refs) > _MAX_BATCH:
        raise ValueError(f"单次最多处理 {_MAX_BATCH} 个文件")
    cleaned = []
    for item in refs:
        text = str(item or "").strip()
        if not text:
            raise ValueError("文件标识不正确")
        cleaned.append(text)
    return cleaned


def _resolve_checked(ref: str) -> tuple[Path, str]:
    path = resolve(ref)
    index = str(ref).split(":", 1)[0]
    return path, index


def transfer(refs, mode: str, target: str, confirmed=False) -> dict:
    """把选中的文件复制 / 移动到分类工作目录的指定子文件夹。"""
    _require_confirm(confirmed)
    mode = str(mode or "").strip().lower()
    if mode not in ("copy", "move"):
        raise ValueError("未知操作方式")
    items = _refs(refs)
    folder = _target_folder(target)
    folder_resolved = folder.resolve()
    done = 0
    outputs = []
    failed = []
    for ref in items:
        try:
            source, _ = _resolve_checked(ref)
            if source.parent.resolve() == folder_resolved:
                failed.append({"ref": ref, "name": source.name,
                               "reason": "已在目标文件夹"})
                continue
            result = FileProcessor.transfer_file_to_folder(
                source, folder, mode)
            if result is None:
                raise RuntimeError("目标文件未生成")
            done += 1
            if len(outputs) < 50:
                outputs.append(result.name)
        except Exception as exc:  # noqa: BLE001
            failed.append({"ref": ref, "reason": str(exc) or "操作失败"})
    return {"ok": done, "mode": mode, "target": folder.name,
            "outputs": outputs, "failed": failed,
            "summary": (f"已{'复制' if mode == 'copy' else '移动'} {done} 个文件"
                        f"到「{folder.name}」"
                        + (f"，{len(failed)} 个未处理" if failed else ""))}


def delete_to_trash(refs, confirmed=False) -> dict:
    """删除 = 移入分类工作目录的「删除」文件夹（与桌面一致，可找回）。"""
    _require_confirm(confirmed)
    items = _refs(refs)
    root = _work_dir()
    if root is None:
        raise ValueError("请先在桌面端「媒体筛选」设置分类工作目录")
    folder = root / BUILTIN_FOLDERS["deleted"]
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError("无法创建「删除」文件夹") from exc
    result = transfer(items, "move", folder.name, confirmed=True)
    result["summary"] = (f"已移入「{folder.name}」{result['ok']} 个文件"
                         + (f"，{len(result['failed'])} 个未处理"
                            if result["failed"] else ""))
    return result


def rename(ref: str, name: str, confirmed=False) -> dict:
    _require_confirm(confirmed)
    source, index = _resolve_checked(ref)
    text = str(name or "").strip()
    if not text:
        raise ValueError("请填写新文件名")
    if "/" in text or "\\" in text:
        raise ValueError("文件名不能包含路径分隔符")
    if _INVALID_NAME.search(text):
        raise ValueError("文件名包含非法字符")
    if text.endswith((" ", ".")):
        raise ValueError("文件名不能以空格或点结尾")
    if not Path(text).suffix:
        text += source.suffix
    if not Path(text).stem:
        raise ValueError("文件名不能为空")
    if Path(text).stem.upper() in _RESERVED_NAMES:
        raise ValueError("文件名是系统保留名")
    if len(text) > 200:
        raise ValueError("文件名过长")
    target = source.with_name(text)
    if target == source:
        return {"ok": 0, "renamed": False, "name": source.name,
                "ref": ref, "summary": "文件名未变化"}
    if target.exists():
        if target.name.lower() != source.name.lower():
            raise ValueError("目标文件已存在")
        # 仅大小写差异：经临时名两步改名
        temp = source.with_name(f".fmjj_rename_{os.getpid()}_{source.name}")
        source.rename(temp)
        try:
            temp.rename(target)
        except OSError:
            temp.rename(source)
            raise
    else:
        source.rename(target)
    rel_text = text
    folders = _folders()
    if index.isdigit() and int(index) < len(folders):
        try:
            rel_text = Path(target).relative_to(folders[int(index)]).as_posix()
        except ValueError:
            rel_text = text
    return {"ok": 1, "renamed": True, "name": target.name,
            "ref": f"{index}:{rel_text}",
            "summary": f"已重命名为 {target.name}"}
