"""腾讯文档 WebUI 写回用例（L3）：白名单两表只读预览 + 单条新增。

范围硬编码为《封茗囧菌数据汇总库》的「官方动态」（ss_h2uh2s）与
「直播记录」（ss_1xgstm），只允许各新增 1 条；不提供批量、修改、删除、
新建下拉选项或图片评论生成。写入前要求「同步前刷新」门禁（或桌面端
免校对模式），写后读回校验并记录推送历史；并发写入由 import_pending
的共用写锁拦截。本模块 Qt-free，只调用既有 L3/L4 用例。
"""

from __future__ import annotations

from toolbox import sync_refresh
from toolbox.tcdoc import bridge, tables
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (ImportErrorDetail, LocalImage,
                                        LocalImportClient, ManagedAuth,
                                        validate_target)

DOC_NAME = "封茗囧菌数据汇总库"
TARGETS = {
    "official": {
        "name": "官方动态",
        "doc_key": "DRnd3bkhkQUR1aEJr",
        "tab_id": "ss_h2uh2s",
        "profile": "posts",
    },
    "live": {
        "name": "直播记录",
        "doc_key": "DRnd3bkhkQUR1aEJr",
        "tab_id": "ss_1xgstm",
        "profile": "lives",
    },
}
DEFAULT_DAYS = 30


class TcdocWriteCancelled(Exception):
    """写入任务在开始前被取消。"""


def status() -> dict:
    """写回入口状态：固定目标、刷新门禁与范围说明。"""
    allowed, message = sync_refresh.upload_allowed()
    return {
        "doc_name": DOC_NAME,
        "targets": [
            {"key": key, "name": item["name"], "tab_id": item["tab_id"]}
            for key, item in TARGETS.items()
        ],
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "bypass": bool(sync_refresh.bypass_enabled()),
        "note": "只允许向「官方动态」「直播记录」各新增单条记录；"
                "写入前需数据刚刷新过（或在桌面端开启免校对模式），"
                "写入后读回校验并记录推送历史，不提供修改 / 删除 / 批量。",
    }


def preview(tab: str, days: int = DEFAULT_DAYS, limit: int = 100) -> dict:
    """只读预览待写记录（白名单两表），不发送任何写请求。"""
    target = _target(tab)
    days = _days(days)
    limit = max(1, min(int(limit or 100), 500))
    allowed, message = sync_refresh.upload_allowed()
    if target["profile"] == "lives":
        result = bridge.build_live_pending(days=days)
        rows = list(result.get("rows") or [])
        items = [_live_item(row) for row in rows[:limit]]
        stats = result.get("stats") or {}
    else:
        result = bridge.build_pending(target["doc_key"], target["tab_id"],
                                      days=days)
        rows = list(result.get("rows") or [])
        items = [_post_item(row) for row in rows[:limit]]
        stats = result.get("stats") or {}
    return {
        "tab": tab,
        "name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "days": days,
        "rows": items,
        "total": len(rows),
        "truncated": len(rows) > limit,
        "stats": {
            "pending": int(stats.get("pending") or 0),
            "duplicates": int(stats.get("duplicates") or 0),
            "remote_rows": int(stats.get("remote_rows") or 0),
            "with_images": int(stats.get("with_images") or 0),
            "image_count": int(stats.get("image_count") or 0),
            "since": str(stats.get("since") or ""),
        },
        "refresh": {"allowed": bool(allowed), "message": str(message)},
        "note": "只读预览：不会写入腾讯文档；每条记录单独确认后才会新增 1 条。",
    }


