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
    if key in ("toolbox_subtitle", "tcdoc", "localwiki", "wechat_article", "rename_batch"):
        return "document"
    if key in ("tasks", "toolbox_calendar"):
        return "calendar"
    if key in ("image_crop", "image_sorter", "cover_maker", "bili_resource"):
        return "image"
    if key in ("video_comments", "fan_groups", "weibo_chaohua"):
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
    elif name == "clipboard":
        rect(5, 4, 14, 17, 2)
        rect(9, 2.5, 6, 3.5, 1)
        line(8, 10, 16, 10)
        line(8, 14, 16, 14)
        line(8, 18, 13, 18)
    elif name == "chip":
        rect(6, 6, 12, 12, 2)
        rect(9.5, 9.5, 5, 5, 1)
        line(12, 2.5, 12, 6)
        line(12, 18, 12, 21.5)
        line(2.5, 12, 6, 12)
        line(18, 12, 21.5, 12)
    elif name == "compass":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        path = QPainterPath(QPointF(15.5, 8.5))
        path.lineTo(13, 13)
        path.lineTo(8.5, 15.5)
        path.lineTo(11, 11)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "edit":
        path = QPainterPath(QPointF(4, 20))
        path.lineTo(5.2, 15.8)
        path.lineTo(16, 5)
        path.lineTo(19, 8)
        path.lineTo(8.2, 18.8)
        path.closeSubpath()
        painter.drawPath(path)
        line(14, 7, 17, 10)
    elif name == "play":
        path = QPainterPath(QPointF(7.5, 5))
        path.lineTo(19, 12)
        path.lineTo(7.5, 19)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "stop":
        rect(6.5, 6.5, 11, 11, 2)
    elif name == "save":
        path = QPainterPath(QPointF(5, 4))
        path.lineTo(16, 4)
        path.lineTo(20, 8)
        path.lineTo(20, 20)
        path.lineTo(5, 20)
        path.closeSubpath()
        painter.drawPath(path)
        rect(8.5, 13.5, 7, 6.5, 1)
        line(9, 4.5, 9, 9)
        line(15, 4.5, 15, 9)
    elif name == "copy":
        rect(4, 4, 11, 11, 2)
        rect(9, 9, 11, 11, 2)
    elif name == "download":
        line(12, 3.5, 12, 15)
        line(7, 10.5, 12, 15.5)
        line(17, 10.5, 12, 15.5)
        line(4.5, 20, 19.5, 20)
    elif name == "check":
        path = QPainterPath(QPointF(4.5, 12.5))
        path.lineTo(9.5, 17.5)
        path.lineTo(19.5, 6.5)
        painter.drawPath(path)
    elif name == "globe":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        painter.drawEllipse(QRectF(8.5, 3.5, 7, 17))
        line(3.5, 12, 20.5, 12)
    elif name == "pulse":
        path = QPainterPath(QPointF(2.5, 12))
        path.lineTo(7, 12)
        path.lineTo(9.5, 5.5)
        path.lineTo(13, 18.5)
        path.lineTo(15.5, 12)
        path.lineTo(21.5, 12)
        painter.drawPath(path)
    elif name == "robot":
        rect(5, 8, 14, 11, 2)
        line(12, 8, 12, 5)
        painter.drawEllipse(QRectF(11, 2.5, 2, 2))
        line(9.5, 12.5, 9.5, 14.5)
        line(14.5, 12.5, 14.5, 14.5)
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
    elif name == "win_min":
        # 窗口控制图标：线条比导航图标细，贴近系统标题栏按钮的观感
        painter.setPen(QPen(QColor(color), 1.4, Qt.SolidLine, Qt.RoundCap))
        line(6, 12, 18, 12)
    elif name == "win_max":
        painter.setPen(QPen(QColor(color), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        rect(6.5, 6.5, 11, 11, 1.2)
    elif name == "win_restore":
        painter.setPen(QPen(QColor(color), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(9.5, 6.5))
        path.lineTo(17.5, 6.5)
        path.lineTo(17.5, 14.5)
        painter.drawPath(path)
        rect(6.5, 9.5, 8, 8, 1.2)
    elif name == "win_close":
        painter.setPen(QPen(QColor(color), 1.4, Qt.SolidLine, Qt.RoundCap))
        line(6.5, 6.5, 17.5, 17.5)
        line(17.5, 6.5, 6.5, 17.5)
    else:
        for x in (4, 14):
            for y in (4, 14):
                rect(x, y, 6, 6, 1.5)
    painter.end()
    return QIcon(pixmap)
