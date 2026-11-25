"""WebUI 直播总结（只读）：转录文件、模型设置快照与已保存总结。

不调用模型、不写总结；AI 总结、模型下载与另存为仍在桌面端。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from toolbox import livestream_summary_rule as rule
from toolbox.config_manager import get_export_subdir, load_convert_config
from toolbox.livestream_summary import config as ls_config

_MAX_TEXT = 512 * 1024


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _saved_dir() -> Path:
    return Path(get_export_subdir("livestream_summary"))


def overview() -> dict:
    cfg = load_convert_config()
    work = str(cfg.get("work_dir") or "").strip()
    transcripts = []
    if work:
        for path in rule.scan_transcripts(work):
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                rel = path.relative_to(Path(work)).as_posix()
            except ValueError:
                rel = path.name
            transcripts.append({
                "name": path.name,
                "rel": rel,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
            })
    saved = []
    out_dir = _saved_dir()
    if out_dir.is_dir():
        for path in sorted(out_dir.iterdir(),
                           key=lambda item: item.stat().st_mtime
                           if item.is_file() else 0, reverse=True):
            if not path.is_file() or path.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            saved.append({
                "name": path.name,
                "size": int(stat.st_size),
                "mtime": _ts(stat.st_mtime),
            })
    try:
        settings = ls_config.load_settings()
        mode = settings.mode
        base_url = settings.base_url
        model = settings.model
        chunk_chars = settings.chunk_chars
        has_key = bool(ls_config.has_api_key())
    except Exception:
        mode, base_url, model, chunk_chars, has_key = "ai", "", "", 6000, False
    return {
        "work_dir": work,
        "transcripts": transcripts,
        "saved": saved,
        "settings": {"mode": mode, "base_url": base_url, "model": model,
                     "chunk_chars": chunk_chars, "has_key": has_key},
        "output_dir": str(out_dir),
        "note": "只读：AI 总结、模型检测 / 下载、另存为与日志仍在桌面端。",
    }


def saved_text(name: str) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少文件名")
    root = _saved_dir().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("文件不在总结目录内") from exc
    if not target.is_file() or target.suffix.lower() not in (".txt", ".md"):
        raise ValueError("总结文件不存在")
    try:
        if target.stat().st_size > _MAX_TEXT:
            raise ValueError("总结文件超过 512KB，请到桌面端查看")
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise ValueError("总结文件读取失败") from exc
    return {"name": target.name, "text": text}
