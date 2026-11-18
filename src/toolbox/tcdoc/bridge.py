"""腾讯文档「官方动态」中间层同步器（只读比对 + 一键复制）。

把本地「已获取」+ 增量数据库里的作品，和已同步到本地的腾讯表格按
链接 / 平台 ID / 标题+日期 三级匹配，找出尚未出现在腾讯表里的条目，
整理成腾讯表约定的列顺序，供用户一键复制后手动粘贴上传。

本模块只读 tables.db 与本地数据库，不写腾讯文档、不调用上传接口。
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import config as tc_cfg
from . import tables

# 中间层列顺序（与用户确认的腾讯「官方动态」列标准一致）
COLUMNS = ("作品标题", "发布距今", "作品内容", "发布账号", "封面图",
           "动态类型", "发布时间", "发布平台", "发布位置", "作品时长",
           "作品链接", "动态链接(b站)")

PLATFORM_TO_CN = {"bilibili": "bilibili", "douyin": "抖音", "weibo": "微博",
                  "kuaishou": "快手", "xiaohongshu": "小红书"}
CN_TO_PLATFORM = {"bilibili": "bilibili", "b站": "bilibili",
                  "抖音": "douyin", "微博": "weibo", "快手": "kuaishou",
                  "小红书": "xiaohongshu"}

REQUIRED_COLUMNS = ("作品标题", "发布时间", "发布平台", "发布账号")
LINK_COLUMNS = ("作品链接", "动态链接(b站)")

# 完整歌曲：只比对/新增，不改不删；本地来源是「音乐信息」页导出结果
SONG_DOC_KEY = "DRnd3bkhkQUR1aEJr"
SONG_TAB_ID = "ss_ktwk0t"
SONG_REQUIRED_COLUMNS = ("歌曲名称", "演唱者", "首发日期")
# 腾讯表头缺省顺序（本地快照读不到时兜底）；实际以快照表头为准。
SONG_FALLBACK_COLUMNS = (
    "封面图", "歌曲名称", "演唱者", "歌曲简介", "完整歌词", "歌曲时长",
    "动态歌词(更新中)", "作词人", "作曲人", "出品方", "作词原创性", "作曲原创性",
    "首发日期", "版权方", "作品性质", "关联线下", "原唱艺人", "合唱形式",
    "可用伴奏形式", "MV", "音乐类型", "语言", "发行平台列表", "B站链接",
    "QQ音乐", "网易云音乐", "酷狗音乐", "5sing", "酷我音乐", "汽水音乐",
    "人声本家", "授权作品", "所属专辑", "初编人员", "初编日期", "节令曲目",
    "备注信息", "终编人员", "发布天数", "线下演唱频次", "其他平台#1", "音轨号",
    "其他平台#2",
)
SONG_LINK_COLUMNS = ("QQ音乐", "网易云音乐", "酷狗音乐", "酷我音乐", "5sing", "汽水音乐")
SONG_PLATFORM_ORDER = (("qq", "QQ音乐"), ("netease", "网易云音乐"),
                       ("kugou", "酷狗音乐"), ("kuwo", "酷我音乐"),
                       ("5sing", "5sing音乐"))
_SONG_COLUMNS_CACHE: dict[str, tuple] = {}

_SONG_PLATFORM_COLUMNS = {"qq": "QQ音乐", "netease": "网易云音乐", "kugou": "酷狗音乐",
                          "kuwo": "酷我音乐", "fivesing": "5sing", "5sing": "5sing",
                          "soda": "汽水音乐"}
_SONG_PLATFORM_CN = {"QQ音乐": "qq", "网易云音乐": "netease", "酷狗音乐": "kugou",
                     "酷我音乐": "kuwo", "5sing": "fivesing", "汽水音乐": "soda"}
_SONG_LANGUAGE = {"国语": "汉语（CN）", "普通话": "汉语（CN）", "粤语": "汉语（CN）",
                  "英语": "英语（EN）", "日语": "日语（JP）", "韩语": "韩语（KP）"}
_ACCOMPANIMENT_RE = re.compile(r"伴奏|off\s*vocal|instrumental", re.I)
_LYRIC_TIMESTAMP_RE = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s?", re.M)
_SONG_CREDIT_RES = (
    ("作词人", re.compile(r"作词[^：:\n]{0,12}[：:]\s*([^\n]+)")),
    ("作曲人", re.compile(r"作曲[^：:\n]{0,12}[：:]\s*([^\n]+)")),
)

_ID_PATTERNS = (
    r"/video/(BV[0-9A-Za-z]+)",
    r"/video/av(\d+)",
    r"/video/(\d+)",
    r"/note/(\d+)",
    r"t\.bilibili\.com/(\d+)",
    r"/opus/(\d+)",
    r"/status/(\d+)",
    r"/detail/(\d+)",
    r"/short-video/([0-9A-Za-z_-]+)",
    r"/explore/([0-9a-fA-F]+)",
    r"/discovery/item/([0-9a-fA-F]+)",
    r"/([0-9A-Za-z_-]{15,})(?:/)?$",
)

# 远程表里没有可参考取值时的兜底
_FALLBACK_TYPE = {
    "weibo": ("动态", "动态"),
    "video": ("视频投稿", "主页"),
    "image": ("图文", "主页"),
    "dynamic": ("图文", "动态"),
}


# ==================== 基础归一化 ====================

def _since_date(days) -> str:
    """近 N 天的起始日期；days<=0 返回空串（全部历史）。"""
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return ""
    return (date.today() - timedelta(days=days)).isoformat()


def _date_key(value) -> str:
    """各种日期文本 → YYYY-MM-DD；解析失败返回空串。"""
    text = str(value or "")
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return ""
    try:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    except (TypeError, ValueError):
        return ""


def _datetime_text(value) -> str:
    """发布时间 → YYYY-MM-DD HH:MM（腾讯日期字段支持时分）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from platforms.base import normalize_datetime
        return normalize_datetime(text)
    except Exception:  # noqa: BLE001 - 归一化失败时保留原值
        return text


def _first(value) -> str:
    parts = _split_multi(value)
    return parts[0] if parts else ""


def _split_multi(value) -> list[str]:
    """腾讯多选值 → 列表（兼容「、」「,」「，」「/」分隔）。"""
    return [part.strip() for part in re.split(r"[、,，/;；]", str(value or ""))
            if part.strip()]


def _platform_of(value) -> str:
    """腾讯「发布平台」值 → 本地平台标识。"""
    for part in _split_multi(value):
        hit = CN_TO_PLATFORM.get(part) or CN_TO_PLATFORM.get(part.lower())
        if hit:
            return hit
    return ""


def _norm_url(url) -> str:
    """URL 归一化：去参数/锚点/尾斜杠，统一协议与 www/m 前缀。"""
    text = str(url or "").strip()
    if not text:
        return ""
    text = text.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
    except ValueError:
        return text.lower()
    host = (parts.netloc or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host == "bilibili.com":
        host = "www.bilibili.com"
    return f"https://{host}{parts.path or ''}"


def _url_ids(url) -> set[str]:
    """从作品链接里抽取平台 ID（BV号 / 抖音ID / 微博mid / 动态号等）。"""
    text = str(url or "")
    ids: set[str] = set()
    for pattern in _ID_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            ids.add(match.group(1))
    return ids


def _norm_title(title) -> str:
    """标题归一化：去【前缀】、#话题#、空白与标点，统一小写。"""
    text = str(title or "")
    text = re.sub(r"^【[^】]*】", "", text)
    text = re.sub(r"#[^#\s]+#", "", text)
    text = text.replace("#", "")
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text.lower()


def _item_key(item) -> str:
    return str(item.get("video_id") or item.get("video_url")
               or item.get("raw_url") or item.get("dedup_key") or "").strip()


def _in_window(item, since: str) -> bool:
    if not since:
        return True
    return _date_key(item.get("publish_time")) >= since


# ==================== 本地作品 ====================

def _increment_items_ro(since: str) -> list[dict] | None:
    """只读快照读取增量库；None 表示需要回退到带锁的实现。

    iter_items() 持有模块级写锁并可能触发迁移，采集正在写库时会把比对拖住，
    所以这里用独立只读连接直接查平台表。
    """
    from toolbox import increment_db

    try:
        path = Path(increment_db.db_path())
    except Exception:  # noqa: BLE001
        return None
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        names = [name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
            if not str(name).startswith("_")]
        out: list[dict] = []
        for name in names:
            try:
                cursor = conn.execute(
                    f'SELECT * FROM "{name}" WHERE "publish_time" >= ?', (since,))
            except sqlite3.Error:
                continue
            columns = [item[0] for item in cursor.description]
            for row in cursor:
                item = dict(zip(columns, row))
                item["platform"] = name
                out.append(item)
        return out
    except sqlite3.Error:
        return None
    finally:
        conn.close()


class SourceBusyError(RuntimeError):
    """本地存档暂时读不到（多因其它任务正在写入），提示用户稍后重试。"""


def _increment_items(since: str) -> list[dict]:
    from toolbox import increment_db

    # 测试或导入脚本会替换 iter_items 注入虚构数据，此时必须尊重替换结果
    if getattr(increment_db.iter_items, "__module__", "") != "toolbox.increment_db":
        return increment_db.iter_items(since)
    try:
        path = Path(increment_db.db_path())
    except Exception:  # noqa: BLE001 - 路径不可用按空库处理
        return []
    if not path.is_file():
        return []
    # 只读直查失败时短暂重试；不再回退到持有模块写锁的 iter_items()，
    # 否则采集正在写库时比对会无限等待，界面一直停在“正在比对”。
    for attempt in range(3):
        rows = _increment_items_ro(since)
        if rows is not None:
            return rows
        time.sleep(0.2 * (attempt + 1))
    raise SourceBusyError("本地增量数据库暂时无法读取（可能正在写入），请稍后重试")


def collect_local_items(days=30, collected=None) -> list[dict]:
    """已获取列表 + 增量数据库合并（按 video_id/链接去重），返回时间窗内条目。"""
    since = _since_date(days)
    merged: dict[str, dict] = {}
    for item in _increment_items(since):
        if not item.get("platform") or item["platform"] == "unknown":
            continue
        key = _item_key(item)
        if key:
            merged[key] = item
    for info in collected or []:
        item = asdict(info) if is_dataclass(info) else dict(info)
        item["platform"] = item.get("platform") or ""
        key = _item_key(item)
        if not key or not _in_window(item, since):
            continue
        merged[key] = item
    return [item for item in merged.values() if _in_window(item, since)]


# ==================== 完整歌曲：本地导出 + 比对 ====================

def _song_title_key(title) -> str:
    """歌曲名归一化：去括号补充、空白与标点，用于按歌曲名比对。"""
    text = str(title or "").lower()
    text = re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _song_cell_text(value) -> str:
    if isinstance(value, list):
        return "、".join(part for part in (_song_cell_text(i) for i in value) if part)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value or "").strip()


