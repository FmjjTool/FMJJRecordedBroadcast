# -*- coding: utf-8 -*-
"""抖音直播真实数据抓取器（供 GUI 调用的后台模块）。

技术链路（已验证打通）：
  1. room/enter 接口（web_rid 短号换 roomId）+ 主播信息
  2. im/fetch 接口（带 ttwid cookie）→ cursor / internalExt / pushServer
  3. Playwright 真实浏览器（真实指纹 + 真实 mssdk.js）→ frontierSign 生成有效签名
  4. WebSocket 连接 → 完整协议（ack + 心跳 hb）保持连接
  5. 解析 PushFrame → Response → Webcast* 消息（弹幕/礼物/进场/点赞/关注）

用法：
  fetcher = DouyinLiveFetcher(room_num, on_message=..., on_room_info=..., on_status=...)
  fetcher.start()
  # 收到数据时回调触发；fetcher.stop() 停止
"""

import time
import threading
import hashlib

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
SDK_VERSION = "1.0.14-beta.0"
DEFAULT_UNIQUE_ID = "7673531686768002601"

# 已知房间号 → roomId（稳定标识，room/enter 被风控时兜底）
KNOWN_ROOM_IDS = {
    "6096197105": "7673693264163261218",
    "310114857513": "7673480294665669411",
}

# Webcast 消息类型 → (中文名, 分类)
# 分类：chat 弹幕 / gift 礼物 / member 进场 / like 点赞 / social 关注 / other 日志
MSG_TYPES = {
    "WebcastChatMessage": ("弹幕", "chat"),
    "WebcastGiftMessage": ("礼物", "gift"),
    "WebcastMemberMessage": ("进场", "member"),
    "WebcastLikeMessage": ("点赞", "like"),
    "WebcastSocialMessage": ("关注", "social"),
    "WebcastEmojiChatMessage": ("表情弹幕", "chat"),
    "WebcastRoomMessage": ("系统消息", "other"),
    "WebcastRoomIntroMessage": ("房间介绍", "other"),
    "WebcastRoomUserSeqMessage": ("在线人数", "other"),
    "WebcastInRoomBannerMessage": ("横幅", "other"),
    "WebcastRoomStatsMessage": ("直播数据", "other"),
    "WebcastRoomDataSyncMessage": ("数据同步", "other"),
    "WebcastRanklistHourEntranceMessage": ("小时榜", "other"),
    "WebcastFansclubMessage": ("粉丝团", "other"),
    "WebcastRoomRankMessage": ("排行榜", "other"),
}


def stub_md5(room_id, unique_id):
    stub = (
        f"live_id=1,aid=6383,version_code=180800,webcast_sdk_version={SDK_VERSION},"
        f"room_id={room_id},sub_room_id=,sub_channel_id=,did_rule=3,"
        f"user_unique_id={unique_id},device_platform=web,device_type=,ac=,identity=audience"
    )
    return hashlib.md5(stub.encode()).hexdigest()


