"""腾讯文档监控 · 智能表格图片字段增量采集。

同步表格时把类型 5（图片）字段里的 URL 登记到 tables.db 的 images 表，
再增量下载到 download/腾讯文档数据/图片/<文档>/<工作表>/<字段>/。

口径：
- 同一张图片重复同步不重下（URL 路径作为图片身份，query 里的尺寸参数不算变化）；
- 已落盘路径记录在 images.local_path，字段/行标题改名后继续沿用旧路径；
- 只下载腾讯文档图床（*.docs.qq.com），表格里其它外链不碰；
- 图片字段 URL 变化时才重新下载，并保留旧文件不删除。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from toolbox.utils import sanitize_filename

from . import config as cfg_mod
from .tables import row_summary

CST = timezone(timedelta(hours=8))
IMAGE_TYPE = 5
WORKERS = 6
TIMEOUT = 60
MAX_BYTES = 128 * 1024 * 1024

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://docs.qq.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".heic", ".heif"}
_CT_EXTS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
_URL_RE = re.compile(r"https?://[^\s<>\"'，,；;]+", re.IGNORECASE)
_TRAIL_PUNCT = ".,;，。；）)】]"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    doc_key TEXT NOT NULL,
    tab_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    field TEXT NOT NULL,
    idx INTEGER NOT NULL,
    tab_name TEXT,
    row_no INTEGER,
    summary TEXT,
    url TEXT,
    image_key TEXT,
    local_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    updated_at TEXT,
    PRIMARY KEY (doc_key, tab_id, record_id, field, idx));
CREATE INDEX IF NOT EXISTS idx_images_doc ON images (doc_key, tab_id, row_no);
"""


# ==================== 基础 ====================

