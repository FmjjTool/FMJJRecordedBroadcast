"""全局明暗主题（黑白可切换）。

方案：
- 全局外观：LIGHT_QSS / DARK_QSS 两套整体样式表，按配置切换
- 页面内联样式：install_auto_remap() 给 QWidget.setStyleSheet 打补丁，
  按当前主题自动重映射 background/border/color 里的十六进制色值
  （属性感知：`color:` 只走文字色映射，避免误伤按钮白字；
   选择器安全：QPushButton:hover 等不会被改写）
- 切换时：retheme_all() 遍历全部顶层控件重映射已设置的样式
- 持久化：config["theme"] = "light" | "dark"
- 定时开关：check_scheduled_switch() 按配置时间自动黑白（主窗口 QTimer 周期调用）

注意：刻意在两种主题下保持不变的色值（强调绿/红/蓝、视频区纯黑、
终端预览深底等）不进映射表；test_page 监控按钮 #2a5a8c 是浅色主题下的
刻意深色，因此深色对应物刻意选用邻近但不冲突的色值。
"""

import re
from datetime import datetime

from toolbox.config_manager import load_convert_config, save_convert_config

LIGHT_QSS = """
QMainWindow, QWidget { background-color: #f4f5f7; color: #2f3440; font-family: "Microsoft YaHei"; font-size: 13px; }
QScrollArea { border: none; background: #eceff3; }
QPushButton { background: #ffffff; color: #2f3440; padding: 6px 12px; border: 1px solid #c3c9d4; border-radius: 4px; text-align: center; }
QPushButton:hover { background: #2e7d32; color: #ffffff; border-color: #3a8c3a; }
QPushButton:pressed { background: #1a5c2a; color: #ffffff; }
QPushButton:checked { background: #1a5c2a; color: #ffffff; border-color: #2e7d32; }
QPushButton:disabled { background: #eef0f4; color: #9aa3b0; border-color: #d9dee6; }
QLineEdit, QListWidget, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #ffffff; color: #2f3440; border: 1px solid #c8cdd6; border-radius: 4px; padding: 6px; selection-background-color: #2a6a9c; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #2a6a9c; background: #ffffff; }
QListWidget { padding: 2px; }
QListWidget::item { padding: 3px; border-radius: 3px; }
QListWidget::item:selected { background: #2a6a9c; color: #ffffff; }
QListWidget::item:hover { background: #e3e9f2; }
QTreeWidget, QTreeView, QTableWidget, QTableView { background: #ffffff; color: #2f3440; border: 1px solid #c8cdd6; border-radius: 4px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: #2a6a9c; color: #ffffff; }
QHeaderView::section { background: #eef0f4; color: #5a6474; border: none; border-bottom: 1px solid #d0d6df; padding: 5px; }
QCheckBox { color: #2f3440; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background: #2e7d32; border: 1px solid #1a5c2a; border-radius: 3px; }
QCheckBox::indicator:unchecked { background: #ffffff; border: 1px solid #b6bdca; border-radius: 3px; }
QRadioButton::indicator:checked { background: #2a6a9c; border: 1px solid #2a6a9c; border-radius: 8px; }
QTabBar::tab { background: #e4e8ee; color: #5a6474; padding: 8px 16px; border: 1px solid #d0d6df; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1a5c2a; color: #ffffff; }
QTabBar::tab:hover { background: #2e7d32; color: #ffffff; }
QTabWidget::pane { border: 1px solid #d0d6df; background: #eceff3; }
QProgressBar { background: #ffffff; border: 1px solid #c8cdd6; border-radius: 4px; text-align: center; color: #2f3440; }
QProgressBar::chunk { background: #2e7d32; border-radius: 3px; }
QScrollBar:vertical { background: #eceff3; width: 12px; }
QScrollBar::handle:vertical { background: #c2c8d2; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #9fb0c8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #eceff3; height: 12px; }
QScrollBar::handle:horizontal { background: #c2c8d2; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #9fb0c8; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: #eceff3; color: #6b7280; }
QStatusBar::item { border: none; }
QLabel { color: #2f3440; }
QMenuBar { background: #eceff3; color: #2f3440; }
QMenuBar::item:selected { background: #2e7d32; color: #ffffff; }
QMenu { background: #ffffff; color: #2f3440; border: 1px solid #c8cdd6; }
QMenu::item:selected { background: #2e7d32; color: #ffffff; }
QGroupBox { border: 1px solid #d0d6df; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { color: #5a6474; subcontrol-origin: margin; left: 10px; }
QSpinBox, QDoubleSpinBox { padding: 3px 6px; }
QComboBox QAbstractItemView { background: #ffffff; color: #2f3440; selection-background-color: #2a6a9c; selection-color: #ffffff; }
QSlider::groove:horizontal { height: 6px; background: #d9dee6; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #2e7d32; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; border: 1px solid #9aa3b0; width: 12px; margin: -4px 0; border-radius: 6px; }
"""

