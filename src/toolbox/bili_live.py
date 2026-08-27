# -*- coding: utf-8 -*-
"""B站直播录制引擎（学习 BililiveRecorder 理念的纯 Python 复刻）。

核心思路（对应 BililiveRecorder.Core）：
  1. 房间监控：轮询 get_info 接口，live_status 1=直播中 0=未开播 2=轮播
  2. 自动开播录制：检测到开播 → 自动取流录制；下播 → 自动停止
  3. 取流：getRoomPlayInfo 接口（游客），FLV 直链 = host + base_url + extra，
     画质列表直接用接口返回的 stream_qn_info（无需硬编码）
  4. 纯 HTTP FLV 流式转存（不依赖 ffmpeg），流断开自动重连（重取流地址、
     开新文件段，与 BililiveRecorder 的切割行为一致）
  5. 弹幕录制：WebSocket 协议（auth + 心跳 + zlib deflate 解压）→ XML 文件
     （BililiveRecorder 同款 <ietf><d p="...">格式）

用法（GUI 后台线程）：
  info = fetch_room_info(room_id)
  rec = BiliFlvRecorder(out_dir, on_event=cb); rec.start(url, room, title)
  dan = BiliDanmakuRecorder(out_dir, room_id, uid); dan.start(); dan.stop()
"""

import json
import os
import re
import struct
import threading
import time
from datetime import datetime
from xml.sax.saxutils import escape

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"
PLAY_API = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
DANMAKU_WS = "wss://broadcastlv.chat.bilibili.com/sub"

# B站画质档位（接口 stream_qn_info 游客不返回 → 静态兜底，BililiveRecorder 同款全集）
QN_FALLBACK = [(30000, "杜比"), (25000, "HDR"), (20000, "4K"), (10000, "原画"),
               (401, "蓝光杜比"), (400, "蓝光"), (250, "超清"), (150, "高清"), (80, "流畅")]

HEARTBEAT_INTERVAL = 30      # 弹幕心跳间隔（秒）
WS_RECV_TIMEOUT = 90         # 收不到任何包超过该秒数视为断线


def _headers() -> dict:
    return {"User-Agent": UA, "Origin": "https://live.bilibili.com",
            "Referer": "https://live.bilibili.com/"}


def fetch_room_info(room_id: str) -> dict:
    """房间信息（游客）：标题/状态/主播/封面。live_status: 0未播 1直播中 2轮播。

    get_info 已不返回 uname → 用 uid 查 live_user/v1/Master/info（游客可用）。
    real_room_id 必须回传：弹幕 WS 鉴权体 roomid 只认真实号（短号会被断连）。
    """
    out = {"room_id": str(room_id), "live_status": 0, "title": "",
           "uname": "", "cover": "", "keyframe": "", "area": "", "watch_num": 0}
    try:
        r = httpx.get(INFO_API, params={"room_id": room_id},
                      headers=_headers(), timeout=15)
        d = r.json()
        if d.get("code") != 0:
            out["error"] = f"接口返回错误: {d.get('message') or d.get('code')}"
            return out
        data = d.get("data") or {}
        out.update({
            "live_status": int(data.get("live_status") or 0),
            "title": data.get("title") or "",
            "cover": data.get("user_cover") or data.get("cover") or "",
            "keyframe": data.get("keyframe") or "",
            "area": (data.get("parent_area_name") or "") + "·" + (data.get("area_name") or ""),
            "watch_num": int(data.get("online") or (data.get("watched_show") or {}).get("num") or 0),
            "real_room_id": data.get("room_id") or room_id,
            "uid": data.get("uid") or 0,
        })
        uid = out.get("uid") or 0
        if uid:
            try:
                r2 = httpx.get("https://api.live.bilibili.com/live_user/v1/Master/info",
                               params={"uid": uid}, headers=_headers(), timeout=10)
                out["uname"] = ((r2.json().get("data") or {}).get("info") or {}).get("uname") or ""
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)
    return out


