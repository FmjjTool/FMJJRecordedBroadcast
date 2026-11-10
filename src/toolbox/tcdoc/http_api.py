"""腾讯文档监控 · 纯 HTTP 直连采集（不启动浏览器）。

参考 FmjjTool/webwiki-api 的 `tcdoc/http_sync.py`：dop-api 只校验 Referer，
带上文档地址即可拿 JSON；之前浏览器能请求，是因为同源页面自动带了 Referer。

链路：
- 表格：opendoc 文档骨架（取 rev + 工作表清单）→ 逐表分段拉取 → 复用 tables 解码；
- 指标：opendoc 骨架取 padId/domainId →
  `permission/collaboration/get_count`（成员数）+
  `online_docs/browser_list`（浏览量 / 已查看人数）。

省流量：骨架里的 `collab_client_vars.rev` 是版本号，未变化时定时任务只发
一个骨架请求（gzip 后约 76 KB），不拉整份表格。
"""

from __future__ import annotations

import json
import time
from typing import Callable

import httpx

from . import session
from . import tables as t

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TIMEOUT = 60
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
GET_COUNT_URL = "https://docs.qq.com/api/permission/collaboration/get_count"
BROWSER_LIST_URL = "https://docs.qq.com/cgi-bin/online_docs/browser_list"


class HttpError(RuntimeError):
    """采集失败（网络 / 状态码 / 结构异常）。"""


# ==================== 基础 ====================

def _cookie_jar() -> dict[str, str]:
    try:
        return session.parse_cookie_string(session.cookie_string())
    except Exception:  # noqa: BLE001 - 公开文档无 Cookie 也能读表格
        return {}


def _xsrf(jar: dict[str, str]) -> str:
    return str(jar.get("TOK") or "")


def _referer(doc: dict) -> str:
    return str(doc.get("url") or "")


def _client(jar: dict[str, str] | None = None) -> httpx.Client:
    return httpx.Client(headers=BASE_HEADERS, cookies=jar or {},
                        timeout=TIMEOUT, follow_redirects=True)


def _get_json(client: httpx.Client, url: str, doc: dict) -> dict:
    try:
        resp = client.get(url, headers={"Referer": _referer(doc)})
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HttpError(f"HTTP {exc.response.status_code}（{url[:80]}…）") from exc
    except httpx.HTTPError as exc:
        raise HttpError(f"网络错误：{exc}") from exc
    text = t.strip_jsonp(resp.text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"返回内容不是 JSON：{text[:120]}") from exc
    if not isinstance(data, dict):
        raise HttpError("接口返回结构异常（不是对象）")
    return data


def _post_json(client: httpx.Client, url: str, doc: dict, payload: dict) -> dict:
    try:
        resp = client.post(url, json=payload, headers={"Referer": _referer(doc)})
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HttpError(f"HTTP {exc.response.status_code}（{url[:80]}…）") from exc
    except httpx.HTTPError as exc:
        raise HttpError(f"网络错误：{exc}") from exc
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"返回内容不是 JSON：{resp.text[:120]}") from exc
    if not isinstance(data, dict):
        raise HttpError("接口返回结构异常（不是对象）")
    return data


def _client_vars(data: dict) -> dict:
    return data.get("clientVars") or {}


def _collab_vars(data: dict) -> dict:
    return _client_vars(data).get("collab_client_vars") or {}


def doc_skeleton(client: httpx.Client, doc: dict, retries: int = 1) -> dict:
    """文档骨架：返回 rev / 工作表清单 / padId / domainId / 标题。

    大文档首次可能返回空 header，表格同步传 retries=4 多等几轮；
    指标采集只需要 padId，重试 1 次即可。
    """
    key = doc.get("key") or t.doc_key_of(doc.get("url"))
    url = t.API_BASE.format(key=key)
    last: dict = {}
    for attempt in range(max(1, retries)):
        if attempt:
            time.sleep(2.0)
        data = _get_json(client, url, doc)
        cv, cc = _client_vars(data), _collab_vars(data)
        tabs = [tab for head in cc.get("header") or [] for tab in head.get("d") or []]
        pad_id = str(cv.get("padId") or cc.get("padId") or "")
        domain_id = str(cv.get("domainId") or "")
        if "$" in pad_id:
            prefix, _, plain = pad_id.partition("$")
            pad_id = plain
            domain_id = domain_id or prefix
        last = {
            "doc_key": key,
            "rev": str(cc.get("rev") or ""),
            "tabs": tabs,
            "pad_id": pad_id,
            "domain_id": domain_id or "300000000",
            "title": cv.get("title") or doc.get("name") or key,
        }
        if tabs or retries <= 1:
            return last
    return last


