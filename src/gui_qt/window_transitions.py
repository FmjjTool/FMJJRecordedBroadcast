"""Windows 原生窗口过渡；不模拟快照、透明度或逐帧窗口几何。

显示/隐藏/关闭保留 Qt 的正常窗口生命周期；DWM 决定系统过渡效果。
最大化、最小化和还原直接使用 ShowWindow，避免旧 Qt 对无边框窗口的
MoveWindow 特殊分支。开关只控制当前 HWND，不改系统全局动画设置。
"""

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication


_GWL_STYLE = -16
_NATIVE_FRAME_STYLE = 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
_SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010
_WM_NCCALCSIZE = 0x0083
_DWMWA_TRANSITIONS_FORCEDISABLED = 3
_SW_MINIMIZE = 6
_SW_MAXIMIZE = 3
_SW_RESTORE = 9


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _WindowsApi:
    """明确声明指针宽度，避免64位 HWND 被 ctypes 默认 int 截断。"""

    def __init__(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.set_pos = user32.SetWindowPos
        self.set_pos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.set_pos.restype = wintypes.BOOL
        self.show = user32.ShowWindow
        self.show.argtypes = [wintypes.HWND, ctypes.c_int]
        self.show.restype = wintypes.BOOL
        self.is_zoomed = user32.IsZoomed
        self.is_zoomed.argtypes = [wintypes.HWND]
        self.is_zoomed.restype = wintypes.BOOL
        self.monitor_from_window = user32.MonitorFromWindow
        self.monitor_from_window.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.monitor_from_window.restype = wintypes.HMONITOR
        self.monitor_info = user32.GetMonitorInfoW
        self.monitor_info.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
        self.monitor_info.restype = wintypes.BOOL
        self.set_attribute = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        self.set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD]
        self.set_attribute.restype = ctypes.c_long


class WindowTransitions(QObject):
    """为无边框主窗保留系统窗口能力，状态与还原位置由 Windows 管理。"""

    def __init__(self, window, enabled=True):
        super().__init__(window)
        self.window = window
        self.enabled = bool(enabled)
        self._api = None
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            try:
                self._api = _WindowsApi()
            except (AttributeError, OSError):
                pass

    def prepare_show(self):
        """首次显示前保留系统frame样式，非客户区由 nativeEvent 隐去。"""
        if self._api is None:
            return False
        hwnd = int(self.window.winId())
        style = self._api.get_style(hwnd, _GWL_STYLE)
        if not style:
            return False
        wanted = style | _NATIVE_FRAME_STYLE
        if style != wanted:
            ctypes.set_last_error(0)
            self._api.set_style(hwnd, _GWL_STYLE, wanted)
            if ctypes.get_last_error():
                return False
            self._api.set_pos(hwnd, None, 0, 0, 0, 0, _SWP_FRAMECHANGED)
        disabled = wintypes.BOOL(not self.enabled)
        self._api.set_attribute(hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED,
                                ctypes.byref(disabled), ctypes.sizeof(disabled))
        return True

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.prepare_show()

    def minimize(self):
        if self.window.isVisible() and self.prepare_show():
            self._api.show(int(self.window.winId()), _SW_MINIMIZE)
        else:
            self.window.showMinimized()

    def restore(self):
        if self.window.isVisible() and self.prepare_show():
            self._api.show(int(self.window.winId()), _SW_RESTORE)
        else:
            self.window.showNormal()

    def toggle_max_restore(self):
        if self.window.isVisible() and self.prepare_show():
            hwnd = int(self.window.winId())
            command = _SW_RESTORE if self._api.is_zoomed(hwnd) else _SW_MAXIMIZE
            self._api.show(hwnd, command)
        elif self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def native_event(self, message):
        if self._api is None:
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != _WM_NCCALCSIZE or not msg.wParam or not msg.lParam:
            return False, 0
        # 保留 WS_CAPTION/WS_THICKFRAME 给 DWM，但不显示第二套系统标题栏。
        # 最大化时客户区取实际工作区，防止系统缩放边框遮住顶栏或任务栏。
        if self._api.is_zoomed(msg.hwnd) and not self.window.isFullScreen():
            monitor = self._api.monitor_from_window(msg.hwnd, 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if self._api.monitor_info(monitor, ctypes.byref(info)):
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                rect = params.rgrc[0]
                rect.left, rect.top = info.rcWork.left, info.rcWork.top
                rect.right, rect.bottom = info.rcWork.right, info.rcWork.bottom
        return True, 0
