# -*- coding: utf-8 -*-
"""抖音直播视频引擎：FFmpeg 拉流解码预览 / 直播录制 / 时长探测。

- VideoStreamPlayer: ffmpeg 子进程解码为 rawvideo rgb24 帧流（stdout 管道），
  固定画布（scale+pad 16:9）保证帧大小确定；后台读帧线程 + 主线程定时渲染，
  支持直播 / 本地文件、seek、倍速、播放位置推算。
- StreamRecorder: ffmpeg -c copy 直接转存 FLV 直播流（无损、低 CPU）。
- probe_duration: ffprobe 探测本地视频时长（秒）。
"""

import os
import shutil
import subprocess
import threading
import time

from .utils import find_ffmpeg_exe

_ffmpeg_path = [""]


def _has_rawvideo(exe):
    """探测 ffmpeg 是否带 rawvideo 封装（预览/回放解码需要；精简版无）。"""
    try:
        r = subprocess.run([exe, "-hide_banner", "-muxers"],
                           capture_output=True, timeout=8)
        return b"rawvideo" in r.stdout
    except Exception:
        return False


def find_ffmpeg():
    """定位 ffmpeg：缓存 → 候选中优先带 rawvideo 的完整版（PATH 可能命中精简版）。

    候选首位是统一定位器（系统 PATH 优先 → 全局设置自定义位置），
    其后是本机常见完整版安装位置兜底。
    """
    if _ffmpeg_path[0]:
        return _ffmpeg_path[0]
    candidates = [find_ffmpeg_exe(),
                  "D:/Programs/ffmpeg/bin/ffmpeg.exe",
                  "C:/ffmpeg/bin/ffmpeg.exe"]
    fallback = None
    for exe in candidates:
        if not exe or not os.path.exists(exe):
            continue
        if _has_rawvideo(exe):
            _ffmpeg_path[0] = exe
            return exe
        if fallback is None:
            fallback = exe
    if fallback:
        _ffmpeg_path[0] = fallback
    return fallback


def find_ffprobe():
    """定位 ffprobe（与 ffmpeg 同目录优先）。"""
    exe = find_ffmpeg()
    if exe:
        probe = os.path.join(os.path.dirname(exe), "ffprobe.exe")
        if os.path.exists(probe):
            return probe
    return shutil.which("ffprobe")


def probe_duration(path):
    """探测本地视频时长（秒），失败返回 None。"""
    exe = find_ffprobe()
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, timeout=15)
        if r.returncode == 0:
            return float(r.stdout.decode(errors="ignore").strip())
    except Exception:
        pass
    return None


