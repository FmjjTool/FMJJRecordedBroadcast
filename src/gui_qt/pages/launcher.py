"""继续操作页（PySide6 版）：外部软件/链接启动器 4×3 面板 + 配置管理。

对应原版 gui/toolbox_pages.py LauncherPage：
- 功能 tab：4×3 面板（最多 12 项），点击启动；软件/链接区分底色；支持 150px 图标
- 设置 tab：每项 名称/路径 输入（实时同步）+ 图标预览与选择 + 上移/下移/删除
  + 添加软件 / 添加链接 / 保存并刷新面板
- 启动：http/https 链接 → 浏览器；文件 → os.startfile（不存在则警告）
- 跟随进程树：勾选后启动的软件纳入 Job Object，本工具退出时整棵进程树一并关闭
"""

import os
import subprocess
import webbrowser

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt.pages.base import BasePage
from gui_qt.process_tree import assign_job, create_kill_job, terminate_job


class LauncherPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.items = load_convert_config().get("launcher_items", [])
        # 已链接进程树的任务列表 [(job 或 None, proc 或 None), ...]
        self._linked = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 进程树控制行
        tree_row = QHBoxLayout()
        self.link_tree_var = QCheckBox(
            "跟随进程树（启动的软件纳入进程树：本工具退出时一并关闭全部子进程）")
        self.link_tree_var.setChecked(True)
        tree_row.addWidget(self.link_tree_var)
        self.stop_all_btn = QPushButton("⏹ 停止已链接软件")
        self.stop_all_btn.setStyleSheet("background: #c62828; color: white; padding: 4px 12px;")
        self.stop_all_btn.clicked.connect(self._stop_all_linked)
        tree_row.addWidget(self.stop_all_btn)
        self.linked_lbl = QLabel("", styleSheet="color: #888; font-size: 11px;")
        tree_row.addWidget(self.linked_lbl)
        tree_row.addStretch(1)
        layout.addLayout(tree_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_panel(), "功能")
        self.tabs.addTab(self._build_config(), "设置")
        self._refresh_panel()
        self._refresh_linked_lbl()

    # ==================== 功能 tab（4×3 面板） ====================

    def _build_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        self.panel_grid = QGridLayout()
        self.panel_grid.setSpacing(8)
        v.addLayout(self.panel_grid)
        v.addStretch(1)
        return w

    def _refresh_panel(self):
        while self.panel_grid.count():
            item = self.panel_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, item in enumerate(self.items):
            if i >= 12:
                break
            row, col = divmod(i, 4)
            name = item.get("name", f"软件{i + 1}")
            path = item.get("path", "")
            icon = item.get("icon", "")
            is_link = item.get("type") == "link"

            card = QPushButton()
            card.setMinimumHeight(150)
            card.setCursor(Qt.PointingHandCursor)
            card.setStyleSheet(
                "QPushButton { background: #1a3a2a; border-radius: 16px; "
                "font-size: 11px; color: #ccc; padding: 8px; }"
                "QPushButton:hover { background: #22553a; }"
                if is_link else
                "QPushButton { background: #1e1e3a; border-radius: 16px; "
                "font-size: 11px; color: #ccc; padding: 8px; }"
                "QPushButton:hover { background: #2b2b52; }")

            if icon and os.path.isfile(icon):
                pm = QPixmap(icon)
                if not pm.isNull():
                    card.setIcon(QIcon(pm))
                    card.setIconSize(pm.size().boundedTo(QSize(150, 150)))
            if card.icon().isNull():
                card.setText(f"{'🔗' if is_link else '📦'}\n{name}")
            else:
                card.setText(name)
            card.clicked.connect(lambda _c=False, p=path: self._launch(p))
            self.panel_grid.addWidget(card, row, col)
        # 补满空格子保持网格均匀
        for i in range(len(self.items), 12):
            row, col = divmod(i, 4)
            placeholder = QWidget()
            self.panel_grid.addWidget(placeholder, row, col)

    def _launch(self, path):
        if not path:
            return
        # 链接模式：http/https 链接直接跳浏览器
        if path.lower().startswith(("http://", "https://")):
            try:
                webbrowser.open(path)
            except Exception as e:
                QMessageBox.warning(self, "打开失败", str(e))
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "无法启动", f"文件不存在:\n{path}")
            return
        if self.link_tree_var.isChecked():
            self._launch_linked(path)
        else:
            os.startfile(path)  # noqa: S606 (Windows) 独立运行，不随本工具退出

    def _launch_linked(self, path):
        """纳入进程树启动：exe 直接 Popen；bat/cmd 经 cmd /c；其余退回 startfile。"""
        try:
            if path.lower().endswith((".exe", ".com")):
                proc = subprocess.Popen([path])
            elif path.lower().endswith((".bat", ".cmd")):
                proc = subprocess.Popen(
                    ["cmd", "/c", path], cwd=os.path.dirname(path),
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                # 文档等非可执行文件无法纳入 Job → 独立打开
                os.startfile(path)  # noqa: S606 (Windows)
                return
        except Exception as e:
            QMessageBox.warning(self, "启动失败", str(e))
            return
        job = create_kill_job()
        if job and assign_job(job, proc):
            self._linked.append((job, proc))
            self.app.toast(f"已启动（跟随进程树）: {os.path.basename(path)}", "success")
        else:
            # Job 绑定失败：退化为 taskkill /T 兜底跟踪
            if job:
                terminate_job(job)
            self._linked.append((None, proc))
            self.app.toast(f"已启动（进程树链接降级）: {os.path.basename(path)}", "warning")
        self._refresh_linked_lbl()

    def _refresh_linked_lbl(self):
        # 清理已退出的任务
        self._linked = [(job, p) for job, p in self._linked
                        if p.poll() is None]
        n = len(self._linked)
        self.linked_lbl.setText(f"已链接 {n} 个进程" if n else "")
        self.stop_all_btn.setEnabled(n > 0)

    def _stop_all_linked(self):
        """手动停止：终止全部已链接进程树。"""
        for job, proc in self._linked:
            if job is not None:
                terminate_job(job)
            else:
                try:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
        self._linked = []
        self._refresh_linked_lbl()
        self.app.toast("已链接软件进程树已全部终止", "success")

    def shutdown(self):
        """软件退出：跟随进程树的软件整树一并关闭（父关子关）。"""
        self._stop_all_linked_silent()

    def _stop_all_linked_silent(self):
        for job, proc in self._linked:
            if job is not None:
                terminate_job(job)
            else:
                try:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
        self._linked = []

    def on_show(self):
        # 切回页面时同步已链接进程状态（软件可能已被用户手动关闭）
        self._refresh_linked_lbl()

    # ==================== 设置 tab（配置管理） ====================

    def _build_config(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)

        self.cfg_scroll = QScrollArea()
        self.cfg_scroll.setWidgetResizable(True)
        self.cfg_widget = QWidget()
        self.cfg_layout = QVBoxLayout(self.cfg_widget)
        self.cfg_layout.setAlignment(Qt.AlignTop)
        self.cfg_layout.setSpacing(4)
        self.cfg_scroll.setWidget(self.cfg_widget)
        v.addWidget(self.cfg_scroll, 1)

        bar = QHBoxLayout()
        add_btn = QPushButton("➕ 添加软件")
        add_btn.clicked.connect(self._add_item)
        bar.addWidget(add_btn)
        link_btn = QPushButton("🔗 添加链接")
        link_btn.setStyleSheet("background: #1a5c2a; color: white;")
        link_btn.clicked.connect(self._add_link)
        bar.addWidget(link_btn)
        save_btn = QPushButton("💾 保存并刷新面板")
        save_btn.setStyleSheet("background: #2e7d32; color: white;")
        save_btn.clicked.connect(self._save_and_refresh)
        bar.addWidget(save_btn)
        bar.addStretch(1)
        v.addLayout(bar)
        self._refresh_config_cards()
        return w

    def _mini_btn(self, text, slot, danger=False):
        b = QPushButton(text)
        b.setFixedSize(28, 24)
        if danger:
            b.setStyleSheet("background: #c62828; color: white;")
        b.clicked.connect(slot)
        return b

    def _refresh_config_cards(self):
        while self.cfg_layout.count():
            item = self.cfg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._icon_refs = []
        for i, item in enumerate(self.items):
            is_link = item.get("type") == "link"
            card = QWidget()
            card.setStyleSheet(
                "QWidget { background: #1a3a2a; border-radius: 6px; }" if is_link
                else "QWidget { background: #1a1a3e; border-radius: 6px; }")
            h = QHBoxLayout(card)
            h.setContentsMargins(8, 3, 4, 3)
            h.setSpacing(3)

            drag_lbl = QLabel("⋮⋮")
            drag_lbl.setStyleSheet("color: #666; background: transparent; border: none;")
            drag_lbl.setFixedWidth(20)
            h.addWidget(drag_lbl)

            # 名称输入（实时同步）
            name_entry = QLineEdit(item.get("name", ""))
            name_entry.setFixedWidth(120)
            name_entry.setPlaceholderText("名称")
            name_entry.textChanged.connect(
                lambda t, idx=i: self._on_item_change(idx, "name", t))
            h.addWidget(name_entry)

            # 图标预览（小）
            icon_path = item.get("icon", "")
            icon_lbl = QLabel("🖼")
            icon_lbl.setFixedSize(28, 24)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("background: transparent; border: none;")
            if icon_path and os.path.isfile(icon_path):
                pm = QPixmap(icon_path)
                if not pm.isNull():
                    pm = pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    icon_lbl.setPixmap(pm)
                    self._icon_refs.append(pm)
            h.addWidget(icon_lbl)

            # 路径输入（实时同步）
            path_entry = QLineEdit(item.get("path", ""))
            path_entry.setPlaceholderText("http/https 链接" if is_link else "程序路径")
            path_entry.textChanged.connect(
                lambda t, idx=i: self._on_item_change(idx, "path", t))
            h.addWidget(path_entry, 1)

            # 浏览按钮（仅软件项显示）
            if not is_link:
                h.addWidget(self._mini_btn(
                    "📁", lambda _c=False, e=path_entry, idx=i: self._browse_file(e, idx)))
            # 图标选择
            h.addWidget(self._mini_btn(
                "🖼", lambda _c=False, idx=i: self._browse_icon(idx)))
            # 上移 / 下移 / 删除
            h.addWidget(self._mini_btn(
                "⬆", lambda _c=False, idx=i: self._move_launcher_item(idx, -1)))
            h.addWidget(self._mini_btn(
                "⬇", lambda _c=False, idx=i: self._move_launcher_item(idx, 1)))
            h.addWidget(self._mini_btn(
                "✕", lambda _c=False, idx=i: self._delete_launcher_item(idx), danger=True))

            self.cfg_layout.addWidget(card)

    def _on_item_change(self, idx, key, value):
        if 0 <= idx < len(self.items):
            self.items[idx][key] = value

    def _browse_file(self, entry, idx):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序", "", "可执行文件 (*.exe);;所有文件 (*.*)")
        if path and 0 <= idx < len(self.items):
            entry.setText(path)
            self.items[idx]["path"] = path

    def _browse_icon(self, idx):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图标", "", "图片 (*.png *.ico *.jpg);;所有文件 (*.*)")
        if path and 0 <= idx < len(self.items):
            self.items[idx]["icon"] = path
            self._refresh_config_cards()  # 刷新显示

    def _add_item(self):
        self.items.append({"name": "", "path": "", "icon": ""})
        self._refresh_config_cards()

    def _add_link(self):
        self.items.append({"name": "", "path": "", "icon": "", "type": "link"})
        self._refresh_config_cards()

    def _move_launcher_item(self, idx, direction):
        new_idx = idx + direction
        if 0 <= new_idx < len(self.items):
            self.items.insert(new_idx, self.items.pop(idx))
            self._refresh_config_cards()

    def _delete_launcher_item(self, idx):
        if 0 <= idx < len(self.items):
            self.items.pop(idx)
            self._refresh_config_cards()

    def _save_and_refresh(self):
        cfg = load_convert_config()
        cfg["launcher_items"] = self.items
        save_convert_config(cfg)
        self._refresh_config_cards()
        self._refresh_panel()
        self.app.toast("软件面板已更新", "success")
