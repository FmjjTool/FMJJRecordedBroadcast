"""腾讯文档 · 文档成员名单（纯 HTTP，全量游标翻页）。

2026-11-10 实测（Chrome 抓包 + Python 直连双验证）：

- `POST https://docs.qq.com/api/collaboratorsearch/list?xsrf=TOK`
  body `{docId: <local padId>, size: 100, keyword: "", pageCookie?: "<游标>"}`
  → `result.total` / `result.page_cookie` / `result.collaborators[]`。
- `page_cookie` 是字符串游标（形如 `{"page":1}`），原样回传直到为空；
  每页最多 100 条，1707 条约 18 页。
- 成员字段：`id`(uid)、`name`(昵称)、`avatar`、`role_id`、`is_creator`、
  `uid_source`、`corp_name`、`is_org`、`type`、`permission_ids`。
- role_id 权限等级映射（来自前端 `docs-component-permission-roles`）：
  1=可查看、2=可编辑（收集表为可填写）、3=可填写、4=可使用、
  5=可上传下载、6=仅下载、7=可标注、10=外部权限、12=可填写、13=可查看、
  20=可继承、33=特殊权限、1024=可管理、2048=所有者。
- 接口会返回同一 uid 的重复记录（实测 1707 条 → 1624 个唯一用户），
  落库按 uid 去重；`total` 是记录数，跟界面「文档成员 · 1707」一致。
- 需要文档所有者 / 可编辑权限；无权限的文档返回 `total=0`。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg_mod
from . import http_api
from . import tables as t
from .revision import RevisionError, _login_user

CST = timezone(timedelta(hours=8))
LIST_URL = "https://docs.qq.com/api/collaboratorsearch/list"

ROLE_LABELS = {
    0: "无权限", 1: "可查看", 2: "可编辑", 3: "可填写", 4: "可使用",
    5: "可上传下载", 6: "仅下载", 7: "可标注", 10: "外部权限", 12: "可填写",
    13: "可查看", 20: "可继承", 33: "特殊权限", 1024: "可管理", 2048: "所有者",
}
SOURCE_LABELS = {1: "QQ", 2: "QQ", 3: "微信", 4: "企业微信", 5: "邮箱"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    doc_key TEXT, uid TEXT, name TEXT, role_id INTEGER, role TEXT,
    avatar TEXT, uid_source INTEGER, source TEXT, corp_name TEXT, is_org INTEGER,
    is_creator INTEGER, type INTEGER, raw_json TEXT,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
    PRIMARY KEY (doc_key, uid));
CREATE INDEX IF NOT EXISTS idx_members_active ON members (doc_key, active, role_id DESC);
CREATE TABLE IF NOT EXISTS member_meta (
    doc_key TEXT PRIMARY KEY, title TEXT, pad_id TEXT, total_records INTEGER,
    unique_users INTEGER, roles_json TEXT, login_user TEXT,
    fetched_at TEXT, complete INTEGER);
"""


def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _role_label(role_id: int, kind: str = "") -> str:
    if role_id == 2 and (kind or "").lower() == "form":
        return "可填写"
    return ROLE_LABELS.get(role_id, f"角色{role_id}")


