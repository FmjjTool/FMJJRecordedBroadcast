"""冒烟：酷狗 / 5sing 登录态识别（Cookie 管理弹窗 + whoami 验证）。

覆盖：
  1. 酷狗 KuGoo Cookie 解析（昵称 / %uXXXX / 老式 KugooID+t / 游客）
  2. 5sing 站内 checkstatus 接口判定（游客态 → 未登录）
  3. Cookie 管理弹窗的登录特征配置（酷狗认 KuGoo、5sing 走接口）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

FAILS = []


def check(name, cond, extra=""):
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


from tracker import whoami  # noqa: E402

print("[1] 酷狗 KuGoo Cookie 解析")
kg_cookie = ("kg_mid=abc; KuGoo=KugooID=1234567&UserName=%E6%AD%8C%E6%85%95"
             "&NickName=%E6%AD%8C%E6%85%95XINMU&Pic=http%3A%2F%2Fx%2Fy.jpg&t=tok123&a_id=1014")
r = whoami.verify("kugou", kg_cookie)
check("登录态判定为有效", r.get("ok") is True, r)
check("昵称解码正确", r.get("username") == "歌慕XINMU", r.get("username"))

r = whoami.verify("kugou", "kg_mid=abc; KuGoo=KugooID=88&NickName=%u6B46%u6155&t=x")
check("%uXXXX 昵称可解", r.get("username") == "歆慕", r.get("username"))

r = whoami.verify("kugou", "KugooID=99; t=abc")
check("老式 KugooID+t 仍认登录", r.get("ok") is True, r)

r = whoami.verify("kugou", "kg_mid=abc; kg_dfid=def")
check("游客态判为未登录（不是无法验证）",
      r.get("ok") is False and not r.get("username"), r)

print("[2] 5sing 站内接口")
r = whoami.verify("5sing", "")
check("空 Cookie → 未登录", r.get("ok") is False, r)

guest = "sl-session=T2wXWvp922oxPpH4kqqwYQ==; kg_mid=b9c6; kg_dfid=4FL9; cct=665af0a4"
r = whoami.verify("5sing", guest)
check("游客 Cookie → 未登录或接口不可达", r.get("ok") in (False, None), r)
check("游客 Cookie 不误报已登录", r.get("ok") is not True, r)

print("[3] Cookie 管理弹窗配置")
from gui_qt.cookie_manager import (  # noqa: E402
    BROWSER_LOGIN_CONFIGS, REMOTE_VERIFY_PLATFORMS,
)

kg = BROWSER_LOGIN_CONFIGS["kugou"]
check("酷狗登录特征含 KuGoo", "KuGoo" in kg[2], kg[2])
check("酷狗不再只认 KugouID 单 o", "KugouID" not in kg[2], kg[2])
fs = BROWSER_LOGIN_CONFIGS["5sing"]
check("5sing 认 5sing_auth（登录后才种）", "5sing_auth" in fs[2], fs[2])
check("5sing 仍要站内接口复核", "5sing" in REMOTE_VERIFY_PLATFORMS)
check("5sing 走 http 登录页", fs[0].startswith("http://"), fs[0])

print("[4] whoami 注册表")
check("kugou/5sing 都在验证表内",
      "kugou" in whoami._VERIFIERS and "5sing" in whoami._VERIFIERS)

print("[5] 列表状态文案（不能把「无法验证」说成「已登录」）")
from gui_qt.cookie_manager import CookieManagerDialog  # noqa: E402


class _Fake:
    def __init__(self, cache):
        self._whoami_cache = cache

    _short = staticmethod(CookieManagerDialog._short)


cases = [
    ("5sing", {"ok": None, "username": ""}, "🟡 已保存 · 无法验证"),
    ("5sing", {"ok": False, "username": ""}, "🟠 已保存 · 验证失败"),
    ("5sing", {"ok": True, "username": "歌慕XINMU"}, "🟢 已登录 · 歌慕XINMU"),
    ("kugou", {"ok": None, "username": "用户ID 123"}, "🟢 已登录 · 用户ID 123"),
]
for plat, info, want in cases:
    got = CookieManagerDialog._status_text(_Fake({plat: info}), plat, True, "")
    check(f"{plat} {info} → {want}", got == want, got)

if FAILS:
    print(f"\n{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("\n全部通过")
