"""继续操作页（PySide6 版）：外部软件/链接启动器 4×3 分页面板 + 配置管理。

对应原版 gui/toolbox_pages.py LauncherPage：
- 功能 tab：4×3 面板（每页 12 项），超过 12 项自动分页——‹ › 按钮/页码指示/滚轮滑动翻页；
  点击启动；软件/链接区分底色；支持 150px 图标
- 设置 tab：每项 名称/路径 输入（实时同步）+ 图标预览与选择 + 上移/下移/删除
  + 每项独立「跟随进程树」勾选 + 添加软件 / 添加链接 / 保存并刷新面板
- 启动：http/https 链接 → 浏览器；文件 → os.startfile（不存在则警告）
- 跟随进程树：按每项勾选决定；勾选项启动后纳入 Job Object，本工具退出时整棵进程树一并关闭
"""

import os
import subprocess
import webbrowser

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from toolbox.config_manager import load_convert_config, save_convert_config

from gui_qt import common
from gui_qt.pages.base import BasePage
from gui_qt.process_tree import assign_job, create_kill_job, terminate_job

CARD_SOFT_QSS = (
    "QToolButton { background: #eceefb; border: 1px solid #c6ccef;"
    " border-radius: 16px; font-size: 12px; color: #3a3f6d; padding: 6px; }"
    "QToolButton:hover { background: #dfe3f8; }")
CARD_LINK_QSS = (
    "QToolButton { background: #e8f5ee; border: 1px solid #b7e0c8;"
    " border-radius: 16px; font-size: 12px; color: #1d5c3a; padding: 6px; }"
    "QToolButton:hover { background: #d4eddf; }")

# 图标浮动缩放区间：下限保证小窗口也清晰可见，上限防止大窗口溢出卡片
ICON_MIN, ICON_MAX = 88, 168


def _emoji_pixmap(ch: str) -> QPixmap:
    """无图标项兜底：把 emoji 画成大 pixmap 当图标用（原文字模式 emoji 只有字号大小）。"""
    pm = QPixmap(ICON_MIN, ICON_MIN)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    f = p.font()
    f.setPixelSize(ICON_MIN - 12)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, ch)
    p.end()
    return pm


