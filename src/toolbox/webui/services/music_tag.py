"""标签 WebUI 适配（L2）：标签读取 + 编辑写入 / 扫描 / 歌词任务。"""

from __future__ import annotations

from toolbox import webui_music_tag


class MusicTagService:
    def __init__(self, jobs=None):
        self.jobs = jobs
        from toolbox.webui_music_match import MusicMatchPlans
        self.match_plans = MusicMatchPlans()

    def match_options(self) -> dict:
        return self.match_plans.options()

    def _start_match(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        body = dict(payload)
        job = self.jobs.submit(kind, lambda task: handler(
            body, is_cancelled=lambda: task.cancel_requested,
            on_progress=lambda message, progress: task.update(message, progress)))
        return {"job": job.id}

    def start_match_preview(self, payload: dict) -> dict:
        return self._start_match("tag_match_preview", payload, self.match_plans.preview)

    def start_match_apply(self, payload: dict) -> dict:
        return self._start_match("tag_match_apply", payload, self.match_plans.apply)

    def overview(self, limit: int = 120) -> dict:
        return webui_music_tag.overview(limit)

    def scan(self, payload: dict) -> dict:
        return webui_music_tag.scan(dict(payload))

    def detail(self, rel: str) -> dict:
        return webui_music_tag.detail({"rel": rel})

    def history(self, rel: str) -> dict:
        return webui_music_tag.history({"rel": rel})

    def encoding_preview(self, payload: dict) -> dict:
        return webui_music_tag.encoding_preview(dict(payload))

    def encoding_profiles(self) -> dict:
        return webui_music_tag.encoding_profiles()

    def pictures(self, rel: str) -> dict:
        return webui_music_tag.pictures({"rel": rel})

    def picture(self, rel: str, index: int, revision: str, *, thumbnail: bool = False) -> dict:
        return webui_music_tag.picture({"rel": rel, "index": index, "revision": revision},
                                      thumbnail=thumbnail)

    def _submit(self, kind: str, payload: dict, handler) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        job = self.jobs.submit(kind, lambda task: handler(payload))
        return {"job": job.id}

    def start_write(self, payload: dict) -> dict:
        return self._submit("tag_write", dict(payload), webui_music_tag.write)

    def start_extract_cover(self, payload: dict) -> dict:
        from toolbox.webui.jobs import JobCancelled

        if self.jobs is None:
            raise ValueError("任务服务不可用")
        body = dict(payload)

        def run(task):
            try:
                return webui_music_tag.extract_cover(
                    body, is_cancelled=lambda: task.cancel_requested,
                    on_progress=lambda message, progress: task.update(message, progress))
            except InterruptedError:
                raise JobCancelled() from None

        job = self.jobs.submit("tag_extract_cover", run)
        return {"job": job.id}

    def start_lyric_search(self, payload: dict) -> dict:
        return self._submit("tag_lyric_search", dict(payload),
                            webui_music_tag.lyric_candidates)

    def start_lyric_apply(self, payload: dict) -> dict:
        return self._submit("tag_lyric_apply", dict(payload),
                            webui_music_tag.lyric_apply)
