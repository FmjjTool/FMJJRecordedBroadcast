"""腾讯文档 · 浏览记录（已查看明细，纯 HTTP 直连）。

接口 `cgi-bin/online_docs/browser_list`（2026-11-10 实测）：

- `start` / `limit` 翻页（limit 上限 100），`next` 是下一页起点；
  `total`（已查看人数）和 `views`（浏览量）只在第一页返回，后续页为 0。
- `operaters[]`：`uid` / `nick` / `avatar` / `uid_source`（1=QQ、2=微信）/
  `last_operate_ts`（该用户最近一次浏览的秒级时间戳）。
- 只覆盖「已查看」页签；「未查看」没有独立接口（界面按成员 - 已查看本地算）。

落盘：并入 tcdoc.db 的 `browsers` 表（按 uid 增量 upsert），
报表增加「浏览人」工作表；仅随手动「立即采集」执行，不进每日定时任务。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import http_api
from . import tables as t

CST = timezone(timedelta(hours=8))
BROWSER_LIST_URL = http_api.BROWSER_LIST_URL
PAGE_LIMIT = 100          # 接口上限实测 100，超过按 100 返回
PAGE_SLEEP = 0.1          # 翻页间隔，避免连续请求触发限流
UID_SOURCE = {1: "QQ", 2: "微信"}


class BrowseError(RuntimeError):
    """浏览记录拉取失败（登录态 / 权限 / 接口结构异常）。"""


def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _time_text(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), CST).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def _fetch_page(client, doc: dict, domain_id: str, pad_id: str,
                start: int, xsrf: str) -> dict:
    url = (f"{BROWSER_LIST_URL}?u=&domain_id={domain_id}&pad_id={pad_id}"
           f"&start={start}&limit={PAGE_LIMIT}&xsrf={xsrf}"
           f"&t={int(time.time() * 1000)}")
    try:
        data = http_api._get_json(client, url, doc)
    except http_api.HttpError as exc:
        raise BrowseError(f"第 {start // PAGE_LIMIT + 1} 页拉取失败：{exc}") from exc
    code = data.get("retcode", data.get("ret"))
    if code not in (0, None):
        raise BrowseError(f"第 {start // PAGE_LIMIT + 1} 页接口失败："
                          f"{data.get('msg') or code}")
    return data


def collect(doc: dict, on_status=None, max_pages: int | None = None) -> dict:
    """拉取该文档全部已查看浏览人（自动翻页到没有下一页）。

    max_pages 仅用于调试/冒烟；正式拉取传 None。
    返回 {doc_key, name, title, pad_id, views, readers, rows[], pages,
    complete, fetched_at}；rows 每项 uid/nick/avatar/uid_source/last_ts/last_time/source。
    """
    jar = http_api._cookie_jar()
    xsrf = http_api._xsrf(jar)
    if not xsrf:
        raise BrowseError("缺少腾讯文档登录态（TOK）：请先到「Cookie 管理器」登录")

    name = doc.get("name") or doc.get("key")
    key = str(doc.get("key") or t.doc_key_of(doc.get("url")) or "")
    _emit(on_status, f"读取文档信息：{name}")
    with http_api._client(jar) as client:
        sk = http_api.doc_skeleton(client, doc)
        pad_id = sk.get("pad_id") or ""
        if not pad_id:
            raise BrowseError("文档骨架里没有 padId（可能无权限 / 文档已删除）")
        domain_id = sk.get("domain_id") or "300000000"
        views = readers = None
        start, pages, complete = 0, 0, True
        rows: list[dict] = []
        while True:
            data = _fetch_page(client, doc, domain_id, pad_id, start, xsrf)
            if pages == 0:
                views = data.get("views")
                readers = data.get("total")
                if isinstance(readers, int) and readers > 0:
                    _emit(on_status, f"浏览记录：共 {readers} 人，开始翻页…")
            operaters = data.get("operaters") or []
            if not operaters:
                break
            rows.extend(operaters)
            pages += 1
            nxt = data.get("next")
            if nxt is None or nxt <= start:
                break
            if max_pages and pages >= max_pages:
                complete = False
                break
            start = int(nxt)
            time.sleep(PAGE_SLEEP)

    seen: set[str] = set()
    items: list[dict] = []
    for op in rows:
        uid = str(op.get("uid") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        ts = op.get("last_operate_ts")
        source = UID_SOURCE.get(op.get("uid_source"), "")
        items.append({
            "uid": uid,
            "nick": str(op.get("nick") or ""),
            "avatar": str(op.get("avatar") or ""),
            "uid_source": op.get("uid_source") if isinstance(op.get("uid_source"), int) else None,
            "last_ts": int(ts) if isinstance(ts, (int, float)) else None,
            "last_time": _time_text(ts),
            "source": source,
        })
    items.sort(key=lambda r: r["last_ts"] or 0, reverse=True)

    return {
        "doc_key": key, "name": name, "title": sk.get("title") or name,
        "pad_id": pad_id,
        "views": int(views) if isinstance(views, (int, float)) else None,
        "readers": int(readers) if isinstance(readers, (int, float)) else None,
        "rows": items, "pages": pages, "complete": complete,
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    }
