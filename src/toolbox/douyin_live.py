# -*- coding: utf-8 -*-
"""抖音直播真实数据抓取器（供 GUI 调用的后台模块）。

技术链路（已验证打通）：
  1. room/enter 接口（web_rid 短号换 roomId）+ 主播信息
  2. im/fetch 接口（带 ttwid cookie）→ cursor / internalExt / pushServer
  3. Playwright 真实浏览器（真实指纹 + 真实 mssdk.js）→ frontierSign 生成有效签名
  4. WebSocket 连接（握手凭证 = ttwid 匿名会话 + sessionid 登录凭证：
     ttwid 过握手强制校验，sessionid 决定礼物消息是否推送）
     → 完整协议（ack + 心跳 hb）保持连接
  5. 解析 PushFrame → Response → Webcast* 消息（弹幕/礼物/进场/点赞/关注）
  6. WS 确认收到服务端推送后才关闭浏览器释放登录 Cookie（出弹幕后才断 Cookie 链接）

用法：
  fetcher = DouyinLiveFetcher(room_num, on_message=..., on_room_info=..., on_status=...)
  fetcher.start()
  # 收到数据时回调触发；fetcher.stop() 停止
"""

import time
import json
import threading
import hashlib

import httpx

from toolbox.browser import launch as pw_launch

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
    "WebcastBindingGiftMessage": ("礼物", "gift"),
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
    "WebcastHotChatMessage": ("热议", "other"),
    "WebcastLotteryDrawResultEventMessage": ("抽奖", "other"),
}


def stub_md5(room_id, unique_id):
    stub = (
        f"live_id=1,aid=6383,version_code=180800,webcast_sdk_version={SDK_VERSION},"
        f"room_id={room_id},sub_room_id=,sub_channel_id=,did_rule=3,"
        f"user_unique_id={unique_id},device_platform=web,device_type=,ac=,identity=audience"
    )
    return hashlib.md5(stub.encode()).hexdigest()


def _query_enter(info, c, web_rid, cookie_str, cache, cache_key):
    """调用 room/web/enter 解析房间信息写入 info；成功返回 True。"""
    import urllib.parse as up
    params = {
        "aid": "6383", "app_name": "douyin_web", "live_id": "1",
        "device_platform": "web", "language": "zh-CN", "browser_language": "zh-CN",
        "browser_platform": "Win32", "browser_name": "Mozilla",
        "browser_version": UA, "web_rid": web_rid,
    }
    url = "https://live.douyin.com/webcast/room/web/enter/?" + up.urlencode(params)
    r = c.get(url, headers={"User-Agent": UA, "Referer": f"https://live.douyin.com/{web_rid}", "Cookie": cookie_str}, timeout=15)
    if r.status_code != 200 or not r.text.strip():
        info["error"] = f"room/enter 接口返回异常（可能被风控），状态 {r.status_code}"
        return False
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
        # 原画/蓝光直链在 live_core_sdk_data（flv_pull_url 最高只有 1080P）
        core = _extract_core_streams(stream_url)
        info["streams"] = {**core, **flv}
        if stream_url.get("hls_pull_url"):
            info["hls"] = stream_url["hls_pull_url"]
    # 更新缓存（roomId 是稳定标识，缓存起来供风控时兜底）
    if info.get("roomId"):
        cache[cache_key] = {
            "roomId": info["roomId"],
            "anchor": info.get("anchor", ""),
            "title": info.get("title", ""),
        }
        _save_room_cache(cache)
        info.pop("error", None)
        return True
    # enter 业务失败（HTTP 200 但无房间数据）：房间不存在 / 内容不可查看（下播清除、
    # 被封禁等）——透传官方提示，避免「无错误也无房间」的静默失败；
    # 标记业务错误，让缓存兜底只在风控/网络故障时生效（内容不可看时用旧 roomId
    # 强连只会得到 WS 失败的困惑表现）
    info["error"] = (d.get("data") or {}).get("prompts") or \
        f"未获取到房间信息（直播间可能不存在或已结束，code={d.get('status_code')}）"
    info["_biz_error"] = True
    return False


_ttwid_cache = {"value": "", "ts": 0.0}   # 游客 ttwid 缓存（6 小时内复用，免每次访问首页）


def _ttwid_valid() -> bool:
    return bool(_ttwid_cache["value"]) and time.time() - _ttwid_cache["ts"] < 6 * 3600


def _merge_login_cookie(cookie_str: str, login_cookie: str) -> str:
    """把登录 Cookie 合并进会话 Cookie 串（不覆盖已有键）。"""
    if not login_cookie:
        return cookie_str
    have = {p.split("=", 1)[0].strip()
            for p in cookie_str.split(";") if "=" in p}
    extra = [p.strip() for p in login_cookie.split(";")
             if "=" in p and p.split("=", 1)[0].strip() not in have]
    return "; ".join([cookie_str] + extra) if extra else cookie_str


