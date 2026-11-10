"""腾讯文档监控 · 浏览器能力（正文预览 / 登录 / 诊断 + 旧版指标兜底）。

浏览量和表格同步已改走 `http_api` 纯 HTTP 直连，不再启动浏览器；
本模块保留浏览器用于：
1. 正文预览（AI 文档逐页抓取，页面交互无法用接口替代）；
2. Cookie 管理器登录；
3. 页面改版时的诊断包；
4. `collect()` 旧版浏览器指标采集，作为接口异常时的备用路径。

浏览器统一走 toolbox.browser.launch（系统 Edge → Chrome → 本地内核），
不单独维护渠道列表。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from toolbox.browser import launch as pw_launch

from . import config as cfg_mod
from . import session

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


def _fetch_web_views(page) -> dict:
    """直接调文档自己的统计接口取浏览量 / 已查看人数。

    智能表格新版分享面板已经不展示「浏览记录」，等页面自己发 browser_list 请求
    会不稳定；这里用 opendoc 拿到真实 padId，再直接请求 browser_list。
    """
    try:
        info = page.evaluate("""async () => {
            const m = document.cookie.match(/(?:^|; )TOK=([^;]+)/);
            const tok = m ? decodeURIComponent(m[1]) : '';
            const key = location.pathname.split('/').filter(Boolean).pop() || '';
            const r = await fetch('/dop-api/opendoc?u=&noEscape=1&id='
                + encodeURIComponent(key) + '&normal=1&outformat=1&wb=1&nowb=0',
                {credentials: 'include'});
            if (!r.ok) return null;
            const raw = (await r.text()).trim();
            let data;
            try {
                // 大部分是纯 JSON；旧版接口可能包成 JSONP，先看首字符再剥壳
                if (raw[0] === '{' || raw[0] === '[') {
                    data = JSON.parse(raw);
                } else {
                    const i = raw.indexOf('('), j = raw.lastIndexOf(')');
                    data = JSON.parse(i >= 0 && j > i ? raw.slice(i + 1, j) : raw);
                }
            } catch (e) { return null; }
            const cv = data.clientVars || {};
            if (!cv.padId) return null;
            const url = '/cgi-bin/online_docs/browser_list?domain_id='
                + encodeURIComponent(cv.domainId || '300000000')
                + '&pad_id=' + encodeURIComponent(cv.padId)
                + '&start=0&limit=15&xsrf=' + encodeURIComponent(tok);
            const r2 = await fetch(url, {credentials: 'include'});
            if (!r2.ok) return null;
            const d2 = await r2.json();
            return {views: d2.views, readers: d2.total, pad: cv.padId};
        }""")
    except Exception:  # noqa: BLE001 - 接口失败继续走界面兜底
        return {}
    if not isinstance(info, dict):
        return {}
    out = {}
    if isinstance(info.get("views"), int):
        out["views"] = info["views"]
    if isinstance(info.get("readers"), int):
        out["readers"] = info["readers"]
    return out


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


# 文档正文预览：不同文档类型的正文容器（取不到就回退到整页文本）
PREVIEW_SELECTORS = {
    "aio": ("#sc-page-content", "#sc-page-edit-content", "#grid-root"),
    "doc": ("#workbench-content-container", "#grid-root"),
    "slide": ("#workbench-content-container",),
    "form": ("#workbench-content-container",),
    "pdf": ("#workbench-content-container",),
}
_ZW_SPACE = "\u200b\u200c\u200d\ufeff"


_PREVIEW_TEXT_JS = """(selector) => {
    const box = document.querySelector(selector);
    if (!box) return null;
    // 代码块的行号栏不属于正文：读取时临时隐藏，避免正文里混进 1/2/3…
    const gutters = Array.from(box.querySelectorAll(
        '.sc-code-linenumber-wrapper, .sc-block-code-linenumber-wrapper, .sc-code-linenumber-item'));
    const saved = gutters.map(el => el.style.display);
    try {
        gutters.forEach(el => { el.style.display = 'none'; });
        return box.innerText || '';
    } finally {
        gutters.forEach((el, i) => { el.style.display = saved[i]; });
    }
}"""


def _preview_text(page, selectors) -> str:
    """按候选顺序取第一个存在的正文容器（容器未渲染时返回空串，外层会重试）。"""
    for selector in selectors:
        try:
            text = page.evaluate(_PREVIEW_TEXT_JS, selector)
        except Exception:  # noqa: BLE001 - 选择器/页面异常
            continue
        if text is not None:
            return text or ""
    return ""


def _clean_preview_text(text: str) -> str:
    """去掉零宽字符与连续空行，避免预览框里出现大片空白。"""
    lines: list[str] = []
    for raw in str(text or "").translate({ord(ch): None for ch in _ZW_SPACE}).splitlines():
        line = raw.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# ==================== AI 文档多页面预览 ====================
# /aio/ 是「一篇文档里挂着很多子页面」的结构，左侧页面树里每个节点对应 ?p=<id>。
# 展开整棵树后逐页点击读取，才能拿到用户真正需要的全部正文。

AIO_SIDEBAR = "#sc-sidebar-section"
AIO_TREE_NODE = f"{AIO_SIDEBAR} .sc-tree-node"
_AIO_EXPANDED_CLASS = "css-xxkc6x"  # 展开态箭头（哈希类名，改版时靠点击前后节点数兜底判断）


def _aio_pages(page) -> list[dict]:
    """侧栏页面树 → [{id,title,level}]（调用前需先展开全部节点）。"""
    try:
        return page.evaluate("""() => Array.from(
            document.querySelectorAll('#sc-sidebar-section .sc-tree-node')
        ).map(e => {
            const wrap = e.closest('[data-block-id]');
            const title = e.querySelector('.sc-page-item-rich-title');
            const pad = parseInt(getComputedStyle(e).paddingLeft) || 8;
            return {
                id: wrap ? wrap.getAttribute('data-block-id') : '',
                title: title ? (title.innerText || '').trim() : '',
                level: Math.max(0, Math.round((pad - 8) / 12)),
            };
        }).filter(x => x.id)""")
    except Exception:  # noqa: BLE001
        return []


def _expand_aio_tree(page) -> tuple[int, bool]:
    """展开左侧页面树（含多级子页面），返回 (页面节点数, 是否完整展开)。"""
    done: set[str] = set()
    complete = True
    for _ in range(300):
        nodes = page.query_selector_all(AIO_TREE_NODE)
        target = None
        for index, node in enumerate(nodes):
            arrow = node.query_selector(':scope > div > [data-tid="sc-button"]')
            if arrow is None:
                continue
            pid = node.evaluate(
                "e => { const w = e.closest('[data-block-id]');"
                " return w ? w.getAttribute('data-block-id') : ''; }")
            key = pid or f"__anon_{index}"
            if key in done:
                continue
            target = (arrow, key)
            break
        if target is None:
            break
        arrow, key = target
        try:
            if _AIO_EXPANDED_CLASS in (arrow.get_attribute("class") or ""):
                done.add(key)
                continue
            before = len(nodes)
            arrow.click()
            after = before
            deadline = time.time() + 2
            while time.time() < deadline:
                page.wait_for_timeout(150)
                after = len(page.query_selector_all(AIO_TREE_NODE))
                if after != before:
                    break
            if after < before:
                # 误点了展开态：再点一次恢复，避免把已显示的子页面收起来
                arrow.click()
                page.wait_for_timeout(450)
        except Exception:  # noqa: BLE001 - 单个节点异常不影响整体展开
            complete = False
        done.add(key)
    else:
        complete = False  # 节点太多没展开完：本次不做删除判定

    try:
        fully_expanded = page.evaluate("""() => Array.from(
            document.querySelectorAll('#sc-sidebar-section .sc-tree-node')
        ).every(e => {
            const arrow = e.querySelector(':scope > div > [data-tid="sc-button"]');
            return !arrow || arrow.className.includes('css-xxkc6x');
        })""")
    except Exception:  # noqa: BLE001 - 类名改版时按不完整处理（宁可不删）
        fully_expanded = False
    return len(page.query_selector_all(AIO_TREE_NODE)), bool(complete and fully_expanded)


def _aio_page_text(page, selectors, prev_text: str) -> str:
    """切页后等正文渲染完：内容非空、与上一篇不同，并稳定一小段时间再取。

    子页面里的代码块 / 附件 / 子页面列表是分批渲染的，看到第一段文字就返回会截断，
    所以用「连续几次读到的内容一致」作为完成信号。
    """
    start = time.time()
    last, stable_since = "", 0.0
    while True:
        text = _preview_text(page, selectors)
        now = time.time()
        elapsed = now - start
        if text.strip() and text != prev_text:
            if text == last:
                if now - stable_since >= 0.4 and elapsed >= 0.85:
                    return text
            else:
                last, stable_since = text, now
        elif text.strip() and text == prev_text and elapsed >= 2.5:
            return text  # 两篇内容完全相同（少见）：别无限等下去
        if elapsed > 2.5 and not text.strip():
            try:
                if page.query_selector("#sc-page-content") is not None:
                    return ""  # 容器已渲染但确实没有正文
            except Exception:  # noqa: BLE001
                pass
        if elapsed > 10:
            return text
        page.wait_for_timeout(150)


def _read_aio_pages(page, pages: list[dict], on_status) -> list[dict]:
    """逐页点击侧栏切换，抓取每篇正文（进度通过 on_status 汇报）。"""
    selectors = PREVIEW_SELECTORS["aio"] + ("#workbench-content-container",)
    results: list[dict] = []
    prev = ""
    total = len(pages)
    for i, item in enumerate(pages, 1):
        title = item.get("title") or f"第 {i} 篇"
        _emit(on_status, f"正在读取第 {i}/{total} 篇：{title}")
        text, error = "", ""
        try:
            entry = page.query_selector(
                f'#sc-sidebar-section [data-block-id="{item["id"]}"] .sc-page-item-rich-title')
            if entry is None:
                raise RuntimeError("侧栏中找不到该页面")
            entry.click()
            page.wait_for_function(
                "pid => location.search.includes('p=' + pid)",
                arg=item["id"], timeout=8000)
            text = _aio_page_text(page, selectors, prev)
            prev = text or prev
        except Exception as exc:  # noqa: BLE001 - 单页失败继续抓其余页面
            error = str(exc)[:120]
        results.append({**item, "text": _clean_preview_text(text), "error": error})
    return results


def join_pages(pages: list[dict]) -> str:
    """多篇正文合并成一段可滚动的文本，篇与篇之间带标题分隔。"""
    chunks = []
    for i, item in enumerate(pages, 1):
        title = item.get("title") or f"第 {i} 篇"
        body = item.get("error") or item.get("text") or "（空白页）"
        chunks.append(f"━━━━━━━━ {i}. {title} ━━━━━━━━\n{body}")
    return "\n\n".join(chunks)


def _finish_preview(doc: dict, page, text: str, pages: list[dict],
                    complete: bool) -> dict:
    """组装预览结果，并自动存档（落库 + Markdown 快照）；存档失败不影响预览。"""
    result = {"ok": True, "title": doc.get("name") or page.title(),
              "url": doc.get("url") or page.url, "text": text, "pages": pages}
    try:
        from . import archive
        result["save"] = archive.save_preview(doc, pages, text, complete=complete)
    except Exception as exc:  # noqa: BLE001 - 本地存档失败不能挡住预览
        result["save_error"] = str(exc)[:200]
    return result


def preview(doc: dict, on_status=None) -> dict:
    """打开文档并提取正文（软件内只读预览；表格类请在「表格同步」里看）。"""
    sync_playwright = _sync_api()
    state = cfg_mod.state_path()
    _emit(on_status, "正在打开文档……")
    with sync_playwright() as p:
        browser = pw_launch(p, headless=True, args=_LAUNCH_ARGS)
        context = _new_context(browser, state if state.exists() else None)
        try:
            page = context.new_page()
            page.set_default_timeout(20_000)
            page.goto(doc["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            # 预览不需要等完整初始化：给标题栏 15 秒，正文由下面的轮询等待
            try:
                page.wait_for_selector("#workbench-titlebar", timeout=15_000)
            except Exception:  # noqa: BLE001 - 页面结构可能改版
                pass
            page.wait_for_timeout(1200)
            if _is_login_required(page):
                raise LoginRequired("登录态已失效：请到「Cookie 管理器」重新登录腾讯文档")

            _emit(on_status, "正在读取正文……")
            kind = (doc.get("kind") or "").lower()
            selectors = PREVIEW_SELECTORS.get(kind, ()) + ("#workbench-content-container",)

            # AI 文档：展开左侧页面树，逐篇抓取（只抓当前页会漏掉其余子页面）
            tree_complete = False
            if kind == "aio":
                try:
                    _emit(on_status, "正在展开文档页面列表……")
                    _, tree_complete = _expand_aio_tree(page)
                    pages = _aio_pages(page)
                    if len(pages) > 1:
                        _emit(on_status, f"发现 {len(pages)} 篇页面，正在逐篇读取……")
                        results = _read_aio_pages(page, pages, on_status)
                        return _finish_preview(doc, page, join_pages(results),
                                               results, complete=tree_complete)
                except Exception:  # noqa: BLE001 - 页面树异常时退回单篇预览
                    tree_complete = False

            text = ""
            deadline = time.time() + 15
            while True:
                text = _preview_text(page, selectors)
                if len(text.strip()) >= 80 or time.time() > deadline:
                    break
                page.wait_for_timeout(800)
            if not text.strip():
                text = _body_text(page, 200_000)
            text = _clean_preview_text(text)
            if not text:
                raise RuntimeError("没有读取到文档正文，页面可能尚未加载完成或结构已改版")

            page_id = "main"
            if kind == "aio":
                try:
                    page_id = (parse_qs(urlparse(page.url).query).get("p") or ["main"])[0]
                except Exception:  # noqa: BLE001
                    page_id = "main"
            page_item = {"id": page_id, "title": doc.get("name") or page.title(),
                         "level": 0, "text": text, "error": ""}
            return _finish_preview(doc, page, text, [page_item],
                                   complete=(kind != "aio"))
        finally:
            try:
                context.close()
            finally:
                browser.close()


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
            debug: bool = True, save_session: bool = True) -> list[dict]:
    """旧版浏览器指标采集（保留备用；正常链路走 http_api.collect_metrics）。

    单文档失败不影响其它文档。定时任务并行采集时传 save_session=False，
    避免多个浏览器线程同时改写登录快照。
    """
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档，请先在「文档管理」里添加")
    state = cfg_mod.state_path()
    if not session.has_login():
        raise LoginRequired(
            "尚未登录腾讯文档：请到「Cookie 管理器」的「腾讯文档」一行登录")
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
            try:
                if save_session:
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
        # 分享面板改版后不再主动请求浏览量；先直接查统计接口，再退回页面监听/界面兜底
        api = {} if is_aio else _fetch_web_views(page)
        api = _wait_api(page, responses,
                        timeout=15 if api.get("views") is not None else 25, data=api)
        if api.get("views") is None and not is_aio:
            if _open_share_panel(page):
                api = _wait_api(page, responses, timeout=10, data=api)

        members, views = api.get("members"), api.get("views")
        source = "接口"
        if members is None:
            members = _read_members(page)
            source = "界面兜底"
        if views is None and not is_aio:
            try:
                views = _read_views(page)
                source = "界面兜底"
            except Exception:  # noqa: BLE001 - 浏览量缺失不应让整篇文档失败
                source = "未读取到浏览量（按无浏览量记录）"
        if views is None and is_aio:
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
