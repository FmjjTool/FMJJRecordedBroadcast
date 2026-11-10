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

落盘：SQLite `修订记录/revisions.db`（按 rev 增量去重）+ 每文档一份
JSON（完整结构，含作者头像）+ Excel（时间 / 版本 / 编辑人 / 修订号）。
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
    PRIMARY KEY (doc_key, rev, sub_rev));
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


def collect(doc: dict, on_status=None, max_pages: int | None = None) -> dict:
    """拉取该文档全部修订记录（自动翻完 history.pages 页）。

    max_pages 仅用于调试/冒烟；正式拉取传 None。返回结构：
      {doc_key, name, title, pad_id, pages, versions[], rows[], authors,
       login_user, complete, fetched_at}
    """
    jar = http_api._cookie_jar()
    xsrf = http_api._xsrf(jar)
    if not xsrf:
        raise RevisionError("缺少腾讯文档登录态（TOK）：请先到「Cookie 管理器」登录")

    name = doc.get("name") or doc.get("key")
    key = str(doc.get("key") or t.doc_key_of(doc.get("url")) or "")
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
        items = list(history.get("items") or [])
        authors: dict[str, dict] = dict(first.get("authors") or {})
        seen: set[int] = set()
        complete = True

        if not pages and not items:
            _emit(on_status, f"「{name}」没有修订记录（或当前账号无权查看）")
        for page in range(2, pages + 1):
            if max_pages and page > max_pages:
                complete = False
                break
            if page % 5 == 0 or page == pages:
                _emit(on_status, f"拉取修订记录 {page}/{pages} 页…")
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    data = _fetch_page(client, doc, global_pad, page, xsrf)
                    items.extend(data.get("history", {}).get("items") or [])
                    authors.update(data.get("authors") or {})
                    last_err = None
                    break
                except RevisionError as exc:
                    last_err = exc
                    time.sleep(0.8 * (attempt + 1))
            if last_err is not None:
                raise last_err
            time.sleep(0.05)   # 222 页连发时稍微让一下，避免触发限流

    versions: list[dict] = []
    rows: list[dict] = []
    for item in items:
        try:
            rev = int(item.get("i") or 0)
            version_no = int(item.get("v") or 0)
        except (TypeError, ValueError):
            continue
        if not rev or rev in seen:
            continue
        seen.add(rev)
        version_authors = [str(a) for a in (item.get("a") or []) if a]
        version_time = item.get("t")
        sub_items = item.get("l") or []
        sub_rows = []
        for sub in sub_items:
            if not isinstance(sub, dict):
                continue
            uid = str(sub.get("a") or (version_authors[0] if version_authors else ""))
            sub_rows.append({
                "rev": int(sub.get("i") or rev),
                "ts_ms": int(sub.get("t") or version_time or 0),
                "author_uid": uid,
            })
        if not sub_rows:
            uid = version_authors[0] if version_authors else ""
            sub_rows.append({"rev": rev, "ts_ms": int(version_time or 0),
                             "author_uid": uid})
        versions.append({
            "version": version_no,
            "rev": rev,
            "time_ms": int(version_time or 0),
            "author_uids": version_authors,
            "rev_type": item.get("rev_type"),
            "restored": bool(item.get("r")),
            "rows": sub_rows,
        })
        for sub in sub_rows:
            rows.append({
                "version": version_no,
                "rev": sub["rev"],
                "sub_rev": sub["rev"],
                "ts_ms": sub["ts_ms"],
                "author_uid": sub["author_uid"],
                "rev_type": item.get("rev_type"),
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
            str((authors.get(uid) or {}).get("name") or uid) for uid in version["author_uids"]]
        version["time"] = _ts_text(version["time_ms"])

    rows.sort(key=lambda r: r["ts_ms"], reverse=True)
    versions.sort(key=lambda v: v["version"], reverse=True)
    last_ts = max((r["ts_ms"] for r in rows), default=0)
    return {
        "doc_key": key, "name": name, "title": sk.get("title") or name,
        "pad_id": pad_id, "pages": pages, "versions": versions, "rows": rows,
        "authors": authors, "login_user": login_user, "complete": complete,
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "last_ts_ms": last_ts,
    }


# ==================== 存储 ====================

def db_path() -> Path:
    return cfg_mod.revisions_db_path()


def init_db(path=None) -> Path:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
    return path


def save(result: dict, path=None) -> dict:
    """增量写入 SQLite（按 rev 去重），并导出 JSON / Excel。"""
    path = init_db(path)
    doc_key = str(result["doc_key"])
    now = result.get("fetched_at") or datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    with sqlite3.connect(path) as conn:
        for row in result.get("rows") or []:
            cur = conn.execute(
                "INSERT OR IGNORE INTO revision_rows (doc_key, rev, sub_rev, version, "
                "ts_ms, author_uid, author_name, rev_type, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_key, int(row["rev"]), int(row["sub_rev"]), int(row["version"]),
                 int(row["ts_ms"]), row["author_uid"], row["author_name"],
                 row.get("rev_type"), now))
            added += max(cur.rowcount, 0)
        conn.execute(
            "INSERT OR REPLACE INTO revision_meta (doc_key, title, pad_id, pages, "
            "versions, total, authors_json, login_user, last_ts_ms, complete, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc_key, result.get("title") or result.get("name"), result.get("pad_id"),
             int(result.get("pages") or 0), len(result.get("versions") or []),
             len(result.get("rows") or []),
             json.dumps(result.get("authors") or {}, ensure_ascii=False),
             (result.get("login_user") or {}).get("name") or "",
             int(result.get("last_ts_ms") or 0), 1 if result.get("complete") else 0, now))
        conn.commit()

    out = {"doc_key": doc_key, "added": added, "db": path,
           "versions": len(result.get("versions") or []),
           "total": len(result.get("rows") or []), "json": None, "excel": None,
           "excel_error": ""}
    stem = cfg_mod.doc_stem({"key": doc_key, "name": result.get("name")})
    try:
        target = cfg_mod.revisions_dir() / f"{stem}.json"
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                          encoding="utf-8")
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
            "FROM revision_rows WHERE doc_key=? ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
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
