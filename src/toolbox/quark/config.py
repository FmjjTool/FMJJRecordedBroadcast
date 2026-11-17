# -*- coding: utf-8 -*-
"""夸克网盘备份 · 配置（多组映射 + 计划设置）。

全部存在 `convert` 配置里（与腾讯文档监控 / 账号追踪的计划设置同一处），
键名统一 `quark_` 前缀，读写都走 `toolbox.config_manager`。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# 备份后端：key → 中文名（界面与日志都用这张表）
PROVIDERS = {"quark": "夸克网盘", "baidu": "百度网盘"}

CFG_MAPPINGS = "quark_backup_mappings"
CFG_MODE = "quark_schedule_mode"
CFG_TIME = "quark_backup_time"
CFG_INTERVAL = "quark_interval_minutes"


def _cfg():
    from toolbox.config_manager import load_convert_config, save_convert_config
    return load_convert_config, save_convert_config


@dataclass
class Mapping:
    """一条「本地目录 → 云端文件夹」映射。"""

    # 存储后端：quark（夸克网盘）/ baidu（百度网盘）
    provider: str = "quark"
    local_dir: str = ""
    remote_dir: str = ""
    enabled: bool = True
    # 排除规则：分号或换行分隔的 glob，支持目录前缀（如 cache/*、*.tmp）
    exclude: str = ""
    # 单文件大小上限（MB），0 = 不限；超过则跳过并记入日志
    max_file_mb: int = 0
    # 本地文件内容变了：True = 先删掉上次上传的那一份再传（只删自己记录过的 fid），
    # False = 只追加，云端会留下 (1)(2) 副本
    replace_changed: bool = True
    last_run: str = ""
    last_result: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Mapping":
        data = data or {}
        try:
            max_mb = int(data.get("max_file_mb") or 0)
        except (TypeError, ValueError):
            max_mb = 0
        return cls(
            provider=str(data.get("provider") or "quark").strip() or "quark",
            local_dir=str(data.get("local_dir") or "").strip(),
            remote_dir=normalize_remote_dir(data.get("remote_dir") or ""),
            enabled=bool(data.get("enabled", True)),
            exclude=str(data.get("exclude") or ""),
            max_file_mb=max(0, max_mb),
            replace_changed=bool(data.get("replace_changed", True)),
            last_run=str(data.get("last_run") or ""),
            last_result=str(data.get("last_result") or ""),
            id=str(data.get("id") or "") or uuid.uuid4().hex[:12],
        )

    def exclude_patterns(self) -> list[str]:
        raw = (self.exclude or "").replace("\r", "\n")
        for sep in (";", "；", "|"):
            raw = raw.replace(sep, "\n")
        return [p.strip().replace("\\", "/") for p in raw.split("\n") if p.strip()]

    def provider_label(self) -> str:
        return PROVIDERS.get(self.provider, self.provider or "未知")

    def title(self) -> str:
        local = self.local_dir or "（未选本地目录）"
        remote = self.remote_dir or "（未设云端目录）"
        return f"{local} → {remote}"


def normalize_remote_dir(value: str) -> str:
    """云端目录统一成不带首尾斜杠的形式：`/备份/2026/` → `备份/2026`。"""
    parts = [p for p in str(value or "").replace("\\", "/").split("/") if p.strip()]
    return "/".join(p.strip() for p in parts)


def load_mappings() -> list[Mapping]:
    load, _save = _cfg()
    cfg = load()
    raw = cfg.get(CFG_MAPPINGS)
    if not isinstance(raw, list):
        return []
    out: list[Mapping] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(Mapping.from_dict(item))
    return out


def save_mappings(items: list[Mapping]) -> None:
    load, save = _cfg()
    cfg = load()
    cfg[CFG_MAPPINGS] = [m.to_dict() for m in items]
    save(cfg)


def mapping_by_id(mapping_id: str) -> Mapping | None:
    for item in load_mappings():
        if item.id == mapping_id:
            return item
    return None


def update_mapping(mapping: Mapping) -> None:
    """按 id 覆盖写回一条映射（不存在则追加）。"""
    items = load_mappings()
    for index, item in enumerate(items):
        if item.id == mapping.id:
            items[index] = mapping
            break
    else:
        items.append(mapping)
    save_mappings(items)


def delete_mapping(mapping_id: str) -> bool:
    items = load_mappings()
    kept = [m for m in items if m.id != mapping_id]
    if len(kept) == len(items):
        return False
    save_mappings(kept)
    return True


def load_schedule() -> dict[str, Any]:
    load, _save = _cfg()
    cfg = load()
    try:
        interval = int(cfg.get(CFG_INTERVAL, 120) or 120)
    except (TypeError, ValueError):
        interval = 120
    return {
        "mode": str(cfg.get(CFG_MODE) or "daily"),
        "time": str(cfg.get(CFG_TIME) or "02:30"),
        "interval_minutes": max(1, min(1439, interval)),
    }


def save_schedule(mode: str, time_str: str, interval_minutes: int) -> dict[str, Any]:
    load, save = _cfg()
    cfg = load()
    cfg[CFG_MODE] = "interval" if str(mode) == "interval" else "daily"
    cfg[CFG_TIME] = str(time_str or "02:30")
    try:
        minutes = int(interval_minutes)
    except (TypeError, ValueError):
        minutes = 120
    cfg[CFG_INTERVAL] = max(1, min(1439, minutes))
    save(cfg)
    return load_schedule()
