"""全局设置页（PySide6 版，对应原版 gui/global_settings.py）。

- 默认工作目录（set_work_dir + 全局广播 on_dir_changed）
- 统一导出目录（set_export_dir，目录不存在自动创建）
- 配置与依赖检查（项目根/配置目录/工作目录/FFmpeg 位置/清理 __pycache__ 和 .pyc/依赖检测）
- 关于（制作歆慕 / B站主页 / 官方网站）
"""

import os
import re

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTimeEdit, QVBoxLayout, QWidget, QFrame,
)

from toolbox.config_manager import (
    get_app_root, get_config_dir, get_export_dir, load_convert_config,
    has_date_subfolders, save_convert_config, set_export_dir, set_ffmpeg_path,
    set_work_dir,
)
from toolbox.skills import list_skills, skill_total_size_text
from toolbox.utils import find_ffmpeg_exe, find_ffprobe_exe, invalidate_ffmpeg_cache

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

        # ---- 主题自动切换（跟随系统 / 定时黑白） ----
        v.addWidget(self._card_title("🌗 主题自动切换（跟随系统 / 定时黑白）"))
        fs_row = QHBoxLayout()
        self.follow_sys_cb = QCheckBox("跟随系统深浅色")
        self.follow_sys_cb.setToolTip(
            "系统在 浅色/深色 模式之间切换时，应用主题自动跟随同步；\n"
            "与定时切换互斥（同时只能开一个）；手动点击侧栏主题方块会关闭本功能")
        fs_row.addWidget(self.follow_sys_cb)
        fs_row.addStretch(1)
        v.addLayout(fs_row)
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
        self.follow_sys_cb.toggled.connect(self._on_follow_system_changed)
        self.sched_enable.toggled.connect(self._on_schedule_changed)
        self.sched_light.timeChanged.connect(self._on_schedule_changed)
        self.sched_dark.timeChanged.connect(self._on_schedule_changed)

        v.addSpacing(12)

        # ---- 配置与依赖检查 ----
        v.addWidget(self._card_title("📦 配置与依赖检查"))
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #888; font-size: 11px;")
        self.info_label.setWordWrap(True)
        v.addWidget(self.info_label)
        info_row = QHBoxLayout()
        info_row.addWidget(self._btn("🔄 刷新信息", self._refresh_info))
        info_row.addWidget(self._btn("🧹 清理 Python 缓存 (__pycache__)", self._clean_pycache))
        info_row.addStretch(1)
        v.addLayout(info_row)
        self.clean_result_label = QLabel("")
        self.clean_result_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self.clean_result_label)

        # ---- FFmpeg 位置（可选；系统 PATH 优先，未找到时才用这里指定的位置）----
        ff_row = QHBoxLayout()
        self.ffmpeg_entry = QLineEdit()
        self.ffmpeg_entry.setPlaceholderText(
            "留空 = 自动使用系统 PATH；也可填 ffmpeg 所在目录或 ffmpeg.exe 完整路径")
        ff_row.addWidget(self.ffmpeg_entry, 1)
        ff_row.addWidget(self._btn("📂 浏览...", self._browse_ffmpeg))
        ff_row.addWidget(self._btn("💾 保存", self._save_ffmpeg))
        v.addLayout(ff_row)
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setStyleSheet("color: #888; font-size: 11px;")
        self.ffmpeg_status.setWordWrap(True)
        v.addWidget(self.ffmpeg_status)

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
        self.recheck_btn = QPushButton("🔄 重新检测")
        self.recheck_btn.setCursor(Qt.PointingHandCursor)
        self.recheck_btn.clicked.connect(self._refresh_dependencies)
        dep_head.addWidget(self.recheck_btn)
        dep_v.addLayout(dep_head)
        # 占位：_refresh_dependencies 重建 self.dependency_layout
        self.dependency_layout = QVBoxLayout()
        self.dependency_layout.setContentsMargins(0, 4, 0, 0)
        self.dependency_layout.setSpacing(3)
        dep_v.addLayout(self.dependency_layout)
        v.addWidget(self.dependency_frame)
        # 骨架屏加载：检测在后台线程跑，页面打开即刻渲染灰色占位条（__init__ 末尾 on_show 已触发）
        self._dep_cache = None      # [(key, name, ok, version)] 上次检测结果
        self._dep_loading = False
        self._skel_bars = []
        self._skel_phase = 0
        self._skel_timer = QTimer(self)
        self._skel_timer.setInterval(480)
        self._skel_timer.timeout.connect(self._pulse_dep_skeleton)
        self._ffmpeg_ok = None     # FFmpeg 可用性缓存（None = 未检测）

        v.addSpacing(12)

        # ---- AI Skill 下载（内置 Skill 导出到本地，供 AI 助手使用） ----
        v.addWidget(self._card_title("🤖 AI Skill 下载（导出内置 Skill 到本地）"))
        skill_row = QHBoxLayout()
        self._skill_list = list_skills()
        skill_count = len(self._skill_list)
        names = "、".join(m["display_name"] for m in self._skill_list) or "无"
        skill_info = QLabel(
            f"内置 {skill_count} 个 Skill（{skill_total_size_text()}）：{names}\n"
            "导出后可将 Skill 文件夹放入 AI 助手的 Skill 目录使用（如直播转录总结）")
        skill_info.setStyleSheet("color: #888; font-size: 11px;")
        skill_info.setWordWrap(True)
        skill_row.addWidget(skill_info, 1)
        skill_row.addWidget(self._btn("⬇ 下载 Skill…", self._open_skill_download))
        v.addLayout(skill_row)

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
        # FFmpeg 位置
        self.ffmpeg_entry.setText(str(load_convert_config().get("ffmpeg_path", "") or ""))
        self._update_ffmpeg_status()
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

    # ---- 主题自动切换（跟随系统 / 定时） ----
    def _load_theme_schedule(self):
        cfg = load_convert_config()
        # 载入时屏蔽信号，避免触发「改动即保存」
        for w in (self.follow_sys_cb, self.sched_enable, self.sched_light, self.sched_dark):
            w.blockSignals(True)
        self.follow_sys_cb.setChecked(bool(cfg.get("theme_follow_system")))
        self.sched_enable.setChecked(bool(cfg.get("theme_schedule_enabled")))
        light = QTime.fromString(str(cfg.get("theme_light_time", "07:00")), "HH:mm")
        dark = QTime.fromString(str(cfg.get("theme_dark_time", "19:00")), "HH:mm")
        self.sched_light.setTime(light if light.isValid() else QTime(7, 0))
        self.sched_dark.setTime(dark if dark.isValid() else QTime(19, 0))
        for w in (self.follow_sys_cb, self.sched_enable, self.sched_light, self.sched_dark):
            w.blockSignals(False)
        self._update_sched_status()

    def _update_sched_status(self):
        from gui_qt import theme
        if self.follow_sys_cb.isChecked():
            sysd = "🌙 深色" if theme.system_theme() == "dark" else "☀ 浅色"
            cur = "🌙 深色" if theme.is_dark() else "☀ 浅色"
            self.sched_status.setText(
                f"已启用跟随系统：系统当前为 {sysd} · 主题已同步 {cur}（每 10 秒检测，系统切换即变）")
            self.sched_status.setStyleSheet("color: #4caf50; font-size: 11px;")
        elif self.sched_enable.isChecked():
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
            self.sched_status.setText("未启用：手动点击侧栏主题方块（🌙/☀️）切换")
            self.sched_status.setStyleSheet("color: #888; font-size: 11px;")

    def _on_follow_system_changed(self):
        """跟随系统一变即保存并立即对齐；与定时切换互斥（程序改勾选需屏蔽信号）。"""
        if self.follow_sys_cb.isChecked():
            self.sched_enable.blockSignals(True)
            self.sched_enable.setChecked(False)
            self.sched_enable.blockSignals(False)
        self._save_theme_schedule(silent=True)

    def _on_schedule_changed(self):
        """开关/时间一变即保存并立即对齐主题（无需点保存）。"""
        if self.sched_enable.isChecked() and self.follow_sys_cb.isChecked():
            self.follow_sys_cb.blockSignals(True)
            self.follow_sys_cb.setChecked(False)
            self.follow_sys_cb.blockSignals(False)
        self._save_theme_schedule(silent=True)

    def _save_theme_schedule_clicked(self):
        self._save_theme_schedule(silent=False)

    def _save_theme_schedule(self, silent: bool):
        cfg = load_convert_config()
        cfg["theme_follow_system"] = self.follow_sys_cb.isChecked()
        cfg["theme_schedule_enabled"] = self.sched_enable.isChecked()
        cfg["theme_light_time"] = self.sched_light.time().toString("HH:mm")
        cfg["theme_dark_time"] = self.sched_dark.time().toString("HH:mm")
        save_convert_config(cfg)
        # 立即生效：让主窗口立刻对齐（启用/修改后无需等下一个检查点）
        if hasattr(self.app, "_check_theme_schedule"):
            from gui_qt.common import post_to_main
            post_to_main(self.app._check_theme_schedule)
        if not silent:
            self.app.toast("✅ 主题自动切换设置已保存", "success")
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

    def _open_skill_download(self):
        """打开「下载 Skill」二级对话框（选择内置 Skill 导出到本地目录）。"""
        from gui_qt.skill_download import SkillDownloadDialog
        dlg = SkillDownloadDialog(self.app, self.app)
        dlg.exec()

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

    def _info_text(self, ffmpeg_ok=None) -> str:
        cfg = load_convert_config()
        wd = cfg.get("work_dir", "")
        if ffmpeg_ok is None:
            ffmpeg_ok = self._ffmpeg_ok
        mark = "⏳ 检测中…" if ffmpeg_ok is None else (
            "✅" if ffmpeg_ok else "⚠️ 未检测到")
        return (
            f"项目根: {get_app_root()}\n"
            f"配置目录: {get_config_dir()}\n"
            f"工作目录: {wd or '(未设置)'}\n"
            f"FFmpeg: {mark}"
        )

    def _refresh_info(self, show_toast=True):
        """刷新配置信息：路径部分立即显示，FFmpeg 检测放后台（不阻塞页面）。"""
        self.info_label.setText(self._info_text())
        if show_toast:
            # 按用户要求：每个按钮点击后在右下角 toast 提示
            self.app.toast("已刷新配置信息", "info")

        def work():
            return self._check_ffmpeg()

        def done(result, err):
            if err is None and result is not None:
                self._ffmpeg_ok = bool(result)
                self.info_label.setText(self._info_text(self._ffmpeg_ok))

        run_bg(work, done)

    def _check_ffmpeg(self):
        try:
            import subprocess
            subprocess.run([find_ffmpeg_exe(), "-version"], capture_output=True, timeout=3)
            return True
        except Exception:
            return False

    # ---- FFmpeg 位置 ----

    def _update_ffmpeg_status(self):
        invalidate_ffmpeg_cache()
        exe = find_ffmpeg_exe()
        if exe == "ffmpeg":
            self.ffmpeg_status.setText(
                "⚠️ 未找到 ffmpeg：转换 / 音频提取 / 版权写入 / 直播录制将不可用，"
                "请安装后加入系统 PATH，或在上方指定位置")
            self.ffmpeg_status.setStyleSheet("color: #ff5252; font-size: 11px;")
            return
        import shutil
        src = "系统 PATH"
        which = shutil.which("ffmpeg")
        if not (which and os.path.normcase(os.path.abspath(exe))
                == os.path.normcase(os.path.abspath(which))):
            src = "自定义位置"
        self.ffmpeg_status.setText(f"当前使用（{src}）: {exe}")
        self.ffmpeg_status.setStyleSheet("color: #4caf50; font-size: 11px;")

    def _browse_ffmpeg(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择 FFmpeg 所在目录", self.ffmpeg_entry.text().strip() or os.getcwd())
        if d:
            self.ffmpeg_entry.setText(d)
            self.app.toast(f"已选择 FFmpeg 目录: {d}", "info")

    def _save_ffmpeg(self):
        path = self.ffmpeg_entry.text().strip()
        if path:
            is_dir = os.path.isdir(path)
            is_exe = os.path.isfile(path) and os.path.basename(path).lower().startswith("ffmpeg")
            if not (is_dir or is_exe):
                self.app.toast("路径无效：请填 ffmpeg 所在目录或 ffmpeg.exe 完整路径", "warning")
                return
        set_ffmpeg_path(path)
        invalidate_ffmpeg_cache()
        self._update_ffmpeg_status()
        self._refresh_info(show_toast=False)
        self._refresh_dependencies()
        self.app.toast("FFmpeg 位置已保存" if path else "已恢复默认：使用系统 PATH", "success")

    def _clean_pycache(self):
        """清理程序根目录与工作目录下所有 __pycache__ 和 .pyc（后台执行，结果写入标签）。

        两处都清：work_dir 常被设到纯数据目录（无代码），只清它等于没清。
        """
        import shutil
        wd = getattr(self.app, "work_dir", None)
        wd = str(wd) if wd else ""
        if not os.path.isdir(wd):
            wd = ""
        targets = [str(get_app_root())]
        if wd and os.path.normpath(wd) != os.path.normpath(str(get_app_root())):
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
        """依赖检测：骨架屏占位 + 后台线程执行（打开页面不卡顿）。

        首次检测先渲染灰色骨架条（脉冲动画），检测完成回填结果并缓存；
        再次进入页面先立即显示上次缓存结果，后台静默复核更新。
        """
        if self._dep_loading:
            return
        self._dep_loading = True
        self.recheck_btn.setEnabled(False)
        if self._dep_cache is None:
            self._show_dep_skeleton()
        else:
            self._fill_dep_rows(self._dep_cache)

        def work():
            results = []
            for key, name, fn_name, usage, url in self._DEPENDENCIES:
                fn = getattr(self, fn_name)
                try:
                    ok, version = fn()
                except Exception:
                    ok, version = False, "异常"
                results.append((key, name, ok, version))
            return results

        def done(result, err):
            self._dep_loading = False
            self.recheck_btn.setEnabled(True)
            self._stop_dep_skeleton()
            if err or result is None:
                self._clear_dep_rows()
                tip = QLabel(f"❌ 依赖检测失败: {err or '未知错误'}")
                tip.setStyleSheet("color: #d32f2f; font-size: 11px; padding: 2px;")
                self.dependency_layout.addWidget(tip)
                return
            self._dep_cache = result
            self._fill_dep_rows(result)

        run_bg(work, done)

    # ----- 骨架屏 -----

    def _clear_dep_rows(self):
        while self.dependency_layout.count():
            item = self.dependency_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _skel_bar(self, width=None):
        bar = QLabel()
        bar.setFixedHeight(14)
        if width:
            bar.setFixedWidth(width)
        bar.setStyleSheet("background: #dfe4ea; border-radius: 4px;")
        self._skel_bars.append(bar)
        return bar

    def _show_dep_skeleton(self):
        """按依赖行布局渲染灰色骨架条占位（后台检测完成后替换）。"""
        self._clear_dep_rows()
        self._skel_bars = []
        for _spec in self._DEPENDENCIES:
            row = QFrame()
            row.setObjectName("depSkelRow")
            row.setStyleSheet(
                "QFrame#depSkelRow { background: #f4f6f9; border-radius: 4px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            for width in (70, 110, 180):
                h.addWidget(self._skel_bar(width))
            h.addWidget(self._skel_bar(), 1)
            self.dependency_layout.addWidget(row)
        tip = QLabel("⏳ 正在检测依赖…")
        tip.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        self.dependency_layout.addWidget(tip)
        self._skel_phase = 0
        self._skel_timer.start()

    def _pulse_dep_skeleton(self):
        self._skel_phase = 1 - self._skel_phase
        color = "#e7ebf1" if self._skel_phase else "#d7dde6"
        for bar in self._skel_bars:
            bar.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def _stop_dep_skeleton(self):
        self._skel_timer.stop()
        self._skel_bars = []

    def _fill_dep_rows(self, results):
        """按检测结果重建依赖清单：每项一行 + 一键下载/安装按钮（缺失项标红）。"""
        self._clear_dep_rows()
        missing = 0
        for (key, name, fn_name, usage, url), (_rk, _rn, ok, version) in zip(
                self._DEPENDENCIES, results):
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
            if key in ("ffmpeg", "ffprobe"):
                exe = find_ffmpeg_exe() if key == "ffmpeg" else find_ffprobe_exe()
                if exe not in ("ffmpeg", "ffprobe"):
                    ver_lbl.setToolTip(f"位置: {exe}")
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

    def _probe_tool(self, exe: str):
        """运行 <exe> -version，返回 (ok, 版本号, 首行原文)。

        版本号只取首行 "version" 后的一个词（如 6.1.1 / 9.0-full_build-www.gyan.dev），
        避免整行截断后出现 "…Copyright (c) 2" 这类残缺串。
        """
        import subprocess
        try:
            r = subprocess.run([exe, "-version"], capture_output=True, timeout=3, text=True)
        except Exception:
            return False, "", ""
        if r.returncode != 0:
            return False, "", ""
        head = (r.stdout or "").splitlines()[0] if r.stdout else ""
        m = re.search(r"version\s+(\S+)", head)
        return True, (m.group(1) if m else head[:40]), head

    def _check_ffmpeg_detail(self):
        ok, ver, _head = self._probe_tool(find_ffmpeg_exe())
        return ok, ver

    def _check_ffprobe_detail(self):
        ok, ver, _head = self._probe_tool(find_ffprobe_exe())
        return ok, ver

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
