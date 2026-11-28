"""WebUI 网络库：备份映射增删改、立即备份、云端目录浏览与下载。

备份与下载复用桌面端引擎（`toolbox.quark.backup` / `toolbox.quark.download`），
与桌面共用同一份映射配置与登录态；下载目录限制在导出目录或工作目录内。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

ROOT_IDS = {"quark": "0", "baidu": "/", "yun139": "/", "115": "0"}
_MAX_LIST = 3000
_AUTO_EXTRACT_MAX_ENTRIES = 10000
_AUTO_EXTRACT_MAX_TOTAL_SIZE = 10 * 1024 ** 3
_AUTO_EXTRACT_MAX_FILE_SIZE = 5 * 1024 ** 3
_AUTO_EXTRACT_MAX_RATIO = 500


def _modules():
    from toolbox.quark import config as qconfig
    from toolbox.quark import job as qjob
    from toolbox.quark import state as qstate
    return qconfig, qstate, qjob


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _tail(path: Path, max_lines: int = 200, max_bytes: int = 65536) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-max_lines:]


def overview() -> dict:
    qconfig, qstate, qjob = _modules()
    try:
        mappings = qconfig.load_mappings()
    except Exception:
        mappings = []
    rows = []
    for mapping in mappings:
        try:
            stats = qstate.mapping_stats(mapping.id)
        except Exception:
            stats = {"files": 0, "bytes": 0}
        rows.append({
            "id": mapping.id,
            "provider": mapping.provider,
            "provider_name": mapping.provider_label(),
            "local_dir": mapping.local_dir,
            "remote_dir": mapping.remote_dir,
            "enabled": bool(mapping.enabled),
            "exclude": mapping.exclude,
            "max_file_mb": mapping.max_file_mb,
            "share": bool(mapping.share),
            "last_run": mapping.last_run or "",
            "last_result": mapping.last_result or "",
            "last_share_url": mapping.last_share_url or "",
            "files": int(stats.get("files") or 0),
            "bytes": int(stats.get("bytes") or 0),
        })
    try:
        runs = qstate.recent_runs(10)
    except Exception:
        runs = []
    run_rows = [{
        "finished_at": _ts(row.get("finished_at")),
        "local_dir": row.get("local_dir") or "",
        "remote_dir": row.get("remote_dir") or "",
        "scanned": row.get("scanned") or 0,
        "uploaded": row.get("uploaded") or 0,
        "skipped": row.get("skipped") or 0,
        "failed": row.get("failed") or 0,
        "bytes": row.get("bytes") or 0,
        "ok": bool(row.get("ok")),
        "message": str(row.get("message") or "")[:200],
    } for row in runs]
    try:
        schedule = qconfig.load_schedule()
    except Exception:
        schedule = {"mode": "daily", "time": "02:30", "interval_minutes": 120}
    try:
        log_lines = _tail(qjob._log_path())
    except Exception:
        log_lines = []
    return {
        "mappings": rows,
        "total": len(rows),
        "runs": run_rows,
        "schedule": schedule,
        "log": log_lines,
        "providers": [{"value": key, "label": label}
                      for key, label in _providers().items()],
        "root_ids": dict(ROOT_IDS),
        "default_save_dir": str(_default_save_dir()),
        "note": ("可新增 / 编辑 / 启停 / 删除备份映射，并「立即备份」；"
                 "「文件下载」可浏览云端目录并下载到导出目录或工作目录内。"),
    }


def _default_save_dir() -> Path:
    from toolbox.config_manager import get_export_dir
    return get_export_dir() / "网盘下载"


def _providers() -> dict:
    from toolbox.quark import config as qconfig
    return dict(qconfig.PROVIDERS)


def save_mapping(payload: dict) -> dict:
    """新增或修改一条备份映射（按 id 判断修改）。"""
    from toolbox.quark import config as qconfig

    provider = str(payload.get("provider") or "quark").strip()
    if provider not in qconfig.PROVIDERS:
        raise ValueError("未知网盘类型")
    local = str(payload.get("local_dir") or "").strip()
    if not local:
        raise ValueError("请填写本地目录")
    local_path = Path(local)
    if not local_path.is_absolute():
        raise ValueError("本地目录请填写完整路径")
    if not local_path.is_dir():
        raise ValueError("本地目录不存在或不是文件夹")
    remote = qconfig.normalize_remote_dir(payload.get("remote_dir"))
    if not remote:
        raise ValueError("请填写云端目录（如 封茗囧菌直播录屏2017年起）")
    try:
        max_mb = int(payload.get("max_file_mb") or 0)
    except (TypeError, ValueError):
        max_mb = 0
    if max_mb < 0 or max_mb > 1048576:
        raise ValueError("单文件上限需在 0 到 1048576 MB 之间")
    expire = qconfig.normalize_share_expire(payload.get("share_expire"))
    mapping_id = str(payload.get("id") or "").strip()
    if mapping_id:
        mapping = qconfig.mapping_by_id(mapping_id)
        if mapping is None:
            raise ValueError("映射不存在，可能已被删除")
    else:
        mapping = qconfig.Mapping()
    mapping.provider = provider
    mapping.local_dir = str(local_path)
    mapping.remote_dir = remote
    mapping.enabled = bool(payload.get("enabled", True))
    mapping.exclude = str(payload.get("exclude") or "")
    mapping.max_file_mb = max_mb
    mapping.replace_changed = bool(payload.get("replace_changed", True))
    mapping.share = bool(payload.get("share"))
    mapping.share_expire = expire
    mapping.share_passcode = bool(payload.get("share_passcode"))
    qconfig.update_mapping(mapping)
    return {"ok": True, "id": mapping.id,
            "summary": ("已保存映射：" + mapping.title())[:200]}


def set_mapping_enabled(payload: dict) -> dict:
    from toolbox.quark import config as qconfig

    mapping_id = str(payload.get("id") or "").strip()
    mapping = qconfig.mapping_by_id(mapping_id)
    if mapping is None:
        raise ValueError("映射不存在，可能已被删除")
    mapping.enabled = bool(payload.get("enabled"))
    qconfig.update_mapping(mapping)
    return {"ok": True, "id": mapping.id, "enabled": mapping.enabled,
            "summary": "已" + ("启用" if mapping.enabled else "停用") + "该映射"}


def delete_mapping(payload: dict) -> dict:
    from toolbox.quark import config as qconfig

    if payload.get("confirmed") is not True:
        raise ValueError("请确认后再删除映射")
    mapping_id = str(payload.get("id") or "").strip()
    if not qconfig.delete_mapping(mapping_id):
        raise ValueError("映射不存在，可能已被删除")
    return {"ok": True, "id": mapping_id, "summary": "已删除该备份映射"}


def backup(on_progress=None, on_log=None, is_cancelled=None) -> dict:
    """立即备份：与桌面端同一条引擎，逐组更新映射的最近运行结果。"""
    from toolbox.quark import backup as qbackup
    from toolbox.quark import config as qconfig

    mappings = [item for item in qconfig.load_mappings()
                if item.enabled and item.local_dir]
    if not mappings:
        raise ValueError("没有启用的备份目录，请先新增或启用一条映射")
    stop_event = threading.Event()

    def progress(payload) -> None:
        if is_cancelled and is_cancelled():
            stop_event.set()
        if not on_progress:
            return
        percent = int(payload.get("percent") or 0)
        phase = str(payload.get("phase") or "")
        name = str(payload.get("name") or "")
        text = {"mapping": "第 %d/%d 组：%s" % (int(payload.get("current") or 0) + 1,
                                               int(payload.get("total") or 0), name),
                "hash": "校验中 · " + name,
                "file": "上传中 · " + name}.get(phase) or name or "备份中"
        on_progress(percent / 100, text)

    def log(message) -> None:
        if on_log:
            on_log(str(message))

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    results = qbackup.run_all(mappings, progress=progress, log=log,
                              stop_event=stop_event)
    for result in results:
        mapping = qconfig.mapping_by_id(result.mapping_id)
        if mapping is None:
            continue
        mapping.last_run = started
        mapping.last_result = (f"上传 {result.uploaded} · 跳过 {result.skipped}"
                               + (f" · 失败 {result.failed}"
                                  if result.failed else ""))
        if getattr(result, "share_url", ""):
            mapping.last_share_url = result.share_url
        qconfig.update_mapping(mapping)
    failed = [item for item in results if not item.ok]
    uploaded = sum(item.uploaded for item in results)
    skipped = sum(item.skipped for item in results)
    total_bytes = sum(item.bytes for item in results)
    shares = [{"url": item.share_url,
               "passcode": getattr(item, "share_passcode", "")}
              for item in results if getattr(item, "share_url", "")]
    return {
        "ok": not stop_event.is_set(),
        "cancelled": stop_event.is_set(),
        "groups": len(results),
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": len(failed),
        "bytes": total_bytes,
        "shares": shares,
        "failures": [{"local_dir": item.local_dir,
                      "message": str(item.message or "")[:200]}
                     for item in failed],
        "summary": (f"备份完成：上传 {uploaded} · 跳过 {skipped} · "
                    f"{_human_size(total_bytes)}"
                    + (f" · {len(failed)} 组失败" if failed else ""))
                   + ("（已停止）" if stop_event.is_set() else ""),
    }


def ensure_cloud_list(payload: dict) -> dict:
    """列目录前置校验（请求线程执行，失败直接 400）。"""
    provider = str(payload.get("provider") or "quark").strip()
    if provider not in ROOT_IDS:
        raise ValueError("未知网盘类型")
    fid = str(payload.get("fid") or ROOT_IDS[provider]).strip() or ROOT_IDS[provider]
    return {"provider": provider, "fid": fid}


def ensure_cloud_download(payload: dict) -> dict:
    """下载前置校验：网盘、清单与保存目录（不创建目录）。"""
    provider = str(payload.get("provider") or "quark").strip()
    if provider not in ROOT_IDS:
        raise ValueError("未知网盘类型")
    auto_extract = payload.get("auto_extract", False)
    if not isinstance(auto_extract, bool):
        raise ValueError("自动解压选项格式不正确")
    files = payload.get("files") or []
    folders = payload.get("folders") or []
    if not isinstance(files, list) or not isinstance(folders, list):
        raise ValueError("下载清单格式不正确")
    if not files and not folders:
        raise ValueError("请先勾选要下载的文件或文件夹")
    if len(files) > 500 or len(folders) > 50:
        raise ValueError("单次下载数量过多，请分批")
    save_dir = _save_dir(payload.get("save_dir"), create=False)
    return {"provider": provider, "files": files, "folders": folders,
            "save_dir": str(save_dir), "auto_extract": auto_extract}


def cloud_list(provider: str, fid: str) -> dict:
    """列云端目录（最多 3000 项）；只读。"""
    from toolbox.quark import backup as qbackup

    data = ensure_cloud_list({"provider": provider, "fid": fid})
    provider, fid = data["provider"], data["fid"]
    client = qbackup.build_client(provider)
    entries = []
    truncated = False
    try:
        for batch in client.iter_entries(fid):
            for entry in batch:
                entries.append(_entry_row(entry))
                if len(entries) >= _MAX_LIST:
                    truncated = True
                    break
            if truncated:
                break
    except Exception as exc:  # noqa: BLE001 - 各后端异常类型不同
        raise ValueError(f"读取云端目录失败：{exc}") from exc
    entries.sort(key=lambda item: (not item["dir"], item["name"]))
    return {"provider": provider, "fid": fid, "entries": entries,
            "total": len(entries), "truncated": truncated,
            "note": "只读列目录；勾选文件或进入文件夹后可下载。"}


def _entry_row(entry: dict) -> dict:
    name = str(entry.get("file_name") or "")
    return {"fid": str(entry.get("fid") or ""),
            "name": name,
            "dir": bool(entry.get("dir")),
            "size": int(entry.get("size") or 0),
            "updated": _fmt_time(entry.get("updated_at")),
            "download_id": str(entry.get("download_id")
                               or entry.get("fid") or "")}


def _fmt_time(value) -> str:
    if isinstance(value, str) and "T" in value:
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value[:16].replace("T", " ")
    try:
        num = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if num > 10 ** 12:
        num //= 1000
    try:
        return datetime.fromtimestamp(num).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(value or "")


def _save_dir(text: str, create: bool = True) -> Path:
    """下载保存目录：必须是导出目录或工作目录下的路径（安全边界）。"""
    from toolbox.config_manager import get_export_dir, load_convert_config

    raw = str(text or "").strip()
    if not raw:
        raw = str(get_export_dir() / "网盘下载")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("保存目录请填写完整路径")
    roots = [get_export_dir()]
    work = str(load_convert_config().get("work_dir") or "").strip()
    if work and Path(work).is_dir():
        roots.append(Path(work))
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            break
        except ValueError:
            continue
    else:
        raise ValueError("保存目录必须在导出目录或工作目录内")
    if create:
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"保存目录不可用：{exc}") from exc
    return resolved


def _auto_extract_failure(exc: Exception) -> str:
    """Map archive errors to short messages without leaking local paths."""
    message = str(exc)
    safe_messages = {
        "压缩包包含不安全的路径": "压缩包包含不安全路径，已跳过解压",
        "压缩包包含不支持的链接": "压缩包包含不支持的链接，已跳过解压",
        "压缩包解压体积超过安全上限": "解压体积超过 10 GB 安全上限，已跳过解压",
        "压缩包条目数超过安全上限": "压缩包条目超过 10000 个，已跳过解压",
        "压缩包目录内容超过限制": "压缩包目录过大，已跳过解压",
        "压缩包目录单行内容超过限制": "压缩包路径过长，已跳过解压",
        "读取压缩包目录超时": "读取压缩包目录超时，已跳过解压",
        "加密压缩包不支持自动解压": "加密压缩包不支持自动解压，请手动处理",
        "压缩包单文件超过安全上限": "压缩包单文件超过 5 GB 安全上限，已跳过解压",
        "压缩包压缩比超过安全上限": "压缩比超过 500 倍安全上限，已跳过解压",
        "压缩包无法读取，请检查文件完整性":
            "压缩包无法读取，请检查文件完整性",
        "未找到 7z 可执行文件（内置二进制缺失且系统未安装 7-Zip）":
            "未找到 7-Zip，无法自动解压",
    }
    return safe_messages.get(
        message, "压缩包无法解压，请检查文件完整性或保存目录权限")


def _is_reparse_or_symlink(path: Path) -> bool:
    """Reject links and Windows reparse points before archive I/O."""
    try:
        if path.is_symlink():
            return True
        attributes = int(getattr(path.stat(), "st_file_attributes", 0))
    except OSError:
        return True
    return bool(attributes & 0x0400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _validate_extract_paths(archive: str | Path, save_dir: Path) -> tuple[Path, Path]:
    """Keep archive input and same-name output inside the download root."""
    from toolbox import sevenzip

    root = Path(save_dir).resolve()
    source = Path(archive)
    if not source.is_absolute():
        raise sevenzip.SevenZipError("压缩包路径无效")
    try:
        relative = source.relative_to(root)
    except ValueError as exc:
        raise sevenzip.SevenZipError("压缩包路径超出保存目录") from exc
    current = root
    for part in relative.parts:
        current /= part
        if _is_reparse_or_symlink(current):
            raise sevenzip.SevenZipError("压缩包路径包含不支持的链接")
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise sevenzip.SevenZipError("压缩包路径无效") from exc
    if not resolved.is_file():
        raise sevenzip.SevenZipError("压缩包路径无效")
    output = sevenzip.default_extract_dir(resolved)
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise sevenzip.SevenZipError("解压目录超出保存目录") from exc
    current = root
    output_relative = output.relative_to(root)
    for part in output_relative.parts:
        current /= part
        if current.exists() and _is_reparse_or_symlink(current):
            raise sevenzip.SevenZipError("解压目录包含不支持的链接")
    return resolved, output


def cloud_download(payload: dict, on_progress=None, on_log=None,
                   is_cancelled=None) -> dict:
    """下载选中的云端文件 / 文件夹；可选复用桌面 7-Zip 自动解压。"""
    from toolbox.quark import backup as qbackup
    from toolbox.quark import download as qdownload

    data = ensure_cloud_download(payload)
    provider = data["provider"]
    files, folders = data["files"], data["folders"]
    save_dir = _save_dir(data["save_dir"])
    client = qbackup.build_client(provider)
    stop_event = threading.Event()

    def progress(name, done, total, speed=0.0) -> None:
        if is_cancelled and is_cancelled():
            stop_event.set()
        if on_progress:
            on_progress(done / total if total else None,
                        f"{name} {_human_size(done)}/{_human_size(total)}")

    def log(message) -> None:
        if on_log:
            on_log(str(message))

    downloader = qdownload.Downloader(
        client, save_dir, on_progress=progress, on_log=log,
        stop_event=stop_event)
    items = []
    for entry in files:
        items.append(qdownload.DownloadItem(
            fid=str(entry.get("fid") or ""),
            name=str(entry.get("name") or ""),
            size=int(entry.get("size") or 0),
            updated=str(entry.get("updated") or ""),
            download_id=str(entry.get("download_id")
                            or entry.get("fid") or "")))
    for folder in folders:
        items.extend(downloader.walk(str(folder.get("fid") or ""),
                                     str(folder.get("name") or "")))
    if not items:
        raise ValueError("没有可下载的文件（文件夹可能为空）")
    results = downloader.run(items)
    ok = [item for item in results if item.ok]
    failed = [item for item in results if not item.ok]
    total_bytes = sum(item.size for item in ok)
    extracted = 0
    extract_failures = []
    extract_skipped = 0
    extract_tool_unavailable = False
    archives = []
    if data["auto_extract"]:
        from toolbox import sevenzip

        archives = [item for item in results
                    if item.ok and not item.skipped and item.local_path
                    and sevenzip.is_archive(item.local_path)]
        if archives and not sevenzip.available():
            extract_tool_unavailable = True
            extract_skipped = len(archives)
            log("未找到 7-Zip，跳过自动解压")
        else:
            for index, item in enumerate(archives):
                if is_cancelled and is_cancelled():
                    stop_event.set()
                if stop_event.is_set():
                    extract_skipped = len(archives) - index
                    break
                log(f"解压：{item.name}")

                def report_extract_progress(percent, _text=""):
                    if is_cancelled and is_cancelled():
                        stop_event.set()
                    if on_progress:
                        try:
                            pct = max(0, min(100, int(percent)))
                        except (TypeError, ValueError):
                            pct = 0
                        fraction = (index + pct / 100) / len(archives)
                        on_progress(fraction, f"解压 {item.name} · {pct}%")

                try:
                    archive_path, out_dir = _validate_extract_paths(
                        item.local_path, save_dir)
                    entries = sevenzip.list_archive_limited(
                        archive_path,
                        max_entries=_AUTO_EXTRACT_MAX_ENTRIES,
                        max_total_size=_AUTO_EXTRACT_MAX_TOTAL_SIZE,
                        stop_event=stop_event)
                    files_in_archive = [entry for entry in entries
                                        if not entry.is_dir]
                    if not files_in_archive:
                        extract_skipped += 1
                        log(f"压缩包没有可解压文件：{item.name}")
                        continue
                    if any(entry.encrypted for entry in files_in_archive):
                        raise sevenzip.SevenZipError(
                            "加密压缩包不支持自动解压")
                    if any(entry.size > _AUTO_EXTRACT_MAX_FILE_SIZE
                           for entry in files_in_archive):
                        raise sevenzip.SevenZipError(
                            "压缩包单文件超过安全上限")
                    total_unpacked = sum(entry.size for entry in files_in_archive)
                    total_packed = sum(entry.packed for entry in files_in_archive)
                    if (total_unpacked and (not total_packed or
                            total_unpacked / total_packed
                            > _AUTO_EXTRACT_MAX_RATIO)):
                        raise sevenzip.SevenZipError(
                            "压缩包压缩比超过安全上限")
                    sevenzip.extract(
                        archive_path, out_dir, overwrite=True,
                        stop_event=stop_event,
                        on_progress=report_extract_progress)
                    extracted += 1
                    log(f"解压完成：{out_dir.name}")
                except sevenzip.SevenZipError as exc:
                    if stop_event.is_set():
                        extract_skipped = len(archives) - index
                        log(f"解压已停止：{item.name}")
                        break
                    reason = _auto_extract_failure(exc)
                    extract_failures.append({
                        "name": item.name,
                        "error": reason,
                    })
                    log(f"解压失败：{item.name}，{reason}")
                except OSError:
                    extract_failures.append({
                        "name": item.name,
                        "error": "目标目录不可写，请检查保存目录权限后重试",
                    })
                    log(f"解压失败：{item.name}，请检查保存目录权限后重试")

    summary = (f"下载完成：成功 {len(ok)} · 失败 {len(failed)} · "
               f"{_human_size(total_bytes)}")
    if data["auto_extract"]:
        if extract_tool_unavailable:
            summary += " · 自动解压不可用（未找到 7-Zip）"
        elif archives:
            summary += f" · 解压成功 {extracted} · 解压失败 {len(extract_failures)}"
            if extract_skipped:
                summary += f" · 解压跳过 {extract_skipped}"
        else:
            summary += " · 没有新下载的压缩包需要解压"
    if stop_event.is_set():
        summary += "（已停止）"
    return {
        "ok": not stop_event.is_set(),
        "cancelled": stop_event.is_set(),
        "downloaded": len(ok),
        "failed": len(failed),
        "auto_extract": data["auto_extract"],
        "extracted": extracted,
        "extract_failed": len(extract_failures),
        "extract_failures": extract_failures[:50],
        "extract_skipped": extract_skipped,
        "extract_tool_unavailable": extract_tool_unavailable,
        "bytes": total_bytes,
        "save_dir": str(save_dir),
        "failures": [{"name": item.name, "error": str(item.error or "")[:200]}
                     for item in failed][:50],
        "summary": summary,
    }


def _human_size(value) -> str:
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        num = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{int(num)} B" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