class _CardButton(QToolButton):
    """启动器卡片：图标在上文字在下，图标尺寸随卡片大小浮动缩放（固定下限/上限）。

    QToolButton 默认水平 size policy 是 Fixed，卡片会被锁死在 sizeHint 宽度
    （无图标时塌缩到几十像素），必须显式 Expanding 才能撑满网格格子。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = None
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(150)
        self.setMinimumWidth(150)

    def set_icon_pixmap(self, pix: QPixmap):
        self._pix = pix
        self._apply_icon_size()

    def _apply_icon_size(self):
        if self._pix is None or self._pix.isNull():
            return
        side = max(ICON_MIN, min(ICON_MAX, min(self.width(), self.height()) - 64))
        self.setIcon(QIcon(self._pix.scaled(
            side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        self.setIconSize(QSize(side, side))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_icon_size()


class LauncherPage(BasePage):
    PAGE_SIZE = 12  # 每页 4×3

    def __init__(self, app):
        super().__init__(app)
        self.items = load_convert_config().get("launcher_items", [])
        # 已链接进程树的任务列表 [(job 或 None, proc 或 None), ...]
        self._linked = []
        self._page = 0  # 当前面板页码（0 起）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 进程树控制行（跟随与否改为每项单独勾选）
        tree_row = QHBoxLayout()
        hint = QLabel("跟随进程树为每项单独设置：在「设置」页配置行勾选，"
                      "勾选项启动后纳入进程树，本工具退出时一并关闭")
        hint.setStyleSheet("color: #5a6072; font-size: 11px;")
        hint.setWordWrap(True)
        tree_row.addWidget(hint, 1)
        self.stop_all_btn = QPushButton("⏹ 停止已链接软件")
        self.stop_all_btn.setStyleSheet("background: #c62828; color: white; padding: 4px 12px;")
        self.stop_all_btn.clicked.connect(self._stop_all_linked)
        tree_row.addWidget(self.stop_all_btn)
        self.linked_lbl = QLabel("", styleSheet="color: #5a6072; font-size: 11px;")
        tree_row.addWidget(self.linked_lbl)
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
        # 面板本体接滚轮：下滑=下一页，上滑=上一页（单页时无效）
        self._panel_host = w
        w.installEventFilter(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)

        # 分页导航行（单页时整体隐藏）
        self.page_nav = QWidget()
        nav = QHBoxLayout(self.page_nav)
        nav.setContentsMargins(2, 0, 2, 0)
        self.prev_btn = QPushButton("‹ 上一页")
        self.prev_btn.setFixedHeight(26)
        self.prev_btn.clicked.connect(lambda: self._goto_page(self._page - 1))
        nav.addWidget(self.prev_btn)
        nav.addStretch(1)
        self.page_lbl = QLabel("")
        self.page_lbl.setStyleSheet("color: #5a6072; font-size: 12px;")
        nav.addWidget(self.page_lbl)
        nav.addStretch(1)
        self.next_btn = QPushButton("下一页 ›")
        self.next_btn.setFixedHeight(26)
        self.next_btn.clicked.connect(lambda: self._goto_page(self._page + 1))
        nav.addWidget(self.next_btn)
        v.addWidget(self.page_nav)

        self.panel_grid = QGridLayout()
        self.panel_grid.setSpacing(8)
        v.addLayout(self.panel_grid, 1)
        return w

    def _page_count(self) -> int:
        return max(1, -(-len(self.items) // self.PAGE_SIZE))

    def _goto_page(self, page: int):
        """翻页（clamp 到有效范围），页码变化时刷新面板。"""
        p = max(0, min(page, self._page_count() - 1))
        if p != self._page:
            self._page = p
            self._refresh_panel()

    def eventFilter(self, watched, event):
        # 功能面板滚轮滑动翻页（wheel 事件从卡片逐级冒泡到面板本体）
        if watched is self._panel_host and event.type() == QEvent.Wheel:
            dy = event.angleDelta().y()
            if dy < 0:
                self._goto_page(self._page + 1)
            elif dy > 0:
                self._goto_page(self._page - 1)
            return True
        return super().eventFilter(watched, event)

    def _refresh_panel(self):
        while self.panel_grid.count():
            item = self.panel_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        pages = self._page_count()
        self._page = max(0, min(self._page, pages - 1))
        start = self._page * self.PAGE_SIZE
        shown = min(len(self.items) - start, self.PAGE_SIZE)
        for i in range(shown):
            abs_idx = start + i  # 绝对索引（跨页点击仍指向正确条目）
            item = self.items[abs_idx]
            row, col = divmod(i, 4)
            name = item.get("name", f"软件{abs_idx + 1}")
            icon = item.get("icon", "")
            is_link = item.get("type") == "link"

            cell = QFrame()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(2)

            # 卡片：图标在上文字在下、撑满格子，图标尺寸随卡片浮动（固定 88~168px 区间）
            card = _CardButton()
            card.setCursor(Qt.PointingHandCursor)
            card.setStyleSheet(CARD_LINK_QSS if is_link else CARD_SOFT_QSS)
            pix = None
            if icon and os.path.isfile(icon):
                pix = QPixmap(icon)
            if pix is None or pix.isNull():
                pix = _emoji_pixmap("🔗" if is_link else "📦")
            card.set_icon_pixmap(pix)
            card.setText(name)
            card.clicked.connect(lambda _c=False, idx=abs_idx: self._launch_item(idx))
            cv.addWidget(card, 1)

            self.panel_grid.addWidget(cell, row, col)
        # 补满空格子保持网格均匀
        for i in range(shown, self.PAGE_SIZE):
            row, col = divmod(i, 4)
            self.panel_grid.addWidget(QWidget(), row, col)
        # 行列均分拉伸：卡片撑满整个面板（原挤在顶部留大片空白）
        for r in range(3):
            self.panel_grid.setRowStretch(r, 1)
        for c in range(4):
            self.panel_grid.setColumnStretch(c, 1)
        # 分页导航：单页隐藏；多页显示页码并按边界禁用
        if pages > 1:
            self.page_nav.setVisible(True)
            self.page_lbl.setText(f"第 {self._page + 1} / {pages} 页 · 共 {len(self.items)} 项")
            self.prev_btn.setEnabled(self._page > 0)
            self.next_btn.setEnabled(self._page < pages - 1)
        else:
            self.page_nav.setVisible(False)

    def _set_item_linked(self, idx, state):
        if 0 <= idx < len(self.items):
            # stateChanged 载荷可能是 int 或 CheckState 枚举（PySide6 版本差异），统一取值
            val = getattr(state, "value", state)
            self.items[idx]["linked"] = bool(val)
            self._persist_items()

    def _persist_items(self):
        cfg = load_convert_config()
        cfg["launcher_items"] = self.items
        save_convert_config(cfg)

    def _launch_item(self, idx):
        if not (0 <= idx < len(self.items)):
            return
        item = self.items[idx]
        path = item.get("path", "")
        if not path:
            return
        linked = bool(item.get("linked", True))
        # 链接模式：http/https 链接直接跳浏览器（无进程树概念）
        if path.lower().startswith(("http://", "https://")):
            try:
                webbrowser.open(path)
            except Exception as e:
                self.app.toast(f"打开失败: {str(e)[:60]}", "error")
            return
        if not os.path.isfile(path):
            self.app.toast(f"文件不存在: {path}", "error")
            return
        if linked:
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
            self.app.toast(f"启动失败: {str(e)[:60]}", "error")
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
        self._stop_all_linked_silent()
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

    def _mini_btn(self, text, tip, slot, danger=False):
        b = QPushButton(text)
        b.setToolTip(tip)
        # 加大尺寸：原 34×26 在 1100 宽左右会挤成一团；
        # 这里保留原风格（白底圆角），但保证最小可视尺寸，文字更清晰
        b.setFixedSize(60, 28)
        b.setCursor(Qt.PointingHandCursor)
        if danger:
            b.setStyleSheet(
                common.icon_btn_style(True, "background: #fdecea; color: #c62828;"
                    " border: 1px solid #f2b8b5; border-radius: 4px; font-weight: bold;")
                + "QPushButton:hover { background: #f9d3d0; }")
        else:
            b.setStyleSheet(
                common.icon_btn_style(True, "background: #ffffff; color: #2f3440;"
                    " border: 1px solid #c3cad6; border-radius: 4px; font-size: 12px;")
                + "QPushButton:hover { background: #e9edf3; border-color: #7a8db0; }")
        b.clicked.connect(slot)
        return b

    def _refresh_config_cards(self):
        """设置 Tab 行：
        左侧 checkbox（🌲 跟随进程树） → 📁 浏览 → 📌（强标「高级」入口） →
        🆑 清空项配置 → ⬆ → ⬇ → ✕ 删除。
        按钮尺寸已放大，并把浏览按钮显式使用 📁 图标，避免与其他 💾 保存混淆。
        """
        while self.cfg_layout.count():
            item = self.cfg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._icon_refs = []
        for i, item in enumerate(self.items):
            is_link = item.get("type") == "link"
            card = QFrame()
            card.setObjectName("linkCard" if is_link else "softCard")
            # 用 objectName 限定样式，避免 QWidget 选择器污染子控件
            card.setStyleSheet(
                "QFrame#linkCard { background: #e8f5ee; border-radius: 6px; }"
                if is_link else
                "QFrame#softCard { background: #eef1f6; border-radius: 6px; }")
            h = QHBoxLayout(card)
            h.setContentsMargins(10, 6, 8, 6)
            h.setSpacing(6)

            drag_lbl = QLabel("⋮⋮")
            drag_lbl.setStyleSheet("color: #8a93a5; background: transparent; border: none;")
            drag_lbl.setFixedWidth(18)
            h.addWidget(drag_lbl)

            # 名称输入（实时同步）
            name_entry = QLineEdit(item.get("name", ""))
            name_entry.setFixedWidth(110)
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

            # 📁 浏览（路径右侧；放在操作按钮的最左，方便"对准路径"操作）
            if not is_link:
                h.addWidget(self._mini_btn(
                    "📁 浏览", "选择程序文件",
                    lambda _c=False, e=path_entry, idx=i: self._browse_file(e, idx)))
            # 第二位起图标依次：📌(跟随进程树) → 🆑 清空 → ⬆ → ⬇ → ✕
            if not is_link:
                cb = QCheckBox("📌")
                cb.setToolTip("跟随进程树：本工具退出时该项启动的软件整树一并关闭")
                cb.setChecked(item.get("linked", True))
                cb.stateChanged.connect(
                    lambda st, idx=i: self._set_item_linked(idx, st))
                h.addWidget(cb)
            h.addWidget(self._mini_btn(
                "🆑 清空", "清空该项的名称 / 路径 / 图标等配置",
                lambda _c=False, idx=i: self._clear_item(idx)))
            h.addWidget(self._mini_btn(
                "⬆ 上移", "与上一项交换位置",
                lambda _c=False, idx=i: self._move_launcher_item(idx, -1)))
            h.addWidget(self._mini_btn(
                "⬇ 下移", "与下一项交换位置",
                lambda _c=False, idx=i: self._move_launcher_item(idx, 1)))
            h.addWidget(self._mini_btn(
                "✕ 删除", "删除该项",
                lambda _c=False, idx=i: self._delete_launcher_item(idx), danger=True))

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

    def _clear_item(self, idx):
        """🆑 清空按钮：清空该项的 name/path/icon，linked 保留默认 True。

        不弹二次确认（外已有 ✕ 删除按钮做危险操作，避免两个高危动作堆叠）。
        清空后点「保存并刷新面板」立即生效。
        """
        if not (0 <= idx < len(self.items)):
            return
        self.items[idx]["name"] = ""
        self.items[idx]["path"] = ""
        self.items[idx]["icon"] = ""
        self._refresh_config_cards()
        self.app.toast("已清空该项配置（未保存，点底部「保存并刷新面板」生效）", "info")

    def _add_item(self):
        self.items.append({"name": "", "path": "", "icon": "", "linked": True})
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
        self._persist_items()
        self._refresh_config_cards()
        self._refresh_panel()
        self.app.toast("软件面板已更新", "success")
