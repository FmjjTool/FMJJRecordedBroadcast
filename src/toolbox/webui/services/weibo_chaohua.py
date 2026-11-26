"""微博超话 WebUI 适配（L2）：概览 / 分析 + 解析 / 采集 / 导出任务。"""

from __future__ import annotations

from toolbox import webui_weibo_chaohua


class WeiboChaohuaService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_weibo_chaohua.overview()

    def analysis(self, topic_id: str) -> dict:
        return webui_weibo_chaohua.analysis(topic_id)

    def start_parse(self, payload: dict) -> dict:
        payload = dict(payload)
        if not str(payload.get("text") or "").strip():
            raise ValueError("请输入超话链接或 ID")
        return self._submit("chaohua_parse", payload,
                            webui_weibo_chaohua.parse)

    def start_collect(self, payload: dict) -> dict:
        payload = dict(payload)
        if not str(payload.get("topic_id") or "").strip():
            raise ValueError("请先解析超话")
        if not payload.get("channels"):
            raise ValueError("请至少勾选一个频道")
        return self._submit("chaohua_collect", payload,
                            webui_weibo_chaohua.collect)

    def export(self, payload: dict) -> dict:
        return webui_weibo_chaohua.export(dict(payload))

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        job = self.jobs.submit(
            kind,
            lambda task: handler(payload, on_log=task.log,
                                 is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}