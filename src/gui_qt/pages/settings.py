"""全局设置页（PySide6 版，对应原版 gui/global_settings.py）。

- 默认工作目录（set_work_dir + 全局广播 on_dir_changed）
- 统一导出目录（set_export_dir，目录不存在自动创建）
- 配置与依赖检查（项目根/配置目录/工作目录/FFmpeg 位置/清理 __pycache__ 和 .pyc/依赖检测）
- 关于（制作歆慕 / B站主页 / 官方网站）
"""

import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QTimeEdit, QToolButton, QVBoxLayout,
    QWidget, QFrame,
)
from PySide6.QtGui import QPixmap

from image_sorter.core.cache_manager import CacheManager
from toolbox.config_manager import (
    get_app_root, get_config_dir, get_export_dir, load_convert_config,
    has_date_subfolders, save_convert_config, set_export_dir, set_ffmpeg_path,
    set_work_dir,
)
from toolbox.skills import list_skills, skill_total_size_text
from toolbox.utils import (find_ffmpeg_exe, find_ffprobe_exe, format_size,
                           invalidate_ffmpeg_cache)
from toolbox import edition, updater
from version import APP_VERSION

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.backup_panel import BackupPanel
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard


# FFmpeg 一键下载源：gyan.dev 是 ffmpeg.org 下载页指向的 Windows 官方构建站，
# release-essentials 包同时含 ffmpeg.exe / ffprobe.exe / ffplay.exe（只取前两个）
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_DOWNLOAD_PAGE = "https://www.gyan.dev/ffmpeg/builds/"


def _extract_ffmpeg_binaries(zip_path, target):
    """从官方 zip 只取 ffmpeg.exe / ffprobe.exe（包内结构 <版本>/bin/*.exe）。"""
    import shutil as _shutil
    import zipfile

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            member = next(
                (n for n in names
                 if n.replace("\\", "/").split("/")[-1].lower() == exe), "")
            if not member:
                raise RuntimeError(f"安装包内未找到 {exe}")
            with zf.open(member) as src, open(target / exe, "wb") as dst:
                _shutil.copyfileobj(src, dst)
    return target


class _SettingsScrollArea(QScrollArea):
    """宽度跟随视口；换行标签的期望高度按实际宽度重算，窄窗口不压扁卡片裁字。"""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_content()

    def sync_content(self):
        body = self.widget()
        if body is None or body.layout() is None:
            return
        width = max(1, self.viewport().width())
        need = body.layout().heightForWidth(width)
        if need <= 0:
            need = body.sizeHint().height()
        body.setMinimumHeight(max(need, self.viewport().height()))


class _ComponentsSectionCard(ToolSectionCard):
    """设置页组件区：整行标题切换展开状态，不影响其他页面的共享卡片。"""

    def __init__(self, title, icon):
        super().__init__(title, icon=icon)
        layout = self.layout()
        layout.removeItem(self.heading_layout)
        layout.removeItem(self.content_layout)
        self.heading_layout.setParent(None)
        self.content_layout.setParent(None)

        self.toggle_button = QPushButton(self)
        self.toggle_button.setObjectName("settingsComponentsToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setAccessibleName(title)
        self.toggle_button.setMinimumHeight(24)
        self.toggle_button.setStyleSheet(
            "QPushButton#settingsComponentsToggle { background: transparent;"
            " border: 1px solid transparent; padding: 0; }"
            "QPushButton#settingsComponentsToggle:hover {"
            " background: palette(alternate-base); border-radius: 6px; }"
            "QPushButton#settingsComponentsToggle:focus {"
            " border: 1px solid palette(highlight); }")
        header = QHBoxLayout(self.toggle_button)
        header.setContentsMargins(0, 0, 0, 0)
        header.addLayout(self.heading_layout, 1)
        for label in (self.icon_label, self.title_label):
            if label is not None:
                label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._arrow = QToolButton(self.toggle_button)
        self._arrow.setAutoRaise(True)
        self._arrow.setFocusPolicy(Qt.NoFocus)
        self._arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self._arrow)
        layout.addWidget(self.toggle_button)

        self.content_widget = QWidget(self)
        self.content_widget.setLayout(self.content_layout)
        layout.addWidget(self.content_widget)
        self.toggle_button.toggled.connect(self._set_expanded)
        self._set_expanded(False)

    def _set_expanded(self, expanded):
        self.content_widget.setVisible(expanded)
        self._arrow.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_button.setToolTip("收起组件" if expanded else "展开组件")
        self.updateGeometry()


class GlobalSettingsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        # 可滚动内容
        scroll = _SettingsScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self._section_cards = []  # [(card, icon_name)] 供主题切换时重绘图标

        # ---- 默认工作目录 ----
        card = self._section_card("默认工作文件夹（用于录播工具箱）", "folder")
        wd_row = QHBoxLayout()
        self.workdir_entry = QLineEdit()
        self.workdir_entry.setPlaceholderText("工作目录路径")
        wd_row.addWidget(self.workdir_entry, 1)
        wd_row.addWidget(self._btn("浏览...", self._browse))
        wd_row.addWidget(self._btn("保存", self._save))
        self.workdir_lock_cb = QCheckBox("锁定工作文件夹")
        self.workdir_lock_cb.setToolTip("锁定后拖入文件夹不会改变此目录，直到解锁")
        wd_row.addWidget(self.workdir_lock_cb)
        card.content_layout.addLayout(wd_row)
        self.workdir_status = QLabel("")
        self.workdir_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(self.workdir_status)
        v.addWidget(card)

        # ---- 统一导出目录 ----
        card = self._section_card("统一导出目录（音乐/已获取/信息获取数据导出）", "upload")
        ex_row = QHBoxLayout()
        self.exportdir_entry = QLineEdit()
        self.exportdir_entry.setPlaceholderText("导出目录路径（默认 download/）")
        ex_row.addWidget(self.exportdir_entry, 1)
        ex_row.addWidget(self._btn("浏览...", self._browse_export))
        ex_row.addWidget(self._btn("保存", self._save_export))
        self.exportdir_lock_cb = QCheckBox("锁定导出文件夹")
        self.exportdir_lock_cb.setToolTip("锁定后拖入文件夹不会改变此目录，直到解锁")
        ex_row.addWidget(self.exportdir_lock_cb)
        card.content_layout.addLayout(ex_row)
        self.exportdir_status = QLabel("")
        self.exportdir_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(self.exportdir_status)
        v.addWidget(card)
        # 文件夹锁定：改动即保存（拖入文件夹不改变，直到解锁）
        self.workdir_lock_cb.toggled.connect(self._on_workdir_lock_changed)
        self.exportdir_lock_cb.toggled.connect(self._on_exportdir_lock_changed)

        # ---- 主题自动切换（跟随系统 / 定时黑白） ----
        card = self._section_card("主题自动切换（跟随系统 / 定时黑白）", "palette")
        fs_row = QHBoxLayout()
        self.follow_sys_cb = QCheckBox("跟随系统深浅色")
        self.follow_sys_cb.setToolTip(
            "系统在 浅色/深色 模式之间切换时，应用主题自动跟随同步；\n"
            "与定时切换互斥（同时只能开一个）；手动点击侧栏主题方块会关闭本功能")
        fs_row.addWidget(self.follow_sys_cb)
        fs_row.addStretch(1)
        card.content_layout.addLayout(fs_row)
        ts_row = QHBoxLayout()
        self.sched_enable = QCheckBox("启用定时切换")
        self.sched_enable.setToolTip(
            "到达设定时间自动切换主题，改动即时生效；\n"
            "启用期间手动切换会在数秒内被拉回目标主题，关闭后手动切换不受影响")
        ts_row.addWidget(self.sched_enable)
        ts_row.addStretch(1)
        ts_row.addWidget(QLabel("浅色时间:"))
        self.sched_light = QTimeEdit()
        self.sched_light.setDisplayFormat("HH:mm")
        self.sched_light.setFixedWidth(96)
        ts_row.addWidget(self.sched_light)
        ts_row.addWidget(QLabel("深色时间:"))
        self.sched_dark = QTimeEdit()
        self.sched_dark.setDisplayFormat("HH:mm")
        self.sched_dark.setFixedWidth(96)
        ts_row.addWidget(self.sched_dark)
        ts_row.addWidget(self._btn("保存", self._save_theme_schedule_clicked))
        card.content_layout.addLayout(ts_row)
        self.sched_status = QLabel("")
        self.sched_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(self.sched_status)
        theme_note = QLabel("配色由全局色板统一维护，页面不提供自定义配色。")
        theme_note.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(theme_note)
        v.addWidget(card)
        # 改动即时生效：开关/时间一变就写入配置并立刻对齐主题（无需点保存）
        self.follow_sys_cb.toggled.connect(self._on_follow_system_changed)
        self.sched_enable.toggled.connect(self._on_schedule_changed)
        self.sched_light.timeChanged.connect(self._on_schedule_changed)
        self.sched_dark.timeChanged.connect(self._on_schedule_changed)

        # ---- 后台模式 ----
        card = self._section_card("后台模式", "pulse")
        self.close_to_tray_cb = QCheckBox("启用后台模式（关闭主窗口时隐藏到系统托盘）")
        self.close_to_tray_cb.setToolTip(
            "勾选后点关闭只会隐藏窗口，任务和录制继续运行，并保留托盘图标；\n"
            "双击托盘图标恢复窗口，右键托盘图标可退出软件。\n"
            "取消勾选即取消后台：不再保留托盘图标，点关闭会直接退出软件。")
        self.close_to_tray_cb.toggled.connect(self._on_close_to_tray_changed)
        card.content_layout.addWidget(self.close_to_tray_cb)
        v.addWidget(card)

        # ---- WebUI 独立服务 ----
        card = self._section_card("WebUI 独立服务（浏览器访问）", "globe")
        webui_row = QHBoxLayout()
        webui_row.addWidget(QLabel("端口:"))
        self.webui_port = QLineEdit()
        self.webui_port.setPlaceholderText("8765")
        self.webui_port.setFixedWidth(80)
        webui_row.addWidget(self.webui_port)
        self.webui_start_btn = self._btn("启动", self._start_webui)
        self.webui_stop_btn = self._btn("停止", self._stop_webui)
        self.webui_open_btn = self._btn("打开 WebUI", self._open_webui)
        self.webui_stop_btn.setEnabled(False)
        self.webui_open_btn.setEnabled(False)
        webui_row.addWidget(self.webui_start_btn)
        webui_row.addWidget(self.webui_stop_btn)
        webui_row.addWidget(self.webui_open_btn)
        webui_row.addStretch(1)
        card.content_layout.addLayout(webui_row)
        webui_lan_row = QHBoxLayout()
        self.webui_lan_cb = QCheckBox("允许局域网访问（手机同一 Wi-Fi）")
        self.webui_lan_cb.setToolTip(
            "勾选后 WebUI 监听局域网地址，手机与电脑连同一 Wi-Fi 即可打开；\n"
            "首次使用需要在 Windows 防火墙提示中允许访问。公网访问请使用隧道，不要直接映射端口。")
        self.webui_lan_cb.toggled.connect(self._on_webui_lan_changed)
        self.webui_qr_btn = self._btn("手机二维码", self._open_webui_qr)
        self.webui_qr_btn.setEnabled(False)
        webui_lan_row.addWidget(self.webui_lan_cb)
        webui_lan_row.addWidget(self.webui_qr_btn)
        webui_lan_row.addStretch(1)
        card.content_layout.addLayout(webui_lan_row)
        self.webui_auto_cb = QCheckBox("随软件启动 WebUI 服务")
        self.webui_auto_cb.setToolTip(
            "勾选后下次打开软件自动在后台启动 WebUI，关闭软件时停止由本软件启动的服务；\n"
            "端口在点「启动」时保存。WebUI 是独立进程，不依赖桌面窗口。")
        self.webui_auto_cb.toggled.connect(self._on_webui_auto_changed)
        card.content_layout.addWidget(self.webui_auto_cb)
        self.webui_status = QLabel("")
        self.webui_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.webui_status.setWordWrap(True)
        card.content_layout.addWidget(self.webui_status)
        v.addWidget(card)

        # ---- WebUI 公网访问（隧道） ----
        card = self._section_card("WebUI 公网访问（隧道）", "globe")
        tunnel_row = QHBoxLayout()
        self.tunnel_mode = QComboBox()
        self.tunnel_mode.addItem("关闭", "none")
        self.tunnel_mode.addItem("Cloudflare 快速隧道", "cloudflare")
        self.tunnel_mode.addItem("Tailscale（私有网络）", "tailscale")
        self.tunnel_mode.addItem("frp（自建服务器）", "frp")
        self.tunnel_mode.setToolTip(
            "Cloudflare 快速隧道提供临时公网 HTTPS 地址，无需账号；\n"
            "Tailscale 使用私有网络（需登录，WebUI 需允许局域网）；\n"
            "frp 需要选择 frpc 配置文件，并在服务器侧配置公网域名。")
        self.tunnel_mode.currentIndexChanged.connect(
            self._on_tunnel_mode_changed)
        tunnel_row.addWidget(self.tunnel_mode)
        self.tunnel_start_btn = self._btn("启动隧道", self._start_tunnel)
        self.tunnel_stop_btn = self._btn("停止隧道", self._stop_tunnel)
        self.tunnel_stop_btn.setEnabled(False)
        tunnel_row.addWidget(self.tunnel_start_btn)
        tunnel_row.addWidget(self.tunnel_stop_btn)
        tunnel_row.addStretch(1)
        card.content_layout.addLayout(tunnel_row)
        self.tunnel_frp_row = QHBoxLayout()
        self.tunnel_frp_row.addWidget(QLabel("frpc 配置:"))
        self.tunnel_frp_config = QLineEdit()
        self.tunnel_frp_config.setPlaceholderText("选择 frpc.toml / frpc.ini")
        self.tunnel_frp_row.addWidget(self.tunnel_frp_config, 1)
        self.tunnel_frp_browse = self._btn("选择", self._pick_frp_config)
        self.tunnel_frp_row.addWidget(self.tunnel_frp_browse)
        card.content_layout.addLayout(self.tunnel_frp_row)
        self.tunnel_status = QLabel("")
        self.tunnel_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.tunnel_status.setWordWrap(True)
        card.content_layout.addWidget(self.tunnel_status)
        v.addWidget(card)

        # ---- 配置信息与缓存清理 ----
        card = self._section_card("配置信息与缓存清理", "broom")
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.info_label.setWordWrap(True)
        card.content_layout.addWidget(self.info_label)
        info_row = QHBoxLayout()
        info_row.addWidget(self._btn("刷新信息", self._refresh_info))
        info_row.addWidget(self._btn("清理 Python 缓存 (__pycache__)", self._clean_pycache))
        info_row.addWidget(self._btn("清理媒体筛选缓存", self._clean_media_sorter_cache))
        info_row.addStretch(1)
        card.content_layout.addLayout(info_row)
        self.clean_result_label = QLabel("")
        self.clean_result_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(self.clean_result_label)
        v.addWidget(card)

        # ---- 运行环境与组件（FFmpeg / 依赖 / 模型 / Skill）----
        card = _ComponentsSectionCard(
            "运行环境与组件（FFmpeg / 依赖 / 模型 / Skill）", "chip")
        self.components_card = card
        self._section_cards.append((card, "chip"))
        card.toggle_button.toggled.connect(
            lambda _expanded: QTimer.singleShot(0, self.scroll.sync_content))

        # FFmpeg 位置（可选；系统 PATH 优先，未找到时才用这里指定的位置）
        ff_row = QHBoxLayout()
        self.ffmpeg_entry = QLineEdit()
        self.ffmpeg_entry.setPlaceholderText(
            "留空 = 自动使用系统 PATH；也可填 ffmpeg 所在目录或 ffmpeg.exe 完整路径")
        ff_row.addWidget(self.ffmpeg_entry, 1)
        ff_row.addWidget(self._btn("浏览...", self._browse_ffmpeg))
        ff_row.addWidget(self._btn("保存", self._save_ffmpeg))
        card.content_layout.addLayout(ff_row)
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.ffmpeg_status.setWordWrap(True)
        card.content_layout.addWidget(self.ffmpeg_status)

        # 检测依赖与其他强依赖：缺失项标红 + 快捷下载按钮
        dep_head = QHBoxLayout()
        dep_title = QLabel("依赖与可选组件")
        dep_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        dep_head.addWidget(dep_title)
        dep_head.addStretch(1)
        self.recheck_btn = QPushButton("重新检测")
        self.recheck_btn.setCursor(Qt.PointingHandCursor)
        self.recheck_btn.clicked.connect(self._refresh_dependencies)
        dep_head.addWidget(self.recheck_btn)
        card.content_layout.addLayout(dep_head)
        # 占位：_refresh_dependencies 重建 self.dependency_layout
        self.dependency_layout = QVBoxLayout()
        self.dependency_layout.setContentsMargins(0, 0, 0, 0)
        self.dependency_layout.setSpacing(3)
        card.content_layout.addLayout(self.dependency_layout)
        # 组件下载进度/结果（采集引擎、转写程序与模型、图识别模型）
        self.dep_status = QLabel("")
        self.dep_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.dep_status.setWordWrap(True)
        card.content_layout.addWidget(self.dep_status)
        v.addWidget(card)
        # 骨架屏加载：检测在后台线程跑，页面打开即刻渲染灰色占位条（__init__ 末尾 on_show 已触发）
        self._dep_cache = None      # [(key, name, ok, version)] 上次检测结果
        self._dep_loading = False
        self._skel_bars = []
        self._skel_phase = 0
        self._skel_timer = QTimer(self)
        self._skel_timer.setInterval(480)
        self._skel_timer.timeout.connect(self._pulse_dep_skeleton)
        self._ffmpeg_ok = None     # FFmpeg 可用性缓存（None = 未检测）
        self._ffmpeg_installing = False  # 一键下载进行中（防重复点击）
        self._dep_downloading = False    # 组件下载进行中（防重复点击）

        # ---- AI Skill 下载（内置 Skill 导出到本地，供 AI 助手使用） ----
        card = self._section_card("AI Skill（导出到本地）", "robot")
        skill_row = QHBoxLayout()
        self._skill_list = list_skills()
        skill_count = len(self._skill_list)
        names = "、".join(m["display_name"] for m in self._skill_list) or "无"
        skill_info = QLabel(
            f"内置 {skill_count} 个 Skill（{skill_total_size_text()}）：{names}\n"
            "导出后可将 Skill 文件夹放入 AI 助手的 Skill 目录使用（如直播转录总结）")
        skill_info.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        skill_info.setWordWrap(True)
        skill_row.addWidget(skill_info, 1)
        skill_row.addWidget(self._btn("下载 Skill…", self._open_skill_download))
        card.content_layout.addLayout(skill_row)
        v.addWidget(card)

        # ---- 备份与恢复 ----
        card = self._section_card("备份与恢复", "package")
        backup_note = QLabel(
            "配置与资产备份都在本卡片完成；网盘文件下载与网盘备份在「网盘」页，"
            "计划任务仍在「任务」页管理。")
        backup_note.setProperty("role", "caption")
        backup_note.setWordWrap(True)
        card.content_layout.addWidget(backup_note)
        self.backup_panel = BackupPanel(app, self)
        card.content_layout.addWidget(self.backup_panel)
        v.addWidget(card)

        # ---- 关于 ----
        card = self._section_card("关于", "question")
        maker_row = QHBoxLayout()
        maker_lbl = QLabel("制作：歆慕")
        maker_lbl.setStyleSheet("color: palette(link); font-weight: bold;")
        maker_row.addWidget(maker_lbl)
        maker_row.addStretch(1)
        maker_row.addWidget(self._btn(
            "B站主页",
            lambda: self._open_site("B站主页", "https://space.bilibili.com/622821754")))
        maker_row.addWidget(self._btn(
            "抖音主页",
            lambda: self._open_site(
                "抖音主页",
                "https://www.douyin.com/user/MS4wLjABAAAANr2f2DlSduMtGo3LtUm2MoxXu9cblLhTs2pJRDDZQHY")))
        maker_row.addWidget(self._btn(
            "微博主页",
            lambda: self._open_site("微博主页", "https://weibo.com/u/7262942477")))
        card.content_layout.addLayout(maker_row)

        # 四个站点直接做成按钮（3 个官方站点 + 数据库 Wiki）
        sites = [
            ("封茗囧菌数据汇总库", "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"),
            ("无料周边汇总", "https://docs.qq.com/smartsheet/DRkZIbkZtQ0taTHd4"),
            ("用户协议与编辑须知",
             "https://docs.qq.com/aio/p/scisutrcci3apy1?p=w5TSrLIyhtOn7j9KjoGXP3"),
            ("数据库 Wiki", "https://mandisa.wiki"),
        ]
        for start in (0, 2):
            site_row = QHBoxLayout()
            for name, url in sites[start:start + 2]:
                b = self._btn(
                    f"{name}", lambda n=name, u=url: self._open_site(n, u))
                b.setToolTip(url)
                b.setCursor(Qt.PointingHandCursor)
                site_row.addWidget(b, 1)
            card.content_layout.addLayout(site_row)

        repo_row = QHBoxLayout()
        repo_row.addWidget(self._btn(
            "GitHub 仓库",
            lambda: self._open_site(
                "GitHub 仓库", "https://github.com/FmjjTool/FMJJRecordedBroadcast")))
        repo_row.addWidget(self._btn(
            "开源许可证（GPL-3.0）",
            lambda: self._open_site(
                "GPL-3.0 许可证",
                "https://github.com/FmjjTool/FMJJRecordedBroadcast/blob/main/LICENSE")))
        repo_row.addStretch(1)
        card.content_layout.addLayout(repo_row)

        # ---- 软件更新（GitHub Releases） ----
        up_row = QHBoxLayout()
        self.update_version_lbl = QLabel(f"当前版本 v{APP_VERSION}")
        self.update_version_lbl.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        up_row.addWidget(self.update_version_lbl)
        up_row.addStretch(1)
        self.update_btn = self._btn("检查更新", self._check_update)
        self.update_btn.setCursor(Qt.PointingHandCursor)
        up_row.addWidget(self.update_btn)
        self.update_cb = QCheckBox("启动时自动检查更新（每天最多一次）")
        self.update_cb.setToolTip(
            "程序启动约 15 秒后在后台查询 GitHub Releases；发现新版本只弹一次托盘提示，\n"
            "不会自动下载或安装。是否真正更新始终由你在本页点击决定。")
        self.update_cb.toggled.connect(self._on_auto_update_changed)
        up_row.addWidget(self.update_cb)
        card.content_layout.addLayout(up_row)
        self.update_status = QLabel("")
        self.update_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.update_status.setWordWrap(True)
        card.content_layout.addWidget(self.update_status)

        about = QLabel(
            "封茗囧菌数据汇总库 · 统一维护工具箱 · 制作歆慕\n"
            "开源协议：GPL-3.0（GNU General Public License v3.0）\n"
            "GitHub：https://github.com/FmjjTool/FMJJRecordedBroadcast\n"
            "依赖: ffmpeg, ffprobe, PySide6"
        )
        about.setWordWrap(True)
        about.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        card.content_layout.addWidget(about)
        v.addWidget(card)

        v.addStretch(1)

        self.on_show()

    # ---- 小工具 ----
    def _section_card(self, title, icon):
        card = ToolSectionCard(title, icon=icon)
        self._section_cards.append((card, icon))
        return card

    def on_theme_changed(self):
        for card, icon in self._section_cards:
            card.set_icon(icon)
        self.scroll.sync_content()

    def _btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        return b

    # 状态行统一配色：正常=次级灰、检测通过=绿、异常=红（两条状态行共用同一套规则）
    _STATUS_TONES = {"muted": "placeholder-text", "ok": "link-visited", "warn": "bright-text"}

    def _set_status(self, label, text, tone="muted"):
        label.setText(text)
        color = self._STATUS_TONES.get(tone, "placeholder-text")
        label.setStyleSheet(f"color: palette({color}); font-size: 11px;")

    # ---- 逻辑 ----
    def scroll_to_backup(self):
        """滚动到「备份与恢复」卡片（资产管理页「备份」按钮跳转用）。"""
        QTimer.singleShot(
            0, lambda: self.scroll.ensureWidgetVisible(self.backup_panel, 0, 24))

    def on_show(self):
        cfg = load_convert_config()
        # 工作目录
        self.workdir_entry.setText(cfg.get("work_dir", ""))
        # 文件夹锁定状态（屏蔽信号：程序 setChecked 不触发「改动即保存」）
        for cb, key in ((self.workdir_lock_cb, "work_dir_locked"),
                        (self.exportdir_lock_cb, "export_dir_locked")):
            cb.blockSignals(True)
            cb.setChecked(bool(cfg.get(key)))
            cb.blockSignals(False)
        self._refresh_workdir_status()
        # 导出目录
        exd = str(get_export_dir())
        self.exportdir_entry.setText(exd)
        self._refresh_exportdir_status()
        # 主题定时切换
        self._load_theme_schedule()
        # 后台运行（关闭窗口隐藏到托盘）
        self.close_to_tray_cb.blockSignals(True)
        self.close_to_tray_cb.setChecked(bool(cfg.get("close_to_tray", True)))
        self.close_to_tray_cb.blockSignals(False)
        # 软件更新：自动检查开关 + 已发现的新版本提示
        self.update_cb.blockSignals(True)
        self.update_cb.setChecked(bool(cfg.get("auto_check_update", True)))
        self.update_cb.blockSignals(False)
        self._sync_update_status()
        # FFmpeg 位置
        self.ffmpeg_entry.setText(str(load_convert_config().get("ffmpeg_path", "") or ""))
        self._update_ffmpeg_status()
        # 信息
        self._refresh_info(show_toast=False)
        # 依赖检测（每次回到本页都重检一次，不依赖外部信号）
        self._refresh_dependencies()
        # WebUI 独立服务：端口、自动启动与运行状态
        self._load_webui()
        self._load_webui_tunnel()

    def on_hide(self):
        self._skel_timer.stop()

    # ---- WebUI 独立服务 ----
    def _webui(self):
        from toolbox.webui_launcher import get_launcher
        return get_launcher()

    def _load_webui(self):
        cfg = self._webui().config()
        self.webui_port.setText(str(cfg["port"]))
        self.webui_auto_cb.blockSignals(True)
        self.webui_auto_cb.setChecked(bool(cfg["auto_start"]))
        self.webui_auto_cb.blockSignals(False)
        self.webui_lan_cb.blockSignals(True)
        self.webui_lan_cb.setChecked(bool(cfg["lan"]))
        self.webui_lan_cb.blockSignals(False)
        self._refresh_webui_async()

    def _refresh_webui_async(self):
        launcher = self._webui()
        self._set_status(self.webui_status, "正在检查 WebUI 状态…", "muted")
        run_bg(launcher.status,
               lambda status, err: self._apply_webui_status(status, err))

    def _apply_webui_status(self, status, err=None):
        if err is not None or not isinstance(status, dict):
            self._set_status(self.webui_status, f"状态检查失败：{err}", "warn")
            self.webui_start_btn.setEnabled(True)
            self.webui_stop_btn.setEnabled(False)
            self.webui_open_btn.setEnabled(False)
            return
        running = bool(status.get("running"))
        owned = bool(status.get("owned"))
        self.webui_start_btn.setEnabled(not running)
        self.webui_stop_btn.setEnabled(running and owned)
        self.webui_open_btn.setEnabled(running)
        self.webui_qr_btn.setEnabled(running and bool(status.get("lan_url")))
        if running:
            note = "" if owned else "（由其他方式启动）"
            text = f"运行中 · 本机 http://127.0.0.1:{status.get('port')}{note}"
            if status.get("lan_ip"):
                text += f" · 手机 http://{status['lan_ip']}:{status.get('port')}"
            self._set_status(self.webui_status, text, "ok")
        else:
            message = status.get("error") or "未运行"
            self._set_status(self.webui_status, message,
                             "warn" if status.get("error") else "muted")

    def _webui_port_value(self):
        text = self.webui_port.text().strip()
        try:
            port = int(text or "8765")
        except ValueError:
            self._set_status(self.webui_status,
                             "端口必须是 1024-65535 之间的数字", "warn")
            return None
        if not 1024 <= port <= 65535:
            self._set_status(self.webui_status,
                             "端口必须是 1024-65535 之间的数字", "warn")
            return None
        return port

    def _start_webui(self):
        port = self._webui_port_value()
        if port is None:
            return
        lan = self.webui_lan_cb.isChecked()
        self.webui_start_btn.setEnabled(False)
        self._set_status(self.webui_status, "正在启动 WebUI…", "muted")
        launcher = self._webui()

        def work():
            launcher.save_config(port=port, lan=lan)
            return launcher.start(port, lan=lan)

        def done(status, err):
            if err is not None:
                self._set_status(self.webui_status, f"启动失败：{err}", "warn")
                self._refresh_webui_async()
                return
            self.app.toast("WebUI 已启动", "success")
            self._apply_webui_status(status, None)

        run_bg(work, done)

    def _stop_webui(self):
        self.webui_stop_btn.setEnabled(False)
        self._set_status(self.webui_status, "正在停止 WebUI…", "muted")
        launcher = self._webui()

        def done(result, err):
            if err is not None:
                self._set_status(self.webui_status, f"停止失败：{err}", "warn")
            elif result and result.get("stopped"):
                self.app.toast("WebUI 已停止", "info")
            else:
                self.app.toast((result or {}).get("message", "WebUI 未运行"),
                               "info")
            self._refresh_webui_async()

        run_bg(launcher.stop, done)

    def _open_webui(self):
        try:
            self._webui().open_browser()
        except Exception as exc:
            self._set_status(self.webui_status, str(exc), "warn")
            self._refresh_webui_async()

    def _open_webui_qr(self):
        launcher = self._webui()
        status = launcher.status()
        if not status.get("running"):
            self._set_status(self.webui_status, "WebUI 未运行，请先启动", "warn")
            return
        if not status.get("lan_url"):
            self._set_status(self.webui_status,
                             "当前未开启局域网访问，二维码不可用", "warn")
            return
        try:
            import io

            import qrcode

            image = qrcode.make(launcher.phone_url())
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pixmap = QPixmap()
            if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
                raise ValueError("二维码图片无效")
        except Exception as exc:
            self._set_status(self.webui_status, f"二维码生成失败：{exc}", "warn")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("手机访问 WebUI")
        layout = QVBoxLayout(dialog)
        picture = QLabel()
        picture.setPixmap(pixmap.scaled(260, 260, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))
        picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(picture)
        note = QLabel(
            f"手机与电脑连同一 Wi-Fi 后扫码打开。\n"
            f"局域网地址：http://{status.get('lan_ip')}:{status.get('port')}\n"
            "二维码包含访问令牌，请勿转发给不可信设备。")
        note.setWordWrap(True)
        layout.addWidget(note)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def _on_webui_lan_changed(self, checked):
        try:
            self._webui().save_config(lan=bool(checked))
        except Exception as exc:
            self.app.toast(f"保存 WebUI 设置失败：{exc}", "error")
            return
        self._set_status(self.webui_status,
                         "局域网设置已保存；重启 WebUI 后生效", "muted")
        self._refresh_webui_async()

    def _on_webui_auto_changed(self, checked):
        try:
            self._webui().save_config(auto_start=bool(checked))
        except Exception as exc:
            self.app.toast(f"保存 WebUI 设置失败：{exc}", "error")

    # ---- WebUI 公网访问（隧道） ----
    def _tunnel(self):
        from toolbox.webui_tunnel import get_tunnel
        return get_tunnel()

    def _load_webui_tunnel(self):
        cfg = self._tunnel().config()
        index = self.tunnel_mode.findData(cfg["mode"])
        self.tunnel_mode.blockSignals(True)
        self.tunnel_mode.setCurrentIndex(max(0, index))
        self.tunnel_mode.blockSignals(False)
        self.tunnel_frp_config.setText(cfg.get("frp_config") or "")
        self._update_frp_row()
        self._refresh_tunnel_async()

    def _update_frp_row(self):
        frp = (self.tunnel_mode.currentData() or "") == "frp"
        for index in range(self.tunnel_frp_row.count()):
            widget = self.tunnel_frp_row.itemAt(index).widget()
            if widget is not None:
                widget.setVisible(frp)
        self.tunnel_frp_row.setEnabled(frp)

    def _pick_frp_config(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择 frpc 配置文件", "",
            "frp 配置 (*.toml *.ini *.yaml *.yml);;所有文件 (*)")
        if not path:
            return
        self.tunnel_frp_config.setText(path)
        try:
            self._tunnel().save_config(frp_config=path)
        except Exception as exc:
            self.app.toast(f"保存 frp 配置失败：{exc}", "error")

    def _refresh_tunnel_async(self):
        run_bg(self._tunnel().status,
               lambda status, err: self._apply_tunnel_status(status, err))

    def _apply_tunnel_status(self, status, err=None):
        if err is not None or not isinstance(status, dict):
            self._set_status(self.tunnel_status, f"隧道状态检查失败：{err}",
                             "warn")
            return
        running = bool(status.get("running"))
        self.tunnel_start_btn.setEnabled(not running)
        self.tunnel_stop_btn.setEnabled(running)
        clients = status.get("clients") or {}
        mode = status.get("mode") or (self.tunnel_mode.currentData() or "")
        exe_key = {"cloudflare": "cloudflare", "tailscale": "tailscale",
                   "frp": "frp"}.get(mode, "cloudflare")
        exe_ok = bool(clients.get(exe_key) or "")
        if running:
            url = status.get("public_url") or "等待公网地址…"
            self._set_status(self.tunnel_status, f"运行中 · {url}", "ok")
            return
        text = "未运行"
        if not exe_ok:
            text += (" · 未检测到 " + {"cloudflare": "cloudflared",
                                        "tailscale": "Tailscale",
                                        "frp": "frpc"}[exe_key]
                     + "，请先在「运行环境与组件」查看")
        error = status.get("error") or ""
        self._set_status(self.tunnel_status, error or text,
                         "warn" if (error or not exe_ok) else "muted")

    def _start_tunnel(self):
        mode = self.tunnel_mode.currentData() or "none"
        if mode == "none":
            self._set_status(self.tunnel_status, "请选择隧道类型", "warn")
            return
        webui = self._webui().status()
        if not webui.get("running"):
            self._set_status(self.tunnel_status, "请先启动 WebUI 服务", "warn")
            return
        frp_config = self.tunnel_frp_config.text().strip()
        if mode == "frp" and not frp_config:
            self._set_status(self.tunnel_status, "请先选择 frpc 配置文件",
                             "warn")
            return
        self.tunnel_start_btn.setEnabled(False)
        self._set_status(self.tunnel_status, "正在启动隧道…", "muted")
        tunnel = self._tunnel()

        def work():
            tunnel.save_config(mode=mode, frp_config=frp_config)
            return tunnel.start(mode, int(webui["port"]))

        def done(status, err):
            if err is not None:
                self._set_status(self.tunnel_status, f"启动失败：{err}", "warn")
                self._refresh_tunnel_async()
                return
            self.app.toast("隧道已启动", "success")
            self._apply_tunnel_status(status, None)

        run_bg(work, done)

    def _stop_tunnel(self):
        self.tunnel_stop_btn.setEnabled(False)
        self._set_status(self.tunnel_status, "正在停止隧道…", "muted")
        tunnel = self._tunnel()

        def done(result, err):
            if err is not None:
                self._set_status(self.tunnel_status, f"停止失败：{err}", "warn")
            elif result and result.get("stopped"):
                self.app.toast("隧道已停止", "info")
            self._refresh_tunnel_async()

        run_bg(tunnel.stop, done)

    def _on_tunnel_mode_changed(self, _index):
        mode = self.tunnel_mode.currentData() or "none"
        self._update_frp_row()
        try:
            self._tunnel().save_config(mode=mode)
        except Exception as exc:
            self.app.toast(f"保存隧道设置失败：{exc}", "error")

    # ---- 文件夹锁定（锁定后拖入文件夹不改变，直到解锁） ----
    def _refresh_workdir_status(self):
        """工作目录状态行：日期子文件夹检测 + 锁定标记（通过=绿 / 异常=红）。"""
        wd = self.workdir_entry.text().strip()
        if not wd:
            return
        lock = "已锁定 · " if self.workdir_lock_cb.isChecked() else ""
        if os.path.isdir(wd):
            ok = has_date_subfolders(wd)
            hint = "已检测到日期子文件夹" if ok else "无日期子文件夹"
            self._set_status(self.workdir_status, f"{lock}{hint} · {wd}",
                             "ok" if ok else "warn")
        else:
            self._set_status(self.workdir_status, f"{lock}目录不存在: {wd}", "warn")

    def _refresh_exportdir_status(self):
        """导出目录状态行：当前路径 + 锁定标记（目录可用=绿 / 不存在=红）。"""
        lock = "已锁定 · " if self.exportdir_lock_cb.isChecked() else ""
        exd = self.exportdir_entry.text().strip() or str(get_export_dir())
        if os.path.isdir(exd):
            self._set_status(self.exportdir_status, f"{lock}当前: {exd}", "ok")
        else:
            self._set_status(self.exportdir_status, f"{lock}目录不存在: {exd}", "warn")

    def _on_workdir_lock_changed(self, checked):
        cfg = load_convert_config()
        cfg["work_dir_locked"] = bool(checked)
        save_convert_config(cfg)
        self._refresh_workdir_status()

    def _on_exportdir_lock_changed(self, checked):
        cfg = load_convert_config()
        cfg["export_dir_locked"] = bool(checked)
        save_convert_config(cfg)
        self._refresh_exportdir_status()

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
            sysd = "深色" if theme.system_theme() == "dark" else "浅色"
            cur = "深色" if theme.is_dark() else "浅色"
            self.sched_status.setText(
                f"已启用跟随系统：系统当前为 {sysd} · 主题已同步 {cur}（每 10 秒检测，系统切换即变）")
            self.sched_status.setStyleSheet("color: palette(link-visited); font-size: 11px;")
        elif self.sched_enable.isChecked():
            want = theme.scheduled_theme()
            if want:
                self.sched_status.setText(
                    f"已启用：当前为{'浅色' if want == 'light' else '深色'}时段 · "
                    f"{self.sched_light.time().toString('HH:mm')} 自动切浅色 / "
                    f"{self.sched_dark.time().toString('HH:mm')} 自动切深色（每 10 秒自动检测，到点即变）")
                self.sched_status.setStyleSheet("color: palette(link-visited); font-size: 11px;")
            else:
                self.sched_status.setText("两个时间点相同，定时无效，请修改")
                self.sched_status.setStyleSheet("color: palette(bright-text); font-size: 11px;")
        else:
            self.sched_status.setText("未启用：手动点击侧栏主题方块切换")
            self.sched_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")

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
            self.app.toast("主题自动切换设置已保存", "success")
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

    def _on_close_to_tray_changed(self, checked):
        cfg = load_convert_config()
        cfg["close_to_tray"] = bool(checked)
        save_convert_config(cfg)
        setter = getattr(self.app, "set_background_enabled", None)
        if callable(setter):
            setter(bool(checked))
        if self.app is not None:
            self.app.toast(
                "已开启后台模式" if checked else "已取消后台模式（关闭窗口将退出）",
                "info")

    def _open_site(self, label, url):
        """统一站点打开：所有「打开官方链接」按钮走这里，加 toast 提示。"""
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

    # ---- 软件更新（GitHub Releases） ----
    def _on_auto_update_changed(self, checked):
        cfg = load_convert_config()
        cfg["auto_check_update"] = bool(checked)
        save_convert_config(cfg)
        if checked:
            self._set_status(self.update_status, "已开启：启动后每天最多检查一次，仅托盘提示", "muted")

    def _check_update(self):
        info = getattr(self.app, "available_update", None)
        if info is not None and updater.is_newer(info.version, APP_VERSION):
            self._open_update_dialog(info)
            return
        self.update_btn.setEnabled(False)
        self.update_btn.setText("检查中…")
        self._set_status(self.update_status, "正在查询 GitHub Releases…", "muted")
        run_bg(lambda: updater.check_for_update(APP_VERSION), self._on_update_checked, owner=self)

    def _on_update_checked(self, result, err):
        self.update_btn.setEnabled(True)
        self.update_btn.setText("检查更新")
        if err:
            self._set_status(self.update_status, f"检查失败：{err}", "warn")
            return
        info, _ = result
        if info is None:
            self.app.available_update = None
            self._set_status(self.update_status, f"已是最新版本（v{APP_VERSION}）", "ok")
            return
        self.app.available_update = info
        self._show_update_available(info)
        self._open_update_dialog(info)

    def _show_update_available(self, info):
        self._set_status(
            self.update_status,
            f"发现新版本 v{info.version}（安装包 {info.size_text}）· 点右侧按钮查看并安装",
            "ok")
        self.update_btn.setText(f"更新到 v{info.version}")

    def _open_update_dialog(self, info):
        if not updater.is_newer(info.version, APP_VERSION):
            self.app.available_update = None
            self.update_btn.setText("检查更新")
            self._set_status(self.update_status, f"已是最新版本（v{APP_VERSION}）", "ok")
            return
        from gui_qt.update_dialog import UpdateDialog
        UpdateDialog(self.app, info, self).exec()

    def _sync_update_status(self):
        info = getattr(self.app, "available_update", None)
        if info is not None and updater.is_newer(info.version, APP_VERSION):
            self._show_update_available(info)
        else:
            self.update_btn.setText("检查更新")

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
        lock = "已锁定 · " if self.exportdir_lock_cb.isChecked() else ""
        self._set_status(self.exportdir_status, f"{lock}当前: {path}", "ok")
        self.app.toast("导出目录已保存", "success")

    def _info_text(self, ffmpeg_ok=None) -> str:
        cfg = load_convert_config()
        wd = cfg.get("work_dir", "")
        if ffmpeg_ok is None:
            ffmpeg_ok = self._ffmpeg_ok
        mark = "检测中…" if ffmpeg_ok is None else (
            "正常" if ffmpeg_ok else "未检测到")
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
                "未找到 ffmpeg：转换 / 音频提取 / 版权写入 / 直播录制将不可用，"
                "请安装后加入系统 PATH，或在上方指定位置")
            self.ffmpeg_status.setStyleSheet("color: palette(bright-text); font-size: 11px;")
            return
        import shutil
        src = "系统 PATH"
        which = shutil.which("ffmpeg")
        if not (which and os.path.normcase(os.path.abspath(exe))
                == os.path.normcase(os.path.abspath(which))):
            src = "自定义位置"
        self.ffmpeg_status.setText(f"当前使用（{src}）: {exe}")
        self.ffmpeg_status.setStyleSheet("color: palette(link-visited); font-size: 11px;")

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
                self.clean_result_label.setText(f"清理失败: {str(err)[:50]}")
                self.clean_result_label.setStyleSheet("color: palette(bright-text); font-size: 11px;")
                return
            d_n, f_n = result or (0, 0)
            scope = " + 工作目录" if len(targets) > 1 else ""
            self.clean_result_label.setText(
                f"清理完成: {d_n} 个 __pycache__ 目录, {f_n} 个 .pyc"
                f"（程序目录{scope}；运行中新生导入会重建，属正常）")
            self.clean_result_label.setStyleSheet("color: palette(link-visited); font-size: 11px;")

        self.clean_result_label.setText("清理中...")
        self.clean_result_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        run_bg(work, done)

    def _clean_media_sorter_cache(self):
        """清理媒体筛选页缓存（点击直接清理，无选择/确认弹窗）。

        覆盖：导入扫描缓存、磁盘缩略图缓存、MD5 计算缓存、
        已加载筛选页的内存预览缓存。
        不动「已分类 MD5 记录」——清掉后历史已分类文件会重新出现在待筛选列表。
        """
        def work():
            cm = CacheManager()
            cm.clear_scan_cache()
            cm.clear_thumbnail_cache()
            cm.clear_md5_cache()
            return str(cm.base_dir)

        def done(result, err):
            if err:
                self.clean_result_label.setText(f"清理失败: {str(err)[:50]}")
                self.clean_result_label.setStyleSheet("color: palette(bright-text); font-size: 11px;")
                self.app.toast(f"清理媒体筛选缓存失败: {str(err)[:40]}", "error")
                return
            page = self.app.pages.get("image_sorter")
            if page is not None:
                try:
                    page.clear_memory_caches()
                except Exception:
                    pass
            self.clean_result_label.setText("媒体筛选缓存已清理（导入扫描/缩略图/MD5）")
            self.clean_result_label.setStyleSheet("color: palette(link-visited); font-size: 11px;")
            self.app.toast("媒体筛选缓存已清理", "success")

        self.clean_result_label.setText("清理媒体筛选缓存中...")
        self.clean_result_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        run_bg(work, done)

    # ----- 检测依赖 -----

    # 依赖规格表：(键, 显示名, 检测函数, 用途说明, 下载地址/命令, 是否可选, 安装动作)
    # 检测函数返回 (ok: bool, version: str)
    # 安装动作 action：
    #   pip        - pip install <下载地址>
    #   ffmpeg     - 一键下载官方包 + 安装教程
    #   external   - 打开官方下载页，用户自行安装
    #   pw_shell / asr_program / asr_model / clip_model / proxy_core - 程序内一键下载组件
    # 可选依赖未安装时标「可选」而非「缺失」，不计入缺失数量，不影响主流程
    # FULL 内置版随包提供、状态显示「已内置」的组件
    _FULL_BUNDLED = {"ffmpeg", "ffprobe", "pw_shell", "asr_program",
                     "asr_model", "clip_model"}

    _DEPENDENCIES = [
        ("ffmpeg", "FFmpeg", "_check_ffmpeg_detail",
         "视频处理、转码、合并、截图等核心功能", "https://www.gyan.dev/ffmpeg/builds/", False, "ffmpeg"),
        ("ffprobe", "FFprobe", "_check_ffprobe_detail",
         "ffmpeg 同包，探测视频元数据（分辨率/时长/编码）", "https://www.gyan.dev/ffmpeg/builds/", False, "ffmpeg"),
        ("PySide6", "PySide6", "_check_pyside6_detail",
         "桌面 GUI 框架（Qt for Python）", "pip install PySide6", False, "pip"),
        ("requests", "requests", "_check_requests_detail",
         "HTTP 请求库（各平台 API 调用）", "pip install requests", False, "pip"),
        ("PIL", "Pillow", "_check_pillow_detail",
         "图片处理（封面 / 截图）", "pip install Pillow", False, "pip"),
        ("bs4", "BeautifulSoup4", "_check_bs4_detail",
         "HTML 解析（封面 / 简介提取）", "pip install beautifulsoup4", False, "pip"),
        ("lxml", "lxml", "_check_lxml_detail",
         "更快的 HTML/XML 解析依赖", "pip install lxml", False, "pip"),
        ("httpx", "httpx", "_check_httpx_detail",
         "HTTP 请求库（B站 / 视频解析 / 腾讯文档）", "pip install httpx", False, "pip"),
        ("openpyxl", "openpyxl", "_check_openpyxl_detail",
         "Excel 表格导入导出", "pip install openpyxl", False, "pip"),
        ("mutagen", "mutagen", "_check_mutagen_detail",
         "音乐标签读写（MP3 / FLAC 等）", "pip install mutagen", False, "pip"),
        ("websocket", "websocket-client", "_check_websocket_detail",
         "直播弹幕长连接（B站 / 抖音）", "pip install websocket-client", False, "pip"),
        ("playwright", "playwright", "_check_playwright_detail",
         "登录 / 采集浏览器驱动（缺失时相关页面不可用）", "pip install playwright", False, "pip"),
        ("douyin_sign", "抖音签名环境", "_check_douyin_sign_detail",
         "抖音评论区/弹幕/视频详情接口的 a_bogus 签名（node 或 Playwright 内置 node）",
         "https://nodejs.org/zh-cn", False, "external"),
        ("pw_shell", "采集引擎", "_check_pw_shell_detail",
         "无系统 Edge/Chrome 时的浏览器内核（约 91MB，系统已装 Edge 则不需要）",
         "https://playwright.dev/", True, "pw_shell"),
        ("asr_program", "本地转写程序", "_check_asr_program_detail",
         "离线字幕转写程序（约 84MB；不装可用在线转写接口）",
         "https://www.modelscope.cn/", True, "asr_program"),
        ("asr_model", "本地转写模型", "_check_asr_model_detail",
         "离线转写模型，越大越准也越慢（Tiny 72MB ~ Large 2944MB）",
         "https://www.modelscope.cn/", True, "asr_model"),
        ("clip_model", "图识别模型", "_check_clip_model_detail",
         "以图搜图的 CLIP 视觉编码器（约 85MB，仅图识别使用）",
         "https://hf-mirror.com/", True, "clip_model"),
        ("git", "Git", "_check_git_detail",
         "可选：本地资料站拉取代码 / 版本回退（不装不影响其他功能）",
         "https://git-scm.com/download/win", True, "external"),
        ("node", "Node.js", "_check_node_detail",
         "可选：本地资料站前端构建（需 node + npm 同时在 PATH，内置 node 无 npm）",
         "https://nodejs.org/zh-cn", True, "external"),
        ("proxy_core", "代理内核", "_check_proxy_core_detail",
         "可选：网络代理页启动内置代理时使用（mihomo 单文件，约 21MB，自动选最快加速源，支持断点续传与本地导入）",
         "https://github.com/MetaCubeX/mihomo/releases", True, "proxy_core"),
        ("cloudflared", "cloudflared（Cloudflare 隧道）", "_check_cloudflared_detail",
         "可选：WebUI 公网快速隧道（临时 HTTPS 地址，无需账号）",
         "https://github.com/cloudflare/cloudflared/releases", True, "external"),
        ("tailscale", "Tailscale", "_check_tailscale_detail",
         "可选：WebUI 公网私有网络（需登录，启动能力后续接入）",
         "https://tailscale.com/download", True, "external"),
        ("frpc", "frp 客户端", "_check_frpc_detail",
         "可选：WebUI 自建服务器隧道（需 frpc 配置，启动能力后续接入）",
         "https://github.com/fatedier/frp/releases", True, "external"),
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
            for key, name, fn_name, usage, url, optional, _action in self._DEPENDENCIES:
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
                tip = QLabel(f"依赖检测失败: {err or '未知错误'}")
                tip.setStyleSheet("color: palette(bright-text); font-size: 11px; padding: 2px;")
                self.dependency_layout.addWidget(tip)
                self.scroll.sync_content()
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
        bar.setStyleSheet("background: palette(midlight); border-radius: 4px;")
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
                "QFrame#depSkelRow { background: palette(alternate-base);"
                " border-radius: 4px; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            for width in (70, 110, 180):
                h.addWidget(self._skel_bar(width))
            h.addWidget(self._skel_bar(), 1)
            self.dependency_layout.addWidget(row)
        tip = QLabel("正在检测依赖…")
        tip.setStyleSheet("color: palette(placeholder-text); font-size: 11px; padding: 2px;")
        self.dependency_layout.addWidget(tip)
        self._skel_phase = 0
        self._skel_timer.start()
        self.scroll.sync_content()

    def _pulse_dep_skeleton(self):
        self._skel_phase = 1 - self._skel_phase
        color = "palette(shadow)" if self._skel_phase else "palette(midlight)"
        for bar in self._skel_bars:
            bar.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def _stop_dep_skeleton(self):
        self._skel_timer.stop()
        self._skel_bars = []

    def _fill_dep_rows(self, results):
        """按检测结果重建依赖清单：每项一行 + 一键下载/安装按钮（缺失项标红）。"""
        self._clear_dep_rows()
        missing = 0
        optional_missing = 0
        for (key, name, fn_name, usage, url, optional, action), (_rk, _rn, ok, version) in zip(
                self._DEPENDENCIES, results):
            # 三种状态：已安装 / 缺失（必装，标红）/ 可选（未装，中性色）
            # FULL 版内置项显示「已内置」，与用户自行安装区分
            bundled = (key in self._FULL_BUNDLED and ok
                       and edition.is_full())
            if ok:
                mark_text = "已内置" if bundled else "已安装"
                mark_color = "palette(link-visited)"
                name_color, row_bg = "palette(text)", "palette(midlight)"
            elif optional:
                mark_text, mark_color = "可选", "palette(placeholder-text)"
                name_color, row_bg = "palette(text)", "palette(alternate-base)"
            else:
                mark_text, mark_color = "缺失", "palette(bright-text)"
                name_color, row_bg = "palette(bright-text)", "palette(light)"
            row = QFrame()
            row.setObjectName("depRow")
            row.setStyleSheet(
                "QFrame#depRow { background: %s; border-radius: 4px; }" % row_bg)
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            # 状态指示
            mark = QLabel(mark_text)
            mark.setStyleSheet(
                "color: %s; font-weight: bold; font-size: 12px;"
                " background: transparent; border: none;" % mark_color)
            mark.setFixedWidth(70)
            h.addWidget(mark)
            name_lbl = QLabel(f"{name}")
            name_lbl.setStyleSheet(
                "color: %s; font-weight: bold; font-size: 12px;"
                " background: transparent; border: none;" % name_color)
            name_lbl.setFixedWidth(110)
            h.addWidget(name_lbl)
            ver_lbl = QLabel(version or "—")
            ver_lbl.setStyleSheet(
                "color: palette(placeholder-text); font-size: 11px; background: transparent; border: none;")
            ver_lbl.setFixedWidth(180)
            if key in ("ffmpeg", "ffprobe"):
                exe = find_ffmpeg_exe() if key == "ffmpeg" else find_ffprobe_exe()
                if exe not in ("ffmpeg", "ffprobe"):
                    ver_lbl.setToolTip(f"位置: {exe}")
            elif key == "douyin_sign":
                try:
                    import multi_downloader as _md
                    ver_lbl.setToolTip(f"node 位置: {_md._find_node()}")
                except Exception:
                    pass
            h.addWidget(ver_lbl)
            usage_lbl = QLabel(usage)
            usage_lbl.setStyleSheet(
                "color: palette(placeholder-text); font-size: 11px; background: transparent; border: none;")
            usage_lbl.setWordWrap(True)
            h.addWidget(usage_lbl, 1)
            # 安装动作统一在本卡片完成（约定 11），功能页只保留说明文案
            # 打包态（frozen）没有可用的 pip：装到的是用户自己的 Python 而不是程序本身，
            # 所以 pip 类依赖只提示重装，不给会误导的「安装」按钮
            if action == "pip" and not ok:
                if getattr(sys, "frozen", False):
                    hint = QLabel("未内置，请重装软件")
                    hint.setStyleSheet(
                        "color: palette(placeholder-text); font-size: 11px;"
                        " background: transparent; border: none;")
                    h.addWidget(hint)
                else:
                    install_btn = self._dep_btn("安装")
                    install_btn.clicked.connect(
                        lambda _c=False, k=key, n=name, u=url:
                        self._pip_install(k, n, u))
                    h.addWidget(install_btn)
            elif action == "ffmpeg" and not ok:
                # 两者同一个官方包：一键下载装好 ffmpeg，ffprobe 随之可用
                h.addWidget(self._dep_btn("一键下载", self._install_ffmpeg))
                h.addWidget(self._dep_btn("教程", self._show_ffmpeg_tutorial))
            elif action == "external" and not ok:
                dl_btn = self._dep_btn(
                    "下载", lambda n=name, u=url: self._open_browser_for_dep(n, u))
                dl_btn.setToolTip("打开官方下载页；安装后需把安装目录加入系统 PATH")
                h.addWidget(dl_btn)
            elif action == "pw_shell" and not ok:
                h.addWidget(self._dep_btn("下载", self._download_pw_shell))
            elif action == "asr_program" and not ok:
                h.addWidget(self._dep_btn("下载", self._download_asr_program))
            elif action == "asr_model":
                from toolbox.asr_engine import LOCAL_MODELS, local_model_present
                combo = QComboBox()
                combo.setFixedWidth(210)
                present_models = []
                for mkey, label, size_mb in LOCAL_MODELS:
                    combo.addItem(f"{label} · {size_mb}MB", mkey)
                for mkey, _label, _size in LOCAL_MODELS:
                    if local_model_present(mkey):
                        present_models.append(mkey)
                if present_models:
                    for index in range(combo.count()):
                        if combo.itemData(index) in present_models:
                            combo.setCurrentIndex(index)
                            break
                h.addWidget(combo)
                h.addWidget(self._dep_btn(
                    "下载", lambda c=combo: self._download_asr_model(c.currentData())))
                if present_models and not bundled:
                    uninstall = self._dep_btn("卸载")
                    uninstall.setToolTip("卸载下拉选中的转写模型")
                    uninstall.clicked.connect(
                        lambda _c=False, c=combo: self._confirm_uninstall_asr_model(c))
                    h.addWidget(uninstall)
            elif action == "clip_model" and not ok:
                h.addWidget(self._dep_btn("下载", self._download_clip_model))
            elif action == "proxy_core" and not ok:
                dl_btn = self._dep_btn("下载", self._download_proxy_core)
                dl_btn.setToolTip("自动测速选最快的加速源，支持断点续传")
                h.addWidget(dl_btn)
                local_btn = self._dep_btn("本地导入", self._import_proxy_core)
                local_btn.setToolTip("选择自己下载好的 mihomo-windows-amd64-*.zip")
                h.addWidget(local_btn)
            # 应用自行下载的本地组件：仅受控目录确实存在文件时提供卸载
            if ok and not bundled:
                if key == "pw_shell":
                    from toolbox.browser import (headless_shell_installed,
                                                 headless_shell_size,
                                                 uninstall_headless_shell)
                    if headless_shell_installed():
                        self._add_uninstall_btn(
                            h, "采集引擎",
                            "只删除程序下载的采集内核（当前约 %s），系统 Edge/Chrome "
                            "不受影响；删除后无系统浏览器时相关采集会不可用，可重新下载。"
                            % format_size(headless_shell_size()),
                            uninstall_headless_shell,
                            "只删除本地下载的采集内核")
                elif key == "asr_program":
                    from toolbox.asr_engine import (local_program_ready,
                                                    uninstall_local_program)
                    if local_program_ready():
                        self._add_uninstall_btn(
                            h, "本地转写程序",
                            "删除后本地离线字幕转写不可用（在线转写不受影响），可重新下载。",
                            uninstall_local_program)
                elif key == "clip_model":
                    from toolbox import image_embedding
                    files = image_embedding.find_model_files(
                        image_embedding.default_model_dir())
                    if files:
                        size = 0
                        for path in files:
                            try:
                                size += path.stat().st_size
                            except OSError:
                                continue
                        self._add_uninstall_btn(
                            h, "图识别模型",
                            "只删除默认模型目录中的模型（当前约 %s），自定义路径与内置模型"
                            "不受影响；删除后可重新下载。" % format_size(size),
                            image_embedding.uninstall_default_model)
                elif key == "proxy_core":
                    from toolbox.proxy import core as proxy_core_module
                    from toolbox.proxy import install as proxy_install
                    if proxy_install.installed() is not None:
                        running = proxy_core_module.is_running()
                        self._add_uninstall_btn(
                            h, "代理内核",
                            "删除后内置代理不可用，外部内核与提供商配置不受影响；可重新下载。"
                            + ("当前内核正在运行，请先在网络代理页停止。" if running else ""),
                            proxy_install.uninstall_core,
                            "运行中需先停止代理" if running else "")
            if not ok:
                if optional:
                    optional_missing += 1
                else:
                    missing += 1
            self.dependency_layout.addWidget(row)
        # 总览标签 —— 通过顶部右侧的依赖卡片对外展示
        if missing or optional_missing:
            parts = []
            if missing:
                parts.append(
                    f"{missing} 项缺失（已标红 · 点「安装」自动修复，FFmpeg 可一键下载或看教程）")
            if optional_missing:
                parts.append(f"{optional_missing} 项可选未安装（不影响主流程）")
            summary = QLabel("检测完成：" + "；".join(parts))
            summary.setStyleSheet(
                "color: %s; font-size: 11px; padding: 2px;" % (
                    "palette(bright-text)" if missing else "palette(placeholder-text)"))
        else:
            summary = QLabel("所有依赖已就绪")
            summary.setStyleSheet(
                "color: palette(link-visited); font-size: 11px; padding: 2px;")
        self.dependency_layout.addWidget(summary)
        self.scroll.sync_content()

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

    def _check_httpx_detail(self):
        try:
            import httpx
            return True, f"httpx {getattr(httpx, '__version__', '?')}"
        except Exception:
            return False, ""

    def _check_openpyxl_detail(self):
        try:
            import openpyxl
            return True, f"openpyxl {getattr(openpyxl, '__version__', '?')}"
        except Exception:
            return False, ""

    def _check_mutagen_detail(self):
        try:
            import mutagen
            return True, f"mutagen {getattr(mutagen, 'version_string', '?')}"
        except Exception:
            return False, ""

    def _check_websocket_detail(self):
        """直播弹幕走 websocket-client（import 名 websocket，包名不同）。"""
        try:
            import websocket  # noqa: F401
            from importlib.metadata import version as pkg_version
            try:
                ver = pkg_version("websocket-client")
            except Exception:
                ver = "?"
            return True, f"websocket-client {ver}"
        except Exception:
            return False, ""

    def _check_playwright_detail(self):
        try:
            import playwright  # noqa: F401
            from importlib.metadata import version as pkg_version
            try:
                ver = pkg_version("playwright")
            except Exception:
                ver = "?"
            return True, f"playwright {ver}"
        except Exception:
            return False, ""

    def _check_pw_shell_detail(self):
        """采集引擎：系统 Edge / Chrome 任一存在即可，否则需要本地内核。"""
        from toolbox.browser import detect, headless_shell_installed
        try:
            env = detect()
        except Exception:
            env = {"edge": None, "chrome": None}
        shell = headless_shell_installed()
        if env.get("edge"):
            return True, f"系统 Edge {env['edge']}" + (" · 本地内核已下载" if shell else "")
        if env.get("chrome"):
            return True, f"系统 Chrome {env['chrome']}" + (" · 本地内核已下载" if shell else "")
        if shell:
            return True, "本地内核已就绪"
        return False, ""

    def _check_asr_program_detail(self):
        from toolbox.asr_engine import local_program_path, local_program_ready
        from toolbox.utils import format_size
        if not local_program_ready():
            return False, ""
        try:
            return True, f"已下载 {format_size(local_program_path().stat().st_size)}"
        except Exception:
            return True, "已下载"

    def _check_asr_model_detail(self):
        from toolbox.asr_engine import LOCAL_MODELS, local_model_ready
        ready = [label for key, label, _size in LOCAL_MODELS if local_model_ready(key)]
        if not ready:
            return False, ""
        name = ready[0].split("（")[0]
        return True, (f"{name} 等 {len(ready)} 个" if len(ready) > 1 else name)

    def _check_clip_model_detail(self):
        from toolbox import image_embedding
        if getattr(image_embedding, "ort", None) is None:
            return False, "缺 onnxruntime"
        status = image_embedding.model_status()
        if not status.get("ready"):
            return False, ""
        size = status.get("size") or 0
        return True, (f"已就绪 {size / 1024 ** 2:.0f}MB" if size else "已就绪")

    def _check_git_detail(self):
        """可选依赖：本地资料站拉代码用 Git（要求已在 PATH）。"""
        import shutil
        import subprocess
        exe = shutil.which("git")
        if not exe:
            return False, ""
        try:
            r = subprocess.run([exe, "--version"], capture_output=True,
                               timeout=3, text=True)
            ver = (r.stdout or "").strip().replace("git version ", "") or "?"
        except Exception:
            ver = "?"
        return True, f"git {ver}"

    def _check_cloudflared_detail(self):
        from toolbox.webui_tunnel import get_tunnel
        path = get_tunnel().detect().get("cloudflare") or ""
        return bool(path), path

    def _check_tailscale_detail(self):
        from toolbox.webui_tunnel import get_tunnel
        path = get_tunnel().detect().get("tailscale") or ""
        return bool(path), path

    def _check_frpc_detail(self):
        from toolbox.webui_tunnel import get_tunnel
        path = get_tunnel().detect().get("frp") or ""
        return bool(path), path

    def _check_proxy_core_detail(self):
        """可选依赖：网络代理页的内置 mihomo 内核。

        外部客户端已装（Clash Verge 等）或用户手动指定过内核时也算就绪，
        不再重复下载；只有确实找不到才提示安装。
        """
        from toolbox.proxy import core as proxy_core
        from toolbox.proxy import install as proxy_install
        path = proxy_install.installed()
        if path is not None:
            try:
                return True, f"已下载 {path.stat().st_size / 1024 ** 2:.0f}MB"
            except OSError:
                return True, "已下载"
        try:
            found = proxy_core.resolve_core()
        except Exception:
            found = None
        if found is not None:
            try:
                return True, f"已找到 {found.name}"
            except OSError:
                return True, "已找到"
        try:
            part = proxy_install.part_path()
            if part.is_file() and part.stat().st_size:
                return False, f"可续传 {part.stat().st_size / 1024 ** 2:.0f}MB"
        except OSError:
            pass
        return False, ""

    def _check_node_detail(self):
        """可选依赖：本地资料站前端构建需要 node + npm 同时在 PATH。

        打包自带的 Playwright 驱动里只有 node.exe、没有 npm，所以不能用
        `multi_downloader._find_node()` 的兜底结果来判断这一项。
        """
        import shutil
        import subprocess
        node = shutil.which("node")
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not node:
            return False, ""
        if not npm:
            return False, "缺 npm"
        try:
            r = subprocess.run([node, "-v"], capture_output=True, timeout=3, text=True)
            ver = (r.stdout or "").strip() or "?"
        except Exception:
            ver = "?"
        return True, f"node {ver} / npm 就绪"

    def _check_douyin_sign_detail(self):
        """抖音签名环境：node 可用 + a_bogus 能生成签名。

        node 来源（PATH / Playwright 内置 / 安装位）决定显示文案；
        签名跑不通时抖音评论区会恒 0 条——这是它的核心诊断项。
        """
        import shutil
        try:
            import multi_downloader as md
            sig = md._douyin_abogus(
                "device_platform=webapp&aid=6383&count=20", md.UA_PC)
            if not sig:
                return False, "签名失败"
            node = md._find_node()
            which = shutil.which("node")
            src = "PATH" if which and os.path.normcase(os.path.abspath(node)) \
                == os.path.normcase(os.path.abspath(which)) else "Playwright 内置"
            return True, f"node: {src}"
        except Exception as e:
            return False, f"node 未找到: {str(e)[:20]}"

    # ----- 缺失项快捷安装 -----

    def _dep_btn(self, text, slot=None):
        """依赖行里的小按钮（高亮主色，等比缩进）。"""
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background: palette(bright-text); color: white;"
            " border-radius: 3px; padding: 3px 10px; font-size: 11px; }"
            "QPushButton:hover { background: palette(bright-text); }")
        if slot is not None:
            btn.clicked.connect(lambda _c=False: slot())
        return btn

    def _add_uninstall_btn(self, layout, name, detail, action, tooltip=""):
        btn = self._dep_btn("卸载")
        btn.setToolTip(tooltip or ("卸载 %s" % name))
        btn.clicked.connect(
            lambda _checked=False: self._confirm_and_uninstall(name, detail, action))
        layout.addWidget(btn)
        return btn

    def _confirm_and_uninstall(self, name, detail, action):
        if self._dep_downloading:
            self.app.toast("已有组件下载或卸载在进行中", "warning")
            return
        box = QMessageBox(self)
        box.setWindowTitle("卸载组件")
        box.setIcon(QMessageBox.Warning)
        box.setText("卸载%s？" % name)
        box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        self._dep_downloading = True
        self.dep_status.setText("正在卸载 %s…" % name)
        self.dep_status.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px;")

        def done(result, err):
            self._dep_downloading = False
            if err or not result:
                message = str(err or "未知错误")[:300]
                self.dep_status.setText("%s 卸载失败：%s" % (name, message))
                self.dep_status.setStyleSheet(
                    "color: palette(bright-text); font-size: 11px;")
                self.app.toast("%s 卸载失败" % name, "error")
            else:
                ok, freed, message = result
                if ok:
                    suffix = ("（释放 %s）" % format_size(freed)) if freed else ""
                    self.dep_status.setText("%s：%s%s" % (name, message, suffix))
                    self.dep_status.setStyleSheet(
                        "color: palette(link-visited); font-size: 11px;")
                    self.app.toast("%s 已卸载" % name, "success")
                else:
                    self.dep_status.setText("%s 卸载失败：%s" % (name, message))
                    self.dep_status.setStyleSheet(
                        "color: palette(bright-text); font-size: 11px;")
                    self.app.toast("%s 卸载失败" % name, "error")
            self._refresh_dependencies()

        run_bg(action, done)

    def _confirm_uninstall_asr_model(self, combo):
        from toolbox.asr_engine import local_model_present, uninstall_local_model

        key = combo.currentData()
        label = combo.currentText()
        if not local_model_present(key):
            self.app.toast("该转写模型未下载", "warning")
            return
        self._confirm_and_uninstall(
            "转写模型 %s" % label.split(" · ")[0],
            "将删除该模型目录；其他模型与已导出的字幕不受影响，可重新下载。",
            lambda: uninstall_local_model(key))

    def _start_dep_download(self, name, work):
        """组件下载统一入口：work(report) 在后台跑，report 回主线程写进度。

        所有安装动作只在全局设置里发起（约定 11）；完成后重跑依赖检测刷新状态。
        """
        if self._dep_downloading:
            return
        self._dep_downloading = True
        self.dep_status.setText(f"正在下载 {name}…")
        self.dep_status.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px;")

        def report(text):
            post_to_main(lambda: self.dep_status.setText(f"{name}：{text}"))

        def job():
            work(report)
            return True

        def done(result, err):
            self._dep_downloading = False
            if err or not result:
                self.dep_status.setText(
                    f"{name} 下载失败：{str(err or '未知错误')[:300]}")
                self.dep_status.setStyleSheet(
                    "color: palette(bright-text); font-size: 11px;")
                self.app.toast(f"{name} 下载失败", "error")
                return
            self.dep_status.setText(f"{name} 已就绪")
            self.dep_status.setStyleSheet(
                "color: palette(link-visited); font-size: 11px;")
            self.app.toast(f"{name} 已就绪", "success")
            self._refresh_dependencies()

        run_bg(job, done)

    def _download_pw_shell(self):
        """采集引擎（Playwright headless shell，约 91MB，下载到用户目录）。"""
        from toolbox.browser import download_headless_shell

        def work(report):
            ok = download_headless_shell(log=lambda line: report(line[:60]))
            if not ok:
                raise RuntimeError("下载失败，可重试或安装 Edge/Chrome")

        self._start_dep_download("采集引擎", work)

    def _download_asr_program(self):
        """本地转写程序（whisper-faster.exe，约 84MB）。"""
        from toolbox.asr_engine import download_program

        def work(report):
            download_program(progress=lambda p, m: report(f"{m}（{p}%）"))

        self._start_dep_download("本地转写程序", work)

    def _download_asr_model(self, model_key):
        """本地转写模型（ModelScope 拉取，按所选模型大小从 72MB 到 2.9GB）。"""
        if not model_key:
            return
        from toolbox.asr_engine import LOCAL_MODELS, download_model

        label = next((lb for k, lb, _s in LOCAL_MODELS if k == model_key), model_key)

        def work(report):
            download_model(model_key, progress=lambda p, m: report(f"{m}（{p}%）"))

        self._start_dep_download(f"转写模型 {label.split('（')[0]}", work)

    def _download_clip_model(self):
        """图识别模型（CLIP 视觉编码器 ONNX，约 85MB，支持断点续传）。"""
        from toolbox import image_embedding

        def work(report):
            def on_progress(done_bytes, total_bytes):
                total = total_bytes or 1
                report(f"{done_bytes / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MB")

            image_embedding.download_default_model(progress=on_progress)

        self._start_dep_download("图识别模型", work)

    def _download_proxy_core(self):
        """代理内核（mihomo 单文件，约 21MB）：自动挑最快加速源，断点续传。"""
        from toolbox.proxy import install as proxy_install

        def work(report):
            state = {"at": time.monotonic(), "done": 0, "speed": 0.0}

            def on_progress(done_bytes, total_bytes):
                now = time.monotonic()
                span = now - state["at"]
                if span >= 0.5:
                    instant = (done_bytes - state["done"]) / span / 1024 ** 2
                    if instant > 0:
                        state["speed"] = instant
                    state["at"], state["done"] = now, done_bytes
                text = f"{done_bytes / 1024 ** 2:.1f}"
                if total_bytes:
                    text += f" / {total_bytes / 1024 ** 2:.1f} MB"
                    text += f"（{done_bytes * 100 // total_bytes}%）"
                else:
                    text += " MB"
                if state["speed"]:
                    text += f" · {state['speed']:.1f} MB/s"
                report(text)

            proxy_install.download_core(progress=on_progress, status=report)

        self._start_dep_download("代理内核", work)

    def _import_proxy_core(self):
        """本地导入：用浏览器/下载器下好 mihomo zip，绕开 GitHub 慢速。"""
        from toolbox.proxy import install as proxy_install

        path, _ = QFileDialog.getOpenFileName(
            self, "选择 mihomo 内核压缩包", "", "压缩包 (*.zip)")
        if not path:
            return

        def work(report):
            report("正在解压…")
            proxy_install.install_from_zip(
                Path(path), progress=lambda done, total: None)

        self._start_dep_download("代理内核", work)

    def _ffmpeg_install_dir(self):
        """一键下载的落地目录：用户目录（Program Files 下没有写权限）。"""
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "FengMingToolbox" / "ffmpeg" / "bin"

    def _install_ffmpeg(self):
        """一键下载官方 FFmpeg 构建，解压 ffmpeg/ffprobe 后自动写回「FFmpeg 位置」。

        下载源是 ffmpeg.org 下载页指向的 Windows 官方构建站（gyan.dev）的
        release-essentials 包，约 110MB；两个 exe 装进用户目录，避免提权。
        """
        if self._ffmpeg_installing:
            return
        target = self._ffmpeg_install_dir()
        if (target / "ffmpeg.exe").is_file() and (target / "ffprobe.exe").is_file():
            self._apply_ffmpeg_dir(target)
            return
        self._ffmpeg_installing = True
        self.ffmpeg_status.setText("正在下载 FFmpeg（约 110MB，请保持网络畅通）…")
        self.ffmpeg_status.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px;")
        self.app.toast("开始下载 FFmpeg，完成后会自动配置", "info")

        def work():
            import tempfile

            import httpx

            target.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="fmjj_ffmpeg_") as tmp:
                zip_path = Path(tmp) / "ffmpeg.zip"
                timeout = httpx.Timeout(60.0, read=600.0)
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    with client.stream("GET", FFMPEG_ZIP_URL) as resp:
                        resp.raise_for_status()
                        with open(zip_path, "wb") as fh:
                            for chunk in resp.iter_bytes(chunk_size=262144):
                                fh.write(chunk)
                _extract_ffmpeg_binaries(zip_path, target)
            return target

        def done(result, err):
            self._ffmpeg_installing = False
            if err or result is None:
                msg = str(err or "未知错误")[:80]
                self.ffmpeg_status.setText(
                    f"一键下载失败：{msg}（可点「教程」手动安装）")
                self.ffmpeg_status.setStyleSheet(
                    "color: palette(bright-text); font-size: 11px;")
                self.app.toast("FFmpeg 下载失败，请看「教程」手动安装", "error")
                return
            self._apply_ffmpeg_dir(result)

        run_bg(work, done)

    def _apply_ffmpeg_dir(self, target):
        """把一键下载的目录写进设置并刷新界面。"""
        set_ffmpeg_path(str(target))
        invalidate_ffmpeg_cache()
        self.ffmpeg_entry.setText(str(target))
        self._update_ffmpeg_status()
        self._refresh_dependencies()
        self.app.toast("FFmpeg 已就绪", "success")

    def _show_ffmpeg_tutorial(self):
        """内置安装教程：一键安装说明 + 手动安装步骤 + 官方下载页入口。"""
        box = QMessageBox(self)
        box.setWindowTitle("FFmpeg 安装教程")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(
            "<b>方法一：一键自动安装（推荐）</b><br>"
            "点依赖清单里的「一键下载」，程序会从 ffmpeg.org 指向的官方 Windows "
            "构建站下载 release-essentials 包，把 ffmpeg.exe / ffprobe.exe 解压到"
            "<br><code>%LOCALAPPDATA%\\FengMingToolbox\\ffmpeg\\bin</code><br>"
            "并自动写入「FFmpeg 位置」，约 110MB，无需手动配置。<br><br>"
            "<b>方法二：手动安装</b><br>"
            "1. 点下方「打开官方下载页」，下载 <code>ffmpeg-release-essentials.zip</code><br>"
            "2. 解压后进入 <code>bin</code> 目录，应能看到 ffmpeg.exe 与 ffprobe.exe<br>"
            "3. 把 bin 目录放到固定位置，例如 <code>D:\\Tools\\ffmpeg\\bin</code><br>"
            "4. 回到本页上方输入框粘贴该路径，点「保存」即生效<br><br>"
            "<b>说明</b><br>"
            "· ffprobe 与 ffmpeg 在同一个包里，装好 ffmpeg 后 ffprobe 自动可用<br>"
            "· 也可把 bin 目录加入系统环境变量 PATH，程序优先使用 PATH 里的版本<br>"
            "· 机器上已有 FFmpeg 的话不用再装，直接在上方指定位置即可")
        open_btn = box.addButton("打开官方下载页", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_browser_for_dep("FFmpeg", FFMPEG_DOWNLOAD_PAGE)

    def _pip_install(self, key, name, cmd):
        """pip install 走 subprocess，后台线程不阻塞 UI；结果 toast 提示。"""
        self.app.toast(f"开始安装 {name} ...", "info")

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
                self.app.toast(f"{name} 安装完成", "success")
                self._refresh_dependencies()

        run_bg(work, done)

    def _open_browser_for_dep(self, name, url):
        """打开依赖的官方下载页（FFmpeg 教程里的「手动安装」入口）。"""
        try:
            import webbrowser
            webbrowser.open(url)
            self.app.toast(f"已打开 {name} 官方下载页", "info")
        except Exception as e:
            self.app.toast(f"无法打开浏览器: {e}", "error")
