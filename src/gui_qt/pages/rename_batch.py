"""实用工具 · 批量重命名页（zRenamer 风格）：8 标签规则 + 时间修改 + 双列预览 + 撤销。

标签：整体 / 替换 / 添加 / 删除 / 序号 / 模板 / 文本模式 / 选项 / 时间修改
规则叠加生效：所有规则标签按 整体→替换→添加→删除→序号→模板 顺序依次应用到文件名，
最后统一预览（原文件名 → 新文件名），确认后执行；自动记录改名历史支持一键撤销。
最后的「时间修改」标签内嵌文件时间修改页，与改名规则互不影响。
"""

import os
import re
import shutil
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt import table_sort
from gui_qt.common import AutoFitTabWidget, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.pages.file_time import FileTimePage
from gui_qt.theme import current_palette


# ==================== 规则引擎（Qt-free，实现在 toolbox.rename_rules） ====================

from toolbox.rename_rules import apply_rules, join_name, split_name


class RenameBatchPage(BasePage):
    SESSION_FIELDS = ("files", "history", "cfg")

    def session_restored(self):
        self._collect()

    """zRenamer 风格批量重命名。"""

    def __init__(self, app):
        super().__init__(app)
        self.files: list[dict] = []       # {path, stem, ext, new, status}
        self.history: list[tuple[str, str]] = []   # (旧path, 新path)
        self.cfg: dict = {}
        self._icons: list = []
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_rules_card())
        layout.addWidget(self._build_table_card(), 1)
        layout.addWidget(self._build_bottom_bar())

    # ==================== 布局片段 ====================
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "caption")
        return label

    @staticmethod
    def _separator(vertical: bool = False) -> QFrame:
        line = QFrame()
        line.setStyleSheet("background: palette(shadow); border: none;")
        if vertical:
            line.setFixedWidth(1)
        else:
            line.setFixedHeight(1)
        return line

    @staticmethod
    def _icon_color(role: str = "button") -> str:
        pal = current_palette()
        if role == "primary":
            return pal.color(QPalette.ColorRole.HighlightedText).name()
        return pal.color(QPalette.ColorRole.ButtonText).name()

    def _icon_button(self, text: str, icon_name: str, variant: str = "secondary") -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("variant", variant)
        role = "primary" if variant == "primary" else "button"
        btn.setIcon(line_icon(icon_name, self._icon_color(role), 16))
        btn.setIconSize(QSize(16, 16))
        self._icons.append((btn, icon_name, role))
        return btn

    def on_theme_changed(self):
        for btn, name, role in self._icons:
            btn.setIcon(line_icon(name, self._icon_color(role), 16))

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)

        add_file_btn = self._icon_button("添加文件", "plus", "primary")
        add_file_btn.setToolTip("选择要改名的文件（可多选，也可直接把文件拖入本页）")
        add_file_btn.clicked.connect(lambda: self._add(False))
        h.addWidget(add_file_btn)

        add_dir_btn = self._icon_button("添加文件夹", "folder")
        add_dir_btn.setToolTip("把文件夹内的文件加入列表（是否含子文件夹由「选项」页签决定）")
        add_dir_btn.clicked.connect(lambda: self._add(True))
        h.addWidget(add_dir_btn)

        h.addWidget(self._separator(vertical=True))
        clear_btn = self._icon_button("清空", "trash")
        clear_btn.setToolTip("清空文件列表与改名记录（不动磁盘上的文件）")
        clear_btn.clicked.connect(self._clear)
        h.addWidget(clear_btn)

        refresh_btn = self._icon_button("刷新预览", "refresh")
        refresh_btn.setToolTip("按当前规则重新计算全部新文件名")
        refresh_btn.clicked.connect(self._refresh)
        h.addWidget(refresh_btn)

        h.addStretch(1)
        h.addWidget(self._caption("可直接拖入文件 / 文件夹"))
        h.addWidget(self._separator(vertical=True))
        self.undo_btn = self._icon_button("撤销上次", "arrow_left")
        self.undo_btn.setToolTip("把上一次「执行改名」的文件全部改回原名")
        self.undo_btn.clicked.connect(self._undo)
        h.addWidget(self.undo_btn)
        return bar

    def _build_rules_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # 规则标签（高度跟随当前标签页，短表单不再被「时间修改」撑高留白）
        self.tabs = AutoFitTabWidget(card)
        self.tabs.addTab(self._tab_whole(), "整体")
        self.tabs.addTab(self._tab_replace(), "替换")
        self.tabs.addTab(self._tab_add(), "添加")
        self.tabs.addTab(self._tab_del(), "删除")
        self.tabs.addTab(self._tab_num(), "序号")
        self.tabs.addTab(self._tab_template(), "模板")
        self.tabs.addTab(self._tab_textmode(), "文本模式")
        self.tabs.addTab(self._tab_options(), "选项")
        self.tabs.addTab(FileTimePage(self.app), "时间修改")
        v.addWidget(self.tabs)
        return card

    def _build_table_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("预览", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.cnt = self._caption("0 个文件")
        self.cnt.setToolTip("总数 / 过滤后数量 / 将要改名的数量")
        head.addWidget(self.cnt)
        v.addLayout(head)

        self.table = QTableWidget(0, 6, card)
        self.table.setHorizontalHeaderLabels(["序号", "文件名", "新文件名", "状态", "扩展名", "大小"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for col in (3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        # 行号 == 当前显示记录下标：排序时同步重排显示列表，按选中行执行改名仍正确
        self._shown: list[dict] = []
        self._sorter = table_sort.install(self.table, skip_columns=())
        v.addWidget(self.table, 1)
        return card

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)

        sel_all = self._icon_button("全选", "check")
        sel_all.setToolTip("选中全部行（只对选中行执行改名）")
        sel_all.clicked.connect(lambda: self.table.selectAll())
        h.addWidget(sel_all)
        invert = self._icon_button("反选", "swap")
        invert.setToolTip("反选当前选中状态")
        invert.clicked.connect(self._invert)
        h.addWidget(invert)

        h.addWidget(self._separator(vertical=True))
        self.status = self._caption("")
        h.addWidget(self.status, 1)

        run_btn = self._icon_button("执行改名", "check", "primary")
        run_btn.setToolTip("对将要改名的文件执行重命名（有选中行时只改选中行）")
        run_btn.clicked.connect(self._run)
        h.addWidget(run_btn)
        return bar

    # ==================== 标签页 ====================
    def _tab_whole(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("大小写："))
        self.w_case = QComboBox()
        self.w_case.addItems(["（不变）", "全部大写", "全部小写", "首字母大写"])
        self.w_case.setMinimumWidth(128)
        self.w_case.setMaximumWidth(300)
        self.w_case.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_case, 1)
        r1.addSpacing(6)
        r1.addWidget(QLabel("数字："))
        self.w_num = QComboBox()
        self.w_num.addItems(["（不变）", "加随机数", "序号化"])
        self.w_num.setMinimumWidth(128)
        self.w_num.setMaximumWidth(300)
        self.w_num.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_num, 1)
        r1.addSpacing(6)
        r1.addWidget(QLabel("转换："))
        self.w_conv = QComboBox()
        self.w_conv.addItems(["（不转换）", "全角→半角", "半角→全角"])
        self.w_conv.setMinimumWidth(128)
        self.w_conv.setMaximumWidth(300)
        self.w_conv.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_conv, 1)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(QLabel("扩展名："))
        self.w_ext_case = QComboBox()
        self.w_ext_case.addItems(["（不变）", "大写", "小写", "删除"])
        self.w_ext_case.setMinimumWidth(128)
        self.w_ext_case.setMaximumWidth(300)
        self.w_ext_case.currentIndexChanged.connect(lambda i: self._collect())
        r2.addWidget(self.w_ext_case, 1)
        r2.addSpacing(6)
        r2.addWidget(QLabel("改为："))
        self.w_new_ext = QLineEdit()
        self.w_new_ext.setPlaceholderText("如 png（留空不改）")
        self.w_new_ext.setMinimumWidth(128)
        self.w_new_ext.setMaximumWidth(300)
        self.w_new_ext.textChanged.connect(self._collect)
        r2.addWidget(self.w_new_ext, 1)
        r2.addSpacing(6)
        r2.addWidget(QLabel("过滤："))
        self.w_filter = QComboBox()
        self.w_filter.addItems(["全部文件", "仅图片", "仅视频", "仅音频", "仅字幕", "仅文档"])
        self.w_filter.setMinimumWidth(120)
        self.w_filter.setMaximumWidth(300)
        self.w_filter.currentIndexChanged.connect(lambda i: self._refresh())
        r2.addWidget(self.w_filter, 1)
        v.addLayout(r2)
        v.addStretch(1)
        return w

    def _tab_replace(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        r0 = QHBoxLayout()
        r0.setSpacing(12)
        self.r_case = QCheckBox("区分大小写")
        self.r_case.setChecked(True)
        self.r_all = QCheckBox("替换所有")
        self.r_all.setChecked(True)
        self.r_regex = QCheckBox("正则表达式")
        for cb in (self.r_case, self.r_all, self.r_regex):
            cb.stateChanged.connect(self._collect)
            r0.addWidget(cb)
        r0.addStretch(1)
        v.addLayout(r0)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.r_find = QLineEdit()
        self.r_find.setPlaceholderText("查找内容")
        self.r_find.textChanged.connect(self._collect)
        r1.addWidget(self.r_find, 1)
        mid = self._caption("→")
        mid.setToolTip("替换为")
        r1.addWidget(mid)
        self.r_to = QLineEdit()
        self.r_to.setPlaceholderText("替换为（空=删除）")
        self.r_to.textChanged.connect(self._collect)
        r1.addWidget(self.r_to, 1)
        v.addLayout(r1)
        v.addStretch(1)
        return w

    def _tab_add(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("前缀："))
        self.a_prefix = QLineEdit()
        self.a_prefix.setPlaceholderText("加在文件名最前面")
        self.a_prefix.textChanged.connect(self._collect)
        r1.addWidget(self.a_prefix, 1)
        r1.addWidget(QLabel("后缀："))
        self.a_suffix = QLineEdit()
        self.a_suffix.setPlaceholderText("加在文件名最后面")
        self.a_suffix.textChanged.connect(self._collect)
        r1.addWidget(self.a_suffix, 1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(QLabel("位置："))
        self.a_pos = QLineEdit("0")
        self.a_pos.setFixedWidth(56)
        self.a_pos.textChanged.connect(self._collect)
        r2.addWidget(self.a_pos)
        self.a_right = QCheckBox("从右往左")
        self.a_right.stateChanged.connect(self._collect)
        r2.addWidget(self.a_right)
        r2.addWidget(QLabel("文本："))
        self.a_pos_text = QLineEdit()
        self.a_pos_text.setPlaceholderText("插入到指定位置的文本")
        self.a_pos_text.textChanged.connect(self._collect)
        r2.addWidget(self.a_pos_text, 1)
        v.addLayout(r2)
        r3 = QHBoxLayout()
        r3.setSpacing(8)
        r3.addWidget(QLabel("关键词："))
        self.a_kw = QLineEdit()
        self.a_kw.setPlaceholderText("定位用的关键词")
        self.a_kw.textChanged.connect(self._collect)
        r3.addWidget(self.a_kw, 1)
        r3.addWidget(QLabel("添加："))
        self.a_kw_text = QLineEdit()
        self.a_kw_text.setPlaceholderText("在关键词前/后插入的文本")
        self.a_kw_text.textChanged.connect(self._collect)
        r3.addWidget(self.a_kw_text, 1)
        self.a_after = QCheckBox("关键词后面")
        self.a_after.setChecked(True)
        self.a_after.stateChanged.connect(self._collect)
        r3.addWidget(self.a_after)
        v.addLayout(r3)
        v.addStretch(1)
        return w

    def _tab_del(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._caption("按类型删除"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.d_types = {}
        for i, (key, label) in enumerate(
                [("space", "空格"), ("digit", "数字"), ("lower", "小写"),
                 ("upper", "大写"), ("chinese", "中文"), ("special", "特殊字符")]):
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._collect)
            self.d_types[key] = cb
            grid.addWidget(cb, i // 3, i % 3)
        left.addLayout(grid)
        left.addStretch(1)
        top.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(self._caption("按位置删除"))
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(QLabel("从第"))
        self.d_pos = QLineEdit("0")
        self.d_pos.setFixedWidth(46)
        self.d_pos.textChanged.connect(self._collect)
        r2.addWidget(self.d_pos)
        r2.addWidget(QLabel("个字符起"))
        self.d_right = QCheckBox("从右往左")
        self.d_right.stateChanged.connect(self._collect)
        r2.addWidget(self.d_right)
        right.addLayout(r2)
        r2b = QHBoxLayout()
        r2b.setSpacing(8)
        r2b.addWidget(QLabel("删除"))
        self.d_cnt = QLineEdit("0")
        self.d_cnt.setFixedWidth(46)
        self.d_cnt.textChanged.connect(self._collect)
        r2b.addWidget(self.d_cnt)
        r2b.addWidget(QLabel("个字符"))
        r2b.addStretch(1)
        right.addLayout(r2b)
        right.addSpacing(8)
        right.addWidget(self._caption("按关键词删除"))
        r3 = QHBoxLayout()
        r3.setSpacing(8)
        self.d_kw = QLineEdit()
        self.d_kw.setPlaceholderText("定位用的关键词")
        self.d_kw.textChanged.connect(self._collect)
        r3.addWidget(self.d_kw, 1)
        r3.addWidget(QLabel("往"))
        self.d_dir = QComboBox()
        self.d_dir.addItems(["右", "左"])
        self.d_dir.setFixedWidth(64)
        self.d_dir.currentIndexChanged.connect(lambda i: self._collect())
        r3.addWidget(self.d_dir)
        r3.addWidget(QLabel("删除"))
        self.d_kw_cnt = QLineEdit("0")
        self.d_kw_cnt.setFixedWidth(46)
        self.d_kw_cnt.textChanged.connect(self._collect)
        r3.addWidget(self.d_kw_cnt)
        r3.addWidget(QLabel("个字符"))
        right.addLayout(r3)
        r4 = QHBoxLayout()
        self.d_incl = QCheckBox("包括关键词本身")
        self.d_incl.stateChanged.connect(self._collect)
        r4.addWidget(self.d_incl)
        r4.addStretch(1)
        right.addLayout(r4)
        right.addStretch(1)
        top.addLayout(right, 1)
        v.addLayout(top)
        v.addStretch(1)
        return w

    def _tab_num(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.n_on = QCheckBox("启用序号")
        self.n_on.setToolTip("按列表顺序给文件名加序号")
        self.n_on.stateChanged.connect(self._collect)
        r1.addWidget(self.n_on)
        self.n_mode = QComboBox()
        self.n_mode.addItems(["后缀加序号", "前缀加序号"])
        self.n_mode.setFixedWidth(120)
        self.n_mode.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.n_mode)
        r1.addSpacing(6)
        r1.addWidget(QLabel("起始值："))
        self.n_start = QLineEdit("1")
        self.n_start.setFixedWidth(56)
        self.n_start.textChanged.connect(self._collect)
        r1.addWidget(self.n_start)
        r1.addWidget(QLabel("增量："))
        self.n_step = QLineEdit("1")
        self.n_step.setFixedWidth(56)
        self.n_step.textChanged.connect(self._collect)
        r1.addWidget(self.n_step)
        r1.addWidget(QLabel("位数："))
        self.n_width = QLineEdit("2")
        self.n_width.setFixedWidth(56)
        self.n_width.textChanged.connect(self._collect)
        r1.addWidget(self.n_width)
        r1.addStretch(1)
        v.addLayout(r1)
        v.addStretch(1)
        return w

    def _tab_template(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("模板："))
        self.t_entry = QLineEdit()
        self.t_entry.setPlaceholderText("如 {序号}_{名}，留空=不启用")
        self.t_entry.setToolTip("例：封茗囧菌_{日期}_{名}；变量见下方说明")
        self.t_entry.textChanged.connect(self._collect)
        r1.addWidget(self.t_entry, 1)
        v.addLayout(r1)
        hint = self._caption("可用变量：{名}/{name} 原文件名 · {扩展}/{ext} · {序号}/{num} 三位序号 · "
                             "{日期} 2026-08-21 · {时间} · {年}{月}{日}")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _tab_textmode(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        hint = self._caption("直接编辑新文件名（每行一个，与文件顺序对应；空行=保持预览结果）。"
                             "从其它标签切到这里会自动带入当前预览。")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.tm_edit = QTextEdit()
        self.tm_edit.textChanged.connect(self._textmode_apply)
        v.addWidget(self.tm_edit, 1)
        return w

    def _tab_options(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(8)
        self.o_ignore_ext = QCheckBox("忽略扩展名（规则只作用于主文件名）")
        self.o_ignore_ext.setChecked(True)
        self.o_ignore_ext.stateChanged.connect(self._collect)
        grid.addWidget(self.o_ignore_ext, 0, 0)
        self.o_sub = QCheckBox("包含子文件夹")
        self.o_sub.setToolTip("添加文件夹时是否递归扫描子文件夹")
        grid.addWidget(self.o_sub, 0, 1)
        self.o_dir_rename = QCheckBox("文件夹改名")
        self.o_dir_rename.setToolTip("添加文件夹时把子文件夹本身也加入改名列表")
        grid.addWidget(self.o_dir_rename, 1, 0)
        self.o_conflict = QCheckBox("解决同名冲突（自动加序号）")
        self.o_conflict.setChecked(True)
        self.o_conflict.stateChanged.connect(self._collect)
        grid.addWidget(self.o_conflict, 1, 1)
        grid.setColumnStretch(2, 1)
        v.addLayout(grid)
        v.addStretch(1)
        return w

    # ==================== 收集配置 + 预览 ====================
    def _int(self, edit: QLineEdit, default: int) -> int:
        try:
            return int(edit.text())
        except ValueError:
            return default

    def _collect(self, *_):
        self.cfg = {
            "whole": {
                "case": ["", "upper", "lower", "capital"][self.w_case.currentIndex()],
                "num": ["", "random", "inc"][self.w_num.currentIndex()],
                "convert": ["", "full2half", "half2full"][self.w_conv.currentIndex()],
                "ext_case": ["", "upper", "lower", "delete"][self.w_ext_case.currentIndex()],
                "new_ext": self.w_new_ext.text().strip(),
            },
            "replace": {
                "find": self.r_find.text(), "to": self.r_to.text(),
                "case": self.r_case.isChecked(), "all": self.r_all.isChecked(),
                "regex": self.r_regex.isChecked(),
            },
            "add": {
                "prefix": self.a_prefix.text(), "suffix": self.a_suffix.text(),
                "pos_idx": self._int(self.a_pos, 0), "pos_text": self.a_pos_text.text(),
                "from_right": self.a_right.isChecked(),
                "kw": self.a_kw.text(), "kw_text": self.a_kw_text.text(),
                "kw_after": self.a_after.isChecked(),
            },
            "del": {
                "types": [k for k, cb in self.d_types.items() if cb.isChecked()],
                "pos": self._int(self.d_pos, 0), "cnt": self._int(self.d_cnt, 0),
                "from_right": self.d_right.isChecked(),
                "kw": self.d_kw.text(), "kw_cnt": self._int(self.d_kw_cnt, 0),
                "dir_right": self.d_dir.currentIndex() == 0,
                "incl_kw": self.d_incl.isChecked(),
            },
            "num": {
                "on": self.n_on.isChecked(), "mode": "suffix" if self.n_mode.currentIndex() == 0 else "prefix",
                "start": self._int(self.n_start, 1), "step": self._int(self.n_step, 1),
                "width": max(1, self._int(self.n_width, 1)),
            },
            "template": self.t_entry.text().strip(),
            "options": {
                "ignore_ext": self.o_ignore_ext.isChecked(),
                "conflict": self.o_conflict.isChecked(),
            },
        }
        # hasattr 判断不行：_fill_textmode 结束后 _tm_busy 恒存在（值 False），
        # 会把之后所有规则变更的自动预览全部短路（规则怎么改都不生效的根因）
        if not getattr(self, "_tm_busy", False):
            self._refresh()

    def _filter_files(self) -> list[dict]:
        f = self.w_filter.currentIndex()
        exts = {1: (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"),
                2: (".mp4", ".flv", ".mkv", ".avi", ".mov", ".ts", ".webm"),
                3: (".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg"),
                4: (".srt", ".ass", ".vtt", ".lrc"),
                5: (".txt", ".md", ".doc", ".docx", ".pdf", ".xlsx")}
        if f == 0:
            return self.files
        keep = exts.get(f, ())
        return [x for x in self.files if x["ext"].lower() in keep or ("." + x["ext"].lower()) in keep]

    def _refresh(self):
        if getattr(self, "_tm_busy", False):
            return
        now = datetime.now()
        items = self._filter_files()
        cfg = getattr(self, "cfg", {})
        conflict = cfg.get("options", {}).get("conflict", True)
        used: dict[str, int] = {}
        for i, item in enumerate(items):
            stem, ext = split_name(os.path.basename(item["path"]), cfg.get("options", {}).get("ignore_ext", True))
            new_stem, new_ext = apply_rules(stem, ext, cfg, i, now)
            if not new_stem or new_stem in (".", ".."):
                new_stem = stem or "未命名"
            new_name = join_name(new_stem, new_ext)
            if conflict:
                key = new_name.lower()
                if key in used or any(new_name == os.path.basename(o["path"]) and o is not item for o in self.files):
                    used[key] = used.get(key, 0) + 1
                    dot = new_name.rfind(".")
                    base, e = (new_name[:dot], new_name[dot:]) if dot > 0 else (new_name, "")
                    new_name = f"{base}_{used[key]}{e}"
            item["new"] = new_name
        self._render()

    def _textmode_apply(self):
        lines = self.tm_edit.toPlainText().splitlines()
        items = self._filter_files()
        for i, item in enumerate(items):
            if i < len(lines) and lines[i].strip():
                item["new"] = lines[i].strip()
        self._render()

    def _render(self):
        self.table.setRowCount(0)
        items = self._filter_files()
        shown = items[:2000]
        for i, item in enumerate(shown):
            self.table.insertRow(i)
            old = os.path.basename(item["path"])
            new = item.get("new", old)
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(old))
            changed = QTableWidgetItem(new if new != old else "（不变）")
            if new != old:
                changed.setForeground(Qt.green)
            self.table.setItem(i, 2, changed)
            self.table.setItem(i, 3, QTableWidgetItem(item.get("status", "")))
            self.table.setItem(i, 4, QTableWidgetItem(item["ext"] or "（无）"))
            try:
                size = os.path.getsize(item["path"])
                size_txt = f"{size/1024:.0f} KB" if size > 1024 * 1024 else f"{size/1024/1024:.1f} MB"
            except OSError:
                size_txt = "?"
            self.table.setItem(i, 5, QTableWidgetItem(size_txt))
        # 排序只改显示顺序（不动 self.files，序号/文本模式顺序保持不变）
        self._sorter.attach(shown).apply()
        self._shown = shown
        n_changed = sum(1 for x in items if x.get("new") and x["new"] != os.path.basename(x["path"]))
        self.cnt.setText(f"{len(self.files)} 个文件（过滤后 {len(items)}，将改名 {n_changed} 个）")

    # ==================== 文件管理 ====================
    def _add(self, from_dir: bool):
        if from_dir:
            d = QFileDialog.getExistingDirectory(self, "选择文件夹")
            if not d:
                return
            include_sub = self.o_sub.isChecked()
            new = []
            if include_sub:
                for root, _dirs, names in os.walk(d):
                    if not self.o_dir_rename.isChecked() or root != d:
                        new.extend(os.path.join(root, n) for n in names)
                if self.o_dir_rename.isChecked():
                    for root, dirs, _names in os.walk(d):
                        new.extend(os.path.join(root, x) for x in dirs)
                    new = list(dict.fromkeys(new))
            else:
                new = [os.path.join(d, n) for n in os.listdir(d) if
                       os.path.isfile(os.path.join(d, n)) or self.o_dir_rename.isChecked()]
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, "选择文件")
            new = paths
        existing = {x["path"].lower() for x in self.files}
        for p in new:
            if p.lower() not in existing:
                stem, ext = split_name(os.path.basename(p), True)
                self.files.append({"path": p, "stem": stem, "ext": ext, "new": None, "status": ""})
        self._collect()
        # 文本模式带入当前预览
        self._fill_textmode()

    def _fill_textmode(self):
        self._tm_busy = True
        try:
            self.tm_edit.setPlainText("\n".join(x.get("new") or os.path.basename(x["path"]) for x in self._filter_files()))
        finally:
            self._tm_busy = False

    def _clear(self):
        self.files.clear()
        self.history.clear()
        self._render()

    def _invert(self):
        sm = self.table.selectionModel()
        for r in range(self.table.rowCount()):
            idx = self.table.model().index(r, 0)
            if sm.isSelected(idx):
                sm.select(idx, sm.selectionFlags() & ~Qt.ItemIsSelectable | Qt.NoItemFlags)
        for r in range(self.table.rowCount()):
            idx = self.table.model().index(r, 0)
            if not sm.isSelected(idx):
                sm.select(idx, Qt.ItemIsSelectable | Qt.Selected)
            else:
                sm.select(idx, Qt.ItemIsSelectable | Qt.Deselect)

    # ==================== 执行 / 撤销 ====================
    def _run(self):
        items = self._filter_files()
        pending = [x for x in items if x.get("new") and x["new"] != os.path.basename(x["path"])]
        if not pending:
            self.app.toast("没有需要改名的文件（先调整规则再预览）", "warning")
            return
        sel_rows = set(idx.row() for idx in self.table.selectionModel().selectedRows()) if \
            self.table.selectionModel().selectedRows() else None
        # 选中行号按表格当前显示顺序（可能已排序）取记录
        shown = self._shown or items
        batch = pending if sel_rows is None else [x for i, x in enumerate(shown) if i in sel_rows and x in pending]

        def job():
            ok, fail = 0, []
            self.history.clear()
            for item in batch:
                folder = os.path.dirname(item["path"])
                dst = os.path.join(folder, item["new"])
                try:
                    if os.path.exists(dst):
                        fail.append((item, "目标已存在"))
                        item["status"] = "冲突"
                        continue
                    os.rename(item["path"], dst)
                    self.history.append((dst, item["path"]))   # 撤销用
                    item["path"] = dst
                    item["status"] = "成功"
                    ok += 1
                except OSError as e:
                    fail.append((item, str(e)))
                    item["status"] = "失败"
            return ok, fail

        def done(res, err):
            if err:
                self.status.setText(f"执行出错：{err}")
                return
            ok, fail = res
            stem_, ext_ = split_name(os.path.basename(batch[0]["path"]), True)
            self.status.setText(f"改名完成 {ok}/{len(batch)}" + (f"，{len(fail)} 失败" if fail else ""))
            self.app.toast(f"批量改名完成 {ok}/{len(batch)}", "success" if not fail else "warning")
            self._render()
            self._fill_textmode()

        run_bg(job, done)

    def _undo(self):
        if not self.history:
            self.app.toast("没有可撤销的改名记录", "warning")
            return
        pairs = list(self.history)

        def job():
            ok = 0
            for new, old in reversed(pairs):
                try:
                    os.rename(new, old)
                    ok += 1
                except OSError:
                    pass
            self.history.clear()
            return ok

        def done(ok, err):
            if err:
                self.status.setText(f"撤销出错：{err}")
                return
            self.status.setText(f"已撤销 {ok} 个")
            self.app.toast(f"撤销 {ok} 个改名", "info")
            for x in self.files:
                x["status"] = ""
            self._collect()
            self._fill_textmode()

        run_bg(job, done)

    # ==================== 拖放 ====================
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        from PySide6.QtCore import QUrl
        added = False
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                for root, _dirs, names in os.walk(p):
                    for n in names:
                        fp = os.path.join(root, n)
                        if not any(x["path"].lower() == fp.lower() for x in self.files):
                            stem, ext = split_name(os.path.basename(fp), True)
                            self.files.append({"path": fp, "stem": stem, "ext": ext, "new": None, "status": ""})
                            added = True
            elif os.path.isfile(p):
                if not any(x["path"].lower() == p.lower() for x in self.files):
                    stem, ext = split_name(os.path.basename(p), True)
                    self.files.append({"path": p, "stem": stem, "ext": ext, "new": None, "status": ""})
                    added = True
        if added:
            self._collect()
            self._fill_textmode()
