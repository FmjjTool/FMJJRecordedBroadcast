"""录制页 WebUI 适配（L2）：只读概览 + 桌面控制桥代理。

只调用 L3（`live_record_status` / `webui_record_bridge`），不直接读配置文件
或凭据。浏览器只接触 WebUI 会话，不接触桥令牌。
"""

from __future__ import annotations

from toolbox import live_record_status, webui_record_bridge


class RecordService:
    def overview(self) -> dict:
        return live_record_status.overview()

    def bridge_status(self) -> dict:
        return webui_record_bridge.status()

    def control(self, payload: dict) -> dict:
        platform = str(payload.get("platform") or "").strip()
        room = str(payload.get("room") or "").strip()
        action = str(payload.get("action") or "").strip()
        if not platform or not room:
            raise ValueError("缺少平台或房间号")
        if action not in webui_record_bridge.ACTIONS:
            raise ValueError("不支持的操作")
        result = webui_record_bridge.action(platform, room, action)
        if not isinstance(result, dict) or not result.get("ok"):
            message = "操作失败"
            if isinstance(result, dict):
                message = str(result.get("message") or message)
            raise ValueError(message)
        return result