"""桌面服务只读框架的 WebUI 适配（L2）：只调用 L3 概览。"""

from __future__ import annotations

from toolbox import webui_modules


class ModuleService:
    def list(self) -> dict:
        return webui_modules.list_modules()

    def detail(self, key: str) -> dict:
        return webui_modules.overview(key)

    def tasks(self) -> dict:
        return webui_modules.task_rows()

    def system(self) -> dict:
        return webui_modules.system_info()