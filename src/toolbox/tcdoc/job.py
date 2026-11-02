"""腾讯文档监控 · 采集编排（GUI 与 Windows 定时任务共用同一条链路）。

链路：读文档清单 → 浏览器采集 → 落库（每日一行 + 采集明细）→ 生成各自 Excel → 写日志。

首次调用时会把旧版独立工具（腾讯文档数据保存）的历史数据与登录态导进来，
用户不必重新扫码、历史曲线也不会断。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from . import collector, config as cfg_mod, report, storage

LOG_MAX_BYTES = 2 * 1024 * 1024


# ==================== 日志 ====================

def log(message: str) -> None:
    """追加一行采集日志（pythonw / 无控制台环境下也能留痕）。"""
    try:
        path = cfg_mod.log_path()
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            backup = path.with_suffix(".1.log")
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
            except OSError:
                pass
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 - 日志失败不能打断采集
        pass


# ==================== 首次就绪：导旧数据 + 建库 ====================

def _migrate_legacy_once() -> str:
    """旧版数据导入（只跑一次）。返回给用户看的说明，无动作时返回空串。"""
    if cfg_mod.load_cfg().get("tcdoc_legacy_migrated"):
        return ""
    legacy = cfg_mod.find_legacy_dir()
    messages: list[str] = []
    if legacy is not None:
        history = cfg_mod.legacy_db(legacy)
        daily_n, snap_n = storage.import_legacy(history, cfg_mod.db_path())
        if daily_n:
            # 并入旧行后，新旧行之间的增量要按时间顺序重算（否则增量是导入时的旧口径）
            db = cfg_mod.db_path()
            for doc in cfg_mod.load_docs():
                storage.refresh_deltas(db, doc["key"])
            messages.append(f"已导入旧版历史记录 {daily_n} 条")
        old_state = cfg_mod.legacy_state(legacy)
        state = cfg_mod.state_path()
        if old_state.is_file() and not state.exists():
            try:
                state.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old_state, state)
                messages.append("已继承旧版登录态（无需重新扫码）")
            except OSError:
                pass
    cfg_mod.save_cfg(tcdoc_legacy_migrated=True)
    if messages:
        log("旧版数据导入：" + "；".join(messages))
    return "；".join(messages)


def ensure_ready() -> str:
    """建库 + 一次性旧数据导入，返回迁移说明（可能为空串）。"""
    storage.init_db(cfg_mod.db_path())
    return _migrate_legacy_once()


# ==================== 采集 ====================

def collect_all(on_status=None, debug: bool = True) -> list[dict]:
    """采集全部文档并落库 / 出报表。单个文档失败不影响其它文档。"""
    ensure_ready()
    cfg = cfg_mod.load_cfg()
    docs = cfg_mod.load_docs()
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档，请先在「文档管理」里添加")
    db = cfg_mod.db_path()

    log(f"开始采集，共 {len(docs)} 个文档，headless={bool(cfg.get('tcdoc_headless', True))}")
    results = collector.collect(
        docs,
        headless=bool(cfg.get("tcdoc_headless", True)),
        on_status=on_status,
        debug=debug,
    )

    ts, day = storage.now_str(), storage.today_str()
    for item in results:
        if not item.get("ok"):
            storage.add_snapshot(db, item["doc_key"], ts, None, None, None,
                                 False, item.get("error", ""))
            item["date"] = day
            log(f"[{item['name']}] 采集失败：{item.get('error') or '未知错误'}")
            continue

        storage.add_snapshot(db, item["doc_key"], ts, item["views"], item["members"],
                             item.get("readers"), True, "")
        storage.append_daily(db, item["doc_key"], ts, day,
                             item["views"], item["members"], item.get("readers"))
        item["date"] = day
        try:
            out = report.build_report(db, cfg_mod.report_file(item),
                                      item["doc_key"], item["name"])
            item["report"] = str(out)
        except Exception as exc:  # noqa: BLE001 - Excel 被 WPS 占用等
            item["report_error"] = f"报表写入失败（文件可能正被打开）：{exc}"
            log(f"[{item['name']}] {item['report_error']}")

        row = storage.latest(db, item["doc_key"])
        views = "-" if row["views"] is None else row["views"]
        vdelta = "-" if row["views_delta"] is None else f"{row['views_delta']:+d}"
        mdelta = "-" if row["members_delta"] is None else f"{row['members_delta']:+d}"
        log(f"[{item['name']}] 日期={day} 浏览量={views}({vdelta}) "
            f"成员数={row['members']}({mdelta}) 已查看={item.get('readers')} "
            f"来源={item.get('source')}")

    ok_n = sum(1 for item in results if item.get("ok"))
    log(f"采集结束：成功 {ok_n}，失败 {len(results) - ok_n}")
    return results


def rebuild_reports() -> int:
    """按数据库重建全部文档报表，返回文档数。"""
    ensure_ready()
    db, count = cfg_mod.db_path(), 0
    for doc in cfg_mod.load_docs():
        storage.refresh_deltas(db, doc["key"])
        report.build_report(db, cfg_mod.report_file(doc), doc["key"], doc["name"])
        count += 1
    log(f"已重建 {count} 个文档的报表")
    return count


def report_path(doc: dict) -> Path:
    """取当前文档报表路径；不存在则按库里数据即时生成。"""
    path = cfg_mod.report_file(doc)
    if not path.exists():
        storage.refresh_deltas(cfg_mod.db_path(), doc["key"])
        report.build_report(cfg_mod.db_path(), path, doc["key"], doc["name"])
    return path


# ==================== 定时任务入口 ====================

def main() -> int:
    """无界面采集（Windows 计划任务 / 打包 exe --tcdoc-collect 调用）。"""
    try:
        results = collect_all()
    except Exception as exc:  # noqa: BLE001
        log(f"定时采集失败：{exc}")
        return 1
    return 0 if all(item.get("ok") for item in results) else 1
