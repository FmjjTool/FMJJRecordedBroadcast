# -*- coding: utf-8 -*-
"""直播录制 · 二级界面（抖音 / B站 同源）。

界面结构对齐参考项目「江湖录播」（D:/PythonApps/Python版全量/ui/main_window.py）：
  菜单栏（文件 / 主播 / 工具 / 设置 / 帮助）
  + 工具栏（添加主播 / 批量导入 / 批量修改 / 开启监控录制 / 一键停止 /
            立即检测 / 录制设置 / 推送设置 / FLV 修复 / 打开录制目录）
  + 顶部详情条 + 主播表格（状态着色、勾选列、双击切换监控/录制）
  + 下方运行日志（QSplitter 可拖）+ 状态栏
  + 右键菜单（参考项目原版全部项目，逐条对应）
  + 底部「抖音直播 / B站直播」切换（同源 UI，两套列表互不影响）

录制特性参考 BililiveRecorder 2.20.0：
  断线续传 / 按时间·按大小分段 / 断流重连 / 弹幕录制（B站 XML、抖音 JSONL）/
  封面保存 / 标题过滤 / 文件按主播分目录 / FLV 修复。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
    QStackedWidget, QStatusBar, QTableView, QVBoxLayout, QWidget,
)

from toolbox import live_record as LR

A = Qt.AlignmentFlag

# 列：(key, 标题, 宽, 对齐)
COLUMNS = [
    ("selected", "选择", 44, A.AlignCenter),
    ("idx", "序号", 44, A.AlignCenter),
    ("name", "主播名称", 150, A.AlignLeft | A.AlignVCenter),
    ("live", "直播状态", 80, A.AlignCenter),
    ("checked", "监控时间", 84, A.AlignCenter),
    ("elapsed", "已录时间", 84, A.AlignCenter),
    ("stop", "录制n分钟\n停止录制", 88, A.AlignCenter),
    ("rec", "录制状态", 78, A.AlignCenter),
    ("content", "录制\n内容", 62, A.AlignCenter),
    ("monitoring", "是否\n监控", 58, A.AlignCenter),
    ("want_record", "是否\n录制", 58, A.AlignCenter),
    ("quality", "录制质量", 88, A.AlignCenter),
    ("segment", "分段\n录制", 76, A.AlignCenter),
    ("online", "在线人数", 78, A.AlignCenter),
    ("title", "直播标题", 180, A.AlignLeft | A.AlignVCenter),
]
COL_INDEX = {c[0]: i for i, c in enumerate(COLUMNS)}
VOLATILE = ("live", "checked", "elapsed", "rec", "monitoring",
            "want_record", "online", "stop", "content", "quality")

C_LIVE_BG = QColor("#FFF4F4")
C_REC_BG = QColor("#FFE4E4")
C_ERR_FG = QColor("#C00000")
C_MUTED_FG = QColor("#9A9A9A")

# 清晰度档位（手动可选，不必等「识别」返回）
QUALITY_PRESETS = {
    "douyin": [("自动（最高可用）", ""), ("原画", "_origin"), ("蓝光", "_md"),
               ("1080P", "FULL_HD1"), ("720P", "HD1"), ("540P", "SD1"),
               ("360P", "SD2")],
    "bili": [("自动（最高可用）", ""), ("原画", "10000"), ("蓝光", "400"),
             ("超清", "250"), ("高清", "150"), ("流畅", "80"),
             ("4K", "20000"), ("HDR", "25000"), ("杜比", "30000")],
}


def fmt_num(v):
    try:
        return "{:,}".format(int(v))
    except (TypeError, ValueError):
        return str(v or "-")


def _parse_room(platform, text):
    """从输入里提取房间号（房间号 / 直播间链接）。"""
    import re
    s = (text or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s
    if platform == "bili":
        m = re.search(r"live\.bilibili\.com/(\d+)", s) or re.search(r"^(\d{2,})$", s)
        return m.group(1) if m else ""
    m = (re.search(r"live\.douyin\.com/(\d+)", s)
         or re.search(r"web_rid=(\d+)", s)
         or re.search(r"(\d{6,})", s))
    return m.group(1) if m else ""


def _open_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.startfile(path)  # noqa: S606
    except Exception:
        pass


def _ffplay():
    exe = LR.find_ffmpeg()
    if exe:
        p = os.path.join(os.path.dirname(exe), "ffplay.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("ffplay") or ""


class AnchorModel(QAbstractTableModel):
    """主播表格模型（只发 dataChanged，刷新不丢选中与滚动位置）。"""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager

    @property
    def anchors(self):
        return self.manager.anchors

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.anchors)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.isValid() and COLUMNS[index.column()][0] == "selected":
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if index.isValid() and role == Qt.ItemDataRole.CheckStateRole \
                and COLUMNS[index.column()][0] == "selected":
            a = self.anchors[index.row()]
            a.selected = value in (Qt.CheckState.Checked.value, Qt.CheckState.Checked)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.anchors)):
            return None
        a = self.anchors[index.row()]
        key = COLUMNS[index.column()][0]

        if role == Qt.ItemDataRole.CheckStateRole and key == "selected":
            return Qt.CheckState.Checked if a.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(a, index.row(), key)
        if role == Qt.ItemDataRole.BackgroundRole:
            if a.recording:
                return QBrush(C_REC_BG)
            if a.live:
                return QBrush(C_LIVE_BG)
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if a.error:
                return QBrush(C_ERR_FG)
            if key in ("monitoring", "want_record") and not self._flag(a, key):
                return QBrush(C_MUTED_FG)
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(COLUMNS[index.column()][3])
        if role == Qt.ItemDataRole.FontRole and a.recording:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(a)
        return None

    @staticmethod
    def _flag(a, key):
        return a.monitoring if key == "monitoring" else getattr(a, "_want_record", False)

    def _text(self, a, row, key):
        if key == "selected":
            return ""
        if key == "idx":
            return str(row + 1)
        if key == "name":
            return a.name or a.room or "-"
        if key == "live":
            if a.error:
                return "取流失败"
            return "已开播" if a.live else "等待开播"
        if key == "checked":
            return (a.last_check or "")[11:19] or "-"
        if key == "elapsed":
            return LR.fmt_duration(self.manager.elapsed(a))
        if key == "rec":
            return "录制中" if a.recording else (a.rec_state or "未录制")
        if key == "stop":
            n = a.stop_minutes or int(self.manager.settings.get("stop_minutes") or 0)
            return "%d 分钟" % n if n else "不限"
        if key == "content":
            return "音频" if int(getattr(a, "content", 0) or 0) == 1 else "视频"
        if key == "monitoring":
            return "是" if a.monitoring else "否"
        if key == "want_record":
            return "是" if getattr(a, "_want_record", False) else "否"
        if key == "quality":
            return a.quality_label or a.quality or "自动"
        if key == "segment":
            if a.segment:
                if a.segment_mb:
                    return "%d MB/段" % a.segment_mb
                return "%d 分钟/段" % (a.segment_minutes or 30)
            mode = self.manager.settings.get("segment_mode") or "关闭"
            if mode == "按时间":
                return "全局%d分钟" % int(self.manager.settings.get("segment_minutes") or 0)
            if mode == "按大小":
                return "全局%s MB" % self.manager.settings.get("segment_mb")
            return "否"
        if key == "online":
            return fmt_num(a.online) if a.online not in (None, "") else "-"
        if key == "title":
            return a.title or ""
        return ""

    @staticmethod
    def _tooltip(a):
        lines = [f"房间号：{a.room}", f"主播：{a.name or '-'}",
                 f"状态：{'已开播' if a.live else '等待开播'}",
                 f"最后检测：{a.last_check or '-'}",
                 f"录制质量：{a.quality_label or a.quality or '自动'}",
                 f"录制内容：{'音频' if int(getattr(a, 'content', 0) or 0) == 1 else '视频'}",
                 f"录满停止：{'%d 分钟' % a.stop_minutes if a.stop_minutes else '不限'}"]
        if a.segment:
            lines.append("分段：" + ("%d MB/段" % a.segment_mb if a.segment_mb
                                    else "%d 分钟/段" % (a.segment_minutes or 30)))
        if a.remark:
            lines.append("备注：" + a.remark)
        if a.recording:
            lines.append("正在录制")
        if a.error:
            lines.append("错误：" + a.error)
        if a.title:
            lines.append("标题：" + a.title)
        return "\n".join(lines)

    def refresh_all(self):
        self.beginResetModel()
        self.endResetModel()

    def refresh_volatile(self):
        if not self.anchors:
            return
        cols = [COL_INDEX[k] for k in VOLATILE]
        roles = [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.BackgroundRole,
                 Qt.ItemDataRole.ForegroundRole]
        for c in cols:
            self.dataChanged.emit(self.index(0, c),
                                  self.index(len(self.anchors) - 1, c), roles)


class Bridge(QObject):
    """监控线程 → GUI 线程。"""
    logged = Signal(str, str)
    changed = Signal()


class PlatformPane(QWidget):
    """一个平台的录制面板（表格 + 日志 + 工具栏 + 右键菜单）。"""

    def __init__(self, win, platform, parent=None):
        super().__init__(parent)
        self.win = win
        self.platform = platform
        self.pinfo = LR.PLATFORMS[platform]
        self.bridge = Bridge()
        self.bridge.logged.connect(self._on_log, Qt.ConnectionType.QueuedConnection)
        self.bridge.changed.connect(self._on_changed, Qt.ConnectionType.QueuedConnection)

        state = (win.state.get("platforms") or {}).get(platform) or {}
        self.manager = LR.RecordManager(platform, state.get("settings"),
                                        on_event=self.bridge.logged.emit,
                                        on_change=self.bridge.changed.emit)
        self.manager.load_dict(state)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._tbtn(bar, "＋ 添加主播", self.on_add, "添加一个直播间（房间号 / 直播间链接）")
        self._tbtn(bar, "批量导入", self.on_batch, "每行一个房间号或直播间链接")
        self.btn_batch = self._tbtn(bar, "批量修改 ▾", None, "对勾选的主播批量设置")
        self.btn_batch.clicked.connect(self._on_batch_menu)
        bar.addSpacing(10)
        self.btn_mon = self._tbtn(bar, "▶ 开启监控录制", self.on_toggle_monitor,
                                  "按间隔检测开播，开播即自动录制")
        self._tbtn(bar, "⏹ 一键停止录制", self.on_stop_all, "停止本平台全部录制任务")
        self._tbtn(bar, "⟳ 立即检测", self.on_check_now, "立刻检测所有监控中的主播")
        bar.addSpacing(10)
        self._tbtn(bar, "录制设置", self.on_settings)
        self._tbtn(bar, "推送设置", self.on_push_settings)
        self._tbtn(bar, "FLV 修复", self.on_repair_flv, "修复录制中断/时间戳错乱的 FLV")
        self._tbtn(bar, "打开录制目录", lambda: _open_dir(self.manager.out_dir()))
        bar.addStretch(1)
        self._tbtn(bar, "全选", lambda: self._check_all(True))
        self._tbtn(bar, "取消", lambda: self._check_all(False))
        lay.addLayout(bar)

        # ---- 顶部详情条 ----
        self.info = QLabel("共 0 个主播")
        self.info.setStyleSheet(
            "background:#FFFFFF;border:1px solid #E0E0E0;padding:3px 8px;color:#333;")
        self.info.setMinimumHeight(24)
        lay.addWidget(self.info)

        # ---- 表格 + 日志 ----
        split = QSplitter(Qt.Orientation.Vertical)
        self.model = AnchorModel(self.manager, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        for i, (_k, _t, w, _a) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_menu)
        self.table.doubleClicked.connect(self._on_double)
        self.table.clicked.connect(self._on_click)

        logbox = QWidget()
        lv = QVBoxLayout(logbox)
        lv.setContentsMargins(0, 2, 0, 0)
        lv.setSpacing(2)
        cap = QLabel("运行日志")
        cap.setStyleSheet("color:#555;font-weight:bold;")
        lv.addWidget(cap)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setStyleSheet("background:#FAFAFA;border:1px solid #E0E0E0;")
        lv.addWidget(self.log)

        split.addWidget(self.table)
        split.addWidget(logbox)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([520, 180])
        lay.addWidget(split, 1)

        self._log(f"{self.pinfo['name']}录制面板就绪（保存目录：{self.manager.out_dir()}）", "info")

    # ---------- 小工具 ----------
    def _tbtn(self, layout, text, slot, tip=""):
        b = QPushButton(text)
        b.setStyleSheet("padding:5px 10px;")
        if tip:
            b.setToolTip(tip)
        if slot is not None:
            b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _log(self, text, level="debug"):
        color = {"ok": "#0A7A0A", "warn": "#B06000",
                 "error": "#C00000", "info": "#0A5AA0"}.get(level, "#777777")
        ts = time.strftime("%H:%M:%S")
        safe = (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.log.appendHtml('<span style="color:#9A9A9A">[%s]</span> '
                            '<span style="color:%s">%s</span>' % (ts, color, safe))
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_log(self, level, text):
        self._log(text, level)

    def _on_changed(self):
        self.model.refresh_volatile()
        self.btn_mon.setText("■ 停止监控录制" if self.manager.running else "▶ 开启监控录制")
        self.win.refresh_status()

    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.manager.anchors[r] for r in rows
                if 0 <= r < len(self.manager.anchors)]

    def _checked(self):
        return [a for a in self.manager.anchors if a.selected]

    def _check_all(self, value):
        for a in self.manager.anchors:
            a.selected = value
        self.model.refresh_all()

    # ---------- 表格交互 ----------
    def _on_click(self, index):
        if COLUMNS[index.column()][0] == "selected":
            a = self.manager.anchors[index.row()]
            a.selected = not a.selected
            self.model.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])

    def _on_double(self, index):
        key = COLUMNS[index.column()][0]
        if key == "monitoring":
            self._toggle_field("monitoring")
        elif key == "want_record":
            self._toggle_field("_want_record")
        elif key == "selected":
            return
        else:
            self.on_anchor_settings()

    def _toggle_field(self, field):
        items = self._selected() or self._checked()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return
        new = not all(bool(getattr(a, field, False)) for a in items)
        for a in items:
            setattr(a, field, new)
            if field == "_want_record" and not new and a.recording:
                self.manager.stop_record(a, reason="退出录制")
        self.model.refresh_volatile()
        self.save()

    # ---------- 右键菜单（对应参考项目原版全项） ----------
    def _on_menu(self, pos):
        idx = self.table.indexAt(pos)
        if idx.isValid():
            self.table.selectRow(idx.row())
        self._build_context_menu().exec(self.table.viewport().mapToGlobal(pos))

    def _build_context_menu(self):
        """构造右键菜单（独立出来便于测试/复用）。"""
        has = bool(self._selected())
        menu = QMenu(self)
        ad = menu.addAction

        ad(self._act("当前主播_立即检测开播", self.on_check_selected))
        ad(self._act("当前主播_播放直播源", self.on_preview_stream))
        menu.addSeparator()
        ad(self._act("当前主播_加入监控", lambda: self._set_flag("monitoring", True)))
        ad(self._act("当前主播_退出监控", lambda: self._set_flag("monitoring", False)))
        ad(self._act("当前主播_加入录制", lambda: self._set_flag("_want_record", True)))
        ad(self._act("当前主播_退出录制", lambda: self._set_flag("_want_record", False)))
        sub = menu.addMenu("当前主播_修改录制多少分钟停止录制快捷值")
        for mins in (0, 5, 10, 30, 60, 120, 180):
            label = "不限制" if mins == 0 else "%d 分钟" % mins
            sub.addAction(self._act(label, lambda _c=False, m=mins: self._set_stop_minutes(m)))
        sub.addSeparator()
        sub.addAction(self._act("自定义…", lambda: self._set_stop_minutes(None)))
        ad(self._act("当前主播_已录时间归零重新录制",
                     lambda: self.on_reset_elapsed(True)))
        menu.addSeparator()
        ad(self._act("当前主播_独立设置功能", self.on_anchor_settings))
        menu.addSeparator()
        ad(self._act("当前主播_移动至序号", self.on_move_to_index))
        ad(self._act("当前主播_修改备注信息", self.on_edit_remark))
        ad(self._act("当前主播_复制直播源", self.on_copy_stream))
        ad(self._act("当前主播_复制直播网址", self.on_copy_url))
        ad(self._act("当前主播_复制主播整体数据", self.on_copy_data))
        ad(self._act("当前主播_删除当前选择", self.on_delete))
        menu.addSeparator()
        ad(self._act("当前主播_打开当前视频保存目录", self.on_open_anchor_dir))
        ad(self._act("当前主播_打开记录数据文件夹", self.on_open_record_dir))
        menu.addSeparator()
        ad(self._act("全部主播_复制直播网址", self.on_copy_all_url))
        ad(self._act("列表拉篮选择_勾选", lambda: self._check_all(True)))
        ad(self._act("列表拉篮选择_不选", lambda: self._check_all(False)))
        ad(self._act("列表拉篮选择_反选", self.on_lan_reverse))
        menu.addSeparator()
        ad(self._act("当前主播_运行日志", self.on_show_log))
        for a in menu.actions():
            if a.isSeparator() or a.menu():
                continue
            if a.text().startswith(("全部主播", "列表拉篮")):
                continue
            a.setEnabled(has)
        return menu

    def _act(self, text, slot):
        a = QAction(text, self)
        a.triggered.connect(lambda _c=False: slot())
        return a

    # ---------- 批量修改菜单（对应原版 MS_BatchModify） ----------
    def _on_batch_menu(self):
        self._build_batch_menu().exec(
            self.btn_batch.mapToGlobal(self.btn_batch.rect().bottomLeft()))

    def _build_batch_menu(self):
        menu = QMenu(self)
        ad = menu.addAction
        ad(self._act("列表勾选_加入监控", lambda: self._set_flag("monitoring", True)))
        ad(self._act("列表勾选_退出监控", lambda: self._set_flag("monitoring", False)))
        ad(self._act("列表勾选_加入录制", lambda: self._set_flag("_want_record", True)))
        ad(self._act("列表勾选_退出录制", lambda: self._set_flag("_want_record", False)))
        menu.addSeparator()
        ad(self._act("列表勾选_修改录制多少分钟停止录制",
                     lambda: self._set_stop_minutes(None)))
        quick = menu.addMenu("列表勾选_修改录制多少分钟停止录制快捷值")
        for mins in (0, 5, 10, 30, 60, 120, 180):
            label = "不限制" if mins == 0 else "%d 分钟" % mins
            quick.addAction(self._act(label, lambda _c=False, m=mins: self._set_stop_minutes(m)))
        ad(self._act("列表勾选_分段录制每段几分钟设置", self.on_batch_segment))
        ad(self._act("列表勾选_录制清晰度与优先顺序", self.on_batch_quality))
        ad(self._act("列表勾选_录制视频(有视频有声)", lambda: self.on_set_content(0)))
        ad(self._act("列表勾选_录制音频(无视频有声)", lambda: self.on_set_content(1)))
        menu.addSeparator()
        ad(self._act("列表勾选_已录时间归零", lambda: self.on_reset_elapsed(False)))
        ad(self._act("列表勾选_已录时间归零重新录制", lambda: self.on_reset_elapsed(True)))
        ad(self._act("列表勾选_修改备注", self.on_edit_remark))
        menu.addSeparator()
        ad(self._act("列表勾选_提醒与记录设置", self.on_remind_setting))
        ad(self._act("列表勾选_删除主播(不删除录制数据)", self.on_delete))
        return menu

    def _targets(self, batch=False):
        got = self._checked() if batch else self._selected()
        if not got:
            self.win.toast("请先勾选/选择主播", "warning")
        return got

    def _set_flag(self, field, value):
        items = self._selected() or self._checked()
        if not items:
            return
        for a in items:
            setattr(a, field, value)
            if field == "_want_record" and not value and a.recording:
                self.manager.stop_record(a, reason="退出录制")
        self.model.refresh_volatile()
        self.save()

    def _set_stop_minutes(self, minutes):
        items = self._selected() or self._checked()
        if not items:
            return
        if minutes is None:
            minutes, ok = QInputDialog.getInt(
                self, "修改录制多少分钟停止录制",
                "录制多少分钟后自动停止（0=不限制）：",
                int(items[0].stop_minutes or 0), 0, 1440, 5)
            if not ok:
                return
        for a in items:
            a.stop_minutes = int(minutes)
        self.model.refresh_volatile()
        self.save()
        self._log("已设置 %d 个主播：录满 %s 停止"
                  % (len(items), "%d 分钟" % minutes if minutes else "不限制"), "ok")

    def on_set_content(self, content):
        items = self._selected() or self._checked()
        if not items:
            return
        for a in items:
            a.content = int(content)
        self.model.refresh_volatile()
        self.save()
        self._log("已设置 %d 个主播录制内容为 %s"
                  % (len(items), "音频" if content else "视频"), "ok")

    def on_batch_segment(self):
        items = self._targets(True)
        if not items:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("分段录制设置（%d 个主播）" % len(items))
        lay = QVBoxLayout(dlg)
        cb = QCheckBox("启用分段录制")
        cb.setChecked(bool(items[0].segment))
        lay.addWidget(cb)
        row = QHBoxLayout()
        row.addWidget(QLabel("每段"))
        sp_min = QSpinBox()
        sp_min.setRange(1, 720)
        sp_min.setSuffix(" 分钟")
        sp_min.setValue(int(items[0].segment_minutes or 30))
        row.addWidget(sp_min)
        sp_mb = QSpinBox()
        sp_mb.setRange(0, 65536)
        sp_mb.setSuffix(" MB（0=按时间）")
        sp_mb.setValue(int(items[0].segment_mb or 0))
        row.addWidget(sp_mb)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for a in items:
            a.segment = cb.isChecked()
            a.segment_minutes = sp_min.value()
            a.segment_mb = sp_mb.value()
        self.model.refresh_volatile()
        self.save()
        self._log("已设置 %d 个主播分段录制" % len(items), "ok")

    def on_batch_quality(self):
        items = self._targets(True)
        if not items:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("录制清晰度（%d 个主播）" % len(items))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("选择录制清晰度（取不到时自动回退到最高可用）："))
        cb = QComboBox()
        for label, key in QUALITY_PRESETS[self.platform]:
            cb.addItem(label, key)
        i = cb.findData(items[0].quality or "")
        if i >= 0:
            cb.setCurrentIndex(i)
        lay.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for a in items:
            a.quality = str(cb.currentData() or "")
            a.quality_label = cb.currentText() if cb.currentData() else ""
        self.model.refresh_volatile()
        self.save()
        self._log("已设置 %d 个主播清晰度：%s" % (len(items), cb.currentText()), "ok")

    def on_remind_setting(self):
        items = self._targets(True)
        if not items:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("提醒与记录设置（%d 个主播）" % len(items))
        lay = QVBoxLayout(dlg)
        fields = [("notify_live", "开播提醒"), ("notify_offline", "关播提醒"),
                  ("notify_rec_start", "开始录制提醒"),
                  ("notify_rec_stop", "停止录制提醒"),
                  ("notify_online", "在线人数提醒")]
        boxes = []
        for attr, label in fields:
            cb = QCheckBox(label)
            cb.setChecked(bool(getattr(items[0], attr, False)))
            lay.addWidget(cb)
            boxes.append((attr, cb))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for attr, cb in boxes:
            for a in items:
                setattr(a, attr, cb.isChecked())
        self.model.refresh_volatile()
        self.save()
        self._log("已设置 %d 个主播的提醒开关" % len(items), "ok")

    # ---------- 动作 ----------
    def on_add(self):
        dlg = AddAnchorDialog(self, self.platform, self.manager)
        a = dlg.exec_get()
        if not a:
            return
        if any(x.room == a.room for x in self.manager.anchors):
            self.win.toast("该房间已在列表中", "info")
            return
        self.manager.anchors.append(a)
        self.model.refresh_all()
        self.save()
        self._log("已添加：%s（%s）" % (a.name or a.room, a.room), "ok")

    def on_batch(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "批量导入", "每行一个房间号或直播间链接：", "")
        if not ok or not text.strip():
            return
        added = 0
        for line in text.splitlines():
            room = _parse_room(self.platform, line)
            if not room or any(x.room == room for x in self.manager.anchors):
                continue
            a = LR.Anchor(platform=self.platform, room=room, monitoring=True)
            a._want_record = False
            self.manager.anchors.append(a)
            added += 1
        self.model.refresh_all()
        self.save()
        self._log("批量导入：新增 %d 个房间" % added, "ok")

    def on_toggle_monitor(self):
        if self.manager.running:
            self.manager.stop_monitor()
        else:
            if not self.manager.start_monitor():
                self.win.toast("请先给主播打开「是否监控」", "warning")
        self._on_changed()

    def on_stop_all(self):
        n = self.manager.active_count()
        if not n:
            self.win.toast("当前没有正在录制的任务", "info")
            return
        if QMessageBox.question(
                self, "确认", "停止全部 %d 个录制任务？" % n,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manager.stop_all()
        self.model.refresh_volatile()

    def on_check_now(self):
        items = [a for a in self.manager.anchors if a.monitoring] or list(self.manager.anchors)
        if not items:
            self.win.toast("请先添加主播", "info")
            return
        self.on_check_selected(items)

    def on_check_selected(self, items=None):
        items = items or self._selected()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return
        self._log("开始检测 %d 个主播…" % len(items), "info")

        def work():
            for a in items:
                try:
                    self.manager.check_one(a)
                except Exception as e:  # noqa: BLE001
                    self.bridge.logged.emit("error", "「%s」检测异常：%s"
                                            % (a.name or a.room, e))
            self.bridge.logged.emit("ok", "检测完成")

        self._run_bg(work)

    def on_preview_stream(self):
        items = self._selected()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return
        a = items[0]
        exe = _ffplay()
        if not exe:
            self.win.toast("未找到 ffplay（ffmpeg 同目录），无法预览直播源", "warning")
            return

        def work():
            info = LR.fetch_info(a.platform, a.room, a.quality)
            url = info.get("stream") or ""
            if not url:
                self.bridge.logged.emit("warn", "「%s」未开播/取不到直播流"
                                        % (a.name or a.room))
                return
            ref = LR.PLATFORMS[a.platform]["referer"]
            try:
                subprocess.Popen([exe, "-loglevel", "error", "-window_title",
                                  "直播预览 - %s" % (a.name or a.room),
                                  "-headers", "Referer: %s\r\nUser-Agent: %s\r\n"
                                  % (ref, LR.UA), url])
                self.bridge.logged.emit("ok", "已用 ffplay 打开「%s」直播源"
                                        % (a.name or a.room))
            except Exception as e:  # noqa: BLE001
                self.bridge.logged.emit("error", "预览失败：%s" % e)

        self._run_bg(work)

    def on_start_record(self):
        items = self._selected()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return

        def work():
            for a in items:
                a._want_record = True
                self.manager.start_record(a)

        self._run_bg(work)

    def on_stop_record(self):
        for a in self._selected():
            a._want_record = False
            self.manager.stop_record(a, reason="手动停止")

    def on_reset_elapsed(self, restart=True):
        items = self._selected() or self._checked()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return
        for a in items:
            was = a.recording
            if was:
                self.manager.stop_record(a, reason="已录时间归零")
            a.elapsed = 0
            if was and restart and a.live:
                self.manager.start_record(a)
        self.model.refresh_volatile()
        self.save()
        self._log("已录时间归零（%d 个）" % len(items), "ok")

    def on_move_to_index(self):
        items = self._selected()
        if not items:
            return
        a = items[0]
        cur = self.manager.anchors.index(a) + 1
        n, ok = QInputDialog.getInt(
            self, "移动至序号", "把「%s」移动到第几行？" % (a.name or a.room),
            cur, 1, len(self.manager.anchors))
        if not ok:
            return
        self.manager.anchors.remove(a)
        self.manager.anchors.insert(n - 1, a)
        self.model.refresh_all()
        self.save()
        self._log("「%s」已移动到第 %d 行" % (a.name or a.room, n), "ok")

    def on_edit_remark(self):
        items = self._selected() or self._checked()
        if not items:
            return
        text, ok = QInputDialog.getText(
            self, "修改备注信息",
            "为 %d 个主播设置备注：" % len(items) if len(items) > 1
            else "「%s」的备注：" % (items[0].name or items[0].room),
            text=items[0].remark or "")
        if not ok:
            return
        for a in items:
            a.remark = text
        self.model.refresh_volatile()
        self.save()

    def on_copy_url(self):
        items = self._selected()
        if not items:
            return
        QApplication.clipboard().setText("\n".join(a.live_url for a in items))
        self._log("已复制 %d 条直播间地址" % len(items), "ok")

    def on_copy_all_url(self):
        QApplication.clipboard().setText("\n".join(a.live_url for a in self.manager.anchors))
        self._log("已复制全部 %d 条直播间地址" % len(self.manager.anchors), "ok")

    def on_copy_stream(self):
        items = self._selected()
        if not items:
            return
        a = items[0]

        def work():
            info = LR.fetch_info(a.platform, a.room, a.quality)
            url = info.get("stream") or ""
            if url:
                QApplication.clipboard().setText(url)
                self.bridge.logged.emit("ok", "已复制直播源：%s" % url[:80])
            else:
                self.bridge.logged.emit("warn", "未取到直播源（未开播或被限制）")

        self._run_bg(work)

    def on_copy_data(self):
        items = self._selected()
        if not items:
            return
        lines = []
        for a in items:
            lines.append("\t".join(str(x) for x in [
                a.name, "已开播" if a.live else "等待开播", a.online, a.room,
                a.quality_label or a.quality, a.rec_state, a.title, a.live_url]))
        QApplication.clipboard().setText("\n".join(lines))
        self._log("已复制 %d 个主播的数据" % len(items), "ok")

    def on_lan_reverse(self):
        for a in self.manager.anchors:
            a.selected = not a.selected
        self.model.refresh_all()

    def on_delete(self):
        items = self._selected() or self._checked()
        if not items:
            return
        if QMessageBox.question(
                self, "确认", "删除选中的 %d 个主播？\n（录制文件不会被删除）" % len(items),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        for a in items:
            if a.recording:
                self.manager.stop_record(a, reason="删除")
            if a in self.manager.anchors:
                self.manager.anchors.remove(a)
        self.model.refresh_all()
        self.save()
        self._log("已删除 %d 个主播" % len(items), "warn")

    def on_open_anchor_dir(self):
        items = self._selected()
        if not items:
            return
        _open_dir(os.path.join(self.manager.out_dir(),
                               LR.safe_name(items[0].name or items[0].room)))

    def on_open_record_dir(self):
        _open_dir(os.path.join(os.path.abspath(self.manager.out_dir()), "..", "记录数据"))

    def on_show_log(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("运行日志")
        dlg.resize(820, 520)
        lay = QVBoxLayout(dlg)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setFont(QFont("Consolas", 9))
        box.setPlainText(self.log.toPlainText())
        lay.addWidget(box)
        dlg.exec()

    def on_settings(self):
        dlg = RecordSettingsDialog(self, self.manager)
        if dlg.exec_get():
            self.model.refresh_all()
            self.save()
            self._log("录制设置已保存", "ok")

    def on_push_settings(self):
        dlg = PushSettingsDialog(self, self.manager)
        if dlg.exec_get():
            self.save()
            self._log("推送设置已保存", "ok")

    def on_repair_flv(self):
        FlvRepairDialog(self, self.manager).exec()

    def on_anchor_settings(self):
        items = self._selected()
        if not items:
            self.win.toast("请先选择主播", "warning")
            return
        dlg = AnchorSettingsDialog(self, items[0], self.manager)
        if dlg.exec_get():
            self.model.refresh_volatile()
            self.save()
            self._log("「%s」独立设置已保存" % (items[0].name or items[0].room), "ok")

    # ---------- 线程 ----------
    def _run_bg(self, work):
        import threading
        threading.Thread(target=work, daemon=True).start()

    # ---------- 保存 ----------
    def to_state(self):
        d = self.manager.to_dict()
        for a, src in zip(d["anchors"], self.manager.anchors):
            a["want_record"] = bool(getattr(src, "_want_record", False))
        return d

    def save(self):
        self.win.save_state()


# ==================== 对话框 ====================

class AddAnchorDialog(QDialog):
    """添加主播：输入 → （可选）识别 → 确认。不识别也能直接添加。"""

    resolved = Signal(object)

    def __init__(self, parent, platform, manager):
        super().__init__(parent)
        self.platform = platform
        self.manager = manager
        self.anchor = None
        self.resolved.connect(self._after, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("添加主播 · " + LR.PLATFORMS[platform]["name"])
        self.setMinimumWidth(640)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("房间号或直播间链接："))
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            "如 544820 / https://live.bilibili.com/544820" if platform == "bili"
            else "如 582775732015 / https://live.douyin.com/582775732015")
        self.edit.returnPressed.connect(self._resolve)
        row.addWidget(self.edit, 1)
        self.btn_pick = QPushButton("📇 已存账号")
        self.btn_pick.setToolTip("从账号获取 / 直播录制列表 / 房间缓存里选")
        self.btn_pick.clicked.connect(self._pick_saved)
        row.addWidget(self.btn_pick)
        self.btn_ok = QPushButton("识别")
        self.btn_ok.clicked.connect(self._resolve)
        row.addWidget(self.btn_ok)
        lay.addLayout(row)

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMinimumHeight(140)
        self.info.setPlainText("点「识别」补全主播名称/直播状态（不识别也能直接添加，"
                               "清晰度可在下方手动选）")
        lay.addWidget(self.info)

        box = QGroupBox("添加后默认")
        bl = QVBoxLayout(box)
        r1 = QHBoxLayout()
        self.ck_mon = QCheckBox("加入监控（检测开播）")
        self.ck_mon.setChecked(True)
        self.ck_rec = QCheckBox("加入录制（开播即录）")
        r1.addWidget(self.ck_mon)
        r1.addWidget(self.ck_rec)
        r1.addStretch(1)
        bl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("录制质量"))
        self.cb_q = QComboBox()
        for label, key in QUALITY_PRESETS[platform]:
            self.cb_q.addItem(label, key)
        r2.addWidget(self.cb_q)
        r2.addWidget(QLabel("录满"))
        self.sp_stop = QSpinBox()
        self.sp_stop.setRange(0, 1440)
        self.sp_stop.setSuffix(" 分钟（0=不限）")
        r2.addWidget(self.sp_stop)
        r2.addStretch(1)
        bl.addLayout(r2)
        lay.addWidget(box)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("添加")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _pick_saved(self):
        """从本机已存账号里选一个直播间（同平台的直接填，跨平台的提示切换）。"""
        try:
            from gui_qt.room_picker import RoomPickerDialog
        except Exception as e:  # noqa: BLE001
            self.info.setPlainText("账号选择器加载失败：%s" % e)
            return
        e = RoomPickerDialog(self, platform=self.platform).exec_get()
        if not e:
            return
        if e.platform != self.platform:
            self.info.setPlainText("该账号属于%s，请切到%s面板添加"
                                   % ("B站" if e.platform == "bili" else "抖音",
                                      "B站" if e.platform == "bili" else "抖音"))
            return
        self.edit.setText(e.room)
        self._resolve()

    def _resolve(self):
        room = _parse_room(self.platform, self.edit.text())
        if not room:
            self.info.setPlainText("无法识别房间号：请粘贴直播间链接或直接填数字房间号")
            return
        self.edit.setText(room)
        self.btn_ok.setEnabled(False)
        self.info.setPlainText("识别中…")

        def work():
            info = LR.fetch_info(self.platform, room)
            self.resolved.emit((room, info))

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _after(self, payload):
        room, info = payload
        self.btn_ok.setEnabled(True)
        self.cb_q.blockSignals(True)
        cur = self.cb_q.currentData()
        self.cb_q.clear()
        for label, key in QUALITY_PRESETS[self.platform]:
            self.cb_q.addItem(label, key)
        for label, key in (info.get("qualities") or {}).items():
            if self.cb_q.findData(str(key)) < 0:
                self.cb_q.addItem(str(label), str(key))
        i = self.cb_q.findData(cur)
        if i >= 0:
            self.cb_q.setCurrentIndex(i)
        self.cb_q.blockSignals(False)
        self.info.setPlainText(
            "识别成功：\n"
            f"  房间号：{room}\n"
            f"  主播：{info.get('name') or '-'}\n"
            f"  状态：{'直播中' if info.get('live') else '未开播'}"
            f"{'　在线 ' + str(info.get('online')) if info.get('online') else ''}\n"
            f"  标题：{info.get('title') or '-'}\n"
            f"  可用清晰度：{' / '.join((info.get('qualities') or {}).keys()) or '未知'}\n"
            f"  取流：{'已获取' if info.get('stream') else (info.get('error') or '未开播')}")

    def _ok(self):
        room = _parse_room(self.platform, self.edit.text())
        if not room:
            self.info.setPlainText("请先填写房间号/链接")
            return
        if self.anchor is None or self.anchor.room != room:
            self.anchor = LR.Anchor(platform=self.platform, room=room)
        self.anchor.monitoring = self.ck_mon.isChecked()
        self.anchor._want_record = self.ck_rec.isChecked()
        self.anchor.quality = str(self.cb_q.currentData() or "")
        self.anchor.quality_label = self.cb_q.currentText() if self.cb_q.currentData() else ""
        self.anchor.stop_minutes = self.sp_stop.value()
        self.accept()

    def exec_get(self):
        return self.anchor if self.exec() == QDialog.DialogCode.Accepted else None


class RecordSettingsDialog(QDialog):
    """录制设置（江湖「录制设置」+ 录播姬 CuttingMode / TimingStreamRetry / 弹幕录制）。"""

    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        s = manager.settings
        self.setWindowTitle("录制设置")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)

        g1 = QGroupBox("录制")
        f1 = QFormLayout(g1)
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["下载流", "录制流"])
        self.cb_mode.setCurrentText(s.get("mode") or "下载流")
        self.cb_mode.setToolTip("下载流：直接下载 FLV，断线自动续传（推荐）\n"
                                "录制流：ffmpeg -c copy，兼容 HLS / 音频录制")
        f1.addRow("录制方式", self.cb_mode)

        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        self.cb_seg = QComboBox()
        self.cb_seg.addItems(["关闭", "按时间", "按大小"])
        self.cb_seg.setCurrentText(s.get("segment_mode") or "关闭")
        sl.addWidget(self.cb_seg)
        self.sp_seg_min = QSpinBox()
        self.sp_seg_min.setRange(1, 720)
        self.sp_seg_min.setSuffix(" 分钟/段")
        self.sp_seg_min.setValue(int(s.get("segment_minutes") or 30))
        sl.addWidget(self.sp_seg_min)
        self.sp_seg_mb = QSpinBox()
        self.sp_seg_mb.setRange(50, 65536)
        self.sp_seg_mb.setSuffix(" MB/段")
        self.sp_seg_mb.setValue(int(s.get("segment_mb") or 1024))
        sl.addWidget(self.sp_seg_mb)
        sl.addStretch(1)
        f1.addRow("自动分段", seg)
        self.cb_seg.currentTextChanged.connect(self._sync_seg)
        self._sync_seg(self.cb_seg.currentText())

        stop = QWidget()
        stl = QHBoxLayout(stop)
        stl.setContentsMargins(0, 0, 0, 0)
        stl.addWidget(QLabel("录满"))
        self.sp_stop = QSpinBox()
        self.sp_stop.setRange(0, 1440)
        self.sp_stop.setValue(int(s.get("stop_minutes") or 0))
        stl.addWidget(self.sp_stop)
        stl.addWidget(QLabel("分钟后自动停止（0=不限）"))
        stl.addStretch(1)
        f1.addRow("", stop)
        lay.addWidget(g1)

        g2 = QGroupBox("目录与重连")
        f2 = QFormLayout(g2)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.ed_dir = QLineEdit(s.get("outdir") or manager.out_dir())
        rl.addWidget(self.ed_dir, 1)
        b1 = QPushButton("选择…")
        b1.clicked.connect(self._pick_dir)
        rl.addWidget(b1)
        b2 = QPushButton("打开")
        b2.clicked.connect(lambda: _open_dir(self.ed_dir.text().strip()))
        rl.addWidget(b2)
        f2.addRow("保存目录", row)
        hint = QLabel("录制文件：<保存目录>/<主播名>/<主播名>_<日期时间>.flv（音频为 .ts）")
        hint.setStyleSheet("color:#888;")
        f2.addRow("", hint)
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(5, 3600)
        self.sp_interval.setSuffix(" 秒")
        self.sp_interval.setValue(int(s.get("interval") or 30))
        f2.addRow("开播检测间隔", self.sp_interval)
        self.sp_retry = QSpinBox()
        self.sp_retry.setRange(1, 600)
        self.sp_retry.setSuffix(" 秒")
        self.sp_retry.setValue(int(s.get("retry_delay") or 6))
        f2.addRow("断流重连间隔", self.sp_retry)
        self.sp_max_retry = QSpinBox()
        self.sp_max_retry.setRange(0, 999)
        self.sp_max_retry.setSuffix(" 次（0=不限）")
        self.sp_max_retry.setValue(int(s.get("max_retry") or 30))
        f2.addRow("单场重试上限", self.sp_max_retry)
        self.ed_title_filter = QLineEdit(s.get("title_filter") or "")
        self.ed_title_filter.setPlaceholderText("如 广告;录播;回放（正则，多个用 ; 分隔）")
        f2.addRow("标题过滤", self.ed_title_filter)
        lay.addWidget(g2)

        g3 = QGroupBox("附带内容（录播姬特性）")
        gl = QVBoxLayout(g3)
        self.ck_danmaku = QCheckBox("同时录制弹幕（B站 XML / 抖音 JSONL，与视频同名）")
        self.ck_danmaku.setChecked(bool(s.get("save_danmaku")))
        gl.addWidget(self.ck_danmaku)
        self.ck_cover = QCheckBox("保存直播封面（录制开始时存到该主播目录）")
        self.ck_cover.setChecked(bool(s.get("save_cover", True)))
        gl.addWidget(self.ck_cover)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("在线人数提醒阈值"))
        self.sp_online = QSpinBox()
        self.sp_online.setRange(0, 10_000_000)
        self.sp_online.setSingleStep(1000)
        self.sp_online.setValue(int(s.get("online_threshold") or 0))
        row3.addWidget(self.sp_online)
        row3.addWidget(QLabel("（0=不提醒）"))
        row3.addStretch(1)
        gl.addLayout(row3)
        lay.addWidget(g3)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _sync_seg(self, mode):
        self.sp_seg_min.setEnabled(mode == "按时间")
        self.sp_seg_mb.setEnabled(mode == "按大小")

    def _pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, "选择录制保存目录",
                                             self.ed_dir.text().strip())
        if p:
            self.ed_dir.setText(p)

    def _ok(self):
        self.manager.settings.update({
            "mode": self.cb_mode.currentText(),
            "segment_mode": self.cb_seg.currentText(),
            "segment_minutes": self.sp_seg_min.value(),
            "segment_mb": self.sp_seg_mb.value(),
            "stop_minutes": self.sp_stop.value(),
            "outdir": self.ed_dir.text().strip(),
            "interval": self.sp_interval.value(),
            "retry_delay": self.sp_retry.value(),
            "max_retry": self.sp_max_retry.value(),
            "title_filter": self.ed_title_filter.text().strip(),
            "save_danmaku": self.ck_danmaku.isChecked(),
            "save_cover": self.ck_cover.isChecked(),
            "online_threshold": self.sp_online.value(),
        })
        self.accept()

    def exec_get(self):
        return self.exec() == QDialog.DialogCode.Accepted


class AnchorSettingsDialog(QDialog):
    """独立设置：录制内容 / 清晰度 / 分段 / 录满 / 备注 / 提醒（全部可手动填）。"""

    def __init__(self, parent, anchor, manager):
        super().__init__(parent)
        self.anchor = anchor
        self.manager = manager
        self.setWindowTitle("独立设置 · %s（%s）" % (
            anchor.name or anchor.room, anchor.room))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        g1 = QGroupBox("录制")
        f1 = QFormLayout(g1)
        self.cb_content = QComboBox()
        self.cb_content.addItems(["视频（有视频有声）", "音频（无视频有声）"])
        self.cb_content.setCurrentIndex(1 if int(anchor.content or 0) == 1 else 0)
        f1.addRow("录制内容", self.cb_content)

        self.cb_q = QComboBox()
        for label, key in QUALITY_PRESETS[manager.platform]:
            self.cb_q.addItem(label, key)
        if anchor.quality:
            i = self.cb_q.findData(str(anchor.quality))
            if i < 0:
                self.cb_q.addItem(anchor.quality_label or str(anchor.quality),
                                  str(anchor.quality))
                i = self.cb_q.count() - 1
            self.cb_q.setCurrentIndex(i)
        f1.addRow("录制质量", self.cb_q)

        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        self.ck_seg = QCheckBox("分段录制")
        self.ck_seg.setChecked(anchor.segment)
        sl.addWidget(self.ck_seg)
        self.sp_min = QSpinBox()
        self.sp_min.setRange(1, 720)
        self.sp_min.setSuffix(" 分钟/段")
        self.sp_min.setValue(anchor.segment_minutes or 30)
        sl.addWidget(self.sp_min)
        self.sp_mb = QSpinBox()
        self.sp_mb.setRange(0, 65536)
        self.sp_mb.setSuffix(" MB/段（0=按时间）")
        self.sp_mb.setValue(anchor.segment_mb or 0)
        sl.addWidget(self.sp_mb)
        sl.addStretch(1)
        # 两个数值框始终可编辑；一改数值就自动勾上「分段录制」
        self.sp_min.valueChanged.connect(lambda _v: self.ck_seg.setChecked(True))
        self.sp_mb.valueChanged.connect(
            lambda v: self.ck_seg.setChecked(True) if v else None)
        f1.addRow("分段", seg)

        self.sp_stop = QSpinBox()
        self.sp_stop.setRange(0, 1440)
        self.sp_stop.setSuffix(" 分钟（0=不限）")
        self.sp_stop.setValue(anchor.stop_minutes or 0)
        f1.addRow("录满停止", self.sp_stop)
        lay.addWidget(g1)

        g2 = QGroupBox("提醒（WebHook 推送，按事件）")
        gl = QVBoxLayout(g2)
        self.notify_boxes = {}
        row = None
        for i, (attr, label) in enumerate([
                ("notify_live", "开播提醒"), ("notify_offline", "关播提醒"),
                ("notify_rec_start", "开始录制提醒"),
                ("notify_rec_stop", "停止录制提醒"),
                ("notify_online", "在线人数提醒")]):
            if i % 3 == 0:
                row = QHBoxLayout()
                gl.addLayout(row)
            cb = QCheckBox(label)
            cb.setChecked(bool(getattr(anchor, attr, False)))
            row.addWidget(cb)
            self.notify_boxes[attr] = cb
        row.addStretch(1)
        lay.addWidget(g2)

        g3 = QGroupBox("备注")
        f3 = QFormLayout(g3)
        self.ed_note = QLineEdit(anchor.remark or "")
        f3.addRow("", self.ed_note)
        lay.addWidget(g3)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _ok(self):
        a = self.anchor
        a.content = self.cb_content.currentIndex()
        a.quality = str(self.cb_q.currentData() or "")
        a.quality_label = self.cb_q.currentText() if self.cb_q.currentData() else ""
        a.segment = self.ck_seg.isChecked()
        a.segment_minutes = self.sp_min.value()
        a.segment_mb = self.sp_mb.value()
        a.stop_minutes = self.sp_stop.value()
        for attr, cb in self.notify_boxes.items():
            setattr(a, attr, cb.isChecked())
        a.remark = self.ed_note.text().strip()
        self.accept()

    def exec_get(self):
        return self.exec() == QDialog.DialogCode.Accepted


class PushSettingsDialog(QDialog):
    """WebHook 推送设置（企业微信 / 钉钉 / 飞书 / 自定义）。"""

    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        from toolbox.webhook import DEFAULT_EVENTS, PLATFORMS
        conf = dict(manager.settings.get("push") or {})
        events = {**DEFAULT_EVENTS, **(conf.get("events") or {})}
        self.setWindowTitle("推送设置")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        g = QGroupBox("WebHook")
        f = QFormLayout(g)
        self.ck_enabled = QCheckBox("启用推送")
        self.ck_enabled.setChecked(bool(conf.get("enabled")))
        f.addRow("", self.ck_enabled)
        self.cb_platform = QComboBox()
        self.cb_platform.addItems(PLATFORMS)
        self.cb_platform.setCurrentText(conf.get("platform") or PLATFORMS[0])
        f.addRow("平台", self.cb_platform)
        self.ed_url = QLineEdit(conf.get("url") or "")
        self.ed_url.setPlaceholderText("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…")
        f.addRow("Webhook 地址", self.ed_url)
        self.ed_prefix = QLineEdit(conf.get("prefix") or "")
        self.ed_prefix.setPlaceholderText("可选，如：封茗囧菌直播间")
        f.addRow("标题前缀", self.ed_prefix)
        lay.addWidget(g)

        g2 = QGroupBox("推送事件")
        gl = QVBoxLayout(g2)
        self.event_boxes = {}
        row = None
        for i, name in enumerate(["开播", "关播", "开始录制", "停止录制", "在线人数"]):
            if i % 3 == 0:
                row = QHBoxLayout()
                gl.addLayout(row)
            cb = QCheckBox(name)
            cb.setChecked(bool(events.get(name)))
            row.addWidget(cb)
            self.event_boxes[name] = cb
        row.addStretch(1)
        gl.addWidget(QLabel("注：还要在该主播的「独立设置 → 提醒」里打开对应开关"))
        lay.addWidget(g2)

        row2 = QHBoxLayout()
        test = QPushButton("发送测试消息")
        test.clicked.connect(self._test)
        row2.addWidget(test)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.test_label = QLabel("")
        self.test_label.setStyleSheet("color:#666;")
        lay.addWidget(self.test_label)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _current(self):
        return {"enabled": self.ck_enabled.isChecked(),
                "platform": self.cb_platform.currentText(),
                "url": self.ed_url.text().strip(),
                "prefix": self.ed_prefix.text().strip(),
                "events": {k: cb.isChecked() for k, cb in self.event_boxes.items()}}

    def _test(self):
        from toolbox.webhook import Pusher
        ok, msg = Pusher(self._current()).test()
        self.test_label.setText(("✅ 已发送：" if ok else "❌ 失败：") + str(msg)[:200])
        self.test_label.setStyleSheet("color:%s;" % ("#0A7A0A" if ok else "#C00000"))

    def _ok(self):
        self.manager.settings["push"] = self._current()
        self.accept()

    def exec_get(self):
        return self.exec() == QDialog.DialogCode.Accepted


class FlvRepairDialog(QDialog):
    """FLV 修复（对应录播姬工具箱）。"""

    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("FLV 修复")
        self.resize(720, 480)
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.ed_file = QLineEdit()
        self.ed_file.setPlaceholderText("选择要修复的 .flv 文件")
        row.addWidget(self.ed_file, 1)
        b = QPushButton("选择文件…")
        b.clicked.connect(self._pick_file)
        row.addWidget(b)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("输出目录"))
        self.ed_out = QLineEdit()
        self.ed_out.setPlaceholderText("留空 = 与源文件同目录（文件名加 _修复）")
        row2.addWidget(self.ed_out, 1)
        b2 = QPushButton("选择…")
        b2.clicked.connect(self._pick_dir)
        row2.addWidget(b2)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_run = QPushButton("开始修复")
        self.btn_run.setStyleSheet("background:#2e7d32;color:white;padding:5px 16px;")
        self.btn_run.clicked.connect(self._run)
        row3.addWidget(self.btn_run)
        row3.addWidget(QLabel("丢掉截断 tag、时间戳改单调递增、重算 PreviousTagSize"))
        row3.addStretch(1)
        lay.addLayout(row3)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        lay.addWidget(self.log, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.clicked.connect(lambda _b: self.reject())
        lay.addWidget(btns)

    def _pick_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 FLV 文件", "", "FLV 视频 (*.flv)")
        if p:
            self.ed_file.setText(p)

    def _pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if p:
            self.ed_out.setText(p)

    def _log(self, text):
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _run(self):
        src = self.ed_file.text().strip()
        if not src or not os.path.isfile(src):
            self._log("❌ 请先选择有效的 FLV 文件")
            return
        outdir = self.ed_out.text().strip() or os.path.dirname(src)
        os.makedirs(outdir, exist_ok=True)
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + "_修复.flv")
        self.btn_run.setEnabled(False)
        self._log(f"开始修复：{src}")

        def work():
            from toolbox.flv_repair import repair_flv
            st = repair_flv(src, dst)
            if st.get("ok"):
                self._log(f"✅ 完成：{st['tags']} 个 tag，丢弃 {st['dropped']} 个，"
                          f"时间戳修正 {st['ts_fixed']} 处，重新对齐 {st['resync']} 次")
                self._log(f"   输出：{st['out']}")
            else:
                self._log(f"❌ 失败：{st.get('error') or '未知错误'}")
            self.btn_run.setEnabled(True)

        import threading
        threading.Thread(target=work, daemon=True).start()


# ==================== 主窗口 ====================

class LiveRecordWindow(QMainWindow):
    """直播录制二级界面：底部切换 抖音 / B站，两套列表互不影响。"""

    def __init__(self, app=None, platform="douyin", room=""):
        super().__init__()
        self.app = app
        self.state = LR.load_state() or {}
        self.setWindowTitle("直播录制 · 抖音 / B站（同源录制）")
        size = self.state.get("window") or [1360, 760]
        self.resize(int(size[0]), int(size[1]))
        self.setMinimumSize(1080, 600)

        self.panes = {}
        self.current = "douyin"
        self.stack = QStackedWidget()
        for key in ("douyin", "bili"):
            pane = PlatformPane(self, key)
            self.panes[key] = pane
            self.stack.addWidget(pane)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.stack, 1)

        # ---- 底部平台切换 ----
        bottom = QWidget()
        bottom.setStyleSheet("background:#F5F6F8;border-top:1px solid #E0E0E0;")
        bh = QHBoxLayout(bottom)
        bh.setContentsMargins(10, 5, 10, 5)
        self.plat_btns = {}
        for key in ("douyin", "bili"):
            b = QPushButton("%s %s" % (LR.PLATFORMS[key]["icon"],
                                       LR.PLATFORMS[key]["name"]))
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton{padding:6px 18px;border-radius:4px;}"
                "QPushButton:checked{background:#1976d2;color:white;font-weight:bold;}")
            b.clicked.connect(lambda _c=False, k=key: self.switch_platform(k))
            self.plat_btns[key] = b
            bh.addWidget(b)
        bh.addStretch(1)
        self.plat_hint = QLabel("两个平台的列表与录制任务互相独立，切换不会中断录制")
        self.plat_hint.setStyleSheet("color:#777;font-size:11px;")
        bh.addWidget(self.plat_hint)
        v.addWidget(bottom)
        self.setCentralWidget(central)

        self.status_label = QLabel("就绪")
        sb = QStatusBar()
        sb.addWidget(self.status_label, 1)
        self.setStatusBar(sb)

        self._build_menus()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self.switch_platform(platform)
        if room:
            pane = self.panes.get(platform)
            if pane and not any(a.room == room for a in pane.manager.anchors):
                a = LR.Anchor(platform=platform, room=room, monitoring=True)
                a._want_record = False
                pane.manager.anchors.append(a)
                pane.model.refresh_all()
                pane._log("已从弹幕姬带入房间 %s" % room, "info")

    # ---------- 菜单 ----------
    def _act(self, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        a.triggered.connect(lambda _c=False: slot())
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setStatusTip(tip)
        return a

    def _build_menus(self):
        mb = self.menuBar()

        m = mb.addMenu("文件(&F)")
        m.addAction(self._act("添加主播…", lambda: self.pane.on_add(), "Ctrl+N"))
        m.addAction(self._act("批量导入…", lambda: self.pane.on_batch(), "Ctrl+I"))
        m.addSeparator()
        m.addAction(self._act("打开录制目录",
                              lambda: _open_dir(self.pane.manager.out_dir())))
        m.addAction(self._act("打开记录数据文件夹", lambda: self.pane.on_open_record_dir()))
        m.addSeparator()
        m.addAction(self._act("退出", self.close, "Ctrl+Q"))

        m = mb.addMenu("主播(&A)")
        m.addAction(self._act("加入监控", lambda: self.pane._set_flag("monitoring", True)))
        m.addAction(self._act("退出监控", lambda: self.pane._set_flag("monitoring", False)))
        m.addSeparator()
        m.addAction(self._act("加入录制", lambda: self.pane._set_flag("_want_record", True)))
        m.addAction(self._act("退出录制", lambda: self.pane._set_flag("_want_record", False)))
        m.addSeparator()
        m.addAction(self._act("立即检测开播", lambda: self.pane.on_check_now(), "F6"))
        m.addAction(self._act("播放直播源", lambda: self.pane.on_preview_stream()))
        m.addAction(self._act("开始录制", lambda: self.pane.on_start_record()))
        m.addAction(self._act("停止录制", lambda: self.pane.on_stop_record()))
        m.addAction(self._act("一键停止全部录制", lambda: self.pane.on_stop_all(), "F9"))
        m.addSeparator()
        m.addAction(self._act("独立设置…", lambda: self.pane.on_anchor_settings()))
        m.addAction(self._act("已录时间归零（重新录制）",
                              lambda: self.pane.on_reset_elapsed(True)))
        m.addAction(self._act("移动至序号…", lambda: self.pane.on_move_to_index()))
        m.addAction(self._act("修改备注…", lambda: self.pane.on_edit_remark()))
        m.addSeparator()
        m.addAction(self._act("删除选中", lambda: self.pane.on_delete(), "Del"))

        m = mb.addMenu("工具(&T)")
        m.addAction(self._act("录制设置…", lambda: self.pane.on_settings()))
        m.addAction(self._act("推送设置…", lambda: self.pane.on_push_settings()))
        m.addAction(self._act("FLV 修复…", lambda: self.pane.on_repair_flv()))
        m.addSeparator()
        m.addAction(self._act("复制直播源", lambda: self.pane.on_copy_stream()))
        m.addAction(self._act("复制直播网址", lambda: self.pane.on_copy_url()))
        m.addAction(self._act("运行日志…", lambda: self.pane.on_show_log()))

        m = mb.addMenu("设置(&S)")
        m.addAction(self._act("录制设置…", lambda: self.pane.on_settings()))
        m.addAction(self._act("推送设置…", lambda: self.pane.on_push_settings()))
        m.addSeparator()
        m.addAction(self._act("切换平台：抖音直播", lambda: self.switch_platform("douyin")))
        m.addAction(self._act("切换平台：B站直播", lambda: self.switch_platform("bili")))

        m = mb.addMenu("帮助(&H)")
        m.addAction(self._act("使用说明", self._show_help))
        m.addAction(self._act("关于", self._show_about))

    def _show_help(self):
        QMessageBox.information(
            self, "使用说明",
            "1. ＋添加主播：填房间号或直播间链接，可直接添加（识别只是补名称/状态）\n"
            "2. 打开「是否监控」→ 点「▶ 开启监控录制」，开播即自动录制\n"
            "3. 「是否录制」控制该主播开播时是否自动录；右键/批量修改可批量设置\n"
            "4. 录制设置：录制方式（下载流/录制流）、分段（按时间/按大小）、录满停止、"
            "保存目录、断流重连间隔、标题过滤、弹幕录制、封面保存\n"
            "5. 独立设置：单个主播的录制内容/清晰度/分段/录满/提醒/备注\n"
            "6. 推送设置：企业微信/钉钉/飞书/自定义 WebHook，按事件推送\n"
            "7. 工具 → FLV 修复：修复截断或时间戳错乱的录制文件\n"
            "8. 底部按钮切换抖音 / B站，两套列表与录制任务互不影响\n"
            "9. 录制文件：<保存目录>/<主播名>/<主播名>_<日期时间>.flv（音频为 .ts）")

    def _show_about(self):
        QMessageBox.information(
            self, "关于",
            "直播录制 · 抖音 / B站 同源\n\n"
            "界面与交互参考：江湖录播（DY直播数据分析）\n"
            "录制特性参考：BililiveRecorder 2.20.0\n"
            "（断线续传 / 分段 / 断流重连 / 弹幕录制 / 封面 / 标题过滤 / FLV 修复）")

    # ---------- 平台切换 ----------
    def switch_platform(self, key):
        if key not in self.panes:
            return
        self.current = key
        self.stack.setCurrentWidget(self.panes[key])
        for k, b in self.plat_btns.items():
            b.setChecked(k == key)
        self.plat_hint.setText(
            "%s · 列表与录制任务独立，切换不会中断录制" % LR.PLATFORMS[key]["name"])
        self.refresh_status()

    @property
    def pane(self):
        return self.panes[self.current]

    def refresh_status(self):
        panes = getattr(self, "panes", {})
        if len(panes) < 2:
            return
        parts = []
        for key in ("douyin", "bili"):
            m = self.panes[key].manager
            parts.append("%s %d 个（录 %d）" % (
                LR.PLATFORMS[key]["name"], len(m.anchors), m.active_count()))
        cur = self.pane.manager
        push_on = bool((cur.settings.get("push") or {}).get("enabled"))
        self.status_label.setText(
            "监控：%s　|　%s　|　轮次 %d　|　上次检测 %s　|　推送：%s　|　ffmpeg：%s" % (
                "运行中" if cur.running else "已停止",
                "　|　".join(parts), cur.cycles, cur.last_run or "-",
                "开" if push_on else "关",
                "已就绪" if LR.find_ffmpeg() else "未找到"))

    def _tick(self):
        for pane in self.panes.values():
            pane.manager.sync_tasks()
            pane.model.refresh_volatile()
        self.refresh_status()
        self._update_info()

    def _update_info(self):
        pane = self.pane
        m = pane.manager
        sel = pane._selected()
        if not sel:
            pane.info.setText("共 %d 个主播　|　监控 %d　|　录制中 %d　|　勾选 %d" % (
                len(m.anchors), sum(1 for a in m.anchors if a.monitoring),
                m.active_count(), len(pane._checked())))
            return
        a = sel[0]
        text = ("【%s】　房间 %s　|　%s　|　在线 %s　|　检测 %s　|　已录 %s　|　%s　|　%s"
                % (a.name or a.room, a.room,
                   "已开播" if a.live else "等待开播",
                   a.online or "-", (a.last_check or "-")[11:19] or "-",
                   LR.fmt_duration(m.elapsed(a)), a.rec_state or "未录制",
                   a.title or ""))
        if int(getattr(a, "content", 0) or 0) == 1:
            text += "　|　音频"
        if a.remark:
            text += "　|　备注：" + a.remark
        pane.info.setText(text)

    # ---------- 状态保存 / 关闭 ----------
    def save_state(self):
        self.state["platforms"] = {k: p.to_state() for k, p in self.panes.items()}
        self.state["window"] = [self.width(), self.height()]
        LR.save_state(self.state)

    def toast(self, msg, kind="info"):
        if self.app is not None and hasattr(self.app, "toast"):
            try:
                self.app.toast(msg, kind)
                return
            except Exception:
                pass
        self.statusBar().showMessage(msg, 4000)

    def closeEvent(self, event):
        active = sum(p.manager.active_count() for p in self.panes.values())
        if active:
            r = QMessageBox.question(
                self, "确认关闭",
                "当前有 %d 个录制任务在运行，关闭窗口会停止全部录制。确定关闭？" % active,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._timer.stop()
        self.save_state()
        for p in self.panes.values():
            p.manager.shutdown()
        if self.app is not None and getattr(self.app, "live_record_win", None) is self:
            self.app.live_record_win = None
        super().closeEvent(event)

    def shutdown(self):
        self._timer.stop()
        self.save_state()
        for p in self.panes.values():
            p.manager.shutdown()


def open_live_record_window(app, platform="douyin", room=""):
    """打开（或复用）直播录制二级界面，返回窗口实例。"""
    win = getattr(app, "live_record_win", None)
    if win is None:
        win = LiveRecordWindow(app, platform=platform, room=room)
        if app is not None:
            app.live_record_win = win
    else:
        if room and not any(a.room == room for a in win.panes[platform].manager.anchors):
            a = LR.Anchor(platform=platform, room=room, monitoring=True)
            a._want_record = False
            win.panes[platform].manager.anchors.append(a)
            win.panes[platform].model.refresh_all()
        win.switch_platform(platform)
    win.show()
    win.raise_()
    win.activateWindow()
    return win
