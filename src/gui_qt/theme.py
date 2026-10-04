"""全局明暗主题（黑白可切换）。

方案（2026-10 性能重构）：
- 全局外观：一套与主题无关的 NEUTRAL_QSS（颜色全部走 palette() 角色），
  启动时设置一次；主题色由浅/深两套 QPalette 承载，切换 = app.setPalette
  （实测 36 页全加载：setStyleSheet 全量 repolish 15~20s，setPalette ≈0.03s）
- 页面内联样式：install_auto_remap() 给 QWidget.setStyleSheet 打补丁，
  按当前主题自动重映射 background/border/color 里的十六进制色值
  （属性感知：`color:` 只走文字色映射，避免误伤按钮白字；
   选择器安全：QPushButton:hover 等不会被改写）
- 切换时：retheme_all() 重映射可见控件（主窗口未激活的懒加载页跳过，
  show_page 切回时再 adapt，幂等），避免几十个页面全量重映射卡顿
- 持久化：config["theme"] = "light" | "dark"
- 定时开关：check_auto_switch() 按配置时间/系统深浅色自动黑白（主窗口 QTimer 周期调用）

注意：刻意在两种主题下保持不变的色值（强调绿/红/蓝、视频区纯黑、
终端预览深底等）不进映射表，也不走 palette 角色。
"""

import re
import sys
from datetime import datetime

from toolbox.config_manager import load_convert_config, save_convert_config

NEUTRAL_QSS = """
QMainWindow, QWidget { background-color: palette(window); color: palette(window-text); font-family: "Microsoft YaHei"; font-size: 13px; }
QScrollArea { border: none; background: palette(window); }
QPushButton { background: palette(button); color: palette(button-text); padding: 6px 12px; border: 1px solid palette(shadow); border-radius: 4px; text-align: center; }
QPushButton:hover { background: #2e7d32; color: #ffffff; border-color: #3a8c3a; }
QPushButton:pressed { background: #1a5c2a; color: #ffffff; }
QPushButton:checked { background: #1a5c2a; color: #ffffff; border-color: #2e7d32; }
QPushButton:disabled { background: palette(alternate-base); color: palette(placeholder-text); border-color: palette(midlight); }
QLineEdit, QListWidget, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 4px; padding: 6px; selection-background-color: palette(highlight); }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid palette(highlight); background: palette(base); }
QListWidget { padding: 2px; }
QListWidget::item { padding: 3px; border-radius: 3px; }
QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QListWidget::item:hover { background: palette(midlight); }
QTreeWidget, QTreeView, QTableWidget, QTableView { background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 4px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QHeaderView::section { background: palette(alternate-base); color: palette(placeholder-text); border: none; border-bottom: 1px solid palette(shadow); padding: 5px; }
QCheckBox { color: palette(window-text); spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background: #2e7d32; border: 1px solid #1a5c2a; border-radius: 3px; }
QCheckBox::indicator:unchecked { background: palette(button); border: 1px solid palette(mid); border-radius: 3px; }
QRadioButton::indicator:checked { background: palette(highlight); border: 1px solid palette(highlight); border-radius: 8px; }
QTabBar::tab { background: palette(alternate-base); color: palette(placeholder-text); padding: 8px 16px; border: 1px solid palette(shadow); border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1a5c2a; color: #ffffff; }
QTabBar::tab:hover { background: #2e7d32; color: #ffffff; }
QTabWidget::pane { border: 1px solid palette(shadow); background: palette(window); }
QProgressBar { background: palette(base); border: 1px solid palette(mid); border-radius: 4px; text-align: center; color: palette(text); }
QProgressBar::chunk { background: #2e7d32; border-radius: 3px; }
QScrollBar:vertical { background: palette(window); width: 12px; }
QScrollBar::handle:vertical { background: palette(mid); border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: palette(dark); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: palette(window); height: 12px; }
QScrollBar::handle:horizontal { background: palette(mid); border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: palette(dark); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: palette(window); color: palette(placeholder-text); border-top: 1px solid palette(shadow); }
QStatusBar::item { border: none; }
QLabel { color: palette(window-text); }
QMenuBar { background: palette(window); color: palette(window-text); }
QMenuBar::item:selected { background: #2e7d32; color: #ffffff; }
QMenu { background: palette(base); color: palette(text); border: 1px solid palette(mid); }
QMenu::item:selected { background: #2e7d32; color: #ffffff; }
QGroupBox { border: 1px solid palette(shadow); border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { color: palette(placeholder-text); subcontrol-origin: margin; left: 10px; }
QSpinBox, QDoubleSpinBox { padding: 3px 6px; }
QComboBox QAbstractItemView { background: palette(base); color: palette(text); selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }
QSlider::groove:horizontal { height: 6px; background: palette(mid); border-radius: 3px; }
QSlider::sub-page:horizontal { background: #2e7d32; border-radius: 3px; }
QSlider::handle:horizontal { background: palette(highlighted-text); border: 1px solid #9aa3b0; width: 12px; margin: -4px 0; border-radius: 6px; }
"""

