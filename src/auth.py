"""
认证模块 - Cookie 管理 / 内置浏览器登录
"""

import json
import os
import threading
import base64
import hashlib
from typing import Optional
from datetime import datetime, timedelta

from rich.console import Console

from toolbox.config_manager import get_config_dir
from toolbox.browser import launch as pw_launch

console = Console()

# 统一走 config_manager：exe 旁不可写（如装在 Program Files）时自动落 AppData
COOKIE_FILE = str(get_config_dir() / "cookies.json")
_cookie_lock = threading.Lock()  # 线程安全


# ==================== Cookie 加密（Fernet + 本机标识派生密钥） ====================

def _machine_guid() -> str:
    """获取本机唯一标识（Windows MachineGuid，重装系统前保持不变），用于派生加密密钥。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        v, _ = winreg.QueryValueEx(k, "MachineGuid")
        return str(v)
    except Exception:
        pass
    try:
        import uuid
        node = uuid.getnode()
        if node:
            return f"node-{node}"
    except Exception:
        pass
    return "local-default"


def _fernet_key() -> bytes:
    """从本机标识派生 Fernet 密钥（SHA256 摘要 → urlsafe base64）。"""
    seed = f"fmjj-cookie-v1:{_machine_guid()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _encrypt_cookie(s: str) -> str:
    """加密 cookie 值，返回 "enc:" + base64；加密失败降级返回原文（不丢数据）。"""
    if not s:
        return ""
    try:
        from cryptography.fernet import Fernet
        return "enc:" + Fernet(_fernet_key()).encrypt(s.encode("utf-8")).decode("ascii")
    except Exception:
        return s


def _decrypt_cookie(s: str) -> str:
    """解密 cookie 值；无 "enc:" 前缀（旧明文）原样返回，解密失败（密钥不匹配）返回空。"""
    if not s:
        return ""
    if not s.startswith("enc:"):
        return s  # 旧格式明文，向后兼容
    try:
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).decrypt(s[4:].encode("ascii")).decode("utf-8")
    except Exception:
        return ""  # 密钥不匹配（如重装系统），需重新登录

# 各平台 Cookie 有效期（天），0 表示永不过期
# 微博 SUB 实际有效期约 1 个月，过短会导致用户明明登录了却提示未登录
COOKIE_TTL_DAYS = {
    "weibo": 30,
    "douyin": 0,
    "bilibili": 0,
    "qq": 0,
    "kugou": 0,
    "kuwo": 0,
    "5sing": 0,
    "xiaohongshu": 0,
    "kuaishou": 0,
    "taobao": 0,
}

# 平台 key -> 中文名（用于 UI 展示）
PLATFORM_NAMES = {
    "bilibili": "B站",
    "douyin": "抖音",
    "weibo": "微博",
    "netease": "网易云",
    "qq": "QQ音乐",
    "kugou": "酷狗",
    "kuwo": "酷我",
    "5sing": "5sing",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "taobao": "淘宝",
}


def _load_raw() -> dict:
    """加载原始 JSON 文件"""
    if os.path.exists(COOKIE_FILE):
        try:
            with _cookie_lock:
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    return {}


# 内存缓存：mtime 变化才重读磁盘（避免 get_cookie 高频读文件）
_raw_cache: dict | None = None
_raw_cache_mtime: float | None = None


def _load_raw_cached() -> dict:
    """带 mtime 缓存的 _load_raw（文件未变则复用上次解析结果）。"""
    global _raw_cache, _raw_cache_mtime
    try:
        mtime = os.path.getmtime(COOKIE_FILE)
    except OSError:
        mtime = None
    if _raw_cache is None or _raw_cache_mtime != mtime:
        _raw_cache = _load_raw()
        _raw_cache_mtime = mtime
    return _raw_cache


def _invalidate_cache():
    """写文件后失效缓存，强制下次重读。"""
    global _raw_cache_mtime
    _raw_cache_mtime = None


def load_cookies() -> dict[str, str]:
    """加载所有 Cookie（自动解密 enc: 前缀；不过滤过期）"""
    raw = _load_raw_cached().get("cookies", {})
    return {k: _decrypt_cookie(v) for k, v in raw.items() if isinstance(v, str)}


def _load_cookie_times() -> dict[str, str]:
    """加载各平台 Cookie 的保存时间"""
    raw = _load_raw_cached()
    times = raw.get("cookie_times", {})
    # 兼容旧格式：从全局 updated_at 推断
    if not times and raw.get("updated_at"):
        global_time = raw["updated_at"]
        for p in raw.get("cookies", {}):
            times[p] = global_time
    return times


def save_cookies(cookies: dict[str, str], cookie_times: dict[str, str] | None = None):
    """保存 Cookie 及时间戳（cookie 值加密后落盘）"""
    raw = _load_raw()
    raw["cookies"] = {k: _encrypt_cookie(v) for k, v in cookies.items()}
    raw["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if cookie_times is None:
        cookie_times = raw.get("cookie_times", {})
    raw["cookie_times"] = cookie_times
    with _cookie_lock:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    _invalidate_cache()


def get_cookie(platform: str) -> str:
    """获取 Cookie，如果已过期或总开关关闭则返回空字符串"""
    if not is_cookie_enabled():
        return ""
    cookie_str = load_cookies().get(platform, "")
    if not cookie_str:
        return ""

    ttl = COOKIE_TTL_DAYS.get(platform, 0)
    if ttl <= 0:
        return cookie_str  # 永不过期

    times = _load_cookie_times()
    saved_at = times.get(platform, "")
    if not saved_at:
        return cookie_str  # 没有时间记录，先放行

    try:
        saved_dt = datetime.strptime(saved_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() - saved_dt > timedelta(days=ttl):
            console.print(f"[yellow]! {platform} Cookie 已过期（超过 {ttl} 天），请重新登录[/yellow]")
            return ""
    except ValueError:
        pass

    return cookie_str


def is_cookie_enabled() -> bool:
    """Cookie 总开关状态（默认开启）"""
    return _load_raw_cached().get("cookie_enabled", True)


def set_cookie_enabled(enabled: bool):
    """设置 Cookie 总开关"""
    raw = _load_raw()
    raw["cookie_enabled"] = enabled
    with _cookie_lock:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    _invalidate_cache()
    state = "[green]开启[/green]" if enabled else "[red]关闭[/red]"
    console.print(f"Cookie 总开关已{state}")


def set_cookie(platform: str, cookie_str: str):
    """保存 Cookie 并记录时间戳"""
    all_cookies = load_cookies()
    all_cookies[platform] = cookie_str

    cookie_times = _load_cookie_times()
    cookie_times[platform] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_cookies(all_cookies, cookie_times)


def clear_cookie(platform: str):
    """清除指定平台的 Cookie 及时间记录"""
    all_cookies = load_cookies()
    all_cookies.pop(platform, None)

    cookie_times = _load_cookie_times()
    cookie_times.pop(platform, None)

    save_cookies(all_cookies, cookie_times)
    if platform == "netease":
        # 同时删旧 netease/cookie.txt，否则下次读取会被迁移逻辑"复活"成刚登录
        legacy = _netease_legacy_file()
        try:
            if os.path.exists(legacy):
                os.remove(legacy)
        except OSError:
            pass


def _netease_legacy_file() -> str:
    """网易云旧 cookie 文件路径（netease/cookie.txt）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "netease", "cookie.txt")


