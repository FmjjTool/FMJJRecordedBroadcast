# -*- coding: utf-8 -*-
"""资产页「图识别 · 以图搜图」弹窗。

选择一张本地图片，用用户自行下载的本地 CLIP 图像编码器（ONNX）提取特征，
在「已获取资源」的本地图片里找视觉最接近的条目；双击结果打开资产详情。
L2：只负责交互与渲染，图片收集、缓存与相似度由 toolbox.asset_image_search 提供。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import common, table_sort, theme
from gui_qt.common import run_bg
from toolbox import asset_image_search, image_embedding
from toolbox.config_manager import load_convert_config, save_convert_config

_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.webp *.gif *.bmp)"
_HF_REPO = "https://huggingface.co/Xenova/clip-vit-base-patch32/tree/main/onnx"
_HF_MIRROR = "https://hf-mirror.com/Xenova/clip-vit-base-patch32/tree/main/onnx"


def _styles() -> str:
    return """
    QDialog#imageSearchDialog { background: palette(window); }
    QFrame#imageSearchCard { background: palette(base); border: 1px solid palette(shadow); border-radius: 12px; }
    QFrame#imageSearchCard QLabel { background: transparent; border: none; }
    QLabel#imageSearchTitle { color: palette(window-text); font-size: 14px; font-weight: bold; }
    QLabel#imageSearchHint { color: palette(placeholder-text); font-size: 11px; }
    QLabel#imageSearchStatus { color: palette(placeholder-text); font-size: 12px; }
    QLabel#imageSearchPreview { background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 10px; color: palette(placeholder-text); }
    QTableWidget#imageSearchTable { border: 1px solid palette(shadow); border-radius: 10px; gridline-color: palette(shadow); }
    """


class ImageSearchDialog(QDialog):
    """以图搜图：模型状态 → 选图 → 建索引/匹配 → 打开资产详情。"""

    def __init__(self, parent=None, *, entries=None, entries_provider=None,
                 open_detail=None, owner=None):
        super().__init__(parent)
        self.setObjectName("imageSearchDialog")
        self.setWindowTitle("图识别 · 以图搜图")
        self.resize(1040, 720)
        self.setMinimumSize(760, 540)
        self.setStyleSheet(_styles())
        self.setAcceptDrops(True)

        self._entries = tuple(entries or ())
        self._entries_provider = entries_provider
        self._open_detail = open_detail
        self._owner = owner
        self._query_path = ""
        self._running = False
        self._indexing = False
        self._closed = False
        self._cancel = threading.Event()
        self._index_cancel = threading.Event()
        self._model_ready = False
        self._partial_count = 0
        self._partial_indexed = 0
        self._thumb_cache: dict[str, QPixmap | None] = {}
        self._last_result_signature = None

        self._build_ui()
        self._refresh_model()

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        model_card = QFrame()
        model_card.setObjectName("imageSearchCard")
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(12, 10, 12, 10)
        model_layout.setSpacing(6)
        model_layout.addWidget(self._title_label("本地模型"))
        self.model_status = QLabel("")
        self.model_status.setObjectName("imageSearchStatus")
        self.model_status.setWordWrap(True)
        model_layout.addWidget(self.model_status)
        self.model_hint = QLabel("")
        self.model_hint.setObjectName("imageSearchHint")
        self.model_hint.setWordWrap(True)
        self.model_hint.setTextFormat(Qt.RichText)
        self.model_hint.setOpenExternalLinks(True)
        model_layout.addWidget(self.model_hint)
        model_actions = QHBoxLayout()
        model_actions.setSpacing(8)
        self.open_dir_btn = QPushButton("打开模型目录")
        self.open_dir_btn.clicked.connect(self._open_model_dir)
        model_actions.addWidget(self.open_dir_btn)
        self.choose_model_btn = QPushButton("选择模型文件")
        self.choose_model_btn.clicked.connect(self._choose_model)
        model_actions.addWidget(self.choose_model_btn)
        self.refresh_model_btn = QPushButton("重新检测")
        self.refresh_model_btn.clicked.connect(self._refresh_model)
        model_actions.addWidget(self.refresh_model_btn)
        model_actions.addStretch(1)
        model_layout.addLayout(model_actions)
        layout.addWidget(model_card)

        query_card = QFrame()
        query_card.setObjectName("imageSearchCard")
        query_layout = QHBoxLayout(query_card)
        query_layout.setContentsMargins(12, 10, 12, 10)
        query_layout.setSpacing(12)
        self.preview = QLabel("选择或拖入一张图片")
        self.preview.setObjectName("imageSearchPreview")
        self.preview.setFixedSize(132, 132)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setWordWrap(True)
        query_layout.addWidget(self.preview)
        query_right = QVBoxLayout()
        query_right.setSpacing(6)
        query_right.addWidget(self._title_label("待识别图片"))
        self.query_path_label = QLabel("未选择图片")
        self.query_path_label.setObjectName("imageSearchHint")
        self.query_path_label.setWordWrap(True)
        query_right.addWidget(self.query_path_label, 1)
        query_actions = QHBoxLayout()
        query_actions.setSpacing(8)
        self.choose_image_btn = QPushButton("选择图片")
        self.choose_image_btn.setProperty("variant", "primary")
        self.choose_image_btn.clicked.connect(self._choose_image)
        query_actions.addWidget(self.choose_image_btn)
        query_actions.addStretch(1)
        query_right.addLayout(query_actions)
        query_layout.addLayout(query_right, 1)
        layout.addWidget(query_card)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.index_btn = QPushButton("建立特征索引")
        self.index_btn.setToolTip("不需要选择图片；预建/补齐已获取资源的图片特征，之后识别更快")
        self.index_btn.clicked.connect(self._build_index)
        run_row.addWidget(self.index_btn)
        self.start_btn = QPushButton("开始识别")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.clicked.connect(self._start)
        run_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        run_row.addWidget(self.stop_btn)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(14)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        run_row.addWidget(self.progress, 1)
        layout.addLayout(run_row)

        self.status = QLabel("首次识别会为已获取资源的本地图片建立特征缓存，可随时停止，下次继续。")
        self.status.setObjectName("imageSearchStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("imageSearchTable")
        self.table.setHorizontalHeaderLabels(["匹配度", "作品标题", "平台 / 作者", "命中图片"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(64, 40))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((86, 420, 220, 260)):
            self.table.setColumnWidth(index, width)
        self.table.cellDoubleClicked.connect(self._open_row)
        # 记录存在匹配度单元格的 UserRole 里，直接按单元格排序即可
        self._sorter = table_sort.install(self.table, skip_columns=())
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        hint = QLabel("双击结果打开资产详情；匹配度是图片特征余弦相似度，"
                      "适合找同一张或近似构图的图片，裁切、拼接不保证命中。")
        hint.setObjectName("imageSearchHint")
        hint.setWordWrap(True)
        footer.addWidget(hint, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    @staticmethod
    def _title_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("imageSearchTitle")
        return label

    # ==================== 模型 ====================

    def _configured_model(self) -> str:
        try:
            return str(load_convert_config().get("image_search_model") or "")
        except Exception:  # noqa: BLE001 - 配置读取失败按默认目录查找
            return ""

    def _model_hint_html(self, reason: str) -> str:
        link = theme.color("link")
        return (
            f"<div>{reason}</div>"
            "<div>需要本地 CLIP 图像编码器（ONNX）：</div>"
            "<ol>"
            "<li>到「全局设置 → 检测依赖」的「图识别模型」一行点「下载」"
            "（约 85 MB，支持断点续传）；</li>"
            f"<li>也可从 <a href=\"{_HF_MIRROR}\" style=\"color:{link}\">国内镜像</a>"
            f"或 <a href=\"{_HF_REPO}\" style=\"color:{link}\">Hugging Face</a> 手动下载"
            " vision_model_quantized.onnx / vision_model.onnx；</li>"
            "<li>已下载的文件可点「选择模型文件」直接指定。</li>"
            "</ol>"
        )

    def _refresh_model(self) -> None:
        status = image_embedding.model_status(self._configured_model())
        self._model_ready = bool(status.get("ready"))
        if self._model_ready:
            path = Path(str(status.get("path") or ""))
            size_mb = float(status.get("size") or 0) / (1024 ** 2)
            self.model_status.setText(f"已就绪：{path.name}（{size_mb:.0f} MB）")
            self.model_hint.setVisible(False)
        else:
            self.model_status.setText("未就绪")
            self.model_hint.setText(self._model_hint_html(str(status.get("reason") or "")))
            self.model_hint.setVisible(True)
        self._sync_run_buttons()

    def _sync_run_buttons(self) -> None:
        idle = not self._running and not self._indexing
        self.start_btn.setEnabled(bool(self._model_ready and self._query_path and idle))
        self.index_btn.setEnabled(bool(self._model_ready and idle))
        self.choose_image_btn.setEnabled(idle)
        self.stop_btn.setEnabled(not idle)
        self.choose_model_btn.setEnabled(True)
        self.refresh_model_btn.setEnabled(True)

    def _choose_model(self) -> None:
        configured = self._configured_model()
        start = Path(configured).parent if configured else image_embedding.default_model_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CLIP 图像编码器", str(start), "ONNX 模型 (*.onnx)")
        if not path:
            return
        try:
            config = load_convert_config()
            config["image_search_model"] = path
            save_convert_config(config)
        except Exception:  # noqa: BLE001 - 配置保存失败仍按本次选择使用
            pass
        self._refresh_model()

    def _open_model_dir(self) -> None:
        directory = image_embedding.default_model_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "目录不可用", f"无法创建模型目录：{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    # ==================== 选图 ====================

    def _choose_image(self) -> None:
        start = str(Path(self._query_path).parent) if self._query_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", start, _IMAGE_FILTER)
        if path:
            self._set_query(path)

    def _set_query(self, path: str) -> None:
        if self._running:
            return
        if Path(str(path)).suffix.lower() not in image_embedding.IMAGE_EXTS:
            QMessageBox.information(self, "格式不支持", "请选择 JPG / PNG / WebP / GIF / BMP 图片。")
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "图片不存在", "选中的图片已不存在，请重新选择。")
            return
        self._query_path = path
        pixmap = self._scaled_pixmap(path, QSize(126, 126))
        if pixmap is None:
            self.preview.setText("无法预览")
        else:
            self.preview.setPixmap(pixmap)
        self.query_path_label.setText(path)
        self.query_path_label.setToolTip(path)
        self._sync_run_buttons()

    def _thumb(self, path: str) -> QPixmap | None:
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pixmap = self._scaled_pixmap(path, QSize(64, 40))
        if len(self._thumb_cache) > 300:
            self._thumb_cache.clear()
        self._thumb_cache[path] = pixmap
        return pixmap

    @staticmethod
    def _scaled_pixmap(path: str, size: QSize) -> QPixmap | None:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        source = reader.size()
        if source.isValid():
            reader.setScaledSize(source.scaled(size, Qt.KeepAspectRatio))
        image = reader.read()
        return None if image.isNull() else QPixmap.fromImage(image)

    def dragEnterEvent(self, event) -> None:
        if self._dropped_image(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        path = self._dropped_image(event.mimeData())
        if path:
            self._set_query(path)
            event.acceptProposedAction()

    @staticmethod
    def _dropped_image(mime) -> str:
        if mime is None or not mime.hasUrls():
            return ""
        for url in mime.urls():
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in image_embedding.IMAGE_EXTS:
                return path
        return ""

    # ==================== 识别 ====================

    def _build_index(self) -> None:
        """不选图也能预建/补齐已获取资源的图片特征。"""
        if self._running or self._indexing:
            return
        status = image_embedding.model_status(self._configured_model())
        if not status.get("ready"):
            self._refresh_model()
            QMessageBox.warning(
                self, "模型未就绪",
                str(status.get("reason") or "请先下载模型并放入模型目录。"))
            return
        entries = self._entries or None
        provider = self._entries_provider
        model_path = str(status.get("path") or "")
        self._indexing = True
        self._index_cancel.clear()
        self._partial_count = 0
        self._partial_indexed = 0
        self._sync_run_buttons()
        self.progress.setRange(0, 0)
        self.progress.setFormat("准备中…")
        self.progress.setVisible(True)
        self.status.setText("正在准备已获取资源清单...")

        def work():
            source = entries if entries is not None else provider() or []
            if not source:
                raise RuntimeError("已获取资源清单为空，请先刷新资产页")
            return asset_image_search.prepare_index(
                source, model_path,
                progress=lambda event: common.post_to_main(self._on_progress, event),
                cancel=self._index_cancel)

        thread = run_bg(work, self._finish_index, owner=self._owner)
        if thread is None:
            self._indexing = False
            self.progress.setVisible(False)
            self._sync_run_buttons()
            self.status.setText("建立特征索引任务未启动，请重试。")

    def _finish_index(self, result, err) -> None:
        self._indexing = False
        if self._closed:
            self._dispose_when_idle()
            return
        self.progress.setVisible(False)
        self._sync_run_buttons()
        if err:
            text = str(err)
            if "已停止" in text:
                self.status.setText(f"{text}；已完成的特征会保留，下次继续。")
            else:
                self.status.setText(f"建立特征索引失败：{text}")
                QMessageBox.warning(self, "建立特征索引失败", text)
            return
        stats = result or {}
        skipped = int(stats.get("skipped") or 0)
        note = f" · 跳过不可读 {skipped} 张（悬停查看）" if skipped else ""
        self.status.setText(
            f"特征索引完成：已索引 {stats.get('indexed', 0)}/{stats.get('images', 0)} 张"
            f" · 本次新增 {stats.get('embedded', 0)} 张{note}"
            f" · 耗时 {stats.get('seconds', 0)} 秒；选图后识别会直接复用。")
        self.status.setToolTip(self._skipped_tooltip(stats) if skipped else "")
        if skipped:
            preview = "\n".join(
                f"{Path(str(path)).name}：{reason}"
                for path, reason in (stats.get("skipped_items") or [])[:10])
            QMessageBox.information(
                self, "部分图片未纳入索引",
                f"有 {skipped} 张图片无法读取，已跳过（空文件、下载不完整或格式损坏）：\n\n"
                f"{preview}" + ("\n…" if skipped > 10 else "")
                + "\n\n可在资产页对这些条目重新下载后再建索引。")

    def _start(self) -> None:
        if self._running:
            return
        if not self._query_path:
            QMessageBox.information(self, "尚未选图", "请先选择一张要识别的图片。")
            return
        status = image_embedding.model_status(self._configured_model())
        if not status.get("ready"):
            self._refresh_model()
            QMessageBox.warning(
                self, "模型未就绪",
                str(status.get("reason") or "请先下载模型并放入模型目录。"))
            return

        try:
            top_k = int(load_convert_config().get("image_search_topk", 30) or 30)
        except Exception:  # noqa: BLE001
            top_k = 30
        entries = self._entries or None
        provider = self._entries_provider
        model_path = str(status.get("path") or "")
        query_path = self._query_path

        self._running = True
        self._cancel.clear()
        self._partial_count = 0
        self._partial_indexed = 0
        self._last_result_signature = None
        self._sync_run_buttons()
        self.table.setRowCount(0)
        self.progress.setRange(0, 0)
        self.progress.setFormat("准备中…")
        self.progress.setVisible(True)
        self.status.setText("正在准备已获取资源清单...")

        def work():
            source = entries if entries is not None else provider() or []
            if not source:
                raise RuntimeError("已获取资源清单为空，请先刷新资产页")
            return asset_image_search.search(
                source, query_path, model_path, top_k=top_k,
                progress=lambda event: common.post_to_main(self._on_progress, event),
                cancel=self._cancel,
                on_partial=lambda rows, stats: common.post_to_main(
                    self._on_partial, rows, stats))

        thread = run_bg(work, self._finish, owner=self._owner)
        if thread is None:
            self._running = False
            self.progress.setVisible(False)
            self._sync_run_buttons()
            self.status.setText("识别任务未启动，请重试。")

    def _stop(self) -> None:
        if self._indexing:
            self._index_cancel.set()
            self.stop_btn.setEnabled(False)
            self.status.setText("正在停止建立特征索引...")
            return
        if not self._running:
            return
        self._cancel.set()
        self.stop_btn.setEnabled(False)
        self.status.setText("正在停止识别...")

    def _on_progress(self, event: dict) -> None:
        if self._closed:
            return
        message = str(event.get("message") or "")
        if self._cancel.is_set():
            message = "正在停止识别..."
        stage = str(event.get("stage") or "")
        total = int(event.get("total") or 0)
        done = int(event.get("done") or 0)
        if stage == "embed" and total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat("%p%")
        else:
            self.progress.setRange(0, 0)
            self.progress.setFormat("处理中…")
        if message:
            if self._partial_count:
                message = (f"{message} · 暂定 {self._partial_count} 条"
                           f"（已用 {self._partial_indexed} 张）")
            self.status.setText(message)

    def _on_partial(self, results, stats) -> None:
        """索引过程中用已提取部分刷新暂定结果，不阻塞继续提取。"""
        if self._closed or not self._running:
            return
        self._partial_count = len(results or [])
        self._partial_indexed = int((stats or {}).get("indexed") or 0)
        self._fill_results(list(results or []))

    def _finish(self, result, err) -> None:
        self._running = False
        if self._closed:
            self._dispose_when_idle()
            return
        self.progress.setVisible(False)
        self._sync_run_buttons()
        if err:
            text = str(err)
            if "已停止" in text:
                if "已缓存" in text:
                    self.status.setText(f"{text}；下次识别会直接复用。")
                else:
                    self.status.setText("已停止识别；已完成的特征会保留在缓存里。")
            elif self._partial_count:
                self.status.setText(
                    f"识别失败（保留已提取 {self._partial_indexed} 张的暂定结果）：{text}")
            else:
                self.status.setText(f"识别失败：{text}")
                QMessageBox.warning(self, "识别失败", text)
            return
        rows = (result or {}).get("results") or []
        stats = (result or {}).get("stats") or {}
        stopped = bool((result or {}).get("stopped"))
        self._fill_results(rows)
        if stopped:
            self._partial_count = len(rows)
            self._partial_indexed = int(stats.get("indexed") or 0)
            self.status.setText(
                f"已停止提取，按已索引 {stats.get('indexed', 0)}/{stats.get('images', 0)} 张"
                f"显示 {len(rows)} 条暂定结果；下次识别会继续复用缓存。")
            self.status.setToolTip(self._skipped_tooltip(stats)
                                   if stats.get("skipped") else "")
            return
        self._partial_count = len(rows)
        self._partial_indexed = int(stats.get("indexed") or 0)
        self.status.setText(
            f"命中 {len(rows)} 个条目 · 索引 {stats.get('indexed', 0)}/{stats.get('images', 0)} 张图片"
            f" · 缓存命中 {stats.get('cache_hits', 0)} 张"
            f" · 跳过读取失败 {stats.get('skipped', 0)} 张"
            f" · 用时 {stats.get('seconds', 0)} 秒")
        self.status.setToolTip(self._skipped_tooltip(stats)
                               if stats.get("skipped") else "")

    @staticmethod
    def _skipped_tooltip(stats: dict) -> str:
        items = stats.get("skipped_items") or []
        lines = [f"{Path(str(path)).name}：{reason}" for path, reason in items[:20]]
        return "以下图片无法读取，已跳过（文件损坏或格式不支持）：\n" + "\n".join(lines)

    def _fill_results(self, rows: list[dict]) -> None:
        signature = tuple(
            (str((row.get("entry") or {}).get("title") or ""),
             round(float(row.get("score") or 0), 4),
             str(row.get("image") or ""))
            for row in rows)
        if signature == self._last_result_signature:
            return
        self._last_result_signature = signature
        self.table.setRowCount(len(rows))
        for row, result in enumerate(rows):
            entry = result.get("entry") or {}
            score_item = QTableWidgetItem(f"{float(result.get('score') or 0) * 100:.1f}%")
            score_item.setData(Qt.UserRole, result)
            self.table.setItem(row, 0, score_item)

            title = str(entry.get("title") or "(无标题)")
            title_item = QTableWidgetItem(title)
            title_item.setToolTip(title)
            self.table.setItem(row, 1, title_item)

            platform = str(entry.get("platform") or "")
            author = str(entry.get("author") or "").strip()
            meta = f"{platform} · @{author}" if author else platform
            meta_item = QTableWidgetItem(meta)
            meta_item.setToolTip(meta)
            self.table.setItem(row, 2, meta_item)

            image_path = str(result.get("image") or "")
            image_item = QTableWidgetItem(os.path.basename(image_path))
            image_item.setToolTip(image_path)
            pixmap = self._thumb(image_path)
            if pixmap is not None:
                image_item.setIcon(pixmap)
            self.table.setItem(row, 3, image_item)
        self._sorter.reapply()

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        result = item.data(Qt.UserRole) if item is not None else None
        if not result or not callable(self._open_detail):
            return
        try:
            self._open_detail(result.get("entry"))
        except Exception as exc:  # noqa: BLE001 - 详情失败不影响检索结果
            QMessageBox.warning(self, "无法打开详情", str(exc))

    # ==================== 关闭 ====================

    def done(self, result: int) -> None:
        self._closed = True
        self._cancel.set()
        self._index_cancel.set()
        self._entries = ()
        self._entries_provider = None
        self._open_detail = None
        self._thumb_cache.clear()
        self.table.setRowCount(0)
        self.preview.clear()
        super().done(result)
        self._dispose_when_idle()

    def _dispose_when_idle(self):
        # worker/已排队的回调可能还持有此对象，先等正常取消收尾再销毁 Qt 子树。
        if self._closed and not self._running and not self._indexing:
            self.deleteLater()
