"""实用工具 · 套版封面制作页：底图 + 标题/日期/备注三段文字的封面生成器。

核心设计：
- 渲染：QPainter + QImage（Antialiasing + TextAntialiasing），系统级字体渲染
- 预览/导出分离：预览固定 960x540 小画布轻量重绘，导出按所选分辨率完整重渲染
- 实时渲染：参数变更走 10ms 节流（活跃期间不重启防饿死），文字/滑条/拖动即时跟手不卡顿
- 全参数比例化：字号 = px/1080、位置 x_ratio/y_ratio 按画布比例存储，
  1080p/2K/4K 分辨率切换布局不乱
- 文字自动换行（不超过 70% 画布宽）+ 溢出自动缩小字号（最小阈值）
- 模板：JSON 存三字段样式与位置比例 + 记忆当时所用底图路径（不保存文字内容）
- 底图选择：方块平铺缩略图浏览全部扫描图片，指定哪张作为底图（蒙版）
- 交互规范：下拉/滑条/数字框均不响应滚轮改值（滚轮专用于滚动页面）；
  字体下拉可直接输入字体名识别（无效字体名自动回退）；颜色选择用内置
  主题色板（预设色 + 十六进制 / RGB 输入），不弹系统原生黑窗
- 导出：PNG / JPG（质量 60~100）
- 集成：主程序经 create_cover_module() 获取 QWidget 实例（亦可作为普通页面注册）
"""

import json
import os
import random
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QImage, QImageReader,
    QPainter, QPalette, QPen, QPixmap, QTextLayout, QTextOption,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFontComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QListView,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from toolbox.config_manager import get_config_dir, get_export_dir

from gui_qt.common import icon_btn_style, post_to_main, run_bg
from gui_qt.help_overlay import attach_help_overlay
from gui_qt.pages.base import BasePage
from gui_qt.theme import is_dark

# ==================== 常量 ====================

FIELD_KEYS = ("title", "date", "remark")
FIELD_LABELS = {"title": "📌 标题", "date": "📅 日期", "remark": "📝 备注"}
FIELD_PLACEHOLDERS = {
    "title": "输入标题文字（预览区可直接拖动定位）",
    "date": "输入日期文字，如 2026-09-05",
    "remark": "多行备注，支持自动换行",
}

DEFAULT_FONT_FAMILY = "Microsoft YaHei"
CANVAS_REF_H = 1080          # 字号比例基准高度（font_size_ratio = px / 1080）
PREVIEW_W, PREVIEW_H = 960, 540
TEXT_MAX_WIDTH_RATIO = 0.7    # 文字块最大宽度占画布比例
TEXT_MAX_HEIGHT_RATIO = 0.88 # 触发自动缩小字号的高度阈值
MIN_FONT_PX = 12
FONT_SIZE_MIN, FONT_SIZE_MAX = 20, 300   # 滑条范围（1080p 基准 px）

FIT_CROP = "crop"            # 裁剪填充（KeepAspectRatioByExpanding）
FIT_CONTAIN = "contain"      # 完整包含（KeepAspectRatio 留白）
FIT_ITEMS = [("裁剪填充(默认)", FIT_CROP), ("完整包含(留白)", FIT_CONTAIN)]
ALIGN_ITEMS = [("居中", "center"), ("左对齐", "left"), ("右对齐", "right")]

EXPORT_RESOLUTIONS = ["1920x1080", "2560x1440", "3840x2160", "1280x720"]
JPG_QUALITY_MIN, JPG_QUALITY_MAX, JPG_QUALITY_DEFAULT = 60, 100, 95

CANVAS_BG_FALLBACK = "#181a20"   # 无底图/留白时的画布底色
PREVIEW_LETTERBOX = "#101216"   # 预览组件画框底色（深浅主题固定）
DRAG_HINT_COLOR = "#f2c14e"

# 滚轮拦截/弹窗主题控件（见 NoWheelMixin / force_combo_popup_theme）
COMBO_POPUP_BG = {"light": "#ffffff", "dark": "#23262f"}
COMBO_POPUP_FG = {"light": "#2f3440", "dark": "#c6cbd6"}
COMBO_POPUP_SEL = {"light": "#2a6a9c", "dark": "#2a5a8c"}

TEMPLATE_VERSION = 1
_CONFIG_SECTION = "cover_maker"

HELP_HTML = (
    "<p><b>套版封面制作：随机底图 + 标题/日期/备注三段文字，快速产出静态封面。</b></p>"
    "<p>· <b>底图</b>：从底图文件夹递归扫描随机抽取；「上传图片」临时替换当前底图；"
    "适配模式支持裁剪填充（默认）或完整包含（留白）</p>"
    "<p>· <b>文字</b>：三组字段独立设置字体/字号/颜色/加粗/斜体/对齐；"
    "「同步样式」可按字体、颜色、字号单项同步到全部字段</p>"
    "<p>· <b>定位</b>：预览区直接拖动文字块；「重置文字位置」恢复三行垂直居中；"
    "Y轴微调整体上下平移（画布高度比例）</p>"
    "<p>· <b>分辨率</b>：所有字号与位置按画布比例存储，预览 960x540，"
    "导出可切 1080p/2K/4K 不乱版；超长文字自动换行（不超过 70% 画布宽）并自动缩小防溢出</p>"
    "<p>· <b>导出</b>：PNG 无损 / JPG 质量可调（60~100），保存到 download/套版封面/</p>"
    "<p>· <b>模板</b>：保存三字段样式与位置比例，并记忆当时所用底图（文件仍在时加载"
    "自动切换），不保存文字内容；存放在 config/cover_templates/，字体缺失自动回退并提示</p>"
    "<p>· <b>选择底图</b>：方块平铺缩略图浏览底图文件夹全部图片，按名称筛选，"
    "指定哪张作为当前底图（蒙版）</p>"
    "<p>· <b>交互规范</b>：下拉/滑条/数字框不响应滚轮改值（滚轮专用于滚动页面）；"
    "字体下拉可直接输入字体名回车识别，输入无效字体名自动回退原字体；"
    "颜色选择为内置主题色板（48 预设色双击直接应用 + 十六进制 / RGB 输入），"
    "跟随应用明暗主题，不弹系统原生黑窗</p>"
)


# ==================== 数据模型 ====================

@dataclass
class FieldState:
    """单个文字字段的完整状态（全部参数比例化存储）。"""

    text: str = ""
    font_family: str = DEFAULT_FONT_FAMILY
    font_size_ratio: float = 48.0 / CANVAS_REF_H   # 字号 = 画布高度 x 比例
    color: str = "#ffffff"
    bold: bool = False
    italic: bool = False
    alignment: str = "center"   # left/center/right
    x_ratio: float = 0.5        # 文字块水平锚点（对齐方式决定锚点含义）
    y_ratio: float = 0.5        # 文字块垂直中心

    def to_dict(self, include_text: bool = True) -> Dict[str, Any]:
        d = {
            "font_family": self.font_family,
            "font_size_ratio": round(float(self.font_size_ratio), 6),
            "color": self.color,
            "bold": bool(self.bold),
            "italic": bool(self.italic),
            "alignment": self.alignment,
            "x_ratio": round(float(self.x_ratio), 6),
            "y_ratio": round(float(self.y_ratio), 6),
        }
        if include_text:
            d["text"] = self.text
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base: Optional["FieldState"] = None) -> "FieldState":
        st = base if base is not None else cls()
        st.font_family = str(data.get("font_family") or st.font_family)
        try:
            st.font_size_ratio = float(data.get("font_size_ratio", st.font_size_ratio))
        except (TypeError, ValueError):
            pass
        color = str(data.get("color") or st.color)
        st.color = color if QColor(color).isValid() else st.color
        st.bold = bool(data.get("bold", st.bold))
        st.italic = bool(data.get("italic", st.italic))
        alignment = str(data.get("alignment") or st.alignment)
        st.alignment = alignment if alignment in ("left", "center", "right") else st.alignment
        for key in ("x_ratio", "y_ratio"):
            try:
                setattr(st, key, float(data.get(key, getattr(st, key))))
            except (TypeError, ValueError):
                pass
        if "text" in data:
            st.text = str(data.get("text") or "")
        return st


def default_states() -> Dict[str, FieldState]:
    """默认三字段布局：三行垂直居中。"""
    return {
        "title": FieldState(font_size_ratio=88.0 / CANVAS_REF_H, y_ratio=0.40),
        "date": FieldState(font_size_ratio=44.0 / CANVAS_REF_H, y_ratio=0.54),
        "remark": FieldState(font_size_ratio=32.0 / CANVAS_REF_H, y_ratio=0.67),
    }