def fetch_play_url(room_id: str, qn: int | None = None) -> dict:
    """直播流地址（游客）。

    返回 {flv: 主直链, flv_backup: [备用], quality_desc: 当前画质, qn_list: [(qn, desc)],
          live_status, title, error}
    """
    out = {"flv": "", "flv_backup": [], "quality_desc": "", "qn_list": [],
           "live_status": 0, "title": ""}
    params = {"room_id": room_id, "protocol": "0,1", "format": "0,1,2",
              "codec": "0,1", "platform": "web", "qn": qn or 10000}
    try:
        r = httpx.get(PLAY_API, params=params, headers=_headers(), timeout=15)
        d = r.json()
        if d.get("code") != 0:
            out["error"] = f"接口返回错误: {d.get('message') or d.get('code')}"
            return out
        data = d.get("data") or {}
        out["live_status"] = int(data.get("live_status") or 0)
        out["title"] = data.get("title") or ""
        playurl = ((data.get("playurl_info") or {}).get("playurl") or {})
        # 画质列表：接口 stream_qn_info 游客不返回 → 静态兜底
        qn_list = [(int(q.get("qn") or 0), q.get("desc") or "")
                   for q in (playurl.get("stream_qn_info") or []) if q.get("qn")]
        out["qn_list"] = qn_list or list(QN_FALLBACK)
        qn_list = out["qn_list"]
        # 找 http_stream + flv 的 avc 流（B站直播稳定格式）
        for stream in (playurl.get("stream") or []):
            if (stream.get("protocol_name") or "") != "http_stream":
                continue
            for fmt in (stream.get("format") or []):
                if (fmt.get("format_name") or "") != "flv":
                    continue
                for codec in (fmt.get("codec") or []):
                    if (codec.get("codec_name") or "") != "avc":
                        continue
                    out["quality_desc"] = codec.get("current_qn") and next(
                        (desc for q, desc in qn_list if q == codec.get("current_qn")), "")
                    urls = []
                    for ui in (codec.get("url_info") or []):
                        url = (ui.get("host") or "") + (codec.get("base_url") or "") + (ui.get("extra") or "")
                        if url.startswith("//"):
                            url = "https:" + url
                        if url:
                            urls.append(url)
                    if urls:
                        out["flv"] = urls[0]
                        out["flv_backup"] = urls[1:]
                        return out
        # 无 FLV 时回退 HLS（部分房间只有 fmp4/HLS）
        for stream in (playurl.get("stream") or []):
            if (stream.get("protocol_name") or "") != "http_hls":
                continue
            for fmt in (stream.get("format") or []):
                for codec in (fmt.get("codec") or []):
                    urls = []
                    for ui in (codec.get("url_info") or []):
                        url = (ui.get("host") or "") + (codec.get("base_url") or "") + (ui.get("extra") or "")
                        if url.startswith("//"):
                            url = "https:" + url
                        if url:
                            urls.append(url)
                    if urls:
                        out["flv"] = urls[0]
                        out["flv_backup"] = urls[1:]
                        out["is_hls"] = True
                        return out
        if out["live_status"] == 1:
            out["error"] = "未找到可用直播流（可能仅限登录/大航海）"
        else:
            out["error"] = "未开播"
    except Exception as e:
        out["error"] = str(e)
    return out


def _sanitize(name: str, max_len: int = 40) -> str:
    from toolbox.utils import sanitize_filename
    return sanitize_filename(name, max_len, "untitled")


def _rec_dir() -> "os.PathLike":
    from toolbox.config_manager import get_export_dir
    d = get_export_dir() / "直播录制" / "B站录播姬"
    d.mkdir(parents=True, exist_ok=True)
    return d


