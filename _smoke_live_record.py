# -*- coding: utf-8 -*-
"""冒烟测试：直播录制内核 + 二级窗口 + 打赏榜口径 + 字幕当天筛选。

离屏运行：
  QT_QPA_PLATFORM=offscreen python _smoke_live_record.py
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 1. FLV 写入
def make_tag(payload, ts=0):
    hdr = b"\x09" + len(payload).to_bytes(3, "big") + ts.to_bytes(3, "big") + b"\x00" + b"\x00\x00\x00"
    return hdr + payload + (len(payload) + 11).to_bytes(4, "big")


def test_flv_writer():
    from toolbox.live_record import FlvWriter

    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.flv")
    w = FlvWriter(p)
    header = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00\x00\x00\x00"
    t1, t2 = make_tag(b"A" * 100, 0), make_tag(b"B" * 50, 40)
    blob = header + t1 + t2
    # 故意在 tag 中间切开喂入
    for i in range(0, len(blob), 17):
        w.feed(blob[i:i + 17])
    check("FLV 头 + 2 个 tag 完整落盘", os.path.getsize(p) == len(blob),
          f"{os.path.getsize(p)}/{len(blob)}")
    check("tag 计数正确", w.tags == 2, str(w.tags))
    # 只喂半个 tag：不应落盘
    w.feed(make_tag(b"C" * 30)[:20])
    check("半个 tag 不落盘", os.path.getsize(p) == len(blob))
    w.close()


# ---------------------------------------------------------------- 2. 打赏榜
class FakePage:
    """只实现 _add_gift_rank 依赖的属性。"""

    def __init__(self):
        self._gift_sessions = {}
        self._gift_rank = {}
        self.refresh_calls = 0

    def _refresh_gift_rank(self):
        self.refresh_calls += 1


def test_gift_rank():
    from datetime import datetime, timedelta
    from gui_qt.pages.test_page import TestPage

    page = FakePage()
    rank = TestPage._add_gift_rank.__get__(page, FakePage)
    t0 = datetime.now()

    # 会员/粉丝团礼物：price 缺失（0）也要按 1 抖币计入
    m = {"nickname": "李涵", "giftName": "点亮粉丝团", "giftCount": 1,
         "giftPrice": 0, "repeatEnd": 1}
    rank(m, t0)
    d = page._gift_rank["李涵"]
    check("点亮粉丝团计入打赏榜", d["giftCount"] == 1 and d["totalValue"] == 1,
          str(d))

    # 入团卡（截图里的「送出了入团卡 x1」）：同样缺价格 → 也要计入
    m0 = {"nickname": "小团子", "giftName": "入团卡", "giftCount": 1,
          "giftPrice": 0, "repeatEnd": 1}
    rank(m0, t0)
    d0 = page._gift_rank["小团子"]
    check("入团卡计入打赏榜", d0["giftCount"] == 1 and d0["totalValue"] == 1, str(d0))

    # 同一人 15 秒内再送一次 x1（无 repeatEnd）→ 不能被当成重推帧吞掉
    m2 = {"nickname": "李涵", "giftName": "粉丝团灯牌", "giftCount": 1,
          "giftPrice": 0}
    rank(m2, t0 + timedelta(seconds=2))
    rank(m2, t0 + timedelta(seconds=4))
    d = page._gift_rank["李涵"]
    check("15 秒内重复单发两次都计入", d["giftCount"] == 3 and d["totalValue"] == 3,
          str(d))

    # 连击累计帧：x7 → x19 → x34 只应结算 34
    page._gift_rank = {}
    page._gift_sessions = {}
    for cnt in (7, 19, 34):
        rank({"nickname": "甲", "giftName": "小心心", "giftCount": cnt,
              "giftPrice": 1}, t0)
    d = page._gift_rank["甲"]
    check("连击累计只结算最终值", d["giftCount"] == 34, str(d))

    # 捆绑礼物：67 包 × 66 抖币
    page._gift_rank = {}
    page._gift_sessions = {}
    rank({"nickname": "乙", "giftName": "66个小心心", "giftCount": 67,
          "giftPrice": 66}, t0)
    check("捆绑礼物价值 = 数量×整包价",
          page._gift_rank["乙"]["totalValue"] == 67 * 66,
          str(page._gift_rank["乙"]))


# ---------------------------------------------------------------- 2.5 实时记录恢复
def test_realtime_restore():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    class FakeApp:
        work_dir = tempfile.mkdtemp()

        def toast(self, *a, **k):
            pass

    from gui_qt.pages.test_page import TestPage
    p = TestPage(FakeApp())
    room = "888888"
    p._rt_open(room)
    p._record_cast("弹幕", "[甲]：你好",
                   {"type": "WebcastChatMessage", "nickname": "甲", "content": "你好"})
    p._record_cast("礼物", "[乙]：送出了入团卡 x1",
                   {"type": "WebcastGiftMessage", "nickname": "乙",
                    "giftName": "入团卡", "giftPrice": 0, "giftCount": 1})
    p._rt_close()
    # 模拟重启：清空内存状态，从文件恢复
    p._chat_log, p._session_casts, p._gift_rank = [], [], {}
    n = p._rt_load_tail(room)
    check("从实时记录恢复条数", n == 2, str(n))
    check("恢复后聊天区有内容", "你好" in p.chat_gift_box.toPlainText())
    check("恢复后打赏榜重算", p._gift_rank.get("乙", {}).get("totalValue") == 1,
          str(p._gift_rank))
    try:
        os.remove(p._rt_path)
    except Exception:
        pass


# ---------------------------------------------------------------- 2.6 FLV 修复
def test_flv_repair():
    from toolbox.flv_repair import repair_flv, probe_flv
    d = tempfile.mkdtemp()
    src = os.path.join(d, "broken.flv")
    header = b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00"
    t1 = make_tag(b"A" * 40, 0)
    t2 = make_tag(b"B" * 40, 1000)
    t3 = make_tag(b"C" * 40, 500)      # 时间戳回退（断流重连）
    t4 = make_tag(b"D" * 40, 12000)    # 时间戳大跳
    with open(src, "wb") as f:
        f.write(header + t1 + t2 + t3 + t4 + make_tag(b"E" * 40, 13000)[:25])
    st = repair_flv(src)
    check("FLV 修复成功", st["ok"] and st["tags"] == 4, str(st))
    check("尾部截断 tag 被丢弃", st["dropped"] == 1)
    check("时间戳回退/跳变被修正", st["ts_fixed"] >= 2, str(st["ts_fixed"]))
    # 校验输出：tag 时间戳单调不减
    tss = []
    with open(st["out"], "rb") as f:
        f.read(13)
        while True:
            hdr = f.read(11)
            if len(hdr) < 11:
                break
            size = int.from_bytes(hdr[1:4], "big")
            tss.append(int.from_bytes(hdr[4:7], "big") | (hdr[7] << 24))
            f.seek(size + 4, os.SEEK_CUR)
    check("修复后时间戳单调递增", tss == sorted(tss) and len(tss) == 4, str(tss))
    info = probe_flv(st["out"])
    check("修复后可正常探测", info["ok"] and info["tags"] == 4, str(info))


# ---------------------------------------------------------------- 2.7 WebHook
def test_webhook():
    from toolbox import webhook as WH
    body = WH.build_body("企业微信机器人", "hi")
    check("企微消息体", body.get("msgtype") == "text" and body["text"]["content"] == "hi")
    body2 = WH.build_body("飞书机器人", "hi")
    check("飞书消息体", body2.get("msg_type") == "text")
    check("非法地址被拦", WH.validate_url("ftp://x") != "")
    check("空地址视为未填", WH.validate_url("") == "未填写")
    p = WH.Pusher({"enabled": True, "url": ""})
    check("无地址不启用", p.enabled() is False)
    p2 = WH.Pusher({"enabled": True, "url": "http://127.0.0.1:9/x",
                    "events": {"开播": False}})
    check("事件开关生效", p2.want("开播") is False and p2.want("关播") is True)


# ---------------------------------------------------------------- 2.8 弹幕录制
def test_danmaku_recorder():
    from toolbox.danmaku_recorder import DanmakuRecorder
    d = tempfile.mkdtemp()
    # B站 XML
    rec = DanmakuRecorder("bili", "544820", d, "t1")
    rec._f = open(os.path.join(d, "t1.xml"), "w", encoding="utf-8")
    rec.path = os.path.join(d, "t1.xml")
    rec._f.write('<?xml version="1.0" encoding="UTF-8"?><i>')
    rec._on_msg("弹幕", "[甲]：你好", {"type": "DANMU_MSG", "ts": rec._start_ms + 1500,
                                      "nickname": "甲", "content": "你好"})
    rec._f.write("</i>")
    rec._f.close()
    xml = open(rec.path, encoding="utf-8").read()
    check("B站弹幕 XML 结构", xml.startswith("<?xml") and "<d p=\"1.500," in xml
          and "你好</d></i>" in xml, xml[:80])
    # 抖音 JSONL
    rec2 = DanmakuRecorder("douyin", "123", d, "t2")
    rec2.path = os.path.join(d, "t2.jsonl")
    rec2._f = open(rec2.path, "w", encoding="utf-8")
    rec2._on_msg("礼物", "[乙]：送出了入团卡 x1",
                 {"type": "WebcastGiftMessage", "ts": rec2._start_ms + 100,
                  "nickname": "乙", "content": "送出了入团卡 x1",
                  "giftName": "入团卡", "giftCount": 1, "giftPrice": 0})
    rec2._f.close()
    import json as _json
    line = _json.loads(open(rec2.path, encoding="utf-8").read().strip())
    check("抖音弹幕 JSONL 字段", line["user"]["name"] == "乙"
          and line["gift"]["name"] == "入团卡", str(line)[:120])


# ---------------------------------------------------------------- 2.9 已存账号选择器
def test_room_picker():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui_qt.room_picker import (RoomPickerDialog, load_local_entries,
                                    parse_account_url)
    check("直播间链接→房间号",
          parse_account_url("https://live.douyin.com/582775732015")[:2]
          == ("douyin", "582775732015"))
    check("B站直播间链接",
          parse_account_url("https://live.bilibili.com/544820")[:2] == ("bili", "544820"))
    check("B站空间→UID",
          parse_account_url("https://space.bilibili.com/1467772/video")[:3]
          == ("bili", "", "1467772"))
    check("抖音主页→sec_uid",
          parse_account_url("https://www.douyin.com/user/MS4wLjABAAAAx")[3]
          == "MS4wLjABAAAAx")
    entries = load_local_entries()
    check("能读到本机已存账号", len(entries) > 0, str(len(entries)))
    check("条目平台合法", all(e.platform in ("douyin", "bili") for e in entries))
    dlg = RoomPickerDialog(None, platform="douyin")
    check("选择器有列表", dlg.table.rowCount() >= 1, str(dlg.table.rowCount()))
    # 选中一条可直接用的并确认
    picked = None
    for i, e in enumerate(dlg._rows):
        if e.room:
            dlg.table.selectRow(i)
            picked = e
            break
    if picked:
        dlg._accept()
        check("选中后能取到房间号",
              dlg.entry is not None and dlg.entry.room == picked.room,
              str(getattr(dlg, "entry", None) and dlg.entry.room))
    else:
        check("选中后能取到房间号", False, "没有可用条目")
    dlg.close()


# ---------------------------------------------------------------- 3. 字幕页
def test_subtitle_page():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    class FakeApp:
        work_dir = tempfile.mkdtemp()

        def toast(self, *a, **k):
            pass

    from gui_qt.pages.subtitle import SubtitlePage
    page = SubtitlePage(FakeApp())
    check("字幕页有日期下拉", hasattr(page, "date_combo"))
    labels = [b.text() for b in page.findChildren(type(page.convert_btn))]
    check("字幕页有「当天」按钮", any("当天" in t for t in labels), str(labels[:8]))
    check("字幕页有「全部日期」按钮", any("全部日期" in t for t in labels))
    # 当天没有文件夹 → 不抛异常，给出提示
    page._scan_today()
    check("当天无目录时安全返回", "今天" in page.log_text.toPlainText()
          or "当天" in page.log_text.toPlainText())
    # 造一个今天的日期文件夹
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    d = os.path.join(FakeApp.work_dir, today)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "a.srt"), "w", encoding="utf-8").write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    page._refresh_date_combo()
    page._scan_today()
    check("当天目录只扫当天文件", len(page.input_files) == 1, str(page.input_files))


# ---------------------------------------------------------------- 4. 录制窗口
def test_live_record_window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui_qt.live_record_window import LiveRecordWindow
    from toolbox import live_record as LR

    # 隔离状态文件：冒烟测试不污染真实 config/live_record.json
    tmp_state = os.path.join(tempfile.mkdtemp(), "live_record.json")
    LR._state_path = lambda: __import__("pathlib").Path(tmp_state)

    win = LiveRecordWindow(None, platform="douyin", room="123456")
    win.show()
    pane = win.panes["douyin"]
    check("带入房间号", any(a.room == "123456" for a in pane.manager.anchors))
    a = LR.Anchor(platform="douyin", room="999", name="测试主播",
                  monitoring=True, live=True, online=1234, quality="原画")
    a._want_record = True
    pane.manager.anchors.append(a)
    pane.model.refresh_all()
    check("表格行数", pane.model.rowCount() == 2)
    idx = pane.model.index(1, 2)
    check("主播名称列渲染", pane.model.data(idx) == "测试主播", str(pane.model.data(idx)))
    check("直播状态列渲染",
          pane.model.data(pane.model.index(1, 3)) == "已开播")
    win.switch_platform("bili")
    check("底部切换到 B站", win.stack.currentWidget() is win.panes["bili"])
    win.switch_platform("douyin")
    # 设置对话框（不 exec，只构造检查控件）
    from gui_qt.live_record_window import RecordSettingsDialog
    dlg = RecordSettingsDialog(pane, pane.manager)
    check("录制设置含分段模式", dlg.cb_seg.count() == 3)
    check("录制设置含录制方式", dlg.cb_mode.count() == 2)
    check("录制设置含标题过滤", hasattr(dlg, "ed_title_filter"))
    check("二级窗口有菜单栏", win.menuBar() is not None and len(win.menuBar().actions()) >= 4,
          str([a.text() for a in win.menuBar().actions()]))
    # 右键菜单：参考项目原版项目是否齐全
    need = ["当前主播_立即检测开播", "当前主播_播放直播源", "当前主播_加入监控",
            "当前主播_退出监控", "当前主播_加入录制", "当前主播_退出录制",
            "当前主播_修改录制多少分钟停止录制快捷值",
            "当前主播_已录时间归零重新录制", "当前主播_独立设置功能",
            "当前主播_移动至序号", "当前主播_修改备注信息", "当前主播_复制直播源",
            "当前主播_复制直播网址", "当前主播_复制主播整体数据",
            "当前主播_删除当前选择", "当前主播_打开当前视频保存目录",
            "当前主播_打开记录数据文件夹", "全部主播_复制直播网址",
            "列表拉篮选择_勾选", "列表拉篮选择_不选", "列表拉篮选择_反选",
            "当前主播_运行日志"]
    got = [a.text() for a in pane._build_context_menu().actions()]
    missing = [n for n in need if n not in got]
    check("右键菜单项目齐全", not missing, str(missing))
    batch = [a.text() for a in pane._build_batch_menu().actions()]
    need2 = ["列表勾选_加入监控", "列表勾选_退出监控", "列表勾选_加入录制",
             "列表勾选_退出录制", "列表勾选_修改录制多少分钟停止录制",
             "列表勾选_分段录制每段几分钟设置", "列表勾选_录制清晰度与优先顺序",
             "列表勾选_录制视频(有视频有声)", "列表勾选_录制音频(无视频有声)",
             "列表勾选_已录时间归零", "列表勾选_已录时间归零重新录制",
             "列表勾选_修改备注", "列表勾选_提醒与记录设置",
             "列表勾选_删除主播(不删除录制数据)"]
    check("批量修改菜单齐全", not [n for n in need2 if n not in batch],
          str([n for n in need2 if n not in batch]))
    # 独立设置：清晰度可手动选、分段/录满可填
    from gui_qt.live_record_window import AnchorSettingsDialog
    dlg2 = AnchorSettingsDialog(pane, a, pane.manager)
    check("独立设置清晰度可手动选", dlg2.cb_q.count() >= 6, str(dlg2.cb_q.count()))
    check("独立设置含录制内容", dlg2.cb_content.count() == 2)
    check("独立设置含提醒开关", len(dlg2.notify_boxes) == 5)
    check("独立设置录满可编辑", dlg2.sp_stop.isEnabled() and dlg2.sp_stop.maximum() == 1440)
    check("表格含录制n分钟/录制内容列",
          pane.model.columnCount() == 15 and pane.model.data(pane.model.index(1, 6)) == "不限"
          and pane.model.data(pane.model.index(1, 8)) == "视频",
          f"{pane.model.columnCount()} / {pane.model.data(pane.model.index(1, 6))}")
    # 状态保存
    win.save_state()
    st = LR.load_state()
    check("状态已落盘", "douyin" in (st.get("platforms") or {}))
    win._timer.stop()
    win.close()


def test_manager_state_roundtrip():
    from toolbox import live_record as LR
    m = LR.RecordManager("bili", {"interval": 15, "mode": "录制流"})
    m.load_dict({"settings": {"interval": 20},
                 "anchors": [{"room": "544820", "name": "A", "monitoring": True,
                              "want_record": True, "quality": "10000"}]})
    check("设置合并", m.settings["interval"] == 20 and m.settings["mode"] == "下载流")
    check("主播载入", len(m.anchors) == 1 and m.anchors[0].platform == "bili")
    check("重启后无活动录制", m.anchors[0].recording is False)
    check("want_record 恢复", getattr(m.anchors[0], "_want_record", False) is True)


if __name__ == "__main__":
    print("== FLV 写入 ==")
    test_flv_writer()
    print("== 打赏榜口径 ==")
    test_gift_rank()
    print("== FLV 修复 ==")
    test_flv_repair()
    print("== WebHook ==")
    test_webhook()
    print("== 弹幕录制 ==")
    test_danmaku_recorder()
    print("== 已存账号选择器 ==")
    test_room_picker()
    print("== 实时记录恢复 ==")
    test_realtime_restore()
    print("== 字幕当天筛选 ==")
    test_subtitle_page()
    print("== 直播录制窗口 ==")
    test_live_record_window()
    print("== 录制状态持久化 ==")
    test_manager_state_roundtrip()
    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("全部通过")
