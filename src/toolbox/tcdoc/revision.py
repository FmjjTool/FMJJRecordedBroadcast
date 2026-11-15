"""腾讯文档 · 修订记录（纯 HTTP 直连，全量翻页拉取）。

2026-11-10 实测（Chrome 抓包 + Python 直连双验证）：

- 文档骨架 `dop-api/opendoc` 取 padId（domain$padId 的 domain 部分即 domainId）；
- 修订列表 `GET https://docs.qq.com/dop-api/get/history?padId=<domain$padId>&page=N&xsrf=TOK`
  → `authors`（uid → 昵称 / 头像）+ `history.items`（每页固定 20 个版本，
  `history.pages` 是总页数，如「封茗囧菌数据汇总库」4430 版 = 222 页）。
- 版本 item：`v` 界面版本号（第 N 版）、`i` 修订号、`t` 毫秒时间戳、
  `a` 本版作者 uid、`l` 子记录（每次编辑的 `i` / `t` / `a`）。
- **没有一次拉全的参数**：`page_size` / `limit` 都会被忽略，全量 = 顺序翻完 pages 页。
- 需要登录 Cookie（TOK 作 xsrf）；修订记录的可见范围跟会员等级有关，
  接口返回的 `policy.view_days` 决定能看多少天。
- 智能表格同样走这套接口；v6 的 `/api/v6/file/record/revision/get_list`
  只对普通表格可用，这里统一用 dop-api 老接口。

落盘：SQLite `修订记录/revisions.db`（按「修订号 + 子修订号 + 编辑人」去重）
+ 每文档一份 JSON（完整结构，含作者头像）+ Excel（时间 / 版本 / 编辑人 / 修订号）。

增量策略：默认从第 1 页开始翻，遇到上次已拉取且内容一致（修订号 + 编辑人 +
时间）的记录即停止；同版本 / 同修订号但编辑人不同的记录全部保留。只有首次
拉取或显式「全量重拉」时才翻完所有页面。
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg_mod
from . import http_api
from . import tables as t

CST = timezone(timedelta(hours=8))
HISTORY_URL = "https://docs.qq.com/dop-api/get/history"
USER_INFO_URL = "https://docs.qq.com/cgi-bin/online_docs/user_info"
REV_TYPES = {0: "编辑", 1: "保存版本", 2: "还原"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revision_rows (
    doc_key TEXT, rev INTEGER, sub_rev INTEGER, version INTEGER,
    ts_ms INTEGER, author_uid TEXT, author_name TEXT, rev_type INTEGER,
    fetched_at TEXT,
    PRIMARY KEY (doc_key, rev, sub_rev, author_uid));
CREATE INDEX IF NOT EXISTS idx_rev_rows_ts ON revision_rows (doc_key, ts_ms DESC);
CREATE TABLE IF NOT EXISTS revision_meta (
    doc_key TEXT PRIMARY KEY, title TEXT, pad_id TEXT, pages INTEGER,
    versions INTEGER, total INTEGER, authors_json TEXT, login_user TEXT,
    last_ts_ms INTEGER, complete INTEGER, fetched_at TEXT);
"""


class RevisionError(RuntimeError):
    """修订记录拉取失败（登录态 / 权限 / 接口结构异常）。"""


def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _ts_text(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, CST).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(ms or "")


def _fetch_page(client, doc: dict, global_pad: str, page: int, xsrf: str) -> dict:
    url = (f"{HISTORY_URL}?u=&padId={global_pad}&page={page}"
           f"&_r={random.randint(1, 9999)}&xsrf={xsrf}&uid_only=0")
    try:
        data = http_api._get_json(client, url, doc)
    except http_api.HttpError as exc:
        raise RevisionError(f"第 {page} 页拉取失败：{exc}") from exc
    code = data.get("retcode")
    if code not in (0, None):
        raise RevisionError(f"第 {page} 页接口失败：{data.get('msg') or code}")
    return data


def _login_user(client, doc: dict, xsrf: str) -> dict:
    try:
        data = http_api._get_json(
            client, f"{USER_INFO_URL}?u=&xsrf={xsrf}&get_vip_info=1", doc)
        nick = str(data.get("nick") or data.get("qq_nick") or "").strip()
        return {"uid": str(data.get("uid") or ""), "name": nick}
    except Exception:  # noqa: BLE001 - 登录用户名只是附加信息
        return {}


