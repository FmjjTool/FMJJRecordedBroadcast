"""平台昵称缓存（config/nick_cache.json）。

为什么需要：QQ音乐（微信登录）的 musicu 用户接口恒返回 500003、快手的
graphql/rest 接口被 antispam 拦——登录态能确认，但昵称问不到。
只能在"登录成功那一刻"顺手抓一次存下来（QQ音乐取扫码换票返回的 nick，
快手在登录窗页面上下文里问一次 /rest/wd/user/profile）。

Cookie 管理弹窗展示昵称时，whoami 先问接口，问不到再读这里。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from toolbox.config_manager import get_config_dir

_LOCK = threading.Lock()

# 站点的占位/失效名（QQ音乐对没设昵称的账号返回「已注销」），存了反而误导
_BAD_NAMES = {"已注销", "用户已注销", "注销", "null", "None", "undefined", "-"}


def _file() -> Path:
    return get_config_dir() / "nick_cache.json"


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def save(platform: str, userid, name: str) -> None:
    """记一条昵称（空名/占位名不写，避免把有效昵称覆盖成「已注销」）。"""
    name = (name or "").strip()
    if not platform or not name or name in _BAD_NAMES:
        return
    with _LOCK:
        d = _load()
        d[platform] = {"userid": str(userid or ""), "name": name,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            f = _file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def get(platform: str, userid="") -> str:
    """取缓存昵称；传了 userid 且与缓存账号不一致时返回空（防换号后张冠李戴）。"""
    d = _load().get(platform) or {}
    if not isinstance(d, dict):
        return ""
    name = str(d.get("name") or "").strip()
    if not name:
        return ""
    if userid and d.get("userid") and str(d["userid"]) != str(userid):
        return ""
    return name


def drop(platform: str) -> None:
    """清除某平台缓存（退出登录/清除 Cookie 时调用）。"""
    with _LOCK:
        d = _load()
        if platform in d:
            d.pop(platform, None)
            try:
                _file().write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            except Exception:
                pass
