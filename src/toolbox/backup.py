# -*- coding: utf-8 -*-
"""配置与资产备份：导出 / 导入。

配置备份（zip 单文件）：
  config/ 目录下的配置文件（设置/Cookie/账号列表/分组/弹幕姬配置等）
  + dev 模式散落在模块旁的账号数据（music_account / tracker，打包后本就在 config/ 内）。
  排除资产库（collected.db / increment.db 归资产备份）、日志、缓存图片、SQLite 临时文件。

资产备份（zip 或文件夹）：
  config/collected.db + config/increment.db（sqlite 快照导出，导入时按主键合并）
  + 整个统一导出目录（download/，含已获取数据/媒体/弹幕/录制等全部产物）。

zip 内布局（文件夹模式同构，无压缩层）：
  backup_manifest.json        类型标记（config / assets）+ 统计
  config/<相对路径>            仅配置备份
  modules/<模块>/<文件名>      仅配置备份（dev 散落账号数据）
  data/<相对路径>              仅资产备份（导出目录全树）
  db/<库名>                    仅资产备份（sqlite 快照）
"""

import json
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from .config_manager import get_config_dir, get_export_dir

MANIFEST = "backup_manifest.json"

# config/ 目录内排除项：资产库（归资产备份）、日志、缓存图片目录、SQLite 临时/备份文件
_CONFIG_EXCLUDE_FILES = {"collected.db", "increment.db", "rename_log.log"}
_CONFIG_EXCLUDE_DIRS = {"cache"}
_CONFIG_EXCLUDE_SUFFIXES = (".db-wal", ".db-shm", ".bak")

# dev 模式散落在模块旁的账号数据（frozen 时位于 config/，与目录扫描结果去重）
_MODULE_FILES = (
    ("music_account", "music_accounts.json"),
    ("music_account", "music_groups.json"),
    ("tracker", "accounts.json"),
    ("tracker", "groups.json"),
)

# 资产库清单（config/ 下，sqlite 快照导出）
_ASSET_DBS = ("collected.db", "increment.db")

# 媒体类文件 zip 时不压缩（本身已压缩，deflate 只浪费 CPU）
_STORED_SUFFIXES = {".mp4", ".flv", ".mkv", ".mov", ".avi", ".ts", ".m4v", ".wmv",
                    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
                    ".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus",
                    ".zip", ".7z", ".rar"}

# 表名/列名白名单（导入的 sqlite 可能含任意对象名，防注入）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class BackupError(Exception):
    """备份/导入失败（含校验不通过）。"""


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise BackupError("已取消")


def _ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _fmt_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"


def _module_file_path(mod: str, name: str) -> Path:
    """账号数据实际位置：源码运行=模块旁；打包后=config/。"""
    if getattr(sys, "frozen", False):
        return get_config_dir() / name
    return Path(__file__).resolve().parent.parent / mod / name


def _safe_rel(rel_parts) -> bool:
    """zip/清单内相对路径安全检查：不允许 .. / 绝对路径 / 盘符。"""
    return bool(rel_parts) and all(p not in ("", ".", "..") for p in rel_parts)


# ==================== 配置导出 / 导入 ====================

def _collect_config_entries() -> list:
    """[(绝对路径, zip 内相对路径)]：config 目录扫描 + dev 散落账号数据。"""
    entries = []
    cfg = get_config_dir()
    if cfg.is_dir():
        for p in sorted(cfg.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(cfg)
            if rel.parts[0] in _CONFIG_EXCLUDE_DIRS:
                continue
            if p.name in _CONFIG_EXCLUDE_FILES or p.suffix.lower() in _CONFIG_EXCLUDE_SUFFIXES:
                continue
            entries.append((p, ("config",) + rel.parts))
    seen = {str(p.resolve()) for p, _ in entries}
    for mod, name in _MODULE_FILES:
        p = _module_file_path(mod, name)
        if p.exists() and str(p.resolve()) not in seen:
            entries.append((p, ("modules", mod, name)))
    return entries


def export_config(zip_path: str) -> dict:
    """导出配置到 zip（覆盖已存在文件）。返回 {files, bytes, size_text}。"""
    entries = _collect_config_entries()
    if not entries:
        raise BackupError("没有可导出的配置文件")
    total = sum(p.stat().st_size for p, _ in entries)
    zp = Path(zip_path)
    zp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps({
            "type": "config", "version": 1, "created": datetime.now().isoformat(),
            "generator": "FMJJRecordedBroadcast",
        }, ensure_ascii=False, indent=2))
        for p, rel in entries:
            z.write(p, "/".join(rel))
    return {"files": len(entries), "bytes": total, "size_text": _fmt_size(total)}