# ==================== 渲染引擎 ====================

def _supported_image_exts() -> set:
    exts = set()
    for fmt in QImageReader.supportedImageFormats():
        try:
            exts.add(bytes(fmt).decode("utf-8").lower())
        except Exception:
            continue
    exts.update({"jpg", "jpeg"})
    return exts


def _resolve_font_family(family: str) -> str:
    """字体不存在时回退默认字体（模板加载/配置恢复用）。"""
    text = str(family or "").strip()
    if not text:
        return DEFAULT_FONT_FAMILY
    try:
        families = set(QFontDatabase.families())
    except Exception:
        return text
    if text in families:
        return text
    return DEFAULT_FONT_FAMILY


def _font_family_exists(family: str) -> bool:
    """字体下拉输入校验：字体名是否真实存在。

    环境无法枚举字体（如离屏平台字体库为空）时按存在处理，保持输入可用。
    """
    try:
        families = QFontDatabase.families()
    except Exception:
        return True
    if not families:
        return True
    return family in families


def _build_font(state: FieldState, canvas_h: int) -> QFont:
    px = max(MIN_FONT_PX, int(round(state.font_size_ratio * canvas_h)))
    font = QFont(state.font_family)
    font.setPixelSize(px)
    font.setBold(state.bold)
    font.setItalic(state.italic)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _measure_block(text: str, font: QFont, max_w: float
                   ) -> Tuple[QTextLayout, List, float, float]:
    """按最大宽度自动换行排版，返回 (layout, 行列表, 块宽, 块高)。"""
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.setCacheEnabled(True)
    lines: List = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max_w)
        lines.append(line)
    layout.endLayout()
    block_w = 0.0
    total_h = 0.0
    for line in lines:
        block_w = max(block_w, line.naturalTextWidth())
        total_h += line.height()
    return layout, lines, block_w, total_h