def fetch_room_info(room_num):
    """通过 room/enter 接口拿房间信息 + 主播信息。"""
    import urllib.parse as up
    info = {"roomNum": room_num, "uniqueId": DEFAULT_UNIQUE_ID}
    cache = _load_room_cache()
    cached = cache.get(room_num, {})
    try:
        with httpx.Client(headers={"User-Agent": UA, "Referer": "https://live.douyin.com/"}, follow_redirects=True) as c:
            c.get(f"https://live.douyin.com/{room_num}", timeout=15)
            cookie_str = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
            # 合并 Cookie 管理器的抖音登录 Cookie（匿名 cookie 拿不到原画等高画质流）
            login_cookie = _get_douyin_cookie()
            if login_cookie:
                have = {p.split("=", 1)[0].strip()
                        for p in cookie_str.split(";") if "=" in p}
                extra = [p.strip() for p in login_cookie.split(";")
                         if "=" in p and p.split("=", 1)[0].strip() not in have]
                if extra:
                    cookie_str = "; ".join([cookie_str] + extra)
            params = {
                "aid": "6383", "app_name": "douyin_web", "live_id": "1",
                "device_platform": "web", "language": "zh-CN", "browser_language": "zh-CN",
                "browser_platform": "Win32", "browser_name": "Mozilla",
                "browser_version": UA, "web_rid": room_num,
            }
            url = "https://live.douyin.com/webcast/room/web/enter/?" + up.urlencode(params)
            r = c.get(url, headers={"User-Agent": UA, "Referer": f"https://live.douyin.com/{room_num}", "Cookie": cookie_str}, timeout=15)
            if r.status_code == 200 and r.text.strip():
                d = r.json()
                room = (d.get("data", {}).get("data") or [{}])[0]
                if room.get("id_str"):
                    info["roomId"] = room["id_str"]
                owner = room.get("owner") or {}
                if owner.get("nickname"):
                    info["anchor"] = owner["nickname"]
                if room.get("title"):
                    info["title"] = room["title"]
                if room.get("status") is not None:
                    info["status"] = int(room["status"])
                stats = room.get("stats") or {}
                if stats.get("user_count_str"):
                    info["online"] = stats["user_count_str"]
                # 封面 / 主播头像（弹幕姬左栏展示；web 端字段为 cover，兼容 cover_url）
                cover = room.get("cover") or room.get("cover_url") or {}
                if isinstance(cover, dict):
                    cover = cover.get("url_list") or [""]
                cover_url = cover[0] if isinstance(cover, (list, tuple)) and cover else (cover or "")
                if cover_url:
                    info["cover"] = cover_url
                avatar = owner.get("avatar_thumb") or {}
                if isinstance(avatar, dict):
                    av_list = avatar.get("url_list") or []
                    if av_list:
                        info["avatar"] = av_list[0]
                # 粉丝数 / 累计观看（字段名兼容多种）
                fans = _pick(owner, "follower_count", "followers") or _pick(stats, "follower_count")
                if fans is not None:
                    info["fans"] = _fmt_count(fans)
                total = _pick(stats, "total_user", "total_user_str", "total_user_count")
                if total is not None:
                    info["total_viewers"] = _fmt_count(total)
                like = _pick(stats, "like_count", "like_count_str")
                if like is not None:
                    info["like_count"] = _fmt_count(like)
                # 直播流（清晰度 → FLV 拉流地址），供预览播放 / 录制
                stream_url = room.get("stream_url") or {}
                flv = stream_url.get("flv_pull_url") or {}
                if isinstance(flv, dict) and flv:
                    info["streams"] = dict(flv)
                    # 原画兜底：flv_pull_url 没有 _origin 时，从 pull_data.stream_data 解析
                    if "_origin" not in info["streams"]:
                        origin_url = _extract_origin_stream(stream_url)
                        if origin_url:
                            info["streams"] = {"_origin": origin_url, **info["streams"]}
                    if stream_url.get("hls_pull_url"):
                        info["hls"] = stream_url["hls_pull_url"]
                # 更新缓存（roomId 是稳定标识，缓存起来供风控时兜底）
                if info.get("roomId"):
                    cache[room_num] = {
                        "roomId": info["roomId"],
                        "anchor": info.get("anchor", ""),
                        "title": info.get("title", ""),
                    }
                    _save_room_cache(cache)
            else:
                info["error"] = f"room/enter 接口返回异常（可能被风控），状态 {r.status_code}"
    except Exception as e:
        info["error"] = str(e)
    # 缓存兜底：room/enter 失败（被风控）时，用缓存的 roomId 继续连接
    if not info.get("roomId") and cached.get("roomId"):
        info["roomId"] = cached["roomId"]
        info["anchor"] = cached.get("anchor", "")
        info["title"] = cached.get("title", "")
        info["from_cache"] = True
    # 内置映射兜底：缓存也没有时，用已知 roomId
    if not info.get("roomId") and room_num in KNOWN_ROOM_IDS:
        info["roomId"] = KNOWN_ROOM_IDS[room_num]
        info["from_cache"] = True
    return info


def _room_cache_path():
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir() / "douyin_live_rooms.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "config" / "douyin_live_rooms.json"


