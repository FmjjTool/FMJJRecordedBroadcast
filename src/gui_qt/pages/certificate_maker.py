"""实用工具 · 内部认证证书生成器页（协议文档录入 → 字段匹配 → 复核 → 出证书）。

交互分五步：
1. 导入协议文档（txt / md / docx / pdf）或 xlsx 填写表，也可直接粘贴正文；
2. 自动匹配编号、作品、署名、期限、双方确认等字段，以及文档里的「□」勾选状态；
3. 在「复选框复核」区人工核对勾选（自动匹配到的项带「已匹配」标记）；
4. 在「图片」区导入作品图，默认自动合成水印；「历史」区可回填以往证书数据；
5. 生成两页证书（证书页 + 图片页），导出 PNG 300dpi 与合并 PDF。

界面只做表单、预览与调用；解析、渲染、导出在 `toolbox/certificate_engine.py`，
历史存储在 `toolbox/certificate_history.py`（只存数据，不存图片），页面不重复业务逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)
from PIL import Image as PILImage

from gui_qt import theme
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from toolbox import certificate_engine as engine
from toolbox import certificate_history
from toolbox.config_manager import get_config_dir, get_export_dir

_CONFIG_SECTION = "certificate_maker"
_PREVIEW_MIN_WIDTH = 760      # 适配宽度时的下限，保证默认就能看清正文
_PREVIEW_MAX_WIDTH = 3600     # 上限，避免超宽屏拉得过大
_PREVIEW_BASE_WIDTH = 1700    # 「100%」对应的预览宽度
_TEXT_FIELDS = (
    ("title", "作品标题", "协议第一条第 1.1 项"),
    ("project_name", "歌曲 / 项目", "作品对应歌曲或项目名称，如有则填"),
    ("author_name", "授权方圈名", "姓名 / 圈名"),
    ("author_contact", "联系方式", "邮箱 / 微信 / 其他常用联系方式"),
    ("manager_rep", "管理组代表", "被授权方管理组代表 / 负责人"),
    ("contact_person", "对接人", "被授权方对接人"),
    ("lead_designer", "主要设计人", "附件一文字填写项"),
    ("co_designers", "协作设计人", "多人用顿号分隔，如有则填"),
)
_WM_FIELDS = (
    ("wm_author", "图片中署名", "留空自动同授权方圈名"),
    ("wm_lead", "图中主要设计人", "留空自动同主要设计人"),
    ("wm_co", "图中协作设计人", "留空自动同协作设计人"),
    ("wm_title", "图中作品名", "留空自动同作品标题"),
    ("wm_archive_no", "图中认证号", "留空自动同内部认证号"),
    ("wm_other", "其他约定标识", "如有则填"),
    ("credit_note", "署名写法说明（选填）", "如：圈名 / ID / 固定格式"),
    ("watermark_file", "水印样件文件名", "导出后自动填"),
)
_DATE_FIELDS = (
    ("period_start", "授权起始", "YYYY-MM-DD"),
    ("period_end", "授权截止", "YYYY-MM-DD"),
    ("author_sign_date", "授权方确认", "留空按今天"),
    ("licensee_sign_date", "被授权方确认", "留空按今天"),
    ("renew_until", "续签至", "状态为「已续签」时填写"),
    ("issue_date", "签发日期", "留空按今天"),
)
_SIGN_FIELDS = (
    ("licensee", "签发方", "默认封茗囧菌数据汇总库管理组"),
    ("signer", "签发方代表", "签名 / 电子章，可留空"),
    ("author_sign", "授权方确认", "签名 / 圈名，留空同圈名"),
    ("licensee_rep_sign", "被授权方代表", "签名"),
    ("confirm_archive_no", "确认认证号", "留空同内部认证号"),
)
_CERT_FIELDS = (
    ("cert_title", "载明作品标题", "留空同作品标题"),
    ("cert_type", "载明作品类型", "留空同作品类型"),
    ("cert_author", "载明授权方", "留空同授权方圈名"),
    ("cert_lead", "载明主要设计人", "留空同主要设计人"),
    ("cert_co", "载明协作设计人", "留空同协作设计人"),
    ("cert_period_start", "载明起始日", "留空同授权起始"),
    ("cert_period_end", "载明届满日", "留空同授权截止"),
)
_OTHER_EDITS = {
    "art_type": ("art_type_other", "其他类型", "勾选「其他」时填写"),
    "sample_format": ("sample_other", "其他格式", "样本格式选「其他」时填写"),
    "image_format": ("image_other", "其他格式", "图片格式选「其他」时填写"),
    "usage_places": ("usage_other", "其他位置", "使用位置选「其他」时填写具体位置"),
}
_WM_CHECK_FIELDS = {
    "授权方署名/圈名": "wm_author",
    "主要设计人署名": "wm_lead",
    "协作设计人署名": "wm_co",
    "作品标题/项目名称": "wm_title",
    "内部认证号": "wm_archive_no",
    "其他约定标识": "wm_other",
}

# 必填标记：REQUIRED 判红框；AUTO 只标星（留空由回落值补齐）
_REQUIRED_KEYS = {key for key, _label in engine.REQUIRED_FIELDS}
_AUTO_REQUIRED_KEYS = {
    "watermark_file", "wm_author", "wm_lead", "author_sign", "confirm_archive_no",
    "cert_title", "cert_type", "cert_author", "cert_lead", "cert_period_start",
    "cert_period_end", "issue_date", "signer", "renew_until",
}
_GROUP_REQUIRED_KEYS = {"art_type", "sample_format", "image_format", "cert_status"}


def _danger() -> str:
    """当前主题的危险色（用于富文本；QSS 用 theme(danger) 随主题重绘）。"""
    return theme.color("danger")


def _invalid_qss() -> str:
    # 用当前主题的实色：QSS 的 palette(bright-text) 在部分 Qt 版本不生效
    return f"border: 2px solid {_danger()}; border-radius: 3px;"


def _field_label(text: str, key: str = "") -> QLabel:
    """必填字段标签：红色 * 标记；自动回落的字段提示「留空自动带出」。"""
    if key in _REQUIRED_KEYS:
        label = QLabel(f'{text} <span style="color:{_danger()};">*</span>')
        label.setTextFormat(Qt.RichText)
        label.setToolTip("必填项")
        return label
    if key in _AUTO_REQUIRED_KEYS:
        label = QLabel(f'{text} <span style="color:{_danger()};">*</span>')
        label.setTextFormat(Qt.RichText)
        label.setToolTip("必填，留空自动带出")
        return label
    return QLabel(text)


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """卡片容器：base 底 + 细边框，正文从 12px 内边距开始。"""
    frame = QFrame()
    frame.setObjectName("certCard")
    frame.setStyleSheet(
        "#certCard { background: palette(base); border: 1px solid palette(shadow);"
        " border-radius: 6px; }"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setStyleSheet("font-size: 13px; font-weight: bold; color: palette(window-text);")
    layout.addWidget(label)
    return frame, layout


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
    return label


class DateField(QDateEdit):
    """带下拉日历的日期输入；留空时显示空串（用最小日期当哨兵）。

    对外保持 QLineEdit 风格的 `text()` / `setText()`，页面其余读写逻辑不用改。
    """

    EMPTY = QDate(1900, 1, 1)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setMinimumDate(self.EMPTY)
        self.setMaximumDate(QDate(2100, 12, 31))
        self.setSpecialValueText("")
        self.setDate(self.EMPTY)
        self.setToolTip("点右侧箭头用日历选择；留空表示不填")

    def text(self) -> str:  # noqa: D401 - 兼容 QLineEdit 用法
        current = self.date()
        return "" if current == self.EMPTY else current.toString("yyyy-MM-dd")

    def setText(self, value: str) -> None:  # noqa: N802 - 兼容 QLineEdit 用法
        text = str(value or "").strip()
        parsed = QDate.fromString(text[:10], "yyyy-MM-dd")
        self.setDate(parsed if parsed.isValid() and parsed != self.EMPTY
                     else self.EMPTY)


class TextPasteDialog(QDialog):
    """粘贴协议正文的对话框（不弹系统原生输入框）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("粘贴协议正文")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(_hint("粘贴整份协议（正文 + 附件一/三），自动匹配字段与 □ 勾选状态。"
                               "已填好的 Excel 填写表请用工具栏「导入文档」。"))
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在此粘贴协议全文…")
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("解析")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.editor.toPlainText()


