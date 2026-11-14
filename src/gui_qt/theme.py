"""统一主题：语义配色、完整 QSS 切换及旧页面样式兼容。

Qt 的样式表会缓存 palette() 的解析结果，仅 setPalette 不能让现有控件
正确换色。本模块将样式规范为稳定的配色角色模板，切换时重新 polish
可见控件，隐藏页在显示前更新；避免全应用 repolish 带来的长时间卡顿。
内联样式保留原文，不从上次渲染结果反向猜测颜色，避免多次切换后漂移。
Polish/Show 事件同时覆盖构造参数 styleSheet=、懒加载页面和新弹窗。
"""

import re
import sys
from datetime import datetime
from functools import lru_cache
from weakref import WeakKeyDictionary, WeakSet

from toolbox.config_manager import load_convert_config, save_convert_config

# 选中指示器交给 Qt 原生绘制，避免给 :checked 覆盖背景后丢失勾号/圆点。
NEUTRAL_QSS = """
QWidget { background-color: transparent; color: palette(window-text); font-family: "Microsoft YaHei", "Segoe UI Emoji", "Segoe UI Symbol"; font-size: 13px; }
QMainWindow, QDialog, QMessageBox, QStackedWidget, QWidget[pageSurface="true"], QWidget[windowSurface="true"] { background-color: palette(window); }
QScrollArea { border: none; background: palette(window); }
QPushButton { background: palette(button); color: palette(button-text); padding: 6px 12px; border: 1px solid palette(shadow); border-radius: 6px; text-align: center; }
QPushButton:hover { background: palette(midlight); color: palette(link); border-color: palette(highlight); }
QPushButton:pressed, QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); }
QPushButton:focus { border-color: palette(highlight); }
QPushButton:disabled { background: palette(alternate-base); color: palette(placeholder-text); border-color: palette(midlight); }
QLineEdit, QListWidget, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 6px; padding: 6px; selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: palette(dark); }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid palette(highlight); background: palette(base); }
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: palette(alternate-base); color: palette(placeholder-text); border-color: palette(shadow); }
QListWidget { padding: 2px; }
QListWidget::item { padding: 3px; border-radius: 3px; }
QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QListWidget::item:hover { background: palette(midlight); }
QTreeWidget, QTreeView, QTableWidget, QTableView { background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 6px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QHeaderView::section { background: palette(alternate-base); color: palette(placeholder-text); border: none; border-bottom: 1px solid palette(shadow); padding: 5px; }
QCheckBox, QRadioButton { color: palette(window-text); spacing: 6px; }
QCheckBox:disabled, QRadioButton:disabled { color: palette(placeholder-text); }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked { background: palette(button); border: 1px solid palette(mid); border-radius: 3px; }
QCheckBox::indicator:unchecked:disabled { background: palette(alternate-base); border-color: palette(shadow); }
QTabBar::tab { background: palette(alternate-base); color: palette(placeholder-text); padding: 7px 14px; border: 1px solid palette(shadow); border-bottom: 2px solid transparent; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:hover { background: palette(midlight); color: palette(link); }
QTabBar::tab:selected { background: palette(base); color: palette(link); border-bottom-color: palette(highlight); }
QTabWidget::pane { border: 1px solid palette(shadow); background: palette(window); }
QProgressBar { background: palette(base); border: 1px solid palette(mid); border-radius: 4px; text-align: center; color: palette(text); }
QProgressBar::chunk { background: palette(highlight); border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: palette(mid); border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: palette(dark); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: palette(mid); border-radius: 3px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: palette(dark); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QStatusBar { background: palette(base); color: palette(placeholder-text); border-top: 1px solid palette(shadow); }
QStatusBar::item { border: none; }
QLabel { color: palette(window-text); }
QMenuBar { background: palette(window); color: palette(window-text); }
QMenuBar::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu { background: palette(base); color: palette(text); border: 1px solid palette(mid); }
QMenu::item { padding: 5px 18px; }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QToolTip { background: palette(tool-tip-base); color: palette(tool-tip-text); border: 1px solid palette(mid); padding: 5px 7px; }
QGroupBox { border: 1px solid palette(shadow); border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { color: palette(placeholder-text); subcontrol-origin: margin; left: 10px; }
QSpinBox, QDoubleSpinBox { padding: 3px 6px; }
QComboBox QAbstractItemView { background: palette(base); color: palette(text); selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }
QSlider::groove:horizontal { height: 6px; background: palette(mid); border-radius: 3px; }
QSlider::sub-page:horizontal { background: palette(highlight); border-radius: 3px; }
QSlider::handle:horizontal { background: palette(highlighted-text); border: 1px solid #9aa3b0; width: 12px; margin: -4px 0; border-radius: 6px; }
QWidget#navigationPanel { background: palette(base); border-right: 1px solid palette(shadow); }
QWidget#navigationItems, QWidget#navigationViewport, QScrollArea#navigationScroll { background: palette(base); border: none; }
QPushButton#navigationTitle, QPushButton#navigationButton, QLabel#navigationGroup { font-family: "Microsoft YaHei", "Segoe UI Emoji", "Segoe UI Symbol"; }
QPushButton#navigationTitle { background: transparent; color: palette(window-text); border: none; padding: 16px 10px; text-align: left; font-size: 14px; font-weight: bold; }
QPushButton#navigationTitle:hover, QPushButton#navigationTitle:checked { color: palette(link); }
QLabel#navigationGroup { background: transparent; color: palette(placeholder-text); font-size: 11px; font-weight: bold; padding: 12px 8px 4px; }
QPushButton#navigationButton { background: transparent; color: palette(window-text); border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 7px; text-align: left; padding: 6px 8px; }
QPushButton#navigationButton:hover { background: palette(alternate-base); color: palette(link); }
QPushButton#navigationButton:checked { background: palette(midlight); color: palette(link); border-left-color: palette(link); font-weight: bold; }
QPushButton#navigationButton:focus { border-top-color: palette(highlight); border-right-color: palette(highlight); border-bottom-color: palette(highlight); }
QWidget#cookiePanel { background: palette(window); border: 1px solid palette(shadow); border-radius: 9px; }
QFrame[variant="card"] { background: palette(base); border: 1px solid palette(shadow); border-radius: 12px; }
QFrame[variant="subtle"] { background: palette(alternate-base); border: 1px solid palette(shadow); border-radius: 10px; }
QFrame[variant="toolbar"] { background: palette(base); border: 1px solid palette(shadow); border-radius: 10px; }
QLabel[role="title"] { color: palette(window-text); font-size: 23px; font-weight: bold; background: transparent; }
QLabel[role="subtitle"], QLabel[role="muted"] { color: palette(placeholder-text); background: transparent; }
QLabel[role="caption"] { color: palette(placeholder-text); font-size: 11px; background: transparent; }
QLabel[role="sectionTitle"] { color: palette(window-text); font-size: 14px; font-weight: bold; background: transparent; }
QPushButton[variant="primary"] { background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); font-weight: bold; padding: 7px 14px; }
QPushButton[variant="primary"]:hover { background: theme(accent_hover); border-color: theme(accent_hover); color: palette(highlighted-text); }
QPushButton[variant="danger"] { background: theme(danger_soft); color: theme(danger); border-color: theme(danger_border); }
QPushButton[variant="danger"]:hover { background: theme(danger); border-color: theme(danger); color: palette(window); }
QPushButton[variant="secondary"] { background: palette(button); color: palette(button-text); border-color: palette(shadow); }
QPushButton[variant="secondary"]:hover { background: palette(midlight); border-color: palette(highlight); color: palette(link); }
QPushButton[variant="ghost"] { background: transparent; color: palette(placeholder-text); border-color: transparent; }
QPushButton[variant="ghost"]:hover { background: palette(midlight); color: palette(link); }
QPushButton[variant="primary"]:disabled, QPushButton[variant="danger"]:disabled, QPushButton[variant="secondary"]:disabled, QPushButton[variant="ghost"]:disabled { background: palette(alternate-base); color: palette(placeholder-text); border-color: palette(shadow); }
"""

