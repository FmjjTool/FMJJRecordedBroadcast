"""腾讯文档 WebUI 服务：概览、同步状态、本地任务与白名单单条写回。

采集/同步只调用既有 L3 任务并写入本地库；写回固定为「官方动态」「直播记录」
两表的单条新增，经 `webui_tcdoc_write` 用例执行，桌面与 WebUI 共用写锁。
"""

from __future__ import annotations

import threading

from toolbox.tcdoc import config, push_history, storage, tables
from toolbox.tcdoc import job as tcdoc_job
from toolbox.webui.errors import Conflict
from toolbox.webui.jobs import JobCancelled

BusyError = Conflict


class TCDocService:
    def __init__(self, jobs):
        self.jobs = jobs
        self._lock = threading.Lock()

    def overview(self, doc_key: str = "") -> dict:
        docs = config.load_docs()
        if doc_key not in {doc.get("key") for doc in docs}:
            doc_key = (docs[0].get("key") if docs else "")
        latest, daily, doc_stats = {}, [], [0, ""]
        browser_stats = [0, ""]
        db_path = config.db_path()
        if doc_key and db_path.exists():
            row = storage.latest(db_path, doc_key)
            latest = _row_dict(row)
            daily = [dict(item) for item in storage.all_daily(db_path, doc_key)][-30:]
            doc_stats = list(storage.doc_stats(db_path, doc_key))
            browser_stats = list(storage.browser_stats(db_path, doc_key))
        table_stats, sheets = {}, []
        tables_db = config.tables_db_path()
        if doc_key and tables_db.exists():
            try:
                table_stats = tables.stats(doc_key)
                sheets = tables.sheets_of(doc_key)
            except Exception:
                table_stats, sheets = {}, []
        try:
            pushes = push_history.load(20)
        except Exception:
            pushes = []
        return {
            "docs": [{"key": doc.get("key"), "name": doc.get("name"),
                      "kind": doc.get("kind")} for doc in docs],
            "selected": doc_key,
            "auto": config.auto_sync_enabled(),
            "latest": latest,
            "daily": daily,
            "doc_stats": {"records": doc_stats[0], "last": doc_stats[1]},
            "browser_stats": {"records": browser_stats[0], "last": browser_stats[1]},
            "tables": {"stats": table_stats, "sheets": sheets},
            "push_history": pushes,
        }

    def table_rows(self, doc_key: str, tab_id: str, page: int,
                   page_size: int) -> dict:
        if not doc_key or not tab_id:
            raise ValueError("缺少文档或子表")
        db_path = config.tables_db_path()
        if not db_path.exists():
            raise ValueError("暂无表格同步数据")
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        sheets = {item["tab_id"] for item in tables.sheets_of(doc_key)}
        if tab_id not in sheets:
            raise ValueError("子表不存在")
        rows, total = tables.rows_page(
            doc_key, tab_id, offset=(page - 1) * page_size, limit=page_size)
        pages = max(1, (int(total) + page_size - 1) // page_size)
        return {"doc_key": doc_key, "tab_id": tab_id, "rows": rows,
                "total": int(total), "page": page, "page_size": page_size,
                "pages": pages}

    def writeback_status(self) -> dict:
        return _write_module().status()

    def writeback_preview(self, tab: str, days: int, limit: int) -> dict:
        return _write_module().preview(tab, days, limit)

    def start_writeback_upload(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        tab = str(payload.get("tab") or "")
        if tab not in ("official", "live"):
            raise ValueError("写回目标只支持「官方动态」或「直播记录」")
        key = str(payload.get("key") or "").strip()
        if not key:
            raise ValueError("请选择要写入的记录")
        if not payload.get("confirmed"):
            raise ValueError("缺少写入确认")
        try:
            days = int(payload.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        _write_module().ensure_uploadable(tab)
        job = self.jobs.submit(
            "tcdoc_write",
            lambda task: self._writeback_job(task, tab, key, days))
        return {"job": job.id}

    def _writeback_job(self, job, tab: str, key: str, days: int) -> dict:
        module = _write_module()

        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在写入腾讯文档：{name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return module.upload_single(
                tab, key, confirmed=True, days=days, on_progress=progress,
                is_cancelled=cancelled)
        except module.TcdocWriteCancelled as exc:
            raise JobCancelled(str(exc)) from exc

    def start_collect(self):
        return self._start("tcdoc_collect", "采集腾讯文档", tcdoc_job.collect_all)

    def start_sync(self):
        return self._start("tcdoc_sync", "同步腾讯文档表格", tcdoc_job.sync_tables)

    def _start(self, kind: str, title: str, action):
        if not self._lock.acquire(blocking=False):
            raise BusyError("已有采集或同步任务在运行")
        def run(job):
            try:
                job.log(f"{title}…")
                result = action(on_status=lambda text: job.log(str(text)))
                count = len(result) if isinstance(result, list) else 0
                job.log(f"{title}完成，处理 {count} 项")
                return {"count": count}
            finally:
                self._lock.release()
        try:
            return self.jobs.submit(kind, run)
        except Exception:
            self._lock.release()
            raise


def _write_module():
    from toolbox import webui_tcdoc_write
    return webui_tcdoc_write


def _row_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return {}
