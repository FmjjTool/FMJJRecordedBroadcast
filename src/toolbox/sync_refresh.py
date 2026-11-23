"""上传前的「同步前刷新」：平台资料 → 腾讯文档，全部成功才允许上传。

数据清洁度规则（用户要求）：
1. 先采平台资料 —— 信息获取（已获取数据）+ 网媒账号追踪（指标 / 抖音直播历史）；
2. 再拉一遍腾讯文档表格（刷新本地快照）；
3. 两步都成功后写一条刷新记录，中间层「上传」才可用。
上传成功或超过有效期后记录作废，下次上传必须重新刷新，不允许拿旧快照比对。
例外：用户开启「免校对模式」（config.json 的 tcdoc_skip_sync_refresh）后，
上传门禁直接放行，用当前本地数据推送；写回仍只新增，不绕过白名单。

状态落在 config/sync_refresh.json：{ts, ts_epoch, steps:{info,tracker,tcdoc}}。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

STALE_MINUTES = 60          # 刷新记录有效期：超过就得重刷
STATE_FILE = "sync_refresh.json"
STEP_LABELS = {"info": "信息获取", "tracker": "网媒账号追踪", "tcdoc": "拉取腾讯文档"}
SKIP_CONFIG_KEY = "tcdoc_skip_sync_refresh"   # 免校对模式（config.json convert 段）


# ==================== 免校对模式 ====================

def bypass_enabled() -> bool:
    """免校对模式是否开启：开启后上传不再要求先「同步前刷新」。"""
    from toolbox.config_manager import load_convert_config
    try:
        return bool(load_convert_config().get(SKIP_CONFIG_KEY, False))
    except Exception:  # noqa: BLE001 - 配置读不到按关闭处理，保持原有门禁
        return False


def set_bypass(enabled: bool) -> None:
    from toolbox.config_manager import load_convert_config, save_convert_config
    cfg = load_convert_config()
    cfg[SKIP_CONFIG_KEY] = bool(enabled)
    save_convert_config(cfg)


def upload_allowed() -> tuple[bool, str]:
    """上传门禁：免校对模式直接放行，否则要求当前数据刚刷新过。"""
    if bypass_enabled():
        return True, "免校对模式：跳过同步前刷新"
    return is_fresh()


# ==================== 状态文件 ====================

def _state_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / STATE_FILE


def load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass


def _step_ok(state: dict, key: str) -> bool:
    steps = state.get("steps") or {}
    return bool((steps.get(key) or {}).get("ok"))


def is_fresh() -> tuple[bool, str]:
    """当前数据是否可以直接用于上传，返回 (是否新鲜, 给用户看的说明)。"""
    state = load_state()
    if not state:
        return False, "尚未执行「同步前刷新」：请先采集平台资料并拉取腾讯文档"
    missing = [label for key, label in STEP_LABELS.items() if not _step_ok(state, key)]
    if missing:
        return False, f"上次刷新未完成（{'、'.join(missing)}失败），请重新刷新"
    age_min = (time.time() - float(state.get("ts_epoch") or 0)) / 60
    if age_min > STALE_MINUTES:
        return False, (f"上次刷新是 {state.get('ts') or '未知时间'}，已超过 {STALE_MINUTES} 分钟，"
                       "请重新刷新后再上传")
    return True, f"数据已于 {state.get('ts')} 刷新（{age_min:.0f} 分钟前）"


def invalidate(reason: str = "上传完成") -> None:
    """作废当前刷新记录：上传后必须重新刷新才能再次上传。"""
    _save_state({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "ts_epoch": time.time(),
                 "steps": {key: {"ok": False, "msg": reason} for key in STEP_LABELS}})


# ==================== 三步刷新 ====================

def _refresh_info(emit) -> tuple[bool, str]:
    """信息获取：采集已配置账号的最新作品并入库（增量）。"""
    from accounts_manager import load_accounts
    from toolbox.account_collection import collect_accounts
    from toolbox.collected_store import ingest_items

    accounts = [a for a in load_accounts() if getattr(a, "url", "")]
    if not accounts:
        return True, "未配置账号，跳过"

    def persist(posts, incremental):
        ingest_items(posts, incremental=True)
        return "", {}

    def on_event(event):
        if event.get("type") == "progress":
            emit(f"信息获取：{event.get('done', 0)}/{event.get('total', 0)} 个账号")

    total, _log, _posts = collect_accounts(
        accounts, 10, incremental=True, on_event=on_event, persist_batch=persist)
    return True, f"{len(accounts)} 个账号 / {total} 条内容"


def _refresh_tracker(emit) -> tuple[bool, str]:
    """网媒账号追踪：指标 + 抖音直播历史（与定时任务同一条链路）。"""
    from toolbox import account_tracker_job

    ok, total = account_tracker_job.collect_tracker_all(
        on_message=lambda msg: emit(f"账号追踪：{msg[:60]}"))
    if total == 0:
        return True, "未配置账号，跳过"
    return ok == total, f"成功 {ok}/{total} 个账号"


def _refresh_tcdoc(emit) -> tuple[bool, str]:
    """拉取腾讯文档：表格快照全量刷新（不含浏览量指标）。"""
    from toolbox.tcdoc import job

    emit("拉取腾讯文档：同步表格快照…")
    results = job.sync_tables()
    ok_n = sum(1 for item in results if item.get("ok"))
    return ok_n == len(results), f"{ok_n}/{len(results)} 篇文档"


def run(on_status=None) -> dict:
    """执行刷新（阻塞，调用方放后台线程）。返回 {ok, steps, ts, message}。"""
    emit = on_status or (lambda _text: None)
    steps: dict = {}
    for key, fn in (("info", _refresh_info), ("tracker", _refresh_tracker),
                    ("tcdoc", _refresh_tcdoc)):
        label = STEP_LABELS[key]
        emit(f"刷新：{label}…")
        try:
            ok, msg = fn(emit)
        except Exception as exc:  # noqa: BLE001 - 单步失败即停，避免脏数据
            ok, msg = False, f"{type(exc).__name__}: {str(exc)[:120]}"
        steps[key] = {"ok": bool(ok), "msg": msg}
        if not ok:
            break

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_ok = len(steps) == len(STEP_LABELS) and all(v["ok"] for v in steps.values())
    _save_state({"ts": ts, "ts_epoch": time.time(), "steps": steps})
    try:
        from toolbox.tcdoc import job
        job.log("同步前刷新：" + "；".join(
            f"{STEP_LABELS[k]}={'成功' if v['ok'] else '失败'}({v['msg']})"
            for k, v in steps.items()))
    except Exception:  # noqa: BLE001 - 日志失败不影响刷新结果
        pass
    if all_ok:
        message = "刷新完成，可以上传"
    else:
        failed = next((STEP_LABELS[k] for k, v in steps.items() if not v["ok"]),
                      "未知步骤")
        message = f"{failed}失败，上传已锁定，请重试"
    return {"ok": all_ok, "steps": steps, "ts": ts, "message": message}