def fetch_room_info(room_num, login=True):
    """通过 room/enter 接口拿房间信息 + 主播信息。

    login=True 合并登录 Cookie（建连拿原画流用，一次性）；
    login=False 游客态（统计采集/开播监控等高频循环用，不占用账号）。

    输入可以是 web_rid 短号，也可以是 19 位长号（新格式 web_rid 本身就是长号）。
    """
    info = {"roomNum": room_num, "uniqueId": DEFAULT_UNIQUE_ID}
    cache = _load_room_cache()
    cached = cache.get(room_num, {})

    def _session_cookies() -> str:
        cs = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        return _merge_login_cookie(cs, login_cookie)

    try:
        with httpx.Client(headers={"User-Agent": UA, "Referer": "https://live.douyin.com/"}, follow_redirects=True) as c:
            login_cookie = _get_douyin_cookie() if login else ""
            # 游客 ttwid 缓存命中：跳过首页访问（实测省 ~1s；enter 失败再回源刷新）
            used_cache = False
            if _ttwid_valid():
                c.cookies.set("ttwid", _ttwid_cache["value"], domain=".douyin.com")
                used_cache = True
            else:
                c.get(f"https://live.douyin.com/{room_num}", timeout=15)
                ttwid = c.cookies.get("ttwid") or ""
                if ttwid:
                    _ttwid_cache["value"] = ttwid
                    _ttwid_cache["ts"] = time.time()
            ok = _query_enter(info, c, room_num, _session_cookies(), cache, room_num)
            if not ok and used_cache:
                # 缓存的 ttwid 可能已失效：回源刷新一次再试
                c.cookies.delete("ttwid", domain=".douyin.com")
                try:
                    c.get(f"https://live.douyin.com/{room_num}", timeout=15)
                    ttwid = c.cookies.get("ttwid") or ""
                    if ttwid:
                        _ttwid_cache["value"] = ttwid
                        _ttwid_cache["ts"] = time.time()
                    _query_enter(info, c, room_num, _session_cookies(), cache, room_num)
                except Exception:
                    pass
    except Exception as e:
        info["error"] = str(e)
    # 缓存兜底：room/enter 失败（被风控/网络故障）时，用缓存的 roomId 继续连接；
    # 业务错误（内容不可查看/房间不存在）不兜底
    if (not info.get("roomId") and not info.get("_biz_error")
            and cached.get("roomId")):
        info["roomId"] = cached["roomId"]
        info["anchor"] = cached.get("anchor", "")
        info["title"] = cached.get("title", "")
        info["from_cache"] = True
    # 内置映射兜底：缓存也没有时，用已知 roomId
    if (not info.get("roomId") and not info.get("_biz_error")
            and room_num in KNOWN_ROOM_IDS):
        info["roomId"] = KNOWN_ROOM_IDS[room_num]
        info["from_cache"] = True
    info.pop("_biz_error", None)
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
    "monitor_interval": 30,     # 开播监控检测间隔（秒，供「👁 开播监控」用）
    "stats_interval": 1,        # 统计数据采集间隔（秒）
    "poll_interval": 60,        # 轮询间隔（秒）
    "save_danmaku": False,      # 保存弹幕到文件
    "save_stats": True,         # 保存统计数据（默认勾选）
    "keep_scroll": True,        # 保持滚动（强制最新）
    "dark_mode": True,          # 深色模式
    "last_room": "582775732015",  # 默认房号
    "poll_enabled": True,       # 轮询开关（顶栏）
    "mode": "danmaku_only",     # 弹幕方式：默认仅弹幕
    "auto_record": False,       # 自动录播（勾选后拿到流即录）
    "auto_save_danmaku": True,  # 自动导出回放包
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
            data = json.loads(p.read_text(encoding="utf-8"))
            # 一次性迁移：旧版本默认值变更（保存统计 False→True）。
            # 老文件里的 save_stats=False 是旧默认值而非用户选择，丢弃让新默认生效；
            # 迁移后写入 _cfg_v1 标记，用户之后的手动开关正常持久化。
            if not data.get("_cfg_v1"):
                data.pop("save_stats", None)
                data["_cfg_v1"] = True
            data.pop("room_list", None)  # 旧版遗留键（无消费者），加载时清掉
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    d = dict(DEFAULT_SETTINGS)
    d["_cfg_v1"] = True
    return d


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


def _extract_core_streams(stream_url):
    """解析 live_core_sdk_data 里的高画质直链（登录态才有）。

    新版结构：stream_url.live_core_sdk_data.pull_data.stream_data 为 JSON 字符串
    {"common":..., "data":{"origin":{"main":{"flv":...}}, "md":{...}, "uhd":{...}}}，
    质量树在 "data" 键下；旧版：stream_url.pull_data.stream_data 根下直接是质量树。
    origin=原画（_or4 后缀），md=蓝光档，均高于 flv_pull_url 的 FULL_HD1(1080P)。"""
    import json as _json
    outs = {}
    candidates = []
    lcd = (stream_url.get("live_core_sdk_data") or {}).get("pull_data") or {}
    if lcd.get("stream_data"):
        candidates.append(lcd["stream_data"])
    pd = stream_url.get("pull_data") or {}
    if pd.get("stream_data"):
        candidates.append(pd["stream_data"])
    for raw in candidates:
        try:
            data = _json.loads(raw) if isinstance(raw, str) else raw
            data = (data or {}).get("data") or data  # 新版多包一层 {common, data}
            for key, name in (("_origin", "origin"), ("_md", "md")):
                main = ((data.get(name) or {}).get("main")) or {}
                flv = main.get("flv") or ""
                if flv and key not in outs:
                    outs[key] = flv
            if outs:
                break
        except Exception:
            continue
    return outs


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
    """检测直播间是否在直播（游客态，不占用 Cookie）。返回 (是否直播中, info)。"""
    info = fetch_room_info(room_num, login=False)
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


# ==================== 统计落盘 ====================
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
                info = fetch_room_info(self.room_num, login=False)
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


# ============ 浏览器端 JS（过风控 + 拿 WS 连接参数，随即关浏览器释放 Cookie） ============
_PREP_JS = r"""
async (args) => {
  const roomId = args.roomId, uniqueId = args.uniqueId, md5hex = args.md5hex;
  const out = {};
  const UA = navigator.userAgent;

  function makeReader(b){let p=0;return{varint(){let r=0n,s=0n;while(true){const x=b[p++];r|=BigInt(x&0x7f)<<s;if(!(x&0x80))break;s+=7n;}return r;},str(l){const s=new TextDecoder().decode(b.slice(p,p+l));p+=l;return s;},take(l){const s=b.slice(p,p+l);p+=l;return s;},skip(n){p+=n;},get pos(){return p;}};}
  function decodeResponse(b){const r=makeReader(b);let needAck=false,cursor=null,internalExt=null;const messages=[];
    while(r.pos<b.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;
      if(w===0){const v=r.varint();if(f===9)needAck=(v===1n);}
      else if(w===2){const l=Number(r.varint());if(f===1){messages.push(b.slice(r.pos,r.pos+l));r.skip(l);}else if(f===2)cursor=r.str(l);else if(f===5)internalExt=r.str(l);else r.skip(l);}
      else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}
    return{needAck,cursor,internalExt,messages};}

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
  out.cursor=cursor;
  out.internalExt=internalExt;
  out.pushServer=pushServer;
  out.screenHeight=screen.height;
  out.screenWidth=screen.width;
  return out;
}
"""


# ==================== Python 端 protobuf 编解码（WS 弹幕消息） ====================
# 浏览器负责过风控 + 拿连接参数（signature/cursor/internalExt/pushServer）+ 提供
# ttwid/sessionid 握手凭证；WS 确认收流后才关浏览器释放登录 Cookie。之后弹幕 WS
# 由 Python 直连，握手仅带 ttwid+sessionid（会话凭证，连接保持期不占浏览器登录态）。

import gzip as _gzip
import re as _re


