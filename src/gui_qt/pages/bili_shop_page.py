"""B站小店页（PySide6）：分享短链 / UP主 UID → 小店信息 + 商品列表。

- 顶部粘贴 b23.tv 短链、mall.bilibili.com 小店链接、space 主页或纯 UID
- 可选排序：综合 / 销量 / 价格 / 新品
- 左侧商品列表，选中行 → 右侧封面 + 详情
- 查询后自动保存 download/B站小店/<小店名>_<UID>/小店信息.json
- 「导出 Excel」→ 商品列表 xlsx；「打开保存目录」直达落盘目录

游客即可查询（仅需 B站访客 buvid，不要求登录）。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import bili_shop as bs

_PREVIEW_W = 240
_PREVIEW_H = 240
_AVATAR_W = 56


def _center(text):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(Qt.AlignCenter)
    return it


class BiliShopPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._shop: bs.BiliShop | None = None
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("🛍 B站小店")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ B站小店 · 功能介绍", (
            "<p><b>解析 B站 UP主小店，游客可用（无需登录）。</b></p>"
            "<p>· <b>用法</b>：粘贴 b23.tv 小店短链、mall.bilibili.com 小店链接、"
            "space.bilibili.com 主页或纯 UID → 点「🔍 解析」</p>"
            "<p>· <b>能拿到</b>：小店名 / 头像 / 粉丝数 / 商品数 + 每件商品的 "
            "ID / 标题 / 价格 / 封面 / 跳转链 / 来源类型</p>"
            "<p>· <b>保存位置</b>：download/B站小店/&lt;小店名&gt;_&lt;UID&gt;/"
            "（小店信息.json；商品列表可导出 Excel）</p>"
        )))
        layout.addLayout(title_row)

        top = QFrame()
        top.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(
            "粘贴 b23.tv 小店短链 / mall.bilibili.com 小店链接 / space.bilibili.com/1467772")
        self.input_edit.returnPressed.connect(self._do_parse)
        row.addWidget(self.input_edit, 1)

        self.sort_box = QComboBox()
        for key, label in bs.SORT_LABELS.items():
            self.sort_box.addItem(label, key)
        self.sort_box.setFixedWidth(80)
        row.addWidget(self.sort_box)

        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.parse_btn.clicked.connect(self._do_parse)
        row.addWidget(self.parse_btn)
        tv.addLayout(row)

        info_row = QHBoxLayout()
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(_AVATAR_W, _AVATAR_W)
        self.avatar_label.setStyleSheet(
            "QLabel { background: #f4f6f8; border: 1px solid #d0d6df; border-radius: 28px; }")
        self.avatar_label.setAlignment(Qt.AlignCenter)
        info_row.addWidget(self.avatar_label)

        self.shop_info = QLabel("尚未解析小店")
        self.shop_info.setStyleSheet("color: #444;")
        self.shop_info.setTextFormat(Qt.RichText)
        info_row.addWidget(self.shop_info, 1)

        self.export_btn = QPushButton("📊 导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._do_export)
        info_row.addWidget(self.export_btn)

        self.dir_btn = QPushButton("📂 打开保存目录")
        self.dir_btn.setEnabled(False)
        self.dir_btn.clicked.connect(self._open_dir)
        info_row.addWidget(self.dir_btn)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.clicked.connect(self._clear)
        info_row.addWidget(clear_btn)
        tv.addLayout(info_row)
        layout.addWidget(top)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lb = QLabel("商品列表（选中一行看右侧详情）")
        lb.setStyleSheet("font-weight: bold;")
        lv.addWidget(lb)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["商品标题", "价格", "商品ID", "来源"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 4):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        lv.addWidget(self.table, 1)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(6)
        lb2 = QLabel("商品详情")
        lb2.setStyleSheet("font-weight: bold;")
        rv.addWidget(lb2)
        self.preview = QLabel("（选中商品后显示封面）")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self.preview.setStyleSheet(
            "QLabel { background: #f4f6f8; border: 1px solid #d0d6df; border-radius: 6px; color: #999; }")
        rv.addWidget(self.preview, 0, Qt.AlignHCenter)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("商品字段详情")
        rv.addWidget(self.detail, 1)

        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("🌐 打开商品页")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_item)
        btn_row.addWidget(self.open_btn)
        self.copy_btn = QPushButton("📋 复制商品ID")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_item_id)
        btn_row.addWidget(self.copy_btn)
        btn_row.addStretch(1)
        rv.addLayout(btn_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("粘贴 B站小店链接或 UP主 UID → 🔍 解析")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

    # ---------- 状态 ----------
    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.sort_box.setEnabled(not busy)
        self.parse_btn.setEnabled(not busy)
        self.export_btn.setEnabled((not busy) and bool(self._shop and self._shop.items))
        if text:
            self.status_label.setText(text)

    def _selected(self) -> bs.BiliShopItem | None:
        if not self._shop:
            return None
        row = self.table.currentRow()
        if 0 <= row < len(self._shop.items):
            return self._shop.items[row]
        return None

    # ---------- 解析 ----------
    def _do_parse(self):
        if self._busy:
            return
        text = self.input_edit.text().strip()
        if not text:
            self.status_label.setText("❌ 请先粘贴 B站小店链接或 UP主 UID")
            return
        sort = self.sort_box.currentData() or "total_rank"
        self._set_busy(True, "⏳ 正在解析...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"⏳ {msg}")

        def work():
            shop = bs.fetch_shop(text, sort=sort, progress=prog)
            path = bs.save_shop_json(shop)
            return shop, path

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 解析失败：{err}")
                self.app.toast(f"解析失败：{err}"[:100], "warning")
                return
            shop, path = result
            self._shop = shop
            self._fill_table()
            self._fill_shop_info()
            if shop.items:
                self.table.selectRow(0)
            self.export_btn.setEnabled(bool(shop.items))
            self.dir_btn.setEnabled(True)
            if shop.items:
                self.status_label.setText(
                    f"✅ {shop.name or shop.mid}：{len(shop.items)} 件商品，"
                    f"已保存 {os.path.basename(str(path))}")
                self.app.toast(f"解析完成：{len(shop.items)} 件商品", "success")
            else:
                self.status_label.setText(
                    f"⚠️ {shop.name or shop.mid}：该排序下没有商品，换「综合」或「价格」再试")
                self.app.toast("该排序下没有商品", "warning")
            if shop.avatar:
                self._load_image(shop.avatar, self.avatar_label, "_avatar")

        run_bg(work, done)

    def _fill_shop_info(self):
        s = self._shop
        if not s:
            self.shop_info.setText("尚未解析小店")
            return
        parts = [f"<b>{s.name or '未命名小店'}</b>"]
        meta = [f"UID {s.mid}"]
        if s.fans:
            meta.append(f"粉丝 {s.fans}")
        if s.item_count_text:
            meta.append(f"商品 {s.item_count_text}")
        meta.append(f"排序 {bs.SORT_LABELS.get(s.sort, s.sort)}")
        parts.append(" · ".join(meta))
        self.shop_info.setText("<br>".join(parts))

    def _fill_table(self):
        items = self._shop.items if self._shop else []
        self.table.setRowCount(len(items))
        for r, it in enumerate(items):
            t = QTableWidgetItem(it.title)
            t.setToolTip(it.title)
            self.table.setItem(r, 0, t)
            self.table.setItem(r, 1, _center(it.price_text))
            self.table.setItem(r, 2, _center(it.item_id or "-"))
            self.table.setItem(r, 3, _center(it.source or "-"))
        self.export_btn.setEnabled(bool(items))

    # ---------- 选中 ----------
    def _on_select(self):
        it = self._selected()
        if not it:
            self.open_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            return
        self.open_btn.setEnabled(bool(it.card_url))
        self.copy_btn.setEnabled(bool(it.item_id))
        lines = [
            f"商品标题：{it.title or '-'}",
            f"价格：{it.price_text}",
            f"商品ID：{it.item_id or '-'}",
            f"来源：{it.source or '-'}",
        ]
        if it.sub_title:
            lines.append(f"副标题：{it.sub_title}")
        lines += [
            f"上架状态：{'已售罄' if it.sell_out else '在售'}",
            f"封面链接：{it.cover or '-'}",
            f"商品链接：{it.card_url or '-'}",
            "",
            "提示：B站小店商品页可能要求 App 打开，网页端仅作跳转。",
        ]
        self.detail.setPlainText("\n".join(lines))
        if it.cover:
            self._load_image(it.cover, self.preview, f"_item:{it.item_id}", scaled=True)
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("（该商品没有封面）")

    # ---------- 图片 ----------
    def _load_image(self, url: str, label: QLabel, want: str, scaled: bool = False):
        if scaled:
            label.setText("⏳ 加载封面...")
            label.setPixmap(QPixmap())

        def work():
            import httpx
            with httpx.Client(timeout=30.0, follow_redirects=True) as cli:
                r = cli.get(url, headers={"User-Agent": bs.UA_MOBILE,
                                          "Referer": "https://www.bilibili.com/"})
                r.raise_for_status()
                return r.content

        def done(data, err):
            if scaled:
                cur = self._selected()
                if cur is None or f"_item:{cur.item_id}" != want:
                    return
            if err or not data:
                if scaled:
                    label.setText("（封面加载失败）")
                return
            pm = QPixmap()
            if not pm.loadFromData(data):
                if scaled:
                    label.setText("（封面格式不支持）")
                return
            if scaled:
                label.setPixmap(pm.scaled(_PREVIEW_W - 8, _PREVIEW_H - 8,
                                          Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                label.setPixmap(pm.scaled(_AVATAR_W - 4, _AVATAR_W - 4,
                                          Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation))

        run_bg(work, done)

    # ---------- 导出 / 打开 ----------
    def _do_export(self):
        if not self._shop or not self._shop.items or self._busy:
            return
        shop = self._shop
        self._set_busy(True, "⏳ 正在导出 Excel...")

        def work():
            return bs.export_items_xlsx(shop)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 导出失败：{err}")
                self.app.toast(f"导出失败：{err}", "warning")
                return
            self.status_label.setText(f"✅ 已导出：{path}")
            self.app.toast(f"已导出 {os.path.basename(str(path))}", "success")

        run_bg(work, done)

    def _open_dir(self):
        if not self._shop:
            return
        try:
            os.startfile(str(bs.shop_subdir(self._shop)))
        except Exception:
            self.app.toast("保存目录不存在（先完成一次解析）", "info")

    def _open_item(self):
        it = self._selected()
        if it and it.card_url:
            QDesktopServices.openUrl(QUrl(it.card_url))

    def _copy_item_id(self):
        it = self._selected()
        if not it or not it.item_id:
            return
        QGuiApplication.clipboard().setText(it.item_id)
        self.status_label.setText(f"✅ 已复制商品 ID：{it.item_id}")
        self.app.toast("商品 ID 已复制", "success")

    def _clear(self):
        if self._busy:
            return
        self.input_edit.clear()
        self._shop = None
        self.table.setRowCount(0)
        self.detail.clear()
        self.preview.setPixmap(QPixmap())
        self.preview.setText("（选中商品后显示封面）")
        self.avatar_label.setPixmap(QPixmap())
        self.shop_info.setText("尚未解析小店")
        self.open_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.dir_btn.setEnabled(False)
        self.status_label.setText("粘贴 B站小店链接或 UP主 UID → 🔍 解析")
