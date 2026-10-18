"""抖音商品页（PySide6）：抖音商城分享文案 → 商品信息（无需登录）。

- 粘贴分享文案（可一次粘多条，每行一条）→ 🔍 解析全部
- 左侧商品列表（标题/价格区间/销量/商品ID/店铺ID），选中行 → 右侧主图 + 详情
- 默认解析后自动下载商品主图并保存单商品 JSON 到
  download/抖音商品/<标题>_<商品ID>/
- 「导出 Excel」→ 整批商品列表 xlsx（已下载的主图会插入表格）
- 「打开商品页」→ 浏览器打开抖音商城商品页

能拿到：标题 / 商品ID / 价格区间 / 销量 / 主图 / 店铺ID / 带货达人ID / 分享来源
暂拿不到（需登录态或 App）：店铺名称、SKU 规格库存、店铺全部商品列表
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import douyin_shop as ds

_PREVIEW_W = 260
_PREVIEW_H = 260


def _center(text):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(Qt.AlignCenter)
    return it


class DouyinShopPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._products: list[ds.DouyinProduct] = []
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 标题行 + 帮助 ----
        title = QLabel("🏬 抖音商品")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 抖音商品 · 功能介绍", (
            "<p><b>解析抖音商城（抖音小店）商品分享链接，<b>无需登录</b>。</b></p>"
            "<p>· <b>用法</b>：把分享文案整段粘贴到输入框（含 v.douyin.com 短链），"
            "一行一条，可一次粘多条 → 点「🔍 解析全部」</p>"
            "<p>· <b>能拿到</b>：商品标题 / 商品ID / 价格区间 / 销量 / 主图 / "
            "店铺ID / 带货达人ID / 分享来源</p>"
            "<p>· <b>拿不到</b>：店铺名称、SKU 规格库存、详情图（抖音对非 App 打开的分享链"
            "只返回「商品预览」数据，商品页 H5 在网页端一律提示「请进入抖音查看」）。"
            "要看得点「📋 复制链接」粘到抖音 App 里打开</p>"
            "<p>· <b>保存位置</b>：download/抖音商品/&lt;标题&gt;_&lt;商品ID&gt;/"
            "（主图 + 单商品 JSON）；整批列表可导出 Excel 到 download/抖音商品/</p>"
        )))
        layout.addLayout(title_row)

        # ---- 输入区 ----
        top = QFrame()
        top.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(6)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "把抖音商城分享文案整段粘到这里（可多行，一行一条）\n"
            "例：【抖音商城】https://v.douyin.com/xxxx/ 【封茗囧菌】2026.6.7生日会周边...")
        self.input_edit.setFixedHeight(72)
        tv.addWidget(self.input_edit)

        row = QHBoxLayout()
        self.parse_btn = QPushButton("🔍 解析全部")
        self.parse_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.parse_btn.clicked.connect(self._do_parse)
        row.addWidget(self.parse_btn)

        self.autosave_cb = QCheckBox("解析后自动下载主图并保存 JSON")
        self.autosave_cb.setChecked(True)
        self.autosave_cb.setToolTip("保存到 download/抖音商品/<标题>_<商品ID>/")
        row.addWidget(self.autosave_cb)

        row.addStretch(1)

        self.export_btn = QPushButton("📊 导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.setToolTip("把当前商品列表导出为 xlsx（已下载的主图会插入表格）")
        self.export_btn.clicked.connect(self._do_export)
        row.addWidget(self.export_btn)

        open_dir_btn = QPushButton("📂 打开保存目录")
        open_dir_btn.setToolTip("打开 download/抖音商品/")
        open_dir_btn.clicked.connect(self._open_dir)
        row.addWidget(open_dir_btn)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.clicked.connect(self._clear)
        row.addWidget(clear_btn)
        tv.addLayout(row)
        layout.addWidget(top)

        # ---- 左列表 + 右详情 ----
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addWidget(self._section_label("商品列表（选中一行看右侧详情）"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["商品标题", "价格区间", "销量", "商品ID", "店铺ID"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        lv.addWidget(self.table, 1)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(self._section_label("商品详情"))
        self.preview = QLabel("（选中商品后显示主图）")
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
        self.open_btn.setToolTip("用浏览器打开抖音商城商品页（分享链在网页端只给预览，完整详情需 App）")
        self.open_btn.clicked.connect(self._open_detail)
        btn_row.addWidget(self.open_btn)
        self.copy_btn = QPushButton("📋 复制链接")
        self.copy_btn.setEnabled(False)
        self.copy_btn.setToolTip("复制商品链接，粘到抖音 App 里打开即可看到店铺与详情图")
        self.copy_btn.clicked.connect(self._copy_link)
        btn_row.addWidget(self.copy_btn)
        self.folder_btn = QPushButton("📁 打开该商品目录")
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self._open_product_dir)
        btn_row.addWidget(self.folder_btn)
        btn_row.addStretch(1)
        rv.addLayout(btn_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("粘贴分享文案 → 🔍 解析全部")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

    # ---------- 小工具 ----------
    @staticmethod
    def _section_label(text):
        lb = QLabel(text)
        lb.setStyleSheet("font-weight: bold;")
        return lb

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        self.parse_btn.setEnabled(not busy)
        self.export_btn.setEnabled((not busy) and bool(self._products))
        if text:
            self.status_label.setText(text)

    def _selected(self) -> ds.DouyinProduct | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._products):
            return self._products[row]
        return None

    # ---------- 解析 ----------
    def _do_parse(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("❌ 请先粘贴抖音商城分享文案")
            return
        if self._busy:
            return
        autosave = self.autosave_cb.isChecked()
        self._set_busy(True, "⏳ 正在解析...")

        def prog(msg):
            post_to_main(self.status_label.setText, f"⏳ {msg}")

        def work():
            products, errs = ds.parse_many(text, progress=prog)
            if autosave:
                for i, p in enumerate(products, 1):
                    prog(f"保存第 {i}/{len(products)} 个商品...")
                    d = ds.product_dir(p)
                    img = ds.download_main_image(p, d)
                    p.local_dir = str(d)
                    p.image_file = img.name if img else ""
                    ds.save_product_json(p, d)
            return products, errs

        def done(result, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 解析失败：{err}")
                return
            products, errs = result
            self._products = products
            self._fill_table()
            if products:
                self.table.selectRow(0)
            parts = []
            if products:
                parts.append(f"✅ 成功 {len(products)} 条")
            if errs:
                parts.append(f"⚠️ 失败 {len(errs)} 条：" +
                             "；".join(f"{src} → {msg}" for src, msg in errs[:3]))
            self.status_label.setText("  ".join(parts) or "❌ 没找到抖音链接")
            if products:
                saved = "，主图与 JSON 已保存" if autosave else ""
                self.app.toast(f"解析完成：{len(products)} 个商品{saved}", "success")
            else:
                self.app.toast("没有解析出商品，请检查分享文案", "warning")

        run_bg(work, done)

    def _fill_table(self):
        self.table.setRowCount(len(self._products))
        for r, p in enumerate(self._products):
            it = QTableWidgetItem(p.title)
            it.setToolTip(p.title)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, _center(p.price_text))
            self.table.setItem(r, 2, _center(p.sales))
            self.table.setItem(r, 3, _center(p.product_id or "-"))
            self.table.setItem(r, 4, _center(p.shop_id or "-"))
        self.export_btn.setEnabled(bool(self._products))

    # ---------- 选中行 → 详情 ----------
    def _on_select(self):
        p = self._selected()
        if not p:
            self.open_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.folder_btn.setEnabled(False)
            return
        self.open_btn.setEnabled(bool(p.detail_url))
        self.copy_btn.setEnabled(bool(p.share_url or p.detail_url))
        self.folder_btn.setEnabled(bool(p.local_dir))
        self.detail.setPlainText(self._detail_text(p))
        self._show_image(p)

    @staticmethod
    def _detail_text(p: ds.DouyinProduct) -> str:
        lines = [
            f"商品标题：{p.title or '-'}",
            f"价格区间：{p.price_text}",
            f"销量：{p.sales}",
            f"商品ID：{p.product_id or '-'}",
            f"店铺ID：{p.shop_id or '-'}",
            f"带货达人ID：{p.kol_id or '-'}",
            f"分享来源：{p.enter_from or '-'}"
            + (f"（客户端 {p.app_version}）" if p.app_version else ""),
            f"分享账号：{p.sec_author_id or '-'}",
            f"商品页：{p.detail_url or '-'}",
            f"主图链接：{p.main_image or '-'}",
            f"采集时间：{p.parsed_at or '-'}",
            "",
            "提示：抖音对非 App 打开的分享链只返回「商品预览」数据，",
            "所以店铺名称、SKU 规格、详情图需要点「📋 复制链接」粘到抖音 App 里查看。",
        ]
        if p.local_dir:
            lines.append(f"本地目录：{p.local_dir}")
        return "\n".join(lines)

    def _show_image(self, p: ds.DouyinProduct):
        # 优先用本地已下载的主图，避免重复请求
        if p.local_dir and p.image_file:
            path = os.path.join(p.local_dir, p.image_file)
            if os.path.exists(path):
                self._set_pixmap_from_file(path)
                return
        if not p.main_image:
            self.preview.setText("（该商品没有主图）")
            self.preview.setPixmap(QPixmap())
            return
        self.preview.setText("⏳ 加载主图...")
        self.preview.setPixmap(QPixmap())
        want = p.product_id or p.main_image

        def work():
            import httpx
            with httpx.Client(timeout=30.0, follow_redirects=True) as cli:
                r = cli.get(p.main_image, headers={"User-Agent": ds.UA_MOBILE})
                r.raise_for_status()
                return r.content

        def done(data, err):
            # 用户可能已经切到别的商品了：只认当前选中的那条
            cur = self._selected()
            if cur is None or (cur.product_id or cur.main_image) != want:
                return
            if err or not data:
                self.preview.setText("（主图加载失败）")
                return
            pm = QPixmap()
            if pm.loadFromData(data):
                self._apply_pixmap(pm)
            else:
                self.preview.setText("（主图格式不支持）")

        run_bg(work, done)

    def _set_pixmap_from_file(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            self.preview.setText("（主图读取失败）")
            return
        self._apply_pixmap(pm)

    def _apply_pixmap(self, pm: QPixmap):
        self.preview.setPixmap(
            pm.scaled(_PREVIEW_W - 8, _PREVIEW_H - 8,
                      Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---------- 导出 / 打开 ----------
    def _do_export(self):
        if not self._products or self._busy:
            return
        products = list(self._products)
        self._set_busy(True, "⏳ 正在导出 Excel...")

        def work():
            return ds.export_products_xlsx(products)

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
        try:
            os.startfile(str(ds.shop_dir()))
        except Exception:
            self.app.toast("目录尚未创建（先完成一次解析）", "info")

    def _open_product_dir(self):
        p = self._selected()
        if not p or not p.local_dir:
            return
        try:
            os.startfile(str(p.local_dir))
        except Exception:
            self.app.toast("该商品目录不存在", "info")

    def _open_detail(self):
        p = self._selected()
        if p and p.detail_url:
            QDesktopServices.openUrl(QUrl(p.detail_url))

    def _copy_link(self):
        p = self._selected()
        if not p:
            return
        link = p.share_url or p.detail_url
        QGuiApplication.clipboard().setText(link)
        self.status_label.setText("✅ 链接已复制 → 粘到抖音 App 打开可看店铺与详情图")
        self.app.toast("链接已复制，粘到抖音 App 里打开即可看完整详情", "success")

    def _clear(self):
        if self._busy:
            return
        self.input_edit.clear()
        self._products = []
        self.table.setRowCount(0)
        self.detail.clear()
        self.preview.setPixmap(QPixmap())
        self.preview.setText("（选中商品后显示主图）")
        self.open_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_label.setText("粘贴分享文案 → 🔍 解析全部")
