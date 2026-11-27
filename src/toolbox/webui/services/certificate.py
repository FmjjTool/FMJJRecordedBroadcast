"""证书证据导出的 Web 表现适配，不保存密码或任务结果。"""

from __future__ import annotations

import threading
import time
import uuid

from toolbox import webui_certificate_evidence


class CertificateService:
    def __init__(self):
        self._export_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = None

    def overview(self) -> dict:
        return webui_certificate_evidence.overview()

    def prepare(self) -> dict:
        """Reserve one short-lived export; never store its password or content."""
        with self._state_lock:
            active = self._active
            if active and (active["status"] in ("running", "cancelling")
                           or active["status"] == "prepared" and active["expires"] > time.monotonic()):
                raise ValueError("已有证据包正在导出，请取消或等待完成后重试")
            active = {"export_id": uuid.uuid4().hex, "cancel": threading.Event(),
                      "status": "prepared", "done": 0, "total": 0,
                      "expires": time.monotonic() + 300}
            self._active = active
            return {"export_id": active["export_id"]}

    def _find(self, export_id):
        active = self._active
        if (not isinstance(export_id, str) or not active
                or active["export_id"] != export_id
                or active["status"] == "prepared" and active["expires"] <= time.monotonic()):
            raise ValueError("导出会话已失效，请重新导出")
        return active

    def progress(self, export_id: str) -> dict:
        with self._state_lock:
            active = self._find(export_id)
            return {key: active[key] for key in ("status", "done", "total")}

    def cancel(self, export_id: str) -> dict:
        with self._state_lock:
            active = self._find(export_id)
            if active["status"] in ("prepared", "running", "cancelling"):
                active["cancel"].set()
                active["status"] = ("cancelled" if active["status"] == "prepared"
                                    else "cancelling")
                return {"cancelled": True}
            return {"cancelled": active["status"] == "cancelled"}

    def export(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("导出参数无效，请重新选择证书记录")
        if not self._export_lock.acquire(blocking=False):
            raise ValueError("已有证据包正在导出，请稍后重试")
        active = None
        try:
            body = dict(payload)
            export_id = body.pop("export_id", None)
            with self._state_lock:
                if export_id is not None:
                    active = self._find(export_id)
                    if active["status"] != "prepared":
                        raise ValueError("导出会话已使用或已取消，请重新导出")
                else:
                    pending = self._active
                    if pending and (pending["status"] in ("running", "cancelling")
                                    or pending["status"] == "prepared" and pending["expires"] > time.monotonic()):
                        raise ValueError("已有证据包等待导出，请取消或等待完成后重试")
                    active = {"export_id": uuid.uuid4().hex, "cancel": threading.Event(),
                              "status": "prepared", "done": 0, "total": 0,
                              "expires": time.monotonic() + 300}
                    self._active = active
                active["status"] = "running"

            def update(done, total):
                with self._state_lock:
                    active.update(done=max(0, int(done)), total=max(0, int(total)))

            result = webui_certificate_evidence.export(
                body, is_cancelled=active["cancel"].is_set, on_progress=update)
            with self._state_lock:
                if active["cancel"].is_set():
                    raise ValueError("已取消证据导出")
                active["status"] = "completed"
            return result
        except OSError:
            raise ValueError("读取证据文件失败，请确认文件可访问后重试") from None
        finally:
            if active is not None:
                with self._state_lock:
                    if active["status"] != "completed":
                        active["status"] = ("cancelled" if active["cancel"].is_set() else "failed")
            self._export_lock.release()
