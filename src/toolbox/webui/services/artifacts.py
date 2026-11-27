"""采集工具 WebUI 适配（L2）：本地产物列表、图片预览与文件下载。"""

from __future__ import annotations

from toolbox import webui_artifacts


class ArtifactService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def kinds(self) -> list[dict]:
        return webui_artifacts.kind_names()

    def start_comments(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox import webui_collect_tools
        webui_collect_tools.ensure_comments(dict(payload))
        job = self.jobs.submit(
            "comments_fetch",
            lambda task: webui_collect_tools.comments_run(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_wechat(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox import webui_collect_tools, webui_wechat, wechat_library
        from toolbox.webui.jobs import JobCancelled
        webui_collect_tools.ensure_wechat(dict(payload))

        def worker(task):
            def progress(done, total, text):
                task.update(webui_wechat.safe_message(text, "处理中"),
                            float(done) / total if total else None)

            try:
                if task.cancel_requested:
                    raise JobCancelled()
                result = webui_collect_tools.wechat_run(
                    dict(payload), on_progress=progress,
                    on_log=lambda text: task.log(webui_wechat.safe_message(text, "处理中")),
                    is_cancelled=lambda: bool(task.cancel_requested))
                if task.cancel_requested:
                    raise JobCancelled()
                return {"ok": int(result.get("ok") or 0),
                        "failed": int(result.get("failed") or 0),
                        "summary": webui_wechat.safe_message(result.get("summary"), "导出完成")}
            except (JobCancelled, webui_collect_tools.CollectToolCancelled):
                raise JobCancelled() from None
            except Exception as exc:
                if task.cancel_requested:
                    raise JobCancelled() from None
                raise ValueError(webui_wechat.safe_message(
                    wechat_library.user_error(exc))) from None

        job = self.jobs.submit("wechat_parse", worker)
        return {"job": job.id}

    def bili_emote_search(self, payload: dict) -> dict:
        from toolbox import webui_bili_tools
        return webui_bili_tools.emote_search(dict(payload))

    def start_bili_emote_download(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not (payload.get("ids") or []):
            raise ValueError("请先勾选要下载的表情包")
        from toolbox import webui_bili_tools
        job = self.jobs.submit(
            "bili_emote_download",
            lambda task: webui_bili_tools.emote_download(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_bili_garb_download(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("item_id") or "").strip():
            raise ValueError("请填写装扮 ID 或链接")
        from toolbox import webui_bili_tools
        job = self.jobs.submit(
            "bili_garb_download",
            lambda task: webui_bili_tools.garb_download(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_bili_up_download(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("uid") or "").strip():
            raise ValueError("请填写 UP 主 UID 或空间链接")
        from toolbox import webui_bili_tools
        job = self.jobs.submit(
            "bili_up_download",
            lambda task: webui_bili_tools.up_download(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def douyin_emoji_fetch(self, payload: dict) -> dict:
        from toolbox import webui_collect_tools
        return webui_collect_tools.douyin_emoji_fetch(dict(payload))

    def start_douyin_emoji_download(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        if not str(payload.get("room") or "").strip():
            raise ValueError("请填写直播间号或链接")
        from toolbox import webui_collect_tools
        job = self.jobs.submit(
            "douyin_emoji_download",
            lambda task: webui_collect_tools.douyin_emoji_download(
                dict(payload), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    @staticmethod
    def _progress(task):
        def progress(done, total, text) -> None:
            fraction = (float(done) / total) if total else None
            task.update(str(text), fraction)
        return progress

    def list(self, kind: str, group_limit: int = 200,
             file_limit: int = 200) -> dict:
        return webui_artifacts.list_artifacts(kind, group_limit, file_limit)

    def file(self, kind: str, rel: str, preview: bool = False):
        return webui_artifacts.resolve_file(kind, rel, preview=preview)
