"""Qt-free Web use cases for optional local inference components.

The shared runtime owns component/model files. This layer only validates Web
identifiers and resolves model import sources within the configured work folder.
No Web-specific copy of model settings or component state is maintained.
"""

from __future__ import annotations

import os
from pathlib import Path

from toolbox.config_manager import load_convert_config
from toolbox.livestream_summary import optional_runtime
from toolbox.livestream_summary.local_options import FIELD_SPECS


def overview() -> dict:
    data = optional_runtime.status()
    return {**data, "fields": FIELD_SPECS,
            "note": "CPU / GPU 模块与 GGUF 模型均为可选下载，标准版及 FULL 版均不内置。"}


def _work_root() -> Path:
    value = str(load_convert_config().get("work_dir") or "").strip()
    if not value:
        raise ValueError("请先在全局设置中选择工作目录，再导入其中的 GGUF 模型")
    root = Path(value).resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在，请先检查目录设置")
    return root


def model_sources() -> dict:
    """List server-side import candidates, exposing relative IDs only."""
    try:
        root = _work_root()
    except ValueError as exc:
        return {"items": [], "note": str(exc)}
    items = []
    visited = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > 1000:
            break
        parent = Path(base)
        dirs[:] = sorted(name for name in dirs
                         if not name.startswith(".")
                         and not (parent / name).is_symlink()
                         and len((parent / name).relative_to(root).parts) <= 4)
        for name in sorted(files):
            path = parent / name
            if path.suffix.lower() != ".gguf" or path.is_symlink():
                continue
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(root).as_posix()
                size = path.stat().st_size
            except (ValueError, OSError):
                continue
            if "-of-" in name and "-00001-of-" not in name:
                continue
            items.append({"id": relative, "name": relative, "size": size})
            if len(items) >= 200:
                return {"items": items, "note": "仅列出前 200 个 GGUF 首文件。"}
    return {"items": items, "note": "从工作目录导入；分片模型请选择首文件并保留其余分片。"}


def _source_path(value) -> Path:
    value = str(value or "").strip()
    if not value or len(value) > 2048 or value.startswith(("/", "\\")) \
            or any(char in value for char in (":", "\x00")):
        raise ValueError("请选择工作目录中的 GGUF 文件")
    relative = Path(value)
    if ".." in relative.parts:
        raise ValueError("模型文件必须位于工作目录内")
    root = _work_root()
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root)
    except (ValueError, OSError):
        raise ValueError("模型文件必须位于工作目录内") from None
    if candidate.is_symlink() or not candidate.is_file() \
            or candidate.suffix.lower() != ".gguf":
        raise ValueError("模型文件不存在或不是可导入的 GGUF 文件")
    return candidate.resolve()


def prepare_action(action: str, payload: dict) -> dict:
    payload = dict(payload or {})
    if action == "install-runtime":
        backend = str(payload.get("backend") or "")
        if backend not in ("cpu", "vulkan"):
            raise ValueError("请选择 CPU 或 GPU（Vulkan）模块")
        return {"backend": backend}
    if action == "download-model":
        value = str(payload.get("model") or "")
        if value not in {row["id"] for row in optional_runtime.catalog()["models"]}:
            raise ValueError("请选择列表中的模型")
        return {"model": value}
    if action == "import-model":
        _source_path(payload.get("source"))
        return {"source": str(payload.get("source"))}
    if action in ("remove-runtime", "remove-model"):
        if payload.get("confirmed") is not True:
            raise ValueError("请确认卸载范围后再继续")
        value = str(payload.get("id") or "")
        key = "runtimes" if action == "remove-runtime" else "models"
        if value not in {row["id"] for row in optional_runtime.status()[key]}:
            raise ValueError("所选组件或模型不存在，请刷新列表")
        return {"id": value, "confirmed": True}
    raise ValueError("不支持的本地模型操作")


class _CancelFlag:
    def __init__(self, check):
        self.check = check

    def is_set(self):
        return bool(self.check())


def run_action(action: str, payload: dict, *, on_progress=None,
               is_cancelled=None) -> dict:
    payload = prepare_action(action, payload)
    cancel = _CancelFlag(is_cancelled or (lambda: False))
    progress = on_progress or (lambda _percent, _message: None)
    if action == "install-runtime":
        item = optional_runtime.install_runtime(payload["backend"],
                                                progress=progress, cancel=cancel)
        summary = "推理模块已安装，可在直播总结中选择"
    elif action == "download-model":
        item = optional_runtime.download_model(payload["model"],
                                               progress=progress, cancel=cancel)
        summary = "模型已下载，可在直播总结中选择"
    elif action == "import-model":
        item = optional_runtime.import_model(_source_path(payload["source"]),
                                             progress=progress, cancel=cancel)
        summary = "模型已导入，源文件保留，可在直播总结中选择"
    elif action == "remove-runtime":
        item = {"removed": optional_runtime.remove_runtime(payload["id"])}
        summary = "推理模块已卸载，模型与总结设置保留"
    else:
        item = {"removed": optional_runtime.remove_model(payload["id"])}
        summary = "所选模型已删除，导入源文件与推理模块保留"
    return {"summary": summary, "item": item}
