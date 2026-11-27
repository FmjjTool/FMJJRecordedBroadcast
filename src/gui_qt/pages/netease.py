"""音乐搜索下载页（PySide6 版，原「音乐信息获取」+「音乐下载」合并为单页）。

一条主线：平台选择 → 输入 → 结果表 → 勾选后下载 / 导出。
- 平台选择 + 模式按钮 + 动态输入区（搜索模式带歌手预填）
- 「五平台对比」：一次并行搜五个平台，结果进同一张表（平台标签 + 平台筛选）
- 网易云走 netease 模块（登录态 + 音质选择）
- 其余平台走 music/scrapers（游客直采：搜索/单曲解析/歌单解析/专辑解析/歌手主页）
- 五个主平台能力对齐：搜索 / 单曲 / 歌单 / 专辑 / 歌手主页（名称或主页链接）
- 详情滚动区：歌曲列表（封面+时长）、单曲详情卡（封面+歌词）、歌单信息卡、
  专辑详情卡、歌手主页（信息卡 + 热门歌曲 + 专辑，均可勾选下载）
- 下载：网易云 MusicDownloader；其余 music/downloader（默认最高可用，失败自动换源）
- 导出：勾选结果导出 Excel/JSON/Markdown（原「音乐信息获取」能力）
"""

import html
import os
import re
import time
import weakref

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.cover_loader import load_cover
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import ElidedLabel, make_row_clickable
from toolbox.config_manager import load_convert_config, save_convert_config

QUALITY_NAMES = {
    "standard": "标准",
    "exhigh": "极高",
    "lossless": "无损",
    "hires": "Hi-Res",
    "sky": "沉浸环绕声",
    "jyeffect": "高清环绕声",
    "jymaster": "超清母带",
}

# (platform_id, 显示名, 模式列表) —— netease 走独立模块，其余走 music scrapers
PLATFORMS = [
    ("all", "五平台对比", ["search"]),
    ("netease", "网易云音乐", ["search", "song", "playlist", "album", "artist"]),
    ("qq", "QQ音乐", ["search", "song", "playlist", "album", "artist"]),
    ("kugou", "酷狗音乐", ["search", "song", "playlist", "album", "artist"]),
    ("kuwo", "酷我音乐", ["search", "song", "playlist", "album", "artist"]),
    # 5sing 没有专辑，不提供「专辑解析」
    ("5sing", "5sing音乐", ["search", "song", "playlist", "artist"]),
    ("kugou_lite", "酷狗概念版", ["search", "song", "playlist", "album", "artist"]),
]

MERGE_PLATFORM = "all"

# 五平台对比的参与平台（酷狗概念版需登录，单独入口，不进对比）
MERGE_SOURCES = [
    ("kugou", "酷狗"), ("qq", "QQ"), ("netease", "网易云"),
    ("kuwo", "酷我"), ("5sing", "5sing"),
]

MODES = [
    ("search", "歌曲搜索"),
    ("song", "单曲解析"),
    ("playlist", "歌单解析"),
    ("album", "专辑解析"),
    ("artist", "歌手主页"),
]

PLACEHOLDERS = {
    "netease": {
        "song": "歌曲链接或 ID（例：https://music.163.com/song?id=xxx）",
        "playlist": "歌单链接或 ID（例：https://music.163.com/playlist?id=xxx）",
        "album": "专辑链接 / ID / 专辑名",
        "artist": "歌手名或主页链接（例：https://music.163.com/artist?id=xxx）",
    },
    "qq": {
        "song": "歌曲链接或 songmid（例：https://y.qq.com/n/ryqq/songDetail/xxx）",
        "playlist": "歌单链接或 dissid（例：https://y.qq.com/n/ryqq/playlist/7256912512）",
        "album": "专辑链接 / 专辑 mid / 专辑名",
        "artist": "歌手名或主页链接（例：https://y.qq.com/n/ryqq/singer/xxx）",
    },
    "kugou": {
        "song": "歌曲链接或 hash（例：https://www.kugou.com/mixsong/xxx.html）",
        "playlist": "歌单链接或 specialid（例：https://www.kugou.com/yy/special/single/9580287.html）",
        "album": "专辑链接 / 专辑 ID / 专辑名",
        "artist": "歌手名或主页链接（例：https://www.kugou.com/singer/188254.html 或 singer/info/ 哈希链接）",
    },
    "kuwo": {
        "song": "歌曲链接或 rid（例：https://www.kuwo.cn/play_detail/228908）",
        "playlist": "歌单链接或 pid（例：https://www.kuwo.cn/playlist_detail/1082685104）",
        "album": "专辑链接 / 专辑 ID / 专辑名",
        "artist": "歌手名或主页链接（例：https://www.kuwo.cn/singer_detail/260226）",
    },
    "5sing": {
        "song": "歌曲页链接（例：https://5sing.kugou.com/yc/4219209.html）",
        "playlist": "歌单链接（例：https://5sing.kugou.com/65188239/dj/61d1a130683d8134f5a28493.html）",
        "artist": "歌手名或音乐空间链接（例：https://5sing.kugou.com/65188239/default.html）",
    },
    "kugou_lite": {
        "song": "歌曲 hash（32 位十六进制，可从酷狗概念版搜索结果复制）",
        "playlist": "歌单链接或 specialid（与酷狗同库，例：https://www.kugou.com/yy/special/single/9580287.html）",
        "album": "专辑链接 / 专辑 ID / 专辑名（与酷狗同库）",
        "artist": "歌手名或主页链接（与酷狗同库，例：https://www.kugou.com/singer/188254.html）",
    },
}


def _parse_netease_id(text):
    """从输入解析网易云 ID（支持纯数字或链接里的 id=xxx）。"""
    s = (text or "").strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"id[=/](\d+)", s)
    if m:
        return int(m.group(1))
    return None


def _fmt_duration(ms):
    if not ms:
        return "?:??"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _song_to_row(s):
    """SongInfo(pydc) → 页面统一行 dict（duration 统一为 ms）。"""
    return {
        "id": s.song_id,
        "name": s.song_name or s.title,
        "artists": s.singer or "",
        "album": s.album_name or "",
        "picUrl": s.cover_url or "",
        "duration": (s.duration or 0) * 1000,
        "_platform": s.platform,
        "_mode": getattr(s, "mode", "") or "",
        "_song": s,
    }


# 五平台栏里的勾选框：默认样式在白色栏内太浅（选中态只剩一个勾、看不到框），
# 这里未选态加深边框，选中态用绿底 + 自绘白勾，保证两种状态都能一眼看清。
# 整页只应用一次（按 #mergeCheck 选中），不再逐行 setStyleSheet。
MERGE_CHECK_QSS = (
    "QCheckBox#mergeCheck { spacing: 0px; }"
    "QCheckBox#mergeCheck::indicator { width: 16px; height: 16px; }"
    "QCheckBox#mergeCheck::indicator:unchecked {"
    " background: #ffffff; border: 2px solid #77818f; border-radius: 4px; }"
    "QCheckBox#mergeCheck::indicator:unchecked:hover { border-color: #2e7d32; }"
    "QCheckBox#mergeCheck::indicator:checked {"
    " background: #2e7d32; border: 2px solid #2e7d32; border-radius: 4px;"
    " image: url(\"%s\"); }"
    "QCheckBox#mergeCheck::indicator:checked:hover { background: #276b2b; border-color: #276b2b; }"
)

_MERGE_CHECK_PATH = None


