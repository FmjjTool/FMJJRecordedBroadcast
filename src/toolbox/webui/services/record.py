"""录制页 WebUI 适配（L2）：只读概览 + 桌面控制桥代理 + 新增主播。

只调用 L3（`live_record_status` / `webui_record_bridge`）与桌面侧的录制
控制桥，不直接读写 `config/live_record.json`——录制配置的唯一写入方仍是
桌面进程，避免两边同时写导致主播列表被覆盖。
"""

from __future__ import annotations

from toolbox import (live_record, live_record_status, webui_record_bridge,
                     webui_recording_repair)
from toolbox.webui.jobs import JobCancelled


class RecordService:
    def __init__(self, jobs=None):
        self._jobs = jobs

    def repair_sources(self, platform: str) -> dict:
        return webui_recording_repair.repair_sources(platform)

    def start_repair(self, payload: dict) -> dict:
        if self._jobs is None:
            raise ValueError("任务服务不可用")
        platform, rel = webui_recording_repair.validate_request(payload)
        job = self._jobs.submit(
            "record_repair", lambda task: self._repair_job(task, platform, rel))
        return {"job": job.id}

    def _repair_job(self, job, platform: str, rel: str) -> dict:
        def progress(value):
            # TS 只有开始 / 结束两个已知端点，其间保留不确定进度。
            job.update("修复已完成" if value >= 1 else "修复中…",
                       value if value > 0 else None)

        try:
            result = webui_recording_repair.repair_job(
                platform, rel, job.workspace, on_progress=progress,
                cancel=lambda: bool(job.cancel_requested))
        except webui_recording_repair.RepairCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        job.add_file(job.workspace / result["output"])
        return result

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
        return _result(webui_record_bridge.action(platform, room, action))

    def inspect(self, payload: dict) -> dict:
        """只读识别房间：房间号 / 主播名 / 是否在播 / 可用清晰度。"""
        platform, room = _platform_room(payload)
        info = live_record.fetch_info(platform, room)
        if not isinstance(info, dict):
            raise ValueError("识别失败：接口没有返回信息")
        return {
            "platform": platform,
            "room": room,
            "name": info.get("name") or "",
            "live": bool(info.get("live")),
            "online": int(info.get("online") or 0),
            "title": info.get("title") or "",
            "qualities": list((info.get("qualities") or {}).keys()),
            "stream": bool(info.get("stream")),
            "error": info.get("error") or "",
        }

    def add_anchor(self, payload: dict) -> dict:
        """新增主播：经桌面控制桥写入（桌面未运行则直接报错）。"""
        platform, room = _platform_room(payload)
        extra = {
            "name": str(payload.get("name") or "").strip(),
            "remark": str(payload.get("remark") or "").strip(),
            "monitoring": payload.get("monitoring", True) is not False,
            "want_record": bool(payload.get("want_record")),
            "quality": str(payload.get("quality") or "").strip(),
            "quality_label": str(payload.get("quality_label") or "").strip(),
            "stop_minutes": _int(payload.get("stop_minutes"), 0, 0, 1440),
        }
        return _result(webui_record_bridge.action(platform, room,
                                                  "add_anchor", extra))

    def import_anchors(self, payload: dict) -> dict:
        """批量导入：每行一个房间号或直播间链接。"""
        platform = str(payload.get("platform") or "").strip()
        if platform not in live_record.PLATFORMS:
            raise ValueError("未知平台")
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("请填写要导入的房间号或链接")
        if len(text.splitlines()) > 500:
            raise ValueError("单次最多导入 500 行")
        return _result(webui_record_bridge.action(platform, "", "import_anchors",
                                                  {"text": text}))


def _platform_room(payload: dict) -> tuple[str, str]:
    platform = str(payload.get("platform") or "").strip()
    if platform not in live_record.PLATFORMS:
        raise ValueError("未知平台")
    from toolbox.live_room_resolver import parse_room_input

    room = parse_room_input(platform, payload.get("room"))
    if not room:
        raise ValueError("无法识别房间号：请填数字房间号或直播间链接")
    return platform, str(room)


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _result(result) -> dict:
    if not isinstance(result, dict) or not result.get("ok"):
        message = "操作失败"
        if isinstance(result, dict):
            message = str(result.get("message") or message)
        raise ValueError(message)
    return result
