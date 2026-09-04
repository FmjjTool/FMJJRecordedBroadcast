"""业务常量（迁移自源项目 utils/constants.py）。

与源版本的差异：
- STATUS_COLORS 由 QColor 改为 hex 字符串（QSS / theme.adapt 自动深浅映射）
- DEFAULT_CONFIG 移至 settings.py（config.json「image_sorter」节）
"""

from __future__ import annotations

from enum import Enum


# 支持的媒体格式
# - HEIC/HEIF: iPhone 常见格式
# - LIVP: 华为/荣耀等机型实况图封装格式
SUPPORTED_IMAGE_FORMATS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".livp",
}

SUPPORTED_VIDEO_FORMATS = {".mp4", ".avi", ".mov", ".flv", ".m4v"}
SUPPORTED_FORMATS = SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS


# 系统内置文件夹名称
BUILTIN_FOLDERS = {
    "default": "默认",
    "favorite": "收藏",
    "deleted": "删除",
    "duplicate": "重复文件",
}


class FileStatus(Enum):
    """文件状态枚举"""

    NORMAL = "normal"
    KEPT = "kept"  # 保留
    FAVORITED = "favorited"  # 收藏
    DELETED = "deleted"  # 删除
    DUPLICATE = "duplicate"  # 重复
    CORRUPTED = "corrupted"  # 损坏
    SELECTED = "selected"  # 选中


# 状态颜色映射（hex，走 theme.adapt 自动深浅色映射；红/绿等在深色下转浅色变体）
STATUS_COLORS = {
    FileStatus.KEPT: "#4caf50",
    FileStatus.FAVORITED: "#ffc107",
    FileStatus.DELETED: "#f44336",
    FileStatus.DUPLICATE: "#f44336",
    FileStatus.CORRUPTED: "#9e9e9e",
    FileStatus.SELECTED: "#2196f3",
}


# 缩略图配置
# 列表显示约为 176x176，缓存按约 1.8x 生成，兼顾清晰度与体积
THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_COLUMNS = 3


# 提示消息显示时长(秒)
MESSAGE_DURATION = 3
TOOLTIP_DURATION = 2


# 文件大小单位换算
SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]
SIZE_UNIT_STEP = 1024


# 码率单位
BITRATE_UNITS = ["bps", "Kbps", "Mbps"]
BITRATE_UNIT_STEP = 1000


# 懒加载缩略图调优参数
LAZY_INITIAL_BATCH_SIZE = 30        # 首屏加载数量
LAZY_STEP_BATCH_SIZE = 15           # 每次增量加载数量
LAZY_UNLOAD_BATCH_SIZE = 15         # 每次回收的缩略图数量
LAZY_THUMBNAIL_RESIDENT_LIMIT = 180  # 缩略图常驻内存上限
LAZY_UNLOAD_VISIBLE_LOOKAROUND = 8   # 可见区保护窗口（不回收）
LAZY_UNLOAD_FOCUS_RADIUS = 12        # 焦点保护半径（不回收）
PENDING_THUMB_MAX = 120              # 待补图队列最大长度
