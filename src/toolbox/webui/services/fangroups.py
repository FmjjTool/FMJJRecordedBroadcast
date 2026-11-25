"""粉丝群（QQ 群）WebUI 适配（L2）：只读列表、人数历史与头像。"""

from __future__ import annotations

from toolbox import webui_fangroups


class FanGroupService:
    def overview(self) -> dict:
        return webui_fangroups.overview()

    def history(self, group_no: str, limit: int = 200) -> dict:
        return webui_fangroups.history(group_no, limit)

    def avatar(self, group_no: str):
        return webui_fangroups.avatar_file(group_no)
