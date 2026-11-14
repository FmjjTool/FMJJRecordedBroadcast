"""工作台线形图标：Qt 原生绘制，按颜色缓存，不依赖外部字体或图片。"""

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def tool_icon_name(key):
    if key == "home":
        return "grid"
    if key in ("info_accounts", "douyin_index"):
        return "search"
    if key in ("info_results", "asset", "toolbox_organize", "toolbox_migrate"):
        return "folder"
    if key in ("tracker", "music_tracker"):
        return "chart"
    if key in ("toolbox_convert", "toolbox_editor", "test_page", "bili_danmaku"):
        return "video"
    if key.startswith("music") or key == "toolbox_audio":
        return "music"
    if key in ("toolbox_subtitle", "tcdoc", "wechat_article", "rename_batch"):
        return "document"
    if key in ("tasks", "toolbox_calendar"):
        return "calendar"
    if key in ("image_crop", "image_sorter", "cover_maker", "bili_resource"):
        return "image"
    if key in ("video_comments", "fan_groups"):
        return "message"
    if key == "global_settings":
        return "settings"
    if key == "toolbox_launcher":
        return "arrow"
    return "grid"


@lru_cache(maxsize=256)
def line_icon(name, color, size=20):
    """返回指定颜色的图标；以 2 倍分辨率绘制，兼顾高 DPI。"""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def rect(x, y, w, h, radius=2):
        painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    if name == "search":
        painter.drawEllipse(QRectF(4, 3.5, 12, 12))
        line(14, 14, 20, 20)
    elif name == "folder":
        path = QPainterPath(QPointF(3, 7))
        path.lineTo(3, 5)
        path.lineTo(9, 5)
        path.lineTo(11, 7)
        path.lineTo(21, 7)
        path.lineTo(21, 19)
        path.lineTo(3, 19)
        path.closeSubpath()
        painter.drawPath(path)
        line(3, 10, 21, 10)
    elif name == "chart":
        line(4, 4, 4, 20)
        line(4, 20, 21, 20)
        for x, top in ((8, 12), (13, 8), (18, 4)):
            line(x, top, x, 16)
    elif name == "video":
        rect(3, 5, 18, 14)
        path = QPainterPath(QPointF(10, 9))
        path.lineTo(15, 12)
        path.lineTo(10, 15)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "music":
        line(9, 17, 9, 6)
        line(9, 6, 19, 4)
        line(19, 4, 19, 15)
        painter.drawEllipse(QRectF(3, 15, 6, 5))
        painter.drawEllipse(QRectF(13, 13, 6, 5))
    elif name == "document":
        rect(5, 3, 14, 18)
        line(9, 8, 15, 8)
        line(9, 12, 15, 12)
        line(9, 16, 13, 16)
    elif name == "calendar":
        rect(3, 5, 18, 16)
        line(7, 3, 7, 7)
        line(17, 3, 17, 7)
        line(3, 10, 21, 10)
        line(8, 14, 10, 14)
        line(14, 14, 16, 14)
        line(8, 17, 10, 17)
    elif name == "image":
        rect(3, 4, 18, 16)
        painter.drawEllipse(QRectF(7, 7, 3, 3))
        path = QPainterPath(QPointF(4, 18))
        path.lineTo(10, 12)
        path.lineTo(14, 16)
        path.lineTo(17, 13)
        path.lineTo(21, 17)
        painter.drawPath(path)
    elif name == "message":
        rect(3, 4, 18, 14)
        line(6, 18, 6, 21)
        line(6, 21, 10, 18)
        line(7, 9, 17, 9)
        line(7, 13, 14, 13)
    elif name == "settings":
        for y, x in ((6, 9), (12, 16), (18, 7)):
            line(3, y, x - 2, y)
            line(x + 2, y, 21, y)
            painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
    elif name == "moon":
        path = QPainterPath(QPointF(16, 3.8))
        path.cubicTo(7, 0, 0, 12, 7, 19)
        path.cubicTo(13, 24, 21, 19, 21, 13)
        path.cubicTo(12, 17, 9, 8, 16, 3.8)
        painter.drawPath(path)
    elif name == "sun":
        painter.drawEllipse(QRectF(7, 7, 10, 10))
        for x1, y1, x2, y2 in ((12, 2, 12, 4), (12, 20, 12, 22),
                              (2, 12, 4, 12), (20, 12, 22, 12),
                              (5, 5, 6, 6), (18, 18, 19, 19),
                              (5, 19, 6, 18), (18, 6, 19, 5)):
            line(x1, y1, x2, y2)
    elif name == "refresh":
        painter.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 285 * 16)
        line(19, 3, 19, 9)
        line(14, 9, 19, 9)
    elif name == "arrow":
        line(5, 12, 19, 12)
        line(14, 7, 19, 12)
        line(14, 17, 19, 12)
    else:
        for x in (4, 14):
            for y in (4, 14):
                rect(x, y, 6, 6, 1.5)
    painter.end()
    return QIcon(pixmap)
