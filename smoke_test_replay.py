# -*- coding: utf-8 -*-
"""回放功能离线冒烟测试（不需要真实直播间）。

覆盖：
  1. 回放包 zip 解析（data.json / recording.flv / recording_meta.json / cover）
  2. 双格式消息归一化（dycast DyMessage 与原始 type/ts 格式）
  3. 回放启动 / 心跳渲染 / 进度推进
  4. 进度条快进（打赏榜全量重建 + 面板末尾渲染 + 视频对位）
  5. 暂停 / 继续 / 倍速
  6. 弹幕快照记录（_record_cast → dycast 导出格式）
  7. 页面生命周期（on_hide 全停 / on_show 自动重连）
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication

APP = QApplication([])
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class FakeApp:
    """替身 MainWindow：只提供 toast / export_dir。"""
    toasts = []

    def toast(self, msg, level="info"):
        self.toasts.append((level, msg))
        print(f"  [toast/{level}] {msg}")

    export_dir = tempfile.mkdtemp(prefix="replay_test_export_")


def make_test_flv():
    """生成 12 秒测试视频（无则用 ffmpeg 合成，仓库不存二进制）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_replay.flv")
    if not os.path.exists(path):
        from toolbox.live_video import find_ffmpeg
        exe = find_ffmpeg()
        if not exe:
            return None
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=duration=12:size=320x180:rate=10",
             "-pix_fmt", "yuv420p", "-y", path], check=True)
    return path if os.path.exists(path) else None


def make_zip(path, casts, with_video=True, rec_meta=None):
    bundle = {"_bundle": True, "exportTime": "2026-08-20T00:00:00",
              "roomId": "123456", "nickname": "测试主播", "title": "回放测试",
              "casts": casts}
    # 1x1 红色 JPEG 头（仅验证字节透传，不要求可解码）
    cover = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("data.json", json.dumps(bundle, ensure_ascii=False))
        z.writestr("cover.jpg", cover)
        if rec_meta:
            z.writestr("recording_meta.json", json.dumps(rec_meta))
        if with_video:
            src = make_test_flv()
            if src:
                z.write(src, "recording.flv")


