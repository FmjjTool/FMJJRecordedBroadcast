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
    """无控制台时给 subprocess.Popen 补 CREATE_NO_WINDOW：
    ffmpeg / node 等子进程不再弹出黑窗；launcher/dycast 刻意要求
    CREATE_NEW_CONSOLE 的调用不受影响。"""
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


def main():
    _headless_io()
    # 无控制台环境（pythonw 启动或打包后）隐藏子进程黑窗
    if getattr(sys, "frozen", False) or sys.__stdout__ is None:
        _suppress_child_consoles()

    from toolbox.config_manager import init_all_configs
    init_all_configs()

    # 初始化日志（每次运行生成独立 log，上限可配置，见 max_log_files）
    try:
        from app_logger import setup_logging
        from toolbox.config_manager import load_convert_config
        setup_logging(max_files=int(load_convert_config().get("max_log_files", 50) or 50))
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
    QLoggingCategory.setFilterRules("qt.text.font.db.warning=false")

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