class _Reader:
    """protobuf 流式读取器（对应 JS makeReader）。"""

    __slots__ = ("b", "p")

    def __init__(self, b):
        self.b = b
        self.p = 0

    def varint(self):
        r = 0
        s = 0
        while True:
            x = self.b[self.p]
            self.p += 1
            r |= (x & 0x7F) << s
            if not (x & 0x80):
                break
            s += 7
        return r

    def str_(self, l):
        v = bytes(self.b[self.p:self.p + l]).decode("utf-8", errors="replace")
        self.p += l
        return v

    def take(self, l):
        v = bytes(self.b[self.p:self.p + l])
        self.p += l
        return v

    def skip(self, n):
        self.p += n

    @property
    def pos(self):
        return self.p


def _enc_varint(n):
    a = []
    while n > 0x7F:
        a.append((n & 0x7F) | 0x80)
        n >>= 7
    a.append(n)
    return bytes(a)


def _enc_push_frame(payload_type="", payload=b"", log_id=None):
    """编码 PushFrame（心跳 hb / 应答 ack），字段号与 JS encPushFrame 一致。"""
    parts = []
    if payload_type:
        s = payload_type.encode()
        parts.append(b"\x3a" + _enc_varint(len(s)) + s)   # field 7 LEN
    if payload:
        parts.append(b"\x42" + _enc_varint(len(payload)) + payload)  # field 8 LEN
    if log_id:
        parts.append(b"\x10" + _enc_varint(log_id))       # field 2 VARINT
    return b"".join(parts)


def _heartbeat():
    return _enc_push_frame(payload_type="hb")


def _ack_msg(ext, log_id):
    return _enc_push_frame(payload_type="ack", payload=(ext or "").encode(), log_id=log_id)


def decode_push_frame(b):
    """解码 PushFrame → {logId, payloadType, payload, headers}。"""
    r = _Reader(b)
    log_id = None
    payload_type = None
    payload = None
    headers = {}
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 0:
            v = r.varint()
            if f == 2:
                log_id = v
        elif w == 2:
            l = r.varint()
            if f == 5:  # headers map<str,str>
                end = r.pos + l
                key = val = None
                while r.pos < end:
                    t2 = r.varint()
                    f2, w2 = t2 >> 3, t2 & 7
                    if w2 != 2:
                        break
                    l2 = r.varint()
                    if f2 == 1:
                        key = r.str_(l2)
                    elif f2 == 2:
                        val = r.str_(l2)
                    else:
                        r.skip(l2)
                if key is not None:
                    headers[key] = val or ""
            elif f == 7:
                payload_type = r.str_(l)
            elif f == 8:
                payload = r.take(l)
            else:
                r.skip(l)
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return {"logId": log_id, "payloadType": payload_type, "payload": payload, "headers": headers}


def decode_response(b):
    """解码 Response → {needAck, cursor, internalExt, messages}。"""
    r = _Reader(b)
    need_ack = False
    cursor = None
    internal_ext = None
    messages = []
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 0:
            v = r.varint()
            if f == 9:
                need_ack = (v == 1)
        elif w == 2:
            l = r.varint()
            if f == 1:
                messages.append(r.take(l))
            elif f == 2:
                cursor = r.str_(l)
            elif f == 5:
                internal_ext = r.str_(l)
            else:
                r.skip(l)
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return {"needAck": need_ack, "cursor": cursor, "internalExt": internal_ext, "messages": messages}


def _parse_user(b):
    """User → nickname（field 3）。"""
    r = _Reader(b)
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 2:
            l = r.varint()
            if f == 3:
                return r.str_(l)
            r.skip(l)
        elif w == 0:
            r.varint()
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return None


def _parse_gift(b):
    """GiftStruct → name(16)/diamondCount(12)/image(1)。
    字段号参考 dycast 项目 src/core/model.ts（历史抓包依据）：
    f4=duration(特效时长ms) f5=id f9=forFansclub(bool) f11=type
    f12=diamondCount(抖币价) f14=primaryEffectId f16=name。"""
    r = _Reader(b)
    name = price = icon = None
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 0:
            v = r.varint()
            if f == 12:
                price = v
        elif w == 2:
            l = r.varint()
            if f == 16:
                name = r.str_(l)
            elif f == 1:
                icon = _parse_image_url(r.take(l))
            else:
                r.skip(l)
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return {"name": name, "price": price, "icon": icon}


_IMG_URL_RE = _re.compile(r"https?://[^\s\"'\\]{8,400}")


def _scan_image_urls(b, depth=0):
    """兜底：在嵌套 protobuf 里找图片 URL（VIP 表情等非标准片段结构）。
    仅认 douyinstatic/webcastcdn 域或图片扩展名，防二进制误报。"""
    if depth > 3 or not isinstance(b, (bytes, bytearray)):
        return []
    urls = []
    try:
        r = _Reader(b)
    except Exception:
        return urls
    while r.pos < len(r.b):
        try:
            tag = r.varint()
        except Exception:
            break
        f, w = tag >> 3, tag & 7
        if w == 2:
            try:
                l = r.varint()
                d = r.take(l)
            except Exception:
                break
            if len(d) < 8:
                continue
            printable = sum(32 <= c < 127 for c in d) / len(d)
            if printable > 0.9:
                s = d.decode("utf-8", errors="replace")
                for u in _IMG_URL_RE.findall(s):
                    low = u.lower()
                    if ("webcastcdn" in low or "douyinstatic" in low
                            or low.endswith((".png", ".gif", ".webp", ".jpg", ".jpeg"))):
                        urls.append(u)
            else:
                urls.extend(_scan_image_urls(d, depth + 1))
        elif w == 0:
            try:
                r.varint()
            except Exception:
                break
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return urls


