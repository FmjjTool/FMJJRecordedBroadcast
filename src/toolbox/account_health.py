"""账号登录状态中间层：登录态检测只在这里发生。

三层职责分工：
    account_registry   账号身份与所属功能（四份主数据是唯一事实来源）
    account_health     各平台登录态检测 + 结果落盘   ← 本模块
    GUI                「登录状态」页签 / 侧栏卡片，只读 status() / states()

为什么必须单独一层：
- 检测要联网（每个平台一次接口校验），不能放在绘制路径上；
- 结果要跨会话保留（「最后正常时间」只有落盘才有意义）；
- 登录态按平台维护（一个平台一张票），同一份结果要给「登录状态」页签和侧栏卡片
  同时使用，检测逻辑只写一处，避免两边各问一遍、结论互相打架。

状态（界面绿/黄/红/灰）：
    ok      已登录（站内接口确认票有效）
    warn    已保存 · 无法验证（平台没有校验通道 / 接口不可达）
    bad     未登录 / 登录态已失效
    unknown 还没检测过
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 常量 ====================

CACHE_NAME = "account_health.json"
CACHE_VERSION = 2  # v2：记录键从「账号」改为「平台登录态」
# 登录态多久算「过期」：只用于界面标注（stale），不再驱动自动重测——
# 自动检测只在进入软件时跑一遍、以及单个平台刚登录时补测一次。
STALE_SECONDS = 6 * 3600
DEFAULT_WORKERS = 4

STATE_OK = "ok"
STATE_WARN = "warn"
STATE_BAD = "bad"
STATE_UNKNOWN = "unknown"

STATE_TEXT = {STATE_OK: "已登录", STATE_WARN: "无法验证", STATE_BAD: "未登录",
              STATE_UNKNOWN: "未检测"}

_LOCK = threading.Lock()


# ==================== 缓存读写 ====================

def _cache_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / CACHE_NAME


def _empty() -> dict:
    return {"v": CACHE_VERSION, "platforms": {}}


def _read() -> dict:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(data, dict) or int(data.get("v") or 0) != CACHE_VERSION:
        return _empty()  # 结构升级（旧版记录的是账号主页探测）：旧结果不可信，重测
    data.setdefault("platforms", {})
    return data


def _write(data: dict) -> None:
    data["v"] = CACHE_VERSION
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception:
        pass  # 缓存写不进去不影响本次检测结果的使用


def records() -> dict:
    """{platform: 记录} 快照（只读）。"""
    return dict(_read().get("platforms") or {})


def forget_platform_login(platform: str) -> None:
    """平台重新登录/清除 Cookie 后：缓存立刻作废，下次检测重问。"""
    with _LOCK:
        data = _read()
        if data["platforms"].pop(str(platform), None) is not None:
            _write(data)


# ==================== 时间与文案 ====================

def human_age(ts, now=None) -> str:
    """时间戳 → 「刚刚 / 12 分钟前 / 3 小时前 / 2 天前」。"""
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    delta = max(0.0, (float(now) if now else time.time()) - ts)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"


def stamp(ts) -> str:
    """时间戳 → 「11-22 14:03」（详情提示用）。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


# ==================== 状态合成 ====================

def _status_of_entry(entry: dict | None, platform: str = "") -> dict:
    """平台缓存记录 → GUI 直接可用的状态字段。"""
    entry = entry or {}
    if not entry:
        return {"state": STATE_UNKNOWN, "text": STATE_TEXT[STATE_UNKNOWN],
                "detail": "还没检测过这个平台的登录态", "checked_at": 0,
                "ok_at": 0, "username": "", "stale": False,
                "platform": platform}
    state = entry.get("login") or STATE_UNKNOWN
    if state not in STATE_TEXT:
        state = STATE_UNKNOWN
    checked_at = float(entry.get("checked_at") or 0)
    return {
        "state": state,
        "text": STATE_TEXT[state],
        "detail": str(entry.get("detail") or "") or "登录态正常",
        "checked_at": checked_at,
        "ok_at": float(entry.get("ok_at") or 0),
        "username": str(entry.get("username") or ""),
        "stale": bool(checked_at) and (time.time() - checked_at) > STALE_SECONDS,
        "platform": platform or str(entry.get("platform") or ""),
    }


