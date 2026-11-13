"""四平台在线标签/歌词/封面搜索：对齐 musictag 的联网匹配行为。

数据源：网易云、QQ 音乐、酷狗、酷我。
能力：
    · search_tracks()   多源多趟搜索（全名 / 仅标题 / 专辑+艺术家）
    · rank_candidates() 按标题/艺术家/专辑相似度排序 + musictag 的优先提升规则
    · load_lyric()      按平台取 LRC（含网易云/QQ 翻译）
    · download_cover()  按图床带 Referer 下载封面（webp 自动转 jpg）
    · auto_match()      单文件自动匹配：最佳候选 + 歌词 + 封面
"""

from __future__ import annotations

import base64
import json
import random
import re
import string
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

try:
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover - requirements 已声明 pycryptodome
    AES = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SOURCE_NAMES = {
    "netease": "网易云音乐",
    "qq": "QQ音乐",
    "kugou": "酷狗音乐",
    "kuwo": "酷我音乐",
}
DEFAULT_SOURCES = ["netease", "qq", "kugou", "kuwo"]
DEFAULT_CONNECTOR = "/"


@dataclass
class TagCandidate:
    source: str = ""
    source_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    track: int = 0
    disc: int = 0
    genre: str = ""
    comment: str = ""
    cover_url: str = ""
    duration: int = 0          # 毫秒
    original_title: str = ""
    # 平台附加标识
    qq_mid: str = ""
    kugou_hash: str = ""
    netease_album_id: str = ""
    # 排序用
    search_pass: int = 0
    result_order: int = 0
    source_order: int = 0
    scores: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # 歌词
    lyric: str = ""
    lyric_trans: str = ""

    @property
    def source_name(self) -> str:
        return SOURCE_NAMES.get(self.source, self.source)

    @property
    def track_label(self) -> str:
        if self.track <= 0:
            return ""
        return f"Track {self.track}" + (f" of {self.disc}" if self.disc > 1 else "")


@dataclass
class MatchResult:
    candidate: TagCandidate | None = None
    ranked: list[TagCandidate] = field(default_factory=list)
    cover: bytes = b""
    cover_mime: str = ""
    errors: dict[str, str] = field(default_factory=dict)


# ==================== HTTP ====================

def _client(timeout: float = 20.0, headers: dict | None = None) -> httpx.Client:
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=h)


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        text = (resp.text or "").strip()
        if text.startswith("MusicJsonCallback"):
            text = text[text.find("(") + 1:]
            if text.endswith(")"):
                text = text[:-1]
        try:
            return json.loads(text)
        except Exception:
            return None


def referer_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "163" in host or "126.net" in host:
        return "https://music.163.com/"
    if "qq.com" in host or "gtimg.cn" in host:
        return "https://y.qq.com/"
    if "kugou" in host or "kgimg" in host:
        return "https://www.kugou.com/"
    if "kuwo" in host:
        return "https://www.kuwo.cn/"
    return ""


# ==================== 网易云 weapi 加密 ====================

_NE_AES_KEY = b"0CoJUm6Qyw8W8jud"
_NE_AES_IV = b"0102030405060708"
_NE_RSA_EXP = 0x010001
_NE_RSA_MOD = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725"
    "152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312e"
    "cbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d8"
    "13cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7", 16)
_NE_ALPHABET = string.ascii_letters + string.digits
_NE_COMMENT_PREFIX = "163 key(Don't modify):"
_NE_COMMENT_KEY = b"#14ljk_!\\]&0U<'("


def _aes_cbc_b64(data: bytes, key: bytes, iv: bytes) -> str:
    if AES is None:
        raise RuntimeError("缺少 pycryptodome，无法调用网易云加密接口")
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    return base64.b64encode(AES.new(key, AES.MODE_CBC, iv).encrypt(data)).decode()


def _rsa_encrypt(text: str) -> str:
    message = int.from_bytes(text[::-1].encode("ascii"), "big")
    cipher = pow(message, _NE_RSA_EXP, _NE_RSA_MOD)
    return format(cipher, "x").rjust(256, "0")


def _netease_encrypt(payload: dict) -> tuple[str, str]:
    secret = "".join(random.choice(_NE_ALPHABET) for _ in range(16))
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    first = _aes_cbc_b64(raw.encode("utf-8"), _NE_AES_KEY, _NE_AES_IV)
    params = _aes_cbc_b64(first.encode("ascii"), secret.encode("ascii"), _NE_AES_IV)
    return params, _rsa_encrypt(secret)


