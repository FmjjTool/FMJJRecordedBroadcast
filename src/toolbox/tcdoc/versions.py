"""不可变的腾讯文档采集版本（本地只读打开，不恢复云端或覆盖当前库）。

Owner: 本模块唯一写入 ``腾讯文档数据/采集版本/<doc-key SHA256>/``。
Schema 1: 每次成功采集一个 .tcdoc ZIP，内含 manifest.json、content.json
及内容寻址的图片字节；主数据是当次本地采集内容，当前 tables/pages 库仍由原
owner 管理。版本永久保留，不自动删除；无凭据、浏览器状态、请求头或配置副本。
读取只接受文档 key 和版本 ID，不解压、不执行文件、不接受外部路径。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg_mod

CST = timezone(timedelta(hours=8))
SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_CONTENT_BYTES = 128 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 10002
MAX_RECORDS = 1_000_000
MAX_IMAGE_PIXELS = 50_000_000
MAX_COMPRESSION_RATIO = 1000
_VERSION_RE = re.compile(r"^\d{8}T\d{12}-[0-9a-f]{32}$")
_ASSET_RE = re.compile(r"^[0-9a-f]{64}$")
_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
          "WEBP": "image/webp", "BMP": "image/bmp", "AVIF": "image/avif"}


class VersionError(ValueError):
    """版本缺失、损坏或不符合受控格式；错误信息不含文件路径。"""


def _key(doc_key) -> str:
    value = str(doc_key or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise VersionError("文档标识无效")
    return value


def _version_id(value) -> str:
    value = str(value or "")
    if not _VERSION_RE.fullmatch(value):
        raise VersionError("采集版本标识无效")
    try:
        datetime.strptime(value.split("-", 1)[0], "%Y%m%dT%H%M%S%f")
    except ValueError as exc:
        raise VersionError("采集版本标识无效") from exc
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


def _scoped(path: Path, root: Path) -> Path:
    """限定根目录，拒绝路径中任意链接 / Windows reparse point。"""
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        relative = path.relative_to(root)
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError) as exc:
        raise VersionError("采集版本路径无效") from exc
    cursor = root
    if _is_link(cursor):
        raise VersionError("采集版本目录不允许链接")
    for part in relative.parts:
        cursor /= part
        if _is_link(cursor):
            raise VersionError("采集版本目录不允许链接")
    return path


def _folder(doc_key: str) -> Path:
    data = Path(cfg_mod.data_dir())
    root = _scoped(data / "采集版本", data)
    return _scoped(root / _sha(_key(doc_key).encode("utf-8")), root)


def _target(doc_key: str, version_id: str) -> Path:
    folder = _folder(doc_key)
    return _scoped(folder / f"{_version_id(version_id)}.tcdoc", folder)


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_read(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise VersionError("采集版本含重复字段")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _value: (_ for _ in ()).throw(
                              VersionError("采集版本数值无效")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise VersionError("采集版本内容损坏") from exc


def _database(path: Path):
    """仅在已存在库上打开读事务，所有 SELECT 共享同一个 SQLite 快照。"""
    path = Path(path).absolute()
    if not path.is_file() or _is_link(path):
        raise VersionError("暂无可保存的本地采集内容")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def _image(data: bytes) -> str:
    from PIL import Image
    import warnings
    try:
        if not 0 < len(data) <= MAX_ASSET_BYTES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as pic:
                mime = _MIMES.get(str(pic.format or "").upper())
                if (not mime or min(pic.size) < 1
                        or pic.width * pic.height > MAX_IMAGE_PIXELS):
                    raise ValueError
                pic.verify()
        return mime
    except (OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise VersionError("本地图片不可用") from exc


class _Assets:
    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}
        self.total = 0
        self.missing = 0

    def add(self, local_path, *, source: str, status="downloaded") -> dict:
        result = {"asset_id": "", "status": "missing"}
        try:
            if status != "downloaded" or not local_path:
                raise VersionError("图片尚未下载")
            root = Path(cfg_mod.data_dir()).absolute()
            given = Path(str(local_path))
            path = given if given.is_absolute() else root / given
            controlled = root / ("正文图片" if source == "body" else "图片")
            _scoped(controlled, root)
            path = _scoped(path, controlled)
            if (not path.is_file() or not 0 < path.stat().st_size <= MAX_ASSET_BYTES):
                raise VersionError("本地图片不可用")
            with path.open("rb") as stream:
                data = stream.read(MAX_ASSET_BYTES + 1)
            mime = _image(data)
            asset_id = _sha(data)
            if asset_id not in self.files:
                if len(self.files) >= MAX_ENTRIES - 2:
                    raise VersionError("采集版本图片数量超出限制")
                if self.total + len(data) > MAX_TOTAL_BYTES - MAX_CONTENT_BYTES:
                    raise VersionError("采集版本图片总量超出限制")
                self.files[asset_id] = (data, mime)
                self.total += len(data)
            return {"asset_id": asset_id, "status": "downloaded"}
        except (OSError, VersionError):
            self.missing += 1
            return result


def _write(doc: dict, source: str, sheets: list, pages: list,
           assets: _Assets, complete: bool) -> dict:
    key = _key(doc.get("key") or doc.get("doc_key"))
    stamp = datetime.now(CST)
    random_id = uuid.uuid4().hex
    version_id = stamp.strftime("%Y%m%dT%H%M%S%f") + "-" + random_id
    captured_at = stamp.strftime("%Y-%m-%d %H:%M:%S")
    meta = {"version_id": version_id, "captured_at": captured_at,
            "label": f"{captured_at} - {random_id}", "source": source,
            "complete": bool(complete), "missing_images": assets.missing,
            "sheets": len(sheets), "rows": sum(len(s.get("rows", [])) for s in sheets),
            "pages": len(pages), "images": len(assets.files),
            "name": str(doc.get("name") or key)}
    content = _json_bytes({"sheets": sheets, "pages": pages})
    if len(content) > MAX_CONTENT_BYTES:
        raise VersionError("采集版本正文或表格超出大小限制")
    files = {"content.json": {"size": len(content), "sha256": _sha(content)}}
    asset_meta = {}
    for asset_id, (data, mime) in assets.files.items():
        entry = f"assets/{asset_id}"
        files[entry] = {"size": len(data), "sha256": asset_id}
        asset_meta[asset_id] = {"entry": entry, "mime": mime}
    manifest = _json_bytes({"schema_version": SCHEMA_VERSION, "doc_key": key,
                            "meta": meta, "files": files, "assets": asset_meta})
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise VersionError("采集版本索引超出大小限制")
    if len(manifest) + len(content) + assets.total > MAX_TOTAL_BYTES:
        raise VersionError("采集版本总量超出限制")
    target = _target(key, version_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    _scoped(target, target.parent)
    fd, temporary = tempfile.mkstemp(prefix=".capture-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w+b") as stream:
            # STORED 避免高度可压缩的正文被误判为 ZIP bomb，也无需解压临时目录。
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as package:
                package.writestr("manifest.json", manifest)
                package.writestr("content.json", content)
                for asset_id, (data, _mime) in assets.files.items():
                    package.writestr(f"assets/{asset_id}", data)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise VersionError("采集版本标识重复，请重试")
        # Windows rename 在目标存在时失败；POSIX 用链接发布避免覆盖既有版本。
        if os.name == "nt":
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
            os.unlink(temporary)
        return meta
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture_tables(doc: dict, *, path=None, complete: bool = True) -> dict:
    """从已提交的表格库一致性只读快照创建版本；不经 init_db、不改原库。"""
    key = _key(doc.get("key") or doc.get("doc_key"))
    assets = _Assets()
    sheets = []
    count = 0
    with closing(_database(Path(path or cfg_mod.tables_db_path()))) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sheets", "rows"} <= names:
            raise VersionError("暂无可保存的本地表格")
        for item in conn.execute("SELECT * FROM sheets WHERE doc_key=? ORDER BY rowid", (key,)):
            sheet = {"tab_id": item["tab_id"], "tab_name": item["tab_name"],
                     "kind": item["kind"], "columns": _json_read(str(item["cols_json"] or "[]").encode()),
                     "skipped": bool(item["skipped"]), "fetched_at": item["fetched_at"], "rows": []}
            for row in conn.execute("SELECT record_id,row_no,data_json,updated_ts,synced_at FROM rows "
                                    "WHERE doc_key=? AND tab_id=? ORDER BY row_no,record_id", (key, item["tab_id"])):
                count += 1
                if count > MAX_RECORDS:
                    raise VersionError("采集版本行数超出限制")
                sheet["rows"].append({"record_id": row["record_id"], "row_no": row["row_no"],
                                      "values": _json_read(str(row["data_json"] or "{}").encode()),
                                      "updated_ts": row["updated_ts"], "synced_at": row["synced_at"], "images": []})
            by_id = {r["record_id"]: r for r in sheet["rows"]}
            if "images" in names:
                for image in conn.execute("SELECT record_id,field,idx,local_path,status FROM images "
                                          "WHERE doc_key=? AND tab_id=? ORDER BY row_no,field,idx", (key, item["tab_id"])):
                    row = by_id.get(image["record_id"])
                    if row is not None:
                        row["images"].append({"field": image["field"], "index": image["idx"],
                                              **assets.add(image["local_path"], source="tables", status=image["status"])})
            sheets.append(sheet)
        conn.rollback()
    if not sheets:
        raise VersionError("暂无可保存的本地表格")
    return _write(doc, "tables", sheets, [], assets, complete)


def capture_body(doc: dict, *, path=None, complete: bool = True) -> dict:
    """保存完整已提交正文和受控本地图片；不使用截断的 page_changes。"""
    key = _key(doc.get("key") or doc.get("doc_key"))
    assets = _Assets()
    pages = []
    with closing(_database(Path(path or cfg_mod.pages_db_path()))) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pages" not in names:
            raise VersionError("暂无可保存的本地正文")
        image_map = ({r["page_id"]: _json_read(str(r["images_json"] or "[]").encode())
                      for r in conn.execute("SELECT page_id,images_json FROM page_assets WHERE doc_key=?", (key,))}
                     if "page_assets" in names else {})
        for item in conn.execute("SELECT page_id,title,level,page_order,content,fetched_at FROM pages "
                                 "WHERE doc_key=? ORDER BY page_order,page_id", (key,)):
            if len(pages) >= MAX_RECORDS:
                raise VersionError("采集版本页数超出限制")
            images = []
            for image in image_map.get(item["page_id"], []):
                if not isinstance(image, dict):
                    raise VersionError("本地正文图片清单无效")
                images.append({"key": str(image.get("key") or ""),
                               "name": str(image.get("name") or "图片"),
                               "width": int(image.get("width") or 0), "height": int(image.get("height") or 0),
                               **assets.add(image.get("path"), source="body", status=image.get("status"))})
            pages.append({"id": item["page_id"], "title": item["title"], "text": item["content"],
                          "level": item["level"], "order": item["page_order"], "fetched_at": item["fetched_at"],
                          "images_captured": item["page_id"] in image_map, "images": images})
        conn.rollback()
    return _write(doc, "body", [], pages, assets, complete)


def _package(doc_key: str, version_id: str):
    key, version_id = _key(doc_key), _version_id(version_id)
    target = _target(key, version_id)
    try:
        if not target.is_file() or target.stat().st_size > MAX_TOTAL_BYTES + 4 * 1024 * 1024:
            raise VersionError("采集版本不存在或大小超出限制")
        package = zipfile.ZipFile(target, "r")
        try:
            info = package.infolist()
            if not 2 <= len(info) <= MAX_ENTRIES:
                raise VersionError("采集版本文件数量无效")
            seen, size = set(), 0
            for item in info:
                name = item.filename
                if (name in seen or item.flag_bits & 1 or item.is_dir()
                        or stat.S_ISLNK(item.external_attr >> 16)
                        or name not in ("manifest.json", "content.json")
                        and not re.fullmatch(r"assets/[0-9a-f]{64}", name)):
                    raise VersionError("采集版本文件清单无效")
                limit = MAX_MANIFEST_BYTES if name == "manifest.json" else (
                    MAX_CONTENT_BYTES if name == "content.json" else MAX_ASSET_BYTES)
                if (item.file_size < 0 or item.file_size > limit
                        or item.file_size > max(1, item.compress_size) * MAX_COMPRESSION_RATIO
                        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise VersionError("采集版本文件大小或压缩方式无效")
                size += item.file_size
                seen.add(name)
            if size > MAX_TOTAL_BYTES or not {"manifest.json", "content.json"} <= seen:
                raise VersionError("采集版本总量或内容无效")
            manifest = _json_read(package.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
                raise VersionError("不支持该采集版本格式")
            meta, files, assets = manifest.get("meta"), manifest.get("files"), manifest.get("assets")
            if (manifest.get("doc_key") != key or not isinstance(meta, dict)
                    or meta.get("version_id") != version_id or meta.get("source") not in ("tables", "body")
                    or not isinstance(files, dict) or set(files) != seen - {"manifest.json"}
                    or not isinstance(assets, dict)
                    or set(assets) != {n.split("/", 1)[1] for n in seen if n.startswith("assets/")}):
                raise VersionError("采集版本与文档不匹配或索引无效")
            for name, item in files.items():
                if (not isinstance(item, dict) or item.get("size") != package.getinfo(name).file_size
                        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))):
                    raise VersionError("采集版本校验信息无效")
            for asset_id, item in assets.items():
                if (not isinstance(item, dict) or item.get("entry") != f"assets/{asset_id}"
                        or item.get("mime") not in _MIMES.values()
                        or files[item["entry"]]["sha256"] != asset_id):
                    raise VersionError("采集版本图片索引无效")
            return package, manifest
        except Exception:
            package.close()
            raise
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError) as exc:
        raise VersionError("采集版本缺失或损坏") from exc


def _checked(package, manifest, entry: str) -> bytes:
    try:
        info = manifest["files"][entry]
        with package.open(entry) as stream:
            data = stream.read(int(info["size"]) + 1)
        if len(data) != info["size"] or _sha(data) != info["sha256"]:
            raise VersionError("采集版本完整性校验失败")
        return data
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError) as exc:
        raise VersionError("采集版本完整性校验失败") from exc


def _meta(manifest: dict) -> dict:
    """仅返回显示字段，忽略不可信归档里附带的任何额外属性。"""
    meta = manifest["meta"]
    version_id = meta["version_id"]
    stamp, random_id = version_id.split("-", 1)
    captured_at = datetime.strptime(stamp, "%Y%m%dT%H%M%S%f").strftime("%Y-%m-%d %H:%M:%S")
    def number(name):
        value = meta.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RECORDS:
            raise VersionError("采集版本统计无效")
        return value
    return {"version_id": version_id, "captured_at": captured_at,
            "label": f"{captured_at} - {random_id}", "source": meta["source"],
            "complete": meta.get("complete") is True,
            "missing_images": number("missing_images"),
            **{k: number(k) for k in ("sheets", "rows", "pages", "images")},
            "name": str(meta.get("name") or manifest["doc_key"])}


def list_versions(doc_key: str, source=None) -> list[dict]:
    """按采集时间新到旧列出可读索引；损坏版本不影响其它版本显示。"""
    key = _key(doc_key)
    if source is not None and source not in ("tables", "body"):
        raise VersionError("采集版本来源无效")
    folder = _folder(key)
    if not folder.is_dir():
        return []
    result = []
    for target in sorted(folder.glob("*.tcdoc"), reverse=True):
        if not _VERSION_RE.fullmatch(target.stem):
            continue
        try:
            package, manifest = _package(key, target.stem)
            with package:
                meta = _meta(manifest)
            if source is None or meta["source"] == source:
                result.append(meta)
        except (VersionError, TypeError, ValueError):
            continue
    return result


def read_version(doc_key: str, version_id: str) -> dict:
    """校验内容摘要后返回完整表格 / 正文 DTO；不改变当前采集状态。"""
    package, manifest = _package(doc_key, version_id)
    with package:
        data = _json_read(_checked(package, manifest, "content.json"))
    if (not isinstance(data, dict) or not isinstance(data.get("sheets"), list)
            or not isinstance(data.get("pages"), list)):
        raise VersionError("采集版本内容结构无效")
    # 归档仅有本模块生成的显示 DTO；拒绝图片中的额外路径 / URL，保持读取边界。
    sheets, pages = [], []
    for sheet in data["sheets"]:
        if not isinstance(sheet, dict) or not isinstance(sheet.get("rows"), list) or not isinstance(sheet.get("columns"), list):
            raise VersionError("采集版本表格结构无效")
        rows = []
        for row in sheet["rows"]:
            if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                raise VersionError("采集版本行结构无效")
            _validate_images(row.get("images", []), manifest)
            rows.append({key: row.get(key) for key in
                         ("record_id", "row_no", "values", "updated_ts", "synced_at", "images")})
        sheets.append({**{key: sheet.get(key) for key in
                          ("tab_id", "tab_name", "kind", "columns", "skipped", "fetched_at")}, "rows": rows})
    for page in data["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("text"), str):
            raise VersionError("采集版本正文结构无效")
        _validate_images(page.get("images", []), manifest)
        pages.append({key: page.get(key) for key in
                      ("id", "title", "text", "level", "order", "fetched_at", "images_captured", "images")})
    if ((manifest["meta"]["source"] == "tables" and pages)
            or (manifest["meta"]["source"] == "body" and sheets)):
        raise VersionError("采集版本来源与内容不匹配")
    return {"meta": _meta(manifest), "sheets": sheets, "pages": pages}


def _validate_images(images, manifest):
    if not isinstance(images, list):
        raise VersionError("采集版本图片结构无效")
    allowed = {"asset_id", "status", "key", "name", "width", "height", "field", "index"}
    for image in images:
        if (not isinstance(image, dict) or set(image) - allowed
                or image.get("asset_id") and image["asset_id"] not in manifest["assets"]):
            raise VersionError("采集版本图片结构无效")


def read_asset(doc_key: str, version_id: str, asset_id: str) -> tuple[bytes, str]:
    """只读归档中已登记的图片字节；无网络、无文件提取。"""
    asset_id = str(asset_id or "")
    if not _ASSET_RE.fullmatch(asset_id):
        raise VersionError("采集版本图片标识无效")
    package, manifest = _package(doc_key, version_id)
    with package:
        entry = manifest["assets"].get(asset_id)
        if not entry:
            raise VersionError("采集版本中没有该图片")
        data = _checked(package, manifest, entry["entry"])
    mime = _image(data)
    if mime != entry["mime"]:
        raise VersionError("采集版本图片格式校验失败")
    return data, mime