def _load_room_cache():
    try:
        import json
        p = _room_cache_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_room_cache(cache):
    try:
        import json
        p = _room_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _pick(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def _fmt_count(v):
    if isinstance(v, (int, float)):
        return f"{int(v):,}"
    return str(v)


# ==================== 设置存储 ====================
DEFAULT_SETTINGS = {
    "monitor_interval": 30,     # 弹幕监控间隔（秒）
    "stats_interval": 1,        # 统计数据采集间隔（秒）
    "room_list": "",            # 监控房间列表（每行一个）
    "save_danmaku": False,      # 保存弹幕到文件
    "save_stats": False,        # 保存统计数据
    "keep_scroll": True,        # 保持滚动（强制最新）
    "dark_mode": True,          # 深色模式
}


def _settings_path():
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir() / "douyin_live.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "config" / "douyin_live.json"


def load_settings():
    p = _settings_path()
    if p.exists():
        try:
            import json
            return {**DEFAULT_SETTINGS, **json.loads(p.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def _login_state_path():
    """抖音登录态（playwright storage_state）文件路径。"""
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir() / "douyin_login_state.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "config" / "douyin_login_state.json"


def _get_douyin_cookie():
    """复用主程序的抖音 Cookie（config/cookies.json，由 Cookie 管理登录保存）。"""
    try:
        from auth import get_cookie
        return get_cookie("douyin")
    except Exception:
        return ""


def _extract_origin_stream(stream_url):
    """从 stream_url.pull_data.stream_data 里解析原画 FLV 地址（登录态下才有）。"""
    import json as _json
    pull = stream_url.get("pull_data") or {}
    raw = pull.get("stream_data")
    if not raw:
        return None
    try:
        data = _json.loads(raw) if isinstance(raw, str) else raw
        origin = (data or {}).get("origin") or {}
        main = origin.get("main") or {}
        flv = main.get("flv") or ""
        return flv or None
    except Exception:
        return None


def _parse_cookie_str(cookie_str, domain):
    """把 'name1=v1; name2=v2' 字符串解析为 playwright add_cookies 格式。"""
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            name = name.strip()
            if name:
                cookies.append({"name": name, "value": value.strip(), "domain": domain, "path": "/"})
    return cookies


def save_settings(s):
    try:
        import json
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def check_live_status(room_num):
    """检测直播间是否在直播。返回 (是否直播中, info)。"""
    info = fetch_room_info(room_num)
    return info.get("status") == 2, info


def _danmaku_dir():
    """弹幕导出目录：统一导出路径/弹幕文件/。"""
    try:
        from toolbox.config_manager import get_export_subdir
        return get_export_subdir("danmaku")
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "download" / "弹幕文件"


def _stats_dir():
    """统计数据导出目录：与弹幕同目录（统一导出路径/弹幕文件/）。"""
    return _danmaku_dir()


# ==================== sessionid 凭证加密存储（存 src 内） ====================
def _session_key_path():
    from pathlib import Path
    return Path(__file__).resolve().parent / ".danmaku.key"


def _session_dat_path():
    from pathlib import Path
    return Path(__file__).resolve().parent / ".danmaku_session.dat"


def _get_cipher():
    from cryptography.fernet import Fernet
    kp = _session_key_path()
    if kp.exists():
        key = kp.read_bytes()
    else:
        key = Fernet.generate_key()
        kp.write_bytes(key)
    return Fernet(key)


def save_sessionid(sessionid):
    """加密保存 sessionid 到 src/toolbox/.danmaku_session.dat。"""
    try:
        cipher = _get_cipher()
        _session_dat_path().write_bytes(cipher.encrypt(sessionid.encode("utf-8")))
        return True
    except Exception:
        return False


def load_sessionid():
    """解密读取已保存的 sessionid，无或失败返回空串。"""
    p = _session_dat_path()
    if not p.exists():
        return ""
    try:
        cipher = _get_cipher()
        return cipher.decrypt(p.read_bytes()).decode("utf-8")
    except Exception:
        return ""


def append_stats_file(room_num, stats_line):
    """把一条统计数据追加写入文件（download/stats/房间号_日期.csv）。"""
    try:
        import datetime
        d = _stats_dir()
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{room_num}_{datetime.date.today().strftime('%Y%m%d')}.csv"
        path = d / fname
        if not path.exists():
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("时间,在线人数,点赞数,粉丝数,累计观看\n")
        with open(path, "a", encoding="utf-8-sig") as f:
            f.write(stats_line + "\n")
        return str(path)
    except Exception:
        return ""


class StatsCollector:
    """统计数据采集器：定时调用 room/enter 拿最新 stats，通过回调推送。"""

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
                            self.on_change(is_live, info)
                        except Exception:
                            pass
            except Exception:
                pass
            self._stop.wait(self.interval)


# ============ 浏览器端 JS（完整协议 + 持续监听） ============
_START_JS = r"""
async (args) => {
  const roomId = args.roomId, uniqueId = args.uniqueId, md5hex = args.md5hex;
  const out = {};
  const UA = navigator.userAgent;
  window.__queue = [];

  function encVarint(n) { n=BigInt(n); const a=[]; while(n>0x7fn){a.push(Number(n&0x7fn)|0x80);n>>=7n;} a.push(Number(n)); return a; }
  function encPushFrame(o) {
    const parts=[];
    if(o.payloadType){const s=new TextEncoder().encode(o.payloadType);parts.push(0x3A,s.length,...s);}
    if(o.payload){parts.push(0x42,o.payload.length,...o.payload);}
    if(o.logId){parts.push(0x10,...encVarint(o.logId));}
    return new Uint8Array(parts);
  }
  const heartbeat = () => encPushFrame({payloadType:'hb'});
  const ackMsg = (ext,logId) => encPushFrame({payloadType:'ack', payload:new TextEncoder().encode(ext||''), logId});

  function makeReader(b){let p=0;return{varint(){let r=0n,s=0n;while(true){const x=b[p++];r|=BigInt(x&0x7f)<<s;if(!(x&0x80))break;s+=7n;}return r;},str(l){const s=new TextDecoder().decode(b.slice(p,p+l));p+=l;return s;},take(l){const s=b.slice(p,p+l);p+=l;return s;},skip(n){p+=n;},get pos(){return p;}};}
  function decodePushFrame(b){const r=makeReader(b);let logId=null,payloadType=null,payload=null;const headers={};
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===0){const v=r.varint();if(f===2)logId=v;}
      else if(w===2){const l=Number(r.varint());
        if(f===5){const e=r.pos+l;let key=null,val=null;while(r.pos<e){const t2=Number(r.varint());const f2=t2>>3;const l2=Number(r.varint());if(f2===1)key=r.str(l2);else if(f2===2)val=r.str(l2);else r.skip(l2);}if(key)headers[key]=val;}
        else if(f===7)payloadType=r.str(l);else if(f===8){payload=b.slice(r.pos,r.pos+l);r.skip(l);}else r.skip(l);}
      else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return{logId,payloadType,payload,headers};}
  function decodeResponse(b){const r=makeReader(b);let needAck=false,cursor=null,internalExt=null;const messages=[];
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===0){const v=r.varint();if(f===9)needAck=(v===1n);}
      else if(w===2){const l=Number(r.varint());if(f===1){messages.push(b.slice(r.pos,r.pos+l));r.skip(l);}else if(f===2)cursor=r.str(l);else if(f===5)internalExt=r.str(l);else r.skip(l);}
      else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return{needAck,cursor,internalExt,messages};}
  function parseUser(b){ // 解析 User，返回 nickname（field 3）
    const r=makeReader(b);
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r.varint());if(f===3){const s=r.str(l);return s;}r.skip(l);}
      else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return null;
  }
  function parseImageUrl(b){ // 解析 Image，返回 urlList[0]（field 1）
    const r=makeReader(b);
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r.varint());if(f===1){const s=r.str(l);return s;}r.skip(l);}
      else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return null;
  }
  function parseGift(b){ // 解析 GiftStruct：name(16)/diamondCount(9)/image(1→Image.urlList[0])
    const r=makeReader(b);let name=null,price=null,icon=null;
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===0){const v=r.varint();if(f===9)price=Number(v);}
      else if(w===2){const l=Number(r.varint());
        if(f===16)name=r.str(l);
        else if(f===1)icon=parseImageUrl(r.take(l));
        else r.skip(l);}
      else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return{name,price,icon};
  }
  function parsePieces(b){ // 解析 Text.pieces(4)：[{type:1 text stringValue=11} | {type:2 emoji imageValue=25→image=1}]
    const r=makeReader(b);const out=[];
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r.varint());const d=r.take(l);
        if(f===4){const pr=makeReader(d);let ptype=null,str=null,img=null;
          while(pr.pos<d.length){const t2=Number(pr.varint());const f2=t2>>3,w2=t2&7;
            if(w2===0){const v=pr.varint();if(f2===1)ptype=Number(v);}
            else if(w2===2){const l2=Number(pr.varint());
              if(f2===11)str=pr.str(l2);
              else if(f2===25){const imgb=pr.take(l2);const ir=makeReader(imgb);
                while(ir.pos<imgb.length){const t3=Number(ir.varint());const f3=t3>>3,w3=t3&7;
                  if(w3===2){const l3=Number(ir.varint());if(f3===1)img=ir.str(l3);else ir.skip(l3);}
                  else if(w3===0)ir.varint();else if(w3===1)ir.skip(8);else if(w3===5)ir.skip(4);else break;}}
              else pr.skip(l2);}
            else if(w2===1)pr.skip(8);else if(w2===5)pr.skip(4);else break;}
          if(img)out.push({type:2,url:img,text:str||''});
          else if(str!=null)out.push({type:1,text:str});}}
      else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return out;
  }
  function parseText(b){ // 解析 Text，返回 defaultPattern（field 2，礼物显示名）
    const r=makeReader(b);
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r.varint());if(f===2){const s=r.str(l);return s;}r.skip(l);}
      else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return null;
  }
  function parseTrayInfo(b){ // 解析 GiftTrayInfo，返回 trayDisplayText.defaultPattern（field 1 → Text field 2）
    const r=makeReader(b);
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r.varint());const d=b.slice(r.pos,r.pos+l);r.skip(l);
        if(f===1){const s=parseText(d);if(s)return s;}}
      else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return null;
  }
  function extractMsg(m){
    // 1. 解析 Message 外层：field 1 = method（类型名），field 2 = payload（真正消息体）
    const r0=makeReader(m);
    let method=null,payload=null;
    while(r0.pos<m.length){const tag=Number(r0.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r0.varint());const d=m.slice(r0.pos,r0.pos+l);r0.skip(l);
        if(f===1)method=new TextDecoder('utf-8',{fatal:false}).decode(d);else if(f===2)payload=d;}
      else if(w===0)r0.varint();else if(w===1)r0.skip(8);else if(w===5)r0.skip(4);else break;}
    if(!payload)payload=m; // 兜底：无 wrapper 时整个就是消息体
    const type=method||'';

    // 2. 解析 payload（各消息类型 user 字段号不同：Chat/Social/Member=2, Gift=7, Like=5）
    // 仅对已知用户消息类型解析昵称，其它类型 field 2 非 User 结构（解析出乱码）
    const userFieldMap={WebcastGiftMessage:7,WebcastLikeMessage:5,WebcastChatMessage:2,WebcastMemberMessage:2,WebcastSocialMessage:2,WebcastEmojiChatMessage:2};
    const userField=userFieldMap[type];
    const r=makeReader(payload);
    let nickname=null,content=null,giftName=null,giftPrice=null,giftIcon=null,comboCount=null,repeatCount=null,action=null,trayText=null;
    let repeatEnd=null,giftCount=null,likeTotal=null,likeCount=null,followCount=null,roomTotal=null,roomTotalUser=null,memberCount=null,rtf=null;
    while(r.pos<payload.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===0){const v=r.varint();
        if(f===5){if(type==='WebcastGiftMessage')repeatCount=v;}
        else if(f===6){if(type==='WebcastGiftMessage')comboCount=v;else if(type==='WebcastSocialMessage')followCount=v;}
        else if(f===2){if(type==='WebcastGiftMessage')giftCount=v;else if(type==='WebcastLikeMessage')likeCount=v;}
        else if(f===3){if(type==='WebcastLikeMessage')likeTotal=v;else if(type==='WebcastRoomUserSeqMessage')roomTotal=v;else if(type==='WebcastMemberMessage')memberCount=v;}
        else if(f===7){if(type==='WebcastRoomUserSeqMessage')roomTotalUser=v;}
        else if(f===9){if(type==='WebcastGiftMessage')repeatEnd=v;}}
      else if(w===2){const l=Number(r.varint());const d=payload.slice(r.pos,r.pos+l);r.skip(l);
        if(f===userField){nickname=parseUser(d);}
        else if(f===3&&type!=='WebcastLikeMessage'&&type!=='WebcastRoomUserSeqMessage'){content=new TextDecoder('utf-8',{fatal:false}).decode(d);}
        else if(f===8&&type==='WebcastRoomUserSeqMessage'){roomTotalUser=new TextDecoder('utf-8',{fatal:false}).decode(d);}
        else if(f===15&&type==='WebcastGiftMessage'){const g=parseGift(d);if(g.name)giftName=g.name;if(g.price!=null)giftPrice=g.price;if(g.icon)giftIcon=g.icon;}
        else if(f===11){action=new TextDecoder('utf-8',{fatal:false}).decode(d);}
        else if(f===19){trayText=parseText(d);}
        else if(f===21){const t=parseTrayInfo(d);if(t)trayText=t;}
        else if(f===41&&type==='WebcastChatMessage'){rtf=parsePieces(d);}
        else if(f===4&&type==='WebcastEmojiChatMessage'){rtf=parsePieces(d);}}
      else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    // 3. 根据类型构建内容
    let outContent='';
    if(type==='WebcastGiftMessage'){
      const cnt=comboCount!=null?comboCount:(repeatCount!=null?repeatCount:1);
      const gname=giftName||trayText||'礼物';
      outContent='送出了'+gname+' x'+cnt;
    }else if(type==='WebcastMemberMessage'){
      outContent=action||'进入直播间';
    }else if(type==='WebcastLikeMessage'){
      outContent='为主播点赞了('+(likeCount!=null?likeCount:1)+')';
    }else if(type==='WebcastSocialMessage'){
      outContent='关注了主播';
    }else if(type==='WebcastChatMessage'||type==='WebcastEmojiChatMessage'){
      outContent=content||'';
    }else if(type==='WebcastRoomStatsMessage'){
      // 房间实时统计：displayMiddle(field 3) 已进 content，如 "1.2万"
      outContent=content||'';
    }else{
      // 系统消息等：提取有意义的中文短语，过滤 JSON 噪音 + 截断
      const txt=new TextDecoder('utf-8',{fatal:false}).decode(payload);
      const noise=/荣誉等级|粉丝团等级|级勋章|勋章|人气开场|重庆|中部|新星|万粉团|多阶段任务|tier_icon|default_selector|is_current|desc|icon_src|selector|直播已结束|http|douyinpic|\.com|\.png|\.jpg|\.webp|\.mp4|\.jpeg|\.gif|current|target|status|already|light_current|a_type_roof_text|pro_conf|reward_list|小时榜|挑战榜|人气榜/;
      // 提取中文短语，去掉尾部的 JSON 分隔符（引号/逗号/冒号/反斜杠）
      const m2=txt.match(/[\u4e00-\u9fff][\u4e00-\u9fff\w\s]{3,60}/g)||[];
      const parts=m2.map(t=>t.replace(/[\",:;\\]+$/g,'').trim())
                     .filter(t=>t.length>=4&&!noise.test(t));
      outContent=parts[0]||'';
      if(outContent.length>40)outContent=outContent.slice(0,40)+'…';
    }
    return{type,nickname,content:outContent,
      giftName:giftName,giftPrice:giftPrice,giftIcon:giftIcon,repeatEnd:repeatEnd,
      giftCount:(repeatCount!=null?repeatCount:(comboCount!=null?comboCount:1)),
      likeTotal:likeTotal,likeCount:likeCount,followCount:followCount,
      roomTotal:roomTotal,roomTotalUser:roomTotalUser,memberCount:memberCount,
      rtf:(rtf&&rtf.length?rtf:null)};
  }

  out.signature = window.byted_acrawler.frontierSign({'X-MS-STUB':md5hex})['X-Bogus'];

  const msToken = Array.from({length:184},()=> 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'[Math.floor(Math.random()*64)]).join('');
  const params = new URLSearchParams({aid:'6383',app_name:'douyin_web',browser_language:'zh-CN',browser_name:'Mozilla',browser_online:'true',browser_platform:'Win32',browser_version:UA,cookie_enabled:'true',cursor:'',device_id:'',device_platform:'web',did_rule:'3',endpoint:'live_pc',fetch_rule:'1',identity:'audience',insert_task_id:'',internal_ext:'',last_rtt:'0',live_id:'1',live_reason:'',need_persist_msg_count:'15',resp_content_type:'protobuf',screen_height:screen.height,screen_width:screen.width,support_wrds:'1',tz_name:'Asia/Shanghai',version_code:'180800',msToken:msToken,room_id:roomId,user_unique_id:uniqueId,live_pc:roomId,a_bogus:'00000000'});
  let cursor=null,internalExt=null,pushServer=null;
  try{
    const resp=await fetch('/webcast/im/fetch/?'+params.toString(),{credentials:'include'});
    const buf=new Uint8Array(await resp.arrayBuffer());
    const res=decodeResponse(buf);
    cursor=res.cursor;internalExt=res.internalExt;
    const r2=makeReader(buf);
    while(r2.pos<buf.length){const tag=Number(r2.varint());const f=tag>>3,w=tag&7;
      if(w===2){const l=Number(r2.varint());if(f===10)pushServer=r2.str(l);else r2.skip(l);}
      else if(w===0)r2.varint();else if(w===1)r2.skip(8);else if(w===5)r2.skip(4);else break;}
  }catch(e){out.error='imfetch:'+e.message;}

  if(!cursor||!internalExt||!pushServer||!out.signature){out.result='missing';return out;}

  const wsParams=new URLSearchParams({aid:'6383',app_name:'douyin_web',browser_language:'zh-CN',browser_name:'Mozilla',browser_online:'true',browser_platform:'Win32',browser_version:UA,compress:'gzip',cookie_enabled:'true',device_platform:'web',did_rule:'3',endpoint:'live_pc',heartbeatDuration:'0',host:'https://live.douyin.com',identity:'audience',insert_task_id:'',live_id:'1',live_reason:'',need_persist_msg_count:'15',screen_height:screen.height,screen_width:screen.width,support_wrds:'1',tz_name:'Asia/Shanghai',update_version_code:'1.0.14-beta.0',version_code:'180800',webcast_sdk_version:'1.0.14-beta.0',room_id:roomId,user_unique_id:uniqueId,cursor:cursor,internal_ext:internalExt,signature:out.signature});
  const wsUrl=pushServer+'?'+wsParams.toString();

  window.__wsClosed = false;
  try {
    const ws = new WebSocket(wsUrl);
    window.__ws = ws;
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { out.wsOpen = true; try{ws.send(heartbeat());}catch(e){} };
    ws.onmessage = async (ev) => {
      const bytes = new Uint8Array(ev.data);
      let frame; try { frame = decodePushFrame(bytes); } catch(e){ return; }
      let payload = frame.payload;
      if (frame.headers['compress_type']==='gzip' && payload) {
        try { const ds=new DecompressionStream('gzip'); const st=new Blob([payload]).stream().pipeThrough(ds); payload=new Uint8Array(await new Response(st).arrayBuffer()); } catch(e){}
      }
      let needAck=false, ext=frame.headers['im-internal_ext']||'';
      if (payload) {
        try {
          const res = decodeResponse(payload);
          if (res.needAck) needAck=true;
          if (res.internalExt) ext=res.internalExt;
          for (const m of res.messages) {
            const e = extractMsg(m);
            if (e.type) {
              window.__queue.push(e);
            }
          }
        } catch(e){}
      }
      if (needAck) { try { ws.send(ackMsg(ext, frame.logId)); } catch(e){} }
    };
    ws.onclose = (e) => { window.__wsClosed = true; };
    window.__hbTimer = setInterval(() => { if (ws.readyState===1) { try{ws.send(heartbeat());}catch(e){} } }, 10000);
  } catch(e) {
    out.error = 'ws:'+e.message;
  }
  return out;
}
"""

_READ_QUEUE_JS = "() => { const q = window.__queue || []; window.__queue = []; return q; }"
_WS_CLOSED_JS = "() => !!window.__wsClosed"


class DouyinLiveFetcher:
    """抖音直播真实数据抓取器（后台线程）。"""

    def __init__(self, room_num, on_message=None, on_room_info=None, on_status=None):
        """
        on_message(name, text): 收到一条消息。name 为中文类型（弹幕/礼物/进场/点赞/关注...），text 为内容。
        on_room_info(info): 房间信息 dict（anchor/title/online/status...）。
        on_status(status, msg): 状态变化（connecting/connected/disconnected/error）。
        """
        self.room_num = room_num
        self.on_message = on_message
        self.on_room_info = on_room_info
        self.on_status = on_status
        self._stop = threading.Event()
        self._captcha_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._captcha_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._captcha_event.set()

    def continue_captcha(self):
        """用户在浏览器里过完验证码后调用，让连接流程继续。"""
        self._captcha_event.set()

    def _emit(self, cb, *args):
        if cb:
            try:
                cb(*args)
            except Exception:
                pass

    @staticmethod
    def _has_acrawler(page):
        """检测 mssdk.js（byted_acrawler）是否加载成功（加载失败=被风控）。"""
        try:
            return bool(page.evaluate("() => typeof window.byted_acrawler !== 'undefined'"))
        except Exception:
            return False

    @staticmethod
    def _format_line(name, nickname, content):
        """构建 '[昵称]：内容' 格式字符串（不含时间戳，时间戳由 UI 端加）。

        nickname 为 protobuf 精确解析出的用户昵称，content 为消息内容。
        """
        nickname = (nickname or "").strip()
        content = (content or "").strip()
        if name == "进场":
            return f"[{nickname}]：{content or '进入直播间'}" if nickname else (content or "进入直播间")
        if name == "关注":
            return f"[{nickname}]：关注了主播" if nickname else "关注了主播"
        if name == "点赞":
            # content 已含次数：为主播点赞了(N)
            if nickname:
                return f"[{nickname}]：{content or '为主播点赞了'}"
            return content or "为主播点赞了"
        # 弹幕 / 礼物 / 表情弹幕 / 其他
        if nickname and content:
            return f"[{nickname}]：{content}"
        if nickname:
            return f"[{nickname}]："
        return content

    def _run(self):
        try:
            self._emit(self.on_status, "connecting", "正在获取房间信息...")
            info = fetch_room_info(self.room_num)
            self._emit(self.on_room_info, info)
            if not info.get("roomId"):
                self._emit(self.on_status, "error", info.get("error") or "未获取到房间信息（直播间可能不存在或已结束）")
                return

            md5hex = stub_md5(info["roomId"], info["uniqueId"])
            self._emit(self.on_status, "connecting", "正在建立弹幕连接（真实浏览器）...")

            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # 登录态：若已保存，加载它以绕过风控
                ls = _login_state_path()
                ctx_kwargs = {"user_agent": UA, "viewport": {"width": 1920, "height": 1080}}
                if ls.exists():
                    ctx_kwargs["storage_state"] = str(ls)
                # 复用主程序 Cookie 管理里保存的抖音 Cookie（优先于登录态文件）
                douyin_cookie = _get_douyin_cookie()

                def _inject_cookie(ctx):
                    if douyin_cookie:
                        try:
                            ctx.add_cookies(_parse_cookie_str(douyin_cookie, ".douyin.com"))
                        except Exception:
                            pass

                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(**ctx_kwargs)
                _inject_cookie(ctx)
                page = ctx.new_page()
                page.goto(f"https://live.douyin.com/{self.room_num}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)

                # 检测风控：byted_acrawler（mssdk.js）未加载 → 触发验证码
                if not self._has_acrawler(page):
                    browser.close()
                    self._emit(self.on_status, "captcha", "检测到抖音风控，请在弹出的浏览器窗口手动完成验证码...")
                    # 切换有头浏览器，弹出验证窗口，等待用户手动过验证码
                    browser = p.chromium.launch(headless=False)
                    ctx = browser.new_context(**ctx_kwargs)
                    _inject_cookie(ctx)
                    page = ctx.new_page()
                    try:
                        page.goto(f"https://live.douyin.com/{self.room_num}", wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    # 等待用户手动过验证码（GUI 弹「继续」按钮，点击后 continue_captcha）
                    if not self._captcha_event.wait(timeout=600):
                        self._emit(self.on_status, "error", "验证超时，请稍后重试")
                        browser.close()
                        return
                    # 用户点了继续：保存 cookie 状态（下次免验证），继续连接
                    try:
                        ctx.storage_state(path=str(ls))
                    except Exception:
                        pass
                    self._emit(self.on_status, "connecting", "验证通过，继续连接...")

                result = page.evaluate(_START_JS, {"roomId": info["roomId"], "uniqueId": info["uniqueId"], "md5hex": md5hex})
                if result.get("error"):
                    self._emit(self.on_status, "error", f"连接失败: {result['error']}")
                    browser.close()
                    return
                if result.get("result") == "missing":
                    self._emit(self.on_status, "error", "连接参数不完整（可能被抖音风控拦截）")
                    browser.close()
                    return

                self._emit(self.on_status, "connected", f"已连接 · 房间 {self.room_num}")
                while not self._stop.is_set():
                    time.sleep(1)
                    try:
                        msgs = page.evaluate(_READ_QUEUE_JS)
                    except Exception:
                        break
                    now_ms = int(time.time() * 1000)
                    for m in msgs:
                        m["ts"] = now_ms
                        mtype = m.get("type", "")
                        name, _cat = MSG_TYPES.get(mtype, (mtype, "other"))
                        nickname = m.get("nickname", "")
                        content = m.get("content", "")
                        line = self._format_line(name, nickname, content)
                        if line:
                            self._emit(self.on_message, name, line, m)
                        else:
                            # 统计类消息可能无正文，但仍需更新 UI（在线数/点赞总数等）
                            self._emit(self.on_message, name, "", m)
                    try:
                        if page.evaluate(_WS_CLOSED_JS):
                            self._emit(self.on_status, "disconnected", "连接已断开")
                            break
                    except Exception:
                        break
                browser.close()
        except Exception as e:
            self._emit(self.on_status, "error", f"抓取异常: {e}")
        finally:
            self._emit(self.on_status, "disconnected", "已停止")
