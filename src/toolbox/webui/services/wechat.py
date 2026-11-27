"""公众号资料服务：共享用例的 Web 任务编排。"""

from __future__ import annotations

from toolbox import webui_wechat
from toolbox.webui.jobs import JobCancelled


class WechatService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def schema(self):
        return webui_wechat.schema()

    def accounts(self, payload):
        return self._call(webui_wechat.list_accounts, payload)

    def articles(self, payload):
        return self._call(webui_wechat.list_articles, payload)

    def save_account(self, payload):
        return self._call(webui_wechat.save_account, payload)

    def index_csv(self, payload):
        return self._call(webui_wechat.index_csv, payload)

    @staticmethod
    def _call(fn, payload):
        try:
            return fn(dict(payload or {}))
        except Exception as exc:
            raise ValueError(WechatService._error(exc)) from None

    @staticmethod
    def _error(exc):
        if isinstance(exc, webui_wechat.WebWechatError):
            return str(exc)
        return webui_wechat.safe_message(webui_wechat._library().user_error(exc))

    def start(self, action, payload):
        if action not in ("collect", "sync", "export", "retry"):
            raise ValueError("不支持的操作")
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        normalized = webui_wechat.normalize(action, dict(payload or {}))

        def worker(task):
            def progress(done, total, text):
                task.update(webui_wechat.safe_message(text, "处理中"),
                            float(done) / total if total else None)

            try:
                if task.cancel_requested:
                    raise JobCancelled()
                result = webui_wechat.run(
                    action, normalized, on_progress=progress,
                    on_log=lambda text: task.log(webui_wechat.safe_message(text, "处理中")),
                    is_cancelled=lambda: bool(task.cancel_requested))
                if task.cancel_requested:
                    raise JobCancelled()
                return result
            except JobCancelled:
                raise
            except Exception as exc:
                raise ValueError(self._error(exc)) from None

        return {"job": self.jobs.submit("wechat_" + action, worker).id}
