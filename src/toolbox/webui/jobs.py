"""WebUI 后台任务（Qt-free）：线程执行、进度、日志与结果文件下载。"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


class JobCancelled(Exception):
    """任务主动取消：由任务函数在检查取消标记后抛出。"""


@dataclass
class Job:
    id: str
    kind: str
    workspace: Path
    status: str = "queued"
    message: str = "排队中"
    progress: float | None = None
    result: dict | None = None
    error: str = ""
    logs: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    cancel_requested: bool = False

    def log(self, text: str) -> None:
        line = str(text).strip()
        if not line:
            return
        self.logs.append(line)
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]
        self.message = line

    def update(self, message: str | None = None, progress: float | None = None) -> None:
        if message:
            self.log(message)
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))

    def add_file(self, path: str | Path) -> None:
        target = Path(path).resolve()
        try:
            target.relative_to(self.workspace.resolve())
        except ValueError as exc:
            raise ValueError("任务产物必须位于任务目录内") from exc
        self.files.append(target)

    def public(self) -> dict:
        payload = {
            "id": self.id, "kind": self.kind, "status": self.status,
            "message": self.message, "progress": self.progress,
            "result": self.result, "error": self.error,
            "logs": self.logs[-100:], "created": self.created,
        }
        if self.status == "done" and self.files:
            payload["download"] = f"/api/jobs/{self.id}/download"
            payload["files"] = [path.name for path in self.files]
        return payload


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._root = Path(tempfile.mkdtemp(prefix="fmjj_webui_"))
        self._closed = False

    def submit(self, kind: str, fn) -> Job:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            job_id = uuid.uuid4().hex[:12]
            workspace = self._root / job_id
            workspace.mkdir(parents=True, exist_ok=True)
            job = Job(id=job_id, kind=kind, workspace=workspace)
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run, args=(job, fn), name=f"webui-{kind}-{job_id}",
            daemon=True)
        thread.start()
        return job

    def _run(self, job: Job, fn) -> None:
        job.status = "running"
        job.log("开始执行")
        try:
            result = fn(job)
            if isinstance(result, dict):
                job.result = result
            job.status = "done"
            job.progress = 1.0
            job.message = "已完成"
        except JobCancelled:
            job.status = "cancelled"
            job.message = "已取消"
            job.log("任务已取消")
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)[:500] or exc.__class__.__name__
            job.message = "失败"
            job.log(job.error)
            _log.error("WebUI 任务失败 %s: %s\n%s", job.kind, exc,
                       traceback.format_exc(limit=6))

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
        if job is not None:
            return job
        return None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status not in ("queued", "running"):
            return False
        job.cancel_requested = True
        job.log("收到取消请求")
        return True

    def download(self, job_id: str) -> Path | None:
        job = self.get(job_id)
        if job is None or job.status != "done" or not job.files:
            return None
        target = job.files[0]
        return target if target.is_file() else None

    def _prune_locked(self, now: float) -> None:
        expired = [job_id for job_id, job in self._jobs.items()
                   if now - job.created > 2 * 3600]
        for job_id in expired:
            job = self._jobs.pop(job_id, None)
            if job is not None:
                shutil.rmtree(job.workspace, ignore_errors=True)

    def close(self) -> None:
        self._closed = True
        with self._lock:
            self._jobs.clear()
        shutil.rmtree(self._root, ignore_errors=True)
