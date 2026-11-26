# -*- coding: utf-8 -*-
"""表格表头点击排序（全局唯一实现）。

约定（convention-skill 约定 14）：需要排序的表格必须支持「点击表头切换 升序/降序」，
不得只靠下拉筛选或分页找数据。新表格一律调用本模块，不要各页自写排序逻辑。

两种接入方式：

1）``install(table)`` —— 通用模式：直接按解析后的排序键重排表格行（连同单元格控件）。
   用于「行号 == 数据列表下标」不成立的表格，即页面只用 ``item.data(Qt.UserRole)``
   或按钮闭包取记录的表。

2）``install(table, rows=self._rows)`` —— 记录模式：重排行内条目的同时，把页面自己的
   记录列表按同一顺序在原位重排，保证 ``row()`` / ``currentRow()`` 仍能定位到正确记录。
   页面重新填充表格后调用一次 ``sorter.reapply()`` 即可保留上次排序。

排序键解析：数字（支持千分位、万/亿/k/w 后缀）< 日期时间 < 文本 < 空值，
空值（``""`` / ``-`` / ``—``）无论升降序都排在最后。单元格可用
``item.setData(SORT_KEY_ROLE, 数值)`` 指定自定义键（例如时长 ``01:02:03``）。
"""

from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget

# 单元格自定义排序键（整数/浮点/日期字符串均可，优先于显示文本）
SORT_KEY_ROLE = int(Qt.UserRole) + 120

_EMPTY = (9, 0)          # 空值哨兵：恒排最后
_NUM_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_UNIT = (("亿", 1e8), ("万", 1e4), ("w", 1e4), ("W", 1e4),
         ("千", 1e3), ("k", 1e3), ("K", 1e3))
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d", "%Y年%m月%d日", "%m-%d %H:%M", "%H:%M:%S", "%H:%M",
)
_STRIP_SUFFIX = ("次播放", "分钟/段", "MB/段", "GB/段", "分钟", "小时",
                 "播放", "次", "人", "条", "个", "秒", "分", "%",
                 "kbps", "kHz", "MB", "GB", "KB", "元", "张", "字", "首")


def _as_number(text: str):
    t = text.replace(",", "").replace("，", "").replace(" ", "").strip()
    for suffix in _STRIP_SUFFIX:
        if t.endswith(suffix) and len(t) > len(suffix):
            t = t[: -len(suffix)]
            break
    mult = 1.0
    for unit, factor in _UNIT:
        if t.endswith(unit) and len(t) > len(unit):
            t, mult = t[: -len(unit)], factor
            break
    if not _NUM_RE.match(t):
        return None
    try:
        return float(t) * mult
    except ValueError:
        return None


def _as_datetime(text: str):
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def sort_key(text) -> tuple:
    """显示文本 → 可比较排序键。空值返回 ``_EMPTY``。"""
    if text is None:
        return _EMPTY
    s = str(text).strip()
    if not s or s in {"-", "—", "--", "N/A", "n/a", "无", "未知"}:
        return _EMPTY
    num = _as_number(s)
    if num is not None:
        return (0, num)
    dt = _as_datetime(s)
    if dt is not None:
        return (1, dt.timestamp())
    return (2, s.casefold())


def sort_records(records, key_func, ascending: bool = True) -> list:
    """按 ``key_func(record)`` 排序记录列表；空值恒排最后。返回新列表。"""
    filled, empty = [], []
    for rec in records:
        k = key_func(rec)
        (empty if k == _EMPTY else filled).append(rec)
    filled.sort(key=key_func, reverse=not ascending)
    return filled + empty


def _item_key(table: QTableWidget, row: int, col: int) -> tuple:
    item = table.item(row, col)
    if item is None:
        return _EMPTY
    raw = item.data(SORT_KEY_ROLE)
    if raw is not None:
        try:
            return (0, float(raw))
        except (TypeError, ValueError):
            return sort_key(raw)
    return sort_key(item.text())


def _reorder(table: QTableWidget, order: list) -> None:
    """按 order（旧行号序列）原地重排表格行，保留单元格项、单元格控件、竖表头与行高。"""
    cols = table.columnCount()
    snapshot = []
    for src in order:
        cells = []
        for c in range(cols):
            item = table.takeItem(src, c)
            widget = table.cellWidget(src, c)
            if widget is not None:
                table.removeCellWidget(src, c)
            cells.append((item, widget))
        snapshot.append((cells, table.takeVerticalHeaderItem(src), table.rowHeight(src)))
    table.setRowCount(0)
    table.setRowCount(len(snapshot))
    for row, (cells, head_item, height) in enumerate(snapshot):
        for col, (item, widget) in enumerate(cells):
            if item is not None:
                table.setItem(row, col, item)
            if widget is not None:
                table.setCellWidget(row, col, widget)
        if head_item is not None:
            table.setVerticalHeaderItem(row, head_item)
        if height > 0:
            table.setRowHeight(row, height)