class VideoStreamPlayer:
    """FFmpeg 解码播放器：直播流 URL 或本地视频文件 → on_frame(w, h, rgb) 主线程回调。"""

    def __init__(self, on_frame=None, on_status=None, width=640, fps=15):
        self.on_frame = on_frame
        self.on_status = on_status
        self.width = width
        self.height = width * 9 // 16
        self.fps = fps
        self._proc = None
        self._reader = None
        self._latest = None          # (w, h, bytes) 最新帧，读帧线程覆盖写
        self._frame_count = 0        # 已渲染帧数（文件模式推算播放位置）
        self._start_sec = 0.0        # 本次启动的视频起始位置（seek 后非 0）
        self._speed = 1.0
        self._source = None
        self._is_live = True
        self._closed = False

    # ---------- 生命周期 ----------
    def start(self, source, is_live=True, start_sec=0.0, speed=1.0):
        """开始播放。source: 直播 URL 或本地文件路径；返回是否启动成功。"""
        self.stop()
        exe = find_ffmpeg()
        if not exe:
            self._emit_status("error", "未找到 FFmpeg，无法播放视频")
            return False
        self._source = source
        self._is_live = is_live
        self._start_sec = max(0.0, start_sec)
        self._speed = max(0.1, speed)
        self._frame_count = 0
        self._closed = False
        self._emit_status("connecting", "正在获取直播流..." if is_live else "正在解码视频...")

        filters = []
        if self._speed != 1.0:
            filters.append(f"setpts=PTS/{self._speed}")
        filters.append(f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2")
        filters.append(f"fps={self.fps}")
        args = [exe, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if is_live:
            args += ["-fflags", "nobuffer", "-flags", "low_delay",
                     "-probesize", "64k", "-analyzeduration", "0"]
        else:
            args += ["-ss", f"{self._start_sec:.3f}"]
        args += ["-i", source, "-vf", ",".join(filters),
                 "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError as e:
            self._emit_status("error", f"FFmpeg 启动失败: {e}")
            return False
        self._reader = threading.Thread(target=self._read_frames, daemon=True)
        self._reader.start()
        self._poll_status()
        return True

    def stop(self):
        self._closed = True
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            # 等进程退出，确保 Windows 下视频文件句柄释放（rmtree 临时目录依赖）
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._latest = None

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    # ---------- 控制 ----------
    def seek(self, sec):
        """跳转到指定秒（文件模式：以 -ss 重启进程）。"""
        if self._source and not self._is_live:
            self.start(self._source, is_live=False,
                       start_sec=max(0.0, sec), speed=self._speed)

    def set_speed(self, speed):
        if abs(speed - self._speed) < 0.01:
            return
        speed = max(0.1, speed)
        if self._source and not self._is_live:
            pos = self.video_pos_sec() or 0.0
            self.start(self._source, is_live=False, start_sec=pos, speed=speed)
        else:
            self._speed = speed

    # ---------- 位置 ----------
    def video_pos_sec(self):
        """当前视频位置（秒）：起始偏移 + 已渲染帧数×(速度/输出fps)。直播返回 None。"""
        if self._is_live:
            return None
        return self._start_sec + self._frame_count * self._speed / self.fps

    # ---------- 内部 ----------
    def _read_frames(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        size = self.width * self.height * 3
        try:
            while not self._closed:
                buf = proc.stdout.read(size)
                if not buf or len(buf) < size:
                    break
                self._latest = (self.width, self.height, buf)
        except Exception:
            pass
        finally:
            if not self._closed:
                self._emit_status("stopped",
                                  "视频播放结束" if not self._is_live else "直播流中断")

    def _poll_status(self):
        """主线程定时渲染：拉最新帧 + 存活检查（递归 singleShot 驱动）。"""
        from PySide6.QtCore import QTimer
        frame = self._latest
        if frame is not None and self.on_frame:
            w, h, data = frame
            self._frame_count += 1
            self.on_frame(w, h, data)
        if self._closed or self._proc is None:
            return
        if self._proc.poll() is not None and self._latest is None:
            self._emit_status("error", "直播流获取失败（可能未开播或已下播）")
            self.stop()
            return
        QTimer.singleShot(int(1000 / self.fps), self._poll_status)

    def _emit_status(self, state, detail):
        if self.on_status:
            try:
                self.on_status(state, detail)
            except Exception:
                pass


class StreamRecorder:
    """FLV 直播流录制：ffmpeg -c copy 无损转存。stop 优雅收尾（写 'q'）。"""

    def __init__(self, on_status=None):
        self.on_status = on_status
        self._proc = None
        self.start_ts = 0
        self.path = None

    def start(self, url, out_path):
        exe = find_ffmpeg()
        if not exe:
            return False
        self.stop()
        self.path = out_path
        self.start_ts = int(time.time() * 1000)
        args = [exe, "-hide_banner", "-loglevel", "error",
                "-fflags", "nobuffer", "-i", url,
                "-c", "copy", "-f", "flv", "-y", out_path]
        try:
            self._proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError:
            return False
        if self.on_status:
            self.on_status("recording", out_path)
        return True

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        if self.on_status:
            self.on_status("stopped", self.path)

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None
