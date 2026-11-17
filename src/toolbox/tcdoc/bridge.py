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


def _increment_items(since: str) -> list[dict]:
    from toolbox import increment_db

    # 测试或导入脚本会替换 iter_items 注入虚构数据，此时必须尊重替换结果
    if getattr(increment_db.iter_items, "__module__", "") != "toolbox.increment_db":
        return increment_db.iter_items(since)
    rows = _increment_items_ro(since)
    if rows is not None:
        return rows
    return increment_db.iter_items(since)


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


# ==================== 腾讯表格 ====================

def _is_sync_sheet(sheet: dict) -> bool:
    names = {str(col.get("name") or "") for col in sheet.get("columns") or []}
    return all(name in names for name in REQUIRED_COLUMNS) and any(
        name in names for name in LINK_COLUMNS)


def list_targets() -> list[dict]:
    """本地已同步、且字段像「官方动态」的工作表清单。"""
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
            if sheet.get("skipped") or not _is_sync_sheet(sheet):
                continue
            targets.append({
                "doc_key": key,
                "doc_name": doc.get("name") or key,
                "doc_url": doc.get("url") or "",
                "tab_id": sheet["tab_id"],
                "tab_name": sheet["tab_name"],
                "n_rows": sheet.get("n_rows") or 0,
                "columns": sheet.get("columns") or [],
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


def _iter_remote_pages(doc_key: str, tab_id: str) -> list[dict]:
    rows = []
    offset = 0
    while True:
        page, total = tables.rows_page(doc_key, tab_id, offset, 1000)
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        if offset >= total:
            break
    return rows


def _remote_snapshot(doc_key: str, tab_id: str) -> tuple[list[dict], dict]:
    """同一份存档只解析一次；表格库被同步写入后按文件戳自动失效。"""
    try:
        stamp = _file_stamp(Path(tables.db_path()))
    except Exception:  # noqa: BLE001
        stamp = (0, 0)
    key = (doc_key, tab_id)
    cached = _REMOTE_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    rows = _read_remote_ro(doc_key, tab_id)
    if rows is None:
        rows = _iter_remote_pages(doc_key, tab_id)
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
        return ""
    if seconds <= 0:
        return ""
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
        "rows": pending,
        "columns": list(COLUMNS),
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


def to_tsv(rows: list[dict]) -> str:
    """待同步行 → 制表符分隔文本（不含表头，可直接粘贴）。"""
    lines = []
    for row in rows:
        values = [_clean_cell(row.get(column, "")) for column in COLUMNS]
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
                       if item["doc_key"] == doc_key), None)
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
