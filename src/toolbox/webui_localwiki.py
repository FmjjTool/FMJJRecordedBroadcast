"""WebUI 资料站：仓库同步、站点启停、访问统计与源文档链接。

可执行：运行微信小程序开发工程、启动 Wiki 网站或两者；一键更新全部仓库并部署 Wiki、
重建站点数据、停止 Wiki 网站、采集今日统计、保存后台密码。
GitHub 登录态、后台密码都复用桌面端已保存的凭据，不在页面回显。
"""

from __future__ import annotations

import threading
from datetime import datetime

from toolbox.webwiki import projects as wiki_projects

_SERVICE = None
_LOCK = threading.Lock()


def _webwiki():
    from toolbox.webwiki import (analytics, config as wiki_cfg,
                                 credentials as wiki_creds, deploy,
                                 gitops, localimages, server)
    return analytics, wiki_cfg, wiki_creds, deploy, gitops, localimages, server


def _service():
    global _SERVICE
    _, wiki_cfg, _, _, _, _, server = _webwiki()
    with _LOCK:
        if _SERVICE is None:
            _SERVICE = server.SiteService(wiki_cfg.root_dir())
        return _SERVICE


def _ts(value) -> str:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).strftime(
                "%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return ""
    return str(value or "")


def _repo_rows(gitops, wiki_cfg) -> list[dict]:
    rows = []
    for name in ("mandisa-miniprogram", "webwiki-api", "webwiki"):
        label = wiki_cfg.REPO_LABELS.get(name, name)
        try:
            state = gitops.repo_state(name)
        except Exception as exc:
            rows.append({"name": name, "label": label, "exists": False,
                         "text": f"{label}：{str(exc)[:60]}"})
            continue
        if state.get("exists"):
            extra = "（有本地改动）" if state.get("dirty") else ""
            rows.append({"name": name, "label": label, "exists": True,
                         "commit": state.get("commit") or "?",
                         "dirty": bool(state.get("dirty")),
                         "text": f"{label}（{name}）@{state.get('commit') or '?'}{extra}"})
        else:
            rows.append({"name": name, "label": label, "exists": False,
                         "text": f"{label}（{name}）：未拉取"})
    return rows


def overview() -> dict:
    analytics, wiki_cfg, wiki_creds, deploy, gitops, localimages, server = \
        _webwiki()
    try:
        creds = wiki_creds.load() or {}
    except Exception:
        creds = {}
    logged_in = bool(creds.get("token"))
    repos = _repo_rows(gitops, wiki_cfg)
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
        "repo_options": list(wiki_cfg.REPO_OPTIONS),
        "project_options": list(wiki_projects.PROJECT_OPTIONS),
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
        "has_password": bool(analytics.has_password()),
        "note": "微信小程序主程序与 Wiki 网站是两个独立项目；可分别运行或运行全部。",
    }


def deploy(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """一键部署 Wiki：更新三仓库 → 装依赖 → 重建 / 构建 → 启动网页 + API。"""
    analytics, wiki_cfg, wiki_creds, deploy_mod, _gitops, _images, server = \
        _webwiki()
    log = on_log or (lambda _msg: None)
    creds = wiki_creds.load() or {}
    token = str(creds.get("token") or "")
    if not token:
        raise ValueError("未登录 GitHub：请先在桌面端「资料站」登录")
    root = wiki_cfg.root_dir()
    cancel = is_cancelled or (lambda: False)

    svc = _service()
    if svc.running:
        log("停止旧服务…")
        svc.stop(log)

    deploy_mod.deploy_all(
        root, token, log,
        progress=lambda step, total, label: log(f"[{step}/{total}] {label}"),
        cancel=lambda: bool(cancel()))
    if cancel():
        raise ValueError("已取消部署")
    log("启动服务…")
    url = svc.start(log)
    return {"url": url, "status": svc.status(),
            "summary": "部署完成，站点已启动：" + url}


def pull_repositories(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """更新单个或全部仓库，只同步代码，不构建或启动服务。"""
    _analytics, wiki_cfg, wiki_creds, _deploy, gitops, _images, _server = _webwiki()
    if not payload.get("confirmed"):
        raise ValueError("请先确认要更新所选仓库")
    creds = wiki_creds.load() or {}
    token = str(creds.get("token") or "")
    if not token:
        raise ValueError("尚未登录 GitHub，请先在桌面端资料站登录")
    root = wiki_cfg.root_dir()
    log = on_log or (lambda _msg: None)

    def safe_log(message):
        text = str(message).replace(str(root), "资料站目录")
        log(text)

    summary = gitops.pull_repositories(
        payload.get("repositories"), token, safe_log, root,
        is_cancelled=is_cancelled)
    return {"repositories": summary,
            "summary": "仓库已更新：" + summary + "。网站服务未启动。"}


def rebuild(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """只重建站点数据并构建前端（不拉代码、不装依赖）。"""
    _a, wiki_cfg, _c, deploy_mod, _g, _i, _s = _webwiki()
    log = on_log or (lambda _msg: None)
    root = wiki_cfg.root_dir()
    cancel = is_cancelled or (lambda: False)
    log("重建站点数据…")
    deploy_mod.sync_docs(root, log)
    if cancel():
        raise ValueError("已取消")
    log("构建前端…")
    deploy_mod.build_frontend(root, log)
    return {"summary": "站点数据与前端已重建（如服务在运行，刷新页面即可）"}


def start_service() -> dict:
    svc = _service()
    url = svc.start(lambda msg: None)
    return {"url": url, "status": svc.status(), "summary": "服务已启动：" + url}


def start_projects(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """在本机开发者工具打开小程序，启动 Wiki 网站，或执行两项。"""
    _analytics, wiki_cfg, _creds, _deploy, _gitops, _images, _server = _webwiki()
    if is_cancelled is not None and is_cancelled():
        raise ValueError("运行项目已取消")
    summary = wiki_projects.start_selected(
        payload.get("scope"), wiki_cfg.root_dir(),
        on_log or (lambda _msg: None), service=_service())
    return {"summary": summary}


def stop_service() -> dict:
    svc = _service()
    svc.stop(lambda msg: None)
    return {"status": svc.status(), "summary": "服务已停止"}


def collect_today(payload: dict, on_log=None, is_cancelled=None) -> dict:
    analytics, wiki_cfg, _c, _d, _g, _i, _s = _webwiki()
    log = on_log or (lambda _msg: None)
    scope = str(payload.get("scope") or wiki_cfg.analytics_scope() or "human")
    row = analytics.collect(scope=scope, log=log)
    return {
        "date": row.get("date") or "",
        "visitors": row.get("visitors"),
        "sessions": row.get("sessions"),
        "pageviews": row.get("pageviews"),
        "online": row.get("online"),
        "summary": (f"{row.get('date')} 访客 {row.get('visitors')} · "
                    f"打开 {row.get('sessions')} · 浏览量 {row.get('pageviews')}"),
    }


def save_password(payload: dict) -> dict:
    analytics, _c, _creds, _d, _g, _i, _s = _webwiki()
    password = str(payload.get("password") or "").strip()
    if not password:
        raise ValueError("请填写后台密码")
    if not analytics.save_password(password):
        raise ValueError("密码保存失败（本机加密存储不可用）")
    return {"has_password": True, "summary": "后台密码已保存（加密）"}
