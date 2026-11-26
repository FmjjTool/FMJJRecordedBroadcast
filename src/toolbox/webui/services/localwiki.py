"""资料站 WebUI 适配（L2）：概览 + 部署 / 重建 / 启停 / 采集任务。"""

from __future__ import annotations

from toolbox import webui_localwiki


class LocalWikiService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_localwiki.overview()

    def start_deploy(self, payload: dict) -> dict:
        payload = dict(payload)
        if not payload.get("confirmed"):
            raise ValueError("一键部署会拉取代码并重建站点，请先确认")
        return self._submit("wiki_deploy", payload, webui_localwiki.deploy)

    def start_rebuild(self, payload: dict) -> dict:
        payload = dict(payload)
        if not payload.get("confirmed"):
            raise ValueError("重建会覆盖站点数据，请先确认")
        return self._submit("wiki_rebuild", payload, webui_localwiki.rebuild)

    def start_collect(self, payload: dict) -> dict:
        return self._submit("wiki_collect", dict(payload),
                            webui_localwiki.collect_today)

    def start_service(self) -> dict:
        return webui_localwiki.start_service()

    def stop_service(self) -> dict:
        return webui_localwiki.stop_service()

    def save_password(self, payload: dict) -> dict:
        return webui_localwiki.save_password(dict(payload))

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        job = self.jobs.submit(
            kind,
            lambda task: handler(payload, on_log=task.log,
                                 is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}