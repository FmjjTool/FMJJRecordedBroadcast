"""全局设置页（PySide6 版，对应原版 gui/global_settings.py）。

- 默认工作目录（set_work_dir + 全局广播 on_dir_changed）
- 统一导出目录（set_export_dir，目录不存在自动创建）
- 配置与缓存（项目根/配置目录/工作目录/FFmpeg 检测/旧缓存迁移/清理 __pycache__ 和 .pyc）
- 关于（制作歆慕 / B站主页 / 官方网站）
"""

import os

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from toolbox.config_manager import (
    get_app_root, get_config_dir, get_export_dir, load_convert_config,
    migrate_old_cache, has_old_cache_files, has_date_subfolders,
    set_export_dir, set_work_dir,
)

from gui_qt.common import run_bg
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
        wd_row.addWidget(self._btn("💾 保存", self._save))
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
        info_row = QHBoxLayout()
        info_row.addWidget(self._btn("🔄 刷新信息", self._refresh_info))
        info_row.addWidget(self._btn("📦 迁移旧缓存到 config/", self._migrate))
        info_row.addWidget(self._btn("🧹 清理 Python 缓存 (__pycache__)", self._clean_pycache))
        info_row.addStretch(1)
        v.addLayout(info_row)
        self.clean_result_label = QLabel("")
        self.clean_result_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.clean_result_label)

        v.addSpacing(12)

        # ---- 关于 ----
        v.addWidget(self._card_title("🛠 关于"))
        maker_row = QHBoxLayout()
        maker_lbl = QLabel("🛠 制作：歆慕")
        maker_lbl.setStyleSheet("color: #1976d2; font-weight: bold;")
        maker_row.addWidget(maker_lbl)
        maker_row.addStretch(1)
        maker_row.addWidget(self._btn("🔗 B站主页", lambda: os.startfile("https://space.bilibili.com/622821754")))
        v.addLayout(maker_row)

        site_row = QHBoxLayout()
        site_lbl = QLabel("📋 官方网站：封茗囧菌数据汇总库")
        site_lbl.setStyleSheet("color: #1976d2; font-weight: bold;")
        site_row.addWidget(site_lbl)
        site_row.addStretch(1)
        site_row.addWidget(self._btn("🔗 打开", lambda: os.startfile("https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr")))
        v.addLayout(site_row)

        about = QLabel(
            "封茗囧菌数据汇总库 · 统一维护工具箱 · 制作歆慕\n"
            "依赖: ffmpeg, ffprobe, PySide6"
        )
        about.setWordWrap(True)
        about.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(about)

        v.addStretch(1)

        self.on_show()

    # ---- 小工具 ----
    def _card_title(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        return b

    # ---- 逻辑 ----
    def on_show(self):
        # 工作目录
        wd = load_convert_config().get("work_dir", "")
        self.workdir_entry.setText(wd)
        if wd:
            if os.path.isdir(wd):
                ok = has_date_subfolders(wd)
                hint = "✅ 已检测到日期子文件夹" if ok else "⚠️ 无日期子文件夹"
                self.workdir_status.setText(f"{hint} · {wd}")
                self.workdir_status.setStyleSheet(
                    "color: #4caf50; font-size: 11px;" if ok else "color: #ff9800; font-size: 11px;")
            else:
                self.workdir_status.setText(f"⚠️ 目录不存在: {wd}")
                self.workdir_status.setStyleSheet("color: #ff5252; font-size: 11px;")
        # 导出目录
        exd = str(get_export_dir())
        self.exportdir_entry.setText(exd)
        self.exportdir_status.setText(f"当前: {exd}")
        self.exportdir_status.setStyleSheet("color: #888; font-size: 11px;")
        # 信息
        self._refresh_info()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择工作目录", self.workdir_entry.text().strip() or os.getcwd())
        if d:
            self.workdir_entry.setText(d)

    def _browse_export(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择导出目录", self.exportdir_entry.text().strip() or os.getcwd())
        if d:
            self.exportdir_entry.setText(d)

    def _save(self):
        path = self.workdir_entry.text().strip()
        if not path:
            QMessageBox.warning(self, "提示", "请输入工作目录")
            return
        set_work_dir(path)
        QMessageBox.information(self, "已保存", f"默认工作目录已设为:\n{path}")
        # 同步刷新全局工作目录并广播到所有页面
        if hasattr(self.app, "work_dir"):
            self.app.work_dir = path
        if hasattr(self.app, "_refresh_all"):
            self.app._refresh_all()
        self.on_show()

    def _save_export(self):
        path = self.exportdir_entry.text().strip()
        if not path:
            self.app.toast("请输入导出目录", "warning")
            return
        if not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                self.app.toast(f"目录创建失败: {e}", "error")
                return
        set_export_dir(path)
        self.exportdir_status.setText(f"当前: {path}")
        self.exportdir_status.setStyleSheet("color: #4caf50; font-size: 11px;")
        self.app.toast("导出目录已保存", "success")

    def _refresh_info(self):
        cfg = load_convert_config()
        wd = cfg.get("work_dir", "")
        self.info_label.setText(
            f"项目根: {get_app_root()}\n"
            f"配置目录: {get_config_dir()}\n"
            f"工作目录: {wd or '(未设置)'}\n"
            f"FFmpeg: {'✅' if self._check_ffmpeg() else '⚠️ 未检测到'}\n"
            f"旧缓存: {'⚠️ 有，可迁移' if has_old_cache_files() else '✅ 无'}"
        )

    def _check_ffmpeg(self):
        try:
            import subprocess
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3)
            return True
        except Exception:
            return False

    def _migrate(self):
        def work():
            return migrate_old_cache()

        def done(result, err):
            if err:
                QMessageBox.critical(self, "迁移失败", str(err))
                return
            QMessageBox.information(self, "完成", f"迁移完成：{result}")
            self._refresh_info()

        run_bg(work, done)

    def _clean_pycache(self):
        """清理工作目录下所有 __pycache__ 和 .pyc 文件（静默，结果写入标签）。"""
        import shutil
        target = self.app.work_dir if hasattr(self.app, "work_dir") and os.path.isdir(self.app.work_dir) else get_app_root()
        deleted_dirs = 0
        deleted_files = 0
        for root, dirs, files in os.walk(str(target), topdown=False):
            for d in dirs:
                if d == "__pycache__":
                    try:
                        shutil.rmtree(os.path.join(root, d))
                        deleted_dirs += 1
                    except Exception:
                        pass
            for f in files:
                if f.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(root, f))
                        deleted_files += 1
                    except Exception:
                        pass
        self.clean_result_label.setText(f"✅ 清理完成: {deleted_dirs} 个 __pycache__ 目录, {deleted_files} 个 .pyc")
        self.clean_result_label.setStyleSheet("color: #4caf50; font-size: 11px;")
