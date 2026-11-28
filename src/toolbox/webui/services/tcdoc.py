"""腾讯文档 WebUI 服务：概览、同步状态、本地任务与白名单单条写回。

采集/同步只调用既有 L3 任务并写入本地库；写回固定为「官方动态」「直播记录」
两表的单条新增，经 `webui_tcdoc_write` 用例执行，桌面与 WebUI 共用写锁。
"""

from __future__ import annotations

import threading

from toolbox.tcdoc import config, members as members_mod
from toolbox.tcdoc import push_history, revision, storage, tables
from toolbox.tcdoc import job as tcdoc_job
from toolbox.webui.errors import Conflict
from toolbox.webui.jobs import JobCancelled

BusyError = Conflict


class TCDocService:
    def __init__(self, jobs):
        self.jobs = jobs
        self._lock = threading.Lock()
        from toolbox.webui_tcdoc_mapping import MappingSessions
        self._mapping = MappingSessions()

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
        doc_rows = []
        for doc in docs:
            entry = {"key": doc.get("key"), "name": doc.get("name"),
                     "kind": doc.get("kind"),
                     "kind_label": config.kind_label(doc.get("kind")),
                     "url": str(doc.get("url") or ""),
                     "records": 0, "last": ""}
            if db_path.exists():
                try:
                    count, last = storage.doc_stats(db_path, doc.get("key"))
                    entry["records"] = int(count or 0)
                    entry["last"] = last or ""
                except Exception:
                    pass
            doc_rows.append(entry)
        return {
            "docs": doc_rows,
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
                   page_size: int, newest_first: bool = True,
                   sort_column: str = "", direction: str = "") -> dict:
        from toolbox import webui_tcdoc_tables
        return webui_tcdoc_tables.table_rows(
            doc_key, tab_id, page, page_size, newest_first, sort_column, direction)

    def recent_changes(self, doc_key: str, limit: int = 60) -> dict:
        from toolbox import webui_tcdoc_tables
        return webui_tcdoc_tables.recent_changes(doc_key, limit)

    def snapshots(self, doc_key: str, limit: int = 500) -> dict:
        """采集明细（本地每次采集的成功 / 失败记录）。"""
        doc_key = str(doc_key or "").strip()
        if not doc_key:
            raise ValueError("缺少文档")
        db_path = config.db_path()
        if not db_path.exists():
            return {"rows": [], "total": 0}
        limit = max(1, min(5000, int(limit)))
        rows = storage.all_snapshots(db_path, doc_key, limit=limit)
        return {"rows": [dict(row) for row in rows], "total": len(rows)}

    def revisions(self, doc_key: str, limit: int = 200) -> dict:
        """本地修订记录（只读）。"""
        doc_key = str(doc_key or "").strip()
        if not doc_key:
            raise ValueError("缺少文档")
        limit = max(1, min(2000, int(limit)))
        rows, total = revision.load_rows(doc_key, limit=limit)
        return {"rows": rows, "total": int(total), "meta": revision.meta(doc_key)}

    def members(self, doc_key: str, limit: int = 500) -> dict:
        """本地成员名单（只读）。"""
        doc_key = str(doc_key or "").strip()
        if not doc_key:
            raise ValueError("缺少文档")
        limit = max(1, min(5000, int(limit)))
        users, total = members_mod.load_users(doc_key, limit=limit)
        return {"users": users, "total": int(total),
                "meta": members_mod.meta(doc_key)}

    def body(self, doc_key: str) -> dict:
        return _body_module().cached(doc_key)

    def versions(self, doc_key: str) -> dict:
        from toolbox import webui_tcdoc_versions
        return webui_tcdoc_versions.overview(doc_key)

    def open_version(self, doc_key: str, version_id: str) -> dict:
        from toolbox import webui_tcdoc_versions
        return webui_tcdoc_versions.open_version(doc_key, version_id)

    def version_image(self, doc_key: str, version_id: str,
                      asset_id: str) -> tuple[bytes, str]:
        from toolbox import webui_tcdoc_versions
        return webui_tcdoc_versions.image(doc_key, version_id, asset_id)

    def version_table_page(self, doc_key: str, version_id: str, tab_id: str,
                           page: int, sort_column: str, direction: str) -> dict:
        from toolbox import webui_tcdoc_versions
        if direction not in ("", "asc", "desc"):
            raise ValueError("排序方向无效，请重新选择")
        return webui_tcdoc_versions.table_page(doc_key, version_id, tab_id, page,
                                               sort_column, direction == "desc")

    def body_image(self, doc_key: str, page_id: str,
                   image_key: str) -> tuple[bytes, str]:
        return _body_module().read_image(doc_key, page_id, image_key)

    def start_body_fetch(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        doc_key = str((payload or {}).get("doc_key") or "").strip()
        if not doc_key:
            raise ValueError("请选择文档")
        _body_module().validate_document(doc_key)
        if not self._lock.acquire(blocking=False):
            raise BusyError("已有采集或同步任务在运行")

        def run(job):
            from toolbox.tcdoc.body_images import BodyImagesCancelled
            try:
                result = _body_module().fetch(
                    doc_key, on_status=lambda text: job.update(str(text)),
                    is_cancelled=lambda: bool(job.cancel_requested))
                capture = result.get("capture") or {}
                if capture.get("skipped"):
                    job.log(f"{capture['skipped']} 篇页面抓取失败，已保留可用旧存档")
                if not capture.get("complete", True):
                    job.log("页面列表未完整读取，缺失页面的旧存档保留")
                if capture.get("snapshot_failed"):
                    job.log("正文与图片已存入数据库，Markdown 快照写入失败")
                if capture.get("version_failed"):
                    job.log("正文与图片已存入数据库，采集版本保存失败，请检查磁盘空间后重试")
                return result
            except BodyImagesCancelled as exc:
                raise JobCancelled("正文抓取已取消") from exc
            finally:
                self._lock.release()

        try:
            job = self.jobs.submit("tcdoc_body", run)
        except Exception:
            self._lock.release()
            raise
        return {"job": job.id}

    def writeback_status(self) -> dict:
        return _write_module().status()

    def writeback_preview(self, tab: str, days: int, limit: int) -> dict:
        return _write_module().preview(tab, days, limit)

    def mapping_import(self, payload: dict) -> dict:
        return self._mapping.import_table(payload)

    def mapping_rows(self, session: str, page: int, mode: str, sort_column: str,
                     direction: str, preview: str) -> dict:
        return self._mapping.page(session, page, mode, sort_column, direction, preview)

    def mapping_cloud_rows(self, session: str, page: int, query: str,
                           sort_column: str, direction: str, visible: list[str]) -> dict:
        return self._mapping.cloud_rows(session, page, query, sort_column, direction, visible)

    def start_mapping_cloud(self, operation: str, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = dict(payload or {})
        def run(job):
            cancelled = lambda: bool(job.cancel_requested)
            if operation in {"schema", "refresh"}:
                return self._mapping.cloud_schema(
                    str(payload.get("tab") or "official"), job.log, cancelled,
                    str(payload.get("session") or "") if operation == "refresh" else None)
            if operation == "preview":
                return self._mapping.cloud_preview(payload, job.log, cancelled)
            if operation == "apply":
                return self._mapping.cloud_execute(payload, job.log, cancelled)
            raise ValueError("云端映射操作无效")
        job = self.jobs.submit("tcdoc_mapping_cloud_" + operation, run)
        return {"job": job.id}

    def start_mapping(self, operation: str, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        sid = str(payload.get("session") or "")
        reservation = self._mapping.reserve(payload) if operation == "apply" else None

        def run(job):
            cancelled = lambda: bool(job.cancel_requested)
            if operation == "schema":
                result = self._mapping.schema(sid, str(payload.get("tab") or ""), job.log, cancelled)
            elif operation == "compare":
                result = self._mapping.compare(payload, job.log, cancelled)
            elif operation == "apply":
                result = self._mapping.execute(reservation, job.log, cancelled)
            else:
                raise ValueError("表格操作无效")
            if result.get("cancelled") and not result.get("row_nos"):
                raise JobCancelled()
            return result

        try:
            job = self.jobs.submit("tcdoc_mapping_" + operation, run)
        except Exception:
            if reservation is not None:
                self._mapping.release(sid)
            raise
        return {"job": job.id}

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

    def edit_status(self) -> dict:
        return _edit_module().status()

    def edit_preview(self, payload: dict) -> dict:
        payload = payload or {}
        try:
            limit = int(payload.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        return _edit_module().preview(
            str(payload.get("tab") or ""),
            str(payload.get("query") or ""), limit)

    def edit_update(self, payload: dict) -> dict:
        payload = payload or {}
        values = payload.get("values")
        if not isinstance(values, dict):
            raise ValueError("修改内容格式错误")
        return _edit_module().update(
            str(payload.get("tab") or ""),
            str(payload.get("record_id") or ""),
            values, bool(payload.get("confirmed")))

    def edit_delete(self, payload: dict) -> dict:
        payload = payload or {}
        ids = payload.get("record_ids")
        if not isinstance(ids, list):
            raise ValueError("记录清单格式错误")
        return _edit_module().delete(
            str(payload.get("tab") or ""), ids,
            bool(payload.get("confirmed")))

    def bridge_overview(self) -> dict:
        return _bridge_module().overview()
    def bridge_preview(self, payload: dict) -> dict:
        return _bridge_module().preview(payload)

    def bridge_option_preview(self, payload: dict) -> dict:
        return _bridge_module().option_preview(dict(payload or {}))

    def bridge_manual_schema(self, payload: dict) -> dict:
        return _bridge_module().manual_schema(dict(payload or {}))

    def bridge_manual_tsv(self, payload: dict) -> dict:
        return _bridge_module().manual_tsv(dict(payload or {}))

    def promo_status(self) -> dict:
        return _image_module().status()

    def promo_preview(self, payload: dict) -> dict:
        payload = payload or {}
        try:
            days = int(payload.get("days") or 365)
        except (TypeError, ValueError):
            days = 365
        try:
            limit = int(payload.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        return _image_module().preview(days, str(payload.get("query") or ""),
                                       limit)

    def promo_apply(self, payload: dict) -> dict:
        payload = payload or {}
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise ValueError("活动清单格式错误")
        try:
            days = int(payload.get("days") or 365)
        except (TypeError, ValueError):
            days = 365
        return _image_module().apply(keys, days, bool(payload.get("confirmed")))

    def start_bridge_refresh(self) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not self._lock.acquire(blocking=False):
            raise BusyError("已有采集 / 同步 / 刷新任务在运行")
        module = _bridge_module()

        def run(job):
            try:
                job.log("同步前刷新：采集平台资料 + 拉取腾讯文档…")
                result = module.refresh(on_status=lambda text: job.log(str(text)))
                if not result or not result.get("ok"):
                    detail = "；".join(
                        f"{label}：{'成功' if (result.get('steps') or {}).get(key, {}).get('ok') else '失败'}"
                        for key, label in (("info", "信息获取"),
                                           ("tracker", "网媒账号追踪"),
                                           ("tcdoc", "拉取腾讯文档")))
                    raise RuntimeError(
                        f"{result.get('message') or '刷新未完成'}（{detail}）")
                job.log("刷新完成，已允许上传")
                return {"ok": True, "ts": result.get("ts") or "",
                        "steps": result.get("steps") or {}}
            finally:
                self._lock.release()

        try:
            return self.jobs.submit("tcdoc_bridge_refresh", run)
        except Exception:
            self._lock.release()
            raise

    def start_bridge_upload(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        module = _bridge_module()
        module.ensure_uploadable(payload)
        job = self.jobs.submit(
            "tcdoc_bridge_upload",
            lambda task: self._bridge_upload_job(task, module, payload))
        return {"job": job.id}

    def bridge_events(self, payload: dict) -> dict:
        return _bridge_module().event_list(dict(payload or {}))

    def start_bridge_playlist(self, payload: dict) -> dict:
        """线下活动歌单链接写入「活动歌单_*」三列（只改这三列）。"""
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        module = _bridge_module()
        job = self.jobs.submit(
            "tcdoc_bridge_playlist",
            lambda task: module.write_event_playlist(dict(payload)))
        return {"job": job.id}

    def _bridge_upload_job(self, job, module, payload: dict) -> dict:
        def progress(done, total, name) -> None:
            fraction = (float(done) / total) if total else None
            job.update(f"正在上传到腾讯文档：{name}", fraction)

        def cancelled() -> bool:
            return bool(job.cancel_requested)

        try:
            return module.upload(dict(payload), on_progress=progress,
                                 is_cancelled=cancelled)
        except module.BridgeCancelled as exc:
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
                version_failures = sum(bool(item.get("version_error")) for item in result
                                       if isinstance(item, dict)) if isinstance(result, list) else 0
                if version_failures:
                    job.log(f"当前数据已保存，但 {version_failures} 篇采集版本保存失败，请检查磁盘空间后重试")
                job.log(f"{title}完成，处理 {count} 项")
                return {"count": count, "version_failures": version_failures}
            finally:
                self._lock.release()
        try:
            return self.jobs.submit(kind, run)
        except Exception:
            self._lock.release()
            raise


def _body_module():
    from toolbox import webui_tcdoc_body
    return webui_tcdoc_body


def _write_module():
    from toolbox import webui_tcdoc_write
    return webui_tcdoc_write


def _bridge_module():
    from toolbox import webui_tcdoc_bridge
    return webui_tcdoc_bridge


def _edit_module():
    from toolbox import webui_tcdoc_edit
    return webui_tcdoc_edit


def _image_module():
    from toolbox import webui_tcdoc_image
    return webui_tcdoc_image


def _row_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return {}
