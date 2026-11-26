"""WebUI 直播总结：转录文件、模型设置、已保存总结，以及总结任务。

AI 总结走 `toolbox/livestream_summary` 的同一套流水线（分块提取 → 合并 → 终稿 →
格式校验），本地规则模式不联网；结果写入统一导出目录的「直播总结」。
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from pathlib import Path

from toolbox import livestream_summary_rule as rule
from toolbox.config_manager import get_export_subdir, load_convert_config
from toolbox.livestream_summary import config as ls_config
from toolbox.livestream_summary import pipeline
from toolbox.livestream_summary.local_options import FIELD_SPECS, defaults, normalize_options
from toolbox.livestream_summary.session import client_session

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
        local = settings.local
    except Exception:
        mode, base_url, model, chunk_chars, has_key = "ai", "", "", 6000, False
        local = defaults()
    from toolbox.livestream_summary import optional_runtime
    return {
        "work_dir": work,
        "transcripts": transcripts,
        "saved": saved,
        "settings": {"mode": mode, "base_url": base_url, "model": model,
                     "chunk_chars": chunk_chars, "has_key": has_key, "local": local},
        "local_fields": FIELD_SPECS,
        "local_runtime": optional_runtime.status(),
        "output_dir": str(out_dir),
        "note": "本地推理模块与模型在全局设置中按需准备，标准版与 FULL 版均不内置。",
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


def settings_snapshot() -> dict:
    settings = ls_config.load_settings()
    return {
        "mode": settings.mode,
        "base_url": settings.base_url,
        "model": settings.model,
        "chunk_chars": settings.chunk_chars,
        "local": settings.local,
        "local_fields": FIELD_SPECS,
        "has_key": bool(ls_config.has_api_key()),
        "modes": ls_config.MODES,
        "default_base_url": ls_config.DEFAULT_BASE_URL,
    }


def save_settings(payload: dict) -> dict:
    """保存模型设置；api_key 仅在提交非空时覆盖，永不回显。"""
    current = ls_config.load_settings()
    base_url = str(payload.get("base_url") or current.base_url).strip()
    model = str(payload.get("model", current.model) or "").strip()
    mode = str(payload.get("mode") or current.mode)
    try:
        chunk_chars = int(payload.get("chunk_chars") or current.chunk_chars)
    except (TypeError, ValueError):
        raise ValueError("每块字数必须是数字") from None
    if mode not in ls_config.MODES:
        raise ValueError("未知的总结模式")
    if mode == "ai" and not model:
        raise ValueError("请填写模型名称")
    local = normalize_options(payload.get("local", current.local))
    ls_config.save_settings(ls_config.Settings(
        base_url=base_url or ls_config.DEFAULT_BASE_URL,
        model=model,
        chunk_chars=max(2000, min(20000, chunk_chars)),
        output_dir=current.output_dir,
        mode=mode,
        local=local,
    ))
    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        if not ls_config.save_api_key(api_key):
            raise ValueError("API Key 保存失败（本机加密存储不可用）")
    return {"settings": settings_snapshot(),
            "summary": "设置已保存" + ("（含新 API Key）" if api_key else "")}


def test_connection(on_log=None, is_cancelled=None, on_progress=None) -> dict:
    settings = ls_config.load_settings()
    log = on_log or (lambda _message: None)
    cancel = _CancelFlag(is_cancelled or (lambda: False))
    def progress(percent, message):
        log(message)
        if on_progress:
            on_progress(percent, message)
    with client_session(settings, cancel=cancel, progress=progress) as client:
        message = client.test_connection(cancel=cancel)
    return {"message": str(message or "连接正常"),
            "summary": "本地模型连接正常" if settings.mode == "local"
            else f"连接正常 · {settings.model}"}


def _transcript_path(rel: str) -> Path:
    cfg = load_convert_config()
    work = str(cfg.get("work_dir") or "").strip()
    if not work:
        raise ValueError("还没有设置工作目录（系统设置 → 目录）")
    base = Path(work).resolve()
    value = str(rel or "").strip()
    if not value or value.startswith("/") or ":" in value:
        raise ValueError("请选择转录文件")
    target = (base / value).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError("转录文件不在工作目录内") from None
    if not target.is_file() or target.suffix.lower() not in (".txt", ".srt"):
        raise ValueError("转录文件不存在")
    return target


def check_transcript(rel: str) -> None:
    _transcript_path(rel)


def run_summary(payload: dict, on_log=None, is_cancelled=None, on_progress=None) -> dict:
    log = on_log or (lambda _msg: None)
    settings = ls_config.load_settings()
    mode = str(payload.get("mode") or settings.mode)
    if mode not in ls_config.MODES:
        raise ValueError("未知的总结模式")
    settings = replace(settings, mode=mode)
    source = _transcript_path(str(payload.get("rel") or ""))
    log(f"开始总结（{ls_config.MODES[mode]}）：{source.name}")

    text = rule.read_transcript(source)
    folder = str(source.parent)
    cancel = is_cancelled or (lambda: False)

    def progress(stage: str, percent: int, message: str) -> None:
        log(f"{message}")
        if on_progress:
            on_progress(percent, message)

    if mode == "rule":
        result = pipeline.run_rule(text, source.name, folder=folder)
    else:
        flag = _CancelFlag(cancel)
        with client_session(settings, cancel=flag,
                            progress=lambda percent, message: progress(
                                "load", percent, message)) as client:
            log(f"每块 {settings.chunk_chars} 字")
            result = pipeline.run(
                text, source.name, client,
                progress=progress, cancel=flag,
                chunk_chars=settings.chunk_chars, folder=folder,
                max_tokens=settings.local["max_tokens"] if mode == "local" else 8192)

    target = pipeline.default_output_path(
        result.text, source, str(get_export_subdir("livestream_summary")))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.text, encoding="utf-8")
    log(f"已保存：{target}")

    stats = result.stats or {}
    summary = (f"完成：{stats.get('chunks', 0)} 块 · {stats.get('requests', 0)} 次请求"
               if mode != "rule" else f"完成：本地规则 · {stats.get('chars', 0)} 字")
    return {
        "name": target.name,
        "path": str(target),
        "text": result.text,
        "stats": stats,
        "problems": list(result.problems or []),
        "mode": mode,
        "summary": summary + " · 已保存",
    }


class _CancelFlag:
    """把 is_cancelled 回调包装成 pipeline 需要的 is_set() 接口。"""

    def __init__(self, check):
        self._check = check

    def is_set(self) -> bool:
        return bool(self._check())
