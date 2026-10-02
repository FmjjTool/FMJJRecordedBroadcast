"""离屏冒烟测试：评论区 B 站动态解析修复（t.bilibili.com / opus 链接）。

背景：评论区页 B 站输入解析（bili_comments.parse_input）只认 BV/av 视频，
动态链接（t.bilibili.com/{id}、bilibili.com/opus/{id}）直接报
「无法识别输入」——用户描述为「链接不可用」。

关键实现细节（实测结论）：
- polymer/web-dynamic/v1/detail 参数名必须是 **id**（dynamic_id 被 Gaia 风控拦 4101139）
- basic.comment_id_str/comment_type 是权威评论坐标：图文/转发动态的评论
  挂相簿（type=1/11）或源动态下，只有纯文字动态才是 type=17 + 动态 ID 本身
- 新版返回 modules 是 dict，旧版是 list，双兼容

覆盖：
1. 模块导入 + MainWindow 真实 show + 切到评论区页
2. parse_input 单测：动态链接三种形式 / 长数字 ID / BV/av 回归
3. fetch_dynamic_info 解析：新版 dict modules / 旧版 list modules / comment_count dict（mock）
4. 真实动态全链路（网络可选）：本机资产 json 挖 dyn_ 动态 ID，
   fetch_dynamic_info 预检 → 用 comment_id/comment_type 抓评论
5. fetch_comments 回归：ctype=1 走 view+reply，ctype=17 直连 reply（mock）
"""
import importlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])

mods = ["toolbox.bili_comments", "gui_qt.main_window", "gui_qt.pages.video_comments"]
for m in mods:
    importlib.import_module(m)
print("IMPORTS OK (%d modules)" % len(mods))

from gui_qt.main_window import MainWindow
from toolbox import bili_comments as bc

win = MainWindow()
win.show()
win.show_page("video_comments")
for _ in range(50):
    app.processEvents()
    time.sleep(0.01)
print("MAINWINDOW SHOW OK, page:", type(win.pages["video_comments"]).__name__)


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# ---- 2. parse_input 单测 ----
DYN_ID = "1234567890123456789"
assert bc.parse_input(f"https://t.bilibili.com/{DYN_ID}") == ("dynamic", DYN_ID)
assert bc.parse_input(f"https://www.bilibili.com/opus/{DYN_ID}") == ("dynamic", DYN_ID)
assert bc.parse_input(f"https://www.bilibili.com/dynamic/{DYN_ID}?spm=x") == ("dynamic", DYN_ID)
assert bc.parse_input(DYN_ID) == ("dynamic", DYN_ID)          # 纯长数字动态 ID
# 回归：视频输入不受影响
assert bc.parse_input("https://www.bilibili.com/video/BV1xx411c7mD") == ("bvid", "BV1xx411c7mD")
assert bc.parse_input("BV1xx411c7mD") == ("bvid", "BV1xx411c7mD")
assert bc.parse_input("https://www.bilibili.com/video/av12345678") == ("aid", "12345678")
assert bc.parse_input("12345678") == ("aid", "12345678")      # 5-12 位仍按 av 解析
assert bc.parse_input("BV1smoke") == ("", "")                 # 残缺 BV 不误判
print("PARSE OK: 动态三种形式 + 长数字 ID + 视频/av 回归全通过")


# ---- 3. fetch_dynamic_info 解析（mock：新版 dict / 旧版 list / count dict）----
def _dyn_payload(modules, comment_count):
    return {"code": 0, "data": {"item": {
        "basic": {"comment_id_str": "338377435", "comment_type": 11},
        "modules": modules}}}


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


import httpx

_payload = {}


