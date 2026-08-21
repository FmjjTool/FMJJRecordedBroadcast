"""PySide6 版桌面入口（与原版 app_gui.py 对应）。

启动：python app_qt.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication, QMessageBox

from gui_qt.main_window import MainWindow, LIGHT_QSS


def main():
    from toolbox.config_manager import init_all_configs
    init_all_configs()

    # 初始化日志（每次运行生成独立 log，上限可配置，见 max_log_files）
    try:
        from app_logger import setup_logging
        from toolbox.config_manager import load_convert_config
        setup_logging(max_files=int(load_convert_config().get("max_log_files", 50) or 50))
    except Exception:
        pass  # 日志初始化失败不影响主程序

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_QSS)

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