def _item_parts(item: dict) -> dict | None:
    """版本 item → 统一结构；字段异常返回 None。"""
    if not isinstance(item, dict):
        return None
    try:
        version_rev = int(item.get("i") or 0)
        version_no = int(item.get("v") or 0)
    except (TypeError, ValueError):
        return None
    if not version_rev:
        return None
    version_authors = [str(a) for a in (item.get("a") or []) if a]
    try:
        version_time = int(item.get("t") or 0)
    except (TypeError, ValueError):
        version_time = 0
    sub_rows = []
    for sub in item.get("l") or []:
        if not isinstance(sub, dict):
            continue
        try:
            rev = int(sub.get("i") or version_rev)
            ts_ms = int(sub.get("t") or version_time)
        except (TypeError, ValueError):
            continue
        sub_rows.append({
            "rev": rev,
            "ts_ms": ts_ms,
            "author_uid": str(sub.get("a") or
                              (version_authors[0] if version_authors else "")),
        })
    if not sub_rows:
        sub_rows.append({
            "rev": version_rev,
            "ts_ms": version_time,
            "author_uid": version_authors[0] if version_authors else "",
        })
    return {
        "version": version_no,
        "rev": version_rev,
        "time_ms": version_time,
        "author_uids": version_authors,
        "rev_type": item.get("rev_type"),
        "restored": bool(item.get("r")),
        "rows": sub_rows,
    }


def _item_known(parts: dict, known: dict[tuple[int, str], int]) -> bool:
    """逐条校验内容：修订号 + 编辑人 + 时间都一致才算已拉取。

    同修订号但编辑人不同、或时间/内容变了，都视为新记录继续保留。
    """
    for sub in parts.get("rows") or []:
        key = (int(sub["rev"]), str(sub["author_uid"] or ""))
        if key not in known or int(known[key]) != int(sub["ts_ms"] or 0):
            return False
    return True


def _cut_index(items: list[dict], known: dict[tuple[int, str], int],
               last_ts: int) -> int | None:
    """本页从哪条开始全部是「已拉取且时间不晚于上次」的记录；没有返回 None。"""
    if not known or not items:
        return None
    for index, item in enumerate(items):
        parts = _item_parts(item)
        if not parts:
            continue
        if not _item_known(parts, known):
            continue
        rest = items[index:]
        rest_parts = [_item_parts(other) for other in rest]
        if any(part is None or not _item_known(part, known) for part in rest_parts):
            continue
        newest = max((part["time_ms"] for part in rest_parts if part), default=0)
        if newest <= last_ts:
            return index
    return None


def _load_known(doc_key: str) -> tuple[dict[tuple[int, str], int], int]:
    """本地已拉取的 (修订号, 编辑人) → 时间，以及上次最近修订时间。"""
    path = init_db()
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT rev, author_uid, ts_ms FROM revision_rows WHERE doc_key=?",
            (str(doc_key),)).fetchall()
        meta = conn.execute(
            "SELECT last_ts_ms FROM revision_meta WHERE doc_key=?",
            (str(doc_key),)).fetchone()
    known = {(int(row[0]), str(row[1] or "")): int(row[2] or 0) for row in rows}
    return known, int(meta[0] or 0) if meta else 0


