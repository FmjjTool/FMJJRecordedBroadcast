"""资产管理页（PySide6 版）：已获取资源磁盘扫描 + 本地资源文件夹扫描，双标签。

对应原版 gui/asset_page.py：
- 已获取资源：自动扫描 download/已获取数据/ 下历史导出（封面/本地媒体缩略图 + 摘要），
  支持平台筛选、关键词搜索（防抖）、状态筛选（未下载/已下载/下载不完整）、时间排序、
  点击行弹详情窗、单条下载（B站 draw/av 分发、抖音重解析、图片兜底）、删除（json+md+xlsx+raw+媒体）
- 本地资源：选文件夹 → 扫描图片/视频/音频 → 缩略图（图片）+ 摘要（视频/音频）懒加载预览，
  支持扫描上限（0=全部）、分批间隔（限流）、增量模式（扫描结果增量写入索引文件）
- 详情窗：完整信息 + 正文 + 媒体文件网格（5 列）+ 📷 导出预览图（紫色渐变拼贴）
"""

import io
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QMimeData, QObject, QPoint, QPointF, QRect, QSize,
                            Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QImage, QPainter,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtWidgets import QLayout

from gui_qt import common
from gui_qt.common import _fmt, icon_button, run_bg
from gui_qt.pages.base import BasePage
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.utils import PLATFORM_CN, platform_cn

# 支持的资源扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".flv", ".avi", ".ts", ".m4v", ".wmv"}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus"}

BATCH = 20  # 懒加载：每批渲染数量

# 平台筛选下拉显示中文名 -> 英文标识（筛选用英文键）
_CN2EN = {cn: en for en, cn in PLATFORM_CN.items()}


class _ProgressBridge(QObject):
    """工作线程 → GUI 线程的进度信号桥（queued connection 保证线程安全）。"""
    updated = Signal(int, int, str)

    def emit_progress(self, done: int, total: int, name: str):
        self.updated.emit(done, total, name)

    def emit_bytes(self, done: int, total: int):
        """大文件字节级进度（走同一信号，GUI 按 total 量级区分为字节模式）。"""
        self.updated.emit(done, total, "")


def _make_progress_dlg(parent, title: str, total: int, cancel_event: threading.Event):
    """构造下载进度对话框：实时显示 N/M + 当前文件名，可取消。"""
    dlg = QProgressDialog("准备下载...", "取消", 0, max(total, 1), parent)
    dlg.setWindowTitle(title)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setMinimumDuration(0)
    dlg.setMinimumWidth(420)
    dlg.canceled.connect(cancel_event.set)
    dlg.show()
    return dlg


def _fmt_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"


def _parse_duration_text(text) -> int:
    """json「时长」字段 → 秒。兼容 'M:SS' / 'H:MM:SS' / 纯数字秒。"""
    s = str(text or "").strip()
    if not s:
        return 0
    if re.fullmatch(r"\d+", s):
        return int(s)
    parts = s.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        return 0
    vals = [int(p) for p in parts]
    if len(vals) == 3:
        return vals[0] * 3600 + vals[1] * 60 + vals[2]
    if len(vals) == 2:
        return vals[0] * 60 + vals[1]
    return vals[0]


def _fmt_duration_cn(sec) -> str:
    """秒 → 中文时长，各段两位数：05秒 / 01分19秒 / 01时02分03秒。"""
    sec = int(sec or 0)
    if sec <= 0:
        return ""
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}时{m:02d}分{s:02d}秒"
    if m:
        return f"{m:02d}分{s:02d}秒"
    return f"{s:02d}秒"


_DURATION_CACHE: dict = {}


