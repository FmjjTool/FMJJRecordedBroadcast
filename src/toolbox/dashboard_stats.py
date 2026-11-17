"""首页看板统计与账号快照（只读，不依赖 Qt）。

load_dashboard_data() 汇总 increment.db / weibo_chaohua.db / tables.db /
tcdoc.db / asset_index.db，返回首页直接渲染的 DTO。页面不再写 SQL。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from toolbox.config_manager import get_config_dir, get_export_subdir

ACCOUNT_PLATFORM_LABELS = {
    "bilibili": "B站", "douyin": "抖音", "weibo": "微博",
    "kuaishou": "快手", "xiaohongshu": "小红书",
    "netease": "网易云", "qq": "QQ音乐", "kugou": "酷狗",
    "kuwo": "酷我", "fivesing": "5sing",
}


def tracked_accounts_snapshot():
    """读取网媒 + 音乐账号配置，生成抽屉/看板共用的账号快照。"""
    accounts = []
    sources = []
    try:
        from tracker.accounts import load_accounts as load_media
        sources.append(("tracker", "网媒", load_media))
    except Exception:
        pass
    try:
        from music_account.accounts import load_accounts as load_music
        sources.append(("music", "音乐", load_music))
    except Exception:
        pass
    for source, source_label, loader in sources:
        try:
            items = loader() or []
        except Exception:
            continue
        for item in items:
            name = str(item.get("name") or "").strip() or "未命名账号"
            platform = str(item.get("platform") or "")
            url = str(item.get("url") or "")
            accounts.append({
                "key": f"{source}|{platform}|{url or name}",
                "name": name,
                "platform": platform,
                "platform_label": ACCOUNT_PLATFORM_LABELS.get(platform, platform or "未知"),
                "source": source,
                "source_label": source_label,
                "url": url,
            })
    return accounts


def watched_account_keys():
    """已保存的观察账号 key 集合；None 表示尚未配置（默认全部观察）。"""
    try:
        from toolbox.config_manager import load_convert_config
        raw = load_convert_config().get("home_watch_accounts")
        if isinstance(raw, list):
            return {str(item) for item in raw}
    except Exception:
        pass
    return None


def load_account_history(account):
    """读取单个账号最新两次采集记录（最新在前）。"""
    if account["source"] == "tracker":
        from tracker.accounts import load_history
    else:
        from music_account.accounts import load_history
    return (load_history(account["name"]) or [])[:2]


def tcdoc_docs():
    """腾讯文档清单；配置缺失时回退到 tcdoc.db 中已采集过的 doc_key。"""
    try:
        from toolbox.tcdoc.config import load_docs
        docs = load_docs()
        if docs:
            return docs
    except Exception:
        pass
    docs = []
    try:
        path = get_export_subdir("tcdoc") / "tcdoc.db"
        if path.exists():
            conn = sqlite3.connect(
                f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
            try:
                docs = [{"key": row[0], "name": row[0]}
                        for row in conn.execute(
                            "SELECT DISTINCT doc_key FROM daily ORDER BY doc_key")]
            finally:
                conn.close()
    except Exception:
        pass
    return docs


def load_tcdoc_history(doc_key):
    """读取单文档的历史记录，并统计每天修订记录次数。"""
    base = get_export_subdir("tcdoc")
    rows = []
    revisions = {}
    total = 0
    path = base / "tcdoc.db"
    if path.exists():
        conn = sqlite3.connect(
            f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        try:
            total = int(conn.execute(
                "SELECT COUNT(*) FROM daily WHERE doc_key = ?", (doc_key,)).fetchone()[0])
            rows = [dict(zip(
                ("ts", "date", "views", "views_delta", "members",
                 "members_delta", "readers"), row))
                for row in conn.execute(
                    "SELECT ts, date, views, views_delta, members, members_delta,"
                    " readers FROM daily WHERE doc_key = ?"
                    " ORDER BY ts DESC, id DESC LIMIT 5", (doc_key,))]
        finally:
            conn.close()
    rev_path = base / "revisions.db"
    if rev_path.exists():
        conn = sqlite3.connect(
            f"file:{str(rev_path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        try:
            revisions = {str(day): int(count) for day, count in conn.execute(
                "SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS d, COUNT(*)"
                " FROM revision_rows WHERE doc_key = ? GROUP BY d", (doc_key,))}
        finally:
            conn.close()
    return {"rows": rows, "revisions": revisions, "total": total}


def load_dashboard_data():
    """汇总全功能看板数据：平台内容 / 微博超话 / 腾讯文档 / 资源文件 / 追踪账号。"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=6)
    labels = [(start + timedelta(days=i)).strftime("%m-%d") for i in range(7)]
    start_str = start.strftime("%Y-%m-%d 00:00:00")

    data = {
        "stats": {
            "platform_items": 0,
            "chaohua_posts": 0,
            "tcdoc_rows": 0,
            "asset_files": 0,
            "doc_views": 0,
            "doc_members": 0,
            "doc_readers": 0,
            "doc_count": 0,
            "tracked_accounts": 0,
            "tracked_total": 0,
        },
        "by_source": {},
        "labels": labels,
        "counts": [0] * 7,
        "week_total": 0,
        "today_total": 0,
    }

    def connect_ro(path):
        """只读连接；库不存在或打不开时返回 None，看板其余部分照常展示。"""
        try:
            if not path.exists():
                return None
            return sqlite3.connect(
                f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=5)
        except Exception:
            return None

    def add_daily(day, count):
        try:
            index = (datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
                     - start.date()).days
        except (TypeError, ValueError):
            return
        if 0 <= index < 7:
            data["counts"][index] += int(count or 0)

    def add_daily_rows(rows):
        for day, count in rows:
            add_daily(day, count)

    # ---- 平台内容（increment.db 各平台表）----
    conn = connect_ro(get_config_dir() / "increment.db")
    if conn is not None:
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for table in tables:
                if table.startswith("_") or table.startswith("sqlite_"):
                    continue
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    data["stats"]["platform_items"] += int(count)
                    data["by_source"][table] = int(count)
                    add_daily_rows(conn.execute(
                        f'SELECT date(fetched_at) AS d, COUNT(*) FROM "{table}"'
                        " WHERE fetched_at >= ? GROUP BY d", (start_str,)).fetchall())
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 微博超话（去重帖子 + 首次入库日期）----
    conn = connect_ro(get_config_dir() / "weibo_chaohua.db")
    if conn is not None:
        try:
            count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            data["stats"]["chaohua_posts"] = int(count)
            data["by_source"]["weibo_chaohua"] = int(count)
            add_daily_rows(conn.execute(
                "SELECT date(first_seen, 'unixepoch', 'localtime') AS d, COUNT(*)"
                " FROM posts WHERE first_seen >= ? GROUP BY d",
                (start.timestamp(),)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 腾讯文档（表格行数 + 最新浏览量/成员数/已查看人数）----
    tcdoc_dir = get_export_subdir("tcdoc")
    conn = connect_ro(tcdoc_dir / "tables.db")
    if conn is not None:
        try:
            count = conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
            data["stats"]["tcdoc_rows"] = int(count)
            data["by_source"]["tcdoc"] = int(count)
            add_daily_rows(conn.execute(
                "SELECT date(ts) AS d, COUNT(*) FROM changes"
                " WHERE kind='新增' AND ts >= ? GROUP BY d", (start_str,)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    conn = connect_ro(tcdoc_dir / "tcdoc.db")
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT views, members, readers FROM daily d WHERE id = ("
                " SELECT id FROM daily x WHERE x.doc_key = d.doc_key"
                " ORDER BY ts DESC, id DESC LIMIT 1)").fetchall()
            data["stats"]["doc_count"] = len(rows)
            data["stats"]["doc_views"] = sum(int(row[0] or 0) for row in rows)
            data["stats"]["doc_members"] = sum(int(row[1] or 0) for row in rows)
            data["stats"]["doc_readers"] = sum(int(row[2] or 0) for row in rows)
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 资源文件（asset_index 登记的本地资源）----
    conn = connect_ro(get_config_dir() / "asset_index.db")
    if conn is not None:
        try:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM asset_json").fetchone()[0])
            data["stats"]["asset_files"] = count
            data["by_source"]["assets"] = count
            add_daily_rows(conn.execute(
                "SELECT date(CAST(mtime AS REAL), 'unixepoch', 'localtime') AS d,"
                " COUNT(*) FROM asset_json WHERE CAST(mtime AS REAL) >= ? GROUP BY d",
                (start.timestamp(),)).fetchall())
        except Exception:
            pass
        finally:
            conn.close()

    # ---- 追踪账号（按用户配置的观察范围统计）----
    accounts = tracked_accounts_snapshot()
    if accounts:
        watched = watched_account_keys()
        selected = (set(a["key"] for a in accounts)
                    if watched is None else watched & set(a["key"] for a in accounts))
        data["stats"]["tracked_accounts"] = len(selected)
        data["stats"]["tracked_total"] = len(accounts)
    else:
        # 账号配置不可读时回退到已下载的账号 xlsx 数量
        for category in ("tracker", "music_tracker"):
            try:
                folder = get_export_subdir(category)
                data["stats"]["tracked_accounts"] += sum(
                    1 for path in folder.glob("*.xlsx") if not path.name.startswith("~$"))
            except Exception:
                continue
        data["stats"]["tracked_total"] = data["stats"]["tracked_accounts"]

    data["week_total"] = sum(data["counts"])
    data["today_total"] = data["counts"][-1] if data["counts"] else 0
    return data
