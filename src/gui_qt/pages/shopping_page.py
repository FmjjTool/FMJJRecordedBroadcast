"""购物页（PySide6 版）：淘宝店铺商品 + 商品详情解析（交互对齐票务页）。

- 淘宝店铺链接 → 商品列表（店铺信息游客可获取；商品列表需淘宝 Cookie）
- 左列表选中商品 → 右侧立即显示商品简介（标题/价格/销量/优惠券 + 主图预览）
- 「获取选中商品详情」→ 主图 / 选购参数(SKU 价) / 参数信息 / 图文详情长图，
  图片自动下载到 download/淘宝商品/<商品ID>/{main,sku,desc}/
- 「导出单商品 Excel」→ 单商品 xlsx 五 sheet（商品信息/参数信息/选购参数/
  主图/图文详情），本地图片直接插入表格
- 「导出商品 JSON」→ 整店商品列表
- 淘宝商品链接（item.taobao.com/item.htm?id=xxx）→ 直接解析该商品详情
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui_qt.common import first_number, post_to_main, run_bg, sales_number, stable_sort
from gui_qt.pages.base import BasePage

import shopping as shopping_mod


class ShoppingPage(BasePage):
    # 表格两套列头（店铺模式含勾选列 / 商品详情模式）
    COLS_SHOP = ["选", "商品标题", "价格", "销量", "优惠券", "商品ID", "链接"]
    COLS_ITEM = ["SKU 选项", "价格", "库存"]

    # 输入框留空直接回车时默认解析的店铺
    DEFAULT_SHOP_URL = "https://f03rr9e3mrh0fdmsfil8w1sdcgp77kl.taobao.com/shop/view_shop.htm"

    def __init__(self, app):
        super().__init__(app)
        self._products = []          # 当前商品列表（ShopProduct，店铺模式）
        self._products_original = [] # 解析时的原始顺序（排序三态回退用）
        self._shop = None            # 最近一次店铺信息
        self._detail = None          # 当前获取的商品详情（TaobaoItemDetail）
        self._detail_cache = {}      # item_id → TaobaoItemDetail（本次列表已拉过的）
        self._detail_sku_imgs = []   # 详情模式各行预览图 URL（行号对齐）
        self._skus_original = []     # SKU 原始顺序（排序三态回退用）
        self._sku_imgs_original = [] # SKU 原始顺序对应的预览图
        self._preview_seq = 0        # 图片预览请求序号（防止旧图覆盖新图）
        self._filling = False        # 表格填充中（抑制勾选计数刷新）
        self._sort_col = None        # 当前排序列（None=原始顺序）
        self._sort_state = 0         # 0=原顺序 1=A-Z 2=Z-A

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🛒 购物解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("支持淘宝店铺链接（店铺信息游客可获取，商品列表需淘宝 Cookie）"
                      "和商品链接 item.taobao.com/item.htm?id=xxx（详情需淘宝 Cookie）。"
                      "勾选商品（全选/反选/清除）后点「获取选中商品详情」逐件拉取并连带导出 Excel；"
                      "「导出商品 JSON」输出全量数据。Excel 默认存在商品图片同目录，不弹窗选位置")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText(
            "留空直接回车 = 默认店铺 · 或输入 https://xxx.taobao.com/shop/view_shop.htm / "
            "https://item.taobao.com/item.htm?id=123456")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        self.detail_btn = QPushButton("🛍 获取选中商品详情")
        self.detail_btn.setEnabled(False)
        self.detail_btn.setToolTip(
            "勾选了商品→逐件拉取主图/选购参数/参数信息/图文详情（图片自动下载、"
            "Excel 连带导出到商品目录）；未勾选→只拉当前选中行")
        self.detail_btn.clicked.connect(self._fetch_detail)
        row.addWidget(self.detail_btn)
        self.export_btn = QPushButton("💾 导出商品 JSON")
        self.export_btn.setEnabled(False)
        self.export_btn.setToolTip(
            "导出勾选的商品为全量 JSON（主图/规格参数/选购参数/参数信息/图文详情/"
            "本地图片路径）；未拉过详情的自动逐件拉取并下载图片")
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        self.cookie_btn = QPushButton("🍪 淘宝 Cookie")
        self.cookie_btn.setToolTip("打开 Cookie 管理并定位淘宝登录入口")
        self.cookie_btn.clicked.connect(self._open_cookie)
        row.addWidget(self.cookie_btn)
        layout.addLayout(row)

        # 勾选工具行：全选 / 反选 / 清除 + 已勾选计数（多选导出用）
        sel_row = QHBoxLayout()
        self.sel_all_btn = QPushButton("☑ 全选")
        self.sel_all_btn.setToolTip("勾选全部商品")
        self.sel_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        sel_row.addWidget(self.sel_all_btn)
        self.sel_invert_btn = QPushButton("⇄ 反选")
        self.sel_invert_btn.setToolTip("反转每行勾选状态")
        self.sel_invert_btn.clicked.connect(self._invert_checks)
        sel_row.addWidget(self.sel_invert_btn)
        self.sel_clear_btn = QPushButton("✖ 清除")
        self.sel_clear_btn.setToolTip("清除全部勾选")
        self.sel_clear_btn.clicked.connect(lambda: self._set_all_checks(False))
        sel_row.addWidget(self.sel_clear_btn)
        self.check_count_label = QLabel("已勾选 0 件")
        self.check_count_label.setStyleSheet("color: #888;")
        sel_row.addWidget(self.check_count_label)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        # 左：商品表格 / 右：商品详情（简介 + 图预览 + 单商品导出）
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(self.COLS_SHOP))
        self.table.setHorizontalHeaderLabels(self.COLS_SHOP)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 36)   # 勾选列
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(self._open_item)
        # 点击表头排序：A-Z → Z-A → 原顺序（勾选与选中行保留）
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        splitter.addWidget(self.table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self.info_caption = QLabel("商品详情")
        rv.addWidget(self.info_caption)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText(
            "选中左侧商品查看简介；点「获取选中商品详情」拉主图/选购参数/参数信息/图文详情")
        rv.addWidget(self.detail_text, 1)
        self.pic_label = QLabel("选中商品后显示主图预览")
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setMinimumHeight(180)
        self.pic_label.setStyleSheet("color: #888;")
        rv.addWidget(self.pic_label)
        btn_row = QHBoxLayout()
        self.export_xlsx_btn = QPushButton("📊 导出单商品 Excel")
        self.export_xlsx_btn.setEnabled(False)
        self.export_xlsx_btn.setToolTip(
            "导出右侧商品的 xlsx（图片已插入），默认存在商品图片同目录，不弹窗")
        self.export_xlsx_btn.clicked.connect(self._export_xlsx)
        btn_row.addWidget(self.export_xlsx_btn)
        self.link_btn = QPushButton("🌐 打开商品页面")
        self.link_btn.setEnabled(False)
        self.link_btn.clicked.connect(self._open_item_btn)
        btn_row.addWidget(self.link_btn)
        rv.addLayout(btn_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    # ==================== 解析 ====================

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            # 留空直接回车 → 默认店铺
            self.url_entry.setText(self.DEFAULT_SHOP_URL)
            url = self.DEFAULT_SHOP_URL
        kind, _ = shopping_mod.detect_shopping_platform(url)
        if kind == "taobao_item":
            self._parse_item(url)
        else:
            self._parse_shop(url)

    def _log(self, msg):
        def apply():
            self.status_label.setText(f"⏳ {msg}")
        try:
            post_to_main(apply)
        except Exception:
            pass

    def _parse_shop(self, url):
        self.parse_btn.setEnabled(False)
        self.status_label.setText("⏳ 解析中...")

        def work():
            import auth
            cookie = auth.get_cookie("taobao") or ""
            return shopping_mod.parse_shopping_url(url, cookie=cookie, log=self._log)

        def done(result, err):
            self.parse_btn.setEnabled(True)
            self.status_label.setText("")
            if err:
                QMessageBox.warning(self, "解析失败", str(err))
                return
            shop, products = result
            self._shop = shop
            self._products = products
            self._detail = None
            self._detail_cache.clear()
            self.info_caption.setText("商品详情")
            self._fill_table_shop(products)
            self._reset_right_panel()
            if products:
                self.status_label.setText(
                    f"✅ {shop.shop_name or '店铺'}：获取到 {len(products)} 件商品；"
                    f"勾选后「导出商品 JSON」全量导出，或选中一行拉详情")
            else:
                self.status_label.setText(
                    "✅ 店铺信息获取成功（未配置淘宝 Cookie，商品列表需登录后获取）")
            self.export_btn.setEnabled(bool(products))

        run_bg(work, done)

    def _parse_item(self, url):
        """直接解析商品链接 → 列表填 SKU 档位，右侧显示完整详情。"""
        self.parse_btn.setEnabled(False)
        self.status_label.setText("⏳ 解析商品详情中...")

        def work():
            import auth
            from toolbox.config_manager import get_export_dir
            cookie = auth.get_cookie("taobao") or ""
            detail = shopping_mod.parse_taobao_item_url(url, cookie, log=self._log)
            base = get_export_dir() / "淘宝商品"
            shopping_mod.download_item_images(detail, base, log=self._log)
            shopping_mod.export_item_xlsx(
                detail, shopping_mod.default_item_xlsx_path(detail, base))
            return detail

        def done(detail, err):
            self.parse_btn.setEnabled(True)
            self.status_label.setText("")
            if err:
                QMessageBox.warning(self, "解析失败", str(err))
                return
            self._detail = detail
            self._products = []
            self._shop = None
            self._detail_cache[detail.item_id] = detail
            self._fill_table_item(detail)
            self._show_detail(detail)
            imgs = (f"主图 {len(detail.local_main) or len(detail.main_images)}"
                    f" / 规格 {len(detail.local_sku)}"
                    f" / 详情 {len(detail.local_desc) or len(detail.desc_images)}")
            self.status_label.setText(
                f"✅ {detail.title[:40]}｜图片已下载（{imgs}），Excel 已连带导出到商品目录")
            self.export_btn.setEnabled(False)

        run_bg(work, done)

    # ==================== 表格 ====================

    def _fill_table_shop(self, products):
        self._products_original = list(products)
        self._sort_col = None
        self._sort_state = 0
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self._fill_shop_rows(products)
        for b in (self.sel_all_btn, self.sel_invert_btn, self.sel_clear_btn):
            b.setEnabled(True)

    def _fill_shop_rows(self, products):
        """纯填表（不动排序状态/原始顺序备份，排序重填用）。"""
        self._filling = True
        self.table.setColumnCount(len(self.COLS_SHOP))
        self.table.setHorizontalHeaderLabels(self.COLS_SHOP)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 36)
        for col, w in ((2, 70), (3, 90), (4, 70), (5, 120), (6, 60)):
            self.table.setColumnWidth(col, w)
        self.table.setRowCount(0)
        for pr in products:
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                         | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk)
            self.table.setItem(row, 1, QTableWidgetItem(pr.title or "-"))
            self.table.setItem(row, 2, QTableWidgetItem(pr.price or ""))
            self.table.setItem(row, 3, QTableWidgetItem(pr.sales or ""))
            coupon = f"-¥{pr.coupon}" if pr.coupon else ""
            self.table.setItem(row, 4, QTableWidgetItem(coupon))
            self.table.setItem(row, 5, QTableWidgetItem(pr.item_id))
            self.table.setItem(row, 6, QTableWidgetItem("打开"))
        self._filling = False
        self._update_check_count()

    def _fill_table_item(self, d):
        self._filling = True
        self._sort_col = None
        self._sort_state = 0
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.setColumnCount(len(self.COLS_ITEM))
        self.table.setHorizontalHeaderLabels(self.COLS_ITEM)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 90), (2, 70)):
            self.table.setColumnWidth(col, w)
        self.table.setRowCount(0)
        self._detail_sku_imgs = []
        sku_imgs = []
        for grp in d.sku_props:
            for v in grp.get("values") or []:
                sku_imgs.append(v.get("image") or "")
        for i, s in enumerate(d.skus):
            img = sku_imgs[i] if i < len(sku_imgs) else ""
            self._detail_sku_imgs.append(img or (d.main_images[0] if d.main_images else ""))
        self._skus_original = list(d.skus)
        self._sku_imgs_original = list(self._detail_sku_imgs)
        self._filling = False
        self._refill_sku_rows()
        for b in (self.sel_all_btn, self.sel_invert_btn, self.sel_clear_btn):
            b.setEnabled(False)   # SKU 模式无勾选

    def _refill_sku_rows(self):
        """按当前 _detail.skus + _detail_sku_imgs 填 SKU 表（两者顺序对齐）。"""
        self._filling = True
        self.table.setRowCount(0)
        for s in self._detail.skus:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(s.names or "-"))
            self.table.setItem(row, 1, QTableWidgetItem(s.price or ""))
            self.table.setItem(row, 2, QTableWidgetItem(s.quantity or ""))
        self._filling = False

    # ==================== 勾选（多选导出） ====================

    def _on_item_changed(self, item):
        """勾选状态变化 → 更新计数（填充表格时忽略）。"""
        if not self._filling and item.column() == 0:
            self._update_check_count()

    def _is_shop_mode(self) -> bool:
        return self._detail is None and bool(self._products)

    def _row_checked(self, row: int) -> bool:
        it = self.table.item(row, 0)
        return bool(it and it.checkState() == Qt.Checked)

    def _checked_rows(self) -> list:
        if not self._is_shop_mode():
            return []
        return [r for r in range(self.table.rowCount()) if self._row_checked(r)]

    def _set_all_checks(self, checked: bool):
        if not self._is_shop_mode():
            return
        self._filling = True
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(state)
        self._filling = False
        self._update_check_count()

    def _invert_checks(self):
        if not self._is_shop_mode():
            return
        self._filling = True
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                                 else Qt.Checked)
        self._filling = False
        self._update_check_count()

    def _update_check_count(self):
        n = len(self._checked_rows())
        self.check_count_label.setText(f"已勾选 {n} 件")

    # ==================== 表头排序（A-Z / Z-A / 原顺序） ====================

    _PRODUCT_KEYS = {
        1: lambda pr: (pr.title or "").lower(),
        2: lambda pr: first_number(pr.price),
        3: lambda pr: sales_number(pr.sales),
        4: lambda pr: first_number(pr.coupon) if pr.coupon else None,
        5: lambda pr: int(pr.item_id) if (pr.item_id or "").isdigit() else None,
    }

    _SKU_KEYS = {
        0: lambda s: (s.names or "").lower(),
        1: lambda s: first_number(s.price),
        2: lambda s: first_number(s.quantity),
    }

    def _on_header_clicked(self, col):
        """点击表头循环切换：A-Z → Z-A → 原顺序；店铺模式保留勾选与选中行。"""
        if self._is_shop_mode():
            if col == 0 or col == 6 or not self._products:
                return
            keys = self._PRODUCT_KEYS
            # 先按当前顺序记录勾选/选中的 key（随后 _products 顺序改变，行号会错位）
            checked = {self._products[r].item_id for r in self._checked_rows()}
            sel_rows = self.table.selectionModel().selectedRows()
            sel_key = (self._products[sel_rows[0].row()].item_id
                       if sel_rows and sel_rows[0].row() < len(self._products) else None)
        elif self._detail is not None:
            keys = self._SKU_KEYS
            if col not in keys:
                return
            checked = sel_key = None
        else:
            return
        if self._sort_col != col:
            self._sort_col, self._sort_state = col, 1
        else:
            self._sort_state = (self._sort_state + 1) % 3
        header = self.table.horizontalHeader()
        if self._sort_state == 0:
            if self._is_shop_mode():
                self._products = list(self._products_original)
            else:
                self._detail.skus = list(self._skus_original)
                self._detail_sku_imgs = list(self._sku_imgs_original)
            header.setSortIndicatorShown(False)
        else:
            if self._is_shop_mode():
                stable_sort(self._products, keys[col], desc=(self._sort_state == 2))
            else:
                # SKU 行与预览图成对移动，保持行号对齐
                pairs = list(zip(self._detail.skus, self._detail_sku_imgs))
                stable_sort(pairs, key=lambda p: keys[col](p[0]),
                            desc=(self._sort_state == 2))
                self._detail.skus = [s for s, _ in pairs]
                self._detail_sku_imgs = [img for _, img in pairs]
            header.setSortIndicatorShown(True)
            header.setSortIndicator(
                col, Qt.DescendingOrder if self._sort_state == 2 else Qt.AscendingOrder)
        if self._is_shop_mode():
            self._refill_products_preserving(checked, sel_key)
        else:
            self._refill_sku_rows()

    def _refill_products_preserving(self, checked, sel_key):
        """按当前 self._products 重填商品表，恢复勾选状态与选中行（key 在排序前采集）。"""
        self._fill_shop_rows(self._products)
        if checked:
            self._filling = True
            for r, pr in enumerate(self._products):
                if pr.item_id in checked:
                    self.table.item(r, 0).setCheckState(Qt.Checked)
            self._filling = False
            self._update_check_count()
        if sel_key:
            for r, pr in enumerate(self._products):
                if pr.item_id == sel_key:
                    self.table.selectRow(r)
                    break

    def _reset_right_panel(self):
        self.detail_text.setPlaceholderText(
            "选中左侧商品查看简介；点「获取选中商品详情」拉主图/选购参数/参数信息/图文详情")
        self.detail_text.clear()
        self.pic_label.setPixmap(QPixmap())
        self.pic_label.setText("选中商品后显示主图预览")
        self.export_xlsx_btn.setEnabled(False)
        self.link_btn.setEnabled(False)
        self.detail_btn.setEnabled(False)

    # ==================== 选中 → 简介（学票务页） ====================

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.detail_btn.setEnabled(False)
            self.link_btn.setEnabled(False)
            return
        row = rows[0].row()
        if self._detail is not None:
            # 商品链接模式：选中 SKU 行 → 预览该档规格图
            self.detail_btn.setEnabled(False)
            self.link_btn.setEnabled(bool(self._detail.url))
            self._preview_url(self._detail_sku_imgs[row] if row < len(self._detail_sku_imgs) else "")
            return
        if not (0 <= row < len(self._products)):
            return
        pr = self._products[row]
        self.detail_btn.setEnabled(True)
        self.link_btn.setEnabled(bool(pr.url))
        # 已拉过详情 → 直接显示完整详情；否则显示列表简介
        cached = self._detail_cache.get(pr.item_id)
        if cached:
            self._show_detail(cached)
        else:
            self.detail_text.setPlainText(self._format_summary(pr))
        self._preview_url(pr.pic)

    def _fetch_detail(self):
        """「获取选中商品详情」：勾选了商品→逐件拉详情+下载图片+连带导出 xlsx；
        未勾选→只拉当前选中行。已拉过的（缓存）自动跳过。"""
        if self._detail is not None:
            return
        if not self._is_shop_mode():
            return
        checked = self._checked_rows()
        if checked:
            rows = sorted(set(checked))
        else:
            rows = [r.row() for r in self.table.selectionModel().selectedRows()]
        rows = [r for r in rows if 0 <= r < len(self._products)]
        if not rows:
            return
        todo = [(r, self._products[r]) for r in rows
                if self._products[r].item_id not in self._detail_cache]
        if not todo:
            self._show_cached_or(rows)
            return
        total = len(todo)
        self.detail_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_label.setText(f"⏳ 获取 {total} 件商品详情（件间隔 3 秒防风控）...")

        def work():
            import time
            import auth
            from toolbox.config_manager import get_export_dir
            cookie = auth.get_cookie("taobao") or ""
            base = get_export_dir() / "淘宝商品"
            done_items, failed = [], []
            for n, (r, pr) in enumerate(todo, 1):
                self._log(f"详情进度 {n}/{total}：{pr.title[:26]}")
                if n > 1:
                    time.sleep(3)   # 件间隔：连续请求会触发淘宝风控
                for attempt in (1, 2):
                    try:
                        d = shopping_mod.fetch_taobao_item_detail(
                            pr.item_id, cookie, log=self._log)
                        shopping_mod.download_item_images(d, base, log=self._log)
                        xlsx = shopping_mod.default_item_xlsx_path(d, base)
                        shopping_mod.export_item_xlsx(d, xlsx)
                        self._log(f"  Excel 已导出：{Path(xlsx).name}")
                        done_items.append(d)
                        break
                    except Exception as e:
                        if attempt == 1:
                            self._log(f"  {pr.title[:16]} 失败，10 秒后重试...")
                            time.sleep(10)
                        else:
                            failed.append((pr.title, str(e)[:60]))
            return done_items, failed

        def done(result, err):
            self.parse_btn.setEnabled(True)
            self.export_btn.setEnabled(bool(self._products))
            self.detail_btn.setEnabled(
                bool(self.table.selectionModel().selectedRows()))
            if err:
                self.status_label.setText("")
                QMessageBox.warning(self, "详情获取失败", str(err))
                return
            done_items, failed = result
            for d in done_items:
                self._detail_cache[d.item_id] = d
            self._show_cached_or([r for r, _ in todo])
            tip = f"，{len(failed)} 件失败" if failed else ""
            self.status_label.setText(
                f"✅ 已获取 {len(done_items)} 件详情并连带导出 Excel{tip}"
                f"（download/淘宝商品/，每件一个目录）")

        run_bg(work, done)

    def _show_cached_or(self, rows):
        """显示当前选中行（无选中则 rows 首行）的已缓存详情。"""
        show = None
        sel = self.table.selectionModel().selectedRows()
        if sel and 0 <= sel[0].row() < len(self._products):
            show = self._detail_cache.get(self._products[sel[0].row()].item_id)
        if show is None:
            for r in rows:
                if 0 <= r < len(self._products):
                    show = self._detail_cache.get(self._products[r].item_id)
                    if show:
                        break
        if show is not None:
            self._show_detail(show)

    # ==================== 右侧详情渲染 ====================

    def _format_summary(self, pr) -> str:
        """列表商品 → 简介文本（无需网络，选中即显）。"""
        lines = [f"标题：{pr.title or '-'}"]
        if pr.price:
            lines.append(f"价格：{pr.price}")
        if pr.reserve_price and pr.reserve_price != pr.price:
            lines.append(f"原价：{pr.reserve_price}")
        if pr.sales:
            lines.append(f"销量：{pr.sales}")
        if pr.coupon:
            lines.append(f"优惠券：减 ¥{pr.coupon}")
        lines.append(f"商品ID：{pr.item_id}")
        lines.append("")
        lines.append("点「🛍 获取选中商品详情」拉取")
        lines.append("主图 / 选购参数 / 参数信息 / 图文详情（图片自动下载）")
        return "\n".join(lines)

    def _show_detail(self, d):
        """完整详情 → 右侧文本 + 主图预览 + 启用单商品导出。"""
        self.info_caption.setText("商品详情")
        self.detail_text.setPlainText(self._format_detail(d))
        self.export_xlsx_btn.setEnabled(True)
        self.link_btn.setEnabled(bool(d.url))
        self._preview_url(d.local_main[0] if d.local_main
                          else (d.main_images[0] if d.main_images else ""),
                          local=True)

    def _format_detail(self, d) -> str:
        lines = [f"标题：{d.title or '-'}"]
        if d.price:
            lines.append(f"价格：{d.price}")
        if d.sales:
            lines.append(f"销量：{d.sales}")
        if d.shop_name:
            lines.append(f"店铺：{d.shop_name}")
        if d.delivery:
            lines.append(f"物流：{d.delivery}")
        if d.shop_scores:
            lines.append(f"店铺评分：{d.shop_scores}")
        if d.sku_props:
            lines.append("")
            lines.append("═══ 规格参数 ═══")
            for grp in d.sku_props:
                vals = " / ".join(v.get("name") or "" for v in grp.get("values") or [])
                lines.append(f"  {grp.get('name') or '规格'}：{vals}")
        if d.skus:
            lines.append("")
            lines.append(f"═══ 选购参数（{len(d.skus)} 档）═══")
            for s in d.skus:
                parts = [s.names or "-", s.price or ""]
                if s.quantity:
                    parts.append(f"库存 {s.quantity}")
                lines.append("  · " + " | ".join(parts))
        if d.params:
            lines.append("")
            lines.append(f"═══ 参数信息（{len(d.params)} 项）═══")
            for n, v in d.params:
                lines.append(f"  {n}：{v}")
        if d.main_images:
            lines.append("")
            lines.append(f"═══ 主图（{len(d.main_images)} 张）═══")
            for i, u in enumerate(d.main_images[:8], 1):
                lines.append(f"  [{i}] {u}")
        if d.desc_images:
            lines.append("")
            lines.append(f"═══ 图文详情（{len(d.desc_images)} 张长图）═══")
            for i, u in enumerate(d.desc_images[:10], 1):
                lines.append(f"  [{i}] {u}")
        dl = [f"主图 {len(d.local_main)}", f"规格 {len(d.local_sku)}",
              f"详情 {len(d.local_desc)}"]
        lines.append("")
        lines.append(f"已下载图片：{' / '.join(dl)}")
        lines.append(f"链接：{d.url}")
        return "\n".join(lines)

    # ==================== 图片预览 ====================

    def _preview_url(self, url: str, local: bool = False):
        """右侧预览一张图（后台加载，旧请求作废；local=True 直接读本地文件）。"""
        if not url:
            self.pic_label.setPixmap(QPixmap())
            self.pic_label.setText("无图片")
            return
        self._preview_seq += 1
        seq = self._preview_seq
        self.pic_label.setPixmap(QPixmap())
        self.pic_label.setText("图片加载中...")

        if local:
            pm = QPixmap(url)
            if not pm.isNull():
                self.pic_label.setText("")
                self.pic_label.setPixmap(
                    pm.scaled(self.pic_label.size(), Qt.KeepAspectRatio,
                              Qt.SmoothTransformation))
            else:
                self.pic_label.setText("图片加载失败")
            return

        def work():
            import httpx
            r = httpx.get(url, timeout=15,
                          headers={"User-Agent": "Mozilla/5.0",
                                   "Referer": "https://www.taobao.com/"})
            r.raise_for_status()
            return r.content

        def done(data, err):
            if seq != self._preview_seq:
                return  # 已切到别的行，丢弃旧图
            if err or not data:
                self.pic_label.setText("图片加载失败")
                return
            pm = QPixmap()
            if not pm.loadFromData(data):
                self.pic_label.setText("图片解析失败")
                return
            self.pic_label.setText("")
            self.pic_label.setPixmap(
                pm.scaled(self.pic_label.size(), Qt.KeepAspectRatio,
                          Qt.SmoothTransformation))

        run_bg(work, done)

    # ==================== 交互 ====================

    def _open_item_btn(self):
        """右侧「打开商品页面」：当前显示的商品（详情或列表选中）。"""
        url = ""
        if self._detail is not None:
            url = self._detail.url
        else:
            rows = self.table.selectionModel().selectedRows()
            if rows and 0 <= rows[0].row() < len(self._products):
                url = self._products[rows[0].row()].url
        if url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as QU
            QDesktopServices.openUrl(QU(url))

    def _open_item(self, row, _col):
        """双击行 → 打开商品页。"""
        if self._detail is not None:
            url = self._detail.url
        elif 0 <= row < len(self._products):
            url = self._products[row].url
        else:
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl as QU
        QDesktopServices.openUrl(QU(url))

    def _open_cookie(self):
        """打开 Cookie 管理对话框（淘宝条目在列表中，浏览器登录）。"""
        try:
            from gui_qt.cookie_manager import CookieDialog
            CookieDialog(self).exec()
        except Exception:
            QMessageBox.information(
                self, "提示", "请从左下角「全局设置」打开 Cookie 管理登录淘宝")

    # ==================== 导出 ====================

    def _export(self):
        """多选导出 JSON：勾选的商品逐件拉全量详情（含图片下载），一次导出。"""
        if not self._products:
            return
        checked = self._checked_rows()
        if not checked:
            QMessageBox.information(
                self, "提示",
                "请先勾选要导出的商品（表格第一列复选框），或点「☑ 全选」")
            return
        total = len(checked)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出商品 JSON（全量）",
            f"淘宝商品_已选{total}件.json", "JSON (*.json)")
        if not path:
            return
        self.export_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.detail_btn.setEnabled(False)

        def work():
            import time
            import auth
            from toolbox.config_manager import get_export_dir
            cookie = auth.get_cookie("taobao") or ""
            base = get_export_dir() / "淘宝商品"
            items, failed = [], []
            for n, r in enumerate(checked, 1):
                pr = self._products[r]
                self._log(f"导出进度 {n}/{total}：{pr.title[:26]}")
                if n > 1:
                    time.sleep(3)   # 件间隔：连续请求会触发淘宝风控
                d = self._detail_cache.get(pr.item_id)
                if d is None:
                    # 单件失败（风控抖动）等待 10 秒原地重试一次
                    for attempt in (1, 2):
                        try:
                            d = shopping_mod.fetch_taobao_item_detail(
                                pr.item_id, cookie, log=self._log)
                            shopping_mod.download_item_images(d, base, log=self._log)
                            xlsx = shopping_mod.default_item_xlsx_path(d, base)
                            shopping_mod.export_item_xlsx(d, xlsx)
                            break
                        except Exception as e:
                            d = None
                            err = str(e)[:60]
                            if attempt == 1:
                                self._log(f"  {pr.title[:16]} 失败，10 秒后重试...")
                                time.sleep(10)
                            else:
                                failed.append((pr.title, err))
                if d is not None:
                    self._detail_cache[pr.item_id] = d
                items.append((pr, d))
            return items, failed

        def done(result, err):
            self.export_btn.setEnabled(True)
            self.parse_btn.setEnabled(True)
            self.detail_btn.setEnabled(
                bool(self.table.selectionModel().selectedRows()))
            if err:
                self.status_label.setText("")
                QMessageBox.warning(self, "导出失败", str(err))
                return
            items, failed = result
            products_json = []
            for pr, d in items:
                if d is not None:
                    products_json.append(d.to_dict())
                else:
                    row = pr.to_dict()
                    row["详情获取失败"] = next(
                        (e for t, e in failed if t == pr.title), "")
                    products_json.append(row)
            data = {
                "店铺": self._shop.to_dict() if self._shop else {},
                "商品数": len(products_json),
                "商品": products_json,
            }
            if failed:
                data["导出告警"] = [f"{t[:20]}：{e}" for t, e in failed]
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))
                return
            tip = f"，{len(failed)} 件详情获取失败" if failed else ""
            self.status_label.setText(
                f"✅ 已导出 {len(products_json)} 件商品全量 JSON{tip}到 {path}")

        run_bg(work, done)

    def _export_xlsx(self):
        """导出右侧商品详情为单商品 xlsx（图片直接插入）。"""
        d = self._detail
        if d is None:
            # 列表模式：取选中行的已缓存详情
            rows = self.table.selectionModel().selectedRows()
            if rows and 0 <= rows[0].row() < len(self._products):
                d = self._detail_cache.get(self._products[rows[0].row()].item_id)
        if not d:
            QMessageBox.information(
                self, "提示", "请先点「获取选中商品详情」拉取商品详情，再导出 Excel")
            return
        from toolbox.config_manager import get_export_dir
        base = get_export_dir() / "淘宝商品"
        path = shopping_mod.default_item_xlsx_path(d, base)
        try:
            shopping_mod.export_item_xlsx(d, path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.status_label.setText(f"✅ 已导出单商品 Excel：{path}")
