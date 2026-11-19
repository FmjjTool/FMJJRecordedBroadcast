# -*- coding: utf-8 -*-
"""录制时间轴锚点（P1）：把「绝对墙钟」映射到「视频内时间」。

录制时在每个起录/续录/分段起点记一条锚点 `{pts_ms, wall_ms}`，写成
`<视频主干>_时间轴.json`；之后对齐弹幕（每条消息自带 epoch 毫秒）或写时间码时，
按锚点分段线性换算：

    video_ms = anchor.pts_ms + (wall_ms - anchor.wall_ms)

断流重连会新增一条锚点（pts 接 _output_ts_offset），因此停顿期间墙钟在走、
视频时间不走，映射天然正确；不需要单独记 gap 列表。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Optional

SUFFIX = "_时间轴.json"
VERSION = 1


def path_for(video_path) -> str:
    """视频文件 → 同名时间轴文件（`a.ts` → `a_时间轴.json`）。"""
    root, _ = os.path.splitext(str(video_path))
    return root + SUFFIX


def load(video_path) -> Optional[dict]:
    """读时间轴：直接给视频路径或时间轴路径都行；不存在返回 None。"""
    path = str(video_path)
    if not path.endswith(SUFFIX):
        path = path_for(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("files"):
        return None
    return data


def _atomic_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class TimelineRecorder:
    """一场录制的锚点写入器；每次 add_anchor 立即落盘（崩溃也不丢）。"""

    def __init__(self, video_path, *, platform="", room="", base=""):
        self.video_path = os.path.abspath(str(video_path))
        self.json_path = path_for(self.video_path)
        self.data = {
            "version": VERSION,
            "platform": platform,
            "room": str(room),
            "base": base or os.path.splitext(os.path.basename(self.video_path))[0],
            "created": int(time.time() * 1000),
            "files": [],
        }

    # ---------- 写入 ----------
    def _entry(self, file_path):
        path = os.path.abspath(str(file_path))
        for item in self.data["files"]:
            if os.path.normcase(item["file"]) == os.path.normcase(path):
                return item
        item = {"file": path, "anchors": []}
        self.data["files"].append(item)
        return item

    def add_anchor(self, file_path, wall_ms, pts_ms=0):
        entry = self._entry(file_path)
        entry["anchors"].append({"pts_ms": int(pts_ms), "wall_ms": int(wall_ms)})
        entry["anchors"].sort(key=lambda a: a["pts_ms"])
        self.save()

    def save(self):
        try:
            _atomic_json(self.json_path, self.data)
        except OSError:
            pass       # 时间轴写失败绝不影响录制


# ---------- 换算 ----------
def anchor_wall_ms(tl: dict, file_path=None) -> Optional[int]:
    """第一个锚点的墙钟时间（通常就是首帧时间）。"""
    for entry in tl.get("files") or []:
        if file_path and os.path.normcase(os.path.abspath(str(file_path))) != \
                os.path.normcase(entry.get("file", "")):
            continue
        anchors = entry.get("anchors") or []
        if anchors:
            return int(min(anchors, key=lambda a: a["pts_ms"])["wall_ms"])
    return None


def start_datetime(tl: dict, file_path=None) -> Optional[datetime]:
    wall = anchor_wall_ms(tl, file_path)
    return datetime.fromtimestamp(wall / 1000) if wall else None


def video_ms_for_wall(tl: dict, wall_ms, file_path=None) -> Optional[int]:
    """墙钟 → 该文件内的视频毫秒；没有可用锚点返回 None。"""
    for entry in tl.get("files") or []:
        if file_path and os.path.normcase(os.path.abspath(str(file_path))) != \
                os.path.normcase(entry.get("file", "")):
            continue
        anchors = sorted(entry.get("anchors") or [], key=lambda a: a["pts_ms"])
        if not anchors:
            continue
        chosen = anchors[0]
        for a in anchors:
            if a["wall_ms"] <= wall_ms:
                chosen = a
            else:
                break
        later = [a for a in anchors if a["wall_ms"] > chosen["wall_ms"]]
        elapsed = wall_ms - chosen["wall_ms"]
        if later:
            nxt = min(later, key=lambda a: a["wall_ms"])
            # 两条锚点之间 = 正常播放 + 可能的停顿；最多只推进到下一锚点的 PTS
            return int(chosen["pts_ms"] + min(elapsed, nxt["pts_ms"] - chosen["pts_ms"]))
        return int(chosen["pts_ms"] + elapsed)
    return None


def wall_ms_for_video(tl: dict, video_ms, file_path=None) -> Optional[int]:
    """视频毫秒 → 墙钟（取所在锚点段）。"""
    for entry in tl.get("files") or []:
        if file_path and os.path.normcase(os.path.abspath(str(file_path))) != \
                os.path.normcase(entry.get("file", "")):
            continue
        anchors = sorted(entry.get("anchors") or [], key=lambda a: a["pts_ms"])
        if not anchors:
            continue
        chosen = anchors[0]
        for a in anchors:
            if a["pts_ms"] <= video_ms:
                chosen = a
            else:
                break
        return int(chosen["wall_ms"] + (video_ms - chosen["pts_ms"]))
    return None