def encode_netease_comment(text: str) -> str:
    """写 163 key 注释（musictag 的 CommentTagWrite163Key 行为）。"""
    if AES is None:
        return text
    pad = 16 - len(text.encode("utf-8")) % 16
    data = text.encode("utf-8") + bytes([pad]) * pad
    cipher = AES.new(_NE_COMMENT_KEY, AES.MODE_ECB).encrypt(data)
    return _NE_COMMENT_PREFIX + base64.b64encode(cipher).decode()


def decode_netease_comment(comment: str) -> str:
    if AES is None or not comment or not comment.startswith(_NE_COMMENT_PREFIX):
        return ""
    try:
        cipher = base64.b64decode(comment[len(_NE_COMMENT_PREFIX):])
        plain = AES.new(_NE_COMMENT_KEY, AES.MODE_ECB).decrypt(cipher)
        return plain[:-plain[-1]].decode("utf-8", "replace")
    except Exception:
        return ""


def _ne_headers() -> dict:
    return {
        "Host": "music.163.com",
        "Referer": "https://music.163.com",
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/webp,image/apng,*/*;q=0.8"),
        "X-Real-IP": f"112.88.{random.randint(0, 255)}.{random.randint(0, 255)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _netease_search_raw(query: str, limit: int) -> list[dict]:
    params, enc_sec = _netease_encrypt({
        "s": query, "type": 1, "limit": limit, "total": "true", "offset": 0,
    })
    body = f"params={_urlencode(params)}&encSecKey={_urlencode(enc_sec)}"
    with _client(headers=_ne_headers()) as client:
        resp = client.post("https://music.163.com/weapi/cloudsearch/pc", content=body.encode("utf-8"))
    data = _json(resp)
    if not isinstance(data, dict):
        return []
    result = data.get("result") if isinstance(data.get("result"), dict) else None
    songs = (result or {}).get("songs") if result else data.get("songs")
    return songs if isinstance(songs, list) else []


def _urlencode(value: str) -> str:
    from urllib.parse import quote
    return quote(str(value), safe="")


def _netease_candidate(item: dict, connector: str) -> TagCandidate:
    album = item.get("al") or item.get("album") or {}
    artists = item.get("ar") or item.get("artists") or []
    names = [a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")]
    aliases = item.get("alia") or item.get("alias") or []
    year = _year_from_ts(album.get("publishTime") or item.get("publishTime") or 0)
    track = _to_int(item.get("no") or item.get("position") or 0)
    disc = _to_int(str(item.get("cd") or item.get("disc") or "").split("/")[0])
    cover = (album.get("picUrl") or "").replace("http://", "https://")
    if cover:
        cover = cover.split("?")[0]
    return TagCandidate(
        source="netease", source_id=str(item.get("id") or ""),
        title=str(item.get("name") or ""), artist=connector.join(names),
        album=str(album.get("name") or ""), year=year, track=track, disc=disc,
        comment="\n".join(str(a) for a in aliases if a),
        cover_url=cover, duration=_to_int(item.get("dt") or item.get("duration") or 0),
        netease_album_id=str(album.get("id") or ""),
    )


def search_netease(query: str, limit: int = 15, connector: str = DEFAULT_CONNECTOR) -> list[TagCandidate]:
    return [_netease_candidate(x, connector) for x in _netease_search_raw(query, limit)
            if isinstance(x, dict) and x.get("id")]


# ==================== QQ 音乐 ====================

_QQ_CALLBACK = "MusicJsonCallback34475857153687595"
_QQ_EMPTY_LYRIC = ("WzAwOjAwOjAwXeatpOatjOabsuS4uuayoeacieWhq+ivjeeahOe6r+mfs+S5kO+8"
                   "jOivt+aCq+aso+i1jw==")
_QQ_GENRES = {
    0: "流行", 1: "Pop", 2: "摇滚", 3: "民谣", 4: "电子", 5: "舞曲", 6: "说唱",
    7: "轻音乐", 8: "爵士", 9: "乡村", 10: "古典", 11: "R&B", 12: "古风",
    13: "影视原声", 14: "动漫", 15: "游戏", 16: "网络", 17: "实验", 18: "另类",
    19: "世界音乐", 20: "新世纪",
}


def _qq_headers() -> dict:
    return {
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "referer": "https://i.y.qq.com/",
        "Content-Type": "application/json",
    }


def _qq_search_raw(query: str, limit: int, retries: int = 4) -> list[dict]:
    payload = {
        "req_0": {
            "method": "DoSearchForQQMusicDesktop",
            "module": "music.search.SearchCgiService",
            "param": {"search_type": 0, "query": query, "page_num": 1,
                      "num_per_page": limit},
        }
    }
    import time as _time
    for attempt in range(retries):
        with _client(headers=_qq_headers()) as client:
            resp = client.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=payload)
        data = _json(resp)
        if isinstance(data, dict):
            req = data.get("req_0") or {}
            if req.get("code") == 2001 and attempt + 1 < retries:
                _time.sleep(2 ** (attempt + 1))
                continue
            songs = ((req.get("data") or {}).get("body") or {}).get("song") or {}
            items = songs.get("list") if isinstance(songs, dict) else None
            return items if isinstance(items, list) else []
    return []


def _decode_html(text: str) -> str:
    return (str(text or "").replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))


def _qq_candidate(item: dict, connector: str) -> TagCandidate:
    album = item.get("album") or {}
    singers = item.get("singer") or []
    names = [s.get("name", "") for s in singers if isinstance(s, dict) and s.get("name")]
    mid = str(album.get("mid") or "")
    cover = f"https://y.qq.com/music/photo_new/T002R800x800M000{mid}.jpg" if mid else ""
    genre_id = _to_int(item.get("genre") or 0)
    return TagCandidate(
        source="qq", source_id=str(item.get("id") or item.get("mid") or ""),
        qq_mid=str(item.get("mid") or ""),
        title=_decode_html(item.get("title") or item.get("name") or ""),
        original_title=_decode_html(item.get("name") or ""),
        artist=connector.join(names),
        album=_decode_html(album.get("name") or ""),
        year=_year_of_text(item.get("time_public") or ""),
        track=_to_int(item.get("index_album") or 0),
        disc=_to_int(item.get("index_cd") or 0),
        genre=_QQ_GENRES.get(genre_id, ""),
        comment=_decode_html(item.get("subtitle") or ""),
        cover_url=cover, duration=_to_int(item.get("interval") or 0) * 1000,
    )


def search_qq(query: str, limit: int = 15, connector: str = DEFAULT_CONNECTOR) -> list[TagCandidate]:
    out = []
    for item in _qq_search_raw(query, limit):
        if not isinstance(item, dict):
            continue
        cand = _qq_candidate(item, connector)
        if cand.source_id and cand.album:
            out.append(cand)
    return out


# ==================== 酷狗 ====================

def _kugou_headers() -> dict:
    return {
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/webp,image/apng,*/*;q=0.8"),
    }


