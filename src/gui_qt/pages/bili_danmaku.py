# -*- coding: utf-8 -*-
"""B站弹幕姬页：完整复用抖音弹幕姬（TestPage）的 UI 与交互框架，仅替换平台后端。

- 后端：toolbox.bili_live（BiliLiveFetcher 弹幕 WS / 房间信息 / 直播流 / 统计 / 监控）
- 画质：B站按 qn 档位现取直链（游客会被限档，切档即按该档重新取流）
- 与抖音页的差异消息：粉丝数实时刷新（ROOM_REAL_TIME_MESSAGE_UPDATE）、
  累计点赞静默更新（LIKE_INFO_V3_UPDATE）、SC/上舰并入打赏榜（价值按 1000电池=1元 折算）
"""

from toolbox.bili_live import DEFAULT_ROOM, fetch_play_url, parse_room_id_input
from gui_qt.common import run_bg
from gui_qt.pages.test_page import TestPage


class BiliDanmakuPage(TestPage):
    _BACKEND = "toolbox.bili_live"

    HELP_INTRO = (
        "<p><b>B站直播弹幕姬：完整复用抖音弹幕姬框架，接B站直播间实时数据。</b></p>"
        "<p>· <b>连接</b>：房间号或直播间链接（链接自动提取房号，短号合法）；"
        "留空自动填默认房间号，▶ 连接</p>"
        "<p>· <b>实时数据</b>：弹幕 / 礼物 / SC / 上舰 / 进场 / 关注 / 点赞；"
        "主播粉丝数实时刷新，累计点赞静默更新；打赏榜价值按 1000电池=1元 折算</p>"
        "<p>· <b>四种模式</b>：正常（视频+弹幕）/ 仅视频 / 仅弹幕 / 回放</p>"
        "<p>· <b>视频与录制</b>：直播预览 + 画质切换（按 qn 档位现取直链，游客可能被限档，"
        "切档即按该档重新取流）；⏺ 手动/自动录播，切页自动断开重连</p>"
        "<p>· <b>🔁 轮询等开播</b>：未开播自动等待，开播自动接入；下播后开关开着则继续轮询</p>"
        "<p>· <b>搜索</b>：弹幕关键字/按人筛选，事件区（点赞/进入/关注/横幅/系统）独立筛选</p>"
        "<p>· <b>保存与回放</b>：💾 保存弹幕、📊 保存统计；📦 导出/导入/合并回放包</p>"
        "<p>· <b>⚙ 设置</b>：深色模式、保持滚动、自动导出回放包等</p>"
        "<p>· 弹幕数据游客优先获取，不占用 Cookie</p>"
    )

    # 回放包分类 TXT（B站消息类型）
    CAST_TXT_TYPES = (
        ("DANMU_MSG", "弹幕"),
        ("DANMU_MSG_EMOJI", "弹幕"),
        ("SEND_GIFT", "礼物"),
        ("SUPER_CHAT_MESSAGE", "礼物"),
        ("GUARD_BUY", "礼物"),
        ("INTERACT_WORD_FOLLOW", "关注"),
        ("LIKE_INFO_V3_CLICK", "点赞"),
        ("INTERACT_WORD", "进入"),
        ("INTERACT_WORD_V2", "进入"),
        ("ENTRY_EFFECT", "进入"),
    )

    def __init__(self, app):
        self._bili_qn_list = []     # [(qn, desc)] 画质档位表（连接时由后端带回）
        super().__init__(app)
        # 平台文案与单位
        self.title_lbl.setText("📺 B站弹幕 · 实时数据接入")
        self.subtitle_lbl.setText(
            "真实连接B站直播间，实时抓取弹幕 / 礼物 / SC / 上舰 / 进场 / 关注 / 点赞")
        self.gift_table.setHorizontalHeaderLabels(
            ["排名", "昵称", "礼物数", "价值(电池)", "更新时间"])
        # 房间号：B站短号合法（抖音页的 ≥6 位校验不适用），支持粘贴直播间链接。
        # 上次房号 <4 位（如测试残留"6"）不回填：留空走 placeholder + 默认 544820
        self.room_entry.setPlaceholderText(f"房间号或直播间链接，如 {DEFAULT_ROOM}")
        room = str(self.settings.get("last_room", "") or "")
        self.room_entry.setText(room if room.isdigit() and len(room) >= 4 else "")

    # ======================== 平台差异覆写 ========================
    def _default_room(self):
        room = str(self.settings.get("last_room", "") or "")
        if room.isdigit() and len(room) >= 4:
            return room
        return DEFAULT_ROOM

    def _rec_dir(self):
        from toolbox.config_manager import get_export_dir
        return get_export_dir() / "直播录制" / "B站弹幕姬"

    def _connect(self):
        # 支持直接粘贴 live.bilibili.com 链接（自动提取房间号）
        text = self.room_entry.text().strip()
        if text:
            parsed = parse_room_id_input(text)
            if parsed != text:
                self.room_entry.setText(parsed)
        super()._connect()

    def _on_room_info(self, info):
        """房间信息：先记下画质档位表，其余交给基类。"""
        if isinstance(info, dict) and info.get("qn_list"):
            self._bili_qn_list = list(info["qn_list"])
        super()._on_room_info(info)

    def _on_room_streams(self, streams):
        """B站画质：下拉=qn_list（desc→qn 档位），流地址用当前已取的直链。

        游客会被限制到最高可看档（请求高画质返回实际档位），切档时按选中
        qn 现取新直链（见 _on_quality_changed），不预取全部档位。
        """
        self._streams = streams or {}
        qn_list = self._bili_qn_list or []
        prev_qn = self.quality_combo.currentData() if self._quality_manual else None
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for qn, desc in qn_list:
            self.quality_combo.addItem(desc or str(qn), qn)
        if not qn_list and self._streams:
            for desc in self._streams:
                self.quality_combo.addItem(desc, None)
        if prev_qn is not None:
            idx = self.quality_combo.findData(prev_qn)
            if idx >= 0:
                self.quality_combo.setCurrentIndex(idx)
        self.quality_combo.blockSignals(False)
        if self._streams:
            self._current_stream = next(iter(self._streams.values()))
        if self._streams and self.mode in ("normal", "video_only"):
            self._start_live_preview()
        # 自动录制：勾选「自动录播」或切页返回后待重开 → 拿到流立即录
        if (self._streams and self.mode != "replay" and not self._recording
                and (self.settings.get("auto_record", False) or self._rec_resume_pending)):
            self._rec_resume_pending = False
            self._start_recording()

    def _on_quality_changed(self, _index=None):
        """手动切画质 → 按选中 qn 现取直链（游客可能被降档），再重启预览。"""
        self._quality_manual = True
        qn = self.quality_combo.currentData()
        room = self.room_entry.text().strip()
        if qn is None or not room or not str(qn).isdigit():
            # 无档位信息：沿用当前已取的流
            if self._current_stream and self.mode in ("normal", "video_only"):
                self._start_live_preview()
            return

        def work():
            return fetch_play_url(room, int(qn))

        def done(play, err=None):
            if err:
                self.app.toast(f"取流失败：{err}", "warning")
                return
            if play and play.get("flv"):
                self._current_stream = play["flv"]
                self._streams = {play.get("quality_desc") or "直播流": play["flv"]}
                if self.mode in ("normal", "video_only"):
                    self._start_live_preview()
                self.video_status_lbl.setText(f"画质：{play.get('quality_desc') or ''}")
            else:
                self.app.toast(
                    f"取流失败：{(play or {}).get('error') or '无可用流'}", "warning")

        run_bg(work, done)

    def _start_live_preview(self):
        """B站预览：直接用当前直链（画质切换时已换新地址）。"""
        if not self._current_stream or self.mode == "replay":
            return
        from toolbox.live_video import VideoStreamPlayer
        if self.video_player:
            self.video_player.stop()
        self.video_player = VideoStreamPlayer(
            on_frame=self._on_video_frame,
            on_status=self._on_video_status)
        self.video_player.start(self._current_stream, is_live=True)

    def _apply_message(self, name, text, m, t, rank=True):
        """B站专属消息分支，其余复用基类渲染。"""
        if name == "粉丝数":
            # ROOM_REAL_TIME_MESSAGE_UPDATE：静默刷新主播粉丝数
            if m.get("followCount"):
                self.anchor_fields["anchor_fans"].setText(f"{int(m['followCount']):,}")
            return
        if name == "点赞" and m.get("likeTotal") and not m.get("nickname"):
            # LIKE_INFO_V3_UPDATE：仅刷新累计点赞（每秒推送），不刷事件日志
            self._like_count = int(m["likeTotal"])
            self.like_label.setText(f"{self._like_count:,}")
            self.anchor_fields["like_count"].setText(f"{self._like_count:,}")
            return
        super()._apply_message(name, text, m, t, rank=rank)