class HeaderSort:
    """表头点击状态机：只管「第几列、升还是降」，排序动作交给子类或回调。"""

    def __init__(self, table: QTableWidget, skip_columns=(0,), *, tri_state=False):
        self.table = table
        self.skip_columns = set(skip_columns or ())
        self.col = -1
        self.ascending = True
        self._busy = False
        self.tri_state = bool(tri_state)

        table.setSortingEnabled(False)      # 分页/记录列表由本模块统一处理
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._on_header_clicked)

    def _on_header_clicked(self, col: int) -> None:
        if col in self.skip_columns or self._busy:
            return
        if col == self.col:
            if self.tri_state and not self.ascending:
                self.col, self.ascending = -1, True
            else:
                self.ascending = not self.ascending
        else:
            self.col, self.ascending = col, True
        self._update_indicator()
        self.apply()

    def _update_indicator(self) -> None:
        header = self.table.horizontalHeader()
        header.setSortIndicatorShown(self.col >= 0)
        if self.col >= 0:
            header.setSortIndicator(
                self.col, Qt.AscendingOrder if self.ascending else Qt.DescendingOrder)

    def apply(self) -> None:
        raise NotImplementedError

    def reapply(self) -> None:
        if self.col >= 0 or self.tri_state:
            self.apply()


class CallbackSort(HeaderSort):
    """排序动作交给页面回调，用于数据库分页查询（排序在 SQL 里做）。"""

    def __init__(self, table, on_sort, skip_columns=(0,), *, tri_state=False):
        super().__init__(table, skip_columns=skip_columns, tri_state=tri_state)
        self._on_sort = on_sort

    def apply(self) -> None:
        if self.col < 0 and not self.tri_state:
            return
        self._on_sort(self.col, self.ascending)


class TableSort(HeaderSort):
    """在当前页内重排 QTableWidget 行；传入 rows 时同步重排页面的记录列表。"""

    def __init__(self, table: QTableWidget, rows=None, skip_columns=(0,)):
        super().__init__(table, skip_columns=skip_columns)
        self.rows = rows            # 页面记录列表（行号与其下标一致时传入）

    def apply(self) -> None:
        if self.col < 0 or self._busy:
            return
        self._busy = True
        try:
            self._sort_rows()
        finally:
            self._busy = False

    def attach(self, rows):
        """页面重新填充前绑定当前记录列表（列表被整体重新赋值时用）。"""
        self.rows = rows
        return self

    def _sort_rows(self) -> None:
        n = self.table.rowCount()
        if n < 2:
            return
        keys = [_item_key(self.table, r, self.col) for r in range(n)]
        filled = [i for i in range(n) if keys[i] != _EMPTY]
        empty = [i for i in range(n) if keys[i] == _EMPTY]
        filled.sort(key=lambda i: keys[i], reverse=not self.ascending)
        order = filled + empty
        if order == list(range(n)):
            return
        _reorder(self.table, order)
        if self.rows is not None and len(self.rows) == n:
            self.rows[:] = [self.rows[i] for i in order]


def install(table: QTableWidget, rows=None, skip_columns=(0,)) -> TableSort:
    """给表格装上「页内重排」式表头排序（返回排序器，重新填充后调 reapply）。"""
    sorter = getattr(table, "_table_sorter", None)
    if isinstance(sorter, TableSort):
        return sorter
    sorter = TableSort(table, rows=rows, skip_columns=skip_columns)
    table._table_sorter = sorter
    return sorter


def install_callback(table: QTableWidget, on_sort, skip_columns=(0,), *,
                     tri_state=False) -> CallbackSort:
    """安装回调排序；tri_state=True 时第三次点击回调 (-1, True) 恢复默认。"""
    sorter = getattr(table, "_table_sorter", None)
    if isinstance(sorter, CallbackSort):
        return sorter
    sorter = CallbackSort(table, on_sort, skip_columns=skip_columns,
                          tri_state=tri_state)
    table._table_sorter = sorter
    return sorter