def ensure_uploadable(tab: str) -> dict:
    """提交写回任务前的同步校验：目标白名单与刷新门禁。

    在请求线程执行，确保门禁不通过时直接返回 400，而不是先建任务再失败。
    """
    target = _target(tab)
    try:
        validate_target(target["doc_key"], target["tab_id"])
    except ImportErrorDetail as exc:
        raise ValueError(str(exc)) from exc
    allowed, message = sync_refresh.upload_allowed()
    if not allowed:
        raise ValueError(
            f"当前数据未刷新，暂不允许写入：{message}。"
            "请先在桌面端完成「同步前刷新」，或在桌面端开启免校对模式。")
    return target


def upload_single(tab: str, key: str, confirmed: bool = False,
                  days: int = DEFAULT_DAYS, on_progress=None,
                  is_cancelled=None) -> dict:
    """新增 1 条到白名单表：重建待写清单 → 定位记录 → 写入 → 读回校验。"""
    if not confirmed:
        raise ValueError("缺少写入确认")
    key = str(key or "").strip()
    if not key:
        raise ValueError("请选择要写入的记录")
    target = ensure_uploadable(tab)
    days = _days(days)
    _check_cancelled(is_cancelled)
    if target["profile"] == "lives":
        return _upload_live(target, key, days, on_progress, is_cancelled)
    return _upload_post(target, key, days, on_progress, is_cancelled)


def _upload_live(target, key, days, on_progress, is_cancelled) -> dict:
    result = bridge.build_live_pending(days=days)
    row = next((item for item in result.get("rows") or []
                if pending.live_key(item) == key), None)
    if row is None:
        raise ValueError("待写记录已变化或已同步，请重新预览后再试")
    _check_cancelled(is_cancelled)
    _progress(on_progress, 0.3, 1, "正在连接腾讯文档")
    client = _client(target)
    client.connect()
    _progress(on_progress, 0.5, 1, "正在新增 1 条直播记录并读回校验")
    receipt = pending.write_lives(client, [row], limit=1, apply=True,
                                  verify_each=True)
    added = list(receipt.get("added") or [])
    skipped = list(receipt.get("skipped") or [])
    errors = list(receipt.get("errors") or [])
    verified = bool(added and added[0].get("verified"))
    if added:
        sync_refresh.invalidate("WebUI 单条写入完成，需重新刷新后再写")
    return {
        "tab": "live",
        "tab_name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "summary": _live_summary(added, skipped, errors, verified),
        "added": len(added),
        "skipped": len(skipped),
        "verified": verified,
        "record_ids": [str(item.get("record_id") or "") for item in added],
        "error": (errors[0].get("error") if errors else ""),
        "note": "只新增了 1 条；本页不提供修改或删除。",
    }


def _upload_post(target, key, days, on_progress, is_cancelled) -> dict:
    result = bridge.build_pending(target["doc_key"], target["tab_id"],
                                  days=days)
    row = next((item for item in result.get("rows") or []
                if _post_key(item) == key), None)
    if row is None:
        raise ValueError("待写记录已变化或已同步，请重新预览后再试")
    _check_cancelled(is_cancelled)
    _progress(on_progress, 0.2, 1, "正在准备记录与本地图片")
    sources = bridge.collect_local_items(days)
    catalogue = pending.build_catalogue(pending.ROOT, sources)
    try:
        records, missing = pending.records_from_rows(pending.ROOT, [row],
                                                     catalogue)
    except ImportErrorDetail as exc:
        raise ValueError(f"该行暂时无法写入：{exc}") from exc
    record = records[0]
    media = pending.ROOT / "download/已获取数据"
    record["_images"] = [
        LocalImage.inspect(pending.ROOT / path, media)
        for path in record.get("images") or []
    ]
    _check_cancelled(is_cancelled)
    _progress(on_progress, 0.4, 1, "正在连接腾讯文档")
    client = _client(target)
    client.connect()
    fields = client.get_fields()
    try:
        pending.make_plan(record, fields, available_only=False,
                          allow_new_options=False)
    except ImportErrorDetail as exc:
        raise ValueError(
            f"该行暂时无法写入：{exc}"
            "（如需新建下拉选项，请在桌面端确认后上传）") from exc
    _progress(on_progress, 0.5, 1, "正在新增 1 条官方动态并读回校验")
    receipt = pending.run(client, [record], apply=True, verify_each=True,
                          profile="posts", allow_new_options=False,
                          source="WebUI 单条上传")
    added = list(receipt.get("added") or [])
    skipped = list(receipt.get("skipped") or [])
    verified = bool(added and added[0].get("verified"))
    if added:
        sync_refresh.invalidate("WebUI 单条写入完成，需重新刷新后再写")
    summary = _post_summary(added, skipped, verified)
    return {
        "tab": "official",
        "tab_name": target["name"],
        "doc_name": DOC_NAME,
        "tab_id": target["tab_id"],
        "summary": summary,
        "added": len(added),
        "skipped": len(skipped),
        "verified": verified,
        "record_ids": [str(item.get("record_id") or "") for item in added],
        "missing_images": len(missing),
        "note": "只新增了 1 条；未生成评论区长图，"
                "本页不提供修改或删除。",
    }


