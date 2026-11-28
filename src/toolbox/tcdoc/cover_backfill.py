"""批量补封面完成后的腾讯文档回填用例（Qt-free）。

目标表由上层选择，但当前授权只允许《封茗囧菌数据汇总库》的「官方动态」
（ss_h2uh2s）「封面图」字段。只追加图片，不新增、不删除、不覆盖已有图片
或其它字段。桌面端与 WebUI 共用本模块。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from toolbox import sync_refresh
from toolbox.tcdoc import bridge
from toolbox.tcdoc import field_maps
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (
    ImportErrorDetail,
    LocalImage,
    LocalImportClient,
    ManagedAuth,
    UnknownWriteResult,
)

DOC_KEY = "DRnd3bkhkQUR1aEJr"
TAB_ID = "ss_h2uh2s"
DOC_NAME = "封茗囧菌数据汇总库"
TAB_NAME = "官方动态"
FIELD_NAME = "封面图"
MAX_ITEMS = 1000

# 图片追加字段与匹配字段集中定义，页面不复制目标表规则。
TARGET_SPECS = {
    (DOC_KEY, TAB_ID): {
        "profile": "posts", "tab_name": "官方动态", "field": "封面图",
        "title": "作品标题", "date": "发布时间",
    },
}


def _normalize_title(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


def _cell_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(part for part in (_cell_text(item) for item in value) if part)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or value.get("name") or "").strip()
    return str(value or "").strip()


def _day(value: object) -> str:
    text = _cell_text(value)
    if text.isdigit() and len(text) >= 12:
        return pending._ms_to_day(text)
    match = re.search(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}", text)
    if not match:
        return text[:10]
    raw = match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
    parts = re.split(r"[-/.]", raw)
    try:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except (IndexError, ValueError):
        return ""


def _target_spec(doc_key: str, tab_id: str) -> dict:
    key = (str(doc_key or "").strip(), str(tab_id or "").strip())
    from toolbox.tcdoc.import_local import validate_target
    try:
        validate_target(*key)
    except ImportErrorDetail:
        raise
    spec = TARGET_SPECS.get(key)
    if spec is None:
        raise ImportErrorDetail("该表未授权批量补封面；请选择《封茗囧菌数据汇总库》的官方动态")
    return {"doc_key": key[0], "tab_id": key[1], **spec}


def _field(fields: list[dict], semantic: str, doc_key: str, tab_id: str) -> dict:
    title = field_maps.target(doc_key, tab_id, semantic)
    if not title:
        raise ImportErrorDetail(f"字段已按映射配置为不写：{semantic}")
    hits = [item for item in fields if str(item.get("fieldTitle") or "") == title]
    if len(hits) != 1:
        raise ImportErrorDetail(f"字段无法唯一匹配：{semantic}→{title}")
    return hits[0]


def _validate_root(root: Path) -> Path:
    value = Path(root).expanduser().resolve(strict=True)
    if not value.is_dir():
        raise ValueError("封面目录不存在，请重新扫描")
    return value


def _image(root: Path, value: object) -> LocalImage:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("生成结果缺少封面路径，请重新扫描")
    relative = Path(value)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("封面路径超出扫描目录，请重新扫描")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("封面文件不存在，请重新生成后再回填")
    try:
        return LocalImage.inspect(path, root)
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc


def _rows(rows: list[dict], root: Path) -> list[dict]:
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ITEMS:
        raise ValueError(f"请提供 1 至 {MAX_ITEMS} 条已生成封面")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("封面回填清单格式不正确，请重新生成")
        video = str(row.get("video") or "").strip()
        key = video.casefold()
        if not video or key in seen:
            raise ValueError("封面回填清单包含空路径或重复视频")
        seen.add(key)
        title = str(row.get("title") or "").strip()
        date = str(row.get("date") or "").strip()[:10]
        if not title:
            raise ValueError("封面标题为空，无法匹配腾讯文档")
        result.append({"video": video, "title": title, "date": date,
                       "cover": str(row.get("cover") or "").strip(),
                       "_image": _image(root, row.get("cover"))})
    return result


def _client_from_cookie(doc_key: str, tab_id: str) -> LocalImportClient:
    import auth as auth_module

    cookie = auth_module.get_cookie("tcdoc_openapi") or ""
    if not cookie:
        raise ValueError("尚未登录腾讯文档，请先在桌面端完成登录后重试")
    try:
        client = LocalImportClient(ManagedAuth(lambda: cookie), doc_key, tab_id)
        client.connect()
        return client
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc


def _match(rows: list[dict], cloud: list[dict], field_title: str,
           title_title: str, date_title: str) -> list[dict]:
    index: dict[tuple[str, str], list[dict]] = {}
    for record in cloud:
        values = record.get("values") or {}
        key = (_normalize_title(_cell_text(values.get(title_title))),
               _day(values.get(date_title)))
        if key[0]:
            index.setdefault(key, []).append(record)
    out = []
    for row in rows:
        key = (_normalize_title(row["title"]), row["date"])
        candidates = index.get(key, [])
        item = {"video": row["video"], "cover": row["cover"], "title": row["title"],
                "date": row["date"], "status": "unmatched", "reason": "",
                "record_id": "", "remote_title": "", "remote_date": "",
                "existing_images": 0, "field": field_title}
        if len(candidates) == 1:
            record = candidates[0]
            values = record.get("values") or {}
            try:
                existing = pending._image_write_entries(values, field_title)
            except ImportErrorDetail as exc:
                item.update(status="blocked", reason=str(exc))
            else:
                names = pending._image_titles(values, field_title)
                item.update(status="matched", record_id=str(record.get("recordID") or ""),
                            remote_title=_cell_text(values.get(title_title)),
                            remote_date=_day(values.get(date_title)),
                            existing_images=len(existing),
                            duplicate=any(name.casefold() == row["_image"].upload_name.casefold()
                                           for name in names))
                if not item["record_id"]:
                    item.update(status="blocked", reason="腾讯文档记录缺少 recordID")
                elif item["duplicate"]:
                    item.update(status="skipped", reason="腾讯文档已有同名封面")
        elif len(candidates) > 1:
            item.update(status="ambiguous", reason="标题和日期匹配到多条记录")
        else:
            item.update(status="unmatched", reason="未找到标题和日期完全匹配的记录")
        out.append(item)
    return out


def list_targets() -> list[dict]:
    """返回当前本地快照中可用于图片追加的授权目标表。"""
    result = []
    try:
        candidates = bridge.list_targets()
    except Exception:
        candidates = []
    for target in candidates:
        try:
            spec = _target_spec(target.get("doc_key"), target.get("tab_id"))
        except ImportErrorDetail:
            continue
        names = {str(col.get("name") or col.get("fieldTitle") or "")
                 for col in target.get("columns") or []}
        image_name = field_maps.target(spec["doc_key"], spec["tab_id"], spec["field"])
        title_name = field_maps.target(spec["doc_key"], spec["tab_id"], spec["title"])
        date_name = field_maps.target(spec["doc_key"], spec["tab_id"], spec["date"])
        if not image_name or not title_name or not date_name:
            continue
        if names and not {image_name, title_name, date_name}.issubset(names):
            continue
        result.append({"doc_key": spec["doc_key"], "tab_id": spec["tab_id"],
                       "doc_name": target.get("doc_name") or DOC_NAME,
                       "tab_name": target.get("tab_name") or spec["tab_name"],
                       "n_rows": target.get("n_rows") or 0,
                       "field": image_name, "title_field": title_name,
                       "date_field": date_name, "profile": spec["profile"]})
    return result


def status() -> dict:
    allowed, message = sync_refresh.upload_allowed()
    targets = list_targets()
    default = next((item for item in targets
                    if (item["doc_key"], item["tab_id"]) == (DOC_KEY, TAB_ID)),
                   targets[0] if targets else None)
    return {"doc_name": DOC_NAME, "tab_name": TAB_NAME, "doc_key": DOC_KEY,
            "tab_id": TAB_ID, "field": FIELD_NAME, "max_items": MAX_ITEMS,
            "targets": targets, "target": default,
            "refresh": {"allowed": bool(allowed), "message": str(message)},
            "bypass": bool(sync_refresh.bypass_enabled())}


def preview(root: Path, rows: list[dict], *, doc_key: str = DOC_KEY,
            tab_id: str = TAB_ID,
            client_factory: Callable[[], LocalImportClient] | None = None) -> dict:
    spec = _target_spec(doc_key, tab_id)
    root = _validate_root(root)
    local = _rows(rows, root)
    client = client_factory() if client_factory else _client_from_cookie(spec["doc_key"], spec["tab_id"])
    fields = client.get_fields()
    field = _field(fields, spec["field"], spec["doc_key"], spec["tab_id"])
    title_field = _field(fields, spec["title"], spec["doc_key"], spec["tab_id"])
    date_field = _field(fields, spec["date"], spec["doc_key"], spec["tab_id"])
    if int(field.get("fieldType") or 0) != 5:
        raise ValueError(f"腾讯文档「{field.get('fieldTitle') or spec['field']}」字段不是图片类型，已停止回填")
    cloud = client.get_records()
    items = _match(local, cloud, str(field.get("fieldTitle")),
                   str(title_field.get("fieldTitle")), str(date_field.get("fieldTitle")))
    counts = {key: sum(item.get("status") == key for item in items)
              for key in ("matched", "skipped", "ambiguous", "unmatched", "blocked")}
    return {"doc_name": DOC_NAME, "tab_name": spec["tab_name"], "doc_key": spec["doc_key"],
            "tab_id": spec["tab_id"], "field": str(field.get("fieldTitle")),
            "title_field": str(title_field.get("fieldTitle")),
            "date_field": str(date_field.get("fieldTitle")), "items": items,
            "counts": counts, "refresh": status()["refresh"],
            "ready": counts["matched"] - counts["skipped"]}


def apply(root: Path, rows: list[dict], *, doc_key: str = DOC_KEY,
          tab_id: str = TAB_ID, confirmed: bool = False,
          client_factory: Callable[[], LocalImportClient] | None = None) -> dict:
    if not confirmed:
        raise ValueError("请先确认腾讯文档回填范围")
    spec = _target_spec(doc_key, tab_id)
    allowed, message = sync_refresh.upload_allowed()
    if not allowed:
        raise ValueError(f"当前数据未刷新，暂不允许回填腾讯文档：{message}。请先「同步前刷新」")
    root = _validate_root(root)
    local = _rows(rows, root)
    client = client_factory() if client_factory else _client_from_cookie(spec["doc_key"], spec["tab_id"])
    fields = client.get_fields()
    field = _field(fields, spec["field"], spec["doc_key"], spec["tab_id"])
    title_field = _field(fields, spec["title"], spec["doc_key"], spec["tab_id"])
    date_field = _field(fields, spec["date"], spec["doc_key"], spec["tab_id"])
    field_title = str(field.get("fieldTitle"))
    if int(field.get("fieldType") or 0) != 5:
        raise ValueError(f"腾讯文档「{field.get('fieldTitle') or spec['field']}」字段不是图片类型，已停止回填")
    with pending._write_lock():
        cloud = client.get_records()
        items = _match(local, cloud, field_title,
                       str(title_field.get("fieldTitle")), str(date_field.get("fieldTitle")))
        ready = [item for item in items if item["status"] == "matched" and not item.get("duplicate")]
        updated, skipped, errors = [], [], []
        by_id = {str(item.get("recordID")): item for item in cloud}
        for item, local_row in zip(items, local):
            if item["status"] == "skipped":
                skipped.append(item)
                continue
            if item["status"] != "matched" or item.get("duplicate"):
                if item["status"] != "matched":
                    errors.append(item)
                continue
            try:
                image = local_row["_image"]
                record = by_id.get(item["record_id"])
                entries = pending._image_write_entries((record or {}).get("values") or {}, field_title)
                image_id = client.upload_image(image)
                client.update_image_field(item["record_id"], field_title,
                                          entries + [{"imageID": image_id}])
                fresh = client.get_records()
                saved = next((row for row in fresh if str(row.get("recordID")) == item["record_id"]), None)
                names = pending._image_titles((saved or {}).get("values") or {}, field_title)
                if saved is None or image.upload_name not in names:
                    raise ValueError("写入后读回未找到新增封面")
                item.update(status="updated", reason="已回填并读回核对", existing_images=len(entries))
                updated.append(item)
            except UnknownWriteResult:
                raise
            except Exception as exc:  # noqa: BLE001 - 单条失败保留已完成结果并汇总
                item.update(status="failed", reason=str(exc)[:180])
                errors.append(item)
        if updated:
            sync_refresh.invalidate("批量补封面回填完成，需重新刷新后再写")
    return {"doc_name": DOC_NAME, "tab_name": spec["tab_name"], "doc_key": spec["doc_key"],
            "tab_id": spec["tab_id"], "field": field_title, "updated": len(updated),
            "skipped": len(skipped), "failed": len(errors),
            "record_ids": [item["record_id"] for item in updated],
            "items": items,
            "summary": f"已回填 {len(updated)} 条，跳过 {len(skipped)} 条，失败 {len(errors)} 条"}
