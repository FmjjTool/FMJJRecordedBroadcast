"""全局设置页（PySide6 版，复用 toolbox.config_manager 引擎）。"""

import os

from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from toolbox.config_manager import (
    get_app_root, get_config_dir, load_convert_config, save_convert_config,
    migrate_old_cache, has_old_cache_files, has_date_subfolders,
)

from gui_qt.pages.base import BasePage


class GlobalSettingsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        title = QLabel("⚙️ 全局设置")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        outer.addWidget(title)

        # 可滚动内容
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 8, 8, 8)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # ---- 默认工作目录 ----
        v.addWidget(self._card_title("📁 默认工作文件夹（用于录播工具箱）"))
        wd_row = QHBoxLayout()
        self.workdir_entry = QLineEdit()
        self.workdir_entry.setPlaceholderText("工作目录路径")
        wd_row.addWidget(self.workdir_entry, 1)
        wd_row.addWidget(self._btn("📂 浏览...", self._browse))
        wd_row.addWidget(self._btn("💾 保存", self._save_workdir))
        v.addLayout(wd_row)
        self.workdir_status = QLabel("")
        self.workdir_status.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.workdir_status)

        v.addSpacing(12)

        # ---- 统一导出目录 ----
        v.addWidget(self._card_title("📤 统一导出目录（音乐/已获取/账号获取数据导出）"))
        ex_row = QHBoxLayout()
        self.exportdir_entry = QLineEdit()
        self.exportdir_entry.setPlaceholderText("导出目录路径（默认 download/）")
        ex_row.addWidget(self.exportdir_entry, 1)
        ex_row.addWidget(self._btn("📂 浏览...", self._browse_export))
        ex_row.addWidget(self._btn("💾 保存", self._save_export))
        v.addLayout(ex_row)
        self.exportdir_status = QLabel("")
        self.exportdir_status.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.exportdir_status)

        v.addSpacing(12)

        # ---- 配置与缓存 ----
        v.addWidget(self._card_title("📦 配置与缓存"))
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #888; font-size: 11px;")
        self.info_label.setWordWrap(True)
        v.addWidget(self.info_label)
        v.addWidget(self._btn("🔄 刷新信息", self._refresh_info))
        v.addWidget(self._btn("📦 迁移旧缓存到 config/", self._migrate))
        v.addWidget(self._btn("🧹 清理 Python 缓存 (__pycache__)", self._clean_pycache))
        self.clean_result_label = QLabel("")
        self.clean_result_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.clean_result_label)

        v.addSpacing(12)

        # ---- 关于 ----
        v.addWidget(self._card_title("🛠 关于"))
        about = QLabel(
            "🛠 制作：歆慕\n"
            "📋 官方网站：封茗囧菌数据汇总库\n"
            "封茗囧菌数据汇总库 · 统一维护工具箱 · PySide6 版\n"
            "依赖：ffmpeg, ffprobe, PySide6"
        )
        about.setWordWrap(True)
        about.setStyleSheet("color: #888; font-size: 11px; line-height: 1.4;")
        v.addWidget(about)

        v.addStretch(1)

        self.on_show()

    # ---- 小工具 ----
    def _card_title(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _btn(self, text, slot, primary=False):
        b = QPushButton(text)
        b.clicked.connect(slot)
        if primary:
            b.setStyleSheet("background: #2e7d32; color: white; padding: 4px 10px; border-radius: 4px;")
        return b

    # ---- 逻辑 ----
    def on_show(self):
        cfg = load_convert_config()
        wd = cfg.get("work_dir", "")
        self.workdir_entry.setText(wd)
        if wd and os.path.isdir(wd):
            ok = has_date_subfolders(wd)
            self.workdir_status.setText(("✅ 已检测到日期子文件夹 · " if ok else "⚠️ 无日期子文件夹 · ") + wd)
            self.workdir_status.setStyleSheet(
                "color: #4caf50; font-size: 11px;" if ok else "color: #ff9800; font-size: 11px;")
        else:
            self.workdir_status.setText("⚠️ 未设置或目录不存在")
            self.workdir_status.setStyleSheet("color: #ff9800; font-size: 11px;")

        self.exportdir_entry.setText(cfg.get("export_dir", ""))
        self._refresh_info()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if d:
            self.workdir_entry.setText(d)

    def _save_workdir(self):
        d = self.workdir_entry.text().strip()
        if not d:
            QMessageBox.warning(self, "提示", "请输入工作目录")
            return
        cfg = load_convert_config()
        cfg["work_dir"] = d
        save_convert_config(cfg)
        self.app.work_dir = d
        self.workdir_status.setText("✅ 已保存 · " + d)
        self.workdir_status.setStyleSheet("color: #4caf50; font-size: 11px;")

    def _browse_export(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d:
            self.exportdir_entry.setText(d)

    def _save_export(self):
        d = self.exportdir_entry.text().strip()
        if not d:
            QMessageBox.warning(self, "提示", "请输入导出目录")
            return
        cfg = load_convert_config()
        cfg["export_dir"] = d
        save_convert_config(cfg)
        self.exportdir_status.setText("✅ 已保存 · " + d)
        self.exportdir_status.setStyleSheet("color: #4caf50; font-size: 11px;")

    def _refresh_info(self):
        self.info_label.setText(
            f"配置目录：{get_config_dir()}\n"
            f"应用根目录：{get_app_root()}\n"
            f"旧缓存文件：{'存在，可迁移' if has_old_cache_files() else '无'}"
        )

    def _migrate(self):
        n = migrate_old_cache()
        QMessageBox.information(self, "迁移完成", f"已迁移 {n} 个缓存文件到 config/")

    def _clean_pycache(self):
        import shutil
        root = get_app_root()
        count = 0
        for dirpath, dirnames, _ in os.walk(root):
            for dn in list(dirnames):
                if dn == "__pycache__":
                    try:
                        shutil.rmtree(os.path.join(dirpath, dn), ignore_errors=True)
                        count += 1
                    except Exception:
                        pass
        self.clean_result_label.setText(f"已清理 {count} 个 __pycache__ 目录")