class CertificateMakerPage(BasePage):
    """内部认证证书生成器。"""

    def __init__(self, app):
        super().__init__(app)
        self._section = self._read_config()
        self._images: list[Path] = [
            Path(item) for item in self._section.get("images") or []
            if Path(str(item)).is_file()
        ]
        self._out_dir = Path(str(self._section.get("out_dir") or "")) \
            if self._section.get("out_dir") else Path(get_export_dir()) / "证书"
        self._notes: list[str] = []
        self._usage_auto = True
        self._checkbox_widgets: dict[str, dict[str, QCheckBox | QRadioButton]] = {}
        self._checkbox_group_boxes: dict[str, QGroupBox] = {}
        self._matched_labels: dict[str, QLabel] = {}
        self._other_edits: dict[str, QLineEdit] = {}
        self._wm_edits: dict[str, QLineEdit] = {}
        self._cert_edits: dict[str, QLineEdit] = {}
        self._group_base_qss: dict[QGroupBox, str] = {}
        self._history_records: list[dict] = []
        self._suspend_signals = False
        self._auto_end = True          # 授权截止是否仍在跟随「起始 +3 年」

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._preview_pages: list = []   # 最近一次渲染的两页，缩放时直接复用
        self._preview_scale_timer = QTimer(self)
        self._preview_scale_timer.setSingleShot(True)
        self._preview_scale_timer.setInterval(120)
        self._preview_scale_timer.timeout.connect(self._scale_preview)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(900)
        self._save_timer.timeout.connect(self._save_config)

        self._build_ui()
        self._restore_year(self._section.get("number_year"))
        self._restore_form(self._section.get("data") or {})
        self.auto_watermark_check.setChecked(
            bool(self._section.get("auto_watermark", True)))
        self._refresh_image_list()
        self._refresh_history()
        self._refresh_preview()
        self._maybe_auto_number()

    # ==================== 配置 ====================

    def _config_path(self) -> Path:
        return get_config_dir() / "config.json"

    def _read_config(self) -> dict:
        try:
            with open(self._config_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            section = data.get(_CONFIG_SECTION)
            return dict(section) if isinstance(section, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_config(self) -> None:
        data: dict = {}
        try:
            with open(self._config_path(), "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
        data[_CONFIG_SECTION] = {
            "data": self._collect_data().to_dict(),
            "images": [str(path) for path in self._images],
            "out_dir": str(self._out_dir),
            "auto_watermark": self.auto_watermark_check.isChecked(),
            "number_year": self._selected_year() or 0,
        }
        try:
            with open(self._config_path(), "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def on_hide(self):
        self._save_config()

    def on_show(self):
        self._refresh_history()
        if hasattr(self, "_preview_timer"):
            self._refresh_preview()

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.source_label = QLabel("尚未导入协议文档 / 填写表")
        self.source_label.setStyleSheet("color: palette(placeholder-text); font-size: 12px;")
        toolbar.addWidget(self.source_label, 1)
        self.import_btn = QPushButton("导入文档")
        self.import_btn.setToolTip(
            "支持 txt / md / docx / pdf 协议，或 xlsx 填写表（字段名称 + 填写内容）")
        self.import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self.import_btn)
        self.paste_btn = QPushButton("粘贴文本")
        self.paste_btn.clicked.connect(self._on_paste)
        toolbar.addWidget(self.paste_btn)
        self.number_btn = QPushButton("自动编号")
        self.number_btn.setToolTip("按所选年份已有证书的 6 位序号顺延，并自动计算校验码")
        self.number_btn.clicked.connect(self._on_auto_number)
        toolbar.addWidget(self.number_btn)
        self.dir_btn = QPushButton("输出目录")
        self.dir_btn.setToolTip(str(self._out_dir))
        self.dir_btn.clicked.connect(self._on_pick_dir)
        toolbar.addWidget(self.dir_btn)
        self.export_btn = QPushButton("生成证书")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self.export_btn)
        self.word_btn = QPushButton("导出 Word")
        self.word_btn.setToolTip(
            "导出「证书页 + 完整协议（正文 / 附件一 / 附件二）」的 .docx，"
            "正文按软件当前字段填充，冲突以软件为准")
        self.word_btn.clicked.connect(self._on_export_docx)
        toolbar.addWidget(self.word_btn)
        root.addLayout(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self._build_left_panel())
        self.splitter.addWidget(self._build_preview_panel())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setHandleWidth(6)
        self.splitter.setSizes([440, 960])
        root.addWidget(self.splitter, 1)

    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        container.setMinimumWidth(430)
        container.setMaximumWidth(580)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(self._scroll_page([
            self._build_source_card(),
            self._build_number_card(),
            self._build_work_card(),
            self._build_watermark_card(),
            self._build_cert_card(),
            self._build_period_card(),
            self._build_sign_card(),
            self._build_usage_card(),
        ]), "字段")
        self.left_tabs.addTab(self._scroll_page([self._build_check_card()]), "复选框复核")
        self.left_tabs.addTab(self._build_image_tab(), "图片")
        self.left_tabs.addTab(self._build_history_tab(), "历史")
        layout.addWidget(self.left_tabs, 1)
        return container

    @staticmethod
    def _scroll_page(widgets: list[QWidget]) -> QWidget:
        """把一组卡片放进可滚动容器。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(10)
        for widget in widgets:
            body_layout.addWidget(widget)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _build_image_tab(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 8, 6, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_image_card())
        self.auto_watermark_check = QCheckBox("自动生成水印（导出时合成到作品图上）")
        self.auto_watermark_check.setToolTip(
            "按「复选框复核 → 水印内容核对」的勾选项，把署名与认证号斜向平铺到作品图，\n"
            "并另存到输出目录的「水印件」文件夹。")
        self.auto_watermark_check.toggled.connect(self._on_field_changed)
        layout.addWidget(self.auto_watermark_check)
        layout.addWidget(_hint("图片按列表顺序排版：第 1 张放最大位，最多 6 张；"
                               "证书页不放图，全部在这里。"))
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        return scroll

    def _build_source_card(self) -> QWidget:
        frame, layout = _card("协议文档")
        self.source_file_label = QLabel("未导入")
        self.source_file_label.setStyleSheet("color: palette(window-text); font-size: 12px;")
        self.source_file_label.setWordWrap(True)
        layout.addWidget(self.source_file_label)
        self.notes_label = _hint("导入后显示解析提示；缺项在对应字段补齐即可。")
        layout.addWidget(self.notes_label)
        return frame

    def _build_number_card(self) -> QWidget:
        frame, layout = _card("证书编号")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.number_edits: dict[str, QLineEdit] = {}
        self.year_combo = QComboBox()
        self.year_combo.setToolTip(
            "默认自动取本机当前年份；也可手动指定年份，序号按该年份顺延")
        self.year_combo.addItem(f"自动（本机 {engine.default_year()}）", 0)
        for year in range(engine.default_year() - 6, engine.default_year() + 5):
            self.year_combo.addItem(str(year), year)
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        grid.addWidget(QLabel("编号年份"), 0, 0)
        grid.addWidget(self.year_combo, 0, 1)
        grid.setColumnStretch(1, 1)
        # 编号整串较长（含校验码），单独占满一行，避免被输入框宽度截断
        grid.addWidget(_field_label("内部认证号", "archive_no"), 1, 0, 1, 2)
        edit = QLineEdit()
        edit.setPlaceholderText("FMJJ-DB-ART-年份-6位序号-8位校验码")
        # 编号是等宽字母数字串，用等宽字体压缩宽度，保证 30 位整串一眼看全
        number_font = QFont(edit.font())
        number_font.setFamilies(["Consolas", "Courier New"])
        edit.setFont(number_font)
        edit.textChanged.connect(self._on_field_changed)
        self.number_edits["archive_no"] = edit
        grid.addWidget(edit, 2, 0, 1, 2)
        layout.addLayout(grid)
        layout.addWidget(_hint(
            "编号格式：FMJJ-DB-ART-年份-6 位序号-8 位校验码；序号按所选年份已有证书顺延，"
            "校验码由编号与作品信息自动计算。"))
        return frame

    def _build_work_card(self) -> QWidget:
        frame, layout = _card("作品与署名")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.text_edits: dict[str, QLineEdit] = {}
        for row, (key, label, placeholder) in enumerate(_TEXT_FIELDS):
            grid.addWidget(_field_label(label, key), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self._on_field_changed)
            self.text_edits[key] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        return frame

    def _build_watermark_card(self) -> QWidget:
        frame, layout = _card("附件一 · 图片内署名（自动水印）")
        layout.addWidget(_hint("留空自动跟随正文；哪些内容进图片水印，由「复选框复核」里"
                               "「水印内容核对」的勾选决定。"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for row, (key, label, placeholder) in enumerate(_WM_FIELDS):
            grid.addWidget(_field_label(label, key), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self._on_field_changed)
            self._wm_edits[key] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        return frame

    def _build_cert_card(self) -> QWidget:
        frame, layout = _card("证书载明（留空自动跟随）")
        layout.addWidget(_hint("证书页优先用这里的值；留空则跟随协议正文 / 附件一。"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for row, (key, label, placeholder) in enumerate(_CERT_FIELDS):
            grid.addWidget(_field_label(label, key), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self._on_field_changed)
            self._cert_edits[key] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        return frame

    def _build_period_card(self) -> QWidget:
        frame, layout = _card("授权期限")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.date_edits: dict[str, DateField] = {}
        for row, (key, label, placeholder) in enumerate(_DATE_FIELDS):
            grid.addWidget(_field_label(label, key), row, 0)
            edit = DateField()
            edit.setToolTip(placeholder)
            if key == "renew_until":
                edit.setEnabled(False)
            edit.dateChanged.connect(self._on_field_changed)
            edit.dateChanged.connect(
                lambda _d, k=key: self._on_date_changed(k))
            self.date_edits[key] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        hint = _hint("点输入框右侧箭头用日历选择；授权截止默认按起始日 +3 年，可手动改。"
                     "留空时证书显示「自双方确认之日起，按协议第四条执行」。")
        layout.addWidget(hint)
        return frame

    def _build_sign_card(self) -> QWidget:
        frame, layout = _card("签发")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.sign_edits: dict[str, QLineEdit] = {}
        for row, (key, label, placeholder) in enumerate(_SIGN_FIELDS):
            grid.addWidget(_field_label(label, key), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self._on_field_changed)
            self.sign_edits[key] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)
        return frame

    def _build_usage_card(self) -> QWidget:
        frame, layout = _card("授权用途摘要")
        self.usage_edit = QPlainTextEdit()
        self.usage_edit.setPlaceholderText("勾选作品类型后自动匹配；手动修改后不再自动覆盖。")
        self.usage_edit.setFixedHeight(76)
        self.usage_edit.textChanged.connect(self._on_usage_edited)
        layout.addWidget(self.usage_edit)
        row = QHBoxLayout()
        row.addWidget(_hint("证书第 12 项载明内容"))
        row.addStretch(1)
        self.usage_reset_btn = QPushButton("按类型恢复")
        self.usage_reset_btn.clicked.connect(self._on_usage_reset)
        row.addWidget(self.usage_reset_btn)
        layout.addLayout(row)
        return frame

    def _build_check_card(self) -> QWidget:
        frame, layout = _card("复选框复核（对应协议中的 □）")
        layout.addWidget(_hint("自动带出文档里的勾选；核对后可直接改，改完即生效。"))

        for group in engine.CHECK_GROUPS:
            box = QGroupBox(group["label"])
            box.setStyleSheet(
                "QGroupBox { border: 1px solid palette(midlight); border-radius: 6px;"
                " margin-top: 10px; padding-top: 6px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
            )
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(10, 8, 10, 10)
            box_layout.setSpacing(6)
            self._group_base_qss[box] = box.styleSheet()
            head = QHBoxLayout()
            if group["key"] in _GROUP_REQUIRED_KEYS:
                star = QLabel("*")
                star.setStyleSheet(f"color: {_danger()}; font-weight: bold;")
                star.setToolTip("必填项")
                head.addWidget(star)
            matched = QLabel("未匹配")
            matched.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
            head.addWidget(matched)
            head.addStretch(1)
            box_layout.addLayout(head)

            widgets: dict[str, QCheckBox | QRadioButton] = {}
            if group["key"] == "watermark_items":
                for item in group["items"]:
                    check = QCheckBox(item)
                    check.setToolTip("勾选后，该内容会合成进作品图水印")
                    check.toggled.connect(self._on_check_toggled)
                    widgets[item] = check
                    box_layout.addWidget(check)
            else:
                cols = 2 if len(group["items"]) > 2 else len(group["items"])
                grid = QGridLayout()
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(4)
                for index, item in enumerate(group["items"]):
                    if group["single"]:
                        widget = QRadioButton(item)
                        widget.setAutoExclusive(False)  # 互斥由代码保证，允许全部取消
                    else:
                        widget = QCheckBox(item)
                    widget.toggled.connect(self._on_check_toggled)
                    widgets[item] = widget
                    grid.addWidget(widget, index // cols, index % cols)
                box_layout.addLayout(grid)
                other_spec = _OTHER_EDITS.get(group["key"])
                if other_spec:
                    field_key, label, placeholder = other_spec
                    other_row = QHBoxLayout()
                    other_row.addWidget(QLabel(label))
                    edit = QLineEdit()
                    edit.setPlaceholderText(placeholder)
                    edit.setEnabled(False)
                    edit.textChanged.connect(self._on_field_changed)
                    self._other_edits[field_key] = edit
                    other_row.addWidget(edit, 1)
                    box_layout.addLayout(other_row)
                    if group["key"] == "art_type":
                        self.art_type_other = edit

            self._checkbox_widgets[group["key"]] = widgets
            self._checkbox_group_boxes[group["key"]] = box
            self._matched_labels[group["key"]] = matched
            layout.addWidget(box)
        return frame

    def _build_image_card(self) -> QWidget:
        frame, layout = _card("图片（第 2 页）")
        layout.addWidget(_hint("作品图 / 水印样本，最多 6 张；完整显示不裁切。"))
        self.image_list = QListWidget()
        self.image_list.setFixedHeight(120)
        self.image_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.image_list)
        row = QHBoxLayout()
        add_btn = QPushButton("添加图片")
        add_btn.clicked.connect(self._on_add_images)
        row.addWidget(add_btn)
        up_btn = QPushButton("上移")
        up_btn.clicked.connect(lambda: self._move_image(-1))
        row.addWidget(up_btn)
        down_btn = QPushButton("下移")
        down_btn.clicked.connect(lambda: self._move_image(1))
        row.addWidget(down_btn)
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(self._on_remove_images)
        row.addWidget(remove_btn)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._on_clear_images)
        row.addWidget(clear_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_history_tab(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 8, 6, 0)
        layout.setSpacing(8)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("按作品 / 认证号 / 圈名筛选")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self._filter_history)
        layout.addWidget(self.history_search)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda _item: self._on_history_load())
        layout.addWidget(self.history_list, 1)
        row = QHBoxLayout()
        load_btn = QPushButton("载入")
        load_btn.setToolTip("把历史记录回填到表单（只含数据，不含图片）")
        load_btn.clicked.connect(self._on_history_load)
        row.addWidget(load_btn)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._on_history_delete)
        row.addWidget(delete_btn)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._on_history_clear)
        row.addWidget(clear_btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(_hint("历史只保存字段数据，不保存图片；同一认证号重复生成会合并为一条。"))
        return body

    def _build_preview_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.preview_status = QLabel("")
        self.preview_status.setWordWrap(True)
        self.preview_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        layout.addWidget(self.preview_status)

        scale_row = QHBoxLayout()
        scale_row.setSpacing(6)
        scale_row.addWidget(QLabel("预览缩放"))
        self.preview_scale = QComboBox()
        for text, value in (("适配宽度", "fit"), ("100%", 1.0), ("125%", 1.25),
                            ("150%", 1.5), ("200%", 2.0)):
            self.preview_scale.addItem(text, value)
        self.preview_scale.setToolTip("预览按比例缩放，100% 约等于半个 A4 横版宽度")
        self.preview_scale.currentIndexChanged.connect(self._scale_preview)
        scale_row.addWidget(self.preview_scale)
        zoom_hint = QLabel("正文看不清时调大")
        zoom_hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        scale_row.addWidget(zoom_hint)
        scale_row.addStretch(1)
        layout.addLayout(scale_row)

        scroll = QScrollArea()
        self._preview_scroll = scroll
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(12)
        self.preview_labels: list[QLabel] = []
        for _ in range(2):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                "background: palette(alternate-base); border: 1px solid palette(shadow);"
                " border-radius: 6px; color: palette(placeholder-text); font-size: 12px;"
            )
            label.setMinimumHeight(300)
            body_layout.addWidget(label)
            self.preview_labels.append(label)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        return container

    # ==================== 表单读写 ====================

    def _collect_data(self) -> engine.CertificateData:
        data = engine.CertificateData()
        for key, edit in {**self.number_edits, **self.text_edits, **self._wm_edits,
                          **self._cert_edits, **self.date_edits, **self.sign_edits}.items():
            setattr(data, key, edit.text().strip())
        for group in engine.CHECK_GROUPS:
            key = group["key"]
            widgets = self._checkbox_widgets.get(key, {})
            selected = [item for item, widget in widgets.items() if widget.isChecked()]
            value = (selected or [""])[0]
            if key == "art_type":
                data.art_type = value
            elif key == "cert_status":
                data.status = value or "有效"
            elif key == "sample_format":
                data.sample_format = value
            elif key == "image_format":
                data.image_format = value
            elif key == "watermark_items":
                data.watermark_items = selected
            elif key == "usage_places":
                data.usage_places = selected
        check_values: dict[str, str] = {}
        for group_key, spec in _OTHER_EDITS.items():
            field_key = spec[0]
            text = self._other_edits[field_key].text().strip()
            setattr(data, field_key, text)
            if text:
                check_values[f"{group_key}.其他"] = text
        check_values.update({
            f"watermark_items.{item}": getattr(data, field, "")
            for item, field in _WM_CHECK_FIELDS.items() if getattr(data, field, "")
        })
        data.check_values = check_values
        data.auto_watermark = self.auto_watermark_check.isChecked()
        if not self._usage_auto:
            data.usage_note = self.usage_edit.toPlainText().strip()
        return data

    def _restore_form(self, payload: dict) -> None:
        data = engine.CertificateData.from_dict(payload)
        previous = self._suspend_signals
        self._suspend_signals = True
        try:
            for key, edit in {**self.number_edits, **self.text_edits, **self._wm_edits,
                              **self._cert_edits, **self.date_edits,
                              **self.sign_edits}.items():
                edit.setText(getattr(data, key, ""))
            for group in engine.CHECK_GROUPS:
                widgets = self._checkbox_widgets.get(group["key"], {})
                selected = {
                    "art_type": data.art_type,
                    "cert_status": data.status,
                    "sample_format": data.sample_format,
                    "image_format": data.image_format,
                }.get(group["key"])
                for item, widget in widgets.items():
                    if group["key"] == "watermark_items":
                        widget.setChecked(item in data.watermark_items)
                    elif group["key"] == "usage_places":
                        widget.setChecked(item in data.usage_places)
                    else:
                        widget.setChecked(item == selected)
            for group_key, spec in _OTHER_EDITS.items():
                field_key = spec[0]
                self._other_edits[field_key].setText(getattr(data, field_key, ""))
            self._usage_auto = not bool(payload.get("usage_note"))
            self.usage_edit.setPlainText(data.usage_note)
            self._sync_other_edits()
            self.date_edits["renew_until"].setEnabled(data.status == "已续签")
            start = self.date_edits["period_start"].text()
            end = self.date_edits["period_end"].text()
            expected = ""
            parsed = QDate.fromString(start, "yyyy-MM-dd") if start else QDate()
            if parsed.isValid():
                expected = parsed.addYears(3).toString("yyyy-MM-dd")
            self._auto_end = not end or end == expected
        finally:
            self._suspend_signals = previous

    def _sync_other_edits(self) -> None:
        """「其他」说明输入框只在对应项被勾选时可用。"""
        for group_key, spec in _OTHER_EDITS.items():
            field_key = spec[0]
            edit = self._other_edits.get(field_key)
            if edit is None:
                continue
            group = engine.CHECK_GROUP_BY_KEY.get(group_key) or {}
            widgets = self._checkbox_widgets.get(group_key, {})
            checked = any(widget.isChecked() for item, widget in widgets.items() if item == "其他")
            if not checked and group.get("single"):
                checked = self._selected(group_key) == "其他"
            edit.setEnabled(checked)

    # ==================== 必填校验 ====================

    def _required_widgets(self) -> dict[str, QWidget]:
        """必填字段 → 对应控件（用于加红框）。"""
        widgets: dict[str, QWidget] = {}
        for key in _REQUIRED_KEYS:
            for table in (self.number_edits, self.text_edits, self._wm_edits,
                          self._cert_edits, self.date_edits, self.sign_edits):
                if key in table:
                    widgets[key] = table[key]
                    break
        for key, group_key in (("art_type", "art_type"),
                               ("sample_format", "sample_format"),
                               ("image_format", "image_format")):
            box = self._checkbox_group_boxes.get(group_key)
            if box is not None:
                widgets[key] = box
        if "renew_until" in self.date_edits:
            widgets["renew_until"] = self.date_edits["renew_until"]
        return widgets

    def _set_invalid(self, widget: QWidget, invalid: bool) -> None:
        if isinstance(widget, QGroupBox):
            base = self._group_base_qss.get(widget, "")
            widget.setStyleSheet(
                base + (f"\nQGroupBox {{ border: 2px solid {_danger()}; }}" if invalid else ""))
        else:
            widget.setStyleSheet(_invalid_qss() if invalid else "")

    def _validate_required(self) -> list[str]:
        """按必填清单给未填字段加红框，返回缺失项中文名。"""
        missing = engine.required_missing(self._collect_data())
        keys = {key for key, _label in missing}
        for key, widget in self._required_widgets().items():
            self._set_invalid(widget, key in keys)
        return [label for _key, label in missing]

    def _confirm_required(self, data: engine.CertificateData) -> bool:
        """必填项缺失时弹确认；确认后仍允许生成（红框已标出）。"""
        missing = engine.required_missing(data)
        self._validate_required()
        if not missing:
            return True
        names = "\n".join(f"· {label}" for _key, label in missing[:12])
        if len(missing) > 12:
            names += f"\n… 共 {len(missing)} 项"
        return QMessageBox.question(
            self, "必填项未填",
            f"还有 {len(missing)} 项必填未填（已标红）：\n\n{names}\n\n仍要继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def _restore_matched_marks(self, checks: dict) -> None:
        for group in engine.CHECK_GROUPS:
            key = group["key"]
            matched = list(checks.get(key) or [])
            if group["single"]:
                matched = matched[:1]
            widgets = self._checkbox_widgets.get(key, {})
            for item, widget in widgets.items():
                hit = item in matched
                widget.setText(f"{item}（已匹配）" if hit else item)
                widget.setToolTip("文档中已勾选，请复核" if hit else "")
                if hit:
                    widget.setChecked(True)
            label = self._matched_labels.get(key)
            if label is not None:
                label.setText(f"自动匹配 {len(matched)}/{len(group['items'])} 项"
                              if matched else "文档中未匹配到勾选")
        self._sync_other_edits()
        self.date_edits["renew_until"].setEnabled(self._selected("cert_status") == "已续签")
        matched_groups = sum(1 for group in engine.CHECK_GROUPS if checks.get(group["key"]))
        self.left_tabs.setTabText(
            1, f"复选框复核 {matched_groups}/{len(engine.CHECK_GROUPS)}")

    def _apply_parse(self, result: engine.ParseResult) -> None:
        self._suspend_signals = True
        try:
            self._restore_form(result.fields.to_dict())
            self._restore_matched_marks(result.checks)
            if result.fields.art_type:
                self._apply_type_usage(result.fields.art_type)
        finally:
            self._suspend_signals = False
        self._maybe_auto_number()
        self._notes = list(result.notes)
        self.source_file_label.setText(result.source or "已导入")
        self.source_label.setText(f"解析完成 · {result.matched_summary()}")
        self._refresh_notes()
        self._refresh_preview()

    def _refresh_notes(self) -> None:
        data = self._collect_data()
        required = [label for _key, label in engine.required_missing(data)]
        if required:
            text = f"必填未填 {len(required)} 项：" + "、".join(required[:6])
            if len(required) > 6:
                text += "…"
            self.notes_label.setStyleSheet(f"color: {_danger()}; font-size: 11px;")
            self.notes_label.setText(text)
            return
        lines = list(self._notes) or []
        if not lines:
            self.notes_label.setText("字段齐全，可直接生成")
            self.notes_label.setStyleSheet("color: palette(link); font-size: 11px;")
            return
        self.notes_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.notes_label.setText("\n".join(f"· {line}" for line in lines))

    # ==================== 交互 ====================

    def _on_date_changed(self, key: str) -> None:
        """授权起始变动时，截止默认跟随「+3 年」；手动改截止后不再跟随。"""
        if self._suspend_signals:
            return
        if key == "period_end":
            self._auto_end = False
            return
        if key != "period_start":
            return
        start = self.date_edits["period_start"].text()
        end_field = self.date_edits["period_end"]
        if not start:
            return
        if end_field.text() and not self._auto_end:
            return
        parsed = QDate.fromString(start, "yyyy-MM-dd")
        if not parsed.isValid():
            return
        previous = self._suspend_signals
        self._suspend_signals = True
        try:
            end_field.setText(parsed.addYears(3).toString("yyyy-MM-dd"))
        finally:
            self._suspend_signals = previous
        self._auto_end = True
        self._on_field_changed()

    def _maybe_auto_number(self) -> None:
        """内部认证号为空时按所选年份历史序号自动补一个；已有编号则核对校验码。"""
        edit = self.number_edits.get("archive_no")
        if edit is None:
            return
        if not edit.text().strip():
            self._apply_auto_number()
            return
        self._sync_fingerprint()

    def _selected_year(self) -> int | None:
        """编号年份：0 表示自动（本机当前年份）。"""
        value = self.year_combo.currentData()
        return int(value) if value else None

    def _restore_year(self, value) -> None:
        try:
            year = int(value or 0)
        except (TypeError, ValueError):
            year = 0
        index = self.year_combo.findData(year)
        if index < 0:
            return
        self.year_combo.blockSignals(True)
        try:
            self.year_combo.setCurrentIndex(index)
        finally:
            self.year_combo.blockSignals(False)

    def _on_year_changed(self, *_args) -> None:
        """切换编号年份后按该年份已有证书重新顺延序号。"""
        if self._suspend_signals:
            return
        self._apply_auto_number()

    def _apply_auto_number(self) -> None:
        """按所选年份（自动=本机年份）生成 6 位序号 + 校验码。"""
        numbers = engine.suggest_numbers(
            year=self._selected_year(), existing=self._existing_codes(),
            data=self._collect_data(),
        )
        edit = self.number_edits.get("archive_no")
        if edit is None:
            return
        previous = self._suspend_signals
        self._suspend_signals = True
        try:
            edit.setText(numbers.get("archive_no", ""))
        finally:
            self._suspend_signals = previous
        self._on_field_changed()

    def _sync_fingerprint(self) -> None:
        """作品信息改动后重算编号末尾的校验码（只处理 6 位序号的新编号）。"""
        edit = self.number_edits.get("archive_no")
        if edit is None:
            return
        current = edit.text().strip()
        if not engine.is_managed_archive_no(current):
            return
        wanted = engine.with_fingerprint(current, self._collect_data())
        if not wanted or wanted == current:
            return
        previous = self._suspend_signals
        self._suspend_signals = True
        try:
            edit.setText(wanted)
        finally:
            self._suspend_signals = previous

    def _on_field_changed(self, *_args) -> None:
        if self._suspend_signals:
            return
        self._sync_fingerprint()
        self._validate_required()
        self._refresh_notes()
        self._schedule_preview()
        self._save_timer.start()

    def _on_usage_edited(self) -> None:
        if self._suspend_signals:
            return
        self._usage_auto = False
        self._schedule_preview()
        self._save_timer.start()

    def _on_usage_reset(self) -> None:
        art_type = self._selected("art_type")
        self._suspend_signals = True
        try:
            self.usage_edit.setPlainText(engine.ART_TYPE_USAGE.get(art_type, engine.DEFAULT_USAGE))
        finally:
            self._suspend_signals = False
        self._usage_auto = True
        self._schedule_preview()

    def _on_check_toggled(self, checked: bool) -> None:
        sender = self.sender()
        if self._suspend_signals:
            return
        # 单选组：新勾选时清掉同组其他项；单选同样允许全部取消
        for group in engine.CHECK_GROUPS:
            if not group["single"]:
                continue
            widgets = self._checkbox_widgets.get(group["key"], {})
            if sender not in widgets.values():
                continue
            item = next(name for name, widget in widgets.items() if widget is sender)
            if checked:
                self._suspend_signals = True
                try:
                    for other_item, widget in widgets.items():
                        if widget is not sender:
                            widget.setChecked(False)
                finally:
                    self._suspend_signals = False
            if group["key"] == "art_type":
                is_other = item == "其他"
                if checked:
                    self._apply_type_usage(item)
            if group["key"] == "cert_status":
                self.date_edits["renew_until"].setEnabled(checked and item == "已续签")
            break
        self._sync_other_edits()
        self._validate_required()
        self._schedule_preview()
        self._save_timer.start()

    def _apply_type_usage(self, art_type: str) -> None:
        """勾选作品类型后自动匹配用途摘要（用户手改过则不覆盖）。"""
        if not self._usage_auto:
            return
        text = engine.ART_TYPE_USAGE.get(art_type, engine.DEFAULT_USAGE)
        if art_type == "其他":
            text = engine.DEFAULT_USAGE
        if self.usage_edit.toPlainText() != text:
            self._suspend_signals = True
            try:
                self.usage_edit.setPlainText(text)
            finally:
                self._suspend_signals = False

    def _selected(self, key: str) -> str:
        for item, widget in self._checkbox_widgets.get(key, {}).items():
            if widget.isChecked():
                return item
        return ""

    def _schedule_preview(self) -> None:
        if hasattr(self, "_preview_timer"):
            self._preview_timer.start()

    # ==================== 预览 ====================

    def _refresh_preview(self) -> None:
        data = self._collect_data()
        self._validate_required()
        try:
            self._preview_pages = engine.render_pages(data, self._images)
        except Exception as exc:  # 预览失败不应打断录入
            self.preview_status.setText(f"预览失败：{exc}")
            return
        self._scale_preview()
        required = [label for _key, label in engine.required_missing(data)]
        if required:
            text = f"必填待补齐 {len(required)} 项：" + "、".join(required[:5])
            if len(required) > 5:
                text += "…"
            self.preview_status.setText(text)
        else:
            mark = " · 自动水印" if data.auto_watermark and data.watermark_text() else ""
            self.preview_status.setText(
                f"可生成 · {data.archive_no or '未编号'} · {len(self._images)} 张图片{mark}"
            )
        self.preview_status.setToolTip(f"输出目录：{self._out_dir}")

    def _preview_target_width(self) -> int:
        """按缩放选项算出预览像素宽度；适配宽度时跟随预览区实际宽度。"""
        mode = self.preview_scale.currentData() if hasattr(self, "preview_scale") else "fit"
        if mode == "fit":
            width = self._preview_scroll.viewport().width() - 28
            return max(_PREVIEW_MIN_WIDTH, min(_PREVIEW_MAX_WIDTH, width))
        return int(_PREVIEW_BASE_WIDTH * float(mode))

    def _scale_preview(self, *_args) -> None:
        """把已渲染的两页缩放到当前比例（不重新排版，切换很快）。"""
        if not self._preview_pages or not hasattr(self, "preview_scale"):
            return
        width = self._preview_target_width()
        for label, page in zip(self.preview_labels, self._preview_pages):
            height = max(1, int(page.height * width / page.width))
            scaled = page.resize((width, height), PILImage.LANCZOS)
            buffer = scaled.tobytes()
            image = QImage(buffer, scaled.width, scaled.height,
                           scaled.width * 3, QImage.Format_RGB888).copy()
            label.setPixmap(QPixmap.fromImage(image))
            # 允许滚动：pixmap 比可视区宽时出现横向滚动条
            label.setMinimumWidth(image.width())
            label.setMinimumHeight(image.height())

    def resizeEvent(self, event):  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if hasattr(self, "_preview_scale_timer") and self._preview_pages:
            self._preview_scale_timer.start()

    # ==================== 图片 ====================

    def _refresh_image_list(self) -> None:
        self.image_list.clear()
        for index, path in enumerate(self._images, start=1):
            self.image_list.addItem(f"{index:02d}  {path.name}")

    def _on_add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择作品图 / 水印样本", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;所有文件 (*.*)",
        )
        if not paths:
            return
        added = 0
        for item in paths:
            path = Path(item)
            if path.is_file() and path not in self._images and len(self._images) < engine.MAX_IMAGES:
                self._images.append(path)
                added += 1
        if len(self._images) >= engine.MAX_IMAGES and added:
            self.app.toast(f"最多 {engine.MAX_IMAGES} 张，其余未加入", "warning")
        self._refresh_image_list()
        self._schedule_preview()
        self._save_timer.start()

    def _on_remove_images(self) -> None:
        rows = sorted({index.row() for index in self.image_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._images):
                self._images.pop(row)
        self._refresh_image_list()
        self._schedule_preview()
        self._save_timer.start()

    def _on_clear_images(self) -> None:
        self._images.clear()
        self._refresh_image_list()
        self._schedule_preview()
        self._save_timer.start()

    def _move_image(self, delta: int) -> None:
        rows = sorted({index.row() for index in self.image_list.selectedIndexes()})
        if not rows:
            return
        for row in (rows if delta < 0 else reversed(rows)):
            target = row + delta
            if 0 <= target < len(self._images):
                self._images[row], self._images[target] = self._images[target], self._images[row]
        self._refresh_image_list()
        new_rows = [max(0, min(len(self._images) - 1, row + delta)) for row in rows]
        for row in new_rows:
            self.image_list.item(row).setSelected(True)
        self._schedule_preview()
        self._save_timer.start()

    # ==================== 历史记录 ====================

    def _refresh_history(self) -> None:
        try:
            self._history_records = certificate_history.load_records()
        except OSError:
            self._history_records = []
        self._filter_history()

    def _filter_history(self) -> None:
        if not hasattr(self, "history_list"):
            return
        query = self.history_search.text().strip().lower()
        self.history_list.clear()
        for record in self._history_records:
            haystack = " ".join(
                str(record.get(key) or "")
                for key in ("title", "archive_no", "author_name")
            ).lower()
            if query and query not in haystack:
                continue
            title = record.get("title") or "（未命名作品）"
            code = record.get("archive_no") or "未编号"
            item = QListWidgetItem(
                f"{title}\n{code} · {record.get('saved_at') or ''} · "
                f"{record.get('status') or '有效'}"
            )
            item.setData(Qt.UserRole, str(record.get("id") or ""))
            self.history_list.addItem(item)
        if self.history_list.count() == 0:
            placeholder = QListWidgetItem("暂无历史记录" if not query else "没有匹配的记录")
            placeholder.setFlags(Qt.NoItemFlags)
            self.history_list.addItem(placeholder)

    def _current_record(self) -> dict | None:
        item = self.history_list.currentItem()
        if item is None:
            return None
        record_id = item.data(Qt.UserRole)
        if not record_id:
            return None
        return next(
            (record for record in self._history_records
             if str(record.get("id")) == str(record_id)),
            None,
        )

    def _on_history_load(self) -> None:
        record = self._current_record()
        if record is None:
            self.app.toast("先选中一条历史记录", "warning")
            return
        self._restore_form(record.get("data") or {})
        self._refresh_notes()
        self._refresh_preview()
        self._save_timer.start()
        self.left_tabs.setCurrentIndex(0)
        self.app.toast("已载入历史数据（不含图片）")

    def _on_history_delete(self) -> None:
        record = self._current_record()
        if record is None:
            self.app.toast("先选中一条历史记录", "warning")
            return
        if QMessageBox.question(
            self, "删除历史记录",
            f"删除「{record.get('title') or '未命名作品'}」这条记录？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        certificate_history.delete_record(str(record.get("id")))
        self._refresh_history()

    def _on_history_clear(self) -> None:
        if not self._history_records:
            return
        if QMessageBox.question(
            self, "清空历史记录",
            f"清空全部 {len(self._history_records)} 条历史记录？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        certificate_history.clear_records()
        self._refresh_history()

    # ==================== 文档导入 ====================

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入协议文档 / 填写表", "",
            "协议与填写表 (*.txt *.md *.docx *.pdf *.xlsx *.xlsm);;所有文件 (*.*)",
        )
        if not path:
            return
        self.source_label.setText("正在解析…")

        def work():
            return engine.parse_file(path)

        def done(result, error):
            if error or not result:
                self.source_label.setText("解析失败")
                self.app.toast(f"解析失败：{error}", "error")
                return
            self._apply_parse(result)

        run_bg(work, done, owner=self)

    def _on_paste(self) -> None:
        dialog = TextPasteDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        text = dialog.text().strip()
        if not text:
            self.app.toast("没有粘贴内容", "warning")
            return
        self._apply_parse(engine.parse_document(text, source="粘贴文本"))

    def _on_auto_number(self) -> None:
        self._apply_auto_number()

    def _existing_codes(self) -> list[str]:
        codes: list[str] = []
        try:
            for path in self._out_dir.glob("*_内部认证证书*"):
                codes.append(path.name)
        except OSError:
            pass
        try:
            for record in certificate_history.load_records():
                codes.append(str(record.get("archive_no") or ""))
        except OSError:
            pass
        return codes

    def _on_pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择证书输出目录", str(self._out_dir))
        if not path:
            return
        self._out_dir = Path(path)
        self.dir_btn.setToolTip(str(self._out_dir))
        self._save_timer.start()
        self._refresh_preview()

    # ==================== 导出 ====================

    def _on_export(self) -> None:
        data = self._collect_data()
        missing = engine.missing_fields(data)
        if missing:
            QMessageBox.warning(self, "还差必填项", "请先补齐：\n\n" + "\n".join(f"· {item}" for item in missing))
            return
        if not self._confirm_required(data):
            return
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.app.toast(f"输出目录不可写：{exc}", "error")
            return
        images = list(self._images)
        out_dir = self._out_dir
        self.export_btn.setEnabled(False)
        self.app.toast("正在生成证书…")

        def work():
            return engine.export_certificate(data, images, out_dir)

        def done(result, error):
            self.export_btn.setEnabled(True)
            if error or result is None:
                self.app.toast(f"生成失败：{error}", "error")
                return
            folder = Path(result.folder_path or out_dir)
            self.app.toast(
                f"已生成 {result.page_count} 页 PNG + PDF，并打包 zip"
                + (f"，水印件 {len(result.watermarked_paths)} 张"
                   if result.watermarked_paths else "")
            )
            try:
                certificate_history.save_record(data)
            except OSError:
                pass
            self._refresh_history()
            self._save_config()
            extra = (f"\n已加水印作品图：水印件/ 共 {len(result.watermarked_paths)} 张"
                     if result.watermarked_paths else "")
            answer = QMessageBox.question(
                self, "生成完成",
                f"证书文件夹：\n{folder}\n\n"
                f"附属压缩包：\n{result.zip_path}\n\n"
                f"2 页 PNG（300dpi）+ 合并 PDF。{extra}\n是否打开证书文件夹？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

        run_bg(work, done, owner=self)

    def _on_export_docx(self) -> None:
        """导出 Word：证书页 + 完整协议（正文 + 附件一 + 附件二）。"""
        data = self._collect_data()
        missing = engine.missing_fields(data)
        if missing:
            QMessageBox.warning(
                self, "还差必填项",
                "请先补齐：\n\n" + "\n".join(f"· {item}" for item in missing))
            return
        if not self._confirm_required(data):
            return
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            path = engine.export_docx(data, self._out_dir)
        except Exception as exc:  # noqa: BLE001 - 导出失败直接提示
            self.app.toast(f"导出 Word 失败：{exc}", "error")
            return
        self.app.toast(f"已导出 Word 到证书文件夹：{Path(path).name}", "success")
        self._save_config()
