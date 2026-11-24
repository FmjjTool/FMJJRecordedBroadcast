"""5sing 歌单写入（网页 songlist 接口，JSONP）。

协议来源：`/my/writing/songlist`（歌单管理）页面的 `songlist_v3.js`：

- 创建/修改：`GET {service}/songlist/songlistadd?jsoncallback=&title=&pic=&content=&label=&id=`
- 加歌：`GET {service}/songlist/addsongtolist?jsoncallback=&ZjID=&IDType=,yc$123,fc$456`
- 封面：`POST {service}/upload/pic?type=album` 表单上传，返回 `data.file`

平台约束：歌单必须有封面、至少一个标签；歌曲标识是「类型$歌曲ID」
（类型取 yc/fc/bz），因此写入前要把曲库里的条目按歌名+歌手回查 5sing 自己的 ID。
所有请求都只读 `auth` 提供的当前登录态，不回写凭据。
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Optional

import httpx

from ..models import SongInfo
from .base import (
    AddSongsResult,
    AuthRequiredError,
    BasePlaylistWriter,
    ParseChangedError,
    PlatformRejectedError,
    RateLimitedError,
)

SERVICE = "http://service.5sing.kugou.com"
MAIN = "https://5sing.kugou.com"
SEARCH_PAGE = "https://5ssearch.kugou.com/home/index"
MANAGE_PAGE = f"{MAIN}/my/writing/songlist"

# 歌单标签只能取平台给定的词表；「流行」是最通用的一个
DEFAULT_LABEL = "流行"
DEFAULT_CONTENT = "由封茗囧菌数据汇总库创建"

_SONG_KEY_RE = re.compile(r"^(yc|fc|bz)\$(\d+)$")
_SONG_URL_RE = re.compile(r"5sing\.kugou\.com/(?:m/detail/)?(yc|fc|bz)/(\d+)\.html")
_JSONP_RE = re.compile(r"^\s*[\w$.]+\((.*)\)\s*;?\s*$", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ALBUM_ID_RE = re.compile(r'albumid\s*=\s*"([0-9a-fA-F]{16,32})"')
_ALBUM_TITLE_RE = re.compile(r'class="s_title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S)
_DATA_LIST_RE = re.compile(r"var\s+dataList\s*=\s*'(.*?)'\s*;", re.S)


def _json_object(text: str) -> dict:
    """平台返回体有两种壳：JSONP `cb({...})` 与 HTML 里嵌 `{...}`，都取出对象。"""
    raw = (text or "").strip()
    match = _JSONP_RE.match(raw)
    if match:
        raw = match.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    body = json.loads(raw[start:end + 1])
    if not isinstance(body, dict):
        raise ValueError("not an object")
    return body


def _plain(text: str) -> str:
    """去掉搜索结果的 <em> 高亮与多余空白，统一成可比较文本。"""
    value = _TAG_RE.sub("", text or "")
    return " ".join(value.split()).lower()


def _similar(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _title_matches(shown: str, wanted: str) -> bool:
    """歌单页会把「测试」这类词打码成 **，比较时按通配符处理。"""
    if _plain(shown) == _plain(wanted):
        return True
    pattern = "".join("." if ch == "*" else re.escape(ch) for ch in _plain(shown))
    return bool(pattern) and re.fullmatch(pattern, _plain(wanted)) is not None


class FiveSingPlaylistWriter(BasePlaylistWriter):
    platform = "5sing"
    platform_name = "5sing"
    supports_create = True
    verified = True
    supports_sort = False
    note = "创建/加歌已实测通过；封面与标签自动补齐，歌曲按歌名回查 5sing ID"
    batch_size = 20

    # ------------------------------------------------------------------
    # 底层请求
    def _jsonp(self, client: httpx.Client, path: str, params: dict,
               phase: str) -> dict:
        """调用 JSONP 接口并解析花括号对象；非 JSONP 或空响应归为结构变化。"""
        query = dict(params)
        query["jsoncallback"] = "cb"
        resp = client.get(f"{SERVICE}{path}", params=query)
        text = (resp.text or "").strip()
        try:
            return _json_object(text)
        except Exception as exc:  # noqa: BLE001 - 统一转类型化错误
            snippet = " ".join(text.split())[:120] or "空响应"
            raise ParseChangedError(
                f"平台返回内容无法解析（HTTP {resp.status_code}）：{snippet}",
                platform=self.platform, phase=phase,
            ) from exc

    def _ensure_login(self, client: httpx.Client, phase: str) -> None:
        """歌单管理页只有登录后才带 currentUserId；否则是跳转占位页。"""
        resp = client.get(MANAGE_PAGE)
        text = resp.text or ""
        if not re.search(r"var\s+currentUserId\s*=\s*\d+", text):
            raise AuthRequiredError(
                "5sing 登录状态不可用，请先在账号管理里重新登录",
                platform=self.platform, phase=phase,
            )

    # ------------------------------------------------------------------
    # 封面：歌单必须带封面，这里生成一张纯色方图上传
    def _cover_bytes(self) -> bytes:
        try:
            from io import BytesIO

            from PIL import Image

            image = Image.new("RGB", (320, 320), (70, 130, 120))
            for y in range(320):
                shade = int(70 + 90 * (y / 320))
                image.paste((shade - 30, shade, shade - 20), (0, y, 320, y + 1))
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception:  # noqa: BLE001 - 缺 Pillow 时用内置 1×1 PNG 兜底
            import base64

            return base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
                "YGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")

    def _upload_cover(self, client: httpx.Client) -> str:
        resp = client.post(
            f"{SERVICE}/upload/pic",
            params={"back": f"{MAIN}/my/handler/getimgwithaccess",
                    "tm": "force", "type": "album"},
            files={"album_fileUpload": ("cover.png", self._cover_bytes(), "image/png")},
            headers={"Referer": MANAGE_PAGE, "X-Requested-With": "XMLHttpRequest"},
        )
        text = (resp.text or "").strip()
        try:
            body = _json_object(text)
        except Exception as exc:  # noqa: BLE001
            raise ParseChangedError(
                f"封面上传返回无法解析：{' '.join(text.split())[:120] or '空响应'}",
                platform=self.platform, phase="create",
            ) from exc
        data = body.get("data")
        path = (data or {}).get("file") if isinstance(data, dict) else ""
        if not path or body.get("isSuccess") is False:
            message = body.get("message") or "封面上传失败"
            raise PlatformRejectedError(
                f"上传歌单封面失败：{message}",
                platform=self.platform, phase="create",
            )
        return str(path)

    # ------------------------------------------------------------------
    # 歌曲 ID 回查：曲库条目多为共享库 hash，写入需要 5sing 自己的「类型$ID」
    def _search_native(self, client: httpx.Client, keyword: str) -> list[dict]:
        resp = client.get(SEARCH_PAGE, params={"keyword": keyword})
        match = _DATA_LIST_RE.search(resp.text or "")
        if not match:
            return []
        try:
            body = match.group(1).strip()
            try:
                rows = json.loads(body)
            except json.JSONDecodeError:
                # 页面里是 '转义后的 JSON 字符串'，先解一层字符串再解析
                rows = json.loads(json.loads('"' + body + '"'))
        except Exception as exc:  # noqa: BLE001 - 结构变化时按未找到处理
            raise ParseChangedError(
                "5sing 搜索结果结构变化，无法定位歌曲 ID",
                platform=self.platform, phase="add",
            ) from exc
        return [row for row in rows if isinstance(row, dict) and row.get("songId")]

    def resolve_song_key(self, client: httpx.Client, song: SongInfo) -> str:
        """把一条曲目换成 `yc$123456` 形式；找不到时返回空串。"""
        song_id = (song.song_id or "").strip()
        match = _SONG_KEY_RE.match(song_id)
        if match:
            return song_id
        match = _SONG_URL_RE.search(song.url or "") or _SONG_URL_RE.search(song_id)
        if match:
            return f"{match.group(1)}${match.group(2)}"

        title = _plain(song.song_name or song.title)
        singer = _plain(song.singer)
        if not title:
            return ""
        rows = self._search_native(client, f"{(song.song_name or song.title)} {song.singer or ''}".strip())
        best_key, best_score = "", 0.0
        for row in rows:
            row_title = _plain(str(row.get("songName") or ""))
            row_singer = _plain(str(row.get("singer") or row.get("nickName") or ""))
            score = _similar(title, row_title)
            if score < 0.62:
                continue
            if singer and row_singer:
                score = score * 0.7 + _similar(singer, row_singer) * 0.3
            if score > best_score:
                best_score = score
                best_key = f"{row.get('typeEname')}${row.get('songId')}"
        return best_key if best_score >= 0.62 else ""

    # ------------------------------------------------------------------
    def _find_playlist_id(self, client: httpx.Client, title: str) -> str:
        """歌单管理页按标题取最新一个；songlistadd 不回传 ID。"""
        resp = client.get(MANAGE_PAGE)
        # 按 <li> 切块后再配对，避免跨卡片把 ID 和别人的标题连在一起
        for chunk in re.split(r"<li\b", resp.text or ""):
            id_match = _ALBUM_ID_RE.search(chunk)
            title_match = _ALBUM_TITLE_RE.search(chunk)
            if not (id_match and title_match):
                continue
            if _title_matches(title_match.group(1), title):
                return id_match.group(1)
        return ""

    def create_playlist(self, name: str, cookie: dict[str, str], *,
                        transport: Optional[httpx.BaseTransport] = None) -> str:
        playlist_title = (name or "").strip()
        if not playlist_title:
            raise PlatformRejectedError(
                "歌单名不能为空", platform=self.platform, phase="create")
        with self._client(cookie, transport) as client:
            self._ensure_login(client, "create")
            pic = self._upload_cover(client)
            body = self._jsonp(client, "/songlist/songlistadd", {
                "title": playlist_title,
                "pic": pic,
                "content": DEFAULT_CONTENT,
                "label": DEFAULT_LABEL,
                "id": "",
            }, "create")
            code = body.get("code")
            if code != 0:
                message = body.get("msg") or body.get("message") or ""
                if "登录" in str(message):
                    raise AuthRequiredError(
                        "5sing 登录状态不可用，请先在账号管理里重新登录",
                        platform=self.platform, phase="create")
                raise PlatformRejectedError(
                    f"创建歌单失败（错误码 {code}）{'：' + str(message) if message else ''}",
                    platform=self.platform, phase="create")
            playlist_id = self._find_playlist_id(client, playlist_title)
            if not playlist_id:
                raise ParseChangedError(
                    "歌单创建成功但未能取回歌单 ID，请到 5sing「歌单管理」确认后重试",
                    platform=self.platform, phase="create")
            return playlist_id

    def add_songs(self, playlist_id: str, songs: list[SongInfo],
                  cookie: dict[str, str], *,
                  transport: Optional[httpx.BaseTransport] = None,
                  progress=None) -> AddSongsResult:
        result = AddSongsResult()
        if not songs:
            return result
        with self._client(cookie, transport) as client:
            self._ensure_login(client, "add")
            keys: list[tuple[str, str]] = []
            for song in songs:
                label = song.song_name or song.title or song.song_id
                try:
                    key = self.resolve_song_key(client, song)
                except ParseChangedError as exc:
                    result.failed.append((label, str(exc)))
                    continue
                if key:
                    keys.append((key, label))
                else:
                    result.failed.append((label, "5sing 曲库未找到这首歌"))

            total = len(keys)
            done = 0
            for start in range(0, total, self.batch_size):
                batch = keys[start:start + self.batch_size]
                body = self._jsonp(client, "/songlist/addsongtolist", {
                    "ZjID": playlist_id,
                    "IDType": "," + ",".join(key for key, _ in batch),
                }, "add")
                code = body.get("code")
                message = str(body.get("msg") or body.get("message") or "")
                if code == 0:
                    result.added.extend(key for key, _ in batch)
                elif "频繁" in message:
                    raise RateLimitedError(
                        "5sing 提示操作过于频繁，请稍后再试",
                        platform=self.platform, phase="add", retryable=True)
                elif "登录" in message:
                    raise AuthRequiredError(
                        "5sing 登录状态不可用，请先在账号管理里重新登录",
                        platform=self.platform, phase="add")
                else:
                    detail = f"：{message}" if message else f"（错误码 {code}）"
                    for _, label in batch:
                        result.failed.append((label, f"添加失败{detail}"))
                done += len(batch)
                if progress:
                    progress(done, total)
        return result