def init_db(path=None) -> Path:
    path = Path(path or cfg_mod.tables_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
    return path


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _urls(text) -> list[str]:
    """图片单元格 → URL 列表（支持多图与夹杂标点，忽略非 http 链接）。"""
    out: list[str] = []
    for match in _URL_RE.findall(str(text or "")):
        url = match.strip().rstrip(_TRAIL_PUNCT)
        if url and url not in out:
            out.append(url)
    return out


def _image_key(url: str) -> str:
    """图片身份：只取路径，去掉 ?w=&h= 这类尺寸参数。"""
    try:
        path = urlsplit(url).path or url
    except ValueError:
        path = url
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


def _allowed(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "docs.qq.com" or host.endswith(".docs.qq.com")


def _ext(url: str) -> str:
    try:
        suffix = Path(urlsplit(url).path).suffix.lower()
    except ValueError:
        suffix = ""
    return suffix if suffix in _IMAGE_EXTS else ""


def _ext_from_ct(content_type: str) -> str:
    return _CT_EXTS.get((content_type or "").split(";", 1)[0].strip().lower(), "")


def _folder_name(name: str, max_len: int) -> str:
    return sanitize_filename(str(name or ""), max_len, "未命名")


def _doc_folder(doc_key: str, doc_name: str) -> str:
    name = _folder_name(doc_name, 40)
    key = str(doc_key or "")
    suffix = hashlib.md5(key.encode("utf-8")).hexdigest()[:6] if key else ""
    return f"{name}_{suffix}" if suffix else name


def _target_path(doc_name: str, rec: dict) -> Path:
    """按文档 / 工作表 / 字段归档；文件名带行摘要 + 记录 ID 便于翻找。"""
    field_dir = cfg_mod.images_dir() / _doc_folder(rec.get("doc_key"), doc_name)
    field_dir = field_dir / _folder_name(rec.get("tab_name") or rec.get("tab_id"), 24)
    field_dir = field_dir / _folder_name(rec.get("field"), 16)
    summary = _folder_name(rec.get("summary") or f"第{rec.get('row_no') or 1}行", 24)
    rid = _folder_name(rec.get("record_id"), 16)
    idx = int(rec.get("idx") or 0) + 1
    suffix = _ext(rec.get("url") or "") or ".jpg"
    key = str(rec.get("image_key") or "")[:8]
    return field_dir / f"{summary}_{rid}_{key}_{idx:02d}{suffix}"


def _relative(path: Path) -> str:
    try:
        return path.relative_to(cfg_mod.data_dir()).as_posix()
    except ValueError:
        return str(path)


def _absolute(local_path: str) -> Path:
    path = Path(local_path)
    return path if path.is_absolute() else cfg_mod.data_dir() / path


# ==================== 登记 ====================

def _prune_missing(conn, doc_key: str, tab_id: str, record_ids) -> None:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _keep_records (record_id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _keep_records")
    conn.executemany("INSERT OR IGNORE INTO _keep_records VALUES (?)",
                     [(str(rid),) for rid in record_ids])
    conn.execute(
        "DELETE FROM images WHERE doc_key=? AND tab_id=? AND record_id NOT IN "
        "(SELECT record_id FROM _keep_records)", (doc_key, tab_id))


def _register_rows(conn, doc_key: str, sheet: dict, now: str,
                   prune: bool = False) -> int:
    tab_id = str(sheet.get("tab_id") or "")
    tab_name = str(sheet.get("tab_name") or tab_id)
    rows = sheet.get("rows") or []
    fields = [c.get("name") for c in (sheet.get("columns") or [])
              if c.get("name") and c.get("type") == IMAGE_TYPE]
    if not fields:
        if prune:
            conn.execute("DELETE FROM images WHERE doc_key=? AND tab_id=?",
                         (doc_key, tab_id))
        return 0
    if prune:
        # 行列表是完整快照时才清理已删除记录；文件保留在磁盘，方便找回
        _prune_missing(conn, doc_key, tab_id,
                       (str(row.get("record_id")) for row in rows))
    count = 0
    for row in rows:
        values = row.get("values") or {}
        record_id = str(row.get("record_id") or f"row{row.get('row_no') or 0}")
        row_no = int(row.get("row_no") or 0)
        summary = row_summary(values)
        for field in fields:
            urls = _urls(values.get(field))
            for idx, url in enumerate(urls):
                key = _image_key(url)
                old = conn.execute(
                    "SELECT image_key FROM images WHERE doc_key=? AND tab_id=? "
                    "AND record_id=? AND field=? AND idx=?",
                    (doc_key, tab_id, record_id, field, idx)).fetchone()
                if old is None:
                    conn.execute(
                        "INSERT INTO images (doc_key,tab_id,record_id,field,idx,tab_name,"
                        "row_no,summary,url,image_key,local_path,file_size,status,error,"
                        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'',0,'pending','',?)",
                        (doc_key, tab_id, record_id, field, idx, tab_name, row_no,
                         summary, url, key, now))
                elif str(old[0] or "") != key:
                    # 图片被替换：保留旧文件，新图重新落盘
                    conn.execute(
                        "UPDATE images SET tab_name=?, row_no=?, summary=?, url=?, "
                        "image_key=?, local_path='', file_size=0, status='pending', "
                        "error='', updated_at=? WHERE doc_key=? AND tab_id=? "
                        "AND record_id=? AND field=? AND idx=?",
                        (tab_name, row_no, summary, url, key, now, doc_key, tab_id,
                         record_id, field, idx))
                else:
                    conn.execute(
                        "UPDATE images SET tab_name=?, row_no=?, summary=?, url=?, "
                        "updated_at=? WHERE doc_key=? AND tab_id=? AND record_id=? "
                        "AND field=? AND idx=?",
                        (tab_name, row_no, summary, url, now, doc_key, tab_id,
                         record_id, field, idx))
                count += 1
            # 单元格图片变少时清掉多余登记，旧文件保留
            conn.execute(
                "DELETE FROM images WHERE doc_key=? AND tab_id=? AND record_id=? "
                "AND field=? AND idx>=?", (doc_key, tab_id, record_id, field, len(urls)))
    return count


def register_sheet(doc_key: str, sheet: dict, path=None, prune: bool = False) -> int:
    """把一张工作表里的图片 URL 登记（同步流程调用，只写库不下载）。"""
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        n = _register_rows(conn, str(doc_key), sheet, _now(), prune=prune)
        conn.commit()
    return n


def register_doc(doc_key: str, path=None) -> int:
    """从本地已同步的表格数据补登记图片（供「采集图片」按钮补采旧数据）。"""
    db = init_db(path)
    total = 0
    now = _now()
    with sqlite3.connect(db) as conn:
        metas = conn.execute(
            "SELECT tab_id, tab_name, cols_json FROM sheets WHERE doc_key=? AND skipped=0",
            (str(doc_key),)).fetchall()
        for tab_id, tab_name, cols_json in metas:
            try:
                columns = json.loads(cols_json or "[]")
            except json.JSONDecodeError:
                continue
            if not any(c.get("type") == IMAGE_TYPE for c in columns or []):
                continue
            rows = []
            for record_id, row_no, payload in conn.execute(
                    "SELECT record_id, row_no, data_json FROM rows "
                    "WHERE doc_key=? AND tab_id=?", (str(doc_key), tab_id)):
                try:
                    values = json.loads(payload or "{}")
                except json.JSONDecodeError:
                    values = {}
                rows.append({"record_id": record_id, "row_no": row_no, "values": values})
            total += _register_rows(conn, str(doc_key), {
                "tab_id": tab_id, "tab_name": tab_name,
                "columns": columns, "rows": rows}, now, prune=bool(columns))
        conn.commit()
    return total


# ==================== 下载 ====================

def _login_cookies() -> dict:
    """私有文档的图床可能需要登录 Cookie；公开文档不带也能下。"""
    try:
        from . import session
        return session.parse_cookie_string(session.cookie_string())
    except Exception:  # noqa: BLE001 - 拿不到 Cookie 按公开图床处理
        return {}


def _download(url: str, target: Path, cookies: dict | None = None) -> tuple[Path, int]:
    if not _allowed(url):
        raise RuntimeError("不是腾讯文档图床，已跳过")
    if target.is_file() and target.stat().st_size > 0:
        return target, target.stat().st_size
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.part")
    last = ""
    for attempt in range(2):
        try:
            with httpx.stream("GET", url, headers=HEADERS, cookies=cookies or None,
                              timeout=TIMEOUT, follow_redirects=True) as resp:
                resp.raise_for_status()
                ext = _ext_from_ct(resp.headers.get("content-type", ""))
                final = target.with_suffix(ext) if ext and target.suffix.lower() != ext else target
                done = 0
                with open(part, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=262144):
                        done += len(chunk)
                        if done > MAX_BYTES:
                            raise RuntimeError("图片超过 128MB")
                        fh.write(chunk)
                if done <= 0:
                    raise RuntimeError("图床返回空文件")
                os.replace(part, final)
                return final, done
        except Exception as exc:  # noqa: BLE001 - 重试一次，仍失败交给调用方记录
            last = str(exc)
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt == 0:
                time.sleep(0.8)
    raise RuntimeError(last or "下载失败")


def _update(conn, rec: dict, **fields) -> None:
    cols = ", ".join(f"{key}=?" for key in fields)
    conn.execute(
        f"UPDATE images SET {cols}, updated_at=? WHERE doc_key=? AND tab_id=? "
        "AND record_id=? AND field=? AND idx=?",
        (*fields.values(), _now(), rec["doc_key"], rec["tab_id"],
         rec["record_id"], rec["field"], rec["idx"]))


def download_doc(doc_key: str, doc_name: str, on_status=None, path=None,
                 limit: int = 0) -> dict:
    """下载该文档全部未落盘的图片，返回本次统计（limit 仅用于调试）。"""
    doc_key = str(doc_key)
    db = init_db(path)
    register_doc(doc_key, db)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM images WHERE doc_key=? "
            "ORDER BY tab_id, row_no, field, idx", (doc_key,))]
    total = len(rows)
    existing = 0
    pending: list[dict] = []
    with sqlite3.connect(db) as conn:
        for rec in rows:
            local = _absolute(rec["local_path"]) if rec.get("local_path") else None
            if local is not None and local.is_file() and local.stat().st_size > 0:
                existing += 1
                if rec.get("status") != "done":
                    _update(conn, rec, status="done", file_size=local.stat().st_size, error="")
                rec["_local"] = local
                continue
            if rec.get("local_path"):
                _update(conn, rec, status="pending", local_path="", file_size=0)
                rec["local_path"] = ""
            pending.append(rec)
        conn.commit()

    if limit and limit > 0:
        pending = pending[:limit]
    stats = {"doc_key": doc_key, "total": total, "existing": existing,
             "downloaded": 0, "failed": 0, "pending": len(pending),
             "errors": []}
    if not pending:
        _emit(on_status, f"图片已是最新（共 {total} 张）" if total else "该文档没有图片")
        return stats

    _emit(on_status, f"开始下载图片：待处理 {len(pending)} 张（共 {total} 张）")
    done = failed = 0
    errors: list[str] = []
    cookies = _login_cookies()
    with sqlite3.connect(db) as conn:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_download, rec["url"], _target_path(doc_name, rec),
                                   cookies): rec for rec in pending}
            for i, future in enumerate(as_completed(futures), start=1):
                rec = futures[future]
                try:
                    final, size = future.result()
                    done += 1
                    _update(conn, rec, local_path=_relative(final), file_size=size,
                            status="done", error="")
                except Exception as exc:  # noqa: BLE001 - 单张失败继续其余图片
                    failed += 1
                    message = str(exc)[:200]
                    _update(conn, rec, status="failed", error=message)
                    if len(errors) < 20:
                        errors.append(f"{rec.get('summary') or rec['record_id']}：{message}")
                if i % 10 == 0 or i == len(pending):
                    conn.commit()
                    _emit(on_status, f"下载表格图片 {i}/{len(pending)}（失败 {failed}）")
        conn.commit()

    stats.update(downloaded=done, failed=failed, pending=0, errors=errors)
    return stats


def clear_doc(doc_key: str, path=None) -> int:
    """删除该文档的图片登记（已下载文件保留，便于重新添加文档后继续用）。"""
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM images WHERE doc_key=?",
                         (str(doc_key),)).fetchone()[0]
        conn.execute("DELETE FROM images WHERE doc_key=?", (str(doc_key),))
        conn.commit()
    return int(n or 0)


def stats(doc_key: str, tab_id: str | None = None, path=None) -> dict:
    db = init_db(path)
    sql = ("SELECT COUNT(*), SUM(status='done'), SUM(status='failed') "
           "FROM images WHERE doc_key=?")
    args: list = [str(doc_key)]
    if tab_id:
        sql += " AND tab_id=?"
        args.append(str(tab_id))
    with sqlite3.connect(db) as conn:
        total, done, failed = conn.execute(sql, args).fetchone()
    total, done, failed = int(total or 0), int(done or 0), int(failed or 0)
    return {"total": total, "done": done, "failed": failed,
            "pending": max(0, total - done - failed)}
