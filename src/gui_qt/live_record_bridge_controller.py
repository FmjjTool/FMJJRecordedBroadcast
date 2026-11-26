"""桌面录制控制桥的 Qt 控制器：在主线程打开/操作录制窗口。

由 `main.py` 创建并注入 `toolbox.live_record_bridge.DesktopBridge`。
HTTP 线程收到请求后经 `common.post_to_main` 切主线程，等待结果返回。
只暴露房间号级操作，不读取或返回任何凭据。
"""

from __future__ import annotations

import threading

from toolbox import live_record


class DesktopRecordController:
    def __init__(self, app):
        self._app = app

    def status(self) -> dict:
        win = getattr(self._app, "live_record_win", None)
        if win is None:
            return {"open": False, "platforms": _config_platforms(),
                    "note": "录制窗口未打开；显示已保存的配置状态。"}
        platforms = []
        for key, pane in win.panes.items():
            info = live_record.PLATFORMS.get(key) or {}
            anchors = []
            for anchor in pane.manager.anchors:
                anchors.append({
                    "room": str(anchor.room or ""),
                    "name": anchor.name or "",
                    "remark": anchor.remark or "",
                    "monitoring": bool(anchor.monitoring),
                    "recording": bool(anchor.recording),
                    "want_record": bool(getattr(anchor, "_want_record", False)),
                })
            platforms.append({"key": key,
                              "name": info.get("name") or key,
                              "anchors": anchors})
        return {"open": True, "platforms": platforms, "note": ""}

    def action(self, platform: str, room: str, action: str,
               extra: dict | None = None) -> dict:
        result: dict = {"ok": False, "message": "操作失败"}
        done = threading.Event()

        def run():
            try:
                result.update(self._do_action(platform, room, action,
                                              extra or {}))
            except Exception as exc:  # noqa: BLE001 - 回传桌面错误
                result.update(ok=False, message=str(exc)[:300])
            finally:
                done.set()

        from gui_qt import common
        common.post_to_main(run)
        if not done.wait(timeout=20):
            return {"ok": False, "message": "桌面主线程响应超时"}
        return result

    def _do_action(self, platform: str, room: str, action: str,
                   extra: dict | None = None) -> dict:
        from gui_qt.live_record_window import open_live_record_window

        extra = extra or {}
        win = open_live_record_window(self._app, platform=platform, room=room)
        pane = win.panes.get(platform)
        if pane is None:
            return {"ok": False, "message": "平台不存在"}
        manager = pane.manager
        if action == "import_anchors":
            return _import_anchors(pane, manager, platform, extra)
        anchor = next((item for item in manager.anchors
                       if str(item.room) == str(room)), None)
        if action == "add_anchor":
            if anchor is not None:
                return {"ok": False, "message": "该房间已在列表中"}
            return _add_anchor(pane, manager, platform, room, extra)
        if anchor is None:
            return {"ok": False, "message": "主播不存在"}
        if action == "start_monitor":
            anchor.monitoring = True
            if not manager.running:
                manager.start_monitor()
            message = "已开启监控"
        elif action == "stop_monitor":
            anchor.monitoring = False
            if not any(item.monitoring for item in manager.anchors):
                manager.stop_monitor()
            message = "已关闭监控"
        elif action == "start_record":
            anchor._want_record = True
            ok, text = manager.start_record(anchor)
            if not ok:
                return {"ok": False, "message": text or "无法开始录制"}
            message = "已开始录制"
        elif action == "stop_record":
            anchor._want_record = False
            manager.stop_record(anchor)
            message = "已请求停止录制"
        else:
            return {"ok": False, "message": "不支持的操作"}
        try:
            pane.model.refresh_all()
            pane.save()
        except Exception:
            pass
        return {"ok": True, "message": message}


def _add_anchor(pane, manager, platform: str, room: str,
                extra: dict) -> dict:
    """新增一个主播：与桌面「添加主播」同样的落库路径（追加 + 刷新 + 保存）。"""
    anchor = live_record.Anchor(
        platform=platform, room=room,
        name=str(extra.get("name") or ""),
        monitoring=bool(extra.get("monitoring", True)),
        remark=str(extra.get("remark") or ""),
        quality=str(extra.get("quality") or ""),
        quality_label=str(extra.get("quality_label") or ""),
        stop_minutes=int(extra.get("stop_minutes") or 0))
    anchor._want_record = bool(extra.get("want_record"))
    manager.anchors.append(anchor)
    if anchor.monitoring and not manager.running:
        try:
            manager.start_monitor()
        except Exception:  # noqa: BLE001 - 监控起不来不影响已添加
            pass
    if anchor._want_record:
        try:
            manager.start_record(anchor)
        except Exception:  # noqa: BLE001
            pass
    try:
        pane.model.refresh_all()
        pane.save()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "room": room,
            "message": "已添加：%s（%s）" % (anchor.name or room, room)}


def _import_anchors(pane, manager, platform: str, extra: dict) -> dict:
    """批量导入：每行一个房间号或直播间链接，重复的跳过。"""
    from toolbox.live_room_resolver import parse_room_input

    lines = [line.strip() for line in str(extra.get("text") or "").splitlines()
             if line.strip()]
    if not lines:
        return {"ok": False, "message": "请填写要导入的房间号或链接"}
    existing = {str(item.room) for item in manager.anchors}
    added = skipped = 0
    for line in lines:
        room = parse_room_input(platform, line)
        if not room or room in existing:
            skipped += 1
            continue
        anchor = live_record.Anchor(platform=platform, room=room,
                                    monitoring=True)
        anchor._want_record = False
        manager.anchors.append(anchor)
        existing.add(room)
        added += 1
    if not added:
        return {"ok": False, "added": 0, "skipped": skipped,
                "message": "没有新增（全部重复或无法识别）"}
    if not manager.running:
        try:
            manager.start_monitor()
        except Exception:  # noqa: BLE001
            pass
    try:
        pane.model.refresh_all()
        pane.save()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "added": added, "skipped": skipped,
            "message": "新增 %d 个房间%s" % (
                added, ("，跳过 %d" % skipped) if skipped else "")}


def _config_platforms() -> list[dict]:
    """录制窗口未打开时，从已保存状态返回主播配置（只读）。"""
    try:
        state = live_record.load_state() or {}
    except Exception:
        return []
    rows = []
    for key, data in (state.get("platforms") or {}).items():
        info = live_record.PLATFORMS.get(key) or {}
        anchors = []
        for item in (data or {}).get("anchors") or []:
            item = item or {}
            anchors.append({
                "room": str(item.get("room") or ""),
                "name": item.get("name") or "",
                "remark": item.get("remark") or "",
                "monitoring": bool(item.get("monitoring")),
                "recording": False,
                "want_record": bool(item.get("want_record")
                                    or item.get("recording")),
            })
        if anchors:
            rows.append({"key": key, "name": info.get("name") or key,
                         "anchors": anchors})
    return rows