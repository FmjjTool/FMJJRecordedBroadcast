"""资产库 WebUI 表现层（L2）：参数校验、L3 用例调用与任务适配。

不直接访问导出目录、索引库或增量数据库；业务查询与扫描由
`toolbox.asset_query.AssetQueryService`（L3）提供。
"""

from __future__ import annotations

import threading

from toolbox import webui_assets_dl
from toolbox.asset_query import AssetQueryService
from toolbox.webui.jobs import JobCancelled

_STATUS_KEYS = ("", "missing", "partial", "ok", "text")


class AssetService:
    def __init__(self, jobs=None, query: AssetQueryService | None = None):
        self._jobs = jobs
        self._query = query or AssetQueryService()
        self._local_jobs: dict[str, str] = {}
        self._local_lock = threading.Lock()

    def summary(self) -> dict:
        return self._query.summary()

    def db_tables(self) -> dict:
        return self._query.db_tables()

    def db_rows(self, platform: str, page: int, page_size: int,
                keyword: str = "", author: str = "", order_by: str = "",
                ascending: bool = False) -> dict:
        return self._query.db_rows(platform, page, page_size, keyword, author,
                                   order_by, ascending)

    def entries(self, page: int, page_size: int, keyword: str = "",
                platform: str = "") -> dict:
        return self._query.entries(page, page_size, keyword, platform)

    def entries_status(self, page: int, page_size: int, keyword: str = "",
                       platform: str = "", status: str = "") -> dict:
        if status not in _STATUS_KEYS:
            raise ValueError("未知状态筛选")
        return webui_assets_dl.entries_with_status(
            page, page_size, keyword, platform, status)

    def preview_local(self, category: str, rel_dir: str, name: str):
        """返回 (bytes, mime)；只读预览，详见 L3 `preview_local`。"""
        return self._query.preview_local(category, rel_dir, name)

    def start_download(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        paths = payload.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ValueError("请选择要下载的条目")
        quality = str(payload.get("quality") or "最高画质")
        job = self._jobs.submit(
            "assets_download",
            lambda task: self._download_job(task, paths, quality))
        return {"job": job.id}

    def _download_job(self, job, paths, quality: str) -> dict:
        def progress(done: int, total: int, name: str) -> None:
            job.update(f"正在下载 {done}/{total}: {name}",
                       done / total if total else None)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return webui_assets_dl.download_entries(
                paths, quality=quality, on_progress=progress,
                is_cancelled=cancelled)
        except webui_assets_dl.DownloadCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def start_local_scan(self, category: str) -> dict:
        self._query.category_root(category)
        if self._jobs is None:
            raise ValueError("扫描任务不可用")
        with self._local_lock:
            job_id = self._local_jobs.get(category)
            if job_id:
                job = self._jobs.get(job_id)
                if job is not None and job.status in ("queued", "running"):
                    return {"category": category, "status": "scanning",
                            "job": job.id}
            job = self._jobs.submit(
                "assets_local_scan",
                lambda task: self._scan_local(task, category))
            self._local_jobs[category] = job.id
        return {"category": category, "status": "scanning", "job": job.id}

    def _scan_local(self, job, category: str) -> dict:
        def progress(count: int) -> None:
            if job.cancel_requested:
                raise JobCancelled("扫描已取消")
            if count:
                job.update(f"已扫描 {count} 个文件")

        try:
            return self._query.scan_local(category, on_progress=progress)
        finally:
            with self._local_lock:
                if self._local_jobs.get(category) == job.id:
                    self._local_jobs.pop(category, None)

    def local_groups(self, category: str, page: int = 1,
                     page_size: int = 40) -> dict:
        data = self._query.local_groups(category, page, page_size)
        with self._local_lock:
            job_id = self._local_jobs.get(category, "")
        job = self._jobs.get(job_id) if (job_id and self._jobs) else None
        scanning = bool(job is not None and job.status in ("queued", "running"))
        data["status"] = "scanning" if scanning else data["status"]
        data["job"] = job.id if scanning else ""
        return data