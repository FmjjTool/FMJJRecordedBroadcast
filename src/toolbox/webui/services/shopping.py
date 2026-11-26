"""购物解析 WebUI 适配（L2）：淘宝 / 抖音 / B站小店解析任务。"""

from __future__ import annotations

from toolbox import webui_shopping


class ShoppingService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def start_parse(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        platform = str(payload.get("platform") or "")
        if platform not in ("taobao", "douyin", "bili"):
            raise ValueError("平台只支持淘宝 / 抖音 / B站小店")
        if not str(payload.get("text") or "").strip():
            raise ValueError("请粘贴链接或分享文案")
        job = self.jobs.submit(
            "shopping_parse",
            lambda task: webui_shopping.parse(
                dict(payload), on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}