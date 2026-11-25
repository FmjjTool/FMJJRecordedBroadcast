"""采集任务 WebUI 适配（L2）：只读账号清单与设置快照。"""

from __future__ import annotations

from toolbox import webui_accounts


class InfoAccountsService:
    def overview(self) -> dict:
        return webui_accounts.overview()
