"""读取中间层待同步结果，保存本地清单并导入用户明确指定的测试表。

默认预览。--available-fields-only 仅用于此已授权测试表；--skip-missing-images
允许明确跳过不存在的本地图片。正式白名单、既有行和表结构均不修改。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from toolbox import increment_db
from toolbox.tcdoc import bridge, config as tc_cfg
from toolbox.tcdoc.import_local import (
    ALIASES, BEIJING, CHOICE_COLUMNS, MAX_IMAGE_BYTES, ImportErrorDetail, LocalImage,
    LocalImportClient, ManagedAuth, TEST_TARGET, UnknownWriteResult, field_options,
    image_mime, install_readonly_guard, managed_cookie_reader, validate_target, weibo_key,
)
from toolbox.utils import PLATFORM_CN, sanitize_filename


def link_key(link: str) -> str | None:
    """按平台分开主键，禁止标题相同的跨平台作品相互去重。"""
    key = weibo_key(link)
    if key:
        return key
    try:
        url = urlsplit(str(link).strip())
    except ValueError:
        return None
    host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if url.scheme not in {"https", "http"}:
        return None
    patterns = {
        "bilibili.com": ("bilibili:video:", r"^/video/(BV[0-9A-Za-z]+|av\d+)/?$"),
        "t.bilibili.com": ("bilibili:dynamic:", r"^/(\d+)/?$"),
        "douyin.com": ("douyin:", r"^/(?:video|note)/(\d+)/?$"),
        "kuaishou.com": ("kuaishou:", r"^/short-video/([A-Za-z0-9_-]+)/?$"),
        "xiaohongshu.com": ("xiaohongshu:", r"^/(?:explore|discovery/item)/([A-Fa-f0-9]+)/?$"),
    }
    if host not in patterns:
        return None
    prefix, pattern = patterns[host]
    match = re.match(pattern, url.path)
    return prefix + match[1] if match else None


def read_db(path: Path):
    if not path.is_file():
        raise ImportErrorDetail(f"中间层来源数据库缺失：{path.name}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def pending_result(project: Path, days=30):
    """复用中间层算法，两个实际 DB 读取入口改为独立进程中的只读快照。

    不调用其 init_db、迁移、WAL 设置、真实配置初始化或 GUI 登录流程。
    """
    since = bridge._since_date(days)
    with closing(read_db(project / "download/腾讯文档数据/tables.db")) as conn:
        remote = [{"values": json.loads(r[0])} for r in conn.execute(
            "SELECT data_json FROM rows WHERE doc_key=? AND tab_id=? ORDER BY row_no",
            ("DRnd3bkhkQUR1aEJr", "ss_h2uh2s"))]
    with closing(read_db(project / "config/collected.db")) as conn:
        collected = [json.loads(r[0]) for r in conn.execute(
            "SELECT data FROM videos WHERE json_extract(data,'$.publish_time')>=? ORDER BY rowid", (since,))]
    inc = []
    with closing(read_db(project / "config/increment.db")) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for platform in PLATFORM_CN:
            if platform not in tables:
                continue
            for row in conn.execute(f'SELECT * FROM "{platform}" WHERE publish_time>=?', (since,)):
                item = dict(row)
                item["platform"] = platform
                inc.append(item)
    with patch.object(bridge, "load_remote", return_value=remote), \
            patch.object(increment_db, "iter_items", return_value=inc):
        result = bridge.build_pending("DRnd3bkhkQUR1aEJr", "ss_h2uh2s", days=days,
                                      accounts="default", collected=collected)
        sources = bridge.collect_local_items(days, collected)
    wanted = set(result["accounts"])
    return result, [i for i in sources if i.get("author") in wanted]


def _source_paths(project: Path, item: dict) -> dict[str, list[Path]]:
    platform = item.get("platform")
    if platform not in PLATFORM_CN:
        return {}
    author = sanitize_filename(item.get("author") or "未知")
    title = sanitize_filename(item.get("title") or "", 50)
    day = re.sub(r"[^0-9]", "", str(item.get("publish_time") or "")[:10])
    directory = project / "download/已获取数据" / (PLATFORM_CN[platform] + "_媒体") / author
    stem = f"{day}_{author}_{title}"
    owner_path = directory / ".stem_owners.json"
    owners = json.loads(owner_path.read_text(encoding="utf-8-sig")) if owner_path.is_file() else {}
    owned = [s for s, owner in owners.items() if owner == str(item.get("video_id") or "")]
    stems = list(dict.fromkeys([stem] + owned))
    raw = json.loads(item.get("raw_json") or "{}")
    album = item.get("images") or raw.get("images") or raw.get("image_urls") or []
    if not isinstance(album, list):
        raise ImportErrorDetail("本地图片列表格式异常")
    mapping = {}
    for index, url in enumerate(album, 1):
        if not isinstance(url, str):
            continue
        suffix = ".jpg" if len(album) == 1 else f"_{index}.jpg"
        mapping.setdefault(url, []).extend(directory / (s + suffix) for s in stems)
    cover = str(item.get("cover_url") or "")
    if cover and cover not in album:
        ext = Path(urlsplit(cover).path).suffix.lower()
        ext = ext if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"} else ".jpg"
        # 旧 B站下载器把封面直接保存为 stem.jpg；新媒体下载器使用 _封面 后缀。
        mapping.setdefault(cover, []).extend(
            p for s in stems for p in (directory / (s + "_封面" + ext), directory / (s + ext)))
    return mapping


def stem_images(project: Path, columns: dict) -> list[Path]:
    """视频类条目的本地封面兜底：按「日期_账号_标题」定位 _封面 / 同名图片。

    仅在中间层只给出一张图（视频封面）且 URL 精确匹配失败时使用，
    避免多图微博按标题误配到别的图片。
    """
    platform = bridge.CN_TO_PLATFORM.get(str(columns.get("发布平台") or ""))
    if platform not in PLATFORM_CN:
        return []
    author = sanitize_filename(columns.get("发布账号") or "未知")
    title = sanitize_filename(columns.get("作品标题") or "", 50)
    day = re.sub(r"[^0-9]", "", str(columns.get("发布时间") or "")[:10])
    directory = project / "download/已获取数据" / (PLATFORM_CN[platform] + "_媒体") / author
    if not directory.is_dir():
        return []
    suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    stem = f"{day}_{author}_{title}"
    covers = sorted(p for p in directory.glob(stem + "_封面.*") if p.suffix.lower() in suffixes)
    plain = sorted(p for p in directory.glob(stem + ".*") if p.suffix.lower() in suffixes)
    return covers + plain


def build_catalogue(project: Path, sources: list[dict]) -> dict:
    """本地条目 → 图片 URL 到候选文件的映射（GUI 与命令行共用）。"""
    catalogue: dict[str, list[Path]] = {}
    for item in sources:
        for url, paths in _source_paths(project, item).items():
            catalogue.setdefault(url, []).extend(paths)
    return catalogue


def records_from_rows(project: Path, rows: list[dict], catalogue: dict):
    """中间层待同步行 → 可导入记录（含本地图片路径与缺图槽位）。"""
    project = Path(project).resolve()
    records, missing = [], []
    for row in rows:
        columns = {name: str(row.get(name) or "") for name in bridge.COLUMNS}
        key = link_key(columns["作品链接"] or columns["动态链接(b站)"])
        if not key:
            raise ImportErrorDetail("待同步行缺少可确定身份的原帖链接")
        paths = []
        gaps = []
        album = row.get("_images") or []
        for slot, url in enumerate(album, 1):
            candidates = list(dict.fromkeys(catalogue.get(url, [])))
            existing = [p for p in candidates if p.is_file()]
            if not existing and len(album) == 1:
                # 视频条目的封面 URL 带有时效签名，精确匹配常落空；
                # 单图条目按本地 stem 兜底，多图条目不做模糊匹配。
                existing = stem_images(project, columns)
            if existing:
                chosen = existing[0].resolve()
                if not chosen.is_relative_to(project / "download/已获取数据"):
                    raise ImportErrorDetail("图片路径越出本地媒体目录")
                paths.append(chosen.relative_to(project).as_posix())
            else:
                gaps.append(slot)
                # URL 仅供本进程必要下载使用，不写清单、日志或报告。
                missing.append({"key": key, "slot": slot, "url": url,
                                "path": candidates[0] if candidates else None})
        records.append({"key": key, "columns": columns, "images": paths,
                        "expected_images": len(row.get("_images") or []), "missing_slots": gaps})
    return records, missing


# ==================== 完整歌曲：封面下载 + 记录 ====================

_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
             "image/bmp": ".bmp", "image/webp": ".webp"}
_COVER_REFERERS = (("gtimg.cn", "https://y.qq.com/"), ("music.126.net", "https://music.163.com/"),
                   ("kugou.com", "https://www.kugou.com/"), ("kuwo.cn", "https://www.kuwo.cn/"),
                   ("sinaimg.cn", "https://weibo.com/"))


def _cover_referer(url: str) -> str:
    host = urlsplit(url).hostname or ""
    for key, referer in _COVER_REFERERS:
        if host.endswith(key):
            return referer
    return "https://y.qq.com/"


def _cover_path(url: str, cache: Path) -> Path | None:
    for ext in _MIME_EXT.values():
        path = cache / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext)
        if path.is_file():
            return path
    return None


def _fetch_cover(url: str, cache: Path) -> Path | None:
    """下载完整歌曲封面到缓存目录；失败返回 None，不输出 URL。"""
    if not url.startswith(("https://", "http://")):
        return None
    cached = _cover_path(url, cache)
    if cached:
        return cached
    try:
        import httpx

        cache.mkdir(parents=True, exist_ok=True)
        response = httpx.get(url, timeout=20, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0",
                                      "Referer": _cover_referer(url)})
        response.raise_for_status()
        content = response.content
        if not content or len(content) > MAX_IMAGE_BYTES:
            return None
        ext = _MIME_EXT[image_mime(content)]
        path = cache / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext)
        path.write_bytes(content)
        return path
    except Exception:  # noqa: BLE001 - 单张封面失败按缺图处理
        return None


def song_records_from_rows(project: Path, rows: list[dict]) -> list[dict]:
    """完整歌曲待同步行 → 可导入记录（封面下载到本地缓存）。"""
    from concurrent.futures import ThreadPoolExecutor

    project = Path(project).resolve()
    cache = tc_cfg.data_dir() / "中间层" / "封面缓存"

    def fetch(row):
        url = str(row.get("_cover_url") or "").strip()
        return _fetch_cover(url, cache) if url else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        covers = list(pool.map(fetch, rows))
    records = []
    for row, cover in zip(rows, covers):
        columns = {name: str(row.get(name) or "") for name in bridge.SONG_COLUMNS}
        key = "song:" + bridge._song_title_key(columns["歌曲名称"])
        if key == "song:":
            raise ImportErrorDetail("待同步歌曲缺少歌曲名称")
        expected = 1 if str(row.get("_cover_url") or "").strip() else 0
        images, gaps = [], []
        if cover is not None:
            try:
                image = LocalImage.inspect(cover, cache)
            except ImportErrorDetail:
                image = None
            if image is not None:
                images.append(image)
            else:
                gaps.append(1)
        elif expected:
            gaps.append(1)
        records.append({
            "key": key,
            "title": columns["歌曲名称"],
            "account": columns["演唱者"],
            "platform": "多平台",
            "time": columns["首发日期"],
            "columns": columns,
            "images": [str(image.path) for image in images],
            "expected_images": expected,
            "missing_slots": gaps,
            "_images": images,
        })
    return records


SONG_TEXT_FIELDS = ("歌曲名称", "演唱者", "完整歌词", "歌曲时长",
                    "作词人", "作曲人", "出品方", "所属专辑")
SONG_CHOICE_FIELDS = ("语言", "合唱形式")
SONG_DATE_FIELDS = ("首发日期",)


def make_song_plan(record: dict, fields: list[dict]):
    """完整歌曲字段计划：只写明确支持的文本/日期/单选/链接/封面字段。"""
    columns = record["columns"]
    by_title: dict[str, list[dict]] = {}
    for field in fields:
        by_title.setdefault(str(field.get("fieldTitle") or ""), []).append(field)

    def unique(name: str) -> dict:
        hits = by_title.get(name) or []
        if len(hits) != 1:
            raise ImportErrorDetail(f"字段无法唯一匹配：{name}")
        return hits[0]

    values, kinds, skipped = {}, {}, {}
    names = SONG_TEXT_FIELDS + SONG_CHOICE_FIELDS + SONG_DATE_FIELDS + bridge.SONG_LINK_COLUMNS
    for name in names:
        value = str(columns.get(name) or "").strip()
        if not value:
            continue
        field = unique(name)
        kind = field.get("fieldType")
        if name in SONG_TEXT_FIELDS and kind == 1:
            encoded = [{"type": "text", "text": value}]
        elif name in SONG_DATE_FIELDS and kind == 4:
            try:
                date = datetime.fromisoformat(value)
            except ValueError:
                raise ImportErrorDetail(f"{name}无法识别") from None
            date = date.replace(tzinfo=BEIJING) if date.tzinfo is None else date.astimezone(BEIJING)
            encoded = str(int(date.timestamp() * 1000))
        elif name in SONG_CHOICE_FIELDS and kind in {9, 17}:
            options = [o for o in field_options(field) if o.get("text") == value and o.get("id")]
            if len(options) != 1:
                skipped[name] = "没有匹配的既有选项"
                continue
            encoded = [options[0]]
        elif name in bridge.SONG_LINK_COLUMNS and kind == 8:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                skipped[name] = "链接无法识别"
                continue
            encoded = [{"type": "url", "text": value, "link": value}]
        else:
            skipped[name] = "字段类型不匹配"
            continue
        values[field["fieldTitle"]] = encoded
        kinds[field["fieldTitle"]] = kind

    if record.get("_images"):
        field = unique("封面图")
        if field.get("fieldType") != 5:
            raise ImportErrorDetail("封面图字段不是图片类型")
        kinds[field["fieldTitle"]] = field["fieldType"]

    if "歌曲名称" not in values:
        raise ImportErrorDetail("必需字段无法写入：歌曲名称")
    return values, kinds, skipped


def song_remote_keys(rows: list[dict]) -> dict[str, list]:
    """远端完整歌曲按歌曲名建立身份键，用于跳过已存在歌曲。"""
    keys: dict[str, list] = {}
    for row in rows:
        values = row.get("values") or {}
        key = bridge._song_title_key(bridge._song_cell_text(values.get("歌曲名称")))
        if key:
            keys.setdefault(key, []).append(row.get("recordID"))
    return keys


def count_song_title_keys(rows: list[dict]) -> dict[str, int]:
    return {key: len(ids) for key, ids in song_remote_keys(rows).items()}


def make_manifest(project=ROOT, days=30):
    project = project.resolve()
    result, sources = pending_result(project, days)
    catalogue = build_catalogue(project, sources)
    records, missing = records_from_rows(project, result["rows"], catalogue)
    manifest = {"version": 1, "created_at": datetime.now(BEIJING).isoformat(),
                "source": {"doc_key": "DRnd3bkhkQUR1aEJr", "tab_id": "ss_h2uh2s", "stats": result["stats"]},
                "records": records}
    return manifest, missing


def load_manifest(path: Path, project=ROOT, skip_missing=False):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        raise ImportErrorDetail("无法读取中间层导入清单") from None
    if data.get("version") != 1 or not isinstance(data.get("records"), list) or not 1 <= len(data["records"]) <= 50:
        raise ImportErrorDetail("中间层导入清单版本或记录数异常")
    records = copy.deepcopy(data["records"])
    seen = set()
    for record in records:
        columns = record["columns"]
        key = link_key(columns.get("作品链接") or columns.get("动态链接(b站)"))
        if not key or key != record.get("key") or key in seen:
            raise ImportErrorDetail("导入清单主键缺失、不一致或重复")
        seen.add(key)
        if record.get("missing_slots") and not skip_missing:
            raise ImportErrorDetail("有本地缺图，请补齐或显式选择 --skip-missing-images")
        record["_images"] = [LocalImage.inspect(project / p, project / "download/已获取数据")
                             for p in record["images"]]
        if len(record["_images"]) + len(record.get("missing_slots", [])) != record["expected_images"]:
            raise ImportErrorDetail("图集数量与中间层清单不一致")
    return records


def make_plan(record: dict, fields: list[dict], available_only=False):
    columns = record["columns"]
    aliases = {**ALIASES, "作品时长": ("作品时长",), "动态链接(b站)": ("动态链接(b站)",)}
    values, kinds, skipped = {}, {}, {}
    for name, names in aliases.items():
        value = columns.get(name, "")
        hits = [f for f in fields if f.get("fieldTitle") in names]
        if len(hits) != 1:
            if not available_only:
                raise ImportErrorDetail(f"字段无法唯一匹配：{name}")
            skipped[name] = "目标缺少字段"
            continue
        f = hits[0]
        title, kind = f["fieldTitle"], f["fieldType"]
        if name == "封面图":
            if kind != 5:
                raise ImportErrorDetail("封面图字段不是图片类型")
            kinds[title] = kind
            continue
        if name in CHOICE_COLUMNS and kind in {9, 17}:
            options = [o for o in field_options(f) if o.get("text") == value and o.get("id")]
            if len(options) != 1:
                if not available_only:
                    raise ImportErrorDetail(f"字段没有唯一既有选项：{name}")
                skipped[name] = "没有匹配的既有选项"
                continue
            encoded = [options[0]]
        elif name == "发布时间" and kind == 4:
            try:
                date = datetime.fromisoformat(value)
            except ValueError:
                raise ImportErrorDetail("发布时间无法识别") from None
            date = date.replace(tzinfo=BEIJING) if date.tzinfo is None else date.astimezone(BEIJING)
            encoded = str(int(date.timestamp() * 1000))
        elif name in {"作品链接", "动态链接(b站)"} and kind == 8:
            if not value:
                continue
            if not link_key(value):
                raise ImportErrorDetail("原帖链接无法确定平台身份")
            encoded = [{"type": "url", "text": value, "link": value}]
        elif kind == 1:
            encoded = [{"type": "text", "text": value}]
        else:
            if not available_only:
                raise ImportErrorDetail(f"字段类型不匹配：{name}")
            skipped[name] = "字段类型不匹配"
            continue
        values[title] = encoded
        kinds[title] = kind
    for required in ("作品标题", "作品内容", "发布时间"):
        if required not in values:
            raise ImportErrorDetail(f"必需字段无法写入：{required}")
    if not any(kinds.get(name) == 8 for name in ("作品链接", "链接", "动态链接(b站)")):
        raise ImportErrorDetail("没有可写的原帖链接字段")
    if not any(kind == 5 for kind in kinds.values()):
        raise ImportErrorDetail("没有可写的图片字段")
    return values, kinds, skipped


def count_remote_keys(rows: list[dict]) -> dict[str, int]:
    """按原帖链接统计出现次数：>1 说明表里已有重复行。"""
    counts: dict[str, int] = {}
    for row in rows:
        for key in remote_keys([row]):
            counts[key] = counts.get(key, 0) + 1
    return counts


def remote_keys(rows: list[dict]):
    keys = {}
    for row in rows:
        values = row.get("values") or {}
        for title in ("作品链接", "链接", "动态链接(b站)"):
            for part in values.get(title) or []:
                key = link_key(part.get("link", "")) if isinstance(part, dict) else None
                if key:
                    keys.setdefault(key, []).append(row["recordID"])
    return keys


def verify(row: dict, values: dict, kinds: dict, images: list[LocalImage]):
    saved, checks = row.get("values") or {}, {}
    for title, kind in kinds.items():
        actual, expected = saved.get(title), values.get(title)
        if kind == 5:
            entries = actual or []
            checks[title] = isinstance(entries, list) and len(entries) == len(images) and all(isinstance(i, dict) for i in entries)
            if checks[title] and images:
                checks[title] = ([i.get("title") for i in entries] == [i.path.name for i in images] and
                                 len({i.get("id") for i in entries if i.get("id")}) == len(images))
        elif kind == 1:
            checks[title] = "".join(str(i.get("text") or "") for i in actual or [] if isinstance(i, dict)) == expected[0]["text"]
        elif kind == 8:
            checks[title] = isinstance(actual, list) and any(i.get("link") == expected[0]["link"] for i in actual if isinstance(i, dict))
        elif kind == 4:
            checks[title] = str(actual) == str(expected)
        else:
            checks[title] = isinstance(actual, list) and [i.get("id") for i in actual] == [i.get("id") for i in expected]
    return checks


CHOICE_TITLES = ("发布账号", "发布平台", "动态类型", "发布位置")


def choice_values(record: dict, fields: list[dict]):
    """为四个单选列构造写入值：已有同名选项复用其 id，缺失时只给文本由表内快速添加。"""
    values, skipped = {}, {}
    for title in CHOICE_TITLES:
        text = str(record["columns"].get(title) or "").strip()
        hits = [f for f in fields if f.get("fieldTitle") == title]
        if len(hits) != 1:
            skipped[title] = "目标缺少字段"
            continue
        field = hits[0]
        if field.get("fieldType") not in {9, 17}:
            skipped[title] = "字段不是单选类型"
            continue
        if not text:
            skipped[title] = "本地值为空"
            continue
        existing = [o for o in field_options(field) if o.get("text") == text and o.get("id")]
        values[title] = [{"id": existing[0]["id"], "text": text}] if existing else [{"text": text}]
    return values, skipped


class ChoiceFillClient(LocalImportClient):
    """仅补写四个单选列：沿用同一身份校验，拒绝其他改动。"""

    def update_records(self, records: list[dict]) -> list[dict]:
        validate_target(self.doc_key, self.tab_id, self.allow_test)
        if (self.doc_key, self.tab_id) != TEST_TARGET:
            raise ImportErrorDetail("补写选项仅允许本次授权测试目标")
        if not records:
            return []
        for record in records:
            if not str(record.get("recordID") or "") or set(record.get("values") or {}) - set(CHOICE_TITLES):
                raise ImportErrorDetail("只允许按 recordID 补写四个单选列")
        path = (f"/smartbook/v2/files/{quote(self.file_id, safe='')}"
                f"/sheets/{quote(self.tab_id, safe='')}")
        data = self._request("POST", path, {"updateRecords": {"records": records}}, record_write=True)
        result = data.get("updateRecords")
        rows = result.get("records") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != len(records):
            raise UnknownWriteResult("补写结果缺少逐条回执，请按 recordID 读回核对")
        return rows


def fill_choices(client, records, *, apply=False, emit=lambda event: None):
    """按中间层清单补写四个单选列，并逐条读回核对文本。"""
    fields = client.get_fields()
    before = client.get_records()
    by_key = {}
    for row in before:
        for key, ids in remote_keys([row]).items():
            by_key.setdefault(key, ids[0])
    plan, updates = [], []
    for record in records:
        rid = by_key.get(record["key"])
        values, skipped = choice_values(record, fields)
        plan.append({"key": record["key"], "title": record["columns"]["作品标题"],
                     "record_id": rid, "values": {k: v[0].get("text") for k, v in values.items()},
                     "skipped_fields": skipped})
        if values and rid:
            updates.append({"recordID": rid, "values": values})
    emit({"phase": "fill_preview", "apply": apply, "rows_before": len(before),
          "planned": len(updates), "records": plan})
    result = {"phase": "fill_complete", "apply": apply, "rows_before": len(before),
              "updated": 0, "verified": 0, "failed": []}
    if not apply:
        return result
    if client.get_fields() != fields:
        raise ImportErrorDetail("字段在预览后变化，请重新预览")
    receipts = client.update_records(updates)
    result["updated"] = len(receipts)
    after = {row["recordID"]: row for row in client.get_records()}
    for item in updates:
        saved = after.get(item["recordID"])
        ok = bool(saved)
        if ok:
            for title, expected in item["values"].items():
                actual = [i.get("text") for i in (saved.get("values") or {}).get(title) or [] if isinstance(i, dict)]
                if actual != [expected[0].get("text")]:
                    ok = False
        if ok:
            result["verified"] += 1
        else:
            result["failed"].append(item["recordID"])
    emit(result)
    if result["failed"]:
        raise ImportErrorDetail("补写后读回不一致，请按 record_id 核对")
    return result


def _record_title(record: dict) -> str:
    return str(record.get("title") or record["columns"].get("歌曲名称")
               or record["columns"].get("作品标题") or "")


def run(client, records, *, apply=False, available_only=False, verify_each=True,
        emit=lambda event: None, profile="posts"):
    """预览并导入记录。

    verify_each=True（命令行默认）逐条新增后立刻读回，异常即停；
    界面批量上传传 False：整批新增完只读回一次，避免每条都翻完整张表。
    profile="songs" 仅用于完整歌曲：按歌曲名去重，只新增。
    """
    if available_only and (client.doc_key, client.tab_id) != TEST_TARGET:
        raise ImportErrorDetail("只写可用字段的例外仅允许本次授权测试目标")
    fields = client.get_fields()
    if profile == "songs":
        plans = [(record, *make_song_plan(record, fields)) for record in records]
    else:
        plans = [(record, *make_plan(record, fields, available_only)) for record in records]
    before = client.get_records()
    known = ({"song:" + key for key in song_remote_keys(before)}
             if profile == "songs" else set(remote_keys(before)))
    emit({"phase": "preview", "apply": apply, "doc_key": client.doc_key, "tab_id": client.tab_id,
          "records": [{"key": r["key"], "title": _record_title(r), "images": len(r["_images"]),
                       "missing_images": len(r["missing_slots"]), "skipped_fields": skipped,
                       "operation": "skip" if r["key"] in known else "add"}
                      for r, v, k, skipped in plans]})
    receipt = {"phase": "complete", "doc_key": client.doc_key, "tab_id": client.tab_id, "apply": apply,
               "rows_before": len(before), "rows_after": len(before), "added": [], "skipped": [],
               "uploaded_images": 0, "missing_images": sum(len(r["missing_slots"]) for r in records),
               "updated": 0, "deleted": 0}
    if not apply:
        return receipt

    pending_verify = []
    for record, values, kinds, skipped in plans:
        try:
            if record["key"] in known:
                receipt["skipped"].append({"key": record["key"], "record_ids": []})
                continue
            images = record["_images"]
            for image in images:
                image.read_verified()
            tickets = []
            for image in images:
                tickets.append(client.upload_image(image))
                receipt["uploaded_images"] += 1
                emit({"phase": "image_uploaded", "key": record["key"], "name": image.path.name})
            values = copy.deepcopy(values)
            if tickets:
                image_title = next(title for title, kind in kinds.items() if kind == 5)
                values[image_title] = [{"imageID": ticket} for ticket in tickets]
            rid = client.add_record(values)
            known.add(record["key"])
            added = {"key": record["key"], "title": _record_title(record), "record_id": rid,
                     "image_count": len(images), "missing_images": len(record["missing_slots"]),
                     "skipped_fields": skipped, "verified": False}
            receipt["added"].append(added)
            receipt["rows_after"] = receipt["rows_before"] + len(receipt["added"])
            emit({"phase": "record_created", **added})
            if verify_each:
                fresh = client.get_records()
                saved = [row for row in fresh if row.get("recordID") == rid]
                if len(saved) != 1:
                    raise ImportErrorDetail("新增记录未查回，请按 record_id 核对")
                checks = verify(saved[0], values, kinds, images)
                added.update({"verified": all(checks.values()), "checks": checks})
                receipt["rows_after"] = len(fresh)
                emit({"phase": "readback", **added})
                if not added["verified"]:
                    raise ImportErrorDetail("读回不一致，停止后续条目")
            else:
                pending_verify.append((added, values, kinds, images))
        except ImportErrorDetail as exc:
            emit({**receipt, "phase": "stopped", "key": record["key"], "error": str(exc),
                  "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
            raise

    if pending_verify:
        fresh = client.get_records()
        saved_by_id = {str(row.get("recordID")): row for row in fresh if row.get("recordID")}
        counts = (count_song_title_keys(fresh) if profile == "songs"
                  else count_remote_keys(fresh))
        for added, values, kinds, images in pending_verify:
            # 同一原帖链接/歌曲名在表里出现多次 = 之前已存在或被并发写入，标记出来
            lookup = added["key"][5:] if profile == "songs" else added["key"]
            added["duplicate"] = counts.get(lookup, 0) > 1
            saved = saved_by_id.get(str(added["record_id"]))
            if saved is None:
                emit({"phase": "readback", **added})
                continue
            checks = verify(saved, values, kinds, images)
            added.update({"verified": all(checks.values()), "checks": checks})
            emit({"phase": "readback", **added})
        receipt["rows_after"] = len(fresh)
        receipt["duplicates"] = [item["key"] for item in receipt["added"] if item.get("duplicate")]
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-key", required=True)
    parser.add_argument("--tab-id", required=True)
    parser.add_argument("--allow-test-document", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--snapshot", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--available-fields-only", action="store_true")
    parser.add_argument("--skip-missing-images", action="store_true")
    parser.add_argument("--fill-choices", action="store_true",
                        help="按清单补写发布账号/发布平台/动态类型/发布位置四个单选列")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    try:
        validate_target(args.doc_key, args.tab_id, args.allow_test_document)
        if args.snapshot and args.fill_choices:
            raise ImportErrorDetail("生成清单与补写单选列不能同时执行")
        if args.snapshot:
            if not args.manifest.resolve().is_relative_to(ROOT / "download/腾讯文档数据/中间层"):
                raise ImportErrorDetail("中间层清单只能保存在专用目录")
            data, missing = make_manifest(days=args.days)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            emit({"phase": "snapshot", "file": str(args.manifest.resolve()), "stats": data["source"]["stats"],
                  "local_images": sum(len(r["images"]) for r in data["records"]),
                  "missing_images": len(missing), "missing": [{"key": i["key"], "slot": i["slot"],
                    "name": i["path"].name if i["path"] else None} for i in missing]})
            return 0
        install_readonly_guard()
        records = load_manifest(args.manifest, skip_missing=args.skip_missing_images)
        auth = ManagedAuth(managed_cookie_reader())
        client_cls = ChoiceFillClient if args.fill_choices else LocalImportClient
        client = client_cls(auth, args.doc_key, args.tab_id, args.allow_test_document)
        client.connect()
        if args.fill_choices:
            emit(fill_choices(client, records, apply=args.apply, emit=emit))
        else:
            emit(run(client, records, apply=args.apply,
                     available_only=args.available_fields_only, emit=emit))
        return 0
    except ImportErrorDetail as exc:
        emit({"phase": "error", "error": str(exc), "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
        return 3 if isinstance(exc, UnknownWriteResult) else 2


if __name__ == "__main__":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
