"""gui_qt 包入口：导入时安装 emoji → 线形图标自动转换、滚轮守卫、下拉弹层圆角。"""

from gui_qt import emoji_icons as _emoji_icons

_emoji_icons.install()

from gui_qt import wheel_guard as _wheel_guard

_wheel_guard.install()