def main():
    from gui_qt.pages.test_page import TestPage

    page = TestPage(FakeApp())
    page.video_label.resize(640, 360)

    # ---------- 造数据：dycast DyMessage 格式，10 秒 21 条 ----------
    base_ts = 1755648000000  # 固定基准，回放时间戳应原样显示
    casts = []
    for i in range(11):
        casts.append({"method": "WebcastChatMessage", "timestamp": base_ts + i * 1000,
                      "user": {"name": f"观众{i}"}, "content": f"第{i}条弹幕"})
    for i in range(5):
        casts.append({"method": "WebcastGiftMessage", "timestamp": base_ts + 2000 + i * 1000,
                      "user": {"name": "土豪"}, "content": "",
                      "gift": {"name": "小心心", "count": i + 1, "price": 100, "icon": ""}})
    casts.append({"method": "WebcastLikeMessage", "timestamp": base_ts + 8000,
                  "user": {"name": "点赞党"}, "content": "点赞"})
    casts.append({"method": "WebcastMemberMessage", "timestamp": base_ts + 9000,
                  "user": {"name": "新观众"}, "content": "进入直播间"})
    casts.append({"method": "WebcastChatMessage", "timestamp": base_ts + 10000,
                  "user": {"name": "最后"}, "content": "结束"})
    # 原始格式一条（导出旧格式兼容）
    casts.append({"type": "WebcastChatMessage", "ts": base_ts + 5000,
                  "nickname": "旧格式", "content": "原始格式消息"})

    tmpdir = tempfile.mkdtemp(prefix="replay_zip_")
    zip_path = os.path.join(tmpdir, "回放测试.zip")
    # 录制起点比第一条弹幕早 5 秒 → 视频基准偏移应为 5 秒
    make_zip(zip_path, casts, rec_meta={"startTimestamp": base_ts - 5000,
                                        "endTimestamp": base_ts + 11000,
                                        "file": "recording.flv"})

    print("== 1. zip 解析 ==")
    data = page._parse_replay_file(zip_path)
    check("解析成功", data is not None)
    check("casts 数量", data and len(data["casts"]) == 20, f"got {len(data['casts']) if data else 0}")
    check("视频已解压", data and data.get("video") and os.path.exists(data["video"]))
    check("录制元数据", data and (data.get("rec_meta") or {}).get("startTimestamp") == base_ts - 5000)
    check("封面字节", data and data.get("cover"))
    check("bundle 主播", data and data["bundle"].get("nickname") == "测试主播")

    print("== 2. 消息归一化 ==")
    m = TestPage._norm_cast(casts[0])
    check("dycast→内部 type", m.get("type") == "WebcastChatMessage")
    check("dycast→内部 ts", m.get("ts") == base_ts)
    check("dycast→内部 nickname", m.get("nickname") == "观众0")
    m2 = TestPage._norm_cast(casts[-1])
    check("原始格式透传", m2.get("type") == "WebcastChatMessage" and m2.get("ts") == base_ts + 5000)
    m3 = TestPage._norm_cast(casts[11])
    check("礼物文案补全", "小心心" in (m3.get("content") or ""))
    check("礼物字段映射", m3.get("giftName") == "小心心" and m3.get("giftPrice") == 100)

    print("== 3. 回放启动 ==")
    page.mode = "replay"
    page._apply_mode()
    page._start_replay(data)
    r = page._replay
    check("回放状态建立", r is not None)
    check("总时长 = last-first", r and r["total_ms"] == 10000, f"got {r['total_ms'] if r else '-'}")
    check("首条时间戳", r and r["first"] == base_ts)
    check("进度条范围", page.replay_slider.maximum() == 10000)
    check("主播信息恢复", page.anchor_fields["anchor_name"].text() == "测试主播")
    check("房间号恢复", page.room_entry.text() == "123456")
    check("视频基准偏移 5s", abs(page._video_base_sec() - 5.0) < 0.001,
          f"got {page._video_base_sec()}")
    check("视频播放器启动", page.video_player is not None and page.video_player.is_running())
    check("弹幕框清空", page.chat_gift_box.toPlainText() == "")

    print("== 4. 心跳渲染（模拟推进到 3 秒） ==")
    r["start_real"] = time.time() - 3.0  # 回拨：等效已播 3 秒
    page._replay_tick()
    check("渲染至 3s（弹幕 0-3 + 礼物@2s）", r["idx"] >= 4, f"idx={r['idx']}")
    check("打赏榜有记录", page.gift_table.rowCount() >= 1)
    check("进度条 = 3s", page.replay_slider.value() >= 2900 and page.replay_slider.value() <= 3100,
          f"got {page.replay_slider.value()}")
    check("时间标签", page.replay_time_lbl.text().startswith("00:03"), page.replay_time_lbl.text())
    body = page.chat_gift_box.toPlainText()
    check("弹幕原文时间显示", "观众0" in body and "第0条弹幕" in body)

    print("== 5. 快进到 6 秒（榜全量重建 + 视频对位） ==")
    page._seek_replay(6000)
    check("seek 后 idx 对位", r["idx"] >= 8, f"idx={r['idx']}")
    # 到 6s 的礼物：2000+i*1000（i=0..4，ts=2..6s）五条全含 → 土豪 count 累计 1+2+3+4+5=15
    check("打赏榜礼物数累计", page.gift_table.rowCount() == 1
          and page.gift_table.item(0, 2).text() == "15",
          f"rows={page.gift_table.rowCount()}")
    check("打赏榜价值", page.gift_table.item(0, 3).text() == "1,500",
          page.gift_table.item(0, 3).text() if page.gift_table.rowCount() else "-")
    check("视频重启到 5+6=11s", page.video_player is not None
          and abs(page.video_player._start_sec - 11.0) < 0.001,
          f"got {page.video_player._start_sec if page.video_player else '-'}")
    check("seek 后面板含旧格式", "旧格式" in page.chat_gift_box.toPlainText())
    check("seek 后面板不含 6s 后消息", "结束" not in page.chat_gift_box.toPlainText())
    check("seek 后 offset 重置", abs(r["offset_ms"] - 6000) < 0.001)

    print("== 6. 暂停 / 继续 / 倍速 ==")
    page._pause_replay()
    check("暂停标志", r["paused"] and r["paused_at"] >= 6000)
    check("暂停停视频", page.video_player is None)
    page._resume_replay()
    check("继续后恢复播放", not r["paused"] and page.video_player is not None)
    page.replay_speed.setCurrentIndex(2)  # 4.0x → 触发 _on_replay_speed_changed
    check("倍速切换", abs(r["speed"] - 4.0) < 0.001, f"got {r['speed']}")
    r["start_real"] = time.time() - 1.0   # 4x 播 1 秒 → 前进 4 秒
    page._replay_tick()
    check("4x 推进到 ~10s（结束触发回放完成）", r["paused"] and abs(r["paused_at"] - 10000) < 1,
          f"paused_at={r.get('paused_at')}")
    check("全部渲染完", r["idx"] == len(r["casts"]), f"idx={r['idx']}/{len(r['casts'])}")

    print("== 7. 弹幕快照（直播录制 → dycast 导出格式） ==")
    page._stop_replay()
    check("停止清理临时目录", r.get("zip_dir") and not os.path.exists(r["zip_dir"]))
    page._record_cast("礼物", "[土豪]：送出 火箭 x1",
                      {"type": "WebcastGiftMessage", "ts": base_ts, "nickname": "土豪",
                       "giftName": "火箭", "giftCount": 1, "giftPrice": 1000})
    page._record_cast("弹幕", "[路人]：你好",
                      {"type": "WebcastChatMessage", "ts": base_ts + 100,
                       "nickname": "路人", "content": "你好"})
    c0 = page._session_casts[0]
    check("method 字段", c0["method"] == "WebcastGiftMessage")
    check("timestamp 字段", c0["timestamp"] == base_ts)
    check("user.name 字段", c0["user"]["name"] == "土豪")
    check("gift 字段", c0["gift"]["name"] == "火箭" and c0["gift"]["price"] == 1000)
    # 导出格式可直接再被回放解析（往返）
    rt = os.path.join(tmpdir, "roundtrip.zip")
    make_zip(rt, page._session_casts)
    data2 = page._parse_replay_file(rt)
    check("导出格式可再解析（往返）", data2 is not None and len(data2["casts"]) == 2)
    os.remove(rt)

    print("== 8. 页面生命周期 ==")
    calls = []
    page._connect = lambda: calls.append("connect")
    page.on_show()
    check("未断开过 → 不自动连接", calls == [])
    page._auto_stopped = True
    page.room_entry.setText("6096197105")
    page.on_show()
    check("切回自动重连", calls == ["connect"])
    # on_hide 全停
    page.connected = True
    page.video_player = None
    page.fetcher = None
    page._recording = False
    stopped = []
    page._disconnect = lambda: stopped.append("disconnect")
    page.on_hide()
    check("切走全停（断开）", stopped == ["disconnect"] and page._auto_stopped)
    page.connected = False
    page.on_hide()
    check("空闲切走不误标记", not page._auto_stopped)

    print("== 9. json 回放包（无视频） ==")
    json_path = os.path.join(tmpdir, "session.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"casts": casts[:3], "savedAt": "x"}, f, ensure_ascii=False)
    data3 = page._parse_replay_file(json_path)
    check("json 包解析", data3 is not None and len(data3["casts"]) == 3)
    page._start_replay(data3)
    check("json 包回放（无视频）", page._replay is not None and page.video_player is None)
    page._stop_replay()

    print("== 10. 四模式切换（含仅弹幕模式） ==")
    page._recording = False
    for mode, video_h, chat_h in (("normal", False, False), ("video_only", False, True),
                                  ("danmaku_only", True, False), ("replay", False, False)):
        page.mode = mode
        page._apply_mode()
        ok = page.video_card.isHidden() == video_h and page.chat_card.isHidden() == chat_h
        check(f"模式 {mode} 布局", ok)
    page.mode = "danmaku_only"
    page._apply_mode()
    check("仅弹幕：隐藏录播控制行", page.live_ctrl_row_widget.isHidden())

    print("== 11. 清晰度排序 + 默认最高 + 手动切换 ==")
    page._start_live_preview = lambda: None  # 防真实拉流
    streams = {"SD1": "url_sd", "_origin": "url_origin", "HD1": "url_hd"}
    page._quality_manual = False
    page._on_room_streams(dict(streams))
    check("默认最高流（原画优先）", page.quality_combo.currentData() == "_origin")
    check("录制流 = 最高流", page._current_stream == "url_origin")
    check("排序：原画第一", page.quality_combo.itemText(0) == "原画")
    page.quality_combo.setCurrentIndex(page.quality_combo.findData("HD1"))
    check("手动切换标记", page._quality_manual)
    check("录制流跟随手动切换", page._current_stream == "url_hd")
    page._on_room_streams(dict(streams))
    check("重连保持手动选择", page.quality_combo.currentData() == "HD1")
    page._quality_manual = False
    page._on_room_streams(dict(streams))
    check("重置后回到最高", page.quality_combo.currentData() == "_origin")

    print("== 12. 轮询（按间隔反复访问直播间） ==")
    page.poll_var.setChecked(True)
    page.connected = True
    page.settings["poll_interval"] = 90
    page._setup_poll_timer()
    check("轮询定时器创建", page._poll_timer is not None and page._poll_timer.isActive())
    check("轮询间隔 90s", page._poll_timer is not None and page._poll_timer.interval() == 90000)
    seq = []
    page._disconnect = lambda: (seq.append("disc"), setattr(page, "connected", False))
    page._connect = lambda: (seq.append("conn"), setattr(page, "connected", True))
    page._poll_reconnect()
    check("轮询重访顺序 断开→重连", seq == ["disc", "conn"])
    check("轮询标志复位", page._polling is False)
    page.poll_var.setChecked(False)
    check("取消轮询 → 定时器停", page._poll_timer is None)

    print("== 13. 自动导出回放包（断开/退出时默认 zip 而非 txt） ==")
    page.connected = False
    page.room_entry.setText("6096197105")
    page.anchor_fields["anchor_name"].setText("自动导出")
    page._cover_bytes = None
    page._session_casts = []
    check("无弹幕数据不导出", page._auto_save_danmaku() is None)
    page._session_casts = list(casts)
    path = page._auto_save_danmaku()
    check("自动导出为回放包 zip", bool(path) and os.path.exists(path) and path.endswith(".zip"))
    check("保存到 回放包 目录", "回放包" in path)
    if path:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        check("含 data.json + 全量弹幕.json", "data.json" in names and "全量弹幕.json" in names)
        check("含分类 TXT（不只一个 txt）", any(n.endswith(".txt") for n in names))
        os.remove(path)

    print("== 14. 导出 dycast 全量包结构 ==")
    import gui_qt.pages.test_page as tp_mod
    orig_run_bg = tp_mod.run_bg
    tp_mod.run_bg = lambda work, done=None: (done or (lambda r, e=None: None))(work())
    page._session_casts = list(casts)
    page._cover_bytes = None
    page.anchor_fields["anchor_name"].setText("测试主播")
    page._export_replay_zip(quiet=True)
    from toolbox.douyin_live import _danmaku_dir as _dd
    out_dir = _dd().parent / "回放包"
    zips = sorted(out_dir.glob("测试主播-*.zip"))
    check("导出 zip 生成", len(zips) >= 1)
    if zips:
        with zipfile.ZipFile(zips[-1]) as z:
            names = set(z.namelist())
            check("含 data.json", "data.json" in names)
            check("含 全量弹幕.json", "全量弹幕.json" in names)
            check("含分类TXT（弹幕/礼物/进入）",
                  "弹幕.txt" in names and "礼物.txt" in names and "进入.txt" in names)
            b = json.loads(z.read("data.json").decode("utf-8"))
            check("_bundle 标记（dycast 可导入）", b.get("_bundle") is True)
            check("casts 数量", len(b.get("casts", [])) == 20)
            txt = z.read("弹幕.txt").decode("utf-8")
            check("TXT 行格式 dycast 同款", "|[观众0]：第0条弹幕" in txt)
        os.remove(zips[-1])
    tp_mod.run_bg = orig_run_bg

    print("== 15. 录制中切页面保持后台运作 ==")
    seq2 = []

    class FakeFetcher:
        def stop(self):
            seq2.append("fetcher_stop")

    page._recording = True
    page.fetcher = FakeFetcher()
    page.connected = True
    page.video_player = None
    page._stop_recording = lambda: seq2.append("stop_rec")
    page._disconnect = lambda: seq2.append("disc")
    page.on_hide()
    check("录制中切走：抓取/录制继续", seq2 == [] and page.fetcher is not None and page.connected)
    check("录制中切走：不标记自动重连", not page._auto_stopped)
    page.shutdown()
    check("软件退出：录制停止+断开", seq2 == ["stop_rec", "disc"])
    page._recording = False
    page.fetcher = None
    page.connected = False

    print("== 16. dycast 进程树（Job Object 父关子关） ==")
    from gui_qt.pages.dycast import DycastPage
    from gui_qt.process_tree import create_kill_job, terminate_job
    job = create_kill_job()
    check("Job Object 创建", bool(job))
    if job:
        check("Job 终止+释放", terminate_job(job))
    dp = DycastPage(FakeApp())
    check("链接进程树默认勾选", dp.link_tree_var.isChecked())
    check("启动时停止按钮禁用", not dp.stop_btn.isEnabled())

    class FakeProc:
        pid = 99999

        @staticmethod
        def poll():
            return None

    dp._proc = FakeProc()
    dp.on_show()  # 切回页面同步按钮状态
    FakeApp.toasts.clear()
    dp._launch()
    check("单实例拒绝二次启动", any(l == "warning" for l, _ in FakeApp.toasts))
    check("运行中停止按钮可用", dp.stop_btn.isEnabled())
    dp._proc = None
    dp._job = None
    dp.shutdown()
    check("shutdown 清理安全", dp._proc is None and dp._job is None)

    print("== 17. 手动断开彻底停止（修复：断开后还会继续） ==")
    from PySide6.QtWidgets import QLabel, QPushButton
    fp = TestPage(FakeApp())
    fp.connected = True
    fp._timer_running = True
    fp._auto_stopped = True   # 之前切走页面被自动断开过

    class FakeStop:
        def __init__(self):
            self.calls = []

        def stop(self):
            self.calls.append("stop")

    fetcher_spy = FakeStop()

    class FakePlayer2:
        def stop(self):
            fetcher_spy.calls.append("player")

    fp.fetcher = fetcher_spy
    fp.video_player = FakePlayer2()
    fp._polling = False
    fp._disconnect()
    check("断开停抓取线程", fetcher_spy.calls[:1] == ["stop"]
          and "player" in fetcher_spy.calls and fp.fetcher is None)
    check("断开停视频预览", fp.video_player is None)
    check("断开重置自动重连标记", fp._auto_stopped is False)
    calls3 = []
    fp._connect = lambda: calls3.append("connect")
    fp.on_show()
    check("手动断开后切回页面不自动重连", calls3 == [])
    # 文字修正 + 按钮移除
    all_text = [l.text() for l in fp.findChildren(QLabel)] + \
               [b.text() for b in fp.findChildren(QPushButton)]
    check("错字修正为「歆慕」", any("歆慕" in t for t in all_text)
          and not any("欲墓" in t for t in all_text))
    check("「Cookie 登录」按钮已移除", not any("Cookie 登录" in t for t in all_text))
    check("「登录抖音」按钮已移除", not any("登录抖音" in t for t in all_text))

    print("== 18. 轮询重连竞态防护（旧实例回调隔离） ==")
    import toolbox.douyin_live as dl_mod

    class FakeFetcher:
        def __init__(self, room, on_message=None, on_room_info=None, on_status=None):
            self.on_status = on_status
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    orig_cls = dl_mod.DouyinLiveFetcher
    dl_mod.DouyinLiveFetcher = FakeFetcher
    try:
        fp2 = TestPage(FakeApp())
        fp2.save_stats_var.setChecked(False)
        fp2.poll_var.setChecked(False)
        fp2.room_entry.setText("123")
        fp2._connect()
        f1 = fp2.fetcher
        f1.on_status("connected", "已连接 · 房间 123")
        check("新实例连接状态生效", fp2.connected is True
              and fp2.status_label.text().startswith("已连接"))
        # 轮询重连：旧实例收尾回调不得影响新连接
        fp2._polling = True
        fp2._connect()
        f2 = fp2.fetcher
        check("重连产生新实例", f2 is not f1)
        f1.on_status("disconnected", "已停止")
        check("旧实例 disconnected 被忽略", fp2.connected is True)
        check("状态不被旧回调改写", fp2.status_label.text() != "已停止")
        f2.on_status("disconnected", "已停止")
        check("新实例 disconnected 正常生效", fp2.connected is False)
        fp2.shutdown()
    finally:
        dl_mod.DouyinLiveFetcher = orig_cls

    print("== 19. 继续操作页：跟随进程树（父关子关） ==")
    from gui_qt.pages.launcher import LauncherPage

    def has_ping():
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PING.EXE"],
                             capture_output=True, text=True).stdout
        return "PING.EXE" in out

    lp = LauncherPage(FakeApp())
    check("跟随进程树默认开启", lp.link_tree_var.isChecked())
    bat = os.path.join(tmpdir, "tree_ping.bat")
    with open(bat, "w") as f:
        f.write("@echo off\r\nping -n 60 127.0.0.1 >nul\r\n")
    lp._launch(bat)
    check("启动纳入进程树", len(lp._linked) == 1 and lp._linked[0][0] is not None)
    time.sleep(2)
    check("子进程运行中", has_ping())
    lp._stop_all_linked()
    time.sleep(1)
    check("停止后进程树全灭（父关子关）", not has_ping())
    check("链接列表清空", len(lp._linked) == 0)
    # 退出兜底
    lp._launch(bat)
    lp.shutdown()
    time.sleep(1)
    check("软件退出进程树全灭", not has_ping())

    # 清理
    try:
        os.remove(zip_path)
    except OSError:
        pass
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