# ==================== 浅色 / 深色调色板 ====================
# 通用控件共用的层级配色：页面底色、卡片、边框、次级文字与蓝色交互强调。
# shadow=按钮边框、mid=输入框边框、dark=滚动条悬停、midlight=列表悬停/禁用边框、
# alternate-base=表头/标签页底、placeholder-text=次级灰字（状态栏/表头/分组标题）。
_PALETTE_SPEC = {
    # role: (light, dark)
    "Window": ("#f3f5f9", "#151c29"),
    "WindowText": ("#263449", "#dce5f3"),
    "Base": ("#ffffff", "#1d2737"),
    "AlternateBase": ("#f0f3f8", "#243044"),
    "Text": ("#263449", "#dce5f3"),
    "Button": ("#ffffff", "#243044"),
    "ButtonText": ("#263449", "#dce5f3"),
    "Midlight": ("#e8effc", "#293d5c"),
    "Mid": ("#cbd5e3", "#3a4b63"),
    "Dark": ("#93a7c5", "#6c84a6"),
    "Shadow": ("#dce3ed", "#303e52"),
    "Highlight": ("#356bd6", "#447ccf"),
    "HighlightedText": ("#ffffff", "#ffffff"),
    "Link": ("#285ebc", "#93bcff"),
    "PlaceholderText": ("#6e7f96", "#9caec5"),
    "ToolTipBase": ("#ffffff", "#243044"),
    "ToolTipText": ("#263449", "#dce5f3"),
    # 禁用态文字灰（原生控件用；QSS 管理的控件走 :disabled 规则）
    "DisabledText": ("#8b9aae", "#6e809b"),
    "BrightText": ("#b93842", "#ff969f"),
    "LinkVisited": ("#18754e", "#7cd6af"),
    "Light": ("#fff1f2", "#38252e"),
}

