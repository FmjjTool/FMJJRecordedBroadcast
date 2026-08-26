"""购物页（PySide6 版）：淘宝店铺商品解析。

- 输入淘宝店铺链接 → 店铺信息（游客可获取）+ 商品列表（需淘宝 Cookie + Playwright）
- 商品表格：标题 / 价格 / 销量 / 商品ID，双击打开商品页
- 导出 JSON / 打开淘宝 Cookie 管理
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

import shopping as shopping_mod


class ShoppingPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._products = []       # 当前商品列表（ShopProduct）
        self._shop = None         # 最近一次店铺信息

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🛒 购物解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("支持淘宝店铺链接；店铺信息游客可获取，商品列表需要淘宝 Cookie"
                      "（Cookie 管理中登录淘宝后自动获取）")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("https://xxx.taobao.com/shop/view_shop.htm")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析店铺")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        self.cookie_btn = QPushButton("🍪 淘宝 Cookie")
        self.cookie_btn.setToolTip("打开 Cookie 管理并定位淘宝登录入口")
        self.cookie_btn.clicked.connect(self._open_cookie)
        row.addWidget(self.cookie_btn)
        layout.addLayout(row)

        # 左：商品表格 / 右：店铺信息 + 图片预览
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["商品标题", "价格", "销量", "商品ID", "链接"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 60)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._open_item)
        splitter.addWidget(self.table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("店铺信息"))
        self.shop_text = QTextEdit()
        self.shop_text.setReadOnly(True)
        self.shop_text.setPlaceholderText("解析店铺后显示店铺信息")
        self.shop_text.setFixedHeight(160)
        rv.addWidget(self.shop_text)
        self.pic_label = QLabel("双击表格行打开商品页")
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
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    # ==================== 解析 ====================

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入淘宝店铺链接")
            return
        self.parse_btn.setEnabled(False)
        self.status_label.setText("⏳ 解析中...")

        def log(msg):
            def apply():
                self.status_label.setText(f"⏳ {msg}")
            try:
                from gui_qt.common import post_to_main
                post_to_main(apply)
            except Exception:
                pass

        def work():
            import auth
            cookie = auth.get_cookie("taobao") or ""
            return shopping_mod.parse_shopping_url(url, cookie=cookie, log=log)

        def done(result, err):
            self.parse_btn.setEnabled(True)
            self.status_label.setText("")
            if err:
                QMessageBox.warning(self, "解析失败", str(err))
                return
            shop, products = result
            self._shop = shop
            self._products = products
            self.shop_text.setPlainText(self._shop_text(shop))
            self._fill_table(products)
            if products:
                self.status_label.setText(
                    f"✅ {shop.shop_name or '店铺'}：获取到 {len(products)} 件商品")
            else:
                self.status_label.setText(
                    "✅ 店铺信息获取成功（未配置淘宝 Cookie，商品列表需登录后获取）")
            self.export_btn.setEnabled(bool(products))

        run_bg(work, done)

    def _shop_text(self, shop) -> str:
        lines = [f"店铺名：{shop.shop_name or '-'}"]
        if shop.seller_id:
            lines.append(f"卖家ID：{shop.seller_id}")
        if shop.shop_id:
            lines.append(f"店铺ID：{shop.shop_id}")
        lines.append(f"链接：{shop.url}")
        return "\n".join(lines)

    def _fill_table(self, products):
        self.table.setRowCount(0)
        for pr in products:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(pr.title or "-"))
            self.table.setItem(row, 1, QTableWidgetItem(pr.price or ""))
            self.table.setItem(row, 2, QTableWidgetItem(pr.sales or ""))
            self.table.setItem(row, 3, QTableWidgetItem(pr.item_id))
            self.table.setItem(row, 4, QTableWidgetItem("打开"))

    # ==================== 交互 ====================

    def _open_item(self, row, _col):
        if 0 <= row < len(self._products):
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