def _kugou_search_raw(query: str, limit: int) -> list[dict]:
    with _client(headers=_kugou_headers()) as client:
        resp = client.get(
            "http://mobilecdn.kugou.com/api/v3/search/song",
            params={"format": "json", "keyword": query, "page": 1,
                    "pagesize": limit, "showtype": 1})
    data = _json(resp)
    info = ((data or {}).get("data") or {}).get("info")
    return info if isinstance(info, list) else []


def _kugou_candidate(item: dict, connector: str) -> TagCandidate:
    return TagCandidate(
        source="kugou", source_id=str(item.get("audio_id") or item.get("hash") or ""),
        kugou_hash=str(item.get("hash") or ""),
        title=str(item.get("songname") or ""),
        artist=str(item.get("singername") or ""),
        album=str(item.get("album_name") or ""),
        duration=_to_int(item.get("duration") or 0) * 1000,
    )


def search_kugou(query: str, limit: int = 15, connector: str = DEFAULT_CONNECTOR) -> list[TagCandidate]:
    return [_kugou_candidate(x, connector) for x in _kugou_search_raw(query, limit)
            if isinstance(x, dict) and (x.get("audio_id") or x.get("hash"))]


# ==================== 酷我 ====================

def _kuwo_headers() -> dict:
    return {
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/webp,image/apng,*/*;q=0.8"),
        "referer": "https://kuwo.cn",
    }


def _kuwo_search_raw(query: str, limit: int) -> list[dict]:
    url = ("https://search.kuwo.cn/r.s?all={q}&client=kt&pn=0&rn={n}"
           "&ver=kwplayer_ar_9.2.3.2&vipver=1&show_copyright_off=1&newver=1"
           "&correct=1&ft=music&cluster=0&strategy=2012&encoding=utf8"
           "&rformat=json&vermerge=1&mobi=1&issubtitle=1").format(
               q=_urlencode(query), n=limit)
    with _client(headers=_kuwo_headers()) as client:
        resp = client.get(url)
    data = _json(resp)
    items = (data or {}).get("abslist")
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("ALBUM"):
            out.append(item)
            continue
        children = item.get("SUBLIST")
        if isinstance(children, list):
            for child in children:
                if (isinstance(child, dict)
                        and child.get("SONGNAME") == item.get("SONGNAME")
                        and child.get("ARTISTID") == item.get("ARTISTID")
                        and child.get("ALBUM")):
                    out.append(child)
    return out


def _kuwo_cover(short: str) -> str:
    s = (short or "").strip()
    if not s:
        return ""
    s = re.sub(r"^\d+/", "500/", s)
    if s.startswith("http"):
        return s
    return "https://img2.kuwo.cn/star/albumcover/" + s.lstrip("/")


def _kuwo_candidate(item: dict, connector: str) -> TagCandidate:
    rid = re.sub(r"^MUSIC_", "", str(item.get("MUSICRID") or ""))
    return TagCandidate(
        source="kuwo", source_id=rid,
        title=_decode_html(item.get("SONGNAME") or ""),
        original_title=_decode_html(item.get("NAME") or ""),
        artist=_decode_html(item.get("ARTIST") or ""),
        album=_decode_html(item.get("ALBUM") or ""),
        duration=_to_int(item.get("DURATION") or 0) * 1000,
        cover_url=_kuwo_cover(item.get("web_albumpic_short") or ""),
    )


def search_kuwo(query: str, limit: int = 8, connector: str = DEFAULT_CONNECTOR) -> list[TagCandidate]:
    return [_kuwo_candidate(x, connector) for x in _kuwo_search_raw(query, limit)
            if isinstance(x, dict)]


# ==================== 歌词 ====================

def _netease_lyric(candidate: TagCandidate) -> tuple[str, str]:
    with _client(headers={"Referer": "https://music.163.com/",
                          "X-Requested-With": "XMLHttpRequest"}) as client:
        resp = client.get("https://music.163.com/api/song/lyric",
                          params={"os": "pc", "id": candidate.source_id,
                                  "lv": -1, "kv": -1, "tv": -1})
    data = _json(resp)
    if not isinstance(data, dict):
        return "", ""
    lyric = ((data.get("lrc") or {}).get("lyric") or "")
    trans = ((data.get("tlyric") or {}).get("lyric") or "")
    return ("" if lyric == "null" else lyric,
            "" if trans == "null" else trans)


def _qq_lyric(candidate: TagCandidate) -> tuple[str, str]:
    if not candidate.qq_mid:
        return "", ""
    with _client(headers={"Referer": "https://y.qq.com/"}) as client:
        resp = client.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                          params={"songmid": candidate.qq_mid, "g_tk": 5381,
                                  "jsonpCallback": _QQ_CALLBACK, "format": "jsonp"})
    text = resp.text or ""
    m = re.search(re.escape(_QQ_CALLBACK) + r"\((.+)\)\s*$", text.strip(), re.S)
    if not m:
        return "", ""
    data = _json_text(m.group(1))
    if not isinstance(data, dict):
        return "", ""
    encoded = data.get("lyric") or ""
    encoded_trans = data.get("trans") or ""
    if encoded == "null":
        encoded = ""
    if encoded_trans == "null":
        encoded_trans = ""
    if encoded == _QQ_EMPTY_LYRIC:
        return "", ""
    lyric = _b64_text(encoded)
    trans = _b64_text(encoded_trans)
    return _decode_html(lyric), _decode_html(trans)


def _kugou_lyric(candidate: TagCandidate) -> tuple[str, str]:
    keyword = f"{candidate.title.strip()} - {candidate.artist.strip()}".strip(" -")
    if not candidate.kugou_hash:
        return "", ""
    with _client(headers=_kugou_headers()) as client:
        resp = client.get("https://m3ws.kugou.com/api/v1/krc/get_krc",
                          params={"keyword": keyword, "hash": candidate.kugou_hash,
                                  "timelength": candidate.duration})
    data = _json(resp)
    if not isinstance(data, dict):
        return "", ""
    lrc = ((data.get("data") or {}).get("lrc") or "")
    return str(lrc), ""


def _kuwo_lyric(candidate: TagCandidate) -> tuple[str, str]:
    if not candidate.source_id:
        return "", ""
    with _client(headers=_kuwo_headers()) as client:
        resp = client.get("https://m.kuwo.cn/newh5/singles/songinfoandlrc",
                          params={"musicId": candidate.source_id})
    data = _json(resp)
    if not isinstance(data, dict):
        return "", ""
    payload = data.get("data") or {}
    info = payload.get("songinfo") or {}
    if info.get("pic"):
        candidate.cover_url = _kuwo_cover(str(info["pic"]).replace("http://", "https://"))
    lines = payload.get("lrclist") or []
    if not isinstance(lines, list):
        return "", ""
    grouped: dict[int, list[str]] = {}
    for line in lines:
        if not isinstance(line, dict):
            continue
        try:
            ms = int(float(line.get("time") or 0) * 1000)
        except (TypeError, ValueError):
            continue
        text = _decode_html(str(line.get("lineLyric") or ""))
        grouped.setdefault(ms, []).append(text)
    if not grouped:
        return "", ""
    primary, trans = [], []
    for ms in sorted(grouped):
        texts = [t for t in grouped[ms] if t.strip()]
        if not texts:
            continue
        stamp = _fmt_lrc_time(ms)
        main = texts[0]
        others = texts[1:]
        if others and not _has_chinese(main) and _has_chinese(others[0]):
            main, others = others[0], [main] + others[1:]
        primary.append(f"{stamp}{main}")
        if others:
            trans.append(f"{stamp}{' '.join(others)}")
    return "\n".join(primary), "\n".join(trans)


def load_lyric(candidate: TagCandidate) -> tuple[str, str]:
    """返回 (原文 LRC, 翻译 LRC)。"""
    try:
        if candidate.source == "netease":
            return _netease_lyric(candidate)
        if candidate.source == "qq":
            return _qq_lyric(candidate)
        if candidate.source == "kugou":
            return _kugou_lyric(candidate)
        if candidate.source == "kuwo":
            return _kuwo_lyric(candidate)
    except Exception:
        return "", ""
    return "", ""


def merge_lyrics(lyric: str, translated: str) -> str:
    """把翻译按时间轴插到原文下一行（musictag 的 MergeDownloadedLyrics 简化版）。"""
    if not translated:
        return lyric
    trans_map: dict[str, str] = {}
    for line in translated.splitlines():
        m = re.match(r"(\[[^\]]+\])(.*)", line.strip())
        if m and m.group(2).strip():
            trans_map[m.group(1)] = m.group(2).strip()
    if not trans_map:
        return lyric
    out = []
    for line in lyric.splitlines():
        out.append(line)
        m = re.match(r"(\[[^\]]+\])(.*)", line.strip())
        if m and m.group(1) in trans_map:
            out.append(f"{m.group(1)}{trans_map[m.group(1)]}")
    return "\n".join(out)


def _b64_text(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", "replace")
    except Exception:
        return value


def _json_text(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _fmt_lrc_time(ms: int) -> str:
    total, millis = divmod(max(0, int(ms)), 1000)
    m, s = divmod(total, 60)
    return f"[{m:02d}:{s:02d}.{millis // 10:02d}]"


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


# ==================== 封面 ====================

def download_cover(url: str, timeout: float = 15.0) -> tuple[bytes, str]:
    url = (url or "").strip()
    if not url:
        return b"", ""
    headers = {"User-Agent": UA}
    ref = referer_for(url)
    if ref:
        headers["Referer"] = ref
    try:
        with _client(timeout=timeout, headers=headers) as client:
            resp = client.get(url)
    except Exception:
        return b"", ""
    if resp.status_code != 200:
        return b"", ""
    data = resp.content
    if len(data) < 500:
        return b"", ""
    mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if "png" in mime:
        mime = "image/png"
    elif "gif" in mime:
        mime = "image/gif"
    elif "webp" in mime:
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    if mime == "image/webp":
        converted = _webp_to_jpeg(data)
        if converted:
            data, mime = converted, "image/jpeg"
    return data, mime


def _webp_to_jpeg(data: bytes) -> bytes:
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        out = BytesIO()
        img.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:
        return b""


# ==================== 相似度与排序 ====================

_ARTIST_SEP = re.compile(r"[/&,]|，|、")
_PUNCT = {"（": "(", "）": ")", "！": "!", "？": "?", "…": "...", "，": ",",
          "。": ".", "：": ":", "．": "."}


def normalize_for_match(text: str) -> str:
    text = str(text or "")
    for a, b in _PUNCT.items():
        text = text.replace(a, b)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[:] +", ":", text)
    text = re.sub(r"[,] +", ",", text)
    text = re.sub(r"[.] +", ".", text)
    text = re.sub(r"[!] +", "!", text)
    text = re.sub(r"[?] +", "?", text)
    text = re.sub(r" +[/]", "/", text)
    text = re.sub(r"[/] +", "/", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip().lower()


def _levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if ca.upper() == cb.upper() else 1)))
        prev = cur
    return prev[-1]


def text_similarity(reference: str, candidate: str) -> float:
    max_len = max(len(reference or ""), len(candidate or ""))
    if max_len <= 0:
        return 0.0
    return 1.0 - _levenshtein(reference or "", candidate or "") / max_len


def artist_similarity(reference: str, candidate: str) -> float:
    sim = text_similarity(reference, candidate)
    if sim >= 0.6:
        return sim
    parts = [p for p in _ARTIST_SEP.split(candidate or "") if p.strip()]
    if len(parts) > 1:
        for part in parts:
            sim = max(text_similarity(reference, part) - 0.01, sim)
    return sim


def calculate_scores(title: str, artist: str, album: str,
                     cand: TagCandidate) -> tuple[float, float, float]:
    t = normalize_for_match(title)
    a = normalize_for_match(artist)
    al = normalize_for_match(album)
    ct = normalize_for_match(cand.title)
    ca = normalize_for_match(cand.artist)
    cal = normalize_for_match(cand.album)
    co = normalize_for_match(cand.original_title)
    if not a:
        # 目标没有艺术家：把「标题 艺术家 / 艺术家 标题」也纳入标题比较
        title_only = ct
        ct = f"{title_only} {ca}".strip()
        variants = [title_only, f"{ca} {title_only}".strip()]
        if co:
            variants += [co, f"{co} {ca}".strip(), f"{ca} {co}".strip()]
        ca = ""
        title_score = max([text_similarity(t, ct)] + [text_similarity(t, v) for v in variants if v])
    else:
        title_score = text_similarity(t, ct)
        for alt in (co,):
            if alt:
                title_score = max(title_score, text_similarity(t, alt))
    artist_score = artist_similarity(a, ca)
    album_score = text_similarity(al, cal)
    return title_score, artist_score, album_score


def _contains_either_way(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a in b or b in a)


def _is_instrumental(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in ("instrumental", "off vocal", "伴奏", "纯音乐"))


def rank_candidates(candidates: list[TagCandidate], title: str, artist: str,
                    album: str, used_filename: bool = False) -> list[TagCandidate]:
    if not candidates:
        return []
    for cand in candidates:
        cand.scores = calculate_scores(title, artist, album, cand)
    ranked = sorted(candidates, key=lambda c: (
        -c.scores[0], -c.scores[1], -c.scores[2],
        c.source_order, c.search_pass, c.result_order))
    ranked = _promote_best(ranked, title, artist, album)
    if used_filename and " - " in (title or ""):
        ranked = _filename_fallback(ranked, title)
    return ranked


def _promote_best(ranked: list[TagCandidate], title: str, artist: str,
                  album: str) -> list[TagCandidate]:
    if not ranked:
        return ranked
    t = normalize_for_match(title)
    a = normalize_for_match(artist)
    al = normalize_for_match(album)
    best = ranked[0]
    best_title = normalize_for_match(best.title)
    best_comp = normalize_for_match(best.original_title or best.title)
    best_artist = normalize_for_match(best.artist)
    best_album = normalize_for_match(best.album)

    def move_to_front(idx: int) -> list[TagCandidate]:
        return [ranked[idx]] + ranked[:idx] + ranked[idx + 1:]

    # 伴奏/纯音乐变体：优先换回非伴奏母版
    if not _is_instrumental(t) and _is_instrumental(best_title):
        for i, cand in enumerate(ranked[1:], 1):
            ct = normalize_for_match(cand.title)
            if (not _is_instrumental(ct)
                    and normalize_for_match(cand.artist) == best_artist
                    and ct and best_title.startswith(ct)):
                return move_to_front(i)

    if a:
        # 艺术家不够强时，优先提升「艺术家强匹配 + 标题/专辑命中」的候选
        if ranked[0].scores[1] < 0.8:
            for i, cand in enumerate(ranked[1:], 1):
                if cand.scores[1] < 0.8:
                    continue
                ct = normalize_for_match(cand.title)
                ccomp = normalize_for_match(cand.original_title or cand.title)
                cal = normalize_for_match(cand.album)
                if _contains_either_way(ccomp, t) and cal and al and _contains_either_way(cal, al):
                    return move_to_front(i)
            for i, cand in enumerate(ranked[1:], 1):
                if cand.scores[1] < 0.8:
                    continue
                ccomp = normalize_for_match(cand.original_title or cand.title)
                if _contains_either_way(ccomp, t):
                    return move_to_front(i)
        # 当前最佳标题/专辑与目标明显不符，而后面有强艺术家候选
        if (not _contains_either_way(best_comp, t)
                and ranked[0].scores[0] < 0.8):
            for i, cand in enumerate(ranked[1:], 1):
                if (cand.scores[1] >= 0.8
                        and _contains_either_way(normalize_for_match(cand.artist), a)
                        and _contains_either_way(normalize_for_match(cand.original_title or cand.title), t)):
                    return move_to_front(i)
    if al:
        # 专辑命中优先
        for i, cand in enumerate(ranked):
            cal = normalize_for_match(cand.album)
            ccomp = normalize_for_match(cand.original_title or cand.title)
            if cal and _contains_either_way(cal, al) and _contains_either_way(ccomp, t):
                if i:
                    return move_to_front(i)
                break
    return ranked


def _filename_fallback(ranked: list[TagCandidate], filename_title: str) -> list[TagCandidate]:
    """文件名是「艺术家 - 标题」时，按拆分后的标题/艺术家再排一次；可信才采用。"""
    parts = filename_title.split(" - ", 1)
    if len(parts) != 2:
        return ranked
    artist_part, title_part = parts[0].strip(), parts[1].strip()
    if not artist_part or not title_part:
        return ranked
    fb = []
    for cand in ranked:
        cand.scores = calculate_scores(title_part, artist_part, "", cand)
        fb.append(cand)
    fb.sort(key=lambda c: (-c.scores[0], -c.scores[1], -c.scores[2],
                           c.source_order, c.search_pass, c.result_order))
    fb = _promote_best(fb, title_part, artist_part, "")
    best = fb[0]
    title_ok = (_contains_either_way(title_part.lower(), best.title.lower())
                and best.scores[0] >= 0.5)
    artist_ok = (_contains_either_way(artist_part.lower(), best.artist.lower())
                 and best.scores[1] >= 0.5)
    strong = best.scores[0] >= 0.8 and best.scores[1] >= 0.8
    return fb if (title_ok and artist_ok) or strong else ranked


# ==================== 多源搜索 ====================

_SOURCE_FUNCS = {
    "netease": search_netease,
    "qq": search_qq,
    "kugou": search_kugou,
    "kuwo": search_kuwo,
}


def search_tracks(title: str, artist: str = "", album: str = "",
                  sources: list[str] | None = None, *, used_filename: bool = False,
                  connector: str = DEFAULT_CONNECTOR,
                  errors: dict | None = None,
                  progress=None) -> list[TagCandidate]:
    """多源多趟搜索并排序（对齐 musictag SearchCurrentContextTracks）。"""
    sources = [s for s in (sources or DEFAULT_SOURCES) if s in _SOURCE_FUNCS]
    title = (title or "").strip()
    artist = (artist or "").strip()
    album = (album or "").strip()
    if not title:
        return []

    seen: set[tuple[str, str]] = set()
    all_candidates: list[TagCandidate] = []
    pass_no = 0

    for order, source in enumerate(sources):
        func = _SOURCE_FUNCS[source]
        queries: list[tuple[str, int]] = []
        if source in ("netease", "qq"):
            queries.append((f"{title} {artist}".strip(), 15))
            if artist:
                queries.append((title, 10))
            if album and album != title:
                queries.append((f"{album} {artist}".strip(), 8))
        elif source == "kuwo":
            queries.append((f"{title} {artist}".strip(), 8))
        else:  # kugou
            queries.append((f"{title} {artist}".strip(), 15))
            if artist:
                queries.append((title, 10))

        got_any = False

        def run_query(query: str, limit: int, q_index: int):
            nonlocal got_any
            if not query:
                return
            if progress:
                try:
                    progress(f"搜索 {SOURCE_NAMES.get(source, source)}：{query}")
                except Exception:
                    pass
            try:
                results = func(query, limit, connector)
            except Exception as exc:
                if errors is not None:
                    errors[source] = f"{type(exc).__name__}: {exc}"
                results = []
            for r_index, cand in enumerate(results):
                key = (cand.source, cand.source_id)
                if not cand.source_id or key in seen:
                    continue
                seen.add(key)
                cand.source_order = order
                cand.search_pass = pass_no + q_index
                cand.result_order = r_index
                all_candidates.append(cand)
                got_any = True

        for q_index, (query, limit) in enumerate(queries):
            run_query(query, limit, q_index)
        # 酷我：首趟无结果时按「专辑 + 艺术家」再搜一次（musictag 行为）
        if source == "kuwo" and not got_any and album and album != title:
            run_query(f"{album} {artist}".strip(), 8, len(queries))
        pass_no += len(queries) + (1 if source == "kuwo" else 0)

    return rank_candidates(all_candidates, title, artist, album, used_filename)


def auto_match(title: str, artist: str = "", album: str = "",
               sources: list[str] | None = None, *, used_filename: bool = False,
               need_lyric: bool = True, need_cover: bool = True,
               connector: str = DEFAULT_CONNECTOR,
               progress=None) -> MatchResult:
    errors: dict[str, str] = {}
    ranked = search_tracks(title, artist, album, sources, used_filename=used_filename,
                           connector=connector, errors=errors, progress=progress)
    result = MatchResult(ranked=ranked, errors=errors)
    if not ranked:
        return result
    best = ranked[0]
    result.candidate = best

    if need_lyric:
        if progress:
            progress(f"获取歌词：{best.title}")
        best.lyric, best.lyric_trans = load_lyric(best)
        if best.lyric and best.lyric_trans:
            best.lyric = merge_lyrics(best.lyric, best.lyric_trans)
        if not best.lyric and len(ranked) > 1 and _same_track(ranked[1], best):
            ranked[1].lyric, ranked[1].lyric_trans = load_lyric(ranked[1])
            if ranked[1].lyric:
                if ranked[1].lyric_trans:
                    ranked[1].lyric = merge_lyrics(ranked[1].lyric, ranked[1].lyric_trans)
                result.candidate = ranked[1]
                best = ranked[1]

    if need_cover:
        urls = []
        for cand in [best] + [c for c in ranked[1:] if _same_track(c, best)]:
            if cand.cover_url and cand.cover_url not in urls:
                urls.append(cand.cover_url)
        for url in urls[:2]:
            if progress:
                progress(f"下载封面：{best.title}")
            data, mime = download_cover(url)
            if data:
                result.cover, result.cover_mime = data, mime
                break
    return result


def _same_track(a: TagCandidate, b: TagCandidate) -> bool:
    return (normalize_for_match(a.title) == normalize_for_match(b.title)
            and normalize_for_match(a.artist) == normalize_for_match(b.artist)
            and normalize_for_match(a.album) == normalize_for_match(b.album))


# ==================== 工具 ====================

def _to_int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _year_of_text(value: str) -> str:
    m = re.search(r"(\d{4})", str(value or ""))
    return m.group(1) if m else ""


def _year_from_ts(value) -> str:
    try:
        ms = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y")
    except (OSError, ValueError):
        return ""


def format_duration(ms: int) -> str:
    total = max(0, int(ms or 0)) // 1000
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def build_search_keyword(title: str, artist: str) -> str:
    """网络搜索关键词：标题 + 艺术家。"""
    return f"{title} {artist}".strip()


def clean_filename_keyword(stem: str) -> str:
    """按 musictag 的文件名回退：去掉音质后缀，'艺术家 - 标题' → '标题 艺术家'。"""
    s = re.sub(r"\s*[\[(](FLAC|320K|320kbps|24bit|96kHz|44\.1kHz)[\])]\s*", "", stem or "", flags=re.I)
    parts = [p.strip() for p in re.split(r"\s+-\s+", s) if p.strip()]
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}"
    return s
