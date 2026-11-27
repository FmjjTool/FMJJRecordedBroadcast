"""Qt-free dependency status and installation use cases for global settings.

Python installation delegates to the same controlled installer as desktop.
Local inference downloads/removals remain owned by optional_runtime and use
the existing webui_local_llm actions; this module only exposes their status.
"""

from __future__ import annotations

import os
import shutil
import sys


def _douyin_sign() -> tuple[bool, str]:
    try:
        import multi_downloader as md

        signature = md._douyin_abogus(
            "device_platform=webapp&aid=6383&count=20", md.UA_PC)
        if not signature:
            return False, "签名失败"
        node = md._find_node()
        which = shutil.which("node")
        same = (which and os.path.normcase(os.path.abspath(node))
                == os.path.normcase(os.path.abspath(which)))
        return True, f"node: {'PATH' if same else 'Playwright 内置'}"
    except Exception as exc:
        return False, f"node 未找到: {str(exc)[:20]}"


def _browser_engine() -> tuple[bool, str]:
    from toolbox.browser import detect, headless_shell_installed

    try:
        env = detect()
    except Exception:
        env = {}
    shell = headless_shell_installed()
    if env.get("edge"):
        return True, f"系统 Edge {env['edge']}" + (
            " · 本地内核已下载" if shell else "")
    if env.get("chrome"):
        return True, f"系统 Chrome {env['chrome']}" + (
            " · 本地内核已下载" if shell else "")
    if shell:
        return True, "本地内核已就绪"
    return False, "未检测到 Edge / Chrome 或本地内核"


def _asr_program() -> tuple[bool, str]:
    from toolbox.asr_engine import local_program_path, local_program_ready
    from toolbox.utils import format_size

    if not local_program_ready():
        return False, "未下载"
    try:
        return True, f"已下载 {format_size(local_program_path().stat().st_size)}"
    except OSError:
        return True, "已下载"


def _asr_model() -> tuple[bool, str]:
    from toolbox.asr_engine import LOCAL_MODELS, local_model_ready

    ready = [label for key, label, _size in LOCAL_MODELS if local_model_ready(key)]
    if not ready:
        return False, "未下载"
    name = ready[0].split("（")[0]
    return True, (f"{name} 等 {len(ready)} 个" if len(ready) > 1 else name)


def _clip_model() -> tuple[bool, str]:
    from toolbox import image_embedding

    if getattr(image_embedding, "ort", None) is None:
        return False, "缺 onnxruntime"
    status = image_embedding.model_status()
    if not status.get("ready"):
        return False, str(status.get("reason") or "未下载")[:40]
    size = status.get("size") or 0
    return True, (f"已就绪 {size / 1024 ** 2:.0f}MB" if size else "已就绪")


_CHECKS = [
    ("douyin_sign", "抖音签名环境", "抖音评论区 / 弹幕 / 详情接口签名", _douyin_sign),
    ("browser_engine", "采集引擎", "无系统 Edge / Chrome 时使用的本地内核", _browser_engine),
    ("asr_program", "本地转写程序", "离线字幕转写程序", _asr_program),
    ("asr_model", "本地转写模型", "离线转写模型", _asr_model),
    ("clip_model", "图识别模型", "以图搜图的视觉编码器", _clip_model),
]

_PYTHON_DEPENDENCIES = (
    ("PySide6", "桌面 GUI 框架（Qt for Python）", False),
    ("requests", "HTTP 请求库", False),
    ("PIL", "图片处理", False),
    ("bs4", "HTML 解析", False),
    ("lxml", "HTML / XML 解析", False),
    ("httpx", "HTTP 请求库", False),
    ("openpyxl", "Excel 表格导入导出", False),
    ("mutagen", "音乐标签读写", False),
    ("pyzipper", "可选：证书记录的 AES-256 ZIP 加密导出，不装不影响其他功能", True),
    ("websocket", "直播弹幕长连接", False),
    ("playwright", "登录 / 采集浏览器驱动", False),
)


def prepare_install(key: str) -> str:
    key = str(key or "")
    if key not in {row[0] for row in _PYTHON_DEPENDENCIES}:
        raise ValueError("所选依赖不支持安装，请刷新列表")
    if getattr(sys, "frozen", False):
        raise ValueError("未内置，请重装软件")
    return key


def install(key: str, *, on_progress=None, is_cancelled=None) -> dict:
    from toolbox import dependency_installer

    return dependency_installer.install_dependency(
        prepare_install(key), progress=on_progress, cancel=is_cancelled)


def dependency_status() -> dict:
    """逐项检测依赖可用性（只读）。"""
    from toolbox import dependency_installer
    from toolbox.livestream_summary import optional_runtime

    items = []
    for key, usage, optional in _PYTHON_DEPENDENCIES:
        snapshot = dependency_installer.dependency_status(key)
        installed = bool(snapshot.get("installed"))
        items.append({"key": key, "name": snapshot.get("name") or key,
                      "usage": usage, "optional": optional, "ok": installed,
                      "detail": snapshot.get("version") or snapshot.get("reason") or "未安装",
                      "can_install": bool(snapshot.get("supported")) and not installed})
    for key, name, usage, check in _CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, f"检测异常: {str(exc)[:40]}"
        items.append({"key": key, "name": name, "usage": usage,
                      "ok": bool(ok), "detail": detail})
    local = optional_runtime.status()
    for key, name, kind, ready, detail in (
        ("local_llm_runtime", "本地总结模块", "runtime",
         any(row.get("installed") for row in local.get("runtimes", ())),
         "CPU / GPU 模块独立下载，标准版与 FULL 版均不内置"),
        ("local_llm_model", "本地总结模型", "model", bool(local.get("models")),
         "GGUF 模型与模块分别准备，可下载或导入"),
    ):
        items.append({"key": key, "name": name, "usage": detail,
                      "ok": ready, "optional": True,
                      "detail": "已就绪" if ready else "未下载",
                      "local_llm_kind": kind})
    return {
        "items": items,
        "ready": sum(1 for item in items if item["ok"]),
        "total": len(items),
        "note": "源码运行时可安装缺失的 Python 依赖；打包版缺失内置组件请重装软件。"
                "本地总结模块和模型可分别下载、卸载。",
    }
