"""抖音指数指标补丁的纯数据合并（L5，无存储与网络副作用）。

各存储 owner 复用本模块校验和新鲜度规则；不新增数据库 schema。
只改已有条目的四项互动计数与 raw_data.douyin_index 来源元数据。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

METRIC_FIELDS = {
    "like_count": "点赞数", "comment_count": "评论数",
    "share_count": "分享数", "favorite_count": "收藏数",
}
_SOURCE_FIELDS = ("schema_version", "fetched_at", "uid", "days",
                  "start_date", "end_date", "video_index")
_CST = timezone(timedelta(hours=8))


def metric_patch_result(*, changed_paths=False):
    """返回各 owner 共用、可序列化的空回执。计数按实际匹配记录统计。"""
    result = {"matched": 0, "updated": 0, "stale": 0, "invalid": 0,
              "errors": [], "matched_ids": [], "updated_ids": []}
    if changed_paths:
        result["changed_paths"] = []
    return result


def metric_timestamp(value):
    """将已有时间与快照时间统一为北京时间；空值/非法时间返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=_CST) if parsed.tzinfo is None else parsed.astimezone(_CST)
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_count(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return False


def _valid_index(value):
    return (isinstance(value, (str, int, float)) and not isinstance(value, bool)
            and str(value).strip() not in ("", "-", "—", "None", "nan", "inf", "-inf"))


def metric_patch_status(record, patch, *, localized=False):
    """返回 ready / stale / invalid；缺失数据不补零，旧快照不能覆盖新记录。"""
    if not isinstance(record, dict) or not isinstance(patch, dict):
        return "invalid"
    if record.get("平台" if localized else "platform") != "douyin":
        return "invalid"
    source = patch.get("source")
    if not isinstance(source, dict) or source.get("schema_version") != 1:
        return "invalid"
    current_time = metric_timestamp(source.get("fetched_at"))
    if current_time is None:
        return "invalid"
    count_fields = [name for name in METRIC_FIELDS if name in patch]
    if any(not _valid_count(patch[name]) for name in count_fields):
        return "invalid"
    if not count_fields and not _valid_index(source.get("video_index")):
        return "invalid"
    raw = record.get("原始数据" if localized else "raw_data")
    if raw is not None and not isinstance(raw, dict):
        return "invalid"
    old_source = (raw or {}).get("douyin_index")
    if old_source is not None and not isinstance(old_source, dict):
        return "invalid"
    dates = [record.get("获取时间" if localized else "fetched_at"),
             (old_source or {}).get("fetched_at")]
    for value in dates:
        if value in (None, ""):
            continue
        previous_time = metric_timestamp(value)
        if previous_time is None:
            return "invalid"
        if previous_time > current_time:
            return "stale"
    return "ready"


def merge_existing_metrics(record, patch, *, localized=False):
    """复制并合并单条记录；保留未知字段、内容获取时间与旧有效视频指数。"""
    if metric_patch_status(record, patch, localized=localized) != "ready":
        return record, False
    merged = dict(record)
    for name, chinese in METRIC_FIELDS.items():
        if name in patch:
            merged[chinese if localized else name] = patch[name]
    raw_key = "原始数据" if localized else "raw_data"
    raw = dict(record.get(raw_key) or {})
    source = dict(raw.get("douyin_index") or {})
    for name in _SOURCE_FIELDS:
        if name not in patch["source"]:
            continue
        value = patch["source"][name]
        if name == "video_index" and not _valid_index(value):
            continue
        source[name] = value
    raw["douyin_index"] = source
    merged[raw_key] = raw
    return merged, merged != record
