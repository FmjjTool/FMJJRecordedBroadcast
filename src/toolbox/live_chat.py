# -*- coding: utf-8 -*-
"""录制时的多平台弹幕采集（快手 / 微博 / 小红书 / 网易云）。

与 bili_live.BiliLiveFetcher、douyin_live.DouyinLiveFetcher 保持同一接口：
  start() / stop() / on_message(name, text, m) / on_status(status, msg)
分类与 JSONL 字段对齐现有两平台：弹幕、礼物、进场、点赞、关注；
m 里统一提供 type / ts / nickname / content，礼物再带 giftName、
giftCount、giftPrice、giftIcon。

实现方式：无头浏览器打开直播间，监听页面自身 WebSocket 的 framereceived。
- 文本帧优先按 JSON 结构化解析；
- 二进制帧按 Protobuf 长度字段扫描（快手 payloadType=310 为聊天消息），
  其余帧只在出现明确互动文本时归类，避免把统计帧写成弹幕。
只捕获页面已经建立的消息通道，不伪造登录态；登录态来自统一管理接口。
连接异常最多重连 3 次，弹幕失败绝不影响视频录制。
"""

from __future__ import annotations

import gzip
import json
import queue
import re
import threading
import time
import zlib

PLATFORMS = ("kuaishou", "weibo", "xiaohongshu", "netease")

MAX_QUEUE = 3000
MAX_DEPTH = 8
MAX_JSON_BYTES = 512 * 1024
RECONNECT_MAX = 3
DEDUPE_SECONDS = 3

_CATEGORIES = ("弹幕", "礼物", "进场", "点赞", "关注")

_TYPE_KEYS = ("msgType", "msg_type", "messageType", "message_type", "type",
              "method", "cmd", "event", "action", "bizType", "biz_type")
_USER_OBJECT_KEYS = ("user", "sender", "from", "author", "member", "owner",
                     "userInfo", "user_info")
_USER_NAME_KEYS = ("nickname", "nickName", "nick_name", "userName", "user_name",
                   "uname", "nick", "displayName", "display_name",
                   "senderName", "sender_name", "fromNick", "from_nick")
_CONTENT_KEYS = ("content", "text", "message", "msg", "comment", "danmaku",
                 "word", "body", "chatContent", "chat_content",
                 "messageText", "message_text")
_GIFT_OBJECT_KEYS = ("gift", "giftInfo", "gift_info", "present")
_GIFT_NAME_KEYS = ("giftName", "gift_name", "name", "title", "giftTitle")
_GIFT_COUNT_KEYS = ("giftCount", "gift_count", "count", "num", "quantity",
                    "giftNum", "gift_num")
_GIFT_PRICE_KEYS = ("giftPrice", "gift_price", "price", "value", "coin")
_GIFT_ICON_KEYS = ("giftIcon", "gift_icon", "icon", "pic", "image")

_GIFT_CONTENT_RE = re.compile(r"(?:送出|赠送|投喂|打赏)[了]?\s*(.{1,24}?)(?:\s*[x×*]\s*(\d+))?$")
_ENTER_CONTENT_RE = re.compile(r"^(.*?)(?:来了|进入直播间|加入了?直播间)$")
_CHAT_LINE_RE = re.compile(r"^(.{1,32}?)\s*[:：]\s*(.{1,500})$")
_GIFT_LINE_RE = re.compile(
    r"^(.{1,32}?)\s*(?:送出了?|赠送了?|投喂了?|打赏了?)\s*(.{1,24}?)(?:\s*[x×*]\s*(\d+))?\s*$")
