"""线下活动「活动歌单」回写（L3，Qt-free）。

歌单在网易云 / QQ音乐 / 酷狗创建成功后，把各平台歌单链接写到腾讯文档
「线下活动」对应的三个链接列：

    活动歌单_网易云 / 活动歌单_酷狗 / 活动歌单_QQ

约定：
- 列由用户在腾讯文档侧维护，程序只写不建列；表里没有该列就跳过并报原因；
- 只改目标行这三个列，其它字段一律不动；
- 单元格已有别的链接时不覆盖（除非显式 overwrite），避免冲掉人工登记；
- 写完立即读回校验，回执里区分 已写入 / 已是该链接 / 已有其它链接 / 未通过校验；
- 酷狗链接先做只读网页探测（`playlist_link.VERIFY_BEFORE_WRITE`），
  探测不通过就不写，避免把打不开的地址写进真实表格。
"""

from __future__ import annotations

import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from music import playlist_link

from .import_local import ImportErrorDetail, LocalImportClient, ManagedAuth

EVENT_DOC = "DRnd3bkhkQUR1aEJr"
EVENT_TAB = "ss_e1kkm2"
NAME_COLUMN = "活动名称"
DATE_COLUMN = "活动日期"
_PROBE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
STATUS_LABELS = {
    "written": "已写入",
    "unchanged": "已是该链接",
    "conflict": "已有其它链接",
    "missing_column": "缺少目标列",
    "unverified": "链接校验未通过",
    "failed": "写入失败",
}


def summary_text(receipt) -> str:
    """回写回执 → 一行说明（桌面页 / WebUI 共用）。"""
    if not receipt:
        return "活动歌单：未回写"
    items = receipt.get("items") or []
    if not items:
        return f"活动歌单：{receipt.get('message') or '未回写'}"
    parts = []
    for item in items:
        label = STATUS_LABELS.get(item.get("status"), item.get("status") or "")
        parts.append(f"{item.get('column') or item.get('platform')} {label}"
                     + (f"（{item['message']}）" if item.get("message") else ""))
    return "活动歌单：" + "；".join(parts)


def _default_reader():
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""


def _text(value) -> str:
    if isinstance(value, list):
        return "".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value or "").strip()


def _cell_url(value) -> str:
    """单元格值 → 链接 URL；兼容两级字符串、链接数组与纯文本。"""
    if isinstance(value, list):
        for item in value:
            url = _cell_url(item)
            if url:
                return url
        return ""
    if isinstance(value, dict):
        raw = str(value.get("link") or value.get("url")
                  or value.get("text") or "").strip()
        match = re.search(r"https?://\S+", raw)
        return match.group(0).rstrip("）)】]") if match else ""
    raw = str(value or "").strip()
    if "\u0002" in raw:
        raw = raw.split("\u0002", 1)[1]
    match = re.search(r"https?://\S+", raw)
    return match.group(0).rstrip("）)】]") if match else ""


def _norm_date(value) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if raw.isdigit():
        seconds = int(raw) / (1000 if len(raw) > 10 else 1)
        try:
            return time.strftime("%Y-%m-%d", time.localtime(seconds))
        except (OSError, OverflowError):
            return raw
    return raw[:10]


def _field_type(fields, title: str) -> int:
    hits = [f for f in fields or () if str(f.get("fieldTitle") or "") == title]
    if len(hits) != 1:
        return 0
    return int(hits[0].get("fieldType") or 0)


def find_event_record(records, event) -> dict | None:
    """在远端记录里定位这场活动：先 recordID，再「活动名称 + 活动日期」。"""
    record_id = str((event or {}).get("record_id") or "").strip()
    if record_id:
        for row in records:
            if str(row.get("recordID") or "") == record_id:
                return row
    name = _text((event or {}).get("name"))
    day = _norm_date((event or {}).get("date"))
    if not name:
        return None
    same_name = []
    for row in records:
        values = row.get("values") or {}
        if _text(values.get(NAME_COLUMN)) != name:
            continue
        if day and _norm_date(values.get(DATE_COLUMN)) == day:
            return row
        same_name.append(row)
    return same_name[0] if len(same_name) == 1 else None


