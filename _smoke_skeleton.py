"""冒烟测试：启动骨架页只占位内容区（去掉内部导航列）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

failures = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        failures.append(name)


from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])

from gui_qt.skeleton import SkeletonPage

sk = SkeletonPage()
sk.resize(900, 640)
sk.show()
app.processEvents()

# 1) 不再有 180px 固定宽的导航占位列
nav_like = [c for c in sk.findChildren(QWidget)
            if c.maximumWidth() == 180 and c.minimumWidth() == 180]
check("无固定 180px 导航占位列", not nav_like)

# 2) 骨架块（QFrame 灰块）铺满整个宽度附近：主区块宽度应接近页宽（左右仅 12px 边距）
from PySide6.QtWidgets import QFrame
blocks = [b for b in sk.findChildren(QFrame) if b.height() > 40]
check("存在主骨架区块", len(blocks) >= 1)
if blocks:
    main_block = max(blocks, key=lambda b: b.width())
    check(f"主区块横向铺满内容区（宽 {main_block.width()} / 页宽 900，边距 12）",
          main_block.width() >= 900 - 12 * 2 - 4)

# 3) 呼吸动画与提示文字仍在
from PySide6.QtCore import QAbstractAnimation
check("呼吸动画存在", sk._anim.state() == QAbstractAnimation.Running)
check("提示文字存在", sk._tip.text().startswith("⏳"))

# 4) 渲染截图（离屏无微软雅黑，中文或成方框——仅看布局结构）
img = sk.grab().toImage()
img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_smoke_skeleton.png"))
check("截图已保存", os.path.exists("_smoke_skeleton.png"))

# 5) 主窗口导入 + 骨架挂载点不受影响（构造 MainWindow 重量大，仅验证导入与 SkeletonPage 引用）
import gui_qt.main_window as mw
check("main_window 导入且仍引用 SkeletonPage",
      hasattr(mw.MainWindow, "_deferred_boot") and mw.SkeletonPage is SkeletonPage)

sk.hide()
print()
if failures:
    print(f"结果：{len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果：全部通过")
