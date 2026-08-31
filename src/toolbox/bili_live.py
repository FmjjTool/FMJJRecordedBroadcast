# -*- coding: utf-8 -*-
"""B站直播采集后端（学习 BililiveRecorder 理念的纯 Python 复刻）。

两类使用者：
  1. B站弹幕姬页（gui_qt/pages/bili_danmaku.py）：BiliLiveFetcher 弹幕抓取 +
     fetch_room_info 房间信息 + fetch_play_url 直播流 + StatsCollector/LiveMonitor。
  2. 协议要点：
     - 房间信息：room/v1/Room/get_info（游客），live_status 0未播 1直播中 2轮播
     - 取流：getRoomPlayInfo（游客），FLV 直链 = host + base_url + extra，
       画质列表直接用接口返回的 stream_qn_info（缺失时静态兜底）
     - 弹幕：WebSocket（wss://broadcastlv.chat.bilibili.com/sub），
       鉴权(7) + 心跳(2, 30s) + zlib(2) 解压，帧头大端
"""

import json
import re
import struct
import threading
import time
from datetime import datetime

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

DEFAULT_ROOM = "544820"       # 默认示例房号（与直播间表情页一致，用户确认可用）


def _headers() -> dict:
    return {"User-Agent": UA, "Origin": "https://live.bilibili.com",
            "Referer": "https://live.bilibili.com/"}


def fetch_room_info(room_id: str) -> dict:
    """房间信息（游客）：标题/状态/主播/封面/粉丝。live_status: 0未播 1直播中 2轮播。

    get_info 已不返回 uname/fans → 用 uid 查 live_user/v1/Master/info（游客可用）。
    real_room_id 必须回传：弹幕 WS 鉴权体 roomid 只认真实号（短号会被断连）。
    """
    out = {"room_id": str(room_id), "live_status": 0, "title": "",
           "uname": "", "cover": "", "keyframe": "", "area": "", "watch_num": 0,
           "fans": 0, "avatar": ""}
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
                d2 = r2.json().get("data") or {}
                out["uname"] = (d2.get("info") or {}).get("uname") or ""
                out["avatar"] = (d2.get("info") or {}).get("face") or ""
                try:
                    out["fans"] = int(d2.get("follower")
                                      or (d2.get("info") or {}).get("fans") or 0)
                except (TypeError, ValueError):
                    out["fans"] = 0
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


# ==================== 弹幕姬后端（与 douyin_live 同构接口） ====================

DEFAULT_SETTINGS = {
    "monitor_interval": 30,     # 开播监控检测间隔（秒）
    "stats_interval": 1,        # 统计数据采集间隔（秒）
    "poll_interval": 60,        # 轮询间隔（秒）
    "save_danmaku": False,      # 保存弹幕到文件
    "save_stats": True,         # 保存统计数据（默认勾选）
    "keep_scroll": True,        # 保持滚动（强制最新）
    "dark_mode": True,          # 深色模式
    "last_room": DEFAULT_ROOM,  # 默认房号
    "poll_enabled": True,       # 轮询开关（顶栏）
    "mode": "danmaku_only",     # 弹幕方式：默认仅弹幕
    "auto_record": False,       # 自动录播（勾选后拿到流即录）
    "auto_save_danmaku": True,  # 自动导出回放包
}


def _settings_path():
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir() / "bili_live.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "config" / "bili_live.json"


def load_settings():
    p = _settings_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s):
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _danmaku_dir():
    """弹幕导出目录：统一导出路径/弹幕文件B站/（与抖音弹幕姬分开存放）。"""
    try:
        from toolbox.config_manager import get_export_subdir
        return get_export_subdir("danmaku_bili")
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "download" / "弹幕文件B站"


def _stats_dir():
    """统计数据导出目录：与弹幕同目录。"""
    return _danmaku_dir()


def append_stats_file(room_num, stats_line):
    """把一条统计数据追加写入文件（弹幕文件B站/房间号_日期.csv）。"""
    try:
        d = _stats_dir()
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{room_num}_{datetime.now().strftime('%Y%m%d')}.csv"
        path = d / fname
        if not path.exists():
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("时间,在线人数,点赞数,粉丝数,累计观看\n")
        with open(path, "a", encoding="utf-8-sig") as f:
            f.write(stats_line + "\n")
        return str(path)
    except Exception:
        return ""


