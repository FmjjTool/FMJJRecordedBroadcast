"""Local inference options shared by configuration, desktop and WebUI.

Pure schema only: importing this module performs no IO or dependency detection.
"""

from __future__ import annotations

import math

FIELD_SPECS = (
    {"key": "backend", "label": "推理模块", "type": "select", "default": "cpu",
     "choices": (("cpu", "CPU"), ("vulkan", "GPU（Vulkan）"))},
    {"key": "model", "label": "本地模型", "type": "model", "default": ""},
    {"key": "context", "label": "上下文长度", "type": "int", "default": 16384,
     "min": 2048, "max": 131072, "step": 1024},
    {"key": "threads", "label": "CPU 线程数", "type": "int", "default": 0,
     "min": 0, "max": 256, "step": 1},
    {"key": "gpu_layers", "label": "GPU 卸载层数", "type": "int", "default": 99,
     "min": 0, "max": 999, "step": 1},
    {"key": "batch", "label": "批处理大小", "type": "int", "default": 256,
     "min": 32, "max": 2048, "step": 32},
    {"key": "max_tokens", "label": "最大输出长度", "type": "int", "default": 2048,
     "min": 64, "max": 32768, "step": 64},
    {"key": "temperature", "label": "采样温度", "type": "float", "default": 0.3,
     "min": 0.0, "max": 2.0, "step": 0.1},
)


def defaults() -> dict:
    return {field["key"]: field["default"] for field in FIELD_SPECS}


def normalize_options(values=None, *, strict: bool = True) -> dict:
    """Validate new input; recover malformed legacy settings only on reads."""
    if not isinstance(values, dict):
        if strict and values is not None:
            raise ValueError("本地模型配置格式不正确")
        values = {}
    result = defaults()
    for field in FIELD_SPECS:
        key = field["key"]
        raw = values.get(key, field["default"])
        try:
            if field["type"] == "select":
                value = str(raw or "").strip()
                if value not in {item[0] for item in field["choices"]}:
                    raise ValueError("请选择 CPU 或 GPU（Vulkan）模块")
            elif field["type"] == "model":
                value = str(raw or "").strip()
                if value and (len(value) > 255 or value.startswith(".")
                              or any(char in value for char in '/\\:\x00')
                              or not value.lower().endswith(".gguf")):
                    raise ValueError("请选择已安装的 GGUF 模型")
            else:
                if isinstance(raw, bool):
                    raise ValueError("数值格式不正确")
                if field["type"] == "int":
                    number = float(raw)
                    if not math.isfinite(number) or not number.is_integer():
                        raise ValueError("请输入整数")
                    value = int(number)
                else:
                    value = float(raw)
                if not math.isfinite(value) or not field["min"] <= value <= field["max"]:
                    raise ValueError(f"请输入 {field['min']}–{field['max']} 范围内的数值")
            result[key] = value
        except (ValueError, TypeError, OverflowError) as exc:
            if strict:
                raise ValueError(f"{field['label']}：{exc}") from None
    if result["max_tokens"] >= result["context"]:
        if strict:
            raise ValueError("最大输出长度必须小于上下文长度")
        result["max_tokens"] = min(2048, result["context"] // 2)
    return result
