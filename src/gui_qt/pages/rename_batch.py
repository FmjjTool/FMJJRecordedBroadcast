"""实用工具 · 批量重命名页（zRenamer 风格）：8 标签规则 + 双列预览 + 撤销。

标签：整体 / 替换 / 添加 / 删除 / 序号 / 模板 / 文本模式 / 选项
规则叠加生效：所有标签的规则按 整体→替换→添加→删除→序号→模板 顺序依次应用到文件名，
最后统一预览（原文件名 → 新文件名），确认后执行；自动记录改名历史支持一键撤销。
"""

import os
import re
import shutil
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage


# ==================== 规则引擎 ====================

def split_name(filename: str, ignore_ext: bool) -> tuple[str, str]:
    """按需拆分 主名.扩展名。"""
    if not ignore_ext:
        return filename, ""
    dot = filename.rfind(".")
    if dot <= 0:
        return filename, ""
    return filename[:dot], filename[dot + 1:]


def join_name(stem: str, ext: str) -> str:
    return f"{stem}.{ext}" if ext else stem


_SPECIAL_RE = re.compile(r"[!-/:-@\[-`{-~\s]")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def apply_rules(stem: str, ext: str, cfg: dict, index: int, now: datetime) -> tuple[str, str]:
    """按标签顺序应用全部规则，返回 (新主名, 新扩展名)。"""
    # ---------- 整体 ----------
    w = cfg.get("whole", {})
    case = w.get("case", "")
    if case == "upper":
        stem = stem.upper()
    elif case == "lower":
        stem = stem.lower()
    elif case == "capital":
        stem = stem[:1].upper() + stem[1:]
    num_mode = w.get("num", "")
    if num_mode == "random":
        import random
        stem = f"{stem}_{random.randint(1000, 9999)}"
    conv = w.get("convert", "")
    if conv == "full2half":
        stem = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in stem)
    elif conv == "half2full":
        stem = "".join(chr(ord(c) + 0xFEE0) if 33 <= ord(c) <= 126 else c for c in stem)
    ext_case = w.get("ext_case", "")
    if ext_case == "upper" and ext:
        ext = ext.upper()
    elif ext_case == "lower" and ext:
        ext = ext.lower()
    elif ext_case == "delete":
        ext = ""
    new_ext = w.get("new_ext", "")
    if new_ext:
        ext = new_ext.lstrip(".")

    # ---------- 替换 ----------
    r = cfg.get("replace", {})
    find, to = r.get("find", ""), r.get("to", "")
    if find:
        count = 0 if r.get("all", True) else 1
        if r.get("regex", False):
            try:
                stem = re.sub(find, to, stem, count=0 if count == 0 else 1)
            except re.error:
                pass
        else:
            if not r.get("case", False):
                stem = re.sub(re.escape(find), to.replace("\\", "\\\\"), stem,
                              count=count, flags=re.IGNORECASE)
            else:
                stem = stem.replace(find, to, count) if count else stem.replace(find, to)

    # ---------- 添加 ----------
    a = cfg.get("add", {})
    stem = a.get("prefix", "") + stem + a.get("suffix", "")
    pos_text = a.get("pos_text", "")
    pos_idx = a.get("pos_idx", 0)
    if pos_text:
        if a.get("from_right", False):
            idx = max(len(stem) - pos_idx, 0)
        else:
            idx = min(pos_idx, len(stem))
        stem = stem[:idx] + pos_text + stem[idx:]
    kw = a.get("kw", "")
    if kw:
        after = a.get("kw_after", True)
        found = stem.find(kw)
        if found >= 0:
            cut = found + len(kw) if after else found
            stem = stem[:cut] + a.get("kw_text", "") + stem[cut:]

    # ---------- 删除 ----------
    d = cfg.get("del", {})
    types = d.get("types", [])
    if types:
        def _keep(c):
            if "space" in types and c.isspace():
                return False
            if "digit" in types and c.isdigit():
                return False
            if "lower" in types and c.islower() and c.isascii():
                return False
            if "upper" in types and c.isupper() and c.isascii():
                return False
            if "chinese" in types and _CHINESE_RE.match(c):
                return False
            if "special" in types and _SPECIAL_RE.match(c):
                return False
            return True
        stem = "".join(c for c in stem if _keep(c))
    d_pos = d.get("pos", 0)
    d_cnt = d.get("cnt", 0)
    if d_cnt > 0:
        if d.get("from_right", False):
            stem = stem[:max(len(stem) - d_pos - d_cnt, 0)] + stem[max(len(stem) - d_pos, 0):]
        else:
            stem = stem[:d_pos] + stem[d_pos + d_cnt:]
    d_kw = d.get("kw", "")
    if d_kw:
        found = stem.find(d_kw)
        if found >= 0:
            right = d.get("dir_right", True)
            d_kw_cnt = d.get("kw_cnt", 0)
            include = d.get("incl_kw", False)
            if right:
                start = found + len(d_kw) if not include else found
                n = d_kw_cnt + (len(d_kw) if include else 0)
                stem = stem[:start] + stem[start + n:]
            else:
                end = found + len(d_kw) if include else found
                start = max(end - d_kw_cnt, 0)
                stem = stem[:start] + stem[end:]

    # ---------- 序号 ----------
    n = cfg.get("num", {})
    if n.get("on", False):
        start, step, width = n.get("start", 1), n.get("step", 1), n.get("width", 1)
        no = start + index * step
        stem = f"{stem}{no:0{width}d}" if n.get("mode", "suffix") == "suffix" else f"{no:0{width}d}_{stem}"

    # ---------- 模板 ----------
    t = cfg.get("template", "")
    if t:
        rep = {
            "{名}": stem, "{name}": stem,
            "{扩展}": ext, "{ext}": ext,
            "{序号}": f"{index + 1:03d}", "{num}": f"{index + 1:03d}",
            "{日期}": now.strftime("%Y-%m-%d"), "{date}": now.strftime("%Y-%m-%d"),
            "{时间}": now.strftime("%H%M%S"),
            "{年}": now.strftime("%Y"), "{月}": now.strftime("%m"), "{日}": now.strftime("%d"),
        }
        for k, v in rep.items():
            t = t.replace(k, v)
        if ("{ext}" in cfg["template"]) or ("{扩展}" in cfg["template"]):
            ext = ""    # 模板已消费扩展名
        stem = t

    return stem, ext


