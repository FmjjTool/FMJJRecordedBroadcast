# -*- coding: utf-8 -*-
"""B站弹幕姬页：完整复用抖音弹幕姬（TestPage）的 UI 与交互框架，仅替换平台后端。

- 后端：toolbox.bili_live（BiliLiveFetcher 弹幕 WS / 房间信息 / 直播流 / 统计 / 监控）
- 画质：B站按 qn 档位现取直链（游客会被限档，切档即按该档重新取流）
- 与抖音页的差异消息：粉丝数实时刷新（ROOM_REAL_TIME_MESSAGE_UPDATE）、
  累计点赞静默更新（LIKE_INFO_V3_UPDATE）、SC/上舰并入打赏榜（价值按 1000电池=1元 折算）
"""

from toolbox.bili_live import DEFAULT_ROOM
from gui_qt.pages.test_page import TestPage


class BiliDanmakuPage(TestPage):
    _BACKEND = "toolbox.bili_live"

    HELP_INTRO = (
        "<p><b>B站直播弹幕姬：完整复用抖音弹幕姬框架，接B站直播间实时数据。</b></p>"
        "<p>· <b>连接</b>：房间号或直播间链接（含 b23.tv 短链，自动提取房号）；"
        "留空自动填默认房间号，房间号右侧按钮连接，连接成功后变为「断开」</p>"
        "<p>· <b>实时数据</b>：弹幕 / 礼物 / SC / 上舰 / 进场 / 关注 / 点赞；"
        "主播粉丝数实时刷新，累计点赞静默更新；打赏榜价值按 1000电池=1元 折算</p>"
        "<p>· <b>两种模式</b>：仅弹幕（只抓弹幕，不拉视频）/ 回放（导入回放包重演整场）</p>"
        "<p>· <b>⏺ 录播（顶栏右侧）</b>：视频录制全部在「直播录制」二级窗口，"
        "支持开播自动录制、断流自动重连续录、分段与录满自动停</p>"
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
        self._bili_qn_list = []     # [(qn, desc)] 画质档位表（连接时由后端带回，仅展示）
        super().__init__(app)
        # 平台文案与单位
        self.title_lbl.setText("📺 B站弹幕 · 实时数据接入")
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

    def _on_room_info(self, info):
        """房间信息：先记下画质档位表，其余交给基类。"""
        if isinstance(info, dict) and info.get("qn_list"):
            self._bili_qn_list = list(info["qn_list"])
        super()._on_room_info(info)

    def _on_room_streams(self, streams):
        """B站直链：记下来即可（本页只抓弹幕，预览/录制在「直播录制」窗口）。"""
        self._streams = streams or {}
        self._current_stream = next(iter(self._streams.values()), None)

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
