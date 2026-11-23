"""工作台线形图标：Qt 原生绘制，按颜色缓存，不依赖外部字体或图片。"""

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def tool_icon_name(key):
    if key == "home":
        return "grid"
    if key == "info_accounts":
        return "search"
    if key in ("info_results", "asset", "toolbox_organize", "toolbox_migrate"):
        return "folder"
    if key in ("tracker", "music_tracker", "douyin_index"):
        return "chart"
    if key in ("toolbox_convert", "toolbox_editor", "douyin_danmaku", "bili_danmaku"):
        return "video"
    if key.startswith("music") or key == "toolbox_audio":
        return "music"
    if key in ("toolbox_subtitle", "tcdoc", "localwiki", "wechat_article", "rename_batch",
               "certificate_maker"):
        return "document"
    if key in ("tasks", "toolbox_calendar"):
        return "calendar"
    if key in ("image_crop", "image_sorter", "cover_maker", "bili_resource"):
        return "image"
    if key in ("video_comments", "fan_groups", "weibo_chaohua"):
        return "message"
    if key == "global_settings":
        return "settings"
    if key == "proxy":
        return "globe"
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
    elif name == "trash":
        line(4, 7, 20, 7)
        line(9, 4, 15, 4)
        line(10.5, 4, 10.5, 7)
        line(13.5, 4, 13.5, 7)
        path = QPainterPath(QPointF(6, 7))
        path.lineTo(7, 20)
        path.lineTo(17, 20)
        path.lineTo(18, 7)
        painter.drawPath(path)
        line(10, 10.5, 10, 16.5)
        line(14, 10.5, 14, 16.5)
    elif name == "plus":
        line(12, 5, 12, 19)
        line(5, 12, 19, 12)
    elif name == "close":
        line(6, 6, 18, 18)
        line(18, 6, 6, 18)
    elif name == "upload":
        line(12, 20, 12, 8)
        line(7, 12.5, 12, 7.5)
        line(17, 12.5, 12, 7.5)
        line(4.5, 4, 19.5, 4)
    elif name == "target":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        painter.drawEllipse(QRectF(8.5, 8.5, 7, 7))
        painter.drawEllipse(QRectF(11, 11, 2, 2))
    elif name == "rocket":
        path = QPainterPath(QPointF(14, 4))
        path.cubicTo(17.5, 4, 20, 6.5, 20, 10)
        path.lineTo(13, 17)
        path.lineTo(7, 11)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QRectF(10.5, 8.5, 3.5, 3.5))
        line(7, 11, 4, 14)
        line(13, 17, 16, 20)
    elif name == "lock":
        rect(5, 10, 14, 10, 2)
        painter.drawArc(QRectF(8, 4, 8, 10), 0, 180 * 16)
        line(12, 13.5, 12, 16.5)
    elif name == "unlock":
        rect(5, 10, 14, 10, 2)
        path = QPainterPath(QPointF(9, 10))
        path.lineTo(9, 7)
        path.cubicTo(9, 3.5, 17, 4, 17, 7.5)
        painter.drawPath(path)
        line(12, 13.5, 12, 16.5)
    elif name == "question":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        path = QPainterPath(QPointF(9, 9.5))
        path.cubicTo(9, 6.5, 15, 6, 15, 9)
        path.cubicTo(15, 11, 12, 11.5, 12, 14)
        painter.drawPath(path)
        line(12, 17, 12, 17.2)
    elif name == "users":
        painter.drawEllipse(QRectF(7.5, 4, 6, 6))
        path = QPainterPath(QPointF(3.5, 20))
        path.cubicTo(3.5, 14.5, 17.5, 14.5, 17.5, 20)
        painter.drawPath(path)
        painter.drawArc(QRectF(13.8, 5.2, 5.6, 5.6), -50 * 16, 150 * 16)
    elif name == "key":
        painter.drawEllipse(QRectF(3.5, 8.5, 6.5, 6.5))
        line(9.5, 12.5, 20.5, 20)
        line(15, 16.5, 17.5, 14)
        line(17.5, 18.5, 20, 16)
    elif name == "cart":
        path = QPainterPath(QPointF(3, 4.5))
        path.lineTo(6, 4.5)
        path.lineTo(8.5, 16)
        path.lineTo(18, 16)
        path.lineTo(20, 8)
        path.lineTo(7, 8)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(8.5, 18, 2.5, 2.5))
        painter.drawEllipse(QRectF(15.5, 18, 2.5, 2.5))
    elif name == "package":
        rect(4, 8, 16, 12, 1.5)
        path = QPainterPath(QPointF(4, 8))
        path.lineTo(12, 4)
        path.lineTo(20, 8)
        painter.drawPath(path)
        line(12, 4, 12, 8)
        line(12, 12, 12, 20)
    elif name == "clock":
        painter.drawEllipse(QRectF(4, 4, 16, 16))
        line(12, 7.5, 12, 12)
        line(12, 12, 15.5, 14)
    elif name == "ticket":
        rect(3, 6, 18, 12, 2)
        line(15, 6.5, 15, 9.5)
        line(15, 11, 15, 14)
        line(15, 15.5, 15, 17.5)
        line(6.5, 10, 6.5, 14)
    elif name == "heart":
        path = QPainterPath(QPointF(12, 20))
        path.cubicTo(4.5, 14.5, 2.5, 10, 4.8, 6.8)
        path.cubicTo(7.2, 3.6, 11, 4.6, 12, 7.2)
        path.cubicTo(13, 4.6, 16.8, 3.6, 19.2, 6.8)
        path.cubicTo(21.5, 10, 19.5, 14.5, 12, 20)
        painter.drawPath(path)
    elif name == "smile":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        painter.drawEllipse(QRectF(8.2, 8.2, 1.6, 1.6))
        painter.drawEllipse(QRectF(14.2, 8.2, 1.6, 1.6))
        painter.drawArc(QRectF(7.5, 9.5, 9, 8), 200 * 16, 140 * 16)
    elif name == "palette":
        path = QPainterPath(QPointF(12, 3.5))
        path.cubicTo(6, 3.5, 3.5, 7.5, 3.5, 12)
        path.cubicTo(3.5, 16.5, 6.5, 20.5, 11, 20.5)
        path.cubicTo(13.5, 20.5, 13, 18.5, 12, 17.3)
        path.cubicTo(11, 16, 12, 14.5, 14, 14.5)
        path.lineTo(17.5, 14.5)
        path.cubicTo(20.3, 14.5, 20.8, 12, 20.8, 10.2)
        path.cubicTo(20.8, 6, 17, 3.5, 12, 3.5)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(7, 8, 1.6, 1.6))
        painter.drawEllipse(QRectF(11.5, 6.3, 1.6, 1.6))
        painter.drawEllipse(QRectF(15.8, 8.2, 1.6, 1.6))
    elif name == "dice":
        rect(4, 4, 16, 16, 3)
        painter.setBrush(QColor(color))
        for cx, cy in ((8.2, 8.2), (15.8, 8.2), (12, 12), (8.2, 15.8), (15.8, 15.8)):
            painter.drawEllipse(QRectF(cx - 1, cy - 1, 2, 2))
        painter.setBrush(Qt.NoBrush)
    elif name == "compress":
        line(12, 3.5, 12, 9)
        line(9, 6, 12, 9)
        line(15, 6, 12, 9)
        line(12, 20.5, 12, 15)
        line(9, 18, 12, 15)
        line(15, 18, 12, 15)
        line(3.5, 12, 9, 12)
        line(6, 9, 9, 12)
        line(6, 15, 9, 12)
        line(20.5, 12, 15, 12)
        line(18, 9, 15, 12)
        line(18, 15, 15, 12)
    elif name == "scissors":
        painter.drawEllipse(QRectF(4, 4, 4, 4))
        painter.drawEllipse(QRectF(4, 16, 4, 4))
        line(7.8, 7.3, 20, 19)
        line(7.8, 16.7, 20, 5)
    elif name == "star":
        path = QPainterPath(QPointF(12, 3.5))
        path.lineTo(14.5, 9.2)
        path.lineTo(20.8, 9.9)
        path.lineTo(16.1, 14.1)
        path.lineTo(17.5, 20.4)
        path.lineTo(12, 17)
        path.lineTo(6.5, 20.4)
        path.lineTo(7.9, 14.1)
        path.lineTo(3.2, 9.9)
        path.lineTo(9.5, 9.2)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "link":
        line(9, 15, 15, 9)
        painter.save()
        painter.translate(7.5, 8)
        painter.rotate(-45)
        painter.drawRoundedRect(QRectF(-5, -2.6, 10, 5.2), 2.6, 2.6)
        painter.restore()
        painter.save()
        painter.translate(16.5, 16)
        painter.rotate(-45)
        painter.drawRoundedRect(QRectF(-5, -2.6, 10, 5.2), 2.6, 2.6)
        painter.restore()
    elif name == "truck":
        rect(3, 8, 11, 9, 1)
        path = QPainterPath(QPointF(14, 8))
        path.lineTo(18, 8)
        path.lineTo(21, 11)
        path.lineTo(21, 17)
        path.lineTo(14, 17)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(6, 17.5, 3, 3))
        painter.drawEllipse(QRectF(16, 17.5, 3, 3))
    elif name == "eye":
        path = QPainterPath(QPointF(2, 12))
        path.cubicTo(6, 5.8, 18, 5.8, 22, 12)
        path.cubicTo(18, 18.2, 6, 18.2, 2, 12)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(10, 10, 4, 4))
    elif name == "broom":
        line(4, 20.5, 14, 10.5)
        path = QPainterPath(QPointF(14, 10.5))
        path.lineTo(20, 8)
        path.lineTo(16, 16)
        path.lineTo(11, 12.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(13.5, 9, 16.5, 12)
    elif name == "antenna":
        painter.drawArc(QRectF(4, 3, 16, 14), 0, 180 * 16)
        line(12, 10, 12, 19)
        line(8, 20, 16, 20)
        painter.drawEllipse(QRectF(11, 9, 2, 2))
    elif name == "tag":
        path = QPainterPath(QPointF(3.5, 11))
        path.lineTo(11, 3.5)
        path.lineTo(20.5, 3.5)
        path.lineTo(20.5, 13)
        path.lineTo(13, 20.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QRectF(15.5, 7, 2.2, 2.2))
    elif name == "arrow_left":
        line(19, 12, 5, 12)
        line(10, 7, 5, 12)
        line(10, 17, 5, 12)
    elif name == "dot":
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(9, 9, 6, 6))
        painter.setBrush(Qt.NoBrush)
    elif name == "alert":
        path = QPainterPath(QPointF(12, 3.5))
        path.lineTo(21, 19.5)
        path.lineTo(3, 19.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(12, 9, 12, 14)
        line(12, 16.8, 12, 17)
    elif name == "pause":
        rect(7, 5, 3, 14, 1)
        rect(14, 5, 3, 14, 1)
    elif name == "pin":
        line(9, 3.5, 15, 3.5)
        line(10, 3.5, 10, 10)
        line(14, 3.5, 14, 10)
        line(7.5, 10, 16.5, 10)
        line(12, 10, 12, 20.5)
    elif name == "skip_next":
        path = QPainterPath(QPointF(6.5, 5.5))
        path.lineTo(16, 12)
        path.lineTo(6.5, 18.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(18, 5, 18, 19)
    elif name == "skip_prev":
        path = QPainterPath(QPointF(17.5, 5.5))
        path.lineTo(8, 12)
        path.lineTo(17.5, 18.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(6, 5, 6, 19)
    elif name == "mic":
        rect(9, 3, 6, 10, 3)
        painter.drawArc(QRectF(6.5, 8, 11, 10), 180 * 16, 180 * 16)
        line(12, 18, 12, 21)
        line(9, 21, 15, 21)
    elif name == "minus":
        line(5, 12, 19, 12)
    elif name == "swap":
        line(4, 8, 18, 8)
        line(14, 5, 18, 8)
        line(14, 11, 18, 8)
        line(20, 16, 6, 16)
        line(10, 13, 6, 16)
        line(10, 19, 6, 16)
    elif name == "expand":
        line(4, 9, 4, 4)
        line(4, 4, 9, 4)
        line(15, 4, 20, 4)
        line(20, 4, 20, 9)
        line(20, 15, 20, 20)
        line(20, 20, 15, 20)
        line(9, 20, 4, 20)
        line(4, 20, 4, 15)
    elif name == "keyboard":
        rect(3, 7, 18, 11, 2)
        for x in (6, 9, 12, 15, 18):
            line(x, 10.5, x, 10.8)
        line(8, 15, 16, 15)
    elif name == "phone":
        rect(7, 3, 10, 18, 2)
        line(10.5, 17.5, 13.5, 17.5)
    elif name == "cloud":
        path = QPainterPath(QPointF(7, 18))
        path.cubicTo(3.5, 18, 3, 14, 6, 12.5)
        path.cubicTo(5.5, 8.5, 9, 6.5, 12, 8)
        path.cubicTo(13.5, 4.5, 19, 5, 19.5, 9.5)
        path.cubicTo(22.5, 10.5, 22, 16, 18, 16.5)
        path.lineTo(17.5, 18)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "list":
        painter.setBrush(QColor(color))
        for cy in (7, 12, 17):
            painter.drawEllipse(QRectF(4.5, cy - 1, 2, 2))
        painter.setBrush(Qt.NoBrush)
        line(9.5, 7, 20, 7)
        line(9.5, 12, 20, 12)
        line(9.5, 17, 20, 17)
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
    elif name == "undo":
        # 右键菜单：向左回卷的箭头，尾巴向下收，和 redo 成镜像
        line(3.5, 12, 9, 6.5)
        line(3.5, 12, 9, 17.5)
        path = QPainterPath(QPointF(3.5, 12))
        path.lineTo(12, 12)
        path.cubicTo(16.5, 12, 19, 14, 19, 17.5)
        painter.drawPath(path)
    elif name == "redo":
        line(20.5, 12, 15, 6.5)
        line(20.5, 12, 15, 17.5)
        path = QPainterPath(QPointF(20.5, 12))
        path.lineTo(12, 12)
        path.cubicTo(7.5, 12, 5, 14, 5, 17.5)
        painter.drawPath(path)
    elif name == "select_all":
        # 虚线选框 + 四角外伸手柄：和「全选」语义一致，只描边不填充
        painter.setPen(QPen(QColor(color), 1.65, Qt.DashLine, Qt.RoundCap))
        rect(5.5, 5.5, 13, 13, 2)
        painter.setPen(QPen(QColor(color), 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for x, y, sx, sy in ((5.5, 5.5, -1, -1), (18.5, 5.5, 1, -1),
                             (5.5, 18.5, -1, 1), (18.5, 18.5, 1, 1)):
            line(x, y, x + 3 * sx, y)
            line(x, y, x, y + 3 * sy)
    else:
        for x in (4, 14):
            for y in (4, 14):
                rect(x, y, 6, 6, 1.5)
    painter.end()
    return QIcon(pixmap)