def collect(doc: dict, on_status=None, max_pages: int | None = None,
            incremental: bool = True) -> dict:
    """拉取该文档修订记录。

    incremental=True（默认）：先从本地库读上次拉取位置，从第 1 页开始翻，
      遇到「修订号 + 编辑人 + 时间」都一致的记录就停止继续翻页；同版本
      不同编辑人、同修订号不同编辑人都会保留。首次拉取或强制全量时翻完
      history.pages 页。
    max_pages 仅用于调试/冒烟；正式拉取传 None。
    """
    jar = http_api._cookie_jar()
    xsrf = http_api._xsrf(jar)
    if not xsrf:
        raise RevisionError("缺少腾讯文档登录态（TOK）：请先到「Cookie 管理器」登录")

    name = doc.get("name") or doc.get("key")
    key = str(doc.get("key") or t.doc_key_of(doc.get("url")) or "")
    known, last_ts = _load_known(key) if incremental else ({}, 0)
    if incremental:
        _emit(on_status, f"增量拉取「{name}」：本地已有 {len(known)} 条，"
                         f"翻到上次拉取位置即停")
    else:
        _emit(on_status, f"全量拉取「{name}」：将翻完全部页面")

    _emit(on_status, f"读取文档版本：{name}")
    with http_api._client(jar) as client:
        sk = http_api.doc_skeleton(client, doc)
        pad_id = sk.get("pad_id") or ""
        if not pad_id:
            raise RevisionError("文档骨架里没有 padId（可能无权限 / 文档已删除）")
        global_pad = f"{sk.get('domain_id') or '300000000'}${pad_id}"
        login_user = _login_user(client, doc, xsrf)

        first = _fetch_page(client, doc, global_pad, 1, xsrf)
        history = first.get("history") or {}
        pages = int(history.get("pages") or 0)
        page_items = list(history.get("items") or [])
        authors: dict[str, dict] = dict(first.get("authors") or {})
        items: list[dict] = []
        complete = True
        stopped_at = 0

        if not pages and not page_items:
            _emit(on_status, f"「{name}」没有修订记录（或当前账号无权查看）")
        for page in range(1, pages + 1):
            if max_pages and page > max_pages:
                complete = False
                break
            if page > 1:
                if page % 5 == 0 or page == pages:
                    _emit(on_status, f"拉取修订记录 {page}/{pages} 页…")
                last_err: Exception | None = None
                page_items = []
                for attempt in range(3):
                    try:
                        data = _fetch_page(client, doc, global_pad, page, xsrf)
                        page_items = list(data.get("history", {}).get("items") or [])
                        authors.update(data.get("authors") or {})
                        last_err = None
                        break
                    except RevisionError as exc:
                        last_err = exc
                        time.sleep(0.8 * (attempt + 1))
                if last_err is not None:
                    raise last_err
                time.sleep(0.05)   # 222 页连发时稍微让一下，避免触发限流

            cut = _cut_index(page_items, known, last_ts)
            if cut is not None:
                items.extend(page_items[:cut])
                stopped_at = page
                _emit(on_status, f"已到上次拉取位置（第 {page} 页），停止翻页")
                break
            items.extend(page_items)

    versions: list[dict] = []
    rows: list[dict] = []
    seen_rows: set[tuple[int, str]] = set()
    seen_versions: set[tuple[int, tuple[str, ...]]] = set()
    for item in items:
        parts = _item_parts(item)
        if not parts:
            continue
        version_key = (parts["rev"], tuple(parts["author_uids"]))
        if version_key in seen_versions:
            continue
        seen_versions.add(version_key)
        versions.append(parts)
        for sub in parts["rows"]:
            row_key = (int(sub["rev"]), str(sub["author_uid"] or ""))
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            rows.append({
                "version": parts["version"],
                "rev": sub["rev"],
                "sub_rev": sub["rev"],
                "ts_ms": sub["ts_ms"],
                "author_uid": sub["author_uid"],
                "rev_type": parts["rev_type"],
            })

    for row in rows:
        author = authors.get(row["author_uid"]) or {}
        row["author_name"] = str(author.get("name") or row["author_uid"] or "")
        row["author_pic"] = str(author.get("userPic") or "")
        row["ts"] = _ts_text(row["ts_ms"])
        row["version_text"] = f"第{row['version']}版" if row["version"] else ""
        row["rev_type_text"] = REV_TYPES.get(row["rev_type"], "编辑")
    for version in versions:
        version["author_names"] = [
            str((authors.get(uid) or {}).get("name") or uid)
            for uid in version["author_uids"]]
        version["time"] = _ts_text(version["time_ms"])

    rows.sort(key=lambda r: r["ts_ms"], reverse=True)
    versions.sort(key=lambda v: v["version"], reverse=True)
    new_last_ts = max((r["ts_ms"] for r in rows), default=0)
    return {
        "doc_key": key, "name": name, "title": sk.get("title") or name,
        "pad_id": pad_id, "pages": pages, "versions": versions, "rows": rows,
        "authors": authors, "login_user": login_user, "complete": complete,
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "last_ts_ms": max(last_ts, new_last_ts),
        "incremental": bool(known),
        "stopped_at_page": stopped_at,
        "known_rows": len(known),
    }