def _parse_image_url(b):
    """Image 结构 → URL。兼容两种布局：
    f1=纯 URL 字符串（ChatMessage 表情片段）；f1=嵌套消息{repeated f1=url_list
    双镜像, f2=uri, f5=颜色}（EmojiChat/VIP 表情）。返回首个 http URL 或 None。"""
    r = _Reader(b)
    while r.pos < len(b):
        try:
            tag = r.varint()
        except Exception:
            return None
        f, w = tag >> 3, tag & 7
        if w == 2:
            try:
                l = r.varint()
                d = r.take(l)
            except Exception:
                return None
            if f != 1:
                continue
            s = d.decode("utf-8", errors="replace")
            if s.startswith("http"):
                return s
            # f1 是嵌套 Image（url_list）：再进一层取 repeated f1
            r2 = _Reader(d)
            while r2.pos < len(d):
                try:
                    t2 = r2.varint()
                except Exception:
                    break
                f2, w2 = t2 >> 3, t2 & 7
                if w2 == 2:
                    try:
                        l2 = r2.varint()
                        d2 = r2.take(l2)
                    except Exception:
                        break
                    if f2 == 1:
                        s2 = d2.decode("utf-8", errors="replace")
                        if s2.startswith("http"):
                            return s2
                elif w2 == 0:
                    r2.varint()
                elif w2 == 1:
                    r2.skip(8)
                elif w2 == 5:
                    r2.skip(4)
                else:
                    break
        elif w == 0:
            try:
                r.varint()
            except Exception:
                return None
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            return None
    return None


def _parse_pieces(b):
    """Text.pieces(4)：[{type:1 text stringValue=11} | {type:2 emoji imageValue=25→image=1}]。"""
    r = _Reader(b)
    out = []
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 2:
            l = r.varint()
            d = r.take(l)
            if f == 4:
                pr = _Reader(d)
                ptype = None
                sval = None
                img = None
                while pr.pos < len(d):
                    t2 = pr.varint()
                    f2, w2 = t2 >> 3, t2 & 7
                    if w2 == 0:
                        v = pr.varint()
                        if f2 == 1:
                            ptype = v
                    elif w2 == 2:
                        l2 = pr.varint()
                        if f2 == 11:
                            sval = pr.str_(l2)
                        elif f2 == 25:
                            imgb = pr.take(l2)
                            u = _parse_image_url(imgb)
                            if u:
                                img = u
                        else:
                            pr.skip(l2)
                    elif w2 == 1:
                        pr.skip(8)
                    elif w2 == 5:
                        pr.skip(4)
                    else:
                        break
                if img:
                    out.append({"type": 2, "url": img, "text": sval or ""})
                elif sval is not None:
                    out.append({"type": 1, "text": sval})
                else:
                    # 非标准片段兜底（VIP 表情等）：扫嵌套结构里的图片 URL
                    for u in _scan_image_urls(d)[:2]:
                        out.append({"type": 2, "url": u, "text": ""})
        elif w == 0:
            r.varint()
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return out


def _parse_text(b):
    """Text → defaultPattern（field 2，礼物显示名）。"""
    r = _Reader(b)
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 2:
            l = r.varint()
            if f == 2:
                return r.str_(l)
            r.skip(l)
        elif w == 0:
            r.varint()
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return None


def _parse_tray_info(b):
    """GiftTrayInfo → trayDisplayText.defaultPattern（field 1 → Text field 2）。"""
    r = _Reader(b)
    while r.pos < len(b):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 2:
            l = r.varint()
            d = r.take(l)
            if f == 1:
                s = _parse_text(d)
                if s:
                    return s
        elif w == 0:
            r.varint()
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break
    return None


_NOISE_RE = _re.compile(
    r"荣誉等级|粉丝团等级|级勋章|勋章|人气开场|重庆|中部|新星|万粉团|多阶段任务|tier_icon|"
    r"default_selector|is_current|desc|icon_src|selector|直播已结束|http|douyinpic|\.com|\.png|"
    r"\.jpg|\.webp|\.mp4|\.jpeg|\.gif|current|target|status|already|light_current|a_type_roof_text|"
    r"pro_conf|reward_list|小时榜|挑战榜|人气榜")
_CN_PHRASE_RE = _re.compile(r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9_\s]{3,60}")
_TAIL_JUNK_RE = _re.compile(r'["\x2C:;\\]+$')

_USER_FIELD_MAP = {
    "WebcastGiftMessage": 7,
    "WebcastBindingGiftMessage": 7,
    "WebcastLikeMessage": 5,
    "WebcastChatMessage": 2,
    "WebcastMemberMessage": 2,
    "WebcastSocialMessage": 2,
    "WebcastEmojiChatMessage": 2,
}

# 按礼物处理的消息类型（GiftMessage 同构：f5/f6=数量 f7=user f15=GiftStruct）
_GIFT_TYPES = ("WebcastGiftMessage", "WebcastBindingGiftMessage")


_BANNER_TYPE_NAMES = {
    "fans": "粉丝团", "ai_gift": "AI 礼物", "tier": "周榜任务", "activity": "活动",
    "gift": "礼物", "rank": "榜单", "task": "任务", "subscribe": "订阅", "vip": "会员",
    "qixi": "七夕", "flow": "充能",
}

_BANNER_TASK_NAMES = {"gift_task": "礼物任务", "quota_task": "额度任务",
                      "anchor_gift_task": "主播礼物任务"}

_TASK_STATUS_NAMES = {0: "未开始", 1: "进行中", 2: "已完成", 3: "已结束"}


def _banner_fans_summary(node):
    """粉丝团榜横幅：p_*_hrank/drank.foot.extra.selectors=分组（如"百万粉团"）。"""
    outs = []
    for k, sub in node.items():
        if not isinstance(sub, dict):
            continue
        core = sub.get(k) or {}
        if not isinstance(core, dict):
            continue
        extra = ((core.get("foot") or {}).get("extra") or {})
        sel = [s for s in (extra.get("selectors") or []) if isinstance(s, str)]
        if sel:
            period = "日榜" if "drank" in k else "时榜"
            outs.append(f"{sel[0]}{period}")
    return outs


def _banner_flow_summary(node):
    """充能类运行横幅：banner_detail.role2views.*.banners[].basic_props（title/desc）。"""
    bd = node.get("banner_detail") or {}
    outs = []
    for rv in (bd.get("role2views") or {}).values():
        if not isinstance(rv, dict):
            continue
        for bn in rv.get("banners") or []:
            bp = (bn or {}).get("basic_props") or {}
            title = (bp.get("title") or [""])[0]
            desc = (bp.get("desc") or [""])[0]
            if title or desc:
                outs.append(" ".join(x for x in (title, desc) if x))
    return outs


