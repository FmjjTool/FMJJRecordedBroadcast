"""WebUI 资料站（只读）：部署状态、站点访问统计与源文档链接。

不拉代码、不部署、不启动服务、不采集统计；这些仍在桌面端。
"""

from __future__ import annotations

from datetime import datetime


def _webwiki():
    from toolbox.webwiki import (analytics, config as wiki_cfg,
                                 credentials as wiki_creds, deploy,
                                 gitops, localimages, server)
    return analytics, wiki_cfg, wiki_creds, deploy, gitops, localimages, server


def _ts(value) -> str:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).strftime(
                "%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return ""
    return str(value or "")


def _repo_rows(gitops) -> list[dict]:
    rows = []
    for name in ("webwiki-api", "webwiki"):
        try:
            state = gitops.repo_state(name)
        except Exception as exc:
            rows.append({"name": name, "exists": False,
                         "text": f"{name}：{str(exc)[:60]}"})
            continue
        if state.get("exists"):
            extra = "（有本地改动）" if state.get("dirty") else ""
            rows.append({"name": name, "exists": True,
                         "commit": state.get("commit") or "?",
                         "dirty": bool(state.get("dirty")),
                         "text": f"{name}@{state.get('commit') or '?'}{extra}"})
        else:
            rows.append({"name": name, "exists": False,
                         "text": f"{name}：未拉取"})
    return rows


def overview() -> dict:
    analytics, wiki_cfg, wiki_creds, deploy, gitops, localimages, server = \
        _webwiki()
    try:
        creds = wiki_creds.load() or {}
    except Exception:
        creds = {}
    logged_in = bool(creds.get("token"))
    repos = _repo_rows(gitops)
    try:
        data = deploy.data_status()
    except Exception:
        data = {"exists": False}
    try:
        local_images = int(localimages.available_count())
    except Exception:
        local_images = 0
    try:
        service = server.SiteService().status()
    except Exception:
        api_port, site_port = wiki_cfg.ports()
        service = {"running": False, "api_running": False,
                   "url": f"http://127.0.0.1:{site_port}/",
                   "api_port": api_port, "site_port": site_port}
    scope = ""
    try:
        scope = wiki_cfg.analytics_scope()
    except Exception:
        scope = "human"
    try:
        latest = analytics.load_latest_ro(scope) or {}
    except Exception:
        latest = {}
    try:
        history = analytics.load_history_ro(14, scope) or []
    except Exception:
        history = []
    try:
        fetch_log = analytics.load_fetch_log_ro(10) or []
    except Exception:
        fetch_log = []
    try:
        has_today = bool(analytics.has_today_ro(scope))
    except Exception:
        has_today = False
    try:
        from toolbox.tcdoc import config as tcdoc_cfg

        docs = [{"name": doc.get("name") or doc.get("key"),
                 "url": doc.get("url") or ""}
                for doc in tcdoc_cfg.load_docs()]
    except Exception:
        docs = []
    return {
        "github": {"logged_in": logged_in},
        "repos": repos,
        "data": {
            "exists": bool(data.get("exists")),
            "mtime": _ts(data.get("mtime")),
            "size_mb": data.get("size_mb") or 0,
            "songs": data.get("songs") or 0,
            "events": data.get("events") or 0,
            "goods": data.get("goods") or 0,
            "posts": data.get("posts") or 0,
            "live": data.get("live") or 0,
            "local_images": local_images,
        },
        "service": {
            "api_running": bool(service.get("api_running")),
            "site_running": bool(service.get("running")),
            "url": service.get("url") or "",
            "api_port": service.get("api_port"),
            "site_port": service.get("site_port"),
        },
        "paths": {"root": str(wiki_cfg.root_dir())},
        "analytics": {
            "scope": scope,
            "scope_label": analytics.scope_label(scope),
            "has_today": has_today,
            "latest": {
                "date": latest.get("stat_date") or latest.get("date") or "",
                "visitors": latest.get("visitors"),
                "sessions": latest.get("sessions"),
                "pageviews": latest.get("pageviews"),
                "online": latest.get("online"),
                "fetched_at": _ts(latest.get("fetched_at")),
            } if latest else {},
            "history": [{
                "date": row.get("stat_date") or "",
                "scope": analytics.scope_label(row.get("scope") or scope),
                "visitors": row.get("visitors"),
                "opens": row.get("sessions"),
                "views": row.get("pageviews"),
            } for row in history],
            "fetch_log": [{
                "time": _ts(row.get("at") or row.get("created_at")),
                "ok": bool(row.get("ok")),
                "message": str(row.get("message") or "")[:120],
            } for row in fetch_log],
        },
        "docs": docs,
        "note": "只读：拉取代码、一键部署、启动 / 停止服务、采集统计与改端口仍在桌面端。",
    }