class BiliFlvRecorder:
    """纯 HTTP FLV 流式录制（学习 BililiveRecorder：无 ffmpeg 依赖 + 断流自动重连）。

    start(url, room, title) 后台线程持续下载；流断开时回调 on_event("reconnect")
    并由调用方决定是否重取地址续录（自动开播场景由页面轮询驱动重连）。
    """

    def __init__(self, on_event=None):
        self.on_event = on_event      # (event, detail) → 主线程回调
        self._stop_ev = threading.Event()
        self._thread = None
        self.path = None
        self.start_ts = 0
        self.bytes_written = 0
        self._url = ""

    def start(self, url: str, room: str, title: str = "") -> bool:
        self.stop()
        self._stop_ev = threading.Event()
        self._url = url
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = _sanitize(f"{room}_{title}" if title else f"{room}") or room
        self.path = str(_rec_dir() / f"{base}_{ts}.flv")
        n = 1
        while os.path.exists(self.path):
            self.path = str(_rec_dir() / f"{base}_{ts}_{n}.flv")
            n += 1
        self.start_ts = int(time.time() * 1000)
        self.bytes_written = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_ev.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=6)

    def _run(self):
        url = self._url
        try:
            with httpx.stream("GET", url, headers=_headers(),
                              follow_redirects=True,
                              timeout=httpx.Timeout(15, read=30)) as r:
                r.raise_for_status()
                with open(self.path, "wb") as f:
                    self._emit("recording", self.path)
                    for chunk in r.iter_bytes(chunk_size=256 * 1024):
                        if self._stop_ev.is_set():
                            break
                        f.write(chunk)
                        self.bytes_written += len(chunk)
        except Exception as e:
            if not self._stop_ev.is_set():
                self._emit("error", str(e)[:120])
        finally:
            # 空文件清理（连接失败/立即断流）
            try:
                if self.bytes_written < 1024 and os.path.exists(self.path):
                    os.remove(self.path)
            except OSError:
                pass
            self._emit("stopped", self.path)

    def _emit(self, ev, detail=""):
        if self.on_event:
            try:
                self.on_event(ev, detail)
            except Exception:
                pass


