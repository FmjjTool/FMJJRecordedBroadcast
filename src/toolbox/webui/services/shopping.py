"""购物解析 WebUI 适配（L2）：链接解析、闲鱼搜索与入库下载任务。"""

from __future__ import annotations

from toolbox import webui_shopping
from toolbox.webui.jobs import JobCancelled


class ShoppingService:
    def __init__(self, jobs=None):
        self.jobs = jobs
        from toolbox.webui_shopping_export import ShoppingExports
        self.exports = ShoppingExports()

    def options(self) -> dict:
        return webui_shopping.options()

    @staticmethod
    def _run_xianyu(task, operation, payload):
        if task.cancel_requested:
            raise JobCancelled()
        try:
            return operation(payload, on_log=task.log,
                             is_cancelled=lambda: bool(task.cancel_requested))
        except Exception:
            if task.cancel_requested:
                raise JobCancelled() from None
            raise

    def start_parse(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_payload(payload)
        def run(task):
            if task.cancel_requested:
                raise JobCancelled()
            try:
                result = self.exports.parse(
                    dict(payload), on_log=task.log,
                    is_cancelled=lambda: bool(task.cancel_requested))
                if task.cancel_requested:
                    raise JobCancelled()
                return result
            except Exception:
                if task.cancel_requested:
                    raise JobCancelled() from None
                raise

        job = self.jobs.submit("shopping_parse", run)
        return {"job": job.id}

    def start_export(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        body = self.exports.validate_payload(payload)

        def run(task):
            if task.cancel_requested:
                raise JobCancelled()
            artifact = None
            try:
                result, artifact = self.exports.export(
                    body, task.workspace,
                    is_cancelled=lambda: bool(task.cancel_requested),
                    on_progress=lambda message, value: task.update(message, value))
                if task.cancel_requested:
                    raise JobCancelled()
                task.add_file(artifact)
                if task.cancel_requested:
                    raise JobCancelled()
                return result
            except Exception as error:
                if artifact is not None:
                    try:
                        artifact.unlink(missing_ok=True)
                    except OSError:
                        pass
                if task.cancel_requested or isinstance(error, InterruptedError):
                    raise JobCancelled() from None
                from toolbox.webui_shopping_export import ShoppingExportError
                if isinstance(error, ShoppingExportError):
                    raise
                raise ShoppingExportError("商品 Excel 导出失败，请重新解析后重试") from None

        job = self.jobs.submit("shopping_export", run)
        return {"job": job.id}

    def start_xianyu_search(self, payload: dict) -> dict:
        """每次提交一个已校验的搜索页；失败与取消不推进客户端页码。"""
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_xianyu_search(payload)
        job = self.jobs.submit(
            "shopping_xianyu_search",
            lambda task: self._run_xianyu(
                task, webui_shopping.search_xianyu, payload))
        return {"job": job.id, "page": payload["page"]}

    def start_xianyu_save(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        payload = webui_shopping.prepare_xianyu_save(payload)
        job = self.jobs.submit(
            "shopping_xianyu_save",
            lambda task: self._run_xianyu(
                task, webui_shopping.save_xianyu, payload))
        return {"job": job.id}
