"""冒烟测试：抖音直播弹幕连接参数获取（截页面自身 im/fetch 响应）。

背景（2026-10-18）：直播页报「连接参数不完整（可能被抖音风控拦截）」。
根因是旧实现自己拼参数发 /webcast/im/fetch/（a_bogus 占位 '00000000'、
user_unique_id 用固定值），抖音现在返回 200 + application/json + **空体**，
cursor/internalExt/pushServer 全缺 → 直接判 missing。页面自己那条请求签名与
did 都是真的，改成截它的响应体解析即可（离线全绿 + 实测 34 条弹幕）。

覆盖（离线，用合成 protobuf + 假 response，不联网）：
1. _parse_im_body：从 Response protobuf 正确取出 cursor/internalExt/pushServer
2. _prep_from_capture：按 room_id 过滤响应、解析 did（wss_push_did 优先）
3. did 缺失时回退取请求 URL 的 user_unique_id
4. 没有捕获到响应 / body 太短 → 返回 {}（上层走旧兜底）
5. _prep_ws_params：捕获成功时用同一 did 重算 stub md5 生成签名；
   捕获失败时回退 _PREP_JS（返回 missing 不崩）
6. 源码级：_run 在 goto 之前布好捕获（否则页面先发请求会漏）
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import toolbox.douyin_live as dl

fails = []


def check(name, cond, detail=""):
    print(("[OK] " if cond else "[FAIL] ") + name + (f" -> {detail}" if not cond else ""))
    if not cond:
        fails.append(name)


def _len_field(num, s):
    b = s.encode()
    return bytes([(num << 3) | 2]) + bytes([len(b)]) + b


def make_body(cursor="t-1_r-2_d-3_u-1",
              ext="internal_src:pushserver|wss_push_did:7692048984651499017|seq:1",
              push="wss://webcast100-ws-web-lf.douyin.com/webcast/im/push/v2/"):
    return _len_field(1, "x") + _len_field(2, cursor) + _len_field(5, ext) + _len_field(10, push)


ROOM_ID = "7697962016266373939"
INFO = {"roomId": ROOM_ID, "uniqueId": "7673531686768002601"}


class FakeResp:
    def __init__(self, url, body):
        self.url = url
        self._body = body
        self.status = 200
        self.headers = {"content-type": "application/protobuffer"}

    def body(self):
        return self._body


class FakePage:
    """只实现 wait_for_timeout / evaluate。"""

    def __init__(self, evaluate_ret=None, evaluate_raise=False):
        self._ret = evaluate_ret
        self._raise = evaluate_raise
        self.evaluated = []

    def wait_for_timeout(self, ms):
        time.sleep(0.001)

    def evaluate(self, js, arg=None):
        self.evaluated.append(arg)
        if self._raise:
            raise RuntimeError("boom")
        return self._ret


# ---- 1. _parse_im_body ----
p = dl.DouyinLiveFetcher._parse_im_body(make_body())
check("解析 cursor", p.get("cursor") == "t-1_r-2_d-3_u-1", str(p))
check("解析 internalExt", (p.get("internalExt") or "").startswith("internal_src:pushserver"), str(p))
check("解析 pushServer", (p.get("pushServer") or "").startswith("wss://"), str(p))
check("空体不崩", dl.DouyinLiveFetcher._parse_im_body(b"") == {})

# ---- 2. _prep_from_capture：按 room_id 过滤 + did ----
good = FakeResp(f"https://live.douyin.com/webcast/im/fetch/?room_id={ROOM_ID}&x=1", make_body())
other = FakeResp("https://live.douyin.com/webcast/im/fetch/?room_id=999&x=1", make_body())
cap = dl.DouyinLiveFetcher._prep_from_capture(FakePage(), [other, good], INFO, wait=0.5)
check("跳过别的房间的响应，取本房间那条", cap.get("cursor") == "t-1_r-2_d-3_u-1", str(cap)[:80])
check("did 取自 wss_push_did", cap.get("did") == "7692048984651499017", str(cap.get("did")))

# ---- 3. did 缺失 → 回退请求 URL 的 user_unique_id ----
body_no_did = make_body(ext="internal_src:pushserver|seq:1")
r3 = FakeResp(f"https://live.douyin.com/webcast/im/fetch/?room_id={ROOM_ID}"
              f"&user_unique_id=1234567890", body_no_did)
cap3 = dl.DouyinLiveFetcher._prep_from_capture(FakePage(), [r3], INFO, wait=0.5)
check("did 回退取 user_unique_id", cap3.get("did") == "1234567890", str(cap3.get("did")))

# ---- 4. 没捕获 / 空体 → {} ----
check("无捕获返回空 dict",
      dl.DouyinLiveFetcher._prep_from_capture(FakePage(), [], INFO, wait=0.2) == {})
empty = FakeResp(f"https://live.douyin.com/webcast/im/fetch/?room_id={ROOM_ID}", b"")
check("空体返回空 dict",
      dl.DouyinLiveFetcher._prep_from_capture(FakePage(), [empty], INFO, wait=0.2) == {})

# ---- 5. _prep_ws_params：捕获成功 → 用 did 重算 stub md5 签名 ----
page = FakePage(evaluate_ret=["SIG-FROM-ACRAWLER", 1920, 1080])
res = dl.DouyinLiveFetcher._prep_ws_params(page, [good], INFO, "deadbeef")
check("拿到签名与连接参数",
      res.get("signature") == "SIG-FROM-ACRAWLER" and res.get("cursor"), str(res)[:100])
check("WS user_unique_id 用页面 did（与 fetch 同源）",
      res.get("uniqueId") == "7692048984651499017", str(res.get("uniqueId")))
check("握手签名用同一 did 重算 stub md5（不是房间 uniqueId）",
      page.evaluated and page.evaluated[0] == dl.stub_md5(ROOM_ID, "7692048984651499017"),
      str(page.evaluated))

# 捕获失败 → 回退旧 _PREP_JS（这里让 evaluate 返回 missing 模拟被风控）
page2 = FakePage(evaluate_ret={"result": "missing"})
res2 = dl.DouyinLiveFetcher._prep_ws_params(page2, [], INFO, "deadbeef")
check("捕获失败回退旧路径（missing 不崩）", res2.get("result") == "missing", str(res2))

# evaluate 抛异常 → 返回 error（不抛给上层）
page3 = FakePage(evaluate_raise=True)
res3 = dl.DouyinLiveFetcher._prep_ws_params(page3, [], INFO, "deadbeef")
check("evaluate 异常转成 error 字段", "error" in res3, str(res3))

# ---- 6. 源码级：捕获必须在 goto 之前布好 ----
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "src", "toolbox", "douyin_live.py"), encoding="utf-8").read()
i_arm = src.find("cap = self._arm_im_capture(page)")
i_goto = src.find('page.goto(f"https://live.douyin.com/{self.room_num}",\n                              wait_until="commit"')
check("_run 先布捕获再 goto", 0 < i_arm < i_goto, f"arm@{i_arm} goto@{i_goto}")

print("\n结果：%s" % ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(0 if not fails else 1)
