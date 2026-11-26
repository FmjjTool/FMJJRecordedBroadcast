"""桌面服务框架的 WebUI 适配（L2）：模块概览、任务管理与系统信息。"""

from __future__ import annotations

from toolbox import webui_deps, webui_modules, webui_settings


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
            payload.get("extra_times"), payload.get("collection"))

    def uninstall_task(self, payload: dict) -> dict:
        return webui_modules.uninstall_task(
            payload.get("id"), bool(payload.get("confirmed")))

    def system(self) -> dict:
        return webui_modules.system_info()

    def system_settings(self) -> dict:
        return webui_settings.system_settings()

    def save_system_dirs(self, payload: dict) -> dict:
        return webui_settings.save_system_dirs(
            payload.get("work_dir"), payload.get("export_dir"),
            payload.get("migrate_target_dir"),
            bool(payload.get("confirmed")))

    def save_system_theme(self, payload: dict) -> dict:
        return webui_settings.save_system_theme(payload.get("theme"))

    def dependencies(self) -> dict:
        return webui_deps.dependency_status()