# ==================== 存储 ====================

def db_path() -> Path:
    return cfg_mod.revisions_db_path()


def init_db(path=None) -> Path:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        # 旧库主键没有 author_uid：同修订号不同编辑人会被覆盖，必须重建表迁移。
        info = conn.execute("PRAGMA table_info(revision_rows)").fetchall()
        pk_columns = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        if "author_uid" not in pk_columns:
            conn.execute("ALTER TABLE revision_rows RENAME TO revision_rows_old")
            conn.execute(
                "CREATE TABLE revision_rows ("
                " doc_key TEXT, rev INTEGER, sub_rev INTEGER, version INTEGER,"
                " ts_ms INTEGER, author_uid TEXT, author_name TEXT, rev_type INTEGER,"
                " fetched_at TEXT,"
                " PRIMARY KEY (doc_key, rev, sub_rev, author_uid))")
            conn.execute(
                "INSERT OR IGNORE INTO revision_rows (doc_key, rev, sub_rev, version,"
                " ts_ms, author_uid, author_name, rev_type, fetched_at)"
                " SELECT doc_key, rev, sub_rev, version, ts_ms, author_uid,"
                " author_name, rev_type, fetched_at FROM revision_rows_old")
            conn.execute("DROP TABLE revision_rows_old")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rev_rows_ts "
                "ON revision_rows (doc_key, ts_ms DESC)")
            conn.commit()
    return path


