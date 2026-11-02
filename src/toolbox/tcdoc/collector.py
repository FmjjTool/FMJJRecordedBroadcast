"""腾讯文档监控 · 浏览器采集。

采集口径（沿用原独立工具，稳定）：
1. 先挂响应监听，页面加载时自动请求的统计接口直接取数（get_count → 成员数，
   browser_list → 浏览量 / 已查看人数）；
2. 接口没命中才走界面兜底：点「文档成员」「浏览记录」等入口，从弹层文案里扫数字；
3. AI 文档（/aio/）不提供浏览量，只记录成员数。

浏览器统一走 toolbox.browser.launch（系统 Edge → Chrome → 本地内核），
不单独维护渠道列表。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from toolbox.browser import launch as pw_launch

from . import config as cfg_mod
from . import session, tables

PAGE_TIMEOUT = 90_000
LOGIN_TIMEOUT = 900

# 页面可用性等待 / 登录态识别
LOGIN_HINTS = ("登录腾讯文档", "立即登录", "扫码登录")

# 弹层里的汇总文案，如「浏览量 16566」「文档成员 · 1704」
VIEW_PATTERNS = (
    r"浏览量\s*[:：]?\s*(\d{1,9})",
    r"浏览(?:次数|人数|总量)\s*[:：]?\s*(\d{1,9})",
)
MEMBER_PATTERNS = (
    r"文档成员\s*[·•:：]?\s*(\d{1,9})",
    r"成员\s*[·•:]\s*(\d{1,9})",
    r"共\s*(\d{1,9})\s*[个位]?成员",
    r"(\d{1,9})\s*位成员",
    r"成员数\s*[:：]?\s*(\d{1,9})",
)

MEMBER_ENTRIES = ["文档成员", "成员管理", "文档成员管理", "协作成员", "成员"]
VIEW_ENTRIES = ["浏览记录", "浏览统计", "文档浏览", "浏览量"]

OVERLAY_SELECTORS = (
    ".dui-modal",
    "[class*=dui-modal]",
    "[class*=drawer]",
    "[class*=dui-dialog]",
    "[class*=dialog]",
    "[class*=popover]",
)

CLICKABLE_ANCESTOR = (
    "e => e.closest('button, [role=button], [class*=button], [class*=trigger]') || e"
)

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--disable-notifications"]


class LoginRequired(RuntimeError):
    """登录态缺失或已过期。"""


# ==================== 浏览器基础 ====================

def _sync_api():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # noqa: BLE001 - 缺依赖时给可操作提示
        raise RuntimeError(
            "未安装 playwright，无法采集腾讯文档。\n"
            "请执行：pip install playwright（首次还需重新打开本页）") from exc
    return sync_playwright


def _new_context(browser, state: Path | None):
    """建浏览器上下文：先套登录快照，再叠 Cookie 管理器里的票（以管理器为准）。"""
    kwargs = {
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport": {"width": 1500, "height": 950},
    }
    if state and Path(state).exists():
        kwargs["storage_state"] = str(state)
    context = browser.new_context(**kwargs)
    # Cookie 管理器里的登录态优先级最高：用户在管理器里重新登录/粘贴后，
    # 即使本地快照里还是旧票，也以管理器为准（同名覆盖）
    session.apply_to_context(context)
    return context


def _emit(on_status, text: str) -> None:
    if on_status:
        try:
            on_status(text)
        except Exception:  # noqa: BLE001 - 状态回调不许影响采集
            pass


def _safe_text(page, selector: str) -> str:
    try:
        return page.inner_text(selector) or ""
    except Exception:  # noqa: BLE001
        return ""


def _body_text(page, limit: int = 400_000) -> str:
    try:
        return (page.inner_text("body") or "")[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _wait_ready(page, timeout_ms: int = PAGE_TIMEOUT) -> None:
    try:
        page.wait_for_selector("#workbench-titlebar", timeout=timeout_ms)
    except Exception:  # noqa: BLE001 - 页面结构可能改版，继续等待即可
        pass
    deadline = time.time() + min(timeout_ms / 1000, 30)
    while time.time() < deadline:
        if "正在下载" not in _body_text(page, 5000):
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(1500)


def _is_login_required(page) -> bool:
    bar = _safe_text(page, "#workbench-titlebar")
    if any(hint in bar for hint in LOGIN_HINTS):
        return True
    body = _body_text(page, 5000)
    return any(hint in body for hint in LOGIN_HINTS) and "只能查看" in body


# ==================== 页面交互 ====================

def _overlay_text(page) -> str:
    chunks: list[str] = []
    for selector in OVERLAY_SELECTORS:
        try:
            handles = page.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            continue
        for handle in handles:
            try:
                if not handle.is_visible():
                    continue
                text = handle.inner_text()
                if text and len(text) < 20_000:
                    chunks.append(text)
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(chunks)


def _scan(page, patterns, timeout: float = 20.0) -> int | None:
    """轮询弹层与页面文本，命中「浏览量 16566」这类汇总文案。"""
    deadline = time.time() + timeout
    while True:
        for text in (_overlay_text(page), _body_text(page)):
            for pattern in patterns:
                found = re.findall(pattern, text)
                if found:
                    return int(found[-1])
        if time.time() > deadline:
            return None
        page.wait_for_timeout(500)


def _click_text(page, texts, exact: bool = False) -> bool:
    """按可见短文本点击入口；跳过弹窗内部文本和说明性长文本。"""
    for text in texts:
        try:
            locator = page.get_by_text(text, exact=exact)
        except Exception:  # noqa: BLE001
            continue
        for index in range(min(locator.count(), 12)):
            element = locator.nth(index)
            try:
                if not element.is_visible():
                    continue
                label = (element.inner_text() or "").strip()
                if len(label) > 12:
                    continue
                inside_overlay = element.evaluate(
                    "e => !!e.closest('.dui-modal, [class*=drawer], [class*=dui-dialog]')")
                if inside_overlay:
                    continue
                target = element.evaluate_handle(CLICKABLE_ANCESTOR)
                target.as_element().click(timeout=4000)
                page.wait_for_timeout(700)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _click_avatars(page) -> bool:
    """点击标题栏协作者头像，通常可打开「文档成员管理」。"""
    selectors = (
        "#workbench-titlebar [class*=avatar]",
        "#workbench-titlebar [class*=avator]",
        "[class*=collaborator]",
    )
    for selector in selectors:
        try:
            handles = page.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            continue
        for handle in handles:
            try:
                if not handle.is_visible():
                    continue
                target = handle.evaluate_handle(CLICKABLE_ANCESTOR)
                target.as_element().click(timeout=4000)
                page.wait_for_timeout(800)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _click_through_more(page, texts) -> bool:
    """先展开「更多」菜单，再在里面找目标入口。"""
    try:
        more = page.get_by_text("更多", exact=True)
    except Exception:  # noqa: BLE001
        return False
    for index in range(min(more.count(), 6)):
        element = more.nth(index)
        try:
            if not element.is_visible():
                continue
            element.click(timeout=3000)
            page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            continue
        if _click_text(page, texts, exact=True) or _click_text(page, texts):
            return True
        _close_overlays(page)
    return False


def _close_overlays(page) -> None:
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            return


def _read_members(page) -> int:
    value = _scan(page, MEMBER_PATTERNS, timeout=3)
    if value:
        return value
    if not _click_text(page, MEMBER_ENTRIES):
        if not _click_avatars(page):
            _click_through_more(page, MEMBER_ENTRIES)
    value = _scan(page, MEMBER_PATTERNS, timeout=20)
    _close_overlays(page)
    if not value:
        raise RuntimeError("未能读取到「文档成员」数量，页面入口可能已改版"
                           "（可在文档管理页生成诊断包）")
    return value


def _read_views(page) -> int:
    value = _scan(page, VIEW_PATTERNS, timeout=3)
    if value:
        return value
    if not _click_text(page, VIEW_ENTRIES):
        _click_through_more(page, VIEW_ENTRIES)
    value = _scan(page, VIEW_PATTERNS, timeout=20)
    _close_overlays(page)
    if not value:
        raise RuntimeError("未能读取到「浏览量」，页面入口可能已改版"
                           "（可在文档管理页生成诊断包）")
    return value


# ==================== 接口取数 ====================

def _merge_api(responses, data: dict) -> None:
    for response in responses:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 或响应已释放
            continue
        if not isinstance(body, dict):
            continue
        url = response.url
        if "get_count" in url:
            result = body.get("result") or {}
            if isinstance(result.get("count"), int):
                data["members"] = result["count"]
        elif "browser_list" in url:
            if isinstance(body.get("views"), int):
                data["views"] = body["views"]
            if isinstance(body.get("total"), int):
                data["readers"] = body["total"]


def _wait_api(page, responses, timeout: float = 25.0, data: dict | None = None) -> dict:
    deadline = time.time() + timeout
    data = dict(data or {})
    while True:
        _merge_api(responses, data)
        if data.get("members") is not None and data.get("views") is not None:
            return data
        if time.time() > deadline:
            return data
        page.wait_for_timeout(700)


def _open_share_panel(page) -> bool:
    """智能表格 / AI 文档等类型打开「分享」面板才会请求浏览统计接口。"""
    try:
        locator = page.get_by_text("分享", exact=True)
    except Exception:  # noqa: BLE001
        return False
    for index in range(min(locator.count(), 6)):
        element = locator.nth(index)
        try:
            if not element.is_visible():
                continue
            element.click(timeout=3000)
            page.wait_for_timeout(1200)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ==================== 对外接口 ====================

def login(docs: list[dict], on_status=None, timeout: int = LOGIN_TIMEOUT) -> bool:
    """打开有界面浏览器扫码登录，成功后写回 Cookie 管理器（唯一事实来源）。"""
    if not docs:
        raise RuntimeError("请先添加至少一个腾讯文档")
    state = cfg_mod.state_path()
    sync_playwright = _sync_api()

    _emit(on_status, "正在打开浏览器，请在窗口中扫码登录……")
    with sync_playwright() as p:
        browser = pw_launch(p, headless=False, args=_LAUNCH_ARGS)
        context = _new_context(browser, state if state.exists() else None)
        page = context.new_page()
        page.set_default_timeout(20_000)
        page.goto(docs[0]["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_ready(page)

        _emit(on_status, "已打开文档，请完成扫码登录（最长等待 "
                         f"{timeout // 60} 分钟）")
        deadline = time.time() + timeout
        last_tip = 0.0
        while time.time() < deadline:
            if page.is_closed():
                break
            if not _is_login_required(page):
                break
            if time.time() - last_tip > 60:
                _emit(on_status, "仍在等待登录……请扫码并在手机上确认")
                last_tip = time.time()
            page.wait_for_timeout(3000)

        ok = (not page.is_closed()) and (not _is_login_required(page))
        if ok:
            try:
                session.save_from_context(context)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"登录成功但保存登录态失败：{exc}") from exc
        browser.close()
    return ok


def collect(docs: list[dict], headless: bool = True, on_status=None,
            debug: bool = True, sync_tables: bool = False) -> list[dict]:
    """批量采集多个文档，共用一次浏览器会话；单文档失败不影响其它文档。

    sync_tables=True 时，在同一会话里顺带把智能表格数据抓下来（省一次浏览器启动），
    结果挂在每个结果的 tables 字段上，由 job 负责落库与导出。
    """
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档，请先在「文档管理」里添加")
    state = cfg_mod.state_path()
    if not session.has_login():
        raise LoginRequired(
            "尚未登录腾讯文档：请到「Cookie 管理器」登录（或点本页「🔐 登录」）")
    sync_playwright = _sync_api()

    results: list[dict] = []
    with sync_playwright() as p:
        browser = pw_launch(p, headless=headless, args=_LAUNCH_ARGS)
        context = _new_context(browser, state if state.exists() else None)
        try:
            total = len(docs)
            for index, doc in enumerate(docs, start=1):
                _emit(on_status, f"（{index}/{total}）正在采集：{doc['name']}")
                result = _collect_one(context, doc, debug)
                results.append(result)
                if sync_tables and (doc.get("kind") or "").lower() in tables.SYNC_KINDS:
                    _sync_tables_one(context, doc, result, on_status)
            try:
                # 刷新登录快照 + 同步回 Cookie 管理器（票续期后两边都更新）
                session.save_from_context(context)
            except Exception:  # noqa: BLE001 - 刷新登录态失败不影响本次结果
                pass
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return results


def _sync_tables_one(context, doc: dict, result: dict, on_status=None) -> None:
    """在既有会话里抓该文档的智能表格；失败只记录，不影响浏览量采集结果。"""
    try:
        _emit(on_status, f"同步表格：{doc['name']}")
        result["tables"] = tables.collect_in_context(context, [doc], on_status)[0]
    except Exception as exc:  # noqa: BLE001 - 表格同步失败不影响主流程
        result["tables_error"] = str(exc)


def _collect_one(context, doc: dict, debug: bool) -> dict:
    page = context.new_page()
    page.set_default_timeout(20_000)
    responses: list = []
    page.on(
        "response",
        lambda r: responses.append(r)
        if ("get_count" in r.url or "browser_list" in r.url) else None,
    )
    result = {"doc_key": doc["key"], "name": doc["name"], "url": doc["url"], "ok": False}
    try:
        page.goto(doc["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_ready(page)
        if _is_login_required(page):
            raise LoginRequired("登录态已失效：请到「Cookie 管理器」重新登录腾讯文档")

        is_aio = (doc.get("kind") or "").lower() == "aio"
        api = _wait_api(page, responses)
        if api.get("views") is None and not is_aio:
            if _open_share_panel(page):
                api = _wait_api(page, responses, timeout=10, data=api)

        members, views = api.get("members"), api.get("views")
        source = "接口"
        if members is None:
            members = _read_members(page)
            source = "界面兜底"
        if views is None and not is_aio:
            views = _read_views(page)
            source = "界面兜底"
        if views is None:
            source = "接口（该文档无浏览量）"

        result.update(ok=True, views=views, members=members,
                      readers=api.get("readers"), source=source, title=page.title())
    except Exception as exc:  # noqa: BLE001 - 单文档失败只记录原因
        result["error"] = str(exc)
        if debug:
            try:
                _dump_debug(page, doc["key"])
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
    return result


def _dump_debug(page, tag: str) -> Path:
    """把页面文本、标题栏元素和截图落盘，便于排查页面改版。"""
    out_dir = cfg_mod.debug_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = out_dir / f"{tag}_{stamp}"
    lines = [f"时间: {stamp}", f"URL: {page.url}", "",
             "==== 标题栏 ====", _safe_text(page, "#workbench-titlebar"), ""]
    lines.append("==== 标题栏可见元素 ====")
    try:
        for handle in page.query_selector_all("#workbench-titlebar *"):
            try:
                if not handle.is_visible():
                    continue
                text = (handle.inner_text() or "").strip().replace("\n", " ")
                if not text or len(text) > 40:
                    continue
                tag_name = handle.evaluate("e => e.tagName")
                class_name = (handle.get_attribute("class") or "")[:80]
                lines.append(f"<{tag_name}> class={class_name!r} text={text!r}")
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    lines += ["", "==== 弹层文本 ====", _overlay_text(page), "",
              "==== 页面文本 ====", _body_text(page, 20_000)]
    txt_path = stem.with_suffix(".txt")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    try:
        page.screenshot(path=str(stem.with_suffix(".png")), full_page=False)
    except Exception:  # noqa: BLE001
        pass
    return txt_path


def probe(doc: dict, on_status=None) -> Path:
    """诊断模式：打开可见浏览器，记录入口探测结果（页面改版时定位新选择器）。"""
    sync_playwright = _sync_api()
    state = cfg_mod.state_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = cfg_mod.debug_dir() / f"probe_{stamp}.txt"
    lines: list[str] = []
    requests: list[str] = []

    _emit(on_status, "正在打开浏览器生成诊断包……")
    with sync_playwright() as p:
        browser = pw_launch(p, headless=False, args=_LAUNCH_ARGS)
        context = _new_context(browser, state if state.exists() else None)
        page = context.new_page()
        page.set_default_timeout(20_000)
        page.on("request", lambda r: requests.append(r.url)
                if re.search(r"(stat|member|visit|view|count|dash|cgi-bin)", r.url, re.I)
                else None)
        page.goto(doc["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_ready(page)

        lines += [f"时间: {stamp}", f"URL: {page.url}", f"标题: {page.title()}",
                  f"登录态: {'未登录' if _is_login_required(page) else '已登录'}", "",
                  "==== 标题栏可见元素 ===="]
        try:
            for handle in page.query_selector_all("#workbench-titlebar *"):
                try:
                    if not handle.is_visible():
                        continue
                    text = (handle.inner_text() or "").strip().replace("\n", " ")
                    if not text or len(text) > 40:
                        continue
                    lines.append(f"<{handle.evaluate('e => e.tagName')}>"
                                 f" class={(handle.get_attribute('class') or '')[:80]!r}"
                                 f" text={text!r}")
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            lines.append("标题栏定位失败")
        try:
            page.screenshot(path=str(cfg_mod.debug_dir() / f"probe_{stamp}_1.png"))
        except Exception:  # noqa: BLE001
            pass

        for label, entries, patterns in (
            ("成员", MEMBER_ENTRIES, MEMBER_PATTERNS),
            ("浏览", VIEW_ENTRIES, VIEW_PATTERNS),
        ):
            lines += ["", f"==== 尝试入口：{label} ===="]
            clicked = _click_text(page, entries)
            lines.append(f"直接点击入口: {clicked}")
            if not clicked:
                more_ok = _click_through_more(page, entries)
                lines.append(f"更多菜单入口: {more_ok}")
                clicked = more_ok
            if clicked:
                page.wait_for_timeout(1200)
                lines.append("弹层文本:\n" + (_overlay_text(page) or "(空)"))
                lines.append(f"命中数值: {_scan(page, patterns, timeout=6)}")
            _close_overlays(page)

        lines += ["", "==== 相关网络请求 ===="]
        lines += requests[-80:]
        out.write_text("\n".join(lines), encoding="utf-8")
        context.close()
        browser.close()
    return out
