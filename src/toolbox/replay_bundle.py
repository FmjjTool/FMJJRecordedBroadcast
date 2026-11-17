"""直播回放包读写（L3）：导出、合并、解析 zip/json。不依赖 Qt。"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime

CAST_TXT_TYPES = (
    ("WebcastChatMessage", "弹幕"),
    ("WebcastGiftMessage", "礼物"),
    ("WebcastSocialMessage", "关注"),
    ("WebcastLikeMessage", "点赞"),
    ("WebcastMemberMessage", "进入"),
)

_tmp_dirs: list[str] = []


def cleanup_tmp_dirs():
    """清理解析回放包时解压出的临时视频目录。"""
    for path in list(_tmp_dirs):
        shutil.rmtree(path, ignore_errors=True)
        try:
            _tmp_dirs.remove(path)
        except ValueError:
            pass


atexit.register(cleanup_tmp_dirs)


def remove_tmp_dir(path):
    """停止回放时立即删除解压目录，并从退出清理列表移除。"""
    shutil.rmtree(path, ignore_errors=True)
    try:
        _tmp_dirs.remove(path)
    except ValueError:
        pass


def cast_txt_line(c: dict):
    """一条弹幕 → dycast TXT 行（HH:mm:ss.SSS|[昵称]：内容）。"""
    ts = c.get("timestamp") or 0
    time_str = (datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S.%f")[:-3]
                if ts else "??:??:??.???")
    name = (c.get("user") or {}).get("name") or "unknown"
    if c.get("gift"):
        text = f"送出了 {c['gift'].get('name') or '礼物'} × {c['gift'].get('count') or 1}"
    else:
        text = c.get("content") or ""
    return f"{time_str}|[{name}]：{text}" if text else None


def _write_zip(out_path, bundle, casts, cover=None, video_path=None, rec_meta=None):
    meta_json = json.dumps(rec_meta, ensure_ascii=False, indent=1) if rec_meta else None
    txt_files = []
    for method, label in CAST_TXT_TYPES:
        lines = [line for c in casts if c.get("method") == method
                 for line in [cast_txt_line(c)] if line]
        if lines:
            txt_files.append((f"{label}.txt", "\n".join(lines)))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("data.json", json.dumps(bundle, ensure_ascii=False, indent=1))
        z.writestr("全量弹幕.json", json.dumps(casts, ensure_ascii=False, indent=1))
        for name, content in txt_files:
            z.writestr(name, content)
        if cover:
            z.writestr("cover.jpg", cover)
        if video_path:
            z.write(video_path, "recording.flv")
            if meta_json:
                z.writestr("recording_meta.json", meta_json)


def write_replay_zip(out_path, casts, *, room, anchor, title="", like_count=0,
                     cover_bytes=None, video_path=None, rec_meta=None) -> str:
    """写 dycast 全量格式回放包；返回输出路径。"""
    has_video = bool(video_path and os.path.exists(video_path)
                     and os.path.getsize(video_path) > 1024)
    if has_video and not rec_meta:
        rec_meta = {"startTimestamp": casts[0].get("timestamp"), "file": "recording.flv"}
    bundle = {
        "_bundle": True,
        "exportTime": datetime.now().isoformat(),
        "roomId": room,
        "nickname": anchor,
        "avatar": "",
        "cover": "",
        "title": title,
        "likeCount": like_count,
        "casts": casts,
    }
    _write_zip(out_path, bundle, casts, cover=cover_bytes,
               video_path=video_path if has_video else None, rec_meta=rec_meta)
    return str(out_path)


def merge_replay_zips(paths, out_dir):
    """读取多个回放包 → 按时间排序去重 → 写一个新的合并包。"""
    all_casts = []
    bundles = []
    cover = None
    skipped = []
    for path in paths:
        try:
            if path.lower().endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                casts = data.get("casts") if isinstance(data, dict) else data
                if isinstance(data, dict) and data.get("_bundle"):
                    bundles.append(data)
            else:
                with zipfile.ZipFile(path) as z:
                    names = z.namelist()
                    casts = None
                    if "全量弹幕.json" in names:
                        casts = json.loads(z.read("全量弹幕.json").decode("utf-8"))
                    if "data.json" in names:
                        data = json.loads(z.read("data.json").decode("utf-8"))
                        if not casts:
                            casts = data.get("casts") or []
                        if data.get("_bundle"):
                            bundles.append(data)
                    if cover is None and "cover.jpg" in names:
                        cover = z.read("cover.jpg")
            if not casts:
                skipped.append(os.path.basename(path))
                continue
            all_casts.extend(c for c in casts
                             if isinstance(c, dict) and c.get("timestamp"))
        except Exception:
            skipped.append(os.path.basename(path))
    if len(all_casts) < 2:
        raise ValueError("no_data")

    all_casts.sort(key=lambda c: c.get("timestamp") or 0)
    seen = set()
    merged = []
    for c in all_casts:
        key = (c.get("timestamp"), c.get("method"),
               (c.get("user") or {}).get("name"), c.get("content"),
               (c.get("gift") or {}).get("name"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)

    bundles.sort(key=lambda b: b.get("exportTime") or "")
    first = bundles[0] if bundles else {}
    anchor = (first.get("nickname") or "未知主播").strip() or "未知主播"
    room = str(first.get("roomId") or "room").strip() or "room"
    like = max((_as_int(b.get("likeCount")) for b in bundles), default=0)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = out_dir / f"{anchor}-{room}-合并-{ts}-{len(merged)}条.zip"
    bundle = {
        "_bundle": True,
        "exportTime": datetime.now().isoformat(),
        "mergedFrom": [os.path.basename(p) for p in paths],
        "roomId": room,
        "nickname": anchor,
        "avatar": first.get("avatar") or "",
        "cover": "",
        "title": first.get("title") or "",
        "likeCount": like,
        "casts": merged,
    }
    _write_zip(out, bundle, merged, cover=cover)
    return {"out": str(out), "total": len(all_casts), "merged": len(merged),
            "skipped": skipped, "files": len(paths)}


def _as_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def parse_replay_file(path):
    """解析回放包 → dict(casts/bundle/video/rec_meta/zip_dir/cover)，失败返回 None。"""
    try:
        if path.lower().endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            casts = (bundle or {}).get("casts") if isinstance(bundle, dict) else None
            if isinstance(casts, list) and casts:
                return {"casts": casts, "bundle": bundle}
            return None
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "data.json" not in names:
                return None
            bundle = json.loads(z.read("data.json").decode("utf-8"))
            casts = bundle.get("casts") if isinstance(bundle, dict) else None
            if not isinstance(casts, list) or not casts:
                return None
            out = {"casts": casts, "bundle": bundle}
            if "recording_meta.json" in names:
                try:
                    out["rec_meta"] = json.loads(
                        z.read("recording_meta.json").decode("utf-8"))
                except Exception:
                    pass
            for name in names:
                if name.lower().startswith("cover."):
                    out["cover"] = z.read(name)
                    break
            if "recording.flv" in names:
                tmp = tempfile.mkdtemp(prefix="replay_")
                vpath = os.path.join(tmp, "recording.flv")
                with z.open("recording.flv") as src, open(vpath, "wb") as f:
                    shutil.copyfileobj(src, f, 1024 * 1024)
                out["video"] = vpath
                out["zip_dir"] = tmp
                _tmp_dirs.append(tmp)
            return out
    except Exception:
        return None
