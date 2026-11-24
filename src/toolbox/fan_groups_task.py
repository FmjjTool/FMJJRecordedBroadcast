"""粉丝群 / QQ群人数快照 · Windows 计划任务（每天 / 按间隔）。

- 任务名：FengMingFanGroupsMonitor
- 命令：pythonw main.py --fan-groups-snapshot（打包后 FengMingToolbox.exe --fan-groups-snapshot）
- 订阅名单：粉丝群账号与 QQ 群共用 config/fan_groups_targets.json，
  每行 {platform, input, name?}；QQ 群记录 platform=qqgroup、input=<分享链接>。
- 定时任务逐个重新查询并保存快照（人数变化以上次快照对比）。
- 日志：config/fan_groups_task.log（超过 1 MB 备份为 .1.log）；
  QQ 群日志只写群号/群名，不写含 token 的分享链接。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TASK_NAME = "FengMingFanGroupsMonitor"
FLAG = "--fan-groups-snapshot"


def _config_dir() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir()


def _targets_path() -> Path:
    return _config_dir() / "fan_groups_targets.json"


def load_targets() -> list[dict]:
    """已订阅的粉丝群主页列表（去重后）。"""
    try:
        data = json.loads(_targets_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for item in data:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").strip()
        value = str(item.get("input") or "").strip()
        key = (platform, value.lower())
        if not platform or not value or key in seen:
            continue
        seen.add(key)
        record = {"platform": platform, "input": value}
        name = str(item.get("name") or "").strip()
        if name:
            record["name"] = name
        out.append(record)
    return out


def add_target(platform: str, owner_input: str, name: str = "") -> bool:
    """把页面上查过的账号加入定时快照名单；已在名单里返回 False。"""
    platform = (platform or "").strip()
    owner_input = (owner_input or "").strip()
    if not platform or not owner_input:
        return False
    items = load_targets()
    key = (platform, owner_input.lower())
    if any((t["platform"], t["input"].lower()) == key for t in items):
        return False
    record = {"platform": platform, "input": owner_input}
    name = (name or "").strip()
    if name:
        record["name"] = name
    items.append(record)
    from toolbox.durable_io import atomic_write_json
    atomic_write_json(_targets_path(), items)
    return True


def update_target(platform: str, old_input: str, new_input: str = "",
                  name: str = "") -> bool:
    """修改订阅条目的链接与名称（账号中心改名/换链接时回写），保持原顺序。"""
    platform = (platform or "").strip()
    old_key = (old_input or "").strip().lower()
    if not platform or not old_key:
        return False
    items = load_targets()
    hit = None
    for item in items:
        if item["platform"] == platform and item["input"].lower() == old_key:
            hit = item
            break
    if hit is None:
        return False
    if (new_input or "").strip():
        hit["input"] = str(new_input).strip()
    name = (name or "").strip()
    if name:
        hit["name"] = name
    elif "name" in hit:
        hit.pop("name", None)
    from toolbox.durable_io import atomic_write_json
    atomic_write_json(_targets_path(), items)
    return True


def remove_target(platform: str, owner_input: str) -> bool:
    items = load_targets()
    key = (platform, (owner_input or "").strip().lower())
    kept = [t for t in items if (t["platform"], t["input"].lower()) != key]
    if len(kept) == len(items):
        return False
    from toolbox.durable_io import atomic_write_json
    atomic_write_json(_targets_path(), kept)
    return True


# ==================== 日志 ====================

def log(message: str) -> None:
    try:
        path = _config_dir() / "fan_groups_task.log"
        try:
            if path.stat().st_size > 1024 * 1024:
                backup = path.with_suffix(".1.log")
                os.replace(path, backup)
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


# ==================== 采集 ====================

def snapshot_all(progress=None) -> tuple[int, int]:
    """逐个订阅账号重新查询并写快照，返回 (成功, 总数)。"""
    from platforms import fan_groups as fg
    from toolbox import qq_groups_store as store

    targets = load_targets()
    ok = 0
    for index, target in enumerate(targets, 1):
        platform, value = target["platform"], target["input"]
        label = fg.PLATFORM_LABELS.get(platform, platform)
        safe = fg.safe_target_text(platform, value)
        if progress:
            progress(f"[{index}/{len(targets)}] {label} {safe}")
        try:
            owner_id, groups = fg.fetch_groups(platform, value)
            if platform == "qqgroup":
                # QQ群：头像进各自群目录，人数历史进 SQLite（不写 JSON 快照）
                for group in groups:
                    fg.download_group_avatar(group)
                previous = store.latest_snapshot(owner_id)
                changes = fg.diff_members(groups, previous)
                for group in groups:
                    store.record(group, source="task")
                moved = sum(1 for v in changes.values() if v)
                log(f"{label} {owner_id}：{len(groups)} 个群，人数变化 {moved} 个，"
                    f"已写入数据库（累计 {store.count(owner_id)} 条）")
                ok += 1
                continue
            previous = fg.load_latest(platform, owner_id)
            changes = fg.diff_members(groups, previous)
            snap = fg.save_snapshot(platform, owner_id, groups)
            moved = sum(1 for v in changes.values() if v)
            log(f"{label} {owner_id}：{len(groups)} 个群，人数变化 {moved} 个，"
                f"快照 {snap.name}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单个账号失败不影响其它账号
            log(f"{label} {safe} 失败：{type(exc).__name__}: {exc}")
    return ok, len(targets)


def main() -> int:
    log("===== 粉丝群快照定时任务开始 =====")
    try:
        from toolbox.config_manager import init_all_configs
        init_all_configs()
        if not load_targets():
            log("尚未订阅任何粉丝群账号或 QQ 群，跳过")
            return 1
        ok, total = snapshot_all(progress=log)
        log(f"===== 粉丝群快照定时任务结束：成功 {ok}/{total} =====")
        return 0 if ok == total else 1
    except Exception as exc:  # noqa: BLE001
        log(f"定时任务失败：{type(exc).__name__}: {exc}")
        return 1


# ==================== 计划任务 ====================

def _run(args: list[str]) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="gbk", errors="ignore", **kwargs)
    except (OSError, ValueError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"无法调用系统计划任务：{exc}")


def _runner_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {FLAG}'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{exe}" "{main_py}" {FLAG}'


def install_daily(time_str: str) -> tuple[bool, str]:
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _runner_command(),
                   "/SC", "DAILY", "/ST", time_str, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, f"每天 {time_str} 自动保存粉丝群快照"


def install_interval(minutes: int) -> tuple[bool, str]:
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False, "间隔分钟数无效"
    if not 1 <= minutes <= 1439:
        return False, "间隔需在 1 ~ 1439 分钟之间（24 小时请用每天模式）"
    result = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _runner_command(),
                   "/SC", "MINUTE", "/MO", str(minutes), "/ST", "00:00", "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    desc = f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"
    return True, f"{desc}自动保存粉丝群快照"


def uninstall() -> tuple[bool, str]:
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        return False, (result.stdout or result.stderr or "未知错误").strip()
    return True, "定时任务已删除，关闭软件后不再保存快照"


def info() -> dict:
    try:
        result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    except Exception:  # noqa: BLE001 - 查不到按未安装处理
        return {}
    if result.returncode != 0:
        return {}
    data: dict = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data