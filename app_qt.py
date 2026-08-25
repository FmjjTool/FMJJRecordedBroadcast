"""PySide6 版桌面入口（与原版 app_gui.py 对应）。

启动：python app_qt.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication, QMessageBox

from gui_qt.main_window import MainWindow
from gui_qt import theme


def _headless_io():
    """无控制台启动（pythonw / 打包 --noconsole）时 stdout/stderr 为 None，
    print / rich / logging 会直接崩溃 — 统一补空流。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _suppress_child_consoles():
    """给 subprocess.Popen 补 CREATE_NO_WINDOW：
    ffmpeg / node 等子进程不再弹出黑窗（无论有无控制台都生效）；
    launcher/dycast 刻意要求 CREATE_NEW_CONSOLE 的调用不受影响。"""
    if os.name != "nt":
        return
    import subprocess
    _orig_init = subprocess.Popen.__init__

    def _patched(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & subprocess.CREATE_NEW_CONSOLE:
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        return _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched


_QT_NOISE = (
    # Windows 中文系统 Qt 字体回退链（雅黑/Tahoma/SimSun/Arial...）不含
    # 印度系文字字体，渲染抓取内容里的旁遮普语等外文时每条都刷警告
    "OpenType support missing",
    # Fixedsys 等老式位图字体无 DirectWrite FontFace，QTextEdit 等宽回退时刷
    "CreateFontFaceFromHDC",
    # 损坏/空图片数据缩略：占位图已兜底，无需每条警告
    "QPixmap::scaled: Pixmap is a null pixmap",
)

# "OpenType support missing" 实测是 QtInfoMsg（不是 warning），
# 只关 warning 级拦不住，必须连 info 级一起关
_FONTDB_RULES = "qt.text.font.db.info=false\nqt.text.font.db.warning=false"


def _install_qt_noise_filter():
    """吞掉已知无害的 Qt 运行期警告（setFilterRules 之外的兜底），
    其余消息照常输出到 stderr（带类别前缀，与 Qt 默认格式一致）。"""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def _handler(msg_type, context, message):
        if msg_type in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg,
                        QtMsgType.QtWarningMsg) and any(
                n in message for n in _QT_NOISE):
            return
        stream = sys.stderr or sys.__stderr__
        if stream:
            cat = getattr(context, "category", "") or ""
            line = f"{cat}: {message}" if cat and cat != "default" else message
            try:
                stream.write(line + "\n")
            except Exception:
                pass

    qInstallMessageHandler(_handler)


def main():
    _headless_io()
    # 任何启动方式（python/pythonw/打包 exe）下都不让子进程弹黑框
    _suppress_child_consoles()

    from toolbox.config_manager import init_all_configs
    init_all_configs()

    # 初始化日志（每次运行生成独立 log，上限可配置，见 max_log_files）。
    # console=False：GUI 模式控制台静默——下载进度/解析输出/Qt 警告只进
    # logs/app_*.log，不再刷 cmd（未处理异常仍回显控制台）
    try:
        from app_logger import setup_logging
        from toolbox.config_manager import load_convert_config
        setup_logging(max_files=int(load_convert_config().get("max_log_files", 50) or 50),
                      console=False)
    except Exception:
        pass  # 日志初始化失败不影响主程序

    # QtWebEngine 必须在 QApplication 之前导入（否则运行期创建内嵌浏览器
    # 会报 "must be imported before QCoreApplication"），缺组件时静默降级
    try:
        from PySide6.QtCore import Qt
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except ImportError:
        pass

    # Windows 中文系统的 Qt 字体回退链（雅黑/Tahoma/SimSun/Arial...）不含印度系文字字体，
    # 渲染抓取内容里的旁遮普语等外文时每条都刷 "OpenType support missing" 警告，关闭降噪
    from PySide6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules(_FONTDB_RULES)
    _install_qt_noise_filter()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 主题：先装 setStyleSheet 自动重映射补丁，再按配置应用浅色/深色 QSS
    theme.init_theme()
    theme.install_auto_remap()
    theme.apply_theme(app)

    win = MainWindow()
    win.show()

    # ffmpeg 检测（缺失时提示，不影响启动）
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
    except Exception:
        QMessageBox.warning(
            win, "⚠️ 依赖缺失",
            "未检测到 ffmpeg！\n\n视频转换和音频提取功能将不可用。\n"
            "请安装 ffmpeg 并添加到系统 PATH。\n下载: https://ffmpeg.org/download.html"
        )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