def _probe_video_duration(path: str) -> int:
    """ffprobe 读本地视频时长（秒，取整）。带缓存；ffmpeg 缺失/失败返回 0。"""
    if not path:
        return 0
    mtime = os.path.getmtime(path) if os.path.exists(path) else 0
    key = (path, mtime)
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    dur = 0
    try:
        import subprocess
        from toolbox.live_video import find_ffmpeg
        ff = find_ffmpeg()
        if ff:
            probe = str(Path(ff)).replace("ffmpeg.exe", "ffprobe.exe")
            if os.path.exists(probe):
                r = subprocess.run(
                    [probe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", path],
                    capture_output=True, timeout=15, encoding="utf-8", errors="replace")
                if r.returncode == 0 and r.stdout.strip():
                    dur = int(float(r.stdout.strip()))
    except Exception:
        dur = 0
    _DURATION_CACHE[key] = dur
    return dur


_WRAP_CHARS = ("/", "\\", "?", "&", "=", "_", "-", ".", "%", ";", ",")


def _wrappable(text: str) -> str:
    """在分隔符后插入零宽空格，让 QLabel 能在长 URL/路径内断行。

    QLabel wordWrap 的换行与 minimumSizeHint 都取「最长单词」：连续长串
    （CDN 直链、本地路径）无断点时既换不了行、最小宽度还等于整行宽度，
    会把详情窗内容体撑出横向滚动条。插零宽空格后视觉不变但处处可断。
    """
    for ch in _WRAP_CHARS:
        if ch in text:
            text = text.replace(ch, ch + "\u200b")
    return text


class _FlowLayout(QLayout):
    """流式布局（Qt 官方 FlowLayout 模式）：子项按宽度自动换行成多行。

    详情窗媒体缩略图用固定 5 列网格时，窗口缩窄会被网格最小尺寸顶出
    横向滚动条；流式布局按实际宽度增减每行列数，窄窗自动多行换行。
    """

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_h = 0
        for it in self._items:
            hint = it.sizeHint()
            nxt = x + hint.width() + self._spacing
            if nxt - self._spacing > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y = y + line_h + self._spacing
                nxt = x + hint.width() + self._spacing
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nxt
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


def _classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


def _sanitize_title_key(title: str) -> str:
    """规范化标题用于媒体匹配键（只留中英文/数字，去掉下划线和所有标点/符号）。"""
    s = re.sub(r'[^\w\u4e00-\u9fff]', '', title or "")
    return re.sub(r'_', '', s)


def _norm_subdir(subdir: str) -> str:
    """规范化平台子目录名（新旧结构/中英文统一为英文 platform）。"""
    from toolbox.utils import norm_platform_dir
    return norm_platform_dir(subdir)


def scan_assets(folder: str, limit: int = 0) -> list:
    """扫描文件夹里的图片/视频/音频，返回文件信息列表。limit=0 表示无上限。"""
    results = []
    try:
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            kind = _classify(name)
            if not kind:
                continue
            st = os.stat(full)
            results.append({
                "path": full, "name": name, "size": st.st_size,
                "type": kind, "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if limit and len(results) >= limit:
                break
    except Exception:
        pass
    return results


def _index_file() -> str:
    """资产管理索引文件（增量模式写入）。"""
    from toolbox.config_manager import get_export_subdir
    return str(get_export_subdir("video_data") / "_assets_index.json")


def append_index(folder: str, files: list):
    """增量写入索引文件（追加去重）。"""
    path = _index_file()
    existing = []
    if os.path.exists(path):
        try:
            existing = json.load(open(path, encoding="utf-8"))
        except Exception:
            existing = []
    seen = {(e.get("path"), e.get("name")) for e in existing}
    for f in files:
        key = (f.get("path"), f.get("name"))
        if key not in seen:
            seen.add(key)
            existing.append(f)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(existing, fp, ensure_ascii=False, indent=2)


class _RowWidget(QWidget):
    """可点击行：点击任意位置触发 clicked 回调（对应原版整行 <Button-1>）；右键弹菜单。"""

    def __init__(self, on_click, on_context=None, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self._on_context = on_context

    def mousePressEvent(self, event):
        # 列表刷新时行已被 deleteLater，但点击事件仍在派发队列中——直接忽略
        try:
            if event.button() == Qt.LeftButton and self._on_click:
                self._on_click()
            super().mousePressEvent(event)
        except RuntimeError:
            pass

    def contextMenuEvent(self, event):
        try:
            if self._on_context:
                gp = event.globalPos()   # PySide6 返回 QPoint（个别绑定是 QPointF）
                self._on_context(gp if isinstance(gp, QPoint) else gp.toPoint())
            super().contextMenuEvent(event)
        except RuntimeError:
            pass


class _ThumbLabel(QLabel):
    """缩略图：吞掉单击（不触发行点击/详情窗），双击播放本地视频。"""

    def __init__(self, on_dblclick, parent=None):
        super().__init__(parent)
        self._on_dblclick = on_dblclick

    def mousePressEvent(self, event):
        event.accept()  # 阻止冒泡到 _RowWidget（单击缩略图不弹详情窗）

    def mouseDoubleClickEvent(self, event):
        try:
            if event.button() == Qt.LeftButton and self._on_dblclick:
                self._on_dblclick()
        except RuntimeError:
            pass


class AssetPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._all_files = []            # 本地扫描结果全量
        self._shown = 0                 # 已渲染数量
        self._folder = ""
        self._disk_items = []           # 磁盘已下载数据全量
        self._collected_all_items = []  # 当前平台筛选后的列表
        self._collected_page = 1        # 当前页码（分页渲染）
        self._dl_state = {}             # 条目下载状态：'downloading' / 'done'
        self._search_var = ""           # 搜索关键词
        self._checked_keys = set()      # 勾选条目键（批量下载用）
        self._reload_gen = 0            # 骨架加载代数守卫（中途刷新作废旧轮）
        self._db_page = 1               # 数据库浏览当前页码
        self._db_total = 0              # 数据库浏览总条数
        self._db_rows = []              # 当前页原始行（含 dedup_key）
        self._db_filling = False        # 数据库表填充中（抑制勾选计数刷新）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(8, 6, 8, 0)
        head.addWidget(QLabel("🗂 资产管理", styleSheet="font-size: 16px; font-weight: bold;"))
        head.addStretch(1)
        backup_btn = QPushButton("💾 备份")
        backup_btn.setToolTip(
            "备份所有资产（已获取数据 / 媒体文件 / 数据库快照）为 zip 包或文件夹，\n"
            "支持导入恢复（文件不覆盖，数据库按去重键合并）")
        backup_btn.clicked.connect(self._show_backup_menu)
        head.addWidget(backup_btn)
        from gui_qt.help_overlay import attach_help_overlay
        head.addWidget(attach_help_overlay(self, "❓ 资产管理 · 功能介绍", (
            "<p><b>已获取内容与本地媒体资产的统一管理中心（三个 Tab）。</b></p>"
            "<p>· <b>已获取资源</b>：自动扫描 download/已获取数据/ 下历史导出，"
            "支持平台/状态筛选（未下载/已下载/下载不完整）、关键词搜索、时间排序、分页浏览；"
            "点击行看详情、双击缩略图播放本地视频；<b>右键行</b>可打开本地 MD 数据文件/"
            "媒体文件（存在哪项才显示哪项）</p>"
            "<p>· <b>下载</b>：单条 ↻ 重新下载（B站视频/图文、抖音视频、图片、封面兜底），"
            "勾选后批量下载（已完整的自动跳过，大文件显示字节进度，可取消）</p>"
            "<p>· <b>删除</b>：可选删除 json/md/xlsx/_raw/媒体文件</p>"
            "<p>· <b>数据库浏览</b>：直查已获取数据库（筛选/排序/勾选导出，右键打开链接/筛选作者）</p>"
            "<p>· <b>本地资源</b>：选择文件夹扫描图片/视频/音频，缩略图懒加载预览，"
            "支持扫描上限与增量索引</p>"
            "<p>· <b>备份</b>：右上角 💾 导出所有资产（zip 包 / 文件夹）或导入恢复</p>"
            "<p>· 详情窗支持导出紫色渐变预览拼贴图 📷</p>")))
        layout.addLayout(head)

        # 顺序：已获取资源 | 数据库浏览（中间） | 本地资源
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_collected_tab(), "已获取资源")
        self.tabs.addTab(self._build_db_tab(), "数据库浏览")
        self.tabs.addTab(self._build_local_tab(), "本地资源")
        # 切到数据库浏览 Tab 时刷新（采集后无需切页面，直接切 Tab 即可见新数据）
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx: int):
        if 0 <= idx < self.tabs.count() and self.tabs.tabText(idx) == "数据库浏览":
            self._db_reload_tables()

    # ==================== 资产备份（导出 / 导入） ====================

    def _show_backup_menu(self):
        """页头 💾 备份 按钮菜单：导出所有资产（zip / 文件夹）+ 导入恢复。"""
        m = QMenu(self)
        m.addAction("📦 导出所有资产（zip 包）…", self._backup_assets_zip)
        m.addAction("📁 导出所有资产（文件夹）…", self._backup_assets_folder)
        m.addSeparator()
        m.addAction("📥 导入备份（zip 包）…", self._import_backup_zip)
        m.addAction("📥 导入备份（文件夹）…", self._import_backup_folder)
        btn = self.sender()
        m.exec(btn.mapToGlobal(QPoint(0, btn.height())) if btn else None)

    def _backup_assets_zip(self):
        default = f"资产备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出所有资产", default, "zip 包 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._run_backup_export(path, "zip")

    def _backup_assets_folder(self):
        dest = QFileDialog.getExistingDirectory(
            self, "选择备份保存位置（将自动创建「资产备份_时间戳」子文件夹）")
        if not dest:
            return
        self._run_backup_export(dest, "folder")

    def _run_backup_export(self, dest: str, mode: str):
        from toolbox import backup

        cancel = threading.Event()
        bridge = _ProgressBridge()
        dlg = _make_progress_dlg(self, "备份所有资产…", 1, cancel)

        def on_ui(done, total, name):
            dlg.setMaximum(max(total, 1))
            dlg.setValue(min(done, total))
            dlg.setLabelText(f"备份中 {done}/{total}：{name}")

        bridge.updated.connect(on_ui)

        def work():
            return backup.export_assets(
                dest, mode=mode,
                on_progress=lambda d, t, n: bridge.emit_progress(d, t, n),
                cancel=cancel)

        def done(result, err):
            bridge.updated.disconnect(on_ui)
            dlg.close()
            if err:
                self.app.toast(f"备份失败：{err}", "error")
                return
            if result.get("cancelled"):
                self.app.toast("已取消备份", "warning")
                return
            self.app.toast(
                f"已备份 {result['files']} 个文件（{result['size_text']}）→ "
                f"{result['path']}", "success")

        run_bg(work, done)

    def _import_backup_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入资产备份（zip 包）", "", "资产备份 (*.zip)")
        if not path:
            return
        self._run_backup_import(path)

    def _import_backup_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "导入资产备份（选择备份文件夹）")
        if not path:
            return
        self._run_backup_import(path)

    def _run_backup_import(self, src: str):
        from toolbox import backup

        cancel = threading.Event()
        bridge = _ProgressBridge()
        dlg = _make_progress_dlg(self, "导入资产备份…", 1, cancel)

        def on_ui(done, total, name):
            dlg.setMaximum(max(total, 1))
            dlg.setValue(min(done, total))
            dlg.setLabelText(f"导入中 {done}/{total}：{name}")

        bridge.updated.connect(on_ui)

        def work():
            return backup.import_assets(
                src, on_progress=lambda d, t, n: bridge.emit_progress(d, t, n),
                cancel=cancel)

        def done(result, err):
            bridge.updated.disconnect(on_ui)
            dlg.close()
            if err == "已取消":
                self.app.toast("已取消导入", "warning")
                return
            if err:
                self.app.toast(f"导入失败：{err}", "error")
                return
            self.app.toast(
                f"导入完成：新增 {result['files_copied']} 个文件、"
                f"跳过已存在 {result['files_skipped']} 个、"
                f"数据库入库 {result['db_rows']} 条", "success")
            # 新资产已落盘 → 刷新当前视图（已获取资源 + 数据库浏览）
            self._refresh_collected()
            self._on_tab_changed(self.tabs.currentIndex())

        run_bg(work, done)

    # ==================== 本地资源 ====================

    def _build_local_tab(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        title = QLabel("📁 本地资源")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        v.addWidget(title)

        # 选文件夹行
        row = QHBoxLayout()
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("资源文件夹路径")
        row.addWidget(self.dir_entry, 1)
        pick_btn = QPushButton("📂 选择")
        pick_btn.clicked.connect(self._pick_folder)
        row.addWidget(pick_btn)
        v.addLayout(row)

        # 策略行：扫描上限 / 分批间隔 / 增量模式
        cfg = QHBoxLayout()
        cfg.addWidget(QLabel("上限:"))
        self.limit_entry = QLineEdit()
        self.limit_entry.setFixedWidth(50)
        self.limit_entry.setText("0")
        cfg.addWidget(self.limit_entry)
        cfg.addWidget(QLabel("(0=全部)"))
        cfg.addSpacing(8)
        cfg.addWidget(QLabel("分批间隔(秒):"))
        self.batch_entry = QLineEdit()
        self.batch_entry.setFixedWidth(40)
        self.batch_entry.setText("0")
        cfg.addWidget(self.batch_entry)
        cfg.addSpacing(8)
        self.inc_var = QCheckBox("增量模式(写入索引)")
        # 勾选状态持久化（下次启动保持）
        try:
            self.inc_var.setChecked(
                bool(load_convert_config().get("local_inc_enabled", False)))
        except Exception:
            pass
        self.inc_var.toggled.connect(self._save_inc_var)
        cfg.addWidget(self.inc_var)
        cfg.addStretch(1)
        scan_btn = QPushButton("🔍 扫描")
        scan_btn.setStyleSheet("background: #2e7d32; color: white;")
        scan_btn.clicked.connect(self._scan)
        cfg.addWidget(scan_btn)
        v.addLayout(cfg)

        self.local_status = QLabel("")
        self.local_status.setStyleSheet("color: #888;")
        v.addWidget(self.local_status)

        # 懒加载列表（滚动区）
        self.local_scroll, _, self.local_layout = self._make_scroll()
        v.addWidget(self.local_scroll, 1)
        return f

    def _save_inc_var(self, checked):
        """本地资源「增量模式(写入索引)」勾选状态写入 config。"""
        try:
            cfg = load_convert_config()
            cfg["local_inc_enabled"] = bool(checked)
            save_convert_config(cfg)
        except Exception:
            pass

    def _make_scroll(self):
        """构造滚动区：QScrollArea + QWidget 容器 + 垂直布局。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        lay.addStretch(1)
        scroll.setWidget(container)
        return scroll, container, lay

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择资源文件夹")
        if path:
            self._folder = path
            self.dir_entry.setText(path)

    def _scan(self):
        folder = self.dir_entry.text().strip() or self._folder
        if not folder or not os.path.isdir(folder):
            self.app.toast("请先选择有效文件夹", "warning")
            return
        try:
            limit = int(self.limit_entry.text().strip() or "0")
        except ValueError:
            limit = 0
        try:
            interval = float(self.batch_entry.text().strip() or "0")
        except ValueError:
            interval = 0
        self._folder = folder
        inc = self.inc_var.isChecked()

        def work():
            import time
            files = scan_assets(folder, limit=limit)
            # 分批限流：每 BATCH 个暂停 interval 秒（模拟/控制扫描节奏）
            if interval > 0 and len(files) > BATCH:
                for i in range(BATCH, len(files), BATCH):
                    time.sleep(interval)
            if inc and files:
                append_index(folder, files)
            return files, folder

        def done(result, err):
            if err:
                self.app.toast(str(err), "error")
                return
            files, folder = result
            self._all_files = files
            self._shown = 0
            self._render_local_batch()
            imgs = sum(1 for x in files if x["type"] == "image")
            vids = sum(1 for x in files if x["type"] == "video")
            auds = sum(1 for x in files if x["type"] == "audio")
            self.local_status.setText(
                f"共 {len(files)} 个资源（图片 {imgs} / 视频 {vids} / 音频 {auds}）"
                + ("，已增量写入索引" if inc else ""))

        run_bg(work, done)

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _render_local_batch(self):
        """懒加载：每次渲染 BATCH 个，最后挂「加载更多」按钮。"""
        lay = self.local_layout
        if self._shown == 0:
            self._clear_layout(lay)
            lay.addStretch(1)
        # 移除末尾 stretch 以便按顺序插入
        lay.takeAt(lay.count() - 1)
        end = min(self._shown + BATCH, len(self._all_files))
        for item in self._all_files[self._shown:end]:
            self._render_local_item(item)
        self._shown = end
        if self._shown < len(self._all_files):
            more = QPushButton(f"加载更多（{len(self._all_files) - self._shown} 个）")
            more.setStyleSheet(
                "background: transparent; border: 1px solid #c8cdd6; color: #6b7280; padding: 4px;")
            more.clicked.connect(self._render_local_batch)
            lay.addWidget(more)
        lay.addStretch(1)

    def _render_local_item(self, item):
        row = QFrame()
        row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        kind = item["type"]
        if kind == "image":
            lbl = self._load_local_thumb(item["path"], 90, 60)
            h.addWidget(lbl)
        else:
            icon = "🎬" if kind == "video" else "🎵"
            lbl = QLabel(icon)
            lbl.setFixedSize(90, 60)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "background: #eef1f5; border-radius: 4px; font-size: 28px;")
            h.addWidget(lbl)
        info = QLabel(f"{item['name']}\n类型: {kind}  大小: {_fmt_size(item['size'])}  {item['mtime']}")
        info.setStyleSheet("font-size: 11px;")
        info.setWordWrap(True)
        h.addWidget(info, 1)
        self.local_layout.addWidget(row)

    def _load_local_thumb(self, path, w, h):
        """后台加载本地图片缩略图，回主线程设置 pixmap。"""
        lbl = QLabel("🖼")
        lbl.setFixedSize(w, h)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #eef1f5; border-radius: 4px;")
        lbl.setToolTip(path)

        def load():
            try:
                from PIL import Image
                img = Image.open(path).convert("RGB")
                img.thumbnail((w, h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                return None

        def done(data, err):
            if data:
                qimg = QImage.fromData(data)
                if not qimg.isNull():
                    lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                        w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        run_bg(load, done)
        return lbl

    # ==================== 已获取资源（磁盘扫描） ====================

    def _build_collected_tab(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        title = QLabel("📦 已获取资源")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        v.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("平台:"))
        self.plat_sel = QComboBox()
        self.plat_sel.addItems(["全部"] + list(PLATFORM_CN.values()))
        self.plat_sel.setFixedWidth(120)
        self.plat_sel.currentTextChanged.connect(lambda _: self._refresh_collected())
        bar.addWidget(self.plat_sel)
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("🔍 搜索标题/作者/ID...")
        self.search_entry.setMinimumWidth(200)  # 可随窗口拉伸（原固定 200 不自适应）
        # 防抖：停止输入 300ms 后才触发过滤，避免每敲一个字符就重扫
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_search)
        self.search_entry.textChanged.connect(self._on_search_key)
        bar.addWidget(self.search_entry)
        self.status_sel = QComboBox()
        self.status_sel.addItems(["全部状态", "未下载", "已下载", "下载不完整"])
        self.status_sel.setFixedWidth(110)
        self.status_sel.currentTextChanged.connect(lambda _: self._refresh_collected())
        bar.addWidget(self.status_sel)
        self.sort_sel = QComboBox()
        self.sort_sel.addItems(["时间倒序", "时间正序"])
        self.sort_sel.setFixedWidth(90)
        self.sort_sel.currentTextChanged.connect(lambda _: self._refresh_collected())
        bar.addWidget(self.sort_sel)
        refresh_btn = icon_button("🔄", "刷新已获取列表", (40, 30), True, "font-size: 15px;", self._refresh_collected)
        bar.addWidget(refresh_btn)
        # 批量操作：全选 + 下载所选
        self.select_all_cb = QCheckBox("全选")
        self.select_all_cb.stateChanged.connect(self._toggle_select_all)
        bar.addWidget(self.select_all_cb)
        self.batch_dl_btn = QPushButton("📥 下载所选")
        self.batch_dl_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.batch_dl_btn.clicked.connect(self._batch_download_checked)
        bar.addWidget(self.batch_dl_btn)
        # 搜索框伸展吃掉剩余空间：窗口越宽搜索框越长（原固定 200px 导致右侧大片空白）
        bar.setStretchFactor(self.search_entry, 1)
        v.addLayout(bar)

        self.collected_scroll, _, self.collected_layout = self._make_scroll()
        # 禁横向滚动：行内文字自动换行自适应宽度，保证右侧下载/删除按钮不滑走
        self.collected_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.collected_scroll, 1)

        # 首次扫描（后台，避免阻塞界面）
        QTimer.singleShot(0, self._refresh_collected)
        return f

    def _on_search_key(self):
        """搜索框输入防抖：300ms 内无新输入才触发过滤。"""
        self._search_timer.start()

    def _apply_search(self):
        text = self.search_entry.text().strip()
        if text == self._search_var:
            return
        self._search_var = text
        self._refresh_collected()

    _MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mov", ".mp4", ".m4v"}
    _IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    def _load_disk_items(self) -> list:
        """扫描数据 json + 实际媒体文件，按「日期+标题」合并为一个条目。"""
        from toolbox.config_manager import get_export_subdir, get_export_dir
        video_dir = get_export_subdir("video_data")   # download/已获取数据
        download_root = get_export_dir()              # download/

        # 1. 扫媒体文件，按 (date, title_key, subdir) 分组
        # 只扫「已获取数据/」（{平台}_文件/_媒体 所在根）+「B站资源/」（资源包整体条目）。
        # 不能全 download/ 递归：淘宝商品/票务演出/音乐账号数据等无关图片会被
        # 当成媒体生成一堆无平台「(媒体)」垃圾条目混进列表。
        media_map = {}
        for scan_root in (video_dir, download_root / "B站资源"):
            if scan_root.is_dir():
                self._scan_media_files(scan_root, media_map)

        # 2. 扫 json（根目录旧平铺 + 所有平台一级子目录，新旧目录名均兼容）
        json_paths = list(video_dir.glob("*.json"))
        if video_dir.is_dir():
            for sub in video_dir.iterdir():
                if sub.is_dir() and _norm_subdir(sub.name):
                    json_paths.extend(sub.glob("*.json"))
        json_paths = sorted(set(json_paths), key=lambda p: p.name, reverse=True)

        results = []
        for path in json_paths:
            if path.stem.endswith("_raw"):
                continue
            # 平台：子目录从目录名（新旧结构均可），根目录旧平铺从文件名末段
            platform = _norm_subdir(path.parent.name)
            if not platform:
                from toolbox.utils import norm_platform_dir
                parts = path.stem.rsplit("_", 1)
                platform = norm_platform_dir(parts[1]) if len(parts) == 2 else ""
                platform = platform or "unknown"
            date_part = path.stem[:8] if len(path.stem) >= 8 and path.stem[:8].isdigit() else ""
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    data = data[0] if data else {}
                if not isinstance(data, dict):
                    continue
                play = int(data.get("阅览量", 0) or 0) if platform == "weibo" else int(data.get("播放量", 0) or 0)
                title = data.get("标题", "") or ""
                entry = {
                    "platform": platform,
                    "video_id": data.get("视频ID", "") or "",
                    "title": title,
                    "description": data.get("正文") or "",
                    "author": data.get("作者", "") or "",
                    "play_count": play,
                    "like_count": int(data.get("点赞数", 0) or 0),
                    "comment_count": int(data.get("评论数", 0) or 0),
                    "cover_url": data.get("封面URL", "") or "",
                    "duration": _parse_duration_text(data.get("时长", "")),
                    "publish_time": data.get("发布时间", "") or "",
                    "video_url": data.get("链接", "") or "",
                    "raw_url": data.get("原始URL", "") or "",
                    "raw_data": data.get("原始数据") if isinstance(data.get("原始数据"), dict) else {},
                    "fetched_at": data.get("获取时间", "") or "",
                    "date_part": date_part,
                    "file_path": str(path),
                    "media_files": [],
                }
                # B站类型标记：动态 video_id=dyn_*（或 t.bilibili.com 链接），视频 BV/av 开头
                if platform == "bilibili":
                    vid = str(entry["video_id"])
                    link = entry["video_url"]
                    entry["is_dynamic"] = (vid.startswith("dyn_")
                                           or "t.bilibili.com" in link)
                # 匹配媒体（date + 标题双向子串 + 平台一致）
                # 平台归属由文件夹 {平台}_{文件|媒体} 决定：媒体的平台目录必须与
                # json 的平台一致才可合并（同内容双平台各发一份时，跨平台匹配会把
                # B站下载的媒体抢给抖音条目——json 平台列与实际媒体张冠李戴）
                title_norm = _sanitize_title_key(title).strip("_")
                json_platform = _norm_subdir(path.parent.name) or platform

                def _match(m_t):
                    m_t = m_t.strip("_") if m_t else ""
                    if not m_t or not title_norm:
                        return False
                    if len(m_t) < 4:  # 太短的标题片段不匹配（避免误配）
                        return False
                    return m_t in title_norm or title_norm[:40] in m_t

                # 第1轮：同日期 + 同平台（最精确）
                for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                    if mfs and mfs[0].get("pkg"):
                        continue  # B站资源包整体条目，不参与 json 匹配
                    if m_date != date_part or m_sub != json_platform:
                        continue
                    if _match(m_title):
                        entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                        break
                # 第2轮：同日期 + 无平台目录的旧平铺媒体（根目录旧 mp4/jpg）；
                # 不允许跨平台匹配（B站媒体绝不给抖音条目）
                if not entry["media_files"]:
                    for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                        if mfs and mfs[0].get("pkg"):
                            continue
                        if m_date != date_part or m_sub:
                            continue
                        if _match(m_title):
                            entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                            break
                # 第3轮：无日期也无平台目录的旧格式媒体，只按标题兜底
                if not entry["media_files"]:
                    for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                        if mfs and mfs[0].get("pkg"):
                            continue
                        if m_date or m_sub:  # 只处理完全旧格式的媒体
                            continue
                        if _match(m_title):
                            entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                            break
                # 预期图片数（从 raw json 提取 pics，用于"下载不完整"筛选）
                try:
                    entry["expected_imgs"] = len(self._extract_pic_urls(entry)[0])
                except Exception:
                    entry["expected_imgs"] = 0
                # 存量数据兜底：json 无时长（B站动态视频 duration_text 采集端曾漏提）
                # 但本地有视频文件 → ffprobe 读实际时长
                if not entry.get("duration"):
                    vd = next((m["path"] for m in entry["media_files"]
                               if m["type"] == "video" and os.path.exists(m["path"])), "")
                    if vd:
                        entry["duration"] = _probe_video_duration(vd)
                results.append(entry)
            except Exception:
                continue

        # 3. 剩余未匹配媒体（无对应 json）→ 独立条目
        for (date_part, tk, subdir), mfs in media_map.items():
            if not mfs:
                continue
            imgs = [m for m in mfs if m["type"] == "image"]
            vids = [m for m in mfs if m["type"] == "video"]
            pkg = next((m.get("pkg") for m in mfs if m.get("pkg")), None)
            results.append({
                "platform": self._media_platform(mfs),
                "title": pkg or tk or "(媒体)",
                "description": "",
                "author": (mfs[0] or {}).get("author", "") if mfs else "",
                "play_count": 0, "like_count": 0, "comment_count": 0,
                "cover_url": imgs[0]["path"] if imgs else (vids[0]["path"] if vids else ""),
                "publish_time": "", "video_url": "",
                "duration": _probe_video_duration(vids[0]["path"]) if vids else 0,
                "fetched_at": "",
                "date_part": date_part,
                "file_path": (mfs[0] or {}).get("path", "") if mfs else "",
                "media_files": mfs,
                "expected_imgs": len(imgs),  # 独立媒体条目本身完整
            })
        results.sort(key=lambda r: (r.get("date_part") or ""), reverse=True)
        return results

    def _scan_media_files(self, root, media_map):
        """递归扫描 download 下实际媒体文件，按 (date, title_key, subdir) 分组进 media_map。

        B站资源（download/B站资源/<类别>/<资源包名>/...）整个资源包聚合成一个条目，
        不再按文件名拆散（否则一个收藏集会变成 N 条散行）。

        root 只会是 已获取数据/ 或 B站资源/（调用方已收窄，无需黑名单过滤）。
        """
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in self._MEDIA_EXTS:
                continue
            parts = list(p.parts)
            if "B站资源" in parts:
                i = parts.index("B站资源")
                category = parts[i + 1] if len(parts) > i + 1 else ""
                pkg = parts[i + 2] if len(parts) > i + 2 else category
                if not pkg:
                    continue
                base = _sanitize_title_key(pkg)[:20] or "B站资源"
                key = ("", base, "bilibili")
                mtype = "image" if p.suffix.lower() in self._IMG_EXTS else "video"
                media_map.setdefault(key, []).append({
                    "path": str(p), "name": p.name, "type": mtype,
                    "date_part": "", "author": category, "subdir": "bilibili",
                    "pkg": pkg,
                })
                continue
            # 平台子目录（取路径中第一个匹配的子目录名，规范化为英文，否则空）
            subdir = ""
            for part in p.parts:
                n = _norm_subdir(part)
                if n:
                    subdir = n
                    break
            date_part, author, title_key = self._parse_media_name(p.name)
            base = _sanitize_title_key(title_key)[:20] if title_key else ""
            key = (date_part, base, subdir)
            mtype = "image" if p.suffix.lower() in self._IMG_EXTS else "video"
            media_map.setdefault(key, []).append({
                "path": str(p), "name": p.name, "type": mtype,
                "date_part": date_part, "author": author, "subdir": subdir,
            })

    def _parse_media_name(self, name):
        """从媒体文件名解析 (date, author, title_key)。命名 {date}_{author}_{title}_{i}.ext"""
        stem = name.rsplit(".", 1)[0]
        parts = stem.split("_")
        date_part = parts[0] if parts and len(parts[0]) == 8 and parts[0].isdigit() else ""
        author = parts[1] if len(parts) > 1 and date_part else ""
        rest = parts[2:] if date_part else parts
        # 去掉末尾序号 / live序号
        while rest and (rest[-1].isdigit() or (rest[-1].startswith("live") and rest[-1][4:].isdigit())):
            rest = rest[:-1]
        # 封面文件与正文媒体同组：download_media 存 {stem}_封面.ext，短标题时
        # 封面组键=标题+封面 → 与正文不同组 → json 只弹一组封面就落单成
        # 「播放0赞0」的独立行（抖音/快手/小红书/微博通病；长标题因[:20]截断侥幸同组）
        while rest and rest[-1] == "封面":
            rest = rest[:-1]
        return date_part, author, "_".join(rest)

    @staticmethod
    def _media_platform(mfs):
        """从媒体文件路径推断平台（兼容 {平台}_媒体 新目录名）。"""
        from toolbox.utils import norm_platform_dir
        for m in mfs:
            for part in Path(m["path"]).parts:
                plat = norm_platform_dir(part)
                if plat:
                    return plat
        return "unknown"

    def _refresh_collected(self, keep_page=False):
        """骨架加载：立即渲染灰色占位行，后台扫描磁盘后一次性替换真实列表。

        扫描要读全部 json + 匹配媒体文件（几百条时明显卡顿），
        与 TrackerPage 同款模式：骨架先行 + run_bg 后台扫描 + 代数守卫防旧轮覆盖。
        keep_page=True 保持当前页码（删除/下载完成后刷新不打断浏览位置），
        筛选/搜索/排序/手动刷新走默认重置第 1 页。
        """
        # 主线程先读筛选条件（后台线程不能碰 Qt 控件）
        # 下拉显示中文名，过滤用英文标识（"全部"原样保留）
        plat = _CN2EN.get(self.plat_sel.currentText(), self.plat_sel.currentText())
        status = self.status_sel.currentText()
        desc = self.sort_sel.currentText() == "时间倒序"
        kw = (self._search_var or "").strip().lower()

        self._reload_gen += 1
        gen = self._reload_gen
        self._collected_all_items = []
        if not keep_page:
            self._collected_page = 1
        self._render_skeleton(8)

        def work():
            return self._load_disk_items()

        def done(disk_items, err):
            if gen != self._reload_gen:
                return  # 中途又触发了刷新，本轮作废
            self._disk_items = disk_items or []
            items = [it for it in self._disk_items if plat == "全部" or it["platform"] == plat]
            # 状态筛选
            if status == "未下载":
                items = [it for it in items if not it.get("media_files")]
            elif status == "已下载":
                items = [it for it in items if it.get("media_files")]
            elif status == "下载不完整":
                items = [it for it in items
                         if it.get("expected_imgs", 0) > sum(1 for m in it.get("media_files", []) if m["type"] == "image")]
            # 关键词过滤（标题/作者/视频ID/正文，大小写不敏感）
            if kw:
                items = [it for it in items
                         if kw in (it.get("title") or "").lower()
                         or kw in (it.get("author") or "").lower()
                         or kw in (it.get("video_id") or "").lower()
                         or kw in (it.get("description") or "").lower()]
            # 时间排序（正序/倒序；空日期始终排最后）
            placeholder = "00000000" if desc else "99999999"

            def _sort_key(it):
                d = it.get("date_part") or ""
                return d if d else placeholder
            self._collected_all_items = sorted(items, key=_sort_key, reverse=desc)
            if not keep_page:
                self._collected_page = 1
            # keep_page 时 _render_collected_batch 内部会把越界页码钳回有效范围
            self._render_collected_batch()
            self._backfill_bili_covers()

        run_bg(work, done)

    def _backfill_bili_covers(self):
        """补齐已下载 B站视频的官方封面（旧版下载的视频没有同名 .jpg 索引）。

        列表缩略图优先用本地媒体图——视频条目只有同名 .jpg 封面才能显示图片。
        只补「有本地视频 + 有封面URL + 缺 .jpg」的条目，下完刷新一次列表。
        """
        if getattr(self, "_cover_backfill_running", False):
            return
        todo = []
        for it in self._collected_all_items:
            if it.get("platform") != "bilibili":
                continue
            cover = (it.get("cover_url") or "").strip()
            if not cover or not cover.startswith(("http://", "https://")):
                continue  # 独立媒体条目的 cover_url 是本地路径，不是可下载的官方封面
            vid_path = next((m["path"] for m in it.get("media_files", [])
                             if m["type"] == "video"), None)
            if not vid_path or not os.path.exists(vid_path):
                continue
            stem = os.path.splitext(vid_path)[0]
            # 封面可能被 _download_one 按 Content-Type 存成 .png，同 stem 任意图片扩展都算已有
            if any(os.path.exists(stem + e) for e in (".jpg", ".jpeg", ".png", ".webp")):
                continue
            cext = ".png" if cover.lower().split("?")[0].endswith(".png") else ".jpg"
            todo.append((cover, stem + cext))
        if not todo:
            return
        self._cover_backfill_running = True

        def work():
            from media_downloader import _download_one
            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com/"}
            ok = 0
            for cover, cpath in todo:
                try:
                    _path, success = _download_one(cover, cpath, hdrs)
                    ok += 1 if success else 0
                except Exception:
                    pass
            return ok

        def done(ok, err):
            self._cover_backfill_running = False
            if ok:
                QTimer.singleShot(100, lambda: self._refresh_collected(keep_page=True))

        run_bg(work, done)

    def _render_skeleton(self, n):
        """扫描期间渲染 n 条骨架占位行（与真实行同构的灰色块）。"""
        lay = self.collected_layout
        self._clear_layout(lay)
        for _ in range(n):
            lay.addWidget(self._make_skeleton_row())
        tip = QLabel("⏳ 正在扫描已获取数据...")
        tip.setStyleSheet("color: gray;")
        tip.setContentsMargins(8, 10, 8, 10)
        lay.addWidget(tip)
        lay.addStretch(1)

    def _make_skeleton_row(self) -> QWidget:
        """骨架行：勾选框 + 灰缩略图 + 三行文字条 + 三个按钮位（固定高，防跳动）。"""
        row = QFrame()
        row.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        row.setFixedHeight(72)
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)
        cb = QCheckBox()
        cb.setEnabled(False)
        h.addWidget(cb)
        thumb = QLabel()
        thumb.setFixedSize(96, 64)
        thumb.setStyleSheet("background: #e4e7ec; border-radius: 4px;")
        h.addWidget(thumb)
        bars = QVBoxLayout()
        bars.setSpacing(7)
        bars.setContentsMargins(0, 6, 0, 6)
        for w in (140, 220, 160):
            bar = QLabel()
            bar.setFixedSize(w, 10)
            bar.setStyleSheet("background: #e4e7ec; border-radius: 5px;")
            bars.addWidget(bar)
        wrap = QWidget()
        wrap.setLayout(bars)
        h.addWidget(wrap, 1)
        for _ in range(3):
            btn = QLabel()
            btn.setFixedSize(42, 32)
            btn.setStyleSheet("background: #e4e7ec; border-radius: 4px;")
            h.addWidget(btn)
        return row

    def _render_collected_batch(self):
        """分页渲染已获取资源：每页 BATCH 条，底部页码导航（首页/上一页/跳页/下一页/末页）。"""
        lay = self.collected_layout
        self._clear_layout(lay)
        items = self._collected_all_items
        if not items:
            tip = ("（download/已获取数据/ 目录为空）" if not self._disk_items
                   else f"（该平台无数据，共 {len(self._disk_items)} 条其他平台数据）")
            lbl = QLabel(tip)
            lbl.setStyleSheet("color: gray;")
            lbl.setContentsMargins(8, 10, 8, 10)
            lay.addWidget(lbl)
            lay.addStretch(1)
            return
        total_pages = (len(items) + BATCH - 1) // BATCH
        page = min(max(1, self._collected_page), total_pages)
        self._collected_page = page
        start = (page - 1) * BATCH
        for i in range(start, min(start + BATCH, len(items))):
            self._render_collected_row(items[i], i + 1)
        lay.addWidget(self._make_pager(len(items), page, total_pages))
        lay.addStretch(1)

    def _make_pager(self, total, page, total_pages):
        """页码导航条（浏览器式）：⏮ ◀ 第N/M页(跳页输入) ▶ ⏭ + 总数。"""
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 6, 8, 2)
        h.setSpacing(4)

        def nav_btn(text, tip, target):
            b = QPushButton(text)
            b.setFixedSize(34, 26)
            b.setToolTip(tip)
            b.setEnabled(1 <= target <= total_pages and target != page)
            b.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #c8cdd6;"
                " color: #374151; }"
                "QPushButton:disabled { color: #c3c8d0; border-color: #e5e8ee; }")
            b.clicked.connect(lambda _=False, t=target: self._goto_collected_page(t))
            return b

        h.addWidget(nav_btn("⏮", "第一页", 1))
        h.addWidget(nav_btn("◀", "上一页", page - 1))
        info = QLabel(f"第 {page} / {total_pages} 页 · 共 {total} 条")
        info.setStyleSheet("color: #6b7280;")
        h.addWidget(info)
        h.addWidget(nav_btn("▶", "下一页", page + 1))
        h.addWidget(nav_btn("⏭", "最后一页", total_pages))
        h.addSpacing(8)
        h.addWidget(QLabel("跳至"))
        spin = QSpinBox()
        spin.setRange(1, total_pages)
        spin.setValue(page)
        spin.setFixedWidth(64)
        spin.setStyleSheet("QSpinBox { padding: 1px 4px; }")
        spin.editingFinished.connect(lambda s=spin: self._goto_collected_page(s.value()))
        h.addWidget(spin)
        h.addWidget(QLabel("页"))
        h.addStretch(1)
        return bar

    def _goto_collected_page(self, page):
        total = len(self._collected_all_items)
        total_pages = (total + BATCH - 1) // BATCH if total else 1
        self._collected_page = min(max(1, page), total_pages)
        self._render_collected_batch()

    def _render_collected_row(self, it, index):
        """渲染一行到列表布局（点击弹详情窗，双击缩略图播放本地视频）。"""
        self.collected_layout.addWidget(self._make_collected_row(it, index))

    def _make_collected_row(self, it, index) -> "QWidget":
        """构建一行控件（不加入布局；行级刷新原地替换用）。"""
        row = _RowWidget(lambda it=it: self._show_detail(it),
                         lambda pos, it=it: self._row_context_menu(it, pos))
        row.setStyleSheet(
            "_RowWidget { background: #ffffff; border-radius: 6px; }"
            "QLabel { background: transparent; }")
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        # 批量勾选框（记住勾选状态，重渲染后恢复）
        key = self._dl_key(it)
        cb = QCheckBox()
        if key in self._checked_keys:
            cb.setChecked(True)
        cb.stateChanged.connect(
            lambda st, k=key: (self._checked_keys.add(k) if st else self._checked_keys.discard(k)))
        cb.setToolTip("勾选后可批量下载")
        h.addWidget(cb)
        # 缩略图：优先本地媒体图，其次网络封面（放大到 96×64，原 72×50 太小看不清）
        # 双击缩略图播放本地视频（无本地视频则打开链接）
        thumb = _ThumbLabel(lambda it=it: self._open_media_video(it))
        thumb.setFixedSize(96, 64)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet("background: #eef1f5; border-radius: 4px;")
        thumb.setToolTip("双击播放视频")
        local_img = next((m["path"] for m in it.get("media_files", []) if m["type"] == "image"), None)
        if local_img:
            self._fill_local_thumb(thumb, local_img, 96, 64)
        elif it["cover_url"]:
            self._fill_net_thumb(thumb, it["cover_url"], 96, 64)
        else:
            thumb.setText("🎬")
            thumb.setStyleSheet("background: #eef1f5; border-radius: 4px; font-size: 28px;")
        h.addWidget(thumb)
        # 文本（中间 expand）
        plat_cn = platform_cn(it["platform"])
        # 显示层截断 + 自动换行：长标题/长文件名会把行的最小宽度撑爆，
        # 导致必须横向滚动才能看到右侧的下载/删除按钮（完整内容在 tooltip）
        full_title = it["title"] or "-"
        title = full_title[:60] + ("…" if len(full_title) > 60 else "")
        fname = os.path.basename(it["file_path"])
        fname = fname[:44] + ("…" if len(fname) > 44 else "")
        date_str = it["date_part"] if it["date_part"] else ""
        # B站条目带类型彩色标签（蓝=视频 橙=动态，与已获取页一致）；其他平台原样
        if it["platform"] == "bilibili" and "is_dynamic" in it:
            if it["is_dynamic"]:
                plat_tag = '<span style="color:#e65100; font-weight:bold;">[B站·动态]</span> '
            else:
                plat_tag = '<span style="color:#1976d2; font-weight:bold;">[B站·视频]</span> '
        else:
            plat_tag = f"[{plat_cn}] "
        imgs = sum(1 for m in it.get("media_files", []) if m["type"] == "image")
        vids = sum(1 for m in it.get("media_files", []) if m["type"] == "video")
        media_str = ""
        if imgs:
            media_str += f" 🖼{imgs}"
        if vids:
            media_str += f" 🎬{vids}"

        def _esc(s):
            return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # 行格式：{序号}{类别}{标题}{发布者}——发布者放首行一眼可见，
        # 次行只留数据统计（作者不再重复）
        author = (it.get("author") or "").strip() or "-"
        author_disp = author[:20] + ("…" if len(author) > 20 else "")
        text = (f"[{index}] {plat_tag}{_esc(title)} "
                f'<span style="color:#555;">@{_esc(author_disp)}</span>{media_str}<br>'
                f"播放{_fmt(it['play_count'])}  赞{_fmt(it['like_count'])}<br>"
                f"📅 {date_str}  📄 {_esc(fname)}")
        txt = QLabel(text)
        txt.setTextFormat(Qt.RichText)
        txt.setStyleSheet("font-size: 11px;")
        txt.setWordWrap(True)
        txt.setToolTip(f"{full_title}\n\n📄 {os.path.basename(it['file_path'])}")
        h.addWidget(txt, 1)
        # 右侧按钮：重新下载 / 下载封面（有封面无媒体时）/ 删除（42×32，原 32×28 不好点）
        redo_btn = QPushButton("↻")
        redo_btn.setFixedSize(42, 32)
        redo_btn.setToolTip("重新下载（忽略已下载状态）")
        redo_btn.setStyleSheet(common.icon_btn_style(
            False, "background: transparent; border: 1px solid #c8cdd6; color: #2e7d32; font-size: 15px;"))
        redo_btn.clicked.connect(lambda _=False, e=it: self._download_for_item(e, force=True))
        h.addWidget(redo_btn)
        if it.get("cover_url") and not it.get("media_files"):
            dl_btn = QPushButton("📥")
            dl_btn.setFixedSize(42, 32)
            # tooltip 与 _download_for_item 路由一致：视频条目实际下的是视频+封面
            plat0 = it.get("platform", "")
            vid0 = str(it.get("video_id") or "")
            if plat0 == "bilibili":
                is_vid = (self._bili_dynamic_type(it) == "av"
                          or (bool(vid0) and not vid0.startswith("dyn_")))
            else:
                is_vid = plat0 == "douyin" or self._item_has_video(it)
            dl_btn.setToolTip("下载视频" if is_vid else "下载封面")
            dl_btn.setStyleSheet(common.icon_btn_style(
                True, "background: transparent; border: 1px solid #c8cdd6; color: white; font-size: 15px;"))
            dl_btn.clicked.connect(lambda _=False, e=it: self._download_for_item(e))
            h.addWidget(dl_btn)
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(42, 32)
        del_btn.setToolTip("删除该条目")
        del_btn.setStyleSheet(common.icon_btn_style(
            True, "background: transparent; border: 1px solid #c8cdd6; color: white; font-size: 15px;"))
        del_btn.clicked.connect(lambda _=False, e=it: self._delete_item(e))
        h.addWidget(del_btn)
        return row

    def _row_context_menu(self, it, global_pos):
        """行右键菜单：复制媒体/链接、打开本地数据文件（MD）/ 打开本地媒体文件——存在哪项才显示哪项。"""
        menu = QMenu(self)
        stem = os.path.splitext(it["file_path"])[0] if it.get("file_path") else ""
        md_path = (stem + ".md") if stem else ""
        media = [m for m in it.get("media_files", [])
                 if m.get("path") and os.path.exists(m["path"])]
        shown = False
        # 复制视频/媒体文件（本地存在才显示）
        if media:
            vids = [m["path"] for m in media if m.get("type") == "video"]
            if vids:
                menu.addAction("📋 复制视频").triggered.connect(
                    lambda _=False, ps=vids: self._copy_files_to_clipboard(ps))
            elif len(media) == 1:
                menu.addAction("📋 复制媒体文件").triggered.connect(
                    lambda _=False, p=media[0]["path"]: self._copy_files_to_clipboard([p]))
            else:
                sub = menu.addMenu(f"📋 复制媒体文件（{len(media)} 个）")
                for m in media:
                    icon = {"image": "🖼 ", "video": "🎬 "}.get(m.get("type"), "📄 ")
                    sub.addAction(icon + os.path.basename(m["path"])).triggered.connect(
                        lambda _=False, p=m["path"]: self._copy_files_to_clipboard([p]))
                sub.addSeparator()
                sub.addAction("复制全部").triggered.connect(
                    lambda _=False: self._copy_files_to_clipboard([m["path"] for m in media]))
            shown = True
        url = (it.get("video_url") or "").strip()
        if url:
            menu.addAction("🔗 复制视频链接").triggered.connect(
                lambda _=False: (QApplication.clipboard().setText(url),
                                 self.app.toast("已复制视频链接", "success")))
            shown = True
        if shown:
            menu.addSeparator()
        if md_path and os.path.exists(md_path):
            menu.addAction("📄 打开数据文件（MD）").triggered.connect(
                lambda _=False, p=md_path: os.startfile(p))  # noqa: PTH110
            shown = True
        if media:
            if len(media) == 1:
                menu.addAction("🎬 打开媒体文件").triggered.connect(
                    lambda _=False, p=media[0]["path"]: os.startfile(p))  # noqa: PTH110
            else:
                sub = menu.addMenu(f"🎬 打开媒体文件（{len(media)} 个）")
                for m in media:
                    icon = {"image": "🖼 ", "video": "🎬 "}.get(m.get("type"), "📄 ")
                    name = os.path.basename(m["path"])
                    sub.addAction(icon + name).triggered.connect(
                        lambda _=False, p=m["path"]: os.startfile(p))  # noqa: PTH110
                sub.addSeparator()
                sub.addAction("📂 打开媒体所在文件夹").triggered.connect(
                    lambda _=False: os.startfile(os.path.dirname(media[0]["path"])))  # noqa: PTH110
            shown = True
        if not shown:
            menu.addAction("（该条目没有本地 MD / 媒体文件）").setEnabled(False)
        menu.exec(global_pos)

    def _open_media_video(self, it):
        """双击缩略图：优先播放本地视频文件，无则打开链接。"""
        try:
            vid_path = next((m["path"] for m in it.get("media_files", [])
                             if m["type"] == "video"), None)
            if vid_path and os.path.exists(vid_path):
                os.startfile(vid_path)  # noqa: PTH110 — Windows 系统播放器
                return
            url = it.get("video_url") or it.get("raw_url") or ""
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                self.app.toast("该条目无本地视频，也没有可打开的链接", "info")
        except Exception as e:
            self.app.toast(f"打开失败: {e}", "error")

    def _dl_key(self, it):
        """条目的下载状态键（唯一标识一个资产）。"""
        return it.get("file_path") or it.get("video_id") or it.get("title", "")

    @staticmethod
    def _item_has_video(it) -> bool:
        """非抖音/B站条目是否有视频源（决定路由到视频下载还是图片下载）。

        微博视频条目的 video_url 常是 m.weibo.cn/detail 网页链接，
        真直链藏在 raw_data（page_info.media_info 或 fid playinfo 顶层），
        需静态检查 raw_data 判断；小红书/快手等平台 video_url 已是直链。
        例外：小红书账号获取条目的「链接」是笔记页链接（视频流在页内，
        非直链、raw_data 为空），也按视频条目处理 → 视频下载路径里
        下载前重新解析笔记页拿新鲜直链（同抖音时效直链模式）。
        """
        from media_downloader import _is_direct_media_url
        if _is_direct_media_url(it.get("video_url") or ""):
            return True
        if it.get("platform") == "xiaohongshu":
            if "xiaohongshu.com" in (it.get("video_url") or "") \
                    or "xiaohongshu.com" in (it.get("raw_url") or ""):
                return True
        raw = it.get("raw_data") or {}
        if not isinstance(raw, dict):
            return False
        mi = (raw.get("page_info") or {}).get("media_info") or {}
        if isinstance(mi, dict) and (mi.get("stream_url") or mi.get("stream_url_hd")):
            return True
        if isinstance(raw.get("urls"), dict) and raw.get("urls"):
            return True  # fid 条目的 h5 playinfo（清晰度标签 → 直链）
        return bool(raw.get("stream_url"))

    def _item_media_complete(self, it) -> bool:
        """本地媒体是否已完整（批量下载跳过判定）。

        视频条目（抖音视频/B站视频动态/微博等平台视频）有本地视频即完整；
        图文条目本地图片数达到 raw json 里的预期图数即完整
        （live 图视频计入媒体但不参与图片计数，缺图时下载会自动跳过已有文件只补缺）；
        纯文本/独立媒体条目本身已有文件，视为完整。
        """
        mfs = [m for m in (it.get("media_files") or [])
               if os.path.exists(m.get("path", ""))]
        vid = str(it.get("video_id") or "")
        plat = it.get("platform", "")
        dtype = self._bili_dynamic_type(it) if plat == "bilibili" else ""
        is_video_item = (
            (plat == "douyin" and bool(vid))
            or (plat == "bilibili" and bool(vid) and not vid.startswith("dyn_"))
            or dtype == "av"
            or (plat not in ("douyin", "bilibili") and self._item_has_video(it))
        )
        if is_video_item:
            if not any(m["type"] == "video" for m in mfs):
                return False
            # 封面同样要求本地已落（资产页缩略图本地优先，网络封面会过期/403）；
            # 无封面源（cover_url 空）的视频条目只看视频本体。
            # 旧数据只下过视频没下封面 → 判不完整，批量下载会自动补（视频
            # 已存在走 Range/416 快速跳过，实际只补封面）
            if not (it.get("cover_url") or "").strip():
                return True
            return any(m["type"] == "image" for m in mfs)
        expected = int(it.get("expected_imgs") or 0)
        n_imgs = sum(1 for m in mfs if m["type"] == "image")
        if expected:
            return n_imgs >= expected
        # 预期图数未知：有可下载源（图/视频链接）但一张都没有 → 不完整
        if it.get("video_url") or it.get("cover_url"):
            return n_imgs > 0 or any(m["type"] == "video" for m in mfs)
        return True

    def _quality_to_qn(self, label):
        """全局清晰度 → B站 qn 编号（最高画质返回 None，B站自动选最高逐级降）。"""
        return {"8K": 127, "杜比视界": 126, "HDR": 125, "4K": 120,
                "1080P60": 116, "1080P": 80, "720P": 64, "480P": 32}.get(label)

    def _download_for_item(self, it, force=False, on_finished=None, quiet=False,
                           sub_bridge=None, cancel_ev=None):
        """按平台下载（防重复点击：下载中/已完成分别提示；force=True 强制重下）。

        quiet=True（批量）：sub_bridge 上报当前文件进度给总进度条，cancel_ev 联动取消。
        """
        key = self._dl_key(it)
        state = self._dl_state.get(key)
        if state == "downloading":
            self.app.toast("文件下载中，请稍后再试", "warning")
            if on_finished:
                on_finished()
            return
        if state == "done" and not force:
            self.app.toast("文件已下载完毕，请勿重复点击", "info")
            if on_finished:
                on_finished()
            return
        self._dl_state[key] = "downloading"
        plat = it.get("platform", "")
        if plat == "bilibili":
            self._download_bilibili_item(it, on_finished=on_finished, quiet=quiet, force=force,
                                         sub_bridge=sub_bridge, cancel_ev=cancel_ev)
        elif plat == "douyin" or (plat not in ("bilibili",) and self._item_has_video(it)):
            # 视频条目统一走视频下载（微博视频/小红书/快手等直链平台）；
            # 微博视频曾误入图片分支（download_videos=False）→ 只下封面不下视频
            self._download_video_item(it, on_finished=on_finished, quiet=quiet,
                                      sub_bridge=sub_bridge, cancel_ev=cancel_ev)
        else:
            self._download_images_for_item(it, on_finished=on_finished, quiet=quiet, force=force,
                                           sub_bridge=sub_bridge, cancel_ev=cancel_ev)

    def _refresh_rows_for(self, items):
        """下载完成后的行级刷新：只重建给定条目的行，不动其他行。

        整列表 _refresh_collected 会重建全部控件 + 骨架闪烁 + 重置滚动位置，
        单条下载完成时体验太差；这里后台重扫磁盘，仅原地替换受影响条目的行。
        条目不在当前页（被筛选/其他页）时只更新数据，不重建行。
        """
        keys = {self._dl_key(it) for it in items}
        gen = self._reload_gen  # 不递增：不使进行中的整列表刷新作废

        def work():
            return self._load_disk_items()

        def done(disk_items, err):
            if gen != self._reload_gen or err:
                return  # 整列表刷新已接管/出错 → 交给全量刷新兜底
            self._disk_items = disk_items or []
            disk_by_key = {}
            for d in self._disk_items:
                k = self._dl_key(d)
                if k in keys:
                    disk_by_key[k] = d
            # 更新 _collected_all_items 里的条目数据（dict 身份不变，筛选排序不动）
            for it in self._collected_all_items:
                nd = disk_by_key.get(self._dl_key(it))
                if nd is not None:
                    it.update(nd)
            # 原地替换当前页里这些条目的行控件
            lay = self.collected_layout
            total = len(self._collected_all_items)
            if not total:
                return
            total_pages = (total + BATCH - 1) // BATCH
            page = min(max(1, self._collected_page), total_pages)
            start = (page - 1) * BATCH
            row_idx = 0  # 布局结构：[行...] + pager + stretch，行从 0 连续排
            for i in range(start, min(start + BATCH, total)):
                it = self._collected_all_items[i]
                if self._dl_key(it) not in keys:
                    row_idx += 1
                    continue
                if row_idx >= lay.count():
                    break
                old = lay.itemAt(row_idx).widget()
                if old is None:  # pager/stretch 位置，说明行已数完
                    break
                lay.insertWidget(row_idx, self._make_collected_row(it, i + 1))
                old.setParent(None)
                old.deleteLater()
                row_idx += 1

        run_bg(work, done)

    def _toggle_select_all(self, state):
        """全选/取消：对所有筛选后的条目生效（含懒加载未渲染的）。"""
        if bool(state):
            self._checked_keys = {self._dl_key(it) for it in self._collected_all_items}
        else:
            self._checked_keys.clear()
        # 重渲染以同步复选框状态（保持当前页码）
        if self._collected_all_items:
            self._render_collected_batch()

    def _batch_download_checked(self):
        """顺序批量下载所有勾选条目：仅初次下载（本地无媒体）或媒体不完整的才下载；
        已完整的直接跳过；部分图片缺失时下载内部会自动跳过已存在文件只补缺。"""
        items = [it for it in self._collected_all_items
                 if self._dl_key(it) in self._checked_keys]
        if not items:
            self.app.toast("请先勾选要下载的条目", "info")
            return
        todo, skip = [], 0
        for it in items:
            key = self._dl_key(it)
            if self._dl_state.get(key) == "downloading":
                skip += 1
                continue
            if self._item_media_complete(it):  # 已有图/文且完整 → 跳过
                skip += 1
                self._dl_state[key] = "done"
                continue
            todo.append(it)
        n = len(todo)
        cancel_ev = threading.Event()
        dlg = QProgressDialog(f"批量下载 0/{n}", "取消", 0, max(n, 1), self)
        dlg.setWindowTitle("批量下载")
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(420)
        dlg.canceled.connect(cancel_ev.set)
        if skip:
            self.app.toast(f"跳过 {skip} 条（已下载完整），开始下载 {n} 条", "info")
        if not todo:
            dlg.close()
            return
        dlg.show()

        done_n = [0]
        # 子进度桥：当前条目内部（视频流字节/图片计数）上报 → 合并进总进度条标签
        sub_bridge = _ProgressBridge()
        cur_title = [""]

        def on_sub(done_b, total_b, name):
            try:
                if total_b > 100000:  # 字节进度（视频流，MB 展示）
                    tail = f"  ⬇ {done_b / 1048576:.1f}/{total_b / 1048576:.1f} MB"
                elif total_b > 0 and name:  # 文件计数（图片/live 图）
                    tail = f"  ⬇ 文件 {done_b}/{total_b}"
                else:
                    return
                dlg.setLabelText(f"批量下载 {done_n[0] + 1}/{n}  {cur_title[0]}{tail}")
            except RuntimeError:
                pass  # 对话框已关

        sub_bridge.updated.connect(on_sub)

        def _advance():
            done_n[0] += 1
            try:
                dlg.setValue(done_n[0])
            except RuntimeError:
                pass
            if cancel_ev.is_set() or done_n[0] >= n:
                # close() 会触发 closeEvent → emit canceled → cancel_ev 被置位，
                # 必须先记下用户是否真取消，再 close
                was_cancelled = cancel_ev.is_set()
                try:
                    sub_bridge.updated.disconnect(on_sub)
                except (RuntimeError, TypeError):
                    pass
                try:
                    dlg.close()
                except RuntimeError:
                    pass
                if was_cancelled:
                    self.app.toast(f"批量下载已取消（完成 {done_n[0]}/{n}）", "warning")
                else:
                    self.app.toast(f"✅ 批量下载完成 {done_n[0]} 条", "success")
                QTimer.singleShot(100, lambda: self._refresh_collected(keep_page=True))
            else:
                _step()

        def _step():
            if cancel_ev.is_set():
                return
            it = todo[done_n[0]]
            cur_title[0] = (it.get("title") or "")[:32]
            try:
                dlg.setLabelText(f"批量下载 {done_n[0] + 1}/{n}  {cur_title[0]}")
            except RuntimeError:
                pass
            self._download_for_item(it, force=False, on_finished=_advance, quiet=True,
                                    sub_bridge=sub_bridge, cancel_ev=cancel_ev)

        _step()

    def _download_bilibili_item(self, it, on_finished=None, quiet=False, force=False,
                                sub_bridge=None, cancel_ev=None):
        """B站分发：图文动态→图片；视频动态(AV)/BV号→视频。

        quiet=True（批量）：视频流字节进度上报 sub_bridge（总进度条显示），cancel_ev 联动取消。
        """
        vid = it.get("video_id", "")
        dtype = self._bili_dynamic_type(it)

        # 图文动态：下载图片（不能套视频下载模式）
        if dtype == "draw":
            self._download_images_for_item(it, on_finished=on_finished, quiet=quiet, force=force,
                                           sub_bridge=sub_bridge, cancel_ev=cancel_ev)
            return
        # 视频动态：从 archive.bvid 取真实 BV 号
        if dtype == "av":
            bvid = self._bili_av_bvid(it)
            if bvid:
                vid = bvid
        # 仍是 dyn_ 且拿不到 BV 号 → 退化为图片（封面兜底）
        if not vid or vid.startswith("dyn_"):
            self._download_images_for_item(it, on_finished=on_finished, quiet=quiet, force=force,
                                           sub_bridge=sub_bridge, cancel_ev=cancel_ev)
            return

        cancel_ev = cancel_ev or threading.Event()
        bridge = _ProgressBridge()
        dlg = None
        if not quiet:
            dlg = _make_progress_dlg(self, "下载 B站视频", 1, cancel_ev)
            dlg.setLabelText("正在解析视频流...")
        elif sub_bridge is not None:
            bridge = sub_bridge  # 批量模式：进度上报给总进度条

        def on_prog(done_n, total_n, name):
            if dlg is None:
                return
            if total_n != dlg.maximum():
                dlg.setMaximum(max(total_n, 1))
            dlg.setValue(done_n)
            if total_n > 100000:  # 字节进度（视频流）
                dlg.setLabelText(
                    f"下载中 {done_n / 1048576:.1f}/{total_n / 1048576:.1f} MB")
            else:
                dlg.setLabelText(f"下载中 {done_n}/{total_n}  {name[:48]}")

        if dlg is not None:
            bridge.updated.connect(on_prog)

        def work():
            from bilibili_download import download_bilibili_by_bvid
            from auth import get_cookie
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("video_data"))
            vpath = download_bilibili_by_bvid(
                vid, quality=self._quality_to_qn(common.CURRENT_QUALITY),
                cookie=get_cookie("bilibili") or "",
                out_dir=out_dir, title=it.get("title", ""),
                author=it.get("author", ""), publish_time=it.get("publish_time", ""),
                progress_cb=(lambda d, t: bridge.emit_progress(int(d), int(t), "视频流")),
            )
            # 同步下载官方封面：与视频同名 .jpg（改名索引），列表即可显示本地缩略图
            cover = (it.get("cover_url") or "").strip()
            if cover and vpath:
                try:
                    from media_downloader import _download_one
                    if cover.startswith("//"):
                        cover = "https:" + cover
                    cpath = os.path.splitext(vpath)[0] + ".jpg"
                    _download_one(cover, cpath, {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36",
                        "Referer": "https://www.bilibili.com/"})
                except Exception:
                    pass
            return vpath

        def done(result, err):
            if dlg is not None and dlg.isVisible():
                dlg.close()
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                self._dl_state[self._dl_key(it)] = "done"
                stem = os.path.splitext(result)[0] if result else ""
                cover_ok = bool(stem) and any(
                    os.path.exists(stem + e) for e in (".jpg", ".jpeg", ".png", ".webp"))
                self.app.toast("✅ 已下载 B站视频" + ("（含封面）" if cover_ok else ""),
                               "success")
            if not quiet:  # 批量模式由 _advance 统一刷新一次，避免逐条骨架闪烁
                self._refresh_rows_for([it])
            if on_finished:
                on_finished()

        self.app.toast("开始下载 B站视频...", "info")
        run_bg(work, done)

    def _bili_dynamic_type(self, it) -> str:
        """返回 B站动态类型：'draw'=图文动态 / 'av'=视频动态 / ''=非动态。"""
        fp = it.get("file_path", "")
        if not fp:
            return ""
        raw = fp.replace(".json", "_raw.json")
        if not os.path.exists(raw):
            return ""
        try:
            rd = json.load(open(raw, encoding="utf-8"))
            t = (rd.get("type", "") or "").upper()
            if "DRAW" in t:
                return "draw"
            if "AV" in t or "DYNAMIC" in t:
                return "av"
            return ""
        except Exception:
            return ""

    def _bili_av_bvid(self, it) -> str:
        """从 B站视频动态 raw.json 提取 archive.bvid（真实 BV 号）。"""
        fp = it.get("file_path", "")
        if not fp:
            return ""
        raw = fp.replace(".json", "_raw.json")
        if not os.path.exists(raw):
            return ""
        try:
            rd = json.load(open(raw, encoding="utf-8"))
            major = ((rd.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
            arch = major.get("archive", {}) or {}
            return arch.get("bvid", "") or ""
        except Exception:
            return ""

    def _download_video_item(self, it, on_finished=None, quiet=False, sub_bridge=None, cancel_ev=None):
        """抖音：直链有时效性（过期 403），下载前用 video_id 重新解析拿新直链。

        quiet=True 时不弹单条进度框，字节进度上报 sub_bridge（批量总进度条显示），
        cancel_ev 联动批量取消；完成后回调 on_finished 续下一条。
        """
        vurl = it.get("video_url", "")
        vid = it.get("video_id", "")
        if not vurl and not vid:
            self.app.toast("此条目无视频链接", "warning")
            if on_finished:
                on_finished()
            return

        cancel_ev = cancel_ev or threading.Event()
        bridge = _ProgressBridge()
        dlg = None
        if not quiet:
            dlg = _make_progress_dlg(self, "下载视频", 1, cancel_ev)
            dlg.setLabelText("正在解析链接...")

            def on_prog(done_n, total_n, name):
                if total_n != dlg.maximum():
                    dlg.setMaximum(max(total_n, 1))
                dlg.setValue(done_n)
                # 字节进度（视频流边下边走）与 B站弹窗同款 MB 展示
                if total_n > 100000:
                    dlg.setLabelText(
                        f"下载中 {done_n / 1048576:.1f}/{total_n / 1048576:.1f} MB  {name[:40]}")
                else:
                    dlg.setLabelText(f"下载中 {done_n}/{total_n}  {name[:48]}")

            bridge.updated.connect(on_prog)
        elif sub_bridge is not None:
            bridge = sub_bridge  # 批量模式：进度上报给总进度条

        def work():
            from platforms.base import VideoInfo, now_str
            from toolbox.config_manager import get_export_subdir
            from media_downloader import download_media, _is_direct_media_url
            vurl2 = vurl
            note_images = None
            raw_data = it.get("raw_data") or {}
            # 抖音：json 里的「链接」是采集时生成的时效直链（几天后 403），
            # 下载前用 video_id 重新解析拿新直链；图文 note 则拿到 images 下图片
            if it["platform"] == "douyin" and vid:
                try:
                    from multi_downloader import parse_video
                    d = parse_video(f"https://www.douyin.com/video/{vid}",
                                    quality=common.CURRENT_QUALITY)
                    if d.get("is_note") or (d.get("images") and not d.get("video_url")):
                        note_images = d.get("images") or []
                    elif d.get("video_url"):
                        vurl2 = d["video_url"]
                except Exception:
                    pass  # 重新解析失败就用旧直链（可能已过期，下载会失败）
            # 微博：weibocdn 直链带 Expires 签名（几天后 403），下载前重新
            # 解析拿新直链 + 新 raw_data（media_downloader 微博分支从 raw_data
            # 挖清晰度直链，不更新会一直用过期的旧链接）。候选链接依次尝试：
            # 原始URL（fid 播放器页/详情页链接）→ detail/{video_id}（时间线
            # 批量采集的条目 raw_url 常是用户主页链接，解析不了单条，video_id
            # 才是微博 mid）
            elif it["platform"] == "weibo":
                from platforms import get_parser
                parser = get_parser("weibo")
                ru = it.get("raw_url") or ""
                candidates = []
                if ru and any(k in ru for k in
                              ("fid=", "/detail/", "/status/", "/tv/show/", "h5.video.weibo.com")):
                    candidates.append(ru)
                if vid and vid.isdigit():
                    candidates.append(f"https://m.weibo.cn/detail/{vid}")
                for u in candidates:
                    try:
                        new_info = parser.parse(u)
                        if new_info and new_info.video_url:
                            vurl2 = new_info.video_url
                            if new_info.raw_data:
                                raw_data = new_info.raw_data
                            break
                    except Exception:
                        continue
            # 小红书：账号获取条目的「链接」是笔记页链接（含 xsec_token），
            # 视频流在笔记页内且直链 ?sign= 有时效 → 下载前重新解析拿新鲜
            # 直链；图文笔记则拿全部图片（同抖音 note 模式）。候选依次尝试：
            # 原始URL（带采集时的 xsec_token）→ 链接 → 无 token 裸笔记页
            elif it["platform"] == "xiaohongshu":
                from platforms import get_parser
                xhs_parser = get_parser("xiaohongshu")
                xhs_candidates = []
                for u in (it.get("raw_url") or "", vurl):
                    if u and u not in xhs_candidates:
                        xhs_candidates.append(u)
                if vid:
                    bare = f"https://www.xiaohongshu.com/discovery/item/{vid}"
                    if bare not in xhs_candidates:
                        xhs_candidates.append(bare)
                for u in xhs_candidates:
                    try:
                        new_info = xhs_parser.parse(u)
                    except Exception:
                        continue
                    if not new_info:
                        continue
                    if new_info.video_url:
                        vurl2 = new_info.video_url
                        break
                    if new_info.images:
                        note_images = new_info.images
                        break

            base_dir = str(get_export_subdir("video_data"))
            info = VideoInfo(
                platform=it["platform"], video_id=vid,
                title=it.get("title") or it.get("date_part") or "未知",
                author=it.get("author", ""),  # 关键：让下载落到 {平台}_媒体/{作者}/ 子目录
                video_url=vurl2, cover_url=it.get("cover_url", ""),
                publish_time=it.get("publish_time", ""),  # 关键：让 date_str 匹配 json date_part
                raw_data=raw_data,
                raw_url="", fetched_at=now_str(),
            )
            # 图文 note：下图片
            if note_images:
                info.images = note_images
                return download_media([info], base_dir=base_dir,
                                      download_images=True, download_live=False, download_videos=False,
                                      progress_callback=bridge.emit_progress,
                                      wait_range=(0.5, 1.5), cancel_event=cancel_ev,
                                      quiet=True)
            if not vurl2:
                raise ValueError("无可用视频链接")
            # 小红书兜底：重新解析全部失败时 vurl2 仍是笔记页链接（非直链），
            # download_media 会静默跳过视频只下封面 —— 显式报错避免误导
            if it["platform"] == "xiaohongshu" and not note_images \
                    and not _is_direct_media_url(vurl2):
                raise ValueError("小红书视频流获取失败：笔记链接可能已失效或被风控，请重新获取该条目")
            # download_images=True 仅为生成「封面」下载任务（images 为空不会多下图）：
            # 资产页缩略图本地优先，封面必须落盘成 {stem}_封面.jpg，
            # 否则只能走网络封面（抖音直链会过期、部分图床 403）
            # 字节进度：视频流边下边报，弹窗显示实际大小（B站同款）
            return download_media([info], base_dir=base_dir,
                                  download_images=True, download_live=False, download_videos=True,
                                  progress_callback=bridge.emit_progress,
                                  byte_progress_callback=bridge.emit_bytes,
                                  wait_range=(0.5, 1.5), cancel_event=cancel_ev,
                                  quiet=True)

        def done(result, err):
            if dlg is not None and dlg.isVisible():
                dlg.close()
                bridge.updated.disconnect(on_prog)
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                n_img = (result or {}).get("images", 0)
                n_vid = (result or {}).get("videos", 0)
                n_cov = (result or {}).get("covers", 0)
                f = (result or {}).get("failed", 0)
                cancelled = (result or {}).get("cancelled", False)
                if n_img:
                    self._dl_state[self._dl_key(it)] = "done" if not cancelled else None
                    self.app.toast(f"✅ 已下载图片 {n_img} 张" + ("（已取消）" if cancelled else ""),
                                   "success" if not cancelled else "warning")
                elif n_vid:
                    self._dl_state[self._dl_key(it)] = "done"
                    msg = f"✅ 已下载视频 {n_vid} 个"
                    if n_cov:
                        msg += f" + 封面 {n_cov} 张"
                    self.app.toast(msg, "success")
                else:
                    self._dl_state[self._dl_key(it)] = None  # 失败可重试
                    if cancelled:
                        self.app.toast("⚠ 下载已取消（半截文件下次续传）", "warning")
                    else:
                        self.app.toast(f"❌ 视频下载失败（{f} 个），直链可能已过期，可重试", "error")
            if not quiet:  # 批量模式由 _advance 统一刷新一次
                self._refresh_rows_for([it])
            if on_finished:
                on_finished()

        run_bg(work, done)

    def _delete_item(self, it):
        """删除当前资产：按类别选择（图片/视频/文本/全部）。"""
        fp = it.get("file_path", "")
        stem = os.path.splitext(fp)[0]
        # 文本文件只认 json/md/xlsx 本体（独立媒体条目的 file_path 是媒体
        # 文件本身，绝不能归入"文本"类别——否则勾"文本"实际删的是图片/视频）
        doc_files = [fp] if fp.lower().endswith((".json", ".md", ".xlsx")) else []
        for ext in (".md", ".xlsx", "_raw.json"):
            cand = stem + ext
            if os.path.exists(cand):
                doc_files.append(cand)
        img_files = [m["path"] for m in it.get("media_files", []) if m["type"] == "image"]
        vid_files = [m["path"] for m in it.get("media_files", []) if m["type"] == "video"]
        doc_files = [f for f in doc_files if f and os.path.exists(f)]
        img_files = [f for f in img_files if f and os.path.exists(f)]
        vid_files = [f for f in vid_files if f and os.path.exists(f)]
        total = len(doc_files) + len(img_files) + len(vid_files)
        if total == 0:
            self.app.toast("无文件可删除", "info")
            return

        # 四类选择对话框（各列数量，无该类时禁用）
        win = QDialog(self)
        win.setWindowTitle("删除文件")
        win.setModal(True)
        win.resize(420, 300)
        v = QVBoxLayout(win)
        v.setSpacing(10)
        v.addWidget(QLabel(f"「{(it.get('title') or '')[:40]}」关联文件："))

        def _mk_row(emoji, label, files, color):
            cb = QCheckBox(f"{emoji} {label}（{len(files)} 个）")
            cb.setStyleSheet(f"color: {color}; font-weight: bold;")
            cb.setEnabled(bool(files))
            if not files:
                cb.setStyleSheet("color: #999;")
            v.addWidget(cb)
            return cb, files

        cb_img, _ = _mk_row("🖼", "图片文件", img_files, "#1976d2")
        cb_vid, _ = _mk_row("🎬", "视频文件", vid_files, "#6a1b9a")
        cb_doc, _ = _mk_row("📄", "文本文件（json/md/xlsx/raw）", doc_files, "#e65100")
        cb_all = QCheckBox("🗑 全部删除（含文本，条目将消失）")
        cb_all.setStyleSheet("color: #c62828; font-weight: bold;")
        v.addWidget(cb_all)

        def _sync_all(checked):
            # 勾「全部」= 三类全选；取消「全部」不动单项（可单独调整）
            if checked:
                cb_img.setChecked(True)
                cb_vid.setChecked(True)
                cb_doc.setChecked(True)

        cb_all.stateChanged.connect(_sync_all)
        hint = QLabel("勾选要删除的文件类别，未勾选的保留")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(72)
        cancel.clicked.connect(win.reject)
        ok = QPushButton("删除")
        ok.setFixedWidth(72)
        ok.setStyleSheet("background: #c62828; color: white; font-weight: bold;")
        ok.clicked.connect(win.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)

        if win.exec() != QDialog.Accepted:
            return
        targets = []
        if cb_img.isChecked():
            targets += img_files
        if cb_vid.isChecked():
            targets += vid_files
        if cb_doc.isChecked():
            targets += doc_files
        if not targets:
            self.app.toast("未选择任何类别", "info")
            return
        # 保留的媒体（未勾选删除的类别）；独立媒体条目无数据文件，
        # 删光全部媒体后条目将从列表消失且无法补下
        remaining = (len(img_files) if not cb_img.isChecked() else 0) \
            + (len(vid_files) if not cb_vid.isChecked() else 0)
        vanish_warn = ("\n\n⚠ 该条目无数据文件（json），删除全部媒体后条目将从列表消失！"
                       if not doc_files and remaining == 0 and (img_files or vid_files) else "")
        if QMessageBox.question(
                self, "确认删除",
                f"将删除 {len(targets)} 个文件：\n\n"
                + "\n".join(os.path.basename(f) for f in targets[:10])
                + ("\n…等" if len(targets) > 10 else "")
                + vanish_warn,
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        deleted = 0
        for f in targets:
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
        # 文本全删 = 条目本身消失，清下载状态允许重新采集
        if cb_doc.isChecked():
            self._dl_state.pop(self._dl_key(it), None)
        self.app.toast(f"已删除 {deleted} 个文件", "success")
        QTimer.singleShot(100, lambda: self._refresh_collected(keep_page=True))

    def _extract_pic_urls(self, it) -> tuple:
        """从 raw json 提取该条目的所有图片 URL 与 live 图视频 URL。

        返回 (pic_urls, live_urls)：live 图的静态帧照常入 pic_urls，
        其视频（videoSrc 播放器 URL，内嵌真实 .mov）入 live_urls。
        """
        urls, lives = [], []
        fp = it.get("file_path", "")
        if not fp:
            return urls, lives
        stem = os.path.splitext(fp)[0]
        raw_candidates = [stem + "_raw.json", stem.replace(".json", "_raw.json")]
        raw_path = next((c for c in raw_candidates if os.path.exists(c)), "")
        if not raw_path:
            return urls, lives
        try:
            data = json.load(open(raw_path, encoding="utf-8"))
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                return urls, lives
            # 方式1：微博 pics 列表（dict 含 large/original/url 或纯字符串）
            from platforms.weibo import WeiboParser
            for pic in data.get("pics", []) or []:
                if isinstance(pic, dict):
                    u = (pic.get("original", {}).get("url")
                         or pic.get("largest", {}).get("url")
                         or pic.get("large", {}).get("url")
                         or pic.get("url") or "")
                    # live 图：videoSrc 是播放器页 URL，需解出真实 .mov 直链
                    lv = pic.get("videoSrc") or ""
                    if lv:
                        mov = WeiboParser._extract_livephoto_url(lv)
                        if mov and mov not in lives:
                            lives.append(mov)
                elif isinstance(pic, str):
                    u = pic
                else:
                    u = ""
                if u and u not in urls:
                    urls.append(u)
            # 顶层 live_photo 字段兜底（空格分隔的多个播放器 URL）
            if not lives and data.get("live_photo"):
                for lv in str(data["live_photo"]).split():
                    if lv:
                        mov = WeiboParser._extract_livephoto_url(lv)
                        if mov and mov not in lives:
                            lives.append(mov)
            # 方式2：微博 pic_ids（图片 ID 列表，构造 sinaimg 大图 URL）
            if not urls:
                for pid in data.get("pic_ids", []) or []:
                    if pid:
                        urls.append(f"https://wx1.sinaimg.cn/large/{pid}.jpg")
            # 方式3：B站图文动态 opus.pics（i0.hdslb.com 原图 + 实况图 live_url）
            if not urls:
                try:
                    major = ((data.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
                    opus_pics = (major.get("opus", {}) or {}).get("pics", []) or []
                    for p in opus_pics:
                        if not isinstance(p, dict):
                            continue
                        u = p.get("url", "") or p.get("src", "")
                        if u and u not in urls:
                            urls.append(u)
                        # 实况图：live_url 是 hdslb 视频直链，直接可下
                        lv = p.get("live_url") or ""
                        if lv and lv not in lives:
                            lives.append(lv)
                except Exception:
                    pass
            # 方式4：B站新版图文动态 draw.items（.src + live_url）
            if not urls:
                try:
                    major = ((data.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
                    draw_items = (major.get("draw", {}) or {}).get("items", []) or []
                    for p in draw_items:
                        if not isinstance(p, dict):
                            continue
                        u = p.get("src", "")
                        if u and u not in urls:
                            urls.append(u)
                        lv = p.get("live_url") or ""
                        if lv and lv not in lives:
                            lives.append(lv)
                except Exception:
                    pass
        except Exception:
            pass
        return urls, lives

    def _download_images_for_item(self, it, on_finished=None, quiet=False, force=False,
                                  sub_bridge=None, cancel_ev=None):
        """下载单条条目所有图片与 live 图视频（优先 raw json 的 pics/pic_ids）。

        quiet=True 时不弹单条进度框，进度上报 sub_bridge（批量总进度条显示），
        cancel_ev 联动批量取消，完成后回调 on_finished 续下一条。
        force=True 强制重下（跳过已下载的原图也要重新下载）。
        """
        # 优先从 raw json 提取所有原图 + live 视频，封面图也加入（B站动态封面 i0.hdslb.com 等）
        pics, lives = self._extract_pic_urls(it)
        cover = it.get("cover_url", "")
        if cover and cover not in pics:
            # 微博封面=首图的缩略图变体（parser 里 cover_url=images[0]），pics 存在时跳过防重复
            if it["platform"] != "weibo" or not pics:
                pics.insert(0, cover)  # 封面优先下载（列表里预览的就是它）
        if not pics and not lives:
            self.app.toast("此条目无可下载图片（raw json 无 pics 且无封面）", "warning")
            if on_finished:
                on_finished()
            return

        cancel_ev = cancel_ev or threading.Event()
        bridge = _ProgressBridge()
        dlg = None
        if not quiet:
            dlg = _make_progress_dlg(self, "下载图片", len(pics) + len(lives), cancel_ev)

            def on_prog(done_n, total_n, name):
                if total_n != dlg.maximum():
                    dlg.setMaximum(max(total_n, 1))
                dlg.setValue(done_n)
                # 字节进度（Live图视频流）与视频下载弹窗同款 MB 展示
                if total_n > 100000:
                    dlg.setLabelText(
                        f"下载中 {done_n / 1048576:.1f}/{total_n / 1048576:.1f} MB  {name[:40]}")
                else:
                    dlg.setLabelText(f"下载中 {done_n}/{total_n}  {name[:48]}")

            bridge.updated.connect(on_prog)
        elif sub_bridge is not None:
            bridge = sub_bridge  # 批量模式：进度上报给总进度条

        def work():
            from platforms.base import VideoInfo, now_str
            from toolbox.config_manager import get_export_subdir
            from media_downloader import download_media
            info = VideoInfo(
                platform=it["platform"],
                title=it.get("title") or it.get("date_part") or "未知",
                author=it.get("author", ""),  # 关键：让下载落到 {平台}_媒体/{作者}/ 子目录
                cover_url=cover,
                publish_time=it.get("publish_time", ""),  # 关键：让 date_str 匹配 json date_part
                raw_url="",
                fetched_at=now_str(),
            )
            info.images = pics
            info.live_photos = lives
            base_dir = str(get_export_subdir("video_data"))
            return download_media([info], base_dir=base_dir,
                                  download_images=True, download_live=True, download_videos=False,
                                  progress_callback=bridge.emit_progress,
                                  byte_progress_callback=bridge.emit_bytes,
                                  wait_range=(0.5, 1.5), cancel_event=cancel_ev,
                                  skip_existing=not force, quiet=True)

        def done(result, err):
            if dlg is not None and dlg.isVisible():
                dlg.close()
                bridge.updated.disconnect(on_prog)
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                n = (result or {}).get("images", 0)
                n_cov = (result or {}).get("covers", 0)
                n_live = (result or {}).get("live", 0)
                n_skip = (result or {}).get("skipped", 0)
                cancelled = (result or {}).get("cancelled", False)
                self._dl_state[self._dl_key(it)] = "done" if not cancelled else None
                if not n and not n_live and not n_cov and n_skip:
                    msg = f"✅ {n_skip} 个文件已下载过，自动跳过"
                else:
                    msg = f"✅ 已下载图片 {n} 张"
                    if n_cov:
                        msg += f" + 封面 {n_cov} 张"
                    if n_live:
                        msg += f" + Live图视频 {n_live} 个"
                    if n_skip:
                        msg += f"（跳过已下载 {n_skip} 个）"
                if cancelled:
                    msg += "（已取消）"
                self.app.toast(msg, "success" if not cancelled else "warning")
            # 刷新该条目（重新扫描磁盘，新下载的图会显示本地缩略图）
            if not quiet:  # 批量模式由 _advance 统一刷新一次
                self._refresh_rows_for([it])
            if on_finished:
                on_finished()

        run_bg(work, done)

    def _fill_local_thumb(self, lbl, path, w, h):
        """后台加载本地图片到已有缩略图 label。"""
        lbl.setToolTip(path)

        def load():
            try:
                from PIL import Image
                img = Image.open(path).convert("RGB")
                img.thumbnail((w, h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                return None

        def done(data, err):
            try:
                if data:
                    qimg = QImage.fromData(data)
                    if not qimg.isNull():
                        lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except RuntimeError:
                pass  # 列表刷新后 label 已销毁

        run_bg(load, done)

    def _fill_net_thumb(self, lbl, url, w, h):
        """后台加载网络封面到已有缩略图 label。"""

        def load():
            try:
                import requests
                h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
                # 图床防盗链：微博 sinaimg 无 Referer 必 403；B站/xhs 顺手带上
                if "hdslb" in url or "bilibili" in url:
                    h["Referer"] = "https://www.bilibili.com/"
                elif "sinaimg" in url or "weibo" in url:
                    h["Referer"] = "https://weibo.com/"
                elif "xhscdn" in url or "xiaohongshu" in url:
                    h["Referer"] = "https://www.xiaohongshu.com/"
                if url.startswith("//"):
                    url2 = "https:" + url
                else:
                    url2 = url
                resp = requests.get(url2, timeout=8, headers=h)
                return resp.content
            except Exception:
                return None

        def done(data, err):
            try:
                if data:
                    qimg = QImage.fromData(data)
                    if not qimg.isNull():
                        lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except RuntimeError:
                pass

        run_bg(load, done)

    def on_show(self):
        self._refresh_collected()
        self._db_reload_tables()

    # ==================== 数据库浏览（增量采集库） ====================

    _DB_PAGE_SIZE = 200
    _DB_HEADERS = ["", "获取时间", "类型", "标题", "作者", "发布时间", "播放/阅览",
                   "点赞", "评论", "链接"]

    def _build_db_tab(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        title = QLabel("🗄 数据库浏览")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        v.addWidget(title)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("平台表:"))
        self.db_plat_sel = QComboBox()
        self.db_plat_sel.setFixedWidth(150)
        self.db_plat_sel.currentIndexChanged.connect(lambda _: self._db_on_platform_changed())
        bar.addWidget(self.db_plat_sel)
        bar.addWidget(QLabel("作者:"))
        self.db_author_sel = QComboBox()
        self.db_author_sel.setFixedWidth(170)
        self.db_author_sel.currentIndexChanged.connect(lambda _: self._db_reload())
        bar.addWidget(self.db_author_sel)
        self.db_search = QLineEdit()
        self.db_search.setPlaceholderText("🔍 搜索标题/作者/正文/ID...")
        # 防抖 300ms（与已获取页同款）
        self._db_search_timer = QTimer(self)
        self._db_search_timer.setSingleShot(True)
        self._db_search_timer.setInterval(300)
        self._db_search_timer.timeout.connect(self._db_reload)
        self.db_search.textChanged.connect(self._db_search_timer.start)
        bar.addWidget(self.db_search, 1)
        refresh_btn = icon_button("🔄", "刷新数据库表", (40, 30), True, "font-size: 15px;", self._db_reload_tables)
        bar.addWidget(refresh_btn)
        v.addLayout(bar)

        # 勾选工具行（购物/票务页同款）：全选/反选/清除 + 导出/删除选中 + 已勾选计数
        selbar = QHBoxLayout()
        self.db_sel_all_btn = QPushButton("☑ 全选")
        self.db_sel_all_btn.clicked.connect(lambda: self._db_set_all_checks(True))
        self.db_sel_inv_btn = QPushButton("☯ 反选")
        self.db_sel_inv_btn.clicked.connect(self._db_invert_checks)
        self.db_sel_clr_btn = QPushButton("✖ 清除")
        self.db_sel_clr_btn.clicked.connect(lambda: self._db_set_all_checks(False))
        self.db_exp_btn = QPushButton("📤 导出选中")
        self.db_exp_btn.clicked.connect(self._db_export_checked)
        self.db_del_btn = QPushButton("🗑 删除选中")
        self.db_del_btn.clicked.connect(self._db_delete_checked)
        self.db_del_btn.setStyleSheet(
            "QPushButton { color: #c62828; }")
        self.db_check_lbl = QLabel("已勾选 0 条")
        self.db_check_lbl.setStyleSheet("color: #888;")
        for w in (self.db_sel_all_btn, self.db_sel_inv_btn, self.db_sel_clr_btn,
                  self.db_exp_btn, self.db_del_btn):
            selbar.addWidget(w)
        selbar.addStretch(1)
        selbar.addWidget(self.db_check_lbl)
        v.addLayout(selbar)

        self.db_table = QTableWidget(0, len(self._DB_HEADERS))
        self.db_table.setHorizontalHeaderLabels(self._DB_HEADERS)
        self.db_table.verticalHeader().setVisible(False)
        self.db_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.db_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.db_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.db_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.db_table.customContextMenuRequested.connect(self._db_context_menu)
        self.db_table.doubleClicked.connect(self._db_open_link)
        self.db_table.cellClicked.connect(self._db_on_cell_clicked)
        self.db_table.itemChanged.connect(self._on_db_item_changed)
        hdr = self.db_table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        self.db_table.setColumnWidth(0, 34)
        for col, w in ((1, 130), (2, 56), (4, 110), (5, 130), (6, 80), (7, 56), (8, 56)):
            self.db_table.setColumnWidth(col, w)
        v.addWidget(self.db_table, 1)

        pager = QHBoxLayout()
        prev_btn = QPushButton("◀ 上一页")
        prev_btn.clicked.connect(lambda: self._db_turn(-1))
        next_btn = QPushButton("下一页 ▶")
        next_btn.clicked.connect(lambda: self._db_turn(1))
        self.db_page_lbl = QLabel("0 条")
        self.db_page_lbl.setStyleSheet("color: #888;")
        pager.addWidget(prev_btn)
        pager.addWidget(next_btn)
        pager.addStretch(1)
        pager.addWidget(self.db_page_lbl)
        v.addLayout(pager)
        return f

    def _db_turn(self, delta: int):
        pages = max(1, -(-self._db_total // self._DB_PAGE_SIZE))
        nxt = min(max(1, self._db_page + delta), pages)
        if nxt != self._db_page:
            self._db_page = nxt
            self._db_reload()

    def _db_reload_tables(self):
        """刷新平台表下拉（含行数），保持当前选择。"""
        def work():
            from toolbox import increment_db
            return increment_db.list_tables()

        def done(tables, err):
            if err:
                return
            tables = tables or []
            prev = self.db_plat_sel.currentText()
            self.db_plat_sel.blockSignals(True)
            self.db_plat_sel.clear()
            names = [f"{n}（{c}）" for n, c in tables] or ["（空）"]
            self.db_plat_sel.addItems(names)
            idx = next((i for i, s in enumerate(names) if s.startswith(prev)), 0) if prev else 0
            self.db_plat_sel.setCurrentIndex(max(0, idx))
            self.db_plat_sel.blockSignals(False)
            if not tables:
                self.db_table.setRowCount(0)
                self._db_rows = []
                self._db_total = 0
                self._db_update_check_count()
                self.db_author_sel.blockSignals(True)
                self.db_author_sel.clear()
                self.db_author_sel.addItems(["全部作者"])
                self.db_author_sel.blockSignals(False)
                self.db_page_lbl.setText(
                    "暂无数据 · 请在「账号获取」页开启「增量模式」并点「获取最新」")
                return
            self._db_reload_authors()

        run_bg(work, done)

    def _db_platform(self) -> str:
        text = self.db_plat_sel.currentText() or ""
        return "" if text.startswith("（") else text.split("（")[0].strip()

    def _db_reload(self):
        plat = self._db_platform()
        author = self._db_author_filter()

        def work():
            from toolbox import increment_db
            kw = self.db_search.text().strip()
            offset = (self._db_page - 1) * self._DB_PAGE_SIZE
            return increment_db.query_rows(plat, kw, author, self._DB_PAGE_SIZE, offset)

        def done(result, err):
            if err:
                self.db_page_lbl.setText("加载失败")
                return
            rows, total = result
            self._db_rows = rows or []
            self._db_total = total
            self._db_filling = True
            self.db_table.setRowCount(len(self._db_rows))
            for r, row in enumerate(self._db_rows):
                # (dedup_key, fetched_at, kind, video_id, title, author, publish_time,
                #  play, like, comment, share, fav, dm, coin, duration, desc,
                #  cover, video_url, tags, category, music, raw_json, content_key)
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                              | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Unchecked)
                self.db_table.setItem(r, 0, chk)
                vals = [row[1], {"dynamic": "动态", "image": "图文"}.get(row[2], "视频"),
                        row[4], row[5], row[6], _fmt(row[7]), _fmt(row[8]), _fmt(row[9]),
                        row[17]]
                for c, text in enumerate(vals):
                    item = QTableWidgetItem(str(text or ""))
                    if c == 2:
                        item.setToolTip(str(row[15] or "")[:400])
                    elif c == 3:  # 作者列：点击即筛选
                        item.setToolTip("点击按该作者筛选")
                        item.setForeground(QBrush(QColor("#1565C0")))
                        item.setData(Qt.UserRole, "author")
                    self.db_table.setItem(r, c + 1, item)
            self._db_filling = False
            self._db_update_check_count()
            pages = max(1, -(-total // self._DB_PAGE_SIZE))
            label = plat if not author else f"{plat} · 作者「{author}」"
            self.db_page_lbl.setText(
                f"{label} · 共 {total} 条 · 第 {self._db_page}/{pages} 页 · "
                f"点击作者筛选，双击打开链接，右键更多")

        run_bg(work, done)

    # ==================== 数据库浏览·作者快速筛选 ====================

    def _db_author_filter(self) -> str:
        text = self.db_author_sel.currentText() or ""
        return "" if text.startswith("全部") else text.split("（")[0].strip()

    def _db_on_platform_changed(self):
        """平台表切换 → 刷新作者下拉后重载（保持作者选择）。"""
        self._db_reload_authors()

    def _db_reload_authors(self):
        plat = self._db_platform()

        def work():
            from toolbox import increment_db
            return increment_db.list_authors(plat)

        def done(authors, err):
            if err:
                return
            prev = self.db_author_sel.currentText()
            prev_author = "" if prev.startswith("全部") else prev.split("（")[0].strip()
            self.db_author_sel.blockSignals(True)
            self.db_author_sel.clear()
            names = ["全部作者"] + [f"{a}（{c}）" for a, c in (authors or [])]
            self.db_author_sel.addItems(names)
            idx = next((i for i, s in enumerate(names) if s.split("（")[0].strip() == prev_author), 0) \
                if prev_author else 0
            self.db_author_sel.setCurrentIndex(idx)
            self.db_author_sel.blockSignals(False)
            self._db_page = 1
            self._db_reload()

        run_bg(work, done)

    def _db_on_cell_clicked(self, row: int, col: int):
        """点击作者列 → 快速按该作者筛选（再次点击同作者 → 清除筛选）。"""
        if col != 4:
            return
        it = self.db_table.item(row, col)
        if it is None:
            return
        author = it.text().strip()
        if not author:
            return
        cur = self._db_author_filter()
        target = "" if cur == author else author
        self._db_set_author_filter(target)

    def _db_set_author_filter(self, author: str):
        """按作者名选中下拉项（不在列表中则临时插入）。"""
        if not author:
            self.db_author_sel.blockSignals(True)
            self.db_author_sel.setCurrentIndex(0)
            self.db_author_sel.blockSignals(False)
            self._db_page = 1
            self._db_reload()
            return
        names = [self.db_author_sel.itemText(i) for i in range(self.db_author_sel.count())]
        idx = next((i for i, s in enumerate(names)
                    if s.split("（")[0].strip() == author), None)
        self.db_author_sel.blockSignals(True)
        if idx is None:
            self.db_author_sel.insertItem(0, author)
            idx = 0
        self.db_author_sel.setCurrentIndex(idx)
        self.db_author_sel.blockSignals(False)
        self._db_page = 1
        self._db_reload()

    # ==================== 数据库浏览·勾选（购物/票务页同款） ====================

    def _on_db_item_changed(self, item):
        if not self._db_filling and item.column() == 0:
            self._db_update_check_count()

    def _db_row_checked(self, row: int) -> bool:
        it = self.db_table.item(row, 0)
        return bool(it and it.checkState() == Qt.Checked)

    def _db_checked_rows(self) -> list:
        return [r for r in range(self.db_table.rowCount()) if self._db_row_checked(r)]

    def _db_set_all_checks(self, checked: bool):
        self._db_filling = True
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.db_table.rowCount()):
            it = self.db_table.item(r, 0)
            if it:
                it.setCheckState(state)
        self._db_filling = False
        self._db_update_check_count()

    def _db_invert_checks(self):
        self._db_filling = True
        for r in range(self.db_table.rowCount()):
            it = self.db_table.item(r, 0)
            if it:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                                 else Qt.Checked)
        self._db_filling = False
        self._db_update_check_count()

    def _db_update_check_count(self):
        n = len(self._db_checked_rows())
        self.db_check_lbl.setText(f"已勾选 {n} 条")

    def _db_delete_checked(self):
        rows = self._db_checked_rows()
        if not rows:
            self.app.toast("请先勾选要删除的记录（表格第一列复选框）", "warning")
            return
        plat = self._db_platform()
        if not plat:
            return
        if QMessageBox.question(
                self, "删除", f"确定删除 {plat} 表中勾选的 {len(rows)} 条记录？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        sel = [self._db_rows[r] for r in rows if 0 <= r < len(self._db_rows)]

        def work():
            from toolbox import increment_db
            return sum(1 for row in sel if increment_db.delete_row(plat, row[0]))

        def done(n, err):
            if err:
                self.app.toast("删除失败", "error")
                return
            self.app.toast(f"已删除 {n} 条", "success")
            self._db_reload_tables()

        run_bg(work, done)

    def _db_export_checked(self):
        """导出勾选记录为 JSON（含 raw_json 全字段）到 download/已获取数据/。"""
        rows = self._db_checked_rows()
        if not rows:
            self.app.toast("请先勾选要导出的记录（表格第一列复选框，支持全选）", "warning")
            return
        plat = self._db_platform() or "database"
        sel = [self._db_rows[r] for r in rows if 0 <= r < len(self._db_rows)]

        def work():
            import json as _json
            from toolbox.config_manager import get_export_subdir
            out = []
            for row in sel:
                rec = {}
                # raw_json 是采集时的完整 asdict（中文键），优先取全量
                try:
                    rec = _json.loads(row[21]) if row[21] else {}
                except Exception:
                    rec = {}
                # DB 列字段（英文键）补齐/覆盖核心信息
                rec.update({
                    "平台": plat, "获取时间": row[1], "类型": row[2],
                    "视频ID": row[3], "标题": row[4], "作者": row[5],
                    "发布时间": row[6], "播放量": row[7], "点赞数": row[8],
                    "评论数": row[9], "分享数": row[10], "收藏数": row[11],
                    "弹幕数": row[12], "投币数": row[13], "时长": row[14],
                    "正文": row[15], "封面URL": row[16], "链接": row[17],
                    "标签": row[18], "分类": row[19], "音乐": row[20],
                })
                out.append(rec)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"数据库导出_{plat}_{len(out)}条_{ts}.json"
            path = get_export_subdir("video_data") / fname
            path.write_text(_json.dumps(out, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            return str(path)

        def done(path, err):
            if err or not path:
                self.app.toast(f"导出失败: {str(err)[:60]}", "error")
                return
            self.app.toast(f"✅ 已导出 {len(sel)} 条 → {path}", "success")

        run_bg(work, done)

    def _db_selected_row(self):
        rows = sorted({i.row() for i in self.db_table.selectedIndexes()})
        return [self._db_rows[r] for r in rows if 0 <= r < len(self._db_rows)]

    def _db_open_link(self, index):
        row = self._db_rows[index.row()] if 0 <= index.row() < len(self._db_rows) else None
        if row and row[17]:
            QDesktopServices.openUrl(QUrl(row[17]))

    def _db_context_menu(self, pos):
        sel = self._db_selected_row()
        if not sel:
            return
        menu = QMenu(self)
        act_open = menu.addAction("🌐 打开链接")
        act_copy = menu.addAction("📋 复制链接")
        menu.addSeparator()
        author = (sel[0][5] or "").strip()
        if author:
            act_author = menu.addAction(f"🔍 筛选作者「{author}」")
        else:
            act_author = None
        menu.addSeparator()
        act_del = menu.addAction(f"🗑 删除选中（{len(sel)} 条）")
        act = menu.exec(self.db_table.viewport().mapToGlobal(pos))
        if act == act_open:
            for row in sel:
                if row[17]:
                    QDesktopServices.openUrl(QUrl(row[17]))
        elif act == act_copy:
            QApplication.clipboard().setText("\n".join(r[17] for r in sel if r[17]))
            self.app.toast("已复制链接", "success")
        elif act_author is not None and act == act_author:
            self._db_set_author_filter(author)
        elif act == act_del:
            if QMessageBox.question(
                    self, "删除", f"确定删除 {len(sel)} 条记录？",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            plat = self._db_platform()

            def work():
                from toolbox import increment_db
                return sum(1 for row in sel if increment_db.delete_row(plat, row[0]))

            def done(n, err):
                if err:
                    self.app.toast("删除失败", "error")
                    return
                self.app.toast(f"已删除 {n} 条", "success")
                self._db_reload_tables()

            run_bg(work, done)

    # ==================== 详情窗 ====================

    def _show_detail(self, item):
        """弹二级窗口展示完整内容（ESC 关闭）。"""
        win = QDialog(self)
        win.setWindowTitle(f"详情 · {(item.get('title') or '')[:30]}")
        win.resize(1080, 720)
        win.setMinimumSize(640, 400)

        outer = QVBoxLayout(win)
        outer.setContentsMargins(10, 10, 10, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        plat_cn = platform_cn(item["platform"])
        title_lbl = QLabel(item.get("title", "") or "(无标题)")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)
        self._attach_copy_menu(title_lbl, item.get("title") or "", "标题")
        # 封面预览（右键：复制图片/复制链接/定位文件），无封面源则跳过
        self._add_detail_cover(v, item)
        # (行名, 显示值, 复制原文, 复制菜单名 None=不提供复制, 是否时间字段)
        dur = _fmt_duration_cn(item.get("duration"))
        info_lines = [
            ("平台", plat_cn, plat_cn, "平台", False),
            ("作者", "@" + (item.get("author") or "-"),
             item.get("author") or "", "发布者", False),
        ]
        if dur:
            info_lines.append(("时长", dur, dur, "时长", False))
        info_lines.extend([
            ("播放/阅览", _fmt(item.get("play_count", 0)),
             str(item.get("play_count") or 0), "播放/阅览", False),
            ("点赞", _fmt(item.get("like_count", 0)),
             str(item.get("like_count") or 0), "点赞", False),
            ("评论", _fmt(item.get("comment_count", 0)),
             str(item.get("comment_count") or 0), "评论", False),
            ("发布时间", item.get("publish_time") or "-",
             item.get("publish_time") or "", "时间", True),
            ("采集时间", item.get("fetched_at") or "-",
             item.get("fetched_at") or "", "时间", True),
            ("源文件", item.get("file_path") or "-",
             item.get("file_path") or "", "文件路径", False),
            ("视频链接", item.get("video_url") or "-",
             item.get("video_url") or "", "作品链接", False),
            ("原始URL", item.get("raw_url") or "-",
             item.get("raw_url") or "", "原始链接", False),
        ])
        for k, val, raw, copy_title, is_time in info_lines:
            row_f = QHBoxLayout()
            kl = QLabel(f"{k}:")
            kl.setFixedWidth(84)
            kl.setStyleSheet("color: #6b7280; font-size: 12px;")
            row_f.addWidget(kl)
            wl = QLabel(_wrappable(str(val)))
            wl.setWordWrap(True)
            wl.setTextFormat(Qt.PlainText)
            wl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            # 显式最小尺寸：零宽空格后最长词仍可能比窄窗宽，
            # 最小尺寸强制为 1 才能彻底摆脱 QLabel minimumSizeHint 撑宽布局
            wl.setMinimumSize(1, 1)
            wl.setStyleSheet("font-size: 12px;")
            row_f.addWidget(wl, 1)
            if copy_title:
                self._attach_copy_menu(wl, raw, copy_title, is_time)
            v.addLayout(row_f)
        desc = item.get("description") or ""
        if desc:
            dh = QLabel("正文")
            dh.setStyleSheet("color: #6b7280; font-weight: bold;")
            v.addWidget(dh)
            dl = QLabel(desc)
            dl.setWordWrap(True)
            # \n 在富文本模式下不生效，转 <br>；零宽空格须在转义前插入
            # （先转义再插会拆坏 &amp; 实体），长 URL 才能断行
            dl.setTextFormat(Qt.RichText)
            dl.setText(_wrappable(desc).replace("&", "&amp;")
                       .replace("<", "&lt;").replace(">", "&gt;")
                       .replace("\n", "<br>"))
            dl.setMinimumSize(1, 1)
            dl.setStyleSheet("font-size: 12px;")
            v.addWidget(dl)
            self._attach_copy_menu(dl, desc, "正文")
        # 媒体文件（实际下载的图片/视频，双击图片看大图、双击视频缩略图播放）
        mfs = item.get("media_files") or []
        if mfs:
            imgs = [m for m in mfs if m["type"] == "image"]
            vids = [m for m in mfs if m["type"] == "video"]
            mh = QLabel(f"🖼 媒体文件（{len(imgs)} 图 / {len(vids)} 视频）")
            mh.setStyleSheet("color: #6b7280; font-weight: bold;")
            v.addWidget(mh)
            if imgs or vids:
                # 流式布局：缩略图按窗口宽度自动换行（窄窗多行，不出横向滚动条）
                cells = [self._load_local_thumb_cell(im["path"], 128, is_video=False)
                         for im in imgs]
                cells += [self._load_local_thumb_cell(vd["path"], 128, is_video=True)
                          for vd in vids]
                grid_holder = QWidget()
                flow = _FlowLayout(grid_holder, spacing=4)
                for c in cells:
                    flow.addWidget(c)
                v.addWidget(grid_holder)
        v.addStretch(1)

        # 底部操作栏
        bottom = QHBoxLayout()
        tip = QLabel("按 ESC 退出（拖动窗口边缘可调整大小）")
        tip.setStyleSheet("color: #888; font-size: 11px;")
        bottom.addWidget(tip)
        bottom.addStretch(1)
        exp_btn = QPushButton("📷 导出预览图")
        exp_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px;")
        exp_btn.clicked.connect(lambda: self._export_preview_image(item))
        bottom.addWidget(exp_btn)
        outer.addLayout(bottom)
        win.exec()

    def _add_detail_cover(self, v, item):
        """详情窗封面预览：本地文件优先（视频同名官方封面/首图），网络封面兜底。

        右键菜单：📋 复制图片（进剪贴板）/ 🔗 复制封面链接 / 📂 打开媒体位置。
        无任何封面源时不占位。
        """
        mfs = item.get("media_files") or []
        vids = [m for m in mfs if m.get("type") == "video"]
        imgs = [m for m in mfs if m.get("type") == "image"]
        cu = (item.get("cover_url") or "").strip()
        local = ""
        if vids:  # 与视频同名的图片 = 官方封面（B站下载时同步保存）
            vstems = {Path(v["path"]).stem for v in vids}
            for im in imgs:
                if Path(im["path"]).stem in vstems:
                    local = im["path"]
                    break
        if not local and cu and not cu.startswith(("http://", "https://", "//")) \
                and os.path.exists(cu):
            local = cu
        if not local and imgs:  # 图文帖：首图即封面
            local = imgs[0]["path"]
        url = cu if cu.startswith(("http://", "https://", "//")) else ""
        if not local and not url:
            return

        state = {"img": None}
        lbl = QLabel("🖼 封面加载中...")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedSize(320, 200)
        lbl.setStyleSheet(
            "background: #eef1f5; border-radius: 6px; color: #9aa1ad;")
        v.addWidget(lbl)

        def load():
            try:
                if local and os.path.exists(local):
                    from PIL import Image
                    img = Image.open(local).convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
                import requests
                h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0.0.0 Safari/537.36"}
                # 图床防盗链：与列表缩略图同源的 Referer 规则
                if "hdslb" in url or "bilibili" in url:
                    h["Referer"] = "https://www.bilibili.com/"
                elif "sinaimg" in url or "weibo" in url:
                    h["Referer"] = "https://weibo.com/"
                elif "xhscdn" in url or "xiaohongshu" in url:
                    h["Referer"] = "https://www.xiaohongshu.com/"
                resp = requests.get(("https:" + url) if url.startswith("//") else url,
                                    timeout=8, headers=h)
                return resp.content if resp.ok else None
            except Exception:
                return None

        def done(data, err):
            try:
                if not data:
                    lbl.setText("🖼 封面不可用")
                    return
                qimg = QImage.fromData(data)
                if qimg.isNull():
                    lbl.setText("🖼 封面不可用")
                    return
                state["img"] = qimg
                lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                    320, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except RuntimeError:
                pass  # 详情窗已关闭

        def menu(pos):
            m = QMenu(lbl)
            act_img = m.addAction("📋 复制图片")
            act_img.setEnabled(state["img"] is not None)
            act_url = m.addAction("🔗 复制封面链接")
            act_url.setEnabled(bool(url))
            act_open = m.addAction("📂 打开媒体位置")
            act_open.setEnabled(bool(local) and os.path.exists(local))
            act = m.exec(lbl.mapToGlobal(pos))
            if act is act_img and state["img"] is not None:
                QApplication.clipboard().setImage(state["img"])
                self.app.toast("已复制封面图片", "success")
            elif act is act_url and url:
                QApplication.clipboard().setText(
                    ("https:" + url) if url.startswith("//") else url)
                self.app.toast("已复制封面链接", "success")
            elif act is act_open and local and os.path.exists(local):
                self._reveal_media(local)

        lbl.setContextMenuPolicy(Qt.CustomContextMenu)
        lbl.customContextMenuRequested.connect(menu)
        run_bg(load, done)

    def _load_local_thumb_cell(self, path, size, is_video=False):
        """媒体网格单格：双击图片用系统看图器开大图 / 双击视频缩略图播放。

        视频先用 ffmpeg 抽首帧做封面（live 图 .mov 的静态帧），失败回退 🎬 占位。
        """
        if is_video:
            lbl = _ThumbLabel(lambda p=path: self._open_media(p))
            lbl.setText("🎬")
        else:
            lbl = _ThumbLabel(lambda p=path: self._open_media(p))
            lbl.setText("🖼")
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #eef1f5; border-radius: 4px;")
        lbl.setToolTip(path)

        # 右键缩略图 → 复制文件（可粘贴到文件夹/聊天软件）/ 打开媒体位置
        def _thumb_menu(pos):
            menu = QMenu(lbl)
            act_copy = menu.addAction("📋 复制视频" if is_video else "📋 复制图片文件")
            act = menu.addAction("📂 打开媒体位置")
            chosen = menu.exec(lbl.mapToGlobal(pos))
            if chosen is act_copy:
                self._copy_files_to_clipboard([path])
            elif chosen is act:
                self._reveal_media(path)

        lbl.setContextMenuPolicy(Qt.CustomContextMenu)
        lbl.customContextMenuRequested.connect(_thumb_menu)

        def load():
            try:
                if is_video:
                    from toolbox.live_video import find_ffmpeg
                    exe = find_ffmpeg()
                    if not exe:
                        return None
                    import subprocess
                    r = subprocess.run(
                        [exe, "-hide_banner", "-loglevel", "error", "-ss", "0.5",
                         "-i", path, "-vframes", "1", "-f", "image2", "pipe:1"],
                        capture_output=True, timeout=15)
                    data = r.stdout if r.returncode == 0 and len(r.stdout) > 100 else b""
                    if not data:  # 首帧 0.5s 失败（短视频/0.5s 处黑帧），重试第 0 秒
                        r = subprocess.run(
                            [exe, "-hide_banner", "-loglevel", "error", "-ss", "0",
                             "-i", path, "-vframes", "1", "-f", "image2", "pipe:1"],
                            capture_output=True, timeout=15)
                        data = r.stdout if r.returncode == 0 else b""
                    return data or None
                from PIL import Image
                img = Image.open(path).convert("RGB")
                img.thumbnail((size, size), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                return None

        def done(data, err):
            try:
                if data:
                    qimg = QImage.fromData(data)
                    if qimg.isNull():
                        return  # 损坏/非图片数据：保持占位，避免 null pixmap 警告刷屏
                    pix = QPixmap.fromImage(qimg).scaled(
                        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    if is_video:
                        # 居中叠 ▶ 播放角标
                        canvas = QPixmap(size, size)
                        canvas.fill(Qt.transparent)
                        cp = QPainter(canvas)
                        cp.drawPixmap((size - pix.width()) // 2,
                                      (size - pix.height()) // 2, pix)
                        cp.setPen(QPen(QColor(0, 0, 0, 130), 1))
                        cp.setBrush(QColor(0, 0, 0, 110))
                        cp.drawEllipse(size // 2 - 17, size // 2 - 17, 34, 34)
                        cp.setPen(QPen(QColor("white"), 2))
                        tri = QPolygonF([QPointF(size / 2 - 6, size / 2 - 9),
                                         QPointF(size / 2 - 6, size / 2 + 9),
                                         QPointF(size / 2 + 10, size / 2)])
                        cp.setBrush(QColor("white"))
                        cp.drawPolygon(tri)
                        cp.end()
                        lbl.setPixmap(canvas)
                    else:
                        lbl.setPixmap(pix)
            except RuntimeError:
                pass  # 详情窗已关闭

        run_bg(load, done)
        return lbl

    def _fmt_time_val(self, raw, with_seconds: bool) -> str:
        """时间文本按带/不带秒重排（发布时间无秒、采集时间有秒）。"""
        s = str(raw or "").strip()
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", s)
        if not m:
            return s
        date, hh, mm, ss = m.groups()
        base = f"{date} {int(hh):02d}:{mm}"
        return f"{base}:{ss or '00'}" if with_seconds else base

    def _attach_copy_menu(self, w, raw, title, is_time=False):
        """详情窗字段右键「复制」菜单：按右键所在字段决定复制内容。

        时间字段两项（不带秒在前、带秒在后）；字段无内容时菜单置灰。
        """

        def show_menu(pos):
            raw_val = str(raw or "")
            menu = QMenu(w)
            if is_time:
                act_no = menu.addAction("📋 复制时间（不带秒）")
                act_yes = menu.addAction("📋 复制时间（带秒）")
            else:
                act_no = menu.addAction(f"📋 复制{title}")
                act_yes = None
            if not raw_val.strip():
                for a in (act_no, act_yes):
                    if a:
                        a.setEnabled(False)
            act = menu.exec(w.mapToGlobal(pos))
            if act is None:
                return
            if is_time:
                text = self._fmt_time_val(raw_val, act is act_yes)
                QApplication.clipboard().setText(text)
                self.app.toast(f"已复制 {text}", "success")
            else:
                QApplication.clipboard().setText(raw_val)
                self.app.toast(f"已复制{title}", "success")

        w.setContextMenuPolicy(Qt.CustomContextMenu)
        w.customContextMenuRequested.connect(show_menu)

    def _reveal_media(self, path):
        """资源管理器中定位媒体文件（打开所在文件夹并选中）。"""
        if not os.path.exists(path):
            self.app.toast("媒体文件不存在或已被移动", "error")
            return
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            self.app.toast(f"打开媒体位置失败: {e}", "error")

    def _open_media(self, path):
        """用系统默认程序打开媒体文件（图片/视频）。"""
        try:
            os.startfile(path)
        except Exception as e:
            self.app.toast(f"打开失败: {e}", "error")

    def _copy_files_to_clipboard(self, paths):
        """媒体文件复制到剪贴板（Windows 文件复制，可粘贴到文件夹/聊天软件发送）。"""
        paths = [p for p in (paths or []) if p and os.path.exists(p)]
        if not paths:
            self.app.toast("媒体文件不存在或已被移动", "error")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(os.path.normpath(p)) for p in paths])
        QApplication.clipboard().setMimeData(mime)
        if len(paths) == 1:
            self.app.toast(f"已复制 {os.path.basename(paths[0])}，可直接粘贴", "success")
        else:
            self.app.toast(f"已复制 {len(paths)} 个文件，可直接粘贴", "success")

    # ==================== 导出预览图 ====================

    def _export_preview_image(self, item):
        """导出单个条目的预览拼贴图（紫色渐变 banner + 平台数据卡片 + 图片网格 + 正文卡片）。"""
        def work():
            from PIL import Image, ImageDraw, ImageFont, ImageOps

            _EMOJI_RE = re.compile(
                r'[\U0001F000-\U0001FAFF'      # 各 emoji 区块（含 1FA70+ 扩展）
                r'\U00002190-\U000021FF'       # 箭头 ←→↑↓
                r'\U00002300-\U000023FF'       # ⏰⌚⏳ 杂项技术符号
                r'\U000025A0-\U000025FF'       # ▶◼● 几何图形（雅黑缺字形会变方框）
                r'\U00002600-\U000027BF'       # ☀☁❤✂ 杂项符号/装饰符号
                r'\U00002B00-\U00002BFF'       # ⭐⬆⬇ 杂项符号与箭头
                r'\U0000FE0E\U0000FE0F'        # 变体选择符（❤️ 等序列尾部）
                r'\U0000200D\U000020E3]'       # 零宽连接符 / 键帽组合
            )

            def _is_emoji_ch(ch):
                return bool(_EMOJI_RE.match(ch))

            def _draw_mixed(d, x, y, text, color, font_main, font_emoji):
                """绘制含 emoji 的字符串：emoji 段用 emoji 字体，普通段用主字体，返回结束 x。

                段间步进必须用 getlength（排版步进宽度）——textbbox 是墨迹
                包围盒：空格段宽≈0、首字符带左边距时都会把后续段叠到前一段
                上（曾致横向错位）。各段统一按 font_main 基线对齐（anchor=ls），
                避免两种字体 ascent 不一致时 emoji 上下飘。"""
                if not text:
                    return x
                if isinstance(font_main, ImageFont.FreeTypeFont):
                    baseline = y + font_main.getmetrics()[0]
                    kw = {"anchor": "ls"}
                else:  # 位图字体（主字体加载全部失败的兜底）不支持 anchor
                    baseline, kw = y, {}
                cx = x
                seg = ""
                cur_emoji = None
                for ch in text:
                    ie = _is_emoji_ch(ch) and font_emoji is not None
                    if cur_emoji is None:
                        cur_emoji = ie
                    if ie == cur_emoji:
                        seg += ch
                    else:
                        font = font_emoji if cur_emoji else font_main
                        d.text((cx, baseline), seg, fill=color, font=font, **kw)
                        cx += font.getlength(seg)
                        seg = ch
                        cur_emoji = ie
                if seg:
                    font = font_emoji if cur_emoji else font_main
                    d.text((cx, baseline), seg, fill=color, font=font, **kw)
                    cx += font.getlength(seg)
                return cx

            def _mixed_len(text, font_main, font_emoji):
                """含 emoji 字符串的排版宽度（与 _draw_mixed 同分段规则）。"""
                if not text:
                    return 0.0
                total = 0.0
                seg = ""
                cur_emoji = None
                for ch in text:
                    ie = _is_emoji_ch(ch) and font_emoji is not None
                    if cur_emoji is None:
                        cur_emoji = ie
                    if ie == cur_emoji:
                        seg += ch
                    else:
                        total += (font_emoji if cur_emoji else font_main).getlength(seg)
                        seg, cur_emoji = ch, ie
                if seg:
                    total += (font_emoji if cur_emoji else font_main).getlength(seg)
                return total

            def _wrap_mixed(text, font_main, font_emoji, max_w):
                """按排版宽度换行（emoji 段用 emoji 字体量宽），尊重显式 \\n 换行。

                PIL 的 d.text 遇 \\n 会自动画多行，若外层按单行高度递进会行行
                重叠——必须在此处先把 \\n 切成独立行。"""
                lines, cur, cur_w = [], "", 0.0
                for ch in text:
                    if ch == "\r":
                        continue
                    if ch == "\n":
                        lines.append(cur)
                        cur, cur_w = "", 0.0
                        continue
                    font = (font_emoji if _is_emoji_ch(ch) and font_emoji is not None
                            else font_main)
                    w = font.getlength(ch)
                    if cur_w + w > max_w and cur:
                        lines.append(cur)
                        cur, cur_w = ch, w
                    else:
                        cur += ch
                        cur_w += w
                if cur:
                    lines.append(cur)
                return lines

            # 中文字体（多字重）
            font_paths = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                          "C:/Windows/Fonts/simsun.ttc"]
            font_file = next((f for f in font_paths if os.path.exists(f)), "")
            # emoji 字体（Windows 自带）
            emoji_paths = ["C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/segui.ttf",
                           "C:/Windows/Fonts/AppleColorEmoji.ttf"]
            emoji_file = next((f for f in emoji_paths if os.path.exists(f)), "")

            def _emoji_font_of(size):
                """与主字体同字号的 emoji 字体（无 emoji 字体时返回 None 回退主字体）。"""
                if not emoji_file:
                    return None
                try:
                    return ImageFont.truetype(emoji_file, size)
                except Exception:
                    return None

            try:
                f_big = ImageFont.truetype(font_file, 36)
                f_title = ImageFont.truetype(font_file, 26)
                f_info = ImageFont.truetype(font_file, 20)
                f_body = ImageFont.truetype(font_file, 18)
                f_small = ImageFont.truetype(font_file, 14)
            except Exception:
                f_big = f_title = f_info = f_body = f_small = ImageFont.load_default()
            f_emoji = _emoji_font_of(20)        # 配 f_info
            f_emoji_sm = _emoji_font_of(14)     # 配 f_small
            f_emoji_body = _emoji_font_of(18)   # 配 f_body
            f_emoji_title = _emoji_font_of(26)  # 配 f_title

            # ============ 布局参数 ============
            W = 720
            pad = 24
            gap = 14
            thumb = 200
            thumb_cols = 3

            # ============ 颜色方案 ============
            BG_TOP = (102, 126, 234)     # 紫色 #667eea
            BG_BOTTOM = (118, 75, 162)   # 深紫 #764ba2
            CARD_BG = (30, 32, 48)       # 卡片 #1e2030
            CARD_BG2 = (24, 26, 40)      # 更深卡片 #181a28
            TEXT_PRIMARY = (255, 255, 255)
            TEXT_SECONDARY = (180, 184, 200)
            TEXT_TERTIARY = (140, 144, 160)
            ACCENT = (139, 92, 246)      # 紫色 #8b5cf6
            ACCENT_GREEN = (74, 222, 128)
            ACCENT_RED = (255, 107, 129)
            ACCENT_BLUE = (96, 165, 250)

            plat_cn = platform_cn(item.get("platform"))
            plat_en = {"douyin": "Douyin", "weibo": "Weibo", "bilibili": "bilibili"}.get(
                item.get("platform"), (item.get("platform") or "").title())
            plat_emoji = {"douyin": "🎵", "weibo": "💬", "bilibili": "📺",
                          "xiaohongshu": "📕", "kuaishou": "⚡", "haokan": "👀",
                          "weishi": "🎥", "pear": "🍐", "pipigx": "😂"}.get(
                item.get("platform"), "📄")
            plat_accent = {"douyin": ACCENT_RED, "weibo": ACCENT_RED, "bilibili": ACCENT_BLUE}.get(
                item.get("platform"), ACCENT)

            title = re.sub(r'[\r\n]+', ' ', item.get("title") or "(无标题)")[:60]
            author = item.get("author") or "-"
            play_count = _fmt(item.get("play_count", 0))
            like_count = _fmt(item.get("like_count", 0))
            comment_count = _fmt(item.get("comment_count", 0))
            pub = item.get("publish_time") or "-"
            fetched = item.get("fetched_at") or "-"
            desc = (item.get("description") or "")[:300]

            # 标题自适应：按宽度换行（最多2行），放不下则逐级缩小字号
            f_head = f_big
            f_emoji_head = _emoji_font_of(36)
            title_lines = [title]
            for size in (36, 32, 28, 24, 20):
                try:
                    f_try = ImageFont.truetype(font_file, size) if font_file else ImageFont.load_default()
                except Exception:
                    f_try = ImageFont.load_default()
                lines = _wrap_mixed(title, f_try, _emoji_font_of(size), W - 2 * pad)
                if len(lines) <= 2:
                    f_head, title_lines = f_try, lines
                    f_emoji_head = _emoji_font_of(size)
                    break
                f_head, title_lines = f_try, lines
            if len(title_lines) > 2:
                title_lines = title_lines[:2]
                title_lines[-1] = title_lines[-1][:-1] + "…"

            # 副标题超宽截断
            subtitle = f"@{author}  ·  {fetched}"
            while f_info.getlength(subtitle) > W - 2 * pad and len(subtitle) > 4:
                subtitle = subtitle[:-2] + "…"

            # banner 高度按标题行数自适应（ascent+descent 才是真实行高）
            line_h = sum(f_head.getmetrics()) + 10
            title_block_h = len(title_lines) * line_h
            banner_h = max(180, 46 + title_block_h + 68)  # 顶部平台行 + 标题块 + 副标题/底条

            # ============ 1. 顶部紫色渐变 banner ============
            banner = Image.new("RGB", (W, banner_h), BG_TOP)
            bdraw = ImageDraw.Draw(banner)
            for y in range(banner_h):
                r = BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * y // banner_h
                g = BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * y // banner_h
                b = BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * y // banner_h
                bdraw.line([(0, y), (W, y)], fill=(r, g, b))

            bdraw.line([(pad, 28), (W - pad, 28)], fill=(255, 255, 255, 80), width=1)
            _draw_mixed(bdraw, pad, 14, f"{plat_emoji}  {plat_cn}", TEXT_PRIMARY, f_small, f_emoji_sm)
            title_y = 42
            for tl in title_lines:
                tl_w = _mixed_len(tl, f_head, f_emoji_head)
                _draw_mixed(bdraw, int((W - tl_w) // 2), title_y, tl,
                            TEXT_PRIMARY, f_head, f_emoji_head)
                title_y += line_h
            sub_y = title_y + 2
            sub_w = f_info.getlength(subtitle)
            bdraw.text(((W - sub_w) // 2, sub_y), subtitle, fill=TEXT_SECONDARY, font=f_info)
            bdraw.line([(pad, banner_h - 24), (W - pad, banner_h - 24)], fill=(255, 255, 255, 60), width=1)
            _draw_mixed(bdraw, pad, banner_h - 18, "📊 内容数据预览", TEXT_PRIMARY, f_small, f_emoji_sm)

            # ============ 2. 平台数据卡片 ============
            card_x = pad
            card_w = W - 2 * pad
            card_h = 150
            data_items = [
                ("播放", play_count, ACCENT_GREEN, "▶"),
                ("点赞", like_count, ACCENT_RED, "❤"),
                ("评论", comment_count, ACCENT_BLUE, "💬"),
            ]
            stats_card = Image.new("RGB", (card_w, card_h), CARD_BG)
            sdraw = ImageDraw.Draw(stats_card)

            _draw_mixed(sdraw, 20, 14, f"{plat_emoji}  {plat_cn}", TEXT_PRIMARY, f_title, f_emoji_title)
            try:
                tag_w = 80
                tag_h = 22
                sdraw.rounded_rectangle(
                    (card_w - tag_w - 20, 14, card_w - 20, 14 + tag_h),
                    radius=11, fill=plat_accent)
                tag_text = plat_en
                tag_bbox = sdraw.textbbox((0, 0), tag_text, font=f_small)
                tag_text_w = tag_bbox[2] - tag_bbox[0]
                sdraw.text((card_w - 20 - tag_w // 2 - tag_text_w // 2, 17),
                           tag_text, fill=TEXT_PRIMARY, font=f_small)
            except Exception:
                sdraw.text((card_w - 90, 16), plat_en, fill=TEXT_PRIMARY, font=f_small)

            sdraw.line([(20, 48), (card_w - 20, 48)], fill=(60, 64, 80), width=1)

            col_w = (card_w - 40) // 3
            try:
                f_val = ImageFont.truetype(font_file, 24)
            except Exception:
                f_val = f_info
            for i, (label, value, color, icon) in enumerate(data_items):
                x = 20 + i * col_w
                _draw_mixed(sdraw, x, 58, f"{icon}  {label}", TEXT_SECONDARY, f_info, f_emoji)
                sdraw.text((x, 84), value, fill=color, font=f_val)

            time_text = f"发布: {pub}  ·  采集: {fetched}"
            sdraw.text((20, card_h - 26), time_text, fill=TEXT_TERTIARY, font=f_small)

            # ============ 3. 媒体网格卡片（图片缩略图 + 视频抽帧封面） ============
            grid_card = None
            grid_card_h = 0
            imgs = [m["path"] for m in item.get("media_files", []) if m["type"] == "image"]
            vids = [m["path"] for m in item.get("media_files", []) if m["type"] == "video"]

            _ffmpeg_ok = [""]  # 缓存可抽帧的 ffmpeg 路径（PATH 里可能是精简版）

            def _video_cover(vpath, cell):
                """用 ffmpeg 抽视频第 1 秒帧作为封面，失败返回 None。"""
                import subprocess
                from io import BytesIO
                args_base = ["-y", "-ss", "1", "-i", vpath, "-frames:v", "1",
                             "-vf", f"scale={cell}:{cell}:force_original_aspect_ratio=increase,crop={cell}:{cell}",
                             "-f", "image2", "-c:v", "mjpeg", "-"]
                candidates = []
                if _ffmpeg_ok[0]:
                    candidates.append(_ffmpeg_ok[0])
                else:
                    from toolbox.utils import find_ffmpeg_exe
                    which = find_ffmpeg_exe()
                    if which:
                        candidates.append(which)
                    candidates.append("D:/Programs/ffmpeg/bin/ffmpeg.exe")
                for exe in candidates:
                    if not exe or not os.path.exists(exe):
                        continue
                    try:
                        r = subprocess.run([exe] + args_base, capture_output=True, timeout=20)
                        if r.returncode == 0 and r.stdout:
                            _ffmpeg_ok[0] = exe
                            return Image.open(BytesIO(r.stdout)).convert("RGB")
                    except Exception:
                        continue
                return None

            def _video_placeholder(cell):
                """视频封面占位卡（ffmpeg 不可用/抽帧失败时）。"""
                ph = Image.new("RGB", (cell, cell), (44, 48, 66))
                pd = ImageDraw.Draw(ph)
                try:
                    f_ico = ImageFont.truetype(emoji_file or font_file, 44)
                except Exception:
                    f_ico = f_title
                bb = pd.textbbox((0, 0), "🎬", font=f_ico)
                pd.text(((cell - (bb[2] - bb[0])) // 2, cell // 2 - 40), "🎬",
                        fill=(255, 255, 255), font=f_ico)
                label = "视频"
                bb2 = pd.textbbox((0, 0), label, font=f_info)
                pd.text(((cell - (bb2[2] - bb2[0])) // 2, cell // 2 + 24), label,
                        fill=TEXT_SECONDARY, font=f_info)
                return ph

            media_cells = [("img", p) for p in imgs] + [("vid", p) for p in vids]
            if media_cells:
                n_rows = (len(media_cells) + thumb_cols - 1) // thumb_cols
                cell = thumb  # 固定方格大小（正方形）
                thumb_gap = 8
                grid_h = n_rows * (cell + thumb_gap) + 16  # 内边距 8*2
                grid_card_h = grid_h + 40  # 卡片上下边距
                grid_card = Image.new("RGB", (card_w, grid_card_h), CARD_BG)
                gdraw = ImageDraw.Draw(grid_card)
                if imgs and vids:
                    head = f"🖼 媒体文件  ({len(imgs)} 图 · {len(vids)} 视频)"
                elif vids:
                    head = f"🎬 媒体文件  ({len(vids)} 视频)"
                else:
                    head = f"🖼 媒体文件  ({len(imgs)} 图)"
                _draw_mixed(gdraw, 20, 12, head, TEXT_PRIMARY, f_info, f_emoji)
                gdraw.line([(20, 42), (card_w - 20, 42)], fill=(60, 64, 80), width=1)
                inner_w = card_w - 32
                total_grid_w = thumb_cols * cell + (thumb_cols - 1) * thumb_gap
                grid_x0 = 16 + max(0, (inner_w - total_grid_w) // 2)  # 居中
                grid_y0 = 52
                for i, (kind, p) in enumerate(media_cells):
                    try:
                        if kind == "img":
                            im = Image.open(p).convert("RGB")
                        else:
                            im = _video_cover(p, cell) or _video_placeholder(cell)
                        im = ImageOps.fit(im, (cell, cell), Image.LANCZOS, centering=(0.5, 0.5))
                        row, col = i // thumb_cols, i % thumb_cols
                        x = grid_x0 + col * (cell + thumb_gap)
                        y = grid_y0 + row * (cell + thumb_gap)
                        mask = Image.new("L", im.size, 0)
                        ImageDraw.Draw(mask).rounded_rectangle(
                            (0, 0, im.size[0], im.size[1]), radius=8, fill=255)
                        grid_card.paste(im, (x, y), mask)
                    except Exception:
                        pass

            # ============ 4. 正文卡片 ============
            desc_card_h = 0
            desc_card = None
            if desc:
                # 换行必须走 _wrap_mixed：①尊重显式 \n（微博正文多段，PIL
                # d.text 遇 \n 自动画多行但外层 cy 按单行递进→行行重叠）；
                # ②emoji 段用 emoji 字体量宽（主字体量出的是豆腐框宽度）；
                # ③绘制走 _draw_mixed，正文里的 emoji 不再变方框
                lines = _wrap_mixed(desc, f_body, f_emoji_body, card_w - 40)
                body_asc, body_dsc = f_body.getmetrics()
                line_h_body = body_asc + body_dsc + 8
                desc_card_h = max(70, 60 + len(lines) * line_h_body)
                desc_card = Image.new("RGB", (card_w, desc_card_h), CARD_BG)
                ddraw = ImageDraw.Draw(desc_card)
                _draw_mixed(ddraw, 20, 14, "📝 正文", TEXT_PRIMARY, f_info, f_emoji)
                ddraw.line([(20, 44), (card_w - 20, 44)], fill=(60, 64, 80), width=1)
                cy = 56
                for line in lines:
                    _draw_mixed(ddraw, 20, cy, line, TEXT_SECONDARY, f_body, f_emoji_body)
                    cy += line_h_body

            # ============ 5. 拼接所有卡片 ============
            blocks = [("banner", banner), ("stats", stats_card)]
            if grid_card:
                blocks.append(("grid", grid_card))
            if desc_card:
                blocks.append(("desc", desc_card))

            total_h = banner_h + gap
            for _, blk in blocks[1:]:
                total_h += blk.size[1] + gap
            total_h += 36  # 底部水印空间

            final = Image.new("RGB", (W, total_h), CARD_BG2)
            fdraw = ImageDraw.Draw(final)
            final.paste(banner, (0, 0))
            y = banner_h + gap
            for name, blk in blocks[1:]:
                final.paste(blk, (card_x, y))
                y += blk.size[1] + gap

            # ============ 6. 底部水印 ============
            wm = "封茗囧菌数据汇总库 · 维护工具"
            fdraw.text(((W - f_small.getlength(wm)) // 2, y + 8), wm,
                       fill=TEXT_TERTIARY, font=f_small)

            # ============ 7. 保存 ============
            from toolbox.config_manager import get_export_dir
            out_dir = get_export_dir() / "导出的图片"
            out_dir.mkdir(parents=True, exist_ok=True)

            def _sf(s):
                return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", s or "").strip()[:30]
            fname = f"{item.get('date_part','')}_{_sf(item.get('author'))}_{_sf(item.get('title'))}_预览.png"
            out_path = out_dir / fname
            final.save(out_path)
            return str(out_path)

        def done(result, err):
            if err:
                self.app.toast(f"导出预览图失败: {str(err)[:60]}", "error")
            else:
                self.app.toast("✅ 预览图已导出", "success")
                try:
                    os.startfile(result)
                except Exception:
                    pass

        self.app.toast("正在生成预览图...", "info")
        run_bg(work, done)