def _netease_legacy_migrate() -> str:
    """旧 netease/cookie.txt 一次性迁移（统一存储从未记录过 netease 才迁）。
    TTL 过期、用户清除都不会从旧文件复活。"""
    if "netease" in _load_cookie_times() or "netease" in load_cookies():
        return ""
    legacy = _netease_legacy_file()
    try:
        if os.path.exists(legacy):
            content = open(legacy, encoding="utf-8").read().strip()
            if content:
                set_cookie("netease", content)  # 迁移到统一存储
                os.remove(legacy)               # 迁移是一次性的，删掉旧文件
                return content
    except Exception:
        pass
    return ""


def get_netease_cookie() -> str:
    """获取网易云 cookie（受总开关/TTL 控制，采集使用）。
    仅当统一存储从未记录过 netease 时，做一次性迁移（旧 netease/cookie.txt）。"""
    return get_cookie("netease") or _netease_legacy_migrate()


def get_netease_cookie_stored() -> str:
    """已存储的网易云 Cookie（不看总开关、不校验过期）。
    仅供登录状态展示/用户名探测使用；采集请走 get_netease_cookie()。"""
    return load_cookies().get("netease", "") or _netease_legacy_migrate()


def sync_netease_to_legacy():
    """把统一存储里的网易云 cookie 写回旧 netease/cookie.txt（供 MusicDownloader 等旧读取方使用）。"""
    cookie = get_cookie("netease")
    if not cookie:
        return
    legacy = _netease_legacy_file()
    try:
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        with open(legacy, "w", encoding="utf-8") as f:
            f.write(cookie)
    except Exception:
        pass