class _FakeCtx:
    """按模块级 _payload 返回响应的假 httpx.Client（动态详情 mock 用）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, **kw):
        return _FakeResp(_payload)


orig_client = httpx.Client
orig_sign_wbi = bc.sign_wbi
orig_bch = bc.build_cookie_header
try:
    # mock 指纹/WBI 层（FakeClient 无法过 buvid 指纹）
    bc.build_cookie_header = lambda c, ck: ck or "buvid3=smoke"
    bc.sign_wbi = lambda url, params, c: f"{url}?{urlencode(params)}"
    httpx.Client = lambda *a, **kw: _FakeCtx()

    # 新版：modules 是 dict，module_stat.comment 是 dict {'count': N}
    _payload.clear()
    _payload.update(_dyn_payload({
        "module_author": {"name": "宫姒"},
        "module_dynamic": {"type": "DYNAMIC_TYPE_DRAW",
                           "desc": {"text": ""},
                           "major": {"opus": {"title": "图集标题"}}},
        "module_stat": {"comment": {"count": 25, "forbidden": False}},
    }, 25))
    info = bc.fetch_dynamic_info(DYN_ID)
    assert info["exists"] and info["author"] == "宫姒", info
    assert info["desc"] == "图集标题"
    assert info["comment_count"] == 25
    assert info["comment_id"] == "338377435" and info["comment_type"] == 11
    print("DYN INFO (dict modules) OK:", info["author"], info["comment_id"], info["comment_type"])

    # 旧版：modules 是 list，comment 是 int
    _payload = {"code": 0, "data": {"item": {
        "basic": {"comment_id_str": DYN_ID, "comment_type": 17},
        "modules": [
            {"module_author": {"name": "UP主"}},
            {"module_dynamic": {"type": "DYNAMIC_TYPE_WORD", "desc": {"text": "纯文字动态"}}},
            {"module_stat": {"comment": 3}},
        ]}}}
    info = bc.fetch_dynamic_info(DYN_ID)
    assert info["exists"] and info["desc"] == "纯文字动态"
    assert info["comment_count"] == 3
    assert info["comment_id"] == DYN_ID and info["comment_type"] == 17
    print("DYN INFO (list modules) OK")

    # 风控码 / 4101139 → unknown=True（不阻断）
    _payload.clear()
    _payload.update({"code": 4101139, "message": "请求数据发生错误"})
    info = bc.fetch_dynamic_info(DYN_ID)
    assert info["unknown"] and not info["exists"]
    print("DYN INFO (risk 4101139 → unknown) OK")
finally:
    httpx.Client = orig_client
    bc.sign_wbi = orig_sign_wbi
    bc.build_cookie_header = orig_bch


# ---- 4. 真实动态全链路（网络可选）：本机资产 json 挖 dyn_ 动态 ID ----
from toolbox.config_manager import get_export_subdir

dyn_ids = []
video_dir = get_export_subdir("video_data")
for p in video_dir.glob("**/*.json"):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            data = data[0] if data else {}
        vid = str(data.get("视频ID") or "")
        if vid.startswith("dyn_") and vid[4:].isdigit():
            dyn_ids.append(vid[4:])
    except Exception:
        continue

if dyn_ids:
    print(f"FOUND {len(dyn_ids)} real dyn ids, testing first 3...")
    for did in dyn_ids[:3]:
        info = bc.fetch_dynamic_info(did)
        if not (info["exists"] or info["unknown"]):
            print(f"  {did[-6:]}: 详情不可得（已删除？）")
            continue
        if info["unknown"]:
            print(f"  {did[-6:]}: 预检被风控（unknown），GUI 会直接尝试抓评论")
            continue
        oid = info["comment_id"] or did
        ctype = info["comment_type"] or 17
        comments = bc.fetch_comments(oid, ctype=ctype, pages=1)
        assert all(bc.comment_text(c) is not None for c in comments)
        print(f"  {did[-6:]}: [{info['author']}] {info['desc'][:20]!r} "
              f"评论坐标 type={ctype} oid={oid} 详情计数={info['comment_count']} "
              f"实抓={len(comments)} 条")
        if comments:
            print("    首条:", bc.comment_user(comments[0]), ":",
                  bc.comment_text(comments[0])[:36])
else:
    print("DYN SKIP: 本机资产无 B 站动态数据（不阻断）")

# ---- 5. fetch_comments 回归（mock 网络层） ----
calls = {"n": 0}
bc.build_cookie_header = lambda c, ck: ck or "buvid3=smoke"
bc.sign_wbi = lambda url, params, c: f"{url}?{urlencode(params)}"


class _FakeReplyCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, **kw):
        calls["n"] += 1
        if "web-interface/view" in url:
            return _FakeResp({"code": 0, "data": {
                "aid": 12345678, "title": "烟测视频",
                "owner": {"name": "烟测作者"}, "stat": {"reply": 2}}})
        replies = [{"content": {"message": f"评论{calls['n']}"},
                    "member": {"uname": "用户"}, "like": 1,
                    "ctime": 1700000000}] * 2
        return _FakeResp({"code": 0, "data": {"replies": replies}})


try:
    httpx.Client = lambda *a, **kw: _FakeReplyCtx()
    # ctype=1 + BV：应先转 aid 再翻页
    cs = bc.fetch_comments("BV1xx411c7mD", pages=1)
    assert len(cs) == 2, len(cs)
    assert calls["n"] == 2, f"应先 view 预检再 reply: {calls['n']}"
    # ctype=17 + 动态 ID：纯数字直接翻页，不走 view
    calls["n"] = 0
    cs = bc.fetch_comments(DYN_ID, ctype=17, pages=1)
    assert len(cs) == 2
    assert calls["n"] == 1, f"动态评论不应触发 view 预检: {calls['n']}"
    # ctype=11 + 相簿 oid：同样直连 reply
    calls["n"] = 0
    cs = bc.fetch_comments("338377435", ctype=11, pages=1)
    assert len(cs) == 2 and calls["n"] == 1
finally:
    httpx.Client = orig_client
    bc.sign_wbi = orig_sign_wbi
    bc.build_cookie_header = orig_bch
print("REGRESSION OK: ctype=1 走 view+reply；ctype=17/11 直连 reply，互不干扰")

win.close()
print("BILI DYNAMIC COMMENTS SMOKE OK")
