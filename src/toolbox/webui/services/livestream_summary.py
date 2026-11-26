"""直播总结 WebUI 适配（L2）：只读概览 + 设置 / 测试连接 / 总结任务。"""

from __future__ import annotations

from toolbox import webui_livestream_summary


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
        return webui_livestream_summary.test_connection()

    def start_summary(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = dict(payload)
        if not str(payload.get("rel") or "").strip():
            raise ValueError("请选择转录文件")
        webui_livestream_summary.check_transcript(str(payload["rel"]))
        mode = str(payload.get("mode") or "ai")
        if mode not in ("ai", "rule"):
            raise ValueError("未知的总结模式")
        job = self.jobs.submit(
            "livestream_summary",
            lambda task: webui_livestream_summary.run_summary(
                payload, on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}