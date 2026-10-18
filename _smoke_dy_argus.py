"""冒烟测试：抖音 aweme/detail 被 Argus 风控（403）后的浏览器兜底链路。

背景（2026-10-18）：抖音把 aweme/detail 对纯 HTTP 客户端全面拦死——
游客、带登录 Cookie、带 a_bogus 签名一律返回
403「Blocked by ArgusSecurityPlugin Uifid Not Found」（Argus 校验浏览器指纹
与 secsdk 票据，httpx 伪造不了）。原来 _parse_douyin 只走 HTTP，拿不到详情
就返回空 dict，于是资产页下载中心只剩「未获取到可用直链」，视频一个都下不了。

覆盖（全部离线，用假 httpx + 假浏览器详情，不联网）：
1. 403 时自动降级到浏览器接口兜底，返回完整直链
2. 同进程内后续条目跳过已确认被拦的 HTTP 请求（_DY_API_BLOCKED 短路）
3. 浏览器兜底也失败时返回空直链（不抛异常，由上层给明确提示）
4. 资产页下载路径含抖音直链兜底校验（源码级断言）
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import multi_downloader as md

fails = []


def check(name, cond, detail=""):
    print(("[OK] " if cond else "[FAIL] ") + name + (f" -> {detail}" if not cond else ""))
    if not cond:
        fails.append(name)


# ---- 假 httpx：所有请求都 403 ----
class _Resp:
    status_code = 403
    text = "Blocked by ArgusSecurityPlugin Uifid Not Found"

    def json(self):
        raise ValueError("not json")


class _Client:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        calls["http"] += 1
        return _Resp()


calls = {"http": 0, "browser": 0}
md.httpx.get = lambda *a, **kw: (calls.__setitem__("http", calls["http"] + 1), _Resp())[1]
md.httpx.Client = _Client

FAKE_DETAIL = {
    "aweme_id": "7697956972028941417",
    "desc": "烟测标题 #neon",
    "aweme_type": 0,
    "author": {"nickname": "烟测作者", "uid": "1"},
    "statistics": {"digg_count": 1, "comment_count": 2, "play_count": 3},
    "create_time": 1792320277,
    "video": {
        "cover": {"url_list": ["https://p3-pc-sign.douyinpic.com/cover.jpeg"]},
        "bit_rate": [
            {"gear_name": "normal_720_0", "bit_rate": 800000,
             "play_addr": {"url_list": ["https://v26-web.douyinvod.com/720.mp4"]}},
            {"gear_name": "normal_1080_0", "bit_rate": 1600000,
             "play_addr": {"url_list": ["https://v26-web.douyinvod.com/1080.mp4"]}},
        ],
    },
}


def fake_browser(aweme_id, timeout_ms=30000):
    calls["browser"] += 1
    return dict(FAKE_DETAIL)


md._douyin_detail_by_browser = fake_browser
md._DY_API_BLOCKED = False

# ---- 1. 403 → 浏览器兜底 ----
d = md.parse_video("https://www.douyin.com/video/7697956972028941417")
check("403 时走浏览器兜底并拿到直链",
      d.get("video_url") == "https://v26-web.douyinvod.com/1080.mp4", str(d.get("video_url")))
check("兜底结果带标题/作者/发布时间",
      d.get("title") == "烟测标题 #neon" and d.get("author") == "烟测作者"
      and bool(d.get("publish_time")), f"{d.get('title')}/{d.get('author')}/{d.get('publish_time')}")
check("浏览器兜底原始数据回传（供 media_downloader 取清晰度）",
      bool(d.get("raw_data")), str(type(d.get("raw_data"))))
check("确认被拦后置位 _DY_API_BLOCKED", md._DY_API_BLOCKED is True)
http_after_first = calls["http"]

# ---- 2. 同进程第二条：不再打 HTTP ----
d2 = md.parse_video("https://www.douyin.com/video/7697956972028941417")
check("第二条跳过 403 请求（HTTP 调用次数不增）",
      calls["http"] == http_after_first, f"{http_after_first} -> {calls['http']}")
check("第二条仍拿到直链", bool(d2.get("video_url")), str(d2.get("video_url")))

# ---- 3. 浏览器也失败 → 空直链不抛异常 ----
md._douyin_detail_by_browser = lambda aweme_id, timeout_ms=30000: {}
d3 = md.parse_video("https://www.douyin.com/video/7697956972028941417")
check("兜底全失败时返回空直链（不抛异常）", d3.get("video_url") == "", str(d3.get("video_url")))
check("兜底全失败时 is_note=False（不会被误判成图文去下图片）",
      d3.get("is_note") is False, str(d3.get("is_note")))

# ---- 4. 资产页下载路径的直链校验（源码级） ----
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "src", "gui_qt", "pages", "asset.py"), encoding="utf-8").read()
check("资产页含抖音直链失败显式报错",
      "抖音直链获取失败（接口风控 + 浏览器兜底均未拿到直链）" in src)

print("\n结果：%s" % ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(0 if not fails else 1)