def probe_page(url: str, name: str = "", timeout: float = 8.0) -> tuple[bool, str]:
    """只读探测歌单网页是否真的可打开；返回 (是否通过, 说明)。"""
    request = Request(url, headers={"User-Agent": _PROBE_UA,
                                    "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            final = str(response.geturl() or url)
            body = response.read(400_000).decode("utf-8", "replace")
    except HTTPError as exc:
        return False, f"网页返回 HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"网页无法访问（{reason}）"
    if status != 200:
        return False, f"网页返回 HTTP {status}"
    path = urlsplit(final).path
    if "/special/single/" not in path and "/playlist" not in path:
        return False, "网页跳转到了非歌单页"
    if name and name not in body:
        return False, "网页里找不到歌单名称"
    return True, "网页可访问"


def write_results(event, results, *, reader=None, client_factory=None,
                  overwrite: bool = False, probe=None) -> dict:
    """把平台歌单链接回写到线下活动行；返回逐项回执。"""
    links = playlist_link.build_links(results)
    receipt = {"ok": False, "event": _text((event or {}).get("name")),
               "record_id": "", "items": [], "message": ""}
    if not links:
        receipt["message"] = "没有可回写的平台歌单（未创建成功或平台不受支持）"
        return receipt
    if not str((event or {}).get("name") or "").strip():
        receipt["message"] = "缺少活动名称，无法定位表格行"
        return receipt

    factory = client_factory or LocalImportClient
    client = factory(ManagedAuth(reader or _default_reader), EVENT_DOC, EVENT_TAB)
    client.connect()
    fields = client.get_fields()
    records = client.get_records()
    record = find_event_record(records, event)
    if record is None:
        receipt["message"] = "腾讯表里找不到这场活动，请先同步表格快照后再试"
        return receipt
    record_id = str(record.get("recordID") or "")
    receipt["record_id"] = record_id
    values = record.get("values") or {}

    written = []
    for link in links:
        item = {"platform": link["platform"], "column": link["column"],
                "url": link["url"], "status": "", "message": ""}
        receipt["items"].append(item)
        kind = _field_type(fields, link["column"])
        if not kind:
            item["status"] = "missing_column"
            item["message"] = f"表里没有「{link['column']}」列"
            continue
        if kind not in (1, 8):
            item["status"] = "missing_column"
            item["message"] = f"「{link['column']}」不是链接/文本列"
            continue
        current = _cell_url(values.get(link["column"]))
        if current == link["url"]:
            item["status"] = "unchanged"
            item["message"] = "已是该歌单链接"
            continue
        if current and not overwrite:
            item["status"] = "conflict"
            item["message"] = f"已有其它链接（{current}），未覆盖"
            continue
        if link.get("verify"):
            check = (probe or probe_page)(link["url"], link.get("playlist_name") or "")
            if not check[0]:
                item["status"] = "unverified"
                item["message"] = f"链接校验未通过：{check[1]}；歌单 ID {link['playlist_id']}"
                continue
        try:
            client.update_playlist_link(record_id, link["column"], link["url"],
                                        field_type=kind,
                                        text=link.get("playlist_name") or "")
        except ImportErrorDetail as exc:
            item["status"] = "failed"
            item["message"] = str(exc)
            continue
        item["status"] = "written"
        written.append(item)

    if written:
        fresh = {str(row.get("recordID") or ""): row
                 for row in client.get_records()}
        fresh_values = (fresh.get(record_id) or {}).get("values") or {}
        for item in written:
            actual = _cell_url(fresh_values.get(item["column"]))
            if actual != item["url"]:
                item["status"] = "failed"
                item["message"] = "写入后读回不一致，请到表格核对"

    counts = {}
    for item in receipt["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    receipt["ok"] = bool(counts.get("written")) and not counts.get("failed")
    receipt["message"] = "；".join(
        f"{name} {count}" for name, count in counts.items()) or "没有可回写的项"
    return receipt