_COLOR_ROLES = {
    "window": "Window", "surface": "Base", "surface_alt": "AlternateBase",
    "text": "WindowText", "muted": "PlaceholderText", "border": "Shadow",
    "accent": "Highlight", "accent_soft": "Midlight", "on_accent": "HighlightedText",
    "link": "Link",
}
_COLORS = {
    "accent_hover": ("#275bc1", "#568fe5"),
    "success": ("#18754e", "#7cd6af"),
    "warning": ("#99600c", "#edc478"),
    "danger": ("#b93842", "#ff969f"),
    "danger_soft": ("#fff1f2", "#38252e"),
    "danger_border": ("#f0cbd0", "#69404b"),
}

_TOKEN_PALETTE = {
    "window": "window", "surface": "base", "surface_alt": "alternate-base",
    "text": "window-text", "muted": "placeholder-text", "border": "shadow",
    "accent": "highlight", "accent_soft": "midlight", "on_accent": "highlighted-text",
    "link": "link", "danger": "bright-text", "danger_soft": "light",
    "danger_border": "mid", "success": "link-visited", "accent_hover": "highlight",
}


def color(name: str, dark: bool | None = None) -> str:
    """语义颜色供图表/富文本使用；普通控件优先使用 variant/role 属性。"""
    if dark is None:
        dark = is_dark()
    colors = _PALETTE_SPEC[_COLOR_ROLES[name]] if name in _COLOR_ROLES else _COLORS[name]
    return colors[1 if dark else 0]


def build_palette(dark: bool):
    """按主题构建统一控件配色的 QPalette（浅/深两套）。"""
    from PySide6.QtGui import QPalette, QColor
    pal = QPalette()
    idx = 1 if dark else 0
    for role_name, colors in _PALETTE_SPEC.items():
        if role_name == "DisabledText":
            pal.setColor(QPalette.Disabled, QPalette.Text, QColor(colors[idx]))
            pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(colors[idx]))
            pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(colors[idx]))
            continue
        role = getattr(QPalette, role_name, None)
        if role is not None:
            pal.setColor(role, QColor(colors[idx]))
    return pal

