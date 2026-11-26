"""Jobs bridge for the optional CPU/GPU inference component use cases."""

from toolbox import webui_local_llm
from toolbox.livestream_summary.client import CancelledError
from toolbox.webui.jobs import JobCancelled


class LocalLLMService:
    def __init__(self, jobs):
        self.jobs = jobs

    def overview(self):
        return webui_local_llm.overview()

    def sources(self):
        return webui_local_llm.model_sources()

    def start(self, action, payload):
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        prepared = webui_local_llm.prepare_action(action, payload)

        def work(task):
            def progress(percent, message):
                task.update(message=message, progress=percent / 100.0)
            try:
                return webui_local_llm.run_action(
                    action, prepared, on_progress=progress,
                    is_cancelled=lambda: bool(task.cancel_requested))
            except CancelledError as exc:
                raise JobCancelled(str(exc)) from exc
        return {"job": self.jobs.submit("local_llm_" + action, work).id}
