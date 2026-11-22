# -*- coding: utf-8 -*-
"""直播录制内核（平台无关）：抖音 / B站 同源。

设计依据（两处参考项目，不是凭感觉写的）：

1. 江湖录播（D:/PythonApps/Python版全量）
   - 两种录制方式：「录制流」= ffmpeg -c copy；「下载流」= 直接 GET FLV 并按
     tag 边界落盘（dy/flv_downloader.py）。
   - 下载流的核心价值：CDN 断流后带 Range 续传，只写完整 FLV tag，任何时刻
     文件都能解析；服务端忽略 Range 时绝不 append（换新文件继续录）。
   - ffmpeg 那路必须先发 'q' 优雅收尾，直接 terminate 会丢 mp4 moov。
   - 分段 / 录满 N 分钟停止 / 清晰度按优先级取流（dy/recorder.py、dy/monitor.py）。

2. 录播姬 BililiveRecorder 2.20.0
   - 自动分段模式：Disabled / ByTime / BySize（CuttingMode.cs）；
   - 断流重连间隔 TimingStreamRetry（默认 6s）、无匹配画质重连更久；
   - 弹幕录制、封面保存、按主播名分目录的文件名模板
     （{{roomId}}-{{name}}/录制-{{roomId}}-{{now}}-{{title}}.flv）。

本模块只做「取流 → 录制 → 分段 → 重连 → 状态」，不碰任何 Qt 控件，
便于离屏测试；界面在 gui_qt/live_record_window.py。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request

# ==================== 常量 ====================

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_TAG_HDR = 11            # FLV tag 头（11 字节）
_PREV_SIZE = 4           # PreviousTagSize
_FLV_HEADER = 13         # 9 字节文件头 + 4 字节 PreviousTagSize0
_READ_CHUNK = 64 * 1024
_RECONNECT_MIN = 0.8
_RECONNECT_MAX = 10.0
_MAX_RECONNECT = 60
_MB = 1024 * 1024
_MAX_TAG = 16 * _MB      # 单 tag 上限，超过视为失步
_SEAM_GAP_MS = 33        # 接缝处补的帧间隔（约 1 帧）
_SEAM_TOL_MS = 100       # 时间戳回退判定容差

# 抖音清晰度优先级（值越大越优先）
DOUYIN_QUALITY_ORDER = {"_origin": 100, "_md": 95, "FULL_HD1": 90, "UHD": 85,
                        "HD1": 80, "SD1": 70, "SD2": 60, "LD": 50}
DOUYIN_QUALITY_LABEL = {"_origin": "原画", "_md": "蓝光", "FULL_HD1": "1080P",
                        "HD1": "720P", "SD1": "540P", "SD2": "360P", "LD": "360P"}

PLATFORMS = {
    "douyin": {"name": "抖音直播", "referer": "https://live.douyin.com/"},
    "bili": {"name": "B站直播", "referer": "https://live.bilibili.com/"},
    "kuaishou": {"name": "快手直播", "referer": "https://live.kuaishou.com/"},
    "weibo": {"name": "微博直播", "referer": "https://weibo.com/"},
    "xiaohongshu": {"name": "小红书直播", "referer": "https://www.xiaohongshu.com/"},
    "netease": {"name": "网易云直播", "referer": "https://look.163.com/"},
}

# 弹幕接入：B站/抖音走原生接口，快手/微博/小红书/网易云走页面消息通道
DANMAKU_PLATFORMS = tuple(PLATFORMS)

# 输出容器：抖音与新增平台默认 TS、B站默认 FLV（用户可在「录制设置 → 输出格式」里改）
OUTPUT_FORMAT_AUTO = "自动（抖音 TS · B站 FLV）"
OUTPUT_FORMATS = (OUTPUT_FORMAT_AUTO, "TS（MPEG-TS）", "FLV")


def resolve_format(platform, settings=None):
    """返回该平台这次录制的容器：'ts' / 'flv'。

    为什么分平台默认：抖音 CDN 断流频繁、时间戳用的是服务器绝对时钟，FLV
    一旦拼接就会花屏，TS 天然能容忍（每个 PES 自带 PTS，可任意拼接）；B站
    FLV 直连稳定、时间戳从 0 起，写 FLV 可以直接落盘、断点续录代价最小。
    快手/微博/小红书/网易云以 HLS 为主，只能走 ffmpeg 转封装到 TS。
    """
    v = str((settings or {}).get("output_format") or OUTPUT_FORMAT_AUTO)
    if v.startswith("TS"):
        return "ts"
    if v.startswith("FLV"):
        return "flv"
    return "flv" if platform == "bili" else "ts"


def supports_danmaku(platform):
    return platform in DANMAKU_PLATFORMS


def supports_flv_download(platform):
    """下载流（FLV tag 直写）只适用于原生 FLV 平台；HLS 平台必须走 ffmpeg。"""
    return platform in ("douyin", "bili")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")


def safe_name(s):
    """文件名安全化（主播名可能带 : / * 等非法字符）。"""
    from toolbox.utils import sanitize_filename
    return sanitize_filename(str(s or ""), 80, "未知主播", collapse_ws=False)


def record_dir(base_dir, anchor_name, when=None, date_folder=True):
    """主播录制目录：`<保存目录>/<主播名>/<YYYYMMDD>`。

    日期层与「自动整理」一致（8 位 YYYYMMDD），录制文件、弹幕、封面、
    时间轴都写在同一日期文件夹里；date_folder=False 时退回主播目录。
    when 传时间戳用于测试/补偿历史日期，默认当前时间。
    """
    anchor_dir = os.path.join(str(base_dir or ""), safe_name(anchor_name))
    if not date_folder:
        return anchor_dir
    moment = time.localtime(when) if when else time.localtime()
    return os.path.join(anchor_dir, time.strftime("%Y%m%d", moment))


def fmt_duration(sec):
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        sec = 0
    sec = max(0, sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ==================== FLV 写入（tag 对齐） ====================

class FlvDownloadError(Exception):
    """不可恢复错误（响应不是 FLV / HTTP 拒绝）。"""


class FlvWriter:
    """按 FLV tag 边界写盘：任何时刻文件都以完整 tag 收尾。

    直播流单个 tag 几 KB，网络分片可能把 tag 切开；只有凑齐
    「11 字节头 + DataSize + 4 字节 PreviousTagSize」才落盘。

    seam_fix=True（「下载流」录制默认开启）时额外做三件事，专治花屏：
      1. tag 校验 + 失步重同步：类型必须是 8/9/18、长度合理、PreviousTagSize
         自洽；不合格就向后扫描重新对齐，绝不把错位字节写进文件；
      2. 接缝对齐：断流重连后丢弃到下一个关键帧为止的视频帧。解码器不会
         再拿到「没有参考帧的 P 帧」，接缝处因此不花屏；
      3. 时间戳续写：把新一段的时间戳整体平移到上一段末尾之后，文件内
         时间戳单调递增（播放器不会因回退而解错帧）。
    """

    def __init__(self, path, resume=False, seam_fix=False):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "ab" if resume else "wb")
        self.path = path
        self.off = os.path.getsize(path) if resume else 0
        self._f.seek(self.off)
        self._buf = bytearray()
        self._state = 0 if self.off == 0 else 1
        self.tags = 0
        self.seam_fix = seam_fix
        self.dropped = 0          # 失步丢弃的字节段 / 帧数
        self.ts_fixed = 0         # 接缝处的时间戳平移次数
        self._shift = 0           # 当前时间戳平移量（ms）
        self._last = {}           # tag 类型 -> 已写入的最后一个时间戳
        self._seam = False        # 处于「等关键帧」状态
        self._f.flush()

    # ---------- 校验 / 重同步 ----------
    @staticmethod
    def _head_ok(buf, pos, n):
        if n - pos < _TAG_HDR:
            return False
        if buf[pos] not in (8, 9, 18):
            return False
        size = int.from_bytes(buf[pos + 1:pos + 4], "big")
        return 0 < size <= _MAX_TAG

    def _resync(self, buf, pos, n, limit=256 * 1024):
        """从 pos+1 起找下一个自洽的 tag，返回 buf 内新偏移（-1=数据不够）。"""
        end = min(n - _TAG_HDR, pos + limit)
        i = pos + 1
        while i <= end:
            if self._head_ok(buf, i, n):
                size = int.from_bytes(buf[i + 1:i + 4], "big")
                blk = _TAG_HDR + size + _PREV_SIZE
                if n - i < blk:
                    return -1            # tag 没到齐，等更多数据再判
                if int.from_bytes(buf[i + _TAG_HDR + size:i + blk], "big") == size + _TAG_HDR:
                    return i
            i += 1
        return -1

    # ---------- 接缝 ----------
    def begin_seam(self):
        """告知「这是一次重连后的新连接」：丢到下一个关键帧为止。

        必须同时清掉缓冲区里上一连接残留的「半个 tag」——它和新的流首尾不相接，
        留着会让写入器一直等这个永远补不齐的 tag，直到 16MB 兜底才恢复，
        表现就是断流之后整段没录上。
        """
        if self.seam_fix:
            self._seam = True
            del self._buf[:]

    @staticmethod
    def _video_key(head):
        """判断视频 tag 是不是真正可解码的关键帧：True/False，判断不了返回 None。

        AVC/HEVC 的 sequence header（packet type 0）虽然 frame_type 也是 1，
        但它只是解码器配置、不是画面，不能当作接缝闭合点——否则接缝会停在
        配置帧上，后面紧跟的 P 帧照样花屏。
        """
        if not head:
            return None
        flag = head[0]
        if flag & 0x80:                       # enhanced FLV（HEVC / AV1）
            if len(head) > 1 and (flag & 0x0F) == 0:
                return None                   # sequence start
            return (flag >> 4) & 0x07 == 1
        if (flag & 0x0F) != 7:                # 非 AVC：不做关键帧过滤
            return None
        if len(head) > 1 and head[1] == 0:
            return None                       # AVC sequence header
        return (flag >> 4) == 1

    def _fix_ts(self, ttype, raw, head):
        """返回 (写入用时间戳, 是否写入)。"""
        if ttype not in (8, 9):
            return raw + self._shift, True
        ts = raw + self._shift
        last = self._last.get(ttype)
        if last is not None and ts < last - _SEAM_TOL_MS:
            # 时间戳重新起算（新一轮连接）→ 平移到上一段末尾之后
            self._shift += (last + _SEAM_GAP_MS) - ts
            ts = raw + self._shift
            self._seam = True
            self.ts_fixed += 1
        if self._seam:
            if ttype == 8:
                return ts, False              # 关键帧没到之前不收音频
            key = self._video_key(head)
            if key is False:
                return ts, False
            if key is True:
                self._seam = False            # 关键帧到了，接缝闭合
        self._last[ttype] = ts
        return ts, True

    def _flush_tags(self):
        buf = self._buf
        n = len(buf)
        pos = written = 0
        while True:
            if self._state == 0:
                if n - pos < _FLV_HEADER:
                    break
                if bytes(buf[pos:pos + 3]) != b"FLV":
                    raise FlvDownloadError(
                        "响应不是 FLV 流（开头 %r）" % bytes(buf[pos:pos + 16]))
                self._f.write(bytes(buf[pos:pos + _FLV_HEADER]))
                written += _FLV_HEADER
                pos += _FLV_HEADER
                self.off += _FLV_HEADER
                self._state = 1
                continue
            if n - pos < _TAG_HDR:
                break
            ttype = buf[pos]
            size = int.from_bytes(buf[pos + 1:pos + 4], "big")
            block = _TAG_HDR + size + _PREV_SIZE
            ok = (ttype in (8, 9, 18) and 0 < size <= _MAX_TAG)
            if not ok:
                if not self.seam_fix:
                    # 老行为：按长度硬切（保留向后兼容）
                    if size <= 0 or n - pos < block:
                        break
                else:
                    nxt = self._resync(buf, pos, n)
                    if nxt < 0:
                        break
                    self.dropped += 1
                    pos = nxt
                    continue
            if n - pos < block:
                break
            if self.seam_fix and int.from_bytes(
                    buf[pos + _TAG_HDR + size:pos + block], "big") != size + _TAG_HDR:
                nxt = self._resync(buf, pos, n)   # PreviousTagSize 不自洽 → 失步
                if nxt < 0:
                    break
                self.dropped += 1
                pos = nxt
                continue
            raw_ts = ((buf[pos + 4] << 16) | (buf[pos + 5] << 8)
                      | buf[pos + 6] | (buf[pos + 7] << 24))
            if self.seam_fix:
                head = bytes(buf[pos + _TAG_HDR:pos + _TAG_HDR + min(size, 8)])
                ts, keep = self._fix_ts(ttype, raw_ts, head)
                if not keep:
                    self.dropped += 1
                    pos += block
                    continue
            else:
                ts = raw_ts
            data = bytearray(buf[pos:pos + block])
            data[4] = (ts >> 16) & 0xFF
            data[5] = (ts >> 8) & 0xFF
            data[6] = ts & 0xFF
            data[7] = (ts >> 24) & 0xFF
            data[-4:] = (size + _TAG_HDR).to_bytes(4, "big")
            self._f.write(data)
            written += block
            pos += block
            self.off += block
            self.tags += 1
        if pos:
            del buf[:pos]
        return written

    def feed(self, data):
        if not data:
            return 0
        self._buf += data
        if len(self._buf) > 16 * _MB:   # 错位兜底：宁可丢一段也不写坏文件
            if self.seam_fix:
                keep = self._resync(self._buf, 0, len(self._buf))
                if keep > 0:
                    del self._buf[:keep]
                else:
                    del self._buf[:]
            else:
                del self._buf[:]
            self._state = 1
        out = self._flush_tags()
        if out:
            self._f.flush()
        return out

    @property
    def buffered(self):
        return len(self._buf)

    @property
    def consumed(self):
        return self.off + len(self._buf)

    def close(self):
        try:
            self._f.flush()
        finally:
            try:
                self._f.close()
            except Exception:
                pass


# ==================== 下载流录制 ====================

class StreamDownloader:
    """直接下载 FLV 流（江湖「下载流」等价实现）。

    比 ffmpeg 强：只写完整 tag、断流后自动缝接（关键帧对齐 + 时间戳续写，
    不花屏、不换文件）、可按时间/大小分段、录满 N 分钟自动停。
    stop() 只置标志，由线程自己关文件。
    """

    def __init__(self, url, path, headers=None, stop_minutes=0,
                 segment_minutes=0, segment_mb=0, base_name="record",
                 outdir=None, label="", refresh_url=None, on_media_start=None):
        self.url = url
        self.path = path
        self.headers = dict(headers or {})
        self.stop_minutes = stop_minutes or 0
        self.segment_minutes = segment_minutes or 0
        self.segment_mb = segment_mb or 0
        self.label = label
        self._base = base_name
        self._outdir = outdir or (os.path.dirname(path) or ".")
        # 重连时刷新直链：B站/抖音的 flv 地址有时效（几分钟），只重试旧地址
        # 会在地址过期后永远失败（录播姬每次重连都重新取流，这里对齐）
        self._refresh_url = refresh_url
        self.on_media_start = on_media_start   # (wall_ms, file_path, pts_ms)
        self._anchor_pending = None
        self._stop = threading.Event()
        self._thread = None
        self._start = 0.0
        self._elapsed_at_end = 0
        self._seg_started = 0.0
        self._seg_index = 1
        self._rotations = 0
        self._last_file = path
        self._written = 0
        self._reconnects = 0
        self._stalled = 0
        self._received = 0
        self.error = ""
        self._w = None

    # ---------- 查询 ----------
    def is_active(self):
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def elapsed(self):
        if not self._start:
            return 0
        return int(time.time() - self._start) if self.is_active() else int(self._elapsed_at_end)

    def output_of(self):
        return self._last_file

    def bytes_written(self):
        return self._written

    def reconnects(self):
        return self._reconnects

    # ---------- 控制 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="flvdl-%s" % (self.label or self._base))
        self._thread.start()
        return True

    def stop(self, timeout=8.0):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        return not (t and t.is_alive())

    # ---------- 文件轮转 ----------
    def _new_writer(self, resume=False):
        if self.segment_minutes or self.segment_mb or self._rotations > 0:
            p = os.path.join(self._outdir, "%s_%03d.flv" % (self._base, self._seg_index))
        else:
            p = self.path
        self._last_file = p
        w = FlvWriter(p, resume=resume, seam_fix=True)
        self._seg_started = time.time()
        self._anchor_pending = p       # 等第一段字节真正写下去再记锚点
        return w

    def _should_segment(self, writer):
        if not writer:
            return False
        if self.segment_minutes and self._seg_started \
                and time.time() - self._seg_started >= self.segment_minutes * 60:
            return True
        if self.segment_mb and writer.off >= self.segment_mb * _MB:
            return True
        return False

    # ---------- 主循环 ----------
    def _run(self):
        backoff = _RECONNECT_MIN
        writer = None
        self._start = time.time()
        self._elapsed_at_end = 0
        try:
            while not self._stop.is_set():
                if self.stop_minutes and time.time() - self._start >= self.stop_minutes * 60:
                    break
                if self._should_segment(writer):
                    writer.close()
                    writer = None
                    self._seg_index += 1
                    continue
                if writer is None:
                    writer = self._new_writer(resume=False)
                    self._w = writer
                    continue

                # 断线重连前刷新直链（地址过期是 B站/抖音断流的常见原因）
                if self._reconnects and self._refresh_url:
                    try:
                        new_url = self._refresh_url()
                        if new_url:
                            self.url = new_url
                    except Exception:
                        pass
                # 重连后的新连接从「onMetaData + seq header + 关键帧」重新开始，
                # 标记接缝：写入器丢弃到关键帧为止的帧，接缝处才不会花屏
                if self._reconnects:
                    try:
                        writer.begin_seam()
                    except Exception:
                        pass

                got_before = self._received
                try:
                    verdict = self._drain(writer)
                except FlvDownloadError as e:
                    self.error = str(e)
                    break
                except Exception as e:
                    if self._stop.is_set():
                        break
                    self.error = str(e)
                    verdict = "error"

                # 连续零进度 → 判定流已结束（避免永远重连）
                if self._received <= got_before:
                    self._stalled += 1
                else:
                    self._stalled = 0
                if self._stalled >= 3:
                    self.error = "连续 %d 次重连都没有新数据，判定流已结束" % self._stalled
                    break

                backoff = _RECONNECT_MIN
                if verdict == "stopped":
                    break
                if self._stop.is_set():
                    break
                self._reconnects += 1
                if self._reconnects >= _MAX_RECONNECT:
                    self.error = "连续重连 %d 次仍失败：%s" % (_MAX_RECONNECT, self.error)
                    break
                time.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)
        finally:
            if writer:
                writer.close()
            self._w = None
            self._elapsed_at_end = int(time.time() - self._start) if self._start else 0

    def _drain(self, writer):
        """开一次连接读到断流为止，返回 eof / stopped / segment。

        直播流是「滑动窗口」，不是静态文件：同一字节偏移在两次连接里对应的
        是完全不同的时间点，因此这里不发 Range 续传——每次重连都从流头重新
        拉，由 FlvWriter 的接缝逻辑（关键帧对齐 + 时间戳平移）把新老两段
        缝到一起。这样断流只会丢掉一两帧，不会产生花屏，也不会再换新文件。
        """
        req = urllib.request.Request(self.url)
        for k, v in self.headers.items():
            if v:
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=90) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if code not in (200, 206):
                raise FlvDownloadError("HTTP %s" % code)
            while not self._stop.is_set():
                if self._should_segment(writer):
                    return "segment"
                if self.stop_minutes and time.time() - self._start >= self.stop_minutes * 60:
                    return "eof"
                chunk = resp.read(_READ_CHUNK)
                if not chunk:
                    return "eof"
                self._received += len(chunk)
                written = writer.feed(chunk)
                self._written += written
                if written and self._anchor_pending:
                    pending, self._anchor_pending = self._anchor_pending, None
                    if self.on_media_start:
                        try:
                            self.on_media_start(int(time.time() * 1000), pending, 0)
                        except Exception:
                            pass
            return "stopped"


# ==================== 录制流（ffmpeg） ====================

def find_ffmpeg():
    """复用工具箱的 ffmpeg 查找（自定义 → 内置 bin → PATH）。"""
    try:
        from toolbox.live_video import find_ffmpeg as _f
        return _f()
    except Exception:
        import shutil
        return shutil.which("ffmpeg") or ""


class FfmpegRecorder:
    """ffmpeg -c copy 录制（江湖「录制流」等价实现）。

    stderr 必须落文件而不是 PIPE：长跑时 PIPE 写满 64KB 会让 ffmpeg 卡死，
    表现为「显示录制中但文件不再增长」（参考项目踩过的坑）。

    fmt="ts"（抖音默认）走 MPEG-TS：TS 每个 PES 自带 PTS，播放器/Premiere
    遇到时间戳跳变会自行重新同步，不像 FLV 那样整段花屏；而且 TS 可以任意
    拼接，断流后 ffmpeg 进程退出重连时用 -output_ts_offset 接着时间戳追加，
    一个文件录到底，不产生碎文件。
    """

    def __init__(self, url, path, headers=None, stop_minutes=0,
                 segment_minutes=0, segment_mb=0, audio_only=False, log_dir=None,
                 fmt="flv", ts_offset_ms=0, on_media_start=None):
        self.url = url
        self.path = path
        self.headers = dict(headers or {})
        self.stop_minutes = stop_minutes or 0
        self.segment_minutes = segment_minutes or 0
        self.segment_mb = segment_mb or 0
        self.audio_only = audio_only
        self.log_dir = log_dir or os.path.dirname(path)
        self.fmt = "ts" if (audio_only or fmt == "ts") else "flv"
        self.ts_offset_ms = int(ts_offset_ms or 0)
        self.on_media_start = on_media_start   # (wall_ms, file_path, pts_ms)
        self._media_started = False
        self._proc = None
        self._errf = None
        self._out = None
        self._pump_t = None
        self._written = 0
        self.start_ts = 0
        self.error = ""
        self._elapsed_at_end = 0

    # ---------- 命令组装 ----------
    def _build_args(self, exe):
        args = [exe, "-hide_banner", "-loglevel", "warning",
                "-rw_timeout", "15000000"]
        hdr = "".join("%s: %s\r\n" % (k, v) for k, v in self.headers.items() if v)
        if hdr:
            args += ["-headers", hdr]
        if self.url.startswith("http"):
            # HTTP 输入断线自动重连（ffmpeg 原生能力，避免一断就退出）。
            # 进程内续流不产生接缝，比反复重启进程干净得多。
            args += ["-reconnect", "1", "-reconnect_streamed", "1",
                     "-reconnect_delay_max", "5"]
        args += ["-i", self.url]
        if self.audio_only:
            args += ["-vn", "-c:a", "copy"]
        else:
            args += ["-c", "copy"]
        if self.stop_minutes:
            args += ["-t", str(int(self.stop_minutes * 60))]
        if self.segment_minutes:
            root, ext = os.path.splitext(self.path)
            args += ["-f", "segment", "-segment_time", str(int(self.segment_minutes * 60)),
                     "-reset_timestamps", "1",
                     "-segment_format", "mpegts" if self.fmt == "ts" else "flv",
                     root + "_%03d" + (ext or ".ts")]
            return args, False
        if self.fmt == "ts":
            if self.ts_offset_ms > 0:
                # 接续上一次录制的时间戳，追加到同一 .ts 上不会出现回退跳变
                args += ["-output_ts_offset", "%.3f" % (self.ts_offset_ms / 1000.0)]
            args += ["-f", "mpegts", "pipe:1"]
            return args, True
        args += ["-f", "flv", self.path]
        return args, False

    def start(self):
        exe = find_ffmpeg()
        if not exe:
            self.error = "未找到 ffmpeg（请把 ffmpeg 放到 bin/ 或加入 PATH）"
            return False
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        args, use_pipe = self._build_args(exe)
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            self._errf = open(os.path.join(
                self.log_dir, os.path.basename(self.path) + ".ffmpeg.log"), "wb")
            if use_pipe:
                # 管道 + 追加写：断流重连后接着原文件写，不截断已录内容
                self._out = open(self.path, "ab")
                self._proc = subprocess.Popen(
                    args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self._errf, bufsize=0,
                    creationflags=CREATE_NO_WINDOW)
                self._pump_t = threading.Thread(target=self._pump, daemon=True,
                                                name="ffmpeg-pump")
                self._pump_t.start()
            else:
                self._proc = subprocess.Popen(
                    args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=self._errf, creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self.error = str(e)
            self._close_all()
            return False
        self.start_ts = int(time.time() * 1000)
        if not use_pipe and self.on_media_start:
            threading.Thread(target=self._watch_outputs, daemon=True,
                             name="ffmpeg-first-frame").start()
        return True

    def _notify_media(self, file_path, pts_ms=0):
        cb = self.on_media_start
        if not cb:
            return
        try:
            cb(int(time.time() * 1000), str(file_path), int(pts_ms or 0))
        except Exception:
            pass

    def _watch_outputs(self):
        """直接落盘/分段模式：轮询输出文件，出现即记一条起录锚点（误差 ≤1 秒）。"""
        root, ext = os.path.splitext(self.path)
        ext = ext or ".ts"
        seen = set()
        while True:
            files = []
            if self.segment_minutes:
                folder = os.path.dirname(self.path) or "."
                prefix = os.path.basename(root) + "_"
                try:
                    files = [os.path.join(folder, n) for n in os.listdir(folder)
                             if n.startswith(prefix) and n.endswith(ext)]
                except OSError:
                    files = []
            else:
                files = [self.path]
            for f in files:
                if f in seen:
                    continue
                try:
                    if os.path.getsize(f) <= 0:
                        continue
                except OSError:
                    continue
                seen.add(f)
                self._notify_media(f, 0 if self.segment_minutes else self.ts_offset_ms)
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            time.sleep(1.0)

    def _pump(self):
        """ffmpeg stdout（TS 字节流）→ 文件。必须在独立线程里读，否则管道堵死。"""
        p = self._proc
        if p is None or p.stdout is None:
            return
        try:
            while True:
                chunk = p.stdout.read(64 * 1024)
                if not chunk:
                    break
                if self._out is not None:
                    try:
                        self._out.write(chunk)
                        self._written += len(chunk)
                    except Exception:
                        break
                    if not self._media_started:
                        self._media_started = True
                        self._notify_media(self.path, self.ts_offset_ms)
        finally:
            try:
                if self._out is not None:
                    self._out.flush()
            except Exception:
                pass

    def is_active(self):
        return self._proc is not None and self._proc.poll() is None

    def elapsed(self):
        if not self.start_ts:
            return 0
        if self.is_active():
            return int(time.time() - self.start_ts / 1000)
        return int(self._elapsed_at_end)

    def output_of(self):
        return self.path

    def bytes_written(self):
        return self._written

    def stop(self, timeout=20):
        """发 'q' 让 ffmpeg 写完文件尾；超时才 terminate（会丢尾）。"""
        p, self._proc = self._proc, None
        if p is None:
            return True
        self._elapsed_at_end = int(time.time() - self.start_ts / 1000) if self.start_ts else 0
        try:
            if p.stdin:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        t = self._pump_t
        if t is not None and t.is_alive():
            t.join(timeout=5)
        self._close_all()
        return True

    def _close_all(self):
        for attr in ("_out", "_errf"):
            f = getattr(self, attr, None)
            setattr(self, attr, None)
            if f:
                try:
                    f.close()
                except Exception:
                    pass


# ==================== 主播（录制任务实体） ====================

class Anchor:
    """一个被监控/录制的主播（平台无关）。"""

    FIELDS = ("platform", "room", "name", "title", "online", "cover",
              "live", "last_check", "monitoring", "recording", "rec_state",
              "elapsed", "quality", "quality_label", "segment",
              "segment_minutes", "segment_mb", "stop_minutes", "content",
              "remark", "notify_live", "notify_offline", "notify_rec_start",
              "notify_rec_stop", "notify_online", "selected", "error",
              # 账号配置里的「用于直播录制」：记住该主播属于哪个统一账号，
              # 取消勾选时按它精确移除；老数据没有这两个字段也能正常加载。
              "account_id", "url")

    def __init__(self, **kw):
        self.platform = kw.get("platform") or "douyin"
        self.room = str(kw.get("room") or "")
        self.name = kw.get("name") or ""
        self.title = kw.get("title") or ""
        self.online = kw.get("online") or 0
        self.cover = kw.get("cover") or ""
        self.live = bool(kw.get("live"))
        self.last_check = kw.get("last_check") or ""
        self.monitoring = bool(kw.get("monitoring"))
        self.recording = bool(kw.get("recording"))
        self.rec_state = kw.get("rec_state") or "未录制"
        self.elapsed = int(kw.get("elapsed") or 0)
        self.quality = kw.get("quality") or ""       # 取流用的档位 key（B站是 qn）
        self.quality_label = kw.get("quality_label") or ""  # 界面显示用（原画/1080P…）
        self.segment = bool(kw.get("segment"))
        self.segment_minutes = int(kw.get("segment_minutes") or 0)
        self.segment_mb = int(kw.get("segment_mb") or 0)
        self.stop_minutes = int(kw.get("stop_minutes") or 0)
        self.content = int(kw.get("content") or 0)   # 0=视频（有视频有声）1=音频
        self.remark = kw.get("remark") or ""
        # 逐主播提醒开关（WebHook 推送按事件 + 该主播开关过滤）
        self.notify_live = bool(kw.get("notify_live", True))
        self.notify_offline = bool(kw.get("notify_offline", True))
        self.notify_rec_start = bool(kw.get("notify_rec_start", True))
        self.notify_rec_stop = bool(kw.get("notify_rec_stop", True))
        self.notify_online = bool(kw.get("notify_online", False))
        self.selected = bool(kw.get("selected"))
        self.error = kw.get("error") or ""
        self.account_id = kw.get("account_id") or ""
        self.url = kw.get("url") or ""
        # 待开播主播（room 暂时为空，靠账号主页解析补号）的重解析时间点
        self._resolve_at = float(kw.get("_resolve_at") or 0)

    @property
    def key(self):
        # 待开播主播还没有房间号，用账号标识兜底，避免多条空房间号互相覆盖
        ident = self.room or ("待开播:" + (self.account_id or self.url))
        return "%s:%s" % (self.platform, ident)

    @property
    def live_url(self):
        if not self.room:
            # 待开播主播还没有房间号：先给出账号主页，别拼出假的直播间地址
            return self.url or ""
        if self.platform == "bili":
            return "https://live.bilibili.com/%s" % self.room
        if self.platform == "douyin":
            return "https://live.douyin.com/%s" % self.room
        from toolbox import live_stream
        return live_stream.live_url(self.platform, self.room)

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.FIELDS})


# ==================== 平台取流 ====================

# 直播录制平台 → 统一账户管理层使用的平台键
_LOGIN_KEYS = {"bili": "bilibili"}


def login_key(platform: str) -> str:
    """直播录制平台对应的统一账户管理平台键。"""
    return _LOGIN_KEYS.get(platform, platform)


def login_cookie(platform: str) -> str:
    """从统一账户管理层读取当前登录态（受总开关/有效期控制）。

    只用于当次平台请求，读完即丢；未登录或已关闭时返回空串，按游客态处理。
    """
    try:
        from auth import get_cookie
        return get_cookie(login_key(platform)) or ""
    except Exception:
        return ""


def has_login(platform: str) -> bool:
    """平台当前是否可用登录态（只返回布尔，不输出凭据内容）。"""
    return bool(login_cookie(platform))


def _pick_douyin_stream(streams, quality=""):
    """按清晰度优先级取抖音流地址（原画 > 蓝光 > 1080P > ...）。"""
    if not streams:
        return "", ""
    if quality and quality in streams:
        return quality, streams[quality]
    for key in sorted(streams, key=lambda k: -DOUYIN_QUALITY_ORDER.get(k, 0)):
        if streams[key]:
            return key, streams[key]
    return "", ""


def fetch_douyin(room, quality=""):
    """抖音房间信息 + 流地址。

    login=True 表示由 douyin_live 从统一账户管理层合并当前登录态
    （用于建连取原画流）；未登录时自动退回游客态。
    """
    from toolbox import douyin_live
    info = douyin_live.fetch_room_info(str(room), login=True)
    streams = info.get("streams") or {}
    qk, url = _pick_douyin_stream(streams, quality)
    status = info.get("status")
    live = int(status or 0) == 2
    # 未开播不是错误：只有「开着播却取不到流」才算错误（避免表格里红字误报）
    err = info.get("error") or ""
    if status is None:
        err = err or "未读取到直播状态，请稍后重试"
        digits = re.sub(r"\D", "", str(room))
        if re.fullmatch(r"\d{15,16}", digits):
            # 抖音分享/主页里给的主播 ID、用户 ID 长得像房间号，但读不到直播
            err = ("这是抖音的用户/主播 ID，不是直播间号。"
                   "请填 11~13 位直播间号（live.douyin.com/ 后面那段）或 19 位房间 ID")
        elif "无法" in err and "查看" in err:
            err += "（该号可能不是直播间号，请粘贴直播间链接或用 19 位房间 ID）"
    elif not live:
        err = ""
    elif not url:
        err = err or "未找到可用直播流"
    return {
        "live": live,
        "status": status,
        "title": info.get("title") or "",
        "name": info.get("anchor") or "",
        "online": info.get("online") or "",
        "cover": info.get("cover") or info.get("avatar") or "",
        "stream": url,
        "quality": DOUYIN_QUALITY_LABEL.get(qk, qk),
        "quality_key": qk,
        "qualities": {DOUYIN_QUALITY_LABEL.get(k, k): k for k in
                      sorted(streams, key=lambda x: -DOUYIN_QUALITY_ORDER.get(x, 0))},
        "error": err,
        # 登录态失效后已自动降级游客态：note 给界面/日志提示，不当成错误
        "note": info.get("login_note") or "",
    }


def fetch_bili(room, qn=10000):
    """B站房间信息 + 流地址。

    登录态从统一账户管理层只读取得（用于高画质取流），未登录时按游客态。
    """
    from toolbox import bili_live
    cookie = login_cookie("bili")
    info = bili_live.fetch_room_info(str(room), cookie=cookie)
    play = bili_live.fetch_play_url(str(room), qn, cookie=cookie)
    status = info.get("live_status")
    if status is None:
        status = play.get("live_status")
    live = int(status or 0) == 1
    url = play.get("flv") or ""
    err = info.get("error") or ""
    if status is None:
        err = err or play.get("error") or "未读取到直播状态，请稍后重试"
    elif not live:
        err = ""      # 未开播/轮播不算错误
    elif not url:
        err = play.get("error") or "未找到可用直播流"
    return {
        "live": live,
        "status": status,
        "title": info.get("title") or play.get("title") or "",
        "name": info.get("uname") or "",
        "online": info.get("watch_num") or 0,
        "cover": info.get("cover") or "",
        "stream": url,
        "quality": play.get("quality_desc") or "",
        "quality_key": "",
        "qualities": {desc or str(q): q for q, desc in (play.get("qn_list") or [])},
        "is_hls": bool(play.get("is_hls")),
        "error": err,
    }


def fetch_info(platform, room, quality=""):
    try:
        if platform == "bili":
            qn = 10000
            if quality and str(quality).isdigit():
                qn = int(quality)
            return fetch_bili(room, qn)
        if platform == "douyin":
            return fetch_douyin(room, quality)
        from toolbox import live_stream
        return live_stream.fetch(platform, room, quality)
    except Exception as e:  # noqa: BLE001
        return {"live": False, "error": str(e), "stream": "", "title": "",
                "name": "", "online": "", "cover": "", "qualities": {}}


def fetch_status(platform, room):
    """直播中仅检测下播，不反复获取流地址/画质。查询失败不视为下播。"""
    try:
        if platform == "bili":
            from toolbox.bili_live import fetch_room_info
            info = fetch_room_info(room)
            field, live_value, online_field = "live_status", 1, "watch_num"
        elif platform == "douyin":
            from toolbox.douyin_live import fetch_room_info
            info = fetch_room_info(room, login=False)
            field, live_value, online_field = "status", 2, "online"
        else:
            from toolbox import live_stream
            return live_stream.fetch_status(platform, room)
        if info.get("error") or field not in info:
            return {"error": info.get("error") or "房间状态缺失"}
        return {"live": int(info[field]) == live_value, "status": info[field],
                "title": info.get("title") or "", "online": info.get(online_field) or 0}
    except Exception as error:
        return {"error": str(error)}


# ==================== 录制任务管理 ====================

DEFAULT_SETTINGS = {
    "interval": 30,          # 开播检测间隔（秒）
    "mode": "下载流",         # 下载流（FLV，断线续传）/ 录制流（ffmpeg）
    "output_format": OUTPUT_FORMAT_AUTO,   # 自动（抖音 TS · B站 FLV）/ TS / FLV
    "segment_mode": "关闭",   # 关闭 / 按时间 / 按大小（录播姬 CuttingMode）
    "segment_minutes": 30,   # 按时间：每段分钟数
    "segment_mb": 1024,      # 按大小：每段 MB
    "stop_minutes": 0,       # 录满 N 分钟停止（0=不限）
    "outdir": "",            # 保存目录（空=导出目录/直播录制/<平台>）
    "date_folder": True,     # 主播目录下再按 YYYYMMDD 建日期文件夹（同自动整理）
    "retry_delay": 6,        # 断流后重连间隔（秒，录播姬 TimingStreamRetry）
    "max_retry": 30,         # 单场最多重试次数
    "save_danmaku": True,    # 六平台 JSONL；B站另存 XML
    "render_danmaku": True,  # 开录自动启动独立弹幕渲染窗口
    "save_cover": True,      # 录制时保存直播封面（录播姬 SaveStreamCover）
    # 标题命中这些正则时不录（录播姬 TitleFilterPatterns，多个用 ; 分隔）
    "title_filter": "",
    # WebHook 推送（企业微信/钉钉/飞书/自定义）
    "push": {"enabled": False, "platform": "企业微信机器人", "url": "",
             "prefix": "",
             "events": {"开播": True, "关播": True, "开始录制": True,
                        "停止录制": True, "在线人数": False}},
    "online_threshold": 10000,   # 在线人数提醒阈值
}

# 待开播主播（还没有房间号，靠账号主页解析）的最短重解析间隔（秒）
RESOLVE_MIN_INTERVAL = 60


class RecordManager:
    """一个平台的录制调度器：检测开播 → 自动开录 → 断流重连 → 关播停止。

    线程模型：一个监控线程按 interval 轮询所有「是否监控」的主播；
    录制任务各自跑在自己的线程/进程里，互不阻塞。
    """

    def __init__(self, platform, settings=None, on_event=None, on_change=None,
                 on_record_state=None, on_danmaku=None):
        self.platform = platform
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.anchors = []
        self._on_event = on_event or (lambda level, text: None)
        self._on_change = on_change or (lambda: None)
        self._on_record_state = on_record_state or (lambda a, active: None)
        self._on_danmaku = on_danmaku or (lambda key, name, text, message: None)
        self._tasks = {}       # key -> StreamDownloader / FfmpegRecorder
        self._task_info = {}   # key -> {"kind": "dl"/"ff", "out": path}
        self._danmaku = {}     # key -> DanmakuRecorder（录制时同步存弹幕）
        self._retry = {}       # key -> {"count": n, "next": ts}
        self._resume = {}      # key -> {"path": .., "offset_ms": ..}（TS 续录）
        self._timelines = {}   # key -> TimelineRecorder（P1 时间轴锚点）
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self.cycles = 0
        self.last_run = ""
        self.running = False
        # 状态代次：清除状态后 +1，正在跑的检测结果按旧代次直接丢弃，
        # 避免清完几秒后被迟到的「取流失败」重新写回表格。
        self._epoch = 0

    # ---------- 事件 / 推送 / 记录 ----------
    def emit(self, text, level="info"):
        self._on_event(level, text)

    def _pusher(self):
        from toolbox.webhook import Pusher
        return Pusher(self.settings.get("push") or {})

    def push(self, a, event, text, flag_attr=""):
        """按「全局事件开关 + 该主播提醒开关」推送 WebHook（异步，失败不影响录制）。"""
        if flag_attr and not getattr(a, flag_attr, True):
            return
        try:
            if self._pusher().push(event, text):
                self.emit("已推送[%s] %s" % (event, text), "info")
        except Exception as e:  # noqa: BLE001
            self.emit("推送失败：%s" % e, "warn")

    def record_event(self, a, action, detail=""):
        """记录数据/YYYY-MM-DD_录制事件.csv（对齐江湖录播的 append_record_event）。"""
        try:
            d = os.path.join(self.out_dir(), "..", "记录数据")
            d = os.path.abspath(d)
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "%s_录制事件.csv" % time.strftime("%Y-%m-%d"))
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8-sig", newline="") as f:
                import csv
                w = csv.writer(f)
                if new:
                    w.writerow(["时间", "平台", "主播名称", "房间号", "动作", "详情"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"),
                            PLATFORMS[a.platform]["name"], a.name or a.room,
                            a.room, action, detail])
        except Exception:
            pass

    def changed(self):
        try:
            self._on_change()
        except Exception:
            pass

    # ---------- 目录 ----------
    def out_dir(self):
        d = self.settings.get("outdir") or ""
        if not d:
            try:
                from toolbox.config_manager import get_export_dir
                d = str(get_export_dir() / "直播录制" / PLATFORMS[self.platform]["name"])
            except Exception:
                d = os.path.join(os.getcwd(), "download", "直播录制",
                                 PLATFORMS[self.platform]["name"])
        os.makedirs(d, exist_ok=True)
        return d

    # ---------- 监控 ----------
    def start_monitor(self):
        if self.running:
            return False
        if not any(a.monitoring for a in self.anchors):
            self.emit("没有主播加入监控：请先在「监控状态」列打开", "warn")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="rec-monitor-%s" % self.platform)
        self.running = True
        self._thread.start()
        self.emit("监控已启动（每 %d 秒检测一次开播）" % int(self.settings["interval"]), "ok")
        self.changed()
        return True

    def stop_monitor(self):
        if not self.running:
            return False
        self._stop.set()
        self.running = False
        self.emit("监控已停止", "warn")
        self.changed()
        return True

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._cycle()
            except Exception as e:  # noqa: BLE001
                self.emit("监控循环异常：%s" % e, "error")
            self.cycles += 1
            self.last_run = time.strftime("%Y-%m-%d %H:%M:%S")
            self.changed()
            interval = max(5, int(self.settings.get("interval") or 30))
            self._stop.wait(max(1.0, interval - (time.time() - t0)))

    def _cycle(self):
        pending = [a for a in list(self.anchors)
                   if a.monitoring and self._needs_resolve(a)]
        if pending:
            self._resolve_pending(pending)
        for a in list(self.anchors):
            if self._stop.is_set():
                return
            if not a.monitoring:
                continue
            if not a.room:
                # 待开播主播本轮还没解析出房间号：等下一轮，别拿空号去请求
                continue
            self.check_one(a, status_only=a.live and a.recording)
        self.poll_finished()

    # ---------- 待开播主播：账号主页 → 房间号 ----------
    def _pending_interval(self):
        """待开播主播的重解析间隔：默认 60 秒（开播后最多 1 分钟就能补上）。"""
        return max(RESOLVE_MIN_INTERVAL, int(self.settings.get("interval") or 30))

    def _needs_resolve(self, a):
        if a.room or not a.url:
            return False
        from toolbox import live_room_resolver
        if not live_room_resolver.can_resolve(a.platform, a.url):
            return False
        return time.time() >= float(a._resolve_at or 0)

    def _resolve_pending(self, anchors):
        """批量解析待开播主播的房间号；抖音账号共用一次浏览器会话。"""
        from toolbox import live_room_resolver
        items = [(a.platform, a.url) for a in anchors]
        try:
            results = live_room_resolver.resolve_many(items)
        except Exception as e:  # noqa: BLE001
            self.emit("待开播主播解析失败：%s" % e, "warn")
            return
        now = time.time()
        for a, (room, note) in zip(anchors, results):
            a.last_check = time.strftime("%Y-%m-%d %H:%M:%S")
            if room:
                a.room = room
                a._resolve_at = 0
                a.error = ""
                self.emit("「%s」已取到直播间号 %s" % (a.name or a.url[:20], room), "ok")
                live_room_resolver.remember(
                    a.account_id, a.platform, a.url, room, note or "账号主页解析")
            else:
                a._resolve_at = now + self._pending_interval()
                if note and note != getattr(a, "_note", ""):
                    a._note = note
                    self.emit("「%s」%s" % (a.name or a.url[:20], note), "info")
        self.changed()

    # ---------- 单个主播检测 ----------
    def check_one(self, a, status_only=False):
        epoch = self._epoch
        if not a.room:
            # 手动「立即检测」或刚加进来的待开播主播：先解析房间号
            self._resolve_pending([a])
            if not a.room:
                self.changed()
                return
        info = fetch_status(a.platform, a.room) if status_only else fetch_info(a.platform, a.room, a.quality)
        if epoch != self._epoch:
            # 检测期间用户清除了状态：这批结果已经过期，不能再写回
            return
        a.last_check = time.strftime("%Y-%m-%d %H:%M:%S")
        prev_error = a.error or ""
        a.error = info.get("error") or ""
        if a.error and info.get("status") is None:
            # 查询失败不是关播；保留上一状态，正常收流的任务继续运行。
            if a.error != prev_error:
                # 表格只显示「取流失败」，真实原因进运行日志，便于排查
                self.emit("「%s」取流失败：%s" % (a.name or a.room, a.error), "warn")
            self.changed()
            return
        if info.get("note") and info["note"] != getattr(a, "_note", ""):
            a._note = info["note"]
            self.emit("「%s」%s" % (a.name or a.room, info["note"]), "warn")
        was_live = a.live
        a.live = bool(info.get("live"))
        if info.get("title"):
            a.title = info["title"]
        if info.get("name") and not a.name:
            a.name = info["name"]
        if info.get("online") not in (None, ""):
            a.online = info["online"]
        if info.get("cover"):
            a.cover = info["cover"]
        if a.live and not was_live:
            text = "「%s」开播了（在线 %s）%s" % (
                a.name or a.room, a.online or "-",
                ("｜" + a.title) if a.title else "")
            self.emit(text, "ok")
            self.record_event(a, "开播", a.title or "")
            self.push(a, "开播", text, "notify_live")
        elif was_live and not a.live:
            self.emit("「%s」已关播" % (a.name or a.room), "warn")
            a._force_flv = False      # 新一场直播重新按默认格式来
            self.record_event(a, "关播", "")
            self.push(a, "关播", "「%s」已关播" % (a.name or a.room), "notify_offline")
            if a.recording:
                self.stop_record(a, reason="关播")
        # 在线人数提醒（阈值全局，逐主播开关）
        if a.live and a.notify_online:
            try:
                th = int(self.settings.get("online_threshold") or 0)
                if th and int(str(a.online).replace(",", "") or 0) >= th:
                    self.push(a, "在线人数", "「%s」在线人数达到 %s"
                              % (a.name or a.room, a.online), "")
            except (TypeError, ValueError):
                pass
        # 自动开录
        if a.live and a.monitoring and self._auto_record(a) and not a.recording:
            self.start_record(a, info if "stream" in info else None)
        self.changed()

    def _auto_record(self, a):
        """是否要录：主播开了「是否录制」，或本平台设置了「监控到就录」。"""
        return bool(a.recording or getattr(a, "_want_record", False)
                    or self.settings.get("record_all"))

    # ---------- 录制 ----------
    def _record_params(self, a):
        seg_mode = self.settings.get("segment_mode") or "关闭"
        seg_min = int(self.settings.get("segment_minutes") or 0) if seg_mode == "按时间" else 0
        seg_mb = int(self.settings.get("segment_mb") or 0) if seg_mode == "按大小" else 0
        if a.segment:
            seg_min = a.segment_minutes or seg_min
            seg_mb = a.segment_mb or seg_mb
        stop_min = a.stop_minutes or int(self.settings.get("stop_minutes") or 0)
        return seg_min, seg_mb, stop_min

    def start_record(self, a, info=None):
        with self._lock:
            if a.recording:
                return False, "已在录制中"
            info = info or fetch_info(a.platform, a.room, a.quality)
            url = info.get("stream") or ""
            # 标题过滤（录播姬 TitleFilterPatterns）：命中则不录，避免录到广告/回放
            tf = str(self.settings.get("title_filter") or "").strip()
            if tf and info.get("title"):
                for pat in re.split(r"[;；]", tf):
                    pat = pat.strip()
                    if not pat:
                        continue
                    try:
                        if re.search(pat, info["title"]):
                            self.emit("「%s」标题命中过滤规则「%s」，本次不录" % (
                                a.name or a.room, pat), "warn")
                            return False, "标题命中过滤规则"
                    except re.error:
                        continue
            if not url:
                self.emit("「%s」没有可用直播流（未开播或被限制）" % (a.name or a.room), "warn")
                return False, "无可用直播流"
            outdir = record_dir(self.out_dir(), a.name or a.room,
                                date_folder=bool(self.settings.get("date_folder", True)))
            os.makedirs(outdir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            base = "%s_%s" % (safe_name(a.name or a.room), ts)
            audio_only = int(getattr(a, "content", 0) or 0) == 1
            fmt = resolve_format(a.platform, self.settings)   # 抖音 ts / B站 flv
            if (getattr(a, "_force_flv", False) and not audio_only
                    and supports_flv_download(a.platform)):
                fmt = "flv"        # 本场 TS 直录失败过 → 退回 FLV 原样下载
            ext = ".ts" if (audio_only or fmt == "ts") else ".flv"
            path = os.path.join(outdir, base + ext)
            ts_off = 0
            # 同一场直播补录：ffmpeg 进程意外退出后回到同一个文件，时间戳接着
            # 上一次继续（-output_ts_offset），不会出现回退跳变，也不会碎文件
            rs = self._resume.pop(a.key, None)
            if rs and fmt == "ts" and not audio_only and os.path.exists(rs.get("path") or ""):
                path = rs["path"]
                ts_off = int(rs.get("offset_ms") or 0)
            # P1 时间轴：首帧墙钟 ↔ 视频 PTS 锚点，供弹幕对齐/写时间码使用
            from toolbox import timeline as TL
            tl = TL.TimelineRecorder(path, platform=a.platform, room=a.room, base=base)
            self._timelines[a.key] = tl

            def _anchor(wall_ms, file_path=None, pts_ms=0, _tl=tl):
                try:
                    _tl.add_anchor(file_path or path, wall_ms, pts_ms)
                except Exception:
                    pass

            seg_min, seg_mb, stop_min = self._record_params(a)
            headers = {"User-Agent": UA,
                       "Referer": PLATFORMS[a.platform]["referer"]}
            mode = self.settings.get("mode") or "下载流"
            if audio_only or info.get("is_hls") or fmt == "ts":
                # 音频录制 / HLS / TS 输出都只能走 ffmpeg（下载流只写 FLV）
                mode = "录制流"
            if mode == "录制流":
                task = FfmpegRecorder(url, path, headers=headers,
                                      stop_minutes=stop_min,
                                      segment_minutes=seg_min, log_dir=outdir,
                                      audio_only=audio_only, fmt=fmt,
                                      ts_offset_ms=ts_off,
                                      on_media_start=_anchor)
                ok = task.start()
                kind = "ff"
            else:
                task = StreamDownloader(url, path, headers=headers,
                                        stop_minutes=stop_min,
                                        segment_minutes=seg_min,
                                        segment_mb=seg_mb,
                                        base_name=base, outdir=outdir,
                                        label=a.name or a.room,
                                        on_media_start=_anchor,
                                        refresh_url=lambda: fetch_info(
                                            a.platform, a.room, a.quality).get("stream") or "")
                ok = task.start()
                kind = "dl"
            if not ok:
                err = getattr(task, "error", "") or "录制启动失败"
                self.emit("「%s」%s" % (a.name or a.room, err), "error")
                return False, err
            self._tasks[a.key] = task
            self._task_info[a.key] = {"kind": kind, "out": path}
            a.recording = True
            a.rec_state = "录制中"
            a.elapsed = 0
            if info.get("quality"):
                a.quality_label = info["quality"]
            self._retry.pop(a.key, None)
            # 录播姬的「保存直播封面」：开录时把封面存到该主播目录（失败不影响录制）
            if info.get("cover") and self.settings.get("save_cover", True):
                self._save_cover(info["cover"], outdir, base)
            # 录播姬的「弹幕录制」：和视频同名，B站 XML / 抖音 JSONL
            self._notify_record_state(a, True)
            if self.settings.get("save_danmaku") or self.settings.get("render_danmaku"):
                self._start_danmaku(a, outdir, base)
            text = "「%s」开始录制（%s · %s · %s）" % (
                a.name or a.room, mode, fmt.upper(),
                a.quality_label or info.get("quality") or "默认清晰度")
            self.emit(text, "ok")
            self.record_event(a, "开始录制", "%s | %s" % (path, a.quality_label or ""))
            self.push(a, "开始录制", text, "notify_rec_start")
            self.changed()
            return True, ""

    # ---------- 弹幕录制 ----------
    def _notify_record_state(self, a, active):
        try:
            self._on_record_state(a, active)
        except Exception:
            pass

    def _start_danmaku(self, a, outdir, base):
        if not supports_danmaku(a.platform):
            self.emit("「%s」该平台暂不支持弹幕录制（不影响视频）"
                      % (a.name or a.room), "info")
            return
        try:
            from toolbox.danmaku_recorder import DanmakuRecorder
            rec = DanmakuRecorder(a.platform, a.room, outdir, base,
                                  on_log=lambda t, lv="info": self.emit(t, lv),
                                  save=bool(self.settings.get("save_danmaku")),
                                  on_message=lambda n, t, m: self._on_danmaku(a.key, n, t, m))
            if rec.start():
                self._danmaku[a.key] = rec
        except Exception as e:  # noqa: BLE001
            self.emit("弹幕录制启动失败（不影响视频）：%s" % e, "warn")

    def _stop_danmaku(self, key):
        rec = self._danmaku.pop(key, None)
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass

    @staticmethod
    def _save_cover(url, outdir, base):
        def work():
            try:
                import httpx
                r = httpx.get(url, timeout=15, follow_redirects=True,
                              headers={"User-Agent": UA})
                if r.status_code == 200 and r.content:
                    with open(os.path.join(outdir, base + "_封面.jpg"), "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def stop_record(self, a, reason=""):
        with self._lock:
            task = self._tasks.pop(a.key, None)
            info = self._task_info.pop(a.key, {})
            if task is not None:
                a.elapsed = task.elapsed()
                task.stop()
            a.recording = False
            a.rec_state = "已停止"
            self._retry.pop(a.key, None)
            self._resume.pop(a.key, None)
        self._stop_danmaku(a.key)
        self._notify_record_state(a, False)
        if task is not None:
            text = "「%s」停止录制%s（已录 %s）" % (
                a.name or a.room, ("·" + reason) if reason else "",
                fmt_duration(a.elapsed))
            self.emit(text, "warn")
            self.record_event(a, "停止录制", reason or "")
            self.push(a, "停止录制", text, "notify_rec_stop")
        self.changed()
        return bool(task)

    def stop_all(self):
        for a in list(self.anchors):
            if a.recording:
                self.stop_record(a, reason="一键停止")

    def reset_states(self, notify=True, keep_intent=False):
        """清空运行状态（主播列表、备注与录制设置保留）。

        停监控、停录制，主播的直播/录制/监控/在线等状态归零，轮次计数与
        上次检测时间清空；调用方负责刷新界面与持久化。
        keep_intent=True 用于启动恢复：保留「是否录制」与「监控状态」两个
        用户意图标记，避免重启后把所有主播的录制/监控设置清成默认。
        """
        self._epoch += 1
        self.stop_monitor()
        self.running = False
        self.stop_all()
        with self._lock:
            self._tasks.clear()
            self._task_info.clear()
            self._retry.clear()
            self._resume.clear()
        self.cycles = 0
        self.last_run = ""
        for a in self.anchors:
            a.live = False
            a.online = 0
            a.last_check = ""
            a.error = ""
            a.recording = False
            a.rec_state = "未录制"
            a.elapsed = 0
            if not keep_intent:
                a.monitoring = False
                a._want_record = False
            a.selected = False
            a.title = ""
            a.cover = ""
            a._note = ""
            a._force_flv = False
        if notify:
            self.changed()

    def is_recording(self, a):
        t = self._tasks.get(a.key)
        return bool(t and t.is_active())

    def elapsed(self, a):
        t = self._tasks.get(a.key)
        if t is not None and a.recording:
            return t.elapsed()
        return a.elapsed

    def output_of(self, a):
        t = self._tasks.get(a.key)
        if t is not None:
            return t.output_of()
        return (self._task_info.get(a.key) or {}).get("out") or ""

    def active_count(self):
        return sum(1 for a in self.anchors if a.recording and self.is_recording(a))

    # ---------- 结束回收 / 断线重连 ----------
    def _reap(self, a, task):
        """任务结束的统一回收（监控线程与界面刷新都走这里）。

        以前界面每秒的 sync_tasks 会先把已结束的任务从 _tasks 里摘掉，
        监控线程的 poll_finished 再看到 task is None 就直接跳过——「自动续录」
        因此被静默吞掉，表现为一场直播被打成好几个碎文件。合并到一处后
        无论谁先发现，续录都只调度一次。
        """
        if getattr(task, "_reaped", False):
            return
        task._reaped = True
        err = getattr(task, "error", "") or ""
        a.elapsed = task.elapsed()
        with self._lock:
            if self._tasks.get(a.key) is task:
                self._tasks.pop(a.key, None)
        self._stop_danmaku(a.key)
        self._notify_record_state(a, False)
        a.recording = False
        a.rec_state = "已停止"
        # TS 直录一个字节都没产出（个别房间推 HEVC / 增强型 FLV，ffmpeg 转封装
        # 失败）→ 本场自动降级成 FLV 原样下载，保证不空手
        try:
            out = task.output_of()
            if (getattr(task, "fmt", "") == "ts" and supports_flv_download(a.platform)
                    and out and os.path.exists(out) and os.path.getsize(out) == 0):
                a._force_flv = True
                self.emit("「%s」TS 直录没有产出，本场改用 FLV 原样下载"
                          % (a.name or a.room), "warn")
        except Exception:
            pass
        if err:
            self.emit("「%s」录制中断：%s" % (a.name or a.room, err), "warn")
        else:
            self.emit("「%s」录制结束（已录 %s）" % (
                a.name or a.room, fmt_duration(a.elapsed)), "info")
        if not (a.live and a.monitoring and getattr(a, "_want_record", False)):
            return
        # 直播还在 + 还开着录制 → 按间隔重试（录播姬 TimingStreamRetry 同款）
        r = self._retry.setdefault(a.key, {"count": 0, "next": 0})
        if r["count"] >= int(self.settings.get("max_retry") or 30):
            return
        r["count"] += 1
        r["next"] = time.time() + int(self.settings.get("retry_delay") or 6)
        # TS 续录：回到同一个文件、时间戳接着上次（避免碎文件 / 跳变花屏）
        try:
            self._resume[a.key] = {
                "path": task.output_of() or "",
                "offset_ms": (int(getattr(task, "ts_offset_ms", 0) or 0)
                              + a.elapsed * 1000 + 300),
            }
        except Exception:
            pass
        self.emit("「%s」将在 %d 秒后自动重连续录（第 %d 次）" % (
            a.name or a.room, int(self.settings.get("retry_delay") or 6),
            r["count"]), "info")

    def poll_finished(self):
        now = time.time()
        for a in list(self.anchors):
            if not a.recording:
                continue
            task = self._tasks.get(a.key)
            if task is None or task.is_active():
                continue
            self._reap(a, task)
        # 到点重试
        for key, r in list(self._retry.items()):
            if not r.get("next") or now < r["next"]:
                continue
            a = next((x for x in self.anchors if x.key == key), None)
            if a is None or not a.live or not getattr(a, "_want_record", False):
                self._retry.pop(key, None)
                continue
            self._retry.pop(key, None)
            self.start_record(a)

    def sync_tasks(self):
        """轻量同步（界面每秒调用）：任务结束时按同一套规则收尾并续录。"""
        for a in list(self.anchors):
            if not a.recording:
                continue
            task = self._tasks.get(a.key)
            if task is None:
                a.recording = False
                a.rec_state = "已停止"
                continue
            if not task.is_active():
                self._reap(a, task)

    # ---------- 状态持久化 ----------
    def to_dict(self):
        return {"settings": dict(self.settings),
                "anchors": [{**a.to_dict(), "want_record": bool(getattr(a, "_want_record", False))}
                            for a in self.anchors]}

    def load_dict(self, d):
        d = d or {}
        self.settings = {**DEFAULT_SETTINGS, **(d.get("settings") or {})}
        self.anchors = []
        for item in (d.get("anchors") or []):
            a = Anchor.from_dict(item)
            a.platform = self.platform
            a.recording = False       # 重启后没有活动任务
            a.rec_state = "未录制"
            a.live = False
            a._want_record = bool(item.get("recording") or item.get("want_record"))
            self.anchors.append(a)

    def shutdown(self):
        self.stop_monitor()
        self.stop_all()
        for key in list(self._danmaku.keys()):
            self._stop_danmaku(key)


# ==================== 配置读写 ====================

def _state_path():
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir() / "live_record.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent.parent.parent / "config" / "live_record.json"


def load_state():
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_state_lock = threading.RLock()


def save_state(state):
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        from toolbox.durable_io import atomic_write_json
        with _state_lock:
            atomic_write_json(p, state)
        return True
    except Exception:
        return False


# ==================== 统一账号 → 录制主播（账号配置「用于直播录制」） ====================

# 统一账号平台键 → 录制平台键（统一层叫 bilibili，录制层叫 bili）
ACCOUNT_PLATFORM_KEYS = {"bilibili": "bili"}


def record_platform(account_platform: str) -> str:
    return ACCOUNT_PLATFORM_KEYS.get(account_platform, account_platform)


def list_account_anchors() -> dict:
    """统一账号 id → 已绑定的录制主播（只读，供账号配置显示勾选状态）。"""
    state = load_state() or {}
    out = {}
    for plat, data in (state.get("platforms") or {}).items():
        for item in (data or {}).get("anchors") or []:
            aid = (item or {}).get("account_id")
            if aid:
                out[aid] = {"platform": plat,
                            "room": str(item.get("room") or ""),
                            "name": item.get("name") or ""}
    return out


def set_account_anchor(account_id: str, account_platform: str, room: str,
                       name: str = "", url: str = ""):
    """把统一账号绑定为一个录制主播；同平台同房间号复用，不重复添加。

    room 可以为空：抖音/B站/快手这类能从账号主页反推直播间号的平台，
    先记成「待开播」主播，监控线程开播时自动补上房间号。
    只写 platform/room/name/account_id/url，其它录制设置保持原样；
    返回 (是否成功, 提示文本)。
    """
    room = str(room or "").strip()
    if not account_id:
        return False, "缺少账号"
    plat = record_platform(account_platform)
    if plat not in PLATFORMS:
        return False, "该平台不支持直播录制"
    if not room:
        from toolbox import live_room_resolver
        if not live_room_resolver.can_resolve(account_platform, url):
            return False, "缺少直播间号"
    state = load_state() or {}
    platforms = state.setdefault("platforms", {})
    data = platforms.setdefault(plat, {})
    anchors = data.setdefault("anchors", [])
    target = None
    for item in anchors:
        if room and str(item.get("room") or "") == room:
            target = item
            break
    if target is None:
        for item in anchors:
            if item.get("account_id") == account_id:
                target = item
                break
    if target is not None:
        target["account_id"] = account_id
        if room:
            target["room"] = room
        if name:
            target["name"] = name
        if url:
            target["url"] = url
        # 一个账号只对应一个主播：房间号换了就把旧的那条清掉
        data["anchors"] = [x for x in anchors
                           if x is target or x.get("account_id") != account_id]
        save_state(state)
        return True, ("已绑定到已有主播" if room else "已在直播录制中（待开播）")
    anchors.append({"platform": plat, "room": room, "name": name,
                    "account_id": account_id, "url": url,
                    "monitoring": False, "recording": False})
    save_state(state)
    if not room:
        return True, "已加入直播录制（未开播，开播后自动补房间号）"
    return True, "已加入直播录制"


def remove_account_anchor(account_id: str) -> bool:
    """取消「用于直播录制」：按 account_id 把主播从列表移除（不删录制文件）。"""
    if not account_id:
        return False
    state = load_state() or {}
    changed = False
    for data in (state.get("platforms") or {}).values():
        anchors = (data or {}).get("anchors") or []
        keep = [x for x in anchors if (x or {}).get("account_id") != account_id]
        if len(keep) != len(anchors):
            data["anchors"] = keep
            changed = True
    if changed:
        save_state(state)
    return changed