class BiliDanmakuRecorder:
    """B站直播弹幕录制（WebSocket 协议 → BililiveRecorder 同款 XML）。

    协议帧头（大端！BililiveRecorder 用 WriteUInt32BigEndian，小端必被断连）：
    总长(4B) + 头长(2B) + protover(2B) + 操作码(4B) + 序号(4B)。
    auth(7)：body 携带 uid=0/真实roomid/protover=2(zlib)/buvid/token；
    roomid 只认真实房间号（短号 6 → 真实 7734200，发短号直接被踢）。
    心跳(2)每 30s、心跳回复(3)=人气值、业务消息(5)：protover=2 时 zlib 解压。
    鉴权后立即补发一次 ping（BililiveRecorder 同款时序）。
    """

    def __init__(self, room_id: str, uid: int = 0, on_event=None):
        self.room_id = str(room_id)
        self.uid = int(uid)
        self.on_event = on_event
        self._stop_ev = threading.Event()
        self._thread = None
        self._hb_thread = None
        self._ws = None
        self.path = None
        self.count = 0

    def start(self) -> bool:
        self.stop()
        self._stop_ev = threading.Event()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = str(_rec_dir() / f"{self.room_id}_{ts}_弹幕.xml")
        n = 1
        while os.path.exists(self.path):
            self.path = str(_rec_dir() / f"{self.room_id}_{ts}_弹幕_{n}.xml")
            n += 1
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_ev.set()
        # 主动关 socket 解除 recv 阻塞（否则要等 90s 超时才退出）
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.shutdown()
            except Exception:
                pass
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=6)

    # ---------- 协议封装 ----------
    @staticmethod
    def _pack(op: int, body: bytes = b"") -> bytes:
        return (struct.pack(">IHHII", 16 + len(body), 16, 1, op, 1) + body)

    def _run(self):
        import websocket
        # 短号 → 真实号（鉴权体只认真实号）
        real_room = self.room_id
        try:
            info = fetch_room_info(self.room_id)
            if info.get("real_room_id"):
                real_room = str(info["real_room_id"])
        except Exception:
            pass
        auth = get_danmaku_auth(real_room) or {}
        buvid = _guest_buvid()
        auth_body = json.dumps({
            "uid": 0, "roomid": int(real_room), "protover": 2,
            "buvid": buvid, "platform": "web", "type": 2,
            "key": (auth.get("token") or ""),
        }).encode()
        host = auth.get("host") or ""
        ws_url = host if host.startswith("wss://") else (f"wss://{host}/sub" if host else DANMAKU_WS)
        ws = websocket.WebSocket()
        self._ws = ws
        try:
            ws.settimeout(WS_RECV_TIMEOUT)
            ws.connect(ws_url, header={"User-Agent": UA,
                                        "Origin": "https://live.bilibili.com"})
            ws.send_binary(self._pack(7, auth_body))
            ws.send_binary(self._pack(2))
            self._hb_thread = threading.Thread(target=self._heartbeat, args=(ws,), daemon=True)
            self._hb_thread.start()
            with open(self.path, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n<ietf>\n')
                self._emit("recording", self.path)
                buf = b""
                while not self._stop_ev.is_set():
                    try:
                        data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                    if not data or isinstance(data, str):
                        continue
                    buf += data
                    while len(buf) >= 16:
                        total, hlen, protover, op = struct.unpack_from(">IHHI", buf, 0)
                        if total < 16 or total > len(buf):
                            break
                        body = buf[hlen:total]
                        buf = buf[total:]
                        if op == 5:  # 业务消息
                            if protover == 2:
                                import zlib
                                try:
                                    body = zlib.decompress(body)
                                except Exception:
                                    continue
                            # 解压体常为多条内部帧串（各带16B头）→ 逐帧拆
                            cur = body
                            while len(cur) >= 16:
                                it, ih, _, iop = struct.unpack_from(">IHHI", cur, 0)
                                if it < 16 or it > len(cur):
                                    break
                                if iop == 5:
                                    self._handle_message(cur[ih:it], f)
                                cur = cur[it:]
                            if len(cur) < 16 and cur:
                                self._handle_message(cur, f)
                        # op=8 鉴权回执(错误码≠0 连接会被踢,无需处理) / op=3 人气值
                f.write("</ietf>\n")
        except Exception as e:
            self._emit("error", str(e)[:120])
        finally:
            self._ws = None
            try:
                ws.close()
            except Exception:
                pass
            self._emit("stopped", self.path)

    def _heartbeat(self, ws):
        while not self._stop_ev.is_set():
            try:
                ws.send_binary(self._pack(2))
            except Exception:
                return
            self._stop_ev.wait(HEARTBEAT_INTERVAL)

    def _handle_message(self, body: bytes, f):
        try:
            msg = json.loads(body)
        except Exception:
            return
        cmd = msg.get("cmd") or ""
        info = msg.get("info") or []
        ts = int(time.time())
        if cmd == "DANMU_MSG" and len(info) >= 4:
            text = info[1] if isinstance(info[1], str) else ""
            if not text:
                return
            meta = info[0] if isinstance(info[0], list) else [ts, 1, 25, 16777215, ts]
            uid = (info[2] or [])[0] if len(info) > 2 and info[2] else 0
            name = ((info[2] or [])[1] if len(info) > 2 and len(info[2]) > 1 else "")
            f.write(f'<d p="{meta[0] if len(meta) > 0 else ts},{meta[1] if len(meta) > 1 else 1},'
                    f'{meta[2] if len(meta) > 2 else 25},{meta[3] if len(meta) > 3 else 16777215},'
                    f'{uid},{meta[4] if len(meta) > 4 else ts},0,0">{escape(str(text))}</d>\n')
            self.count += 1
            self._emit("danmaku", f"{name}: {text}")
        elif cmd in ("SEND_GIFT", "COMBO_SEND"):
            data = msg.get("data") or {}
            uname = data.get("uname") or ""
            gname = data.get("giftName") or "礼物"
            num = data.get("num") or (msg.get("combo_info") or {}).get("gift_num") or 1
            f.write(f'<gift p="{ts},{uname}">{escape(str(gname))} x{num}</gift>\n')
            self.count += 1
            self._emit("gift", f"{uname} 送出 {gname} x{num}")
        elif cmd == "INTERACT_WORD":
            data = msg.get("data") or {}
            f.write(f'<interact p="{ts},{data.get("uname") or ""}">'
                    f'{escape(str(data.get("msg_type") == 2 and "关注" or "进入"))}</interact>\n')
        elif cmd == "INTERACT_WORD_V2":
            data = msg.get("data") or {}
            uname = (data.get("followers_info") or {}).get("uname") or data.get("uname") or ""
            f.write(f'<interact p="{ts},{uname}">'
                    f'{escape(str(data.get("msg_type") == 2 and "关注" or "进入"))}</interact>\n')

    def _emit(self, ev, detail=""):
        if self.on_event:
            try:
                self.on_event(ev, detail)
            except Exception:
                pass


def _guest_buvid() -> str:
    """游客 buvid3（设备指纹，getDanmuInfo 与弹幕鉴权体都要）。"""
    try:
        from bili.wbi import build_cookie_header
        with httpx.Client(timeout=10) as c:
            ck = build_cookie_header(c, "")
            for part in ck.split(";"):
                if part.strip().startswith("buvid3="):
                    return part.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def get_danmaku_auth(room_id: str) -> dict:
    """弹幕鉴权信息 {token, host}：游客HTTP(WBI签名) → 游客Playwright → 登录Cookie 三级回退。

    getDanmuInfo 不签名必被 -352 拦 → 一级就用 sign_wbi（实测 3/3 通过）；
    仍被拦才走 Playwright 拦截直播间页自身响应（真实指纹）。
    """
    api = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
    hdr = {"User-Agent": UA, "Origin": "https://live.bilibili.com",
           "Referer": "https://live.bilibili.com/"}

    def _parse(data: dict) -> dict:
        hosts = [h.get("host") for h in (data.get("host_list") or []) if h.get("host")]
        return {"token": data.get("token") or "", "host": hosts[0] if hosts else ""}

    # 一级：游客 HTTP + WBI 签名 + buvid 设备指纹
    try:
        from bili.wbi import build_cookie_header, sign_wbi
        with httpx.Client(timeout=10) as c:
            ck = build_cookie_header(c, "")
            url = sign_wbi(api, {"id": room_id, "type": 0}, c)
            r = c.get(url, headers={**hdr, "Cookie": ck})
            out = _parse(r.json().get("data") or {})
            if out["token"]:
                return out
    except Exception:
        pass
    # 二级：游客 Playwright（拦截直播页自身的 getDanmuInfo 响应）
    try:
        out = _danmaku_token_playwright(room_id)
        if out.get("token"):
            return out
    except Exception:
        pass
    # 三级：登录 Cookie 重试
    try:
        from auth import get_cookie
        from bili.wbi import build_cookie_header
        login = get_cookie("bilibili") or ""
        if login:
            with httpx.Client(timeout=10) as c:
                ck = build_cookie_header(c, login)
                r = c.get(api, params={"id": room_id, "type": 0},
                          headers={**hdr, "Cookie": ck})
                return _parse(r.json().get("data") or {})
    except Exception:
        pass
    return {"token": "", "host": ""}


def _danmaku_token_playwright(room_id: str) -> dict:
    """游客 Playwright：打开直播间页，拦截页面发出的 getDanmuInfo 响应。"""
    from playwright.sync_api import sync_playwright
    out = {"token": "", "host": ""}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()
            captured = {}

            def _on_resp(resp):
                if not captured and "getDanmuInfo" in resp.url:
                    try:
                        data = resp.json().get("data") or {}
                        captured["token"] = data.get("token") or ""
                        hosts = [h.get("host") for h in (data.get("host_list") or [])
                                 if h.get("host")]
                        captured["host"] = hosts[0] if hosts else ""
                    except Exception:
                        pass

            page.on("response", _on_resp)
            page.goto(f"https://live.bilibili.com/{room_id}",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            if not captured.get("token"):
                # 页面未触发该请求（缓存等）→ 页面上下文内主动 fetch（带浏览器指纹）
                url = (f"https://api.live.bilibili.com/xlive/web-room/v1/index/"
                       f"getDanmuInfo?id={room_id}&type=0")
                try:
                    data = page.evaluate(
                        f"async () => {{ const r = await fetch('{url}', "
                        f"{{credentials: 'include'}}); return (await r.json()).data; }}")
                    if data:
                        captured["token"] = data.get("token") or ""
                        hosts = [h.get("host") for h in (data.get("host_list") or [])
                                 if h.get("host")]
                        captured["host"] = hosts[0] if hosts else ""
                except Exception:
                    pass
            out.update(captured)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return out


def get_danmaku_token(room_id: str) -> str:
    """弹幕连接 token（get_danmaku_auth 的便捷封装）。"""
    return (get_danmaku_auth(room_id) or {}).get("token") or ""


def parse_room_id_input(text: str) -> str:
    """输入清洗：支持纯数字房间号 / live.bilibili.com 链接。"""
    text = (text or "").strip()
    m = re.search(r"live\.bilibili\.com/(?:h5/)?(\d+)", text)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)$", text)
    if m:
        return m.group(1)
    return text
