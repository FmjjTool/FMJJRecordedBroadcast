"""封面自动化 Web 表现适配：预览、确认及可取消后台任务。"""

from __future__ import annotations

import threading

from toolbox import webui_cover


class CoverService:
    def __init__(self, jobs=None):
        self._jobs = jobs
        self._lock = threading.Lock()
        self._running = False

    def overview(self) -> dict:
        return webui_cover.overview()

    def preview(self, payload: dict) -> dict:
        return webui_cover.preview(payload)

    def backfill_status(self) -> dict:
        return webui_cover.backfill_status()

    def backfill_preview(self, payload: dict) -> dict:
        return webui_cover.preview_backfill(payload)

    def start_backfill(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用，请刷新后重试")
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise ValueError("请先确认要回填的腾讯文档记录")
        with self._lock:
            if self._running:
                raise ValueError("已有补封面任务进行中，请等待结束后再开始")
            self._running = True
        try:
            job = self._jobs.submit("cover_backfill",
                                    lambda task: self._backfill(task, dict(payload)))
        except Exception:
            with self._lock:
                self._running = False
            raise
        return {"job": job.id}

    def start_generate(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用，请刷新后重试")
        if payload.get("confirmed") is not True:
            raise ValueError("请先确认要补齐封面的数量和范围")
        plan = webui_cover.prepare(payload)
        with self._lock:
            if self._running:
                raise ValueError("已有补封面任务进行中，请等待结束后再开始")
            self._running = True
        try:
            job = self._jobs.submit("cover_generate", lambda task: self._generate(task, plan))
        except Exception:
            with self._lock:
                self._running = False
            raise
        return {"job": job.id}

    def _generate(self, job, plan) -> dict:
        def progress(done, total, message):
            job.update(f"补封面 {done}/{total}：{message}", done / total if total else None)
        try:
            return webui_cover.generate(
                plan, on_progress=progress,
                is_cancelled=lambda: bool(job.cancel_requested))
        finally:
            with self._lock:
                self._running = False

    def _backfill(self, job, payload: dict) -> dict:
        job.update("正在读取腾讯文档目标表记录…", 0.1)
        try:
            result = webui_cover.apply_backfill(payload)
            job.update(result.get("summary", "腾讯文档回填完成"), 1.0)
            return result
        finally:
            with self._lock:
                self._running = False