class RenameBatchPage(BasePage):
    """zRenamer 风格批量重命名。"""

    def __init__(self, app):
        super().__init__(app)
        self.files: list[dict] = []       # {path, stem, ext, new, status}
        self.history: list[tuple[str, str]] = []   # (旧path, 新path)
        self.cfg: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.setAcceptDrops(True)

        # 工具栏
        bar = QHBoxLayout()
        for text, fn in [("📄 添加文件", lambda: self._add(False)), ("📂 添加文件夹", lambda: self._add(True)),
                         ("🧹 清空", self._clear), ("🔄 刷新预览", self._refresh),
                         ("↩ 撤销上次", self._undo)]:
            b = QPushButton(text)
            if text == "📄 添加文件":
                b.setStyleSheet("background:#1976d2;color:white;")
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.cnt = QLabel("0 个文件（可直接拖入文件/文件夹）")
        self.cnt.setStyleSheet("color:#888;")
        bar.addWidget(self.cnt, 1)
        layout.addLayout(bar)

        # 规则标签
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_whole(), "整体")
        self.tabs.addTab(self._tab_replace(), "替换")
        self.tabs.addTab(self._tab_add(), "添加")
        self.tabs.addTab(self._tab_del(), "删除")
        self.tabs.addTab(self._tab_num(), "序号")
        self.tabs.addTab(self._tab_template(), "模板")
        self.tabs.addTab(self._tab_textmode(), "文本模式")
        self.tabs.addTab(self._tab_options(), "选项")
        layout.addWidget(self.tabs)

        # 预览表
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["序号", "文件名", "新文件名", "状态", "扩展名", "大小"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table, 1)

        # 底部
        bottom = QHBoxLayout()
        sel_all = QPushButton("全选")
        sel_all.clicked.connect(lambda: self.table.selectAll())
        invert = QPushButton("反选")
        invert.clicked.connect(self._invert)
        run_btn = QPushButton("✅ 执行改名")
        run_btn.setStyleSheet("background:#2e7d32;color:white;padding:6px 22px;font-weight:bold;")
        run_btn.clicked.connect(self._run)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#888;")
        bottom.addWidget(sel_all)
        bottom.addWidget(invert)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(run_btn)
        layout.addLayout(bottom)

    # ==================== 标签页 ====================
    def _tab_whole(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("大小写："))
        self.w_case = QComboBox()
        self.w_case.addItems(["（不变）", "全部大写", "全部小写", "首字母大写"])
        self.w_case.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_case)
        r1.addWidget(QLabel("数字："))
        self.w_num = QComboBox()
        self.w_num.addItems(["（不变）", "加随机数", "序号化"])
        self.w_num.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_num)
        r1.addWidget(QLabel("转换："))
        self.w_conv = QComboBox()
        self.w_conv.addItems(["（不转换）", "全角→半角", "半角→全角"])
        self.w_conv.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.w_conv)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("扩展名："))
        self.w_ext_case = QComboBox()
        self.w_ext_case.addItems(["（不变）", "大写", "小写", "删除"])
        self.w_ext_case.currentIndexChanged.connect(lambda i: self._collect())
        r2.addWidget(self.w_ext_case)
        r2.addWidget(QLabel("改为："))
        self.w_new_ext = QLineEdit()
        self.w_new_ext.setPlaceholderText("如 png（留空不改）")
        self.w_new_ext.setFixedWidth(120)
        self.w_new_ext.textChanged.connect(self._collect)
        r2.addWidget(self.w_new_ext)
        r2.addWidget(QLabel("过滤："))
        self.w_filter = QComboBox()
        self.w_filter.addItems(["全部文件", "仅图片", "仅视频", "仅音频", "仅字幕", "仅文档"])
        self.w_filter.currentIndexChanged.connect(lambda i: self._refresh())
        r2.addWidget(self.w_filter)
        v.addLayout(r2)
        v.addStretch(1)
        return w

    def _tab_replace(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        r0 = QHBoxLayout()
        self.r_case = QCheckBox("区分大小写")
        self.r_case.setChecked(True)
        self.r_all = QCheckBox("替换所有")
        self.r_all.setChecked(True)
        self.r_regex = QCheckBox("正则表达式")
        for cb in (self.r_case, self.r_all, self.r_regex):
            cb.stateChanged.connect(self._collect)
            r0.addWidget(cb)
        v.addLayout(r0)
        r1 = QHBoxLayout()
        self.r_find = QLineEdit()
        self.r_find.setPlaceholderText("查找内容")
        self.r_find.textChanged.connect(self._collect)
        r1.addWidget(self.r_find, 1)
        mid = QLabel("替换为 ?")
        mid.setStyleSheet("color:#555;")
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
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("前缀："))
        self.a_prefix = QLineEdit()
        self.a_prefix.textChanged.connect(self._collect)
        r1.addWidget(self.a_prefix)
        r1.addWidget(QLabel("后缀："))
        self.a_suffix = QLineEdit()
        self.a_suffix.textChanged.connect(self._collect)
        r1.addWidget(self.a_suffix)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("位置："))
        self.a_pos = QLineEdit("0")
        self.a_pos.setFixedWidth(50)
        self.a_pos.textChanged.connect(self._collect)
        r2.addWidget(self.a_pos)
        self.a_right = QCheckBox("从右往左")
        self.a_right.stateChanged.connect(self._collect)
        r2.addWidget(self.a_right)
        r2.addWidget(QLabel("文本："))
        self.a_pos_text = QLineEdit()
        self.a_pos_text.textChanged.connect(self._collect)
        r2.addWidget(self.a_pos_text)
        v.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("关键词："))
        self.a_kw = QLineEdit()
        self.a_kw.textChanged.connect(self._collect)
        r3.addWidget(self.a_kw)
        r3.addWidget(QLabel("添加："))
        self.a_kw_text = QLineEdit()
        self.a_kw_text.textChanged.connect(self._collect)
        r3.addWidget(self.a_kw_text)
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
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("按类型删："))
        self.d_types = {}
        for key, label in [("space", "空格"), ("digit", "数字"), ("lower", "小写"), ("upper", "大写"),
                           ("chinese", "中文"), ("special", "特殊字符")]:
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._collect)
            self.d_types[key] = cb
            r1.addWidget(cb)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("按位置删：位置"))
        self.d_pos = QLineEdit("0")
        self.d_pos.setFixedWidth(40)
        self.d_pos.textChanged.connect(self._collect)
        r2.addWidget(self.d_pos)
        self.d_right = QCheckBox("从右往左")
        self.d_right.stateChanged.connect(self._collect)
        r2.addWidget(self.d_right)
        r2.addWidget(QLabel("删除"))
        self.d_cnt = QLineEdit("0")
        self.d_cnt.setFixedWidth(40)
        self.d_cnt.textChanged.connect(self._collect)
        r2.addWidget(self.d_cnt)
        r2.addWidget(QLabel("个字符"))
        v.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("按关键词删："))
        self.d_kw = QLineEdit()
        self.d_kw.textChanged.connect(self._collect)
        r3.addWidget(self.d_kw)
        r3.addWidget(QLabel("往"))
        self.d_dir = QComboBox()
        self.d_dir.addItems(["右", "左"])
        self.d_dir.currentIndexChanged.connect(lambda i: self._collect())
        r3.addWidget(self.d_dir)
        r3.addWidget(QLabel("删除"))
        self.d_kw_cnt = QLineEdit("0")
        self.d_kw_cnt.setFixedWidth(40)
        self.d_kw_cnt.textChanged.connect(self._collect)
        r3.addWidget(self.d_kw_cnt)
        r3.addWidget(QLabel("个字符"))
        self.d_incl = QCheckBox("包括关键词本身")
        self.d_incl.stateChanged.connect(self._collect)
        r3.addWidget(self.d_incl)
        v.addLayout(r3)
        v.addStretch(1)
        return w

    def _tab_num(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        self.n_on = QCheckBox("启用序号")
        self.n_on.stateChanged.connect(self._collect)
        r1.addWidget(self.n_on)
        self.n_mode = QComboBox()
        self.n_mode.addItems(["后缀加序号", "前缀加序号"])
        self.n_mode.currentIndexChanged.connect(lambda i: self._collect())
        r1.addWidget(self.n_mode)
        r1.addWidget(QLabel("起始值："))
        self.n_start = QLineEdit("1")
        self.n_start.setFixedWidth(50)
        self.n_start.textChanged.connect(self._collect)
        r1.addWidget(self.n_start)
        r1.addWidget(QLabel("增量："))
        self.n_step = QLineEdit("1")
        self.n_step.setFixedWidth(50)
        self.n_step.textChanged.connect(self._collect)
        r1.addWidget(self.n_step)
        r1.addWidget(QLabel("位数："))
        self.n_width = QLineEdit("2")
        self.n_width.setFixedWidth(50)
        self.n_width.textChanged.connect(self._collect)
        r1.addWidget(self.n_width)
        r1.addStretch(1)
        v.addLayout(r1)
        v.addStretch(1)
        return w

    def _tab_template(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("模板："))
        self.t_entry = QLineEdit()
        self.t_entry.setPlaceholderText("如 {序号}_{名} 或 封茗囧菌_{日期}_{名}，留空=不启用")
        self.t_entry.textChanged.connect(self._collect)
        r1.addWidget(self.t_entry, 1)
        v.addLayout(r1)
        hint = QLabel("可用变量：{名}/{name} 原文件名 · {扩展}/{ext} · {序号}/{num} 三位序号 · "
                      "{日期} 2026-08-21 · {时间} · {年}{月}{日}")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _tab_textmode(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        hint = QLabel("直接编辑新文件名（每行一个，与文件顺序对应；空行=保持预览结果）。"
                      "从其它标签切到这里会自动带入当前预览。")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.tm_edit = QTextEdit()
        self.tm_edit.textChanged.connect(self._textmode_apply)
        v.addWidget(self.tm_edit, 1)
        return w

    def _tab_options(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        r1 = QHBoxLayout()
        self.o_ignore_ext = QCheckBox("忽略扩展名（规则只作用于主文件名）")
        self.o_ignore_ext.setChecked(True)
        self.o_ignore_ext.stateChanged.connect(self._collect)
        r1.addWidget(self.o_ignore_ext)
        self.o_sub = QCheckBox("包含子文件夹")
        r1.addWidget(self.o_sub)
        self.o_dir_rename = QCheckBox("文件夹改名")
        r1.addWidget(self.o_dir_rename)
        self.o_conflict = QCheckBox("解决同名冲突（自动加序号）")
        self.o_conflict.setChecked(True)
        self.o_conflict.stateChanged.connect(self._collect)
        r1.addWidget(self.o_conflict)
        v.addLayout(r1)
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
        for i, item in enumerate(items[:2000]):
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
            QMessageBox.information(self, "提示", "没有需要改名的文件（先调整规则再预览）")
            return
        sel_rows = set(idx.row() for idx in self.table.selectionModel().selectedRows()) if \
            self.table.selectionModel().selectedRows() else None
        batch = pending if sel_rows is None else [x for i, x in enumerate(items) if i in sel_rows and x in pending]

        def job():
            ok, fail = 0, []
            self.history.clear()
            for item in batch:
                folder = os.path.dirname(item["path"])
                dst = os.path.join(folder, item["new"])
                try:
                    if os.path.exists(dst):
                        fail.append((item, "目标已存在"))
                        item["status"] = "✗ 冲突"
                        continue
                    os.rename(item["path"], dst)
                    self.history.append((dst, item["path"]))   # 撤销用
                    item["path"] = dst
                    item["status"] = "✔"
                    ok += 1
                except OSError as e:
                    fail.append((item, str(e)))
                    item["status"] = "✗"
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
            QMessageBox.information(self, "提示", "没有可撤销的改名记录")
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
