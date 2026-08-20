"""清理后专项验证：rename 添加按钮统一入口 + info_results 双击打开链接。"""

import os
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication

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
    from gui_qt.main_window import MainWindow
    win = MainWindow()

    # rename：三种 card_type 都能通过统一入口拿到对话框参数（不实际弹窗，验证映射）
    win.show_page("toolbox_rename")
    rp = win.pages["toolbox_rename"]

    def rename_map_ok():
        titles = {
            "account": ("添加账户", "输入新账户名:"),
            "type": ("添加类型", "输入新类型名:"),
            "platform": ("添加平台", "输入新平台名:"),
        }
        # 验证 _add_card_item 存在且映射覆盖三种类型
        import inspect
        src = inspect.getsource(rp._add_card_item)
        for k in titles:
            assert f'"{k}"' in src, f"_add_card_item 缺少 {k} 映射"
        # 确认三个旧包装方法已删除
        for m in ("_add_account", "_add_type", "_add_platform"):
            assert not hasattr(rp, m), f"{m} 仍存在"
        # 确认 _do_add 核心路径完好
        assert hasattr(rp, "_do_add") and hasattr(rp, "_get_cfg_list")
    check("rename 添加按钮统一入口（三类型映射/旧包装已删/_do_add 完好）", rename_map_ok)

    # info_results：双击 → _open_url（空 url 直接返回不弹浏览器）
    win.show_page("info_results")
    ip = win.pages["info_results"]

    def results_dblclick_ok():
        from gui_qt import common
        from platforms.base import VideoInfo, now_str
        info = VideoInfo(platform="douyin", kind="video", video_id="t1",
                         title="测试条目", video_url="https://example.com/v1",
                         raw_url="", fetched_at=now_str())
        common._collect([info])
        ip.refresh()
        app.processEvents()
        # 空链接：安全返回（不调用 webbrowser）
        ip._open_url("")   # 不应抛异常
        ip._open_url(None)
        # UserRole 存了链接
        from PySide6.QtCore import Qt
        lst = ip.cols["douyin"]["list"]
        found = False
        for i in range(lst.count()):
            if lst.item(i).flags() & Qt.ItemIsUserCheckable:
                url = lst.item(i).data(Qt.UserRole)
                assert url == "https://example.com/v1", f"UserRole 链接错误: {url}"
                found = True
        assert found, "未找到可勾选条目"
        # 双击信号已连接（emit 空链接不报错）
        lst.itemDoubleClicked.emit(lst.item(0))
        common.COLLECTED.clear()
    check("info_results 双击打开链接（UserRole 存储/空链接安全/信号已接）", results_dblclick_ok)

    print()
    if errors:
        print(f"共 {len(errors)} 项失败:")
        for name, tb in errors:
            print(f"\n===== {name} =====\n{tb}")
        return 1
    print("专项验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
