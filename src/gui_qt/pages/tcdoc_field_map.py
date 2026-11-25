"""腾讯文档写回 · 字段映射窗口（非模态）。

把程序内部字段对应到当前目标表的真实列名，保存到 config.json 的
convert.tcdoc_field_maps；保存后立即生效（映射读取按 config.json 文件戳缓存），
不需要改代码，也不需要重启。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from toolbox.tcdoc import field_maps

_NOT_WRITTEN = "（不写）"


class FieldMapDialog(QDialog):
    """内部字段 → 目标列名；按当前表保存，其它表不动。"""

    def __init__(self, parent, target: dict, on_saved=None):
        super().__init__(parent)
        self._target = dict(target or {})
        self._on_saved = on_saved
        self._doc_key = str(self._target.get("doc_key") or "")
        self._tab_id = str(self._target.get("tab_id") or "")
        self._profile = str(self._target.get("profile") or "posts")
        self._columns = [str(col.get("name") or "")
                         for col in self._target.get("columns") or []
                         if str(col.get("name") or "")]
        self._fields = list(field_maps.INTERNAL_FIELDS.get(
            self._profile, field_maps.INTERNAL_FIELDS["posts"]))
        self._combos: dict[str, QComboBox] = {}
        self._defaults: dict[str, str] = {}

        self.setWindowTitle(f"字段映射 · {self._target.get('tab_name') or ''}")
        self.resize(640, 620)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        title = QLabel(
            f"{self._target.get('doc_name') or ''} · "
            f"{self._target.get('tab_name') or ''}")
        title.setWordWrap(True)
        layout.addWidget(title)

        tip = QLabel(
            "左边是程序内部字段，右边选这张表里要写入的列。\n"
            "选「（不写）」= 该字段不写；保持同名 = 按默认列写。"
            "保存后立即生效，下次比对/上传按新列名走。")
        tip.setWordWrap(True)
        tip.setProperty("role", "caption")
        layout.addWidget(tip)

        self.table = QTableWidget(len(self._fields), 3, self)
        self.table.setHorizontalHeaderLabels(["内部字段", "写入目标列", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 280)
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.setToolTip("把本表映射清空，全部字段回到代码默认列")
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        save = QPushButton("保存")
        save.setProperty("variant", "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self._load()

    # ==================== 行 ====================

    def _load(self) -> None:
        current = field_maps.mapping(self._doc_key, self._tab_id)
        for index, name in enumerate(self._fields):
            self.table.setItem(index, 0, QTableWidgetItem(name))

            combo = QComboBox()
            combo.setMinimumWidth(240)
            combo.addItem(_NOT_WRITTEN, "")
            for column in self._columns:
                combo.addItem(column, column)
            wanted = current.get(name, name)
            self._defaults[name] = name
            if name not in self._columns:
                # 默认列本身在表里不存在：保留同名选项，避免一打开就被当成「不写」
                combo.addItem(f"默认：{name}（表里没有）", name)
            if wanted not in self._columns and wanted != name:
                # 已配置的列在表里已不存在：显示出来，方便改回或重选
                combo.addItem(f"{wanted}（表里没有）", wanted)
            position = combo.findData(wanted)
            combo.setCurrentIndex(position if position >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _index, field=name: self._mark(field))
            self.table.setCellWidget(index, 1, combo)
            self._combos[name] = combo
            self._state_cell(index, name, wanted)
        self._refresh_status()

    def _state_cell(self, row: int, name: str, wanted: str) -> None:
        if wanted == name:
            text = "默认"
        elif not wanted:
            text = "不写"
        else:
            text = "已改"
        item = self.table.item(row, 2)
        if item is None:
            self.table.setItem(row, 2, QTableWidgetItem(text))
        else:
            item.setText(text)

    def _mark(self, name: str) -> None:
        row = self._fields.index(name)
        combo = self._combos[name]
        wanted = str(combo.currentData() or "")
        if wanted == name:
            self._state_cell(row, name, name)
        else:
            self._state_cell(row, name, wanted)
        self._refresh_status()

    def _pairs(self) -> dict[str, str]:
        pairs = {}
        for name, combo in self._combos.items():
            wanted = str(combo.currentData() or "")
            if wanted != name:
                pairs[name] = wanted
        return pairs

    def _refresh_status(self) -> None:
        changed = self._pairs()
        if not changed:
            self.status.setText("当前全部使用默认列；点「保存」会清空本表的映射。")
            return
        parts = []
        for name, wanted in changed.items():
            parts.append(f"{name} → {wanted or '不写'}")
        self.status.setText("本表将保存 " + str(len(changed)) + " 条：" + "；".join(parts))

    # ==================== 动作 ====================

    def _reset(self) -> None:
        for name, combo in self._combos.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(name))
            combo.blockSignals(False)
            self._state_cell(self._fields.index(name), name, name)
        self.status.setText("已恢复默认（还没保存）。")

    def _save(self) -> None:
        pairs = self._pairs()
        try:
            field_maps.save_scope(self._doc_key, self._tab_id, pairs)
        except OSError as exc:
            self.status.setText(f"保存失败：{exc}")
            return
        self._refresh_status()
        self.status.setText(
            (f"已保存 {len(pairs)} 条映射，立即生效。" if pairs
             else "已清空本表映射，恢复默认列。") + " 可关闭窗口后点「重新比对」。")
        if callable(self._on_saved):
            try:
                self._on_saved()
            except Exception:  # noqa: BLE001 - 回调节点失败不影响已保存的配置
                pass