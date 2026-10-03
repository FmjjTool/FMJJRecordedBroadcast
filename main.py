"""PySide6 版桌面入口。

启动：python main.py
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


def _minimize_console():
    """开发模式（python 直接启动、带控制台）时把 cmd 窗口自动最小化，
    避免挡住 GUI。打包 --noconsole / pythonw 启动时无控制台句柄，自动跳过。

    ConPTY 伪控制台（Windows Terminal / IDE 终端 / 隐藏启动的 python）必须用
    SW_HIDE 整体隐藏：这类窗口没有任务栏按钮，SW_MINIMIZE 会把它弹成屏幕
    左下角无法关闭的浮动小方块（PseudoConsoleWindow，只能随程序退出消失）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        u32 = ctypes.windll.user32
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "PseudoConsoleWindow" or not u32.IsWindowVisible(hwnd):
            u32.ShowWindow(hwnd, 0)   # SW_HIDE：伪控制台/已隐藏窗口直接隐藏
        else:
            u32.ShowWindow(hwnd, 6)   # SW_MINIMIZE：真实控制台最小化到任务栏
    except Exception:
        pass


def _suppress_child_consoles():
    """给 subprocess.Popen 补 CREATE_NO_WINDOW：
    ffmpeg / node 等子进程不再弹出黑窗（无论有无控制台都生效）；
    launcher 刻意要求 CREATE_NEW_CONSOLE 的调用不受影响。"""
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
    # 开发模式启动后把 cmd 控制台窗口自动最小化（打包/pythonw 无控制台则跳过）
    _minimize_console()

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

    # QtWebEngine 已随内嵌登录改造移除（登录走系统 Edge + Playwright 嵌入），
    # AA_ShareOpenGLContexts 保留给 QtMultimedia 视频预览的 GL 上下文共享
    from PySide6.QtCore import Qt
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

    # Windows 中文系统的 Qt 字体回退链（雅黑/Tahoma/SimSun/Arial...）不含印度系文字字体，
    # 渲染抓取内容里的旁遮普语等外文时每条都刷 "OpenType support missing" 警告，关闭降噪
    from PySide6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules(_FONTDB_RULES)
    _install_qt_noise_filter()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 窗口/任务栏图标：开发模式读项目根 icon.ico；
    # 打包后不随包携带 ico，Qt 自动回落到 exe 内嵌图标（PyInstaller --icon）
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    if os.path.exists(icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))
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

    # 启动时检测浏览器环境（注册表读取，毫秒级）：Edge/Chrome 版本记日志，
    # 三者全缺时提示一次（采集需要浏览器内核，可在 Cookie 管理里按需下载）
    try:
        import logging
        from toolbox.browser import detect
        env = detect()
        logging.getLogger("startup").info(
            "浏览器环境：Edge=%s Chrome=%s 采集引擎=%s",
            env["edge"] or "无", env["chrome"] or "无",
            "已下载" if env["shell"] else "未下载")
        if not (env["edge"] or env["chrome"] or env["shell"]):
            QMessageBox.information(
                win, "浏览器环境",
                "未检测到系统 Edge / Chrome，也未下载过采集引擎。\n\n"
                "数据采集功能需要浏览器内核，首次使用时会提示下载（约 91 MB），\n"
                "也可现在到「Cookie 管理 → 下载采集引擎」提前下载。")
    except Exception:
        pass

    code = app.exec()

    # —— 退出兜底：确保 python 进程必然结束（根治退出后 CLI 窗口残留）——
    # Python 3.9+ 起 ThreadPoolExecutor 的工作线程是「非 daemon」线程：解释器
    # 退出时 concurrent.futures 注册的 _python_exit 会对它们做无超时 join。
    # 只要还有一个采集/下载任务卡在网络读上（无超时请求、count=0 全量 9999 页
    # 翻页、Playwright 兜底滚动），python.exe 就永远退不出去——开发模式下被
    # 最小化到任务栏的控制台窗口（cmd / PseudoConsole）会一直挂着，直到手动
    # 关闭（GUI 已消失但进程未死）。
    # closeEvent 已同步完成所有必须落盘的清理（停录制并同步打包回放包、终止
    # 启动器进程树），这里显式执行 atexit 收尾（日志落盘、回放临时目录清理）
    # 后强制结束进程：后台残余线程（守护线程 + 卡住的 executor 工作线程）一并
    # 带走，SQLite 落库本身是事务性写入，强退不伤数据。
    try:
        import atexit as _atexit
        _atexit._run_exitfuncs()  # 私有 API 但 3.8~3.13 稳定存在；幂等可重入
    except Exception:
        pass
    try:
        import logging
        logging.shutdown()
    except Exception:
        pass
    os._exit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    main()