DARK_QSS = """
QMainWindow, QWidget { background-color: #1a1d27; color: #c6cbd6; font-family: "Microsoft YaHei"; font-size: 13px; }
QScrollArea { border: none; background: #15171d; }
QPushButton { background: #23262f; color: #c6cbd6; padding: 6px 12px; border: 1px solid #2e3140; border-radius: 4px; text-align: center; }
QPushButton:hover { background: #2e7d32; color: #ffffff; border-color: #3a8c3a; }
QPushButton:pressed { background: #1a5c2a; color: #ffffff; }
QPushButton:checked { background: #1a5c2a; color: #ffffff; border-color: #2e7d32; }
QPushButton:disabled { background: #1a1d27; color: #555; border-color: #23262f; }
QLineEdit, QListWidget, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #23262f; color: #c6cbd6; border: 1px solid #333; border-radius: 4px; padding: 6px; selection-background-color: #2a5a8c; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #2a5a8c; background: #2a2d3a; }
QListWidget { padding: 2px; }
QListWidget::item { padding: 3px; border-radius: 3px; }
QListWidget::item:selected { background: #2a5a8c; color: #ffffff; }
QListWidget::item:hover { background: #23262f; }
QTreeWidget, QTreeView, QTableWidget, QTableView { background: #23262f; color: #c6cbd6; border: 1px solid #333; border-radius: 4px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: #2a5a8c; color: #ffffff; }
QHeaderView::section { background: #23262f; color: #9aa0b4; border: none; border-bottom: 1px solid #2e3140; padding: 5px; }
QCheckBox { color: #c6cbd6; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background: #2e7d32; border: 1px solid #1a5c2a; border-radius: 3px; }
QCheckBox::indicator:unchecked { background: #23262f; border: 1px solid #444; border-radius: 3px; }
QRadioButton::indicator:checked { background: #2a5a8c; border: 1px solid #2a5a8c; border-radius: 8px; }
QTabBar::tab { background: #23262f; color: #888; padding: 8px 16px; border: 1px solid #2e3140; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1a5c2a; color: #ffffff; }
QTabBar::tab:hover { background: #2e7d32; color: #ffffff; }
QTabWidget::pane { border: 1px solid #2e3140; background: #15171d; }
QProgressBar { background: #23262f; border: 1px solid #333; border-radius: 4px; text-align: center; color: #c6cbd6; }
QProgressBar::chunk { background: #2e7d32; border-radius: 3px; }
QScrollBar:vertical { background: #15171d; width: 12px; }
QScrollBar::handle:vertical { background: #3a3d4a; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4a5a8c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #15171d; height: 12px; }
QScrollBar::handle:horizontal { background: #3a3d4a; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #4a5a8c; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: #15171d; color: #888; }
QStatusBar::item { border: none; }
QLabel { color: #c6cbd6; }
QMenuBar { background: #15171d; color: #c6cbd6; }
QMenuBar::item:selected { background: #2e7d32; }
QMenu { background: #1a1d27; color: #c6cbd6; border: 1px solid #2e3140; }
QMenu::item:selected { background: #2e7d32; }
QGroupBox { border: 1px solid #2e3140; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { color: #9aa0b4; subcontrol-origin: margin; left: 10px; }
QSpinBox, QDoubleSpinBox { padding: 3px 6px; }
QComboBox QAbstractItemView { background: #23262f; color: #c6cbd6; selection-background-color: #2a5a8c; }
QSlider::groove:horizontal { height: 6px; background: #23262f; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #2e7d32; border-radius: 3px; }
QSlider::handle:horizontal { background: #c6cbd6; width: 12px; margin: -4px 0; border-radius: 6px; }
"""

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
    # 绿 tint（launcher 状态卡）
    "#e8f5ee": "#16281c",
    "#d4eddf": "#2e5a3c",
    "#b7e0c8": "#1f3a29",
    # 红 tint（launcher 状态卡）
    "#fdecea": "#3a1f1f",
    "#f9d3d0": "#4a2626",
    "#f2b8b5": "#6b3230",
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
}