def import_config(zip_path: str) -> dict:
    """从 zip 导入配置（覆盖写入；散落账号数据按当前运行模式回到实际位置）。

    返回 {files}。导入的是磁盘配置，运行中的页面仍持有内存态，重启后全部生效。
    """
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BackupError(f"无法读取 zip 包：{e}")
    with z:
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except KeyError:
            raise BackupError("不是有效的配置备份（缺 backup_manifest.json）")
        if manifest.get("type") != "config":
            raise BackupError("该备份不是配置备份"
                              + (f"（类型：{manifest.get('type')}）" if manifest.get("type") else ""))
        count = 0
        for name in z.namelist():
            if name == MANIFEST or name.endswith("/"):
                continue
            parts = tuple(name.split("/"))
            if len(parts) >= 2 and parts[0] == "config":
                rel = parts[1:]
                target = get_config_dir().joinpath(*rel)
            elif len(parts) == 3 and parts[0] == "modules":
                target = _module_file_path(parts[1], parts[2])
            else:
                continue
            if not _safe_rel(target.name and parts[1:]):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
            count += 1
    if count == 0:
        raise BackupError("备份包内没有可导入的配置文件")
    return {"files": count}


# ==================== 资产导出 / 导入 ====================

def _snapshot_db(db_path: Path, tmp_path: Path):
    """sqlite 一致性快照（backup API，WAL 下也一致）。"""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(tmp_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


def _merge_db(target: Path, imported: Path) -> int:
    """合并 sqlite：ATTACH + 逐表 INSERT OR IGNORE（主键/去重键冲突跳过）。

    目标缺表则按备份内结构建表，缺列则补列（结构演进兼容）。返回插入行数。
    """
    if not imported.exists() or imported.stat().st_size == 0:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(target, timeout=30)
    inserted = 0
    try:
        dst.execute("ATTACH DATABASE ? AS imp", (str(imported),))
        tables = [r[0] for r in dst.execute(
            "SELECT name FROM imp.sqlite_master WHERE type='table'")]
        tables = [t for t in tables
                  if not t.startswith("sqlite_") and _SAFE_NAME.match(t)]
        for t in tables:
            ddl = (dst.execute("SELECT sql FROM imp.sqlite_master"
                               " WHERE type='table' AND name=?", (t,)).fetchone() or [None])[0]
            if not ddl:
                continue
            if not dst.execute("SELECT 1 FROM sqlite_master"
                               " WHERE type='table' AND name=?", (t,)).fetchone():
                if "IF NOT EXISTS" not in ddl.upper():
                    ddl = ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
                dst.execute(ddl)
            imp_cols = {r[1]: (r[2] or "TEXT") for r in dst.execute(
                f'PRAGMA imp.table_info("{t}")') if _SAFE_NAME.match(r[1] or "")}
            have = {r[1] for r in dst.execute(f'PRAGMA main.table_info("{t}")')}
            for c, ctype in imp_cols.items():
                if c not in have:
                    dst.execute(f'ALTER TABLE main."{t}" ADD COLUMN "{c}" {ctype}')
            if not imp_cols:
                continue
            cols = ",".join(f'"{c}"' for c in imp_cols)
            cur = dst.execute(f'INSERT OR IGNORE INTO main."{t}" ({cols})'
                              f' SELECT {cols} FROM imp."{t}"')
            inserted += max(cur.rowcount, 0)
        dst.commit()
    finally:
        try:
            dst.execute("DETACH DATABASE imp")
        except Exception:
            pass
        dst.close()
    return inserted


def _collect_asset_files() -> list:
    """[(绝对路径, 相对导出根路径)]：统一导出目录全树。"""
    root = get_export_dir()
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                files.append((p, p.relative_to(root)))
    return files


def export_assets(dest: str, mode: str = "zip",
                  on_progress=None, cancel=None) -> dict:
    """导出所有资产。

    mode="zip"：dest = zip 保存路径（已含 .zip）；
    mode="folder"：dest = 目标父目录，内部自动创建「资产备份_时间戳/」子目录。
    返回 {files, bytes, size_text, path, cancelled?}。
    """
    files = _collect_asset_files()
    cfg = get_config_dir()
    dbs = [cfg / n for n in _ASSET_DBS if (cfg / n).exists()]
    if not files and not dbs:
        raise BackupError("没有可备份的资产（导出目录与数据库均为空）")

    total = len(files) + len(dbs)
    done = 0
    total_bytes = sum(p.stat().st_size for p, _ in files)

    if mode == "folder":
        out_root = Path(dest) / f"资产备份_{_ts()}"
        out_root.mkdir(parents=True, exist_ok=True)
        data_dir = out_root / "data"
        db_dir = out_root / "db"
        try:
            for i, (p, rel) in enumerate(files):
                _cancelled(cancel)
                target = data_dir.joinpath(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
                done += 1
                if on_progress:
                    on_progress(done, total, str(rel))
            db_dir.mkdir(parents=True, exist_ok=True)
            for dbp in dbs:
                _cancelled(cancel)
                _snapshot_db(dbp, db_dir / dbp.name)
                done += 1
                if on_progress:
                    on_progress(done, total, dbp.name)
            (out_root / MANIFEST).write_text(json.dumps({
                "type": "assets", "version": 1, "created": datetime.now().isoformat(),
                "generator": "FMJJRecordedBroadcast",
                "export_root": str(get_export_dir()),
                "files": len(files), "bytes": total_bytes,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except BackupError:
            shutil.rmtree(out_root, ignore_errors=True)
            return {"cancelled": True, "files": done, "path": ""}
        return {"files": done, "bytes": total_bytes,
                "size_text": _fmt_size(total_bytes), "path": str(out_root)}

    # zip 模式
    zp = Path(dest)
    zp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr(MANIFEST, json.dumps({
                "type": "assets", "version": 1, "created": datetime.now().isoformat(),
                "generator": "FMJJRecordedBroadcast",
                "export_root": str(get_export_dir()),
                "files": len(files), "bytes": total_bytes,
            }, ensure_ascii=False, indent=2))
            for p, rel in files:
                _cancelled(cancel)
                arc = "data/" + rel.as_posix()
                ctype = (zipfile.ZIP_STORED if p.suffix.lower() in _STORED_SUFFIXES
                         else zipfile.ZIP_DEFLATED)
                z.write(p, arc, compress_type=ctype)
                done += 1
                if on_progress:
                    on_progress(done, total, str(rel))
            with tempfile.TemporaryDirectory() as td:
                for dbp in dbs:
                    _cancelled(cancel)
                    tmp = Path(td) / dbp.name
                    _snapshot_db(dbp, tmp)
                    z.write(tmp, f"db/{dbp.name}", compress_type=zipfile.ZIP_DEFLATED)
                    done += 1
                    if on_progress:
                        on_progress(done, total, dbp.name)
    except BackupError:
        zp.unlink(missing_ok=True)
        return {"cancelled": True, "files": done, "path": ""}
    return {"files": done, "bytes": total_bytes,
            "size_text": _fmt_size(total_bytes), "path": str(zp)}


def import_assets(src: str, on_progress=None, cancel=None) -> dict:
    """导入资产备份（zip 包或备份文件夹，自动识别）。

    文件合并（已存在跳过），数据库按去重键合并（INSERT OR IGNORE）。
    返回 {files_copied, files_skipped, db_rows}。
    """
    src_path = Path(src)
    if src_path.suffix.lower() == ".zip" and src_path.is_file():
        return _import_assets_zip(src_path, on_progress, cancel)
    if src_path.is_dir() and (src_path / MANIFEST).is_file():
        return _import_assets_dir(src_path, on_progress, cancel)
    raise BackupError("请选择资产备份 zip 包或备份文件夹（内含 backup_manifest.json）")


def _check_assets_manifest(manifest) -> None:
    if not isinstance(manifest, dict) or manifest.get("type") != "assets":
        got = (manifest or {}).get("type") if isinstance(manifest, dict) else ""
        raise BackupError("该备份不是资产备份" + (f"（类型：{got}）" if got else ""))


def _import_assets_zip(zp: Path, on_progress, cancel) -> dict:
    try:
        z = zipfile.ZipFile(zp)
    except (zipfile.BadZipFile, OSError) as e:
        raise BackupError(f"无法读取 zip 包：{e}")
    copied = skipped = 0
    db_rows = 0
    export_root = get_export_dir()
    cfg = get_config_dir()
    with z, tempfile.TemporaryDirectory() as td:
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except KeyError:
            raise BackupError("不是有效的资产备份（缺 backup_manifest.json）")
        _check_assets_manifest(manifest)
        names = [n for n in z.namelist() if n != MANIFEST and not n.endswith("/")]
        total = len(names)
        for i, name in enumerate(names, 1):
            _cancelled(cancel)
            parts = tuple(name.split("/"))
            if len(parts) >= 2 and parts[0] == "data" and _safe_rel(parts[1:]):
                target = export_root.joinpath(*parts[1:])
                if target.exists():
                    skipped += 1
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as fsrc, open(target, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    copied += 1
            elif len(parts) == 2 and parts[0] == "db" and _safe_rel((parts[1],)):
                tmp_db = Path(td) / parts[1]
                with z.open(name) as fsrc, open(tmp_db, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
                db_rows += _merge_db(cfg / parts[1], tmp_db)
                tmp_db.unlink()
            if on_progress:
                on_progress(i, total, parts[-1])
    return {"files_copied": copied, "files_skipped": skipped, "db_rows": db_rows}


def _import_assets_dir(root: Path, on_progress, cancel) -> dict:
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except Exception:
        raise BackupError("backup_manifest.json 读取失败（文件损坏）")
    _check_assets_manifest(manifest)
    copied = skipped = 0
    db_rows = 0
    export_root = get_export_dir()
    cfg = get_config_dir()
    files = []
    for sub, base in (("data", export_root), ("db", cfg)):
        d = root / sub
        if d.is_dir():
            files.extend((p, p.relative_to(d), base) for p in sorted(d.rglob("*")) if p.is_file())
    total = len(files)
    for i, (p, rel, base) in enumerate(files, 1):
        _cancelled(cancel)
        if base == cfg:   # db/ → 合并进现有库
            db_rows += _merge_db(cfg / rel.name, p)
        else:             # data/ → 新文件复制，同名跳过
            target = base.joinpath(rel)
            if target.exists():
                skipped += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
                copied += 1
        if on_progress:
            on_progress(i, total, str(rel))
    return {"files_copied": copied, "files_skipped": skipped, "db_rows": db_rows}
