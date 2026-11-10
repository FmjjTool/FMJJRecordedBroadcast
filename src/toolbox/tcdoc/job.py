"""腾讯文档监控 · 采集编排（GUI 与 Windows 定时任务共用同一条链路）。

链路：读文档清单 → 纯 HTTP 采集（指标 + 表格）→ 落库 → 生成各自 Excel → 写日志。
登录、正文预览、诊断仍走 Playwright；浏览量和表格同步不再启动浏览器。

首次调用时会把旧版独立工具（腾讯文档数据保存）的历史数据与登录态导进来，
用户不必重新扫码、历史曲线也不会断。
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import (browse, config as cfg_mod, http_api, images, report, session,
               storage, tables)

LOG_MAX_BYTES = 2 * 1024 * 1024
PARALLEL_WORKERS = 2   # 定时任务并发路数（历史/表格混合，HTTP 请求）


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
    # 已有本地登录快照但 Cookie 管理器里还没登记 → 补登记，登录态统一到管理器
    session.sync_from_state()
    cfg_mod.save_cfg(tcdoc_legacy_migrated=True)
    if messages:
        log("旧版数据导入：" + "；".join(messages))
    return "；".join(messages)


def ensure_ready() -> str:
    """建库 + 一次性旧数据导入，返回迁移说明（可能为空串）。"""
    storage.init_db(cfg_mod.db_path())
    note = _migrate_legacy_once()
    # 幂等：只在「有快照、无 Cookie」时补登记一次，不会覆盖用户在管理器里的新票
    session.sync_from_state()
    return note


# ==================== 采集 ====================

def collect_all(on_status=None) -> list[dict]:
    """采集全部文档的浏览/成员历史并落库 / 出报表；表格数据单独同步。

    手动入口会额外拉取「已查看」浏览人明细（昵称 + 最近浏览时间），
    只在这里执行，不进每日定时任务（浏览记录重翻页，耗时约 5 秒/千条）。
    """
    ensure_ready()
    docs = cfg_mod.load_docs()
    if not docs:
        raise RuntimeError("尚未配置任何腾讯文档，请先在「文档管理」里添加")

    log(f"开始采集历史记录，共 {len(docs)} 个文档（纯 HTTP）")
    results = http_api.collect_metrics(docs, on_status=on_status)
    browse_map = _collect_browse(docs, results, on_status)
    return _store_history_results(results, browse_map)


def _collect_browse(docs: list[dict], results: list[dict], on_status=None) -> dict[str, dict]:
    """逐文档拉浏览人明细；失败只记录，不影响浏览量 / 成员数采集。"""
    ok_keys = {str(item.get("doc_key") or "") for item in results if item.get("ok")}
    by_key = {str(item.get("doc_key") or ""): item for item in results}
    out: dict[str, dict] = {}
    for doc in docs:
        key = str(doc.get("key") or tables.doc_key_of(doc.get("url")) or "")
        if key not in ok_keys:
            continue
        try:
            out[key] = browse.collect(doc, on_status=on_status)
        except Exception as exc:  # noqa: BLE001 - 浏览记录失败不阻断指标采集
            item = by_key.get(key)
            if item is not None:
                item["browse_error"] = str(exc)
            log(f"[{doc.get('name')}] 浏览记录采集失败：{exc}")
    return out


def _store_history_results(results: list[dict],
                           browse_map: dict[str, dict] | None = None) -> list[dict]:
    """把历史采集结果落库并出报表；调用方保证在主线程/单线程执行。"""
    browse_map = browse_map or {}
    db = cfg_mod.db_path()
    ts, day = storage.now_str(), storage.today_str()
    for item in results:
        if not item.get("ok"):
            storage.add_snapshot(db, item["doc_key"], ts, None, None, None,
                                 False, item.get("error", ""))
            item["date"] = day
            log(f"[{item['name']}] 采集失败：{item.get('error') or '未知错误'}")
            continue

        browse_info = browse_map.get(str(item.get("doc_key") or ""))
        if browse_info is not None:
            try:
                stats = storage.save_browsers(db, item["doc_key"],
                                              browse_info.get("rows") or [], ts)
                item["browse"] = stats
                log(f"[{item['name']}] 浏览记录：{stats['total']} 人"
                    f"（新增 {stats['added']}，更新 {stats['updated']}）")
            except Exception as exc:  # noqa: BLE001 - 浏览人入库失败不影响报表主体
                item["browse_error"] = f"浏览记录入库失败：{exc}"
                log(f"[{item['name']}] {item['browse_error']}")

        note = ""
        if item.get("views") is None and "未读取到浏览量" in (item.get("source") or ""):
            note = item["source"]
        storage.add_snapshot(db, item["doc_key"], ts, item["views"], item["members"],
                             item.get("readers"), True, note)
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
        browse = item.get("browse") or {}
        browse_note = (f" 浏览人={browse.get('total')}(新增 {browse.get('added')}"
                       f" 更新 {browse.get('updated')})" if browse else "")
        log(f"[{item['name']}] 日期={day} 浏览量={views}({vdelta}) "
            f"成员数={row['members']}({mdelta}) 已查看={item.get('readers')} "
            f"来源={item.get('source')}{browse_note}")

    ok_n = sum(1 for item in results if item.get("ok"))
    log(f"历史采集结束：成功 {ok_n}，失败 {len(results) - ok_n}")
    return results


def _store_tables(item: dict, info: dict, on_status=None) -> None:
    """把表格同步结果落库并导出 Excel（失败只记录，不影响历史采集）。"""
    if not info.get("ok"):
        item["tables_error"] = info.get("error") or "表格同步失败"
        log(f"[{item['name']}] 表格同步失败：{item['tables_error']}")
        return
    if info.get("unchanged"):
        item["tables_unchanged"] = True
        log(f"[{item['name']}] 文档未变化（rev={info.get('rev') or '-'}），跳过拉取")
        _download_images(item, on_status)
        return
    try:
        stats = tables.save_doc(info)
        item["tables_delta"] = stats["tabs"]
        detail = "  ".join(f"{t['tab_name']}({t['rows']})" for t in stats["tabs"][:6])
        added = sum(t["added"] for t in stats["tabs"])
        updated = sum(t["updated"] for t in stats["tabs"])
        removed = sum(t["removed"] for t in stats["tabs"])
        pending = sum(t.get("pending") or 0 for t in stats["tabs"])
        partial = [t["tab_name"] for t in stats["tabs"] if not t.get("complete", True)]
        if partial:
            # 没拉全的表一律不删本地数据，避免接口截断导致误删
            item["tables_partial"] = "、".join(partial[:6])
            log(f"[{item['name']}] 注意：{'、'.join(partial)} 本次未拉全，"
                f"{pending} 条已保留（未执行删除）")
        log(f"[{item['name']}] 表格同步：{len(stats['tabs'])} 张 / "
            f"新增 {added} 修改 {updated} 删除 {removed}｜{detail}")
        item["tables_rows"] = sum(t["rows"] for t in stats["tabs"])
        excel_ok = True
        try:
            item["tables_excel"] = str(tables.export_excel(item["doc_key"], item["name"]))
        except Exception as exc:  # noqa: BLE001 - Excel 被占用
            excel_ok = False
            item["tables_excel_error"] = f"表格 Excel 写入失败：{exc}"
            log(f"[{item['name']}] {item['tables_excel_error']}")
        # Excel 没写成功不记 rev：下次同步重新拉取并重试，避免一直跳过
        if excel_ok and info.get("rev"):
            tables.set_rev(item["doc_key"], info["rev"])
        _download_images(item, on_status)
    except Exception as exc:  # noqa: BLE001
        item["tables_error"] = f"表格落库失败：{exc}"
        log(f"[{item['name']}] {item['tables_error']}")


def _download_images(item: dict, on_status=None) -> None:
    """表格同步/未变化时都补采缺失图片（已下载自动跳过）。"""
    try:
        image_stats = images.download_doc(item["doc_key"], item["name"],
                                          on_status=on_status)
        item["images"] = image_stats
        if image_stats["total"]:
            log(f"[{item['name']}] 表格图片：共 {image_stats['total']} 张，"
                f"本次下载 {image_stats['downloaded']}，失败 {image_stats['failed']}")
    except Exception as exc:  # noqa: BLE001 - 图片采集失败不影响表格同步
        item["images_error"] = f"图片采集失败：{exc}"
        log(f"[{item['name']}] {item['images_error']}")


def _store_table_results(results: list[dict], on_status=None) -> list[dict]:
    """把表格同步结果逐篇落库/导出；调用方保证在主线程/单线程执行。"""
    for item in results:
        if item.get("ok"):
            _store_tables(item, item, on_status)
        else:
            log(f"[{item.get('name')}] 表格同步失败：{item.get('error')}")
    ok_n = sum(1 for item in results if item.get("ok"))
    changed_n = sum(1 for item in results
                    if item.get("ok") and not item.get("tables_unchanged"))
    log(f"表格同步结束：成功 {ok_n}（变更 {changed_n}，"
        f"未变化 {ok_n - changed_n}），失败 {len(results) - ok_n}")
    return results


def _known_revs(docs: list[dict]) -> dict[str, str]:
    """只对「本地已有表格数据」的文档使用 rev 跳过，避免空库永远同步不到。"""
    out: dict[str, str] = {}
    for doc in docs:
        key = doc.get("key") or tables.doc_key_of(doc.get("url"))
        if not key:
            continue
        try:
            if tables.has_data(key):
                rev = tables.get_rev(key)
                if rev:
                    out[key] = rev
        except Exception:  # noqa: BLE001 - 版本记录异常按首次同步处理
            continue
    return out


def sync_tables(docs: list[dict] | None = None, on_status=None) -> list[dict]:
    """只同步表格数据（不动浏览量）。公开文档不需要登录态也能读。"""
    ensure_ready()
    docs = docs or [d for d in cfg_mod.load_docs()
                    if (d.get("kind") or "").lower() in tables.SYNC_KINDS]
    if not docs:
        raise RuntimeError("清单里没有可同步的表格文档（需要「表格」或「智能表格」类型）")
    log(f"开始同步表格，共 {len(docs)} 个文档（纯 HTTP）")
    results = http_api.collect_tables(docs, on_status=on_status,
                                      known_revs=_known_revs(docs))
    return _store_table_results(results, on_status=on_status)


def tables_excel_path(doc: dict) -> Path:
    """取（或按库里数据即时生成）该文档的表格 Excel。"""
    path = cfg_mod.table_file(doc)
    if not path.exists():
        tables.export_excel(doc["key"], doc["name"], target=path)
    return path


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

def _fetch_history_one(doc: dict) -> dict:
    """定时任务的一个历史指标任务（纯 HTTP）。"""
    try:
        items = http_api.collect_metrics([doc])
        if items:
            return items[0]
        return {"doc_key": doc.get("key"), "name": doc.get("name"),
                "url": doc.get("url"), "ok": False, "error": "采集无返回"}
    except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它任务
        return {"doc_key": doc.get("key"), "name": doc.get("name"),
                "url": doc.get("url"), "ok": False, "error": str(exc)}


def _fetch_table_one(doc: dict, rev: str = "") -> dict:
    """定时任务的一个表格同步任务（纯 HTTP，只抓不落库）。"""
    try:
        key = doc.get("key") or tables.doc_key_of(doc.get("url"))
        known = {key: rev} if key and rev else {}
        items = http_api.collect_tables([doc], known_revs=known)
        if items:
            return items[0]
        return {"doc_key": key, "name": doc.get("name"),
                "url": doc.get("url"), "ok": False, "error": "同步无返回",
                "sheets": [], "skipped": []}
    except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它任务
        return {"doc_key": doc.get("key"), "name": doc.get("name"),
                "url": doc.get("url"), "ok": False, "error": str(exc),
                "sheets": [], "skipped": []}


def run_scheduled() -> int:
    """定时任务：历史指标 + 表格数据并行采集（纯 HTTP，不启动浏览器）。

    历史与表格任务交错提交到同一个线程池（默认 2 路并发），写库/报表统一
    回主线程串行，避免 SQLite 并发写；表格按 rev 跳过未变化的文档。
    """
    ensure_ready()
    docs = cfg_mod.load_docs()
    if not docs:
        log("定时采集失败：尚未配置任何腾讯文档")
        return 1
    table_docs = [d for d in docs
                  if (d.get("kind") or "").lower() in tables.SYNC_KINDS]
    known = _known_revs(table_docs)

    tasks: list[tuple[str, dict]] = []
    for i in range(max(len(docs), len(table_docs))):
        if i < len(docs):
            tasks.append(("history", docs[i]))
        if i < len(table_docs):
            tasks.append(("table", table_docs[i]))

    log(f"定时任务开始：历史 {len(docs)} 篇，表格 {len(table_docs)} 篇，"
        f"并发 {PARALLEL_WORKERS}（纯 HTTP）")
    history_results: list[dict] = []
    table_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        futures = {}
        for kind, doc in tasks:
            if kind == "history":
                fut = pool.submit(_fetch_history_one, doc)
            else:
                key = doc.get("key") or tables.doc_key_of(doc.get("url"))
                fut = pool.submit(_fetch_table_one, doc, known.get(key, ""))
            futures[fut] = (kind, doc)
        for fut in as_completed(futures):
            kind, doc = futures[fut]
            try:
                item = fut.result()
            except Exception as exc:  # noqa: BLE001 - 兜底，正常已在 helper 内捕获
                item = {"doc_key": doc.get("key"), "name": doc.get("name"),
                        "url": doc.get("url"), "ok": False, "error": str(exc)}
            label = "历史" if kind == "history" else "表格"
            state = "成功" if item.get("ok") else (item.get("error") or "失败")
            if item.get("tables_unchanged"):
                state = "未变化"
            log(f"[{doc.get('name')}] {label}采集{state}")
            (history_results if kind == "history" else table_results).append(item)

    _store_history_results(history_results)
    _store_table_results(table_results)
    ok_h = sum(1 for item in history_results if item.get("ok"))
    ok_t = sum(1 for item in table_results if item.get("ok"))
    log(f"定时任务结束：历史 {ok_h}/{len(history_results)}，"
        f"表格 {ok_t}/{len(table_results)}")
    return 0 if ok_h == len(history_results) and ok_t == len(table_results) else 1


def main() -> int:
    """无界面采集（Windows 计划任务 / 打包 exe --tcdoc-collect 调用）。"""
    try:
        return run_scheduled()
    except Exception as exc:  # noqa: BLE001
        log(f"定时采集失败：{exc}")
        return 1