# ==================== 内联样式色值映射（浅色 → 深色） ====================
# 仅收录两个主题下确实需要互换的色值；强调色/刻意深色不进表。
# 同一深色值对应多个浅色键时，反向映射取“最后写入”的代表键。

_SURFACE_L2D = {
    # 滚动区/窗体底色（深色值即 DARK_QSS 的 #15171d，不进映射避免反向误命中）
    "#eceff3": "#14161d",
    "#f4f5f7": "#1a1d27",
    # 浅灰卡片/输入底（代表键 #ffffff 放最后）
    "#eef0f4": "#23262f",
    "#e4e8ee": "#23262f",
    "#e3e9f2": "#23262f",
    "#e8ebf0": "#23262f",
    "#e8eaf0": "#23262f",
    "#eef1f6": "#23262f",
    "#e9edf3": "#23262f",
    "#d9dee6": "#23262f",
    "#ffffff": "#23262f",
    # 资产页统计大卡
    "#eef1f5": "#262a36",
    # 边框（代表键 #d0d6df 放最后）
    "#dfe4ec": "#2e3140",
    "#c3c9d4": "#2e3140",
    "#c3cad6": "#2e3140",
    "#d0d6df": "#2e3140",
    "#c8cdd6": "#333333",
    "#b6bdca": "#444444",
    "#c2c8d2": "#3a3d4a",
    "#9aa3b0": "#555555",
    "#9fb0c8": "#4a5a8c",
    # 分隔线
    "#c9d1dc": "#3a3f52",
    # 蓝 tint 卡片（日历年月按钮 / launcher）
    "#d8e7f6": "#2a3b52",
    "#dfe3f8": "#2a2f45",
    "#eceefb": "#252a3d",
    "#c6ccef": "#2a3350",
    # 绿 tint（launcher 状态卡 / 设置页依赖行）
    "#e8f5ee": "#16281c",
    "#d4eddf": "#2e5a3c",
    "#b7e0c8": "#1f3a29",
    "#f4faf6": "#1c2a20",
    # 红 tint（launcher 状态卡 / 设置页依赖行 / 浮层 hover）
    "#fdecea": "#3a1f1f",
    "#f9d3d0": "#4a2626",
    "#f2b8b5": "#6b3230",
    "#ffeaea": "#3a2525",
}

_TEXT_L2D = {
    "#2f3440": "#c6cbd6",   # 主文字
    "#555555": "#9aa0b4",
    "#666666": "#9aa0b4",
    "#7a8db0": "#9aa0b4",
    "#8a93a5": "#9aa0b4",
    "#5a6072": "#9aa0b4",
    "#5a6474": "#9aa0b4",   # 代表键放最后
    "#6b7280": "#888888",
    "#888888": "#9aa0b4",   # tracker 状态圆点/提示灰（#888 归一化后命中）
    "#1976d2": "#64b5f6",   # 链接蓝/平台标签/时间标签（深色下提亮，否则看不清）
    # launcher 卡片文字：卡片底色会被映射成深色，文字不提亮则深字深底完全看不清
    "#3a3f6d": "#b9c2ea",   # 软件卡片深藏青 → 浅薰衣草
    "#1d5c3a": "#8fd8ae",   # 链接卡片深绿 → 浅绿
    # 状态强调文字（设置页依赖行 / toast 之外的绿色成功标记等）：深字深底看不清，提亮
    "#d32f2f": "#ef5350",   # 红 → 浅红（缺失标记/汇总）
    "#2e7d32": "#81c784",   # 绿 → 浅绿（已安装标记）
}

