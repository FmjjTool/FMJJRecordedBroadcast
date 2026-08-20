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

    # 清理
    try:
        os.remove(zip_path)
    except OSError:
        pass
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
