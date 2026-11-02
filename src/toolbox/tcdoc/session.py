"""腾讯文档登录态 · 统一由 Cookie 管理器持有。

设计（避免两套登录态打架）：
1. **唯一事实来源** = Cookie 管理器（`auth.py`，平台 key `tcdoc`），
   存 `name=value; ...` 字符串，加密落在 `config/cookies.json`，
   用户可以在「Cookie 管理器」里查看 / 重新登录 / 清除。
2. **物化缓存** = `config/tcdoc_state.json`，是从这份 Cookie 导出的 Playwright
   `storage_state`（cookie + localStorage 快照）。必须双写的原因：腾讯文档
   localStorage 里也有一份登录信息（docs.qq.com 下几十个键），只回种 cookie
   会偶发「看着登录了、打开文档又要登录」。它不是第二份真相，删了能从 cookie 重建。

对外只暴露这几个动作：判定登录、把 cookie 注进浏览器上下文、登录后回写。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config as cfg_mod

PLATFORM = "tcdoc"
# 登录后才有的票据（用于判断「抓到了登录态」）
LOGIN_COOKIES = ("uid", "uid_key", "DOC_SID", "SID")
# 回种 cookie 时统一用的域：.qq.com 覆盖 docs.qq.com（Playwright 按后缀匹配）
COOKIE_DOMAIN = ".qq.com"


def _auth():
    """延迟取 auth 模块：toolbox 在 src 下，靠 sys.path 找到；找不到时降级不崩。"""
    try:
        import auth
        return auth
    except Exception:  # noqa: BLE001 - 认证模块缺席时按「未登录」处理
        return None


# ==================== 读写 ====================

def cookie_string() -> str:
    """当前生效的登录 Cookie（受 Cookie 管理器总开关 / 有效期控制）。"""
    mod = _auth()
    if mod is None:
        return ""
    try:
        return mod.get_cookie(PLATFORM) or ""
    except Exception:  # noqa: BLE001 - 认证模块异常不应打断采集
        return ""


def stored_cookie_string() -> str:
    """已存储的登录 Cookie（不看总开关，仅供 UI 展示登录态）。"""
    mod = _auth()
    if mod is None:
        return ""
    try:
        return mod.load_cookies().get(PLATFORM, "") or ""
    except Exception:  # noqa: BLE001
        return ""


def state_path() -> Path:
    """Playwright storage_state 的物化缓存路径。"""
    return cfg_mod.state_path()


def has_login() -> bool:
    """是否已有可用登录态：Cookie 管理器里有票，或本地还有 storage_state。"""
    return bool(cookie_string()) or state_path().exists()


def login_source() -> str:
    """给 UI 看的一句话：登录态从哪来。"""
    if cookie_string():
        return "Cookie 管理器"
    if state_path().exists():
        return "本地登录快照"
    return ""


# ==================== Cookie ↔ 上下文 ====================

def parse_cookie_string(text: str) -> dict[str, str]:
    """`name=value; name2=value2` → dict（忽略空项与无 `=` 的碎片）。"""
    jar: dict[str, str] = {}
    for chunk in str(text or "").split(";"):
        name, sep, value = chunk.strip().partition("=")
        name = name.strip()
        if name and sep:
            jar[name] = value.strip()
    return jar


def cookie_list(text: str | None = None, domain: str = COOKIE_DOMAIN) -> list[dict]:
    """Cookie 字符串 → Playwright `context.add_cookies` 需要的结构。"""
    jar = parse_cookie_string(text if text is not None else cookie_string())
    return [{"name": n, "value": v, "domain": domain, "path": "/"} for n, v in jar.items()]


def apply_to_context(context, text: str | None = None) -> int:
    """把登录 Cookie 注进已建好的浏览器上下文，返回注入条数。

    正常路径下上下文已经带了 storage_state，这里把管理器的票叠上去（同名覆盖）。
    关键细节：**沿用上下文里同名 cookie 的 domain**。否则同一个 cookie 会在
    `.qq.com` 和 `.docs.qq.com` 各存一份，请求会同时带上两条，
    服务端取哪条不确定，反而可能把登录态弄脏。
    """
    jar = parse_cookie_string(text if text is not None else cookie_string())
    if not jar:
        return 0
    domains: dict[str, str] = {}
    values: dict[str, str] = {}
    try:
        for cookie in context.cookies():
            name = cookie.get("name")
            if name and name not in domains:
                domains[name] = cookie.get("domain") or COOKIE_DOMAIN
                values[name] = cookie.get("value") or ""
    except Exception:  # noqa: BLE001 - 读不到现有 cookie 就按默认域注入
        domains, values = {}, {}
    cookies = []
    for name, value in jar.items():
        # 值都一样就别再注一次：host-only 与 `.qq.com` 在 Chromium 里是两个槽位，
        # 重复注入会变成同一条 cookie 存两份
        if values.get(name) == value:
            continue
        cookies.append({"name": name, "value": value,
                        "domain": domains.get(name, COOKIE_DOMAIN), "path": "/"})
    if not cookies:
        return 0
    try:
        context.add_cookies(cookies)
        return len(cookies)
    except Exception:  # noqa: BLE001 - 注入失败就靠 storage_state，不阻断采集
        return 0


def build_state_dict(text: str | None = None) -> dict:
    """仅用 Cookie 造一份最小 storage_state（无 localStorage 时的兜底）。"""
    return {"cookies": [dict(c, expires=-1, httpOnly=False, secure=True,
                             sameSite="Lax") for c in cookie_list(text)],
            "origins": []}


# ==================== 登录后回写 ====================

def save_from_context(context, state: dict | None = None) -> bool:
    """登录成功后调用：落 storage_state 快照，并把 Cookie 写进 Cookie 管理器。

    `state` 可传入浏览器线程里已抓好的 `context.storage_state()` 结果
    （Playwright sync API 非线程安全，抓取要发生在浏览器所在线程）。
    返回是否写成功（失败不抛，采集照常进行）。
    """
    ok = False
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if state:
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            ok = True
        elif context is not None:
            context.storage_state(path=str(path))
            ok = True
    except Exception:  # noqa: BLE001
        ok = False

    text = cookie_string_from_state(state) if state else ""
    if not text and ok:
        text = cookie_string_from_state(read_state())
    if text:
        mod = _auth()
        if mod is not None:
            try:
                mod.set_cookie(PLATFORM, text)
            except Exception:  # noqa: BLE001
                pass
    return ok


def save_state_snapshot(state: dict | None) -> bool:
    """把浏览器线程抓到的 storage_state 落盘 + 同步 Cookie（主线程可调用）。"""
    if not state:
        return False
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False
    text = cookie_string_from_state(state)
    mod = _auth()
    if text and mod is not None:
        try:
            mod.set_cookie(PLATFORM, text)
        except Exception:  # noqa: BLE001
            pass
    return True


def read_state() -> dict:
    path = state_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def cookie_string_from_state(state: dict | None = None) -> str:
    """从 storage_state 里提取 `name=value; ...`（只取 qq.com 域的 cookie）。"""
    state = state if state is not None else read_state()
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in (state.get("cookies") or []):
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        domain = str(cookie.get("domain") or "")
        if not name or value is None or "qq.com" not in domain:
            continue
        if name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def sync_from_state() -> bool:
    """本地已有登录快照、但 Cookie 管理器里没有 → 补登记（旧用户一次性迁移）。"""
    if stored_cookie_string():
        return False
    text = cookie_string_from_state()
    if not text:
        return False
    mod = _auth()
    if mod is None:
        return False
    try:
        mod.set_cookie(PLATFORM, text)
        return True
    except Exception:  # noqa: BLE001
        return False


def clear() -> None:
    """清除登录态：Cookie 管理器里的票 + 本地快照一起删。"""
    mod = _auth()
    if mod is not None:
        try:
            mod.clear_cookie(PLATFORM)
        except Exception:  # noqa: BLE001
            pass
    try:
        path = state_path()
        if path.exists():
            path.unlink()
    except OSError:
        pass
