"""本地视频缺失封面的自动化用例（Qt-free）。

不修改视频、模板、配置或已有图片；唯一输出为同目录同主名 .jpg 派生封面。
本模块是该批量输出的唯一 writer，无新持久化配置/schema，重跑按附属图片去重。
预览 DTO 均为根目录相对路径；执行重新校验路径、重复目标和已有封面。
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable

from toolbox import image_render
from toolbox.cover_engine import render_cover_image, validate_template
from toolbox.date_folders import format_date_range, split_date_range

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".flv", ".ts", ".m2ts", ".mov", ".avi", ".webm", ".m4v"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"})
_DATE = re.compile(r"(?<!\d)(20\d{2})(?:[-_.年](\d{1,2})[-_.月](\d{1,2})日?|(\d{2})(\d{2}))(?=\D|\d{6}(?:\D|$)|$)")
_CLOCK = re.compile(r"^[ _T-]*(?:[0-2]\d[-_:][0-5]\d[-_:][0-5]\d|[0-2]\d[0-5]\d[0-5]\d)(?!\d)")


def _root(root: Path) -> Path:
    result = Path(root).expanduser().resolve(strict=True)
    if not result.is_dir():
        raise ValueError("请选择视频所在的文件夹")
    return result


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _video(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not value or ".." in relative.parts:
        raise ValueError("视频路径超出扫描目录，请重新扫描")
    path = root / relative
    if any(_linked(parent) for parent in (path, *path.parents) if parent != root and root in parent.parents):
        raise ValueError("不处理链接文件或链接目录，请选择原文件")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("视频路径超出扫描目录，请重新扫描")
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("视频已不存在或格式不支持，请重新扫描")
    return path


def _dates(text: str) -> tuple[str, list[tuple[int, int]], str]:
    matches = list(_DATE.finditer(text))
    values, spans = [], []
    for match in matches:
        year, month, day, compact_month, compact_day = match.groups()
        try:
            value = date(int(year), int(month or compact_month), int(day or compact_day)).isoformat()
        except ValueError:
            return "", [], "日期无效，请修正日期后再生成"
        values.append(value)
        spans.append(match.span())
    if not values:
        return "", [], ""
    if len(values) == 1:
        return values[0], spans, ""
    if len(values) == 2 and re.fullmatch(r"\s*[~～至]\s*", text[spans[0][1]:spans[1][0]]):
        start, end = split_date_range("~".join(values))
        if start and end >= start:
            return format_date_range(start, end, dashed=True), [(spans[0][0], spans[1][1])], ""
    return "", [], "识别到多个日期，请确认日期后再生成"


def recognize_video(video: Path, root: Path) -> dict:
    """只识别文件名及扫描根以内最近一层含日期的目录，不用文件修改时间猜日期。"""
    title = video.stem
    value, spans, reason = _dates(title)
    if spans:
        for start, end in reversed(spans):
            after = title[end:]
            clock = _CLOCK.match(after)
            if clock:
                end += clock.end()
            title = title[:start] + " " + title[end:]
        title = re.sub(r"[\[【（(]\s*[\]】）)]", "", title)
    title = re.sub(r"[_\s]+", " ", title).strip(" ._-~～【】[]()（）")
    if not value and not reason:
        parent = video.parent
        while parent == root or root in parent.parents:
            value, _, reason = _dates(parent.name)
            if value or reason or parent == root:
                break
            parent = parent.parent
    if not title:
        reason = "未识别到标题，请填写标题后再生成"
    return {"title": title, "date": value, "reason": reason}


def existing_cover(video: Path, entries=None) -> Path | None:
    """同名、录制器 _封面、常见 _cover/.cover 附属图均视为已有封面。"""
    stems = {video.stem.casefold() + suffix for suffix in ("", "_封面", ".cover", "_cover")}
    for item in video.parent.iterdir() if entries is None else entries:
        if item.stem.casefold() in stems and item.suffix.lower() in IMAGE_EXTENSIONS:
            # 即使损坏或是链接也保留，绝不自动覆盖用户已有内容。
            return item
    return None


def scan_videos(root: Path, recursive: bool = True, *,
                is_cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
    root = _root(root)
    rows = []
    targets: dict[str, list[dict]] = {}

    def walk_error(error):
        raise ValueError("部分目录无法读取，请检查权限后重新扫描") from error

    for folder, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        directories[:] = sorted(d for d in directories if not _linked(Path(folder) / d)) if recursive else []
        images = [Path(folder) / name for name in filenames if Path(name).suffix.lower() in IMAGE_EXTENSIONS]
        for name in sorted(filenames, key=str.casefold):
            if is_cancelled():
                return rows
            video = Path(folder) / name
            if video.suffix.lower() not in VIDEO_EXTENSIONS or _linked(video):
                continue
            if len(rows) >= 10000:
                raise ValueError("视频超过 10000 条，请缩小扫描目录")
            relative = video.relative_to(root).as_posix()
            cover = video.with_suffix(".jpg")
            found = existing_cover(video, images)
            metadata = recognize_video(video, root)
            reason = metadata["reason"]
            row = {"video": relative, **metadata, "cover": cover.relative_to(root).as_posix(),
                   "status": "exists" if found else "invalid" if reason else "ready"}
            if found:
                row["cover"] = found.relative_to(root).as_posix()
                row["reason"] = "已有封面，已跳过"
            elif not reason and not metadata["date"]:
                row["reason"] = "未识别日期，日期将留空（可填写）"
            rows.append(row)
            targets.setdefault(str(cover).casefold(), []).append(row)
    for group in targets.values():
        if len(group) > 1:
            for row in group:
                if row["status"] != "exists":
                    row["status"] = "conflict"
                    row["reason"] = "多个视频对应同一封面名，请先区分文件主名"
    return sorted(rows, key=lambda row: row["video"].casefold())


def _date_value(value: str) -> str:
    if not value:
        return ""
    start, end = split_date_range(value)
    if not start or end < start:
        raise ValueError("日期无效，请填写 YYYY-MM-DD 或日期范围")
    return format_date_range(start, end, dashed=True)


def validate_row_metadata(row: dict) -> dict:
    """桌面/Web 复核编辑共用的元数据校验。"""
    title = str(row.get("title") or "").strip()
    if not title:
        raise ValueError("标题为空，请填写标题后再生成")
    if len(title) > 4000:
        raise ValueError("标题过长，请缩短后再生成")
    return {"title": title, "date": _date_value(str(row.get("date") or "").strip())}


def _publish_new(source: Path, target: Path) -> None:
    """同卷原子发布且不替换；Windows rename 同时兼容 NTFS / exFAT。"""
    if os.name == "nt":
        # Windows rename 在目标已存在时抛 FileExistsError；不得换成 replace。
        os.rename(source, target)
    else:
        # POSIX rename 会覆盖目标，改用原子且排他的 hard link。
        os.link(source, target)


def generate_missing(root: Path, rows: list[dict], template: dict, *,
                     is_cancelled: Callable[[], bool] = lambda: False,
                     on_progress: Callable[[int, int, str], None] = lambda done, total, message: None) -> dict:
    """执行已确认预览；只新增，发布前重查，原子创建且永不替换已有目标。"""
    root = _root(root)
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise ValueError("请先扫描并选择 1 至 10000 条视频")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("视频清单无效，请重新扫描")
    data = validate_template(template)
    result = {"generated": 0, "skipped": 0, "failed": 0, "cancelled": False, "items": []}
    seen = set()
    for index, row in enumerate(rows):
        if is_cancelled():
            result["cancelled"] = True
            break
        item = {"video": str(row.get("video", "")), "status": "failed", "reason": ""}
        temporary = None
        try:
            video = _video(root, item["video"])
            target = video.with_suffix(".jpg")
            identity = str(target).casefold()
            # 不依赖客户端 status：重新核对全部同主名视频，防止只选冲突中的一个。
            siblings = [p for p in video.parent.iterdir() if p.stem.casefold() == video.stem.casefold()
                        and p.suffix.lower() in VIDEO_EXTENSIONS]
            if existing_cover(video) or identity in seen:
                item.update(status="skipped", reason="已有封面或重复条目，已跳过")
                result["skipped"] += 1
            elif len(siblings) > 1:
                raise ValueError("多个视频对应同一封面名，请先区分文件主名")
            else:
                seen.add(identity)
                metadata = validate_row_metadata(row)
                original = recognize_video(video, root)
                if original["reason"] and "日期" in original["reason"] and not metadata["date"]:
                    raise ValueError("文件日期需要复核，请填写有效日期后再生成")
                picture = render_cover_image(data, metadata["title"], metadata["date"], data["remark"])
                if is_cancelled():
                    result["cancelled"] = True
                    break
                descriptor, name = tempfile.mkstemp(prefix=".cover-", suffix=".tmp", dir=target.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    picture.save(stream, "JPEG", quality=data["jpg_quality"], dpi=(image_render.DPI, image_render.DPI))
                    stream.flush()
                    os.fsync(stream.fileno())
                if is_cancelled():
                    result["cancelled"] = True
                    break
                _video(root, item["video"])
                if existing_cover(video):
                    item.update(status="skipped", reason="生成期间出现封面，已保留原文件")
                    result["skipped"] += 1
                else:
                    _publish_new(temporary, target)
                    item.update(status="generated", cover=target.relative_to(root).as_posix(), reason="已生成")
                    result["generated"] += 1
        except FileExistsError:
            item.update(status="skipped", reason="封面已存在，已保留原文件")
            result["skipped"] += 1
        except (OSError, ValueError) as exc:
            # 不把本机路径、系统异常文本透出到 WebUI。
            item["reason"] = str(exc) if isinstance(exc, ValueError) else "文件无法读写，请检查目录权限后重试"
            result["failed"] += 1
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        result["items"].append(item)
        on_progress(index + 1, len(rows), item["reason"])
    return result
