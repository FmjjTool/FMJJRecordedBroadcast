"""资料站 WebUI 适配（L2）：部署状态与访问统计只读。"""

from __future__ import annotations

from toolbox import webui_localwiki


class LocalWikiService:
    def overview(self) -> dict:
        return webui_localwiki.overview()
