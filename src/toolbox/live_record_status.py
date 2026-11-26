"""直播录制只读概览（L3）：读取录制配置中的主播、监控与录制意图。

只读：不创建 RecordManager、不启动监控或录制。实时录制状态、在线人数和
运行日志仍在桌面录制页；WebUI 只展示已保存的配置。
"""

from __future__ import annotations

from toolbox import live_record


def overview() -> dict:
    state = live_record.load_state() or {}
    platforms_cfg = state.get("platforms") or {}
    order = list(live_record.PLATFORMS)
    configured: dict[str, list] = {}
    anchors_total = 0
    monitoring = 0
    want_record = 0
    for key, data in platforms_cfg.items():
        anchors = []
        for item in (data or {}).get("anchors") or []:
            item = item or {}
            anchor = {
                "platform": key,
                "platform_name": (live_record.PLATFORMS.get(key) or {}).get("name") or key,
                "room": str(item.get("room") or ""),
                "name": item.get("name") or "",
                "title": item.get("title") or "",
                "remark": item.get("remark") or "",
                "monitoring": bool(item.get("monitoring")),
                "want_record": bool(item.get("want_record",
                                             item.get("recording"))),
                "quality": (item.get("quality_label")
                            or item.get("quality") or ""),
                "content": int(item.get("content") or 0),
                "account_id": item.get("account_id") or "",
            }
            anchors.append(anchor)
            anchors_total += 1
            monitoring += int(anchor["monitoring"])
            want_record += int(anchor["want_record"])
        configured[key] = anchors
    # 六个平台都列出来（含 0 个主播的），Web 端平台切换与桌面一致
    keys = order + sorted(set(configured) - set(order))
    platform_rows = []
    for key in keys:
        info = live_record.PLATFORMS.get(key) or {}
        platform_rows.append({
            "key": key,
            "name": info.get("name") or key,
            "anchors": configured.get(key, []),
        })
    return {
        "read_only": True,
        "platforms": platform_rows,
        "summary": {
            "platforms": len([row for row in platform_rows if row["anchors"]]),
            "anchors": anchors_total,
            "monitoring": monitoring,
            "want_record": want_record,
        },
        "note": "实时录制状态、在线人数与运行日志请在桌面端录制页查看；"
                "平台与主播清单来自已保存配置，最多每秒刷新一次。",
    }