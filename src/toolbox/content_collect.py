"""超话与抖音指数的订阅式定时采集用例（L3，Qt-free）。

表单、默认值与校验共用 FIELDS。只采明确勾选的目标；已有主题/达人只是候选。
超话复用访客引擎和原有长效库；指数仅保存原有格式快照，不回写资产或平台。
配置唯一 writer 为 content_collect_store，不触发全局初始化、登录或凭据迁移。
"""

from __future__ import annotations

import copy
import re
import time

from toolbox import content_collect_store as store

FIELDS = {
    "chaohua": [
        {"name": "targets", "label": "采集超话", "type": "multiselect", "default": [], "required": True,
         "empty_hint": "尚无可选超话，请先在微博超话完成一次采集后重新打开设置"},
        {"name": "max_pages", "label": "每频道页数", "type": "int", "default": 3,
         "min": 1, "max": 20},
    ],
    "douyin_index": [
        {"name": "targets", "label": "采集达人", "type": "multiselect", "default": [], "required": True,
         "empty_hint": "尚无可选达人，请先在抖音指数完成一次抓取后重新打开设置"},
        {"name": "days", "label": "统计范围", "type": "select", "default": 30,
         "choices": [{"value": days, "label": f"近 {days} 天"}
                     for days in (30, 90, 180, 365)]},
    ],
}
LABELS = {"chaohua": "微博超话采集", "douyin_index": "抖音指数快照"}
MAX_TARGETS = 50
TARGET_INTERVAL = 1.0


def _kind(kind: str) -> str:
    if kind not in FIELDS:
        raise ValueError("未知的内容采集任务")
    return kind


def _target_id(kind: str, value) -> str:
    if not isinstance(value, str):
        raise ValueError("采集对象 ID 格式不正确")
    value = value.strip()
    if kind == "chaohua":
        match = re.fullmatch(r"(?:1022:)?(10080[0-9a-fA-F]{10,80})", value)
        if match:
            return match.group(1).lower()
    elif re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    raise ValueError("采集对象 ID 格式不正确")


