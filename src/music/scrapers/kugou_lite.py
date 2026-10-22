"""
======================================================================
  酷狗概念版 (kugou lite) 抓取器 —— 第六大音乐平台
  链路复刻自 MakcRe/KuGouMusicApi（platform=lite）：
  - 三套签名：Android(lite 盐) / Web / signKey
  - 扫码：login-user.kugou.com/v2/qrcode + /v2/get_userinfo_qrcode
  - 领VIP：gateway.kugou.com/youth/v1/ad/play_report（广告播放上报，+3小时/次）
  - 搜索：gateway.kugou.com/v3/search/song（x-router: complexsearch）
  - 播放链接：gateway.kugou.com/v5/url（x-router: trackercdn）
  会话（token/userid）存 config/kugou_lite_session.json，登录后免扫码。
  搜索需登录（游客报 152）；登录后可领取概念版免费 VIP 下载高音质。
======================================================================
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import uuid as uuidlib
from pathlib import Path
from typing import Optional

SALT_ANDROID_LITE = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA"   # 概念版 Android 签名盐
SALT_WEB = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"            # Web 签名盐（登录用）
SALT_SIGNKEY_LITE = "185672dd44712f60bb1736df5a377e82"   # v5/url 的 key 盐
APPID = 3116                # 概念版 appid（主版 1005）
CLIENTVER = 11440           # 概念版 clientver
SRCAPPID = 2919
UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
KG_HEADERS = {"kg-rc": "1", "kg-thash": "5d816a0", "kg-rec": "1",
              "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F"}

# 音质映射：UI/下载器值 → v5/url quality 参数
QUALITY_MAP = {"128k": "128", "320k": "320", "flac": "flac"}


def _md5(s: str) -> str:
    return hashlib.md5(str(s).encode()).hexdigest()


def _sign_android(params: dict, data: str = "") -> str:
    """Android(lite) 签名：盐 + 排序参数串 + 请求体 + 盐 → md5。"""
    ps = "".join(f"{k}={json.dumps(v, separators=(',', ':')) if isinstance(v, dict) else v}"
                 for k, v in sorted(params.items()))
    return _md5(f"{SALT_ANDROID_LITE}{ps}{data}{SALT_ANDROID_LITE}")


def _sign_web(params: dict, data: str = "") -> str:
    ps = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    return _md5(f"{SALT_WEB}{ps}{data}{SALT_WEB}")


def _sign_key(h: str, mid: str, userid, appid=APPID) -> str:
    """v5/url 的 key：md5(hash + 盐 + appid + mid + userid)。"""
    return _md5(f"{h}{SALT_SIGNKEY_LITE}{appid}{mid}{userid or 0}")


def _calc_mid(guid: str) -> str:
    """mid = int(md5(guid), 16) 的十进制字符串。"""
    return str(int(_md5(guid), 16))


def session_file() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "kugou_lite_session.json"


def load_session() -> dict:
    """读取已存会话：{guid, mid, token, userid[, nickname]}，无/损坏返回空 dict。"""
    f = session_file()
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if d.get("token") else {}
    except Exception:
        return {}


def save_session(guid: str, mid: str, token: str, userid, nickname: str = "") -> None:
    """写会话；nickname 是扫码登录时接口返回的昵称（概念版没有可用的用户信息接口，
    不存下来状态行就只能显示用户ID）。"""
    f = session_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    old = load_session()
    nick = nickname or old.get("nickname") or ""
    f.write_text(json.dumps({
        "guid": guid, "mid": mid, "token": token, "userid": userid,
        "nickname": nick,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_session() -> None:
    try:
        session_file().unlink(missing_ok=True)
    except Exception:
        pass


def session_logged_in() -> bool:
    return bool(load_session().get("token"))


class KuGouLiteClient:
    """酷狗概念版 API 客户端（扫码登录 / 领VIP / 搜索 / 取播放链接）。"""

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.guid = str(uuidlib.uuid4())
        self.mid = _calc_mid(self.guid)
        self.dfid = "-"      # 未注册设备；v5/url 按源码用随机 dfid
        self.token = ""
        self.userid = 0
        self.nickname = ""
        self._load_session()

    # ---- 会话持久化 ----
    def _load_session(self):
        d = load_session()
        if d:
            self.guid = d.get("guid") or self.guid
            self.mid = d.get("mid") or _calc_mid(self.guid)
            self.token = d.get("token") or ""
            self.userid = d.get("userid") or 0
            self.nickname = d.get("nickname") or ""

    def _save_session(self):
        save_session(self.guid, self.mid, self.token, self.userid, self.nickname)

    # ---- 请求封装（仿 KuGouMusicApi request.js 参数注入） ----
    def _request(self, method: str, url: str, extra_params: dict,
                 encrypt="android", x_router=None, body: dict | None = None,
                 dfid=None, no_token=False):
        params = {
            "dfid": dfid or self.dfid, "mid": self.mid, "uuid": "-",
            "appid": APPID, "clientver": CLIENTVER, "clienttime": int(time.time()),
        }
        if self.token and not no_token:
            params["token"] = self.token
        if self.userid and not no_token:
            params["userid"] = self.userid
        params.update(extra_params)
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        params["signature"] = _sign_web(params) if encrypt == "web" else _sign_android(params, body_str)
        headers = {**KG_HEADERS, "dfid": params["dfid"], "mid": self.mid,
                   "clienttime": str(params["clienttime"])}
        if x_router:
            headers["x-router"] = x_router
        if body is not None:
            headers["Content-Type"] = "application/json"
        r = self.s.request(method, url, params=params,
                           data=body_str.encode() if body is not None else None,
                           headers=headers, timeout=20)
        try:
            return r.json()
        except ValueError:
            return {"_raw": r.text[:500], "_status": r.status_code}

    # ---- 扫码登录（GUI 分步驱动：生成 → 展示 → 轮询） ----
    def qr_create(self) -> tuple[str, bytes]:
        """生成登录二维码。返回 (qrcode_key, png_bytes)；失败抛异常。"""
        import base64
        d = self._request("GET", "https://login-user.kugou.com/v2/qrcode", {
            "appid": 1001, "type": 1, "plat": 4,
            "qrcode_txt": f"https://h5.kugou.com/apps/loginQRCode/html/index.html?appid={APPID}&",
            "srcappid": SRCAPPID,
        }, encrypt="web", dfid="-", no_token=True)
        data = d.get("data") or {}
        qrcode_key = data.get("qrcode") or data.get("key") or ""
        if not qrcode_key:
            raise RuntimeError(f"生成二维码失败：{json.dumps(d, ensure_ascii=False)[:200]}")
        img = data.get("qrcode_img") or ""
        if img.startswith("data:image"):
            return qrcode_key, base64.b64decode(img.split(",", 1)[1])
        # 官方图缺失时本地生成
        import qrcode
        import io
        buf = io.BytesIO()
        qr_url = f"https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={qrcode_key}"
        qrcode.make(qr_url).save(buf, format="PNG")
        return qrcode_key, buf.getvalue()

    def qr_poll(self, qrcode_key: str) -> dict:
        """轮询扫码状态。返回 {status, message, logged_in}。
        status: 1=等扫码 2=已扫码待确认 4=成功 0=过期。"""
        extra = {"plat": 4, "appid": APPID, "srcappid": SRCAPPID,
                 "qrcode": qrcode_key}
        dev = self.s.cookies.get("KUGOU_API_DEV")
        if dev:
            extra["dev"] = dev
        d = self._request("GET", "https://login-user.kugou.com/v2/get_userinfo_qrcode",
                          extra, encrypt="web", dfid="-", no_token=True)
        data = d.get("data") or {}
        st = data.get("status")
        if st == 4:
            self.token = data.get("token") or ""
            self.userid = data.get("userid") or 0
            ui = data.get("userinfo") or data.get("user_info") or {}
            self.nickname = (data.get("nickname") or data.get("NickName")
                             or ui.get("nickname") or ui.get("NickName")
                             or ui.get("user_name") or self.nickname or "")
            if self.token:
                self._save_session()
                return {"status": 4, "message": "登录成功", "logged_in": True}
            return {"status": 4, "message": "已确认但未返回 token", "logged_in": False}
        if st == 0:
            return {"status": 0, "message": "二维码已过期，请重新登录", "logged_in": False}
        if st == 2:
            return {"status": 2, "message": "已扫码，请在手机上确认", "logged_in": False}
        return {"status": st or 1, "message": "等待扫码", "logged_in": False}

    # ---- 会话校验（轻量搜索：token 失效即报错，无需真实搜索词） ----
    def verify_session(self) -> tuple[bool, str]:
        """验证已存会话是否仍有效。返回 (ok, 消息)。"""
        if not self.token:
            return False, "未登录（无会话文件）"
        d = self._request("GET", "https://gateway.kugou.com/v3/search/song", {
            "keyword": "a", "page": 1, "pagesize": 1,
            "platform": "AndroidFilter", "albumhide": 0, "iscorrection": 1, "nocollect": 0,
        }, x_router="complexsearch.kugou.com")
        st = d.get("status")
        if st == 1:
            return True, "会话有效"
        err = d.get("error_code")
        if err == 152:
            return False, "token 已失效（被识别为游客），请重新扫码登录"
        return False, f"验证失败：status={st} error_code={err}"

    # ---- 概念版领 VIP（广告播放上报，每次 +3 小时，每日 8 次上限） ----
    def claim_vip(self) -> tuple[bool, str]:
        now = int(time.time() * 1000)
        d = self._request("POST", "https://gateway.kugou.com/youth/v1/ad/play_report", {}, body={
            "ad_id": 12307537187, "play_end": now, "play_start": now - 30000,
        })
        st = d.get("status")
        err = d.get("error_code")
        if st == 1:
            return True, "领取成功（VIP +3 小时）"
        if err == 20026:
            return True, "今日领取次数已达上限（每日 8 次）"
        return False, f"领取未确认（status={st} error_code={err}），需在手机 App 验证"

    # ---- 搜索（概念版 v3 字段：FileHash/FileName/PayType） ----
    def search(self, keyword: str, page: int = 1, pagesize: int = 30) -> list[dict]:
        if not self.token:
            raise RuntimeError("酷狗概念版未登录（需先在 Cookie 管理扫码登录）")
        d = self._request("GET", "https://gateway.kugou.com/v3/search/song", {
            "keyword": keyword, "page": page, "pagesize": pagesize,
            "platform": "AndroidFilter", "albumhide": 0, "iscorrection": 1, "nocollect": 0,
        }, x_router="complexsearch.kugou.com")
        if d.get("status") != 1:
            raise RuntimeError(f"搜索失败：status={d.get('status')} error_code={d.get('error_code')}")
        return (d.get("data") or {}).get("lists") or []

    # ---- 播放链接（v5/url，key 参与签名） ----
    def song_url(self, h: str, quality: str = "128") -> tuple[str, str]:
        """返回 (url, ext)；失败抛 RuntimeError。quality: 128/320/flac。"""
        if not self.token:
            raise RuntimeError("酷狗概念版未登录（需先在 Cookie 管理扫码登录）")
        h = h.lower()
        extra = {
            "album_id": 0, "area_code": 1, "hash": h, "ssa_flag": "is_fromtrack",
            "version": 11430, "page_id": 967177915, "quality": quality,
            "album_audio_id": 0, "behavior": "play", "pid": 411, "cmd": 26,
            "pidversion": 3001, "IsFreePart": 0,
            "ppage_id": "356753938,823673182,967485191",
            "cdnBackup": 1, "module": "",
        }
        # key 必须在 signature 之前注入（参与签名）
        extra["key"] = _sign_key(h, self.mid, self.userid)
        d = self._request("GET", "https://gateway.kugou.com/v5/url", extra,
                          x_router="trackercdn.kugou.com",
                          dfid="".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=24)))
        # v5/url 响应：顶层 url 为数组，backupUrl 为备选
        url = ""
        if isinstance(d.get("url"), list) and d["url"]:
            url = d["url"][0]
        elif isinstance(d.get("url"), str):
            url = d["url"]
        if not url and isinstance(d.get("backupUrl"), list) and d["backupUrl"]:
            url = d["backupUrl"][0]
        if d.get("status") == 1 and url:
            return url, (d.get("extName") or "mp3")
        err = d.get("error_code")
        msg = f"未拿到播放链接（status={d.get('status')} error={err}）"
        if err in (20020, 20021, 30004):
            msg += "：会员歌曲，请先在 Cookie 管理领取概念版 VIP 后重试"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Scraper：接入 music 体系（搜索/详情/解析）

class KuGouLiteScraper:
    """酷狗概念版 Scraper（duck-typing 兼容 BaseScraper 接口）。

    不继承 BaseScraper：lite 的 search 需要登录态，构造时惰性创建客户端，
    且不实现 get_album（概念版无公开专辑接口）。
    """
    platform = "kugou_lite"
    platform_name = "酷狗概念版"

    def __init__(self):
        self._client: KuGouLiteClient | None = None

    @property
    def client(self) -> KuGouLiteClient:
        if self._client is None:
            self._client = KuGouLiteClient()
        return self._client

    # ---- 搜索 ----
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list:
        from ..models import SongInfo, FileInfo, PayInfo
        items = self.client.search(keyword, page, limit)
        songs = []
        for it in items:
            h = (it.get("FileHash") or it.get("Hash") or it.get("hash") or "").lower()
            if not h:
                continue
            name = it.get("FileName") or it.get("SongName") or ""
            singer = it.get("SingerName") or it.get("singername") or ""
            # FileName 形如 "歌手 - 歌名"，SingerName 缺失时回拆
            if not singer and " - " in name:
                singer, name = name.split(" - ", 1)
            pay_type = it.get("PayType")
            duration = it.get("Duration") or it.get("duration") or 0
            size = it.get("FileSize") or it.get("FileSize128") or 0
            songs.append(SongInfo(
                song_id=h,
                song_name=name,
                title=name,
                singer=singer,
                album_name=it.get("AlbumName") or "",
                duration=int(duration) if duration else 0,
                interval=int(duration) if duration else 0,
                cover_url=(it.get("Image") or "").replace("{size}", "480"),
                size128=int(size) if size else 0,
                files=[FileInfo(quality="128kbps", size=int(size), extension="mp3")] if size else [],
                pay=PayInfo(fee=1 if pay_type in (3, "3") else 0),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://www.kugou.com/song/#hash={h}",
                extra={"PayType": pay_type, "MixSongID": it.get("MixSongID") or ""},
            ))
        return songs

    # ---- 详情：hash 与主版酷狗同库，复用游客接口（song/info + krc 歌词） ----
    def get_song_detail(self, song_id: str) -> Optional:
        from .kugou import KugouScraper
        try:
            return KugouScraper().get_song_detail(song_id)
        except Exception:
            from ..models import SongInfo
            return SongInfo(song_id=song_id, platform=self.platform,
                            platform_name=self.platform_name,
                            url=f"https://www.kugou.com/song/#hash={song_id}")

    # ---- 歌单：概念版无公开歌单接口 ----
    def get_playlist(self, playlist_id: str):
        return None

    # ---- 播放直链 ----
    def get_play_url(self, song_id: str) -> str:
        try:
            url, _ = self.client.song_url(song_id, "128")
            return url
        except Exception:
            return ""

    # ---- 链接/输入解析：与主版酷狗一致（hash=xxx / mixsong/xxx / 32位hex） ----
    @staticmethod
    def extract_song_id(text: str) -> str:
        import re
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"hash=([a-fA-F0-9]{32})", s) or re.search(r"mixsong/([a-fA-F0-9]{32})", s)
        if m:
            return m.group(1)
        if re.fullmatch(r"[a-fA-F0-9]{32}", s):
            return s
        return ""

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        return ""

    def search(self, keyword: str, limit: int = 5) -> list:
        songs = self.search_song(keyword, 1, limit)
        for s in songs:
            s.platform_name = self.platform_name
        return songs