def collect(doc: dict, on_status=None) -> dict:
    """拉取文档全部成员（自动翻完游标），按 uid 去重。"""
    jar = http_api._cookie_jar()
    xsrf = http_api._xsrf(jar)
    if not xsrf:
        raise RevisionError("缺少腾讯文档登录态（TOK）：请先到「Cookie 管理器」登录")
    name = doc.get("name") or doc.get("key")
    key = str(doc.get("key") or t.doc_key_of(doc.get("url")) or "")
    kind = str(doc.get("kind") or "")

    _emit(on_status, f"读取文档版本：{name}")
    with http_api._client(jar) as client:
        sk = http_api.doc_skeleton(client, doc)
        pad_id = sk.get("pad_id") or ""
        if not pad_id:
            raise RevisionError("文档骨架里没有 padId（可能无权限 / 文档已删除）")
        login_user = _login_user(client, doc, xsrf)

        records: list[dict] = []
        seen: dict[str, dict] = {}
        total = 0
        cookie = ""
        pages = 0
        complete = False
        while pages < 100:
            body = {"docId": pad_id, "size": 100, "keyword": "", "xsrf": xsrf}
            if cookie:
                body["pageCookie"] = cookie
            try:
                data = http_api._post_json(
                    client, f"{LIST_URL}?xsrf={xsrf}", doc, body)
            except http_api.HttpError as exc:
                raise RevisionError(f"成员名单接口失败：{exc}") from exc
            if data.get("retcode") not in (0, None):
                raise RevisionError(
                    f"成员名单接口失败：{data.get('msg') or data.get('retcode')}")
            result = data.get("result") or {}
            total = int(result.get("total") or 0)
            cols = result.get("collaborators") or []
            pages += 1
            records.extend(cols)
            for raw in cols:
                uid = str(raw.get("id") or "")
                if not uid:
                    continue
                role_id = int(raw.get("role_id") or 0)
                prev = seen.get(uid)
                if prev is None or role_id > prev["role_id"]:
                    seen[uid] = {
                        "uid": uid,
                        "name": str(raw.get("name") or ""),
                        "role_id": role_id,
                        "role": _role_label(role_id, kind),
                        "avatar": str(raw.get("avatar") or ""),
                        "uid_source": int(raw.get("uid_source") or 0),
                        "source": SOURCE_LABELS.get(
                            int(raw.get("uid_source") or 0),
                            f"来源{raw.get('uid_source')}"),
                        "corp_name": str(raw.get("corp_name") or ""),
                        "is_org": bool(raw.get("is_org")),
                        "is_creator": bool(raw.get("is_creator")),
                        "type": int(raw.get("type") or 0),
                        "permission_ids": raw.get("permission_ids") or [],
                    }
            cookie = str(result.get("page_cookie") or "")
            if not cols or not cookie:
                complete = True
                break
            if total and len(records) >= total:
                complete = True
                break
            if pages % 5 == 0:
                _emit(on_status, f"拉取成员名单 {len(records)}/{total or '?'} 条…")
            time.sleep(0.05)

    users = sorted(seen.values(), key=lambda u: (-u["role_id"], u["name"]))
    role_counts: dict[str, int] = {}
    for user in users:
        role_counts[user["role"]] = role_counts.get(user["role"], 0) + 1
    if not total:
        _emit(on_status, f"「{name}」没有返回成员名单（可能无管理权限）")
    return {
        "doc_key": key, "name": name, "title": sk.get("title") or name,
        "pad_id": pad_id, "total_records": total,
        "unique_users": len(users), "users": users,
        "role_counts": role_counts, "login_user": login_user,
        "pages": pages, "complete": complete,
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==================== 存储 ====================

def db_path() -> Path:
    return cfg_mod.members_db_path()


def init_db(path=None) -> Path:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
        if "source" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN source TEXT DEFAULT ''")
        conn.commit()
    return path


def save(result: dict, path=None) -> dict:
    """当前名单快照入库（按 uid 去重，保留历史 first_seen）并导出 JSON/Excel。"""
    path = init_db(path)
    doc_key = str(result["doc_key"])
    now = result.get("fetched_at") or datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE members SET active=0 WHERE doc_key=?", (doc_key,))
        existing = {row[0] for row in conn.execute(
            "SELECT uid FROM members WHERE doc_key=?", (doc_key,))}
        for user in result.get("users") or []:
            if user["uid"] not in existing:
                added += 1
            conn.execute(
                "INSERT INTO members (doc_key, uid, name, role_id, role, avatar, "
                "uid_source, source, corp_name, is_org, is_creator, type, raw_json, "
                "first_seen, last_seen, active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
                "ON CONFLICT(doc_key, uid) DO UPDATE SET "
                "name=excluded.name, role_id=excluded.role_id, role=excluded.role, "
                "avatar=excluded.avatar, uid_source=excluded.uid_source, "
                "source=excluded.source, corp_name=excluded.corp_name, "
                "is_org=excluded.is_org, is_creator=excluded.is_creator, "
                "type=excluded.type, raw_json=excluded.raw_json, "
                "last_seen=excluded.last_seen, active=1",
                (doc_key, user["uid"], user["name"], user["role_id"], user["role"],
                 user["avatar"], user["uid_source"], user["source"], user["corp_name"],
                 int(user["is_org"]), int(user["is_creator"]), user["type"],
                 json.dumps(user, ensure_ascii=False), now, now))
        conn.execute(
            "INSERT OR REPLACE INTO member_meta (doc_key, title, pad_id, total_records, "
            "unique_users, roles_json, login_user, fetched_at, complete) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_key, result.get("title") or result.get("name"), result.get("pad_id"),
             int(result.get("total_records") or 0), len(result.get("users") or []),
             json.dumps(result.get("role_counts") or {}, ensure_ascii=False),
             (result.get("login_user") or {}).get("name") or "",
             now, 1 if result.get("complete") else 0))
        conn.commit()

    out = {"doc_key": doc_key, "added": added,
           "records": int(result.get("total_records") or 0),
           "unique": len(result.get("users") or []),
           "roles": result.get("role_counts") or {}, "json": None, "excel": None,
           "excel_error": ""}
    stem = cfg_mod.doc_stem({"key": doc_key, "name": result.get("name")})
    try:
        target = cfg_mod.members_dir() / f"{stem}.json"
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


