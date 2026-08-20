"""修复验证：toast QFont、跨线程桥无 disconnect 警告、整行点击切换复选框。"""

import os
import sys
import traceback
import warnings

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel

errors = []


def check(name, fn):
    try:
        fn()
        print(f"  OK  {name}")
    except Exception:
        errors.append((name, traceback.format_exc()))
        print(f"  FAIL {name}")


def main():
    app = QApplication(sys.argv)

    # ---- 1. Toast（原 QFont(size=14) 崩溃点）----
    def toast_ok():
        import warnings as w
        with w.catch_warnings():
            w.simplefilter("error")  # 任何警告都变成异常
            from gui_qt.toast import show_toast
            for kind in ("info", "success", "warning", "error"):
                t = show_toast(None, f"测试 {kind}", kind=kind, duration=10)
                app.processEvents()
    check("toast 四种类型创建（QFont 修复）", toast_ok)

    # ---- 2. 跨线程桥：高频 post_to_main / run_bg 不产生 RuntimeWarning ----
    def bridge_ok():
        import gui_qt.common as common

        fired = {"n": 0}
        done = {"n": 0}

        def bump():
            fired["n"] += 1

        def bg_done(result, err):
            done["n"] += 1

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # disconnect 警告会让测试直接失败
            for _ in range(50):
                common.post_to_main(bump)
            for _ in range(5):
                common.run_bg(lambda: 42, bg_done)
            for _ in range(40):  # 处理排队事件
                app.processEvents()

        import time
        t0 = time.time()
        while (fired["n"] < 50 or done["n"] < 5) and time.time() - t0 < 5:
            app.processEvents()
        assert fired["n"] == 50, f"post_to_main 只触发 {fired['n']}/50"
        assert done["n"] == 5, f"run_bg 只完成 {done['n']}/5"
    check("post_to_main×50 + run_bg×5 无警告全部送达", bridge_ok)

    # ---- 3. RowCheckList：点整行切换，点复选框不双重切换 ----
    from gui_qt.row_widgets import RowCheckList, make_row_clickable

    def row_list_ok():
        lw = RowCheckList()
        from PySide6.QtWidgets import QListWidgetItem
        it = QListWidgetItem("20260819.2026-08-19 19-52-41_封号回游_聊天_无弹幕_抖音.mp4  (5.23 GB)")
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Unchecked)
        lw.addItem(it)
        lw.resize(600, 100)

        # 3a. 点行尾文字区域（远离左侧复选框）→ 应被勾选
        rect = lw.visualRect(lw.model().index(0, 0))
        assert rect.width() > 200, f"行宽异常: {rect.width()}"
        QTest.mouseClick(lw.viewport(), Qt.LeftButton, pos=QPoint(rect.width() - 60, rect.center().y()))
        assert it.checkState() == Qt.Checked, "点击整行未勾选"

        # 3b. 再点行尾 → 取消
        QTest.mouseClick(lw.viewport(), Qt.LeftButton, pos=QPoint(rect.width() - 60, rect.center().y()))
        assert it.checkState() == Qt.Unchecked, "点击整行未取消"

        # 3c. 点复选框本身 → 恰好切换一次（不双重切换）
        opt_x = 8  # 指示框大致位置（左缘 + 边距）
        QTest.mouseClick(lw.viewport(), Qt.LeftButton, pos=QPoint(opt_x, rect.center().y()))
        assert it.checkState() == Qt.Checked, "点击复选框未勾选"
    check("RowCheckList 整行点击切换/复选框单次切换", row_list_ok)

    # ---- 4. make_row_clickable：自定义行整行切换 ----
    def custom_row_ok():
        row = QFrame()
        h = QHBoxLayout(row)
        cb = QCheckBox()
        lbl = QLabel("测试歌曲 - 歌手  (3:30)")
        h.addWidget(cb)
        h.addWidget(lbl, 1)
        row.resize(500, 30)
        row.show()  # 激活布局（复选框 geometry 才有效）
        app.processEvents()
        make_row_clickable(row, cb)
        assert cb.checkState() == Qt.Unchecked

        # 点标签区域（复选框右侧）→ 切换
        QTest.mouseClick(lbl, Qt.LeftButton, pos=QPoint(30, 10))
        assert cb.checkState() == Qt.Checked, "点击行文字未勾选"

        # 再点行空白 → 取消
        QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(480, 15))
        assert cb.checkState() == Qt.Unchecked, "点击行空白未取消"

        # 点复选框本身 → 单次切换
        QTest.mouseClick(cb, Qt.LeftButton, pos=QPoint(5, 5))
        assert cb.checkState() == Qt.Checked, "点击复选框未勾选"
    check("make_row_clickable 自定义行整行切换", custom_row_ok)

    print()
    if errors:
        print(f"共 {len(errors)} 项失败:")
        for name, tb in errors:
            print(f"\n===== {name} =====\n{tb}")
        return 1
    print("修复验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
