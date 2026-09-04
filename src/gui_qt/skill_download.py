"""设置页「下载 Skill」二级对话框：选择内置 AI Skill → 导出到本地目录。

数据层见 toolbox/skills.py（定位 / 枚举 / 复制导出）。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from toolbox.config_manager import get_export_dir
from toolbox.skills import export_skill, list_skills


class SkillDownloadDialog(QDialog):
    """内置 Skill 下载对话框：左侧列表选 skill，底部导出到指定目录。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._last_dest = ""
        self._exported_path = ""
        self.setWindowTitle("⬇ 下载 Skill")
        self.resize(640, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        head = QLabel(
            "选择要下载的 Skill，导出后可将 Skill 文件夹放入 AI 助手的 Skill 目录使用。\n"
            "（Skill 来自「封茗囧菌数据汇总库」维护工具箱内置，随版本更新）")
        head.setStyleSheet("color: #666; font-size: 12px;")
        head.setWordWrap(True)
        v.addWidget(head)

        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_select)
        v.addWidget(self.list_widget, 1)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.RichText)
        self.detail_label.setStyleSheet(
            "color: #555; font-size: 12px; background: #f7f9fc;"
            " border: 1px solid #dde3ec; border-radius: 6px; padding: 8px;")
        v.addWidget(self.detail_label)

        btn_row = QHBoxLayout()
        self.open_dir_btn = QPushButton("📂 打开导出目录")
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_exported)
        btn_row.addWidget(self.open_dir_btn)
        btn_row.addStretch(1)
        self.download_btn = QPushButton("⬇ 下载到本地…")
        self.download_btn.setStyleSheet(
            "background: #1976d2; color: white; padding: 6px 18px;")
        self.download_btn.clicked.connect(self._download)
        btn_row.addWidget(self.download_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        self.status_label.setWordWrap(True)
        v.addWidget(self.status_label)

        self._skills = []
        self._reload()

    # ---- 数据 ----

    def _reload(self):
        self.list_widget.clear()
        self._skills = list_skills()
        for meta in self._skills:
            desc = meta.get("description", "")
            if len(desc) > 60:
                desc = desc[:60] + "…"
            item = QListWidgetItem(f"🎁 {meta['display_name']}\n{desc}")
            item.setData(Qt.UserRole, meta)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self.detail_label.setText("未找到内置 Skill")
            self.download_btn.setEnabled(False)

    def _current(self):
        for item in self.list_widget.selectedItems():
            return item.data(Qt.UserRole)
        return None

    # ---- 交互 ----

    def _on_select(self):
        meta = self._current()
        if not meta:
            return
        size_kb = meta.get("size_bytes", 0) / 1024
        self.detail_label.setText(
            f"<b>{meta['display_name']}</b>（{meta['id']}）<br>"
            f"文件数: {meta['file_count']} · 大小: {size_kb:.1f} KB<br>"
            f"{meta.get('description', '')}")

    def _download(self):
        meta = self._current()
        if not meta:
            return
        start = self._last_dest or str(get_export_dir())
        dest = QFileDialog.getExistingDirectory(
            self, "选择 Skill 保存位置", start)
        if not dest:
            return
        try:
            target = export_skill(meta["id"], dest)
        except Exception as e:
            self.status_label.setText(f"❌ 下载失败: {e}")
            self.status_label.setStyleSheet("color: #ff5252; font-size: 11px;")
            self.app.toast(f"Skill 下载失败: {e}", "error")
            return
        self._last_dest = dest
        self._exported_path = str(target)
        self.open_dir_btn.setEnabled(True)
        self.status_label.setText(f"✅ 已下载到: {target}")
        self.status_label.setStyleSheet("color: #4caf50; font-size: 11px;")
        self.app.toast(f"✅ Skill「{meta['display_name']}」已下载到 {target}", "success")

    def _open_exported(self):
        if not self._exported_path:
            return
        try:
            import os
            os.startfile(self._exported_path)
        except Exception as e:
            self.app.toast(f"打开文件夹失败: {e}", "error")