def _music_info_root() -> Path:
    from toolbox.config_manager import get_export_dir
    return Path(get_export_dir()) / "音乐信息"


def _load_song_exports(root: Path) -> list[dict]:
    exports = []
    if not root.is_dir():
        return exports
    for path in sorted(root.glob("*/translated/*_full.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            exports.append(data)
    return exports


def _song_singers(entries: list[dict]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        value = str(entry.get("歌手") or "")
        for part in re.split(r"[、,，/&;；]|\s+(?:feat|ft)\.?\s+", value, flags=re.I):
            part = part.strip()
            if part and part not in names:
                names.append(part)
    return names


def _song_lyrics(entries: list[dict]) -> str:
    best = ""
    for entry in entries:
        text = str(entry.get("歌词") or "")
        if len(text) > len(best):
            best = text
    text = _LYRIC_TIMESTAMP_RE.sub("", best)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _song_credits(lyrics: str) -> dict[str, str]:
    credits = {}
    head = "\n".join(lyrics.splitlines()[:40])
    for name, pattern in _SONG_CREDIT_RES:
        match = pattern.search(head)
        if match:
            value = match.group(1).strip().strip("：:;；,， ")
            if value:
                credits[name] = value[:60]
    return credits


def _song_duration(entries: list[dict]) -> str:
    counts = Counter()
    for entry in entries:
        try:
            seconds = int(entry.get("时长(秒)") or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds > 0:
            counts[seconds] += 1
    if not counts:
        return ""
    seconds = counts.most_common(1)[0][0]
    if seconds >= 3600:
        hours, remain = divmod(seconds, 3600)
        minutes, secs = divmod(remain, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def _song_date(entries: list[dict]) -> str:
    days = [day for day in (_date_key(entry.get("发行时间")) for entry in entries) if day]
    return min(days) if days else ""


def _song_album(entries: list[dict]) -> str:
    counts = Counter(str(entry.get("专辑名") or "").strip()
                     for entry in entries if str(entry.get("专辑名") or "").strip())
    if not counts:
        return ""
    album = counts.most_common(1)[0][0]
    return album if album.startswith("《") and album.endswith("》") else f"《{album}》"


def _song_company(albums: list[dict], album: str) -> str:
    if not album:
        return ""
    key = _song_title_key(album.strip("《》"))
    candidates = []
    for item in albums:
        if _song_title_key(item.get("专辑名")) != key:
            continue
        company = str(item.get("发行公司") or "").strip()
        if company and company != "-":
            candidates.append((company, str(item.get("平台标识") or "")))
    if not candidates:
        return ""
    for company, platform in candidates:
        if platform == "qq":
            return company
    return Counter(company for company, _ in candidates).most_common(1)[0][0]


def _song_cover(entries: list[dict]) -> str:
    for entry in entries:
        url = str(entry.get("歌曲图片") or "").strip()
        if url.startswith("https://"):
            return url
    for entry in entries:
        url = str(entry.get("歌曲图片") or "").strip()
        if url.startswith("http://"):
            return url
    return ""


def _song_language(entries: list[dict]) -> str:
    for entry in entries:
        value = str(entry.get("语种") or "").strip()
        if value in _SONG_LANGUAGE:
            return _SONG_LANGUAGE[value]
    return ""


def _song_links(entries: list[dict]) -> dict[str, str]:
    links: dict[str, str] = {}
    for entry in entries:
        platform = str(entry.get("平台标识") or "").strip().lower()
        if platform not in _SONG_PLATFORM_COLUMNS:
            platform = _SONG_PLATFORM_CN.get(str(entry.get("平台") or "").strip(), "")
        url = str(entry.get("URL链接") or "").strip()
        if not platform or not url.startswith(("https://", "http://")):
            continue
        column = _SONG_PLATFORM_COLUMNS[platform]
        links.setdefault(column, url)
    return links


def _read_song_columns_ro(path: Path) -> tuple[str, ...] | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT cols_json FROM sheets WHERE doc_key=? AND tab_id=?",
            (SONG_DOC_KEY, SONG_TAB_ID)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row:
        return None
    try:
        columns = json.loads(row[0] or "[]")
    except (TypeError, ValueError):
        return None
    names = tuple(str(col.get("name") or "") for col in columns if col.get("name"))
    if not all(name in names for name in SONG_REQUIRED_COLUMNS):
        return None
    return names


def song_columns() -> tuple[str, ...]:
    """完整歌曲的列顺序：以本地快照表头为准，保证复制/粘贴与标题栏对齐。"""
    try:
        path = Path(tables.db_path())
        stamp = _file_stamp(path)
    except Exception:  # noqa: BLE001 - 路径不可用按兜底列顺序
        return SONG_FALLBACK_COLUMNS
    cached = _SONG_COLUMNS_CACHE.get("columns")
    if cached and cached[0] == stamp:
        return cached[1]
    names = _read_song_columns_ro(path) or SONG_FALLBACK_COLUMNS
    _SONG_COLUMNS_CACHE["columns"] = (stamp, names)
    return names


def _row_from_song_group(entries: list[dict], albums: list[dict], columns) -> dict:
    title = Counter(str(entry.get("歌曲名") or "").strip()
                    for entry in entries if str(entry.get("歌曲名") or "").strip())
    title = title.most_common(1)[0][0] if title else ""
    singers = _song_singers(entries)
    lyrics = _song_lyrics(entries)
    credits = _song_credits(lyrics)
    album = _song_album(entries)
    links = _song_links(entries)
    cover = _song_cover(entries)

    row = {name: "" for name in columns}
    row.update({
        "歌曲名称": title,
        "演唱者": "、".join(singers),
        "完整歌词": lyrics,
        "歌曲时长": _song_duration(entries),
        "作词人": credits.get("作词人", ""),
        "作曲人": credits.get("作曲人", ""),
        "出品方": _song_company(albums, album),
        "首发日期": _song_date(entries),
        "语言": _song_language(entries),
        "合唱形式": {1: "单人演唱", 2: "双人合唱"}.get(
            len(singers), "多人合唱" if len(singers) > 2 else ""),
        "所属专辑": album,
    })
    row.update({column: links.get(column, "") for column in SONG_LINK_COLUMNS})
    row["_images"] = [cover] if cover else []
    row["_cover_url"] = cover
    return row


def collect_local_songs(root: Path | None = None) -> list[dict]:
    """音乐信息页导出结果 → 本地歌曲行（按歌曲名去重，排除伴奏条目）。"""
    columns = song_columns()
    exports = _load_song_exports(Path(root) if root else _music_info_root())
    groups: dict[str, dict] = {}
    albums: list[dict] = []
    for data in exports:
        entries = [item for item in data.get("歌曲列表") or [] if isinstance(item, dict)]
        albums.extend(item for item in data.get("专辑列表") or [] if isinstance(item, dict))
        buckets: dict[str, list[dict]] = defaultdict(list)
        for entry in entries:
            title = str(entry.get("歌曲名") or "")
            key = _song_title_key(title)
            if not key or _ACCOMPANIMENT_RE.search(title):
                continue
            buckets[key].append(entry)
        for key, items in buckets.items():
            groups.setdefault(key, {"entries": []})["entries"].extend(items)
    rows = []
    for group in groups.values():
        row = _row_from_song_group(group["entries"], albums, columns)
        if row["歌曲名称"]:
            rows.append(row)
    return rows


def _song_remote_keys(remote: list[dict]) -> set[str]:
    keys = set()
    for row in remote:
        values = row.get("values") or {}
        key = _song_title_key(_song_cell_text(values.get("歌曲名称")))
        if key:
            keys.add(key)
    return keys


def build_song_pending(root: Path | None = None) -> dict:
    """完整歌曲：本地音乐信息导出 vs 腾讯表，按歌曲名比对。"""
    started = time.perf_counter()
    columns = song_columns()
    remote = load_remote(SONG_DOC_KEY, SONG_TAB_ID)
    remote_keys = _song_remote_keys(remote)
    after_remote = time.perf_counter()
    local = collect_local_songs(root)
    after_local = time.perf_counter()
    groups: dict[str, dict] = {}
    for row in local:
        key = _song_title_key(row.get("歌曲名称"))
        if key:
            groups.setdefault(key, row)
    pending = [row for key, row in groups.items() if key not in remote_keys]
    pending.sort(key=lambda row: (row.get("首发日期") or "", row.get("歌曲名称") or ""),
                 reverse=True)
    with_images = sum(1 for row in pending if row.get("_images"))
    image_count = sum(len(row.get("_images") or []) for row in pending)
    finished = time.perf_counter()
    return {
        "profile": "songs",
        "rows": pending,
        "columns": list(columns),
        "accounts": [],
        "local_accounts": [],
        "link_columns": list(SONG_LINK_COLUMNS),
        "stats": {
            "local_items": len(local),
            "local_groups": len(groups),
            "synced_groups": len(groups) - len(pending),
            "pending": len(pending),
            "with_images": with_images,
            "without_images": len(pending) - with_images,
            "image_count": image_count,
            "remote_rows": len(remote),
            "since": "",
            "timing": {
                "remote": round(after_remote - started, 2),
                "local": round(after_local - after_remote, 2),
                "match": round(finished - after_local, 2),
                "total": round(finished - started, 2),
            },
        },
    }


# ==================== 线下活动 / 周边图鉴：只读候选比对 ====================
# 两张表均不在 tcdoc-sync-scope 白名单内：这里只生成候选并比对本地快照，
# 不调用任何写回接口；上传入口由 import_local.validate_target 拦截。

EVENT_TAB_ID = "ss_e1kkm2"
MERCH_TAB_ID = "ss_ekd4cz"
EVENT_REQUIRED_COLUMNS = ("活动名称", "活动日期")
MERCH_REQUIRED_COLUMNS = ("周边名称",)
LIVE_TAB_ID = "ss_1xgstm"
LIVE_REQUIRED_COLUMNS = ("直播标题", "直播平台", "开始时间")
LIVE_FALLBACK_COLUMNS = (
    "直播账户", "直播标题", "开始时间", "结束时间", "直播时长", "直播距今",
    "直播标签", "AI 摘要", "直播平台", "直播链接2", "直播链接1", "直播链接3",
    "直播截图", "初编人员", "备注信息", "终编人员",
)
EVENT_FALLBACK_COLUMNS = ("活动名称", "活动日期", "年份", "城市", "活动类型",
                          "宣传图", "备注信息")
MERCH_FALLBACK_COLUMNS = ("周边名称", "首发日期", "产品图", "封面图", "获取方式",
                          "来源渠道", "备注信息")

# 直播记录：自动字段只写这 6 列；直播时长/直播距今是公式列，任何情况都不写；
# 标签、AI 摘要、链接 2/3、截图、初编/终编、备注信息都属人工列，覆盖模式也不碰。
LIVE_AUTO_COLUMNS = ("直播账户", "直播标题", "开始时间", "结束时间",
                     "直播平台", "直播链接1")
LIVE_FORMULA_COLUMNS = ("直播时长", "直播距今")
LIVE_DUP_MODES = {"skip": "跳过重复", "fill": "只填空字段", "overwrite": "覆盖自动字段"}
_LIVE_ROOM_RE = re.compile(r"live\.douyin\.com/(\d+)")

_EVENT_LINE_RE = re.compile(r"【囧菌线下[·・]?\s*(\d{1,2})\.(\d{1,2})\s*([^】]{0,16})】")
_EVENT_MONTH_DAY_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_EVENT_TYPE_WORDS = ("生日会", "音乐节", "演唱会", "巡演", "漫展", "见面会",
                     "签售", "花车巡游")
_EVENT_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9·]{2,24}(?:生日会|音乐节|演唱会|巡演|漫展|见面会|签售))")
_EVENT_NAME_PREFIXES = ("相约", "和我", "我在", "在", "参加", "出席", "期待",
                        "明天", "今天", "本次", "因为", "这是", "第一次", "一起",
                        "尝试举办", "尝试", "举办", "封茗囧菌", "囧菌", "我",
                        "是", "日")
_MERCH_WORDS = ("周边", "无料", "棉花娃娃", "娃娃", "应援棒", "立牌", "徽章",
                "吧唧", "海报", "专辑", "雨伞", "通贩", "预售", "上架")
_MERCH_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9·]{2,24}(?:棉花娃娃|娃娃|应援棒|雨伞|立牌|徽章|"
    r"吧唧|海报|专辑|周边))")
_MERCH_GENERIC = {"周边", "娃娃", "海报", "专辑", "周边展示", "店铺上架通知",
                  "上架通知", "领养公告", "返图", "说明"}
_MERCH_BAD_WORDS = ("本次", "关于", "普票", "大货", "本款", "观影", "须知",
                    "说明", "返图", "领取", "发货", "打理", "特殊", "公告",
                    "通知", "已经", "开始", "陆续", "禁止", "私自", "制作",
                    "声明", "抵制", "盗版")
_MERCH_NEW_WORDS = ("上架", "预售", "通贩", "领养", "新品", "开售", "发布")


def _clean_event_name(raw) -> str:
    text = re.sub(r"^\d{1,2}月\d{1,2}日", "", str(raw or ""))
    changed = True
    while changed:
        changed = False
        for prefix in _EVENT_NAME_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                changed = True
    return text.strip(" ·-，。！")


def _event_name_score(name: str) -> int:
    score = len(name)
    if any(word in name for word in ("举办", "尝试", "因为", "这是", "本次", "关于")):
        score -= 20
    if re.match(r"^\d{1,2}月\d{1,2}日", name):
        score -= 10
    return score


def _clean_merch_name(raw) -> str:
    text = re.sub(r"^(封茗囧菌|囧菌)", "", str(raw or ""))
    text = re.sub(r"(领养公告|上架通知|公告|通知|说明|须知|返图|预览图|展示图)$", "",
                  text)
    text = re.sub(r"^\d(?:\.\d)?版本的?", "", text)
    return text.strip(" ·-，。！🍑")


def _sheet_columns(doc_key: str, tab_id: str) -> tuple:
    """目标表快照表头；读不到时返回空元组，由调用方回退到最小列集。"""
    try:
        sheets = tables.sheets_of(doc_key)
    except Exception:  # noqa: BLE001 - 快照异常时按空表头处理
        return ()
    for sheet in sheets:
        if str(sheet.get("tab_id") or "") != tab_id:
            continue
        return tuple(str(col.get("name") or "") for col in sheet.get("columns") or []
                     if str(col.get("name") or ""))
    return ()


def _event_candidate(item: dict):
    """本地动态 → 线下活动候选；只识别明确的日期 + 活动线索。"""
    title = str(item.get("title") or "")
    text = (title + "\n" + str(item.get("description") or "")).strip()
    if not text:
        return None
    publish_day = _date_key(item.get("publish_time"))
    year = int(publish_day[:4]) if publish_day else date.today().year
    month = day = 0
    city = ""
    match = _EVENT_LINE_RE.search(text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        tail = match.group(3).strip(" ·-")
        city = re.sub(r"(漫展|生日会|音乐节|演唱会|巡演|见面会|签售|线下活动|线下)$",
                      "", tail).strip()
    else:
        date_match = _EVENT_MONTH_DAY_RE.search(text)
        if not date_match or not any(word in text for word in _EVENT_TYPE_WORDS):
            return None
        month, day = int(date_match.group(1)), int(date_match.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    if publish_day and month + 6 < int(publish_day[5:7] or 1):
        year += 1
    event_type = next((word for word in _EVENT_TYPE_WORDS if word in text), "线下活动")
    if city:
        name = f"{city}{event_type}" if event_type != "线下活动" else f"{city}线下活动"
    else:
        name_match = _EVENT_NAME_RE.search(text)
        name = (_clean_event_name(name_match.group(1)) if name_match
                else f"{month}月{day}日{event_type}")
        if not name:
            name = f"{month}月{day}日{event_type}"
    return {
        "name": name,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "year": str(year),
        "city": city,
        "type": event_type,
        "text": text[:300],
        "images": _item_images(item),
        "source": _page_url(item),
        "author": str(item.get("author") or ""),
    }


def _merch_candidate(item: dict):
    """本地动态 → 周边图鉴候选；只保留能提取出具体周边名称的条目。"""
    title = str(item.get("title") or "")
    text = (title + "\n" + str(item.get("description") or "")).strip()
    if not text or not any(word in text for word in _MERCH_WORDS):
        return None
    has_news = any(word in text for word in _MERCH_NEW_WORDS)
    has_product_bracket = bool(re.search(
        r"【[^】]*(?:棉花娃娃|娃娃|应援棒|雨伞|立牌|徽章|吧唧|海报|专辑|周边)[^】]*】",
        text))
    if not has_news and not has_product_bracket:
        return None
    name = ""
    for match in re.finditer(r"【([^】]{2,40})】", text):
        cleaned = _clean_merch_name(match.group(1).strip())
        if cleaned in _MERCH_GENERIC or not cleaned:
            continue
        if any(word in cleaned for word in _MERCH_WORDS):
            name = cleaned
            break
    if not name:
        match = _MERCH_NAME_RE.search(text)
        name = _clean_merch_name(match.group(1)) if match else ""
    name = name.strip(" ·-")
    if (not name or name in _MERCH_GENERIC
            or any(word in name for word in _MERCH_BAD_WORDS)):
        return None
    if "会员" in text:
        acquire = "会员"
    elif any(word in text for word in ("无料", "免费", "领取")):
        acquire = "领取"
    elif any(word in text for word in ("上架", "预售", "通贩", "购买", "发货", "领养")):
        acquire = "购买"
    else:
        acquire = ""
    return {
        "name": name,
        "date": _date_key(item.get("publish_time")),
        "acquire": acquire,
        "text": text[:300],
        "images": _item_images(item),
        "source": _page_url(item),
        "author": str(item.get("author") or ""),
    }


def _event_synced(candidate: dict, remote_rows: list[dict]) -> bool:
    """按日期 + 名称（或城市）判断远端是否已有该活动。"""
    c_name = _song_title_key(candidate.get("name"))
    c_date = str(candidate.get("date") or "")
    city = str(candidate.get("city") or "")
    for row in remote_rows:
        values = row.get("values") or {}
        r_name_text = str(values.get("活动名称") or "")
        r_name = _song_title_key(r_name_text)
        r_date = (_date_key(values.get("活动日期"))
                  or _date_key(values.get("开始时间")))
        if c_date and r_date and c_date != r_date:
            continue
        if c_name and r_name and (c_name == r_name or c_name in r_name
                                  or r_name in c_name):
            return True
        r_type = str(values.get("活动类型") or "")
        if (c_date and r_date == c_date and r_type
                and candidate.get("type") not in ("", "线下活动")
                and candidate["type"] in r_type):
            return True
        if city and (not r_date or r_date == c_date) and city in r_name_text:
            return True
    return False


def _merch_synced(candidate: dict, remote_rows: list[dict]) -> bool:
    """按周边名称判断远端是否已有该周边。"""
    c_name = _song_title_key(candidate.get("name"))
    if not c_name:
        return False
    for row in remote_rows:
        values = row.get("values") or {}
        r_name = _song_title_key(values.get("周边名称"))
        if not r_name:
            continue
        if c_name == r_name or (len(c_name) >= 4 and c_name in r_name) or (
                len(r_name) >= 4 and r_name in c_name):
            return True
    return False


def _load_sidecars(subdir: str, filename: str, kind: str, days) -> list:
    """读取票务/购物目录下的结构化旁车 JSON，按获取时间窗口筛选。"""
    from toolbox.config_manager import get_export_dir
    root = Path(get_export_dir()) / subdir
    if not root.is_dir():
        return []
    since = _since_date(days)
    records = []
    for path in sorted(root.glob(f"*/{filename}")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        if raw.get("kind") and str(raw.get("kind")) != kind:
            continue
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict) or not data:
            continue
        stamp = _date_key(data.get("获取时间")) or _date_key(data.get("发布时间"))
        if not stamp:
            try:
                stamp = time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
            except OSError:
                stamp = ""
        if since and stamp and stamp < since:
            continue
        records.append((path, data))
    return records


def _load_latest(subdir: str, filename: str, kind: str, days) -> dict:
    """读取页面最近一次解析快照（派生缓存）；无文件或超窗口返回空。"""
    from toolbox.config_manager import get_export_dir
    path = Path(get_export_dir()) / subdir / filename
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or str(raw.get("kind") or "") != kind:
        return {}
    since = _since_date(days)
    saved = _date_key(raw.get("saved_at"))
    if since and saved and saved < since:
        return {}
    return raw


def _event_record_key(data: dict) -> str:
    event_id = str(data.get("演出ID") or "").strip()
    if event_id:
        return event_id
    return f"{data.get('标题') or ''}|{data.get('时间') or ''}"


def _product_record_key(data: dict) -> str:
    item_id = str(data.get("商品ID") or "").strip()
    if item_id:
        return item_id
    return _clean_shop_title(data.get("标题"))


def _local_images(data: dict, keys: tuple, limit: int = 12) -> list:
    """旁车里的本地图片路径（字符串），去重保序。"""
    images = []
    for key in keys:
        for path in data.get(key) or []:
            text = str(path or "").strip()
            if text and text not in images:
                images.append(text)
    return images[:limit]


def _city_text(city) -> str:
    """票务城市 → 腾讯表常用城市名（缺行政后缀时补「市」）。"""
    text = str(city or "").strip()
    if not text:
        return ""
    if text.endswith(("市", "区", "县", "盟", "旗")) or text.endswith("自治州"):
        return text
    return text + "市"


_EVENT_TYPE_MAP = (("漫展", "漫展"), ("音乐节", "音乐节"),
                   ("生日会", "生日会"), ("生日专场", "生日会"),
                   ("演唱会", "演唱会"), ("巡演", "演唱会"),
                   ("专场", "演唱会"), ("音乐会", "演唱会"))


def _infer_event_type(title, category, text) -> str:
    """从票务分类/标题推断腾讯「活动类型」多选值。"""
    haystack = " ".join(str(x or "") for x in (title, category, text))
    for word, value in _EVENT_TYPE_MAP:
        if word in haystack:
            return value
    return "演唱会"


def _event_status(day: str, raw_status) -> str:
    """按取消/延期与日期推断「活动状态」。"""
    raw = str(raw_status or "")
    if "取消" in raw:
        return "已取消"
    if "延期" in raw:
        return "已延期"
    if day:
        return "已结束" if day < date.today().isoformat() else "待开场"
    return ""


def _signing_text(text) -> str:
    """演出介绍/须知里提到签售且没有否定词 → 含签售。"""
    text = str(text or "")
    if "签售" not in text:
        return "无签售"
    if re.search(r"(无|不设|不参与|取消|没有).{0,6}签售", text):
        return "无签售"
    return "含签售"


def _ticket_price_text(data: dict) -> str:
    """场次票档 → 「名称：价格」多行；无票档时回退票价。"""
    lines = []
    for session in data.get("场次明细") or []:
        if not isinstance(session, dict):
            continue
        for tier in session.get("票档") or []:
            if not isinstance(tier, dict):
                continue
            name = str(tier.get("名称") or "").strip()
            price = str(tier.get("价格") or "").strip().replace("¥", "￥")
            if not price:
                continue
            line = f"{name}：{price}" if name and name != price else price
            if line not in lines:
                lines.append(line)
    if not lines:
        range_text = str(data.get("票价") or "").strip().replace("¥", "￥")
        if range_text:
            lines.append(range_text)
    return "\n".join(lines[:8])


def _clean_shop_title(title) -> str:
    """淘宝商品标题 → 周边名称：去掉【官方】前缀与开头的日期。"""
    text = re.sub(r"^【[^】]*】\s*", "", str(title or "")).strip()
    text = re.sub(r"^\d{4}\s*[.\-/年]\s*\d{1,2}\s*[.\-/月]\s*\d{1,2}\s*日?\s*", "", text)
    return text.strip(" ·-") or str(title or "").strip()


def _ticket_candidates(days) -> list:
    """票务解析 → 线下活动候选（详情旁车优先，其次最近一次解析快照）。"""
    records = {}
    latest = _load_latest("票务演出", "_latest.json", "ticket_latest", days)
    for data in latest.get("events") or []:
        if isinstance(data, dict) and data.get("标题"):
            records[_event_record_key(data)] = data
    for path, data in _load_sidecars("票务演出", "event.json", "ticket_event", days):
        records[_event_record_key(data)] = data   # 明细旁车覆盖列表快照
    rows = []
    for data in records.values():
        title = str(data.get("标题") or "").strip()
        if not title:
            continue
        day = _date_key(data.get("时间")) or _date_key(data.get("获取时间"))
        city = _city_text(data.get("城市"))
        text = " ".join(str(data.get(key) or "") for key in
                        ("演出介绍", "购票须知"))
        bits = [data.get("平台"), data.get("场馆"), data.get("票价"),
                data.get("状态")]
        rows.append({
            "name": title,
            "date": day,
            "year": day[:4] if day else "",
            "city": city,
            "venue": str(data.get("场馆") or ""),
            "address": str(data.get("地址") or ""),
            "status": _event_status(day, data.get("状态")),
            "type": _infer_event_type(title, data.get("分类"), text),
            "stage": "含舞台",
            "signing": _signing_text(text),
            "ticket_price": _ticket_price_text(data),
            "text": "｜".join(str(b) for b in bits if b)[:300],
            "images": _local_images(
                data, ("本地海报", "本地封面", "本地宣传图", "本地座位图", "本地场馆图")),
            "source": str(data.get("链接") or ""),
            "author": str(data.get("平台") or ""),
        })
    return rows


def _shopping_candidates(days) -> list:
    """购物解析 → 周边图鉴候选（详情旁车优先，其次最近一次店铺列表快照）。"""
    records = {}
    latest = _load_latest("淘宝商品", "_latest.json", "shop_latest", days)
    shop_name = str(latest.get("shop_name") or "")
    for data in latest.get("products") or []:
        if isinstance(data, dict) and data.get("标题"):
            records[_product_record_key(data)] = data
    for path, data in _load_sidecars("淘宝商品", "item.json", "shop_item", days):
        records[_product_record_key(data)] = data   # 详情旁车覆盖列表快照
    rows = []
    for data in records.values():
        title = str(data.get("标题") or "").strip()
        if not title:
            continue
        name = _clean_shop_title(title)
        bits = []
        if data.get("价格"):
            bits.append(f"价格：{data['价格']}")
        if data.get("销量"):
            bits.append(f"销量：{data['销量']}")
        local = data.get("本地图片")
        images = _local_images(local if isinstance(local, dict) else {},
                               ("主图", "规格图"), limit=10)
        if not images:
            images = _local_images(data, ("本地主图", "本地规格图"), limit=10)
        rows.append({
            "name": name,
            "date": "",
            "acquire": "整套购买、单独购买",
            "text": "｜".join(bits)[:300],
            "images": images,
            "source": str(data.get("链接") or ""),
            "author": str(data.get("店铺") or shop_name or ""),
        })
    return rows


def _build_candidate_result(profile: str, days, collected=None) -> dict:
    """公共编排：本地候选 → 去重 → 与腾讯快照比对 → 只读待补行。"""
    started = time.perf_counter()
    tab_id = EVENT_TAB_ID if profile == "events" else MERCH_TAB_ID
    remote = load_remote(SONG_DOC_KEY, tab_id)
    after_remote = time.perf_counter()
    candidates = (_ticket_candidates(days) if profile == "events"
                  else _shopping_candidates(days))
    source_kind = "ticket" if profile == "events" else "shop"
    if candidates:
        items_count = len(candidates)
    else:
        source_kind = "dynamics"
        items = collect_local_items(days, collected)
        items_count = len(items)
        candidates = []
        for item in items:
            candidate = (_event_candidate(item) if profile == "events"
                         else _merch_candidate(item))
            if candidate:
                candidates.append(candidate)
    after_local = time.perf_counter()
    groups: dict[str, dict] = {}
    for candidate in candidates:
        if profile == "events":
            key = "|".join((str(candidate.get("date") or ""),
                            str(candidate.get("city") or ""),
                            str(candidate.get("type") or "")))
        else:
            key = _song_title_key(candidate.get("name"))
        if not key:
            continue
        group = groups.get(key)
        if group is None:
            groups[key] = {**candidate, "images": []}
            group = groups[key]
        elif (profile == "events"
              and _event_name_score(candidate.get("name") or "")
              > _event_name_score(group.get("name") or "")):
            group["name"] = candidate.get("name")
        for url in candidate.get("images") or []:
            if url not in group["images"]:
                group["images"].append(url)
        if len(candidate.get("text") or "") > len(group.get("text") or ""):
            group["text"] = candidate["text"]
    pending = []
    for group in groups.values():
        synced = (_event_synced(group, remote) if profile == "events"
                  else _merch_synced(group, remote))
        if not synced:
            pending.append(group)
    after_match = time.perf_counter()
    pending.sort(key=lambda item: (str(item.get("date") or ""), item.get("name") or ""),
reverse=True)
    columns = _sheet_columns(SONG_DOC_KEY, tab_id)
    if not columns:
        columns = EVENT_FALLBACK_COLUMNS if profile == "events" else MERCH_FALLBACK_COLUMNS
    rows = []
    for item in pending:
        row = {name: "" for name in columns}
        if profile == "events":
            row.update({
                "活动名称": item.get("name") or "",
                "活动日期": item.get("date") or "",
                "年份": item.get("year") or "",
                "城市": item.get("city") or "",
                "场馆名称": item.get("venue") or "",
                "场馆地址": item.get("address") or "",
                "活动状态": item.get("status") or "",
                "活动类型": item.get("type") or "",
                "舞台环节": item.get("stage") or "",
                "签售环节": item.get("signing") or "",
                "备注信息": item.get("text") or "",
                "购票链接 1": item.get("source") or "",
                "票档价格": item.get("ticket_price") or "",
            })
        else:
            row.update({
                "周边名称": item.get("name") or "",
                "首发日期": item.get("date") or "",
                "获取方式": item.get("acquire") or "",
                "来源渠道": item.get("author") or "",
                "备注信息": item.get("text") or "",
            })
        row["_images"] = list(item.get("images") or [])
        row["_source"] = item.get("source") or ""
        row["_needs_review"] = True
        rows.append(row)
    image_count = sum(len(row.get("_images") or []) for row in rows)
    with_images = sum(1 for row in rows if row.get("_images"))
    return {
        "profile": profile,
        "rows": rows,
        "columns": list(columns),
        "accounts": [],
        "local_accounts": [],
        "link_columns": [],
        "stats": {
            "local_items": items_count,
            "local_groups": len(groups),
            "synced_groups": len(groups) - len(pending),
"pending": len(rows),
            "with_images": with_images,
            "without_images": len(rows) - with_images,
            "image_count": image_count,
            "since": _since_date(days),
            "source": source_kind,
            "timing": {
                "remote": round(after_remote - started, 2),
                "local": round(after_local - after_remote, 2),
                "match": round(after_match - after_local, 2),
                "total": round(after_match - started, 2),
            },
        },
    }


def manual_target_result(profile: str, columns) -> dict:
    """无本地自动数据源的目标表（如直播记录）→ 空结果，供手动录入。"""
    return {
        "profile": profile,
        "rows": [],
        "columns": list(columns or []),
        "accounts": [],
        "local_accounts": [],
        "link_columns": [],
        "stats": {
            "local_items": 0, "local_groups": 0, "synced_groups": 0,
            "pending": 0, "with_images": 0, "without_images": 0,
            "image_count": 0, "since": "", "source": "manual",
            "timing": {"remote": 0, "local": 0, "match": 0, "total": 0},
        },
    }


def build_event_pending(days=365, collected=None) -> dict:
    """线下活动：票务解析旁车优先（旧本地动态兜底）→ 与快照比对（只读）。"""
    return _build_candidate_result("events", days, collected)


def build_merch_pending(days=365, collected=None) -> dict:
    """周边图鉴：购物解析旁车优先（旧本地动态兜底）→ 与快照比对（只读）。"""
    return _build_candidate_result("merch", days, collected)


def _item_value(item, name: str, default=""):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _item_album(item) -> dict:
    extra = _item_value(item, "extra", {}) or {}
    album = extra.get("_album") if isinstance(extra, dict) else None
    return album if isinstance(album, dict) else {}


def _most_common_text(values) -> str:
    counts = Counter(str(value).strip() for value in values
                     if str(value or "").strip())
    if not counts:
        return ""
    best = max(counts.values())
    return max((text for text, count in counts.items() if count == best), key=len)


def _most_common_int(values) -> int:
    counts = Counter()
    for value in values:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            counts[number] += 1
    return counts.most_common(1)[0][0] if counts else 0


def _best_release_date(values) -> str:
    counts = Counter(day for value in values for day in (_date_key(value),) if day)
    if not counts:
        return ""
    best = max(counts.values())
    return min(day for day, count in counts.items() if count == best)


def _language_text(values) -> str:
    for value in values:
        text = str(value or "")
        for key, mapped in _SONG_LANGUAGE.items():
            if key in text:
                return mapped
    return ""


def _normalize_singer_text(text) -> str:
    names: list[str] = []
    for part in re.split(r"[、,，/&;；]|\s+(?:feat|ft)\.?\s+", str(text or ""), flags=re.I):
        part = part.strip()
        if part and part not in names:
            names.append(part)
    return "、".join(names)


def _mmss(seconds: int) -> str:
    if not seconds:
        return ""
    if seconds >= 3600:
        hours, remain = divmod(seconds, 3600)
        minutes, secs = divmod(remain, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def best_platform_songs(results: dict, title: str, singer: str) -> dict[str, object]:
    """五平台搜索结果 → 每平台一首歌名匹配的最佳结果（伴奏默认排除）。"""
    target = _song_title_key(title)
    matches: dict[str, object] = {}
    for key, _label in SONG_PLATFORM_ORDER:
        items = list(results.get(key) or [])
        if "伴奏" not in str(title):
            items = [item for item in items
                     if not _ACCOMPANIMENT_RE.search(
                         str(_item_value(item, "song_name") or ""))]
        ranked = []
        for index, item in enumerate(items):
            name = _song_title_key(_item_value(item, "song_name"))
            score = 100 if name and name == target else (
                60 if name and target and (target in name or name in target) else 0)
            if score and singer and str(singer) in str(_item_value(item, "singer") or ""):
                score += 30
            ranked.append((score, -index, item))
        ranked.sort(reverse=True, key=lambda part: (part[0], part[1]))
        if ranked and ranked[0][0] >= 60:
            matches[key] = ranked[0][2]
    return matches


def song_row_from_matches(matches: dict, title: str, singer: str,
                          columns=None) -> dict:
    """多平台匹配结果 → 一行「完整歌曲」数据，键严格按表头列名。"""
    columns = tuple(columns or song_columns())
    row = {name: "" for name in columns}
    items = [item for item in matches.values() if item is not None]
    target = _song_title_key(title)
    titled = [item for item in items
              if _song_title_key(_item_value(item, "song_name")) == target]
    base = titled or items

    row["歌曲名称"] = _most_common_text(
        _item_value(item, "song_name") for item in base) or str(title or "")
    singer_text = _normalize_singer_text(
        _most_common_text(_item_value(item, "singer") for item in base) or singer)
    row["演唱者"] = singer_text
    singers = [part for part in singer_text.split("、") if part]
    row["合唱形式"] = {1: "单人演唱", 2: "双人合唱"}.get(
        len(singers), "多人合唱" if len(singers) > 2 else "")

    lyrics = max((str(_item_value(item, "lyrics") or "") for item in items),
                 key=len, default="")
    lyrics = _LYRIC_TIMESTAMP_RE.sub("", lyrics)
    lyrics = re.sub(r"\n{3,}", "\n\n", lyrics).strip()
    row["完整歌词"] = lyrics
    credits = _song_credits(lyrics)
    row["作词人"] = credits.get("作词人", "")
    row["作曲人"] = credits.get("作曲人", "")

    row["歌曲时长"] = _mmss(_most_common_int(
        _item_value(item, "duration") for item in items))
    row["首发日期"] = _best_release_date(
        value for item in items
        for value in (_item_value(item, "release_date"),
                      _item_album(item).get("release_date")))
    row["语言"] = _language_text(
        value for item in items
        for value in (_item_value(item, "language"),
                      _item_album(item).get("language")))

    album = _most_common_text(
        _item_value(item, "album_name") or _item_album(item).get("album_name")
        for item in items)
    row["所属专辑"] = "" if not album else (
        album if album.startswith("《") and album.endswith("》") else f"《{album}》")
    company = _most_common_text(_item_album(item).get("company") for item in items)
    row["出品方"] = "" if company == "-" else company
    track = _most_common_int(_item_value(item, "track_no") for item in items)
    row["音轨号"] = str(track) if track else ""

    for key, item in matches.items():
        column = _SONG_PLATFORM_COLUMNS.get(key)
        url = str(_item_value(item, "url") or "").strip()
        if column in row and url.startswith(("http://", "https://")):
            row[column] = url

    cover = next((str(_item_value(item, "cover_url") or "").strip() for item in items
                  if str(_item_value(item, "cover_url") or "").startswith(
                      ("http://", "https://"))), "")
    row["_images"] = [cover] if cover else []
    row["_cover_url"] = cover
    return row


# ==================== 腾讯表格 ====================

def _is_sync_sheet(sheet: dict) -> bool:
    names = {str(col.get("name") or "") for col in sheet.get("columns") or []}
    return all(name in names for name in REQUIRED_COLUMNS) and any(
        name in names for name in LINK_COLUMNS)


def _is_song_sheet(sheet: dict) -> bool:
    names = {str(col.get("name") or "") for col in sheet.get("columns") or []}
    return all(name in names for name in SONG_REQUIRED_COLUMNS)


def list_targets() -> list[dict]:
    """本地已同步的目标表：官方动态（含直播记录结构）+ 完整歌曲。"""
    targets = []
    for doc in tc_cfg.load_docs():
        key = str(doc.get("key") or tables.doc_key_of(doc.get("url")) or "")
        if not key:
            continue
        try:
            sheets = tables.sheets_of(key)
        except Exception:  # noqa: BLE001 - 单个文档异常不影响其它文档
            continue
        for sheet in sheets:
            if sheet.get("skipped"):
                continue
            names = {str(col.get("name") or "") for col in sheet.get("columns") or []}
            if key == SONG_DOC_KEY and sheet["tab_id"] == EVENT_TAB_ID:
                if not all(name in names for name in EVENT_REQUIRED_COLUMNS):
                    continue
                profile = "events"
            elif key == SONG_DOC_KEY and sheet["tab_id"] == MERCH_TAB_ID:
                if not all(name in names for name in MERCH_REQUIRED_COLUMNS):
                    continue
                profile = "merch"
            elif key == SONG_DOC_KEY and sheet["tab_id"] == LIVE_TAB_ID:
                if not all(name in names for name in LIVE_REQUIRED_COLUMNS):
                    continue
                profile = "lives"
            elif key == SONG_DOC_KEY and sheet["tab_id"] == SONG_TAB_ID:
                if not _is_song_sheet(sheet):
                    continue
                profile = "songs"
            elif _is_sync_sheet(sheet):
                profile = "posts"
            else:
                continue
            targets.append({
                "doc_key": key,
                "doc_name": doc.get("name") or key,
                "doc_url": doc.get("url") or "",
                "tab_id": sheet["tab_id"],
                "tab_name": sheet["tab_name"],
                "n_rows": sheet.get("n_rows") or 0,
                "columns": sheet.get("columns") or [],
                "profile": profile,
            })
    return targets


_REMOTE_CACHE: dict[tuple, tuple[tuple, list[dict], dict]] = {}


def _file_stamp(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (0, 0)


def _read_remote_ro(doc_key: str, tab_id: str) -> list[dict] | None:
    """只读快照读取表格存档；None 表示需要回退到旧的分页实现。

    同步任务会持续写 tables.db，旧的 rows_page() 每次调用都跑一遍建表脚本，
    等于每页都要抢一次写锁；这里改成一次性只读连接，不执行任何 DDL。
    """
    try:
        path = Path(tables.db_path())
    except Exception:  # noqa: BLE001 - 路径不可用就回退
        return None
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.5)
    except sqlite3.Error:
        return None
    try:
        conn.execute("PRAGMA busy_timeout=1500")
        rows = []
        for (payload,) in conn.execute(
                "SELECT data_json FROM rows WHERE doc_key=? AND tab_id=? ORDER BY row_no",
                (doc_key, tab_id)):
            try:
                values = json.loads(payload)
            except (TypeError, ValueError):
                values = {}
            rows.append({"values": values})
        return rows
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _remote_snapshot(doc_key: str, tab_id: str) -> tuple[list[dict], dict]:
    """同一份存档只解析一次；表格库被同步写入后按文件戳自动失效。"""
    try:
        db_file = Path(tables.db_path())
        stamp = _file_stamp(db_file)
    except Exception:  # noqa: BLE001
        db_file, stamp = None, (0, 0)
    key = (doc_key, tab_id)
    cached = _REMOTE_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    if db_file is None or not db_file.is_file():
        return [], _remote_index([])
    # 只读快照失败时短暂重试；不再回退到 rows_page()，那条路径每次都跑
    # 建表脚本、需要写锁，表格同步期间会把比对无限拖住。
    rows = None
    for attempt in range(3):
        rows = _read_remote_ro(doc_key, tab_id)
        if rows is not None:
            break
        time.sleep(0.2 * (attempt + 1))
    if rows is None:
        raise SourceBusyError("腾讯表格本地存档暂时无法读取（可能正在同步），请稍后重试")
    index = _remote_index(rows)
    if len(_REMOTE_CACHE) > 8:
        _REMOTE_CACHE.clear()
    _REMOTE_CACHE[key] = (stamp, rows, index)
    return rows, index


def _index_of(rows: list[dict]) -> dict:
    """缓存命中时直接复用索引；被替换过的行（测试注入）才重新计算。"""
    for cached_rows, cached_index in ((v[1], v[2]) for v in _REMOTE_CACHE.values()):
        if cached_rows is rows:
            return cached_index
    return _remote_index(rows)


def load_remote(doc_key: str, tab_id: str) -> list[dict]:
    """读取本地存档的整张腾讯工作表（只读快照 + 内存缓存）。"""
    return _remote_snapshot(doc_key, tab_id)[0]


def _remote_index(rows: list[dict]) -> dict:
    """腾讯表 → 链接/ID/标题索引 + 动态类型投票 + 发布账号统计。"""
    urls: set[str] = set()
    ids: set[str] = set()
    texts: set[tuple] = set()
    titles: set[tuple] = set()
    votes: dict[tuple, Counter] = defaultdict(Counter)
    accounts: Counter = Counter()
    types_seen: set[str] = set()
    positions_seen: set[str] = set()
    plat_types: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        values = row.get("values") or {}
        plat = _platform_of(values.get("发布平台"))
        for column in LINK_COLUMNS:
            url = _norm_url(values.get(column))
            if not url:
                continue
            urls.add(url)
            ids.update(_url_ids(url))
        record_id = str(values.get("作品ID") or "").strip()
        if record_id:
            ids.add(record_id)

        title = _norm_title(values.get("作品标题"))
        day = _date_key(values.get("发布时间"))
        if title:
            if day:
                texts.add((plat, day, title))
                texts.add((plat, day, title[:20]))
            if len(title) >= 10:
                titles.add((plat, title[:20]))
        for name in _split_multi(values.get("发布账号")):
            accounts[name] += 1

        page = _norm_url(values.get("作品链接"))
        if plat == "weibo":
            kind_group = "weibo"
        elif page and ("/note/" in page or _first(values.get("动态类型")) == "图文"):
            kind_group = "image"
        elif page:
            kind_group = "video"
        else:
            kind_group = "dynamic"
        type_value = _first(values.get("动态类型"))
        pos_value = _first(values.get("发布位置"))
        if type_value or pos_value:
            votes[(plat, kind_group)][(type_value, pos_value)] += 1
        if type_value:
            types_seen.add(type_value)
            plat_types[plat][type_value] += 1
        if pos_value:
            positions_seen.add(pos_value)

    return {"urls": urls, "ids": ids, "texts": texts, "titles": titles,
            "votes": votes, "accounts": accounts,
            "types": types_seen, "positions": positions_seen,
            "plat_types": plat_types}


# ==================== 作品链接 ====================

def _item_images(item) -> list[str]:
    """本地条目 → 图片 URL 列表（封面 + 多图，去重保序）。"""
    urls: list[str] = []
    seen: set[str] = set()

    def add(value):
        if isinstance(value, (list, tuple)):
            for part in value:
                add(part)
            return
        if not isinstance(value, str):
            return
        for part in re.split(r"[\s,，]+", value):
            part = html.unescape(part.strip())
            if part.startswith("//"):
                part = "https:" + part
            if part.startswith(("http://", "https://")) and part not in seen:
                seen.add(part)
                urls.append(part)

    add(item.get("cover_url"))
    add(item.get("images"))
    raw = item.get("raw_data")
    if isinstance(raw, dict):
        add(raw.get("images"))
        add(raw.get("image_urls"))
    raw_json = item.get("raw_json")
    if isinstance(raw_json, str):
        try:
            raw = json.loads(raw_json)
        except (TypeError, ValueError):
            raw = None
        if isinstance(raw, dict):
            add(raw.get("images"))
            add(raw.get("image_urls"))
    return urls


def _author_id(item) -> str:
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = item.get("raw_data") if isinstance(item.get("raw_data"), dict) else {}
    for key in ("author_id", "uid", "authorId", "user_id"):
        value = raw.get(key)
        if value:
            return str(value)
    return ""


def _looks_temporary(url: str) -> bool:
    text = str(url or "").lower()
    return any(part in text for part in ("douyinvod", "zjcdn", "xhscdn",
                                         "kwimgs", "video.weibo", "ndcimgs",
                                         "djvod", "kwaicdn"))


def _page_url(item) -> str:
    """本地条目 → 作品页链接（腾讯「作品链接」列）。"""
    plat = str(item.get("platform") or "")
    vid = str(item.get("video_id") or "").strip()
    url = str(item.get("video_url") or "").strip()
    kind = str(item.get("kind") or "")

    if plat == "bilibili":
        if vid.startswith("dyn_"):
            return ""  # 纯动态：作品链接留空，填动态链接(b站)
        if re.match(r"^(BV|av)\w+", vid, re.I):
            return f"https://www.bilibili.com/video/{vid}"
        return _norm_url(url)
    if plat == "douyin":
        if vid:
            suffix = "note" if kind in ("image", "note") else "video"
            return f"https://www.douyin.com/{suffix}/{vid}"
        return _norm_url(url) if "douyin.com" in url else ""
    if plat == "weibo":
        if vid:
            uid = _author_id(item)
            return (f"https://weibo.com/{uid}/{vid}" if uid
                    else f"https://m.weibo.cn/status/{vid}")
        return _norm_url(url) if "weibo" in url else ""
    if plat == "kuaishou" and vid:
        return f"https://www.kuaishou.com/short-video/{vid}"
    if plat == "xiaohongshu" and vid:
        return f"https://www.xiaohongshu.com/explore/{vid}"
    if url.startswith("http") and not _looks_temporary(url):
        return _norm_url(url)
    return ""


def _dyn_url(item) -> str:
    """本地 B 站动态 → 腾讯「动态链接(b站)」列。"""
    if str(item.get("platform") or "") != "bilibili":
        return ""
    vid = str(item.get("video_id") or "").strip()
    url = str(item.get("video_url") or "").strip()
    if "t.bilibili.com" in url or "/opus/" in url:
        return url
    if vid.startswith("dyn_"):
        return f"https://t.bilibili.com/{vid[4:]}"
    return ""


def _local_urls(item) -> set[str]:
    urls = set()
    for url in (item.get("video_url"), _page_url(item), _dyn_url(item)):
        norm = _norm_url(url)
        if norm:
            urls.add(norm)
    return urls


def _local_ids(item) -> set[str]:
    ids: set[str] = set()
    vid = str(item.get("video_id") or "").strip()
    if vid:
        ids.add(vid)
        if vid.startswith("dyn_"):
            ids.add(vid[4:])
    for url in (item.get("video_url"), _page_url(item), _dyn_url(item)):
        ids.update(_url_ids(url))
    return ids


def _matches(item, index: dict) -> bool:
    """本地条目是否已存在于腾讯表。"""
    if _local_urls(item) & index["urls"]:
        return True
    if _local_ids(item) & index["ids"]:
        return True
    plat = str(item.get("platform") or "")
    title = _norm_title(item.get("title"))
    day = _date_key(item.get("publish_time"))
    if title and day and (plat, day, title[:20]) in index["texts"]:
        return True
    if title and len(title) >= 10 and (plat, title[:20]) in index["titles"]:
        return True
    return False


# ==================== 待同步行 ====================

def _group_key(item) -> tuple:
    plat = str(item.get("platform") or "")
    day = _date_key(item.get("publish_time"))
    title = _norm_title(item.get("title"))
    if title:
        return plat, day, title[:20]
    return plat, day, _item_key(item)


def _publish_minute(item) -> str:
    """发布时间 → YYYY-MM-DD HH:MM（用于合并同一分钟发布的 B 站视频+动态）。"""
    text = str(item.get("publish_time") or "")
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T](\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    try:
        return (f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-"
                f"{int(match.group(3)):02d} {int(match.group(4)):02d}:{match.group(5)}")
    except (TypeError, ValueError):
        return ""


def _group_kind(items: list[dict]) -> str:
    for item in items:
        vid = str(item.get("video_id") or "")
        if str(item.get("kind") or "") == "video" and not vid.startswith("dyn_"):
            return "video"
    if any(_dyn_url(item) for item in items):
        return "dynamic"
    return "other"


def _merge_bilibili_pairs(groups: dict[tuple, list[dict]]) -> None:
    """B 站视频与同分钟发布的动态合并成一行（作品链接 + 动态链接(b站)）。

    视频稿件和它的更新动态标题经常不同（如「【封茗囧菌】鱼窥荷」与
    「一首很用心的古风歌…」），按标题分组会拆成两行；同一分钟成对发布
    时合并，和腾讯表「一行一作品」的口径一致。
    """
    buckets: dict[str, list[tuple]] = defaultdict(list)
    for key, items in groups.items():
        if key[0] != "bilibili" or not items:
            continue
        minute = _publish_minute(items[0])
        if minute:
            buckets[minute].append(key)
    for keys in buckets.values():
        if len(keys) != 2:
            continue
        first, second = keys
        kinds = {_group_kind(groups[first]), _group_kind(groups[second])}
        if kinds != {"video", "dynamic"}:
            continue
        video_key = first if _group_kind(groups[first]) == "video" else second
        dyn_key = second if video_key == first else first
        groups[video_key].extend(groups.pop(dyn_key))


def _duration_text(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "0"
    if seconds <= 0:
        return "0"
    if seconds >= 3600:
        hours, remain = divmod(seconds, 3600)
        minutes, secs = divmod(remain, 60)
        return f"{hours}小时{minutes}分{secs}秒" if secs else f"{hours}小时{minutes}分"
    if seconds >= 60:
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}分{secs}秒" if secs else f"{minutes}分"
    return f"{seconds}秒"


def _pick_type(index: dict, plat: str, kind_group: str, has_images: bool) -> str:
    """投票不可用时按内容挑一个「表里确实存在」的动态类型，避免凭空造选项。"""
    valid = index.get("types") or set()
    if not valid:
        return ""
    if kind_group == "video":
        preferred = "视频投稿"
    elif kind_group == "weibo":
        preferred = "图文" if has_images else "文字"
    else:
        preferred = "图文" if has_images else _FALLBACK_TYPE.get(kind_group, ("",))[0]
    if preferred in valid:
        return preferred
    per_platform = index.get("plat_types") or {}
    for text, _count in (per_platform.get(plat) or Counter()).most_common():
        if text in valid:
            return text
    return ""


def _type_position(index: dict, plat: str, kind_group: str,
                   has_images: bool = False) -> tuple[str, str]:
    """动态类型 / 发布位置：优先用同平台同类行的多数值，且必须是表里已有的选项。

    旧数据里微博等平台大量行的「动态类型」为空，直接兜底成「动态」会写出
    表里根本没有的选项，上传时被判为非法值；这里改为按内容挑现有选项。
    """
    fallback = _FALLBACK_TYPE.get(kind_group, ("", ""))
    valid_types = index.get("types") or set()
    valid_positions = index.get("positions") or set()
    type_value = pos_value = ""
    counter = index["votes"].get((plat, kind_group))
    if counter:
        (type_value, pos_value), _count = counter.most_common(1)[0]
    if not type_value or (valid_types and type_value not in valid_types):
        type_value = _pick_type(index, plat, kind_group, has_images)
    if not pos_value or (valid_positions and pos_value not in valid_positions):
        pos_value = fallback[1] if (not valid_positions or fallback[1] in valid_positions) else ""
    return type_value, pos_value


def _row_from_group(items: list[dict], index: dict) -> dict:
    """同一作品的视频/动态合并成一行：作品链接 + 动态链接(b站)。"""
    def longest(key: str) -> str:
        values = [str(item.get(key) or "") for item in items]
        return max(values, key=len) if values else ""

    video_item = next(
        (item for item in items
         if str(item.get("kind") or "") == "video" and _page_url(item)), None)
    image_item = next(
        (item for item in items
         if str(item.get("kind") or "") in ("image", "note") and _page_url(item)), None)
    dyn_item = next((item for item in items if _dyn_url(item)), None)
    base = video_item or image_item or dyn_item or items[0]

    plat = str(base.get("platform") or "")
    page = _page_url(video_item) if video_item else (
        _page_url(image_item) if image_item else "")
    if not page:
        page = _page_url(base)
    dyn = _dyn_url(dyn_item) if dyn_item else ""

    title = longest("title")
    content = longest("description")
    author = next((str(item.get("author") or "") for item in items
                   if item.get("author")), "")
    durations = []
    for item in items:
        try:
            durations.append(int(item.get("duration") or 0))
        except (TypeError, ValueError):
            continue
    duration = max(durations, default=0)

    if plat == "weibo":
        kind_group = "weibo"
    elif image_item:
        kind_group = "image"
    elif video_item or page:
        kind_group = "video"
    else:
        kind_group = "dynamic"

    images: list[str] = []
    for item in items:
        for url in _item_images(item):
            if url not in images:
                images.append(url)
    type_value, pos_value = _type_position(index, plat, kind_group, bool(images))

    return {
        "作品标题": title or content[:80],
        "发布距今": "",
        "作品内容": content,
        "发布账号": author,
        "封面图": "",
        "动态类型": type_value,
        "发布时间": _datetime_text(base.get("publish_time")),
        "发布平台": PLATFORM_TO_CN.get(plat, plat),
        "发布位置": pos_value,
        "作品时长": _duration_text(duration),
        "作品链接": page,
        "动态链接(b站)": dyn,
        # UI 用：检测/复制图片，不参与 TSV 列
        "_images": images,
    }


# ==================== 直播记录（抖音直播历史） ====================

def _cell_text(value) -> str:
    """腾讯单元格值（文本/链接/多选）→ 纯文本。"""
    if isinstance(value, list):
        parts = [str(v.get("text") or v.get("link") or "") if isinstance(v, dict)
                 else str(v) for v in value]
        return " ".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or "")
    return str(value or "")


def _norm_live_title(text) -> str:
    """标题归一：去括号、标点、空白并转小写，用于同一场次的模糊匹配。"""
    cleaned = re.sub(r"[【】\[\]（）()〈〉《》\s·•:：!！?？~～\-—_/、,，.。\"'“”]+", "",
                     str(text or ""))
    return cleaned.lower()


def _live_link_value(value) -> str:
    """链接单元格（[{text,link}] / {text,link} / 两级字符串 / 纯文本）→ URL。"""
    if isinstance(value, str) and tables.LINK_FIELD_SEP in value:
        return tables.link_url(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("link"):
                return str(item["link"])
        return _cell_text(value)
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "")
    return str(value or "")


def _account_field(acc, key: str) -> str:
    """账号追踪的数据行兼容 dict / 对象两种形态。"""
    if isinstance(acc, dict):
        return str(acc.get(key) or "")
    return str(getattr(acc, key, "") or "")


def _is_douyin_account(acc) -> bool:
    platform = _account_field(acc, "platform").lower()
    if platform == "douyin":
        return True
    return "douyin.com" in _account_field(acc, "url").lower()


_LIVE_ACCOUNT_SUFFIX_RE = re.compile(r"(抖音|快手|微博|B站|bilibili|小红书|LIVE|直播)$",
                                     re.I)


def _live_account_label(name: str, options: set[str]) -> str:
    """账号追踪里的名字 → 直播记录「直播账户」多选值。

    优先用表里已出现过的选项；没有时去掉平台后缀（如「封茗囧菌抖音」→「封茗囧菌」），
    避免多选列因为不认识的选项而写不进去。
    """
    name = str(name or "").strip()
    if not name:
        return ""
    if name in options:
        return name
    base = _LIVE_ACCOUNT_SUFFIX_RE.sub("", name).strip() or name
    if base in options:
        return base
    for option in sorted(options):
        if option and (option in name or name in option):
            return option
    return base


def live_accounts(days: int = 0) -> list[dict]:
    """有直播场次的抖音主播（数据来自账号追踪 xlsx 的「直播历史」sheet）。

    返回 [{name, count, latest}]，按最近一场开播时间倒序；days<=0 表示全部历史。
    """
    from tracker.accounts import load_accounts
    from tracker.douyin_live import load_live_history

    since = _since_date(days)
    out = []
    for acc in load_accounts():
        name = _account_field(acc, "name")
        if not name or not _is_douyin_account(acc):
            continue
        rows = [r for r in load_live_history(name)
                if not since or (str(r.get("start") or ""))[:10] >= since]
        if rows:
            out.append({"name": name, "count": len(rows),
                        "latest": str(rows[0].get("start") or "")[:10]})
    out.sort(key=lambda item: (item.get("latest") or "", item.get("count") or 0),
             reverse=True)
    return out


def _live_row(account: str, session: dict) -> dict:
    """一场抖音直播 → 直播记录自动字段行（直播链接1 稍后按 B 站投稿回填）。"""
    room = str(session.get("room_id") or "").strip()
    start = str(session.get("start") or "")
    end = str(session.get("end") or "")
    values = {
        "直播账户": account,
        "直播标题": str(session.get("title") or ""),
        "开始时间": start[:10],
        "结束时间": end[:10],
        "直播平台": "抖音",
        "直播链接1": "",
    }
    return {"values": values, "day": start[:10], "time": start[11:16],
            "title": _norm_live_title(session.get("title")),
            "link": "", "room": room}


_BILI_VIDEO_URL = "https://www.bilibili.com/video/{}"
_RECORD_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}_(.+)$")


def _replay_index() -> dict[tuple[str, str], list[dict]]:
    """B 站投稿记录 → {(录制日期, 平台): [{bvid, time}]}，用于填直播链接1。

    数据来自 `config/bili_upload.db` 的投稿任务（同一台机器上的录播投稿记录），
    录制文件名格式 `2026-11-16_17-57-09_主播_类型_…_抖音.mp4`。
    """
    import sqlite3

    from toolbox.config_manager import get_config_dir

    path = get_config_dir() / "bili_upload.db"
    out: dict[tuple[str, str], list[dict]] = {}
    if not path.exists():
        return out
    try:
        with sqlite3.connect(str(path), timeout=5) as conn:
            rows = conn.execute("SELECT state, data FROM jobs").fetchall()
    except sqlite3.Error:
        return out
    for state, raw in rows:
        if state != "published":
            continue
        try:
            data = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        bvid = str((data.get("result") or {}).get("bvid") or "").strip()
        if not bvid:
            continue
        for item_path in data.get("paths") or []:
            stem = Path(str(item_path or "")).stem
            match = _RECORD_NAME_RE.search(stem)
            if not match:
                continue
            day, hh, mm, rest = match.groups()
            platform = str(rest).split("_")[-1].strip()
            title = str(data.get("title") or "").strip()
            out.setdefault((day, platform), []).append(
                {"bvid": bvid, "time": f"{hh}:{mm}", "title": title})
    return out


def _pick_replay(candidates: list[dict], session_time: str) -> dict:
    """同一天可能投稿多个视频：取开播时间最接近的那个。"""
    if not candidates:
        return {}
    if len(candidates) == 1 or not session_time:
        return candidates[0]

    def distance(item: dict) -> int:
        text = str(item.get("time") or "")
        if len(text) < 5:
            return 24 * 60
        try:
            return abs((int(text[:2]) * 60 + int(text[3:5]))
                       - (int(session_time[:2]) * 60 + int(session_time[3:5])))
        except ValueError:
            return 24 * 60

    return min(candidates, key=distance)


def _merge_live_values(local_values: dict, remote_values: dict,
                       mode: str, columns) -> dict:
    """重复行按模式合并：只动自动字段，公式列与人工列一律保持原值。"""
    merged = {}
    for name in columns:
        current = _cell_text(remote_values.get(name))
        incoming = local_values.get(name, "")
        if name in LIVE_FORMULA_COLUMNS or name not in LIVE_AUTO_COLUMNS:
            merged[name] = current
        elif mode == "overwrite":
            merged[name] = incoming or current
        else:  # fill：只补空
            merged[name] = current or incoming
    return merged


def build_live_pending(days=30, accounts=None, duplicate_mode="skip",
                       columns=None) -> dict:
    """抖音直播历史 → 直播记录待补行。

    重复判定：优先「直播链接1（房间号）」一致，其次「开始时间（到日）+ 归一标题」。
    因为腾讯表只有日期没有时分、也没有房间号列，房间号只在链接列里体现。
    duplicate_mode：skip 丢弃重复行 / fill 只补空字段 / overwrite 覆盖自动字段。
    """
    started = time.perf_counter()
    mode = duplicate_mode if duplicate_mode in LIVE_DUP_MODES else "skip"
    cols = [str(name) for name in (columns or LIVE_FALLBACK_COLUMNS) if str(name).strip()]
    remote = load_remote(SONG_DOC_KEY, LIVE_TAB_ID)

    by_link: dict[str, dict] = {}
    by_day_title: dict[tuple, dict] = {}
    account_options: set[str] = set()
    for idx, row in enumerate(remote):
        values = row.get("values") or {}
        for part in re.split(r"[、,，/]", _cell_text(values.get("直播账户"))):
            if part.strip():
                account_options.add(part.strip())
        link = _norm_url(_live_link_value(values.get("直播链接1")))
        day = _date_key(values.get("开始时间"))
        title = _norm_live_title(_cell_text(values.get("直播标题")))
        entry = {"values": values, "link": link, "idx": idx}
        if link:
            by_link.setdefault(link, entry)
        if day and title:
            by_day_title.setdefault((day, title), entry)

    from tracker.accounts import load_accounts
    from tracker.douyin_live import load_live_history

    since = _since_date(days)
    wanted = {str(name) for name in (accounts or []) if str(name)}
    replays = _replay_index()
    rows: list[dict] = []
    duplicates = 0
    sessions_total = 0
    with_replay = 0
    claimed: set[int] = set()   # 已被占用的远端行：同日多场时不让后续场次再算「重复」
    for acc in load_accounts():
        name = _account_field(acc, "name")
        if not name or not _is_douyin_account(acc):
            continue
        if wanted and name not in wanted:
            continue
        for session in load_live_history(name):
            start = str(session.get("start") or "")
            if since and start[:10] < since:
                continue
            sessions_total += 1
            item = _live_row(name, session)
            item["values"]["直播账户"] = _live_account_label(name, account_options)
            replay = _pick_replay(replays.get((item["day"], "抖音"), []), item["time"])
            if replay.get("bvid"):
                url = _BILI_VIDEO_URL.format(replay["bvid"])
                # 两级结构：显示文本用投稿标题，URL 单独保存（TSV 只带 URL）
                item["values"]["直播链接1"] = tables.join_link(replay.get("title"), url)
                item["link"] = url
                with_replay += 1
            match = (by_link.get(_norm_url(item["link"])) if item["link"] else None) \
                or by_day_title.get((item["day"], item["title"]))
            if match is None:
                rows.append(dict(item["values"], _images=[], _source="",
                                 _needs_review=False, _dup=""))
                continue
            if match["idx"] in claimed:
                # 同一天多场（第二场 / 重开播）：远端只可能有一行，后续场次留给人工判断
                rows.append(dict(item["values"], _images=[], _source="",
                                 _needs_review=True, _dup="同日多场，需人工确认"))
                continue
            claimed.add(match["idx"])
            duplicates += 1
            if mode == "skip":
                continue
            merged = _merge_live_values(item["values"], match["values"], mode, cols)
            rows.append(dict(merged, _images=[], _source="", _needs_review=True,
                             _dup=LIVE_DUP_MODES[mode]))

    rows.sort(key=lambda row: (row.get("开始时间") or ""), reverse=True)
    timing = {"total": time.perf_counter() - started}
    return {
        "profile": "lives",
        "columns": cols,
        "rows": rows,
        "stats": {
            "local_items": sessions_total,
            "local_groups": sessions_total,
            "synced_groups": duplicates if mode == "skip" else duplicates,
            "pending": len(rows),
            "duplicates": duplicates,
            "mode": mode,
            "since": since,
            "with_replay": with_replay,
            "missing_replay": sessions_total - with_replay,
            "with_images": 0,
            "without_images": len(rows),
            "image_count": 0,
            "timing": timing,
        },
    }


def build_pending(doc_key: str, tab_id: str, days=30, accounts=None,
                  collected=None) -> dict:
    """比对并返回待同步行。

    accounts=None 表示不过滤；"default" 表示只保留腾讯表出现过的发布账号；
    传入列表则按列表过滤（空列表同样视为不过滤）。
    """
    if (doc_key, tab_id) == (SONG_DOC_KEY, SONG_TAB_ID):
        return build_song_pending()
    started = time.perf_counter()
    remote = load_remote(doc_key, tab_id)
    index = _index_of(remote)
    after_remote = time.perf_counter()
    local = collect_local_items(days, collected)
    after_local = time.perf_counter()
    all_local_accounts = sorted({str(item.get("author") or "") for item in local
                                 if item.get("author")})

    if accounts == "default":
        wanted = set(index["accounts"])
    elif accounts:
        wanted = {str(name) for name in accounts if str(name)}
    else:
        wanted = set()
    if wanted:
        local = [item for item in local
                 if str(item.get("author") or "") in wanted]

    groups: dict[tuple, list[dict]] = {}
    for item in local:
        groups.setdefault(_group_key(item), []).append(item)
    _merge_bilibili_pairs(groups)

    pending = []
    synced = 0
    for items in groups.values():
        # 组内任意一条已在腾讯表 → 视为该作品已同步，避免重复行
        if any(_matches(item, index) for item in items):
            synced += 1
            continue
        pending.append(_row_from_group(items, index))
    pending.sort(key=lambda row: (row.get("发布时间") or "",
                                  row.get("发布平台") or ""), reverse=True)
    with_images = sum(1 for row in pending if row.get("_images"))
    image_count = sum(len(row.get("_images") or []) for row in pending)
    finished = time.perf_counter()

    return {
        "profile": "posts",
        "rows": pending,
        "columns": list(COLUMNS),
        "link_columns": list(LINK_COLUMNS),
        "accounts": sorted(index["accounts"]),
        "local_accounts": all_local_accounts,
        "stats": {
            "local_items": len(local),
            "local_groups": len(groups),
            "synced_groups": synced,
            "pending": len(pending),
            "with_images": with_images,
            "without_images": len(pending) - with_images,
"image_count": image_count,
            "remote_rows": len(remote),
            "since": _since_date(days),
            "timing": {
                "remote": round(after_remote - started, 2),
                "local": round(after_local - after_remote, 2),
                "match": round(finished - after_local, 2),
                "total": round(finished - started, 2),
            },
        },
    }


def to_tsv(rows: list[dict], columns=None) -> str:
    """待同步行 → 制表符分隔文本（不含表头，可直接粘贴）。"""
    columns = tuple(columns or COLUMNS)
    lines = []
    for row in rows:
        values = [_clean_cell(row.get(column, "")) for column in columns]
        lines.append("\t".join(values))
    return "\r\n".join(lines)


def _clean_cell(value) -> str:
    text = str(value if value is not None else "")
    if tables.LINK_FIELD_SEP in text:
        # 两级的链接单元格：粘贴用 URL（腾讯文档会把 URL 变成可点链接）
        text = tables.link_url(text) or tables.link_text(text)
    text = html.unescape(text)
    return (text.replace("\r\n", " ").replace("\r", " ")
            .replace("\n", " ").replace("\t", " ").strip())


def default_pending_count(doc_key: str, collected=None) -> int:
    """同步完成后给界面的轻量提示：当前文档默认范围内待同步行数。

    返回 -1 表示当前文档没有可识别的工作表（界面保持原提示）。
    """
    try:
        target = next((item for item in list_targets()
                       if item["doc_key"] == doc_key
                       and item.get("profile") == "posts"), None)
        if not target:
            return -1
        cfg = tc_cfg.load_cfg()
        try:
            days = int(cfg.get("tcdoc_bridge_days", 30) or 30)
        except (TypeError, ValueError):
            days = 30
        result = build_pending(doc_key, target["tab_id"], days=days,
                               accounts="default", collected=collected)
        return int(result["stats"]["pending"])
    except Exception:  # noqa: BLE001 - 提示失败不影响同步结果
        return -1
