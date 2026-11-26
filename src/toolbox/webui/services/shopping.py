"""购物解析 WebUI 适配（L2）：链接解析、闲鱼搜索与入库下载任务。"""

from __future__ import annotations

from toolbox import webui_shopping


class ShoppingService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def options(self) -> dict:
        return webui_shopping.options()

    def start_parse(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_payload(payload)
        job = self.jobs.submit(
            "shopping_parse",
            lambda task: webui_shopping.parse(
                dict(payload), on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_xianyu_search(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_xianyu_search(payload)
        job = self.jobs.submit(
            "shopping_xianyu_search",
            lambda task: webui_shopping.search_xianyu(
                payload, on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_xianyu_save(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_xianyu_save(payload)
        job = self.jobs.submit(
            "shopping_xianyu_save",
            lambda task: webui_shopping.save_xianyu(
                payload, on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}
