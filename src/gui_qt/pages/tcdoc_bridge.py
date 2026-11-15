"""腾讯文档 · 中间层同步器弹窗。

只读比对本地「已获取」/增量数据库与已同步的腾讯表格，把尚未上传的条目
按「官方动态」列标准整理成表格，一键复制 TSV 后由用户手动粘贴上传。
不会写腾讯文档，也不会调用任何上传接口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from gui_qt import common
from gui_qt.common import run_bg
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.tcdoc import bridge

_DAYS = (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("全部历史", 0))
_WIDTHS = {
    "作品标题": 240, "发布距今": 90, "作品内容": 240, "发布账号": 110,
    "封面图": 80, "动态类型": 90, "发布时间": 100, "发布平台": 90,
    "发布位置": 80, "作品时长": 90, "作品链接": 220, "动态链接(b站)": 220,
}


class TcDocBridgeDialog(QDialog):
    def __init__(self, parent, app=None, prefer_doc_key: str = ""):
        super().__init__(parent)
        self.app = app or getattr(parent, "app", None)
        self.setWindowTitle("🧩 腾讯文档 · 中间层同步器")
        self.resize(1180, 720)
        self._targets: list[dict] = []
        self._result: dict | None = None
        self._account_names: list[str] = []
        self._account_actions: dict[str, object] = {}
        self._busy = False

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

        self.copy_btn = QPushButton("📋 一键复制（TSV）")
        self.copy_btn.setProperty("variant", "primary")
        self.copy_btn.setToolTip("复制待同步行（不含表头），到腾讯文档表格末尾粘贴")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        top.addWidget(self.copy_btn)
        layout.addLayout(top)

        self.summary = QLabel("加载中…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600; color: #1e3a8a;")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(bridge.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(bridge.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, name in enumerate(bridge.COLUMNS):
            self.table.setColumnWidth(index, _WIDTHS.get(name, 100))
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("复制后到腾讯文档表格末尾粘贴；不会自动上传。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #888;")
        bottom.addWidget(self.status, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # ==================== 目标 / 配置 ====================

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
                "本地还没有可用的表格数据：请先在「腾讯文档监控」页同步一次表格。")
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
            self.summary.setText(
                f"本地 {scope} 共 {stats['local_items']} 条 → 合并 {stats['local_groups']} 个作品；"
                f"已同步 {stats['synced_groups']} 个，待同步 {stats['pending']} 行")
            self.copy_btn.setEnabled(bool(result["rows"]))
            self._refresh_accounts(result)
            if result["rows"]:
                self.status.setText(
                    f"📋 将复制 {len(result['rows'])} 行（不含表头；"
                    "发布距今/封面图留空占位）；到腾讯文档表格末尾粘贴，不会自动上传。")
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
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, column in enumerate(bridge.COLUMNS):
                    text = str(row.get(column) or "")
                    item = QTableWidgetItem(text)
                    item.setToolTip(text[:600])
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)

    # ==================== 复制 ====================

    def _copy(self) -> None:
        rows = (self._result or {}).get("rows") or []
        if not rows:
            return
        QApplication.clipboard().setText(bridge.to_tsv(rows))
        self.status.setText(f"✅ 已复制 {len(rows)} 行（不含表头），去腾讯文档粘贴即可。")
        if self.app is not None:
            try:
                self.app.toast(f"✅ 已复制 {len(rows)} 行待同步内容", "success")
            except Exception:  # noqa: BLE001 - 提示失败不影响复制
                pass