# ==================== 浅色 / 深色调色板 ====================
# 角色取值对齐旧版 LIGHT_QSS / DARK_QSS 的十六进制色（个别近似）：
# shadow=按钮边框、mid=输入框边框、dark=滚动条悬停、midlight=列表悬停/禁用边框、
# alternate-base=表头/标签页底、placeholder-text=次级灰字（状态栏/表头/分组标题）。
_PALETTE_SPEC = {
    # role: (light, dark)
    "Window": ("#f4f5f7", "#1a1d27"),
    "WindowText": ("#2f3440", "#c6cbd6"),
    "Base": ("#ffffff", "#23262f"),
    "AlternateBase": ("#eef0f4", "#23262f"),
    "Text": ("#2f3440", "#c6cbd6"),
    "Button": ("#ffffff", "#23262f"),
    "ButtonText": ("#2f3440", "#c6cbd6"),
    "Midlight": ("#e3e9f2", "#23262f"),
    "Mid": ("#c8cdd6", "#333340"),
    "Dark": ("#9fb0c8", "#4a5a8c"),
    "Shadow": ("#c3c9d4", "#2e3140"),
    "Highlight": ("#2a6a9c", "#2a5a8c"),
    "HighlightedText": ("#ffffff", "#ffffff"),
    "Link": ("#1976d2", "#64b5f6"),
    "PlaceholderText": ("#9aa0b0", "#888888"),
    "ToolTipBase": ("#ffffff", "#23262f"),
    "ToolTipText": ("#2f3440", "#c6cbd6"),
    # 禁用态文字灰（原生控件用；QSS 管理的控件走 :disabled 规则）
    "DisabledText": ("#9aa0b0", "#666677"),
}


def build_palette(dark: bool):
    """按主题构建 QPalette（浅/深两套，颜色对齐旧版 QSS）。"""
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
    """应用当前主题：中性 QSS 只设置一次（内容不变时跳过，全量 repolish 很贵），
    主题色由 QPalette 切换承载（毫秒级）。

    QApplication 不是 QWidget 子类，其 setStyleSheet 未被补丁接管，
    直接调用即可（不会触发二次重映射）。
    """
    global _applied_qss
    from PySide6.QtWidgets import QApplication
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    if _applied_qss != NEUTRAL_QSS:
        app.setStyleSheet(NEUTRAL_QSS)
        _applied_qss = NEUTRAL_QSS
    app.setPalette(current_palette())


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


def adapt(root, skip_subtrees=()):
    """把 root 子树上所有已设置的内联样式重映射到当前主题（幂等）。

    skip_subtrees: 跳过的子树根节点集合（如主窗口里未激活的懒加载页，
    由 show_page 切回时再补 adapt），避免一次重映射几十个页面。"""
    if root is None:
        return
    from PySide6.QtWidgets import QWidget
    dark = is_dark()
    skip = None
    for s in skip_subtrees or ():
        if s is None:
            continue
        if skip is None:
            skip = set()
        skip.add(s)
        skip.update(s.findChildren(QWidget))
    widgets = [root] + root.findChildren(QWidget)
    for w in widgets:
        if skip is not None and w in skip:
            continue
        ss = w.styleSheet()
        if not ss:
            continue
        new = remap_stylesheet(ss, dark)
        if new != ss:
            w.setStyleSheet(new)


def retheme_all(main_window=None):
    """主题切换后：重映射顶层控件的现有样式（含主窗口、已打开弹窗）。

    main_window 传入时，其懒加载未激活页跳过（show_page 切回时再 adapt），
    避免几十个页面全量重映射造成长卡顿。"""
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if main_window is not None and w is main_window:
            pages = getattr(main_window, "pages", {}) or {}
            current = main_window.stack.currentWidget()
            hidden = [p for p in pages.values() if p is not None and p is not current]
            adapt(w, skip_subtrees=hidden)
        else:
            adapt(w)