def _banner_task_summary(node):
    """任务型横幅节点：遍历 *_task.task_items 提取「任务名 状态 进度」摘要。"""
    outs = []
    for k, sub in node.items():
        if not isinstance(sub, dict) or "task_items" not in sub:
            continue
        cname = sub.get("component_name") or k
        label = _BANNER_TASK_NAMES.get(cname, _BANNER_TYPE_NAMES.get(cname, cname))
        bits = []
        for item in sub.get("task_items") or []:
            st_name = _TASK_STATUS_NAMES.get(item.get("status"), "")
            conds = []
            for ph in item.get("phases") or []:
                for c in ph.get("conditions") or []:
                    tgt = c.get("target")
                    if tgt:
                        conds.append(f"{c.get('current') or 0}/{tgt}")
            if st_name:
                bits.append(f"{st_name} {' '.join(conds)}".strip())
        if bits:
            outs.append(f"{label}：{'；'.join(bits)}")
    return outs


def _parse_banner_json(raw):
    """横幅 f2 字段是完整 JSON（活动配置树）。提取人类可读的完整描述：
    活动型→标题/副标题/规则/状态；任务型→gift_task 等进度摘要。失败返回 None。"""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    outs = []
    for key, node in data.items():
        if not isinstance(node, dict):
            continue
        # key 形如 "25v_7_fans" → 取末段语义名映射为中文
        short = key.rsplit("_", 1)[-1] if "_" in key else key
        label = _BANNER_TYPE_NAMES.get(short, short)
        title = node.get("title") or node.get("name") or ""
        sub = node.get("subtitle") or node.get("desc") or node.get("description") or ""
        # 进度/状态类字段
        parts = [p for p in (title, sub) if p]
        status = node.get("status")
        if isinstance(status, dict):
            nm = status.get("Name") or status.get("name")
            if nm:
                parts.append(f"[{nm}]")
        # _extra.step 是运行状态，_extra 里常有 rule 文本
        extra = node.get("_extra") or {}
        if isinstance(extra, dict):
            rule = extra.get("rule") or extra.get("text") or ""
            if rule and len(str(rule)) < 150:
                parts.append(str(rule))
        # 任务型横幅（无 title，进度藏在 *_task.task_items.conditions）
        if not parts:
            parts = _banner_task_summary(node)
            if not parts:
                parts = _banner_fans_summary(node)
            if not parts:
                parts = _banner_flow_summary(node)
            if not parts and isinstance(extra, dict):
                nm = (extra.get("step") or {}).get("Name")
                if nm:
                    parts.append(f"[{nm}]")
        if parts:
            outs.append(f"{label}·{' | '.join(str(p) for p in parts)}")
    return "；".join(outs) if outs else None


def extract_msg(m):
    """解码单条 Message（对应 JS extractMsg），返回与旧版相同结构的 dict。"""
    r0 = _Reader(m)
    method = None
    payload = None
    while r0.pos < len(m):
        tag = r0.varint()
        f, w = tag >> 3, tag & 7
        if w == 2:
            l = r0.varint()
            d = r0.take(l)
            if f == 1:
                method = d.decode("utf-8", errors="replace")
            elif f == 2:
                payload = d
        elif w == 0:
            r0.varint()
        elif w == 1:
            r0.skip(8)
        elif w == 5:
            r0.skip(4)
        else:
            break
    if payload is None:
        payload = m
    mtype = method or ""
    if mtype == "WebcastBindingGiftMessage":
        # 实测 payload 多包一层：{f1: 真正 body, f2: Common}，剥壳后与 GiftMessage 同构
        rb = _Reader(payload)
        while rb.pos < len(payload):
            tag = rb.varint()
            f, w = tag >> 3, tag & 7
            if w == 2:
                l = rb.varint()
                d = rb.take(l)
                if f == 1 and l > 64:
                    payload = d
                    break
            elif w == 0:
                rb.varint()
            elif w == 1:
                rb.skip(8)
            elif w == 5:
                rb.skip(4)
            else:
                break

    user_field = _USER_FIELD_MAP.get(mtype)
    r = _Reader(payload)
    nickname = content = gift_name = gift_price = gift_icon = None
    combo_count = repeat_count = action = tray_text = None
    repeat_end = gift_count = like_total = like_count = follow_count = None
    room_total = room_total_user = room_total_user_str = member_count = rtf = None
    banner_json = hot_label = lottery_json = None
    while r.pos < len(payload):
        tag = r.varint()
        f, w = tag >> 3, tag & 7
        if w == 0:
            v = r.varint()
            if f == 5:
                if mtype in _GIFT_TYPES:
                    repeat_count = v
                elif mtype == "WebcastRoomStatsMessage":
                    room_total = v  # 真实在线人数（display 文本如"3万"只是格式化展示）
            elif f == 6:
                if mtype in _GIFT_TYPES:
                    combo_count = v
                elif mtype == "WebcastSocialMessage":
                    follow_count = v
            elif f == 2:
                if mtype in _GIFT_TYPES:
                    gift_count = v
                elif mtype == "WebcastLikeMessage":
                    like_count = v
            elif f == 3:
                if mtype == "WebcastLikeMessage":
                    like_total = v
                elif mtype == "WebcastRoomUserSeqMessage":
                    room_total = v  # 真实在线人数（varint，非"3万"文本）
                elif mtype == "WebcastMemberMessage":
                    member_count = v
            elif f == 7:
                if mtype == "WebcastRoomUserSeqMessage":
                    room_total_user = v  # 真实累计观看（varint）
            elif f == 9:
                if mtype in _GIFT_TYPES:
                    repeat_end = v
        elif w == 2:
            l = r.varint()
            d = r.take(l)
            if f == user_field:
                nickname = _parse_user(d)
            elif f == 3 and mtype not in ("WebcastLikeMessage", "WebcastRoomUserSeqMessage"):
                content = d.decode("utf-8", errors="replace")
            elif f == 8 and mtype == "WebcastRoomUserSeqMessage":
                room_total_user_str = d.decode("utf-8", errors="replace")  # 如"10万+"，展示用；不覆盖 f7 真实值
            elif f == 15 and mtype in _GIFT_TYPES:
                g = _parse_gift(d)
                if g["name"]:
                    gift_name = g["name"]
                if g["price"] is not None:
                    gift_price = g["price"]
                if g["icon"]:
                    gift_icon = g["icon"]
            elif f == 11:
                action = d.decode("utf-8", errors="replace")
            elif f == 2 and mtype == "WebcastInRoomBannerMessage":
                # f2 是完整活动 JSON（千字节级配置树，正则只能抓到"锋芒II"这类碎片）
                s = d.decode("utf-8", errors="replace")
                if s.lstrip()[:1] == "{":
                    banner_json = s
            elif f == 2 and mtype == "WebcastHotChatMessage":
                hot_label = d.decode("utf-8", errors="replace")  # 栏目名，如"大家说"
            elif f == 4 and mtype == "WebcastLotteryDrawResultEventMessage":
                lottery_json = d.decode("utf-8", errors="replace")  # 结果 JSON
            elif f == 5 and mtype == "WebcastEmojiChatMessage":
                content = d.decode("utf-8", errors="replace")  # "[表情]" 文字兜底
            elif f == 19:
                tray_text = _parse_text(d)
            elif f == 21:
                t = _parse_tray_info(d)
                if t:
                    tray_text = t
            elif f == 41 and mtype == "WebcastChatMessage":
                rtf = _parse_pieces(d)
            elif f == 4 and mtype == "WebcastEmojiChatMessage":
                rtf = _parse_pieces(d)
        elif w == 1:
            r.skip(8)
        elif w == 5:
            r.skip(4)
        else:
            break

    out_content = ""
    if mtype in _GIFT_TYPES:
        # repeatCount(f5) 是连击权威累计值（x7→x19→x34 每帧递增，最终帧即总数）；
        # 显示行与 giftCount 统一取值优先级，保证界面显示与打赏榜统计一致。
        # BindingGift（捆绑礼物如 66个小心心）同构：f15 GiftStruct 含真实价格
        cnt = repeat_count if repeat_count is not None else (combo_count if combo_count is not None else 1)
        gname = gift_name or tray_text or "礼物"
        if not gift_name and tray_text:
            gift_name = tray_text  # 统计与显示用同名（防显示赠送但打赏榜漏计）
        out_content = f"送出了{gname} x{cnt}"
    elif mtype == "WebcastMemberMessage":
        out_content = action or "进入直播间"
    elif mtype == "WebcastLikeMessage":
        out_content = f"为主播点赞了({like_count if like_count is not None else 1})"
    elif mtype == "WebcastSocialMessage":
        out_content = "关注了主播"
    elif mtype in ("WebcastChatMessage", "WebcastEmojiChatMessage"):
        out_content = content or ""
    elif mtype == "WebcastRoomStatsMessage":
        out_content = content or ""
    elif mtype == "WebcastHotChatMessage":
        # f2=栏目名（大家说），f3=热评内容
        out_content = f"{hot_label}：{content}" if (hot_label and content) else (content or "")
    elif mtype == "WebcastLotteryDrawResultEventMessage":
        out_content = ""
        if lottery_json:
            try:
                d = json.loads(lottery_json)
                bits = []
                for k, v in d.items():
                    kn = {"candidate_num": "候选"}.get(k, k)
                    bits.append(f"{kn} {v}")
                out_content = "抽奖结果：" + "，".join(bits)
            except Exception:
                pass
    elif mtype == "WebcastInRoomBannerMessage":
        # 优先完整 JSON 解析（活动名/规则/状态/任务进度），失败退回正则片段
        out_content = _parse_banner_json(banner_json) if banner_json else ""
        if not out_content:
            out_content = content or ""
        if len(out_content) > 200:
            out_content = out_content[:200] + "…"
    else:
        txt = payload.decode("utf-8", errors="replace")
        parts = []
        for t in _CN_PHRASE_RE.findall(txt):
            t = _TAIL_JUNK_RE.sub("", t).strip()
            if len(t) >= 4 and not _NOISE_RE.search(t):
                parts.append(t)
        out_content = parts[0] if parts else ""
        if len(out_content) > 40:
            out_content = out_content[:40] + "…"

    return {
        "type": mtype, "nickname": nickname, "content": out_content,
        "giftName": gift_name, "giftPrice": gift_price, "giftIcon": gift_icon,
        "repeatEnd": repeat_end,
        "giftCount": repeat_count if repeat_count is not None else (combo_count if combo_count is not None else 1),
        "likeTotal": like_total, "likeCount": like_count, "followCount": follow_count,
        "roomTotal": room_total, "roomTotalUser": room_total_user,
        "roomTotalUserStr": room_total_user_str, "memberCount": member_count,
        "rtf": rtf if (rtf and len(rtf)) else None,
    }


