"""重命名页（PySide6 版）：交互式重命名 — 三栏：文件夹 | 账户+类型 | 视频预览。

对应原版 gui/toolbox_pages.py RenamePage，功能一一对应：
- Tab「命名投稿」：
  * 栏1 两级列表：日期文件夹 + 文件夹内视频文件（点击跳转、当前项高亮）
  * 栏2 直播账户（单选）+ 直播类型（多选，按点击顺序编号 [N]，数字键 1-9 快捷切换）
  * 栏3 视频信息（ffprobe）+ 版本（无/有弹幕）+ 平台（配置单选）+ 标题三模式
    （自动提取/手动输入/不保留）+ 预览截图 + 新文件名预览 + 确认/跳过 + 版权开关
  * Enter=确认（已标准命名则跳过）；Esc=跳过；标准命名文件提示「重新命名」
  * 重命名后用 ffmpeg 写入版权元数据
- Tab「参数设置」：账户/类型/平台卡片（上移/下移/删除/添加）、视频后缀、
  匹配阈值、附加信息、写入版权开关、保存设置
"""

import errno
import os
import re
from pathlib import Path
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QSplitter, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from toolbox.config_manager import get_rename_config, save_rename_config
from toolbox.date_folders import (
    date_folder_range, date_folder_value, format_date_range, scan_date_folders,
)
from toolbox.rename_engine import (
    extract_file_title, extract_time_str, is_standard_recording_name,
    rebase_companions, rebase_timeline_paths,
)
from toolbox.utils import (
    capture_video_frame, clean_special_chars, extract_date_range_from_filename,
    extract_start_datetime, get_video_info, is_video_file, video_end_date,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from toolbox import bili_upload as BU


# Windows：ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION
_SHARING_WINERRORS = frozenset({32, 33})


def _is_sharing_violation(error):
    """文件正被占用（预览/播放器/杀软/同步盘）导致的改名失败。"""
    if getattr(error, "winerror", None) in _SHARING_WINERRORS:
        return True
    return getattr(error, "errno", None) in (errno.EACCES, errno.EBUSY, 32)


def _rename_with_retry(src, dst, attempts=6, delay=0.3):
    """改名；短暂占用时稍等重试（调用前已让本软件内的预览松手）。"""
    from toolbox.organize_engine import _locked_reader
    for attempt in range(attempts):
        try:
            with _locked_reader(src):
                os.rename(src, dst)
            return
        except OSError as error:
            if attempt + 1 >= attempts or not _is_sharing_violation(error):
                raise
            time.sleep(delay)


def _rename_dir_with_retry(src, dst, attempts=3, delay=0.2):
    """文件夹改名；被预览/同步盘短暂占用时重试，不打开目录句柄。"""
    for attempt in range(attempts):
        try:
            os.rename(src, dst)
            return
        except OSError as error:
            if attempt + 1 >= attempts or not _is_sharing_violation(error):
                raise
            time.sleep(delay)


class RenamePage(BasePage):
    post_progress = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self.folders = []
        self.current_folder = None
        self.current_videos = []
        self.current_video_idx = 0
        self.rename_config = get_rename_config()
        self.type_order = []      # 按点击顺序记录选中的类型
        self.title_mode = "none"  # none / auto / manual
        self.auto_title = ""
        self._load_token = None   # 防止过期截图/信息覆盖新文件
        self._thumb_pm = None     # 保持 QPixmap 引用避免被回收
        self._thumb_source = None
        self._thumb_size = None
        self._busy = False
        self._loaded_root = None
        self._params_loaded = False
        self._metadata_failed = set()
        self._posted_batches = set()
        self._post_cancel = threading.Event()
        self.card_count_labels = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.sub_tabs = QTabWidget()
        layout.addWidget(self.sub_tabs, 1)
        self.sub_tabs.addTab(self._build_rename_tab(), "命名投稿")
        self.sub_tabs.addTab(self._build_params_tab(), "参数设置")

        self._rebuild_account_radios()
        self._rebuild_type_checkboxes()
        self._rebuild_platform_radios()
        self._set_title_mode("none")

        # 懒加载：先渲染框架，再加载内容（避免切换页面时卡顿）
        self.post_progress.connect(self._log)

    def _lazy_load(self):
        self._load_params_to_ui()
        self._params_loaded = True

    def preload_tasks(self):
        return (self._lazy_load,)

    def has_active_tasks(self):
        return self._busy

    # ==================== 命名投稿 Tab（三栏） ====================

    def _build_rename_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(5)

        # 顶栏：刷新 + 文件夹计数 + 自动投稿 + 投稿入口（压成一行，给主体留高度）
        top = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(72)
        refresh_btn.clicked.connect(self._refresh)
        top.addWidget(refresh_btn)
        self.folder_count_label = QLabel("")
        top.addWidget(self.folder_count_label)
        self.auto_upload = QCheckBox("本文件夹全部重命名完成后自动投稿")
        self.auto_upload.setChecked(bool(BU.load_settings().get("auto_upload", False)))
        self.auto_upload.toggled.connect(self._save_post_switch)
        self.auto_upload.setToolTip("本文件夹全部视频重命名完成后，自动打开投稿流程")
        top.addWidget(self.auto_upload)
        top.addStretch(1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: palette(link);")
        top.addWidget(self.progress_label)
        self.post_btn = QPushButton("投稿已重命名视频")
        self.post_btn.setToolTip("打开投稿窗口；没有视频时也可配置并保存投稿模板")
        self.post_btn.clicked.connect(self._open_upload)
        top.addWidget(self.post_btn)
        v.addLayout(top)

        # 三栏主体
        split = QSplitter(Qt.Horizontal)
        v.addWidget(split, 1)

        # === 栏1: 文件夹 + 文件两级列表 ===
        col1 = QWidget()
        c1 = QVBoxLayout(col1)
        c1.setContentsMargins(0, 0, 0, 0)
        c1.setSpacing(3)
        c1.addWidget(self._section_label("日期文件夹"))
        self.folder_list = QListWidget()
        self.folder_list.setAlternatingRowColors(True)
        self.folder_list.itemClicked.connect(lambda _it: self._on_folder_click())
        c1.addWidget(self.folder_list, 3)
        c1.addWidget(self._section_label("文件列表"))
        self.file_list = QListWidget()
        self.file_list.setAlternatingRowColors(True)
        self.file_list.itemClicked.connect(self._on_file_click)
        c1.addWidget(self.file_list, 4)
        split.addWidget(col1)

        # === 栏2: 账户 + 类型 ===
        col2 = QWidget()
        c2 = QVBoxLayout(col2)
        c2.setContentsMargins(0, 0, 0, 0)
        c2.setSpacing(4)
        c2.addWidget(self._section_label("直播账户"))
        self.account_list = QListWidget()
        self.account_list.setAlternatingRowColors(True)
        self.account_list.itemSelectionChanged.connect(self._update_preview)
        c2.addWidget(self.account_list, 2)
        type_head = QHBoxLayout()
        type_head.setSpacing(6)
        type_head.addWidget(self._section_label("直播类型（多选）"))
        type_head.addStretch(1)
        type_hint = QLabel("按勾选顺序参与命名")
        type_hint.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        type_head.addWidget(type_hint)
        c2.addLayout(type_head)
        self.type_box = QFrame()
        self.type_box.setObjectName("renameTypeBox")
        self.type_box.setStyleSheet(
            "QFrame#renameTypeBox { background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 6px; }")
        self.type_grid = QGridLayout(self.type_box)
        self.type_grid.setContentsMargins(10, 8, 10, 8)
        self.type_grid.setHorizontalSpacing(16)
        self.type_grid.setVerticalSpacing(6)
        self.type_grid.setAlignment(Qt.AlignTop)
        c2.addWidget(self.type_box, 1)
        self.type_checks = {}
        clear_btn = QPushButton("清除所有选择")
        clear_btn.clicked.connect(self._clear_all_types)
        c2.addWidget(clear_btn)
        split.addWidget(col2)

        # === 栏3: 视频信息 + 预览（操作按钮固定在下方，不随内容被裁） ===
        col3 = QWidget()
        c3 = QVBoxLayout(col3)
        c3.setContentsMargins(0, 0, 0, 0)
        c3.setSpacing(6)

        info_card = QFrame()
        info_card.setObjectName("renameInfoCard")
        info_card.setStyleSheet(
            "QFrame#renameInfoCard { background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 6px; }")
        ic = QVBoxLayout(info_card)
        ic.setContentsMargins(10, 8, 10, 10)
        ic.setSpacing(6)
        ic.addWidget(self._section_label("视频信息"))
        self.video_info_label = QLabel("请选择左侧文件夹")
        self.video_info_label.setWordWrap(True)
        self.video_info_label.setStyleSheet("font-size: 12px;")
        self.video_info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ic.addWidget(self.video_info_label)

        # 版本
        ver_row = QHBoxLayout()
        ver_row.setSpacing(10)
        ver_row.addWidget(QLabel("版本"))
        self.version_group = QButtonGroup(self)
        self.ver_none_radio = QRadioButton("无弹幕")
        self.ver_none_radio.setChecked(True)
        self.ver_none_radio.clicked.connect(self._update_preview)
        self.ver_danmu_radio = QRadioButton("有弹幕")
        self.ver_danmu_radio.clicked.connect(self._update_preview)
        self.version_group.addButton(self.ver_none_radio)
        self.version_group.addButton(self.ver_danmu_radio)
        ver_row.addWidget(self.ver_none_radio)
        ver_row.addWidget(self.ver_danmu_radio)
        ver_row.addStretch(1)
        ic.addLayout(ver_row)

        # 平台
        plat_row = QHBoxLayout()
        plat_row.setSpacing(10)
        plat_row.addWidget(QLabel("平台"))
        self.platform_group = QButtonGroup(self)
        self.platform_box = QWidget()
        self.platform_layout = QHBoxLayout(self.platform_box)
        self.platform_layout.setContentsMargins(0, 0, 0, 0)
        self.platform_layout.setSpacing(12)
        plat_row.addWidget(self.platform_box, 1)
        ic.addLayout(plat_row)

        # 标题三模式：标签与按钮同一行，输入框单占一行
        trow = QHBoxLayout()
        trow.setSpacing(6)
        trow.addWidget(QLabel("标题"))
        self.title_auto_btn = QPushButton("自动提取")
        self.title_auto_btn.setCheckable(True)
        self.title_auto_btn.clicked.connect(lambda: self._set_title_mode("auto"))
        trow.addWidget(self.title_auto_btn)
        self.title_manual_btn = QPushButton("手动输入")
        self.title_manual_btn.setCheckable(True)
        self.title_manual_btn.clicked.connect(lambda: self._set_title_mode("manual"))
        trow.addWidget(self.title_manual_btn)
        self.title_none_btn = QPushButton("不保留")
        self.title_none_btn.setCheckable(True)
        self.title_none_btn.clicked.connect(lambda: self._set_title_mode("none"))
        trow.addWidget(self.title_none_btn)
        trow.addStretch(1)
        ic.addLayout(trow)
        self.title_entry = QLineEdit()
        self.title_entry.setPlaceholderText("手动输入标题...")
        self.title_entry.setReadOnly(True)
        self.title_entry.textChanged.connect(lambda _t: self._update_preview())
        ic.addWidget(self.title_entry)
        c3.addWidget(info_card)

        # 预览截图（占满剩余高度）
        c3.addWidget(self._section_label("预览截图"))
        self.thumb_label = QLabel("（选择视频后显示）")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setMinimumHeight(150)
        self.thumb_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.thumb_label.setStyleSheet(
            "background: palette(alternate-base); border-radius: 6px;"
            " color: palette(placeholder-text);")
        c3.addWidget(self.thumb_label, 1)

        # 新文件名预览
        c3.addWidget(self._section_label("新文件名"))
        self.new_name_label = QLabel("")
        self.new_name_label.setWordWrap(True)
        self.new_name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.new_name_label.setStyleSheet(
            "background: palette(alternate-base); color: palette(link-visited);"
            " border-radius: 6px; padding: 6px 8px; font-size: 12px;")
        c3.addWidget(self.new_name_label)

        split.addWidget(col3)
        split.setSizes([250, 280, 520])

        # 固定操作栏：版权开关 + 跳过 / 确认，永远可见
        footer = QFrame()
        footer.setObjectName("renameFooter")
        footer.setStyleSheet(
            "QFrame#renameFooter { background: palette(alternate-base);"
            " border-radius: 8px; }")
        fv = QHBoxLayout(footer)
        fv.setContentsMargins(10, 6, 10, 6)
        fv.setSpacing(10)
        self.rename_extra_check = QCheckBox("写入版权属性")
        self.rename_extra_check.setToolTip(
            "写入标准版权标签（cprt）；MP4 在资源管理器里不显示「版权」，"
            "会同时写一份到「备注」，属性 → 详细信息 → 备注 可看到")
        self.rename_extra_check.setChecked(self.rename_config.append_extra_info)
        self.rename_extra_check.toggled.connect(self._on_extra_toggle)
        fv.addWidget(self.rename_extra_check)
        self.extra_preview_label = QLabel("")
        self.extra_preview_label.setStyleSheet(
            "color: palette(placeholder-text); font-size: 10px;")
        fv.addWidget(self.extra_preview_label, 1)
        self.skip_btn = QPushButton("⏭ 跳过")
        self.skip_btn.setEnabled(False)
        self.skip_btn.clicked.connect(self._skip_video)
        fv.addWidget(self.skip_btn)
        self.confirm_btn = QPushButton("确认重命名 (Enter)")
        self.confirm_btn.setProperty("variant", "primary")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._do_rename)
        fv.addWidget(self.confirm_btn)
        v.addWidget(footer)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(64)
        v.addWidget(self.log_text)

        self._update_extra_preview()
        return w

    # ==================== 账户/类型 UI ====================

    @staticmethod
    def _section_label(text):
        """分区标题：比正文略重，不抢按钮焦点。"""
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600; color: palette(text);")
        return lbl

    def _rebuild_account_radios(self):
        self.account_list.clear()
        cfg = get_rename_config()
        for acc in cfg.account_list:
            self.account_list.addItem(acc)
        if cfg.account_list:
            self.account_list.setCurrentRow(0)

    def _rebuild_type_checkboxes(self):
        """直播类型用真实复选框（两列），勾选顺序仍决定命名顺序。"""
        self.type_order.clear()
        while self.type_grid.count():
            item = self.type_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.type_checks = {}
        for index, t in enumerate(get_rename_config().live_types):
            box = QCheckBox(t)
            box.toggled.connect(lambda checked, name=t: self._on_type_toggled(name, checked))
            self.type_grid.addWidget(box, index // 2, index % 2)
            self.type_checks[t] = box

    def _rebuild_platform_radios(self):
        cfg = get_rename_config()
        current = self._current_platform() or cfg.default_platform
        # 清除旧按钮
        while self.platform_layout.count():
            item = self.platform_layout.takeAt(0)
            wgt = item.widget()
            if wgt:
                if isinstance(wgt, QRadioButton):
                    self.platform_group.removeButton(wgt)
                wgt.deleteLater()
        if not cfg.platform_list:
            cfg.platform_list = ["抖音"]
        if current not in cfg.platform_list:
            current = cfg.platform_list[0]
        for p in cfg.platform_list:
            rb = QRadioButton(p)
            rb.setChecked(p == current)
            rb.clicked.connect(self._update_preview)
            self.platform_group.addButton(rb)
            self.platform_layout.addWidget(rb)
        self.platform_layout.addStretch(1)

    def _refresh_type_labels(self):
        """勾选框文字带上顺序标记，让「按勾选顺序命名」看得见。"""
        marks = "①②③④⑤⑥⑦⑧⑨"
        for name, box in getattr(self, "type_checks", {}).items():
            suffix = ""
            if name in self.type_order:
                index = self.type_order.index(name)
                suffix = f"  {marks[index] if index < len(marks) else index + 1}"
            box.blockSignals(True)
            box.setText(f"{name}{suffix}")
            box.blockSignals(False)

    def _on_type_toggled(self, name, checked):
        if checked and name not in self.type_order:
            self.type_order.append(name)
        elif not checked and name in self.type_order:
            self.type_order.remove(name)
        self._refresh_type_labels()
        self._update_preview()

    def _on_type_click(self, tname):
        """数字键 / 外部调用的统一切换入口。"""
        box = getattr(self, "type_checks", {}).get(tname)
        if box is not None:
            box.setChecked(not box.isChecked())
        else:
            self._on_type_toggled(tname, tname not in self.type_order)

    def _key_toggle_type(self, idx):
        types = get_rename_config().live_types
        if 0 <= idx < len(types):
            self._on_type_click(types[idx])

    def _reset_type_selection(self):
        for box in getattr(self, "type_checks", {}).values():
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        self.type_order.clear()
        self._refresh_type_labels()
        self._update_preview()

    def _clear_all_types(self):
        """一键清除所有类型选择"""
        self._reset_type_selection()

    # ==================== 按键处理 ====================

    def keyPressEvent(self, event):
        # 数字键 1-9 快捷选择类型（仅本页可见时生效）
        if self.isVisible():
            ch = event.text()
            if ch.isdigit() and "1" <= ch <= "9":
                self._key_toggle_type(int(ch) - 1)
                return
        super().keyPressEvent(event)

    def on_return_press(self):
        """主窗口回车统一入口：已标准命名 → 跳过；否则 → 确认重命名。"""
        if not self.current_videos:
            return
        if not (0 <= self.current_video_idx < len(self.current_videos)):
            return
        filename = os.path.basename(self.current_videos[self.current_video_idx])
        path = self.current_videos[self.current_video_idx]
        if self._is_standard_named(filename) and path not in self._metadata_failed:
            self._skip_video()
        else:
            self._do_rename()

    def on_escape_press(self):
        self._skip_video()

    # ==================== 文件夹 / 视频加载 ====================

    def on_dir_changed(self, path):
        self._refresh()

    def _refresh(self):
        if self._busy:
            return
        self._load_token = object()
        self._loaded_root = self.app.work_dir
        self.current_folder = None
        self.current_videos = []
        self.current_video_idx = 0
        self.confirm_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self.folders = scan_date_folders(self.app.work_dir)
        self.folder_list.clear()
        if not self.folders:
            self.folder_list.addItem("（未找到日期文件夹）")
            self.folder_count_label.setText("无日期文件夹")
        else:
            for fn, _dt in self.folders:
                self.folder_list.addItem(f"{fn}")
            self.folder_count_label.setText(f"共 {len(self.folders)} 个文件夹")
        # 清空文件列表
        self.file_list.clear()
        self.file_list.addItem("（请选择文件夹）")
        self._log(f"扫描到 {len(self.folders)} 个日期文件夹")
        # 自动选中第一个文件夹
        if self.folders and self.isVisible():
            self._auto_select_first()

    def _on_folder_click(self):
        """点击文件夹，列出其中视频文件"""
        if self._busy:
            return
        item = self.folder_list.currentItem()
        if item is None:
            return
        line = item.text().strip()
        if not line or "未找到" in line:
            return
        folder_name = line.strip()
        folder_path = os.path.join(self.app.work_dir, folder_name)
        if not os.path.isdir(folder_path):
            return

        self.current_folder = folder_path
        self.confirm_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        # 列出视频文件到下方列表
        self.current_videos = []
        for f in sorted(os.listdir(folder_path)):
            fp = os.path.join(folder_path, f)
            if os.path.isfile(fp) and is_video_file(fp) and \
                    f.lower().endswith(self.rename_config.video_suffix):
                self.current_videos.append(fp)

        self.file_list.clear()
        if not self.current_videos:
            self.file_list.addItem("（无匹配视频）")
            return
        for fp in self.current_videos:
            self.file_list.addItem(f"{os.path.basename(fp)}")

        self.current_video_idx = 0
        self._reset_type_selection()
        self._load_current()

    def _on_file_click(self, item):
        """点击文件列表中某一项，跳转到该视频"""
        if self._busy:
            return
        idx = self.file_list.row(item)
        if 0 <= idx < len(self.current_videos):
            self.current_video_idx = idx
            self._load_current()

    def _auto_select_first(self):
        """自动选中第一个文件夹并加载"""
        if not self.folders or self.folder_list.count() == 0:
            return
        self.folder_list.setCurrentRow(0)
        self._on_folder_click()

    def _load_current(self):
        """加载当前索引的视频（唯一加载入口）"""
        if not self.current_videos or self.current_video_idx >= len(self.current_videos):
            total = len(self.current_videos)
            folder_name = os.path.basename(self.current_folder) if self.current_folder else ""
            self._log(f"文件夹处理完毕: {folder_name}（共 {total} 个）")
            self.video_info_label.setText(f"全部完成！（{total}/{total}）")
            self.confirm_btn.setEnabled(False)
            self.skip_btn.setEnabled(False)
            self.progress_label.setText(f"完成 {total} 个")
            if total and self.auto_upload.isChecked():
                self._post_completed_folder()
            return
        path = self.current_videos[self.current_video_idx]
        if not os.path.exists(path):
            self._log(f"文件不存在，自动跳过: {os.path.basename(path)}")
            self.current_video_idx += 1
            self._load_current()
            return
        self._load_video(path)

    def _is_standard_named(self, filename: str) -> bool:
        """检测是否已经是标准命名格式（YYYY-MM-DD[~YYYY-MM-DD]_HH-MM-SS_...）"""
        return is_standard_recording_name(filename)

    def _load_video(self, video_path):
        """加载单个视频的详细信息 + 截图（后台线程执行，避免卡 UI）"""
        filename = os.path.basename(video_path)
        folder_name = os.path.basename(self.current_folder)
        folder_date = date_folder_value(folder_name) or folder_name[:8]
        name_start, name_end = extract_date_range_from_filename(filename)
        date_str = name_start or folder_date
        self._ctx_start_compact = date_str
        self._ctx_end_compact = name_end if name_end and name_end > date_str else None
        self._ctx_start_dt = extract_start_datetime(filename, date_str)

        time_str = extract_time_str(filename)
        self.auto_title = extract_file_title(filename)

        self._ctx_date = format_date_range(
            date_str, self._ctx_end_compact or date_str, dashed=True)
        self._ctx_time = time_str
        self._set_title_mode("none")  # 默认不保留

        total = len(self.current_videos)
        self.progress_label.setText(f"{self.current_video_idx + 1} / {total}")

        # 高亮当前文件
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            it.setData(Qt.BackgroundRole, None)
            it.setData(Qt.ForegroundRole, None)
        self.file_list.setCurrentRow(self.current_video_idx)
        self.file_list.scrollToItem(
            self.file_list.item(self.current_video_idx) if self.current_video_idx < self.file_list.count() else None)

        # 后台获取视频信息 + 截图
        token = object()
        self._load_token = token
        self._clear_thumb()
        self.video_info_label.setText(f"{filename}\n加载中...")

        def work():
            info = get_video_info(video_path)
            thumb = capture_video_frame(video_path)
            return info, thumb

        def done(result, err):
            if self._load_token is not token:
                return  # 已切换文件，丢弃过期结果
            info, thumb = result if result else ({}, None)
            duration = 0.0
            if isinstance(info, dict) and not info.get("error"):
                try:
                    duration = float(info.get("duration") or 0)
                except (TypeError, ValueError):
                    duration = 0.0
                info_text = (
                    f"{filename}\n时间: {time_str}\n"
                    f"{info.get('resolution', '?')} | {info.get('orientation', '?')}\n"
                    f"{info.get('duration_str', '?')} | {info.get('fps', '?')} fps\n"
                    f"{info.get('file_size_str', '?')} | {info.get('audio_codec', '?')}"
                )
            else:
                info_text = f"{filename}\n时间: {time_str}\n无法获取视频信息"
            self.video_info_label.setText(info_text)
            self._apply_video_date_range(video_path, duration)

            if thumb and os.path.exists(thumb):
                pm = QPixmap(thumb)
                if not pm.isNull():
                    self._thumb_source = pm
                    self._thumb_pm = pm
                    self._thumb_size = None
                    self._apply_thumb()
                else:
                    self._thumb_pm = None
                    self._thumb_source = None
                    self.thumb_label.setText("（截图失败）")
            else:
                self._thumb_pm = None
                self._thumb_source = None
                self.thumb_label.setText("（无法截图）")
            self._update_preview()

        run_bg(work, done)

        # 检测是否已标准命名
        if self._is_standard_named(filename):
            self.confirm_btn.setText("重新命名（点击）")
            self.confirm_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.skip_btn.setFocus()
            self._log(f"已是标准命名: {filename}")
        else:
            self.confirm_btn.setText("确认重命名 (Enter)")
            self.confirm_btn.setEnabled(True)
            self.skip_btn.setText("⏭ 跳过")
            self.skip_btn.setEnabled(True)

    def _apply_video_date_range(self, video_path, duration=None):
        """按开始时间和视频时长补全结束日期，并把当前日期文件夹扩展成跨天范围。"""
        start = getattr(self, "_ctx_start_compact", "")
        if not start:
            return
        end = getattr(self, "_ctx_end_compact", None) or start
        name_start, name_end = extract_date_range_from_filename(
            os.path.basename(video_path))
        if name_start:
            end = max(end, name_end or start)
        if duration is None:
            computed = video_end_date(video_path, fallback_date=start)
        elif duration > 0:
            computed = video_end_date(
                video_path, duration=duration, fallback_date=start)
        else:
            computed = ""
        if computed:
            end = max(end, computed)
        self._ctx_end_compact = end
        self._ctx_date = format_date_range(start, end, dashed=True)
        if end > start:
            self._extend_folder_range(end)

    def _extend_folder_range(self, end_compact):
        """日期文件夹从 20261124 扩展为 20261124~20261126；同名目标存在时不覆盖。"""
        if not self.current_folder or not end_compact:
            return
        folder_name = os.path.basename(self.current_folder.rstrip("\\/"))
        start, current_end = date_folder_range(folder_name)
        if not start or not current_end or end_compact <= current_end:
            return
        suffix = re.sub(r"^~20\d{6}", "", folder_name[8:])
        new_name = f"{start}~{end_compact}{suffix}"
        new_path = os.path.join(os.path.dirname(self.current_folder), new_name)
        if os.path.exists(new_path):
            self._log(f"日期文件夹已存在，未扩展：{new_name}")
            return
        for path in list(self.current_videos):
            self._release_media_holders(path)
        old_path = self.current_folder
        old_key = os.path.normcase(os.path.abspath(old_path))
        try:
            _rename_dir_with_retry(old_path, new_path)
        except OSError as error:
            self._log(f"日期文件夹改名失败：{folder_name} → {new_name}（{error}）")
            return

        def rebase(path):
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) != old_key:
                return path
            return os.path.join(new_path, os.path.basename(path))

        old_videos = list(self.current_videos)
        self.current_folder = new_path
        self.current_videos = [rebase(path) for path in self.current_videos]
        for old_video, new_video in zip(old_videos, self.current_videos):
            try:
                rebase_timeline_paths(old_video, new_video)
            except OSError as error:
                self._log(f"时间轴路径未更新：{os.path.basename(new_video)}（{error}）")
        self._metadata_failed = {rebase(path) for path in self._metadata_failed}
        self._posted_batches = {
            tuple((rebase(path), size, mtime) for path, size, mtime in signature)
            for signature in self._posted_batches}
        for index in range(self.folder_list.count()):
            item = self.folder_list.item(index)
            if item and item.text() == folder_name:
                item.setText(new_name)
                break
        self.folders = [(new_name if name == folder_name else name, label)
                        for name, label in self.folders]
        self._log(f"日期文件夹改为：{new_name}")

    def _ensure_end_date(self):
        """确认重命名前补算结束日期；后台信息未到时同步读取一次视频时长。"""
        if getattr(self, "_ctx_end_compact", None):
            return
        if not (0 <= self.current_video_idx < len(self.current_videos)):
            return
        self._apply_video_date_range(self.current_videos[self.current_video_idx])

    def _write_copyright_metadata(self, filepath, enabled, info):
        """同步写入元数据，完成后才允许投稿；失败保留已重命名的原视频。"""
        if not enabled or not info:
            return True, ""
        from toolbox import video_meta
        return video_meta.write_copyright(filepath, info)

    def _skip_video(self):
        """跳过当前视频，加载下一个；未标准命名的跳过项不会被自动投稿。"""
        if self._busy:
            return
        self.current_video_idx += 1
        self._load_current()

    def _on_extra_toggle(self, checked):
        """版权开关切换"""
        self.rename_config.append_extra_info = checked
        save_rename_config(self.rename_config)
        self._update_extra_preview()

    def _update_extra_preview(self):
        if self.rename_extra_check.isChecked() and self.rename_config.extra_info:
            preview = self.rename_config.extra_info[:40] + (
                "..." if len(self.rename_config.extra_info) > 40 else "")
            self.extra_preview_label.setText(f"→ {preview}")
        else:
            self.extra_preview_label.setText("")

    # ==================== 标题模式 ====================

    def _set_title_mode(self, mode):
        self.title_mode = mode
        self.title_auto_btn.setChecked(mode == "auto")
        self.title_manual_btn.setChecked(mode == "manual")
        self.title_none_btn.setChecked(mode == "none")
        if mode == "manual":
            self.title_entry.setReadOnly(False)
            self.title_entry.setFocus()
        else:
            self.title_entry.setReadOnly(True)
            self.title_entry.clear()
        self._update_preview()

    # ==================== 文件名构建 ====================

    def _current_account(self):
        item = self.account_list.currentItem()
        return item.text() if item else ""

    def _current_platform(self):
        btn = self.platform_group.checkedButton()
        return btn.text() if btn else ""

    def _build_filename(self):
        account = self._current_account()
        # 按点击顺序排列类型
        type_str = "+".join(self.type_order) if self.type_order else ""
        version = "有弹幕" if self.ver_danmu_radio.isChecked() else "无弹幕"
        platform = self._current_platform()
        title = ""
        if self.title_mode == "auto":
            title = self.auto_title
        elif self.title_mode == "manual":
            title = self.title_entry.text().strip()
        date = getattr(self, "_ctx_date", "0000-00-00")
        time_str = getattr(self, "_ctx_time", "00-00-00")
        parts = [date, time_str]
        if account:
            parts.append(account)
        if type_str:
            parts.append(type_str)
        if version:
            parts.append(version)
        if platform:
            parts.append(platform)
        if title:
            t = clean_special_chars(title)
            if t:
                parts.append(t)
        suffix = self.rename_config.video_suffix
        if 0 <= self.current_video_idx < len(self.current_videos):
            suffix = Path(self.current_videos[self.current_video_idx]).suffix.lower() or suffix
        return "_".join(parts) + suffix

    def _update_preview(self):
        self.new_name_label.setText(self._build_filename())

    def _clear_thumb(self):
        self._thumb_pm = None
        self._thumb_source = None
        self._thumb_size = None
        self.thumb_label.clear()

    def _apply_thumb(self):
        """按预览区当前尺寸等比缩放；尺寸没变就跳过，避免布局来回触发。"""
        src = self._thumb_source
        if src is None or src.isNull():
            return
        target = self.thumb_label.size()
        if target.width() <= 4 or target.height() <= 4:
            return
        if target == self._thumb_size:
            return
        self._thumb_size = target
        self.thumb_label.setPixmap(
            src.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_thumb()

    # ==================== 重命名执行 ====================

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.folder_list, self.file_list, self.account_list, self.type_box,
                       self.auto_upload, self.post_btn, self.sub_tabs,
                       self.rename_extra_check):
            widget.setEnabled(not busy)
        self.confirm_btn.setEnabled(not busy and self.current_video_idx < len(self.current_videos))
        self.skip_btn.setEnabled(not busy and self.current_video_idx < len(self.current_videos))

    def _release_media_holders(self, path):
        """本软件内其它页面（如视频操作预览）正占用该视频时，先让它松开文件句柄。"""
        for page in list(getattr(self.app, "pages", {}).values()):
            release = getattr(page, "release_media_file", None)
            if callable(release):
                try:
                    release(path)
                except Exception:  # 释放失败不阻断改名，重试会再兜一次
                    continue

    def _do_rename(self):
        if self._busy or not (0 <= self.current_video_idx < len(self.current_videos)):
            return
        if not self._current_account():
            self._log("请选择直播账户")
            return
        if not self.type_order:
            self._log("请至少选择一种直播类型")
            return
        self._ensure_end_date()
        if not (0 <= self.current_video_idx < len(self.current_videos)):
            return
        index = self.current_video_idx
        src = self.current_videos[index]
        new_name = self._build_filename()
        if Path(new_name).name != new_name or any(character in new_name for character in '<>:"/\\|?*'):
            self._log("新文件名包含非法字符，请检查账户/类型/标题")
            return
        dst = os.path.join(os.path.dirname(src), new_name)
        if src != dst and os.path.exists(dst):
            self._log(f"目标已存在: {new_name}")
            return
        enabled = self.rename_extra_check.isChecked()
        info = self.rename_config.extra_info.strip()
        root = self._loaded_root
        self._release_media_holders(src)
        self._set_busy(True)
        self._load_token = object()
        self._log("正在重命名并完成所选元数据处理…")
        def work():
            if src != dst:
                try:
                    _rename_with_retry(src, dst)
                except OSError as error:
                    if _is_sharing_violation(error):
                        raise OSError(
                            f"视频仍被占用，改名未执行：{os.path.basename(src)}"
                            "（可能有程序正在预览/播放或同步该文件，关闭后重试）"
                        ) from error
                    raise
            sidecars, sidecar_skipped = (
                rebase_companions(src, dst) if src != dst else ([], []))
            metadata_ok, message = self._write_copyright_metadata(dst, enabled, info)
            return metadata_ok, message, sidecars, sidecar_skipped
        def done(result, error):
            self._set_busy(False)
            if error:
                self._log(f"重命名失败: {error}")
                return
            metadata_ok, message, sidecars, sidecar_skipped = result
            self.current_videos[index] = dst
            item = self.file_list.item(index)
            if item:
                item.setText(f"{new_name}")
            self._log(f"{os.path.basename(src)} → {new_name}")
            if sidecars:
                names = "、".join(os.path.basename(path) for path in sidecars[:4])
                more = "…" if len(sidecars) > 4 else ""
                self._log(f"同步重命名附属文件 {len(sidecars)} 个：{names}{more}")
            for reason in sidecar_skipped[:3]:
                self._log(f"附属文件跳过：{reason}")
            if message:
                self._log(message)
            if metadata_ok:
                self._metadata_failed.discard(src)
                self._metadata_failed.discard(dst)
            else:
                self._metadata_failed.add(dst)
                self._log("自动投稿已暂停。可按 Enter 重试；如不需要版权处理，取消勾选后再确认。")
            if root != self.app.work_dir:
                self._refresh()
                return
            if not metadata_ok:
                self._load_current()
                return
            self.current_video_idx = index + 1
            self._load_current()
        run_bg(work, done, owner=self)

    # ==================== 重命名完成后投稿 ====================
    def _save_post_switch(self, checked):
        settings = BU.load_settings()
        settings["auto_upload"] = bool(checked)
        try:
            BU.save_settings(settings)
        except OSError:
            self._log("投稿开关保存失败，请检查目录权限")

    def _open_upload(self):
        if self._busy:
            return
        ready = [path for path in self.current_videos
                 if self._is_standard_named(Path(path).name) and path not in self._metadata_failed]
        from gui_qt.bili_upload_dialog import open_upload_window
        open_upload_window(self, ready, blocked_paths=self._metadata_failed)

    def _post_completed_folder(self):
        if self._busy or not self.current_videos or self._loaded_root != self.app.work_dir:
            return
        paths = list(self.current_videos)
        if any(path in self._metadata_failed for path in paths):
            self._log("自动投稿暂停：元数据处理失败，请先完成处理")
            return
        config = {**BU.DEFAULTS, **BU.load_settings().get("upload", {}), "date": ""}
        try:
            actual = {str(path) for path in Path(self.current_folder).iterdir()
                      if path.is_file() and is_video_file(str(path))
                      and path.name.lower().endswith(self.rename_config.video_suffix)}
            if actual != set(paths):
                raise ValueError("文件夹的视频已变化，请重新选择文件夹完成重命名")
            BU.validate_renamed_videos(paths)
            metadata = BU.build_metadata(paths, config)
            signature = tuple((path, Path(path).stat().st_size, Path(path).stat().st_mtime_ns) for path in paths)
        except (ValueError, OSError) as error:
            self._log(f"自动投稿暂停：{error}")
            return
        if signature in self._posted_batches:
            return
        self._posted_batches.add(signature)
        self._set_busy(True)
        self._post_cancel.clear()
        count_text = f"{len(paths)} P" if len(paths) > 1 else "1 个视频"
        self._log(f"全部重命名及元数据处理完成，开始上传：{metadata['title']} · {count_text}")
        def work():
            uploader = BU.BiliUploader(cancel=self._post_cancel, on_progress=lambda current, total, text:
                self.post_progress.emit(f"{int(current * 100 / max(1, total))}% · {text}"))
            try:
                return uploader.run(paths, config)
            finally:
                uploader.close()
        def done(result, error):
            self._set_busy(False)
            if error:
                self._log(f"上传停止：{error}；本地视频保留，可在投稿窗口的历史里核对")
            else:
                state = "已投稿，等待审核" if result["state"] == "published" else "已上传未投稿"
                self._log(f"{state}：{result.get('bvid') or result.get('aid') or result['task']}")
            if self._loaded_root != self.app.work_dir:
                self._refresh()
                return
            self.confirm_btn.setEnabled(False)
            self.skip_btn.setEnabled(False)
        run_bg(work, done, owner=self)

    # ==================== 参数设置 Tab ====================

    def _build_params_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 上：账户列表 + 直播类型 + 直播平台 三列（均竖向卡片，占满剩余高度）
        cols = QHBoxLayout()
        cols.setSpacing(14)
        cols.addWidget(self._build_card_column("账户列表", "account"), 1)
        cols.addWidget(self._build_card_column("直播类型", "type"), 1)
        cols.addWidget(self._build_card_column("直播平台", "platform"), 1)
        outer.addLayout(cols, 1)

        # 下：设置收进一张卡片，参数与保存同一视觉块
        footer = QFrame()
        footer.setObjectName("renameParamsFooter")
        footer.setStyleSheet(
            "QFrame#renameParamsFooter { background: palette(alternate-base);"
            " border-radius: 8px; }")
        fv = QVBoxLayout(footer)
        fv.setContentsMargins(12, 8, 12, 10)
        fv.setSpacing(8)

        first = QHBoxLayout()
        first.setSpacing(8)
        first.addWidget(QLabel("视频后缀"))
        self.suffix_entry = QLineEdit()
        self.suffix_entry.setFixedWidth(90)
        self.suffix_entry.setText(self.rename_config.video_suffix)
        first.addWidget(self.suffix_entry)
        first.addSpacing(18)
        first.addWidget(QLabel("匹配阈值"))
        self.threshold_entry = QLineEdit()
        self.threshold_entry.setFixedWidth(70)
        self.threshold_entry.setText(str(self.rename_config.subtitle_match_threshold))
        self.threshold_entry.setToolTip("字幕与视频名的相似度阈值（0–1，越大越严格）")
        first.addWidget(self.threshold_entry)
        first.addSpacing(18)
        self.append_extra_check = QCheckBox("写入版权属性")
        self.append_extra_check.setToolTip(
            "写入标准版权标签（cprt）；MP4 在资源管理器里不显示「版权」，"
            "会同时写一份到「备注」，属性 → 详细信息 → 备注 可看到")
        first.addWidget(self.append_extra_check)
        first.addStretch(1)
        save_btn = QPushButton("保存设置")
        save_btn.setProperty("variant", "primary")
        save_btn.clicked.connect(self._save_params)
        first.addWidget(save_btn)
        fv.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(8)
        second.addWidget(QLabel("附加信息"))
        self.extra_info_entry = QLineEdit()
        self.extra_info_entry.setPlaceholderText("写入版权/备注的附加信息，可留空")
        second.addWidget(self.extra_info_entry, 1)
        fv.addLayout(second)

        outer.addWidget(footer)
        return w

    def _build_card_column(self, title, card_type):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
        head.addWidget(lbl)
        count_lbl = QLabel("")
        count_lbl.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        head.addWidget(count_lbl)
        head.addStretch(1)
        add_btn = QPushButton("添加")
        add_btn.setToolTip(f"新增一个{title[:-2] if title.endswith('列表') else title}")
        add_btn.clicked.connect(
            lambda: self._add_card_item(card_type))
        head.addWidget(add_btn)
        v.addLayout(head)
        self.card_count_labels[card_type] = count_lbl

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 6px; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(4)
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        if card_type == "account":
            self.account_cards_layout = box
        elif card_type == "type":
            self.type_cards_layout = box
        elif card_type == "platform":
            self.plat_cards_layout = box
        return w

    def _mini_btn(self, text, slot, danger=False, tip=""):
        """小图标按钮：仅图标（无文字），带可选 tooltip（悬停提示）。

        箭头类按钮统一使用 ↑/↓ 文字符号（Unicode 单色，符合整套 UI 风格）；
        删除按钮用 × (U+00D7)；尺寸 36×30 确保 × 图标清晰可辨（原 28×24
        字号太小，× 几乎看不见）；tip 不为空时挂 setToolTip。
        """
        b = QPushButton(text)
        b.setFixedSize(36, 30)
        b.setCursor(Qt.PointingHandCursor)
        # 字号 16 让 ×/↑/↓ 在 36×30 框内居中且清晰
        b.setStyleSheet(
            "QPushButton { font-size: 16px; padding: 0px;"
            " border: 1px solid palette(mid); border-radius: 4px;"
            " background: palette(base); color: palette(window-text); }"
            "QPushButton:hover { background: palette(alternate-base);"
            " border-color: palette(link); }")
        if tip:
            b.setToolTip(tip)
        if danger:
            b.setStyleSheet(
                "QPushButton { font-size: 18px; padding: 0px;"
                " border: 1px solid palette(mid); border-radius: 4px;"
                " background: palette(light); color: palette(bright-text);"
                " font-weight: bold; }"
                "QPushButton:hover { background: palette(midlight); }")
        b.clicked.connect(slot)
        return b

    def _build_card_list(self, layout, items, card_type):
        """在 layout 中重建卡片列表"""
        while layout.count():
            item = layout.takeAt(0)
            wgt = item.widget()
            if wgt:
                wgt.deleteLater()
        count_lbl = getattr(self, "card_count_labels", {}).get(card_type)
        if count_lbl is not None:
            count_lbl.setText(f"{len(items)} 项")
        for i, name in enumerate(items):
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: palette(alternate-base); border-radius: 6px; }")
            h = QHBoxLayout(card)
            h.setContentsMargins(8, 3, 4, 3)
            h.setSpacing(2)
            drag_lbl = QLabel("⋮⋮")
            drag_lbl.setStyleSheet(
                "color: palette(placeholder-text); border: none; background: transparent;")
            drag_lbl.setFixedWidth(20)
            h.addWidget(drag_lbl)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("border: none; background: transparent;")
            h.addWidget(name_lbl, 1)
            # 上移/下移用箭头文字，并加悬停提示；其他箭头位也保持一致
            h.addWidget(self._mini_btn(
                "↑", lambda _=False, idx=i: self._move_item(card_type, idx, -1),
                tip="上移"))
            h.addWidget(self._mini_btn(
                "↓", lambda _=False, idx=i: self._move_item(card_type, idx, 1),
                tip="下移"))
            h.addWidget(self._mini_btn(
                "×", lambda _=False, nm=name: self._delete_card_item(card_type, nm),
                danger=True, tip="删除"))
            layout.addWidget(card)
        layout.addStretch(1)

    def _get_card_layout(self, card_type):
        if card_type == "account":
            return self.account_cards_layout
        if card_type == "type":
            return self.type_cards_layout
        return self.plat_cards_layout

    def _get_cfg_list(self, card_type):
        cfg = get_rename_config()
        if card_type == "account":
            return cfg, cfg.account_list, "账户"
        if card_type == "type":
            return cfg, cfg.live_types, "类型"
        return cfg, cfg.platform_list, "平台"

    def _move_item(self, card_type, idx, direction):
        cfg, lst, _ = self._get_cfg_list(card_type)
        new_idx = idx + direction
        if 0 <= new_idx < len(lst):
            lst.insert(new_idx, lst.pop(idx))
            save_rename_config(cfg)
            # 只重建当前组卡片，不刷新全部
            self._build_card_list(self._get_card_layout(card_type), lst, card_type)
            self._rebuild_rename_radios(card_type)

    def _delete_card_item(self, card_type, name):
        cfg, lst, item_type = self._get_cfg_list(card_type)
        if name in lst:
            ret = QMessageBox.question(
                self, "确认", f"确定删除{item_type}「{name}」？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret == QMessageBox.Yes:
                lst.remove(name)
                save_rename_config(cfg)
                self._build_card_list(self._get_card_layout(card_type), lst, card_type)
                self._rebuild_rename_radios(card_type)

    def _rebuild_rename_radios(self, card_type):
        if card_type == "account":
            self._rebuild_account_radios()
        elif card_type == "type":
            self._rebuild_type_checkboxes()
        else:
            self._rebuild_platform_radios()

    def _add_card_item(self, card_type):
        titles = {
            "account": ("添加账户", "输入新账户名:"),
            "type": ("添加类型", "输入新类型名:"),
            "platform": ("添加平台", "输入新平台名:"),
        }
        title, prompt = titles[card_type]
        name, ok = QInputDialog.getText(self, title, prompt)
        if ok and name.strip():
            self._do_add(card_type, name.strip())

    def _do_add(self, card_type, name):
        cfg, lst, _ = self._get_cfg_list(card_type)
        if name not in lst:
            lst.append(name)
            save_rename_config(cfg)
            self._build_card_list(self._get_card_layout(card_type), lst, card_type)
            self._rebuild_rename_radios(card_type)

    def _load_params_to_ui(self):
        self.rename_config = get_rename_config()
        self._build_card_list(
            self.account_cards_layout, self.rename_config.account_list, "account")
        self._build_card_list(
            self.type_cards_layout, self.rename_config.live_types, "type")
        self._build_card_list(
            self.plat_cards_layout, self.rename_config.platform_list, "platform")
        self.suffix_entry.setText(self.rename_config.video_suffix)
        self.threshold_entry.setText(str(self.rename_config.subtitle_match_threshold))
        self.extra_info_entry.setText(self.rename_config.extra_info)
        self.append_extra_check.setChecked(self.rename_config.append_extra_info)
        self.rename_extra_check.setChecked(self.rename_config.append_extra_info)
        self._update_extra_preview()

    def _save_params(self):
        cfg = get_rename_config()
        s = self.suffix_entry.text().strip()
        if s:
            cfg.video_suffix = s if s.startswith(".") else f".{s}"
        try:
            cfg.subtitle_match_threshold = float(self.threshold_entry.text().strip())
        except ValueError:
            self.app.toast("阈值必须为数字", "warning")
            return
        cfg.extra_info = self.extra_info_entry.text().strip()
        cfg.append_extra_info = self.append_extra_check.isChecked()
        save_rename_config(cfg)
        self.rename_config = cfg
        self.rename_extra_check.setChecked(cfg.append_extra_info)
        self._update_extra_preview()
        self._rebuild_account_radios()
        self._rebuild_type_checkboxes()
        self._rebuild_platform_radios()
        self.app.toast("参数已保存", "success")

    # ==================== 日志 ====================

    def _log(self, msg):
        self.log_text.append(str(msg))

    def on_show(self):
        if not self._params_loaded:
            self._lazy_load()
        if self._loaded_root != self.app.work_dir or not self.folders:
            self._refresh()

    def on_theme_changed(self):
        self._refresh_type_labels()

    def shutdown(self):
        self._post_cancel.set()
        self._load_token = object()
        self._thumb_pm = None
        self._clear_thumb()
