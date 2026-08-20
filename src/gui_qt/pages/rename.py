"""重命名页（PySide6 版）：交互式重命名 — 三栏：文件夹 | 账户+类型 | 视频预览。

对应原版 gui/toolbox_pages.py RenamePage，功能一一对应：
- Tab「交互重命名」：
  * 栏1 两级列表：日期文件夹 + 文件夹内视频文件（点击跳转、当前项高亮）
  * 栏2 直播账户（单选）+ 直播类型（多选，按点击顺序编号 [N]，数字键 1-9 快捷切换）
  * 栏3 视频信息（ffprobe）+ 版本（无/有弹幕）+ 平台（配置单选）+ 标题三模式
    （自动提取/手动输入/不保留）+ 预览截图 + 新文件名预览 + 确认/跳过 + 版权开关
  * Enter=确认（已标准命名则跳过）；Esc=跳过；标准命名文件提示「重新命名」
  * 重命名后用 ffmpeg 写入版权元数据
- Tab「参数设置」：账户/类型/平台卡片（上移/下移/删除/添加）、视频后缀、
  匹配阈值、附加信息、写入版权开关、保存设置
"""

import os
import re
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSplitter, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget,
)

from toolbox.config_manager import get_rename_config, save_rename_config
from toolbox.rename_engine import (
    extract_file_title, extract_time_str, scan_date_folders,
)
from toolbox.utils import (
    capture_video_frame, clean_special_chars, get_video_info, is_video_file,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage


class RenamePage(BasePage):
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.sub_tabs = QTabWidget()
        layout.addWidget(self.sub_tabs, 1)
        self.sub_tabs.addTab(self._build_rename_tab(), "📁 交互重命名")
        self.sub_tabs.addTab(self._build_params_tab(), "⚙️ 参数设置")

        self._rebuild_account_radios()
        self._rebuild_type_checkboxes()
        self._rebuild_platform_radios()
        self._set_title_mode("none")

        # 懒加载：先渲染框架，再加载内容（避免切换页面时卡顿）
        QTimer.singleShot(0, self._lazy_load)

    def _lazy_load(self):
        self._refresh()
        self._load_params_to_ui()

    # ==================== 交互重命名 Tab（三栏） ====================

    def _build_rename_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(5)

        # 顶栏：刷新 + 文件夹计数 + 进度
        top = QHBoxLayout()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._refresh)
        top.addWidget(refresh_btn)
        self.folder_count_label = QLabel("")
        top.addWidget(self.folder_count_label)
        top.addStretch(1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #64b5f6;")
        top.addWidget(self.progress_label)
        v.addLayout(top)

        # 三栏主体
        split = QSplitter(Qt.Horizontal)
        v.addWidget(split, 1)

        # === 栏1: 文件夹 + 文件两级列表 ===
        col1 = QWidget()
        c1 = QVBoxLayout(col1)
        c1.setContentsMargins(0, 0, 0, 0)
        c1.setSpacing(3)
        c1.addWidget(QLabel("📁 日期文件夹"))
        self.folder_list = QListWidget()
        self.folder_list.itemClicked.connect(lambda _it: self._on_folder_click())
        c1.addWidget(self.folder_list, 3)
        c1.addWidget(QLabel("🎬 文件列表"))
        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self._on_file_click)
        c1.addWidget(self.file_list, 4)
        split.addWidget(col1)

        # === 栏2: 账户 + 类型 ===
        col2 = QWidget()
        c2 = QVBoxLayout(col2)
        c2.setContentsMargins(0, 0, 0, 0)
        c2.setSpacing(3)
        c2.addWidget(QLabel("👤 直播账户"))
        self.account_list = QListWidget()
        self.account_list.itemSelectionChanged.connect(self._update_preview)
        c2.addWidget(self.account_list, 1)
        c2.addWidget(QLabel("🎯 直播类型（多选）"))
        self.type_list = QListWidget()
        self.type_list.itemClicked.connect(self._on_type_item_click)
        c2.addWidget(self.type_list, 1)
        clear_btn = QPushButton("🗑 清除所有选择")
        clear_btn.clicked.connect(self._clear_all_types)
        c2.addWidget(clear_btn)
        split.addWidget(col2)

        # === 栏3: 视频信息 + 预览 + 操作 ===
        col3_area = QScrollArea()
        col3_area.setWidgetResizable(True)
        col3 = QWidget()
        c3 = QVBoxLayout(col3)
        c3.setContentsMargins(6, 4, 6, 4)
        c3.setSpacing(5)

        c3.addWidget(QLabel("📹 视频信息"))
        self.video_info_label = QLabel("请选择左侧文件夹")
        self.video_info_label.setWordWrap(True)
        self.video_info_label.setStyleSheet("font-size: 12px;")
        c3.addWidget(self.video_info_label)

        # 版本
        ver_row = QHBoxLayout()
        ver_row.addWidget(QLabel("📋 版本:"))
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
        c3.addLayout(ver_row)

        # 平台
        plat_row = QHBoxLayout()
        plat_row.addWidget(QLabel("📡 平台:"))
        self.platform_group = QButtonGroup(self)
        self.platform_box = QWidget()
        self.platform_layout = QHBoxLayout(self.platform_box)
        self.platform_layout.setContentsMargins(0, 0, 0, 0)
        plat_row.addWidget(self.platform_box, 1)
        c3.addLayout(plat_row)

        # 标题三模式
        c3.addWidget(QLabel("🏷 标题:"))
        trow = QHBoxLayout()
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
        c3.addLayout(trow)
        self.title_entry = QLineEdit()
        self.title_entry.setPlaceholderText("手动输入标题...")
        self.title_entry.setReadOnly(True)
        self.title_entry.textChanged.connect(lambda _t: self._update_preview())
        c3.addWidget(self.title_entry)

        # 预览截图
        c3.addWidget(QLabel("📸 预览截图"))
        self.thumb_label = QLabel("（选择视频后显示）")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedHeight(180)
        self.thumb_label.setStyleSheet(
            "background: #0a0a1a; border-radius: 6px; color: #666;")
        c3.addWidget(self.thumb_label)

        # 新文件名预览
        c3.addWidget(QLabel("📝 新文件名:"))
        self.new_name_label = QLabel("")
        self.new_name_label.setWordWrap(True)
        self.new_name_label.setStyleSheet(
            "background: #0a2a0a; border-radius: 6px; padding: 6px; font-size: 12px;")
        c3.addWidget(self.new_name_label)

        # 操作按钮
        action_row = QHBoxLayout()
        self.confirm_btn = QPushButton("✅ 确认重命名 (Enter)")
        self.confirm_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._do_rename)
        action_row.addWidget(self.confirm_btn)
        self.skip_btn = QPushButton("⏭ 跳过")
        self.skip_btn.setEnabled(False)
        self.skip_btn.clicked.connect(self._skip_video)
        action_row.addWidget(self.skip_btn)
        action_row.addStretch(1)
        c3.addLayout(action_row)

        # 版权信息开关（与参数设置同步）
        extra_row = QHBoxLayout()
        self.rename_extra_check = QCheckBox("📋 写入版权属性")
        self.rename_extra_check.setChecked(self.rename_config.append_extra_info)
        self.rename_extra_check.toggled.connect(self._on_extra_toggle)
        extra_row.addWidget(self.rename_extra_check)
        self.extra_preview_label = QLabel("")
        self.extra_preview_label.setStyleSheet("color: #888; font-size: 10px;")
        extra_row.addWidget(self.extra_preview_label, 1)
        c3.addLayout(extra_row)
        c3.addStretch(1)

        col3_area.setWidget(col3)
        split.addWidget(col3_area)
        split.setSizes([230, 250, 520])

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(80)
        v.addWidget(self.log_text)

        self._update_extra_preview()
        return w

    # ==================== 账户/类型 UI ====================

    def _rebuild_account_radios(self):
        self.account_list.clear()
        cfg = get_rename_config()
        for acc in cfg.account_list:
            self.account_list.addItem(acc)
        if cfg.account_list:
            self.account_list.setCurrentRow(0)

    def _rebuild_type_checkboxes(self):
        self.type_order.clear()
        self.type_list.clear()
        for t in get_rename_config().live_types:
            it = QListWidgetItem(f"[ ] {t}")
            it.setData(Qt.UserRole, t)
            it.setForeground(QColor("#888888"))
            self.type_list.addItem(it)

    def _rebuild_platform_radios(self):
        # 清除旧按钮
        while self.platform_layout.count():
            item = self.platform_layout.takeAt(0)
            wgt = item.widget()
            if wgt:
                wgt.deleteLater()
        cfg = get_rename_config()
        if not cfg.platform_list:
            cfg.platform_list = ["抖音"]
        current = self._current_platform()
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
        """刷新标签显示：[] 未选中，[N] 选中顺序"""
        for i in range(self.type_list.count()):
            item = self.type_list.item(i)
            t = item.data(Qt.UserRole)
            if t in self.type_order:
                idx = self.type_order.index(t) + 1
                item.setText(f"[{idx}] {t}")
                item.setForeground(QColor("#64b5f6"))
            else:
                item.setText(f"[ ] {t}")
                item.setForeground(QColor("#888888"))

    def _on_type_click(self, tname):
        """点击切换选中，按顺序排列"""
        if tname in self.type_order:
            self.type_order.remove(tname)
        else:
            self.type_order.append(tname)
        self._refresh_type_labels()
        self._update_preview()

    def _on_type_item_click(self, item):
        self._on_type_click(item.data(Qt.UserRole))

    def _key_toggle_type(self, idx):
        types = get_rename_config().live_types
        if 0 <= idx < len(types):
            self._on_type_click(types[idx])

    def _reset_type_selection(self):
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
        if self._is_standard_named(filename):
            self._skip_video()
        else:
            self._do_rename()

    def on_escape_press(self):
        self._skip_video()

    # ==================== 文件夹 / 视频加载 ====================

    def on_dir_changed(self, path):
        self._refresh()

    def _refresh(self):
        self.folders = scan_date_folders(self.app.work_dir)
        self.folder_list.clear()
        if not self.folders:
            self.folder_list.addItem("（未找到日期文件夹）")
            self.folder_count_label.setText("⚠️ 无日期文件夹")
        else:
            for fn, _dt in self.folders:
                self.folder_list.addItem(f"📁 {fn}")
            self.folder_count_label.setText(f"共 {len(self.folders)} 个文件夹")
        # 清空文件列表
        self.file_list.clear()
        self.file_list.addItem("（请选择文件夹）")
        self._log(f"📂 扫描到 {len(self.folders)} 个日期文件夹")
        # 自动选中第一个文件夹
        if self.folders:
            QTimer.singleShot(100, self._auto_select_first)

    def _on_folder_click(self):
        """点击文件夹，列出其中视频文件"""
        item = self.folder_list.currentItem()
        if item is None:
            return
        line = item.text().strip()
        if not line or "未找到" in line:
            return
        folder_name = line.replace("📁 ", "").strip()
        folder_path = os.path.join(self.app.work_dir, folder_name)
        if not os.path.isdir(folder_path):
            return

        self.current_folder = folder_path
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
            self.file_list.addItem(f"🎬 {os.path.basename(fp)}")

        self.current_video_idx = 0
        self._reset_type_selection()
        self._load_current()

    def _on_file_click(self, item):
        """点击文件列表中某一项，跳转到该视频"""
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
            self._log(f"✅ 文件夹处理完毕: {folder_name}（共 {total} 个）")
            self.video_info_label.setText(f"✅ 全部完成！（{total}/{total}）")
            self.confirm_btn.setEnabled(False)
            self.skip_btn.setEnabled(False)
            self.progress_label.setText(f"✅ 完成 {total} 个")
            return
        path = self.current_videos[self.current_video_idx]
        if not os.path.exists(path):
            self._log(f"⚠️ 文件不存在，自动跳过: {os.path.basename(path)}")
            self.current_video_idx += 1
            self._load_current()
            return
        self._load_video(path)

    def _is_standard_named(self, filename: str) -> bool:
        """检测是否已经是标准命名格式（YYYY-MM-DD_HH-MM-SS_...）"""
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_", filename))

    def _load_video(self, video_path):
        """加载单个视频的详细信息 + 截图（后台线程执行，避免卡 UI）"""
        filename = os.path.basename(video_path)
        folder_date = os.path.basename(self.current_folder)
        m = re.match(r"^(\d{8})", folder_date)
        date_str = m.group(1) if m else folder_date[:8]
        fmt_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) >= 8 else date_str

        time_str = extract_time_str(filename)
        self.auto_title = extract_file_title(filename)

        self._ctx_date = fmt_date
        self._ctx_time = time_str
        self._set_title_mode("none")  # 默认不保留

        total = len(self.current_videos)
        self.progress_label.setText(f"📹 {self.current_video_idx + 1} / {total}")

        # 高亮当前文件
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if i == self.current_video_idx:
                it.setBackground(QColor("#1a3a5c"))
                it.setForeground(QColor("#ffeb3b"))
            else:
                it.setBackground(QColor("transparent"))
                it.setForeground(QColor("#c6cbd6"))
        self.file_list.scrollToItem(
            self.file_list.item(self.current_video_idx) if self.current_video_idx < self.file_list.count() else None)

        # 后台获取视频信息 + 截图
        token = object()
        self._load_token = token
        self.video_info_label.setText(f"📄 {filename}\n⏱ 加载中...")

        def work():
            info = get_video_info(video_path)
            thumb = capture_video_frame(video_path)
            return info, thumb

        def done(result, err):
            if self._load_token is not token:
                return  # 已切换文件，丢弃过期结果
            info, thumb = result if result else ({}, None)
            if info.get("error"):
                info_text = f"📄 {filename}\n⏱ 时间: {time_str}\n⚠️ 无法获取视频信息"
            else:
                info_text = (
                    f"📄 {filename}\n⏱ 时间: {time_str}\n"
                    f"📏 {info.get('resolution', '?')} | {info.get('orientation', '?')}\n"
                    f"⏳ {info.get('duration_str', '?')} | 🎞 {info.get('fps', '?')} fps\n"
                    f"📦 {info.get('file_size_str', '?')} | 🎵 {info.get('audio_codec', '?')}"
                )
            self.video_info_label.setText(info_text)

            if thumb and os.path.exists(thumb):
                pm = QPixmap(thumb)
                if not pm.isNull():
                    pm = pm.scaledToHeight(180, Qt.SmoothTransformation)
                    self._thumb_pm = pm
                    self.thumb_label.setPixmap(pm)
                else:
                    self._thumb_pm = None
                    self.thumb_label.setText("（截图失败）")
            else:
                self._thumb_pm = None
                self.thumb_label.setText("（无法截图）")
            self._update_preview()

        run_bg(work, done)

        # 检测是否已标准命名
        if self._is_standard_named(filename):
            self.confirm_btn.setText("✏️ 重新命名（点击）")
            self.confirm_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.skip_btn.setFocus()
            self._log(f"⚠️ 已是标准命名: {filename}")
        else:
            self.confirm_btn.setText("✅ 确认重命名 (Enter)")
            self.confirm_btn.setEnabled(True)
            self.skip_btn.setText("⏭ 跳过")
            self.skip_btn.setEnabled(True)

    def _write_copyright_metadata(self, filepath):
        """用 ffmpeg 写入版权元数据（右键→属性→详细信息→版权），后台执行不阻塞。"""
        if not self.rename_extra_check.isChecked():
            return
        info = self.rename_config.extra_info.strip()
        if not info:
            return

        def work():
            tmp = filepath + ".tmp_meta.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-i", filepath,
                "-c", "copy",
                "-metadata", f"copyright={info}",
                "-movflags", "+faststart",
                "-hide_banner", "-loglevel", "error",
                tmp,
            ]
            p = subprocess.run(cmd, capture_output=True, timeout=120)
            if p.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, filepath)
                return True
            if os.path.exists(tmp):
                os.remove(tmp)
            return False

        def done(ok, err):
            if ok:
                self._log("📋 版权信息已写入")

        run_bg(work, done)

    def _skip_video(self):
        """跳过当前视频，加载下一个"""
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
        return "_".join(parts) + self.rename_config.video_suffix

    def _update_preview(self):
        self.new_name_label.setText(self._build_filename())

    # ==================== 重命名执行 ====================

    def _do_rename(self):
        if not self.current_videos:
            return
        # 必选校验（标题可选）
        if not self._current_account():
            self._log("⚠️ 请选择直播账户")
            return
        if not self.type_order:
            self._log("⚠️ 请至少选择一种直播类型")
            return
        src = self.current_videos[self.current_video_idx]
        new_name = self._build_filename()
        dst = os.path.join(os.path.dirname(src), new_name)
        if os.path.exists(dst):
            self._log(f"⚠️ 目标已存在: {new_name}")
            return
        if src == dst:
            self._log("⏭ 文件名未变化")
            self._skip_video()
            return
        try:
            os.rename(src, dst)
            self._log(f"✅ {os.path.basename(src)} → {new_name}")
            self.current_videos[self.current_video_idx] = dst
            item = self.file_list.item(self.current_video_idx)
            if item is not None:
                item.setText(f"🎬 {new_name}")
            # 写入版权元数据（后台）
            self._write_copyright_metadata(dst)
            self.current_video_idx += 1
            self._load_current()
        except Exception as e:
            self._log(f"❌ 失败: {e}")

    # ==================== 参数设置 Tab ====================

    def _build_params_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        # 上：账户列表 + 直播类型 两列
        cols = QHBoxLayout()
        cols.addWidget(self._build_card_column("👤 账户列表", "account"), 1)
        cols.addWidget(self._build_card_column("🎯 直播类型", "type"), 1)
        outer.addLayout(cols, 1)

        # 直播平台（横向卡片）
        outer.addWidget(QLabel("📡 直播平台"))
        self.plat_scroll = QScrollArea()
        self.plat_scroll.setFixedHeight(120)
        self.plat_scroll.setWidgetResizable(True)
        self.plat_container = QWidget()
        self.plat_flow = QHBoxLayout(self.plat_container)
        self.plat_flow.setContentsMargins(4, 4, 4, 4)
        self.plat_flow.setSpacing(6)
        self.plat_scroll.setWidget(self.plat_container)
        outer.addWidget(self.plat_scroll)
        add_plat_btn = QPushButton("➕ 添加平台")
        add_plat_btn.clicked.connect(self._add_platform)
        outer.addWidget(add_plat_btn)

        # 其他设置：后缀 / 阈值 / 保存
        of = QHBoxLayout()
        of.addWidget(QLabel("视频后缀:"))
        self.suffix_entry = QLineEdit()
        self.suffix_entry.setFixedWidth(90)
        self.suffix_entry.setText(self.rename_config.video_suffix)
        of.addWidget(self.suffix_entry)
        of.addSpacing(20)
        of.addWidget(QLabel("匹配阈值:"))
        self.threshold_entry = QLineEdit()
        self.threshold_entry.setFixedWidth(70)
        self.threshold_entry.setText(str(self.rename_config.subtitle_match_threshold))
        of.addWidget(self.threshold_entry)
        of.addStretch(1)
        save_btn = QPushButton("💾 保存设置")
        save_btn.setStyleSheet("background: #2e7d32; color: white;")
        save_btn.clicked.connect(self._save_params)
        of.addWidget(save_btn)
        outer.addLayout(of)

        # 附加信息 + 版权开关
        ef = QHBoxLayout()
        ef.addWidget(QLabel("附加信息:"))
        self.extra_info_entry = QLineEdit()
        ef.addWidget(self.extra_info_entry, 1)
        self.append_extra_check = QCheckBox("写入版权属性")
        ef.addWidget(self.append_extra_check)
        outer.addLayout(ef)
        outer.addStretch(1)
        return w

    def _build_card_column(self, title, card_type):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        head.addWidget(lbl)
        head.addStretch(1)
        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(
            lambda: self._add_card_item(card_type))
        head.addWidget(add_btn)
        v.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(3)
        scroll.setWidget(container)
        v.addWidget(scroll, 1)

        if card_type == "account":
            self.account_cards_layout = box
        elif card_type == "type":
            self.type_cards_layout = box
        return w

    def _mini_btn(self, text, slot, danger=False):
        b = QPushButton(text)
        b.setFixedSize(28, 24)
        if danger:
            b.setStyleSheet("background: #c62828; color: white;")
        b.clicked.connect(slot)
        return b

    def _build_card_list(self, layout, items, card_type):
        """在 layout 中重建卡片列表"""
        while layout.count():
            item = layout.takeAt(0)
            wgt = item.widget()
            if wgt:
                wgt.deleteLater()
        for i, name in enumerate(items):
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #1a1a3e; border-radius: 6px; }")
            h = QHBoxLayout(card)
            h.setContentsMargins(8, 3, 4, 3)
            h.setSpacing(2)
            drag_lbl = QLabel("⋮⋮")
            drag_lbl.setStyleSheet("color: #666; border: none; background: transparent;")
            drag_lbl.setFixedWidth(20)
            h.addWidget(drag_lbl)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("border: none; background: transparent;")
            h.addWidget(name_lbl, 1)
            h.addWidget(self._mini_btn(
                "⬆", lambda _=False, idx=i: self._move_item(card_type, idx, -1)))
            h.addWidget(self._mini_btn(
                "⬇", lambda _=False, idx=i: self._move_item(card_type, idx, 1)))
            h.addWidget(self._mini_btn(
                "✕", lambda _=False, nm=name: self._delete_card_item(card_type, nm),
                danger=True))
            layout.addWidget(card)
        layout.addStretch(1)

    def _get_card_layout(self, card_type):
        if card_type == "account":
            return self.account_cards_layout
        if card_type == "type":
            return self.type_cards_layout
        return self.plat_flow

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

    def _add_account(self):
        name, ok = QInputDialog.getText(self, "添加账户", "输入新账户名:")
        if ok and name.strip():
            self._do_add("account", name.strip())

    def _add_type(self):
        name, ok = QInputDialog.getText(self, "添加类型", "输入新类型名:")
        if ok and name.strip():
            self._do_add("type", name.strip())

    def _add_platform(self):
        name, ok = QInputDialog.getText(self, "添加平台", "输入新平台名:")
        if ok and name.strip():
            self._do_add("platform", name.strip())

    def _load_params_to_ui(self):
        self.rename_config = get_rename_config()
        self._build_card_list(
            self.account_cards_layout, self.rename_config.account_list, "account")
        self._build_card_list(
            self.type_cards_layout, self.rename_config.live_types, "type")
        self._build_card_list(
            self.plat_flow, self.rename_config.platform_list, "platform")
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
            QMessageBox.warning(self, "格式错误", "阈值必须为数字")
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
        QMessageBox.information(self, "保存成功", "参数已保存")

    # ==================== 日志 ====================

    def _log(self, msg):
        self.log_text.append(str(msg))

    def on_show(self):
        pass
