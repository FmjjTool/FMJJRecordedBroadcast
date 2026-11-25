"""WebUI 依赖组件状态（L3，只读）。

判定口径与桌面端「依赖与可选组件」一致；本模块只做检测，
组件随软件整体安装 / 卸载，不提供单独卸载或安装入口。
"""

from __future__ import annotations

import os
import shutil


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


def dependency_status() -> dict:
    """逐项检测依赖可用性（只读）。"""
    items = []
    for key, name, usage, check in _CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, f"检测异常: {str(exc)[:40]}"
        items.append({"key": key, "name": name, "usage": usage,
                      "ok": bool(ok), "detail": detail})
    return {
        "items": items,
        "ready": sum(1 for item in items if item["ok"]),
        "total": len(items),
        "note": "只读检测；组件随软件整体安装 / 卸载，Web 端不提供单独安装或卸载。",
    }
