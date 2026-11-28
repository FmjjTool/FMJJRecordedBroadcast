"""直播总结 WebUI 适配（L2）：只读概览 + 设置 / 测试连接 / 总结任务。"""

from __future__ import annotations

from toolbox import webui_livestream_summary
from toolbox.livestream_summary.client import CancelledError
from toolbox.webui.jobs import JobCancelled


class LivestreamSummaryService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_livestream_summary.overview()

    def saved_text(self, name: str) -> dict:
        return webui_livestream_summary.saved_text(name)

    def save_settings(self, payload: dict) -> dict:
        return webui_livestream_summary.save_settings(dict(payload))

    def test_connection(self) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        def work(task):
            try:
                return webui_livestream_summary.test_connection(
                    on_log=task.log,
                    is_cancelled=lambda: bool(task.cancel_requested),
                    on_progress=lambda percent, message: task.update(
                        progress=percent / 100.0))
            except CancelledError as exc:
                raise JobCancelled(str(exc)) from exc
        return {"job": self.jobs.submit("local_model_test", work).id}

    def fetch_models(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = dict(payload or {})
        limits = {"base_url": 2048, "api_format": 80, "api_key": 8192}
        for name, limit in limits.items():
            value = payload.get(name, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("模型接口参数无效")

        def work(task):
            try:
                return webui_livestream_summary.fetch_models(
                    payload,
                    is_cancelled=lambda: bool(task.cancel_requested))
            except CancelledError as exc:
                raise JobCancelled(str(exc)) from exc

        return {"job": self.jobs.submit("livestream_model_list", work).id}

    def start_summary(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = dict(payload)
        if not str(payload.get("rel") or "").strip():
            raise ValueError("请选择转录文件")
        webui_livestream_summary.check_transcript(str(payload["rel"]))
        mode = str(payload.get("mode") or "ai")
        if mode not in ("ai", "local", "rule"):
            raise ValueError("未知的总结模式")
        def work(task):
            try:
                return webui_livestream_summary.run_summary(
                    payload, on_log=task.log,
                    is_cancelled=lambda: bool(task.cancel_requested),
                    on_progress=lambda percent, message: task.update(
                        progress=percent / 100.0))
            except CancelledError as exc:
                raise JobCancelled(str(exc)) from exc
        job = self.jobs.submit(
            "livestream_summary", work)
        return {"job": job.id}
