# -*- coding: utf-8 -*-
"""游客优先三级链路验证：
1. bili/api.py 重试链路（mock）：游客失败(风控码) → 接入Cookie；非风控码不重试
2. bili/api.py 真实游客请求（video_view / dynamic_detail）
3. platforms/bilibili.py 游客解析 + 游客用户时间线
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("== 1. bili/api.py 重试链路（mock） ==")
from bili import api as bapi

calls = []


def fake_risk(client, url, params, login_cookie, wbi_sign, use_cache):
    calls.append(login_cookie)
    if not login_cookie:
        raise bapi.BiliApiError("[-352] 风控", code=-352)
    return {"ok": True}


bapi._api_get_once = fake_risk
bapi.get_cookie = lambda p: "SESSDATA=abc"
r = bapi.api_get("https://api.bilibili.com/x/test", {"a": 1})
check("风控码触发Cookie重试", calls == ["", "SESSDATA=abc"] and r == {"ok": True}, str(calls))


calls.clear()


def fake_no_retry(client, url, params, login_cookie, wbi_sign, use_cache):
    calls.append(login_cookie)
    raise bapi.BiliApiError("[-404] 内容不存在", code=-404)


bapi._api_get_once = fake_no_retry
try:
    bapi.api_get("https://api.bilibili.com/x/test2", {"a": 1})
    check("非风控码不重试", False, "未抛异常")
except bapi.BiliApiError:
    check("非风控码不重试", calls == [""], str(calls))

# 无Cookie时风控码直接抛
calls.clear()
bapi.get_cookie = lambda p: ""


def fake_risk2(client, url, params, login_cookie, wbi_sign, use_cache):
    calls.append(login_cookie)
    raise bapi.BiliApiError("[-352] 风控", code=-352)


bapi._api_get_once = fake_risk2
try:
    bapi.api_get("https://api.bilibili.com/x/test3", {"a": 1})
    check("无Cookie风控直接抛", False, "未抛异常")
except bapi.BiliApiError:
    check("无Cookie风控直接抛", calls == [""], str(calls))

# login_required 必须 Cookie
try:
    bapi.api_get("https://api.bilibili.com/x/need_login", {"a": 1}, login_required=True)
    check("login_required无Cookie报错", False, "未抛异常")
except bapi.BiliApiError as e:
    check("login_required无Cookie报错", "需要登录" in str(e), str(e))

print("== 2. bili/api.py 真实游客请求 ==")
# 恢复真实实现与真实 cookie 读取
import importlib
importlib.reload(bapi)
try:
    d = bapi.video_view("BV1GJ411x7h7")
    check("游客 video_view", bool(d.get("title")), str(d.get("code", "")))
except Exception as e:
    check("游客 video_view", False, str(e))

try:
    feed = bapi.space_feed("2")
    items = feed.get("items") or []
    dyn_id = items[0].get("id_str") if items else ""
    d = bapi.dynamic_detail(dyn_id)
    check("游客 dynamic_detail", bool((d.get("item") or {}).get("id_str")), f"dyn={dyn_id}")
except Exception as e:
    check("游客 dynamic_detail", False, str(e))

print("== 3. platforms/bilibili.py 游客解析 ==")
from platforms.bilibili import BilibiliParser

try:
    info = BilibiliParser().parse("https://www.bilibili.com/video/BV1GJ411x7h7")
    check("游客 parse 视频", info is not None and bool(info.title), info.title if info else "None")
except Exception as e:
    check("游客 parse 视频", False, str(e))

try:
    posts = BilibiliParser().fetch_user_posts("https://space.bilibili.com/2", count=5, kind="video")
    check("游客 fetch_user_posts", len(posts) >= 1, f"got {len(posts)}")
except Exception as e:
    check("游客 fetch_user_posts", False, str(e))

print(f"\n结果: {ok} OK, {fail} FAIL")
sys.exit(1 if fail else 0)
