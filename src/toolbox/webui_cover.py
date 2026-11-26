"""封面自动化的 Qt-free Web 用例。

Web 仅在已配置工作目录及其子目录内补同名封面；模板只读复用桌面保存的
cover_templates，不持有第二套配置。图片的唯一写入方是 cover_automation。
"""

from __future__ import annotations

import json
from pathlib import Path

from toolbox import cover_automation
from toolbox.config_manager import get_config_dir, load_convert_config

MAX_ROWS = 2000
FIELDS = {
    "path": {"type": "str", "label": "相对子目录", "default": ""},
    "recursive": {"type": "bool", "label": "包含子目录", "default": True},
    "template": {"type": "template", "label": "封面模板", "default": ""},
}


def normalize(payload: dict | None) -> dict:
    payload = payload or {}
    if not isinstance(payload, dict):
        raise ValueError("封面参数格式不正确，请重新扫描")
    result = {}
    for key, spec in FIELDS.items():
        value = payload.get(key, spec["default"])
        if spec["type"] == "bool":
            if not isinstance(value, bool):
                raise ValueError("扫描选项格式不正确，请重新扫描")
        else:
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError("目录或模板名称格式不正确，请重新选择")
            value = value.strip()
        result[key] = value
    return result


def _work_root() -> Path:
    value = str(load_convert_config().get("work_dir") or "").strip()
    if not value or not Path(value).is_dir():
        raise ValueError("尚未设置有效工作目录，请先在全局设置中配置")
    return Path(value).resolve()


def _scan_root(path: str) -> Path:
    root = _work_root()
    relative = Path(path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("扫描目录须为工作目录内的相对子目录")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_dir():
        raise ValueError("扫描目录不存在或不在工作目录内，请重新选择")
    return candidate


def _template_path(name: str) -> Path:
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".json":
        raise ValueError("请选择已保存的封面模板")
    root = (get_config_dir() / "cover_templates").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("模板不存在或已被移动，请刷新后重新选择")
    return path


def _read_template(name: str) -> dict:
    path = _template_path(name)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("模板文件过大，请在桌面端重新保存")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("模板无法读取，请在桌面端重新保存") from exc
    if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
        raise ValueError("模板格式不正确，请在桌面端重新保存")
    return data


def overview() -> dict:
    try:
        root = _work_root()
    except ValueError:
        root = None
    directory = get_config_dir() / "cover_templates"
    templates = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                _template_path(path.name)
            except ValueError:
                continue
            templates.append({"key": path.name, "name": path.stem})
    return {"has_work_dir": root is not None, "work_dir": root.name if root else "",
            "templates": templates, "fields": FIELDS,
            "note": "模板排版在桌面端编辑并保存；这里可识别本地视频标题和日期，批量补齐同名封面。"}


def preview(payload: dict | None = None) -> dict:
    options = normalize(payload)
    root = _scan_root(options["path"])
    rows = cover_automation.scan_videos(root, recursive=options["recursive"])
    if len(rows) > MAX_ROWS:
        raise ValueError("扫描结果超过 2000 条，请缩小到相对子目录后重试")
    return {"rows": rows, "total": len(rows), "root_label": root.name,
            "ready": sum(row.get("status") == "ready" for row in rows),
            "exists": sum(row.get("status") == "exists" for row in rows),
            "invalid": sum(row.get("status") in ("invalid", "conflict") for row in rows)}


def prepare(payload: dict) -> tuple[Path, list[dict], dict]:
    options = normalize(payload)
    root = _scan_root(options["path"])
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise ValueError("请选择 1 至 2000 条视频后再开始")
    plan = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("封面清单格式不正确，请重新扫描")
        video = row.get("video")
        if not isinstance(video, str) or not video or len(video) > 1000:
            raise ValueError("视频路径格式不正确，请重新扫描")
        relative = Path(video)
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise ValueError("视频须位于扫描目录内，请重新扫描")
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("视频不存在或不在扫描目录内，请重新扫描")
        key = str(candidate).casefold()
        if key in seen:
            raise ValueError("封面清单包含重复视频，请重新扫描")
        seen.add(key)
        title, date = row.get("title", ""), row.get("date", "")
        if (not isinstance(title, str) or len(title) > 2000
                or not isinstance(date, str) or len(date) > 64):
            raise ValueError("标题或日期过长，请修改后再开始")
        plan.append({"video": video, "title": title, "date": date,
                     "status": "ready", "reason": ""})
    return root, plan, _read_template(options["template"])


def generate(plan: tuple[Path, list[dict], dict], *, on_progress, is_cancelled) -> dict:
    root, rows, template = plan
    result = cover_automation.generate_missing(
        root, rows, template, on_progress=on_progress, is_cancelled=is_cancelled)
    result["summary"] = (
        f"{'已取消' if result.get('cancelled') else '已完成'}：生成 {result.get('generated', 0)} 张，"
        f"跳过 {result.get('skipped', 0)} 条，失败 {result.get('failed', 0)} 条；封面位于视频同目录。")
    return result