def _write_json_snapshot(doc_key: str, result: dict, db: Path, target: Path) -> None:
    """从完整库导出 JSON（增量拉取时也保持全量快照，不会被本次部分数据覆盖）。"""
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(
            "SELECT version, rev, ts_ms, author_uid, author_name, rev_type "
            "FROM revision_rows WHERE doc_key=? ORDER BY ts_ms DESC, rev DESC",
            (doc_key,))]
        meta_row = conn.execute(
            "SELECT title, pad_id, pages, versions, total, authors_json, login_user,"
            " last_ts_ms, complete, fetched_at FROM revision_meta WHERE doc_key=?",
            (doc_key,)).fetchone()
    for row in rows:
        row["ts"] = _ts_text(row.get("ts_ms"))
        row["version_text"] = f"第{row['version']}版" if row.get("version") else ""
        row["rev_type_text"] = REV_TYPES.get(row.get("rev_type"), "编辑")
    groups: dict[int, dict] = {}
    for row in rows:
        version_no = int(row.get("version") or 0)
        group = groups.setdefault(version_no, {
            "version": version_no, "rev": 0, "time_ms": 0,
            "author_uids": [], "author_names": [], "rev_type": row.get("rev_type"),
            "restored": False, "rows": [],
        })
        group["rev"] = max(group["rev"], int(row.get("rev") or 0))
        group["time_ms"] = max(group["time_ms"], int(row.get("ts_ms") or 0))
        uid = str(row.get("author_uid") or "")
        if uid not in group["author_uids"]:
            group["author_uids"].append(uid)
        name = str(row.get("author_name") or uid)
        if name not in group["author_names"]:
            group["author_names"].append(name)
        group["rows"].append({"rev": row.get("rev"), "ts_ms": row.get("ts_ms"),
                              "author_uid": uid})
    versions = sorted(groups.values(), key=lambda item: item["version"], reverse=True)
    for version in versions:
        version["time"] = _ts_text(version["time_ms"])
    try:
        authors = json.loads(meta_row["authors_json"] or "{}") if meta_row else {}
    except (TypeError, json.JSONDecodeError):
        authors = {}
    data = {
        "doc_key": doc_key,
        "name": result.get("name"),
        "title": (meta_row["title"] if meta_row else "") or result.get("title"),
        "pad_id": (meta_row["pad_id"] if meta_row else "") or result.get("pad_id"),
        "pages": (meta_row["pages"] if meta_row else 0) or result.get("pages") or 0,
        "versions": versions,
        "rows": rows,
        "authors": authors,
        "login_user": {"name": meta_row["login_user"]} if meta_row and meta_row["login_user"] else {},
        "complete": bool(meta_row["complete"]) if meta_row else True,
        "fetched_at": (meta_row["fetched_at"] if meta_row else "")
        or result.get("fetched_at"),
        "last_ts_ms": int(meta_row["last_ts_ms"] or 0) if meta_row else 0,
        "incremental": bool(result.get("incremental")),
        "stopped_at_page": int(result.get("stopped_at_page") or 0),
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def save(result: dict, path=None) -> dict:
    """增量写入 SQLite，并导出 JSON / Excel。

    去重键 = (修订号, 子修订号, 编辑人)：同版本/同修订号但编辑人不同都会保留；
    已存在且内容一致的行不重复插入，内容有变化则更新时间和昵称。
    """
    path = init_db(path)
    doc_key = str(result["doc_key"])
    now = result.get("fetched_at") or datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    added = updated = 0
    with sqlite3.connect(path) as conn:
        for row in result.get("rows") or []:
            cur = conn.execute(
                "INSERT OR IGNORE INTO revision_rows (doc_key, rev, sub_rev, version, "
                "ts_ms, author_uid, author_name, rev_type, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_key, int(row["rev"]), int(row["sub_rev"]), int(row["version"]),
                 int(row["ts_ms"]), row["author_uid"], row["author_name"],
                 row.get("rev_type"), now))
            if cur.rowcount:
                added += 1
                continue
            previous_row = conn.execute(
                "SELECT version, ts_ms, author_name, rev_type FROM revision_rows"
                " WHERE doc_key=? AND rev=? AND sub_rev=? AND author_uid=?",
                (doc_key, int(row["rev"]), int(row["sub_rev"]),
                 row["author_uid"])).fetchone()
            new_values = (int(row["version"]), int(row["ts_ms"]),
                          row["author_name"], row.get("rev_type"))
            if previous_row and tuple(previous_row) == new_values:
                continue
            conn.execute(
                "UPDATE revision_rows SET version=?, ts_ms=?, author_name=?,"
                " rev_type=?, fetched_at=? WHERE doc_key=? AND rev=? AND sub_rev=?"
                " AND author_uid=?",
                (*new_values, now, doc_key, int(row["rev"]),
                 int(row["sub_rev"]), row["author_uid"]))
            updated += 1
        previous = conn.execute(
            "SELECT authors_json FROM revision_meta WHERE doc_key=?",
            (doc_key,)).fetchone()
        try:
            authors = json.loads(previous[0] or "{}") if previous else {}
        except (TypeError, json.JSONDecodeError):
            authors = {}
        authors.update(result.get("authors") or {})
        total = int(conn.execute(
            "SELECT COUNT(*) FROM revision_rows WHERE doc_key=?",
            (doc_key,)).fetchone()[0])
        versions_total = int(conn.execute(
            "SELECT COUNT(DISTINCT version) FROM revision_rows WHERE doc_key=?",
            (doc_key,)).fetchone()[0])
        last_ts = int(conn.execute(
            "SELECT MAX(ts_ms) FROM revision_rows WHERE doc_key=?",
            (doc_key,)).fetchone()[0] or 0)
        conn.execute(
            "INSERT OR REPLACE INTO revision_meta (doc_key, title, pad_id, pages, "
            "versions, total, authors_json, login_user, last_ts_ms, complete, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc_key, result.get("title") or result.get("name"), result.get("pad_id"),
             int(result.get("pages") or 0), versions_total, total,
             json.dumps(authors, ensure_ascii=False),
             (result.get("login_user") or {}).get("name") or "",
             last_ts, 1 if result.get("complete") else 0, now))
        conn.commit()

    out = {"doc_key": doc_key, "added": added, "updated": updated, "db": path,
           "versions": versions_total, "total": total, "json": None, "excel": None,
           "excel_error": "", "incremental": bool(result.get("incremental")),
           "stopped_at_page": result.get("stopped_at_page") or 0}
    if result.get("incremental") and added == 0 and updated == 0:
        # 增量拉取且内容没变：数据库已是最新，不再重写几万行 JSON / Excel。
        out["skipped_export"] = True
        return out
    stem = cfg_mod.doc_stem({"key": doc_key, "name": result.get("name")})
    try:
        target = cfg_mod.revisions_dir() / f"{stem}.json"
        _write_json_snapshot(doc_key, result, path, target)
        out["json"] = target
    except OSError as exc:
        out["json_error"] = str(exc)
    try:
        out["excel"] = export_excel(doc_key, result.get("name") or doc_key)
    except Exception as exc:  # noqa: BLE001 - Excel 被占用不影响入库与 JSON
        out["excel_error"] = str(exc)
    return out