def build_ws_url(push_server, room_id, unique_id, cursor, internal_ext, signature,
                 screen_h=1080, screen_w=1920):
    """组 WS 连接地址（参数与 JS wsParams 一致）。"""
    import urllib.parse as up
    params = {
        "aid": "6383", "app_name": "douyin_web", "browser_language": "zh-CN",
        "browser_name": "Mozilla", "browser_online": "true", "browser_platform": "Win32",
        "browser_version": UA, "compress": "gzip", "cookie_enabled": "true",
        "device_platform": "web", "did_rule": "3", "endpoint": "live_pc",
        "heartbeatDuration": "0", "host": "https://live.douyin.com", "identity": "audience",
        "insert_task_id": "", "live_id": "1", "live_reason": "",
        "need_persist_msg_count": "15", "screen_height": str(screen_h),
        "screen_width": str(screen_w), "support_wrds": "1", "tz_name": "Asia/Shanghai",
        "update_version_code": "1.0.14-beta.0", "version_code": "180800",
        "webcast_sdk_version": "1.0.14-beta.0", "room_id": str(room_id),
        "user_unique_id": str(unique_id), "cursor": cursor,
        "internal_ext": internal_ext, "signature": signature,
    }
    return push_server + "?" + up.urlencode(params)


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

    @classmethod
    def _wait_acrawler(cls, page, timeout: float = 4.0) -> bool:
        """轮询等待 mssdk.js 就绪（150ms 间隔，就绪即返回）。

        goto(wait_until="commit") 后 JS 上下文可能尚未建立，evaluate 会抛
        "Execution context" 类异常 → 视为未就绪继续等。超时返回 False（风控）。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls._has_acrawler(page):
                return True
            try:
                page.wait_for_timeout(150)
            except Exception:
                break
        return cls._has_acrawler(page)

    @staticmethod
    def _arm_im_capture(page):
        """在页面发请求前布好 im/fetch 响应捕获，返回收集列表。

        注意：handler 里只存 response 对象，**不读 body**（sync API 在事件
        回调里读 body 会重入报错）；body 由 _prep_from_capture 延后读取。
        """
        holder = []

        def _on_resp(resp):
            try:
                if "/webcast/im/fetch/" in resp.url and resp.status == 200:
                    holder.append(resp)
            except Exception:
                pass

        try:
            page.on("response", _on_resp)
        except Exception:
            pass
        return holder

    @staticmethod
    def _parse_im_body(body):
        """从 im/fetch 响应体（Response protobuf）取 cursor/internalExt/pushServer。

        字段号与 _PREP_JS 里一致：2=cursor 5=internalExt 10=pushServer。
        """
        out = {}
        r = _Reader(body)
        try:
            while r.pos < len(body):
                tag = r.varint()
                f, w = tag >> 3, tag & 7
                if w == 2:
                    ln = r.varint()
                    if f == 2:
                        out["cursor"] = r.str_(ln)
                    elif f == 5:
                        out["internalExt"] = r.str_(ln)
                    elif f == 10:
                        out["pushServer"] = r.str_(ln)
                    else:
                        r.skip(ln)
                elif w == 0:
                    r.varint()
                elif w == 1:
                    r.skip(8)
                elif w == 5:
                    r.skip(4)
                else:
                    break
        except Exception:
            return {}
        return out

    @classmethod
    def _prep_from_capture(cls, page, holder, info, wait=12.0):
        """截页面自身 im/fetch 的响应，解析出 WS 连接参数（含 did）。

        为什么不再自己 fetch：2026-10 起抖音对 im/fetch 的 a_bogus 与
        user_unique_id 校验收紧——自己拼参数（a_bogus 占位 '00000000'、
        user_unique_id 用固定值）只会拿到 200 + application/json + **空体**，
        cursor/internalExt/pushServer 全缺，于是旧路径直接报
        「连接参数不完整（可能被抖音风控拦截）」。而页面自己发的那条请求
        签名与 did 都是真的，直接截它的响应体解析即可。
        """
        want = str(info.get("roomId") or "")
        deadline = time.time() + wait
        resp = None
        while time.time() < deadline:
            for r in list(holder):
                try:
                    if want and f"room_id={want}" not in r.url:
                        continue
                except Exception:
                    continue
                resp = r
                break
            if resp is not None:
                break
            try:
                page.wait_for_timeout(200)
            except Exception:
                break
        if resp is None:
            return {}
        try:
            body = resp.body()
        except Exception:
            return {}
        if not body or len(body) < 32:
            return {}
        parsed = cls._parse_im_body(body)
        if not (parsed.get("cursor") and parsed.get("internalExt")
                and parsed.get("pushServer")):
            return {}
        # did：页面自己的 user_unique_id（internalExt 的 wss_push_did 最可靠，
        # 退而求其次从请求 URL 取）——WS 的 user_unique_id 必须与 fetch 时一致
        did = ""
        m = _re.search(r"wss_push_did:(\d+)", parsed.get("internalExt") or "")
        if m:
            did = m.group(1)
        if not did:
            try:
                import urllib.parse as _up
                q = _up.parse_qs(resp.url.split("?", 1)[1])
                did = (q.get("user_unique_id") or [""])[0]
            except Exception:
                did = ""
        parsed["did"] = did
        return parsed

    @classmethod
    def _prep_ws_params(cls, page, holder, info, md5hex):
        """拿 WS 连接参数：优先截页面自身的 im/fetch 响应，失败回退旧的自造请求。

        返回 cursor/internalExt/pushServer/signature/uniqueId/screenW/H；
        失败时带 result='missing'（或 error），由调用方统一报错。
        """
        cap = cls._prep_from_capture(page, holder, info)
        if cap:
            did = cap.get("did") or info["uniqueId"]
            # 握手签名与 user_unique_id 必须同源：用同一个 did 重算 stub md5
            md5hex2 = stub_md5(info["roomId"], did)
            sig, sw, sh = "", 1920, 1080
            try:
                sig, sw, sh = page.evaluate(
                    """m => { const s = window.byted_acrawler.frontierSign({'X-MS-STUB': m});
                              return [s['X-Bogus'], screen.width, screen.height]; }""",
                    md5hex2)
            except Exception as e:
                print(f"[抖音直播] 握手签名生成失败: {e}")
            if sig:
                return {"cursor": cap["cursor"], "internalExt": cap["internalExt"],
                        "pushServer": cap["pushServer"], "signature": sig,
                        "uniqueId": did, "screenWidth": sw, "screenHeight": sh}
        # 旧路径兜底：自己拼参数发 im/fetch（风控期会拿到空体 → missing）
        try:
            return page.evaluate(_PREP_JS, {"roomId": info["roomId"],
                                            "uniqueId": info["uniqueId"],
                                            "md5hex": md5hex})
        except Exception as e:
            return {"error": f"参数获取异常: {e}"}

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
            # 未开播：不硬建浏览器/WS 连接，直接回报状态（UI 提示后由轮询或用户手动重试）
            if info.get("status") is not None and int(info["status"]) != 2:
                self._emit(self.on_status, "not_live",
                           f"主播当前未开播（房间 {self.room_num}）")
                return

            md5hex = stub_md5(info["roomId"], info["uniqueId"])
            self._emit(self.on_status, "connecting", "正在建立弹幕通道（过风控，确认收流后释放 Cookie）...")

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

                browser = pw_launch(p, headless=True)
                ctx = browser.new_context(**ctx_kwargs)
                _inject_cookie(ctx)
                page = ctx.new_page()
                # 页面自己那条 im/fetch 响应是连接参数的可靠来源，必须在
                # goto 之前就挂上监听（页面加载后会自动发起）
                cap = self._arm_im_capture(page)
                # commit 即返回（DOM 未就绪由下方 acrawler 轮询兜底），
                # domcontentloaded 要等整个 DOM 解析完成，实测多耗 1.5~2s
                try:
                    page.goto(f"https://live.douyin.com/{self.room_num}",
                              wait_until="commit", timeout=30000)
                except Exception:
                    page.goto(f"https://live.douyin.com/{self.room_num}",
                              wait_until="domcontentloaded", timeout=30000)
                # 等 mssdk.js（byted_acrawler）就绪：150ms 轮询、就绪即返回
                # （原固定 4 秒等待，实测 acrawler 在 goto 后 0~1 秒内就绪，白等 3 秒+）
                if not self._wait_acrawler(page, timeout=4.0):
                    browser.close()
                    self._emit(self.on_status, "captcha", "检测到抖音风控，请在弹出的浏览器窗口手动完成验证码...")
                    # 切换有头浏览器，弹出验证窗口，等待用户手动过验证码
                    # 可见窗口（验证码弹窗）走统一浏览器层（Edge → Chrome → 默认）
                    browser = pw_launch(p, headless=False)
                    ctx = browser.new_context(**ctx_kwargs)
                    _inject_cookie(ctx)
                    page = ctx.new_page()
                    cap = self._arm_im_capture(page)
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

                result = self._prep_ws_params(page, cap, info, md5hex)
                if result.get("error"):
                    browser.close()
                    self._emit(self.on_status, "error", f"连接失败: {result['error']}")
                    return
                if result.get("result") == "missing":
                    browser.close()
                    self._emit(self.on_status, "error", "连接参数不完整（可能被抖音风控拦截）")
                    return
                # WS 握手凭证：ttwid（匿名会话，握手强制）+ sessionid（登录凭证）。
                # 实测：无 Cookie 握手被 417 拒绝；仅 ttwid 弹幕正常但礼物消息被服务器
                # 过滤（同房间同窗口：全量 Cookie 848 条礼物 / 仅 ttwid 0 条）——
                # 礼物推送需要 sessionid 登录凭证。WS 仅在握手时携带，连接建立后
                # 照旧确认收流即释放浏览器 Cookie，不长期占用账号。
                ck = {}
                try:
                    ck = {c["name"]: c["value"] for c in ctx.cookies()}
                except Exception:
                    pass
                cred = []
                if ck.get("ttwid"):
                    cred.append("ttwid=" + ck["ttwid"])
                if ck.get("sessionid"):
                    cred.append("sessionid=" + ck["sessionid"])
                if not cred:
                    browser.close()
                    self._emit(self.on_status, "error", "未获取到会话凭证（ttwid），无法建立弹幕通道")
                    return
                result["wsCookie"] = "; ".join(cred)
                result["guest"] = not ck.get("sessionid")

                # Cookie 链接（浏览器）保持到弹幕通道确认收流后再释放：
                # 握手失败 / 收不到服务端推送时立即报错，不会带着假连接状态放走。
                released = [False]

                def _release_browser():
                    if not released[0]:
                        released[0] = True
                        try:
                            browser.close()
                        except Exception:
                            pass

                try:
                    self._ws_loop(result, info, release=_release_browser)
                except Exception as e:
                    self._emit(self.on_status, "error", f"弹幕连接异常: {e}")
                finally:
                    _release_browser()
                    # 终态统一在此上报一次（覆盖握手失败/心跳失败/recv 断开/空帧/手动停止全部路径），
                    # _ws_loop 内部不再发 disconnected，避免双重上报互相覆盖文案
                    self._emit(self.on_status, "disconnected",
                               "已停止" if self._stop.is_set() else "直播已结束 / 连接断开")
                return
        except Exception as e:
            self._emit(self.on_status, "error", f"抓取异常: {e}")
            return

    def _ws_loop(self, params, info, release=None):
        """Python 原生 WebSocket 弹幕循环（仅带 ttwid 匿名凭证，不占用账号 Cookie）。

        release: 确认收流后的回调（关浏览器释放登录 Cookie）；未确认前绝不调用。
        """
        import websocket
        ws_url = build_ws_url(
            params["pushServer"], info["roomId"],
            params.get("uniqueId") or info["uniqueId"],
            params["cursor"], params["internalExt"], params["signature"],
            screen_h=params.get("screenHeight") or 1080,
            screen_w=params.get("screenWidth") or 1920)
        headers = ["User-Agent: " + UA, "Origin: https://live.douyin.com"]
        if params.get("wsCookie"):
            headers.append("Cookie: " + params["wsCookie"])
        ws = websocket.WebSocket()
        ws.connect(ws_url, header=headers, timeout=10)
        ws.settimeout(2)
        ws.send(_heartbeat())

        # 首帧确认：握手通过且收到服务端推送（出弹幕/在线人数等）才算连通。
        # 确认前保持浏览器（Cookie 链接）不释放；超时/断开则明确报错。
        confirm_deadline = time.time() + 20
        confirmed = False
        last_hb = time.time()
        while not self._stop.is_set() and time.time() < confirm_deadline:
            if time.time() - last_hb >= 10:
                try:
                    ws.send(_heartbeat())
                    last_hb = time.time()
                except Exception:
                    break
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if raw:
                confirmed = True
                break
        if not confirmed:
            try:
                ws.close()
            except Exception:
                pass
            if release:
                release()
            if self._stop.is_set():
                return
            raise RuntimeError("弹幕通道未确认收流（20 秒内无服务端推送，可能被风控）")

        # 弹幕已确认推送：此刻释放登录 Cookie（关浏览器），WS 靠握手凭证维持
        if release:
            release()
        if params.get("guest"):
            self._emit(self.on_status, "connected",
                       f"已连接 · 房间 {self.room_num}（游客态：无登录凭证，礼物消息收不到）")
        else:
            self._emit(self.on_status, "connected",
                       f"已连接 · 房间 {self.room_num}（已确认收流，Cookie 已释放）")
        last_hb = time.time()
        while not self._stop.is_set():
            # 心跳：每 10 秒
            if time.time() - last_hb >= 10:
                try:
                    ws.send(_heartbeat())
                except Exception:
                    break
                last_hb = time.time()
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw:
                break
            try:
                frame = decode_push_frame(raw)
            except Exception:
                continue
            payload = frame.get("payload")
            headers = frame.get("headers") or {}
            if payload and headers.get("compress_type") == "gzip":
                try:
                    payload = _gzip.decompress(payload)
                except Exception:
                    pass
            if not payload:
                continue
            need_ack = False
            ext = headers.get("im-internal_ext", "")
            try:
                res = decode_response(payload)
                if res["needAck"]:
                    need_ack = True
                if res["internalExt"]:
                    ext = res["internalExt"]
            except Exception:
                res = None
            if res:
                now_ms = int(time.time() * 1000)
                for m in res["messages"]:
                    try:
                        e = extract_msg(m)
                    except Exception:
                        continue
                    if not e.get("type"):
                        continue
                    e["ts"] = now_ms
                    name, _cat = MSG_TYPES.get(e["type"], (e["type"], "other"))
                    line = self._format_line(name, e.get("nickname", ""), e.get("content", ""))
                    if line:
                        self._emit(self.on_message, name, line, e)
                    else:
                        self._emit(self.on_message, name, "", e)
            if need_ack:
                try:
                    ws.send(_ack_msg(ext, frame.get("logId")))
                except Exception:
                    break
        try:
            ws.close()
        except Exception:
            pass


# 弹幕姬页统一抓取器入口（bili_live.LiveFetcher 同名同构）
LiveFetcher = DouyinLiveFetcher