def _post_summary(added, skipped, verified) -> str:
    if added:
        return ("已新增 1 条并读回校验通过" if verified
                else "已新增 1 条，读回校验未通过，请到表格核对")
    if skipped:
        return "云端已存在相同记录，已跳过写入"
    return "未写入任何记录"


def _live_summary(added, skipped, errors, verified) -> str:
    if added:
        return ("已新增 1 条并读回校验通过" if verified
                else "已新增 1 条，读回校验未通过，请到表格核对")
    if skipped:
        return "云端已存在相同直播记录，已跳过写入"
    if errors:
        return f"未写入：{errors[0].get('error') or '记录无法写入'}"
    return "未写入任何记录"


def _post_item(row: dict) -> dict:
    link = _row_link(row.get("作品链接") or row.get("动态链接(b站)"))
    return {
        "key": _post_key(row),
        "title": str(row.get("作品标题") or ""),
        "time": str(row.get("发布时间") or ""),
        "account": str(row.get("发布账号") or ""),
        "platform": str(row.get("发布平台") or ""),
        "link": link,
        "images": len(row.get("_images") or []),
        "flag": "",
    }


def _live_item(row: dict) -> dict:
    _label, url = tables.split_link(row.get("直播链接1"))
    flag = str(row.get("_dup") or "")
    if not flag and row.get("_needs_review"):
        flag = "需人工确认"
    return {
        "key": pending.live_key(row),
        "title": str(row.get("直播标题") or ""),
        "time": str(row.get("开始时间") or ""),
        "account": str(row.get("直播账户") or ""),
        "platform": str(row.get("直播平台") or ""),
        "link": str(url or ""),
        "images": 0,
        "flag": flag,
    }


def _post_key(row: dict) -> str:
    link = _row_link(row.get("作品链接") or row.get("动态链接(b站)"))
    return pending.link_key(link) or ""


def _row_link(value) -> str:
    _label, url = tables.split_link(value)
    return str(url or "")


def _target(tab: str) -> dict:
    key = str(tab or "")
    target = TARGETS.get(key)
    if target is None:
        raise ValueError("写回目标只支持「官方动态」或「直播记录」")
    return target


def _days(days) -> int:
    try:
        value = int(days or DEFAULT_DAYS)
    except (TypeError, ValueError):
        value = DEFAULT_DAYS
    return max(1, min(value, 3650))


def _client(target: dict) -> LocalImportClient:
    if not _read_cookie():
        raise ValueError("尚未登录，无法写入；请先在桌面端完成登录后重试")
    return LocalImportClient(ManagedAuth(_read_cookie), target["doc_key"],
                             target["tab_id"])


def _read_cookie() -> str:
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""


def _check_cancelled(is_cancelled) -> None:
    if is_cancelled and is_cancelled():
        raise TcdocWriteCancelled("任务已取消")


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)
