"""账号评论索引：从本地评论区导出中筛选账号本人评论，生成评论区图片。

数据来源（只读）：
  download/评论区/{平台}/{作品ID}/comments.json
  由「视频评论区」页的「保存原始数据」写入；只读，不改评论文件。

输出（派生、可重建）：
  download/腾讯文档数据/中间层/评论区图片/{平台}/{作品ID}/账号评论索引_*.png
  由本模块唯一写入；删除后会在下次上传时重新生成。

只取封茗囧菌本人的顶层评论，按平台账号 ID 精确匹配（昵称相同不算）；
评论中的原图由 import_pending 下载到中间层图片缓存，不写用户媒体库。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from toolbox import bili_comments, douyin_comments, weibo_comments
from toolbox.config_manager import get_export_subdir
from toolbox.tcdoc import config as tc_cfg
from toolbox.tcdoc.import_local import weibo_key
from toolbox.utils import sanitize_filename

COMMENT_FIELD_TITLE = "评论区图片"
FILTER_DESC = "仅封茗囧菌本人评论"

PLATFORM_INFO = {
    "bilibili": {
        "name": "B站",
        "dir": "B站",
        "backend": bili_comments,
        "aliases": ("bilibili", "b站", "哔哩哔哩"),
    },
    "douyin": {
        "name": "抖音",
        "dir": "抖音",
        "backend": douyin_comments,
        "aliases": ("douyin", "抖音"),
    },
    "weibo": {
        "name": "微博",
        "dir": "微博",
        "backend": weibo_comments,
        "aliases": ("weibo", "微博"),
    },
}

# 主账号 ID 兜底；账号追踪可用时会把同名主账号的 ID 一并合并进来。
DEFAULT_IDENTITIES = {
    "bilibili": {"1467772"},
    "douyin": {"MS4wLjABAAAA0Wk4gxp3AYFnqoqo-IBF6lbdLnrxgjy__DdhPBNBkws", "78087831246"},
    "weibo": {"3056354293"},
}
MAIN_ACCOUNT_NAMES = {
    "bilibili": {"封茗囧菌b站", "封茗囧菌B站", "封茗囧菌b站动态", "封茗囧菌B站动态"},
    "douyin": {"封茗囧菌抖音"},
    "weibo": {"封茗囧菌微博"},
}

_IDENTITIES_CACHE: dict[str, set[str]] | None = None
_SAFE_ID = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
_BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]+)")
_AV_RE = re.compile(r"/video/av(\d+)", re.I)
_OPUS_RE = re.compile(r"/(?:opus|dynamic)/(\d+)")
_DY_VIDEO_RE = re.compile(r"/(?:video|note|slides)/(\d+)")
_WB_STATUS_RE = re.compile(r"/(?:status|detail)/([A-Za-z0-9]+)")
_WB_TV_RE = re.compile(r"/tv/show/([\d:]+)")
_WB_USER_BID_RE = re.compile(r"^/\d+/([A-Za-z0-9]{8,16})/?$")


@dataclass
class CommentIndex:
    platform: str = ""
    platform_name: str = ""
    platform_dir: str = ""
    video_id: str = ""
    title: str = ""
    comments_path: Path | None = None
    all_comments: list = field(default_factory=list)
    artist_comments: list = field(default_factory=list)
    backend: object | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.artist_comments and self.backend is not None)


def _host_platform(link: str) -> str:
    try:
        host = (urlsplit(str(link or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    host = host.removeprefix("www.").removeprefix("m.")
    if host == "b23.tv" or host.endswith("bilibili.com"):
        return "bilibili"
    if host.endswith("douyin.com"):
        return "douyin"
    if host in {"weibo.com", "weibo.cn"} or host.endswith(".weibo.com"):
        return "weibo"
    return ""


def detect_platform(columns: dict) -> tuple[str, str]:
    """作品链接优先判平台；链接不可用时再看「发布平台」列。"""
    for link in (columns.get("作品链接"), columns.get("动态链接(b站)")):
        key = _host_platform(link or "")
        if key:
            return key, ""
    raw = str(columns.get("发布平台") or "").strip().lower()
    if raw:
        for key, info in PLATFORM_INFO.items():
            if any(alias in raw for alias in info["aliases"]):
                return key, ""
    return "", "该平台不支持账号评论索引"


def _append_id(out: list[str], value) -> None:
    text = str(value or "").strip()
    if text and _SAFE_ID.fullmatch(text) and text not in out:
        out.append(text)


def _weibo_mid(token: str) -> str:
    key = weibo_key(f"https://weibo.com/1/{token}")
    return key[6:] if key and key.startswith("weibo:") else ""


def extract_ids(platform: str, link: str) -> list[str]:
    """从作品链接提取评论区导出可能使用的目录 ID，保持优先级顺序。"""
    text = str(link or "").strip()
    if not text:
        return []
    try:
        parsed = urlsplit(text)
    except ValueError:
        return []
    path = parsed.path or ""
    query = parse_qs(parsed.query or "")
    out: list[str] = []

    if platform == "bilibili":
        if match := _BV_RE.search(path):
            _append_id(out, match.group(1))
        if match := _AV_RE.search(path):
            _append_id(out, match.group(1))
        if match := _OPUS_RE.search(path):
            _append_id(out, match.group(1))
        if (parsed.hostname or "").lower().startswith("t.bilibili.com"):
            if match := re.search(r"^/(\d+)", path):
                _append_id(out, match.group(1))
        for value in query.get("bvid") or []:
            _append_id(out, value)
    elif platform == "douyin":
        if match := _DY_VIDEO_RE.search(path):
            _append_id(out, match.group(1))
        for value in query.get("modal_id") or []:
            _append_id(out, value)
    elif platform == "weibo":
        token = ""
        if match := _WB_STATUS_RE.search(path):
            token = match.group(1)
        elif match := _WB_TV_RE.search(path):
            token = match.group(1)
        elif match := _WB_USER_BID_RE.search(path):
            token = match.group(1)
        _append_id(out, token)
        mid = _weibo_mid(token) if token else ""
        _append_id(out, mid)
    return out


def load_artist_identities() -> dict[str, set[str]]:
    """主账号平台 ID 集合；账号追踪不可用时退回内置主账号 ID。"""
    global _IDENTITIES_CACHE
    if _IDENTITIES_CACHE is not None:
        return {key: set(values) for key, values in _IDENTITIES_CACHE.items()}
    ids = {key: set(values) for key, values in DEFAULT_IDENTITIES.items()}
    try:
        from tracker.accounts import load_accounts

        for account in load_accounts():
            name = str(account.get("name") or "").strip()
            url = str(account.get("url") or "").strip()
            for key, names in MAIN_ACCOUNT_NAMES.items():
                if name not in names:
                    continue
                ident = _account_id(key, url)
                if ident:
                    ids.setdefault(key, set()).add(ident)
    except Exception:  # noqa: BLE001 - 账号名单只用于补强，失败时保留兜底 ID
        pass
    _IDENTITIES_CACHE = {key: set(values) for key, values in ids.items()}
    return ids


def _account_id(platform: str, url: str) -> str:
    patterns = {
        "bilibili": r"space\.bilibili\.com/(\d+)",
        "douyin": r"douyin\.com/user/([A-Za-z0-9_-]+)",
        "weibo": r"weibo\.com/(?:u/)?(\d+)",
    }
    match = re.search(patterns.get(platform, ""), url or "")
    return match.group(1) if match else ""


def _author_ids(backend, platform: str, comment: dict) -> set[str]:
    extractor = getattr(backend, "comment_author_ids", None)
    if callable(extractor):
        try:
            values = extractor(comment)
            if isinstance(values, (set, list, tuple)):
                return {str(v).strip() for v in values if str(v).strip()}
        except Exception:  # noqa: BLE001 - 单条字段异常按非本人处理
            pass
    if platform == "bilibili":
        value = str((comment.get("member") or {}).get("mid") or "").strip()
        return {value} if value else set()
    if platform == "douyin":
        user = comment.get("user") or {}
        values = {str(user.get("sec_uid") or "").strip(),
                  str(user.get("uid") or "").strip()}
        return {value for value in values if value}
    if platform == "weibo":
        value = str((comment.get("user") or {}).get("id") or "").strip()
        return {value} if value else set()
    return set()


def find_artist_comments(columns: dict, *, export_root=None,
                         identities=None) -> CommentIndex:
    """按作品链接找到本地评论，仅保留账号本人评论。"""
    platform, reason = detect_platform(columns)
    if not platform:
        return CommentIndex(reason=reason)
    info = PLATFORM_INFO[platform]
    title = str(columns.get("作品标题") or "").strip()

    candidates: list[str] = []
    for link in (columns.get("作品链接"), columns.get("动态链接(b站)")):
        for value in extract_ids(platform, str(link or "")):
            if value not in candidates:
                candidates.append(value)
    if not candidates:
        return CommentIndex(platform=platform, platform_name=info["name"],
                            platform_dir=info["dir"], title=title,
                            reason="作品链接无法定位评论区")

    root = Path(export_root) if export_root else get_export_subdir("video_comments")
    comments_path = None
    matched_id = ""
    for value in candidates:
        path = root / info["dir"] / value / "comments.json"
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if exists:
            comments_path = path
            matched_id = value
            break
    if comments_path is None:
        return CommentIndex(platform=platform, platform_name=info["name"],
                            platform_dir=info["dir"], title=title,
                            reason="未找到该作品的本地评论数据（先在视频评论区保存原始数据）")

    try:
        data = json.loads(comments_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CommentIndex(platform=platform, platform_name=info["name"],
                            platform_dir=info["dir"], video_id=matched_id, title=title,
                            comments_path=comments_path,
                            reason="本地评论数据无法读取")
    if isinstance(data, dict):
        data = data.get("comments") or data.get("data") or []
    if not isinstance(data, list):
        return CommentIndex(platform=platform, platform_name=info["name"],
                            platform_dir=info["dir"], video_id=matched_id, title=title,
                            comments_path=comments_path,
                            reason="本地评论数据格式异常")
    comments = [item for item in data if isinstance(item, dict)]
    id_map = load_artist_identities() if identities is None else identities
    artist_ids = (id_map or {}).get(platform) or set()
    artist = [item for item in comments
              if _author_ids(info["backend"], platform, item) & artist_ids]
    return CommentIndex(
        platform=platform, platform_name=info["name"], platform_dir=info["dir"],
        video_id=matched_id, title=title, comments_path=comments_path,
        all_comments=comments, artist_comments=artist, backend=info["backend"],
        reason="" if artist else "本地评论中没有封茗囧菌本人评论")


def artist_image_urls(index: CommentIndex) -> list[str]:
    """她本人评论中携带的原图 URL，按评论顺序去重。"""
    if not index.ok:
        return []
    out: list[str] = []
    for comment in index.artist_comments:
        for url in index.backend.image_urls(comment) or []:
            text = str(url or "").strip()
            if text.startswith("//"):
                text = "https:" + text
            if not text.startswith(("http://", "https://")):
                continue
            if text not in out:
                out.append(text)
    return out


def _pages(video_dir: Path, stem: str) -> list[Path]:
    first = video_dir / f"{stem}.png"
    if not first.is_file():
        return []
    pages = [(0, first)]
    pattern = re.compile(re.escape(stem) + r"_(\d+)\.png")
    for path in video_dir.glob(f"{stem}_*.png"):
        match = pattern.fullmatch(path.name)
        if match:
            pages.append((int(match.group(1)), path))
    pages.sort(key=lambda item: item[0])
    return [path for _, path in pages]


def render_index_image(index: CommentIndex, *, out_root=None) -> list[Path]:
    """生成（或复用）筛选后的评论区长图；返回首页及多页文件。"""
    if not index.ok:
        return []
    root = Path(out_root) if out_root else tc_cfg.data_dir() / "中间层/评论区图片"
    safe_id = sanitize_filename(index.video_id or "video", 80, fallback="video")
    video_dir = root / index.platform_dir / safe_id
    video_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(
        f"账号评论索引_{index.video_id or safe_id}", 90, fallback="账号评论索引")
    first = video_dir / f"{stem}.png"

    try:
        source_mtime = index.comments_path.stat().st_mtime if index.comments_path else 0
    except OSError:
        source_mtime = 0
    try:
        reusable = (first.is_file() and first.stat().st_size > 0
                    and first.stat().st_mtime >= source_mtime)
    except OSError:
        reusable = False
    if reusable:
        return _pages(video_dir, stem)

    for stale in video_dir.glob(f"{stem}*.png"):
        try:
            stale.unlink()
        except OSError:
            pass

    meta = {
        "platform": index.platform_name,
        "video_desc": index.title or index.video_id,
        "filter_desc": FILTER_DESC,
        "stats": {"total": len(index.all_comments)},
    }
    index.backend.export_comments_png(index.artist_comments, meta, first)
    return _pages(video_dir, stem)
