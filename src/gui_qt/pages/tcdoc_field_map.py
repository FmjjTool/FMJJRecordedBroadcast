"""腾讯文档写回 · 字段映射窗口（非模态）。

左边是腾讯表的列（固定不改），右边选这一列用本地哪个字段的值：
程序字段（如 作品标题）或本地原始 JSON 字段（@title、@cover_url）。
保存到 config.json 的 convert.tcdoc_field_maps，保存后立即生效。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from toolbox.tcdoc import field_maps

_NOT_WRITTEN = "（不写）"
_WRITABLE_TYPES = {1, 4, 8, 9, 17}   # 文本 / 日期 / 链接 / 单选 / 多选
_TYPE_NAMES = {1: "文本", 4: "日期", 8: "链接", 9: "单选", 17: "多选"}


def collect_raw_keys(rows, limit: int = 60) -> list[str]:
    """从待同步行的 _raw 里收集可用的本地 JSON 字段名（顶层 + 一层嵌套标量）。"""
    keys: dict[str, None] = {}
    for row in list(rows or [])[:200]:
        raw = row.get("_raw") if isinstance(row, dict) else None
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            key = str(key or "").strip()
            if not key:
                continue
            if isinstance(value, dict):
                for sub, sub_value in value.items():
                    if isinstance(sub_value, (str, int, float)):
                        keys.setdefault(f"{key}.{sub}", None)
            elif isinstance(value, list):
                if value and all(isinstance(v, (str, int, float)) for v in value):
                    keys.setdefault(key, None)
            else:
                keys.setdefault(key, None)
            if len(keys) >= limit:
                return list(keys)
    return list(keys)


class FieldMapDialog(QDialog):
    """腾讯列（固定） ← 本地字段（可改）；按当前表保存，其它表不动。"""

    def __init__(self, parent, target: dict, rows=None, on_saved=None):
        super().__init__(parent)
        self._target = dict(target or {})
        self._on_saved = on_saved
        self._doc_key = str(self._target.get("doc_key") or "")
        self._tab_id = str(self._target.get("tab_id") or "")
        self._profile = str(self._target.get("profile") or "posts")
        self._columns = [(str(col.get("name") or ""), int(col.get("type") or 1))
                         for col in self._target.get("columns") or []
                         if str(col.get("name") or "")]
        self._program_fields = list(field_maps.INTERNAL_FIELDS.get(
            self._profile, field_maps.INTERNAL_FIELDS["posts"]))
        self._raw_keys = collect_raw_keys(rows)
        self._rows: list[str] = []
        self._combos: dict[str, QComboBox] = {}
        self._defaults: dict[str, str] = {}

        self.setWindowTitle(f"字段映射 · {self._target.get('tab_name') or ''}")
        self.resize(720, 640)
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
            "右边是腾讯文档的列（固定不改），左边选这一列用本地哪个字段：\n"
            "程序字段 = 软件整理好的数据；JSON 字段 = 本地原始数据键（@title、@cover_url）。"
            "「（不写）」= 这一列不写。")
        tip.setWordWrap(True)
        tip.setProperty("role", "caption")
        layout.addWidget(tip)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["本地字段（可改）", "腾讯文档字段（固定）", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(1, 250)
        self.table.setColumnWidth(2, 80)
        layout.addWidget(self.table, 1)

        if not self._raw_keys:
            hint = QLabel("没有读到本地 JSON 字段：先在主界面比对一次，"
                          "再打开本窗口就能选 @ 开头的原始字段。")
            hint.setWordWrap(True)
            hint.setProperty("role", "caption")
            layout.addWidget(hint)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.setToolTip("把本表映射清空：腾讯列按同名程序字段写入，没有同名字段就不写")
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
        for column, kind in self._columns:
            if kind not in _WRITABLE_TYPES:
                continue   # 图片 / 公式 / 人员等列不在这里配
            row = len(self._rows)
            self.table.insertRow(row)
            self._rows.append(column)

            combo = QComboBox()
            combo.setMinimumWidth(300)
            combo.addItem(_NOT_WRITTEN, "")
            for name in self._program_fields:
                combo.addItem(f"程序字段：{name}", name)
            for key in self._raw_keys:
                combo.addItem(f"JSON 字段：@{key}", f"@{key}")
            wanted = current.get(column, column if column in self._program_fields else "")
            self._defaults[column] = (column if column in self._program_fields else "")
            if wanted and combo.findData(wanted) < 0:
                combo.addItem(f"{wanted}（已不可用）", wanted)
            position = combo.findData(wanted)
            combo.setCurrentIndex(position if position >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _index, col=column: self._mark(col))
            self.table.setCellWidget(row, 0, combo)
            self._combos[column] = combo

            type_name = _TYPE_NAMES.get(kind, "")
            self.table.setItem(row, 1, QTableWidgetItem(
                f"{column}（{type_name}）" if type_name else column))
            self._state_cell(row, column, wanted)
        self._refresh_status()

    def _state_cell(self, row: int, column: str, wanted: str) -> None:
        if wanted == self._defaults.get(column, ""):
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

    def _mark(self, column: str) -> None:
        wanted = str(self._combos[column].currentData() or "")
        self._state_cell(self._rows.index(column), column, wanted)
        self._refresh_status()

    def _pairs(self) -> dict[str, str]:
        pairs = {}
        for column, combo in self._combos.items():
            wanted = str(combo.currentData() or "")
            if wanted != self._defaults.get(column, ""):
                pairs[column] = wanted
        return pairs

    def _refresh_status(self) -> None:
        changed = self._pairs()
        if not changed:
            self.status.setText("当前全部使用默认对应；点「保存」会清空本表的映射。")
            return
        parts = [f"{column} ← {wanted or '不写'}"
                 for column, wanted in list(changed.items())[:8]]
        more = "…" if len(changed) > 8 else ""
        self.status.setText(
            f"本表将保存 {len(changed)} 条：" + "；".join(parts) + more)

    # ==================== 动作 ====================

    def _reset(self) -> None:
        for column, combo in self._combos.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(self._defaults.get(column, "")))
            combo.blockSignals(False)
            self._state_cell(self._rows.index(column), column,
                             self._defaults.get(column, ""))
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