def load_users(doc_key: str, limit: int = 5000, path=None) -> tuple[list[dict], int]:
    """当前名单快照（权限高→低、昵称排序），返回 (users, total)。"""
    path = init_db(path)
    with sqlite3.connect(path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM members WHERE doc_key=? AND active=1",
            (str(doc_key),)).fetchone()[0]
        rows = conn.execute(
            "SELECT uid, name, role, role_id, source, corp_name, avatar, "
            "first_seen, last_seen FROM members WHERE doc_key=? AND active=1 "
            "ORDER BY role_id DESC, name LIMIT ?",
            (str(doc_key), int(limit))).fetchall()
    users = [{"uid": r[0], "name": r[1], "role": r[2], "role_id": r[3],
              "source": r[4], "corp_name": r[5], "avatar": r[6],
              "first_seen": r[7], "last_seen": r[8]} for r in rows]
    return users, total


def meta(doc_key: str, path=None) -> dict:
    path = init_db(path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT title, total_records, unique_users, roles_json, login_user, "
            "fetched_at, complete FROM member_meta WHERE doc_key=?",
            (str(doc_key),)).fetchone()
    if not row:
        return {}
    try:
        roles = json.loads(row[3] or "{}")
    except json.JSONDecodeError:
        roles = {}
    return {"title": row[0], "records": row[1], "unique": row[2], "roles": roles,
            "login_user": row[4], "fetched_at": row[5], "complete": bool(row[6])}


# ==================== 导出 ====================

def export_excel(doc_key: str, doc_name: str, path=None, target=None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    users, total = load_users(doc_key, limit=1_000_000, path=path)
    meta_info = meta(doc_key, path=path)
    target = Path(target or (cfg_mod.members_dir()
                             / f"{cfg_mod.doc_stem({'key': doc_key, 'name': doc_name})}.xlsx"))
    target.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "成员名单"
    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True)
    ws.append(["昵称", "权限等级", "账号来源", "UID", "企业", "首次出现", "最近出现"])
    for cell in ws[1]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical="center")
    for user in users:
        ws.append([user["name"], user["role"], user["source"], user["uid"],
                   user["corp_name"], user["first_seen"], user["last_seen"]])
    widths = (24, 14, 12, 24, 20, 21, 21)
    for idx, width in enumerate(widths):
        ws.column_dimensions[chr(65 + idx)].width = width
    ws.freeze_panes = "A2"

    if meta_info:
        info = wb.create_sheet("说明")
        info.append(["文档", meta_info.get("title") or doc_name])
        info.append(["当前登录账号", meta_info.get("login_user") or ""])
        info.append(["接口记录数", meta_info.get("records")])
        info.append(["去重后人数", meta_info.get("unique")])
        info.append(["权限分布", json.dumps(meta_info.get("roles") or {},
                                            ensure_ascii=False)])
        info.append(["拉取时间", meta_info.get("fetched_at")])
        info.append(["是否全量", "是" if meta_info.get("complete") else "否（部分页）"])
        info.column_dimensions["A"].width = 16
        info.column_dimensions["B"].width = 48
    wb.save(target)
    return target
