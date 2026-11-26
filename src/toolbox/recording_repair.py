# -*- coding: utf-8 -*-
"""录制文件修复用例（Qt-free，桌面与 WebUI 共用）。

FLV 复用既有 tag 修复器；TS 由 FFmpeg 丢弃损坏包、补生成时间戳并流复制重封装。
这不恢复已经丢失的画面或声音。源文件只读，结果使用同格式的新文件，不覆盖既有文件。
本模块不读取账号、凭据或录制配置。FFmpeg 查找复用全局依赖定位入口。
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


_MAX_REPAIR_SECONDS = 1800


class _RepairStopped(BaseException):
    """穿过既有 FLV 进度回调的 Exception 兜底，及时关闭文件并交还用例收尾。"""

    def __init__(self, cancelled):
        self.cancelled = cancelled


def _is_cancelled(cancel):
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())


def _notify(callback, value):
    if callback:
        try:
            callback(float(value))
        except Exception:
            pass  # 显示层的进度更新不能破坏已经完成的修复。


def _publish_new(temporary, destination):
    """优先硬链接原子发布；不支持硬链接的文件系统使用独占创建，永不覆盖。"""
    try:
        os.link(temporary, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    created = False
    try:
        with open(destination, "xb") as output:
            created = True
            with open(temporary, "rb") as source:
                shutil.copyfileobj(source, output, 1024 * 1024)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _repair_ts(source, temporary, checkpoint):
    from .utils import find_ffmpeg_exe

    exe = find_ffmpeg_exe()
    if not exe or (not os.path.isfile(exe) and not shutil.which(exe)):
        raise FileNotFoundError("未找到 FFmpeg，请在全局设置中检查依赖")
    args = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-protocol_whitelist", "file", "-f", "mpegts", "-i", str(source),
            "-map", "0:v?", "-map", "0:a?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", "-f", "mpegts", str(temporary)]
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    # FFmpeg 的诊断不回传到界面，避免绝对路径与不受控输出；失败给出可操作的提示。
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               cwd=str(source.parent), **kwargs)
    try:
        while True:
            checkpoint()
            try:
                code = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        checkpoint()
        if code != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError("未能修复有效音视频流，请确认录制已结束且文件含有可读取的音视频")
    finally:
        # 只回收本次用例新建的 FFmpeg，不查找或影响任何已有录制进程。
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def repair_recording(src, dst=None, on_progress=None, cancel=None):
    """修复 .flv / .ts，返回 ``ok/out/error/cancelled/format`` 与 FLV 原统计。

    ``dst`` 省略时输出为同目录的 ``原名_修复.原扩展名``，重名返回错误。
    ``cancel`` 接收 threading.Event 或无参数的布尔回调；FLV 每 500 个 tag 检查，
    TS 每 0.1 秒检查。``on_progress`` 接收 0..1；TS 不伪造未知进度，只发开始/结束。
    单次处理上限 30 分钟；失败/取消清理本次临时输出，源文件与既有结果不受影响。
    """
    result = {"ok": False, "out": "", "error": "", "cancelled": False, "format": ""}
    temporary = None
    started = time.monotonic()

    def checkpoint():
        if _is_cancelled(cancel):
            raise _RepairStopped(True)
        if time.monotonic() - started > _MAX_REPAIR_SECONDS:
            raise _RepairStopped(False)

    try:
        source = Path(src).expanduser().resolve(strict=True)
        suffix = source.suffix.lower()
        result["format"] = suffix.lstrip(".")
        if suffix not in (".flv", ".ts") or not source.is_file():
            raise ValueError("请选择 FLV 或 TS 录制文件")
        destination = Path(dst).expanduser().absolute() if dst else source.with_name(source.stem + "_修复" + suffix)
        result["out"] = str(destination)
        if source == destination.resolve():
            raise ValueError("输出文件不能覆盖源文件，请选择其他文件名")
        if destination.suffix.lower() != suffix:
            raise ValueError("修复输出须与源文件格式相同")
        if os.path.lexists(destination):
            raise FileExistsError("输出文件已存在，请选择其他文件名")
        if not destination.parent.is_dir():
            raise ValueError("输出目录不存在，请选择有效目录")
        checkpoint()
        _notify(on_progress, 0.0)
        fd, name = tempfile.mkstemp(prefix=".recording-repair-", suffix=suffix, dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        if suffix == ".flv":
            from .flv_repair import repair_flv

            def progress(value):
                checkpoint()
                _notify(on_progress, value)
                checkpoint()

            stats = repair_flv(str(source), str(temporary), on_progress=progress)
            if not stats.get("ok"):
                raise ValueError(stats.get("error") or "没有可修复的 FLV 数据，请检查源文件")
            result.update({key: value for key, value in stats.items() if key not in ("ok", "out", "error")})
        else:
            _repair_ts(source, temporary, checkpoint)
        checkpoint()
        _publish_new(temporary, destination)
        result["ok"] = True
        _notify(on_progress, 1.0)
    except _RepairStopped as stopped:
        result["cancelled"] = stopped.cancelled
        result["error"] = "已取消修复" if stopped.cancelled else "修复超过 30 分钟，请缩短文件后重试"
    except FileExistsError:
        result["error"] = "输出文件已存在，请选择其他文件名"
    except FileNotFoundError:
        result["error"] = "找不到源文件或 FFmpeg，请检查文件与全局设置中的依赖"
    except (TypeError, ValueError) as error:
        result["error"] = str(error)
    except PermissionError:
        result["error"] = "无法读写文件，请检查文件占用与输出目录权限"
    except OSError:
        result["error"] = "修复文件失败，请检查文件占用、输出目录权限与磁盘空间"
    except Exception:
        result["error"] = "修复失败，请检查录制文件后重试"
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return result
