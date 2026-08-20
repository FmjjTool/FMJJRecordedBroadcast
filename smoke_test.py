"""冒烟测试：离屏实例化主窗口 + 加载全部页面 + 触发 on_show/on_dir_changed。

用法: python smoke_test.py
"""

import os
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication

errors = []


def main():
    app = QApplication(sys.argv)

    from gui_qt.main_window import MainWindow, PAGE_CLASSES
    win = MainWindow()

    for key in PAGE_CLASSES:
        try:
            win.show_page(key)
            page = win.pages[key]
            app.processEvents()
            if hasattr(page, "on_show"):
                page.on_show()
            app.processEvents()
            print(f"  OK  {key}")
        except Exception as e:
            errors.append((key, traceback.format_exc()))
            print(f"  FAIL {key}: {e}")

    # 模拟目录切换广播
    try:
        win._refresh_all()
        app.processEvents()
        print("  OK  _refresh_all 广播")
    except Exception as e:
        errors.append(("_refresh_all", traceback.format_exc()))
        print(f"  FAIL _refresh_all: {e}")

    print()
    if errors:
        print(f"共 {len(errors)} 个页面加载失败:")
        for key, tb in errors:
            print(f"\n===== {key} =====\n{tb}")
        return 1
    print(f"全部 {len(PAGE_CLASSES)} 个页面加载成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