_ENTER_LINE_RE = re.compile(r"^(.{1,32}?)\s*(?:来了|进入直播间|加入了?直播间)\s*$")
_FOLLOW_LINE_RE = re.compile(r"^(.{1,32}?)\s*关注了?主播\s*$")
_LIKE_LINE_RE = re.compile(r"^(.{1,32}?)\s*(?:为主播)?点赞了?(?:\s*[x×*]\s*(\d+))?\s*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_URL_RE = re.compile(r"^https?://", re.I)


# ==================== 通用工具 ====================

def _brief(value, limit=160) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _as_int(value, default=0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _json_or_none(text):
    raw = str(text or "").strip()
    if not raw or raw[:1] not in "{[":
        return None
    if len(raw) > MAX_JSON_BYTES:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _text_of(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "content", "message", "msg", "name"):
            if isinstance(value.get(key), (str, int, float)):
                return str(value[key]).strip()
    if isinstance(value, (list, tuple)):
        parts = [_text_of(item) for item in value[:20]]
        return " ".join(part for part in parts if part).strip()
    return ""


def _first_text(node, keys) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        text = _text_of(node.get(key))
        if text:
            return text
    return ""


def format_line(name, nickname, content) -> str:
    """与 douyin_live._format_line 同款界面文本，保证弹幕浮层观感一致。"""
    nickname = (nickname or "").strip()
    content = (content or "").strip()
    if name == "进场":
        return f"[{nickname}]：{content or '进入直播间'}" if nickname else (content or "进入直播间")
    if name == "关注":
        return f"[{nickname}]：关注了主播" if nickname else "关注了主播"
    if name == "点赞":
        return f"[{nickname}]：{content or '为主播点赞了'}" if nickname else (content or "为主播点赞了")
    if nickname and content:
        return f"[{nickname}]：{content}"
    if nickname:
        return f"[{nickname}]："
    return content


def _item(name, m) -> dict:
    content = m.get("content") or ""
    return {"name": name, "text": format_line(name, m.get("nickname"), content), "m": m}


# ==================== JSON 归一化 ====================

def normalize_json(platform, payload) -> list[dict]:
    """任意嵌套 JSON → 与现有两平台同构的消息列表。"""
    items, seen = [], set()

    def visit(node, depth):
        if depth > MAX_DEPTH:
            return
        if isinstance(node, dict):
            item = item_from_dict(platform, node)
            if item:
                key = (item["name"], item["m"].get("nickname"),
                       item["m"].get("content"), item["m"].get("giftName"),
                       item["m"].get("giftCount"))
                if key not in seen:
                    seen.add(key)
                    items.append(item)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value, depth + 1)
        elif isinstance(node, list):
            for value in node[:200]:
                visit(value, depth + 1)

    visit(payload, 0)
    return items


def item_from_dict(platform, node) -> dict | None:
    if not isinstance(node, dict):
        return None
    type_text = " ".join(
        text for text in (_text_of(node.get(key)) for key in _TYPE_KEYS) if text)
    nickname = _user_of(node)
    content = _content_of(node)
    gift = _gift_of(node)
    name = _category(type_text, content, gift)
    if not name:
        return None
    if name == "礼物":
        gift_name = gift.get("name") or _gift_name_from_content(content) or "礼物"
        count = gift.get("count") or _gift_count_from_content(content) or 1
        content = f"送出 {gift_name} x{count}"
        gift = {"name": gift_name, "count": count,
                "price": gift.get("price") or 0, "icon": gift.get("icon") or ""}
    elif not content:
        content = {"进场": "进入直播间", "关注": "关注了主播",
                   "点赞": "为主播点赞了"}.get(name, "")
    m = {"type": type_text or "JSON_MESSAGE", "ts": int(time.time() * 1000),
         "nickname": nickname, "content": content}
    if gift:
        m.update({"giftName": gift.get("name") or "",
                  "giftCount": gift.get("count") or 1,
                  "giftPrice": gift.get("price") or 0,
                  "giftIcon": gift.get("icon") or ""})
    return _item(name, m)


def _user_of(node) -> str:
    for key in _USER_OBJECT_KEYS:
        value = node.get(key)
        if isinstance(value, dict):
            name = _first_text(value, _USER_NAME_KEYS + ("name",))
            if name:
                return name
        elif isinstance(value, str) and value.strip() and not _URL_RE.match(value):
            return value.strip()
    return _first_text(node, _USER_NAME_KEYS)


def _content_of(node) -> str:
    for key in _CONTENT_KEYS:
        if key in node:
            text = _text_of(node.get(key))
            if text and not _URL_RE.match(text):
                return text
    return ""


def _gift_of(node) -> dict:
    gift = {}
    for key in _GIFT_OBJECT_KEYS:
        if isinstance(node.get(key), dict):
            gift = node[key]
            break
    name = _first_text(gift, _GIFT_NAME_KEYS) or _first_text(node, ("giftName", "gift_name"))
    count = _as_int(_first_text(gift, _GIFT_COUNT_KEYS)
                    or _first_text(node, ("giftCount", "gift_count")))
    price = _as_int(_first_text(gift, _GIFT_PRICE_KEYS)
                    or _first_text(node, ("giftPrice", "gift_price")))
    icon = _first_text(gift, _GIFT_ICON_KEYS) or _first_text(node, ("giftIcon", "gift_icon"))
    if not name and not count and not price:
        return {}
    return {"name": name, "count": count, "price": price, "icon": icon}


def _category(type_text, content, gift) -> str | None:
    low = str(type_text or "").lower()
    if gift.get("name") or any(key in low for key in ("gift", "present", "reward", "礼物")):
        return "礼物"
    if any(key in low for key in ("social", "follow", "关注")):
        return "关注"
    if any(key in low for key in ("member", "enter", "join", "进场", "进入")):
        return "进场"
    if any(key in low for key in ("like", "zan", "点赞")):
        return "点赞"
    if any(key in low for key in ("chat", "comment", "danmaku", "弹幕")):
        return "弹幕" if content else None
    if not content:
        return None
    if _GIFT_CONTENT_RE.search(content) or "礼物" in content[:20]:
        return "礼物"
    if content in ("来了", "进入直播间") or "进入直播间" in content:
        return "进场"
    if "关注了主播" in content or "关注主播" in content:
        return "关注"
    if "为主播点赞" in content or "点赞了" in content[:20]:
        return "点赞"
    return "弹幕"


def _gift_name_from_content(content) -> str:
    match = _GIFT_CONTENT_RE.search(str(content or ""))
    return match.group(1).strip() if match else ""


def _gift_count_from_content(content) -> int:
    match = _GIFT_CONTENT_RE.search(str(content or ""))
    return _as_int(match.group(2), 0) if match else 0


# ==================== 文本帧 ====================

def normalize_text(platform, text) -> list[dict]:
    payload = str(text or "").strip()
    if not payload:
        return []
    obj = _json_or_none(payload)
    if obj is not None:
        return normalize_json(platform, obj)
    one_line = re.sub(r"\s+", " ", payload).strip()
    if len(one_line) > 500:
        return []
    match = _GIFT_LINE_RE.match(one_line)
    if match:
        m = {"type": "TEXT_GIFT", "ts": int(time.time() * 1000),
             "nickname": match.group(1), "content": f"送出 {match.group(2)} x{match.group(3) or 1}",
             "giftName": match.group(2), "giftCount": _as_int(match.group(3), 1),
             "giftPrice": 0, "giftIcon": ""}
        return [_item("礼物", m)]
    match = _FOLLOW_LINE_RE.match(one_line)
    if match:
        return [_item("关注", {"type": "TEXT_FOLLOW", "ts": int(time.time() * 1000),
                               "nickname": match.group(1), "content": "关注了主播"})]
    match = _ENTER_LINE_RE.match(one_line)
    if match:
        return [_item("进场", {"type": "TEXT_ENTER", "ts": int(time.time() * 1000),
                               "nickname": match.group(1), "content": "进入直播间"})]
    match = _LIKE_LINE_RE.match(one_line)
    if match:
        return [_item("点赞", {"type": "TEXT_LIKE", "ts": int(time.time() * 1000),
                               "nickname": match.group(1), "content": "为主播点赞了"})]
    match = _CHAT_LINE_RE.match(one_line)
    if match and (_CJK_RE.search(match.group(1)) or _CJK_RE.search(match.group(2))):
        return [_item("弹幕", {"type": "TEXT_CHAT", "ts": int(time.time() * 1000),
                               "nickname": match.group(1), "content": match.group(2)})]
    return []


# ==================== 二进制帧（Protobuf） ====================

def _pb_varint(data, pos):
    value = shift = 0
    while pos < len(data) and shift < 70:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    return None, pos


def _pb_fields(data):
    pos = 0
    while pos < len(data):
        key, pos = _pb_varint(data, pos)
        if key is None:
            return
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _pb_varint(data, pos)
            if value is None:
                return
            yield field, wire, value
        elif wire == 2:
            length, pos = _pb_varint(data, pos)
            if length is None or length < 0 or pos + length > len(data):
                return
            yield field, wire, data[pos:pos + length]
            pos += length
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            return


def _decode_text(chunk) -> str:
    if not chunk or len(chunk) > 300:
        return ""
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if not text or _URL_RE.match(text.strip()):
        return ""
    printable = sum(1 for ch in text if ch.isprintable()) / max(1, len(text))
    if printable < 0.85:
        return ""
    if re.fullmatch(r"[\d\W_]+", text):
        return ""
    return text.strip()


def _pb_strings(data, depth=0, out=None) -> list[str]:
    out = out if out is not None else []
    if depth > 6 or not data:
        return out
    for _field, wire, value in _pb_fields(data):
        if wire == 0:
            continue
        if wire != 2:
            continue
        text = _decode_text(value)
        if text:
            out.append(text)
        else:
            _pb_strings(value, depth + 1, out)
    return out


def _is_textish(text: str) -> bool:
    text = str(text or "").strip()
    if not text or len(text) > 120 or _URL_RE.match(text):
        return False
    if re.fullmatch(r"[\d\W_]+", text):
        return False
    return bool(_CJK_RE.search(text) or re.search(r"[A-Za-z]{2,}", text))


def _decompress(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    if data[:1] == b"\x78":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return data
    return data


def parse_binary(platform, data) -> list[dict]:
    """二进制帧 → 消息；快手按 payloadType 区分聊天与互动。"""
    data = _decompress(bytes(data or b""))
    payload_type = 0
    compression = 0
    payload = b""
    for field, wire, value in _pb_fields(data):
        if wire == 0 and field == 1:
            payload_type = int(value)
        elif wire == 0 and field == 2:
            compression = int(value)
        elif wire == 2 and field == 3:
            payload = value
    if not payload:
        payload = data
    if compression == 1:
        payload = _decompress(payload)
    strings = [text for text in _pb_strings(payload) if _is_textish(text)]
    if not strings:
        return []
    if payload_type == 310:
        return _binary_chat(strings)
    return _binary_interaction(strings)


def _binary_chat(strings: list[str]) -> list[dict]:
    content = strings[-1][:300]
    nickname = strings[-2] if len(strings) > 1 else ""
    if nickname == content:
        nickname = ""
    return [_item("弹幕", {"type": "KS_CHAT_MESSAGE", "ts": int(time.time() * 1000),
                           "nickname": nickname, "content": content})]


def _binary_interaction(strings: list[str]) -> list[dict]:
    joined = " ".join(strings)
    nickname = strings[0][:40] if strings else ""
    now = int(time.time() * 1000)
    if any(key in joined for key in ("送出", "赠送", "礼物", "打赏")):
        match = re.search(r"(?:送出了?|赠送了?|打赏了?)\s*([^\s，,。]{1,24})", joined)
        gift_name = match.group(1) if match else "礼物"
        count = _as_int((re.search(r"[x×*]\s*(\d+)", joined) or [0, 0])[1], 1)
        m = {"type": "KS_GIFT_MESSAGE", "ts": now, "nickname": nickname,
             "content": f"送出 {gift_name} x{count}", "giftName": gift_name,
             "giftCount": count, "giftPrice": 0, "giftIcon": ""}
        return [_item("礼物", m)]
    if "关注" in joined:
        return [_item("关注", {"type": "KS_FOLLOW_MESSAGE", "ts": now,
                               "nickname": nickname, "content": "关注了主播"})]
    if "点赞" in joined:
        return [_item("点赞", {"type": "KS_LIKE_MESSAGE", "ts": now,
                               "nickname": nickname, "content": "为主播点赞了"})]
    if "进入" in joined or "来了" in joined:
        return [_item("进场", {"type": "KS_ENTER_MESSAGE", "ts": now,
                               "nickname": nickname, "content": "进入直播间"})]
    return []


# ==================== 统一入口 ====================

def normalize_payload(platform, payload, is_binary=False) -> list[dict]:
    if isinstance(payload, (dict, list)):
        return normalize_json(platform, payload)
    if isinstance(payload, bytes) or is_binary:
        data = payload if isinstance(payload, bytes) else str(payload or "").encode(
            "utf-8", "ignore")
        data = _decompress(data)
        text = _decode_text(data)
        if text:
            obj = _json_or_none(text)
            if obj is not None:
                return normalize_json(platform, obj)
        return parse_binary(platform, data)
    return normalize_text(platform, payload)


# ==================== 浏览器采集器 ====================

class BrowserChatFetcher:
    """一个直播间的浏览器弹幕采集任务（接口同 Bili/Douyin LiveFetcher）。"""

    def __init__(self, platform, room, on_message=None, on_room_info=None,
                 on_status=None):
        self.platform = platform
        self.room = str(room)
        self.on_message = on_message
        self.on_room_info = on_room_info
        self.on_status = on_status
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._thread = None
        self._seen: dict[tuple, float] = {}
        self._dropped = 0

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name="live-chat-%s-%s" % (self.platform, self.room))
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=12)

    def _emit(self, callback, *args):
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            pass

    def _status(self, state, text):
        self._emit(self.on_status, state, text)

    # ---------- 主循环 ----------
    def _run(self):
        attempts = 0
        while not self._stop.is_set() and attempts <= RECONNECT_MAX:
            attempts += 1
            try:
                self._run_session()
            except Exception as exc:  # noqa: BLE001 - 弹幕失败不影响录制
                if not self._stop.is_set():
                    self._status("error", "弹幕连接异常：%s" % _brief(exc))
            if self._stop.is_set():
                break
            if attempts > RECONNECT_MAX:
                break
            self._status("connecting", "弹幕将在 5 秒后重连（第 %d 次）" % attempts)
            self._stop.wait(5)
        if not self._stop.is_set() and attempts > RECONNECT_MAX:
            self._status("error", "弹幕多次重连失败，已停止采集（不影响视频录制）")

    def _run_session(self):
        from playwright.sync_api import sync_playwright

        from toolbox import live_stream
        from toolbox.browser import launch as pw_launch

        with sync_playwright() as pw:
            browser = pw_launch(
                pw, headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"])
            try:
                context = browser.new_context(
                    user_agent=live_stream.UA, locale="zh-CN",
                    viewport={"width": 1280, "height": 800})
                cookies = live_stream.context_cookies(self.platform)
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception:  # noqa: BLE001 - 无效单项不阻断游客访问
                        pass
                page = context.new_page()
                page.on("websocket", self._on_socket)
                page.goto(live_stream.live_url(self.platform, self.room),
                          wait_until="domcontentloaded", timeout=20_000)
                self._status("connected", "已连接 · 房间 %s（页面消息通道）" % self.room)
                self._poke(page)
                last_poke = time.monotonic()
                while not self._stop.is_set():
                    try:
                        page.wait_for_timeout(400)
                    except Exception:
                        break
                    self._drain()
                    if time.monotonic() - last_poke >= 30:
                        self._poke(page)
                        last_poke = time.monotonic()
            finally:
                self._drain()
                browser.close()

    def _on_socket(self, ws):
        try:
            ws.on("framereceived", self._on_frame)
        except Exception:
            pass

    def _on_frame(self, payload):
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped += 1

    def _drain(self):
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                return
            for item in normalize_payload(self.platform, payload):
                self._emit_item(item)

    def _emit_item(self, item):
        key = (item["name"], item["m"].get("nickname"), item["m"].get("content"),
               item["m"].get("giftName"), item["m"].get("giftCount"))
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and now - last < DEDUPE_SECONDS:
            return
        self._seen[key] = now
        if len(self._seen) > 5000:
            cutoff = now - DEDUPE_SECONDS * 2
            self._seen = {k: ts for k, ts in self._seen.items() if ts >= cutoff}
        self._emit(self.on_message, item["name"], item["text"], item["m"])

    @staticmethod
    def _poke(page):
        for label in ("进入直播间", "继续观看", "点击观看"):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=600)
                break
            except Exception:
                continue
        try:
            page.evaluate(
                "() => document.querySelectorAll('video,audio').forEach(e => {"
                "e.muted = true; e.play && e.play().catch(() => {}); })")
        except Exception:
            pass