"""直播总结存档读取（L3，Qt-free）。

给「直播记录」自动推送 AI 摘要用：按开播日期找到对应的直播总结正文。

来源优先级：
1. 统一导出目录「直播总结」下的 txt / md 文件（文件名带 `YYYYMMDD` 或 `YYYY-MM-DD`）；
2. 内置资料库 `src/skills/fengming-livestream-summary/references/database/live/<YYYY-MM>.md`
   对应场次的「AI 摘要」。

两处都没有就返回空串——不生成、不猜测、不拿别的场次凑数。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_DAY_DASH = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DAY_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})")
_HEADING = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})")
_AI_LINE = re.compile(r"^-\s*AI\s*摘要\s*[：:]\s*(.*)$")
_SUFFIXES = {".txt", ".md"}


def normalize_day(value) -> str:
    """多种日期写法 → YYYY-MM-DD；识别不了返回空串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = _DAY_DASH.search(raw)
    if match:
        return "-".join(match.groups())
    match = _DAY_COMPACT.search(raw)
    if match:
        return "-".join(match.groups())
    return ""


def _summary_dir() -> Path | None:
    try:
        from toolbox.config_manager import get_export_subdir

        return Path(get_export_subdir("livestream_summary"))
    except Exception:  # noqa: BLE001 - 配置不可用时不阻断上传
        return None


def _from_files(day: str) -> str:
    root = _summary_dir()
    if root is None or not root.is_dir():
        return ""
    compact = day.replace("-", "")
    found: list[tuple[float, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        name = path.name
        if day not in name and compact not in name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if text:
            found.append((mtime, text))
    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1] if found else ""


def _database_dir() -> Path:
    return (Path(__file__).resolve().parents[2] / "skills"
            / "fengming-livestream-summary" / "references" / "database" / "live")


def _from_database(day: str) -> str:
    path = _database_dir() / f"{day[:7]}.md"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    capture = False
    collect = False
    body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if capture:
                break
            match = _HEADING.match(line)
            capture = bool(match and match.group(1) == day)
            continue
        if not capture:
            continue
        ai = _AI_LINE.match(line)
        if ai and not collect:
            collect = True
            if ai.group(1).strip():
                body.append(ai.group(1).strip())
            continue
        if not collect:
            continue
        if line.startswith("- ") or line.startswith("#"):
            break
        body.append(line)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body).strip()


def summary_for(day) -> str:
    """某场直播的 AI 摘要正文；找不到返回空串。"""
    normalized = normalize_day(day)
    if not normalized:
        return ""
    return _from_files(normalized) or _from_database(normalized)