def render_cover(width: int, height: int, bg: Optional[QImage], fit_mode: str,
                 states: Dict[str, FieldState], y_shift_ratio: float = 0.0,
                 draw_hint: bool = True) -> Tuple[QImage, Dict[str, QRectF]]:
    """渲染封面画布，返回 (图像, 各字段文字块几何区域)。

    bg 为已按画布尺寸缓存缩放的底图（或原始图，内部二次等比缩放为幂等操作）。
    geoms 用于预览区拖动命中测试（画布坐标系）。
    """
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(CANVAS_BG_FALLBACK))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # 1. 底图
    if bg is not None and not bg.isNull():
        aspect = (Qt.AspectRatioMode.KeepAspectRatioByExpanding if fit_mode == FIT_CROP
                  else Qt.AspectRatioMode.KeepAspectRatio)
        if bg.width() == width and bg.height() == height:
            painter.drawImage(0, 0, bg)
        else:
            scaled = bg.scaled(width, height, aspect,
                               Qt.TransformationMode.SmoothTransformation)
            painter.drawImage((width - scaled.width()) // 2,
                              (height - scaled.height()) // 2, scaled)
    elif draw_hint:
        hint_font = QFont(DEFAULT_FONT_FAMILY)
        hint_font.setPixelSize(max(14, height // 36))
        painter.setFont(hint_font)
        painter.setPen(QColor("#8a93a5"))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter,
                         "暂无底图 · 点击「随机底图」或「上传图片」添加")

    # 2. 三段文字
    geoms: Dict[str, QRectF] = {}
    max_w = float(int(width * TEXT_MAX_WIDTH_RATIO))
    for key in FIELD_KEYS:
        state = states.get(key)
        if state is None or not (state.text or "").strip():
            continue
        font = _build_font(state, height)
        layout, lines, block_w, total_h = _measure_block(state.text, font, max_w)
        # 溢出自动缩小字号（最小阈值兜底）
        tries = 0
        while (total_h > height * TEXT_MAX_HEIGHT_RATIO
               and font.pixelSize() > MIN_FONT_PX and tries < 24):
            font.setPixelSize(max(MIN_FONT_PX, int(font.pixelSize() * 0.9)))
            layout, lines, block_w, total_h = _measure_block(state.text, font, max_w)
            tries += 1

        # 位置锚点：x_ratio 为水平锚（左对齐=左边线 / 居中=中心 / 右对齐=右边线），
        # y_ratio 为块垂直中心；再叠加整体 Y 轴微调
        ax = state.x_ratio * width
        ay = (state.y_ratio + y_shift_ratio) * height
        if state.alignment == "left":
            x0 = ax
        elif state.alignment == "right":
            x0 = ax - block_w
        else:
            x0 = ax - block_w / 2.0
        y0 = ay - total_h / 2.0
        # 边界钳制：文字块保持在画布内
        if block_w <= width:
            x0 = min(max(x0, 4.0), width - block_w - 4.0)
        else:
            x0 = (width - block_w) / 2.0
        if total_h <= height:
            y0 = min(max(y0, 4.0), height - total_h - 4.0)
        else:
            y0 = (height - total_h) / 2.0

        # 逐行设置位置实现块内对齐，随后整体平移绘制
        cum = 0.0
        for line in lines:
            lw = line.naturalTextWidth()
            if state.alignment == "left":
                dx = 0.0
            elif state.alignment == "right":
                dx = block_w - lw
            else:
                dx = (block_w - lw) / 2.0
            line.setPosition(QPointF(dx, cum))
            cum += line.height()

        painter.setPen(QColor(state.color))
        layout.draw(painter, QPointF(x0, y0))
        geoms[key] = QRectF(x0, y0, block_w, total_h)

    painter.end()
    return image, geoms


# ==================== 配置 / 模板持久化 ====================

def _config_json_path() -> Path:
    return get_config_dir() / "config.json"


def _read_cover_config() -> Dict[str, Any]:
    try:
        with open(_config_json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        section = data.get(_CONFIG_SECTION)
        return dict(section) if isinstance(section, dict) else {}
    except Exception:
        return {}


def _write_cover_config(section: Dict[str, Any]) -> None:
    data: Dict[str, Any] = {}
    try:
        with open(_config_json_path(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        pass
    data[_CONFIG_SECTION] = section
    try:
        with open(_config_json_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _templates_dir() -> Path:
    d = get_config_dir() / "cover_templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize_template_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name).strip())
    cleaned = cleaned.strip("._")
    return cleaned[:60] or "模板"


def template_dict_from_states(states: Dict[str, FieldState]) -> Dict[str, Dict[str, Any]]:
    """模板负载：三字段样式与位置比例（不含底图与文字内容）。"""
    return {k: states[k].to_dict(include_text=False) for k in FIELD_KEYS if k in states}


def apply_template_to_states(states: Dict[str, FieldState],
                             data: Dict[str, Any]) -> List[str]:
    """把模板负载应用到 states（保留当前文字内容），返回警告列表。"""
    warnings: List[str] = []
    fields = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        return ["模板数据损坏：缺少 fields 字段"]
    for key in FIELD_KEYS:
        item = fields.get(key)
        if not isinstance(item, dict):
            warnings.append(f"{FIELD_LABELS.get(key, key)} 缺失，保留原样式")
            continue
        states[key] = FieldState.from_dict(item, base=states[key])
        family = _resolve_font_family(states[key].font_family)
        if family != states[key].font_family:
            warnings.append(f"{FIELD_LABELS.get(key, key)} 字体「{states[key].font_family}」"
                           f"不存在，回退 {family}")
            states[key].font_family = family
    return warnings


def _restore_states(data: Any) -> Dict[str, FieldState]:
    states = default_states()
    if isinstance(data, dict):
        for key in FIELD_KEYS:
            if isinstance(data.get(key), dict):
                states[key] = FieldState.from_dict(data[key], base=states[key])
                states[key].font_family = _resolve_font_family(states[key].font_family)
    return states


# ==================== 底图引擎 ====================

class BackgroundProvider:
    """底图文件夹递归扫描 + 随机抽取 + 按目标尺寸缩放缓存。"""

    def __init__(self, folder: Optional[Path]):
        self.folder = Path(folder) if folder else None
        self.files: List[Path] = []
        self._scaled_cache: "OrderedDict[Tuple[str, int, int, str], QImage]" = OrderedDict()
        self._original: Optional[QImage] = None
        self._original_path: Optional[str] = None
        self._last_pick: Optional[Path] = None
        self.scan()

    def scan(self) -> int:
        exts = _supported_image_exts()
        files: List[Path] = []
        if self.folder and Path(self.folder).is_dir():
            for root, _dirs, names in os.walk(self.folder):
                for name in names:
                    if Path(name).suffix.lstrip(".").lower() in exts:
                        files.append(Path(root) / name)
        self.files = files
        self._scaled_cache.clear()
        return len(files)

    def set_folder(self, folder: Path) -> int:
        self.folder = Path(folder)
        self._last_pick = None
        return self.scan()

    def random_path(self) -> Optional[Path]:
        if not self.files:
            return None
        pool = [f for f in self.files if f != self._last_pick] or self.files
        pick = random.choice(pool)
        self._last_pick = pick
        return pick

    def _load_original(self, path: Path) -> Optional[QImage]:
        key = str(path)
        if self._original_path == key and self._original is not None:
            return self._original
        img = QImage(key)
        if img.isNull() or img.width() <= 0 or img.height() <= 0:
            return None
        self._original = img
        self._original_path = key
        return img

    def scaled_background(self, path: Path, w: int, h: int, mode: str) -> Optional[QImage]:
        """按目标尺寸与适配模式返回缩放缓存底图（找不到/解码失败返回 None）。"""
        key = (str(path), w, h, mode)
        cached = self._scaled_cache.get(key)
        if cached is not None:
            return cached
        original = self._load_original(path)
        if original is None:
            return None
        aspect = (Qt.AspectRatioMode.KeepAspectRatioByExpanding if mode == FIT_CROP
                  else Qt.AspectRatioMode.KeepAspectRatio)
        scaled = original.scaled(w, h, aspect, Qt.TransformationMode.SmoothTransformation)
        self._scaled_cache[key] = scaled
        while len(self._scaled_cache) > 8:
            self._scaled_cache.popitem(last=False)
        return scaled


# ==================== 滚轮拦截 / 弹窗主题控件 ====================

def _combo_popup_colors():
    side = "dark" if is_dark() else "light"
    return COMBO_POPUP_BG[side], COMBO_POPUP_FG[side], COMBO_POPUP_SEL[side]


def _style_popup_view(view) -> None:
    """把下拉/补全列表视图按当前主题着色（autoFill + 调色板 + 内联 QSS 双保险）。"""
    bg, fg, sel = _combo_popup_colors()
    view.setAutoFillBackground(True)
    pal = view.palette()
    pal.setColor(view.backgroundRole(), QColor(bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(fg))
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(sel))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    view.setPalette(pal)
    view.setStyleSheet(
        f"QAbstractItemView {{ background: {bg}; color: {fg}; "
        f"selection-background-color: {sel}; selection-color: #ffffff; }}")


def force_combo_popup_theme(combo) -> None:
    """强制组合框下拉弹窗跟随应用主题。

    Windows 深色系统下 QSS 对弹窗容器可能只应用文字色不填充背景 →
    弹出黑窗面。这里每次打开前显式设置视图与容器的 autoFillBackground、
    调色板与内联样式，确保按当前主题绘制（所选用色值不在主题重映射
    表内，auto_remap 不会二次改写；每次 showPopup 重套，主题切换自愈）。
    """
    view = combo.view()
    _style_popup_view(view)
    container = view.parentWidget()
    if container is not None:
        container.setAutoFillBackground(True)
        cpal = container.palette()
        cpal.setColor(container.backgroundRole(), QColor(_combo_popup_colors()[0]))
        container.setPalette(cpal)


class NoWheelMixin:
    """滚轮不改变值：忽略滚轮事件让它冒泡给外层 QScrollArea 滚动页面。"""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(NoWheelMixin, QComboBox):
    def showPopup(self):
        force_combo_popup_theme(self)
        super().showPopup()


class NoWheelFontComboBox(NoWheelMixin, QFontComboBox):
    """字体下拉：滚轮不改值；可编辑（输入字体名回车即识别，无效名回退）；
    下拉弹窗与输入补全列表均强制跟随应用主题（补全列表不是 QComboBox
    子控件，全局 QSS 的 QComboBox QAbstractItemView 选择器匹配不到它，
    必须显式套色，否则深色系统下输入时弹黑窗）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self._comp_popup: Optional[QListView] = None
        comp = self.completer()
        if comp is not None:
            self._comp_popup = QListView()
            _style_popup_view(self._comp_popup)
            comp.setPopup(self._comp_popup)
        line = self.lineEdit()
        if line is not None:
            line.textEdited.connect(self._restyle_completer)

    def _restyle_completer(self, _text: str = ""):
        if self._comp_popup is not None:
            _style_popup_view(self._comp_popup)

    def showPopup(self):
        force_combo_popup_theme(self)
        self._restyle_completer()
        super().showPopup()


class NoWheelSpinBox(NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(NoWheelMixin, QDoubleSpinBox):
    pass


class NoWheelSlider(NoWheelMixin, QSlider):
    pass


# ==================== 底图选择对话框 ====================

class BackgroundPickerDialog(QDialog):
    """底图选择：统一方块平铺浏览扫描到的全部图片，指定哪张作为底图（蒙版）。

    每张图片占据固定尺寸的方块格（图标区内等比缩放居中），网格整齐不随
    图片宽高比参差；缩略图在后台线程逐张生成（QImageReader 按目标尺寸
    解码，避免整图解码大图），当前底图优先加载并预选中；支持按文件名
    实时筛选。
    """

    TILE = 168          # 方块图标区边长（逻辑像素）
    GRID_W = 184        # 格宽 = 图标 + 间距
    GRID_H = 216        # 格高 = 图标 + 两行文件名 + 间距

    def __init__(self, files: List[Path], current_path: Optional[Path],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._files = list(files)
        self._dpr = max(1.0, float(self.devicePixelRatioF()))
        self.setWindowTitle(f"选择底图（共 {len(self._files)} 张）")
        self.resize(1000, 640)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按文件名筛选（实时）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, 1)
        self.count_label = QLabel(f"{len(self._files)} 张")
        top.addWidget(self.count_label)
        v.addLayout(top)

        self.listw = QListWidget()
        self.listw.setViewMode(QListWidget.ViewMode.IconMode)
        self.listw.setIconSize(QSize(self.TILE, self.TILE))
        self.listw.setGridSize(QSize(self.GRID_W, self.GRID_H))
        self.listw.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.listw.setMovement(QListWidget.Movement.Static)
        self.listw.setSpacing(8)
        self.listw.setWordWrap(True)
        self.listw.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.listw.itemDoubleClicked.connect(lambda _item: self.accept())
        v.addWidget(self.listw, 1)

        hint = QLabel("双击方块或选中后点「使用此图」；缩略图在后台逐张生成")
        hint.setStyleSheet("font-size: 11px; color: #8a93a5;")
        v.addWidget(hint)

        btns = QHBoxLayout()
        use_btn = QPushButton("使用此图")
        use_btn.setDefault(True)
        use_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(use_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        v.addLayout(btns)

        current_row = -1
        cell = QSize(self.GRID_W, self.GRID_H)
        for i, path in enumerate(self._files):
            item = QListWidgetItem(path.name)
            # 显式占满整格：不依赖图标异步到达后的动态尺寸（图标未到时 item
            # 只有文字高度，曾把整格压成细条），选中框也完整覆盖方块
            item.setSizeHint(cell)
            item.setData(int(Qt.ItemDataRole.UserRole), str(path))
            self.listw.addItem(item)
            if current_path is not None and path == Path(current_path):
                current_row = i
        if current_row >= 0:
            self.listw.setCurrentRow(current_row)
        elif self._files:
            self.listw.setCurrentRow(0)
        self._start_thumbnails(current_row)

    # ---------- 缩略图后台生成 ----------

    def _start_thumbnails(self, priority_row: int):
        order = list(range(len(self._files)))
        if priority_row >= 0 and priority_row < len(order):
            order.remove(priority_row)
            order.insert(0, priority_row)

        def job():
            for i in order:
                post_to_main(self._set_icon, i, self._decode_thumb(self._files[i]))

        run_bg(job, lambda _r, _e: None)

    def _decode_thumb(self, path: Path) -> Optional[QImage]:
        """解码为方块缩略图：整图等比缩放后居中合成到 TILE×TILE 方形画布。

        每张缩略图都是统一正方形（与图标区同尺寸），网格因此完全整齐；
        按屏幕 DPR 放大解码保证高分屏清晰；解码失败返回 None。
        """
        try:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            side = int(round(self.TILE * self._dpr))
            size = reader.size()
            if size.isValid():
                scaled = size.scaled(side, side,
                                     Qt.AspectRatioMode.KeepAspectRatio)
                if scaled.width() > 0 and scaled.height() > 0:
                    reader.setScaledSize(scaled)
            img = reader.read()
            if img.isNull():
                return None
            canvas = QImage(side, side, QImage.Format.Format_ARGB32)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            x = (side - img.width()) // 2
            y = (side - img.height()) // 2
            painter.drawImage(x, y, img)
            painter.end()
            canvas.setDevicePixelRatio(self._dpr)
            return canvas
        except Exception:
            return None

    def _set_icon(self, index: int, img: Optional[QImage]):
        if img is None:
            return
        try:
            item = self.listw.item(index)
            if item is not None:
                item.setIcon(QIcon(QPixmap.fromImage(img)))
        except RuntimeError:
            pass  # 对话框已关闭（C++ 对象已销毁）

    # ---------- 筛选与选择 ----------

    def _apply_filter(self, text: str):
        needle = str(text).strip().lower()
        visible = 0
        for row in range(self.listw.count()):
            item = self.listw.item(row)
            match = not needle or needle in item.text().lower()
            item.setHidden(not match)
            if match:
                visible += 1
        self.count_label.setText(f"{visible}/{len(self._files)} 张")

    def selected_path(self) -> Optional[Path]:
        item = self.listw.currentItem()
        if item is None:
            return None
        return Path(str(item.data(int(Qt.ItemDataRole.UserRole))))


# ==================== 主题化颜色选择 ====================

# 48 预设色：灰阶 8 级 + 8 色相 × 5 明度档（深→浅）
PRESET_COLORS = [
    "#000000", "#262626", "#4d4d4d", "#737373", "#999999", "#bfbfbf", "#e6e6e6", "#ffffff",
    "#b71c1c", "#bf360c", "#f9a825", "#1b5e20", "#006064", "#0d47a1", "#4a148c", "#880e4f",
    "#d32f2f", "#e65100", "#fbc02d", "#2e7d32", "#00838f", "#1565c0", "#6a1b9a", "#ad1457",
    "#ef5350", "#ff9800", "#fdd835", "#4caf50", "#26c6da", "#42a5f5", "#ab47bc", "#f06292",
    "#ef9a9a", "#ffb74d", "#fff176", "#a5d6a7", "#80deea", "#90caf9", "#ce93d8", "#f48fb1",
    "#ffcdd2", "#ffe0b2", "#fff9c4", "#c8e6c9", "#b2ebf2", "#bbdefb", "#e1bee7", "#f8bbd0",
]


class _ColorSwatch(QWidget):
    """颜色小方块：paintEvent 自绘（不经 QSS，避免主题色值重映射改写显示颜色）。

    单击选中；双击选中并直接确定。
    """

    clicked = Signal(str)
    double_clicked = Signal(str)

    def __init__(self, color: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = QColor(color)
        self._hover = False
        self.setFixedSize(30, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self._color.name())

    def set_color(self, hex_color: str):
        color = QColor(hex_color)
        if color.isValid():
            self._color = color
            self.setToolTip(color.name())
            self.update()

    def color(self) -> QColor:
        return QColor(self._color)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(self._color)
        painter.setPen(QPen(QColor("#7a828f") if self._hover else QColor("#b8bfca"),
                           2 if self._hover else 1))
        painter.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 4, 4)
        painter.end()

    def enterEvent(self, _event):
        self._hover = True
        self.update()

    def leaveEvent(self, _event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._color.name())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._color.name())


class ColorPickerDialog(QDialog):
    """主题化文字颜色选择器：预设色板 + 十六进制 / RGB 输入识别。

    取代原生 QColorDialog（Windows 深色系统下弹黑窗）：整个对话框由全局
    明暗主题 QSS 渲染；色块自绘不参与主题重映射，显示颜色始终准确；
    RGB 数字框不响应滚轮（滚轮不改变值），输入即可识别。
    """

    def __init__(self, current: str = "#ffffff", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("选择文字颜色")
        self.setMinimumWidth(380)
        self._updating = False
        color = QColor(current)
        self._color = color if color.isValid() else QColor("#ffffff")

        v = QVBoxLayout(self)

        preset_box = QGroupBox("预设颜色（双击直接应用）")
        grid = QGridLayout(preset_box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        for i, hexc in enumerate(PRESET_COLORS):
            sw = _ColorSwatch(hexc)
            sw.clicked.connect(self._apply_hex)
            sw.double_clicked.connect(self._accept_hex)
            grid.addWidget(sw, i // 8, i % 8)
        v.addWidget(preset_box)

        custom_box = QGroupBox("自定义颜色（十六进制或 RGB 输入即时识别）")
        cv = QGridLayout(custom_box)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setHorizontalSpacing(8)
        cv.setVerticalSpacing(6)

        self.preview_sw = _ColorSwatch(self._color.name())
        self.preview_sw.setFixedSize(64, 40)
        self.preview_sw.setCursor(Qt.CursorShape.ArrowCursor)
        self.preview_sw.setToolTip("当前颜色")
        cv.addWidget(self.preview_sw, 0, 0, 3, 1)

        cv.addWidget(QLabel("十六进制"), 0, 1)
        self.hex_edit = QLineEdit(self._color.name())
        self.hex_edit.setPlaceholderText("#RRGGBB，如 #ff5500")
        self.hex_edit.setFixedWidth(120)
        self.hex_edit.textChanged.connect(self._on_hex_changed)
        cv.addWidget(self.hex_edit, 0, 2)

        self._spins: Dict[int, NoWheelSpinBox] = {}
        for row, (name, ch) in enumerate((("R", 0), ("G", 1), ("B", 2)), start=1):
            spin = NoWheelSpinBox()
            spin.setRange(0, 255)
            spin.setValue(self._color.getRgb()[ch])
            spin.setFixedWidth(80)
            spin.valueChanged.connect(lambda _v, c=ch: self._on_spin_changed(c))
            self._spins[ch] = spin
            cv.addWidget(QLabel(name), row, 1)
            cv.addWidget(spin, row, 2)
        v.addWidget(custom_box)

        btns = QHBoxLayout()
        ok_btn = QPushButton("使用此颜色")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        v.addLayout(btns)

    def selected_color(self) -> QColor:
        return QColor(self._color)

    # ---------- 内部同步 ----------

    def _set_color(self, color: QColor, source: str):
        if self._updating or not color.isValid():
            return
        self._updating = True
        try:
            self._color = QColor(color)
            self.preview_sw.set_color(self._color.name())
            if source != "hex":
                self.hex_edit.setText(self._color.name())
            if source != "spin":
                r, g, b, _a = self._color.getRgb()
                self._spins[0].setValue(r)
                self._spins[1].setValue(g)
                self._spins[2].setValue(b)
        finally:
            self._updating = False

    def _on_hex_changed(self, text: str):
        if self._updating:
            return
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(text).strip())
        if not m:
            return
        self._set_color(QColor(f"#{m.group(1)}"), "hex")

    def _on_spin_changed(self, _channel: int):
        if self._updating:
            return
        r, g, b = (self._spins[c].value() for c in (0, 1, 2))
        self._set_color(QColor(r, g, b), "spin")

    def _apply_hex(self, hexc: str):
        self._set_color(QColor(hexc), "swatch")

    def _accept_hex(self, hexc: str):
        self._set_color(QColor(hexc), "swatch")
        self.accept()


# ==================== 字段控制面板 ====================

class FieldPanel(QWidget):
    """单个文字字段控件组（文本/字体/字号/颜色/粗斜体/对齐），直接写入 FieldState。"""

    def __init__(self, key: str, label: str, multiline: bool, placeholder: str,
                 on_change: Callable[[], None],
                 on_attr_change: Optional[Callable[[Any, str], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self.multiline = multiline
        self._applying = False
        self.state: Optional[FieldState] = None
        self._on_change = on_change
        self._on_attr = on_attr_change

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QGroupBox(label, self)
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # 文本
        if multiline:
            self.text_edit = QTextEdit()
            self.text_edit.setPlaceholderText(placeholder)
            self.text_edit.setFixedHeight(72)
            self.text_edit.textChanged.connect(self._on_text_changed)
        else:
            self.text_edit = QLineEdit()
            self.text_edit.setPlaceholderText(placeholder)
            self.text_edit.setClearButtonEnabled(True)
            self.text_edit.textChanged.connect(self._on_text_changed)
        v.addWidget(self.text_edit)

        # 字体 + 对齐（NoWheel：滚轮不改值；字体下拉可输入识别）
        row1 = QHBoxLayout()
        self.font_combo = NoWheelFontComboBox()
        self.font_combo.setToolTip(
            "文字字体（下拉选择，或直接输入字体名回车识别；滚轮不改变字体）")
        self.font_combo.setMinimumWidth(150)
        row1.addWidget(self.font_combo, 1)
        self.align_combo = NoWheelComboBox()
        self.align_combo.setToolTip("对齐方式（决定文字块水平锚点含义）")
        for text, _key in ALIGN_ITEMS:
            self.align_combo.addItem(text)
        row1.addWidget(self.align_combo)
        v.addLayout(row1)

        # 字号滑条（1080p 基准 px，底层存比例；NoWheel：滚轮不改值）
        row2 = QHBoxLayout()
        size_lab = QLabel("字号")
        size_lab.setStyleSheet("font-weight: 600; color: #5a6474;")
        row2.addWidget(size_lab)
        self.size_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
        self.size_slider.setValue(48)
        self.size_slider.setToolTip("1080p 基准字号（其他分辨率按画布高度等比缩放）")
        row2.addWidget(self.size_slider, 1)
        self.size_label = QLabel("48")
        self.size_label.setMinimumWidth(34)
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row2.addWidget(self.size_label)
        v.addLayout(row2)

        # 颜色 + 加粗/斜体
        row3 = QHBoxLayout()
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(34, 24)
        self.color_btn.setStyleSheet(icon_btn_style(
            emoji=False, extra="border: 1px solid #d0d6df;"))
        self.color_btn.setToolTip("选择文字颜色")
        self.color_btn.clicked.connect(self._pick_color)
        row3.addWidget(self.color_btn)
        self.bold_btn = QPushButton("B")
        self.bold_btn.setFixedSize(30, 24)
        self.bold_btn.setCheckable(True)
        self.bold_btn.setStyleSheet(icon_btn_style(emoji=False, extra="font-weight: 700;"))
        self.bold_btn.setToolTip("加粗")
        self.bold_btn.toggled.connect(self._on_bold)
        row3.addWidget(self.bold_btn)
        self.italic_btn = QPushButton("I")
        self.italic_btn.setFixedSize(30, 24)
        self.italic_btn.setCheckable(True)
        self.italic_btn.setStyleSheet(icon_btn_style(emoji=False, extra="font-style: italic;"))
        self.italic_btn.setToolTip("斜体")
        self.italic_btn.toggled.connect(self._on_italic)
        row3.addWidget(self.italic_btn)
        row3.addStretch(1)
        v.addLayout(row3)

        self.font_combo.currentFontChanged.connect(self._on_font_changed)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        self.align_combo.currentIndexChanged.connect(self._on_align_changed)

    # ---------- 状态同步 ----------

    def set_state(self, state: FieldState):
        self.state = state
        self._applying = True
        try:
            if self.multiline:
                self.text_edit.setPlainText(state.text)
            else:
                self.text_edit.setText(state.text)
            self.font_combo.setCurrentFont(QFont(state.font_family))
            size = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX,
                                          round(state.font_size_ratio * CANVAS_REF_H)))
            self.size_slider.setValue(size)
            self.size_label.setText(str(size))
            align_keys = [k for _t, k in ALIGN_ITEMS]
            index = align_keys.index(state.alignment) if state.alignment in align_keys else 0
            self.align_combo.setCurrentIndex(index)
            self.bold_btn.setChecked(state.bold)
            self.italic_btn.setChecked(state.italic)
            self._refresh_color_icon()
        finally:
            self._applying = False

    def _refresh_color_icon(self):
        pm = QPixmap(20, 14)
        pm.fill(QColor(self.state.color if self.state else "#ffffff"))
        self.color_btn.setIcon(QIcon(pm))
        self.color_btn.setIconSize(QSize(20, 14))

    # ---------- 控件事件（写入 state） ----------

    def _notify(self):
        if self._on_change:
            self._on_change()

    def _notify_attr(self, attr: str):
        if self._on_attr:
            self._on_attr(self, attr)
        else:
            self._notify()

    def _on_text_changed(self):
        if self._applying or self.state is None:
            return
        self.state.text = (self.text_edit.toPlainText() if self.multiline
                            else self.text_edit.text())
        self._notify()

    def _on_font_changed(self, font: QFont):
        if self._applying or self.state is None:
            return
        family = font.family()
        if not family or family == self.state.font_family:
            return
        if not _font_family_exists(family):
            # 输入的不是有效字体名（如只打了前缀）：回滚下拉显示并保持原字体
            self._applying = True
            try:
                self.font_combo.setCurrentFont(QFont(self.state.font_family))
            finally:
                self._applying = False
            return
        self.state.font_family = family
        self._notify_attr("font")

    def _on_size_changed(self, value: int):
        self.size_label.setText(str(value))
        if self._applying or self.state is None:
            return
        self.state.font_size_ratio = value / float(CANVAS_REF_H)
        self._notify_attr("size")

    def _on_align_changed(self, index: int):
        if self._applying or self.state is None:
            return
        keys = [k for _t, k in ALIGN_ITEMS]
        if 0 <= index < len(keys):
            self.state.alignment = keys[index]
        self._notify()

    def _on_bold(self, checked: bool):
        if self._applying or self.state is None:
            return
        self.state.bold = checked
        self._notify()

    def _on_italic(self, checked: bool):
        if self._applying or self.state is None:
            return
        self.state.italic = checked
        self._notify()

    def _pick_color(self):
        if self.state is None:
            return
        dlg = ColorPickerDialog(self.state.color, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        color = dlg.selected_color()
        if not color.isValid():
            return
        self.state.color = color.name()
        self._refresh_color_icon()
        self._notify_attr("color")

    # ---------- 同步样式（外部写入，不回调） ----------

    def apply_font_family(self, family: str):
        if self.state is None or self.state.font_family == family:
            return
        self.state.font_family = family
        self._applying = True
        try:
            self.font_combo.setCurrentFont(QFont(family))
        finally:
            self._applying = False

    def apply_color(self, color_hex: str):
        if self.state is None or self.state.color == color_hex:
            return
        self.state.color = color_hex
        self._refresh_color_icon()

    def apply_size_ratio(self, ratio: float):
        if self.state is None:
            return
        size = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, round(ratio * CANVAS_REF_H)))
        if abs(self.state.font_size_ratio - ratio) < 1e-9:
            return
        self.state.font_size_ratio = size / float(CANVAS_REF_H)
        self._applying = True
        try:
            self.size_slider.setValue(size)
            self.size_label.setText(str(size))
        finally:
            self._applying = False


# ==================== 预览画布（拖动定位） ====================

class CoverPreview(QWidget):
    """封面预览：等比缩放绘制 960x540 画布，支持鼠标拖动文字块更新位置比例。"""

    def __init__(self, on_drag: Optional[Callable[[str, float, float], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 270)
        self._image = QImage()
        self._geoms: Dict[str, QRectF] = {}
        self._anchors: Dict[str, Tuple[float, float]] = {}
        self._drag_key: Optional[str] = None
        self._grab = QPointF()
        self._on_drag = on_drag

    def set_canvas(self, image: QImage, geoms: Dict[str, QRectF],
                   anchors: Dict[str, Tuple[float, float]]):
        self._image = image
        self._geoms = geoms
        self._anchors = anchors
        self.update()

    def _scale_offset(self) -> Tuple[float, QPointF]:
        if self._image.isNull():
            return 1.0, QPointF(0, 0)
        s = min(self.width() / float(self._image.width()),
                self.height() / float(self._image.height()))
        dx = (self.width() - self._image.width() * s) / 2.0
        dy = (self.height() - self._image.height() * s) / 2.0
        return s, QPointF(dx, dy)

    def _map_to_canvas(self, pos: QPointF) -> QPointF:
        s, off = self._scale_offset()
        return QPointF((pos.x() - off.x()) / s, (pos.y() - off.y()) / s)

    def _hit_test(self, canvas_pt: QPointF) -> Optional[str]:
        if self._image.isNull():
            return None
        for key, rect in self._geoms.items():
            expanded = rect.adjusted(-10, -10, 10, 10)
            if expanded.contains(canvas_pt):
                return key
        return None

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PREVIEW_LETTERBOX))
        if not self._image.isNull():
            s, off = self._scale_offset()
            target = QRectF(off.x(), off.y(),
                            self._image.width() * s, self._image.height() * s)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target, self._image)
        if self._drag_key and self._drag_key in self._geoms:
            rect = self._geoms[self._drag_key]
            s, off = self._scale_offset()
            shown = QRectF(off.x() + rect.x() * s, off.y() + rect.y() * s,
                           rect.width() * s, rect.height() * s)
            painter.setPen(QPen(QColor(DRAG_HINT_COLOR), 1, Qt.PenStyle.DashLine))
            painter.drawRect(shown)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._image.isNull():
            return
        canvas_pt = self._map_to_canvas(event.position())
        key = self._hit_test(canvas_pt)
        if not key or key not in self._anchors:
            return
        self._drag_key = key
        xr, yr = self._anchors[key]
        anchor = QPointF(xr * self._image.width(), yr * self._image.height())
        self._grab = canvas_pt - anchor
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()

    def mouseMoveEvent(self, event):
        if self._image.isNull():
            return
        canvas_pt = self._map_to_canvas(event.position())
        if self._drag_key and self._drag_key in self._anchors:
            anchor = canvas_pt - self._grab
            xr = anchor.x() / float(self._image.width())
            yr = anchor.y() / float(self._image.height())
            if self._on_drag:
                self._on_drag(self._drag_key, xr, yr)
        else:
            hovered = self._hit_test(canvas_pt) is not None
            self.setCursor(Qt.CursorShape.SizeAllCursor if hovered
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event):
        if self._drag_key:
            self._drag_key = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()


# ==================== 页面 ====================

class CoverMakerPage(BasePage):
    """实用工具 · 套版封面制作页。"""

    def __init__(self, app, bg_folder=None):
        super().__init__(app)
        section = _read_cover_config()

        self.states = _restore_states(section.get("fields"))
        self.y_shift = 0.0
        try:
            self.y_shift = max(-0.3, min(0.3, float(section.get("y_shift") or 0.0)))
        except (TypeError, ValueError):
            self.y_shift = 0.0
        self.fit_mode = str(section.get("fit_mode") or FIT_CROP)
        if self.fit_mode not in (FIT_CROP, FIT_CONTAIN):
            self.fit_mode = FIT_CROP
        self._export_resolution = str(section.get("export_resolution") or EXPORT_RESOLUTIONS[0])
        self._jpg_quality = JPG_QUALITY_DEFAULT
        try:
            self._jpg_quality = max(JPG_QUALITY_MIN,
                                    min(JPG_QUALITY_MAX, int(section.get("jpg_quality") or JPG_QUALITY_DEFAULT)))
        except (TypeError, ValueError):
            self._jpg_quality = JPG_QUALITY_DEFAULT
        sync = section.get("sync") if isinstance(section.get("sync"), dict) else {}
        self._sync = {
            "font": bool(sync.get("font", True)),
            "color": bool(sync.get("color", False)),
            "size": bool(sync.get("size", False)),
        }

        # 底图文件夹：显式参数 > 持久化 > 默认 download/封面底图
        default_folder = Path(get_export_dir()) / "封面底图"
        stored_folder = str(section.get("bg_folder") or "")
        if bg_folder:
            folder = Path(bg_folder)
        elif stored_folder and Path(stored_folder).is_dir():
            folder = Path(stored_folder)
        else:
            folder = default_folder
        self.provider = BackgroundProvider(folder)
        self._bg_path: Optional[Path] = None
        self.cached_export_image: Optional[QImage] = None

        self._build_ui()
        self._restore_widgets(section)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(10)
        self._debounce.timeout.connect(self._render_preview)

        # 初始底图：上次使用（文件仍在）> 随机抽取
        stored_bg = str(section.get("bg_path") or "")
        if stored_bg and Path(stored_bg).is_file():
            self._bg_path = Path(stored_bg)
        else:
            initial = self.provider.random_path()
            if initial is not None:
                self._bg_path = initial
        self._render_preview()

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("🎨 套版封面制作")
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title)
        head.addWidget(attach_help_overlay(self, "❓ 套版封面制作 · 功能介绍", HELP_HTML))
        head.addStretch(1)

        self.random_bg_btn = QPushButton("🎲 随机底图")
        self.random_bg_btn.clicked.connect(self._on_random_bg)
        head.addWidget(self.random_bg_btn)
        self.pick_bg_btn = QPushButton("🖼 选择底图")
        self.pick_bg_btn.setToolTip("浏览底图文件夹缩略图，指定哪张图片作为当前底图（蒙版）")
        self.pick_bg_btn.clicked.connect(self._on_pick_bg)
        head.addWidget(self.pick_bg_btn)
        self.upload_btn = QPushButton("📁 上传图片")
        self.upload_btn.setToolTip("选择本地图片临时替换当前底图（不写入底图文件夹）")
        self.upload_btn.clicked.connect(self._on_upload_bg)
        head.addWidget(self.upload_btn)
        self.fit_combo = NoWheelComboBox()
        self.fit_combo.setToolTip(
            "裁剪填充：底图等比放大铺满画布并居中裁切；完整包含：整图可见，留白填充")
        for text, _key in FIT_ITEMS:
            self.fit_combo.addItem(text)
        self.fit_combo.currentIndexChanged.connect(self._on_fit_changed)
        head.addWidget(self.fit_combo)
        root.addLayout(head)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self._build_left_panel())
        self.splitter.addWidget(self._build_right_panel())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([380, 760])
        root.addWidget(self.splitter, 1)

        self.status_label = QLabel("")
        root.addWidget(self.status_label)

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea(self.splitter)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background-color: #ffffff; border: 1px solid #d0d6df;"
            " border-radius: 4px; }"
        )
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(460)
        body = QWidget(scroll)
        # 必须用带选择器的样式表：无选择器样式表会传播给全部后代，包括以本栏
        # 控件为父的对话框（ColorPickerDialog），background: transparent 会覆盖
        # 应用级 QSS 把整个弹窗变成未初始化黑底（深浅主题均黑）
        body.setObjectName("coverMakerLeftBody")
        body.setStyleSheet("QWidget#coverMakerLeftBody { background: transparent; }")
        lv = QVBoxLayout(body)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(10)

        # 底图组
        bg_box = QGroupBox("🖼 底图")
        bgv = QVBoxLayout(bg_box)
        bgv.setContentsMargins(8, 8, 8, 8)
        bgv.setSpacing(6)
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #5a6474;")
        bgv.addWidget(self.folder_label)
        row = QHBoxLayout()
        self.folder_btn = QPushButton("📂 更换底图文件夹")
        self.folder_btn.clicked.connect(self._on_change_bg_folder)
        row.addWidget(self.folder_btn, 1)
        self.rescan_btn = QPushButton("🔄 重新扫描")
        self.rescan_btn.clicked.connect(self._on_rescan)
        row.addWidget(self.rescan_btn)
        bgv.addLayout(row)
        self.bg_count_label = QLabel("已扫描 0 张图片")
        bg_count_style = "font-size: 12px; color: #6b7280;"
        self.bg_count_label.setStyleSheet(bg_count_style)
        bgv.addWidget(self.bg_count_label)
        hint = QLabel("随机底图从文件夹递归抽取全部图片；「上传图片」仅临时替换当前底图")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #8a93a5;")
        bgv.addWidget(hint)
        lv.addWidget(bg_box)

        # 同步样式组
        sync_box = QGroupBox("🔗 同步样式")
        sync_box.setStyleSheet("QGroupBox { font-weight: 600; }")
        sh = QVBoxLayout(sync_box)
        sh.setContentsMargins(8, 8, 8, 8)
        sh.setSpacing(6)
        srow = QHBoxLayout()
        self.sync_font_cb = QCheckBox("同步字体")
        self.sync_font_cb.setToolTip("修改任一字段字体时同步到其余字段")
        self.sync_color_cb = QCheckBox("同步颜色")
        self.sync_color_cb.setToolTip("修改任一字段颜色时同步到其余字段")
        self.sync_size_cb = QCheckBox("同步字号")
        self.sync_size_cb.setToolTip("修改任一字段字号时同步到其余字段")
        srow.addWidget(self.sync_font_cb)
        srow.addWidget(self.sync_color_cb)
        srow.addWidget(self.sync_size_cb)
        srow.addStretch(1)
        sh.addLayout(srow)
        sync_hint = QLabel("开启后，修改任一字段的该属性会立即同步应用到其余两个字段")
        sync_hint.setWordWrap(True)
        sync_hint.setStyleSheet("font-size: 11px; color: #8a93a5;")
        sh.addWidget(sync_hint)
        lv.addWidget(sync_box)

        # 三字段面板
        self.panels: Dict[str, FieldPanel] = {}
        for key in FIELD_KEYS:
            panel = FieldPanel(key, FIELD_LABELS[key], multiline=(key == "remark"),
                               placeholder=FIELD_PLACEHOLDERS[key],
                               on_change=self._on_panel_changed,
                               on_attr_change=self._on_panel_attr)
            panel.set_state(self.states[key])
            self.panels[key] = panel
            lv.addWidget(panel)

        # Y轴微调
        yrow = QHBoxLayout()
        ylab = QLabel("Y轴微调(整体)")
        ylab.setStyleSheet("font-weight: 600; color: #5a6474;")
        ylab.setToolTip("全部文字整体上下平移（画布高度比例，正值下移/负值上移）")
        yrow.addWidget(ylab)
        self.y_spin = NoWheelDoubleSpinBox()
        self.y_spin.setRange(-0.3, 0.3)
        self.y_spin.setSingleStep(0.002)
        self.y_spin.setDecimals(3)
        self.y_spin.setToolTip("可直接输入数值；滚轮不改变值")
        self.y_spin.valueChanged.connect(self._on_y_changed)
        yrow.addWidget(self.y_spin, 1)
        lv.addLayout(yrow)

        # 模板行
        trow = QHBoxLayout()
        self.save_tpl_btn = QPushButton("💾 保存模板")
        self.save_tpl_btn.clicked.connect(self._on_save_template)
        trow.addWidget(self.save_tpl_btn, 1)
        self.load_tpl_btn = QPushButton("📂 加载模板")
        self.load_tpl_btn.clicked.connect(self._on_load_template)
        trow.addWidget(self.load_tpl_btn, 1)
        lv.addLayout(trow)

        lv.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _build_right_panel(self) -> QWidget:
        right = QWidget(self.splitter)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        row0 = QHBoxLayout()
        self.reset_pos_btn = QPushButton("🎯 重置文字位置")
        self.reset_pos_btn.setToolTip("恢复默认布局：三行垂直居中")
        self.reset_pos_btn.clicked.connect(self._on_reset_positions)
        row0.addWidget(self.reset_pos_btn)
        row0.addStretch(1)
        res_lab = QLabel("导出分辨率")
        res_lab.setStyleSheet("font-weight: 600; color: #5a6474;")
        row0.addWidget(res_lab)
        self.res_combo = NoWheelComboBox()
        self.res_combo.addItems(EXPORT_RESOLUTIONS)
        self.res_combo.setMinimumWidth(110)
        self.res_combo.currentTextChanged.connect(lambda _t: self._update_status())
        row0.addWidget(self.res_combo)
        rv.addLayout(row0)

        self.preview = CoverPreview(on_drag=self._on_drag_update)
        rv.addWidget(self.preview, 1)

        row2 = QHBoxLayout()
        qlab = QLabel("JPG质量")
        qlab.setStyleSheet("font-weight: 600; color: #5a6474;")
        self.quality_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(JPG_QUALITY_MIN, JPG_QUALITY_MAX)
        self.quality_slider.setValue(JPG_QUALITY_DEFAULT)
        self.quality_slider.setMaximumWidth(160)
        self.quality_slider.setToolTip("滚轮不改变值，拖动或键盘调整")
        self.quality_label = QLabel(str(JPG_QUALITY_DEFAULT))
        self.quality_label.setMinimumWidth(28)
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        row2.addWidget(qlab)
        row2.addWidget(self.quality_slider)
        row2.addWidget(self.quality_label)
        row2.addStretch(1)
        self.export_png_btn = QPushButton("💾 导出PNG")
        self.export_png_btn.clicked.connect(lambda: self._export_image("PNG"))
        row2.addWidget(self.export_png_btn)
        self.export_jpg_btn = QPushButton("🖼 导出JPG")
        self.export_jpg_btn.clicked.connect(lambda: self._export_image("JPEG"))
        row2.addWidget(self.export_jpg_btn)
        rv.addLayout(row2)
        return right

    def _restore_widgets(self, section: Dict[str, Any]):
        self.fit_combo.setCurrentIndex(0 if self.fit_mode == FIT_CROP else 1)
        if self._export_resolution in EXPORT_RESOLUTIONS:
            self.res_combo.setCurrentText(self._export_resolution)
        self.quality_slider.setValue(self._jpg_quality)
        self.quality_label.setText(str(self._jpg_quality))
        self.y_spin.setValue(self.y_shift)
        self.sync_font_cb.setChecked(self._sync["font"])
        self.sync_color_cb.setChecked(self._sync["color"])
        self.sync_size_cb.setChecked(self._sync["size"])

    # ---------- 提示 ----------

    def _toast(self, message: str, kind: str = "info"):
        if self.app is not None and hasattr(self.app, "toast"):
            try:
                self.app.toast(message, kind)
                return
            except Exception:
                pass
        try:
            from gui_qt.toast import show_toast
            show_toast(self.window() or self, message, kind)
        except Exception:
            pass

    # ---------- 预览渲染 ----------

    def _current_bg(self, w: int, h: int) -> Optional[QImage]:
        if self._bg_path is None:
            return None
        return self.provider.scaled_background(self._bg_path, w, h, self.fit_mode)

    def _schedule_preview(self):
        """10ms 节流请求重绘：定时器活跃期间不重启（连续输入/拖动不会把渲染
        一次次往后推导致"饿死"），触发时读取最新状态渲染，且渲染频率被压住防卡顿。"""
        if not self._debounce.isActive():
            self._debounce.start()

    def _render_preview(self):
        bg = self._current_bg(PREVIEW_W, PREVIEW_H)
        image, geoms = render_cover(PREVIEW_W, PREVIEW_H, bg, self.fit_mode,
                                     self.states, self.y_shift, draw_hint=True)
        anchors = {k: (self.states[k].x_ratio, self.states[k].y_ratio + self.y_shift)
                   for k in FIELD_KEYS}
        self.preview.set_canvas(image, geoms, anchors)
        self._update_status()

    def _update_status(self):
        count = len(self.provider.files)
        current = self._bg_path.name if self._bg_path else "无"
        self.status_label.setText(
            f"底图 {count} 张 · 当前 {current} · 预览 {PREVIEW_W}x{PREVIEW_H} · "
            f"导出 {self.res_combo.currentText()} · 预览区可直接拖动文字调整位置")
        self.folder_label.setText(f"底图文件夹：{self.provider.folder}")
        self.bg_count_label.setText(f"已扫描 {count} 张图片")

    # ---------- 事件处理 ----------

    def _on_panel_changed(self):
        self._schedule_preview()

    def _on_panel_attr(self, panel: FieldPanel, attr: str):
        if attr == "font" and self.sync_font_cb.isChecked():
            family = panel.state.font_family
            for other in self.panels.values():
                if other is not panel:
                    other.apply_font_family(family)
        elif attr == "color" and self.sync_color_cb.isChecked():
            color = panel.state.color
            for other in self.panels.values():
                if other is not panel:
                    other.apply_color(color)
        elif attr == "size" and self.sync_size_cb.isChecked():
            ratio = panel.state.font_size_ratio
            for other in self.panels.values():
                if other is not panel:
                    other.apply_size_ratio(ratio)
        self._schedule_preview()

    def _on_fit_changed(self, index: int):
        self.fit_mode = FIT_ITEMS[index][1] if 0 <= index < len(FIT_ITEMS) else FIT_CROP
        self._render_preview()

    def _on_y_changed(self, value: float):
        self.y_shift = value
        self._schedule_preview()

    def _on_quality_changed(self, value: int):
        self._jpg_quality = value
        self.quality_label.setText(str(value))

    def _on_drag_update(self, key: str, x_ratio: float, y_ratio: float):
        state = self.states.get(key)
        if state is None:
            return
        state.x_ratio = min(1.0, max(0.0, x_ratio))
        state.y_ratio = min(1.0, max(0.0, y_ratio - self.y_shift))
        self._schedule_preview()

    def _on_reset_positions(self):
        defaults = default_states()
        for key in FIELD_KEYS:
            self.states[key].x_ratio = defaults[key].x_ratio
            self.states[key].y_ratio = defaults[key].y_ratio
        self.y_spin.setValue(0.0)
        self._render_preview()
        self._toast("已重置文字位置（三行垂直居中）", "success")

    # ---------- 底图操作 ----------

    def _set_bg_path(self, path: Optional[Path], success_msg: str = "") -> bool:
        """切换当前底图（先校验可解码，同时预热缩放缓存），成功后重绘预览。"""
        if path is not None:
            if self.provider.scaled_background(path, PREVIEW_W, PREVIEW_H,
                                               self.fit_mode) is None:
                self._toast(f"底图无法解码：{path.name}", "error")
                return False
        self._bg_path = path
        self._render_preview()
        if success_msg:
            self._toast(success_msg, "success")
        return True

    def _on_random_bg(self):
        path = self.provider.random_path()
        if path is None:
            self._toast(f"底图文件夹内没有图片：{self.provider.folder}", "warning")
            return
        self._set_bg_path(path)

    def _on_pick_bg(self):
        if not self.provider.files:
            self._toast(f"底图文件夹内没有图片：{self.provider.folder}", "warning")
            return
        dlg = BackgroundPickerDialog(self.provider.files, self._bg_path, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.selected_path()
        if path is None or path == self._bg_path:
            return
        if self._set_bg_path(path, f"已选择底图：{path.name}"):
            self._persist_state()

    def _on_upload_bg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择底图图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff);;所有文件 (*)")
        if not path:
            return
        self._set_bg_path(Path(path), "已临时替换底图（不写入底图文件夹）")

    def _on_change_bg_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择底图文件夹（递归扫描内部全部图片）",
            str(self.provider.folder) if self.provider.folder else "")
        if not folder:
            return
        count = self.provider.set_folder(Path(folder))
        self._bg_path = None
        pick = self.provider.random_path()
        if pick is not None:
            self._bg_path = pick
        self._render_preview()
        self._persist_state()
        self._toast(f"底图文件夹已切换，扫描到 {count} 张图片", "success" if count else "warning")

    def _on_rescan(self):
        count = self.provider.scan()
        self._render_preview()
        self._toast(f"重新扫描完成，共 {count} 张图片", "success" if count else "warning")

    # ---------- 导出 ----------

    def _export_size(self) -> Tuple[int, int]:
        try:
            w, h = self.res_combo.currentText().lower().split("x")
            return int(w), int(h)
        except ValueError:
            return 1920, 1080

    def _render_export(self) -> QImage:
        w, h = self._export_size()
        bg = self._current_bg(w, h)
        image, _geoms = render_cover(w, h, bg, self.fit_mode, self.states,
                                     self.y_shift, draw_hint=False)
        self.cached_export_image = image
        return image

    def _export_image(self, fmt: str) -> Optional[Path]:
        image = self._render_export()
        ext = "png" if fmt == "PNG" else "jpg"
        out_dir = Path(get_export_dir()) / "套版封面"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"封面_{time.strftime('%Y%m%d_%H%M%S')}.{ext}"
        quality = self._jpg_quality if fmt == "JPEG" else -1
        ok = image.save(str(path), fmt, quality)
        if not ok:
            self._toast(f"导出失败：{path}", "error")
            return None
        self._persist_state()
        self._toast(f"已导出 {fmt}：{path}", "success")
        return path

    # ---------- 模板 ----------

    def _on_save_template(self):
        name, ok = QInputDialog.getText(self, "保存模板", "模板名称：",
                                        text="我的封面样式")
        if not ok or not str(name).strip():
            return
        name = name.strip()
        path = _templates_dir() / f"{_sanitize_template_name(name)}.json"
        if path.exists():
            answer = QMessageBox.question(
                self, "覆盖确认", f"模板「{name}」已存在，覆盖保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        payload = {"version": TEMPLATE_VERSION,
                   "bg": str(self._bg_path) if self._bg_path else "",
                   "fields": template_dict_from_states(self.states)}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._toast(f"模板保存失败：{exc}", "error")
            return
        bg_note = f"（底图：{self._bg_path.name}）" if self._bg_path else ""
        self._toast(f"模板「{name}」已保存{bg_note}", "success")

    def _load_template_from_path(self, path: Path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self._toast(f"模板文件损坏：{exc}", "error")
            return
        warnings = apply_template_to_states(self.states, data)
        # 模板记忆的底图（蒙版）：文件仍存在则切换过去，否则保留当前底图
        bg_ref = str(data.get("bg") or "") if isinstance(data, dict) else ""
        if bg_ref:
            bg_file = Path(bg_ref)
            if bg_file.is_file():
                self._bg_path = bg_file
            else:
                warnings.append(f"模板底图已不存在：{bg_file.name}，保留当前底图")
        for key in FIELD_KEYS:
            self.panels[key].set_state(self.states[key])
        self._render_preview()
        for w in warnings:
            self._toast(w, "warning")
        if not warnings:
            self._toast(f"已加载模板「{path.stem}」", "success")

    def _on_load_template(self):
        tdir = _templates_dir()
        files = sorted(tdir.glob("*.json"))
        if not files:
            self._toast("暂无模板，请先「保存模板」", "warning")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("加载封面模板")
        dlg.resize(420, 380)
        v = QVBoxLayout(dlg)
        listw = QListWidget()
        for f in files:
            try:
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            except OSError:
                mtime = ""
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    tpl = json.load(fh)
                bg_name = Path(str(tpl.get("bg") or "")).name or "未记录"
            except Exception:
                bg_name = "未记录"
            QListWidgetItem(f"{f.stem}    底图: {bg_name}    {mtime}", listw)
        listw.setCurrentRow(0)
        listw.itemDoubleClicked.connect(lambda _i: dlg.accept())
        v.addWidget(listw, 1)
        hint = QLabel("模板保存三字段样式与位置比例，并记忆保存时所用底图"
                      "（文件仍在时加载自动切换）；不保存文字内容")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #8a93a5;")
        v.addWidget(hint)
        btns = QHBoxLayout()
        load_btn = QPushButton("加载")
        load_btn.setDefault(True)
        load_btn.clicked.connect(dlg.accept)
        del_btn = QPushButton("🗑️ 删除")
        del_btn.clicked.connect(lambda: self._delete_template(files, listw, dlg))
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(load_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        v.addLayout(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        row = listw.currentRow()
        if row < 0 or row >= len(files):
            return
        self._load_template_from_path(files[row])

    def _delete_template(self, files: List[Path], listw: QListWidget, dlg: QDialog):
        row = listw.currentRow()
        if row < 0 or row >= len(files):
            return
        path = files[row]
        answer = QMessageBox.question(
            dlg, "删除模板", f"确定删除模板「{path.stem}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as exc:
            self._toast(f"删除失败：{exc}", "error")
            return
        files.pop(row)
        listw.takeItem(row)
        if not files:
            dlg.reject()
            self._toast("模板已全部删除", "info")
        else:
            listw.setCurrentRow(min(row, len(files) - 1))
            self._toast(f"已删除模板「{path.stem}」", "info")

    # ---------- 持久化 ----------

    def _persist_state(self):
        section = {
            "bg_folder": str(self.provider.folder) if self.provider.folder else "",
            "bg_path": str(self._bg_path) if self._bg_path else "",
            "fit_mode": self.fit_mode,
            "export_resolution": self.res_combo.currentText(),
            "jpg_quality": self.quality_slider.value(),
            "y_shift": round(float(self.y_spin.value()), 4),
            "sync": {
                "font": self.sync_font_cb.isChecked(),
                "color": self.sync_color_cb.isChecked(),
                "size": self.sync_size_cb.isChecked(),
            },
            "fields": {k: self.states[k].to_dict() for k in FIELD_KEYS},
        }
        _write_cover_config(section)

    def shutdown(self):
        try:
            self._persist_state()
        except Exception:
            pass
        super().shutdown()


def create_cover_module(app=None, bg_folder=None) -> CoverMakerPage:
    """主程序集成入口：返回「套版封面制作」组件实例（QWidget）。

    app: 宿主窗口（可选，提供 toast 等能力，独立运行时可传 None）
    bg_folder: 底图文件夹绝对路径（可选，默认 download/封面底图 或持久化值）
    """
    return CoverMakerPage(app, bg_folder=bg_folder)
