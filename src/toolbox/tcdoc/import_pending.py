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
from urllib.parse import urlsplit
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from toolbox import increment_db
from toolbox.tcdoc import bridge
from toolbox.tcdoc.import_local import (
    ALIASES, BEIJING, CHOICE_COLUMNS, ImportErrorDetail, LocalImage, LocalImportClient,
    ManagedAuth, TEST_TARGET, UnknownWriteResult, field_options, install_readonly_guard,
    managed_cookie_reader, validate_target, weibo_key,
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


def make_manifest(project=ROOT, days=30):
    project = project.resolve()
    result, sources = pending_result(project, days)
    catalogue = {}
    for item in sources:
        for url, paths in _source_paths(project, item).items():
            catalogue.setdefault(url, []).extend(paths)
    records, missing = [], []
    for row in result["rows"]:
        columns = {name: str(row.get(name) or "") for name in bridge.COLUMNS}
        key = link_key(columns["作品链接"] or columns["动态链接(b站)"])
        if not key:
            raise ImportErrorDetail("待同步行缺少可确定身份的原帖链接")
        paths = []
        gaps = []
        for slot, url in enumerate(row.get("_images") or [], 1):
            candidates = list(dict.fromkeys(catalogue.get(url, [])))
            existing = [p for p in candidates if p.is_file()]
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


def run(client, records, *, apply=False, available_only=False, emit=lambda event: None):
    if available_only and (client.doc_key, client.tab_id) != TEST_TARGET:
        raise ImportErrorDetail("只写可用字段的例外仅允许本次授权测试目标")
    fields = client.get_fields()
    plans = [(record, *make_plan(record, fields, available_only)) for record in records]
    before = client.get_records()
    keys = remote_keys(before)
    emit({"phase": "preview", "apply": apply, "doc_key": client.doc_key, "tab_id": client.tab_id,
          "records": [{"key": r["key"], "title": r["columns"]["作品标题"], "images": len(r["_images"]),
                       "missing_images": len(r["missing_slots"]), "skipped_fields": skipped,
                       "operation": "skip" if r["key"] in keys else "add"} for r, v, k, skipped in plans]})
    receipt = {"phase": "complete", "doc_key": client.doc_key, "tab_id": client.tab_id, "apply": apply,
               "rows_before": len(before), "rows_after": len(before), "added": [], "skipped": [],
               "uploaded_images": 0, "missing_images": sum(len(r["missing_slots"]) for r in records),
               "updated": 0, "deleted": 0}
    if not apply:
        return receipt
    for record, values, kinds, skipped in plans:
        try:
            if client.get_fields() != fields:
                raise ImportErrorDetail("字段在预览后变化，请重新预览")
            existing = client.get_records()
            current_keys = remote_keys(existing)
            if record["key"] in current_keys:
                receipt["skipped"].append({"key": record["key"], "record_ids": current_keys[record["key"]]})
                receipt["rows_after"] = len(existing)
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
            image_title = next(title for title, kind in kinds.items() if kind == 5)
            if tickets:
                values[image_title] = [{"imageID": ticket} for ticket in tickets]
            latest = client.get_records()
            if record["key"] in remote_keys(latest):
                receipt["skipped"].append({"key": record["key"], "record_ids": remote_keys(latest)[record["key"]]})
                continue
            if client.get_fields() != fields:
                raise ImportErrorDetail("上传期间字段变化，未新增记录")
            rid = client.add_record(values)
            added = {"key": record["key"], "title": record["columns"]["作品标题"], "record_id": rid,
                     "image_count": len(images), "missing_images": len(record["missing_slots"]),
                     "skipped_fields": skipped, "verified": False}
            receipt["added"].append(added)
            emit({"phase": "record_created", **added})
            fresh = client.get_records()
            saved = [r for r in fresh if r.get("recordID") == rid]
            if len(saved) != 1:
                raise ImportErrorDetail("新增记录未查回，请按 record_id 核对")
            checks = verify(saved[0], values, kinds, images)
            added.update({"verified": all(checks.values()), "checks": checks})
            receipt["rows_after"] = len(fresh)
            emit({"phase": "readback", **added})
            if not added["verified"]:
                raise ImportErrorDetail("读回不一致，停止后续条目")
        except ImportErrorDetail as exc:
            emit({**receipt, "phase": "stopped", "key": record["key"], "error": str(exc),
                  "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
            raise
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
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    try:
        validate_target(args.doc_key, args.tab_id, args.allow_test_document)
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
        client = LocalImportClient(auth, args.doc_key, args.tab_id, args.allow_test_document)
        client.connect()
        emit(run(client, records, apply=args.apply, available_only=args.available_fields_only, emit=emit))
        return 0
    except ImportErrorDetail as exc:
        emit({"phase": "error", "error": str(exc), "unknown_record_outcome": isinstance(exc, UnknownWriteResult)})
        return 3 if isinstance(exc, UnknownWriteResult) else 2


if __name__ == "__main__":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
