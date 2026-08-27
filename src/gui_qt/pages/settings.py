"""全局设置页（PySide6 版，对应原版 gui/global_settings.py）。

- 默认工作目录（set_work_dir + 全局广播 on_dir_changed）
- 统一导出目录（set_export_dir，目录不存在自动创建）
- 配置与缓存（项目根/配置目录/工作目录/FFmpeg 检测/旧缓存迁移/清理 __pycache__ 和 .pyc）
- 关于（制作歆慕 / B站主页 / 官方网站）
"""

import os

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
)

from toolbox.config_manager import (
    get_app_root, get_config_dir, get_export_dir, load_convert_config,
    migrate_old_cache, has_old_cache_files, has_date_subfolders,
    save_convert_config, set_export_dir, set_work_dir,
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

        # ---- 主题定时切换 ----
        v.addWidget(self._card_title("🌗 主题定时切换（到点自动黑白）"))
        ts_row = QHBoxLayout()
        self.sched_enable = QCheckBox("启用定时切换")
        self.sched_enable.setToolTip(
            "到达设定时间自动切换主题，改动即时生效；\n"
            "启用期间手动切换会在数秒内被拉回目标主题，关闭后手动切换不受影响")
        ts_row.addWidget(self.sched_enable)
        ts_row.addStretch(1)
        ts_row.addWidget(QLabel("☀ 浅色时间:"))
        self.sched_light = QTimeEdit()
        self.sched_light.setDisplayFormat("HH:mm")
        self.sched_light.setFixedWidth(84)
        ts_row.addWidget(self.sched_light)
        ts_row.addWidget(QLabel("🌙 深色时间:"))
        self.sched_dark = QTimeEdit()
        self.sched_dark.setDisplayFormat("HH:mm")
        self.sched_dark.setFixedWidth(84)
        ts_row.addWidget(self.sched_dark)
        ts_row.addWidget(self._btn("💾 保存", self._save_theme_schedule_clicked))
        v.addLayout(ts_row)
        self.sched_status = QLabel("")
        self.sched_status.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.sched_status)
        # 改动即时生效：开关/时间一变就写入配置并立刻对齐主题（无需点保存）
        self.sched_enable.toggled.connect(self._on_schedule_changed)
        self.sched_light.timeChanged.connect(self._on_schedule_changed)
        self.sched_dark.timeChanged.connect(self._on_schedule_changed)

        v.addSpacing(12)

        # ---- 备注信息（弹幕姬主播信息卡） ----
        v.addWidget(self._card_title("📝 备注信息（显示在弹幕姬主播信息卡 UTC+8 时间下方）"))
        self.remark_edit = QTextEdit()
        self.remark_edit.setPlaceholderText("每行一条备注，回车换行；保存后立即同步到弹幕姬页")
        self.remark_edit.setFixedHeight(96)
        self.remark_edit.setAcceptRichText(False)
        v.addWidget(self.remark_edit)
        remark_row = QHBoxLayout()
        remark_row.addWidget(self._btn("💾 保存", self._save_remark))
        remark_row.addWidget(self._btn("↩ 恢复默认", self._reset_remark))
        remark_row.addStretch(1)
        self.remark_status = QLabel("")
        self.remark_status.setStyleSheet("color: #888; font-size: 11px;")
        remark_row.addWidget(self.remark_status)
        v.addLayout(remark_row)

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
        maker_row.addWidget(self._btn(
            "🔗 B站主页", lambda: os.startfile("https://space.bilibili.com/622821754")))
        maker_row.addWidget(self._btn(
            "🎵 抖音主页",
            lambda: os.startfile(
                "https://www.douyin.com/user/MS4wLjABAAAANr2f2DlSduMtGo3LtUm2MoxXu9cblLhTs2pJRDDZQHY")))
        maker_row.addWidget(self._btn(
            "🎤 微博主页", lambda: os.startfile("https://weibo.com/u/7262942477")))
        v.addLayout(maker_row)

        for name, url in [
            ("封茗囧菌数据汇总库", "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"),
            ("无料周边汇总", "https://docs.qq.com/smartsheet/DRkZIbkZtQ0taTHd4"),
            ("用户协议与编辑须知",
             "https://docs.qq.com/aio/p/scisutrcci3apy1?p=w5TSrLIyhtOn7j9KjoGXP3"),
        ]:
            site_row = QHBoxLayout()
            site_lbl = QLabel(f"📋 官方网站：{name}")
            site_lbl.setStyleSheet("color: #1976d2; font-weight: bold;")
            site_row.addWidget(site_lbl)
            site_row.addStretch(1)
            site_row.addWidget(self._btn("🔗 打开", lambda u=url: os.startfile(u)))
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
        # 主题定时切换
        self._load_theme_schedule()
        # 备注信息
        self.remark_edit.setPlainText(
            str(load_convert_config().get("remark_info") or ""))
        # 信息
        self._refresh_info()

    # ---- 主题定时切换 ----
    def _load_theme_schedule(self):
        cfg = load_convert_config()
        # 载入时屏蔽信号，避免触发「改动即保存」
        for w in (self.sched_enable, self.sched_light, self.sched_dark):
            w.blockSignals(True)
        self.sched_enable.setChecked(bool(cfg.get("theme_schedule_enabled")))
        light = QTime.fromString(str(cfg.get("theme_light_time", "07:00")), "HH:mm")
        dark = QTime.fromString(str(cfg.get("theme_dark_time", "19:00")), "HH:mm")
        self.sched_light.setTime(light if light.isValid() else QTime(7, 0))
        self.sched_dark.setTime(dark if dark.isValid() else QTime(19, 0))
        for w in (self.sched_enable, self.sched_light, self.sched_dark):
            w.blockSignals(False)
        self._update_sched_status()

    def _update_sched_status(self):
        if self.sched_enable.isChecked():
            from gui_qt import theme
            want = theme.scheduled_theme()
            if want:
                self.sched_status.setText(
                    f"已启用：当前为{'☀ 浅色' if want == 'light' else '🌙 深色'}时段 · "
                    f"{self.sched_light.time().toString('HH:mm')} 自动切浅色 / "
                    f"{self.sched_dark.time().toString('HH:mm')} 自动切深色（每 10 秒自动检测，到点即变）")
                self.sched_status.setStyleSheet("color: #4caf50; font-size: 11px;")
            else:
                self.sched_status.setText("两个时间点相同，定时无效，请修改")
                self.sched_status.setStyleSheet("color: #e53935; font-size: 11px;")
        else:
            self.sched_status.setText("未启用：手动点击侧栏「深色/浅色主题」按钮切换")
            self.sched_status.setStyleSheet("color: #888; font-size: 11px;")

    def _on_schedule_changed(self):
        """开关/时间一变即保存并立即对齐主题（无需点保存）。"""
        self._save_theme_schedule(silent=True)

    def _save_theme_schedule_clicked(self):
        self._save_theme_schedule(silent=False)

    def _save_theme_schedule(self, silent: bool):
        cfg = load_convert_config()
        cfg["theme_schedule_enabled"] = self.sched_enable.isChecked()
        cfg["theme_light_time"] = self.sched_light.time().toString("HH:mm")
        cfg["theme_dark_time"] = self.sched_dark.time().toString("HH:mm")
        save_convert_config(cfg)
        # 立即生效：让主窗口立刻对齐（启用/修改时间后无需等下一个检查点）
        if hasattr(self.app, "_check_theme_schedule"):
            from gui_qt.common import post_to_main
            post_to_main(self.app._check_theme_schedule)
        if not silent:
            self.app.toast("✅ 主题定时切换设置已保存", "success")
        self._update_sched_status()

    # ---- 备注信息 ----
    def _save_remark(self):
        text = self.remark_edit.toPlainText().strip()
        if not text:
            self.app.toast("备注信息不能为空（可点「恢复默认」）", "warning")
            return
        cfg = load_convert_config()
        cfg["remark_info"] = text
        save_convert_config(cfg)
        # 弹幕姬页已创建 → 立即刷新显示；未创建（未访问过该页）→ 切页时 on_show 会载入
        page = getattr(self.app, "pages", {}).get("test_page")
        if page and hasattr(page, "refresh_remark_info"):
            page.refresh_remark_info()
        self.remark_status.setText("✅ 已保存并同步到弹幕姬页")
        self.remark_status.setStyleSheet("color: #4caf50; font-size: 11px;")
        self.app.toast("✅ 备注信息已保存", "success")

    def _reset_remark(self):
        from toolbox.config_manager import DEFAULT_CONVERT_CONFIG_DICT
        default = str(DEFAULT_CONVERT_CONFIG_DICT.get("remark_info", ""))
        self.remark_edit.setPlainText(default)
        self._save_remark()

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
        """清理程序根目录与工作目录下所有 __pycache__ 和 .pyc（后台执行，结果写入标签）。

        两处都清：work_dir 常被设到纯数据目录（无代码），只清它等于没清。
        """
        import shutil
        wd = getattr(self.app, "work_dir", None)
        wd = str(wd) if wd else ""
        if not os.path.isdir(wd):
            wd = ""
        targets = [get_app_root()]
        if wd and os.path.normpath(get_app_root()) != wd:
            targets.append(wd)

        def work():
            deleted_dirs = deleted_files = 0
            for target in targets:
                for root, dirs, files in os.walk(target, topdown=False):
                    if os.path.basename(root) == "__pycache__":
                        continue  # 已在删除目录内，跳过
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
            return deleted_dirs, deleted_files

        def done(result, err):
            if err:
                self.clean_result_label.setText(f"❌ 清理失败: {str(err)[:50]}")
                self.clean_result_label.setStyleSheet("color: #ff5252; font-size: 11px;")
                return
            d_n, f_n = result or (0, 0)
            scope = " + 工作目录" if len(targets) > 1 else ""
            self.clean_result_label.setText(
                f"✅ 清理完成: {d_n} 个 __pycache__ 目录, {f_n} 个 .pyc"
                f"（程序目录{scope}；运行中新生导入会重建，属正常）")
            self.clean_result_label.setStyleSheet("color: #4caf50; font-size: 11px;")

        self.clean_result_label.setText("🧹 清理中...")
        self.clean_result_label.setStyleSheet("color: #888; font-size: 11px;")
        run_bg(work, done)