# 兼容旧页面常用的文字/表面色；保留视频纯黑和有意使用的强调背景。
_TEXT_L2D.update({
    **dict.fromkeys(("#111111", "#222222", "#333333", "#374151", "#1f2328"), "#dce5f3"),
    **dict.fromkeys(("#999999", "#9aa1ad", "#9aa0b0", "#6b7280", "#777777"), "#9caec5"),
})
_SURFACE_L2D.update({
    **dict.fromkeys(("#fffdf8", "#fff7f7", "#fafafa", "#f8f9fa", "#f9fafb",
                    "#f7f8fa", "#f5f6f8", "#f7f9fc", "#f4f6f8", "#f4f6f9"), "#1d2737"),
    **dict.fromkeys(("#f3f4f6", "#f5f5f5", "#e4e7ec", "#e3e7ee"), "#243044"),
    **dict.fromkeys(("#e5e7eb", "#dddddd", "#eeeeee", "#cccccc", "#f1c0c0"), "#3a4b63"),
})

_SURFACE_D2L = {v: k for k, v in _SURFACE_L2D.items()}
_TEXT_D2L = {v: k for k, v in _TEXT_L2D.items()}

_TEXT_PROPS = {"color", "selection-color"}

# ==================== 样式重映射 ====================

_DECL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9\-]*)\s*:\s*([^;{}]+?)(?=\s*[;}]|$)")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_TOKEN_RE = re.compile(r"theme\(\s*([\w_]+)\s*\)", re.I)
_STYLE_LITERAL_RE = re.compile(
    r"url\(\s*(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^)])*\)"
    r"|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|/\*.*?\*/", re.I | re.S)


def _norm_hex(h):
    h = h.lower()
    if len(h) == 4:
        h = "#" + "".join(c * 2 for c in h[1:])
    return h


def _remap_hex_value(val, mapping):
    def sub(m):
        return mapping.get(_norm_hex(m.group(0)), m.group(0))
    # 图片路径、引号文本及注释中的 #fff 等片段不是颜色。
    out, start = [], 0
    for literal in _STYLE_LITERAL_RE.finditer(val):
        out.append(_HEX_RE.sub(sub, val[start:literal.start()]))
        out.append(literal.group(0))
        start = literal.end()
    out.append(_HEX_RE.sub(sub, val[start:]))
    return "".join(out)


def _protect_literals(source):
    literals = []

    def remember(match):
        literals.append(match.group(0))
        return f"\x00{len(literals) - 1}\x00"

    return _STYLE_LITERAL_RE.sub(remember, source), literals


def _restore_literals(source, literals):
    if not literals:
        return source
    return re.sub(r"\x00(\d+)\x00", lambda m: literals[int(m.group(1))], source)


def remap_stylesheet(ss, to_dark):
    """按主题方向重映射内联样式表里的十六进制色值（选择器安全、幂等）。"""
    if not ss:
        return ss
    surface = _SURFACE_L2D if to_dark else _SURFACE_D2L
    text = _TEXT_L2D if to_dark else _TEXT_D2L

    def repl(m):
        prop, val = m.group(1), m.group(2)
        mapping = text if prop.lower() in _TEXT_PROPS else surface
        new_val = _remap_hex_value(val, mapping)
        if new_val == val:
            return m.group(0)
        return f"{prop}: {new_val}"

    # 整段先保护字面量，路径里的分号、花括号也不能切断声明解析。
    protected, literals = _protect_literals(ss)
    return _restore_literals(_DECL_RE.sub(repl, protected), literals)


