"""工作台本地数据目录（L3，只读 DTO）。

本模块不保留数据副本、不迁移 schema、不初始化 owner、不触发网络或录制。
collected_store 是平台主库 owner，increment_db 是派生归档；二者分别展示，
不相加。其余库仍由各业务模块独占写入。目录统计只遍历已知业务目录的
固定层级，拒绝链接目录，并限制条目数；出错/超过上限返回未知而非部分值。

返回的 schema_version=1 仅表示 DTO 版本，不创建第二份配置、索引或缓存。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from toolbox.config_manager import (
    EXPORT_CATEGORIES, get_app_root, get_config_dir_readonly,
)

CST = timezone(timedelta(hours=8))
_MAX_JSON = 32 * 1024 * 1024
_MAX_ENTRIES = 20000
_MAX_DB_SNAPSHOT = 512 * 1024 * 1024
_READ_SESSION = ContextVar("dashboard_read_session", default=None)


class CatalogError(ValueError):
    """目录未能完整读取；不向界面暴露原始异常、路径或配置内容。"""


def read_json(path, expected=None, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        if not path.is_file() or path.stat().st_size > _MAX_JSON:
            raise CatalogError("数据文件过大或不可读取")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if expected is not None and not isinstance(value, expected):
            raise CatalogError("数据格式不支持")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise CatalogError("本地数据暂时无法读取") from exc


def readonly_roots():
    """复用配置 owner 的只读路径解析，不调用 mkdir/写探针/迁移。"""
    root = get_app_root()
    config = get_config_dir_readonly()
    values = read_json(config / "config.json", dict, {})
    export = Path(values.get("export_dir") or root / "download")
    convert = values.get("convert", {})
    if not isinstance(convert, dict):
        raise CatalogError("本地设置格式不支持")
    return root, config, export, convert


def export_subdir(category):
    return readonly_roots()[2] / EXPORT_CATEGORIES.get(category, "")


class _ReadSession:
    """请求级业务库快照；SQLite 的 WAL/SHM 仅在系统临时目录处理。

    mode=ro 仍可能在源库旁建立或更新 SHM，immutable 又会忽略 WAL。
    因此复制列明业务库及已有 WAL 后读取，前后核对源文件状态；复制不稳定、
    回滚日志待恢复或超过大小上限时返回未知，不读取不完整副本。
    """

    def __init__(self):
        self.connections = {}
        self.temporary = None

    @staticmethod
    def copy_file(source, target):
        remaining = _MAX_DB_SNAPSHOT
        with source.open("rb") as reader, target.open("wb") as writer:
            while True:
                block = reader.read(min(1024 * 1024, remaining + 1))
                if not block:
                    return
                remaining -= len(block)
                if remaining < 0:
                    raise CatalogError("数据文件正在更新或过大")
                writer.write(block)

    @staticmethod
    def stamp(path):
        try:
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise CatalogError("数据文件含链接，未展开统计")
            info = path.stat()
            if not path.is_file():
                raise CatalogError("本地数据暂时无法读取")
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        except FileNotFoundError:
            return None

    def open(self, source):
        source = Path(source).absolute()
        key = str(source)
        if key in self.connections:
            cached = self.connections[key]
            if isinstance(cached, Exception):
                raise cached
            return cached
        try:
            if self.stamp(source) is None:
                self.connections[key] = None
                return None
            if self.temporary is None:
                self.temporary = tempfile.TemporaryDirectory(prefix="fmjj-dashboard-")
            folder = Path(self.temporary.name) / str(len(self.connections))
            folder.mkdir()
            target = folder / "data.db"
            wal = Path(str(source) + "-wal")
            journal = Path(str(source) + "-journal")
            target_wal = Path(str(target) + "-wal")
            for _ in range(2):
                before = tuple(self.stamp(path) for path in (source, wal, journal))
                if before[0] is None:
                    raise CatalogError("数据文件正在更新")
                if before[2] is not None and before[2][2]:
                    raise CatalogError("数据文件正在更新")
                if sum(stamp[2] for stamp in before[:2] if stamp) > _MAX_DB_SNAPSHOT:
                    raise CatalogError("数据量较大，请在对应页面查看完整数据")
                self.copy_file(source, target)
                if before[1] is not None:
                    self.copy_file(wal, target_wal)
                elif target_wal.exists():
                    target_wal.unlink()
                after = tuple(self.stamp(path) for path in (source, wal, journal))
                if before == after:
                    break
            else:
                raise CatalogError("数据文件正在更新")
            connection = sqlite3.connect(target.as_uri() + "?mode=ro",
                                         uri=True, timeout=2)
            self.connections[key] = connection
            return connection
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.connections[key] = exc
            raise

    def close(self):
        try:
            for connection in self.connections.values():
                if isinstance(connection, sqlite3.Connection):
                    connection.close()
        finally:
            if self.temporary is not None:
                self.temporary.cleanup()


@contextmanager
def connect_ro(path):
    """缺库返回 None；源目录零写入，每个请求复用一份业务库快照。"""
    session = _READ_SESSION.get()
    if session is not None:
        yield session.open(path)
        return
    session = _ReadSession()
    try:
        yield session.open(path)
    finally:
        session.close()


def query(path, sql, params=()):
    with connect_ro(path) as conn:
        return conn.execute(sql, params).fetchall() if conn is not None else []


def scalar(path, sql, params=()):
    rows = query(path, sql, params)
    return max(0, int(rows[0][0] or 0)) if rows else 0


def _files(root, *, depth=2):
    """只读固定深度；不跟随 symlink/junction，不递归扫描任意媒体目录。"""
    root = Path(root)
    if not root.exists():
        return []
    pending = [(root, 0)]
    out = []
    visited = 0
    while pending:
        folder, level = pending.pop()
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            raise CatalogError("目录含链接，未展开统计")
        with os.scandir(folder) as entries:
            for item in entries:
                visited += 1
                if visited > _MAX_ENTRIES:
                    raise CatalogError("目录较大，请在对应页面查看完整数据")
                path = Path(item.path)
                if item.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    continue
                if item.is_file(follow_symlinks=False):
                    out.append(path)
                elif level < depth and item.is_dir(follow_symlinks=False):
                    pending.append((path, level + 1))
    return out


def _metric(key, label, loader, unit="条", note=""):
    try:
        value = max(0, int(loader()))
        return {"key": key, "label": label, "value": value, "unit": unit,
                "note": note, "state": "ready" if value else "empty"}
    except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
        return {"key": key, "label": label, "value": None, "unit": unit,
                "note": "本地数据暂时无法完整读取", "state": "error"}


def _section(key, title, note, metrics):
    return {"key": key, "title": title, "note": note, "metrics": metrics}


def account_entries(root, config, source):
    """只读已有账户清单，不导入会解析写目录或生成默认账号的 owner。"""
    folder = config if getattr(sys, "frozen", False) else root / "src" / (
        "tracker" if source == "tracker" else "music_account")
    filename = "accounts.json" if source == "tracker" else "music_accounts.json"
    items = read_json(folder / filename, list, [])
    if not all(isinstance(item, dict) for item in items):
        raise CatalogError("账号清单格式不支持")
    return items


def load_catalog(root, config, export, convert):
    """读取各数据 owner 的已存清单与库，不新增落盘数据。"""
    sub = lambda category: export / EXPORT_CATEGORIES[category]
    cache = {}

    def files(category, depth=2):
        key = (category, depth)
        if key not in cache:
            try:
                cache[key] = _files(sub(category), depth=depth)
            except (OSError, ValueError) as exc:
                cache[key] = exc
        if isinstance(cache[key], Exception):
            raise cache[key]
        return cache[key]

    def manifest_count(category, predicate, depth=2):
        return sum(1 for path in files(category, depth) if predicate(path))

    def comment_stats():
        if "comments" not in cache:
            manifests = [p for p in files("video_comments", 3) if p.name == "comments.json"]
            cache["comments"] = (len(manifests), sum(
                len(read_json(path, list, [])) for path in manifests))
        return cache["comments"]

    def article_count():
        # 同篇导出的 HTML/PDF/Word/Markdown 共用一个文章目录，仅计一次。
        return len({p.parent for p in files("wechat", 2)
                    if p.suffix.lower() in {".html", ".pdf", ".docx", ".md"}})

    def live_counts():
        if "live_counts" in cache:
            return cache["live_counts"]
        state = read_json(config / "live_record.json", dict, {})
        platforms = state.get("platforms", {})
        if not isinstance(platforms, dict):
            raise CatalogError("主播设置格式不支持")
        anchors = []
        for item in platforms.values():
            if not isinstance(item, dict) or not isinstance(item.get("anchors", []), list):
                raise CatalogError("主播设置格式不支持")
            if not all(isinstance(anchor, dict) for anchor in item.get("anchors", [])):
                raise CatalogError("主播设置格式不支持")
            anchors.extend(item.get("anchors", []))
        cache["live_counts"] = (
            len(anchors), sum(bool(a.get("monitoring")) for a in anchors),
            sum(bool(a.get("want_record", a.get("recording"))) for a in anchors))
        return cache["live_counts"]

    def fan_count():
        known = set()
        for path in files("fan_groups", 2):
            if path.name != "最新.json" or "QQ群" in path.parts:
                continue
            payload = read_json(path, dict, {})
            groups = payload.get("groups", [])
            if not isinstance(groups, list):
                raise CatalogError("群快照格式不支持")
            for group in groups:
                if isinstance(group, dict):
                    known.add((str(payload.get("platform") or path.parent.parent.name),
                               str(group.get("group_id") or group.get("group_no") or "")))
        return len([key for key in known if key[1]])

    tcdoc = sub("tcdoc")
    db_metric = lambda key, label, path, sql, unit="条", note="": _metric(
        key, label, lambda: scalar(path, sql), unit, note)
    qq_db = sub("fan_groups") / "QQ群" / "qq_groups.db"
    sections = [
        _section("accounts", "账号与达人", "来自已保存的账号清单和指数快照，不触发采集", [
            _metric("info_accounts", "采集账号", lambda: sum(
                1 for line in (config / "accounts.txt").read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))
                if (config / "accounts.txt").exists() else 0, "个"),
            _metric("media_accounts", "网媒追踪账号", lambda: len(account_entries(root, config, "tracker")), "个"),
            _metric("music_accounts", "音乐追踪账号", lambda: len(account_entries(root, config, "music")), "个"),
            _metric("index_reports", "指数达人", lambda: manifest_count("douyin_index", lambda p: p.name == "latest.json", 1), "个"),
        ]),
        _section("tcdoc", "文档资料", "本地表格、正文、修订和图片清单，分别计数", [
            db_metric("tcdoc_sheets", "同步工作表", tcdoc / "tables.db", "SELECT COUNT(*) FROM sheets", "张"),
            db_metric("tcdoc_changes", "表格变更记录", tcdoc / "tables.db", "SELECT COUNT(*) FROM changes"),
            db_metric("tcdoc_pages", "正文页面", tcdoc / "pages.db", "SELECT COUNT(*) FROM pages", "页"),
            db_metric("tcdoc_revisions", "修订记录", tcdoc / "revisions.db", "SELECT COUNT(*) FROM revision_rows"),
            db_metric("tcdoc_images", "登记图片", tcdoc / "tables.db", "SELECT COUNT(*) FROM images", "张"),
            db_metric("tcdoc_saved_members", "在列成员", tcdoc / "members.db", "SELECT COUNT(*) FROM members WHERE active=1", "人", "按文档分别计数，同一人可在多个文档出现"),
        ]),
        _section("resources", "资源与导出", "只统计已登记索引和固定业务目录；多格式文章按目录去重", [
            db_metric("asset_media_dirs", "媒体索引目录", config / "asset_index.db", "SELECT COUNT(*) FROM media_dir", "个"),
            _metric("wechat_articles", "导出文章", article_count, "篇"),
            _metric("comment_sets", "评论归档", lambda: comment_stats()[0], "组"),
            _metric("saved_comments", "已保存评论", lambda: comment_stats()[1]),
        ]),
        _section("music", "音乐数据", "历史快照与已归档文件；不扫描自定义歌曲目录", [
            _metric("media_histories", "网媒历史报表", lambda: manifest_count("tracker", lambda p: p.suffix.lower() == ".xlsx" and not p.name.startswith("~$"), 0), "份"),
            _metric("music_histories", "音乐历史报表", lambda: manifest_count("music_tracker", lambda p: p.suffix.lower() == ".xlsx" and not p.name.startswith("~$"), 0), "份"),
            _metric("music_files", "歌曲归档文件", lambda: manifest_count("music", lambda p: p.suffix.lower() in {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".ape"}, 2), "个", "统一导出目录内至多两级子目录，不含自定义位置"),
            db_metric("music_tag_history", "标签历史记录", config / "music_tag_history.db", "SELECT COUNT(*) FROM tag_history"),
        ]),
        _section("community", "群与直播", "直播仅展示已保存设置，不代表实时开播或录制状态", [
            db_metric("qq_groups", "已记录QQ群", qq_db, "SELECT COUNT(DISTINCT group_no) FROM group_snapshot", "个"),
            db_metric("qq_group_snapshots", "QQ群人数快照", qq_db, "SELECT COUNT(*) FROM group_snapshot"),
            _metric("fan_groups", "平台粉丝群", fan_count, "个", "最近群快照，不含QQ群"),
            _metric("live_anchors", "已保存主播", lambda: live_counts()[0], "个"),
            _metric("live_monitoring", "已设监控主播", lambda: live_counts()[1], "个"),
            _metric("live_record_intent", "已设录制主播", lambda: live_counts()[2], "个"),
        ]),
    ]

    def listing_count(folder, key):
        payload = read_json(export / folder / "_latest.json", dict, {})
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise CatalogError("清单格式不支持")
        return len(values)

    def fixed_manifest_count(folder, predicate):
        return sum(1 for p in _files(export / folder, depth=1) if predicate(p))

    sections.append(_section("commerce", "商品与演出", "最近解析清单与单项归档分别统计，不合并为总量", [
        _metric("taobao_products", "最近淘宝商品", lambda: listing_count("淘宝商品", "products"), "件"),
        _metric("douyin_products", "抖音商品归档", lambda: manifest_count("douyin_shop", lambda p: p.name.startswith("商品信息_") and p.suffix == ".json", 1), "件"),
        _metric("bili_shops", "B站小店归档", lambda: manifest_count("bili_shop", lambda p: p.name == "小店信息.json", 1), "家"),
        _metric("xianyu_products", "闲鱼商品归档", lambda: fixed_manifest_count("闲鱼商品", lambda p: p.name == "item.json"), "件"),
        _metric("ticket_events", "最近演出清单", lambda: listing_count("票务演出", "events"), "场"),
        _metric("ticket_archives", "演出资料归档", lambda: fixed_manifest_count("票务演出", lambda p: p.name == "event.json"), "场"),
    ]))
    sections.append(_section("archive", "历史与任务成果", "主库和派生归档独立展示；此处不发起任何任务", [
        db_metric("collected_rows", "采集主库", config / "collected.db", "SELECT COUNT(*) FROM videos"),
        db_metric("chaohua_topics", "超话主题", config / "weibo_chaohua.db", "SELECT COUNT(*) FROM topics", "个"),
        db_metric("netdisk_uploaded", "网盘上传记录", config / "quark_backup.db", "SELECT COUNT(*) FROM uploaded"),
        db_metric("netdisk_runs", "网盘备份批次", config / "quark_backup.db", "SELECT COUNT(*) FROM runs", "次"),
        _metric("summary_exports", "直播总结文件", lambda: manifest_count("livestream_summary", lambda p: p.suffix.lower() in {".txt", ".md", ".docx"}, 1), "份"),
    ]))
    return sections


def load_snapshot():
    """一次读取共用临时业务库快照，结束即清理；不触碰源数据。"""
    session = _ReadSession()
    token = _READ_SESSION.set(session)
    try:
        return _load_snapshot()
    finally:
        _READ_SESSION.reset(token)
        session.close()


def _load_snapshot():
    """生成兼容旧工作台的统计，以及覆盖本地业务领域的分组指标。"""
    from toolbox.platform_names import ALL_PLATFORM_NAMES

    now = datetime.now(CST)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=6)
    root, config, export, convert = readonly_roots()
    tcdoc = export / EXPORT_CATEGORIES["tcdoc"]
    counts = [0] * 7
    stats = {key: 0 for key in (
        "platform_items", "chaohua_posts", "tcdoc_rows", "asset_files",
        "doc_views", "doc_members", "doc_readers", "doc_count",
        "tracked_accounts", "tracked_total", "site_visitors", "site_sessions",
        "site_pageviews", "site_online", "site_collected")}
    stats.update({"site_scope": "", "site_date": "", "site_fetched_at": ""})
    states = {}
    by_source = {}
    trend_errors = []

    def assign(keys, loader):
        try:
            values = loader()
            for key, value in zip(keys, values):
                stats[key] = max(0, int(value or 0))
                states[key] = "ready" if stats[key] else "empty"
        except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
            for key in keys:
                stats[key] = None
                states[key] = "error"

    def add_days(loader):
        try:
            rows = loader()
            source_counts = [0] * 7
            for day, count in rows:
                index = (datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
                         - start.date()).days
                if 0 <= index < 7:
                    source_counts[index] += max(0, int(count or 0))
            for index, count in enumerate(source_counts):
                counts[index] += count
        except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
            trend_errors.append(True)

    archive_path = config / "increment.db"
    archive_total = None
    archive_sources = {}
    archive_days = []
    try:
        with connect_ro(archive_path) as conn:
            archive_total = 0
            if conn is not None:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                for name in tables:
                    if str(name).startswith(("_", "sqlite_")):
                        continue
                    # sqlite_master 名称仍须双引号转义；不拼入未转义的 SQL。
                    table = '"' + str(name).replace('"', '""') + '"'
                    total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    archive_sources[str(name)] = total
                    archive_total += total
                    if not (config / "collected.db").exists():
                        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                        if "fetched_at" in columns:
                            archive_days.extend(conn.execute(
                                f"SELECT date(fetched_at), COUNT(*) FROM {table}"
                                " WHERE fetched_at >= ? GROUP BY date(fetched_at)",
                                (start.strftime("%Y-%m-%d 00:00:00"),)).fetchall())
    except (OSError, ValueError, TypeError, sqlite3.Error):
        archive_total = None
        archive_sources = {}

    collected_path = config / "collected.db"
    if collected_path.exists():
        def platform_rows():
            rows = query(collected_path, "SELECT platform, COUNT(*) FROM videos GROUP BY platform")
            for platform, count in rows:
                by_source[str(platform or "unknown")] = int(count or 0)
            return [sum(int(row[1] or 0) for row in rows)]
        assign(["platform_items"], platform_rows)
        add_days(lambda: query(collected_path,
            "SELECT date(created_at, 'unixepoch', '+8 hours'), COUNT(*) FROM videos"
            " WHERE created_at >= ? GROUP BY date(created_at, 'unixepoch', '+8 hours')",
            (start.timestamp(),)))
        platform_note = "平台内容取自采集主库；派生归档独立展示，不重复相加"
    else:
        stats["platform_items"] = archive_total
        states["platform_items"] = ("error" if archive_total is None
                                     else "ready" if archive_total else "empty")
        by_source.update(archive_sources)
        add_days(lambda: archive_days)
        if archive_total is None:
            trend_errors.append(True)
        platform_note = "尚无采集主库，平台内容暂按已有派生归档展示"

    for key, source, path, sql in (
        ("chaohua_posts", "weibo_chaohua", config / "weibo_chaohua.db", "SELECT COUNT(*) FROM posts"),
        ("tcdoc_rows", "tcdoc", tcdoc / "tables.db", "SELECT COUNT(*) FROM rows"),
        ("asset_files", "assets", config / "asset_index.db", "SELECT COUNT(*) FROM asset_json"),
    ):
        assign([key], lambda p=path, statement=sql: [scalar(p, statement)])
        if stats[key] is not None:
            by_source[source] = stats[key]
    add_days(lambda: query(config / "weibo_chaohua.db",
        "SELECT date(first_seen, 'unixepoch', '+8 hours'), COUNT(*) FROM posts"
        " WHERE first_seen >= ? GROUP BY date(first_seen, 'unixepoch', '+8 hours')",
        (start.timestamp(),)))
    add_days(lambda: query(tcdoc / "tables.db",
        "SELECT date(ts), COUNT(*) FROM changes WHERE kind='新增' AND ts >= ? GROUP BY date(ts)",
        (start.strftime("%Y-%m-%d 00:00:00"),)))

    def doc_totals():
        rows = query(tcdoc / "tcdoc.db",
            "SELECT views, members, readers FROM daily d WHERE id=("
            " SELECT id FROM daily x WHERE x.doc_key=d.doc_key ORDER BY ts DESC,id DESC LIMIT 1)")
        return [sum(int(r[column] or 0) for r in rows) for column in range(3)] + [len(rows)]
    assign(["doc_views", "doc_members", "doc_readers", "doc_count"], doc_totals)

    def account_totals():
        keys = set()
        for source in ("tracker", "music"):
            for item in account_entries(root, config, source):
                name = str(item.get("name") or "").strip() or "未命名账号"
                keys.add(f"{source}|{item.get('platform') or ''}|{item.get('url') or name}")
        watched = convert.get("home_watch_accounts")
        selected = keys if not isinstance(watched, list) else keys & {str(k) for k in watched}
        return [len(selected), len(keys)]
    assign(["tracked_accounts", "tracked_total"], account_totals)

    scope = str(convert.get("webwiki_analytics_scope") or "human")
    if scope not in {"human", "qualified", "all"}:
        scope = "human"
    scope_labels = {"human": "严格验证（默认）", "qualified": "严格验证 + 行为合格", "all": "全部分类"}
    stats["site_scope"] = scope_labels[scope]
    def site_totals():
        site_db = export / EXPORT_CATEGORIES["webwiki_monitor"] / "访问统计.db"
        rows = query(site_db,
            "SELECT visitors,sessions,pageviews,online,stat_date,fetched_at,scope FROM snapshots"
            " WHERE scope=? ORDER BY stat_date DESC,fetched_at DESC LIMIT 1", (scope,))
        if not rows:
            rows = query(site_db,
                "SELECT visitors,sessions,pageviews,online,stat_date,fetched_at,scope FROM snapshots"
                " ORDER BY stat_date DESC,fetched_at DESC LIMIT 1")
        if not rows:
            return [0, 0, 0, 0]
        row = rows[0]
        stats.update(site_collected=1, site_date=str(row[4] or ""),
                     site_fetched_at=str(row[5] or ""),
                     site_scope=scope_labels.get(str(row[6]), "已有口径"))
        return row[:4]
    assign(["site_visitors", "site_sessions", "site_pageviews", "site_online"], site_totals)

    sections = load_catalog(root, config, export, convert)
    sections[-1]["metrics"].insert(1, {
        "key": "increment_rows", "label": "平台派生归档", "value": archive_total, "unit": "条",
        "note": "与采集主库可能重叠，不计入平台内容合计",
        "state": "error" if archive_total is None else "ready" if archive_total else "empty"})
    return {
        "schema_version": 1, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats, "stat_states": states, "by_source": by_source,
        "source_labels": dict(ALL_PLATFORM_NAMES, weibo_chaohua="微博超话",
                              tcdoc="腾讯文档", assets="本地资源", unknown="其他"),
        "labels": [(start + timedelta(days=i)).strftime("%m-%d") for i in range(7)],
        "counts": counts, "week_total": sum(counts), "today_total": counts[-1],
        "trend_state": "error" if trend_errors else "ready" if sum(counts) else "empty",
        "trend_note": "仅含平台内容入库、超话首次入库和表格新增；不含文件修改时间"
                      + ("；部分来源暂不可读取" if trend_errors else ""),
        "platform_note": platform_note, "sections": sections,
    }