def get_cookie_age(platform: str) -> str:
    """返回 Cookie 已保存多久的描述文本，用于 UI 展示"""
    times = _load_cookie_times()
    saved_at = times.get(platform, "")
    if not saved_at:
        return ""
    try:
        saved_dt = datetime.strptime(saved_at, "%Y-%m-%d %H:%M:%S")
        delta = datetime.now() - saved_dt
        if delta.days > 0:
            return f"{delta.days}天前"
        elif delta.seconds >= 3600:
            return f"{delta.seconds // 3600}小时前"
        elif delta.seconds >= 60:
            return f"{delta.seconds // 60}分钟前"
        else:
            return "刚刚"
    except ValueError:
        return ""


# ============================================================
#  内置浏览器登录
# ============================================================

def browser_login(platform: str, blocking: bool = True) -> Optional[str]:
    """启动可见浏览器，用户扫码/登录后自动保存 Cookie。
    blocking=False 时自动轮询 Cookie（适用于 Web UI，无需按 Enter）"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        console.print("[red]需要 Playwright: pip install playwright && playwright install chromium[/red]")
        return None

    configs = {
        "weibo": {"url": "https://m.weibo.cn/", "title": "微博"},
        "douyin": {"url": "https://www.douyin.com/", "title": "抖音"},
        "bilibili": {"url": "https://www.bilibili.com/", "title": "B站"},
    }
    cfg = configs.get(platform)
    if not cfg:
        return None

    console.print(f"\n[bold cyan]🔑 {cfg['title']} 内置浏览器登录[/bold cyan]")
    if blocking:
        console.print("[dim]浏览器窗口将打开 → 请扫码或输入账号登录 → 完成后按 [bold]Enter[/bold] 自动保存 Cookie[/dim]")
    else:
        console.print("[dim]浏览器窗口打开 → 请扫码登录 → 将自动检测并保存 Cookie（最长等待 3 分钟）[/dim]")

    with sync_playwright() as p:
        # 可见窗口登录走统一浏览器层（Edge → Chrome → 默认）
        browser = pw_launch(p, headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        try:
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30000)
            console.print(f"[green]V 浏览器已打开 {cfg['url']}[/green]")

            if blocking:
                input("\n[bold]登录完成后按 Enter 捕获 Cookie...[/bold]")
                cookies = context.cookies()
            else:
                import time as _t
                cookies = []
                # 自动轮询 3 分钟（90 次 × 2 秒），检测到登录就提前保存
                for _ in range(90):
                    _t.sleep(2)
                    cookies = context.cookies()
                    has_auth = any(
                        c["name"] in ("SUB", "SESSDATA", "bili_jct", "sessionid", "passport")
                        or len(c.get("value", "")) > 20
                        for c in cookies
                    )
                    if has_auth and len(cookies) >= 3:
                        console.print("[green]V 检测到登录 Cookie，自动保存...[/green]")
                        break
                else:
                    console.print("[yellow]! 超时未检测到登录，请确认已登录[/yellow]")
            if cookies:
                parts = [f"{c['name']}={c['value']}" for c in cookies]
                cookie_str = "; ".join(parts)
                set_cookie(platform, cookie_str)
                console.print(f"[green]V {cfg['title']} Cookie 已保存 ({len(cookies)} 个)[/green]")
                return cookie_str
            else:
                console.print("[yellow]! 未检测到 Cookie，请确认已登录[/yellow]")
        except Exception as e:
            console.print(f"[red]浏览器登录失败: {e}[/red]")
        finally:
            browser.close()
    return None


def douyin_browser_login() -> Optional[str]:
    return browser_login("douyin")
