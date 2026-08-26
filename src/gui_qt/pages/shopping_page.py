"""购物页（PySide6 版）：淘宝店铺商品 + 商品详情解析。

- 淘宝店铺链接 → 店铺信息（游客可获取）+ 商品列表（需淘宝 Cookie）
- 淘宝商品链接（item.taobao.com/item.htm?id=xxx）→ 商品详情：
  主图 / 选购参数(SKU 价) / 参数信息 / 图文详情长图，图片自动下载到
  download/淘宝商品/<商品ID>/{main,sku,desc}/，可导出单商品 xlsx（插图）
- 店铺商品表格：单击行预览商品图，双击打开商品页
"""

import json
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

import shopping as shopping_mod


class ShoppingPage(BasePage):
    # 表格两套列头（店铺模式 / 商品详情模式）
    COLS_SHOP = ["商品标题", "价格", "销量", "优惠券", "商品ID", "链接"]
    COLS_ITEM = ["SKU 选项", "价格", "库存"]

    def __init__(self, app):
        super().__init__(app)
        self._products = []       # 当前商品列表（ShopProduct，店铺模式）
        self._shop = None         # 最近一次店铺信息
        self._detail = None       # 最近一次商品详情（TaobaoItemDetail，详情模式）
        self._detail_sku_imgs = []   # 详情模式各行预览图 URL（行号对齐）
        self._preview_seq = 0     # 图片预览请求序号（防止旧图覆盖新图）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🛒 购物解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("支持淘宝店铺链接（店铺信息游客可获取，商品列表需淘宝 Cookie）"
                      "和商品链接 item.taobao.com/item.htm?id=xxx（详情需淘宝 Cookie）。"
                      "商品详情自动下载主图/规格图/图文详情，并可导出单商品 Excel。"
                      "单击行预览图片，双击打开网页")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText(
            "https://xxx.taobao.com/shop/view_shop.htm 或 https://item.taobao.com/item.htm?id=123456")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        self.cookie_btn = QPushButton("🍪 淘宝 Cookie")
        self.cookie_btn.setToolTip("打开 Cookie 管理并定位淘宝登录入口")
        self.cookie_btn.clicked.connect(self._open_cookie)
        row.addWidget(self.cookie_btn)
        layout.addLayout(row)

        # 左：表格 / 右：信息 + 图片预览
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(self.COLS_SHOP)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.cellClicked.connect(self._preview_item)
        self.table.cellDoubleClicked.connect(self._open_item)
        splitter.addWidget(self.table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        self.info_caption = QLabel("店铺信息")
        rv.addWidget(self.info_caption)
        self.shop_text = QTextEdit()
        self.shop_text.setReadOnly(True)
        self.shop_text.setPlaceholderText("解析后显示店铺/商品信息")
        self.shop_text.setFixedHeight(170)
        rv.addWidget(self.shop_text)
        self.pic_label = QLabel("单击表格行预览图片\n双击打开网页")
        self.pic_label.setAlignment(Qt.AlignCenter)
        self.pic_label.setMinimumHeight(200)
        self.pic_label.setStyleSheet("color: #888;")
        rv.addWidget(self.pic_label, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        btn_row = QHBoxLayout()
        self.export_btn = QPushButton("💾 导出商品 JSON")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        btn_row.addWidget(self.export_btn)
        self.export_xlsx_btn = QPushButton("📊 导出 Excel")
        self.export_xlsx_btn.setEnabled(False)
        self.export_xlsx_btn.setToolTip(
            "导出当前商品详情 xlsx（商品信息/参数信息/选购参数/主图/图文详情，图片已插入）")
        self.export_xlsx_btn.clicked.connect(self._export_xlsx)
        btn_row.addWidget(self.export_xlsx_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    # ==================== 解析 ====================

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入淘宝店铺或商品链接")
            return
        kind, _ = shopping_mod.detect_shopping_platform(url)
        if kind == "taobao_item":
            self._parse_item(url)
        else:
            self._parse_shop(url)

    def _log(self, msg):
        def apply():
            self.status_label.setText(f"⏳ {msg}")
        try:
            from gui_qt.common import post_to_main
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
            self.info_caption.setText("店铺信息")
            self.shop_text.setPlainText(self._shop_text(shop))
            self._fill_table_shop(products)
            if products:
                self.status_label.setText(
                    f"✅ {shop.shop_name or '店铺'}：获取到 {len(products)} 件商品")
            else:
                self.status_label.setText(
                    "✅ 店铺信息获取成功（未配置淘宝 Cookie，商品列表需登录后获取）")
            self.export_btn.setEnabled(bool(products))
            self.export_xlsx_btn.setEnabled(False)

        run_bg(work, done)

    def _parse_item(self, url):
        self.parse_btn.setEnabled(False)
        self.status_label.setText("⏳ 解析商品详情中...")

        def work():
            import auth
            from toolbox.config_manager import get_export_dir
            cookie = auth.get_cookie("taobao") or ""
            detail = shopping_mod.parse_taobao_item_url(url, cookie, log=self._log)
            base = get_export_dir() / "淘宝商品"
            shopping_mod.download_item_images(detail, base, log=self._log)
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
            self.info_caption.setText("商品信息")
            self.shop_text.setPlainText(self._detail_text(detail))
            self._fill_table_item(detail)
            imgs = (f"主图 {len(detail.local_main) or len(detail.main_images)}"
                    f" / 规格 {len(detail.local_sku)}"
                    f" / 详情 {len(detail.local_desc) or len(detail.desc_images)}")
            self.status_label.setText(
                f"✅ {detail.title[:40]}｜图片已下载（{imgs}），可导出 Excel")
            self.export_btn.setEnabled(False)
            self.export_xlsx_btn.setEnabled(True)

        run_bg(work, done)

    def _shop_text(self, shop) -> str:
        lines = [f"店铺名：{shop.shop_name or '-'}"]
        if shop.seller_id:
            lines.append(f"卖家ID：{shop.seller_id}")
        if shop.shop_id:
            lines.append(f"店铺ID：{shop.shop_id}")
        lines.append(f"链接：{shop.url}")
        return "\n".join(lines)

    def _detail_text(self, d) -> str:
        lines = [f"标题：{d.title or '-'}"]
        if d.price:
            lines.append(f"价格：{d.price}")
        if d.sales:
            lines.append(f"销量：{d.sales}")
        if d.shop_name:
            lines.append(f"店铺：{d.shop_name}")
        if d.delivery:
            lines.append(f"物流：{d.delivery}")
        if d.params:
            lines.append("参数：" + "；".join(f"{n}={v[:30]}" for n, v in d.params[:6]))
        if len(d.params) > 6:
            lines.append(f"　（另有 {len(d.params) - 6} 项参数，导出 Excel 查看全部）")
        lines.append(f"主图 {len(d.main_images)} 张｜SKU {len(d.skus)} 档｜"
                     f"详情图 {len(d.desc_images)} 张")
        return "\n".join(lines)

    def _fill_table_shop(self, products):
        self.table.setColumnCount(len(self.COLS_SHOP))
        self.table.setHorizontalHeaderLabels(self.COLS_SHOP)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 70), (2, 90), (3, 70), (4, 120), (5, 60)):
            self.table.setColumnWidth(col, w)
        self.table.setRowCount(0)
        for pr in products:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(pr.title or "-"))
            self.table.setItem(row, 1, QTableWidgetItem(pr.price or ""))
            self.table.setItem(row, 2, QTableWidgetItem(pr.sales or ""))
            coupon = f"-¥{pr.coupon}" if pr.coupon else ""
            self.table.setItem(row, 3, QTableWidgetItem(coupon))
            self.table.setItem(row, 4, QTableWidgetItem(pr.item_id))
            self.table.setItem(row, 5, QTableWidgetItem("打开"))

    def _fill_table_item(self, d):
        self.table.setColumnCount(len(self.COLS_ITEM))
        self.table.setHorizontalHeaderLabels(self.COLS_ITEM)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 90), (2, 70)):
            self.table.setColumnWidth(col, w)
        self.table.setRowCount(0)
        self._detail_sku_imgs = []
        # 规格图（与 SKU 值顺序对齐；没有规格图时用主图）
        sku_imgs = []
        for grp in d.sku_props:
            for v in grp.get("values") or []:
                sku_imgs.append(v.get("image") or "")
        for i, s in enumerate(d.skus):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(s.names or "-"))
            self.table.setItem(row, 1, QTableWidgetItem(s.price or ""))
            self.table.setItem(row, 2, QTableWidgetItem(s.quantity or ""))
            img = sku_imgs[i] if i < len(sku_imgs) else ""
            self._detail_sku_imgs.append(img or (d.main_images[0] if d.main_images else ""))

    # ==================== 交互 ====================

    def _preview_item(self, row, _col):
        """单击行 → 右侧预览图（后台下载，旧请求作废）。"""
        url = ""
        if self._detail is not None:
            if 0 <= row < len(self._detail_sku_imgs):
                url = self._detail_sku_imgs[row]
        elif 0 <= row < len(self._products):
            url = self._products[row].pic
        if not url:
            self.pic_label.setPixmap(QPixmap())
            self.pic_label.setText("该行无图片")
            return
        self._preview_seq += 1
        seq = self._preview_seq
        self.pic_label.setPixmap(QPixmap())
        self.pic_label.setText("图片加载中...")

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
            scaled = pm.scaled(self.pic_label.size(), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
            self.pic_label.setPixmap(scaled)

        run_bg(work, done)

    def _open_item(self, row, _col):
        if self._detail is not None:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as QU
            QDesktopServices.openUrl(QU(self._detail.url))
        elif 0 <= row < len(self._products):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as QU
            QDesktopServices.openUrl(QU(self._products[row].url))

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
        if not self._products:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出商品列表", "商品列表.json", "JSON (*.json)")
        if not path:
            return
        data = {
            "店铺": self._shop.to_dict() if self._shop else {},
            "商品数": len(self._products),
            "商品": [p.to_dict() for p in self._products],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status_label.setText(f"✅ 已导出 {len(self._products)} 件商品到 {path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _export_xlsx(self):
        """导出当前商品详情为单商品 xlsx（图片直接插入）。"""
        d = self._detail
        if not d:
            return
        safe = re.sub(r'[\\/:*?"<>|]', "_", d.title)[:24] or d.item_id
        default = f"淘宝商品_{d.item_id}_{safe}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出商品详情 Excel", default, "Excel (*.xlsx)")
        if not path:
            return
        try:
            shopping_mod.export_item_xlsx(d, path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.status_label.setText(f"✅ 已导出 Excel：{path}")
