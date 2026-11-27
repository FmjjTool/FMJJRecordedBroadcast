"""Web jobs bridge for the shared, controlled dependency installer."""

from __future__ import annotations

import threading

from toolbox import webui_deps
from toolbox.webui.jobs import JobCancelled


class DependencyService:
    def __init__(self, jobs):
        self.jobs = jobs
        self._lock = threading.Lock()
        self._active = None

    def start_install(self, payload: dict) -> dict:
        from toolbox.dependency_installer import DependencyInstallCancelled

        key = webui_deps.prepare_install(payload.get("key"))
        with self._lock:
            if self._active is not None and self._active.status in ("queued", "running"):
                raise ValueError("已有依赖安装任务，请等待结束或取消后重试")

            def work(task):
                def progress(percent, message):
                    task.update(message=message,
                                progress=None if percent is None else percent / 100.0)
                    if percent is None:
                        task.progress = None

                try:
                    result = webui_deps.install(
                        key, on_progress=progress,
                        is_cancelled=lambda: bool(task.cancel_requested))
                    return {**result, "summary": result.get("summary") or "依赖已安装并通过校验"}
                except DependencyInstallCancelled as exc:
                    raise JobCancelled(str(exc)) from exc

            self._active = self.jobs.submit("dependency_install_" + key, work)
            return {"job": self._active.id}