_SURFACE_D2L = {v: k for k, v in _SURFACE_L2D.items()}
_TEXT_D2L = {v: k for k, v in _TEXT_L2D.items()}

_TEXT_PROPS = {"color", "selection-color"}

# ==================== 样式重映射 ====================

_DECL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9\-]*)\s*:\s*([^;{}]+?)(?=\s*[;}]|$)")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _norm_hex(h):
    h = h.lower()
    if len(h) == 4:
        h = "#" + "".join(c * 2 for c in h[1:])
    return h


def _remap_hex_value(val, mapping):
    def sub(m):
        return mapping.get(_norm_hex(m.group(0)), m.group(0))
    return _HEX_RE.sub(sub, val)


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

    return _DECL_RE.sub(repl, ss)


# ==================== 主题状态与持久化 ====================

_state = {}
_orig_set_stylesheet = None


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


def current_qss():
    return DARK_QSS if is_dark() else LIGHT_QSS


def apply_theme(app=None):
    """应用当前主题的全局样式表。

    QApplication 不是 QWidget 子类，其 setStyleSheet 未被补丁接管，
    直接调用即可（不会触发二次重映射）。
    """
    from PySide6.QtWidgets import QApplication
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(current_qss())


# ==================== setStyleSheet 自动重映射补丁 ====================

def install_auto_remap():
    """给 QWidget.setStyleSheet 打补丁：所有内联样式按当前主题自动换色。

    覆盖构造期与运行期（定时刷新/动态加行/弹窗）的全部 setStyleSheet 调用；
    构造函数 styleSheet= 关键字参数不走 Python 层，由 adapt()/show_page 兜底。
    """
    global _orig_set_stylesheet
    if _orig_set_stylesheet is not None:
        return
    from PySide6.QtWidgets import QWidget
    _orig_set_stylesheet = QWidget.setStyleSheet

    def _patched(self, ss=None):
        try:
            if ss and is_dark():
                ss = remap_stylesheet(ss, True)
        except Exception:
            pass
        _orig_set_stylesheet(self, ss or "")

    QWidget.setStyleSheet = _patched


def adapt(root):
    """把 root 子树上所有已设置的内联样式重映射到当前主题（幂等）。"""
    if root is None:
        return
    from PySide6.QtWidgets import QWidget
    dark = is_dark()
    widgets = [root] + root.findChildren(QWidget)
    for w in widgets:
        ss = w.styleSheet()
        if not ss:
            continue
        new = remap_stylesheet(ss, dark)
        if new != ss:
            w.setStyleSheet(new)


def retheme_all():
    """主题切换后：重映射全部顶层控件（含主窗口、已打开弹窗）的现有样式。"""
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        adapt(w)