def _legacy_role(prop, value):
    """把旧页面中性常量变成稳定配色角色，强调背景/视频黑底保留。"""
    value = _norm_hex(value)
    if prop in _TEXT_PROPS:
        if value in ("#1976d2", "#64b5f6", "#1565c0", "#285ebc", "#93bcff"):
            return "link"
        if value in ("#2e7d32", "#81c784", "#18754e", "#7cd6af"):
            return "link-visited"
        if value in ("#d32f2f", "#ef5350", "#c62828", "#b93842", "#ff969f"):
            return "bright-text"
        if value in ("#263449", "#dce5f3", "#c6cbd6", "#2f3440", "#111111",
                     "#222222", "#333333", "#374151", "#1f2328"):
            return "window-text"
        if value in _TEXT_L2D or value in _TEXT_D2L or value in ("#6e7f96", "#9caec5"):
            return "placeholder-text"
        return None
    if value in ("#151c29", "#f3f5f9", "#f4f5f7", "#1a1d27", "#14161d", "#eceff3"):
        return "window"
    if value in _SURFACE_L2D or value in _SURFACE_D2L:
        if prop.startswith("border") or prop in ("outline", "gridline-color"):
            return "mid" if value in ("#c8cdd6", "#333333", "#333340") else "shadow"
        if value in ("#d8e7f6", "#2a3b52", "#e8effc", "#293d5c"):
            return "midlight"
        if value in ("#ffffff", "#23262f", "#1d2737", "#fffdf8", "#fafafa",
                     "#f8f9fa", "#f9fafb", "#f7f8fa", "#f7f9fc"):
            return "base"
        return "alternate-base"
    if value in ("#dce3ed", "#303e52") and prop.startswith("border"):
        return "shadow"
    return None


@lru_cache(maxsize=4096)
def _theme_template(source, legacy=True):
    """一次性规范原始样式；输出不随当前主题改变。"""
    protected, literals = _protect_literals(source)
    if legacy:
        def declaration(match):
            prop, value = match.group(1).lower(), match.group(2)

            def replace_color(m):
                role = _legacy_role(prop, m.group(0))
                return f"palette({role})" if role else m.group(0)

            value = _HEX_RE.sub(replace_color, value)
            if prop in _TEXT_PROPS:
                value = re.sub(r"(?i)\b(?:gray|grey)\b", "palette(placeholder-text)", value)
            return f"{match.group(1)}: {value}"

        protected = _DECL_RE.sub(declaration, protected)
    protected = _TOKEN_RE.sub(
        lambda m: f"palette({_TOKEN_PALETTE[m.group(1).lower()]})"
        if m.group(1).lower() in _TOKEN_PALETTE else m.group(0), protected)
    return _restore_literals(protected, literals)


# ==================== 主题状态与持久化 ====================

_state = {}
_orig_set_stylesheet = None
_style_sources = WeakKeyDictionary()
_auto_window_surfaces = WeakSet()
_widget_themes = WeakKeyDictionary()
_theme_filter = None
_applying = False
_refreshing = WeakSet()
_applied_dark = None


def init_theme():
    """从配置读取主题到内存缓存（app 启动时调用；get_theme 也会惰性兜底）。"""
    try:
        _state["dark"] = load_convert_config().get("theme") == "dark"
    except Exception:
        _state["dark"] = False


def get_theme():
    if not _state:
        init_theme()
    return "dark" if _state.get("dark") else "light"


def is_dark():
    return get_theme() == "dark"


def set_theme(name):
    dark = name == "dark"
    _state["dark"] = dark
    try:
        cfg = load_convert_config()
        cfg["theme"] = "dark" if dark else "light"
        save_convert_config(cfg)
    except Exception:
        pass


def toggle_theme():
    set_theme("light" if is_dark() else "dark")
    return get_theme()


# ==================== 主题定时开关（按时间自动黑白） ====================

def _parse_hhmm(s, fallback):
    """把 "HH:MM" 解析成当天分钟数，非法值回退 fallback。"""
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, TypeError):
        pass
    return fallback


def scheduled_theme():
    """按当前时间计算定时目标主题：'light' / 'dark'；未启用或配置无效返回 None。"""
    cfg = load_convert_config()
    if not cfg.get("theme_schedule_enabled"):
        return None
    light = _parse_hhmm(cfg.get("theme_light_time"), 7 * 60)
    dark = _parse_hhmm(cfg.get("theme_dark_time"), 19 * 60)
    if light == dark:
        return None
    now = datetime.now().hour * 60 + datetime.now().minute
    if light < dark:
        return "light" if light <= now < dark else "dark"
    # 浅色时间晚于深色时间（跨午夜，如 08:00 亮 / 00:30 暗）
    return "light" if now >= light or now < dark else "dark"


