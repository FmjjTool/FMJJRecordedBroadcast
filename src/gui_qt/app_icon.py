"""Windows 任务栏身份与开发/打包共用图标。"""
import os
import sys
from pathlib import Path


def configure_app_icon(app):
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FmjjTool.FengMingToolbox")
    from PySide6.QtGui import QIcon
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    icon = QIcon(str(root / "icon.ico"))
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