def check_live_status(room_id):
    """检测直播间是否在直播（游客态）。返回 (是否直播中, info)。"""
    info = fetch_room_info(room_id)
    return int(info.get("live_status") or 0) == 1, info


def _norm_info(info):
    """fetch_room_info 原始 dict → 弹幕姬页面契约字段（展示用字符串）。"""
    out = {}
    if info.get("uname"):
        out["anchor"] = info["uname"]
    if info.get("title"):
        out["title"] = info["title"]
    try:
        if int(info.get("fans") or 0):
            out["fans"] = f"{int(info['fans']):,}"
    except (TypeError, ValueError):
        pass
    try:
        if int(info.get("watch_num") or 0):
            out["online"] = f"{int(info['watch_num']):,}"
    except (TypeError, ValueError):
        pass
    cover = info.get("cover") or info.get("keyframe") or ""
    if cover:
        out["cover"] = cover
    if info.get("avatar"):
        out["avatar"] = info["avatar"]
    return out


class StatsCollector:
    """统计数据采集器：定时拉房间信息（人气值/粉丝数），通过回调推送。"""

    def __init__(self, room_num, interval, on_stats=None):
        self.room_num = room_num
        self.interval = max(1, int(interval))
        self.on_stats = on_stats
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                info = fetch_room_info(self.room_num)
                if self.on_stats:
                    try:
                        self.on_stats(info)
                    except Exception:
                        pass
            except Exception:
                pass
            self._stop.wait(self.interval)


class LiveMonitor:
    """开播监控：定时检测直播状态，状态变化时回调。"""

    def __init__(self, room_num, interval=30, on_change=None):
        self.room_num = room_num
        self.interval = max(10, int(interval))
        self.on_change = on_change  # (is_live, info) -> None
        self._stop = threading.Event()
        self._thread = None
        self._last = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                is_live, info = check_live_status(self.room_num)
                if self._last is None or is_live != self._last:
                    self._last = is_live
                    if self.on_change:
                        try:
                            self.on_change(is_live, _norm_info(info))
                        except Exception:
                            pass
            except Exception:
                pass
            self._stop.wait(self.interval)


# 弹幕 cmd → (中文名, 分类)
# 分类：chat 弹幕 / gift 礼物 / member 进场 / like 点赞 / social 关注 / other 日志
MSG_TYPES = {
    "DANMU_MSG": ("弹幕", "chat"),
    "DANMU_MSG_EMOJI": ("表情弹幕", "chat"),
    "SEND_GIFT": ("礼物", "gift"),
    "SUPER_CHAT_MESSAGE": ("礼物", "gift"),
    "GUARD_BUY": ("礼物", "gift"),
    "INTERACT_WORD": ("进场", "member"),
    "INTERACT_WORD_V2": ("进场", "member"),
    "INTERACT_WORD_FOLLOW": ("关注", "social"),
    "ENTRY_EFFECT": ("进场", "member"),
    "LIKE_INFO_V3_CLICK": ("点赞", "like"),
    "LIKE_INFO_V3_UPDATE": ("点赞", "like"),
    "WATCHED_CHANGE": ("在线人数", "other"),
    "ONLINE_RANK_COUNT": ("在线人数", "other"),
    "ROOM_REAL_TIME_MESSAGE_UPDATE": ("粉丝数", "other"),
}


