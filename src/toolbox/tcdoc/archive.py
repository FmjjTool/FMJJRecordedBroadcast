"""腾讯文档监控 · 正文本地存档。

链路：预览抓取 → 逐页正文落 SQLite（新增 / 修改 / 删除变更）→ 自动写 Markdown 快照。

与 tables.py 的表格同步保持同一套增量口径：
- 页面 id（AI 文档侧栏的 data-block-id）作为主键；
- 内容哈希变化 = 修改；本地有、本次完整抓取里没有 = 删除；
- 页面树没展开全 / 单页回退等不完整抓取，绝不删除本地历史，避免误删。

本模块是 pages.db 的唯一写入方；schema v2 追加 page_assets，不改旧正文表。
page_assets 是当前页面的图片清单（派生文件在正文图片/），不是历史版本库。
旧正文没有图片清单时仍可读取，重新抓取后才补图；旧图片文件不自动删除。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import config as cfg_mod

CST = timezone(timedelta(hours=8))
CHANGE_TEXT_LIMIT = 4000   # 变更记录里的正文摘录上限，避免历史版本把库撑大

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    doc_key TEXT, page_id TEXT, title TEXT, level INTEGER, page_order INTEGER,
    page_url TEXT, content TEXT, content_hash TEXT, fetched_at TEXT,
    PRIMARY KEY (doc_key, page_id));
CREATE TABLE IF NOT EXISTS page_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
    doc_key TEXT, page_id TEXT, page_title TEXT,
    kind TEXT, summary TEXT, before_text TEXT, after_text TEXT);
CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages (doc_key, page_order);
CREATE INDEX IF NOT EXISTS idx_page_changes_doc ON page_changes (doc_key, ts);
CREATE TABLE IF NOT EXISTS page_assets (
    doc_key TEXT NOT NULL, page_id TEXT NOT NULL, images_json TEXT NOT NULL,
    PRIMARY KEY (doc_key, page_id));
CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT);
INSERT OR REPLACE INTO archive_meta VALUES ('schema_version', '2');
"""


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def db_path() -> Path:
    return cfg_mod.pages_db_path()


