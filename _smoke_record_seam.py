# -*- coding: utf-8 -*-
"""冒烟：录制花屏修复 + 抖音 TS / B站 FLV 默认输出。

覆盖：
  1. resolve_format 默认值（抖音 ts、B站 flv）+ 手动覆盖
  2. FlvWriter 接缝：重复 FLV 头、时间戳回退、非关键帧开头 → 输出单调、无花屏帧
  3. FlvWriter 失步重同步：垃圾字节不会污染文件
  4. flv_repair 接缝丢帧：花屏段变成干净切口
  5. FfmpegRecorder TS：真实 ffmpeg 走管道落 .ts，可被 ffprobe 解析；
     二次续录用 -output_ts_offset，PTS 不回退
运行： python _smoke_record_seam.py
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import (BaseHTTPRequestHandler, SimpleHTTPRequestHandler,
                         ThreadingHTTPServer)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FAIL") + "  " + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 构造 FLV
FLV_HEADER = b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00"


def tag(ttype, payload, ts):
    hdr = bytes((ttype,)) + len(payload).to_bytes(3, "big") \
        + bytes(((ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF, (ts >> 24) & 0xFF)) \
        + b"\x00\x00\x00"
    return hdr + payload + (len(payload) + 11).to_bytes(4, "big")


def v_key(ts, n=200):
    """AVC 关键帧（frame_type 1 / codec 7 / packet type 1）。"""
    return tag(9, b"\x17\x01" + b"K" * n, ts)


def v_p(ts, n=100):
    """AVC 非关键帧。"""
    return tag(9, b"\x27\x01" + b"P" * n, ts)


def v_seq(ts=0):
    """AVC sequence header（frame_type 1 / codec 7 / packet type 0）。"""
    return tag(9, b"\x17\x00\x00\x00\x00" + b"S" * 20, ts)


def a_tag(ts, n=40):
    return tag(8, b"\xaf\x01" + b"A" * n, ts)


def parse_ts(headers):
    return ((headers[4] << 16) | (headers[5] << 8) | headers[6]) | (headers[7] << 24)


def read_tags(path):
    """返回 [(type, ts, payload_head)]。"""
    out = []
    with open(path, "rb") as f:
        f.read(13)
        while True:
            hdr = f.read(11)
            if len(hdr) < 11:
                break
            size = int.from_bytes(hdr[1:4], "big")
            data = f.read(size)
            f.read(4)
            if len(data) < size:
                break
            out.append((hdr[0], parse_ts(hdr), data[:3]))
    return out


# ---------------------------------------------------------------- 1. 格式默认值
def test_default_format():
    from toolbox import live_record as LR
    check("抖音默认 TS", LR.resolve_format("douyin", {}) == "ts")
    check("B站默认 FLV", LR.resolve_format("bili", {}) == "flv")
    check("空设置也走默认", LR.resolve_format("douyin", None) == "ts")
    check("手动指定 FLV 覆盖自动",
          LR.resolve_format("douyin", {"output_format": "FLV"}) == "flv")
    check("手动指定 TS 覆盖自动",
          LR.resolve_format("bili", {"output_format": "TS（MPEG-TS）"}) == "ts")
    check("DEFAULT_SETTINGS 带输出格式", "output_format" in LR.DEFAULT_SETTINGS)


# ---------------------------------------------------------------- 2. 接缝
def test_seam_writer():
    from toolbox.live_record import FlvWriter
    d = tempfile.mkdtemp()
    p = os.path.join(d, "seam.flv")
    w = FlvWriter(p, seam_fix=True)

    # 第一段：正常流，ts 从 2900000 起（抖音是服务器绝对时钟）
    seg1 = FLV_HEADER + v_seq(2900000) + v_key(2900000) + a_tag(2900000) \
        + v_p(2900040) + a_tag(2900040) + v_p(2900080)
    w.feed(seg1)
    n1 = len(read_tags(p))
    check("第一段落盘 6 个 tag", n1 == 6, str(n1))

    # 第二段：断流重连 —— 带重复 FLV 头、时间戳从 0 重新开始、
    # 开头先是 seq header + 两个 P 帧（这就是花屏源），之后才是关键帧
    w.begin_seam()
    seg2 = FLV_HEADER + v_seq(0) + v_p(0) + v_p(40) + v_key(80) \
        + a_tag(80) + v_p(120)
    w.feed(seg2)
    tags = read_tags(p)
    print("     接缝后 tag 序列:", [(t, s, pl[:1]) for t, s, pl in tags])
    check("重复 FLV 头没有被写进文件", b"FLV" not in open(p, "rb").read()[13:])
    tss = [s for _, s, _ in tags]
    check("时间戳单调不减", tss == sorted(tss), str(tss))
    check("接缝继续上一段时间（不回退到 0）",
          min(s for _, s, _ in tags[len(tags) - 4:]) > 2900000, str(tss))
    # 接缝处被丢掉的：2 个 P 帧（音频在关键帧前也不收）
    check("接缝丢弃非关键帧", w.dropped >= 2, "dropped=%d" % w.dropped)
    check("接缝完成时间戳平移", w.ts_fixed == 1, "ts_fixed=%d" % w.ts_fixed)
    # 接缝后第一「画面」（pkt_type=1）必须是关键帧；seq header 允许在前
    frames = [pl[0] for t, s, pl in tags[n1:] if t == 9 and pl[1] == 1]
    check("接缝后第一帧画面是关键帧", frames and frames[0] == 0x17,
          str([hex(k) for k in frames]))
    check("接缝后没有裸露的 P 帧", 0x27 not in frames[:1], str([hex(k) for k in frames]))
    w.close()


# ---------------------------------------------------------------- 3. 失步重同步
def test_resync():
    from toolbox.live_record import FlvWriter
    d = tempfile.mkdtemp()
    p = os.path.join(d, "resync.flv")
    w = FlvWriter(p, seam_fix=True)
    blob = FLV_HEADER + v_seq(0) + v_key(0) + a_tag(0) \
        + b"\xde\xad\xbe\xef" * 300 + v_p(40) + a_tag(40)
    w.feed(blob)
    tags = read_tags(p)
    check("垃圾字节后重新对齐", len(tags) == 5, str(len(tags)))
    check("垃圾段被计入丢弃", w.dropped >= 1, str(w.dropped))
    check("文件可被重新解析到底", all(t in (8, 9, 18) for t, _, _ in tags))
    w.close()


# ---------------------------------------------------------------- 4. 修复器
def test_repair_seam():
    from toolbox.flv_repair import repair_flv
    d = tempfile.mkdtemp()
    src = os.path.join(d, "broken.flv")
    with open(src, "wb") as f:
        f.write(FLV_HEADER + v_seq(0) + v_key(0) + a_tag(0) + v_p(40) + a_tag(40))
        # 断流：ts 回退到 0，开头是两帧 P（花屏段）
        f.write(v_seq(0) + v_p(0) + v_p(40) + v_key(80) + a_tag(80) + v_p(120))
    st = repair_flv(src)
    check("修复成功", st["ok"], str(st))
    check("修复计入接缝丢帧", st["seam_dropped"] >= 2, str(st["seam_dropped"]))
    check("修复后 tag 数 = 9", st["tags"] == 9, str(st["tags"]))
    tss = [s for _, s, _ in read_tags(st["out"])]
    check("修复后时间戳单调", tss == sorted(tss), str(tss))


# ---------------------------------------------------------------- 5. 真 ffmpeg TS
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_ffmpeg_ts():
    from toolbox.live_record import FfmpegRecorder, find_ffmpeg
    exe = find_ffmpeg()
    check("找到 ffmpeg", bool(exe), exe)
    if not exe:
        return
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.flv")
    mk = subprocess.run(
        [exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x180:rate=25:duration=6", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=6", "-c:v", "libx264",
         "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-f", "flv", src],
        capture_output=True)
    check("合成测试流", mk.returncode == 0 and os.path.getsize(src) > 1000,
          mk.stderr.decode("utf-8", "replace")[-200:])

    srv = ThreadingHTTPServer(("127.0.0.1", _free_port()),
                              lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=d, **k))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/src.flv" % srv.server_address[1]

    out = os.path.join(d, "rec.ts")
    rec = FfmpegRecorder(url, out, fmt="ts", log_dir=d)
    ok = rec.start()
    check("TS 录制启动", ok, rec.error)
    t0 = time.time()
    while time.time() - t0 < 20 and rec.is_active():
        time.sleep(0.3)
    rec.stop()
    size = os.path.getsize(out) if os.path.exists(out) else 0
    check("TS 文件有内容", size > 20000, "%.1f KB" % (size / 1024))
    # 用 ffprobe / ffmpeg 校验可解析
    pr = subprocess.run([exe, "-hide_banner", "-i", out], capture_output=True)
    txt = pr.stderr.decode("utf-8", "replace")
    check("ffmpeg 能识别 TS", "mpegts" in txt.lower() or "Video:" in txt,
          txt.replace("\n", " ")[-160:])
    check("TS 含视频流", "Video: h264" in txt, txt.replace("\n", " ")[-160:])

    # 续录：同一文件 + 时间戳偏移，验证 PTS 不回退
    r1 = subprocess.run([exe, "-hide_banner", "-i", out, "-map", "0:v",
                         "-c", "copy", "-f", "null", "-"], capture_output=True)
    check("TS 可完整解码", r1.returncode == 0,
          r1.stderr.decode("utf-8", "replace")[-160:].replace("\n", " "))

    # 续录到同一文件：第二段用 -output_ts_offset 接在第一段之后
    probe = os.path.join(os.path.dirname(exe), "ffprobe" + os.path.splitext(exe)[1])
    if not os.path.exists(probe):
        probe = "ffprobe"

    def pts_list(path):
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v",
                            "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                           capture_output=True)
        out_ = []
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip().strip(",")
            if line:
                try:
                    out_.append(float(line))
                except ValueError:
                    pass
        return out_

    base = pts_list(out)
    check("第一次录像有 PTS", len(base) > 50, str(len(base)))
    time.sleep(0.2)
    rec2 = FfmpegRecorder(url, out, fmt="ts", log_dir=d,
                          ts_offset_ms=int((base[-1] + 0.2) * 1000))
    check("续录启动", rec2.start(), rec2.error)
    t0 = time.time()
    while time.time() - t0 < 20 and rec2.is_active():
        time.sleep(0.3)
    rec2.stop()
    allpts = pts_list(out)
    check("续录后文件变长", len(allpts) > len(base) + 20,
          "%d -> %d" % (len(base), len(allpts)))
    check("续录后 PTS 单调不回退", allpts == sorted(allpts),
          str([round(x, 2) for x in allpts[len(base) - 2:len(base) + 4]]))
    srv.shutdown()


# ---------------------------------------------------------------- 6. 调度器选型
def test_manager_pick_engine():
    from toolbox import live_record as LR
    d = tempfile.mkdtemp()
    info = {"stream": "http://127.0.0.1:9/nothere.flv", "live": True,
            "is_hls": False, "quality": "原画", "title": "t", "cover": ""}

    m = LR.RecordManager("douyin", {"outdir": d})
    a = LR.Anchor(platform="douyin", room="1", name="抖音测试")
    a._want_record = True
    ok, err = m.start_record(a, info=info)
    out = m.output_of(a)
    check("抖音走 TS 容器", ok and out.endswith(".ts"), out or err)
    check("抖音走 ffmpeg 引擎", isinstance(m._tasks.get(a.key), LR.FfmpegRecorder),
          type(m._tasks.get(a.key)).__name__)
    m.stop_all()

    m2 = LR.RecordManager("bili", {"outdir": d})
    b = LR.Anchor(platform="bili", room="2", name="B站测试")
    b._want_record = True
    ok2, err2 = m2.start_record(b, info=info)
    out2 = m2.output_of(b)
    check("B站走 FLV 容器", ok2 and out2.endswith(".flv"), out2 or err2)
    check("B站走下载流引擎", isinstance(m2._tasks.get(b.key), LR.StreamDownloader),
          type(m2._tasks.get(b.key)).__name__)
    m2.stop_all()

    # 手动覆盖：抖音选 FLV → 下载流
    m3 = LR.RecordManager("douyin", {"outdir": d, "output_format": "FLV"})
    c = LR.Anchor(platform="douyin", room="3", name="覆盖测试")
    c._want_record = True
    ok3, err3 = m3.start_record(c, info=info)
    check("手动覆盖为 FLV", ok3 and m3.output_of(c).endswith(".flv"),
          m3.output_of(c) or err3)
    m3.stop_all()


# ---------------------------------------------------------------- 7. 真流断流重连
def test_live_flv_reconnect():
    """模拟 CDN 中途掐断连接：下载流应缝成「一条能干净解码的文件」。"""
    from toolbox.live_record import StreamDownloader, find_ffmpeg
    exe = find_ffmpeg()
    check("找到 ffmpeg（断流测试）", bool(exe), exe)
    if not exe:
        return
    d = tempfile.mkdtemp()
    src = os.path.join(d, "live.flv")
    subprocess.run(
        [exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x180:rate=25:duration=24", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=24", "-c:v", "libx264",
         "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-f", "flv", src],
        capture_output=True)
    total = os.path.getsize(src)

    # 每次请求只发一部分就掐断，模拟 CDN 断流（不在 tag 边界上断）
    plan = [0.31, 0.28, 0.22, 1.0, 0.0]
    state = {"i": 0}

    class Drop(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = open(src, "rb").read()
            frac = plan[min(state["i"], len(plan) - 1)]
            state["i"] += 1
            n = int(total * frac)
            if n == 0:          # 第 5 次起直接空响应：模拟「直播已结束」
                self.send_response(200)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/x-flv")
            self.send_header("Connection", "close")
            self.end_headers()
            step = 16 * 1024
            for i in range(0, n, step):
                try:
                    self.wfile.write(data[i:min(i + step, n)])
                    self.wfile.flush()
                except Exception:
                    return
                time.sleep(0.004)
            self.close_connection = True

    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Drop)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/live.flv" % port

    out = os.path.join(d, "rec.flv")
    dl = StreamDownloader(url, out, headers={"User-Agent": "t"}, base_name="rec", outdir=d)
    check("下载流启动", dl.start())
    t0 = time.time()
    while time.time() - t0 < 60 and dl.is_active():
        time.sleep(0.2)
    dl.stop()
    srv.shutdown()
    check("发生过重连", dl.reconnects() >= 2, "reconnects=%d" % dl.reconnects())

    tags = read_tags(out)
    check("单文件收录（没有碎成多个文件）",
          len([f for f in os.listdir(d) if f.endswith(".flv")]) == 2,
          str([f for f in os.listdir(d) if f.endswith(".flv")]))
    for ttype, label in ((9, "视频"), (8, "音频")):
        tss = [ts for t, ts, _ in tags if t == ttype]
        check("%s时间戳单调不减（无回退）" % label, tss == sorted(tss),
              "%d 帧 %d~%d" % (len(tss), tss[0] if tss else -1, tss[-1] if tss else -1))

    # 解码校验：有花屏就会报 decode error
    r = subprocess.run([exe, "-v", "error", "-i", out, "-f", "null", "-"],
                       capture_output=True)
    errs = [ln for ln in r.stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    check("整份文件能干净解码（无花屏/解码错误）", len(errs) == 0,
          " | ".join(errs[:3]))
    # 时长按 tag 时间戳算（FLV 头里的 onMetaData duration 是源文件的，不准）
    vts = [ts for t, ts, _ in tags if t == 9]
    span = (vts[-1] - vts[0]) / 1000.0 if vts else 0
    check("时长接上了（>40s，含 3 次接缝）", span > 40, "%.1fs" % span)


if __name__ == "__main__":
    print("== 输出格式默认值 ==")
    test_default_format()
    print("== 断流接缝（花屏）==")
    test_seam_writer()
    print("== 失步重同步 ==")
    test_resync()
    print("== FLV 修复器接缝对齐 ==")
    test_repair_seam()
    print("== 调度器按平台选格式/引擎 ==")
    test_manager_pick_engine()
    print("== ffmpeg TS 真实录制 ==")
    test_ffmpeg_ts()
    print("== 模拟 CDN 断流重连 ==")
    test_live_flv_reconnect()
    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("全部通过")