# ==================== 指标（浏览量 / 成员数 / 已查看人数） ====================

def _members(client: httpx.Client, doc: dict, pad_id: str, xsrf: str) -> int:
    if not xsrf:
        raise HttpError("缺少 xsrf（TOK），腾讯文档登录态可能已失效")
    data = _post_json(client, f"{GET_COUNT_URL}?u=&xsrf={xsrf}", doc,
                      {"pad_id": pad_id})
    if data.get("retcode") not in (0, None):
        raise HttpError(f"成员数接口失败：{data.get('msg') or data.get('retcode')}")
    count = (data.get("result") or {}).get("count")
    if not isinstance(count, int):
        raise HttpError("成员数接口没有返回 count")
    return count


def _browse_metrics(client: httpx.Client, doc: dict, domain_id: str,
                    pad_id: str, xsrf: str) -> tuple[int | None, int | None]:
    if not xsrf:
        raise HttpError("缺少 xsrf（TOK），腾讯文档登录态可能已失效")
    url = (f"{BROWSER_LIST_URL}?u=&domain_id={domain_id}&pad_id={pad_id}"
           f"&start=0&limit=15&xsrf={xsrf}&t={int(time.time() * 1000)}")
    data = _get_json(client, url, doc)
    code = data.get("retcode", data.get("ret"))
    if code not in (0, None):
        raise HttpError(f"浏览记录接口失败：{data.get('msg') or code}")
    views, readers = data.get("views"), data.get("total")
    return (int(views) if isinstance(views, (int, float)) else None,
            int(readers) if isinstance(readers, (int, float)) else None)


def collect_metrics(docs: list[dict], on_status=None) -> list[dict]:
    """纯 HTTP 采集浏览量 / 成员数 / 已查看人数。"""
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档，请先在「文档管理」里添加")
    jar = _cookie_jar()
    xsrf = _xsrf(jar)
    if not xsrf:
        raise RuntimeError("腾讯文档登录态缺失或已失效：请到「Cookie 管理器」重新登录")
    results: list[dict] = []
    with _client(jar) as client:
        for i, doc in enumerate(docs, start=1):
            name = doc.get("name") or doc.get("key")
            _emit(on_status, f"（{i}/{len(docs)}）正在读取指标：{name}")
            key = doc.get("key") or t.doc_key_of(doc.get("url"))
            base = {"doc_key": key, "name": name, "url": doc.get("url"), "ok": False}
            try:
                sk = doc_skeleton(client, doc)
                pad_id = sk["pad_id"]
                if not pad_id:
                    raise HttpError("文档骨架里没有 padId（可能无权限 / 文档已删除）")
                members = views = readers = None
                errors: list[str] = []
                try:
                    members = _members(client, doc, pad_id, xsrf)
                except Exception as exc:  # noqa: BLE001 - 指标允许单边缺失
                    errors.append(f"成员数：{exc}")
                try:
                    views, readers = _browse_metrics(
                        client, doc, sk["domain_id"], pad_id, xsrf)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"浏览记录：{exc}")
                if members is None and views is None:
                    raise HttpError("；".join(errors) or "指标接口无返回")
                source = "接口"
                if views is None:
                    source = "未读取到浏览量（接口）"
                if errors:
                    source += "；" + "；".join(errors)
                base.update(ok=True, views=views, members=members, readers=readers,
                            source=source, title=sk.get("title") or name)
            except Exception as exc:  # noqa: BLE001 - 单文档失败继续其余文档
                base["error"] = str(exc)
            results.append(base)
    return results


# ==================== 表格（骨架 → 逐表 → 解码） ====================