def status(account) -> dict:
    """某个账号（或平台 key）所在平台的登录状态。

    account 可以是注册表账号 dict、平台 key，也可以是账号 id（无法定位平台时返回未检测）。
    """
    if isinstance(account, dict):
        platform = str(account.get("platform") or "")
    else:
        platform = str(account or "")
        known = _read().get("platforms") or {}
        if platform not in known:
            platform = ""
    entry = (_read().get("platforms") or {}).get(platform)
    return _status_of_entry(entry, platform)


def states(platforms) -> dict[str, dict]:
    """一次读盘拿到多个平台的状态（卡片/列表批量展示用）。"""
    entries = _read().get("platforms") or {}
    return {str(p): _status_of_entry(entries.get(str(p)), str(p))
            for p in platforms if p}


def needs_check(account) -> bool:
    """平台没有记录 / 结果超过 STALE_SECONDS。仅作过期判定，不驱动自动重测。"""
    platform = ""
    if isinstance(account, dict):
        platform = str(account.get("platform") or "")
    elif isinstance(account, str):
        platform = account
    if not platform:
        return True
    entry = (_read().get("platforms") or {}).get(platform)
    if not entry:
        return True
    try:
        age = time.time() - float(entry.get("checked_at") or 0)
    except (TypeError, ValueError):
        return True
    return age >= STALE_SECONDS


# ==================== 登录态检测 ====================

def _has_stored(platform: str) -> bool:
    try:
        import auth
        if platform == "netease":
            return bool((auth.get_netease_cookie_stored() or "").strip())
        return bool((auth.load_cookies().get(platform) or "").strip())
    except Exception:
        return False


def probe_platform(platform: str) -> tuple[str, str, str]:
    """跑一次平台登录态校验，返回 (state, username, detail)。不写缓存、不碰界面。"""
    try:
        from tracker import whoami
        info = whoami.probe(platform) or {}
    except Exception as exc:  # noqa: BLE001 - 单个平台异常不影响整批
        return STATE_WARN, "", f"检测失败：{exc}"
    ok = info.get("ok")
    username = str(info.get("username") or "")
    if ok is True:
        return STATE_OK, username, ""
    if ok is False:
        detail = ("还没有登录这个平台" if not _has_stored(platform)
                  else "登录态已失效，请重新登录")
        return STATE_BAD, "", detail
    # ok=None：平台没有校验通道 / 接口不可达 / 只有缓存昵称 → 无法确认
    if platform == "xianyu" and info.get("detail"):
        return STATE_WARN, username, str(info["detail"])
    return STATE_WARN, username, "平台没有可用校验接口或接口不可达"


def _store_platform(platform: str, state: str, username: str, detail: str) -> None:
    now = time.time()
    with _LOCK:
        data = _read()
        entry = data["platforms"].get(platform) or {}
        ok_at = float(entry.get("ok_at") or 0)
        if state == STATE_OK:
            ok_at = now
        data["platforms"][platform] = {
            "platform": platform,
            "login": state,
            "username": username,
            "detail": detail,
            "checked_at": now,
            "ok_at": ok_at,
        }
        _write(data)


def _probe_and_store(platform: str) -> None:
    state, username, detail = probe_platform(platform)
    _store_platform(platform, state, username, detail)


def refresh(accounts=None, only_platforms=None, progress=None,
            workers: int = DEFAULT_WORKERS) -> dict:
    """检测平台登录态并落盘。

    accounts: 账号行（只用来在本参数缺省时推断平台；同一平台只检测一次）
    only_platforms: 只检测这些平台（显式给出时以它为准）
    progress(done, total): 每检测完一个平台回调一次（在工作线程里调用）
    """
    if only_platforms is None and accounts is None:
        from toolbox import account_registry
        accounts = account_registry.list_accounts()
    accounts = list(accounts or [])
    if only_platforms is not None:
        platforms = []
        for platform in only_platforms:
            platform = str(platform or "")
            if platform and platform not in platforms:
                platforms.append(platform)
    else:
        platforms = []
        for account in accounts:
            platform = str(account.get("platform") or "")
            if platform and platform not in platforms:
                platforms.append(platform)
    total = len(platforms)
    if not total:
        return

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = [pool.submit(_probe_and_store, platform) for platform in platforms]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
            done += 1
            if progress is not None:
                try:
                    progress(done, total)
                except Exception:
                    pass