def check_scheduled_switch():
    """定时开关检查：启用时把主题强制对齐到当前时段目标（到点即变）。

    每 10 秒由主窗口调用；启用期间手动切换会在下个检查点被拉回目标主题，
    关闭定时后手动切换不受影响。
    """
    want = scheduled_theme()
    if want is None or is_dark() == (want == "dark"):
        return None
    set_theme(want)
    return want


# ==================== 跟随系统深浅色 ====================

def follow_system_enabled() -> bool:
    """全局设置「跟随系统深浅色」是否启用。"""
    try:
        return bool(load_convert_config().get("theme_follow_system"))
    except Exception:
        return False


def system_theme() -> str:
    """系统当前深浅色偏好：'dark' / 'light'。

    Windows 读注册表 AppsUseLightTheme（1=浅色 0=深色，不依赖 QApplication，
    offscreen 测试也可靠）；其余平台走 Qt 6.5+ QStyleHints.colorScheme()，
    检测不到默认浅色。
    """
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                return "light" if v else "dark"
        except OSError:
            pass
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        if hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return "light"


def check_follow_system_switch():
    """跟随系统检查：启用时把主题对齐到系统深浅色。返回目标主题名或 None。"""
    if not follow_system_enabled():
        return None
    want = system_theme()
    if is_dark() == (want == "dark"):
        return None
    set_theme(want)
    return want


def check_auto_switch():
    """自动主题统一检查：跟随系统开启时完全压制定时切换（避免两种模式互相拉扯）。

    返回 (来源, 主题名)，来源 ∈ {'system', 'schedule'}；无需变化时 (None, None)。
    """
    if follow_system_enabled():
        name = check_follow_system_switch()
        return ("system", name) if name else (None, None)
    name = check_scheduled_switch()
    return ("schedule", name) if name else (None, None)


def current_palette():
    return build_palette(is_dark())


_applied_qss = None


def apply_theme(app=None):
    """把当前完整主题应用到已有与随后创建的控件。

    模板只安装一次；更新调色板后重新 polish 可见控件来清除 Qt 的颜色
    缓存。隐藏页在 Show 前处理，避免切换时重刷整个应用。更新期间抑制
    窗口重绘，完成后一次呈现，不重建页面或触发采集。
    """
    global _applied_qss, _applied_dark, _applying
    from PySide6.QtWidgets import QApplication
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    install_auto_remap()
    dark = is_dark()
    qss = _theme_template(NEUTRAL_QSS, legacy=False)
    template_changed = _applied_qss != qss or app.styleSheet() != qss
    if not template_changed and _applied_dark == dark:
        return
    roots = [(w, w.updatesEnabled()) for w in app.topLevelWidgets() if w.isVisible()]
    _applying = True
    try:
        for w, enabled in roots:
            if enabled:
                w.setUpdatesEnabled(False)
        app.setPalette(build_palette(dark))
        if template_changed:
            app.setStyleSheet(qss)
            _applied_qss = qss
            _widget_themes.clear()
        _applied_dark = dark
        for w, _enabled in roots:
            adapt(w)
            _set_native_titlebar(w, dark)
    finally:
        _applying = False
        for w, enabled in roots:
            try:
                if enabled:
                    w.setUpdatesEnabled(True)
                    w.update()
            except RuntimeError:
                pass  # 应用主题期间关闭的临时弹窗


# ==================== setStyleSheet 自动重映射补丁 ====================

