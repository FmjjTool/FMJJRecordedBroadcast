"""粉丝群 WebUI 适配（L2）：QQ 群列表 / 历史 / 头像，平台查询任务。"""

from __future__ import annotations

from toolbox import webui_fangroups


class FanGroupService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_fangroups.overview()

    def history(self, group_no: str, limit: int = 200) -> dict:
        return webui_fangroups.history(group_no, limit)

    def avatar(self, group_no: str):
        return webui_fangroups.avatar_file(group_no)

    def start_query(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        platform = str(payload.get("platform") or "")
        if platform not in ("douyin", "weibo", "bilibili"):
            raise ValueError("平台只支持抖音 / 微博 / B站")
        if not str(payload.get("text") or "").strip():
            raise ValueError("请粘贴主页链接或输入 UID")
        job = self.jobs.submit(
            "fangroups_query",
            lambda task: webui_fangroups.query(
                dict(payload), on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}