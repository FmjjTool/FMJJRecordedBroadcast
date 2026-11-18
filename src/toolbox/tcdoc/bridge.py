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
_SONG_COLUMNS_CACHE: dict[str, tuple] = {}

_SONG_PLATFORM_COLUMNS = {"qq": "QQ音乐", "netease": "网易云音乐", "kugou": "酷狗音乐",
                          "kuwo": "酷我音乐", "fivesing": "5sing", "soda": "汽水音乐"}
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
            if key == SONG_DOC_KEY and sheet["tab_id"] == SONG_TAB_ID:
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
