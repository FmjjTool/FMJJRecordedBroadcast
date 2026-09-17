"""歌词工具页（PySide6 版）：转换 + KRCD 工具 两个 tab。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from toolbox.lyrics_engine import LyricConverter

from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList


class LyricsToolPage(BasePage):
    SUPPORTED_EXTS = {'.lrc', '.krc', '.ksc', '.qrc', '.trc', '.elrc'}
    OUTPUT_FORMATS = [("纯文字 TXT", "txt"), ("LRC", "lrc"), ("SRT", "srt"), ("ASS", "ass"), ("KSC 逐字", "ksc")]

    def __init__(self, app):
        super().__init__(app)
        self.converter = LyricConverter()
        self.input_files = []
        self.krcd_files = []
        self.work_dir = ""       # 用户指定的歌词工作目录
        self._enter_stage = 0    # 回车快捷：0=待扫描，1=待转换

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        head = QHBoxLayout()
        head.addWidget(QLabel("🎤 歌词转换", styleSheet="font-size: 16px; font-weight: bold;"))
        head.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        head.addWidget(attach_help_overlay(self, "❓ 歌词转换 · 功能介绍", (
            "<p><b>歌词格式转换 + 酷狗 KRC 加解密，两个工具。</b></p>"
            "<p>· <b>🔄 转换</b>：LRC / KRC(酷狗) / KSC(酷我) / QRC(QQ) / TRC·ELRC → "
            "纯文字 TXT / LRC / SRT / ASS / KSC 逐字</p>"
            "<p>· <b>输入方式</b>：选择歌词文件 / 选择文件夹（批量）/ ✍️ 直接粘贴歌词文本"
            "（按 LRC 处理）；可指定工作目录，输出统一存到工作目录</p>"
            "<p>· 支持同目录/日期文件夹等输出选项（按 config 持久化）</p>"
            "<p>· <b>🔑 KRCD工具</b>：KRC（酷狗加密）↔ KRCD（明文）双向转换——"
            "解密为可读明文，或把明文加密回 KRC 放回酷狗</p>"
            "<p>· 回车快捷：第一次回车=扫描/选择，第二次回车=开始转换</p>")))
        layout.addLayout(head)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_convert_tab(), "🔄 转换")
        self.tabs.addTab(self._build_krcd_tab(), "🔑 KRCD工具")

    def _build_convert_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("歌词格式转换", styleSheet="font-size: 16px; font-weight: bold;"))

        frow = QHBoxLayout()
        frow.addWidget(QPushButton("📄 选择歌词文件", clicked=self._select_files))
        frow.addWidget(QPushButton("📁 选择文件夹", clicked=self._select_folder))
        frow.addWidget(QPushButton("📂 指定工作目录", clicked=self._set_work_dir))
        self.input_toggle = QPushButton("✍️ 输入歌词")
        self.input_toggle.setCheckable(True)
        self.input_toggle.toggled.connect(lambda c: self.input_area.setVisible(c))
        frow.addWidget(self.input_toggle)
        self.work_dir_label = QLabel("（未指定）")
        self.work_dir_label.setStyleSheet("color: #888; font-size: 10px;")
        frow.addWidget(self.work_dir_label)
        frow.addStretch(1)
        v.addLayout(frow)

        # 歌词文字输入区（默认折叠）：直接粘贴 LRC 等文本，走正常转换流程
        self.input_area = QWidget()
        iv = QVBoxLayout(self.input_area)
        iv.setContentsMargins(0, 4, 0, 4)
        iv.setSpacing(4)
        self.input_text = QPlainTextEdit()
        self.input_text.setPlaceholderText(
            "在此粘贴歌词文本（LRC / QRC / TRC / KSC 等文本格式），然后点「转换输入内容」")
        self.input_text.setMaximumHeight(120)
        iv.addWidget(self.input_text)
        irow = QHBoxLayout()
        input_btn = QPushButton("🔄 转换输入内容")
        input_btn.setStyleSheet("background: #2e7d32; color: white; padding: 4px 12px; border-radius: 4px;")
        input_btn.clicked.connect(self._convert_input)
        irow.addWidget(input_btn)
        clr_btn = QPushButton("🗑 清空输入")
        clr_btn.clicked.connect(self.input_text.clear)
        irow.addWidget(clr_btn)
        ihint = QLabel("（粘贴内容按 LRC 处理，输出到工作目录）")
        ihint.setStyleSheet("color: #888; font-size: 10px;")
        irow.addWidget(ihint)
        irow.addStretch(1)
        iv.addLayout(irow)
        self.input_area.setVisible(False)
        v.addWidget(self.input_area)
        self.file_count_label = QLabel("未选择文件")
        self.file_count_label.setStyleSheet("color: #888;")
        v.addWidget(self.file_count_label)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("输出格式:"))
        self.fmt_var = "txt"
        for label, val in self.OUTPUT_FORMATS:
            rb = QRadioButton(label)
            if val == "txt":
                rb.setChecked(True)
            rb.toggled.connect(lambda checked, v=val: self._set_fmt(v) if checked else None)
            fmt_row.addWidget(rb)
        fmt_row.addStretch(1)
        v.addLayout(fmt_row)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("已识别歌词文件:"))
        bar.addWidget(QPushButton("☑ 全选", clicked=lambda: self._sel("all")))
        bar.addWidget(QPushButton("🔄 反选", clicked=lambda: self._sel("invert")))
        bar.addWidget(QPushButton("□ 清除", clicked=lambda: self._sel("none")))
        bar.addStretch(1)
        v.addLayout(bar)
        self.lyr_list = RowCheckList()
        self.lyr_list.setMaximumHeight(140)
        v.addWidget(self.lyr_list)

        conv_row = QHBoxLayout()
        conv_btn = QPushButton("🔄 开始转换")
        conv_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        conv_btn.clicked.connect(self._convert)
        conv_row.addWidget(conv_btn)
        self.result_label = QLabel("")
        conv_row.addWidget(self.result_label)
        conv_row.addStretch(1)
        v.addLayout(conv_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        v.addWidget(self.log_text, 1)
        return w

    def _build_krcd_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("KRCD ↔ KRC 工具", styleSheet="font-size: 16px; font-weight: bold;"))

        frow = QHBoxLayout()
        frow.addWidget(QPushButton("📄 选择文件", clicked=self._krcd_select_files))
        frow.addWidget(QPushButton("📁 选择文件夹", clicked=self._krcd_select_folder))
        frow.addStretch(1)
        v.addLayout(frow)
        self.krcd_count = QLabel("未选择文件")
        self.krcd_count.setStyleSheet("color: #888;")
        v.addWidget(self.krcd_count)

        op = QHBoxLayout()
        op.addWidget(QLabel("操作:"))
        self.krcd_mode = "decrypt"
        rb1 = QRadioButton("KRC → KRCD（解密）")
        rb1.setChecked(True)
        rb1.toggled.connect(lambda c: self._set_krcd_mode("decrypt") if c else None)
        op.addWidget(rb1)
        rb2 = QRadioButton("KRCD → KRC（加密）")
        rb2.toggled.connect(lambda c: self._set_krcd_mode("encrypt") if c else None)
        op.addWidget(rb2)
        op.addStretch(1)
        v.addLayout(op)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("文件列表:"))
        bar.addWidget(QPushButton("☑ 全选", clicked=lambda: self._krcd_sel("all")))
        bar.addWidget(QPushButton("🔄 反选", clicked=lambda: self._krcd_sel("invert")))
        bar.addWidget(QPushButton("□ 清除", clicked=lambda: self._krcd_sel("none")))
        bar.addStretch(1)
        v.addLayout(bar)
        self.krcd_list = RowCheckList()
        self.krcd_list.setMaximumHeight(140)
        v.addWidget(self.krcd_list)

        run_row = QHBoxLayout()
        run = QPushButton("▶️ 执行")
        run.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        run.clicked.connect(self._krcd_run)
        run_row.addWidget(run)
        self.krcd_result_label = QLabel("")
        run_row.addWidget(self.krcd_result_label)
        run_row.addStretch(1)
        v.addLayout(run_row)
        self.krcd_log = QTextEdit()
        self.krcd_log.setReadOnly(True)
        v.addWidget(self.krcd_log, 1)
        return w

    # ---- 转换 tab ----
    def on_return_press(self):
        """按当前 tab 分发回车：KRCD工具 → 执行；转换 → 扫描/转换两段。"""
        if self.tabs.currentIndex() == 1:
            self._krcd_run()
            return
        if self._enter_stage == 0:
            self._enter_stage = 1
            target = self.work_dir or self.app.work_dir
            if target and os.path.isdir(target):
                self._scan_dir(target)
            self._log("⏎ 再次按回车将按当前格式转换（或点「开始转换」）")
        else:
            self._enter_stage = 0
            self._convert()

    def _set_fmt(self, v):
        self.fmt_var = v

    def _log(self, msg):
        self.log_text.append(msg)

    def _select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择歌词文件", "", "歌词文件 (*.lrc *.krc *.ksc *.qrc *.trc *.elrc);;所有文件 (*.*)")
        if files:
            self.input_files = list(files)
            self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件")
            self._log_scanned()

    def _select_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self._add_folder_files(d)
            self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件")
            self._log_scanned()

    def _set_work_dir(self):
        d = QFileDialog.getExistingDirectory(self, "指定歌词工作目录")
        if not d:
            return
        self.work_dir = d
        self.work_dir_label.setText(f"📂 {d}")
        self.work_dir_label.setStyleSheet("color: #1976d2; font-size: 10px;")
        self._scan_dir(d)

    def _add_folder_files(self, folder):
        self.input_files = []
        for root, _d, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in self.SUPPORTED_EXTS:
                    self.input_files.append(os.path.join(root, f))

    def _scan_dir(self, d):
        if not os.path.isdir(d):
            self._log("⚠️ 目录不存在")
            return
        self.input_files = []
        for root, _d, files in os.walk(d):
            for f in files:
                if os.path.splitext(f)[1].lower() in self.SUPPORTED_EXTS:
                    self.input_files.append(os.path.join(root, f))
        self.file_count_label.setText(f"已识别 {len(self.input_files)} 个文件")
        self._log_scanned()
        if not self.input_files:
            self._log(f"⚠️ 该目录下未找到歌词文件\n   支持: {', '.join(sorted(self.SUPPORTED_EXTS))}")

    # ---- 拖放 ----
    def add_dropped_files(self, files):
        added = []
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in self.SUPPORTED_EXTS and f not in self.input_files:
                self.input_files.append(f)
                added.append(os.path.basename(f))
        if added:
            self.file_count_label.setText(f"已识别 {len(self.input_files)} 个文件（+{len(added)}）")
            self._log(f"📥 拖放添加 {len(added)} 个歌词文件:")
            for n in added:
                self._log(f"  + {n}")
        else:
            self._log("📥 拖放（无新增歌词文件）")
        self._refresh_list()

    def add_dropped_folder(self, folder):
        added = 0
        for root, _d, files in os.walk(folder):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.splitext(f)[1].lower() in self.SUPPORTED_EXTS and fp not in self.input_files:
                    self.input_files.append(fp)
                    added += 1
        self.work_dir = folder
        self.work_dir_label.setText(f"📂 {folder}")
        self.work_dir_label.setStyleSheet("color: #1976d2; font-size: 10px;")
        self.file_count_label.setText(f"已识别 {len(self.input_files)} 个文件（+{added}）")
        self._log(f"📥 拖放文件夹: {folder}\n   新增 {added} 个歌词文件")
        self._refresh_list()

    def _log_scanned(self):
        self.log_text.clear()
        self._refresh_list()
        if not self.input_files:
            return
        self._log(f"📂 已选择 {len(self.input_files)} 个歌词文件:")
        for i, fp in enumerate(self.input_files, 1):
            self._log(f"  [{i}] {os.path.basename(fp)}")
        self._log("💡 勾选要转换的文件，选择输出格式后按「开始转换」")

    def _refresh_list(self):
        self.lyr_list.clear()
        for fp in self.input_files:
            item = QListWidgetItem(os.path.basename(fp))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, fp)
            self.lyr_list.addItem(item)

    def _sel(self, mode):
        for i in range(self.lyr_list.count()):
            it = self.lyr_list.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "none":
                it.setCheckState(Qt.Unchecked)
            else:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _checked(self):
        return [self.lyr_list.item(i).data(Qt.UserRole) for i in range(self.lyr_list.count())
                if self.lyr_list.item(i).checkState() == Qt.Checked]

    def _convert_one(self, fp, out_fmt):
        """转换单个文件并写回同目录，返回是否成功。"""
        name = os.path.basename(fp)
        try:
            with open(fp, "rb") as f:
                content = f.read()
            r = self.converter.convert(name, content, out_fmt)
            if r["success"]:
                for of in r["output_files"]:
                    out_path = os.path.join(os.path.dirname(fp), of["filename"])
                    data = of["content"]
                    mode = "w" if isinstance(data, str) else "wb"
                    with open(out_path, mode, encoding="utf-8" if mode == "w" else None) as f:
                        f.write(data)
                    self._log(f"  ✅ {name} → {of['filename']}")
                return True
            self._log(f"  ❌ {name}: {r['message']}")
        except Exception as e:
            self._log(f"  ❌ {name}: {e}")
        return False

    def _convert_input(self):
        """输入框粘贴的歌词文本 → 保存为 LRC → 走正常转换流程。"""
        text = self.input_text.toPlainText().strip()
        if not text:
            self.app.toast("请先在输入框粘贴歌词文本", "warning")
            return
        out_dir = self.work_dir or self.app.work_dir or os.getcwd()
        base = "输入歌词.lrc"
        n = 1
        while os.path.exists(os.path.join(out_dir, base)):
            n += 1
            base = f"输入歌词({n}).lrc"
        src = os.path.join(out_dir, base)
        with open(src, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        self.log_text.clear()
        self._log(f"✍️ 已保存输入内容: {src}")
        self._log(f"🔄 转换输入内容 → {self.fmt_var.upper()} ...")
        ok = self._convert_one(src, self.fmt_var)
        if ok:
            self._log("\n✅ 输入内容转换完成")
        else:
            self._log("\n❌ 输入内容转换失败")
        self.result_label.setText("输入转换 " + ("成功" if ok else "失败"))

    def _convert(self):
        to_convert = self._checked()
        if not to_convert:
            self.app.toast("请先勾选要转换的歌词文件", "warning")
            return
        out_fmt = self.fmt_var
        self.log_text.clear()
        self._log(f"🔄 开始转换 {len(to_convert)} 个文件 → {out_fmt.upper()} ...\n")
        success = 0
        for fp in to_convert:
            if self._convert_one(fp, out_fmt):
                success += 1
        self._log(f"\n✅ 转换完成: {success}/{len(to_convert)} 成功")
        self.result_label.setText(f"完成 {success}/{len(to_convert)}")

    # ---- KRCD tab ----
    def _set_krcd_mode(self, m):
        self.krcd_mode = m

    def _krcd_log(self, msg):
        self.krcd_log.append(msg)

    def _krcd_select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", "歌词文件 (*.krc *.krcd);;所有文件 (*.*)")
        if files:
            self.krcd_files = list(files)
            self._krcd_refresh()

    def _krcd_select_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not d:
            return
        self.krcd_files = []
        for root, _d, files in os.walk(d):
            for f in files:
                if os.path.splitext(f)[1].lower() in ('.krc', '.krcd'):
                    self.krcd_files.append(os.path.join(root, f))
        self._krcd_refresh()

    def _krcd_refresh(self):
        self.krcd_count.setText(f"已选择 {len(getattr(self, 'krcd_files', []))} 个文件")
        self.krcd_list.clear()
        for fp in getattr(self, "krcd_files", []):
            item = QListWidgetItem(os.path.basename(fp))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, fp)
            self.krcd_list.addItem(item)

    def _krcd_sel(self, mode):
        for i in range(self.krcd_list.count()):
            it = self.krcd_list.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "none":
                it.setCheckState(Qt.Unchecked)
            else:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _krcd_run(self):
        to_run = [self.krcd_list.item(i).data(Qt.UserRole) for i in range(self.krcd_list.count())
                  if self.krcd_list.item(i).checkState() == Qt.Checked]
        if not to_run:
            self.app.toast("请先在列表中勾选要处理的文件", "warning")
            return
        mode = self.krcd_mode
        self.krcd_log.clear()
        self._krcd_log(f"🔄 开始{'解密 KRC→KRCD' if mode == 'decrypt' else '加密 KRCD→KRC'}，共 {len(to_run)} 个文件")
        success = 0
        for fp in to_run:
            name = os.path.basename(fp)
            ext = os.path.splitext(fp)[1].lower()
            try:
                if mode == "decrypt":
                    if ext == ".krcd":
                        self._krcd_log(f"  ⏭ {name}: 已是 KRCD 明文，跳过")
                        continue
                    with open(fp, "rb") as f:
                        plain = self.converter.krc_to_krcd(f.read())
                    with open(fp + ".krcd", "w", encoding="utf-8") as f:
                        f.write(plain)
                    self._krcd_log(f"  ✅ {name} → {name}.krcd")
                else:
                    if ext == ".krc":
                        self._krcd_log(f"  ⏭ {name}: 已是 KRC，跳过")
                        continue
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        enc = self.converter.krcd_to_krc(f.read())
                    out = fp[:-len(ext)] if fp.endswith(ext) else fp
                    out = out + ".krc"
                    with open(out, "wb") as f:
                        f.write(enc)
                    self._krcd_log(f"  ✅ {name} → {os.path.basename(out)}")
                success += 1
            except Exception as e:
                self._krcd_log(f"  ❌ {name}: {e}")
        self._krcd_log(f"\n✅ 处理完成: {success}/{len(to_run)} 成功")
        self.krcd_result_label.setText(f"完成 {success}/{len(to_run)}")