def _fetch_sheet(client: httpx.Client, doc: dict, tab: dict) -> dict:
    """纯 HTTP 分段拉取一张表，结构与非 HTTP 版 tables._fetch_sheet 一致。"""
    key = doc.get("key") or t.doc_key_of(doc.get("url"))
    base = t.API_BASE.format(key=key)
    columns = None
    merged: dict[str, dict] = {}
    expected = None
    start = 0
    for _ in range(60):  # 60 批 × BATCH 行，足够兜住超大表
        url = f"{base}&tab={tab['id']}&startrow={start}&endrow={start + t.BATCH}"
        data = _get_json(client, url, doc)
        cc = _collab_vars(data)
        chunk = (cc.get("initialAttributedText") or {}).get("text", [{}])[0]
        part = t.parse_chunk(chunk, tab)
        if columns is None:
            columns = part["columns"]
        for pos, row in enumerate(part["rows"]):
            row["row_no"] = start + pos + 1   # 分段后行号要接上
            merged[row["record_id"]] = row

        max_row = chunk.get("max_row")
        last = chunk.get("end_row_index")
        expected = int(max_row) if max_row is not None else None
        if not part["rows"] or last is None:
            break
        if expected is not None and int(last) + 1 >= expected:
            break
        start = int(last) + 1

    rows = sorted(merged.values(), key=lambda r: r["row_no"])
    return {"tab_id": tab["id"], "tab_name": tab.get("name") or tab["id"],
            "kind": tab.get("type"), "columns": columns or [], "rows": rows,
            "expected_rows": expected}


def collect_tables(docs: list[dict], on_status=None,
                   known_revs: dict[str, str] | None = None,
                   force: bool = False) -> list[dict]:
    """纯 HTTP 采集智能表格；known_revs 用于版本未变化时跳过。"""
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档")
    known_revs = known_revs or {}
    jar = _cookie_jar()
    results: list[dict] = []
    with _client(jar) as client:
        for doc in docs:
            name = doc.get("name") or doc.get("key")
            key = doc.get("key") or t.doc_key_of(doc.get("url"))
            _emit(on_status, f"读取文档版本：{name}")
            try:
                sk = doc_skeleton(client, doc, retries=4)
            except HttpError as exc:
                results.append({"doc_key": key, "name": name, "ok": False,
                                "error": str(exc), "sheets": [], "skipped": []})
                continue
            rev = sk["rev"]
            prev = known_revs.get(key)
            if not force and prev and rev and prev == rev:
                _emit(on_status, f"文档未变化（rev={rev}），跳过拉取")
                results.append({"doc_key": key, "name": name, "ok": True,
                                "rev": rev, "unchanged": True,
                                "sheets": [], "skipped": []})
                continue
            if not sk["tabs"]:
                results.append({"doc_key": key, "name": name, "ok": False,
                                "error": "文档没有返回任何工作表"
                                         "（可能已删除 / 无权限 / 是旧版文档）",
                                "sheets": [], "skipped": [], "rev": rev})
                continue

            sheets, skipped = [], []
            total = len(sk["tabs"])
            for index, tab in enumerate(sk["tabs"], start=1):
                kind = tab.get("type")
                if kind not in t.SYNC_KINDS:
                    skipped.append({"tab_id": tab.get("id"), "name": tab.get("name"),
                                    "kind": kind})
                    continue
                _emit(on_status, f"读取表格 {tab.get('name')}（{index}/{total}）")
                try:
                    sheet = _fetch_sheet(client, doc, tab)
                except HttpError as exc:
                    sheets = None
                    results.append({"doc_key": key, "name": name, "ok": False,
                                    "error": f"{tab.get('name')}: {exc}",
                                    "sheets": [], "skipped": [], "rev": rev})
                    break
                sheet["doc_key"] = key
                sheets.append(sheet)
            if sheets is None:
                continue

            # 关联字段（类型 18/21）存的是对方 recordId，要等全部表到齐再翻译成名称
            t.resolve_links(sheets)
            results.append({"doc_key": key, "name": name, "ok": True, "rev": rev,
                            "unchanged": False, "sheets": sheets, "skipped": skipped})
    return results


def _emit(on_status: Callable[[str], None] | None, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass
