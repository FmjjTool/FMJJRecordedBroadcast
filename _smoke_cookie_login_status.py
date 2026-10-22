"""冒烟：Cookie 管理弹窗的登录态识别（whoami 验证 + 状态行文案）。

覆盖：
  1. 酷狗 KuGoo Cookie 解析（昵称 / %uXXXX / 老式 KugooID+t / 游客）
  2. 5sing 站内 checkstatus 接口判定（游客态 → 未登录）
  3. 微博：config.user 恒 null 时改问 users/show 取昵称
  4. 小红书：探索页 SSR state（首页是 JS 壳，旧实现恒「无法验证」）
  5. 酷狗概念版：会话文件 / 同账号酷狗昵称
  6. 弹窗登录特征配置 + 状态行文案
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

print("[3] 微博昵称（config.user 已置空）")
r = whoami.verify("weibo", "SUB=_2A25fake; SUBP=0033WrSXqPxfM725; _T_WM=abc")
check("伪造 Cookie → 未登录或无法验证", r.get("ok") in (False, None), r)
check("伪造 Cookie 不误报已登录", r.get("ok") is not True, r)

print("[4] 小红书（探索页 SSR state）")
r = whoami.verify("xiaohongshu", "a1=fake; web_session=fake")
check("伪造 Cookie → 未登录或无法验证", r.get("ok") in (False, None), r)
check("伪造 Cookie 不误报已登录", r.get("ok") is not True, r)
html = ('<script>window.__INITIAL_STATE__={"user":{"loggedIn":true,"userInfo":'
        '{"guest":false,"nickname":"小红薯ABC"}},"note":{}}</script>')
st = whoami._xhs_state(html)
check("能从 SSR 文本解析 state", st.get("user", {}).get("userInfo", {}).get("nickname") == "小红薯ABC", st)

print("[5] 酷狗概念版昵称")
check("userid 不匹配时不借昵称", whoami.kugou_nick_for(0) == "", whoami.kugou_nick_for(0))

print("[5b] 昵称缓存（QQ音乐/快手 登录时抓的那份）")
import pathlib  # noqa: E402
import tempfile  # noqa: E402

from tracker import nick_cache  # noqa: E402

_tmp = pathlib.Path(tempfile.mkdtemp()) / "nick_cache.json"
nick_cache._file = lambda: _tmp  # 别动用户真实配置
nick_cache.save("qq", "1152921505019484995", "缓存昵称QQ")
nick_cache.save("kuaishou", "1942705834", "缓存昵称KS")
check("qq 缓存生效", whoami.verify("qq", "uin=1152921505019484995; qm_keyst=x")
      .get("username") == "缓存昵称QQ")
check("快手缓存生效", whoami.verify(
    "kuaishou", "kuaishou.server.web_st=1; userId=1942705834").get("username") == "缓存昵称KS")
check("换号后不张冠李戴", whoami.verify("qq", "uin=999; qm_keyst=x")
      .get("username") == "QQ 999")
nick_cache.drop("qq")
check("清除后回到 uin 兜底", whoami.verify("qq", "uin=1152921505019484995; qm_keyst=x")
      .get("username") == "QQ 1152921505019484995")
nick_cache.save("qq", "1152921505019484995", "已注销")
check("占位名「已注销」不入缓存",
      whoami.verify("qq", "uin=1152921505019484995; qm_keyst=x")
      .get("username") == "QQ 1152921505019484995")

print("[6] Cookie 管理弹窗配置")
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

print("[7] whoami 注册表")
for p in ("kugou", "5sing", "weibo", "xiaohongshu"):
    check(f"{p} 在验证表内", p in whoami._VERIFIERS)

print("[8] 列表状态文案")
from gui_qt.cookie_manager import CookieManagerDialog  # noqa: E402


class _Fake:
    def __init__(self, cache):
        self._whoami_cache = cache

    _short = staticmethod(CookieManagerDialog._short)


cases = [
    ("5sing", {"ok": None, "username": ""}, "", "🟡 已保存 · 无法验证"),
    ("5sing", {"ok": False, "username": ""}, "", "🟠 已保存 · 验证失败"),
    ("5sing", {"ok": True, "username": "歌慕XINMU"}, "", "🟢 已登录 · 歌慕XINMU"),
    ("kugou", {"ok": None, "username": "用户ID 123"}, "", "🟢 已登录 · 用户ID 123"),
    # 概念版：没有 whoami 探测，名字由会话文件/酷狗昵称提供
    ("kugou_lite", None, "歆慕", "🟢 已登录 · 歆慕"),
    ("kugou_lite", {"ok": True, "username": ""}, "歆慕", "🟢 已登录 · 歆慕"),
    ("kugou_lite", {"ok": None, "username": ""}, "用户ID 678", "🟢 已登录 · 用户ID 678"),
]
for plat, info, name, want in cases:
    cache = {} if info is None else {plat: info}
    got = CookieManagerDialog._status_text(_Fake(cache), plat, True, "", name)
    check(f"{plat} {info} name={name!r} → {want}", got == want, got)

if FAILS:
    print(f"\n{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("\n全部通过")