def install_auto_remap():
    """兼容旧页面的样式写入，同时记录可恢复的原始样式。"""
    global _orig_set_stylesheet, _theme_filter
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication, QWidget
    if _orig_set_stylesheet is None:
        _orig_set_stylesheet = QWidget.setStyleSheet

        def _patched(self, ss=None):
            source = ss or ""
            previous = _style_sources.get(self)
            # 重复设置 styleSheet() 时保留源文本，不能把渲染结果当新源。
            if previous and source == previous[1]:
                source = previous[0]
            rendered = _theme_template(source)
            _style_sources[self] = (source, rendered)
            if self.styleSheet() != rendered:
                _widget_themes.pop(self, None)
                _orig_set_stylesheet(self, rendered)

        QWidget.setStyleSheet = _patched

    app = QApplication.instance()
    if app is not None and _theme_filter is None:
        class _ThemeFilter(QObject):
            def eventFilter(self, obj, event):
                if not _applying and isinstance(obj, QWidget) and obj not in _refreshing:
                    if event.type() in (QEvent.Polish, QEvent.ParentChange, QEvent.DynamicPropertyChange):
                        if event.type() in (QEvent.ParentChange, QEvent.DynamicPropertyChange):
                            _widget_themes.pop(obj, None)
                        if event.type() != QEvent.DynamicPropertyChange:
                            _adapt_widget(obj)
                        elif obj.isVisible():
                            _refresh_widget(obj)
                    elif event.type() == QEvent.Show:
                        adapt(obj)
                        if obj.isWindow():
                            _set_native_titlebar(obj, is_dark())
                return False

        _theme_filter = _ThemeFilter(app)
        app.installEventFilter(_theme_filter)


def _adapt_widget(widget):
    """刷新一个控件的内联样式；构造参数首次出现时也记录原文。"""
    from PySide6.QtCore import Qt
    # 普通独立 QWidget 窗口也要有不透明底色，子控件则保持透明，避免标签
    # 像截图中那样各自画一个深色矩形。媒体/浮层的透明窗口仍由页面控制。
    if (widget.isWindow() and not widget.testAttribute(Qt.WA_TranslucentBackground)
            and widget.property("windowSurface") is None):
        _auto_window_surfaces.add(widget)
        widget.setProperty("windowSurface", True)
    elif widget in _auto_window_surfaces and (
            not widget.isWindow() or widget.testAttribute(Qt.WA_TranslucentBackground)):
        _auto_window_surfaces.discard(widget)
        if widget.property("windowSurface") is True:
            widget.setProperty("windowSurface", None)
    current = widget.styleSheet()
    previous = _style_sources.get(widget)
    if previous is None or current != previous[1]:
        source = current
    else:
        source = previous[0]
    if not source and previous is None:
        return
    rendered = _theme_template(source)
    _style_sources[widget] = (source, rendered)
    if rendered != current:
        setter = _orig_set_stylesheet
        if setter is None:
            from PySide6.QtWidgets import QWidget
            setter = QWidget.setStyleSheet
        setter(widget, rendered)


def _visible_widgets(root):
    """只遍历当前将显示的分支，隐藏根的子控件用 isVisibleTo(root) 判断。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget
    yield root
    for child in root.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
        if child.isWindow() or not child.isVisibleTo(root):
            continue
        yield from _visible_widgets(child)


def _refresh_widget(widget):
    if widget in _refreshing:
        return
    dark = is_dark()
    _refreshing.add(widget)
    try:
        _adapt_widget(widget)
        if _widget_themes.get(widget) == dark:
            return
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
        _widget_themes[widget] = dark
    finally:
        _refreshing.discard(widget)


def _set_native_titlebar(widget, dark):
    """Windows 标题栏随应用主题，失败时保留系统默认装饰。"""
    if sys.platform != "win32" or not widget.isVisible():
        return
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.platformName() != "windows":
            return
        import ctypes
        value = ctypes.c_int(bool(dark))
        hwnd = ctypes.c_void_p(int(widget.winId()))
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        result = dwm(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        if result:
            dwm(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError, RuntimeError):
        pass


def adapt(root, skip_subtrees=()):
    """刷新即将显示的分支，可安全重复调用；隐藏子树在 Show 时预热。"""
    if root is None:
        return
    skip = set(s for s in skip_subtrees or () if s is not None)
    for w in _visible_widgets(root):
        if w in skip or any(s.isAncestorOf(w) for s in skip):
            continue
        _refresh_widget(w)


def retheme_all(main_window=None):
    """刷新可见窗口；隐藏页面显示前由事件过滤器同步。"""
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if w.isVisible():
            adapt(w)