class BiliLiveFetcher:
    """B站直播弹幕抓取器（后台线程，与 DouyinLiveFetcher 同构接口）。

    on_message(name, text, m)：name 为中文类型（弹幕/礼物/进场/关注/点赞...），
        text 为 '[昵称]：内容'，m 为原始字段 dict（礼物价格/点赞总数/在线人数等）。
    on_room_info(info)：房间信息 dict（anchor/title/fans/online/streams/qn_list...）。
    on_status(status, msg)：状态变化（connecting/connected/disconnected/error/not_live）。
    """

    def __init__(self, room_id, on_message=None, on_room_info=None, on_status=None):
        self.room_id = str(room_id)
        self.on_message = on_message
        self.on_room_info = on_room_info
        self.on_status = on_status
        self._stop_ev = threading.Event()
        self._thread = None
        self._ws = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_ev.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_ev.set()
        # 主动关 socket 解除 recv 阻塞（否则要等 90s 超时才退出）
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.shutdown()
            except Exception:
                pass

    def _emit(self, cb, *args):
        if cb:
            try:
                cb(*args)
            except Exception:
                pass

    @staticmethod
    def _pack(op: int, body: bytes = b"") -> bytes:
        return (struct.pack(">IHHII", 16 + len(body), 16, 1, op, 1) + body)

    @staticmethod
    def _fmt(nick, content):
        nick = (nick or "").strip()
        content = (content or "").strip()
        if nick and content:
            return f"[{nick}]：{content}"
        if nick:
            return f"[{nick}]："
        return content

    def _run(self):
        real_room = self._prepare()
        if not real_room:
            return
        try:
            self._ws_loop(real_room)
        finally:
            # 终态只发一次：手动停止='已停止'，其余（下播/断线/超时）='直播已结束'
            self._emit(self.on_status, "disconnected",
                       "已停止" if self._stop_ev.is_set() else "直播已结束")

    def _prepare(self):
        """取房间信息 + 直播流。返回真实房间号（str），失败/未开播返回 None（状态已上报）。"""
        self._emit(self.on_status, "connecting", "正在获取房间信息...")
        info = fetch_room_info(self.room_id)
        real_room = str(info.get("real_room_id") or "").strip()
        if info.get("error") or not real_room.isdigit() or int(real_room) <= 0:
            self._emit(self.on_status, "error",
                       info.get("error") or "未获取到房间信息（直播间可能不存在）")
            return None
        self._emit(self.on_room_info, _norm_info(info))
        if self._stop_ev.is_set():
            return None
        if int(info.get("live_status") or 0) == 0:
            self._emit(self.on_status, "not_live",
                       f"主播当前未开播（房间 {self.room_id}）")
            return None
        # 直播流（取不到不阻塞弹幕连接，仅无预览/录制）
        self._emit(self.on_status, "connecting", "正在获取直播流...")
        try:
            play = fetch_play_url(real_room)
            if play.get("flv"):
                extra = {"title": play.get("title") or ""}
                extra["streams"] = {play.get("quality_desc") or "直播流": play["flv"]}
                extra["qn_list"] = play.get("qn_list") or []
                self._emit(self.on_room_info, extra)
        except Exception:
            pass
        return real_room

    def _ws_loop(self, real_room: str):
        """弹幕 WS 连接主循环（协议同 BililiveRecorder：鉴权→心跳→收帧）。"""
        import websocket
        auth = get_danmaku_auth(real_room) or {}
        auth_body = json.dumps({
            "uid": 0, "roomid": int(real_room), "protover": 2,
            "buvid": _guest_buvid(), "platform": "web", "type": 2,
            "key": (auth.get("token") or ""),
        }).encode()
        host = auth.get("host") or ""
        ws_url = host if host.startswith("wss://") else (f"wss://{host}/sub" if host else DANMAKU_WS)
        ws = websocket.WebSocket()
        self._ws = ws
        try:
            # recv 超时=心跳间隔：超时即发心跳（send/recv 同线程，websocket-client
            # 非线程安全，独立心跳线程并发写 socket 会被服务端当坏帧断连）
            ws.settimeout(HEARTBEAT_INTERVAL)
            ws.connect(ws_url, header={"User-Agent": UA,
                                       "Origin": "https://live.bilibili.com"})
            ws.send_binary(self._pack(7, auth_body))
            ws.send_binary(self._pack(2))
            authed = False
            buf = b""
            idle = 0
            while not self._stop_ev.is_set():
                try:
                    data = ws.recv()
                except websocket.WebSocketTimeoutException:
                    idle += 1
                    if idle * HEARTBEAT_INTERVAL >= WS_RECV_TIMEOUT:
                        break  # 连续超时累计≥断线阈值：连接已死
                    try:
                        ws.send_binary(self._pack(2))
                    except Exception:
                        break
                    continue
                except Exception:
                    break
                idle = 0
                if not data or isinstance(data, str):
                    continue
                buf += data
                while len(buf) >= 16:
                    total, hlen, protover, op = struct.unpack_from(">IHHI", buf, 0)
                    if total < 16 or total > len(buf):
                        break
                    body = buf[hlen:total]
                    buf = buf[total:]
                    if op == 8:
                        # 鉴权回执：code≠0 会被服务端断开，这里主动报错
                        try:
                            if (json.loads(body) or {}).get("code") != 0:
                                self._emit(self.on_status, "error", "弹幕通道鉴权失败（token 无效）")
                                return
                        except Exception:
                            pass
                        if not authed:
                            authed = True
                            self._emit(self.on_status, "connected",
                                       f"已连接弹幕通道 · 房间 {real_room}")
                    elif op == 5:
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
                                self._handle_frame(cur[ih:it])
                            cur = cur[it:]
                        if len(cur) < 16 and cur:
                            self._handle_frame(cur)
                        if not authed:
                            authed = True
                            self._emit(self.on_status, "connected",
                                       f"已连接弹幕通道 · 房间 {real_room}")
                    # op=3 人气值 / op=2 心跳回复：忽略
        finally:
            self._ws = None
            try:
                ws.close()
            except Exception:
                pass

    # ---------- 消息解析（cmd → on_message） ----------
    def _handle_frame(self, body: bytes):
        try:
            msg = json.loads(body)
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        base = (msg.get("cmd") or "").split(":")[0]
        now_ms = int(time.time() * 1000)
        if base == "DANMU_MSG":
            self._on_danmu(msg, now_ms)
        elif base == "SEND_GIFT":
            self._on_gift(msg, now_ms)
        elif base == "SUPER_CHAT_MESSAGE":
            self._on_super_chat(msg, now_ms)
        elif base == "GUARD_BUY":
            self._on_guard(msg, now_ms)
        elif base in ("INTERACT_WORD", "INTERACT_WORD_V2"):
            self._on_interact(msg, now_ms, v2=(base == "INTERACT_WORD_V2"))
        elif base == "ENTRY_EFFECT":
            self._on_entry_effect(msg, now_ms)
        elif base == "LIKE_INFO_V3_CLICK":
            data = msg.get("data") or {}
            nick = data.get("uname") or ""
            m = {"type": "LIKE_INFO_V3_CLICK", "ts": now_ms, "nickname": nick}
            self._emit(self.on_message, "点赞", self._fmt(nick, "为主播点赞了"), m)
        elif base == "LIKE_INFO_V3_UPDATE":
            data = msg.get("data") or {}
            try:
                total = int(data.get("count") or 0)
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                m = {"type": "LIKE_INFO_V3_UPDATE", "ts": now_ms, "likeTotal": total}
                self._emit(self.on_message, "点赞", f"本场点赞 {total:,}", m)
        elif base == "WATCHED_CHANGE":
            data = msg.get("data") or {}
            try:
                num = int(data.get("num") or 0)
            except (TypeError, ValueError):
                num = 0
            if num > 0:
                m = {"type": "WATCHED_CHANGE", "ts": now_ms, "roomTotalUser": num}
                self._emit(self.on_message, "在线人数", "", m)
        elif base == "ONLINE_RANK_COUNT":
            data = msg.get("data") or {}
            try:
                cnt = int(data.get("count") or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt > 0:
                m = {"type": "ONLINE_RANK_COUNT", "ts": now_ms, "roomTotal": cnt}
                self._emit(self.on_message, "在线人数", "", m)
        elif base == "ROOM_REAL_TIME_MESSAGE_UPDATE":
            data = msg.get("data") or {}
            try:
                fans = int(data.get("fans") or 0)
            except (TypeError, ValueError):
                fans = 0
            if fans > 0:
                m = {"type": "ROOM_REAL_TIME_MESSAGE_UPDATE", "ts": now_ms,
                     "followCount": fans}
                self._emit(self.on_message, "粉丝数", "", m)
        elif base == "ROOM_CHANGE":
            data = msg.get("data") or {}
            if data.get("title"):
                self._emit(self.on_room_info, {"title": data.get("title")})
        # PREPARING（下播）/LIVE（开播）：由断线路径统一回报终态

    def _on_danmu(self, msg, now_ms):
        info = msg.get("info") or []
        text = info[1] if len(info) > 1 and isinstance(info[1], str) else ""
        user = info[2] if len(info) > 2 and isinstance(info[2], list) else []
        nick = str(user[1]) if len(user) > 1 else ""
        if not text and not nick:
            return
        m = {"type": "DANMU_MSG", "ts": now_ms, "nickname": nick, "content": text}
        # 大表情弹幕：info[0][13] = {url, height, ...}
        try:
            meta = info[0] if isinstance(info[0], list) else []
            emo = meta[13] if len(meta) > 13 else None
            if isinstance(emo, dict) and emo.get("url"):
                m["rtf"] = [{"url": emo["url"], "text": "表情"}]
                m["type"] = "DANMU_MSG_EMOJI"
                if not text:
                    m["content"] = "[表情]"
        except Exception:
            pass
        name = "表情弹幕" if m.get("rtf") else "弹幕"
        self._emit(self.on_message, name, self._fmt(nick, m["content"]), m)

    def _on_gift(self, msg, now_ms):
        data = msg.get("data") or {}
        nick = data.get("uname") or ""
        gname = data.get("giftName") or "礼物"
        try:
            num = int(data.get("num") or 1)
        except (TypeError, ValueError):
            num = 1
        # 金瓜子礼物价格单位=电池（1000电池=1元），银瓜子礼物价值按 0 计
        try:
            price = int(data.get("price") or 0) if data.get("coin_type") == "gold" else 0
        except (TypeError, ValueError):
            price = 0
        gi = data.get("gift_info") or {}
        icon = gi.get("gift_pic") or gi.get("webp") or data.get("gift_pic") or ""
        m = {"type": "SEND_GIFT", "ts": now_ms, "nickname": nick,
             "content": f"送出 {gname} x{num}",
             "giftName": gname, "giftCount": num, "giftPrice": price,
             "giftIcon": icon, "repeatEnd": 1}
        self._emit(self.on_message, "礼物", self._fmt(nick, m["content"]), m)

    def _on_super_chat(self, msg, now_ms):
        data = msg.get("data") or {}
        nick = (data.get("user") or {}).get("uname") or ""
        try:
            price = int(float(data.get("price") or 0))
        except (TypeError, ValueError):
            price = 0
        # SC 价格单位=元，榜内统一换算为电池（×1000）与礼物可比
        m = {"type": "SUPER_CHAT_MESSAGE", "ts": now_ms, "nickname": nick,
             "content": f"醒目留言 ¥{price}：{data.get('message') or ''}",
             "giftName": "醒目留言", "giftCount": 1, "giftPrice": price * 1000,
             "giftIcon": "", "repeatEnd": 1}
        self._emit(self.on_message, "礼物", self._fmt(nick, m["content"]), m)

    def _on_guard(self, msg, now_ms):
        data = msg.get("data") or {}
        nick = data.get("uname") or ""
        gname = data.get("gift_name") or "舰长"
        try:
            price = int(float(data.get("price") or 0))
        except (TypeError, ValueError):
            price = 0
        try:
            num = int(data.get("num") or 1)
        except (TypeError, ValueError):
            num = 1
        m = {"type": "GUARD_BUY", "ts": now_ms, "nickname": nick,
             "content": f"开通{gname} x{num}",
             "giftName": gname, "giftCount": num, "giftPrice": price,
             "giftIcon": "", "repeatEnd": 1}
        self._emit(self.on_message, "礼物", self._fmt(nick, m["content"]), m)

    def _on_interact(self, msg, now_ms, v2=False):
        data = msg.get("data") or {}
        nick = (data.get("followers_info") or {}).get("uname") or data.get("uname") or ""
        try:
            mt = int(data.get("msg_type") or 1)
        except (TypeError, ValueError):
            mt = 1
        if mt == 2:
            m = {"type": "INTERACT_WORD_FOLLOW", "ts": now_ms, "nickname": nick}
            self._emit(self.on_message, "关注", self._fmt(nick, "关注了主播"), m)
        elif mt == 1:
            m = {"type": "INTERACT_WORD_V2" if v2 else "INTERACT_WORD",
                 "ts": now_ms, "nickname": nick}
            self._emit(self.on_message, "进场", self._fmt(nick, "进入直播间"), m)
        # mt == 3 分享：静默

    def _on_entry_effect(self, msg, now_ms):
        data = msg.get("data") or {}
        nick = data.get("uname") or ""
        copy = re.sub(r"<[^>]+>", "", data.get("copy_writing") or "")
        if not nick and not copy:
            return
        m = {"type": "ENTRY_EFFECT", "ts": now_ms, "nickname": nick}
        self._emit(self.on_message, "进场", self._fmt(nick, copy or "进入直播间"), m)


# 弹幕姬页统一抓取器入口（douyin_live.LiveFetcher 同名同构）
LiveFetcher = BiliLiveFetcher


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
