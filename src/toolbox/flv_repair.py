# -*- coding: utf-8 -*-
"""FLV 修复器（对应录播姬工具箱的「修复视频文件」）。

解决的问题（直播录制常见损坏形态）：
  1. 文件尾部被截断 → 最后一个 tag 只有半个，播放器解析到一半报错；
  2. 断流重连后时间戳回退/跳变（第二段从 0 开始）→ 播放器卡住或进度条错乱；
  3. PreviousTagSize 字段与实际数据长度不一致（某些 CDN 分片拼接导致）；
  4. 中间夹了非法 tag（类型不是 8/9/18）→ 后续全部无法解析。

处理方式（无损，只重排时间戳与长度字段，不转码、不动音视频数据）：
  · 丢掉尾部不完整的 tag，保证文件以完整 tag 收尾；
  · 时间戳改为单调递增：回退按 0 处理，超过 10 秒的跳变压到 100ms；
  · 重新计算每个 tag 后的 PreviousTagSize；
  · 遇到非法 tag 时向后扫描重新对齐（最多 64KB），失败则在该点截断并报告。

参考：BililiveRecorder.Flv（Tag 读写 / 时间戳修正思路），本实现为纯 Python 流式处理，
大文件不会一次性读进内存。
"""

from __future__ import annotations

import os

_TAG_HDR = 11
_PREV_SIZE = 4
_FLV_HEADER = 13
_CHUNK = 1024 * 1024
_MAX_TAG = 16 * 1024 * 1024     # 单 tag 上限，超过视为错位
_MAX_JUMP_MS = 10000            # 时间戳跳变阈值
_JUMP_FIX_MS = 100            # 跳变修正后的间隔


def _ts24(b):
    return (b[0] << 16) | (b[1] << 8) | b[2]


def _mk_ts(ts, ext):
    ts &= 0xFFFFFF
    return bytes(((ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF, ext & 0xFF))


def probe_flv(path):
    """快速读取 FLV 概况（时长 / tag 数 / 是否以完整 tag 收尾）。"""
    out = {"ok": False, "size": 0, "tags": 0, "duration": 0.0, "error": ""}
    try:
        out["size"] = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(_FLV_HEADER)
            if len(head) < _FLV_HEADER or head[:3] != b"FLV":
                out["error"] = "不是 FLV 文件"
                return out
            first_ts = None
            last_ts = 0
            while True:
                hdr = f.read(_TAG_HDR)
                if len(hdr) < _TAG_HDR:
                    break
                size = int.from_bytes(hdr[1:4], "big")
                if size > _MAX_TAG:
                    out["error"] = "tag 长度异常（文件可能损坏）"
                    break
                ts = _ts24(hdr[4:7]) | (hdr[7] << 24)
                f.seek(size + _PREV_SIZE, os.SEEK_CUR)
                out["tags"] += 1
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            out["duration"] = max(0.0, (last_ts - (first_ts or 0)) / 1000.0)
            out["ok"] = out["tags"] > 0
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


def _find_next_tag(f, start, limit=64 * 1024):
    """从 start 起向后找下一个「看起来合法」的 tag 头，返回偏移或 -1。"""
    f.seek(start)
    buf = f.read(limit)
    for i in range(len(buf) - _TAG_HDR):
        t = buf[i]
        if t not in (8, 9, 18):
            continue
        size = int.from_bytes(buf[i + 1:i + 4], "big")
        if size <= 0 or size > _MAX_TAG:
            continue
        # 校验：该 tag 结束处的 PreviousTagSize 应等于 size + 11
        end = i + _TAG_HDR + size
        if end + _PREV_SIZE > len(buf):
            return start + i if i == 0 else -1   # 缓冲不够，交给主循环再判
        prev = int.from_bytes(buf[end:end + _PREV_SIZE], "big")
        if prev == size + _TAG_HDR:
            return start + i
    return -1


def repair_flv(src, dst=None, on_progress=None):
    """修复 FLV。返回统计 dict：ok / tags / dropped / ts_fixed / resync / out / error。"""
    st = {"ok": False, "tags": 0, "dropped": 0, "ts_fixed": 0,
          "resync": 0, "out": dst or "", "error": ""}
    if dst is None:
        root, ext = os.path.splitext(src)
        dst = root + "_修复" + (ext or ".flv")
    st["out"] = dst
    if os.path.abspath(src) == os.path.abspath(dst):
        st["error"] = "输出文件不能覆盖源文件"
        return st
    total = max(1, os.path.getsize(src))
    try:
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            head = fi.read(_FLV_HEADER)
            if len(head) < _FLV_HEADER or head[:3] != b"FLV":
                st["error"] = "不是 FLV 文件（缺少 FLV 头）"
                return st
            # 文件头标准化：header_size 固定 9，PreviousTagSize0 固定 0
            fo.write(head[:9] + b"\x00\x00\x00\x00")
            prev_src_ts = None
            out_ts = 0
            while True:
                pos = fi.tell()
                hdr = fi.read(_TAG_HDR)
                if len(hdr) < _TAG_HDR:
                    break
                ttype = hdr[0]
                size = int.from_bytes(hdr[1:4], "big")
                if ttype not in (8, 9, 18) or size > _MAX_TAG:
                    # 错位：向后扫描重新对齐
                    nxt = _find_next_tag(fi, pos + 1)
                    if nxt < 0:
                        st["dropped"] += 1
                        break
                    st["resync"] += 1
                    fi.seek(nxt)
                    continue
                data = fi.read(size)
                if len(data) < size:
                    st["dropped"] += 1        # 尾部半个 tag：丢弃
                    break
                fi.read(_PREV_SIZE)           # 原 PreviousTagSize 不用，重算
                src_ts = _ts24(hdr[4:7]) | (hdr[7] << 24)
                if prev_src_ts is None:
                    out_ts = src_ts
                else:
                    delta = src_ts - prev_src_ts
                    if delta < 0:
                        delta = 0
                        st["ts_fixed"] += 1
                    elif delta > _MAX_JUMP_MS:
                        delta = _JUMP_FIX_MS
                        st["ts_fixed"] += 1
                    out_ts += delta
                prev_src_ts = src_ts
                ext = (out_ts >> 24) & 0xFF
                fo.write(bytes((ttype,)) + size.to_bytes(3, "big")
                         + _mk_ts(out_ts, ext) + hdr[8:11] + data
                         + (size + _TAG_HDR).to_bytes(4, "big"))
                st["tags"] += 1
                if on_progress and st["tags"] % 500 == 0:
                    try:
                        on_progress(min(0.99, fi.tell() / total))
                    except Exception:
                        pass
        st["ok"] = st["tags"] > 0
        if not st["ok"]:
            st["error"] = "没有解析到有效 tag"
    except Exception as e:  # noqa: BLE001
        st["error"] = str(e)
        st["ok"] = False
    if not st["ok"]:
        try:
            if os.path.exists(dst) and os.path.getsize(dst) < 1024:
                os.remove(dst)
        except OSError:
            pass
    return st
