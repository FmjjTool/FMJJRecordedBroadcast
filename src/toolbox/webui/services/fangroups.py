"""粉丝群 WebUI 适配（L2）：QQ 群订阅 / 刷新 / 历史，平台查询任务。"""

from __future__ import annotations

from toolbox import webui_fangroups
from toolbox.webui.jobs import JobCancelled


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

    def qq_export(self) -> dict:
        return webui_fangroups.qq_export()

    def start_qq_query(self, payload: dict) -> dict:
        return self._submit("fangroups_qq_query", dict(payload),
                            webui_fangroups.qq_query)

    def start_qq_avatars(self, payload: dict) -> dict:
        return self._submit("fangroups_qq_avatars", dict(payload),
                            webui_fangroups.qq_avatars)

    def start_qq_refresh(self, payload: dict | None = None) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not self.overview()["subscribed_count"]:
            raise ValueError("还没有已订阅的QQ群，请先查询并订阅")

        def run(task):
            result = webui_fangroups.qq_refresh(
                dict(payload or {}), on_log=task.log,
                on_progress=lambda fraction, message: task.update(message, fraction),
                is_cancelled=lambda: bool(task.cancel_requested))
            if result.get("cancelled"):
                task.result = result
                raise JobCancelled("刷新已取消，已完成记录已保留")
            return result

        job = self.jobs.submit("fangroups_qq_refresh", run)
        return {"job": job.id}

    def qq_remove(self, payload: dict) -> dict:
        return webui_fangroups.qq_remove(dict(payload))

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("text") or "").strip() and kind == "fangroups_qq_query":
            raise ValueError("请粘贴群号或群资料分享链接")
        job = self.jobs.submit(
            kind,
            lambda task: handler(payload, on_log=task.log,
                                 is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}
