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
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from gui_qt import common
from gui_qt.common import _fmt, run_bg
from gui_qt.pages.base import BasePage

# 支持的资源扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".flv", ".avi", ".ts", ".m4v", ".wmv"}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus"}

BATCH = 20  # 懒加载：每批渲染数量

PLATFORM_CN = {"douyin": "抖音", "weibo": "微博", "bilibili": "B站"}


def _fmt_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"


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
    """规范化平台子目录名（中文/英文统一为英文 platform）。"""
    if not subdir:
        return ""
    s = subdir.lower()
    if s in ("bilibili", "b站", "哔哩哔哩"):
        return "bilibili"
    if s in ("weibo", "微博"):
        return "weibo"
    if s in ("douyin", "抖音"):
        return "douyin"
    return ""


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
    """可点击行：点击任意位置触发 clicked 回调（对应原版整行 <Button-1>）。"""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click:
            self._on_click()
        super().mousePressEvent(event)


class AssetPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._all_files = []            # 本地扫描结果全量
        self._shown = 0                 # 已渲染数量
        self._folder = ""
        self._disk_items = []           # 磁盘已下载数据全量
        self._collected_all_items = []  # 当前平台筛选后的列表
        self._collected_shown = 0       # 已渲染条数（懒加载游标）
        self._dl_state = {}             # 条目下载状态：'downloading' / 'done'
        self._search_var = ""           # 搜索关键词

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顺序：已获取资源在前，本地资源在后
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_collected_tab(), "已获取资源")
        self.tabs.addTab(self._build_local_tab(), "本地资源")

    # ==================== 本地资源 ====================

    def _build_local_tab(self) -> QWidget:
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        title = QLabel("📁 本地资源")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        v.addWidget(title)
        hint = QLabel("选择文件夹，扫描图片/视频/音频并快速预览（缩略图/摘要，懒加载不卡顿）")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)

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
        hint = QLabel("自动扫描 download/已获取数据/ 下所有历史导出文件（封面 + 摘要）")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("平台:"))
        self.plat_sel = QComboBox()
        self.plat_sel.addItems(["全部", "bilibili", "douyin", "weibo"])
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
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self._refresh_collected)
        bar.addWidget(refresh_btn)
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
        media_map = {}
        self._scan_media_files(download_root, media_map)

        # 2. 扫 json（根目录 + 英文子目录）
        json_paths = list(video_dir.glob("*.json"))
        for sub in ("bilibili", "douyin", "weibo"):
            json_paths.extend((video_dir / sub).glob("*.json"))
        json_paths = sorted(set(json_paths), key=lambda p: p.name, reverse=True)

        results = []
        for path in json_paths:
            if path.stem.endswith("_raw"):
                continue
            # 平台：英文子目录从目录名，根目录从文件名末段
            if path.parent.name in ("bilibili", "douyin", "weibo"):
                platform = path.parent.name
            else:
                parts = path.stem.rsplit("_", 1)
                platform = parts[1] if len(parts) == 2 and parts[1] in ("douyin", "weibo", "bilibili") else "unknown"
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
                    "description": (data.get("正文") or "")[:140],
                    "author": data.get("作者", "") or "",
                    "play_count": play,
                    "like_count": int(data.get("点赞数", 0) or 0),
                    "comment_count": int(data.get("评论数", 0) or 0),
                    "cover_url": data.get("封面URL", "") or "",
                    "publish_time": data.get("发布时间", "") or "",
                    "video_url": data.get("链接", "") or "",
                    "fetched_at": data.get("获取时间", "") or "",
                    "date_part": date_part,
                    "file_path": str(path),
                    "media_files": [],
                }
                # 匹配媒体（date + 标题双向子串 + 子目录匹配）
                title_norm = _sanitize_title_key(title).strip("_")
                json_subdir = _norm_subdir(path.parent.name)

                def _match(m_t):
                    m_t = m_t.strip("_") if m_t else ""
                    if not m_t or not title_norm:
                        return False
                    if len(m_t) < 4:  # 太短的标题片段不匹配（避免误配）
                        return False
                    return m_t in title_norm or title_norm[:40] in m_t

                # 第1轮：同日期 + 同子目录（最精确）
                for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                    if m_date != date_part or m_sub != json_subdir:
                        continue
                    if _match(m_title):
                        entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                        break
                # 第2轮：同日期 + 跨子目录
                if not entry["media_files"]:
                    for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                        if m_date != date_part or m_sub == json_subdir:
                            continue
                        if _match(m_title):
                            entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                            break
                # 第3轮：无日期的旧格式媒体（根目录平铺旧 mp4/jpg），只按标题兜底
                if not entry["media_files"]:
                    for (m_date, m_title, m_sub), mfs in list(media_map.items()):
                        if m_date:  # 只处理 date 为空的旧媒体
                            continue
                        if _match(m_title):
                            entry["media_files"] = media_map.pop((m_date, m_title, m_sub))
                            break
                # 预期图片数（从 raw json 提取 pics，用于"下载不完整"筛选）
                try:
                    entry["expected_imgs"] = len(self._extract_pic_urls(entry))
                except Exception:
                    entry["expected_imgs"] = 0
                results.append(entry)
            except Exception:
                continue

        # 3. 剩余未匹配媒体（无对应 json）→ 独立条目
        for (date_part, tk, subdir), mfs in media_map.items():
            if not mfs:
                continue
            imgs = [m for m in mfs if m["type"] == "image"]
            vids = [m for m in mfs if m["type"] == "video"]
            results.append({
                "platform": self._media_platform(mfs),
                "title": tk or "(媒体)",
                "description": "",
                "author": (mfs[0] or {}).get("author", "") if mfs else "",
                "play_count": 0, "like_count": 0, "comment_count": 0,
                "cover_url": imgs[0]["path"] if imgs else (vids[0]["path"] if vids else ""),
                "publish_time": "", "video_url": "",
                "fetched_at": "",
                "date_part": date_part,
                "file_path": (mfs[0] or {}).get("path", "") if mfs else "",
                "media_files": mfs,
                "expected_imgs": len(imgs),  # 独立媒体条目本身完整
            })
        results.sort(key=lambda r: (r.get("date_part") or ""), reverse=True)
        return results

    def _scan_media_files(self, root, media_map):
        """递归扫描 download 下实际媒体文件，按 (date, title_key, subdir) 分组进 media_map。"""
        skip_dirs = {"账号追踪数据", "弹幕文件", "字幕文件", "歌曲文件", "音乐信息", "danmaku", "stats",
                     "预览图", "导出的图片"}
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in self._MEDIA_EXTS:
                continue
            if any(s in p.parts for s in skip_dirs):
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
        return date_part, author, "_".join(rest)

    @staticmethod
    def _media_platform(mfs):
        """从媒体文件路径推断平台。"""
        for m in mfs:
            p = m["path"].lower()
            if "微博" in p or "weibo" in p:
                return "weibo"
            if "抖音" in p or "douyin" in p:
                return "douyin"
            if "b站" in p or "bilibili" in p:
                return "bilibili"
        return "unknown"

    def _refresh_collected(self):
        """重新扫描 + 平台/状态/关键词筛选 + 懒加载分批渲染（点击 row 弹详情窗）。"""
        self._disk_items = self._load_disk_items()
        plat = self.plat_sel.currentText()
        items = [it for it in self._disk_items if plat == "全部" or it["platform"] == plat]
        # 状态筛选
        status = self.status_sel.currentText()
        if status == "未下载":
            items = [it for it in items if not it.get("media_files")]
        elif status == "已下载":
            items = [it for it in items if it.get("media_files")]
        elif status == "下载不完整":
            items = [it for it in items
                     if it.get("expected_imgs", 0) > sum(1 for m in it.get("media_files", []) if m["type"] == "image")]
        # 关键词过滤（标题/作者/视频ID/正文，大小写不敏感）
        kw = (self._search_var or "").strip().lower()
        if kw:
            items = [it for it in items
                     if kw in (it.get("title") or "").lower()
                     or kw in (it.get("author") or "").lower()
                     or kw in (it.get("video_id") or "").lower()
                     or kw in (it.get("description") or "").lower()]
        # 时间排序（正序/倒序；空日期始终排最后）
        desc = self.sort_sel.currentText() == "时间倒序"
        placeholder = "00000000" if desc else "99999999"

        def _sort_key(it):
            d = it.get("date_part") or ""
            return d if d else placeholder
        items = sorted(items, key=_sort_key, reverse=desc)
        self._collected_all_items = items
        self._collected_shown = 0
        self._render_collected_batch()

    def _render_collected_batch(self):
        """懒加载分批渲染已获取资源（每批 BATCH=20 条，加载更多按钮续）。"""
        lay = self.collected_layout
        self._clear_layout(lay)
        lay.addStretch(1)
        items = self._collected_all_items
        if not items:
            tip = ("（download/已获取数据/ 目录为空）" if not self._disk_items
                   else f"（该平台无数据，共 {len(self._disk_items)} 条其他平台数据）")
            lbl = QLabel(tip)
            lbl.setStyleSheet("color: gray;")
            lbl.setContentsMargins(8, 10, 8, 10)
            lay.takeAt(lay.count() - 1)
            lay.addWidget(lbl)
            lay.addStretch(1)
            return
        lay.takeAt(lay.count() - 1)
        end = min(self._collected_shown + BATCH, len(items))
        for i in range(self._collected_shown, end):
            self._render_collected_row(items[i], i + 1)
        self._collected_shown = end
        if self._collected_shown < len(items):
            more = QPushButton(f"加载更多（{len(items) - self._collected_shown} 条）")
            more.setStyleSheet(
                "background: transparent; border: 1px solid #c8cdd6; color: #6b7280; padding: 4px;")
            more.clicked.connect(self._render_collected_batch)
            lay.addWidget(more)
        lay.addStretch(1)

    def _render_collected_row(self, it, index):
        """渲染一行（点击弹详情窗，双击缩略图打开原图）。"""
        row = _RowWidget(lambda it=it: self._show_detail(it))
        row.setStyleSheet(
            "_RowWidget { background: #ffffff; border-radius: 6px; }"
            "QLabel { background: transparent; }")
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        # 缩略图：优先本地媒体图，其次网络封面（放大到 96×64，原 72×50 太小看不清）
        local_img = next((m["path"] for m in it.get("media_files", []) if m["type"] == "image"), None)
        if local_img:
            lbl = self._load_local_thumb(local_img, 96, 64)
            h.addWidget(lbl)
        elif it["cover_url"]:
            lbl = self._load_net_thumb(it["cover_url"], 96, 64)
            h.addWidget(lbl)
        else:
            lbl = QLabel("🎬")
            lbl.setFixedSize(96, 64)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background: #eef1f5; border-radius: 4px; font-size: 28px;")
            h.addWidget(lbl)
        # 文本（中间 expand）
        plat_cn = PLATFORM_CN.get(it["platform"], it["platform"])
        # 显示层截断 + 自动换行：长标题/长文件名会把行的最小宽度撑爆，
        # 导致必须横向滚动才能看到右侧的下载/删除按钮（完整内容在 tooltip）
        full_title = it["title"] or "-"
        title = full_title[:60] + ("…" if len(full_title) > 60 else "")
        fname = os.path.basename(it["file_path"])
        fname = fname[:44] + ("…" if len(fname) > 44 else "")
        date_str = it["date_part"] if it["date_part"] else ""
        imgs = sum(1 for m in it.get("media_files", []) if m["type"] == "image")
        vids = sum(1 for m in it.get("media_files", []) if m["type"] == "video")
        media_str = ""
        if imgs:
            media_str += f" 🖼{imgs}"
        if vids:
            media_str += f" 🎬{vids}"
        text = (f"[{index}] [{plat_cn}] {title}{media_str}\n"
                f"@{it['author'] or '-'}  播放{_fmt(it['play_count'])}  赞{_fmt(it['like_count'])}\n"
                f"📅 {date_str}  📄 {fname}")
        txt = QLabel(text)
        txt.setStyleSheet("font-size: 11px;")
        txt.setWordWrap(True)
        txt.setToolTip(f"{full_title}\n\n📄 {os.path.basename(it['file_path'])}")
        h.addWidget(txt, 1)
        # 右侧按钮：下载（有封面无媒体时）/ 删除（放大到 42×32，原 32×28 不好点）
        if it.get("cover_url") and not it.get("media_files"):
            dl_btn = QPushButton("📥")
            dl_btn.setFixedSize(42, 32)
            dl_btn.setToolTip("下载封面")
            dl_btn.setStyleSheet(
                "background: transparent; border: 1px solid #c8cdd6; color: white; font-size: 15px;")
            dl_btn.clicked.connect(lambda _=False, e=it: self._download_for_item(e))
            h.addWidget(dl_btn)
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(42, 32)
        del_btn.setToolTip("删除该条目")
        del_btn.setStyleSheet(
            "background: transparent; border: 1px solid #c8cdd6; color: white; font-size: 15px;")
        del_btn.clicked.connect(lambda _=False, e=it: self._delete_item(e))
        h.addWidget(del_btn)
        self.collected_layout.addWidget(row)

    def _dl_key(self, it):
        """条目的下载状态键（唯一标识一个资产）。"""
        return it.get("file_path") or it.get("video_id") or it.get("title", "")

    def _quality_to_qn(self, label):
        """全局清晰度 → B站 qn 编号（最高画质返回 None，B站自动选最高逐级降）。"""
        return {"8K": 127, "杜比视界": 126, "HDR": 125, "4K": 120,
                "1080P60": 116, "1080P": 80, "720P": 64, "480P": 32}.get(label)

    def _download_for_item(self, it):
        """按平台下载（防重复点击：下载中/已完成分别提示）。"""
        key = self._dl_key(it)
        state = self._dl_state.get(key)
        if state == "downloading":
            self.app.toast("文件下载中，请稍后再试", "warning")
            return
        if state == "done":
            self.app.toast("文件已下载完毕，请勿重复点击", "info")
            return
        self._dl_state[key] = "downloading"
        plat = it.get("platform", "")
        if plat == "bilibili":
            self._download_bilibili_item(it)
        elif plat == "douyin":
            self._download_video_item(it)
        else:
            self._download_images_for_item(it)

    def _download_bilibili_item(self, it):
        """B站分发：图文动态→图片；视频动态(AV)/BV号→视频。"""
        vid = it.get("video_id", "")
        dtype = self._bili_dynamic_type(it)

        # 图文动态：下载图片（不能套视频下载模式）
        if dtype == "draw":
            self._download_images_for_item(it)
            return
        # 视频动态：从 archive.bvid 取真实 BV 号
        if dtype == "av":
            bvid = self._bili_av_bvid(it)
            if bvid:
                vid = bvid
        # 仍是 dyn_ 且拿不到 BV 号 → 退化为图片（封面兜底）
        if not vid or vid.startswith("dyn_"):
            self._download_images_for_item(it)
            return

        def work():
            from bilibili_download import download_bilibili_by_bvid
            from auth import get_cookie
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("video_data"))
            return download_bilibili_by_bvid(
                vid, quality=self._quality_to_qn(common.CURRENT_QUALITY),
                cookie=get_cookie("bilibili") or "",
                out_dir=out_dir, title=it.get("title", ""),
                author=it.get("author", ""), publish_time=it.get("publish_time", ""),
            )

        def done(result, err):
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                self._dl_state[self._dl_key(it)] = "done"
                self.app.toast("✅ 已下载 B站视频", "success")
            QTimer.singleShot(100, self._refresh_collected)

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

    def _download_video_item(self, it):
        """抖音：直链有时效性（过期 403），下载前用 video_id 重新解析拿新直链。"""
        vurl = it.get("video_url", "")
        vid = it.get("video_id", "")
        if not vurl and not vid:
            self.app.toast("此条目无视频链接", "warning")
            return

        def work():
            from platforms.base import VideoInfo, now_str
            from toolbox.config_manager import get_export_subdir
            from media_downloader import download_media
            vurl2 = vurl
            note_images = None
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

            base_dir = str(get_export_subdir("video_data"))
            info = VideoInfo(
                platform=it["platform"], video_id=vid,
                title=it.get("title") or it.get("date_part") or "未知",
                author=it.get("author", ""),  # 关键：让下载落到 {平台}/{作者}/ 子目录
                video_url=vurl2, cover_url=it.get("cover_url", ""),
                publish_time=it.get("publish_time", ""),  # 关键：让 date_str 匹配 json date_part
                raw_url="", fetched_at=now_str(),
            )
            # 图文 note：下图片
            if note_images:
                info.images = note_images
                return download_media([info], base_dir=base_dir,
                                      download_images=True, download_live=False, download_videos=False)
            if not vurl2:
                raise ValueError("无可用视频链接")
            return download_media([info], base_dir=base_dir,
                                  download_images=False, download_live=False, download_videos=True)

        def done(result, err):
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                n_img = (result or {}).get("images", 0)
                n_vid = (result or {}).get("videos", 0)
                f = (result or {}).get("failed", 0)
                if n_img:
                    self._dl_state[self._dl_key(it)] = "done"
                    self.app.toast(f"✅ 已下载图片 {n_img} 张", "success")
                elif n_vid:
                    self._dl_state[self._dl_key(it)] = "done"
                    self.app.toast(f"✅ 已下载视频 {n_vid} 个", "success")
                else:
                    self._dl_state[self._dl_key(it)] = None  # 失败可重试
                    self.app.toast(f"❌ 视频下载失败（{f} 个），直链可能已过期，可重试", "error")
            QTimer.singleShot(100, self._refresh_collected)

        self.app.toast("开始下载抖音视频...", "info")
        run_bg(work, done)

    def _delete_item(self, it):
        """删除当前资产（json + 关联 md/xlsx/raw + 媒体文件）。"""
        files = [it.get("file_path", "")]
        stem = os.path.splitext(it.get("file_path", ""))[0]
        # 关联的 md / xlsx / raw
        for ext in (".md", ".xlsx", "_raw.json"):
            cand = stem + ext
            if os.path.exists(cand):
                files.append(cand)
        # 媒体文件
        for m in it.get("media_files", []):
            files.append(m["path"])
        files = [f for f in files if f and os.path.exists(f)]
        if not files:
            self.app.toast("无文件可删除", "info")
            return
        if QMessageBox.question(
                self, "确认删除",
                f"将删除 {len(files)} 个文件：\n\n" + "\n".join(os.path.basename(f) for f in files[:10]),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        deleted = 0
        for f in files:
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
        self.app.toast(f"已删除 {deleted} 个文件", "success")
        # 删除后清空下载状态，允许重新下载
        self._dl_state.pop(self._dl_key(it), None)
        QTimer.singleShot(100, self._refresh_collected)

    def _extract_pic_urls(self, it) -> list:
        """从 raw json 提取该条目的所有图片 URL（微博 pics/pic_ids + B站动态 opus.pics/draw.items）。"""
        urls = []
        fp = it.get("file_path", "")
        if not fp:
            return urls
        stem = os.path.splitext(fp)[0]
        raw_candidates = [stem + "_raw.json", stem.replace(".json", "_raw.json")]
        raw_path = next((c for c in raw_candidates if os.path.exists(c)), "")
        if not raw_path:
            return urls
        try:
            data = json.load(open(raw_path, encoding="utf-8"))
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                return urls
            # 方式1：微博 pics 列表（dict 含 large/original/url 或纯字符串）
            for pic in data.get("pics", []) or []:
                if isinstance(pic, dict):
                    u = (pic.get("original", {}).get("url")
                         or pic.get("largest", {}).get("url")
                         or pic.get("large", {}).get("url")
                         or pic.get("url") or "")
                elif isinstance(pic, str):
                    u = pic
                else:
                    u = ""
                if u and u not in urls:
                    urls.append(u)
            # 方式2：微博 pic_ids（图片 ID 列表，构造 sinaimg 大图 URL）
            if not urls:
                for pid in data.get("pic_ids", []) or []:
                    if pid:
                        urls.append(f"https://wx1.sinaimg.cn/large/{pid}.jpg")
            # 方式3：B站图文动态 opus.pics（i0.hdslb.com 原图）
            if not urls:
                try:
                    major = ((data.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
                    opus_pics = (major.get("opus", {}) or {}).get("pics", []) or []
                    for p in opus_pics:
                        u = (p or {}).get("url", "") if isinstance(p, dict) else ""
                        if u and u not in urls:
                            urls.append(u)
                except Exception:
                    pass
            # 方式4：B站新版图文动态 draw.items（.src）
            if not urls:
                try:
                    major = ((data.get("modules", {}) or {}).get("module_dynamic", {}) or {}).get("major", {}) or {}
                    draw_items = (major.get("draw", {}) or {}).get("items", []) or []
                    for p in draw_items:
                        u = (p or {}).get("src", "") if isinstance(p, dict) else ""
                        if u and u not in urls:
                            urls.append(u)
                except Exception:
                    pass
        except Exception:
            pass
        return urls

    def _download_images_for_item(self, it):
        """下载单条条目所有图片（优先 raw json 的 pics/pic_ids，否则封面图）。"""
        # 优先从 raw json 提取所有原图，封面图也加入（B站动态封面 i0.hdslb.com 等）
        pics = self._extract_pic_urls(it)
        cover = it.get("cover_url", "")
        if cover and cover not in pics:
            pics.insert(0, cover)  # 封面优先下载（列表里预览的就是它）
        if not pics:
            self.app.toast("此条目无可下载图片（raw json 无 pics 且无封面）", "warning")
            return

        def work():
            from platforms.base import VideoInfo, now_str
            from toolbox.config_manager import get_export_subdir
            from media_downloader import download_media
            info = VideoInfo(
                platform=it["platform"],
                title=it.get("title") or it.get("date_part") or "未知",
                author=it.get("author", ""),  # 关键：让下载落到 {平台}/{作者}/ 子目录
                cover_url=cover,
                publish_time=it.get("publish_time", ""),  # 关键：让 date_str 匹配 json date_part
                raw_url="",
                fetched_at=now_str(),
            )
            info.images = pics
            base_dir = str(get_export_subdir("video_data"))
            return download_media([info], base_dir=base_dir,
                                  download_images=True, download_live=False, download_videos=False)

        def done(result, err):
            if err:
                self._dl_state[self._dl_key(it)] = None  # 失败可重试
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
            else:
                n = (result or {}).get("images", 0)
                self._dl_state[self._dl_key(it)] = "done"
                self.app.toast(f"✅ 已下载图片 {n} 张", "success")
            # 刷新列表（重新扫描磁盘，新下载的图会显示本地缩略图）
            QTimer.singleShot(100, self._refresh_collected)

        self.app.toast("开始下载图片...", "info")
        run_bg(work, done)

    def _load_net_thumb(self, url, w, h):
        """后台加载网络封面缩略图。"""
        lbl = QLabel("🖼")
        lbl.setFixedSize(w, h)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #eef1f5; border-radius: 4px;")

        def load():
            try:
                import requests
                resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                return resp.content
            except Exception:
                return None

        def done(data, err):
            if data:
                qimg = QImage.fromData(data)
                lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                    w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        run_bg(load, done)
        return lbl

    def on_show(self):
        self._refresh_collected()

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

        plat_cn = PLATFORM_CN.get(item["platform"], item["platform"])
        title_lbl = QLabel(item.get("title", "") or "(无标题)")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)
        info_lines = [
            ("平台", plat_cn),
            ("作者", "@" + (item.get("author") or "-")),
            ("播放/阅览", _fmt(item.get("play_count", 0))),
            ("点赞", _fmt(item.get("like_count", 0))),
            ("评论", _fmt(item.get("comment_count", 0))),
            ("发布时间", item.get("publish_time") or "-"),
            ("采集时间", item.get("fetched_at") or "-"),
            ("源文件", item.get("file_path") or "-"),
            ("视频链接", item.get("video_url") or "-"),
            ("原始URL", item.get("raw_url") or "-"),
        ]
        for k, val in info_lines:
            row_f = QHBoxLayout()
            kl = QLabel(f"{k}:")
            kl.setFixedWidth(84)
            kl.setStyleSheet("color: #6b7280; font-size: 12px;")
            row_f.addWidget(kl)
            wl = QLabel(str(val))
            wl.setWordWrap(True)
            wl.setStyleSheet("font-size: 12px;")
            row_f.addWidget(wl, 1)
            v.addLayout(row_f)
        desc = item.get("description") or ""
        if desc:
            dh = QLabel("正文")
            dh.setStyleSheet("color: #6b7280; font-weight: bold;")
            v.addWidget(dh)
            dl = QLabel(desc)
            dl.setWordWrap(True)
            dl.setStyleSheet("font-size: 12px;")
            v.addWidget(dl)
        # 媒体文件（实际下载的图片/视频，双击图片看大图、视频用系统播放器）
        mfs = item.get("media_files") or []
        if mfs:
            imgs = [m for m in mfs if m["type"] == "image"]
            vids = [m for m in mfs if m["type"] == "video"]
            mh = QLabel(f"🖼 媒体文件（{len(imgs)} 图 / {len(vids)} 视频）")
            mh.setStyleSheet("color: #6b7280; font-weight: bold;")
            v.addWidget(mh)
            if imgs:
                grid_holder = QWidget()
                grid = QGridLayout(grid_holder)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setSpacing(4)
                cols = 5
                for i, im in enumerate(imgs):
                    cell = self._load_local_thumb_cell(im["path"], 128)
                    grid.addWidget(cell, i // cols, i % cols)
                v.addWidget(grid_holder)
            for vd in vids:
                rowv = QPushButton(f"🎬 {vd['name']}")
                rowv.setStyleSheet(
                    "background: transparent; border: none; color: #1976d2; "
                    "text-align: left; font-size: 12px; padding: 1px;")
                rowv.clicked.connect(lambda _=False, p=vd["path"]: self._open_media(p))
                v.addWidget(rowv)
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

    def _load_local_thumb_cell(self, path, size):
        """图片网格单格（双击用系统默认看图器打开原图）。"""
        lbl = QLabel("🖼")
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #eef1f5; border-radius: 4px;")
        lbl.setToolTip(path)

        def load():
            try:
                from PIL import Image
                img = Image.open(path).convert("RGB")
                img.thumbnail((size, size), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                return None

        def done(data, err):
            if data:
                qimg = QImage.fromData(data)
                lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
                    size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        run_bg(load, done)
        return lbl

    def _open_media(self, path):
        """用系统默认程序打开媒体文件（图片/视频）。"""
        try:
            os.startfile(path)
        except Exception as e:
            self.app.toast(f"打开失败: {e}", "error")

    # ==================== 导出预览图 ====================

    def _export_preview_image(self, item):
        """导出单个条目的预览拼贴图（紫色渐变 banner + 平台数据卡片 + 图片网格 + 正文卡片）。"""
        def work():
            from PIL import Image, ImageDraw, ImageFont, ImageOps

            _EMOJI_RE = re.compile(
                r'[\U0001F300-\U0001F9FF'
                r'\U00002600-\U000027BF'
                r'\U0001F000-\U0001F02F'
                r'\U0001F100-\U0001F1FF'
                r'\U0001F600-\U0001F64F'
                r'\U0001F680-\U0001F6FF'
                r'\U0001F900-\U0001F9FF'
                r'\u2600-\u26FF'
                r'\u2700-\u27BF]'
            )

            def _draw_mixed(d, x, y, text, color, font_main, font_emoji):
                """绘制含 emoji 的字符串：emoji 段用 emoji 字体，普通段用主字体，返回结束 x。"""
                if not text:
                    return x
                cx = x
                seg = ""
                cur_emoji = bool(_EMOJI_RE.match(text[0]))
                for ch in text:
                    ie = bool(_EMOJI_RE.match(ch))
                    if ie == cur_emoji:
                        seg += ch
                    else:
                        font = font_emoji if cur_emoji else font_main
                        d.text((cx, y), seg, fill=color, font=font)
                        bb = d.textbbox((0, 0), seg, font=font)
                        cx += bb[2] - bb[0]
                        seg = ch
                        cur_emoji = ie
                if seg:
                    font = font_emoji if cur_emoji else font_main
                    d.text((cx, y), seg, fill=color, font=font)
                    bb = d.textbbox((0, 0), seg, font=font)
                    cx += bb[2] - bb[0]
                return cx

            # 中文字体（多字重）
            font_paths = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                          "C:/Windows/Fonts/simsun.ttc"]
            font_file = next((f for f in font_paths if os.path.exists(f)), "")
            # emoji 字体（Windows 自带）
            emoji_paths = ["C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/segui.ttf",
                           "C:/Windows/Fonts/AppleColorEmoji.ttf"]
            emoji_file = next((f for f in emoji_paths if os.path.exists(f)), "")
            try:
                f_big = ImageFont.truetype(font_file, 36)
                f_title = ImageFont.truetype(font_file, 26)
                f_info = ImageFont.truetype(font_file, 20)
                f_body = ImageFont.truetype(font_file, 18)
                f_small = ImageFont.truetype(font_file, 14)
                if emoji_file:
                    f_emoji = ImageFont.truetype(emoji_file, 20)
                    f_emoji_sm = ImageFont.truetype(emoji_file, 14)
                else:
                    f_emoji = f_emoji_sm = f_info
            except Exception:
                f_big = f_title = f_info = f_body = f_small = ImageFont.load_default()
                f_emoji = f_emoji_sm = f_info

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

            plat_cn = {"douyin": "抖音", "weibo": "微博", "bilibili": "B站"}.get(
                item.get("platform"), item.get("platform") or "未知")
            plat_en = {"douyin": "Douyin", "weibo": "Weibo", "bilibili": "bilibili"}.get(
                item.get("platform"), item.get("platform") or "")
            plat_emoji = {"douyin": "🎵", "weibo": "💬", "bilibili": "📺"}.get(
                item.get("platform"), "📄")
            plat_accent = {"douyin": ACCENT_RED, "weibo": ACCENT_RED, "bilibili": ACCENT_BLUE}.get(
                item.get("platform"), ACCENT)

            title = (item.get("title") or "(无标题)")[:60]
            author = item.get("author") or "-"
            play_count = _fmt(item.get("play_count", 0))
            like_count = _fmt(item.get("like_count", 0))
            comment_count = _fmt(item.get("comment_count", 0))
            pub = item.get("publish_time") or "-"
            fetched = item.get("fetched_at") or "-"
            desc = (item.get("description") or "")[:300]

            # 标题自适应：按宽度换行（最多2行），放不下则逐级缩小字号
            _tdraw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

            def _wrap_text(text, font, max_w):
                lines, cur = [], ""
                for ch in text:
                    bb = _tdraw.textbbox((0, 0), cur + ch, font=font)
                    if bb[2] - bb[0] > max_w and cur:
                        lines.append(cur)
                        cur = ch
                    else:
                        cur += ch
                if cur:
                    lines.append(cur)
                return lines

            f_head = f_big
            title_lines = [title]
            for size in (36, 32, 28, 24, 20):
                try:
                    f_try = ImageFont.truetype(font_file, size) if font_file else ImageFont.load_default()
                except Exception:
                    f_try = ImageFont.load_default()
                lines = _wrap_text(title, f_try, W - 2 * pad)
                if len(lines) <= 2:
                    f_head, title_lines = f_try, lines
                    break
                f_head, title_lines = f_try, lines
            if len(title_lines) > 2:
                title_lines = title_lines[:2]
                title_lines[-1] = title_lines[-1][:-1] + "…"

            # 副标题超宽截断
            subtitle = f"@{author}  ·  {fetched}"
            while _tdraw.textbbox((0, 0), subtitle, font=f_info)[2] > W - 2 * pad and len(subtitle) > 4:
                subtitle = subtitle[:-2] + "…"

            # banner 高度按标题行数自适应
            line_h = f_head.size + 10
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
                tl_bbox = bdraw.textbbox((0, 0), tl, font=f_head)
                tl_w = tl_bbox[2] - tl_bbox[0]
                bdraw.text(((W - tl_w) // 2, title_y), tl, fill=TEXT_PRIMARY, font=f_head)
                title_y += line_h
            sub_y = title_y + 2
            sub_bbox = bdraw.textbbox((0, 0), subtitle, font=f_info)
            sub_w = sub_bbox[2] - sub_bbox[0]
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

            _draw_mixed(sdraw, 20, 14, f"{plat_emoji}  {plat_cn}", TEXT_PRIMARY, f_title, f_emoji)
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
                import shutil
                import subprocess
                from io import BytesIO
                args_base = ["-y", "-ss", "1", "-i", vpath, "-frames:v", "1",
                             "-vf", f"scale={cell}:{cell}:force_original_aspect_ratio=increase,crop={cell}:{cell}",
                             "-f", "image2", "-c:v", "mjpeg", "-"]
                candidates = []
                if _ffmpeg_ok[0]:
                    candidates.append(_ffmpeg_ok[0])
                else:
                    which = shutil.which("ffmpeg")
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
                lines = []
                cur = ""
                for ch in desc:
                    test = cur + ch
                    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), test, font=f_body)
                    if (bbox[2] - bbox[0]) > (card_w - 40) and cur:
                        lines.append(cur)
                        cur = ch
                    else:
                        cur = test
                if cur:
                    lines.append(cur)
                desc_card_h = max(70, 50 + len(lines) * (f_body.size + 10))
                desc_card = Image.new("RGB", (card_w, desc_card_h), CARD_BG)
                ddraw = ImageDraw.Draw(desc_card)
                _draw_mixed(ddraw, 20, 14, "📝 正文", TEXT_PRIMARY, f_info, f_emoji)
                ddraw.line([(20, 44), (card_w - 20, 44)], fill=(60, 64, 80), width=1)
                cy = 56
                for line in lines:
                    ddraw.text((20, cy), line, fill=TEXT_SECONDARY, font=f_body)
                    cy += f_body.size + 10

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
            wm = "FMJJ Tracker · 数据每小时自动更新"
            wm_bbox = fdraw.textbbox((0, 0), wm, font=f_small)
            fdraw.text(((W - (wm_bbox[2] - wm_bbox[0])) // 2, y + 8), wm,
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
