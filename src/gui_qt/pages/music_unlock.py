"""音乐解密页（PySide6 版）：解密各平台加密音乐（Unlock Music 全格式算法移植）。

对应原版 gui/music_unlock_page.py：
- 选择文件 / 清空 / 开始解密
- 文件列表展示
- 回车快捷键触发解密
- 后台线程解密（不阻塞 UI），完成后清空列表
"""

import os

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QTextEdit,
    QVBoxLayout,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage


class MusicUnlockPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.files = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🔓 音乐解密")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 音乐解密 · 功能介绍", (
            "<p><b>一键批量解密各平台加密音乐文件，输出为通用格式。</b></p>"
            "<p>· <b>支持格式</b>：ncm（网易云）/ qmc·mflac·mgg（QQ）/ kgm（酷狗）/"
            " vpr / kwm（酷我）/ xm（咪咕）/ tm / x2m 等加密音乐</p>"
            "<p>· <b>操作</b>：📁 选择文件（可多选）→ 🔓 开始解密；"
            "解密后的文件输出到全局设置的统一导出路径</p>"
            "<p>· 全程本地解密，不上传、无需 Cookie</p>"
            "<p>· 解密结果与失败原因实时显示在下方日志区</p>"
            "<p>提示：解密后可到「音乐标签」页补全标签，或用「重命名」工具整理文件名</p>")))
        layout.addLayout(title_row)

        bar = QHBoxLayout()
        select_btn = QPushButton("📁 选择文件")
        select_btn.clicked.connect(self._select_files)
        bar.addWidget(select_btn)
        clear_btn = QPushButton("🗑 清空")
        clear_btn.clicked.connect(self._clear_files)
        bar.addWidget(clear_btn)
        bar.addStretch(1)
        self.decrypt_btn = QPushButton("🔓 开始解密")
        self.decrypt_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.decrypt_btn.clicked.connect(self._decrypt)
        bar.addWidget(self.decrypt_btn)
        layout.addLayout(bar)

        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(200)
        layout.addWidget(self.file_list)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

        self._render_files()

    def on_return_press(self):
        """主窗口回车统一入口：触发解密。"""
        self._decrypt()

    def _select_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择加密音乐文件", "",
            "加密音乐 (*.ncm *.qmc0 *.qmc2 *.qmc3 *.qmc4 *.qmc6 *.qmc8 *.qmcflac *.qmcogg "
            "*.mflac *.mflac0 *.mgg *.mgg0 *.mgg1 *.kgm *.kgma *.vpr *.kwm *.xm "
            "*.tm0 *.tm2 *.tm3 *.tm6 *.x2m *.x3m);;所有文件 (*.*)")
        if not paths:
            return
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self._render_files()

    def _clear_files(self):
        self.files.clear()
        self._render_files()

    def _render_files(self):
        self.file_list.clear()
        if not self.files:
            self.file_list.addItem("（未选择文件）")
            return
        for i, p in enumerate(self.files, 1):
            self.file_list.addItem(f"[{i}] {p}")

    def _decrypt(self):
        if not self.files:
            self.app.toast("请先选择要解密的文件", "warning")
            return
        files = list(self.files)
        self.decrypt_btn.setEnabled(False)
        self.log_text.clear()
        self.log_text.append(f"⏳ 开始解密 {len(files)} 个文件...\n")

        from toolbox.config_manager import get_music_download_dir
        out_dir = str(get_music_download_dir())

        def work():
            from music_unlock import decrypt_to
            results = []
            for p in files:
                try:
                    out = decrypt_to(p, out_dir)
                    results.append((os.path.basename(p), "✅", out))
                except Exception as e:
                    results.append((os.path.basename(p), "❌", str(e)))
            return results

        def done(results, err):
            self.decrypt_btn.setEnabled(True)
            if err:
                self.log_text.append(f"❌ {err}")
                return
            for name, tag, msg in results:
                self.log_text.append(f"{tag} {name}")
                self.log_text.append(f"   {msg}")
            self.log_text.append(f"\n完成，输出目录: {out_dir}")
            self.files.clear()
            self._render_files()

        run_bg(work, done)
