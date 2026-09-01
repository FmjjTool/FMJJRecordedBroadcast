"""全局设置页（PySide6 版，对应原版 gui/global_settings.py）。

- 默认工作目录（set_work_dir + 全局广播 on_dir_changed）
- 统一导出目录（set_export_dir，目录不存在自动创建）
- 配置与缓存（项目根/配置目录/工作目录/FFmpeg 检测/旧缓存迁移/清理 __pycache__ 和 .pyc）
- 关于（制作歆慕 / B站主页 / 官方网站）
"""

import os

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTimeEdit, QVBoxLayout, QWidget, QFrame,
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

        # ---- 配置与缓存 ----
        v.addWidget(self._card_title("📦 配置与依赖检查"))
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #888; font-size: 11px;")
        self.info_label.setWordWrap(True)
        v.addWidget(self.info_label)
        info_row = QHBoxLayout()
        info_row.addWidget(self._btn("🔄 刷新信息", self._refresh_info))
        info_row.addWidget(self._btn("📦 迁移旧缓存到 config/", self._migrate))
        info_row.addStretch(1)
        v.addLayout(info_row)
        # 清理 Python 缓存按钮已下线：与依赖检查功能合并到下方的「检测依赖」区
        self.clean_result_label = QLabel("")
        self.clean_result_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.clean_result_label)

        # 检测依赖与其他强依赖：缺失项标红 + 快捷下载按钮
        self.dependency_frame = QFrame()
        self.dependency_frame.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d8dde6; border-radius: 6px; }")
        dep_v = QVBoxLayout(self.dependency_frame)
        dep_v.setContentsMargins(10, 8, 10, 8)
        dep_v.setSpacing(4)
        dep_head = QHBoxLayout()
        dep_title = QLabel("🔍 检测依赖与其他强依赖")
        dep_title.setStyleSheet("font-weight: bold; font-size: 13px; background: transparent; border: none;")
        dep_head.addWidget(dep_title)
        dep_head.addStretch(1)
        recheck_btn = QPushButton("🔄 重新检测")
        recheck_btn.setCursor(Qt.PointingHandCursor)
        recheck_btn.clicked.connect(self._refresh_dependencies)
        dep_head.addWidget(recheck_btn)
        dep_v.addLayout(dep_head)
        # 占位：_refresh_dependencies 重建 self.dependency_layout
        self.dependency_layout = QVBoxLayout()
        self.dependency_layout.setContentsMargins(0, 4, 0, 0)
        self.dependency_layout.setSpacing(3)
        dep_v.addLayout(self.dependency_layout)
        v.addWidget(self.dependency_frame)
        # 后台填充检测结果（避免构造时阻塞）
        QTimer.singleShot(0, self._refresh_dependencies)

        v.addSpacing(12)

        # ---- 关于 ----
        v.addWidget(self._card_title("🛠 关于"))
        maker_row = QHBoxLayout()
        maker_lbl = QLabel("🛠 制作：歆慕")
        maker_lbl.setStyleSheet("color: #1976d2; font-weight: bold;")
        maker_row.addWidget(maker_lbl)
        maker_row.addStretch(1)
        maker_row.addWidget(self._btn(
            "🔗 B站主页",
            lambda: self._open_site("B站主页", "https://space.bilibili.com/622821754")))
        maker_row.addWidget(self._btn(
            "🎵 抖音主页",
            lambda: self._open_site(
                "抖音主页",
                "https://www.douyin.com/user/MS4wLjABAAAANr2f2DlSduMtGo3LtUm2MoxXu9cblLhTs2pJRDDZQHY")))
        maker_row.addWidget(self._btn(
            "🎤 微博主页",
            lambda: self._open_site("微博主页", "https://weibo.com/u/7262942477")))
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
            site_row.addWidget(self._btn(
                "🔗 打开", lambda n=name, u=url: self._open_site(n, u)))
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
        # 信息
        self._refresh_info()
        # 依赖检测（每次回到本页都重检一次，不依赖外部信号）
        self._refresh_dependencies()

    def sync_theme_schedule_from_config(self):
        """主窗口手动切换主题时调用：把当前主题定时面板同步到最新配置状态。

        处理流程：
        - 重新载入 cfg.theme_schedule_enabled 写入 UI
        - 调用 _update_sched_status 重画提示文字
        该方法对用户透明（不要弹 toast —— 主窗口已提示过手动切换结果）。
        """
        try:
            self._load_theme_schedule()
        except Exception:
            pass

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

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择工作目录", self.workdir_entry.text().strip() or os.getcwd())
        if d:
            self.workdir_entry.setText(d)
            self.app.toast(f"已选择工作目录: {d}", "info")

    def _browse_export(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择导出目录", self.exportdir_entry.text().strip() or os.getcwd())
        if d:
            self.exportdir_entry.setText(d)
            self.app.toast(f"已选择导出目录: {d}", "info")

    def _open_site(self, label, url):
        """统一站点打开：所有「🔗 打开官方链接」按钮走这里，加 toast 提示。"""
        try:
            os.startfile(url)
            self.app.toast(f"已打开「{label}」", "info")
        except Exception as e:
            self.app.toast(f"打开失败: {e}", "error")

    def _save(self):
        path = self.workdir_entry.text().strip()
        if not path:
            self.app.toast("请输入工作目录", "warning")
            return
        set_work_dir(path)
        self.app.toast(f"工作目录已设为: {path}", "success")
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
        # 按用户要求：每个按钮点击后在右下角 toast 提示
        self.app.toast("已刷新配置信息", "info")

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
                self.app.toast(f"迁移失败: {str(err)[:80]}", "error")
                return
            self.app.toast(f"迁移完成: {result}", "success")
            self._refresh_info()

        run_bg(work, done)

    def _clean_pycache_removed(self):
        """（已下线）原清理 Python 缓存按钮已合并到「检测依赖」中——按用户要求删除。"""
        pass

    # ----- 检测依赖 -----

    # 依赖规格表：(键, 显示名, 检测函数, 用途说明, 下载 URL)
    # 检测函数返回 (ok: bool, version: str)
    _DEPENDENCIES = [
        ("ffmpeg", "FFmpeg", "_check_ffmpeg_detail",
         "视频处理、转码、合并、截图等核心功能", "https://www.gyan.dev/ffmpeg/builds/"),
        ("ffprobe", "FFprobe", "_check_ffprobe_detail",
         "ffmpeg 同包，探测视频元数据（分辨率/时长/编码）", "https://www.gyan.dev/ffmpeg/builds/"),
        ("PySide6", "PySide6", "_check_pyside6_detail",
         "桌面 GUI 框架（Qt for Python）", "pip install PySide6"),
        ("requests", "requests", "_check_requests_detail",
         "HTTP 请求库（各平台 API 调用）", "pip install requests"),
        ("PIL", "Pillow", "_check_pillow_detail",
         "图片处理（封面 / 截图）", "pip install Pillow"),
        ("bs4", "BeautifulSoup4", "_check_bs4_detail",
         "HTML 解析（封面 / 简介提取）", "pip install beautifulsoup4"),
        ("lxml", "lxml", "_check_lxml_detail",
         "更快的 HTML/XML 解析依赖", "pip install lxml"),
    ]

    def _refresh_dependencies(self):
        """重建依赖检测清单，每项一行 + 一键下载/安装按钮（缺失项标红）。"""
        # 清空旧条目
        while self.dependency_layout.count():
            item = self.dependency_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        missing = 0
        for key, name, fn_name, usage, url in self._DEPENDENCIES:
            fn = getattr(self, fn_name)
            try:
                ok, version = fn()
            except Exception:
                ok, version = False, "异常"
            row = QFrame()
            row.setObjectName("depRow")
            row.setStyleSheet(
                "QFrame#depRow { background: %s; border-radius: 4px; }" % (
                    "#ffeaea" if not ok else "#f4faf6"))
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            # 状态指示
            mark = QLabel("❌ 缺失" if not ok else "✅ 已安装")
            mark.setStyleSheet(
                "color: %s; font-weight: bold; font-size: 12px;"
                " background: transparent; border: none;" % (
                    "#d32f2f" if not ok else "#2e7d32"))
            mark.setFixedWidth(70)
            h.addWidget(mark)
            name_lbl = QLabel(f"{name}")
            name_lbl.setStyleSheet(
                "color: %s; font-weight: bold; font-size: 12px;"
                " background: transparent; border: none;" % (
                    "#d32f2f" if not ok else "#2f3440"))
            name_lbl.setFixedWidth(110)
            h.addWidget(name_lbl)
            ver_lbl = QLabel(version or "—")
            ver_lbl.setStyleSheet(
                "color: #888; font-size: 11px; background: transparent; border: none;")
            ver_lbl.setFixedWidth(180)
            h.addWidget(ver_lbl)
            usage_lbl = QLabel(usage)
            usage_lbl.setStyleSheet(
                "color: #666; font-size: 11px; background: transparent; border: none;")
            usage_lbl.setWordWrap(True)
            h.addWidget(usage_lbl, 1)
            # 缺失项提供快捷下载/安装按钮
            if not ok:
                install_btn = QPushButton("⬇ 下载" if key in ("ffmpeg", "ffprobe") else "⬇ 安装")
                install_btn.setCursor(Qt.PointingHandCursor)
                install_btn.setStyleSheet(
                    "QPushButton { background: #d32f2f; color: white;"
                    " border-radius: 3px; padding: 3px 10px; font-size: 11px; }"
                    "QPushButton:hover { background: #b71c1c; }")
                # Python 包走 pip 静默安装（标准做法）；二进制下载走浏览器打开官方页
                if url.startswith("pip install"):
                    install_btn.clicked.connect(
                        lambda _c=False, k=key, n=name, u=url:
                        self._pip_install(k, n, u))
                else:
                    install_btn.clicked.connect(
                        lambda _c=False, n=name, u=url:
                        self._open_browser_for_dep(n, u))
                h.addWidget(install_btn)
                missing += 1
            self.dependency_layout.addWidget(row)
        # 总览标签 —— 通过顶部右侧的依赖卡片对外展示
        if missing:
            summary = QLabel(
                f"⚠️ 检测完成：{missing} 项缺失（已标红 · 点击「⬇  安装」自动修复或跳转到下载页）")
            summary.setStyleSheet(
                "color: #d32f2f; font-size: 11px; padding: 2px;")
        else:
            summary = QLabel("✅ 所有依赖已就绪")
            summary.setStyleSheet(
                "color: #2e7d32; font-size: 11px; padding: 2px;")
        self.dependency_layout.addWidget(summary)

    # ----- 依赖检测实现：返回 (ok, version_str) -----

    def _check_ffmpeg_detail(self):
        try:
            import subprocess
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3, text=True)
            if r.returncode != 0:
                return False, ""
            # 首行例：ffmpeg version 6.1.1 ...
            head = (r.stdout or "").splitlines()[0] if r.stdout else ""
            return True, head[:60]
        except Exception:
            return False, ""

    def _check_ffprobe_detail(self):
        try:
            import subprocess
            r = subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=3, text=True)
            if r.returncode != 0:
                return False, ""
            head = (r.stdout or "").splitlines()[0] if r.stdout else ""
            return True, head[:60]
        except Exception:
            return False, ""

    def _check_pyside6_detail(self):
        try:
            import PySide6
            ver = getattr(PySide6, "__version__", "?")
            return True, f"PySide6 {ver}"
        except Exception:
            return False, ""

    def _check_requests_detail(self):
        try:
            import requests
            return True, f"requests {requests.__version__}"
        except Exception:
            return False, ""

    def _check_pillow_detail(self):
        try:
            import PIL
            return True, f"Pillow {PIL.__version__}"
        except Exception:
            return False, ""

    def _check_bs4_detail(self):
        try:
            import bs4
            from importlib.metadata import version as pkg_version
            try:
                ver = pkg_version("beautifulsoup4")
            except Exception:
                ver = "?"
            return True, f"bs4 {ver}"
        except Exception:
            return False, ""

    def _check_lxml_detail(self):
        try:
            import lxml
            from importlib.metadata import version as pkg_version
            try:
                ver = pkg_version("lxml")
            except Exception:
                ver = "?"
            return True, f"lxml {ver}"
        except Exception:
            return False, ""

    # ----- 缺失项快捷安装 -----

    def _pip_install(self, key, name, cmd):
        """pip install 走 subprocess，后台线程不阻塞 UI；结果 toast 提示。"""
        self.app.toast(f"⏳ 开始安装 {name} ...", "info")

        def work():
            import subprocess
            try:
                p = subprocess.run(
                    cmd.split() + ["--disable-pip-version-check", "-q"],
                    capture_output=True, timeout=300, text=True)
                if p.returncode == 0:
                    return True, ""
                return False, (p.stderr or p.stdout or "").strip()[:200]
            except Exception as e:
                return False, str(e)[:200]

        def done(result, err):
            ok, msg = result
            if err or not ok:
                self.app.toast(f"{name} 安装失败: {msg or err}", "error")
            else:
                self.app.toast(f"✅ {name} 安装完成", "success")
                self._refresh_dependencies()

        run_bg(work, done)

    def _open_browser_for_dep(self, name, url):
        """二进制类依赖（ffmpeg）：调浏览器打开官方下载页。"""
        try:
            import webbrowser
            webbrowser.open(url)
            self.app.toast(f"已打开 {name} 官方下载页", "info")
        except Exception as e:
            self.app.toast(f"无法打开浏览器: {e}", "error")