def validate_settings(kind: str, values, require_targets: bool = True) -> dict:
    """只接受字段表声明的参数；拒绝布尔整数、超限参数和任意 URL。"""
    _kind(kind)
    if not isinstance(values, dict):
        raise ValueError("采集设置格式不正确")
    clean = {}
    for field in FIELDS[kind]:
        name = field["name"]
        value = values.get(name, copy.deepcopy(field["default"]))
        if field["type"] == "multiselect":
            if not isinstance(value, list) or len(value) > MAX_TARGETS:
                raise ValueError(f"采集对象最多 {MAX_TARGETS} 个")
            value = list(dict.fromkeys(_target_id(kind, item) for item in value))
            if require_targets and not value:
                raise ValueError("请至少勾选一个采集对象")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(field["label"] + "格式不正确")
            try:
                value = int(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(field["label"] + "格式不正确") from exc
            if field["type"] == "int" and not field["min"] <= value <= field["max"]:
                raise ValueError(f"{field['label']}需在 {field['min']}–{field['max']} 之间")
            if field["type"] == "select" and value not in [item["value"] for item in field["choices"]]:
                raise ValueError(field["label"] + "不在支持范围内")
        clean[name] = value
    return clean


def get_settings(kind: str) -> dict:
    _kind(kind)
    values = validate_settings(kind, store.load()["collectors"].get(kind, {}), False)
    fields = copy.deepcopy(FIELDS[kind])
    raw_choices = store.topic_choices() if kind == "chaohua" else store.index_choices()
    choices = {}
    for item in raw_choices:
        try:
            identity = _target_id(kind, item.get("value"))
        except (ValueError, AttributeError):
            continue
        label = " ".join(str(item.get("label") or identity).split())[:100]
        choices.setdefault(identity, {"value": identity, "label": label})
    for identity in values["targets"]:
        choices.setdefault(identity, {"value": identity, "label": identity + "（已订阅）"})
    fields[0]["choices"] = list(choices.values())
    return {"fields": fields, "values": values}


def save_settings(kind: str, values) -> dict:
    clean = validate_settings(kind, values)
    store.save(kind, clean)
    return clean


class CollectCancelled(Exception):
    """停止后保留已经完成的目标，不把未完成目标记作成功。"""


def _check_cancel(is_cancelled) -> None:
    if is_cancelled and is_cancelled():
        raise CollectCancelled("已取消后续采集，已保存结果保留")


def _target_cooldown(is_cancelled) -> None:
    """目标之间保留一秒冷却；短等待使取消无需等完整冷却结束。"""
    deadline = time.monotonic() + TARGET_INTERVAL
    while True:
        _check_cancel(is_cancelled)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _collect_topic(identity: str, settings: dict, is_cancelled) -> dict:
    from platforms import weibo_chaohua as wch
    from platforms import weibo_chaohua_store as chs

    class Flag:
        def is_set(self):
            return bool(is_cancelled and is_cancelled())

    flag = Flag()
    started = time.time()
    client = wch.ChaohuaClient(identity, stop_event=flag)
    try:
        info = client.fetch_overview()
        _check_cancel(is_cancelled)
        channels = [{"name": item.get("name") or "频道",
                     "flow_id": item["flow_id"], "max_pages": settings["max_pages"]}
                    for item in info.get("channels") or []
                    if isinstance(item, dict) and item.get("flow_id")]
        if not channels:
            raise ValueError("未获取到可采集频道")
        data = client.crawl(channels)
        stopped = flag.is_set()
        info.setdefault("topic_id", identity)
        saved = chs.save_crawl(info, data, started, time.time(),
                               status="stopped" if stopped else "done")
        if stopped:
            raise CollectCancelled("已取消，已采集部分已保存")
        return {"posts": len(wch.merge_posts(data)),
                "new_posts": int(saved.get("new_posts") or 0)}
    except wch.ChaohuaStopped as exc:
        raise CollectCancelled("已取消，本次采集未完成") from exc
    finally:
        session = getattr(client, "session", None)
        if session is not None:
            session.close()


def _collect_index(identity: str, settings: dict, is_cancelled) -> dict:
    from platforms import douyin_index as dxi

    def progress(_message):
        _check_cancel(is_cancelled)

    data = dxi.fetch_daren_all(identity, days=settings["days"], progress=progress)
    _check_cancel(is_cancelled)
    dxi.save_snapshot(data)
    return {"videos": len(data.get("videos") or [])}


def _result(kind: str) -> dict:
    return {"kind": kind, "ok": False, "status": "pending", "total": 0,
            "succeeded": 0, "failed": 0, "skipped": 0, "cancelled": False,
            "results": []}


def run(kind: str, on_log=None, is_cancelled=None) -> dict:
    """单类任务防重入；目标顺序执行，单目标异常不阻断其余目标。"""
    _kind(kind)
    outcome = _result(kind)

    def say(message):
        store.log(kind, message)
        if on_log:
            on_log(message)

    try:
        with store.exclusive_lock("run-" + kind):
            settings = validate_settings(kind, store.load()["collectors"].get(kind, {}), False)
            targets = settings["targets"]
            outcome["total"] = len(targets)
            if not targets:
                outcome.update(ok=True, status="skipped", summary="没有已订阅对象，本次跳过")
                say(outcome["summary"])
                return outcome
            collect = _collect_topic if kind == "chaohua" else _collect_index
            say(f"{LABELS[kind]}开始：{len(targets)} 个订阅对象")
            for index, identity in enumerate(targets, 1):
                _check_cancel(is_cancelled)
                if index > 1:
                    _target_cooldown(is_cancelled)
                say(f"采集对象 {index}/{len(targets)}")
                try:
                    details = collect(identity, settings, is_cancelled)
                    outcome["results"].append({"id": identity, "ok": True, **details})
                    outcome["succeeded"] += 1
                    say(f"对象 {index}/{len(targets)}已保存")
                except CollectCancelled:
                    outcome["results"].append({"id": identity, "ok": False, "cancelled": True})
                    raise
                except Exception:
                    if is_cancelled and is_cancelled():
                        outcome["results"].append({"id": identity, "ok": False, "cancelled": True})
                        raise CollectCancelled("已取消后续采集，已保存结果保留") from None
                    outcome["failed"] += 1
                    outcome["results"].append({"id": identity, "ok": False,
                                               "error": "采集或保存失败，请检查网络、登录状态与写入权限"})
                    say(f"对象 {index}/{len(targets)}采集或保存失败，继续其余对象")
            outcome.update(ok=outcome["failed"] == 0,
                           status="done" if outcome["failed"] == 0 else "failed")
    except store.LockBusy:
        outcome.update(status="busy", skipped=1, summary="同类采集任务正在运行，本次未重复执行")
    except CollectCancelled:
        outcome.update(status="cancelled", cancelled=True)
    except Exception:
        outcome.update(status="failed", failed=max(1, outcome["failed"]),
                       summary="采集任务无法开始，请检查订阅设置与写入权限")
    outcome["skipped"] = max(outcome["skipped"], outcome["total"] -
                             outcome["succeeded"] - outcome["failed"])
    outcome.setdefault("summary", f"{LABELS[kind]}：成功 {outcome['succeeded']} / "
                       f"失败 {outcome['failed']} / 未完成 {outcome['skipped']}"
                       + (" · 已取消" if outcome["cancelled"] else ""))
    say(outcome["summary"])
    return outcome


def main(kind: str) -> int:
    """无窗口入口；不运行全局配置迁移或初始化登录模块。"""
    try:
        return 0 if run(kind)["ok"] else 1
    except Exception:
        if kind in FIELDS:
            store.log(kind, "采集任务未能完成，请检查订阅设置")
        return 1
