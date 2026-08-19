"""资产管理页（PySide6 版）：已获取资源搜索 + 详情预览 + 拼贴图导出 + 批量操作。"""

import io
import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from gui_qt.common import COLLECTED, _fmt, post_to_main
from gui_qt.pages.base import BasePage

PLATFORM_CN = {"douyin": "抖音", "weibo": "微博", "bilibili": "B站"}


class AssetPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🗂 资产管理")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题 / 作者（输入即时过滤）")
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._show_detail)
        layout.addWidget(self.list, 1)

        bar = QHBoxLayout()
        dl_btn = QPushButton("⬇️ 下载选中")
        dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        dl_btn.clicked.connect(self._download)
        bar.addWidget(dl_btn)
        bar.addWidget(QPushButton("📷 导出拼贴图", clicked=self._export_collage))
        del_btn = QPushButton("🗑 删除选中")
        del_btn.clicked.connect(self._delete)
        bar.addWidget(del_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._filter("")

    def _filter(self, kw):
        self.list.clear()
        kw = kw.strip().lower()
        shown = 0
        for info in COLLECTED:
            text = f"[{PLATFORM_CN.get(info.platform, info.platform)}] {info.title or '(无标题)'}  @{info.author or '-'}  {_fmt(info.play_count)}"
            if kw and kw not in text.lower():
                continue
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, id(info))
            self.list.addItem(it)
            shown += 1
        self.count_label.setText(f"显示 {shown} / 共 {len(COLLECTED)}")

    def _selected_infos(self):
        ids = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
               if self.list.item(i).isSelected()}
        return [info for info in COLLECTED if id(info) in ids]

    def _show_detail(self, item):
        info = next((i for i in COLLECTED if id(i) == item.data(Qt.UserRole)), None)
        if not info:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"详情 · {(info.title or '')[:30]}")
        dlg.resize(640, 560)
        v = QVBoxLayout(dlg)
        title_lbl = QLabel(info.title or "(无标题)")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)
        plat = PLATFORM_CN.get(info.platform, info.platform)
        lines = [
            ("平台", plat), ("作者", "@" + (info.author or "-")),
            ("播放/阅览", _fmt(info.play_count)), ("点赞", _fmt(info.like_count)),
            ("评论", _fmt(info.comment_count)), ("发布时间", info.publish_time or "-"),
            ("链接", info.video_url or info.raw_url or "-"),
        ]
        for k, val in lines:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{k}:", styleSheet="color: #888;"))
            wl = QLabel(str(val))
            wl.setWordWrap(True)
            row.addWidget(wl, 1)
            v.addLayout(row)
        if info.description:
            v.addWidget(QLabel("正文", styleSheet="color: #888; font-weight: bold;"))
            desc = QLabel(info.description)
            desc.setWordWrap(True)
            v.addWidget(desc)
        # 封面图
        if info.cover_url:
            self._load_cover(dlg, info.cover_url, v)
        # 底部按钮
        btn_row = QHBoxLayout()
        exp = QPushButton("📷 导出预览图")
        exp.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        exp.clicked.connect(lambda: self._export_collage([info]))
        btn_row.addStretch(1)
        btn_row.addWidget(exp)
        v.addLayout(btn_row)
        dlg.exec()

    def _load_cover(self, dlg, url, layout):
        lbl = QLabel("加载封面中...")
        layout.addWidget(lbl)

        def work():
            import requests
            try:
                r = requests.get(url, timeout=15)
                return r.content
            except Exception:
                return None

        def done(data, err):
            if data:
                img = QImage.fromData(data)
                lbl.setPixmap(QPixmap.fromImage(img).scaled(320, 320, Qt.KeepAspectRatio))
                lbl.setText("")

        from gui_qt.common import run_bg
        run_bg(work, done)

    def _export_collage(self, infos=None):
        infos = infos or self._selected_infos()
        if not infos:
            QMessageBox.warning(self, "提示", "请先选中资源")
            return
        from PySide6.QtWidgets import QFileDialog

        def work():
            import requests
            from PIL import Image, ImageDraw
            covers = []
            for info in infos:
                if info.cover_url:
                    try:
                        r = requests.get(info.cover_url, timeout=15)
                        img = Image.open(io.BytesIO(r.content)).convert("RGB")
                        covers.append(img)
                    except Exception:
                        continue
            if not covers:
                return "无封面图可导出"
            # 拼网格（每行 3 张）
            cols = 3
            cell = 300
            rows = (len(covers) + cols - 1) // cols
            final = Image.new("RGB", (cols * cell, rows * cell), (255, 255, 255))
            for i, img in enumerate(covers):
                img = img.resize((cell, cell))
                final.paste(img, ((i % cols) * cell, (i // cols) * cell))
            out = os.path.join(str(getattr(self.app, 'work_dir', os.getcwd())), f"拼贴图_{len(covers)}张.png")
            final.save(out)
            return out

        def done(result, err):
            if err:
                QMessageBox.critical(self, "错误", str(err))
            elif isinstance(result, str) and result.startswith("无封面"):
                QMessageBox.warning(self, "提示", result)
            else:
                self.app.toast(f"✅ 拼贴图已导出: {result}")
                os.startfile(result)

        from gui_qt.common import run_bg
        run_bg(work, done)

    def _download(self):
        infos = self._selected_infos()
        if not infos:
            QMessageBox.warning(self, "提示", "请先选中资源")
            return

        def work():
            from media_downloader import download_media
            from toolbox.config_manager import get_export_subdir
            out = str(get_export_subdir("video_data"))
            ok = 0
            for info in infos:
                url = info.video_url or info.raw_url
                if not url:
                    continue
                try:
                    download_media(url, out)
                    ok += 1
                except Exception:
                    pass
            return ok

        def done(result, err):
            self.app.toast(f"✅ 下载完成 {result}/{len(infos)}")

        from gui_qt.common import run_bg
        run_bg(work, done)

    def _delete(self):
        infos = self._selected_infos()
        if not infos:
            return
        for info in infos:
            if info in COLLECTED:
                COLLECTED.remove(info)
        self._filter(self.search.text())
