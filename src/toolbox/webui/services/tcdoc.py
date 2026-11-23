"""腾讯文档 WebUI 服务：只读概览、同步状态与本地任务。

写回腾讯文档不在 WebUI 暴露；采集/同步只调用既有 L3 任务并写入本地库。
"""

from __future__ import annotations

import threading

from toolbox.tcdoc import config, push_history, storage, tables
from toolbox.tcdoc import job as tcdoc_job
from toolbox.webui.errors import Conflict

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


def _row_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return {}
