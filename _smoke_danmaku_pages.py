# -*- coding: utf-8 -*-
"""冒烟：弹幕姬页（抖音 / B站）模式精简后仍正常。

验证（对应用户要求「只保留仅弹幕 + 回放，录播迁到内置录播」）：
  1. 模式下拉只有「仅弹幕模式」「回放模式」
  2. 仅弹幕模式：视频卡隐藏、弹幕卡占满中部
  3. 回放模式：视频卡 + 回放控制行出现
  4. 视频预览 / 清晰度 / 回放包录制 / 自动回放包录制 控件已全部移除
  5. 老配置残留（mode=normal / video_only）自动兜底到仅弹幕
  6. 切页/退出生命周期不再调用录制相关方法

运行：QT_QPA_PLATFORM=offscreen python _smoke_danmaku_pages.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


class FakeApp:
    work_dir = tempfile.mkdtemp()

    def __init__(self):
        self.pages = {}

    def toast(self, *a, **k):
        pass

    def show_page(self, *a, **k):
        pass


def main():
    app = QApplication.instance() or QApplication([])
    from gui_qt.pages.test_page import TestPage
    from gui_qt.pages.bili_danmaku import BiliDanmakuPage
    from toolbox import douyin_live, bili_live

    # 隔离设置读写：冒烟不污染真实 config（切模式会落盘 mode）
    store = {}
    for mod in (douyin_live, bili_live):
        mod.load_settings = lambda: dict(store)
        mod.save_settings = lambda d: (store.clear(), store.update(d))

    for cls in (TestPage, BiliDanmakuPage):
        tag = cls.__name__
        page = cls(FakeApp())
        page.show()
        app.processEvents()

        labels = [page.mode_combo.itemText(i) for i in range(page.mode_combo.count())]
        check(tag + " 只有两种模式", labels == ["仅弹幕模式", "回放模式"], str(labels))
        check(tag + " 默认仅弹幕", page.mode == "danmaku_only", page.mode)

        # 仅弹幕：视频卡隐藏、弹幕卡可见
        check(tag + " 仅弹幕隐藏视频卡", page.video_card.isHidden())
        check(tag + " 仅弹幕弹幕卡可见", not page.chat_card.isHidden())

        # 录制 / 预览相关控件已移除
        for attr in ("record_btn", "auto_record_var", "quality_combo",
                     "live_ctrl_row_widget", "rec_time_lbl"):
            check(tag + " 已移除 " + attr, not hasattr(page, attr))
        for meth in ("_start_recording", "_stop_recording", "_toggle_record",
                     "_start_live_preview", "_stop_live_preview",
                     "_on_auto_record_toggle", "_restart_recording_after_switch"):
            check(tag + " 已移除方法 " + meth, not hasattr(page, meth))

        # 回放模式：视频卡 + 回放控制行
        page.mode_combo.setCurrentIndex(1)
        app.processEvents()
        check(tag + " 回放显示视频卡", not page.video_card.isHidden())
        check(tag + " 回放显示控制行", not page.replay_row_widget.isHidden())
        check(tag + " 回放标题正确", "回放" in page.video_title.text(),
              page.video_title.text())

        # 切回仅弹幕
        page.mode_combo.setCurrentIndex(0)
        app.processEvents()
        check(tag + " 切回仅弹幕隐藏视频卡", page.video_card.isHidden())

        # 生命周期不炸（未连接时 shutdown 直接走）
        page.on_show()
        page.on_hide()
        page.shutdown()
        check(tag + " 生命周期无异常", True)
        page.close()

    # 老配置残留兜底（normal / video_only 已下线）
    for old in ("normal", "video_only"):
        store["mode"] = old
        page = TestPage(FakeApp())
        check("老配置 %s 兜底为仅弹幕" % old, page.mode == "danmaku_only", page.mode)
        check("兜底后下拉显示仅弹幕[%s]" % old,
              page.mode_combo.currentText() == "仅弹幕模式",
              page.mode_combo.currentText())
        check("兜底后视频卡隐藏[%s]" % old, page.video_card.isHidden())
        page.close()

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