def init_db(path=None) -> Path:
    path = Path(path or db_path())
    existed = path.is_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        if existed:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "pages" in names and "page_assets" not in names:
                _backup_before_images(conn, path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    return path


def _backup_before_images(conn, path: Path) -> None:
    """旧库首次追加图片表前做一次一致性备份；失败则不开始升级。"""
    backup = path.with_name(path.name + ".pre-body-images.bak")
    if backup.exists():
        return
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".bak.tmp", dir=path.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as destination:
            conn.backup(destination)
        if not backup.exists():
            os.replace(temporary, backup)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ==================== 写入 ====================

def _hash(title: str, content: str) -> str:
    return hashlib.md5(f"{title}\n{content}".encode("utf-8")).hexdigest()


def _clip(text: str, limit: int = CHANGE_TEXT_LIMIT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _first_line(text: str, limit: int = 60) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return "（空白页）"


def _page_url(doc: dict, page_id: str) -> str:
    url = str(doc.get("url") or "")
    if (doc.get("kind") or "").lower() == "aio" and page_id != "main":
        return f"{url.split('?', 1)[0]}?p={page_id}"
    return url


def _decode_images(value: str) -> list[dict]:
    try:
        rows = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _image_json(images) -> str:
    """仅持久化约定的展示字段；不保存来源票据、原始 URL 或浏览器状态。"""
    fields = ("key", "name", "path", "status", "error", "width", "height")
    rows = [{key: image.get(key, 0 if key in ("width", "height") else "")
             for key in fields} for image in images if isinstance(image, dict)]
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _keep_downloaded_images(images: list[dict], previous: str | None) -> list[dict]:
    """来源相同而本次下载失败时，保留仍有效的旧文件，并明确显示本次失败。"""
    prior = {row.get("key"): row for row in _decode_images(previous)}
    result = []
    for image in images:
        image = dict(image)
        old = prior.get(image.get("key")) or {}
        if image.get("status") == "failed" and old.get("status") == "downloaded":
            try:
                from .body_images import safe_image_path
                safe_image_path(old.get("path") or "")
            except (OSError, ValueError):
                pass
            else:
                image = {**old, "error": "本次下载失败，保留已有图片"}
        result.append(image)
    return result


def save_preview(doc: dict, pages: list[dict], text: str, complete: bool = True,
                 path=None) -> dict:
    """把本次预览的逐页正文落库，并返回增量统计 + 自动快照路径。"""
    doc_key = str(doc.get("key") or "")
    if not doc_key:
        raise ValueError("文档缺少 key，无法存档")
    path = init_db(path)
    now = now_str()
    stats = {"pages": len(pages), "chars": len(text or ""),
             "added": 0, "updated": 0, "removed": 0, "skipped": 0,
             "complete": bool(complete)}
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("BEGIN IMMEDIATE")
        old = {row[0]: {"title": row[1], "level": row[2], "order": row[3],
                        "content": row[4], "hash": row[5]}
               for row in conn.execute(
                   "SELECT page_id, title, level, page_order, content, content_hash "
                   "FROM pages WHERE doc_key=?", (doc_key,))}
        old_assets = {row[0]: row[1] for row in conn.execute(
            "SELECT page_id, images_json FROM page_assets WHERE doc_key=?", (doc_key,))}
        seen: set[str] = set()
        for index, item in enumerate(pages, 1):
            page_id = str(item.get("id") or f"p{index}")
            title = str(item.get("title") or f"第 {index} 篇")
            seen.add(page_id)
            if item.get("error"):
                stats["skipped"] += 1      # 本篇抓取失败：保留旧正文，不覆盖
                continue
            content = str(item.get("text") or "")
            h = _hash(title, content)
            prev = old.get(page_id)
            previous_images = old_assets.get(page_id)
            images_json = (_image_json(_keep_downloaded_images(
                               item.get("images") or [], previous_images))
                           if "images" in item else previous_images)
            images_changed = images_json != previous_images
            if prev is None:
                stats["added"] += 1
                conn.execute(
                    "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_key, page_id, title, int(item.get("level") or 0), index,
                     _page_url(doc, page_id), content, h, now))
                conn.execute(
                    "INSERT INTO page_changes (ts,doc_key,page_id,page_title,kind,"
                    "summary,before_text,after_text) VALUES (?,?,?,?,?,?,?,?)",
                    (now, doc_key, page_id, title, "新增",
                     _first_line(content), "", _clip(content)))
            elif prev["hash"] != h or images_changed:
                stats["updated"] += 1
                if prev["title"] != title:
                    summary = f"标题变更：{prev['title']} → {title}"[:120]
                elif prev["hash"] != h:
                    summary = f"正文 {len(prev['content'])} → {len(content)} 字"
                else:
                    summary = "正文图片清单变更"
                conn.execute(
                    "UPDATE pages SET title=?, level=?, page_order=?, page_url=?, "
                    "content=?, content_hash=?, fetched_at=? "
                    "WHERE doc_key=? AND page_id=?",
                    (title, int(item.get("level") or 0), index,
                     _page_url(doc, page_id), content, h, now, doc_key, page_id))
                conn.execute(
                    "INSERT INTO page_changes (ts,doc_key,page_id,page_title,kind,"
                    "summary,before_text,after_text) VALUES (?,?,?,?,?,?,?,?)",
                    (now, doc_key, page_id, title, "修改", summary,
                     _clip(prev["content"]), _clip(content)))
            else:
                conn.execute(
                    "UPDATE pages SET title=?, level=?, page_order=?, page_url=?, "
                    "fetched_at=? WHERE doc_key=? AND page_id=?",
                    (title, int(item.get("level") or 0), index,
                     _page_url(doc, page_id), now, doc_key, page_id))
            if images_json is not None:
                conn.execute(
                    "INSERT INTO page_assets VALUES (?,?,?) "
                    "ON CONFLICT(doc_key,page_id) DO UPDATE SET images_json=excluded.images_json",
                    (doc_key, page_id, images_json))

        if complete:
            for page_id, prev in old.items():
                if page_id in seen:
                    continue
                stats["removed"] += 1
                conn.execute("DELETE FROM pages WHERE doc_key=? AND page_id=?",
                             (doc_key, page_id))
                conn.execute("DELETE FROM page_assets WHERE doc_key=? AND page_id=?",
                             (doc_key, page_id))
                conn.execute(
                    "INSERT INTO page_changes (ts,doc_key,page_id,page_title,kind,"
                    "summary,before_text,after_text) VALUES (?,?,?,?,?,?,?,?)",
                    (now, doc_key, page_id, prev["title"], "删除",
                     prev["title"], _clip(prev["content"]), ""))
        conn.commit()

    try:
        # 不完整抓取时从已提交库生成快照，失败页面继续保留上次成功内容。
        saved_pages = pages_of(doc_key, path=path)
        from .preview_content import markdown_for_pages
        target = snapshot_path(doc)
        stats["file"] = str(export_snapshot(
            doc, markdown_for_pages(saved_pages, relative_to=target.parent), path=target))
        stats["images"] = sum(len(page.get("images") or []) for page in saved_pages)
    except Exception as exc:  # noqa: BLE001 - 快照失败不影响预览
        stats["file_error"] = str(exc)[:160]
    return stats


def snapshot_path(doc: dict) -> Path:
    return cfg_mod.docs_dir() / f"{cfg_mod.doc_stem(doc)}.md"


def export_snapshot(doc: dict, text: str, path=None) -> Path:
    """写一份自动更新的 Markdown 快照（每次预览覆盖，历史变化在 page_changes 里）。"""
    target = Path(path or snapshot_path(doc))
    target.parent.mkdir(parents=True, exist_ok=True)
    ts = now_str()
    body = (f"# {doc.get('name') or doc.get('key')}\n\n"
            f"> 来源：{doc.get('url') or ''}  \n"
            f"> 自动存档：{ts}\n\n"
            f"{str(text or '').strip()}\n")
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


# ==================== 读取 ====================

def pages_of(doc_key: str, path=None) -> list[dict]:
    """该文档本地存档的全部页面（按侧栏顺序）。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT page_id, title, level, page_order, page_url, content, fetched_at "
            "FROM pages WHERE doc_key=? ORDER BY page_order", (doc_key,)).fetchall()
        assets = {row[0]: _decode_images(row[1]) for row in conn.execute(
            "SELECT page_id, images_json FROM page_assets WHERE doc_key=?", (doc_key,))}
    return [{"id": r[0], "title": r[1], "level": r[2], "order": r[3],
             "url": r[4], "text": r[5], "fetched_at": r[6],
             "images": assets.get(r[0], []), "images_captured": r[0] in assets} for r in rows]


def recent_changes(doc_key: str, limit: int = 100, path=None) -> list[dict]:
    """最近的正文变更（新增 / 修改 / 删除）。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT ts, page_title, kind, page_id, summary FROM page_changes "
            "WHERE doc_key=? ORDER BY id DESC LIMIT ?", (doc_key, limit)).fetchall()
    return [{"ts": r[0], "page_title": r[1], "kind": r[2],
             "page_id": r[3], "summary": r[4]} for r in rows]


def stats(doc_key: str, path=None) -> dict:
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn:
        n_pages, n_chars = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) FROM pages WHERE doc_key=?",
            (doc_key,)).fetchone()
        last = conn.execute(
            "SELECT MAX(fetched_at) FROM pages WHERE doc_key=?", (doc_key,)).fetchone()[0]
    return {"pages": n_pages, "chars": n_chars, "last": last or ""}


def clear_doc(doc_key: str, path=None) -> int:
    """删除该文档的全部正文存档与变更记录，返回删除的页数。"""
    path = init_db(path)
    with closing(sqlite3.connect(path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM pages WHERE doc_key=?", (doc_key,)).fetchone()[0]
        conn.execute("DELETE FROM pages WHERE doc_key=?", (doc_key,))
        conn.execute("DELETE FROM page_changes WHERE doc_key=?", (doc_key,))
        conn.execute("DELETE FROM page_assets WHERE doc_key=?", (doc_key,))
        conn.commit()
    return n