def load_rows(doc_key: str, limit: int = 2000, offset: int = 0,
              path=None) -> tuple[list[dict], int]:
    """本地修订记录（时间倒序），返回 (rows, total)。"""
    path = init_db(path)
    with sqlite3.connect(path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM revision_rows WHERE doc_key=?", (str(doc_key),)
        ).fetchone()[0]
        data = conn.execute(
            "SELECT version, rev, ts_ms, author_name, rev_type "
            "FROM revision_rows WHERE doc_key=?"
            " ORDER BY ts_ms DESC, rev DESC LIMIT ? OFFSET ?",
            (str(doc_key), int(limit), max(0, int(offset)))).fetchall()
    rows = [{"version": r[0], "rev": r[1], "ts": _ts_text(r[2]),
             "author": r[3] or "", "type": REV_TYPES.get(r[4], "编辑")} for r in data]
    return rows, total


def meta(doc_key: str, path=None) -> dict:
    path = init_db(path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT title, pages, versions, total, authors_json, login_user, "
            "last_ts_ms, complete, fetched_at FROM revision_meta WHERE doc_key=?",
            (str(doc_key),)).fetchone()
    if not row:
        return {}
    try:
        authors = list((json.loads(row[4] or "{}")).values())
    except json.JSONDecodeError:
        authors = []
    names = [str(a.get("name") or "") for a in authors if isinstance(a, dict)]
    return {"title": row[0], "pages": row[1], "versions": row[2], "total": row[3],
            "authors": names, "login_user": row[5], "last_ts_ms": row[6],
            "last_ts": _ts_text(row[6]) if row[6] else "",
            "complete": bool(row[7]), "fetched_at": row[8]}


# ==================== 导出 ====================

def export_excel(doc_key: str, doc_name: str, path=None, target=None) -> Path:
    """把本地修订记录导出成 Excel（时间倒序，含编辑人昵称）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    rows, _total = load_rows(doc_key, limit=1_000_000, path=path)
    meta_info = meta(doc_key, path=path)
    target = Path(target or (cfg_mod.revisions_dir()
                             / f"{cfg_mod.doc_stem({'key': doc_key, 'name': doc_name})}.xlsx"))
    target.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "修订记录"
    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True)
    headers = ["时间", "版本", "编辑人", "修订号", "类型"]
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append([row["ts"], f"第{row['version']}版" if row["version"] else "",
                   row["author"], row["rev"], row["type"]])
    widths = (21, 10, 16, 10, 10)
    for idx, width in enumerate(widths):
        ws.column_dimensions[chr(65 + idx)].width = width
    ws.freeze_panes = "A2"

    if meta_info:
        info = wb.create_sheet("说明")
        info.append(["文档", meta_info.get("title") or doc_name])
        info.append(["当前登录账号", meta_info.get("login_user") or ""])
        info.append(["编辑人", "、".join(meta_info.get("authors") or [])])
        info.append(["版本数", meta_info.get("versions")])
        info.append(["编辑记录数", meta_info.get("total")])
        info.append(["最近修订", meta_info.get("last_ts")])
        info.append(["拉取时间", meta_info.get("fetched_at")])
        info.append(["是否全量", "是" if meta_info.get("complete") else "否（部分页）"])
        info.column_dimensions["A"].width = 16
        info.column_dimensions["B"].width = 40
    wb.save(target)
    return target
