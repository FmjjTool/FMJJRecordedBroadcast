"""读取中间层待同步结果，保存本地清单并导入用户明确指定的测试表。

默认预览。--available-fields-only 仅用于此已授权测试表；--skip-missing-images
允许明确跳过不存在的本地图片。正式白名单、既有行和表结构均不修改。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
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
from toolbox.tcdoc import bridge, comment_images, config as tc_cfg, push_history, tables
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
    owners = _stem_owners(directory)
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
        # 下载器会按 Content-Type 改扩展名（抖音图集存成 .webp、.png 原图存成 .png），
        # 候选必须覆盖所有图片扩展名，不能只按 URL 后缀或 .jpg 猜。
        suffix = "" if len(album) == 1 else f"_{index}"
        mapping.setdefault(url, []).extend(
            directory / (s + suffix + ext) for s in stems for ext in _IMAGE_SUFFIXES)
    cover = str(item.get("cover_url") or "")
    if cover and cover not in album:
        # 旧 B站下载器把封面直接保存为 stem.jpg；新媒体下载器使用 _封面 后缀。
        mapping.setdefault(cover, []).extend(
            directory / (s + "_封面" + ext) for s in stems for ext in _IMAGE_SUFFIXES)
        mapping.setdefault(cover, []).extend(
            directory / (s + ext) for s in stems for ext in _IMAGE_SUFFIXES)
    return mapping


_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

# 目录名 → (目录 mtime_ns, {小写文件名: 路径})；作者媒体目录动辄上万文件，
# 逐行 glob 会把比对拖到分钟级，整批行共用一次扫描结果。
# 有界缓存：单条目可达数万 Path，条目数超限时按插入序淘汰最旧目录，防长期增长。
_DIR_INDEX_MAX = 8
_DIR_INDEX: dict[str, tuple[int, dict[str, Path]]] = {}


def _dir_index(directory: Path) -> dict[str, Path]:
    directory = Path(directory)
    try:
        stamp = directory.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = str(directory)
    cached = _DIR_INDEX.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    files: dict[str, Path] = {}
    if stamp:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_file():
                            files[entry.name.lower()] = Path(entry.path)
                    except OSError:
                        continue
        except OSError:
            files = {}
    _DIR_INDEX.pop(key, None)
    _DIR_INDEX[key] = (stamp, files)
    while len(_DIR_INDEX) > _DIR_INDEX_MAX:
        _DIR_INDEX.pop(next(iter(_DIR_INDEX)))
    return files


_OWNERS_CACHE: dict[str, tuple[int, dict]] = {}
_OWNERS_CACHE_MAX = 64


def _stem_owners(directory: Path) -> dict:
    """作者目录的 .stem_owners.json（按文件 mtime 缓存，避免逐条重复解析）。"""
    path = Path(directory) / ".stem_owners.json"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(path)
    cached = _OWNERS_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        owners = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        owners = {}
    if not isinstance(owners, dict):
        owners = {}
    _OWNERS_CACHE.pop(key, None)
    _OWNERS_CACHE[key] = (stamp, owners)
    while len(_OWNERS_CACHE) > _OWNERS_CACHE_MAX:
        _OWNERS_CACHE.pop(next(iter(_OWNERS_CACHE)))
    return owners


def _stem_files(directory: Path, stem: str) -> list[Path]:
    """按「日期_账号_标题」取本地图片：先 _封面 后同名（一次查表，不 glob）。"""
    index = _dir_index(directory)
    stem = stem.lower()
    covers = [index.get(stem + "_封面" + suffix) for suffix in _IMAGE_SUFFIXES]
    plain = [index.get(stem + suffix) for suffix in _IMAGE_SUFFIXES]
    return [path for path in covers + plain if path is not None]


def _existing_files(paths) -> list[Path]:
    """候选路径中真实存在的文件（按目录扫描缓存判断，不逐条 stat）。

    同一个 URL 常被成百上千条作品共用（微博默认缩略图），必须先按目录分组，
    否则每条候选都要一次 stat，比对会被拖到几分钟。
    """
    grouped: dict[Path, list[Path]] = {}
    for path in paths:
        path = Path(path)
        grouped.setdefault(path.parent, []).append(path)
    found: list[Path] = []
    for directory, group in grouped.items():
        index = _dir_index(directory)
        for path in group:
            hit = index.get(path.name.lower())
            if hit is not None:
                found.append(hit)
    return found


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
    return _stem_files(directory, f"{day}_{author}_{title}")


def build_catalogue(project: Path, sources: list[dict]) -> dict:
    """本地条目 → 图片 URL 到候选文件的映射（GUI 与命令行共用）。"""
    catalogue: dict[str, list[Path]] = {}
    for item in sources:
        for url, paths in _source_paths(project, item).items():
            catalogue.setdefault(url, []).extend(paths)
    return catalogue


def _resolve_row_images(project: Path, columns: dict, album: list, catalogue: dict,
                        memo: dict | None = None):
    """待同步行的图片 URL → 本地文件（相对项目根）+ 缺图槽位。

    memo 用于整批行之间复用「这个 URL 的本地文件有哪些」，大批比对时调用方传入。
    """
    project = Path(project)
    paths, gaps = [], []
    for slot, url in enumerate(album, 1):
        if memo is not None and url in memo:
            existing = list(memo[url])
        else:
            candidates = list(dict.fromkeys(catalogue.get(url, [])))
            existing = _existing_files(candidates)
            if memo is not None:
                memo[url] = existing
        if not existing and len(album) == 1:
            # 视频条目的封面 URL 带有时效签名，精确匹配常落空；
            # 单图条目按本地 stem 兜底，多图条目不做模糊匹配。
            existing = stem_images(project, columns)
        if existing:
            # 候选路径都拼自已解析的项目根，直接字符串比对即可；
            # 逐张 resolve() 会多一次 realpath 系统调用，几千张时非常慢。
            chosen = existing[0]
            if not chosen.is_relative_to(project / "download/已获取数据"):
                raise ImportErrorDetail("图片路径越出本地媒体目录")
            paths.append(chosen.relative_to(project).as_posix())
        else:
            gaps.append(slot)
    return paths, gaps


def records_from_rows(project: Path, rows: list[dict], catalogue: dict):
    """中间层待同步行 → 可导入记录（含本地图片路径与缺图槽位）。"""
    project = Path(project).resolve()
    records, missing = [], []
    memo: dict[str, list[Path]] = {}
    for row in rows:
        columns = {name: str(row.get(name) or "") for name in bridge.COLUMNS}
        key = link_key(columns["作品链接"] or columns["动态链接(b站)"])
        if not key:
            raise ImportErrorDetail("待同步行缺少可确定身份的原帖链接")
        album = list(row.get("_images") or [])
        paths, gaps = _resolve_row_images(project, columns, album, catalogue, memo)
        for slot in gaps:
            url = album[slot - 1] if 0 < slot <= len(album) else ""
            candidates = list(dict.fromkeys(catalogue.get(url, [])))
            # URL 仅供本进程必要下载使用，不写清单、日志或报告。
            missing.append({"key": key, "slot": slot, "url": url,
                            "path": candidates[0] if candidates else None})
        records.append({"key": key, "columns": columns, "images": paths,
                        "expected_images": len(album), "missing_slots": gaps})
    return records, missing


def annotate_local_images(project: Path, rows: list[dict], catalogue: dict) -> None:
    """只读标注每行图片的本地情况：_local_images 已下载 / _missing_images 缺图。

    中间层只比对元数据，不下载媒体；界面必须区分「有图片链接」与「图片在本地」，
    否则会出现「显示 42 张图、上传却说缺图」的矛盾。
    """
    project = Path(project).resolve()
    memo: dict[str, list[Path]] = {}
    for row in rows:
        album = list(row.get("_images") or [])
        if not album:
            row["_local_images"] = row["_missing_images"] = 0
            continue
        columns = {name: str(row.get(name) or "") for name in bridge.COLUMNS}
        try:
            paths, gaps = _resolve_row_images(project, columns, album, catalogue, memo)
        except ImportErrorDetail:
            paths, gaps = [], list(range(1, len(album) + 1))
        row["_local_images"] = len(paths)
        row["_missing_images"] = len(gaps)


def fetch_missing_images(records: list[dict], missing: list[dict], cache=None,
                         workers: int = 4, limit: int | None = None) -> int:
    """缺图记录按 URL 在线补图到中间层缓存，返回补上的张数。

    只写 data_dir 下的缓存目录，不动用户「已获取数据」媒体库；
    单张失败保持缺图，不中断上传。
    """
    from concurrent.futures import ThreadPoolExecutor

    by_key = {record["key"]: record for record in records}
    todo = [(by_key[item["key"]], item) for item in missing
            if item.get("key") in by_key and item.get("url")]
    if not todo:
        return 0
    if limit is not None:
        todo = todo[:limit]
    cache_dir = Path(cache) if cache else tc_cfg.data_dir() / "中间层/图片缓存"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(pair):
        record, item = pair
        return record, item, _fetch_cover(item["url"], cache_dir, timeout=12)

    fetched = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for record, item, path in pool.map(one, todo):
            if path is None:
                continue
            try:
                image = LocalImage.inspect(path, cache_dir)
            except ImportErrorDetail:
                continue
            record.setdefault("_images", []).append(image)
            gaps = record.get("missing_slots") or []
            if item["slot"] in gaps:
                gaps.remove(item["slot"])
            fetched += 1
    return fetched


def _join_notes(*notes) -> str:
    return "；".join(dict.fromkeys(str(note).strip() for note in notes if str(note).strip()))


def prepare_comment_images(record: dict, *, export_root=None, cache_dir=None,
                           out_root=None, identities=None) -> None:
    """为官方动态记录准备账号评论索引图 + 她评论中的原图。

    只读本地 comments.json，生成图写中间层派生目录，原图写中间层图片缓存；
    失败只记 comment_note，不阻断主记录上传。
    """
    if record.get("_comment_images_prepared"):
        return
    record["_comment_images_prepared"] = True
    record["_comment_images"] = []
    notes: list[str] = []
    try:
        index = comment_images.find_artist_comments(
            record.get("columns") or {}, export_root=export_root, identities=identities)
        if not index.artist_comments:
            record["comment_note"] = index.reason or "本地评论中没有封茗囧菌本人评论"
            return

        media_root = Path(out_root) if out_root else tc_cfg.data_dir() / "中间层/评论区图片"
        pages: list[Path] = []
        try:
            pages = comment_images.render_index_image(index, out_root=out_root)
        except Exception:  # noqa: BLE001 - 生成图失败不阻断主记录
            notes.append("评论区长图生成失败")

        images: list[LocalImage] = []
        for page in pages:
            try:
                images.append(LocalImage.inspect(Path(page), media_root))
            except ImportErrorDetail:
                notes.append("评论区长图无法上传")

        urls = comment_images.artist_image_urls(index)
        cache = Path(cache_dir) if cache_dir else tc_cfg.data_dir() / "中间层/图片缓存"
        failed = 0
        seen: set[Path] = set()
        for url in urls:
            path = _fetch_cover(url, cache, timeout=12)
            if path is None:
                failed += 1
                continue
            try:
                image = LocalImage.inspect(path, cache)
            except ImportErrorDetail:
                failed += 1
                continue
            if image.path in seen:
                continue
            seen.add(image.path)
            images.append(image)
        if failed:
            notes.append(
                f"评论区原图 {failed}/{len(urls)} 张下载失败（链接可能已过期，请重新抓取评论）")
        elif not urls and images:
            notes.append("她只发了文字评论，没有原图")

        record["_comment_images"] = images
        if notes:
            record["comment_note"] = _join_notes(*notes)
        elif not images:
            record["comment_note"] = "没有可写入的评论区图片"
    except Exception:  # noqa: BLE001 - 评论区图片是补充项，不阻断上传
        record["_comment_images"] = []
        record["comment_note"] = "评论区图片准备失败"


# ==================== 完整歌曲：封面下载 + 记录 ====================

_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
             "image/bmp": ".bmp", "image/webp": ".webp"}
_COVER_REFERERS = (("gtimg.cn", "https://y.qq.com/"), ("music.126.net", "https://music.163.com/"),
                   ("kugou.com", "https://www.kugou.com/"), ("kuwo.cn", "https://www.kuwo.cn/"),
                   ("sinaimg.cn", "https://weibo.com/"),
                   ("hdslb.com", "https://www.bilibili.com/"),
                   ("bilivideo.com", "https://www.bilibili.com/"),
                   ("douyinpic.com", "https://www.douyin.com/"),
                   ("kwaicdn.com", "https://www.kuaishou.com/"),
                   ("xhscdn.com", "https://www.xiaohongshu.com/"))


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


def _fetch_cover(url: str, cache: Path, timeout: float = 20) -> Path | None:
    """下载图片到缓存目录；失败返回 None，不输出 URL。"""
    if not url.startswith(("https://", "http://")):
        return None
    cached = _cover_path(url, cache)
    if cached:
        return cached
    try:
        import httpx

        cache.mkdir(parents=True, exist_ok=True)
        response = httpx.get(url, timeout=timeout, follow_redirects=True,
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
    song_columns = bridge.song_columns()
    for row, cover in zip(rows, covers):
        columns = {name: str(row.get(name) or "") for name in song_columns}
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


def _remark_plan(columns: dict, fields: list[dict], skipped: dict):
    """备注列计划：返回 (写入值, 字段类型, 字段名)；没有备注列时返回 (None, None, None)。

    任何程序写入（批量/单行/自动推送）都会调用它把
    「自动推送程序上传+北京时间」追加到已有备注后面；表里没这一列就静默跳过
    （不拦下整条记录、也不改表结构），字段存在但不是文本类型时记一笔原因。
    列名兼容「备注信息」（四张子表）与「备注」（官方动态新增列）。
    """
    hits = [f for f in fields
            if str(f.get("fieldTitle") or "") in bridge.REMARK_ALIASES]
    if not hits:
        # 目标表没有备注列：静默跳过，不新增字段、不影响其它列
        return None, None, None
    hits.sort(key=lambda f: bridge.REMARK_ALIASES.index(str(f.get("fieldTitle"))))
    field = hits[0]
    title = str(field.get("fieldTitle"))
    if int(field.get("fieldType") or 0) != 1:
        skipped[title] = "备注字段不是文本类型"
        return None, None, None
    text = bridge.append_auto_remark(columns.get(title))
    return [{"type": "text", "text": text}], 1, title


# B站链接是搜索匹配得到的（song_bili.py），按用户规矩「新字段先单条试写 + 读回校验」
# 再放批量白名单：试写通过前保持 False，预览里显式标「未写入」，不静默跳过。
SONG_BILI_WRITE_ENABLED = False


def make_song_plan(record: dict, fields: list[dict]):
    """完整歌曲字段计划：只写明确支持的文本/日期/单选/链接/封面字段。

    「B站链接」列（type 8）已按本地快照表头核过，但它是搜索匹配得到的，不属于
    官方平台链接，按约定「新字段先单条试写 + 读回校验」：试写通过前本批显式记
    「未写入」，不静默丢弃、也不冒充成功（见 SONG_BILI_WRITE_ENABLED）。
    """
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

    remark, remark_kind, remark_title = _remark_plan(columns, fields, skipped)
    if remark is not None:
        values[remark_title] = remark
        kinds[remark_title] = remark_kind

    bili = str(columns.get(bridge.SONG_BILI_COLUMN) or "").strip()
    if bili:
        hits = by_title.get(bridge.SONG_BILI_COLUMN) or []
        if not SONG_BILI_WRITE_ENABLED:
            skipped[bridge.SONG_BILI_COLUMN] = "B站链接待单条试写校验后启用（本批未写入）"
        elif len(hits) != 1:
            skipped[bridge.SONG_BILI_COLUMN] = "目标缺少字段"
        elif int(hits[0].get("fieldType") or 0) != 8:
            skipped[bridge.SONG_BILI_COLUMN] = "字段类型不匹配"
        else:
            parts = urlsplit(bili)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                skipped[bridge.SONG_BILI_COLUMN] = "链接无法识别"
            else:
                values[bridge.SONG_BILI_COLUMN] = [
                    {"type": "url", "text": bili, "link": bili}]
                kinds[bridge.SONG_BILI_COLUMN] = 8

    if record.get("_images"):
        field = unique("封面图")
        if field.get("fieldType") != 5:
            raise ImportErrorDetail("封面图字段不是图片类型")
        kinds[field["fieldTitle"]] = field["fieldType"]

    if "歌曲名称" not in values:
        raise ImportErrorDetail("必需字段无法写入：歌曲名称")
    return values, kinds, skipped


# ==================== 线下活动 / 周边图鉴：仅新增写回 ====================
# 两张表按用户 2026-11-21 的授权只开放「新增」，不修改、不删除已有行；
# 图片列仍走「复制全部图片」手工补，程序只写文本/日期/链接/下拉与备注。

CANDIDATE_FIELDS = {
    "events": {
        "title": "活动名称",
        "text": ("活动名称", "城市", "场馆名称", "票档价格", "场馆地址（文本）"),
        "date": ("活动日期",),
        "choice": ("年份", "活动状态", "活动类型", "舞台环节", "签售环节"),
        "link": ("购票链接 1",),
        "image": ("宣传图",),
        # 场馆地址列是 type 22 地理位置：OpenAPI 连真实腾讯地图 POI 也返回 22037，
        # 只写文本列「场馆地址（文本）」（见下方 skipped 标注）。
        "required": ("活动名称",),
    },
    "merch": {
        "title": "周边名称",
        "text": ("周边名称", "来源渠道"),
        "date": ("首发日期",),
        "choice": ("获取方式",),
        "link": (),
        "image": (),
        "required": ("周边名称",),
    },
}


def _date_ms(value: str):
    """YYYY-MM-DD → 腾讯日期列要求的毫秒时间戳；无法识别返回原因。"""
    day = str(value or "")[:10]
    if not day:
        return None, "日期为空"
    try:
        moment = datetime.fromisoformat(day)
    except ValueError:
        return None, "日期无法识别"
    moment = (moment.replace(tzinfo=BEIJING)
              if moment.tzinfo is None else moment.astimezone(BEIJING))
    return str(int(moment.timestamp() * 1000)), None


def make_candidate_plan(record: dict, fields: list[dict], profile: str = "events"):
    """线下活动 / 周边图鉴字段计划：只写明确支持的列，不新建下拉选项。

    线下活动可写「宣传图」图片列（本地封面/宣传图）；已存在行只追加该图片列。
    周边图鉴仍不碰图片列。
    """
    spec = CANDIDATE_FIELDS.get(profile) or CANDIDATE_FIELDS["events"]
    columns = record.get("columns") or {}
    by_title: dict[str, list[dict]] = {}
    for field in fields:
        by_title.setdefault(str(field.get("fieldTitle") or ""), []).append(field)

    def one(name: str):
        hits = by_title.get(name) or []
        return hits[0] if len(hits) == 1 else None

    values, kinds, skipped = {}, {}, {}
    is_existing = bool(record.get("_existing"))
    if is_existing:
        # 已存在行只追加「宣传图」：文本/日期/选项/链接一律不重写
        for name in spec.get("image") or ():
            if not record.get("_images"):
                continue
            field = one(name)
            if field is None:
                skipped[name] = "目标缺少字段"
            elif int(field.get("fieldType") or 0) == 5:
                kinds[name] = 5
            else:
                skipped[name] = "字段不是图片类型"
        return values, kinds, skipped
    for name in spec["text"]:
        value = str(columns.get(name) or "").strip()
        if not value:
            continue
        field = one(name)
        if field is None:
            skipped[name] = "目标缺少字段"
            continue
        kind = int(field.get("fieldType") or 1)
        if kind == 1:
            values[name] = [{"type": "text", "text": value}]
            kinds[name] = kind
        else:
            skipped[name] = "字段类型不匹配"

    for name in spec["date"]:
        value = str(columns.get(name) or "").strip()
        if not value:
            continue
        field = one(name)
        if field is None:
            skipped[name] = "目标缺少字段"
            continue
        kind = int(field.get("fieldType") or 1)
        if kind == 4:
            encoded, why = _date_ms(value)
            if why:
                skipped[name] = why
                continue
            values[name] = encoded
            kinds[name] = kind
        elif kind == 1:
            values[name] = [{"type": "text", "text": value}]
            kinds[name] = kind
        else:
            skipped[name] = "字段类型不匹配"

    for name in spec["choice"]:
        value = str(columns.get(name) or "").strip()
        if not value:
            continue
        field = one(name)
        if field is None:
            skipped[name] = "目标缺少字段"
            continue
        kind = int(field.get("fieldType") or 1)
        if kind not in {9, 17}:
            skipped[name] = "字段不是单选类型"
            continue
        options = [o for o in field_options(field)
                   if o.get("text") == value and o.get("id")]
        if len(options) != 1:
            skipped[name] = "没有匹配的既有选项"
            continue
        values[name] = options
        kinds[name] = kind

    for name in spec["link"]:
        value = str(columns.get(name) or "").strip()
        if not value:
            continue
        field = one(name)
        if field is None:
            skipped[name] = "目标缺少字段"
            continue
        kind = int(field.get("fieldType") or 1)
        if kind == 8:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                skipped[name] = "链接无法识别"
                continue
            values[name] = [{"type": "url", "text": value, "link": value}]
            kinds[name] = kind
        elif kind == 1:
            values[name] = [{"type": "text", "text": value}]
            kinds[name] = kind
        else:
            skipped[name] = "字段类型不匹配"

    # 场馆地址（type 22 地理位置列）写不进去：2026-11-24 用真实腾讯地图 POI 实测仍
    # ret=22037。这里显式标注「未写入」，避免再出现「预览有值、表里永远空」。
    if str(columns.get("场馆地址") or "").strip() and one("场馆地址") is not None:
        skipped["场馆地址"] = "地址列（地理位置）不支持 OpenAPI 写入，已写「场馆地址（文本）」"

    for name in spec.get("image") or ():
        if not record.get("_images"):
            continue
        field = one(name)
        if field is None:
            skipped[name] = "目标缺少字段"
        elif int(field.get("fieldType") or 0) == 5:
            kinds[name] = 5
        else:
            skipped[name] = "字段不是图片类型"

    remark, remark_kind, remark_title = _remark_plan(columns, fields, skipped)
    if remark is not None:
        values[remark_title] = remark
        kinds[remark_title] = remark_kind

    for required in spec["required"]:
        if required not in values:
            raise ImportErrorDetail(f"必需字段无法写入：{required}")
    return values, kinds, skipped


def _candidate_columns(row: dict) -> dict:
    """候选行 → 表格列字典；下划线开头的内部字段不参与写入。"""
    return {str(key): ("" if value is None else str(value))
            for key, value in (row or {}).items() if not str(key).startswith("_")}


def event_key(columns: dict) -> str:
    return "event:" + bridge._song_title_key(columns.get("活动名称"))


def merch_key(columns: dict) -> str:
    return "merch:" + bridge._song_title_key(columns.get("周边名称"))


def _candidate_record(columns: dict, key: str, title: str, account: str,
                      platform: str, day: str, images=None, existing: bool = False) -> dict:
    images = list(images or [])
    return {"key": key, "title": title, "account": account, "platform": platform,
            "time": day, "columns": columns, "images": [str(i.path) for i in images],
            "expected_images": len(images), "missing_slots": [], "_images": images,
            "_existing": existing}


def _promo_local_images(row: dict, media_root: Path) -> list:
    """行内本地封面/宣传图 → LocalImage；缺文件或越出票务目录的跳过。"""
    paths = [str(p) for p in (row.get("_promo_images") or [])]
    if not paths:
        paths = [str(p) for p in (row.get("_images") or [])
                 if Path(str(p)).parent.name in ("cover", "images")]
    images, seen = [], set()
    for text in paths:
        try:
            image = LocalImage.inspect(Path(text), media_root)
        except ImportErrorDetail:
            continue
        if image.upload_name in seen:
            continue
        seen.add(image.upload_name)
        images.append(image)
    return images


def event_records_from_rows(rows: list[dict]) -> list[dict]:
    """线下活动候选行 → 可上传记录：新增行带本地宣传图，已存在行只追加图片。"""
    from toolbox.config_manager import get_export_dir

    media_root = Path(get_export_dir()) / "票务演出"
    records = []
    for row in rows:
        columns = _candidate_columns(row)
        name = str(columns.get("活动名称") or "").strip()
        if not name:
            continue
        records.append(_candidate_record(
            columns, event_key(columns), name,
            str(columns.get("城市") or columns.get("活动类型") or ""),
            "线下活动", str(columns.get("活动日期") or ""),
            images=_promo_local_images(row, media_root),
            existing=bool(row.get("_existing"))))
    return records


def merch_records_from_rows(rows: list[dict]) -> list[dict]:
    """周边图鉴候选行 → 可上传记录（图片不随程序上传）。"""
    records = []
    for row in rows:
        columns = _candidate_columns(row)
        name = str(columns.get("周边名称") or "").strip()
        if not name:
            continue
        records.append(_candidate_record(
            columns, merch_key(columns), name,
            str(columns.get("来源渠道") or ""), "周边图鉴",
            str(columns.get("首发日期") or "")))
    return records


def event_remote_keys(rows: list[dict]) -> set[str]:
    keys = set()
    for row in rows:
        name = bridge._song_title_key(
            _live_cell_text((row.get("values") or {}).get("活动名称")))
        if name:
            keys.add("event:" + name)
    return keys


def merch_remote_keys(rows: list[dict]) -> set[str]:
    keys = set()
    for row in rows:
        name = bridge._song_title_key(
            _live_cell_text((row.get("values") or {}).get("周边名称")))
        if name:
            keys.add("merch:" + name)
    return keys


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


# ==================== 直播记录：API 写回（只构造 payload，不发送） ====================

LIVE_FIELDS = ("直播账户", "直播标题", "开始时间", "结束时间", "直播平台", "直播链接1")
LIVE_REQUIRED_FIELDS = ("直播标题", "开始时间")
# 公式列只登记跳过原因，永不写入
LIVE_READONLY_FIELDS = ("直播时长", "直播距今")


def live_key(row: dict) -> str:
    """直播行的去重键：优先回放链接，其次「开始日期 + 标题」。"""
    label, url = tables.split_link(row.get("直播链接1"))
    if url:
        return "link:" + url
    day = str(row.get("开始时间") or "")[:10]
    title = str(row.get("直播标题") or "").strip()
    return f"date:{day}|{title}"


def make_live_plan(record: dict, fields: list[dict], available_only=True):
    """直播记录行 → OpenAPI values/kinds/skipped，只做编码，不发送请求。

    公式列（直播时长/直播距今）不写；多选列没有既有选项时跳过该字段并记原因；
    链接列写成 `[{type:url, text:显示文本, link:URL}]`，保留两级结构。
    """
    values, kinds, skipped = {}, {}, {}
    for name in LIVE_READONLY_FIELDS:
        if any(f.get("fieldTitle") == name for f in fields):
            skipped[name] = "公式列不写"
    for name in LIVE_FIELDS:
        raw = record.get(name, "")
        hits = [f for f in fields if f.get("fieldTitle") == name]
        if len(hits) != 1:
            if not available_only:
                raise ImportErrorDetail(f"字段无法唯一匹配：{name}")
            skipped[name] = "目标缺少字段"
            continue
        field = hits[0]
        title, kind = field["fieldTitle"], int(field.get("fieldType") or 1)
        if kind == 19:
            skipped[name] = "公式列不写"
            continue
        if name in ("直播账户", "直播平台") and kind in {9, 17}:
            text = str(raw or "").strip()
            if not text:
                continue
            options = [o for o in field_options(field)
                       if o.get("text") == text and o.get("id")]
            if len(options) != 1:
                skipped[name] = "没有匹配的既有选项"
                continue
            values[title] = options
            kinds[title] = kind
            continue
        if kind == 4:  # 日期：腾讯要求毫秒时间戳
            day = str(raw or "")[:10]
            if not day:
                continue
            try:
                moment = datetime.fromisoformat(day)
            except ValueError:
                skipped[name] = "日期无法识别"
                continue
            moment = (moment.replace(tzinfo=BEIJING)
                      if moment.tzinfo is None else moment.astimezone(BEIJING))
            values[title] = str(int(moment.timestamp() * 1000))
            kinds[title] = kind
            continue
        if kind == 8:  # 链接：保留显示文本 + URL
            label, url = tables.split_link(raw)
            if not url:
                continue
            values[title] = [{"type": "url", "text": label or url, "link": url}]
            kinds[title] = kind
            continue
        if kind == 1:  # 文本
            text = str(raw or "")
            if not text:
                continue
            values[title] = [{"type": "text", "text": text}]
            kinds[title] = kind
            continue
        skipped[name] = "字段类型不匹配"
    remark, remark_kind, remark_title = _remark_plan(record, fields, skipped)
    if remark is not None:
        values[remark_title] = remark
        kinds[remark_title] = remark_kind
    for required in LIVE_REQUIRED_FIELDS:
        if required not in values:
            raise ImportErrorDetail(f"必需字段无法写入：{required}")
    return values, kinds, skipped


def preview_lives(rows: list[dict], fields: list[dict], remote_keys=(),
                  available_only=True) -> dict:
    """直播记录写回预演：返回每条 payload 与跳过去重结果，**不发送任何请求**。"""
    remote = {str(key) for key in (remote_keys or ())}
    out: dict = {"records": [], "duplicates": [], "errors": [], "sent": 0}
    for row in rows:
        key = live_key(row)
        try:
            values, kinds, skipped = make_live_plan(row, fields, available_only)
        except ImportErrorDetail as exc:
            out["errors"].append({"key": key, "title": row.get("直播标题"),
                                  "error": str(exc)})
            continue
        item = {"key": key, "title": row.get("直播标题"),
                "operation": "skip" if key in remote else "add",
                "skipped_fields": skipped, "values": values, "kinds": kinds}
        if key in remote:
            out["duplicates"].append(item)
        else:
            out["records"].append(item)
    return out


def _live_cell_text(raw) -> str:
    """OpenAPI 单元格值 → 纯文本（链接取 URL、文本取 text、日期原样）。"""
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                parts.append(str(item.get("link") or item.get("text") or ""))
            elif item not in (None, ""):
                parts.append(str(item))
        return " ".join(p for p in parts if p)
    if isinstance(raw, dict):
        return str(raw.get("link") or raw.get("text") or "")
    return str(raw or "")


def _ms_to_day(raw) -> str:
    """毫秒时间戳 → YYYY-MM-DD（已是日期文本则截前 10 位）。"""
    text = str(raw or "").strip()
    if text.isdigit() and len(text) >= 12:
        try:
            return datetime.fromtimestamp(int(text) / 1000, tz=BEIJING).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return text[:10]


def write_lives(client, rows: list[dict], *, limit: int = 1, apply: bool = True,
                verify_each: bool = True, emit=lambda event: None) -> dict:
    """直播记录写回：预演 → 去重 → addRecords → 读回校验。

    limit=1 时只写第一条（单条试写）；apply=False 只返回预演结果，不调用任何写接口。
    """
    fields = client.get_fields()
    before = client.get_records()
    known = set()
    for record in before:
        values = record.get("values") or {}
        known.add(live_key({
            "直播标题": _live_cell_text(values.get("直播标题")),
            "开始时间": _ms_to_day(values.get("开始时间")),
            "直播链接1": _live_cell_text(values.get("直播链接1")),
        }))
    preview = preview_lives(rows, fields, remote_keys=known, available_only=True)
    receipt = {"phase": "complete", "apply": apply, "rows_before": len(before),
               "rows_after": len(before), "sent": 0,
               "added": [], "skipped": [item["key"] for item in preview["duplicates"]],
               "errors": preview["errors"]}
    if not apply:
        receipt["preview"] = preview
        return receipt

    todo = preview["records"]
    if limit and int(limit) > 0:
        todo = todo[:int(limit)]

    def save_history(status="成功", note=""):
        remark_text = ""
        for item in todo:
            for title in bridge.REMARK_ALIASES:
                block = (item.get("values") or {}).get(title)
                if isinstance(block, list) and block and isinstance(block[0], dict):
                    remark_text = str(block[0].get("text") or "")
                    break
            if remark_text:
                break
        push_history.record(
            tab_name=getattr(client, "sheet_title", "") or "",
            tab_id=str(getattr(client, "tab_id", "") or ""),
            doc_key=str(getattr(client, "doc_key", "") or ""),
            profile="lives", source="单条试写", status=status,
            added=len(receipt["added"]), skipped=len(receipt["skipped"]),
            failed=sum(1 for added in receipt["added"] if not added.get("verified")),
            record_ids=[added.get("record_id") for added in receipt["added"]],
            remark=remark_text, note=note)

    try:
        for item in todo:
            rid = client.add_record(item["values"])
            receipt["sent"] += 1
            added = {"key": item["key"], "title": item["title"], "record_id": rid,
                     "verified": False, "skipped_fields": item["skipped_fields"]}
            if verify_each:
                fresh = client.get_records()
                saved = [row for row in fresh if row.get("recordID") == rid]
                if len(saved) != 1:
                    raise ImportErrorDetail(f"新增记录未查回：{rid}")
                checks = verify(saved[0], item["values"], item["kinds"], [])
                added["checks"] = checks
                added["verified"] = bool(checks) and all(checks.values())
                if not added["verified"]:
                    raise ImportErrorDetail(f"读回不一致，停止后续写入：{rid}")
                receipt["rows_after"] = len(fresh)
            receipt["added"].append(added)
            emit({"phase": "record_created", **added})
    except ImportErrorDetail as exc:
        save_history("失败", str(exc))
        raise
    save_history()
    return receipt


def make_plan(record: dict, fields: list[dict], available_only=False,
              allow_new_options=False):
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
            if len(options) == 1:
                encoded = [options[0]]
            elif allow_new_options and value:
                # 只给文本、不给 id：腾讯表格按「快速添加」新建该选项（新 UP 主自动登记）
                encoded = [{"text": value}]
            elif not available_only:
                raise ImportErrorDetail(f"字段没有唯一既有选项：{name}")
            else:
                skipped[name] = "没有匹配的既有选项"
                continue
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
    remark, remark_kind, remark_title = _remark_plan(columns, fields, skipped)
    if remark is not None:
        values[remark_title] = remark
        kinds[remark_title] = remark_kind
    for required in ("作品标题", "作品内容", "发布时间"):
        if required not in values:
            raise ImportErrorDetail(f"必需字段无法写入：{required}")
    if not any(kinds.get(name) == 8 for name in ("作品链接", "链接", "动态链接(b站)")):
        raise ImportErrorDetail("没有可写的原帖链接字段")
    if not any(kind == 5 for kind in kinds.values()):
        raise ImportErrorDetail("没有可写的图片字段")
    return values, kinds, skipped


def new_choice_options(records: list[dict], fields: list[dict]) -> dict[str, list[str]]:
    """这批记录会新建的下拉选项：{字段名: [新选项文本...]}。

    只用于上传前向用户确认；不写任何东西。
    """
    by_title = {f.get("fieldTitle"): f for f in fields if f.get("fieldTitle")}
    out: dict[str, list[str]] = {}
    for record in records:
        for name in CHOICE_COLUMNS:
            value = str(record["columns"].get(name) or "").strip()
            field = by_title.get(name)
            if not value or field is None or field.get("fieldType") not in {9, 17}:
                continue
            if any(o.get("text") == value and o.get("id") for o in field_options(field)):
                continue
            bucket = out.setdefault(name, [])
            if value not in bucket:
                bucket.append(value)
    return out


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


def _plain_text(value) -> str:
    """读回核对用：忽略空白差异（备注换行、表格自动补空格不算不一致）。"""
    return re.sub(r"\s+", "", str(value or ""))


def verify(row: dict, values: dict, kinds: dict, images: list[LocalImage],
           image_groups: dict[str, list[LocalImage]] | None = None):
    saved, checks = row.get("values") or {}, {}
    for title, kind in kinds.items():
        actual, expected = saved.get(title), values.get(title)
        if kind == 5:
            group = list(images) if image_groups is None else list(image_groups.get(title) or [])
            entries = actual or []
            checks[title] = isinstance(entries, list) and len(entries) == len(group) and all(isinstance(i, dict) for i in entries)
            if checks[title] and group:
                checks[title] = ([i.get("title") for i in entries] == [i.upload_name for i in group] and
                                 len({i.get("id") for i in entries if i.get("id")}) == len(group))
        elif kind == 1:
            actual_text = "".join(str(i.get("text") or "") for i in actual or [] if isinstance(i, dict))
            target = ((expected or [{}])[0].get("text")
                      if isinstance(expected, list) else expected)
            checks[title] = _plain_text(actual_text) == _plain_text(target)
        elif kind == 8:
            checks[title] = isinstance(actual, list) and any(i.get("link") == expected[0]["link"] for i in actual if isinstance(i, dict))
        elif kind == 4:
            checks[title] = str(actual) == str(expected)
        else:
            # 单选/多选：按文本核对（新选项由表格快速添加，写入时还没有 id）
            checks[title] = (isinstance(actual, list)
                             and [str(i.get("text") or "") for i in actual if isinstance(i, dict)]
                             == [str(i.get("text") or "") for i in expected if isinstance(i, dict)])
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


def _image_write_entries(values: dict, title: str = "宣传图") -> list[dict]:
    """读回值里的图片条目 → 可回写 imageID；缺图片 ID 时报错，避免覆盖丢图。"""
    raw = (values or {}).get(title)
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list) or not all(isinstance(i, dict) for i in raw):
        raise ImportErrorDetail("目标行图片列格式异常，无法安全追加")
    entries = []
    for item in raw:
        image_id = str(item.get("id") or item.get("imageID") or "")
        if not image_id:
            raise ImportErrorDetail("目标行已有图片缺少图片 ID，请在腾讯文档里手动补图")
        entries.append({"imageID": image_id})
    return entries


def _image_titles(values: dict, title: str = "宣传图") -> list[str]:
    raw = (values or {}).get(title)
    if not isinstance(raw, list):
        return []
    return [str(i.get("title") or "") for i in raw if isinstance(i, dict)]


def _event_live_index(rows: list[dict]) -> dict:
    """线上记录 → 活动键对应的 record；同名只取第一条。"""
    index = {}
    for row in rows:
        values = row.get("values") or {}
        key = "event:" + bridge._song_title_key(_live_cell_text(values.get("活动名称")))
        if key != "event:" and key not in index:
            index[key] = row
    return index


def run(client, records, *, apply=False, available_only=False, verify_each=True,
        emit=lambda event: None, profile="posts", allow_new_options=False,
        source="上传"):
    """预览并导入记录。

    verify_each=True（命令行默认）逐条新增后立刻读回，异常即停；
    界面批量上传传 False：整批新增完只读回一次，避免每条都翻完整张表。
    profile="songs" 仅用于完整歌曲：按歌曲名去重，只新增。
    allow_new_options=True 时，下拉列里没有的名字按「快速添加」新建选项（需用户当次确认）。
    source 只用于推送历史（批量上传 / 单行上传等）。
    """
    if available_only and (client.doc_key, client.tab_id) != TEST_TARGET:
        raise ImportErrorDetail("只写可用字段的例外仅允许本次授权测试目标")
    fields = client.get_fields()
    if profile == "songs":
        plans = [(record, *make_song_plan(record, fields)) for record in records]
    elif profile in ("events", "merch"):
        plans = [(record, *make_candidate_plan(record, fields, profile))
                 for record in records]
    else:
        plans = [(record, *make_plan(record, fields, available_only, allow_new_options))
                 for record in records]
    comment_field = None
    if profile == "posts":
        comment_field = next(
            (field for field in fields
             if str(field.get("fieldTitle") or "") == comment_images.COMMENT_FIELD_TITLE
             and int(field.get("fieldType") or 0) == 5), None)
    before = client.get_records()
    if profile == "songs":
        known = {"song:" + key for key in song_remote_keys(before)}
    elif profile == "events":
        known = event_remote_keys(before)
    elif profile == "merch":
        known = merch_remote_keys(before)
    else:
        known = set(remote_keys(before))
    live_events = _event_live_index(before) if profile == "events" else {}

    def operation(record) -> str:
        if record.get("_existing"):
            return "append_images"
        return "skip" if record["key"] in known else "add"

    emit({"phase": "preview", "apply": apply, "doc_key": client.doc_key, "tab_id": client.tab_id,
          "records": [{"key": r["key"], "title": _record_title(r), "images": len(r["_images"]),
                       "comment_images": len(r.get("_comment_images") or []),
                       "comment_note": r.get("comment_note") or "",
                       "missing_images": len(r["missing_slots"]), "skipped_fields": skipped,
                       "operation": operation(r)}
                      for r, v, k, skipped in plans]})
    receipt = {"phase": "complete", "doc_key": client.doc_key, "tab_id": client.tab_id, "apply": apply,
               "rows_before": len(before), "rows_after": len(before), "added": [], "skipped": [],
               "appended": [],
               "uploaded_images": 0, "uploaded_comment_images": 0,
               "comment_image_failures": 0,
               "missing_images": sum(len(r["missing_slots"]) for r in records),
               "updated": 0, "deleted": 0}
    if not apply:
        return receipt

    remark_text = ""
    for _record, values, _kinds, _skipped in plans:
        for title in bridge.REMARK_ALIASES:
            block = values.get(title)
            if isinstance(block, list) and block and isinstance(block[0], dict):
                remark_text = str(block[0].get("text") or "")
                break
        if remark_text:
            break

    def save_history(status="成功", note=""):
        push_history.record(
            tab_name=getattr(client, "sheet_title", "") or "",
            tab_id=str(getattr(client, "tab_id", "") or ""),
            doc_key=str(getattr(client, "doc_key", "") or ""),
            profile=profile, source=source, status=status,
            added=len(receipt["added"]), skipped=len(receipt["skipped"]),
            images=receipt["uploaded_images"],
            failed=sum(1 for item in receipt["added"] if not item.get("verified")),
            record_ids=[item.get("record_id") for item in receipt["added"]],
            remark=remark_text, note=note)

    try:
        pending_verify = []
        pending_append_verify = []
        for record, values, kinds, skipped in plans:
            try:
                if record.get("_existing"):
                    live = live_events.get(record["key"])
                    if live is None:
                        raise ImportErrorDetail("目标活动行已不存在，请重新比对后再上传")
                    rid = str(live.get("recordID") or "")
                    if not rid:
                        raise ImportErrorDetail("目标活动行缺少 recordID，无法追加宣传图")
                    live_values = live.get("values") or {}
                    existing_entries = _image_write_entries(live_values)
                    existing_names = set(_image_titles(live_values))
                    pending_images = [image for image in record["_images"]
                                      if image.upload_name not in existing_names]
                    if not pending_images:
                        receipt["skipped"].append({"key": record["key"], "record_ids": [rid]})
                        continue
                    image_title = next(
                        (title for title, kind in kinds.items() if kind == 5), "")
                    if not image_title:
                        raise ImportErrorDetail("目标表没有可写的「宣传图」图片列")
                    tickets = []
                    for image in pending_images:
                        image.read_verified()
                        tickets.append(client.upload_image(image))
                        receipt["uploaded_images"] += 1
                        emit({"phase": "image_uploaded", "key": record["key"],
                              "name": image.path.name, "kind": "promo"})
                    client.update_image_field(
                        rid, image_title,
                        existing_entries + [{"imageID": ticket} for ticket in tickets])
                    item = {"key": record["key"], "title": _record_title(record),
                            "record_id": rid, "image_count": len(pending_images),
                            "cover_image_count": 0, "comment_image_count": 0,
                            "comment_failed_count": 0, "comment_note": "",
                            "missing_images": 0, "skipped_fields": skipped,
                            "verified": False, "appended": True,
                            "existing_images": len(existing_entries)}
                    receipt["appended"].append(item)
                    receipt["updated"] += 1
                    emit({"phase": "images_appended", **item})
                    pending_append_verify.append(
                        (item, rid, len(existing_entries), pending_images))
                    continue
                if record["key"] in known:
                    receipt["skipped"].append({"key": record["key"], "record_ids": []})
                    continue
                if profile == "posts" and not record.get("_comment_images_prepared"):
                    emit({"phase": "comment_prepare", "key": record["key"],
                          "title": _record_title(record)})
                    prepare_comment_images(record)
                    emit({"phase": "comment_prepared", "key": record["key"],
                          "count": len(record.get("_comment_images") or []),
                          "note": record.get("comment_note") or ""})
                images = record["_images"]
                for image in images:
                    image.read_verified()
                tickets = []
                for image in images:
                    tickets.append(client.upload_image(image))
                    receipt["uploaded_images"] += 1
                    emit({"phase": "image_uploaded", "key": record["key"],
                          "name": image.path.name, "kind": "cover"})

                image_title = ("封面图" if kinds.get("封面图") == 5 else
                               next((title for title, kind in kinds.items() if kind == 5), ""))
                comment_selected = (list(record.get("_comment_images") or [])
                                    if profile == "posts" else [])
                comment_note = str(record.get("comment_note") or "")
                comment_tickets = []
                comment_uploaded = []
                if comment_selected and image_title == comment_images.COMMENT_FIELD_TITLE:
                    comment_note = _join_notes(
                        comment_note, "封面图与评论区图片是同一字段，未写入评论区图片")
                    comment_selected = []
                if comment_selected and comment_field is None:
                    comment_note = _join_notes(
                        comment_note, "目标表没有可写的「评论区图片」字段")
                elif comment_selected:
                    for image in comment_selected:
                        try:
                            image.read_verified()
                        except ImportErrorDetail:
                            comment_note = _join_notes(
                                comment_note, "评论区图片跳过：文件已变化")
                            continue
                        try:
                            ticket = client.upload_image(image)
                        except ImportErrorDetail:
                            comment_note = _join_notes(
                                comment_note, "评论区图片上传失败")
                            continue
                        comment_tickets.append(ticket)
                        comment_uploaded.append(image)
                        receipt["uploaded_images"] += 1
                        receipt["uploaded_comment_images"] += 1
                        emit({"phase": "image_uploaded", "key": record["key"],
                              "name": image.path.name, "kind": "comment"})
                comment_failed = len(comment_selected) - len(comment_uploaded)

                values = copy.deepcopy(values)
                if tickets and image_title:
                    values[image_title] = [{"imageID": ticket} for ticket in tickets]
                if comment_tickets and comment_field is not None:
                    values[comment_images.COMMENT_FIELD_TITLE] = [
                        {"imageID": ticket} for ticket in comment_tickets]
                    kinds[comment_images.COMMENT_FIELD_TITLE] = 5
                image_groups = {}
                if image_title:
                    image_groups[image_title] = images
                if comment_tickets:
                    image_groups[comment_images.COMMENT_FIELD_TITLE] = comment_uploaded

                rid = client.add_record(values)
                known.add(record["key"])
                added = {"key": record["key"], "title": _record_title(record), "record_id": rid,
                         "image_count": len(images) + len(comment_uploaded),
                         "cover_image_count": len(images),
                         "comment_image_count": len(comment_uploaded),
                         "comment_failed_count": comment_failed,
                         "comment_note": comment_note,
                         "missing_images": len(record["missing_slots"]),
                         "skipped_fields": skipped, "verified": False}
                receipt["added"].append(added)
                receipt["comment_image_failures"] += comment_failed
                receipt["rows_after"] = receipt["rows_before"] + len(receipt["added"])
                emit({"phase": "record_created", **added})
                if verify_each:
                    fresh = client.get_records()
                    saved = [row for row in fresh if row.get("recordID") == rid]
                    if len(saved) != 1:
                        raise ImportErrorDetail("新增记录未查回，请按 record_id 核对")
                    checks = verify(saved[0], values, kinds, images, image_groups)
                    added.update({"verified": all(checks.values()), "checks": checks})
                    receipt["rows_after"] = len(fresh)
                    emit({"phase": "readback", **added})
                    if not added["verified"]:
                        raise ImportErrorDetail("读回不一致，停止后续条目")
                else:
                    pending_verify.append((added, values, kinds, images, image_groups))
            except ImportErrorDetail as exc:
                emit({**receipt, "phase": "stopped", "key": record["key"], "error": str(exc),
                      "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
                raise

        if pending_verify:
            fresh = client.get_records()
            saved_by_id = {str(row.get("recordID")): row for row in fresh if row.get("recordID")}
            counts = (count_song_title_keys(fresh) if profile == "songs"
                      else count_remote_keys(fresh))
            for added, values, kinds, images, image_groups in pending_verify:
                if profile in ("events", "merch"):
                    # 候选表只给「新增」授权，重复性由名称键在上方 already-skip 判断
                    added["duplicate"] = False
                    lookup = ""
                else:
                    lookup = added["key"][5:] if profile == "songs" else added["key"]
                    added["duplicate"] = counts.get(lookup, 0) > 1
                saved = saved_by_id.get(str(added["record_id"]))
                if saved is None:
                    emit({"phase": "readback", **added})
                    continue
                checks = verify(saved, values, kinds, images, image_groups)
                added.update({"verified": all(checks.values()), "checks": checks})
                emit({"phase": "readback", **added})
            receipt["rows_after"] = len(fresh)
            receipt["duplicates"] = [item["key"] for item in receipt["added"] if item.get("duplicate")]
        if pending_append_verify:
            fresh = client.get_records()
            by_id = {str(row.get("recordID")): row for row in fresh if row.get("recordID")}
            for item, rid, before_count, images in pending_append_verify:
                row = by_id.get(rid)
                titles = _image_titles((row or {}).get("values") or {})
                ok = (row is not None
                      and len(titles) == before_count + len(images)
                      and all(image.upload_name in titles for image in images))
                item["verified"] = ok
                emit({"phase": "readback", **item})
                if not ok:
                    raise ImportErrorDetail("追加宣传图后读回不一致，请到表格里核对")
            receipt["rows_after"] = len(fresh)
    except ImportErrorDetail as exc:
        save_history("失败", str(exc))
        raise
    save_history(note=(f"追加宣传图 {len(receipt['appended'])} 条"
                       if receipt["appended"] else ""))
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
