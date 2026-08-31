"""字幕格式转换页（PySide6 版）：批量导入 + 勾选转换。"""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidgetItem,
    QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.subtitle_engine import SubtitleConverter
from toolbox.rename_engine import scan_date_folders
from toolbox.utils import format_size
from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList


class SubtitlePage(BasePage):
    EXTS = {'.bcc', '.json', '.ass', '.srt', '.txt'}
    FORMATS = [("纯文字TXT", "text"), ("带时间轴TXT", "timed_text"),
               ("LRC", "lrc"), ("ASS", "ass"), ("SRT", "srt")]

    def __init__(self, app):
        super().__init__(app)
        self.converter = SubtitleConverter()
        self.input_files = []
        self._enter_stage = 0  # 回车快捷：0=待扫描，1=待转换

        # 持久化的格式选择 / 日期文件夹开关
        fmt_settings = load_convert_config().get("subtitle_formats", ["text", "timed_text", "lrc"])
        use_date_folder = load_convert_config().get("subtitle_use_date_folder", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("字幕格式转换")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("支持 BCC/JSON/ASS/SRT/TXT → 纯文字/带时间轴TXT/LRC/ASS/SRT")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        # 文件按钮
        frow = QHBoxLayout()
        frow.addWidget(self._btn("📄 选择文件", self._select_files))
        frow.addWidget(self._btn("📁 选择文件夹", self._select_folder))
        frow.addWidget(self._btn("📅 扫描日期文件夹", self._scan_date_folders))
        self.file_count_label = QLabel("未选择文件")
        self.file_count_label.setStyleSheet("color: #888;")
        frow.addWidget(self.file_count_label)
        frow.addStretch(1)
        layout.addLayout(frow)

        # 输出格式（持久化）
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("输出格式:"))
        self.fmt_checks = {}
        for label, key in self.FORMATS:
            cb = QCheckBox(label)
            cb.setChecked(key in fmt_settings)
            cb.toggled.connect(lambda _c: self._save_fmt_settings())
            self.fmt_checks[key] = cb
            fmt_row.addWidget(cb)
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        # 文件列表（勾选）
        bar = QHBoxLayout()
        bar.addWidget(QLabel("已识别字幕文件:"))
        bar.addWidget(self._btn("☑ 全选", lambda: self._sel("all"), small=True))
        bar.addWidget(self._btn("🔄 反选", lambda: self._sel("invert"), small=True))
        bar.addWidget(self._btn("□ 清除", lambda: self._sel("none"), small=True))
        bar.addWidget(self._btn("🗑 删除选中", self._delete_selected, small=True, danger=True))
        bar.addStretch(1)
        layout.addLayout(bar)

        self.file_list = RowCheckList()
        self.file_list.setMaximumHeight(150)
        layout.addWidget(self.file_list)

        # 选项 + 转换
        opt_row = QHBoxLayout()
        self.same_dir = QCheckBox("📁 转换后放到原地址")
        self.same_dir.setChecked(True)
        opt_row.addWidget(self.same_dir)
        self.date_folder = QCheckBox("📁 创建日期文件夹")
        self.date_folder.setChecked(use_date_folder)
        self.date_folder.toggled.connect(lambda _c: self._save_sub_setting())
        opt_row.addWidget(self.date_folder)
        opt_row.addStretch(1)
        self.convert_btn = QPushButton("🔄 开始转换")
        self.convert_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.convert_btn.clicked.connect(self._convert)
        opt_row.addWidget(self.convert_btn)
        self.result_label = QLabel("")
        opt_row.addWidget(self.result_label)
        layout.addLayout(opt_row)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def _btn(self, text, slot, small=False, danger=False):
        b = QPushButton(text)
        b.clicked.connect(slot)
        if small:
            b.setStyleSheet("padding: 2px 8px;")
        if danger:
            b.setStyleSheet("background: #c62828; color: white; padding: 2px 8px; border-radius: 3px;")
        return b

    # ---- 回车快捷 / 设置持久化 ----
    def on_return_press(self):
        """第一次回车=扫描日期文件夹，第二次回车=按当前配置转换。"""
        if self._enter_stage == 0:
            self._enter_stage = 1
            self._scan_date_folders()
            self._log("⏎ 再次按回车将按当前格式配置转换（或点「开始转换」）")
        else:
            self._enter_stage = 0
            self._convert()

    def _save_fmt_settings(self):
        """持久化字幕输出格式选择"""
        fmts = [key for key, cb in self.fmt_checks.items() if cb.isChecked()]
        s = load_convert_config()
        s["subtitle_formats"] = fmts
        save_convert_config(s)

    def _save_sub_setting(self):
        """保存字幕日期文件夹开关"""
        s = load_convert_config()
        s["subtitle_use_date_folder"] = self.date_folder.isChecked()
        save_convert_config(s)

    # ---- 拖放 ----
    def add_dropped_files(self, files):
        """拖放文件处理：按扩展名过滤后追加（去重）"""
        added_names = []
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in self.EXTS and f not in self.input_files:
                self.input_files.append(f)
                added_names.append(os.path.basename(f))
        added = len(added_names)
        self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件（+{added}）")
        if added:
            self._log(f"📥 拖放添加 {added} 个文件:")
            for name in added_names:
                self._log(f"  + {name}")
        else:
            self._log("📥 拖放（无新文件）")
        self._refresh_list()

    def add_dropped_folder(self, folder):
        """拖放文件夹处理"""
        self._add_folder_files(folder)
        self._log(f"📥 拖放文件夹: {os.path.basename(folder)}")
        self._log_scanned()

    # ---- 文件选择 ----
    def _select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择字幕文件", "", "字幕文件 (*.bcc *.json *.ass *.srt *.txt);;所有文件 (*.*)")
        if files:
            self.input_files = list(files)
            self._log_scanned()

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self._add_folder_files(folder)
            self._log_scanned()

    def _add_folder_files(self, folder):
        self.input_files = []
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in self.EXTS:
                    self.input_files.append(os.path.join(root, f))
        self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件")

    def _scan_date_folders(self):
        self.input_files = []
        folders = scan_date_folders(self.app.work_dir)
        if not folders:
            self.log_text.setPlainText(f"⚠️ 工作目录下没有日期文件夹\n   当前目录: {self.app.work_dir}")
            self.file_count_label.setText("未找到日期文件夹")
            self._refresh_list()
            return
        for folder_name, _ in folders:
            folder_path = os.path.join(self.app.work_dir, folder_name)
            if not os.path.isdir(folder_path):
                continue
            for f in os.listdir(folder_path):
                fp = os.path.join(folder_path, f)
                if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in self.EXTS:
                    self.input_files.append(fp)
        self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件")
        self._log_scanned()
        if not self.input_files:
            self._log(f"⚠️ 日期文件夹中未找到字幕文件\n   支持: {', '.join(sorted(self.EXTS))}")

    def _log_scanned(self):
        self.log_text.clear()
        self._refresh_list()
        if not self.input_files:
            return
        self._log(f"📂 已选择 {len(self.input_files)} 个字幕文件:\n")
        for i, fp in enumerate(self.input_files, 1):
            self._log(f"  [{i}] {os.path.basename(fp)}  ({format_size(os.path.getsize(fp))})")
        self._log("\n💡 勾选要转换的文件，选择输出格式后按「开始转换」\n")

    # ---- 列表 ----
    def _refresh_list(self):
        self.file_list.clear()
        for fp in self.input_files:
            item = QListWidgetItem(os.path.basename(fp))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, fp)
            self.file_list.addItem(item)

    def _sel(self, mode):
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if mode == "all":
                item.setCheckState(Qt.Checked)
            elif mode == "none":
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _checked_paths(self):
        paths = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.checkState() == Qt.Checked:
                paths.append(item.data(Qt.UserRole))
        return paths

    def _delete_selected(self):
        selected = self._checked_paths()
        if not selected:
            self._log("⚠️ 请先勾选要删除的字幕文件")
            return
        from toolbox.audio_engine import delete_files_safe
        ok, failed = delete_files_safe(selected)
        self._log(f"🗑 已删除 {ok} 个字幕文件（移入回收站）")
        for f in failed[:10]:
            self._log(f"  ❌ {f}")
        deleted = set(selected)
        self.input_files = [f for f in self.input_files if f not in deleted]
        self.file_count_label.setText(f"已选择 {len(self.input_files)} 个文件")
        self._refresh_list()

    # ---- 转换 ----
    def _convert(self):
        if not self.input_files:
            return
        to_convert = self._checked_paths()
        if not to_convert:
            self.app.toast("请先在列表中勾选要转换的字幕文件", "warning")
            return
        output_formats = [key for key, cb in self.fmt_checks.items() if cb.isChecked()]
        if not output_formats:
            self.app.toast("请至少选择一种输出格式", "warning")
            return

        self.log_text.clear()
        self._log(f"🔄 开始转换 {len(to_convert)} 个勾选文件...\n")

        use_same_dir = self.same_dir.isChecked()
        output_dir = None
        if not use_same_dir:
            from toolbox.config_manager import get_export_subdir
            output_dir = get_export_subdir("subtitle")
            if self.date_folder.isChecked():
                from datetime import datetime
                output_dir = output_dir / datetime.now().strftime("%Y%m%d")
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._log("📁 输出到原文件地址\n")

        success_count = 0
        for filepath in to_convert:
            filename = os.path.basename(filepath)
            self._log(f"📄 处理: {filename}")
            try:
                with open(filepath, 'rb') as f:
                    content = f.read()
                result = self.converter.convert_file(filename, content, output_formats)
                if result['success']:
                    for out_file in result['output_files']:
                        out_path = (Path(filepath).parent if use_same_dir else output_dir) / out_file['filename']
                        if isinstance(out_file['content'], str):
                            with open(out_path, 'w', encoding='utf-8') as f:
                                f.write(out_file['content'])
                        else:
                            with open(out_path, 'wb') as f:
                                f.write(out_file['content'])
                        self._log(f"  ✅ {out_file['filename']}")
                    success_count += 1
                else:
                    self._log(f"  ❌ {result['message']}")
            except Exception as e:
                self._log(f"  ❌ 异常: {e}")

        self._log(f"\n✅ 转换完成: {success_count}/{len(to_convert)} 成功（每种格式单独输出）")
        self.result_label.setText(f"完成 {success_count}/{len(to_convert)}")

    def _log(self, msg):
        self.log_text.append(msg)