def _merge_check_image() -> str:
    """生成白勾小图（QSS 里给选中态用），失败时返回空串退回纯绿底。"""
    global _MERGE_CHECK_PATH
    if _MERGE_CHECK_PATH is not None:
        return _MERGE_CHECK_PATH
    import tempfile
    from PySide6.QtCore import QPointF, Qt as _Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

    path = os.path.join(tempfile.gettempdir(), "fmjj_music_check.png")
    if os.path.exists(path):
        _MERGE_CHECK_PATH = path
        return path
    pixmap = QPixmap(32, 32)
    pixmap.fill(_Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#ffffff"), 4.0, _Qt.SolidLine, _Qt.RoundCap, _Qt.RoundJoin))
    painter.drawPolyline(QPolygonF([QPointF(8, 17), QPointF(14, 23), QPointF(24, 10)]))
    painter.end()
    try:
        pixmap.save(path)
    except Exception:
        _MERGE_CHECK_PATH = ""
        return ""
    _MERGE_CHECK_PATH = path
    return path


def _merge_check_qss() -> str:
    path = _merge_check_image()
    if not path:
        return MERGE_CHECK_QSS % ""
    return MERGE_CHECK_QSS % path.replace(os.sep, "/")


# 结果行样式：整页按 objectName 应用一次。旧写法每行、每个控件各自
# setStyleSheet，几百行时光 QSS 解析/重抛光就要一百多毫秒。
ROW_QSS = (
    "QFrame#songRow, QFrame#albumRow, QFrame#mergeRow {"
    " background: transparent; border: none; }"
    "QLabel#rowText { background: transparent; border: none; }"
    "QLabel#rowDur { color: #888; font-size: 11px;"
    " background: transparent; border: none; }"
    "QLabel#rowPic { background: #ffffff; border-radius: 4px; color: #555; }"
)


_TITLE_PUNCT = re.compile(
    r"[\s\-_·・.,，。()（）\[\]【】{}<>《》\"'’‘“”：:;；!！?？/、|~～]+")


def _norm_title(text) -> str:
    return _TITLE_PUNCT.sub("", str(text or "")).lower()


def _match_score(title, keyword) -> int:
    """匹配度：0 完全相同 / 1 前缀相同 / 2 包含 / 3 不相关。"""
    name, key = _norm_title(title), _norm_title(keyword)
    if not key:
        return 0
    if name == key:
        return 0
    if name.startswith(key):
        return 1
    if key in name:
        return 2
    return 3


def _row_cover_url(row) -> str:
    """结果行封面：优先 SongInfo.cover_url，其次各平台返回里的备用字段。"""
    song = row.get("_song")
    url = row.get("picUrl") or getattr(song, "cover_url", "") or ""
    extra = dict(getattr(song, "extra", {}) or {})
    if not url:
        trans = extra.get("trans_param") or {}
        union = trans.get("union_cover") or extra.get("union_cover") or ""
        if union:
            url = str(union).replace("{size}", "400")
    if not url:
        key = row.get("_platform_key") or row.get("_platform") or ""
        album_mid = getattr(song, "album_id", "") or ""
        if key == "qq" and album_mid:
            url = f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg"
    if not url:
        for field in ("picUrl", "imgurl", "img_url", "songimg", "userimg", "cover"):
            value = extra.get(field)
            if isinstance(value, str) and value.startswith("http"):
                url = value
                break
    return _thumb_cover_url(url)


def _thumb_cover_url(url: str) -> str:
    """五平台栏里封面只显示 36px，按平台缩到小图，减少加载失败与流量。"""
    if not url:
        return ""
    if "y.gtimg.cn" in url:
        return (url.replace("T002R800x800M000", "T002R300x300M000")
                   .replace("800x800", "300x300"))
    if "music.126.net" in url:
        return url + ("&" if "?" in url else "?") + "param=300y300"
    if "kuwo.cn" in url:
        return url.replace("/700/", "/300/")
    return url


class _AlbumDescBox(QPlainTextEdit):
    """专辑介绍只读框：内容超出时可滚动，滚到顶/底后把滚轮交还外层页面。"""

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        at_top = bar.value() <= bar.minimum()
        at_bottom = bar.value() >= bar.maximum()
        if (delta > 0 and at_top) or (delta < 0 and at_bottom):
            event.ignore()
            return
        super().wheelEvent(event)


class NeteasePage(BasePage):
    SESSION_FIELDS = ("platform", "mode", "_result_snapshot")

    def restore_session_data(self):
        platform, mode = self.platform, self.mode
        index = next((i for i, (pid, _n, _m) in enumerate(PLATFORMS) if pid == platform), 0)
        self.platform_menu.blockSignals(True)
        self.platform_menu.setCurrentIndex(index)
        self.platform_menu.blockSignals(False)
        self._apply_platform(platform)
        self._switch_mode(mode)
        snapshot = getattr(self, "_result_snapshot", None)
        if snapshot:
            self._render_result(snapshot)

    def __init__(self, app):
        super().__init__(app)
        self.platform = "netease"
        self.mode = "search"
        self.results = []           # 当前模式的结果（统一为 [{id, name, artists, album, duration, _kind}]）
        self.check_boxes = []
        self.single_song_id = None  # 单曲解析时的当前歌曲 ID
        self.single_song = None     # 单曲解析时的 SongInfo（四平台）
        self._cover_epoch = 0       # 结果换代计数：清空结果后旧封面任务作废
        self._cover_pending = []    # [(label 弱引用, url, size, epoch)] 等滚到可见再拉
        self._cover_retry = 0       # 布局未稳定时的重试次数（防止隐藏页面空转）

        # 行样式整页应用一次（按 objectName 选中），不再逐行 setStyleSheet
        self.setStyleSheet(ROW_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._build_func())

    # ===================== 公共：登录态 =====================
    def _cookies(self):
        from auth import get_netease_cookie
        from netease import parse_cookie_string
        try:
            content = get_netease_cookie()
            return parse_cookie_string(content) if content else {}
        except Exception:
            return {}

    def _scraper(self):
        """当前平台的抓取器实例（网易云也走 scraper，仅用于专辑展开）。"""
        from music import ALL_PLATFORMS
        scraper_cls = next(cls for pid, _, cls in ALL_PLATFORMS if pid == self.platform)
        return scraper_cls()

    # ===================== 功能页 =====================
    def _build_func(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        # 顶部：平台选择 + 模式按钮
        top = QHBoxLayout()
        self.platform_menu = QComboBox()
        for _, label, _ in PLATFORMS:
            self.platform_menu.addItem(label)
        self.platform_menu.currentIndexChanged.connect(self._switch_platform)
        top.addWidget(self.platform_menu)
        self.mode_btns = {}
        self.mode_row = QHBoxLayout()
        top.addLayout(self.mode_row)
        top.addStretch(1)
        v.addLayout(top)

        # 输入区（动态，随模式变化）
        self.input_row = QHBoxLayout()
        v.addLayout(self.input_row)

        # 详情区：空白页 + 结果页切换，空态不显示灰框也不留布局空隙
        self.detail_stack = QStackedWidget()
        self.empty_page = QWidget()
        self.detail_stack.addWidget(self.empty_page)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setStyleSheet(
            "QScrollArea { background: #ffffff; border: 1px solid #d0d6df;"
            " border-radius: 6px; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(8, 8, 8, 8)
        self.detail_layout.setSpacing(2)
        self.detail_layout.setAlignment(Qt.AlignTop)
        self.detail_scroll.setWidget(self.detail_container)
        # 结果多时封面按「滚到可见」再拉，避免一次性打出几百个请求
        self.detail_scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self._load_visible_covers())
        self.detail_stack.addWidget(self.detail_scroll)
        # 五平台对比独立页：五个平台各一栏框架，各自滚动，不走上面的大滚动区
        self.merged_page = QWidget()
        self.merged_page.setStyleSheet(_merge_check_qss())
        self.merged_outer = QVBoxLayout(self.merged_page)
        self.merged_outer.setContentsMargins(0, 0, 0, 0)
        self.merged_outer.setSpacing(4)
        self._merged_groups = []
        self.detail_stack.addWidget(self.merged_page)
        v.addWidget(self.detail_stack, 1)

        # 底部：操作栏
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.more_btn = QPushButton("更多")
        self.more_btn.setProperty("menuButton", True)
        more_menu = QMenu(self.more_btn)
        more_menu.setToolTipsVisible(True)
        self.more_btn.setMenu(more_menu)
        self.selection_action_buttons = []
        for label, mode in (("全选", "all"), ("反选", "invert"),
                            ("□ 全不选", "none")):
            action = more_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=mode: self._sel(value))
            self.selection_action_buttons.append(action)
        self.fallback_check = QCheckBox("自动换源")
        self.fallback_check.setChecked(self._load_fallback())
        self.fallback_check.toggled.connect(self._save_fallback)
        self.fallback_check.setToolTip(
            "下载失败或仅试听时，自动到其他平台找同名曲：酷我→酷狗→酷狗概念版→网易云\n"
            "（酷狗概念版需扫码登录；网易云需已登录）")
        bottom.addWidget(self.fallback_check)
        self.cover_check = QCheckBox("下载封面")
        self.cover_check.setChecked(self._load_cover_download())
        self.cover_check.toggled.connect(self._save_cover_download)
        bottom.addWidget(self.cover_check)
        bottom.addWidget(QLabel("音质:"))
        self.quality_menu = QComboBox()
        bottom.addWidget(self.quality_menu)
        # 导出位置双选项：默认位置（统一导出目录/歌曲文件） / 固定位置（自选文件夹）
        bottom.addWidget(QLabel("导出:"))
        self.export_loc_combo = QComboBox()
        self.export_loc_combo.addItem("默认位置")
        self.export_loc_combo.addItem("固定位置")
        try:
            if load_convert_config().get("music_download_use_custom"):
                self.export_loc_combo.setCurrentIndex(1)
        except Exception:
            pass
        self.export_loc_combo.currentIndexChanged.connect(self._on_export_loc_changed)
        bottom.addWidget(self.export_loc_combo)
        pick_dir_btn = QPushButton("")
        pick_dir_btn.setIcon(line_icon("folder", theme.color("text"), 16))
        pick_dir_btn.setFixedSize(30, 26)
        pick_dir_btn.setToolTip("选择固定导出位置（文件夹）")
        pick_dir_btn.clicked.connect(self._pick_music_dir)
        bottom.addWidget(pick_dir_btn)
        bottom.addWidget(self.more_btn)
        self.download_btn = QPushButton("下载选中")
        self.download_btn.setProperty("variant", "primary")
        self.download_btn.clicked.connect(self._download_selected)
        bottom.addWidget(self.download_btn)
        more_menu.addSeparator()
        self.export_btn = more_menu.addAction("导出选中")
        self.export_btn.setToolTip("把勾选结果导出为 Excel/JSON/Markdown（原「音乐信息获取」能力）")
        self.export_btn.triggered.connect(self._export_selected)
        v.addLayout(bottom)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: palette(placeholder-text);")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.status_label)

        # 默认平台/模式：沿用原行为（网易云 + 歌曲搜索）
        self.platform_menu.blockSignals(True)
        self.platform_menu.setCurrentIndex(1)
        self.platform_menu.blockSignals(False)
        self._apply_platform("netease")
        return w

    # ===================== 自动换源勾选持久化 =====================
    @staticmethod
    def _load_fallback() -> bool:
        try:
            return bool(load_convert_config().get("music_fallback_enabled", True))
        except Exception:
            return True

    def _save_fallback(self, checked):
        try:
            cfg = load_convert_config()
            cfg["music_fallback_enabled"] = bool(checked)
            save_convert_config(cfg)
        except Exception:
            pass

    @staticmethod
    def _load_cover_download() -> bool:
        try:
            return bool(load_convert_config().get("music_download_cover", True))
        except Exception:
            return True

    def _save_cover_download(self, checked):
        try:
            cfg = load_convert_config()
            cfg["music_download_cover"] = bool(checked)
            save_convert_config(cfg)
        except Exception:
            pass

    def _set_single_download_selection(self, row, checkbox):
        """让单曲详情复用统一的「下载选中」操作。"""
        checkbox.setChecked(True)
        checkbox.hide()
        self.results = [row]
        self.check_boxes = [checkbox]
        # 单曲只有一个结果，直接自动选中并隐藏批量选择操作。
        for action in self.selection_action_buttons:
            action.setVisible(False)

    # ===================== 导出位置（默认/固定 双选项） =====================
    def _on_export_loc_changed(self, index):
        try:
            cfg = load_convert_config()
            cfg["music_download_use_custom"] = index == 1
            save_convert_config(cfg)
        except Exception:
            pass
        self._update_export_dir_tip()

    def _pick_music_dir(self):
        from PySide6.QtWidgets import QFileDialog
        import os
        try:
            cur = load_convert_config().get("music_download_custom_dir", "") or os.path.expanduser("~")
        except Exception:
            cur = ""
        picked = QFileDialog.getExistingDirectory(self, "选择歌曲固定导出位置", cur)
        if not picked:
            return
        try:
            cfg = load_convert_config()
            cfg["music_download_custom_dir"] = picked
            save_convert_config(cfg)
        except Exception:
            pass
        self.export_loc_combo.setCurrentIndex(1)
        self._update_export_dir_tip()
        self.app.toast(f"固定导出位置: {picked}", "success")

    def _update_export_dir_tip(self):
        try:
            from toolbox.config_manager import get_music_download_dir
            self.export_loc_combo.setToolTip(f"当前下载目录: {get_music_download_dir()}")
        except Exception:
            pass

    # ===================== 平台切换 =====================
    def _switch_platform(self, index):
        self._apply_platform(PLATFORMS[index][0])

    def _apply_platform(self, platform):
        self.platform = platform
        # 模式按钮按平台重建
        while self.mode_row.count():
            item = self.mode_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.mode_btns = {}
        allowed = next(modes for pid, _, modes in PLATFORMS if pid == platform)
        for mode_id, label in MODES:
            if mode_id not in allowed:
                continue
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, m=mode_id: self._switch_mode(m))
            self.mode_row.addWidget(btn)
            self.mode_btns[mode_id] = btn
        # 音质选项
        self.quality_menu.blockSignals(True)
        self.quality_menu.clear()
        if platform == MERGE_PLATFORM:
            # 对比模式混合多平台，网易云优先超清母带，其余取最高可用。
            self.quality_menu.addItem("各平台最高可用", "highest")
            self.quality_menu.setEnabled(False)
        elif platform == "netease":
            self.quality_menu.addItems(list(QUALITY_NAMES.values()))
            self.quality_menu.setCurrentText(QUALITY_NAMES["jymaster"])
            self.quality_menu.setEnabled(True)
        else:
            from music.downloader import quality_options
            for value, name in quality_options(platform):
                self.quality_menu.addItem(name, value)
            self.quality_menu.setEnabled(self.quality_menu.count() > 1)
        self.quality_menu.blockSignals(False)
        # 默认模式（album 仅网易云）
        self._switch_mode("search")
        if platform == MERGE_PLATFORM:
            # 切到五平台对比就先把五个空框架摆出来，不等搜索
            self._result_kind = "multi"
            self._render_merged([])
            self.detail_stack.setCurrentWidget(self.merged_page)

    # ===================== 模式切换 =====================
    def _switch_mode(self, mode):
        self.mode = mode
        for m, btn in self.mode_btns.items():
            btn.setChecked(m == mode)
            if m == mode:
                btn.setStyleSheet("background: #1a5c2a; color: white;")
            else:
                btn.setStyleSheet("")
        # 清空输入区重建
        while self.input_row.count():
            item = self.input_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.singer_entry = None
        if mode == "search":
            self.singer_entry = QLineEdit()
            self.singer_entry.setPlaceholderText("歌手（默认封茗囧菌）")
            self.singer_entry.setFixedWidth(150)
            if self.platform in ("netease", MERGE_PLATFORM):
                self.singer_entry.setText("封茗囧菌")
            self.input_row.addWidget(self.singer_entry)
        ph = PLACEHOLDERS.get(self.platform, {}).get(mode, "歌曲关键词")
        if self.platform == MERGE_PLATFORM:
            ph = "歌曲名 / 专辑名（五个平台一次搜）"
        self.kw_entry = QLineEdit()
        self.kw_entry.setPlaceholderText(ph)
        self.kw_entry.returnPressed.connect(self._search)
        self.input_row.addWidget(self.kw_entry, 1)
        self.search_btn = QPushButton("搜索五平台" if self.platform == MERGE_PLATFORM else "获取")
        self.search_btn.clicked.connect(self._search)
        self.input_row.addWidget(self.search_btn)
        if self.platform == MERGE_PLATFORM:
            self.complete_btn = QPushButton("从完整歌曲选择")
            self.complete_btn.setToolTip("从腾讯文档「完整歌曲」本地快照选歌，优先用歌曲平台链接取详情")
            self.complete_btn.clicked.connect(self._pick_complete_song)
            self.input_row.addWidget(self.complete_btn)
        else:
            self.complete_btn = None
        # 清空详情
        self._clear_detail()
        self.results = []
        self.check_boxes = []
        self._merged_rows = []
        self.single_song_id = None
        self.single_song = None
        self._show_empty()

    def _clear_detail(self):
        # 旧结果的封面任务全部作废，避免清空后还在后台拉几百张图
        self._cover_epoch += 1
        self._cover_pending = []
        self._cover_retry = 0
        for action in self.selection_action_buttons:
            action.setVisible(True)
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        self._clear_merged()

    def _show_empty(self):
        self._clear_detail()
        self.detail_stack.setCurrentWidget(self.empty_page)

    # ===================== 全选/反选 =====================
    def _sel(self, mode):
        for cb in self.check_boxes:
            if mode == "all":
                cb.setChecked(True)
            elif mode == "none":
                cb.setChecked(False)
            else:
                cb.setChecked(not cb.isChecked())

    # ===================== 封面图（共享加载器 + 可见才拉） =====================
    def _cover_check(self, epoch):
        return lambda: epoch != self._cover_epoch

    def _load_image_async(self, url, size, parent_layout, lazy=False):
        """占位 QLabel + 异步封面。

        lazy=True：先登记，滚到可见区域才真正发请求（结果列表动辄几百行）；
        lazy=False：单曲/歌单信息卡、五平台栏等少量图片立即加载。
        """
        lbl = QLabel("")
        lbl.setObjectName("rowPic")
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignCenter)
        parent_layout.addWidget(lbl)
        if not url:
            return lbl

        epoch = self._cover_epoch
        stale = self._cover_check(epoch)
        if lazy:
            self._cover_pending.append((weakref.ref(lbl), url, int(size), epoch))
        else:
            load_cover(url, size, lbl, stale)
        return lbl

    def _load_visible_covers(self):
        """只加载当前视口（上下各留 400px）内的懒加载封面。"""
        pending = self._cover_pending
        if not pending:
            return
        try:
            self.detail_container.layout().activate()
            viewport = self.detail_scroll.viewport().rect()
            need = self.detail_layout.sizeHint().height()
            settled = self.detail_container.height() + 4 >= need
        except (RuntimeError, AttributeError):
            return
        if not settled:
            # 行还没完成布局时（插入瞬间）所有行都挤在顶部，按几何判定会把
            # 全部行误判成可见、一次拉满封面；等布局稳定后再判定（最多重试 20 次，
            # 页面隐藏时不再空转，显示时由 on_show 重置重试）。
            if self._cover_retry < 20:
                self._cover_retry += 1
                QTimer.singleShot(50, self._load_visible_covers)
            return
        self._cover_retry = 0
        keep = []
        for ref, url, size, epoch in pending:
            lbl = ref()
            if lbl is None or epoch != self._cover_epoch:
                continue
            try:
                if not isValid(lbl):
                    continue
                # 布局还没落位时行高为 0，先留着，等下一次判定，
                # 否则所有行会挤在顶部被误判成「全部可见」而一次性全拉。
                if lbl.height() <= 0:
                    keep.append((ref, url, size, epoch))
                    continue
                pos = lbl.mapTo(self.detail_scroll.viewport(), QPoint(0, 0))
            except RuntimeError:
                continue
            rect = QRect(pos, lbl.size())
            if viewport.adjusted(0, -400, 0, 400).intersects(rect):
                load_cover(url, size, lbl, self._cover_check(epoch))
            else:
                keep.append((ref, url, size, epoch))
        self._cover_pending = keep

    def on_show(self):
        # 页面从隐藏变可见时，视口尺寸才有效，需要重新判定可见封面
        self._cover_retry = 0
        QTimer.singleShot(0, self._load_visible_covers)
        QTimer.singleShot(80, self._load_visible_covers)

    # ===================== 搜索/解析入口 =====================
    def _search(self):
        kw = self.kw_entry.text().strip()
        if not kw:
            self.app.toast("请输入关键词或链接", "warning")
            return
        singer = self.singer_entry.text().strip() if getattr(self, "singer_entry", None) else ""

        self.search_btn.setEnabled(False)
        self.status_label.setText("获取中...")
        self._clear_detail()
        self.results = []
        self.check_boxes = []
        self.single_song_id = None
        self.single_song = None

        mode = self.mode
        platform = self.platform

        def work():
            if platform == MERGE_PLATFORM:
                return self._work_all(kw, singer)
            if platform == "netease":
                return self._work_netease(mode, kw, singer)
            return self._work_scraper(platform, mode, kw)

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                if self.platform == MERGE_PLATFORM:
                    self._render_merged([])
                    self.detail_stack.setCurrentWidget(self.merged_page)
                else:
                    self._show_empty()
                self.status_label.setText(f"{err}")
                return
            self._result_snapshot = result
            self._render_result(result)
            if result and result[0] == "multi" and not result[1]:
                self.status_label.setText("五平台都没有搜索结果，换个歌名或检查网络/登录态")

        run_bg(work, done)

    def _render_result(self, result):
        kind, data = result
        self._result_kind = kind
        if kind == "multi":
            self.detail_stack.setCurrentWidget(self.merged_page)
        else:
            self.detail_stack.setCurrentWidget(self.detail_scroll)
        render = {"songs": self._render_songs, "song": self._render_single_song,
                  "playlist": self._render_playlist, "album": self._render_album,
                  "albums": self._render_albums, "multi": self._render_merged,
                  "artist": self._render_artist}.get(kind)
        if render:
            render(data or [] if kind in ("songs", "albums", "multi") else data)
        self._fit_detail_top()

    def _fit_detail_top(self):
        """结果按顶部排，末尾补弹簧。

        不给 detail_layout 留弹簧时，最后一行（歌曲/专辑行）会被拉伸到
        填满整个滚动区：单曲专辑只剩一行时中间出现大片空白。
        """
        layout = self.detail_layout
        if layout.count() and layout.itemAt(layout.count() - 1).spacerItem() is not None:
            return
        layout.addStretch(1)

    # ===================== 五平台对比：搜索与渲染 =====================
    def _work_all(self, kw, singer):
        """五平台并行搜索：结果合并成一张表（不按平台分列）。"""
        from concurrent.futures import ThreadPoolExecutor
        from music import ALL_PLATFORMS

        keyword = f"{singer} {kw}".strip() if singer else kw
        targets = [(key, name, cls) for key, name, cls in ALL_PLATFORMS
                   if key in dict(MERGE_SOURCES)]

        def one(spec):
            key, name, cls = spec
            try:
                scraper = cls()
                if key == "5sing":
                    songs = []
                    for part in scraper.search_all_modes(keyword, limit=8).values():
                        songs.extend(part)
                else:
                    songs = scraper.search(keyword, limit=8)
            except Exception:
                songs = []
            rows = []
            for s in songs or []:
                row = _song_to_row(s)
                row["_platform_key"] = key
                row["_platform_name"] = dict(MERGE_SOURCES).get(key, name)
                rows.append(row)
            return rows

        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
            groups = list(ex.map(one, targets))
        rows = [row for group in groups for row in group]
        if not rows:
            return ("multi", [])
        # 按与搜索词的匹配度排序（稳定排序，同分保留平台自己的相关度顺序）
        rows.sort(key=lambda r: _match_score(r.get("name"), kw))
        return ("multi", rows)

    def _clear_merged(self):
        """清空五平台对比页（筛选栏 + 五个平台栏）。"""
        while self.merged_outer.count():
            item = self.merged_outer.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
                continue
            layout = item.layout()
            if layout is None:
                continue
            while layout.count():
                sub = layout.takeAt(0)
                if sub.widget():
                    sub.widget().hide()
                    sub.widget().deleteLater()
                elif sub.layout():
                    while sub.layout().count():
                        deep = sub.layout().takeAt(0)
                        if deep.widget():
                            deep.widget().hide()
                            deep.widget().deleteLater()
        self._merged_groups = []

    def _render_merged(self, rows, empty_hint="输入歌曲名后点「搜索五平台」"):
        """五平台各一栏独立框架并排：尚未搜索时也先把五个空框架显示出来。"""
        self._clear_merged()
        self._result_kind = "multi"
        self.results = []
        self.check_boxes = []
        rows = list(rows or [])

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 2)
        bar.addWidget(QLabel("平台筛选:"))
        self.filter_menu = QComboBox()
        self.filter_menu.addItem("全部平台", "")
        for key, name in MERGE_SOURCES:
            self.filter_menu.addItem(name, key)
        self.filter_menu.currentIndexChanged.connect(self._apply_merged_filter)
        bar.addWidget(self.filter_menu)
        if not rows:
            hint = QLabel(empty_hint)
            hint.setStyleSheet(
                "color: palette(placeholder-text); font-size: 11px; background: transparent;")
            bar.addSpacing(8)
            bar.addWidget(hint)
        bar.addStretch(1)
        self.merged_outer.addLayout(bar)

        columns = QHBoxLayout()
        columns.setSpacing(8)
        self.merged_outer.addLayout(columns, 1)
        for key, label in MERGE_SOURCES:
            group_rows = [r for r in rows if (r.get("_platform_key") or "") == key]
            frame = QFrame()
            frame.setObjectName("mergeGroup")
            frame.setStyleSheet(
                "QFrame#mergeGroup { background: #ffffff; border: 1px solid #d0d6df;"
                " border-radius: 6px; }")
            fv = QVBoxLayout(frame)
            fv.setContentsMargins(6, 6, 6, 6)
            fv.setSpacing(4)
            head = QLabel(f"{label} · {len(group_rows)}")
            head.setStyleSheet(
                "font-weight: bold; color: #333; background: transparent; border: none;")
            fv.addWidget(head)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
                "QScrollArea > QWidget > QWidget { background: transparent; }")
            host = QWidget()
            hv = QVBoxLayout(host)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(2)
            hv.setAlignment(Qt.AlignTop)
            if not group_rows:
                blank = QLabel("—")
                blank.setAlignment(Qt.AlignCenter)
                blank.setStyleSheet(
                    "color: palette(placeholder-text); background: transparent;"
                    " border: none; padding: 18px 0;")
                hv.addWidget(blank)
            for index, s in enumerate(group_rows):
                self.results.append(s)
                # 每栏第一条（匹配度最高）默认勾选，和原信息页一致
                hv.addWidget(self._merged_row(s, checked=(index == 0)))
            scroll.setWidget(host)
            fv.addWidget(scroll, 1)
            columns.addWidget(frame, 1)
            self._merged_groups.append((key, frame))

        if not rows:
            self.status_label.setText(empty_hint)
            return
        counts = []
        for key, name in MERGE_SOURCES:
            n = sum(1 for r in self.results if r.get("_platform_key") == key)
            if n:
                counts.append(f"{name} {n}")
        self.status_label.setText(
            f"共 {len(self.results)} 首（{' / '.join(counts)}），勾选后下载或导出")

    def _merged_row(self, s, checked=False):
        """五平台栏内的一行：勾选 + 封面 + 歌名/歌手 + 时长。"""
        row = QFrame()
        row.setObjectName("mergeRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(6)
        cb = QCheckBox()
        cb.setObjectName("mergeCheck")
        cb.setFixedSize(20, 20)
        cb.setChecked(bool(checked))
        self.check_boxes.append(cb)
        h.addWidget(cb, 0, Qt.AlignVCenter)
        self._load_image_async(_row_cover_url(s), 36, h)
        name = str(s.get("name") or "")
        if s.get("_mode"):
            name += f"[{s.get('_mode')}]"
        artists = str(s.get("artists") or "")
        info = QLabel(
            f"{name}<br><span style='color:#888;font-size:11px'>{artists}</span>")
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        info.setObjectName("rowText")
        info.setToolTip(f"{name}\n{artists}\n{s.get('album') or ''}".strip())
        h.addWidget(info, 1)
        dur = QLabel(_fmt_duration(s.get("duration", 0)))
        dur.setObjectName("rowDur")
        h.addWidget(dur, 0, Qt.AlignTop)
        make_row_clickable(row, cb)
        return row

    def _apply_merged_filter(self, _index=0):
        selected = self.filter_menu.currentData() if getattr(self, "filter_menu", None) else ""
        for key, frame in getattr(self, "_merged_groups", []):
            frame.setVisible(not selected or key == selected)

    # ===================== 完整歌曲 → 五平台（原音乐信息页入口） =====================
    def _pick_complete_song(self):
        from gui_qt.pages.music_complete_dialog import CompleteSongDialog
        from toolbox.music_complete_songs import load_complete_songs

        songs = load_complete_songs()
        if not songs:
            self.app.toast("没有找到完整歌曲本地快照，请先在腾讯文档页采集", "warning")
            return
        dialog = CompleteSongDialog(songs, self)
        if dialog.exec() != QDialog.Accepted:
            return
        picked = dialog.selected_song()
        if not picked:
            return
        self.singer_entry.setText(picked.get("singer") or "")
        self.kw_entry.setText(picked.get("title") or "")
        self._search_btn_busy(True)
        self.status_label.setText(f"正在按完整歌曲《{picked.get('title')}》匹配五平台...")

        def work():
            from toolbox.music_complete_songs import fetch_platform_songs
            found = fetch_platform_songs(picked)
            rows = []
            names = dict(MERGE_SOURCES)
            for key, items in found.items():
                if key not in names:
                    continue
                for s in items or []:
                    row = _song_to_row(s)
                    row["_platform_key"] = key
                    row["_platform_name"] = names[key]
                    rows.append(row)
            if not rows:
                raise ValueError("完整歌曲链接和歌名搜索都没有返回结果")
            rows.sort(key=lambda r: _match_score(r.get("name"), picked.get("title")))
            return ("multi", rows)

        def done(result, err):
            self._search_btn_busy(False)
            if err:
                self.status_label.setText(f"{err}")
                return
            self._result_snapshot = result
            self._render_result(result)
            if self.results:
                self.status_label.setText(
                    self.status_label.text() + "（完整歌曲：链接优先，缺失平台回退歌名搜索）")

        run_bg(work, done)

    def _search_btn_busy(self, busy):
        self.search_btn.setEnabled(not busy)
        if getattr(self, "complete_btn", None) is not None:
            self.complete_btn.setEnabled(not busy)

    # ===================== 导出选中（原音乐信息页能力） =====================
    def _export_selected(self):
        if getattr(self, "_result_kind", "") not in ("songs", "multi"):
            self.app.toast("当前结果不是歌曲列表，先把歌单/专辑解析成歌曲再导出", "warning")
            return
        selected = [self.results[i] for i, cb in enumerate(self.check_boxes)
                    if cb.isChecked() and i < len(self.results)]
        if not selected:
            self.app.toast("请先勾选要导出的歌曲", "warning")
            return
        keyword = " ".join(
            part for part in ((self.singer_entry.text().strip()
                               if getattr(self, "singer_entry", None) else ""),
                              self.kw_entry.text().strip())
            if part).strip()
        self.export_btn.setEnabled(False)
        self.status_label.setText(f"导出 {len(selected)} 首...")

        def work():
            from music import SearchResult, export_all_formats
            from music.models import SongInfo
            from toolbox.config_manager import get_export_subdir

            songs, platforms = [], []
            for item in selected:
                name = item.get("_platform_name") or ""
                if name and name not in platforms:
                    platforms.append(name)
                song = item.get("_song")
                if not isinstance(song, SongInfo):
                    song = SongInfo(
                        song_id=str(item.get("id", "")),
                        song_name=item.get("name", ""),
                        singer=item.get("artists", ""),
                        album_name=item.get("album", ""),
                        duration=(item.get("duration") or 0) // 1000,
                        cover_url=item.get("picUrl") or "",
                        platform=(item.get("_platform_key") or item.get("_platform")
                                  or self.platform))
                songs.append(song)
            result = SearchResult(
                keyword=keyword or "音乐导出",
                platforms_available=platforms,
                total_songs=len(songs),
                total_albums=0,
                songs=songs,
            )
            return export_all_formats(result, str(get_export_subdir("music_info")))

        def done(outputs, err):
            self.export_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"{err}")
                return
            out_dir = (outputs or {}).get("输出目录") or ""
            self.status_label.setText(
                f"已导出 {len(selected)} 首" + (f"：{out_dir}" if out_dir else ""))

        run_bg(work, done)


    def _work_netease(self, mode, kw, singer):
        from netease import (
            search_music, search_album, playlist_detail, album_detail,
            get_song_detail, get_lyric, get_pic_url,
        )
        cookies = self._cookies()
        if mode == "search":
            keyword = f"{singer} {kw}".strip() if singer else kw
            return ("songs", search_music(keyword, cookies, limit=50))
        if mode == "song":
            sid = _parse_netease_id(kw)
            if sid is None:
                raise ValueError("请输入正确的歌曲链接或 ID")
            info = get_song_detail(sid)
            # 补充歌词和封面
            lyric_data = get_lyric(sid, cookies)
            info["_lyric"] = lyric_data.get("lrc", "")
            al = info.get("al") or {}
            pic = al.get("picUrl")
            if not pic and al.get("pic"):
                pic = get_pic_url(al.get("pic"), 300)
            info["_picUrl"] = pic
            return ("song", info)
        if mode == "playlist":
            pid = _parse_netease_id(kw)
            if pid is None:
                raise ValueError("请输入正确的歌单链接或 ID")
            return ("playlist", playlist_detail(pid, cookies))
        if mode == "album":
            aid = _parse_netease_id(kw)
            if aid is not None:
                return ("album", album_detail(aid, cookies))
            keyword = f"{singer} {kw}".strip() if singer else kw
            return ("albums", search_album(keyword, cookies, limit=30))
        if mode == "artist":
            return self._work_netease_artist(kw, cookies)
        return ("songs", [])

    def _work_netease_artist(self, kw, cookies):
        """歌手主页：链接/ID 直接取详情，否则按名称搜索并取最匹配的一位。"""
        from netease import search_artist, artist_detail, artist_songs, artist_albums

        aid = _parse_netease_id(kw)
        candidates = []
        if aid is None:
            candidates = search_artist(kw, cookies, limit=10)
            if not candidates:
                raise ValueError("没有找到该歌手，换个名字或贴主页链接试试")
            candidates.sort(key=lambda c: _match_score(c.get("name"), kw))
            aid = candidates[0]["id"]
        info = artist_detail(int(aid), cookies)
        songs = artist_songs(int(aid))
        albums = artist_albums(int(aid), limit=50)
        payload = {
            "id": aid,
            "name": info.get("name", ""),
            "pic": info.get("picUrl", ""),
            "desc": info.get("briefDesc", ""),
            "song_count": info.get("musicSize") or len(songs),
            "album_count": info.get("albumSize") or len(albums),
            "url": f"https://music.163.com/#/artist?id={aid}",
            "songs": songs,
            "albums": albums,
        }
        return ("artist", {"candidates": candidates, "artist": payload})

    def _work_scraper(self, platform, mode, kw):
        from music import ALL_PLATFORMS
        scraper_cls = next(cls for pid, _, cls in ALL_PLATFORMS if pid == platform)
        scraper = scraper_cls()
        if mode == "search":
            songs = scraper.search_song(kw, 1, 30) or []
            return ("songs", [_song_to_row(s) for s in songs])
        if mode == "song":
            sid = scraper.extract_song_id(kw)
            if not sid:
                raise ValueError("请输入正确的歌曲链接或 ID")
            info = scraper.get_song_detail(sid)
            if info is None:
                raise ValueError("未找到该歌曲（可能已下架或链接失效）")
            return ("song", info)
        if mode == "playlist":
            pid = scraper.extract_playlist_id(kw)
            if not pid:
                raise ValueError("请输入正确的歌单链接或 ID")
            pl = scraper.get_playlist(pid)
            if pl is None:
                raise ValueError("未找到该歌单（可能已删除或链接失效）")
            return ("playlist", pl)
        if mode == "album":
            aid = scraper.extract_album_id(kw)
            if aid:
                album = scraper.get_album(aid)
                if album is None:
                    raise ValueError("未找到该专辑（可能已下架或链接失效）")
                return ("album", album)
            albums = scraper.search_album(kw, 30) or []
            if not albums:
                raise ValueError("没有找到匹配的专辑，试试专辑链接或更完整的专辑名")
            return ("albums", albums)
        if mode == "artist":
            aid = scraper.extract_artist_id(kw)
            if aid:
                payload = scraper.get_artist(aid)
                if payload is None:
                    raise ValueError("未找到该歌手（可能链接失效或需要登录）")
                return ("artist", {"candidates": [], "artist": payload})
            candidates = scraper.search_artist(kw, 10) or []
            if not candidates:
                raise ValueError("没有找到该歌手，换个名字或贴主页链接试试")
            candidates.sort(key=lambda c: _match_score(c.get("name"), kw))
            best = candidates[0]
            payload = scraper.get_artist(best["id"], best.get("name", ""))
            if payload is None:
                raise ValueError(f"没能打开歌手「{best.get('name')}」的主页")
            return ("artist", {"candidates": candidates, "artist": payload})
        return ("songs", [])

    # ===================== 渲染：歌曲列表（带封面） =====================
    def _render_songs(self, songs):
        self.results = songs
        if not songs:
            lbl = QLabel("（无结果）")
            lbl.setStyleSheet("color: gray; padding: 20px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.detail_layout.addWidget(lbl)
            self.status_label.setText("无结果")
            return
        self._render_song_rows(songs)
        self.status_label.setText(f"共 {len(songs)} 首，勾选后批量下载")

    def _render_song_rows(self, songs):
        """只画歌曲行（不重置 results / status），供歌手主页分节复用。"""
        for s in songs:
            row = QFrame()
            row.setObjectName("songRow")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 1, 0, 1)
            cb = QCheckBox()
            cb.setFixedSize(18, 18)
            self.check_boxes.append(cb)
            h.addWidget(cb)
            pic = s.get("picUrl") or (s.get("al") or {}).get("picUrl")
            self._load_image_async(pic, 44, h, lazy=True)
            text = f"{s.get('name', '')} - {s.get('artists', '')}"
            if s.get("album"):
                text += f"  ({s['album']})"
            info = ElidedLabel(text)
            info.setObjectName("rowText")
            h.addWidget(info, 1)
            dur = QLabel(_fmt_duration(s.get("duration", 0)))
            dur.setObjectName("rowDur")
            h.addWidget(dur)
            make_row_clickable(row, cb)
            self.detail_layout.addWidget(row)
        QTimer.singleShot(0, self._load_visible_covers)

    # ===================== 渲染：单曲解析（详情 + 歌词） =====================
    def _render_single_song(self, song):
        if not song:
            self._show_empty()
            return
        if self.platform == "netease":
            self._render_single_song_netease(song)
            return
        self.single_song = song
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        self._load_image_async(song.cover_url, 120, h)
        lines = [
            f"{song.song_name or song.title}",
            f"歌手: {song.singer or '未知'}",
        ]
        if song.album_name:
            lines.append(f"专辑: {song.album_name}")
        if song.duration:
            lines.append(f"时长: {song.duration // 60}:{song.duration % 60:02d}")
        if song.mode:
            lines.append(f"模式: {song.mode}")
        if song.language:
            lines.append(f"语种: {song.language}")
        if song.genre:
            lines.append(f"流派: {song.genre}")
        if song.release_date:
            lines.append(f"发行: {song.release_date}")
        if song.pay and song.pay.fee:
            lines.append("VIP 歌曲（下载可能受限）")
        lines.append(f"ID: {song.song_id}")
        txt = QLabel("\n".join(lines))
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        selected = QCheckBox(info_card)
        self._set_single_download_selection(_song_to_row(song), selected)
        self.detail_layout.addWidget(info_card)

        lyric = song.lyrics or ""
        if lyric:
            lyric_card = QFrame()
            lyric_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
            lv = QVBoxLayout(lyric_card)
            lv.setContentsMargins(10, 6, 10, 8)
            lv.setSpacing(2)
            title_lbl = QLabel("歌词")
            title_lbl.setStyleSheet("font-weight: bold; font-size: 12px; background: transparent; border: none;")
            title_lbl.setFixedHeight(18)
            lv.addWidget(title_lbl)
            lyric_text = QPlainTextEdit()
            lyric_text.setReadOnly(True)
            lyric_text.setMinimumHeight(160)
            lyric_text.setMaximumHeight(260)
            lyric_text.setStyleSheet("font-family: Consolas; font-size: 12px;")
            # 仅显示前 100 行避免卡顿
            lines = lyric.splitlines()
            display = "\n".join(lines[:100]) + ("\n…(更多歌词省略)" if len(lines) > 100 else "")
            lyric_text.setPlainText(display)
            lv.addWidget(lyric_text, 1)
            self.detail_layout.addWidget(lyric_card)
        else:
            lbl = QLabel("（暂无歌词）")
            lbl.setStyleSheet("color: #888; padding: 8px;")
            self.detail_layout.addWidget(lbl)
        self.status_label.setText("单曲已就绪，点击下方“下载选中”即可开始下载")

    def _render_single_song_netease(self, song):
        self.single_song_id = song.get("id")
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = song.get("_picUrl") or (song.get("al") or {}).get("picUrl")
        self._load_image_async(pic, 120, h)
        info_text = (
            f"{song.get('name', '')}\n"
            f"歌手: {song.get('artists', '')}\n"
            f"专辑: {song.get('album', '')}\n"
            f"时长: {_fmt_duration(song.get('duration', 0))}\n"
            f"ID: {song.get('id', '')}"
        )
        txt = QLabel(info_text)
        h.addWidget(txt, 1)
        selected = QCheckBox(info_card)
        self._set_single_download_selection(song, selected)
        self.detail_layout.addWidget(info_card)

        # 歌词
        lyric = song.get("_lyric", "")
        if lyric:
            lyric_card = QFrame()
            lyric_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
            lv = QVBoxLayout(lyric_card)
            lv.setContentsMargins(10, 6, 10, 8)
            lv.setSpacing(2)
            # 标题行：固定小高度，避免占大块
            title_lbl = QLabel("歌词")
            title_lbl.setStyleSheet("font-weight: bold; font-size: 12px; background: transparent; border: none;")
            title_lbl.setFixedHeight(18)
            lv.addWidget(title_lbl)
            lyric_text = QPlainTextEdit()
            lyric_text.setReadOnly(True)
            lyric_text.setMinimumHeight(160)
            lyric_text.setMaximumHeight(260)
            lyric_text.setStyleSheet("font-family: Consolas; font-size: 12px;")
            # 仅显示前 100 行避免卡顿
            lines = lyric.splitlines()
            display = "\n".join(lines[:100]) + ("\n…(更多歌词省略)" if len(lines) > 100 else "")
            lyric_text.setPlainText(display)
            lv.addWidget(lyric_text, 1)
            self.detail_layout.addWidget(lyric_card)
        else:
            lbl = QLabel("（暂无歌词）")
            lbl.setStyleSheet("color: #888; padding: 8px;")
            self.detail_layout.addWidget(lbl)
        self.status_label.setText("单曲已就绪，点击下方“下载选中”即可开始下载")

    # ===================== 渲染：歌单解析 =====================
    def _render_playlist(self, pl):
        if not pl:
            self._show_empty()
            return
        if self.platform == "netease":
            self._render_playlist_netease(pl)
            return
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        self._load_image_async(pl.cover_url, 96, h)
        lines = [
            f"{pl.name}",
            f"创建者: {pl.creator or '未知'}",
            f"歌曲数: {pl.song_count}",
        ]
        if pl.play_count:
            lines.append(f"▶ 播放: {pl.play_count:,}")
        if pl.tags:
            lines.append(f"标签: {pl.tags}")
        if pl.description:
            lines.append(f"简介: {pl.description[:90]}")
        txt = QLabel("\n".join(lines))
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)
        self.results = [_song_to_row(s) for s in pl.songs]
        self._render_songs(self.results)
        self.status_label.setText(f"歌单「{pl.name}」共 {len(self.results)} 首（{pl.platform_name}）")

    def _render_playlist_netease(self, pl):
        # get_playlist_detail 返回扁平结构：{id, name, coverImgUrl, creator(str), trackCount, description, tracks}
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = pl.get("coverImgUrl")
        self._load_image_async(pic, 96, h)
        txt = QLabel(
            f"{pl.get('name', '')}\n"
            f"创建者: {pl.get('creator') or ''}\n"
            f"歌曲数: {pl.get('trackCount', 0)}\n"
            f"简介: {(pl.get('description') or '无')[:90]}"
        )
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)
        # 歌曲列表（tracks 已是 {id, name, artists, album, picUrl, duration} 结构）
        tracks = [t for t in (pl.get("tracks") or [])
                  if isinstance(t, dict) and t.get("id")]
        self.results = [
            {"id": t["id"], "name": t.get("name", ""),
             "artists": t.get("artists", ""),
             "album": t.get("album", ""),
             "picUrl": t.get("picUrl", ""),
             "duration": t.get("duration", 0)}
            for t in tracks
        ]
        self._render_songs(self.results)
        self.status_label.setText(f"歌单「{pl.get('name', '')}」共 {len(self.results)} 首")

    # ===================== 渲染：专辑详情卡（各平台共用） =====================
    # 卡片高度固定：封面 96 + 上下的 10 边距 + 介绍框 64 + 间距 6
    ALBUM_CARD_HEIGHT = 186
    ALBUM_DESC_HEIGHT = 64

    @staticmethod
    def _clean_album_desc(desc):
        """专辑介绍统一成纯文本（部分平台返回 <br>/实体）。"""
        text = str(desc or "").strip()
        if "<" in text and ">" in text:
            text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
            text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
            text = re.sub(r"<[^>]+>", "", text)
            text = html.unescape(text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _album_info_card(self, name, singer, count, publish, cover, desc):
        """专辑信息卡：封面 + 基本信息 + 固定高度的可滚动介绍框。

        介绍框高度写死（滚动看全文），卡片高度也随之固定，
        不同专辑 / 不同平台之间卡片大小一致。
        """
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        card.setFixedHeight(self.ALBUM_CARD_HEIGHT)
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._load_image_async(cover, 96, row)
        lines = [f"{name}", f"歌手: {singer or '未知'}", f"歌曲数: {count}"]
        if publish:
            lines.append(f"发行: {publish}")
        info = QLabel("\n".join(lines))
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        info.setFixedHeight(96)
        info.setStyleSheet("background: transparent; border: none;")
        row.addWidget(info, 1)
        v.addLayout(row)

        desc_box = _AlbumDescBox()
        desc_box.setReadOnly(True)
        desc_box.setFrameShape(QFrame.NoFrame)
        desc_box.setFixedHeight(self.ALBUM_DESC_HEIGHT)
        desc_box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        desc_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        desc_box.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        desc_box.setPlainText(self._clean_album_desc(desc) or "（暂无专辑介绍）")
        desc_box.setStyleSheet(
            "QPlainTextEdit { background: #f7f8fa; border: 1px solid #e3e7ee;"
            " border-radius: 4px; padding: 2px 4px; font-size: 12px; color: #444; }")
        v.addWidget(desc_box)
        return card

    # ===================== 渲染：专辑解析（专辑详情） =====================
    def _render_album(self, album):
        if not album:
            self._show_empty()
            return
        if not isinstance(album, dict):
            self._render_album_info(album)
            return
        songs = [s for s in (album.get("songs") or []) if isinstance(s, dict)]
        # 网易云 /api/v1/album/ 是扁平结构（顶部 name/coverImgUrl/artist），
        # 同时兼容带一层 album 的旧结构
        album_info = album.get("album") if isinstance(album.get("album"), dict) else album
        pic = (album_info.get("picUrl") or album_info.get("coverImgUrl")
               or album_info.get("blurPicUrl") or "")
        artist = album_info.get("artist") or album_info.get("artists") or ""
        artist_name = artist.get("name", "") if isinstance(artist, dict) else str(artist)
        name = album_info.get("name") or ""
        publish = album_info.get("publishTime") or 0
        try:
            pub_str = (time.strftime("%Y-%m-%d", time.localtime(publish / 1000))
                       if publish else "未知")
        except (ValueError, OSError, OverflowError):
            pub_str = "未知"
        card = self._album_info_card(
            name, artist_name, album_info.get("size") or len(songs), pub_str, pic,
            album_info.get("description") or album_info.get("briefDesc") or "")
        self.detail_layout.addWidget(card)
        self.results = [
            {"id": s["id"], "name": s.get("name", ""),
             "artists": s.get("artists") or "/".join(
                 a.get("name", "") for a in (s.get("ar") or [])),
             "album": (s.get("al") or {}).get("name") or name,
             "picUrl": s.get("picUrl") or (s.get("al") or {}).get("picUrl") or pic,
             "duration": s.get("duration") or s.get("dt") or 0}
            for s in songs if s.get("id")
        ]
        self._render_songs(self.results)
        self.status_label.setText(f"专辑「{name}」共 {len(self.results)} 首")

    # ===================== 渲染：专辑搜索结果（整张专辑下载） =====================
    def _album_row(self, album):
        """专辑（网易云 dict / 各平台 AlbumInfo）→ 页面统一行。"""
        if isinstance(album, dict):
            album_id = album.get("id")
            name = album.get("name", "")
            artist = album.get("artist")
            if isinstance(artist, dict):
                artist = artist.get("name", "")
            pic = album.get("picUrl") or album.get("blurPicUrl") or ""
            count = album.get("size") or album.get("song_count") or 0
        else:
            album_id = album.album_id
            name = album.album_name
            artist = album.singer
            pic = album.cover_url
            count = album.song_count
        return {
            "_kind": "album",
            "id": album_id,
            "name": name,
            "artists": artist or "",
            "picUrl": pic or "",
            "song_count": int(count or 0),
            "_platform": self.platform,
        }

    def _render_albums(self, albums):
        rows = [self._album_row(a) for a in albums or []]
        self.results = rows
        if not rows:
            lbl = QLabel("（无结果）")
            lbl.setStyleSheet("color: gray; padding: 20px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.detail_layout.addWidget(lbl)
            self.status_label.setText("无结果")
            return
        self._render_album_rows(rows)
        self.status_label.setText(f"共 {len(rows)} 个专辑，勾选后下载整张专辑")

    def _render_album_rows(self, rows):
        """只画专辑行（不重置 results / status），供歌手主页分节复用。"""
        for row in rows:
            frame = QFrame()
            frame.setObjectName("albumRow")
            h = QHBoxLayout(frame)
            h.setContentsMargins(0, 1, 0, 1)
            cb = QCheckBox()
            cb.setFixedSize(18, 18)
            self.check_boxes.append(cb)
            h.addWidget(cb)
            self._load_image_async(row.get("picUrl"), 44, h, lazy=True)
            text = f"{row.get('name', '')} - {row.get('artists', '')}"
            if row.get("song_count"):
                text += f"（{row['song_count']} 首）"
            lbl = ElidedLabel(text)
            lbl.setObjectName("rowText")
            h.addWidget(lbl, 1)
            make_row_clickable(frame, cb)
            self.detail_layout.addWidget(frame)
        QTimer.singleShot(0, self._load_visible_covers)

    def _render_album_info(self, album):
        """各平台 AlbumInfo → 专辑信息卡 + 曲目列表。"""
        card = self._album_info_card(
            album.album_name, album.singer,
            album.song_count or len(album.songs), album.release_date,
            album.cover_url, album.description)
        self.detail_layout.addWidget(card)
        self.results = [_song_to_row(s) for s in album.songs]
        self._render_songs(self.results)
        self.status_label.setText(f"专辑「{album.album_name}」共 {len(self.results)} 首")

    # ===================== 渲染：歌手主页 =====================
    def _render_artist(self, payload):
        payload = payload or {}
        artist = payload.get("artist") or {}
        if not artist:
            self._show_empty()
            return
        self._artist_payload = payload
        self._artist_candidates = payload.get("candidates") or []
        self.results = []
        self.check_boxes = []

        if len(self._artist_candidates) > 1:
            bar = QHBoxLayout()
            bar.setContentsMargins(0, 0, 0, 2)
            bar.addWidget(QLabel("匹配歌手:"))
            combo = QComboBox()
            current = 0
            for index, cand in enumerate(self._artist_candidates):
                count = cand.get("song_count") or 0
                label = cand.get("name", "")
                if count:
                    label += f"（{count} 首）"
                combo.addItem(label, cand.get("id"))
                if cand.get("name") == artist.get("name"):
                    current = index
            combo.setCurrentIndex(current)
            combo.currentIndexChanged.connect(self._switch_artist_candidate)
            bar.addWidget(combo)
            bar.addStretch(1)
            self.detail_layout.addLayout(bar)

        songs = self._artist_song_rows(artist.get("songs") or [])
        albums = [self._album_row(a) for a in (artist.get("albums") or [])]

        info_card = QFrame()
        info_card.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = artist.get("pic") or (songs[0].get("picUrl") if songs else "")
        self._load_image_async(pic, 96, h)
        lines = [f"{artist.get('name', '')}"]
        stats = []
        if artist.get("song_count"):
            stats.append(f"歌曲 {artist['song_count']}")
        if artist.get("album_count"):
            stats.append(f"专辑 {artist['album_count']}")
        if artist.get("fans"):
            stats.append(f"粉丝 {artist['fans']}")
        if stats:
            lines.append(" / ".join(stats))
        if artist.get("desc"):
            lines.append(str(artist["desc"])[:120])
        if artist.get("url"):
            lines.append(str(artist["url"]))
        txt = QLabel("\n".join(lines))
        txt.setWordWrap(True)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)

        if songs:
            self._section_title(f"热门歌曲 · {len(songs)}")
            self.results.extend(songs)
            self._render_song_rows(songs)
        if albums:
            self._section_title(f"专辑 · {len(albums)}")
            self.results.extend(albums)
            self._render_album_rows(albums)
        if not songs and not albums:
            hint = QLabel("（没有取到公开曲目：换个输入，或改用「歌曲搜索」按歌手名查）")
            hint.setStyleSheet("color: #888; padding: 10px 2px;")
            self.detail_layout.addWidget(hint)

        album_part = f" / {len(albums)} 个专辑" if albums else ""
        self.status_label.setText(
            f"歌手「{artist.get('name', '')}」：{len(songs)} 首歌曲{album_part}，勾选后下载")

    def _section_title(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-weight: bold; font-size: 12px; padding: 6px 2px 2px 2px;"
            " background: transparent; border: none;")
        self.detail_layout.addWidget(lbl)

    def _artist_song_rows(self, songs):
        rows = []
        for item in songs:
            if isinstance(item, dict):
                row = {
                    "id": item.get("id"),
                    "name": item.get("name", ""),
                    "artists": item.get("artists", ""),
                    "album": item.get("album", ""),
                    "picUrl": item.get("picUrl", ""),
                    "duration": item.get("duration", 0),
                    "_platform": self.platform,
                }
            else:
                row = _song_to_row(item)
            rows.append(row)
        return rows

    def _switch_artist_candidate(self, index):
        cands = getattr(self, "_artist_candidates", [])
        if not (0 <= index < len(cands)):
            return
        cand = cands[index]
        self._load_artist(cand.get("id"), cand.get("name", ""))

    def _load_artist(self, artist_id, name=""):
        """切歌手候选：只重取歌手主页，不动输入框。"""
        if artist_id is None:
            return
        self.status_label.setText(f"正在加载歌手「{name or artist_id}」...")
        self.search_btn.setEnabled(False)
        platform, mode = self.platform, self.mode

        def work():
            if platform == "netease":
                return self._work_netease_artist(str(artist_id), self._cookies())
            from music import ALL_PLATFORMS
            scraper_cls = next(cls for pid, _, cls in ALL_PLATFORMS if pid == platform)
            payload = scraper_cls().get_artist(str(artist_id), name)
            if payload is None:
                raise ValueError("没能打开该歌手主页")
            return {"candidates": getattr(self, "_artist_candidates", []), "artist": payload}

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"{err}")
                return
            if isinstance(result, tuple):
                result = result[1]
            self._clear_detail()
            self._render_result(("artist", result))
            self._result_snapshot = ("artist", result)

        run_bg(work, done)

    # ===================== 下载：批量下载（单曲/歌曲/专辑） =====================
    def _download_selected(self):
        selected = [self.results[i] for i, cb in enumerate(self.check_boxes)
                    if cb.isChecked() and i < len(self.results)]
        if not selected:
            self.app.toast("请先勾选要下载的歌曲", "warning")
            return
        if self.platform == MERGE_PLATFORM:
            self._download_selected_multi(selected)
            return
        if self.platform == "netease":
            self._download_selected_netease(selected)
            return

        quality = self.quality_menu.currentData() or ""
        use_fallback = self.fallback_check.isChecked()
        include_cover = self.cover_check.isChecked()
        self.download_btn.setEnabled(False)
        self.status_label.setText("下载中..." if not use_fallback else "下载中（失败将自动换源）...")
        from gui_qt.download_float import get_float
        card = get_float().add_batch(f"音乐下载 · {self.platform}", total=len(selected), threads=4)

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from music.downloader import download_song_smart
            from music.models import SongInfo
            from toolbox.config_manager import get_music_download_dir
            out_dir = str(get_music_download_dir())
            ok, fail, preview, via_cnt, first_err = 0, 0, 0, 0, ""

            # 勾选的专辑先展开成曲目（各平台专辑接口），再统一下载
            jobs = []
            for item in selected:
                if item.get("_kind") == "album":
                    try:
                        scraper = self._scraper()
                        album = scraper.get_album(str(item.get("id") or ""))
                    except Exception as exc:
                        album = None
                        if not first_err:
                            first_err = f"专辑展开失败：{exc}"
                    if album:
                        for s in album.songs:
                            jobs.append(_song_to_row(s))
                    else:
                        fail += 1
                else:
                    jobs.append(item)
            if jobs:
                card.plan(len(jobs) + fail, 4)

            def _one(item):
                song = item.get("_song")
                if song is None:
                    song = SongInfo(song_id=str(item.get("id", "")),
                                    song_name=item.get("name", ""),
                                    singer=item.get("artists", ""),
                                    album_name=item.get("album", ""),
                                    duration=(item.get("duration") or 0) // 1000,
                                    cover_url=item.get("picUrl") or "",
                                    platform=self.platform)
                name = f"{item.get('name', '')} - {item.get('artists', '')}".strip(" -")
                card.handle_event("start", name[:40] or f"歌曲 {song.song_id}")
                r = download_song_smart(song, out_dir, quality, fallback=use_fallback,
                                        include_cover=include_cover)
                card.handle_event("done", name[:40] or f"歌曲 {song.song_id}",
                                  bool(r and r.success))
                return r

            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(_one, it): it for it in jobs}
                for fut in as_completed(futs):
                    try:
                        r = fut.result()
                    except Exception as e:
                        r = None
                        if not first_err:
                            first_err = str(e)
                    if r is None:
                        fail += 1
                    elif r.success and r.is_preview:
                        preview += 1
                    elif r.success:
                        ok += 1
                        if r.via_platform:
                            via_cnt += 1
                    else:
                        fail += 1
                        if not first_err:
                            first_err = r.error_message
            return ok, fail, preview, via_cnt, first_err

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"{err}")
                card.finish(f"下载失败: {str(err)[:50]}")
                return
            ok, fail, preview, via_cnt, first_err = result
            parts = [f"成功 {ok}"]
            if via_cnt:
                parts.append(f"换源 {via_cnt}")
            if preview:
                parts.append(f"试听 {preview}")
            if fail:
                parts.append(f"失败 {fail}")
            tip = ("完成（" + " / ".join(parts) + ")") if (fail or preview) else ("下载完成（成功 " + str(ok) + "）")
            if first_err and (fail or preview):
                tip += f"：{first_err[:40]}"
            self.status_label.setText(tip)
            card.finish(" / ".join(parts))

        run_bg(work, done)

    def _download_selected_multi(self, selected):
        """五平台勾选批量下载：网易云走 MusicDownloader，其余走 download_song_smart。"""
        use_fallback = self.fallback_check.isChecked()
        include_cover = self.cover_check.isChecked()
        self.download_btn.setEnabled(False)
        self.status_label.setText(
            "下载中（五平台混合，失败将自动换源）..." if use_fallback
            else "下载中（五平台混合）...")
        from gui_qt.download_float import get_float
        card = get_float().add_batch("音乐下载 · 五平台", total=len(selected), threads=4)

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from music.downloader import download_song_smart
            from music.models import SongInfo
            from toolbox.config_manager import get_music_download_dir
            out_dir = str(get_music_download_dir())

            def _one(item):
                key = item.get("_platform_key") or item.get("_platform") or ""
                label = (f"{item.get('name', '')} - {item.get('artists', '')}".strip(" -")
                         or f"歌曲 {item.get('id', '')}")[:40]
                card.handle_event("start", label)
                ok = preview = via = False
                err = ""
                try:
                    if key == "netease":
                        from auth import get_netease_cookie
                        from netease import MusicDownloader
                        dl = MusicDownloader(download_dir=out_dir,
                                             cookie_string=get_netease_cookie())
                        r = dl.download_music_file(
                            int(item.get("id")), "jymaster", include_cover=include_cover)
                        ok = bool(r and r.success)
                        if not ok:
                            err = getattr(r, "error_message", "") or "失败"
                    else:
                        song = item.get("_song")
                        if not isinstance(song, SongInfo):
                            song = SongInfo(
                                song_id=str(item.get("id", "")),
                                song_name=item.get("name", ""),
                                singer=item.get("artists", ""),
                                album_name=item.get("album", ""),
                                duration=(item.get("duration") or 0) // 1000,
                                cover_url=item.get("picUrl") or "",
                                platform=key)
                        r = download_song_smart(song, out_dir, "", fallback=use_fallback,
                                                include_cover=include_cover)
                        ok = bool(r and r.success and not r.is_preview)
                        preview = bool(r and r.success and r.is_preview)
                        via = bool(getattr(r, "via_platform", ""))
                        if r and not r.success:
                            err = r.error_message or "失败"
                except Exception as exc:
                    err = str(exc)
                card.handle_event("done", label, ok or preview)
                return ok, preview, via, err

            ok = fail = preview = via = 0
            first_err = ""
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = [ex.submit(_one, item) for item in selected]
                for fut in as_completed(futs):
                    try:
                        got_ok, got_preview, got_via, got_err = fut.result()
                    except Exception as exc:
                        got_ok, got_preview, got_via, got_err = False, False, False, str(exc)
                    if got_ok:
                        ok += 1
                    elif got_preview:
                        preview += 1
                    else:
                        fail += 1
                        if not first_err:
                            first_err = got_err
                    if got_via:
                        via += 1
            return ok, fail, preview, via, first_err

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"{err}")
                card.finish(f"下载失败: {str(err)[:50]}")
                return
            ok, fail, preview, via, first_err = result
            parts = [f"成功 {ok}"]
            if via:
                parts.append(f"换源 {via}")
            if preview:
                parts.append(f"试听 {preview}")
            if fail:
                parts.append(f"失败 {fail}")
            tip = " / ".join(parts)
            if first_err and (fail or preview):
                tip += f"：{first_err[:40]}"
            self.status_label.setText(f"下载完成（{tip}）")
            card.finish(tip)

        run_bg(work, done)

    def _download_selected_netease(self, selected):
        quality = [k for k, v in QUALITY_NAMES.items() if v == self.quality_menu.currentText()][0]
        include_cover = self.cover_check.isChecked()
        # 歌手主页/专辑搜索结果：行上带 _kind=album 的展开成整张专辑
        album_rows = [r for r in selected if r.get("_kind") == "album"]
        song_rows = [r for r in selected if r.get("_kind") != "album"]

        self.download_btn.setEnabled(False)
        self.status_label.setText("下载中...")
        from gui_qt.download_float import get_float
        # total 先按歌曲数估（专辑要拉详情后才知道总数，plan 由实际任务数修正）
        card = get_float().add_batch("音乐下载 · 网易云",
                                     total=len(selected), threads=4)

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from netease import MusicDownloader, album_detail
            from toolbox.config_manager import get_music_download_dir
            from auth import get_netease_cookie
            out_dir = str(get_music_download_dir())
            cookie_string = get_netease_cookie()
            jobs = []  # (song_id, 显示名)
            fail = 0
            first_err = ""
            if album_rows:
                cookies = self._cookies()
                for alb in album_rows:
                    try:
                        info = album_detail(int(alb["id"]), cookies)
                        for song in info.get("songs", []):
                            jobs.append((song["id"],
                                         (song.get("name") or f"歌曲{song['id']}")[:40]))
                    except Exception as e:
                        fail += 1
                        if not first_err:
                            first_err = str(e)
                if not jobs and not fail:
                    first_err = "专辑内无歌曲"
            jobs.extend((int(s["id"]),
                         (s.get("name") or f"歌曲{s['id']}")[:40]) for s in song_rows)
            card.plan(len(jobs) + fail, 4)

            def _one(job):
                sid, name = job
                card.handle_event("start", name)
                try:
                    dl = MusicDownloader(download_dir=out_dir, cookie_string=cookie_string)
                    r = dl.download_music_file(sid, quality, include_cover=include_cover)
                    okf = bool(r and r.success)
                    errm = (r.error_message if r else "失败")
                except Exception as e:
                    okf = False
                    errm = str(e)
                card.handle_event("done", name, okf)
                return okf, errm

            ok = 0
            if jobs:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = [ex.submit(_one, j) for j in jobs]
                    for fut in as_completed(futs):
                        try:
                            okf, errm = fut.result()
                        except Exception as e:
                            okf, errm = False, str(e)
                        if okf:
                            ok += 1
                        else:
                            fail += 1
                            if not first_err:
                                first_err = errm
            return ok, fail, first_err

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"{err}")
                card.finish(f"下载失败: {str(err)[:50]}")
                return
            ok, fail, first_err = result
            if fail:
                tip = f"完成（成功 {ok} / 失败 {fail}）" + (f"：{first_err[:50]}" if first_err else "")
                self.status_label.setText(tip)
                card.finish(f"成功 {ok} / 失败 {fail}")
            else:
                self.status_label.setText(f"下载完成（成功 {ok}）")
                card.finish(f"成功 {ok}")

        run_bg(work, done)
