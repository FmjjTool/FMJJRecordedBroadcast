"""腾讯文档 · 中间层同步器（页签面板 / 弹窗两种形态）。

只读比对本地「已获取」/增量数据库与已同步的腾讯表格，把尚未上传的条目
按「官方动态」列标准整理成表格，一键复制 TSV 后由用户手动粘贴上传。
不会写腾讯文档，也不会调用任何上传接口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui_qt import common
from gui_qt.common import run_bg
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.tcdoc import bridge

_DAYS = (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("全部历史", 0))
_DISPLAY_COLUMNS = list(bridge.COLUMNS) + ["图片"]
_WIDTHS = {
    "作品标题": 240, "发布距今": 90, "作品内容": 240, "发布账号": 110,
    "封面图": 80, "动态类型": 90, "发布时间": 140, "发布平台": 90,
    "发布位置": 80, "作品时长": 90, "作品链接": 220, "动态链接(b站)": 220,
    "图片": 90,
}


class TcDocBridgePanel(QWidget):
    def __init__(self, parent=None, app=None, prefer_doc_key: str = "",
                 embedded: bool = False):
        super().__init__(parent)
        self.app = app or getattr(parent, "app", None)
        self._embedded = embedded
        self._targets: list[dict] = []
        self._result: dict | None = None
        self._rows: list[dict] = []
        self._account_names: list[str] = []
        self._account_actions: dict[str, object] = {}
        self._busy = False
        self._image_busy = False
        self._image_temp_dir = None

        self._acc_timer = QTimer(self)
        self._acc_timer.setSingleShot(True)
        self._acc_timer.setInterval(250)
        self._acc_timer.timeout.connect(self._run_compare)

        self._build_ui()
        self._load_targets(prefer_doc_key)

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("目标表格"))
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(340)
        self.target_combo.setToolTip("已同步到本地的腾讯表格（自动识别含官方动态字段的工作表）")
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        top.addWidget(self.target_combo, 2)

        top.addWidget(QLabel("时间"))
        self.days_combo = QComboBox()
        for label, days in _DAYS:
            self.days_combo.addItem(label, days)
        self.days_combo.setFixedWidth(100)
        self.days_combo.currentIndexChanged.connect(self._on_days_changed)
        top.addWidget(self.days_combo)

        top.addWidget(QLabel("账号"))
        self.account_btn = QPushButton("全部")
        self.account_btn.setMinimumWidth(130)
        self.account_btn.setToolTip("勾选要比对的发布账号；默认只比对腾讯表出现过的账号")
        self._account_menu = QMenu(self.account_btn)
        self.account_btn.setMenu(self._account_menu)
        top.addWidget(self.account_btn)

        self.compare_btn = QPushButton("🔄 重新比对")
        self.compare_btn.clicked.connect(self._run_compare)
        top.addWidget(self.compare_btn)
        top.addStretch(1)

        self.copy_btn = QPushButton("📋 复制全部（TSV）")
        self.copy_btn.setProperty("variant", "primary")
        self.copy_btn.setToolTip("复制全部待同步行（不含表头），到腾讯文档表格末尾粘贴")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        top.addWidget(self.copy_btn)
        layout.addLayout(top)

        self.summary = QLabel("加载中…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600; color: #1e3a8a;")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(_DISPLAY_COLUMNS))
        self.table.setHorizontalHeaderLabels(_DISPLAY_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, name in enumerate(_DISPLAY_COLUMNS):
            self.table.setColumnWidth(index, _WIDTHS.get(name, 100))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.itemSelectionChanged.connect(self._update_copy_row_btn)
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self._copy_shortcut.activated.connect(self._copy_selected_rows)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("选中行可单行复制，右键行也有复制菜单；不会自动上传。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #888;")
        bottom.addWidget(self.status, 1)
        self.copy_images_btn = QPushButton("🖼 复制全部图片")
        self.copy_images_btn.setToolTip(
            "把检测到的图片下载后复制到剪贴板，可直接粘贴到腾讯文档"
            "（有多少张复制多少张，不参与 TSV 粘贴）")
        self.copy_images_btn.setEnabled(False)
        self.copy_images_btn.clicked.connect(self._copy_all_images)
        bottom.addWidget(self.copy_images_btn)
        self.copy_row_btn = QPushButton("📋 复制选中行")
        self.copy_row_btn.setToolTip("复制表格里选中的行（Ctrl+C 同样可用，可多选），不含表头")
        self.copy_row_btn.setEnabled(False)
        self.copy_row_btn.clicked.connect(self._copy_selected_rows)
        bottom.addWidget(self.copy_row_btn)
        if not self._embedded:
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(self._close)
            bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _close(self) -> None:
        window = self.window()
        accept = getattr(window, "accept", None)
        if callable(accept):
            accept()

    # ==================== 目标 / 配置 ====================

    def set_doc_key(self, doc_key: str) -> None:
        """页签模式下跟随腾讯文档页当前选中的文档。"""
        if not doc_key:
            return
        for index in range(self.target_combo.count()):
            data = self.target_combo.itemData(index)
            if data and data.get("doc_key") == doc_key:
                if index != self.target_combo.currentIndex():
                    self.target_combo.setCurrentIndex(index)
                return

    def refresh(self, reload_targets: bool = False) -> None:
        """手动/切回页签时重新比对；reload_targets=True 时同时刷新表格清单。"""
        if reload_targets:
            self._load_targets(self._preferred_doc_key())
            return
        self._run_compare()

    def _preferred_doc_key(self) -> str:
        data = self.target_combo.currentData()
        if data:
            return str(data.get("doc_key") or "")
        try:
            return str(load_convert_config().get("tcdoc_bridge_doc_key") or "")
        except Exception:  # noqa: BLE001 - 配置读取失败时退回自动选择
            return ""

    def _load_targets(self, prefer_doc_key: str = "") -> None:
        self._targets = bridge.list_targets()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target in self._targets:
            self.target_combo.addItem(
                f"{target['doc_name']} · {target['tab_name']}（{target['n_rows']} 行）",
                target)
        self.target_combo.blockSignals(False)

        if not self._targets:
            self.summary.setText(
                "本地还没有可用的表格数据：请先在「📋 表格同步」页签同步一次表格。")
            self.compare_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            return

        cfg = load_convert_config()
        doc_key = str(cfg.get("tcdoc_bridge_doc_key") or "")
        tab_id = str(cfg.get("tcdoc_bridge_tab_id") or "")
        index = next((i for i, target in enumerate(self._targets)
                      if target["doc_key"] == doc_key and target["tab_id"] == tab_id), -1)
        if index < 0 and prefer_doc_key:
            index = next((i for i, target in enumerate(self._targets)
                          if target["doc_key"] == prefer_doc_key), 0)
        if index < 0:
            index = 0
        self.target_combo.setCurrentIndex(index)

        try:
            days = int(cfg.get("tcdoc_bridge_days", 30) or 30)
        except (TypeError, ValueError):
            days = 30
        days_index = self.days_combo.findData(days)
        self.days_combo.blockSignals(True)
        self.days_combo.setCurrentIndex(days_index if days_index >= 0 else 1)
        self.days_combo.blockSignals(False)

        self._on_target_changed()

    def _save_cfg(self, **kwargs) -> None:
        try:
            cfg = load_convert_config()
            cfg.update(kwargs)
            save_convert_config(cfg)
        except Exception:  # noqa: BLE001 - 配置保存失败不影响本次比对
            pass

    def _on_target_changed(self, *_args) -> None:
        target = self.target_combo.currentData()
        if target:
            self._save_cfg(tcdoc_bridge_doc_key=target["doc_key"],
                           tcdoc_bridge_tab_id=target["tab_id"])
        self._run_compare()

    def _on_days_changed(self, *_args) -> None:
        self._save_cfg(tcdoc_bridge_days=int(self.days_combo.currentData() or 0))
        self._run_compare()

    # ==================== 账号 ====================

    def _rebuild_account_menu(self, names: list[str], checked: set[str]) -> None:
        self._account_names = list(names)
        self._account_menu.clear()
        self._account_actions = {}
        for name in names:
            action = self._account_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name in checked)
            action.toggled.connect(self._on_account_toggled)
            self._account_actions[name] = action
        if names:
            self._account_menu.addSeparator()
            all_action = self._account_menu.addAction("全选")
            all_action.triggered.connect(lambda: self._set_all_accounts(True))
            none_action = self._account_menu.addAction("全不选")
            none_action.triggered.connect(lambda: self._set_all_accounts(False))
        self._update_account_btn()

    def _checked_accounts(self) -> list[str]:
        return [name for name, action in self._account_actions.items()
                if action.isChecked()]

    def _set_all_accounts(self, checked: bool) -> None:
        for action in self._account_actions.values():
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        self._after_account_change()

    def _on_account_toggled(self, _checked: bool) -> None:
        self._after_account_change()

    def _after_account_change(self) -> None:
        self._update_account_btn()
        self._save_cfg(tcdoc_bridge_accounts=self._checked_accounts())
        self._acc_timer.start()

    def _update_account_btn(self) -> None:
        checked = len(self._checked_accounts())
        total = len(self._account_names)
        if not total or checked in (0, total):
            self.account_btn.setText("全部")
        else:
            self.account_btn.setText(f"已选 {checked}/{total}")

    def _selected_accounts(self):
        if not self._account_actions:
            return "default"
        checked = self._checked_accounts()
        return checked or None  # 全部取消勾选 = 不过滤账号

    # ==================== 比对 ====================

    def _run_compare(self, *_args) -> None:
        if self._busy:
            return
        target = self.target_combo.currentData()
        if not target:
            return
        self._busy = True
        self.compare_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.summary.setText("⏳ 正在比对本地数据与腾讯表格…")
        self.status.setText("本地已获取 + 增量数据库合并去重后比对，不会修改腾讯文档。")

        days = int(self.days_combo.currentData() or 0)
        accounts = self._selected_accounts()
        doc_key, tab_id = target["doc_key"], target["tab_id"]
        collected = list(common.COLLECTED)  # 主线程快照，避免采集线程并发追加

        def work():
            return bridge.build_pending(doc_key, tab_id, days=days,
                                        accounts=accounts, collected=collected)

        def done(result, err):
            self._busy = False
            self.compare_btn.setEnabled(True)
            if err:
                self.summary.setText(f"❌ 比对失败：{err}")
                return
            self._result = result
            self._fill_table(result["rows"])
            stats = result["stats"]
            scope = stats["since"] or "全部历史"
            image_note = ""
            if stats["pending"]:
                image_note = (f"（有图片 {stats['with_images']} 行 / "
                              f"{stats['image_count']} 张，无图片 {stats['without_images']} 行）")
            self.summary.setText(
                f"本地 {scope} 共 {stats['local_items']} 条 → 合并 {stats['local_groups']} 个作品；"
                f"已同步 {stats['synced_groups']} 个，待同步 {stats['pending']} 行{image_note}")
            self.copy_btn.setEnabled(bool(result["rows"]))
            self.copy_images_btn.setEnabled(stats["image_count"] > 0)
            self._refresh_accounts(result)
            if result["rows"]:
                self.status.setText(
                    f"📋 共 {len(result['rows'])} 行待同步（不含表头；"
                    "发布距今/封面图留空占位）；选中行可单行复制，右键行也有复制菜单，"
                    "图片可点「复制全部图片」。")
            else:
                self.status.setText(
                    "✅ 没有待同步条目；如果刚上传过，请先同步一次腾讯表格再比对。")

        run_bg(work, done)

    def _refresh_accounts(self, result: dict) -> None:
        names = sorted(set(result.get("accounts") or []) |
                       set(result.get("local_accounts") or []))
        if names == self._account_names:
            return
        previous = set(self._checked_accounts())
        saved = set(load_convert_config().get("tcdoc_bridge_accounts") or [])
        default_checked = (set(result.get("accounts") or [])
                           & set(result.get("local_accounts") or []))
        checked = previous or (saved & set(names)) or default_checked or set(result.get("accounts") or [])
        self._rebuild_account_menu(names, checked)
        if previous or saved:
            # 首次打开时如果配置里已有账号勾选，按新勾选再算一次
            self._acc_timer.start()

    def _fill_table(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self.table.clearSelection()
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, column in enumerate(_DISPLAY_COLUMNS):
                    if column == "图片":
                        images = row.get("_images") or []
                        text = f"有 {len(images)} 张" if images else "无"
                        item = QTableWidgetItem(text)
                        tip = "\n".join(images[:20])
                        if len(images) > 20:
                            tip += "\n…"
                        item.setToolTip(tip)
                    else:
                        text = str(row.get(column) or "")
                        item = QTableWidgetItem(text)
                        item.setToolTip(text[:600])
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self._update_copy_row_btn()
        self.copy_images_btn.setEnabled(
            any(row.get("_images") for row in self._rows))

    # ==================== 复制 ====================

    def _selected_rows(self) -> list[dict]:
        model = self.table.selectionModel()
        if model is None:
            return []
        indexes = sorted(model.selectedRows(), key=lambda item: item.row())
        return [self._rows[index.row()] for index in indexes
                if 0 <= index.row() < len(self._rows)]

    def _update_copy_row_btn(self) -> None:
        button = getattr(self, "copy_row_btn", None)
        if button is not None:
            button.setEnabled(bool(self._selected_rows()))

    def _copy(self) -> None:
        rows = (self._result or {}).get("rows") or []
        if not rows:
            return
        self._copy_rows(rows, f"全部 {len(rows)} 行")

    def _copy_all_images(self) -> None:
        urls: list[str] = []
        for row in self._rows:
            for url in row.get("_images") or []:
                if url not in urls:
                    urls.append(url)
        self._prepare_and_copy_images(urls, "全部行")

    def _copy_row_images(self, row: dict, row_index: int) -> None:
        self._prepare_and_copy_images(row.get("_images") or [], f"第 {row_index + 1} 行")

    def _prepare_and_copy_images(self, urls: list[str], label: str) -> None:
        """把图片下载到临时目录后按文件列表写入剪贴板，可直接粘贴到腾讯文档。"""
        urls = [str(url).strip() for url in urls if str(url).strip()]
        if not urls:
            self.status.setText("没有检测到可复制的图片")
            return
        if self._image_busy:
            return
        self._image_busy = True
        self.copy_images_btn.setEnabled(False)
        self.status.setText(f"⏳ 正在准备{label}的 {len(urls)} 张图片…")

        def work():
            return self._download_images(urls)

        def done(result, err):
            self._image_busy = False
            self.copy_images_btn.setEnabled(
                any(row.get("_images") for row in self._rows))
            if err:
                self.status.setText(f"❌ 图片处理失败：{err}")
                return
            paths, failed = result or ([], 0)
            if not paths:
                self.status.setText("❌ 图片下载失败，未能复制")
                return
            self._set_image_clipboard(paths)
            note = f"，失败 {failed} 张" if failed else ""
            self.status.setText(
                f"✅ 已复制{label} {len(paths)} 张图片内容，可直接到腾讯文档粘贴{note}")
            if self.app is not None:
                try:
                    self.app.toast(f"✅ 已复制 {len(paths)} 张图片", "success")
                except Exception:  # noqa: BLE001 - 提示失败不影响复制
                    pass

        run_bg(work, done)

    def _download_images(self, urls: list[str]):
        """并发下载图片到临时目录，返回 (路径列表, 失败数)。"""
        import mimetypes
        import shutil
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime
        from pathlib import Path

        import httpx

        if self._image_temp_dir is not None:
            shutil.rmtree(self._image_temp_dir, ignore_errors=True)
        root = (Path(tempfile.gettempdir()) / "fmjj_tcdoc_images"
                / datetime.now().strftime("%Y%m%d_%H%M%S"))
        root.mkdir(parents=True, exist_ok=True)
        self._image_temp_dir = root

        def referer(url: str) -> str:
            host = url.lower()
            for key, ref in (("hdslb", "https://www.bilibili.com/"),
                             ("bilibili", "https://www.bilibili.com/"),
                             ("sinaimg", "https://weibo.com/"),
                             ("weibo", "https://weibo.com/"),
                             ("douyin", "https://www.douyin.com/"),
                             ("kuaishou", "https://www.kuaishou.com/"),
                             ("xiaohongshu", "https://www.xiaohongshu.com/"),
                             ("xhscdn", "https://www.xiaohongshu.com/")):
                if key in host:
                    return ref
            return "https://www.bilibili.com/"

        def fetch(item):
            index, url = item
            try:
                response = httpx.get(
                    url, timeout=20, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": referer(url)})
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                ext = mimetypes.guess_extension(content_type) or Path(url.split("?")[0]).suffix
                if not ext or len(ext) > 6:
                    ext = ".jpg"
                if ext.lower() in (".jpe", ".jpeg"):
                    ext = ".jpg"
                target = root / f"{index:03d}{ext.lower()}"
                target.write_bytes(response.content)
                return str(target)
            except Exception:  # noqa: BLE001 - 单张失败不影响其余图片
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, enumerate(urls, 1)))
        paths = [path for path in results if path]
        return paths, len(urls) - len(paths)

    def _set_image_clipboard(self, paths: list[str]) -> None:
        """文件列表写剪贴板；单张图片再附带图片数据，兼容只接收图片的目标。"""
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QImage

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        if len(paths) == 1:
            image = QImage(paths[0])
            if not image.isNull():
                mime.setImageData(image)
        QApplication.clipboard().setMimeData(mime)

    def _copy_selected_rows(self, *_args) -> None:
        rows = self._selected_rows()
        if not rows:
            self.status.setText("请先在表格里选中一行（可按住 Ctrl 多选）")
            return
        label = (f"第 {self.table.currentRow() + 1} 行" if len(rows) == 1
                 else f"选中的 {len(rows)} 行")
        self._copy_rows(rows, label)

    def _copy_rows(self, rows: list[dict], label: str) -> None:
        if not rows:
            return
        QApplication.clipboard().setText(bridge.to_tsv(rows))
        self.status.setText(f"✅ 已复制{label}（不含表头），去腾讯文档粘贴即可。")
        if self.app is not None:
            try:
                self.app.toast(f"✅ 已复制{label}待同步内容", "success")
            except Exception:  # noqa: BLE001 - 提示失败不影响复制
                pass

    def _copy_text(self, text: str, label: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status.setText(f"✅ 已复制{label}")
        if self.app is not None:
            try:
                self.app.toast(f"✅ 已复制{label}", "success")
            except Exception:  # noqa: BLE001
                pass

    def _table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row_index = index.row()
        if not (0 <= row_index < len(self._rows)):
            return
        self.table.selectRow(row_index)
        row = self._rows[row_index]
        page = str(row.get("作品链接") or "").strip()
        dyn = str(row.get("动态链接(b站)") or "").strip()

        menu = QMenu(self.table)
        act_row = menu.addAction("📋 复制该行（TSV）")
        images = row.get("_images") or []
        act_images = menu.addAction("🖼 复制该行图片")
        act_images.setEnabled(bool(images))
        act_page = menu.addAction("🔗 复制作品链接")
        act_page.setEnabled(bool(page or dyn))
        act_dyn = None
        if dyn:
            act_dyn = menu.addAction("🔗 复制动态链接(b站)")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_row:
            self._copy_rows([row], f"第 {row_index + 1} 行")
        elif chosen == act_images:
            self._copy_row_images(row, row_index)
        elif chosen == act_page:
            if page:
                self._copy_text(page, "作品链接")
            elif dyn:
                self._copy_text(dyn, "动态链接")
        elif act_dyn is not None and chosen == act_dyn:
            self._copy_text(dyn, "动态链接")


class TcDocBridgeDialog(QDialog):
    """独立弹窗形态（已获取页入口等），内容复用内嵌面板。"""

    def __init__(self, parent, app=None, prefer_doc_key: str = ""):
        super().__init__(parent)
        self.setWindowTitle("🧩 腾讯文档 · 中间层同步器")
        self.resize(1180, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = TcDocBridgePanel(self, app=app, prefer_doc_key=prefer_doc_key)
        layout.addWidget(self.panel)
