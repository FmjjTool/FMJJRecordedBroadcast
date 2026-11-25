"""桌面服务框架的 WebUI 适配（L2）：模块概览、任务管理与系统信息。"""

from __future__ import annotations

from toolbox import webui_modules


class ModuleService:
    def list(self) -> dict:
        return webui_modules.list_modules()

    def detail(self, key: str) -> dict:
        return webui_modules.overview(key)

    def tasks(self) -> dict:
        return webui_modules.task_rows()

    def task_schedule(self, task_id: str) -> dict:
        return webui_modules.task_schedule(task_id)

    def install_task(self, payload: dict) -> dict:
        return webui_modules.install_task(
            payload.get("id"), payload.get("mode"), payload.get("time"),
            payload.get("interval"), bool(payload.get("confirmed")),
            payload.get("extra_times"))

    def uninstall_task(self, payload: dict) -> dict:
        return webui_modules.uninstall_task(
            payload.get("id"), bool(payload.get("confirmed")))

    def system(self) -> dict:
        return webui_modules.system_info()