"""WebUI 应用装配：路由与服务实例。"""

from __future__ import annotations

from toolbox.webui.errors import NotFound
from toolbox.webui.jobs import JobManager
from toolbox.webui.server import FileResponse, WebUIServer
from toolbox.webui.services.assets import AssetService
from toolbox.webui.services.collected import CollectedService
from toolbox.webui.services.tcdoc import TCDocService
from version import APP_VERSION


def build_app(token: str, host: str = "127.0.0.1", port: int = 0) -> WebUIServer:
    jobs = JobManager()
    collected = CollectedService()
    assets = AssetService(jobs)
    tcdoc = TCDocService(jobs)
    server = WebUIServer(token, host=host, port=port, on_stop=jobs.close)

    server.get("/api/health", lambda req: {
        "ok": True, "name": "录播工具箱 WebUI", "version": APP_VERSION})

    server.get("/api/collected/overview", lambda req: collected.overview())
    server.get("/api/collected/items", lambda req: collected.items(
        req.q("platform"), req.q_int("page", 1, 1),
        req.q_int("page_size", 30, 1, 200), req.q("q")))

    def start_export(req):
        payload = dict(req.json)
        job = jobs.submit("collected_export",
                          lambda task: collected.export_job(task, payload))
        return {"job": job.id}
    server.post("/api/collected/export", start_export)

    def start_download(req):
        payload = dict(req.json)
        job = jobs.submit("collected_download",
                          lambda task: collected.download_job(task, payload))
        return {"job": job.id}
    server.post("/api/collected/download", start_download)

    server.get("/api/assets/summary", lambda req: assets.summary())
    server.get("/api/assets/db/tables", lambda req: assets.db_tables())
    server.get("/api/assets/db/rows", lambda req: assets.db_rows(
        req.q("platform"), req.q_int("page", 1, 1),
        req.q_int("page_size", 50, 1, 200), req.q("q"), req.q("author"),
        req.q("order_by"), _truthy(req.q("ascending"))))
    server.get("/api/assets/local/groups",
               lambda req: assets.local_groups(
                   req.q("cat"), req.q_int("page", 1, 1),
                   req.q_int("page_size", 40, 1, 100)))

    def start_local_scan(req):
        payload = dict(req.json or {})
        return assets.start_local_scan(str(payload.get("cat") or ""))
    server.post("/api/assets/local/scan", start_local_scan)

    server.get("/api/assets/entries", lambda req: assets.entries(
        req.q_int("page", 1, 1), req.q_int("page_size", 20, 1, 100),
        req.q("q"), req.q("platform")))

    server.get("/api/tcdoc/overview",
               lambda req: tcdoc.overview(req.q("doc_key")))

    def start_collect(req):
        job = tcdoc.start_collect()
        return {"job": job.id}

    def start_sync(req):
        job = tcdoc.start_sync()
        return {"job": job.id}

    server.post("/api/tcdoc/collect", start_collect)
    server.post("/api/tcdoc/sync", start_sync)
    server.get("/api/tcdoc/table-rows", lambda req: tcdoc.table_rows(
        req.q("doc_key"), req.q("tab_id"), req.q_int("page", 1, 1),
        req.q_int("page_size", 50, 1, 200)))

    def get_job(req, job_id):
        job = jobs.get(job_id)
        if job is None:
            raise NotFound("任务不存在")
        return job.public()

    def download_job(req, job_id):
        path = jobs.download(job_id)
        if path is None:
            raise NotFound("任务文件不存在")
        return FileResponse(path, path.name, "application/zip")

    def cancel_job(req, job_id):
        if not jobs.cancel(job_id):
            raise NotFound("任务不存在或已结束")
        return {"ok": True}

    server.get_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})", get_job)
    server.get_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})/download", download_job)
    server.post_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})/cancel", cancel_job)
    return server


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
