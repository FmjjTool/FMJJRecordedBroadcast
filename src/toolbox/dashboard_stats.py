"""首页看板统计与账号快照（只读，不依赖 Qt）。

load_dashboard_data() 汇总 increment.db / weibo_chaohua.db / tables.db /
tcdoc.db / asset_index.db，返回首页直接渲染的 DTO。页面不再写 SQL。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from toolbox.dashboard_catalog import export_subdir as get_export_subdir
from toolbox.platform_names import ALL_PLATFORM_NAMES

ACCOUNT_PLATFORM_LABELS = ALL_PLATFORM_NAMES


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
    """只读汇总工作台数据，保留旧 DTO 字段供既有卡片与抽屉使用。"""
    from toolbox.dashboard_catalog import load_snapshot
    return load_snapshot()
