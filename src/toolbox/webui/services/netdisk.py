"""网络库 WebUI 适配（L2）：备份映射与运行状态只读。"""

from __future__ import annotations

from toolbox import webui_netdisk


class NetdiskService:
    def overview(self) -> dict:
        return webui_netdisk.overview()
