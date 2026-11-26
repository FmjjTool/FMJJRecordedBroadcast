"""抖音指数采集与既有资产指标回写用例（L3，Qt-free）。

采集快照是此次回写的来源；collected_store 维护 collected 主数据，
collected_projection / increment_db 维护既有派生归档，exporters.writer
维护既有资产导出 JSON，asset_index 只失效可重建缓存。本服务不直接写库或文件，
不插入作品，不改变各存储 schema、主键、内容、媒体和原有获取时间。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


class FetchCancelled(Exception):
    """在安全检查点取消；已经保存的快照和已提交的回写保持原状。"""


_COUNT_KEYS = {
    "like_count": ("like_cnt",),
    "comment_count": ("coment_cnt", "comment_cnt"),
    "share_count": ("share_cnt",),
    "favorite_count": ("follow_cnt", "collect_cnt", "favorite_cnt", "collect_count"),
}


def _count(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    scale = 1
    for suffix, factor in (("亿", 100000000), ("万", 10000), ("w", 10000), ("k", 1000)):
        if text.lower().endswith(suffix):
            text, scale = text[:-len(suffix)], factor
            break
    try:
        number = Decimal(text) * scale
        if number.is_finite() and number >= 0 and number == number.to_integral_value():
            # 所有投影都必须可存入 SQLite INTEGER。
            return int(number) if number <= 9223372036854775807 else None
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return None


def build_metric_patches(snapshot: dict) -> tuple[dict, int]:
    """平台响应 → 一份按精确视频 ID 去重的指标补丁；不猜测缺失计数。"""
    if not isinstance(snapshot, dict):
        return {}, 1
    videos = snapshot.get("videos") or []
    if not isinstance(videos, list):
        return {}, 1
    meta = snapshot.get("meta") or {}
    if not isinstance(meta, dict):
        return {}, max(1, len(videos))
    fetched_at = str(meta.get("fetched_at") or "").strip()
    try:
        datetime.fromisoformat(fetched_at)
    except ValueError:
        return {}, len(videos)
    source = {"schema_version": 1, "fetched_at": fetched_at}
    for key in ("uid", "days", "start_date", "end_date"):
        value = meta.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            source[key] = value
    patches, invalid = {}, 0
    for video in videos:
        if not isinstance(video, dict):
            invalid += 1
            continue
        identity = video.get("item_id")
        video_id = str(identity).strip() if isinstance(identity, (str, int)) and not isinstance(identity, bool) else ""
        if not video_id or not video_id.isascii() or not video_id.isdigit():
            invalid += 1
            continue
        patch = {}
        for field, keys in _COUNT_KEYS.items():
            for key in keys:
                value = _count(video.get(key))
                if value is not None:
                    patch[field] = value
                    break
        item_source = dict(source)
        index = video.get("video_index")
        if isinstance(index, (str, int, float)) and not isinstance(index, bool):
            text = str(index).strip()
            if text.lower() not in ("", "-", "—", "none", "nan", "inf", "-inf", "infinity", "-infinity"):
                item_source["video_index"] = text
        if not patch and "video_index" not in item_source:
            invalid += 1
            continue
        patch["source"] = item_source
        # 同一响应重复出现作品时只采用最后一份有效返回，不累加计数。
        patches[video_id] = patch
    return patches, invalid


def writeback_snapshot(snapshot: dict, *, collected_path=None, increment_path=None,
                       output_dir=None, progress=None, is_cancelled=None) -> dict:
    """仅回写已有作品；分存储报告失败，快照保存成功不等于回写成功。"""
    from toolbox import asset_index, collected_projection, collected_store
    from exporters import writer

    patches, invalid = build_metric_patches(snapshot)
    result = {"total": len(patches), "matched": 0, "updated": 0, "unmatched": 0,
              "unchanged": 0, "stale": 0, "invalid": invalid, "errors": [],
              "cancelled": False, "stores": {}}
    matched, updated, stale, invalid_ids, changed_paths = set(), set(), set(), set(), []
    if not patches:
        result["summary"] = writeback_summary(result)
        return result
    if output_dir is None:
        from toolbox.config_manager import EXPORT_CATEGORIES, get_export_dir
        output_dir = Path(get_export_dir()) / EXPORT_CATEGORIES["video_data"]
    steps = (
        ("collected", "已获取条目", lambda: collected_store.patch_existing_metrics(
            patches, db_path=collected_path)),
        ("increment", "增量归档", lambda: collected_projection.project_metric_patches(
            patches, db_path=increment_path)),
        ("exports", "资产导出", lambda: writer.patch_existing_video_json(patches, output_dir)),
    )
    for key, label, operation in steps:
        if is_cancelled and is_cancelled():
            result["cancelled"] = True
            break
        if progress:
            progress(f"回写{label}…")
        try:
            outcome = operation()
            result["stores"][key] = {field: outcome.get(field, 0)
                                     for field in ("matched", "updated", "stale", "invalid")}
            matched.update(outcome.get("matched_ids") or [])
            updated.update(outcome.get("updated_ids") or [])
            stale.update(outcome.get("stale_ids") or [])
            invalid_ids.update(outcome.get("invalid_ids") or [])
            changed_paths.extend(outcome.get("changed_paths") or [])
            for _error in outcome.get("errors") or []:
                result["errors"].append(f"{label}存在未成功写入的记录，请检查写入权限后重试")
        except Exception:
            # 不向 UI / Web DTO 转发文件路径、SQL 或平台响应等诊断内容。
            result["errors"].append(f"{label}暂时无法写入，请检查目录权限后重试")
    if changed_paths:
        try:
            asset_index.invalidate_paths(changed_paths, root=output_dir)
        except Exception:
            result["errors"].append("资产缓存未能刷新，请在资产库重建索引")
    result.update(matched=len(matched), updated=len(updated),
                  unmatched=len(set(patches) - matched),
                  stale=len(stale - updated),
                  invalid=invalid + len(invalid_ids - updated),
                  unchanged=len(matched - updated - stale - invalid_ids))
    result["summary"] = writeback_summary(result)
    return result


def writeback_summary(result: dict) -> str:
    text = f"资产回写 {result.get('updated', 0)} 条 · 未匹配 {result.get('unmatched', 0)} 条"
    if result.get("unchanged"):
        text += f" · 已是最新 {result['unchanged']} 条"
    if result.get("stale"):
        text += f" · 旧快照跳过 {result['stale']} 条"
    if result.get("invalid"):
        text += f" · 无效数据跳过 {result['invalid']} 条"
    if result.get("errors"):
        text += f" · {len(result['errors'])} 处未成功，请重试"
    if result.get("cancelled"):
        text = "已取消后续回写，已完成部分保留；" + text
    return text


def fetch_and_save(uid: str, days: int = 30, *, progress=None, is_cancelled=None) -> dict:
    """桌面与 Web 共用：抓取 → 保存完整快照 → 回写既有资产。"""
    from platforms import douyin_index as dxi

    def check_cancel():
        if is_cancelled and is_cancelled():
            raise FetchCancelled("已取消，未回写资产")

    def say(message):
        check_cancel()
        if progress:
            progress(str(message))

    check_cancel()
    try:
        data = dxi.fetch_daren_all(uid, days=days, progress=say)
    except FetchCancelled:
        raise
    except Exception as exc:
        raise ValueError("抓取失败：请检查登录状态与网络连接后重试") from exc
    check_cancel()
    try:
        path = dxi.save_snapshot(data)
    except Exception as exc:
        raise ValueError("保存快照失败：请检查导出目录的写入权限后重试") from exc
    outcome = writeback_snapshot(data, progress=progress, is_cancelled=is_cancelled)
    return {"data": data, "path": path, "writeback": outcome}


def search_daren(keyword: str, total: int = 20):
    from platforms import douyin_index as dxi
    try:
        return dxi.search_daren(keyword, total)
    except Exception as exc:
        raise ValueError("搜索失败：请检查登录状态与网络连接后重试") from exc


def fmt_count(value):
    from platforms import douyin_index as dxi
    return dxi.fmt_count(value)


def index_dir():
    from platforms import douyin_index as dxi
    return dxi.index_dir()
