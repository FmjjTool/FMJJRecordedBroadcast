"""购物解析 WebUI 适配（L2）：链接解析、闲鱼搜索与入库下载任务。"""

from __future__ import annotations

from toolbox import webui_shopping
from toolbox.webui.jobs import JobCancelled


class ShoppingService:
    def __init__(self, jobs=None):
        self.jobs = jobs
        from toolbox.webui_shopping_export import ShoppingExports
        from toolbox.webui_taobao import TaobaoExports
        self.exports = ShoppingExports()
        self.taobao_exports = TaobaoExports()

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
                taobao = {}

                def capture_taobao(shop, products):
                    taobao.update(self.taobao_exports.capture(
                        shop, products, is_cancelled=lambda: bool(task.cancel_requested)))

                result = self.exports.parse(
                    dict(payload), on_log=task.log,
                    is_cancelled=lambda: bool(task.cancel_requested),
                    on_taobao_models=capture_taobao)
                if task.cancel_requested:
                    raise JobCancelled()
                result.update(taobao)
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

    def start_taobao_export(self, payload: dict) -> dict:
        return self._start_taobao_export(payload, json_format=False)

    def start_taobao_json_export(self, payload: dict) -> dict:
        return self._start_taobao_export(payload, json_format=True)

    def _start_taobao_export(self, payload: dict, *, json_format: bool) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox.webui_taobao import TaobaoCancelled, TaobaoExportError
        prepared = self.taobao_exports.prepare(payload)
        runner = self.taobao_exports.run_json if json_format else self.taobao_exports.run
        kind = "shopping_taobao_json_export" if json_format else "shopping_taobao_export"

        def run(task):
            artifact = None
            try:
                result, artifact = runner(
                    prepared, task.workspace, on_log=task.log,
                    on_progress=lambda message, value: task.update(message, value),
                    is_cancelled=lambda: bool(task.cancel_requested))
                task.result = result
                if task.cancel_requested:
                    result = dict(result, cancelled=True,
                                  message="任务已取消，已完成的文件保留在导出目录")
                    raise TaobaoCancelled(result)
                if artifact is not None:
                    task.add_file(artifact)
                if task.cancel_requested:
                    raise TaobaoCancelled(dict(result, cancelled=True,
                        message="任务已取消，已完成的文件保留在导出目录"))
                return result
            except Exception as error:
                if artifact is not None:
                    try:
                        artifact.unlink(missing_ok=True)
                    except OSError:
                        pass
                if isinstance(error, (TaobaoCancelled, TaobaoExportError)):
                    if getattr(error, "result", None) is not None:
                        task.result = error.result
                    if isinstance(error, TaobaoCancelled) or task.cancel_requested:
                        raise JobCancelled() from None
                    raise
                if task.cancel_requested:
                    raise JobCancelled() from None
                raise TaobaoExportError("淘宝商品详情导出失败，请稍后重试") from None

        job = self.jobs.submit(kind, run)
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
