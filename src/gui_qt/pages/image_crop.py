"""实用工具 · 图片裁切页：像素级四边裁切工具（移植自 imgcut 项目）。

与 imgcut Web/桌面版功能对等：四条参考线拖动、精细模式（鼠标 20px → 参考线 1px）、
Shift 加速、边界吸附（预计算行/列边缘画像，后台线程分析）、自动识别内容边界、
像素放大镜（跟随拖动中的参考线）、撤销/重做（每张图独立历史）、精确像素输入、
多图槽位列表、单张与批量导出（保持原格式，PNG 无损，批量存 download/图片裁切/）。

坐标一律存原始图片像素空间（display_crop 拖动中为浮点，释放时取整提交）；
渲染与命中检测用 scale+offset 换算，scale 不设 1.0 上限（图片撑满画布，
横图左右贴边、竖图上下贴边，适合带黑边的录播截图）。
"""

import math
import os
import threading
from dataclasses import dataclass, field, replace
from typing import Optional

from PySide6.QtCore import QPointF, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPalette, QPen, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSizePolicy, QSpinBox,
    QSplitter, QTextEdit, QToolTip, QVBoxLayout, QWidget,
)

from gui_qt.common import icon_button, post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.theme import current_palette
from toolbox.memory_cache import ByteLRU

try:
    import numpy as _np
except Exception:
    _np = None

# ==================== 常量（镜像 imgcut 功能规格） ====================
MIN_CROP_SIZE = 1                 # 裁切区域单边最小像素
GUIDE_HIT_TOLERANCE = 8           # 参考线命中容差（屏幕像素）
INITIAL_INSET_RATIO = 0.2         # 初始四边内缩比例
FINE_MODE_RATIO = 20              # 精细模式：鼠标移动 20px → 参考线移动 1px
SHIFT_SPEED_MULTIPLIER = 5        # 按住 Shift 拖动的加速倍率
SNAP_RANGE = 5                    # 吸附搜索窗口 ±5 像素
EDGE_THRESHOLD = 60               # 吸附判定的逐像素亮度对比阈值
CONTENT_LUMINANCE_THRESHOLD = 20  # 自动识别：背景亮度判定阈值
CONTENT_SAT_THRESHOLD = 0.3       # 自动识别：饱和度阈值
CONTENT_SCAN_STEP = 2             # 自动识别：扫描步长
CONTENT_PADDING = 2               # 自动识别：边缘留白
MAGNIFIER_SIZE = 240              # 放大镜边长(px)
MAGNIFIER_ZOOM_DEFAULT = 10
MAGNIFIER_ZOOM_MIN = 2
MAGNIFIER_ZOOM_MAX = 64
EXPORT_QUALITY = 92               # JPEG/WebP 导出质量（PNG 无损）
VALID_EXTS = (".png", ".jpg", ".jpeg", ".webp")
FMT_BY_EXT = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}

C_CANVAS_BG = QColor(30, 30, 35)        # 画布深底（深浅色主题下均固定）
C_MASK = QColor(0, 0, 0, 140)           # 裁切区外遮罩
C_GUIDE = QColor("#d9dde3")             # 参考线默认
C_GUIDE_ACTIVE = QColor("#f2c14e")      # 拖动中的参考线高亮
C_MAG_BG = QColor("#1a1a1a")
C_MAG_GRID = QColor(255, 255, 255, 30)
C_MAG_CROSS = QColor("#f2c14e")


# ==================== 数据结构 ====================
@dataclass
class CropRect:
    top: int
    bottom: int      # 独占边界（含 top 不含 bottom）
    left: int
    right: int

    def copy(self) -> "CropRect":
        return CropRect(self.top, self.bottom, self.left, self.right)

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass
class ImageSlot:
    sid: int
    path: str
    name: str
    ext: str
    image: QImage                    # 仅列表缩略图，原始像素尺寸单独保存
    crop: CropRect
    display_crop: CropRect
    history: list = field(default_factory=list)
    history_index: int = 0
    profiles: Optional[tuple] = None       # (row_sum, row_cnt, col_sum, col_cnt)
    profile_pending: bool = False
    source_size: Optional[QSize] = None
    stamp: Optional[tuple] = None

    @property
    def width(self):
        return self.source_size.width() if self.source_size is not None else self.image.width()

    @property
    def height(self):
        return self.source_size.height() if self.source_size is not None else self.image.height()


class _ImageCropListWidget(QListWidget):
    importRequested = Signal(QPoint)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.importRequested.emit(event.position().toPoint())


def _image_stamp(path):
    stat = os.stat(path)
    return stat.st_mtime_ns, stat.st_size


def read_image_slot(path, sid=0):
    """只读尺寸与小缩略图；导入一批图片时不保留本批所有原图。"""
    stamp = _image_stamp(path)
    reader = QImageReader(path)
    size = reader.size()
    if not size.isValid():
        raise RuntimeError("图片尺寸无效")
    if size.width() > 160 or size.height() > 160:
        reader.setScaledSize(size.scaled(QSize(160, 160), Qt.KeepAspectRatio))
    thumbnail = reader.read()
    if thumbnail.isNull():
        raise RuntimeError("图片解码失败")
    if _image_stamp(path) != stamp:
        raise RuntimeError("图片已发生变化，请重新导入")
    crop = make_initial_crop(size.width(), size.height())
    return ImageSlot(sid=sid, path=path, name=os.path.basename(path),
                     ext=os.path.splitext(path)[1].lower(), image=thumbnail,
                     crop=crop, display_crop=crop.copy(), history=[crop.copy()],
                     source_size=size, stamp=stamp)


def read_original(slot):
    """原图只在当前编辑/逐张导出时读取，禁止把旧坐标套到已变化的文件上。"""
    if slot.stamp is not None and _image_stamp(slot.path) != slot.stamp:
        raise RuntimeError("原图已发生变化，请重新导入后再裁切")
    image = QImageReader(slot.path).read()
    if image.isNull():
        raise RuntimeError("无法读取原图，请检查源文件")
    if slot.stamp is not None and _image_stamp(slot.path) != slot.stamp:
        raise RuntimeError("原图已发生变化，请重新导入后再裁切")
    return image


# ==================== 纯函数（无 Qt 界面依赖，便于离线自测） ====================
def make_initial_crop(img_w: int, img_h: int) -> CropRect:
    w, h = max(1, img_w), max(1, img_h)
    return CropRect(
        round(h * INITIAL_INSET_RATIO), round(h * (1 - INITIAL_INSET_RATIO)),
        round(w * INITIAL_INSET_RATIO), round(w * (1 - INITIAL_INSET_RATIO)),
    )


def clamp_crop(crop: CropRect, img_w: int, img_h: int) -> CropRect:
    w, h = max(1, img_w), max(1, img_h)
    top = max(0, min(crop.top, h))
    bottom = max(0, min(crop.bottom, h))
    left = max(0, min(crop.left, w))
    right = max(0, min(crop.right, w))
    top = min(top, bottom - MIN_CROP_SIZE)
    bottom = max(bottom, top + MIN_CROP_SIZE)
    left = min(left, right - MIN_CROP_SIZE)
    right = max(right, left + MIN_CROP_SIZE)
    return CropRect(top, bottom, left, right)


def clamp_line(edge: str, raw: float, other: CropRect, img_w: int, img_h: int) -> float:
    """拖动单边的夹取规则：对边坐标保持不变。"""
    w, h = max(1, img_w), max(1, img_h)
    if edge == "top":
        return max(0, min(other.bottom - MIN_CROP_SIZE, raw))
    if edge == "bottom":
        return max(other.top + MIN_CROP_SIZE, min(h, raw))
    if edge == "left":
        return max(0, min(other.right - MIN_CROP_SIZE, raw))
    return max(other.left + MIN_CROP_SIZE, min(w, raw))


def compute_edge_profiles(gray: QImage):
    """预计算行/列边缘画像（吸附查表）。

    row_sum/cnt[y]：|lum(y+1,x) - lum(y-1,x)| 中超过 EDGE_THRESHOLD 的
    求和/计数（只统计强边缘像素，口径与 imgcut Web 版一致）；
    col_sum/cnt[x] 同理为左右相邻列差。返回 (row_sum, row_cnt, col_sum, col_cnt)。
    """
    w, h = gray.width(), gray.height()
    if w < 3 or h < 3:
        return [0.0] * h, [0] * h, [0.0] * w, [0] * w
    stride = gray.bytesPerLine()
    buf = gray.constBits()

    if _np is not None:
        arr = _np.frombuffer(buf, dtype=_np.uint8).reshape(h, stride)[:, :w]
        row_sum, row_cnt = [0.0] * h, [0] * h
        col_sum = _np.zeros(w - 2, dtype=_np.int64)
        col_cnt = _np.zeros(w - 2, dtype=_np.int64)
        # 与原算法逐像素一致；临时 int16/掩码仅占 128 行，不复制整幅灰度图。
        for start in range(1, h - 1, 128):
            end = min(h - 1, start + 128)
            dv = _np.abs(arr[start + 1:end + 1].astype(_np.int16) - arr[start - 1:end - 1])
            mask = dv > EDGE_THRESHOLD
            row_cnt[start:end] = mask.sum(axis=1).tolist()
            dv *= mask
            row_sum[start:end] = dv.sum(axis=1).tolist()
        for start in range(0, h, 128):
            block = arr[start:start + 128]
            dh = _np.abs(block[:, 2:].astype(_np.int16) - block[:, :-2])
            mask = dh > EDGE_THRESHOLD
            col_cnt += mask.sum(axis=0)
            dh *= mask
            col_sum += dh.sum(axis=0)
        return row_sum, row_cnt, [0.0] + col_sum.tolist() + [0.0], [0] + col_cnt.tolist() + [0]

    step = 2 if w * h <= 4_000_000 else 4
    row_sum = [0.0] * h
    row_cnt = [0] * h
    for y in range(1, h - 1):
        below = (y + 1) * stride
        above = (y - 1) * stride
        s = 0.0
        c = 0
        for x in range(0, w, step):
            d = buf[below + x] - buf[above + x]
            if d < 0:
                d = -d
            if d > EDGE_THRESHOLD:
                s += d
                c += 1
        row_sum[y] = s
        row_cnt[y] = c
    col_sum = [0.0] * w
    col_cnt = [0] * w
    for x in range(1, w - 1):
        s = 0.0
        c = 0
        for y in range(0, h, step):
            base = y * stride
            d = buf[base + x + 1] - buf[base + x - 1]
            if d < 0:
                d = -d
            if d > EDGE_THRESHOLD:
                s += d
                c += 1
        col_sum[x] = s
        col_cnt[x] = c
    return row_sum, row_cnt, col_sum, col_cnt


def find_snap_edge(profiles, raw: float, edge: str, img_w: int, img_h: int) -> Optional[int]:
    """在 raw±SNAP_RANGE 内找最强亮度边缘并吸附（仅内容边缘）。"""
    if profiles is None:
        return None
    row_sum, row_cnt, col_sum, col_cnt = profiles
    is_horizontal = edge in ("top", "bottom")
    dim = img_h if is_horizontal else img_w
    s_sum, s_cnt = (row_sum, row_cnt) if is_horizontal else (col_sum, col_cnt)
    pos = int(round(raw))
    start = max(0, pos - SNAP_RANGE)
    end = min(dim - 1, pos + SNAP_RANGE)
    best = None
    best_strength = 0.0
    best_count = 0
    for p in range(start, end + 1):
        cnt = s_cnt[p]
        if cnt <= 0:
            continue
        strength = s_sum[p] / cnt
        if strength > best_strength or (strength == best_strength and cnt > best_count):
            best, best_strength, best_count = p, strength, cnt
    if best is not None and abs(best - pos) <= SNAP_RANGE:
        return best
    return None


def _is_content_pixel(buf: bytes, i: int) -> bool:
    r, g, b = buf[i], buf[i + 1], buf[i + 2]
    lum = r * 0.299 + g * 0.587 + b * 0.114
    if lum > CONTENT_LUMINANCE_THRESHOLD:
        return True
    mx = max(r, g, b)
    mn = min(r, g, b)
    sat = (mx - mn) / mx if mx > 0 else 0.0
    return sat > CONTENT_SAT_THRESHOLD and lum > 10


def find_content_bounds(img: QImage) -> Optional[CropRect]:
    """四向扫描自动识别内容边界（黑色/暗背景裁切）。全图无内容返回 None。"""
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None
    rgba = img.convertToFormat(QImage.Format_RGBA8888)
    stride = rgba.bytesPerLine()
    buf = rgba.constBits()
    step = CONTENT_SCAN_STEP

    top = 0
    found = False
    for y in range(0, h, step):
        base = y * stride
        for x in range(0, w, step):
            if _is_content_pixel(buf, base + x * 4):
                top = max(0, y - CONTENT_PADDING)
                found = True
                break
        if found:
            break
    if not found:
        return None

    bottom = h
    found = False
    for y in range(h - 1, -1, -step):
        base = y * stride
        for x in range(0, w, step):
            if _is_content_pixel(buf, base + x * 4):
                bottom = min(h, y + 1 + CONTENT_PADDING)
                found = True
                break
        if found:
            break

    left = 0
    found = False
    for x in range(0, w, step):
        for y in range(top, bottom, step):
            if _is_content_pixel(buf, y * stride + x * 4):
                left = max(0, x - CONTENT_PADDING)
                found = True
                break
        if found:
            break

    right = w
    found = False
    for x in range(w - 1, -1, -step):
        for y in range(top, bottom, step):
            if _is_content_pixel(buf, y * stride + x * 4):
                right = min(w, x + 1 + CONTENT_PADDING)
                found = True
                break
        if found:
            break

    if right - left < MIN_CROP_SIZE or bottom - top < MIN_CROP_SIZE:
        return None
    return clamp_crop(CropRect(top, bottom, left, right), w, h)


def save_cropped(image: QImage, crop: CropRect, out_path: str) -> Optional[str]:
    """按裁切区域保存图片，保持格式（PNG 无损 / JPEG、WebP 质量 92）。

    返回实际保存路径；WebP 写入器不可用时降级存 .png 并返回新路径；失败返回 None。
    """
    w, h = max(0, crop.width), max(0, crop.height)
    if w <= 0 or h <= 0:
        return None
    img = image.copy(QRect(crop.left, crop.top, w, h))
    ext = os.path.splitext(out_path)[1].lower()
    fmt = FMT_BY_EXT.get(ext, "PNG")
    if fmt == "JPEG" and img.hasAlphaChannel():
        img = img.convertToFormat(QImage.Format_RGB888)
    quality = -1 if fmt == "PNG" else EXPORT_QUALITY
    if img.save(out_path, fmt, quality):
        return out_path
    if fmt != "PNG":
        alt = os.path.splitext(out_path)[0] + ".png"
        if img.save(alt, "PNG"):
            return alt
    return None


# ==================== 放大镜 ====================
class _Magnifier(QWidget):
    """像素放大镜：最近邻放大显示参考线附近区域 + 像素网格 + 中心高亮。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(MAGNIFIER_SIZE, MAGNIFIER_SIZE)
        self._image: Optional[QImage] = None
        self._src = QRectF()
        self._zoom = MAGNIFIER_ZOOM_DEFAULT
        self._horizontal = True
        self.hide()

    def show_region(self, image: QImage, src: QRectF, zoom: int, horizontal: bool):
        self._image = image
        self._src = src
        self._zoom = zoom
        self._horizontal = horizontal
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(0, 0, MAGNIFIER_SIZE, MAGNIFIER_SIZE, C_MAG_BG)
        if self._image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(QRectF(0, 0, MAGNIFIER_SIZE, MAGNIFIER_SIZE), self._image, self._src)
        px = float(self._zoom)
        if px >= 3:
            p.setPen(QPen(C_MAG_GRID, 1))
            x = px
            while x < MAGNIFIER_SIZE:
                p.drawLine(QPoint(int(x), 0), QPoint(int(x), MAGNIFIER_SIZE))
                x += px
            y = px
            while y < MAGNIFIER_SIZE:
                p.drawLine(QPoint(0, int(y)), QPoint(MAGNIFIER_SIZE, int(y)))
                y += px
        cx = math.floor(MAGNIFIER_SIZE / 2 / px) * px
        cy = math.floor(MAGNIFIER_SIZE / 2 / px) * px
        p.setPen(QPen(C_MAG_CROSS, 1.5))
        p.drawRect(QRectF(cx + 0.5, cy + 0.5, px - 1, px - 1))
        p.setPen(QPen(C_MAG_CROSS, 1))
        mid = MAGNIFIER_SIZE / 2
        if self._horizontal:
            p.drawLine(QPoint(0, int(mid)), QPoint(MAGNIFIER_SIZE, int(mid)))
        else:
            p.drawLine(QPoint(int(mid), 0), QPoint(int(mid), MAGNIFIER_SIZE))
        p.setPen(QPen(C_GUIDE, 2))
        p.drawRect(QRectF(1, 1, MAGNIFIER_SIZE - 2, MAGNIFIER_SIZE - 2))
        label = f"{self._zoom}x"
        f = QFont("Consolas")
        f.setPointSize(9)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(label)
        p.fillRect(QRect(MAGNIFIER_SIZE - tw - 14, MAGNIFIER_SIZE - 20, tw + 10, 16),
                   QColor(0, 0, 0, 150))
        p.setPen(QColor("#ffffff"))
        p.drawText(QPoint(MAGNIFIER_SIZE - tw - 9, MAGNIFIER_SIZE - 8), label)


# ==================== 单行省略标签 ====================
class _ElidedLabel(QLabel):
    """按可用宽度中间省略的单行标签：完整内容进 tooltip，避免长文件名撑宽布局。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._full_text = str(text or "")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(self._full_text)

    def setText(self, text: str):
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self.update()

    def text(self) -> str:
        return self._full_text

    def paintEvent(self, _event):
        p = QPainter(self)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(16, self.width()))
        role = (QPalette.ColorRole.PlaceholderText
                if self.property("role") == "caption" else QPalette.ColorRole.WindowText)
        p.setPen(self.palette().color(role))
        p.drawText(self.rect(), int(self.alignment()), shown)
        p.end()


# ==================== 裁切画布 ====================
class CropCanvas(QWidget):
    """居中渲染 + 四条参考线交互画布。所有重交互（拖动/吸附/放大镜）在这里。"""

    crop_committed = Signal()

    def __init__(self, page: "ImageCropPage"):
        super().__init__()
        self.page = page
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        self._dragging: Optional[str] = None
        self._accum_x = 0.0
        self._accum_y = 0.0
        self._fine_x = 0.0
        self._fine_y = 0.0
        self._prev_img_x = 0.0
        self._prev_img_y = 0.0
        self._pix_cache = (None, None)
        self._magnifier = _Magnifier(self)

    # ---- 布局换算 ----
    def _layout(self):
        slot = self.page.active_slot()
        if slot is None:
            return None
        cw, ch = self.width(), self.height()
        if cw <= 1 or ch <= 1:
            return None
        img_w, img_h = max(1, slot.width), max(1, slot.height)
        scale = min(cw / img_w, ch / img_h)   # 不设 1.0 上限：图片撑满画布
        dw = max(1, round(img_w * scale))
        dh = max(1, round(img_h * scale))
        return {
            "scale": scale,
            "ox": (cw - dw) / 2,
            "oy": (ch - dh) / 2,
            "dw": dw, "dh": dh,
            "img_w": img_w, "img_h": img_h,
        }

    def _to_image(self, lay, cx: float, cy: float):
        return (cx - lay["ox"]) / lay["scale"], (cy - lay["oy"]) / lay["scale"]

    def _detect_edge(self, lay, cx: float, cy: float) -> Optional[str]:
        slot = self.page.active_slot()
        if slot is None:
            return None
        dc = slot.display_crop
        s = lay["scale"]
        dist = {
            "top": abs(cy - (lay["oy"] + dc.top * s)),
            "bottom": abs(cy - (lay["oy"] + dc.bottom * s)),
            "left": abs(cx - (lay["ox"] + dc.left * s)),
            "right": abs(cx - (lay["ox"] + dc.right * s)),
        }
        for edge in ("top", "bottom", "left", "right"):
            if dist[edge] <= GUIDE_HIT_TOLERANCE:
                return edge
        return None

    # ---- 渲染 ----
    def _scaled_pixmap(self, slot, lay) -> QPixmap:
        image = self.page._active_preview
        image = image if image is not None else slot.image
        key = (slot.sid, image.cacheKey(), lay["dw"], lay["dh"])
        if self._pix_cache[0] != key:
            pm = QPixmap.fromImage(image.scaled(
                lay["dw"], lay["dh"], Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self._pix_cache = (key, pm)
        return self._pix_cache[1]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), C_CANVAS_BG)
        slot = self.page.active_slot()
        if slot is None:
            p.setPen(QColor("#9aa0ac"))
            f = self.font()
            f.setPointSize(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "将图片拖入窗口，或双击左侧列表导入图片/文件夹\n\n"
                       "支持 PNG / JPG / JPEG / WebP，可多选批量导入")
            return
        lay = self._layout()
        if lay is None:
            return
        p.drawPixmap(QPoint(round(lay["ox"]), round(lay["oy"])),
                     self._scaled_pixmap(slot, lay))

        dc = slot.display_crop
        s = lay["scale"]
        ox, oy = lay["ox"], lay["oy"]
        sx = ox + dc.left * s
        sy = oy + dc.top * s
        sw = max(0.0, (dc.right - dc.left) * s)
        sh = max(0.0, (dc.bottom - dc.top) * s)
        dw, dh = lay["dw"], lay["dh"]

        p.fillRect(QRectF(ox, oy, dw, max(0.0, sy - oy)), C_MASK)
        p.fillRect(QRectF(ox, sy + sh, dw, max(0.0, oy + dh - (sy + sh))), C_MASK)
        p.fillRect(QRectF(ox, sy, max(0.0, sx - ox), sh), C_MASK)
        p.fillRect(QRectF(sx + sw, sy, max(0.0, ox + dw - (sx + sw)), sh), C_MASK)

        pen = QPen(C_GUIDE, 2)
        pen.setDashPattern([3.0, 1.5])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(sx, sy, sw, sh))

        handle = 10.0

        def guide(edge: str, x1: float, y1: float, x2: float, y2: float):
            color = C_GUIDE_ACTIVE if self._dragging == edge else C_GUIDE
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            hx = (x1 + x2) / 2
            hy = (y1 + y2) / 2
            p.setBrush(color)
            p.drawRect(QRectF(hx - handle / 2, hy - handle / 2, handle, handle))

        guide("top", sx, sy, sx + sw, sy)
        guide("bottom", sx, sy + sh, sx + sw, sy + sh)
        guide("left", sx, sy, sx, sy + sh)
        guide("right", sx + sw, sy, sx + sw, sy + sh)

    # ---- 鼠标交互 ----
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        slot = self.page.active_slot()
        lay = self._layout()
        if slot is None or lay is None:
            return
        original = self.page._active_original
        if original is None:
            self._hide_magnifier()
            return
        pos = e.position()
        edge = self._detect_edge(lay, pos.x(), pos.y())
        if edge is None:
            return
        self._dragging = edge
        dc = slot.display_crop
        self._accum_x = float(dc.right if edge == "right" else dc.left)
        self._accum_y = float(dc.bottom if edge == "bottom" else dc.top)
        self._fine_x = 0.0
        self._fine_y = 0.0
        self._prev_img_x, self._prev_img_y = self._to_image(lay, pos.x(), pos.y())
        self._update_magnifier(edge)
        self.update()

    def mouseMoveEvent(self, e):
        lay = self._layout()
        slot = self.page.active_slot()
        pos = e.position()
        if self._dragging is None or slot is None or lay is None:
            if slot is not None and lay is not None:
                edge = self._detect_edge(lay, pos.x(), pos.y())
                if edge in ("top", "bottom"):
                    self.setCursor(Qt.SizeVerCursor)
                elif edge in ("left", "right"):
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
                # 悬停参考线附近即显示放大镜预览（对齐 imgcut 原版），离开则隐藏
                if edge is not None:
                    self._update_magnifier(edge)
                else:
                    self._hide_magnifier()
            return

        img_x, img_y = self._to_image(lay, pos.x(), pos.y())
        dx = img_x - self._prev_img_x
        dy = img_y - self._prev_img_y
        self._prev_img_x, self._prev_img_y = img_x, img_y
        if e.modifiers() & Qt.ShiftModifier:
            dx *= SHIFT_SPEED_MULTIPLIER
            dy *= SHIFT_SPEED_MULTIPLIER

        if self.page.fine_mode:
            self._fine_x += dx
            self._fine_y += dy
            # 与 JS Math.trunc 同语义：拆整数步与余数，负数不会反向累积漂移
            step_x = math.trunc(self._fine_x / FINE_MODE_RATIO)
            step_y = math.trunc(self._fine_y / FINE_MODE_RATIO)
            self._accum_x += step_x
            self._accum_y += step_y
            self._fine_x -= step_x * FINE_MODE_RATIO
            self._fine_y -= step_y * FINE_MODE_RATIO
        else:
            self._accum_x += dx
            self._accum_y += dy

        raw_x, raw_y = self._accum_x, self._accum_y
        edge = self._dragging
        img_w, img_h = lay["img_w"], lay["img_h"]
        if self.page.snap_mode and slot.profiles is not None:
            if edge in ("left", "right"):
                snapped = find_snap_edge(slot.profiles, raw_x, edge, img_w, img_h)
                if snapped is not None:
                    raw_x = snapped
            else:
                snapped = find_snap_edge(slot.profiles, raw_y, edge, img_w, img_h)
                if snapped is not None:
                    raw_y = snapped

        dc = slot.display_crop
        if edge == "top":
            dc.top = clamp_line("top", raw_y, dc, img_w, img_h)
        elif edge == "bottom":
            dc.bottom = clamp_line("bottom", raw_y, dc, img_w, img_h)
        elif edge == "left":
            dc.left = clamp_line("left", raw_x, dc, img_w, img_h)
        else:
            dc.right = clamp_line("right", raw_x, dc, img_w, img_h)

        axis = "Y" if edge in ("top", "bottom") else "X"
        value = round(dc.top if edge == "top" else dc.bottom if edge == "bottom"
                      else dc.left if edge == "left" else dc.right)
        QToolTip.showText(e.globalPosition().toPoint() + QPoint(16, -36),
                          f"{axis}: {value} px", self)
        self._update_magnifier(edge)
        self.page.on_live_crop_changed()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._dragging is None:
            return
        slot = self.page.active_slot()
        if slot is not None:
            dc = slot.display_crop
            rounded = CropRect(round(dc.top), round(dc.bottom), round(dc.left), round(dc.right))
            self.page.commit_crop(slot, rounded)
        self._dragging = None
        self._hide_magnifier()
        QToolTip.hideText()
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def leaveEvent(self, _e):
        if self._dragging is not None:
            slot = self.page.active_slot()
            if slot is not None:
                dc = slot.display_crop
                rounded = CropRect(round(dc.top), round(dc.bottom), round(dc.left), round(dc.right))
                self.page.commit_crop(slot, rounded)
            self._dragging = None
            QToolTip.hideText()
        self._hide_magnifier()

    def reset_interaction(self):
        self._dragging = None
        self._hide_magnifier()
        QToolTip.hideText()
        self.setCursor(Qt.ArrowCursor)
        self._pix_cache = (None, None)
        self.update()

    # ---- 放大镜 ----
    def _update_magnifier(self, edge: str):
        slot = self.page.active_slot()
        lay = self._layout()
        if slot is None or lay is None:
            return
        original = self.page._active_original
        if original is None:
            self._hide_magnifier()
            return
        dc = slot.display_crop
        img_w, img_h = lay["img_w"], lay["img_h"]
        zoom = self.page.magnifier_zoom
        src_size = MAGNIFIER_SIZE / zoom
        is_horizontal = edge in ("top", "bottom")
        if is_horizontal:
            line_x, line_y = img_w / 2, dc.top if edge == "top" else dc.bottom
        else:
            line_x, line_y = dc.left if edge == "left" else dc.right, img_h / 2
        # 源窗口始终以参考线为中心、不夹取图片边界：
        # 线拖到图片边缘时越界部分显示深色底，可直观看到已经到边、无法再移动
        self._magnifier.show_region(
            original,
            QRectF(line_x - src_size / 2, line_y - src_size / 2, src_size, src_size),
            zoom, is_horizontal)

        s = lay["scale"]
        mx = lay["ox"] + line_x * s
        my = lay["oy"] + line_y * s
        cx0, cx1 = lay["ox"] + dc.left * s, lay["ox"] + dc.right * s
        cy0, cy1 = lay["oy"] + dc.top * s, lay["oy"] + dc.bottom * s
        sz, margin = MAGNIFIER_SIZE, 24
        cw, ch = self.width(), self.height()

        def clamp_canvas(v, limit):
            return min(max(v, 4.0), max(4.0, limit - sz - 4))

        if cx1 - cx0 >= sz and cy1 - cy0 >= sz:
            # 放得下：窗口放裁切区内侧（拖动线前进方向 24px），遮罩与画布黑边不被盖住
            if edge == "left":
                x = min(mx + margin, cx1 - sz)
            elif edge == "right":
                x = max(mx - sz - margin, cx0)
            else:
                x = min(max(mx - sz / 2, cx0), cx1 - sz)
            if edge == "top":
                y = min(my + margin, cy1 - sz)
            elif edge == "bottom":
                y = max(my - sz - margin, cy0)
            else:
                y = min(max(my - sz / 2, cy0), cy1 - sz)
        else:
            # 放不下（窄/扁选区）：沿拖动轴整体移到裁切区外（拖动线外侧优先、
            # 其次对侧），保证选区本身可见；另一轴以线为中心夹画布
            def outside(near, far, limit):
                for c in (near, far):
                    if 4 <= c <= limit - sz - 4:
                        return c
                return clamp_canvas(near, limit)

            if is_horizontal:
                y = outside(cy0 - sz - margin if edge == "top" else cy1 + margin,
                            cy1 + margin if edge == "top" else cy0 - sz - margin, ch)
                x = clamp_canvas(mx - sz / 2, cw)
            else:
                x = outside(cx0 - sz - margin if edge == "left" else cx1 + margin,
                            cx1 + margin if edge == "left" else cx0 - sz - margin, cw)
                y = clamp_canvas(my - sz / 2, ch)
        x = clamp_canvas(x, cw)
        y = clamp_canvas(y, ch)
        self._magnifier.setGeometry(int(x), int(y), sz, sz)
        self._magnifier.raise_()
        self._magnifier.show()

    def _hide_magnifier(self):
        self._magnifier.hide()
        self._magnifier._image = None


# ==================== 精确裁切尺寸输入对话框 ====================
class CropInputDialog(QDialog):
    def __init__(self, parent, img_w: int, img_h: int, crop: CropRect):
        super().__init__(parent)
        self.setWindowTitle("精确裁切尺寸")
        self.setModal(True)
        self.img_w = img_w
        self.img_h = img_h
        grid = QGridLayout(self)
        self.spins = {}
        specs = [("top", "上边距 (Y 起)", img_h), ("bottom", "下边距 (Y 止)", img_h),
                 ("left", "左边距 (X 起)", img_w), ("right", "右边距 (X 止)", img_w)]
        for row, (key, label, limit) in enumerate(specs):
            grid.addWidget(QLabel(label), row, 0)
            sp = QSpinBox()
            sp.setRange(0, limit)
            sp.setValue(max(0, min(limit, round(getattr(crop, key)))))
            grid.addWidget(sp, row, 1)
            self.spins[key] = sp
        self.info = QLabel()
        grid.addWidget(self.info, 4, 0, 1, 2)
        btns = QHBoxLayout()
        btn_apply = QPushButton("应用")
        btn_cancel = QPushButton("取消")
        btns.addStretch(1)
        btns.addWidget(btn_apply)
        btns.addWidget(btn_cancel)
        grid.addLayout(btns, 5, 0, 1, 2)
        for sp in self.spins.values():
            sp.valueChanged.connect(self._refresh)
        btn_apply.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        self._refresh()

    def _refresh(self):
        w = max(0, self.spins["right"].value() - self.spins["left"].value())
        h = max(0, self.spins["bottom"].value() - self.spins["top"].value())
        self.info.setText(f"宽: {w}px · 高: {h}px")

    def value(self) -> CropRect:
        return CropRect(self.spins["top"].value(), self.spins["bottom"].value(),
                        self.spins["left"].value(), self.spins["right"].value())


# ==================== 页面 ====================
class ImageCropPage(BasePage):
    SESSION_FIELDS = ("_session_slots", "_active", "_slot_seq")

    def prepare_session_state(self):
        self._session_slots = [dict(sid=s.sid, path=s.path, crop=s.crop.copy(),
                                    display_crop=s.display_crop.copy(), history=s.history,
                                    history_index=s.history_index, stamp=s.stamp) for s in self._slots]

    def restore_session_data(self):
        records = getattr(self, "_session_slots", [])
        active = self._active

        def work():
            return [(r, read_image_slot(r["path"], r["sid"])) for r in records]

        def done(result, err):
            if err:
                return
            self._slots = []
            for r, slot in result:
                if r.get("stamp") is not None and r["stamp"] != slot.stamp:
                    self.app.toast("原图已发生变化，请重新导入后再裁切", "warning")
                    continue
                slot.crop, slot.display_crop = r["crop"], r["display_crop"]
                slot.history, slot.history_index = r["history"], r["history_index"]
                self._slots.append(slot)
            self._rebuild_list()
            if self._slots:
                self._select_slot(max(0, min(active, len(self._slots) - 1)))
            self._sync_ui()

        if records:
            run_bg(work, done)

    def __init__(self, app):
        super().__init__(app)
        self._slots: list[ImageSlot] = []
        self._active = -1
        self._slot_seq = 0
        self.fine_mode = True
        self.snap_mode = True
        self.magnifier_zoom = MAGNIFIER_ZOOM_DEFAULT
        self._batch_running = False
        self._auto_running = False
        self._building_list = False
        self._icons: list = []
        self._originals = ByteLRU(128 * 1024 * 1024,
                                  size_of=lambda img: img.sizeInBytes(), max_items=2)
        self._active_original = self._active_preview = None
        self._source_loading = False
        self._view_visible = True
        self._load_error = None

        self._build_ui()
        self._bind_shortcuts()
        self._sync_ui()

    # ---------- UI ----------
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "caption")
        return label

    @staticmethod
    def _separator(vertical: bool = False) -> QFrame:
        line = QFrame()
        line.setStyleSheet("background: palette(shadow); border: none;")
        if vertical:
            line.setFixedWidth(1)
        else:
            line.setFixedHeight(1)
        return line

    def _register_icon(self, widget: QWidget, name: str, role: str = "button"):
        self._icons.append((widget, name, role))
        self._apply_icon(widget, name, role)

    def _apply_icon(self, widget: QWidget, name: str, role: str):
        widget.setIcon(line_icon(name, self._icon_color(role), 16))
        widget.setIconSize(QSize(16, 16))

    @staticmethod
    def _icon_color(role: str) -> str:
        pal = current_palette()
        if role == "primary":
            return pal.color(QPalette.ColorRole.HighlightedText).name()
        return pal.color(QPalette.ColorRole.ButtonText).name()

    def on_theme_changed(self):
        for widget, name, role in self._icons:
            self._apply_icon(widget, name, role)
        self.canvas.update()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_list_panel())
        self.splitter.addWidget(self._build_canvas_panel())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([270, 840])
        root.addWidget(self.splitter, 1)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)

        self.btn_undo = icon_button("", "撤销 (Ctrl+Z)", (30, 26), emoji=False,
                                    on_click=self.undo)
        self.btn_redo = icon_button("", "重做 (Ctrl+Y)", (30, 26), emoji=False,
                                    on_click=self.redo)
        self._register_icon(self.btn_undo, "undo")
        self._register_icon(self.btn_redo, "redo")
        h.addWidget(self.btn_undo)
        h.addWidget(self.btn_redo)

        h.addWidget(self._separator(vertical=True))
        h.addWidget(self._caption("放大镜"))
        self.btn_zoom_out = icon_button("−", "缩小放大镜", (26, 24), emoji=False,
                                        on_click=lambda: self._set_zoom(self.magnifier_zoom - 1))
        self.zoom_label = QLabel(f"{self.magnifier_zoom}x")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(36)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.zoom_label.setFont(f)
        self.btn_zoom_in = icon_button("+", "放大放大镜", (26, 24), emoji=False,
                                       on_click=lambda: self._set_zoom(self.magnifier_zoom + 1))
        h.addWidget(self.btn_zoom_out)
        h.addWidget(self.zoom_label)
        h.addWidget(self.btn_zoom_in)

        h.addWidget(self._separator(vertical=True))
        self.btn_auto = QPushButton("自动对齐", bar)
        self.btn_auto.setProperty("variant", "secondary")
        self.btn_auto.setToolTip("自动识别内容边界，一键裁掉黑边")
        self.btn_auto.clicked.connect(self._auto_align)
        self._register_icon(self.btn_auto, "expand")
        h.addWidget(self.btn_auto)
        h.addWidget(self._separator(vertical=True))
        self.cb_fine = QCheckBox("精细模式", bar)
        self.cb_fine.setToolTip("精细模式：鼠标移动 20px → 参考线移动 1px")
        self.cb_fine.setChecked(True)
        self.cb_fine.toggled.connect(self._on_fine_toggled)
        h.addWidget(self.cb_fine)
        self.cb_snap = QCheckBox("边界吸附", bar)
        self.cb_snap.setToolTip("边界吸附：参考线靠近颜色边缘时自动对齐")
        self.cb_snap.setChecked(True)
        self.cb_snap.toggled.connect(self._on_snap_toggled)
        h.addWidget(self.cb_snap)

        h.addStretch(1)
        self.btn_clear = QPushButton("清空", bar)
        self.btn_clear.setProperty("variant", "secondary")
        self.btn_clear.setToolTip("清空全部图片（各自调整过的裁切区域将丢失）")
        self.btn_clear.clicked.connect(self._clear_all)
        self._register_icon(self.btn_clear, "trash")
        h.addWidget(self.btn_clear)
        return bar

    def _build_list_panel(self) -> QFrame:
        card = QFrame(self.splitter)
        card.setProperty("variant", "card")
        card.setMinimumWidth(240)
        card.setMaximumWidth(340)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("图片列表", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.list_count_label = self._caption("0 张")
        head.addWidget(self.list_count_label)
        v.addLayout(head)

        self.list_widget = _ImageCropListWidget(card)
        self.list_widget.setIconSize(QSize(40, 40))
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._list_menu)
        self.list_widget.importRequested.connect(self._show_list_import_menu)
        self.list_widget.currentRowChanged.connect(self._on_list_row_changed)
        v.addWidget(self.list_widget, 1)

        hint = self._caption("拖入图片/文件夹，双击导入图片/文件夹")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return card

    def _build_canvas_panel(self) -> QWidget:
        right = QWidget(self.splitter)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(12)

        card = QFrame(right)
        card.setProperty("variant", "card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 12)
        cv.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.status_label = _ElidedLabel(
            "将图片拖入窗口，或双击左侧图片列表导入图片或文件夹", card)
        self.status_label.setProperty("role", "caption")
        head.addWidget(self.status_label, 1)
        self.file_label = _ElidedLabel("", card)
        self.file_label.setProperty("role", "caption")
        self.file_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.file_label.setMaximumWidth(280)
        head.addWidget(self.file_label, 1)
        cv.addLayout(head)
        self.canvas = CropCanvas(self)
        self.canvas.setToolTip("拖动四条参考线调整裁切 · Ctrl+Z 撤销 · Ctrl+Y 重做 · Shift 加速拖动")
        cv.addWidget(self.canvas, 1)
        rv.addWidget(card, 1)

        bar = QFrame(right)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)
        h.addWidget(self._caption("裁切尺寸"))
        self.btn_size = QPushButton("— × — px", bar)
        self.btn_size.setProperty("variant", "secondary")
        self.btn_size.setMinimumWidth(112)
        self.btn_size.setToolTip("点击输入精确裁切尺寸")
        self.btn_size.clicked.connect(self._open_precise_input)
        h.addWidget(self.btn_size)
        h.addStretch(1)

        self.btn_copy = QPushButton("复制", bar)
        self.btn_copy.setProperty("variant", "secondary")
        self.btn_copy.setToolTip("把当前裁切结果复制到剪贴板，可直接粘到 PS / PR / 微信")
        self.btn_copy.clicked.connect(self._copy_current)
        self._register_icon(self.btn_copy, "copy")
        h.addWidget(self.btn_copy)
        self.btn_export = QPushButton("导出", bar)
        self.btn_export.setProperty("variant", "secondary")
        self.btn_export.setToolTip("把当前裁切结果另存为图片")
        self.btn_export.clicked.connect(self._export_current)
        self._register_icon(self.btn_export, "download")
        h.addWidget(self.btn_export)
        self.btn_batch = QPushButton("批量导出", bar)
        self.btn_batch.setProperty("variant", "primary")
        self.btn_batch.setToolTip("全部图片按各自裁切区域导出到 download/图片裁切/")
        self.btn_batch.clicked.connect(self._batch_export)
        self._register_icon(self.btn_batch, "download", "primary")
        h.addWidget(self.btn_batch)
        rv.addWidget(bar)
        return right

    def _bind_shortcuts(self):
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.redo)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        sc_del = QShortcut(QKeySequence.Delete, self.list_widget)
        sc_del.activated.connect(self._delete_selected)

    # ---------- 槽位 ----------
    def active_slot(self) -> Optional[ImageSlot]:
        if 0 <= self._active < len(self._slots):
            return self._slots[self._active]
        return None

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.webp);;所有文件 (*.*)")
        if paths:
            self._load_paths(list(paths))

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if path:
            self.add_dropped_folder(path)

    def _show_list_import_menu(self, position: QPoint):
        menu = QMenu(self.list_widget)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        menu.addAction("导入图片…", self._pick_files)
        menu.addAction("导入文件夹…", self._pick_folder)
        menu.popup(self.list_widget.viewport().mapToGlobal(position))

    def add_dropped_files(self, files: list):
        imgs = [f for f in files if os.path.splitext(f)[1].lower() in VALID_EXTS]
        if not imgs:
            self.app.toast("拖入的文件中没有受支持的图片格式（PNG/JPG/JPEG/WebP）", "warning")
            return
        self._load_paths(imgs)

    def add_dropped_folder(self, path: str):
        if not os.path.isdir(path):
            return
        files = [os.path.join(path, f) for f in os.listdir(path)
                 if os.path.splitext(f)[1].lower() in VALID_EXTS]
        if not files:
            self.app.toast("该文件夹内没有图片文件", "warning")
            return
        self._load_paths(files)

    def _load_paths(self, paths: list):
        existing = {s.path for s in self._slots}
        todo = [p for p in paths if p not in existing]
        if not todo:
            self.app.toast("这些图片已在列表中", "info")
            return

        def job():
            loaded = []
            failed = []
            for p in todo:
                try:
                    slot = read_image_slot(p)
                except Exception:
                    failed.append(os.path.basename(p))
                    continue
                loaded.append(slot)
            return loaded, failed

        def done(res, err):
            if err:
                self.app.toast(f"导入失败：{err}", "error")
                return
            loaded, failed = res
            if failed:
                self.app.toast(f"{len(failed)} 个文件解码失败：{failed[0]} 等", "warning")
            if not loaded:
                return
            slots = []
            existing = {s.path for s in self._slots}
            for slot in loaded:
                if slot.path in existing:
                    continue
                existing.add(slot.path)
                self._slot_seq += 1
                slot.sid = self._slot_seq
                slots.append(slot)
            self._slots.extend(slots)
            self._rebuild_list()
            self._select_slot(len(self._slots) - 1)
            if len(slots) > 1:
                self.app.toast(f"已导入 {len(slots)} 张图片", "success")

        run_bg(job, done)

    def _rebuild_list(self):
        self._building_list = True
        self.list_widget.clear()
        for slot in self._slots:
            item = QListWidgetItem(slot.name)
            self._apply_item_text(item, slot)
            icon_pm = QPixmap.fromImage(slot.image).scaled(
                40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item.setIcon(QIcon(icon_pm))
            item.setData(Qt.UserRole, slot.sid)
            self.list_widget.addItem(item)
        self._building_list = False

    def _apply_item_text(self, item: QListWidgetItem, slot: ImageSlot):
        avail = max(90, self.list_widget.viewport().width() - 52)
        name = self.list_widget.fontMetrics().elidedText(
            slot.name, Qt.TextElideMode.ElideMiddle, avail)
        item.setText(f"{name}\n{slot.crop.width}×{slot.crop.height}")
        item.setToolTip(slot.name)

    def _on_list_row_changed(self, row: int):
        if self._building_list or row < 0 or row >= len(self._slots):
            return
        self._select_slot(row)

    def _list_menu(self, pos):
        row = self.list_widget.row(self.list_widget.itemAt(pos))
        if row < 0:
            return
        menu = QMenu(self.list_widget)
        act = menu.addAction("删除该图")
        if menu.exec(self.list_widget.mapToGlobal(pos)) == act:
            self._delete_at(row)

    def _delete_selected(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            self._delete_at(row)

    def _delete_at(self, row: int):
        if not (0 <= row < len(self._slots)):
            return
        removed = self._slots.pop(row)
        self._originals.pop((removed.path, removed.stamp), None)
        if self._active >= len(self._slots):
            self._active = len(self._slots) - 1
        self._rebuild_list()
        target = min(self._active, len(self._slots) - 1)
        if target >= 0:
            self._select_slot(target)
        else:
            self._active = -1
            self._active_original = self._active_preview = None
            self.canvas.reset_interaction()
            self._sync_ui()
        self.app.toast(f"已删除：{removed.name}", "info")

    def _clear_all(self):
        if not self._slots:
            return
        ret = QMessageBox.question(
            self, "清空全部图片",
            f"确定清空全部 {len(self._slots)} 张图片？（各自调整过的裁切区域将丢失）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self._slots.clear()
        self._originals.clear()
        self._active_original = self._active_preview = None
        self._active = -1
        self._rebuild_list()
        self.canvas.reset_interaction()
        self._sync_ui()
        self.app.toast("已清空全部图片", "info")

    def _select_slot(self, index: int):
        if not (0 <= index < len(self._slots)):
            return
        self._active = index
        self._active_original = self._active_preview = None
        self._load_error = None
        self.canvas.reset_interaction()
        if self.list_widget.currentRow() != index:
            self._building_list = True
            self.list_widget.setCurrentRow(index)
            self._building_list = False
        slot = self._slots[index]
        for other in self._slots:
            if other is not slot:
                other.profiles = None
        self._start_source_load()
        self._sync_ui()

    def _start_source_load(self):
        slot = self.active_slot()
        if slot is None or self._source_loading or not self._view_visible:
            return
        self._source_loading = True
        want_profiles = self.snap_mode and slot.profiles is None

        def job():
            if slot.stamp is not None and _image_stamp(slot.path) != slot.stamp:
                raise RuntimeError("原图已发生变化，请重新导入后再裁切")
            image = self._originals.get((slot.path, slot.stamp))
            if image is None:
                image = read_original(slot)
            preview = (image.scaled(QSize(1600, 1600), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                       if image.width() > 1600 or image.height() > 1600 else image)
            profiles = compute_edge_profiles(image.convertToFormat(QImage.Format_Grayscale8)) if want_profiles else None
            return image, preview, profiles

        def done(result, err):
            self._source_loading = False
            current = self.active_slot()
            if not self._view_visible:
                return
            if current is not slot:
                self._start_source_load()
                return
            if err:
                self._load_error = str(err)
                self.app.toast(str(err), "error")
            else:
                image, preview, profiles = result
                self._originals[(slot.path, slot.stamp)] = image
                self._active_original, self._active_preview = image, preview
                if profiles is not None:
                    slot.profiles = profiles
                self.canvas.reset_interaction()
            self._sync_ui()
            if not err and self.snap_mode and slot.profiles is None:
                self._start_source_load()

        run_bg(job, done)

    def on_hide(self):
        self._view_visible = False
        self.canvas.reset_interaction()
        self._active_original = self._active_preview = None
        self._originals.clear()
        for slot in self._slots:
            slot.profiles = None
        self._sync_ui()

    def on_show(self):
        self._view_visible = True
        self._load_error = None
        if self._active_original is None:
            self._start_source_load()
        self._sync_ui()

    # ---------- 裁切提交 / 历史 ----------
    def commit_crop(self, slot: ImageSlot, crop: CropRect):
        img_w, img_h = slot.width, slot.height
        crop = clamp_crop(crop, img_w, img_h)
        slot.crop = crop
        slot.display_crop = crop.copy()
        trimmed = slot.history[:slot.history_index + 1]
        trimmed.append(crop.copy())
        slot.history = trimmed
        slot.history_index = len(trimmed) - 1
        self._sync_ui()
        self.canvas.update()
        self.canvas.crop_committed.emit()

    def on_live_crop_changed(self):
        slot = self.active_slot()
        if slot is None:
            return
        dc = slot.display_crop
        w = max(0, round(dc.right) - round(dc.left))
        h = max(0, round(dc.bottom) - round(dc.top))
        self.btn_size.setText(f"{w} × {h} px")

    def _focus_in_text_input(self) -> bool:
        fw = QApplication.focusWidget()
        return isinstance(fw, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit))

    def undo(self):
        slot = self.active_slot()
        if slot is None or slot.history_index <= 0 or self._focus_in_text_input():
            return
        slot.history_index -= 1
        c = slot.history[slot.history_index]
        slot.crop = c.copy()
        slot.display_crop = c.copy()
        self._sync_ui()
        self.canvas.update()

    def redo(self):
        slot = self.active_slot()
        if slot is None or slot.history_index >= len(slot.history) - 1 \
                or self._focus_in_text_input():
            return
        slot.history_index += 1
        c = slot.history[slot.history_index]
        slot.crop = c.copy()
        slot.display_crop = c.copy()
        self._sync_ui()
        self.canvas.update()

    # ---------- 模式开关 ----------
    def _on_fine_toggled(self, checked: bool):
        self.fine_mode = checked

    def _on_snap_toggled(self, checked: bool):
        self.snap_mode = checked
        if checked:
            slot = self.active_slot()
            if slot is not None:
                self._ensure_profiles(slot)

    def _ensure_profiles(self, slot: ImageSlot):
        if slot.profiles is not None or slot.profile_pending:
            return
        self._start_source_load()

    # ---------- 放大镜倍率 ----------
    def _set_zoom(self, zoom: int):
        zoom = max(MAGNIFIER_ZOOM_MIN, min(MAGNIFIER_ZOOM_MAX, zoom))
        if zoom != self.magnifier_zoom:
            self.magnifier_zoom = zoom
            self.zoom_label.setText(f"{zoom}x")

    # ---------- 自动对齐 / 精确输入 ----------
    def _auto_align(self):
        slot = self.active_slot()
        if slot is None or self._auto_running:
            return
        image = self._active_original
        if image is None:
            return
        self._auto_running = True
        self.btn_auto.setEnabled(False)

        def job():
            return find_content_bounds(image)

        def done(res, err):
            self._auto_running = False
            self.btn_auto.setEnabled(True)
            if err:
                self.app.toast(f"自动识别失败：{err}", "error")
                return
            if res is None:
                self.app.toast("未检测到有效内容区域", "warning")
                return
            self.commit_crop(slot, res)
            self.app.toast("已自动对齐内容边界", "success")

        run_bg(job, done)

    def _open_precise_input(self):
        slot = self.active_slot()
        if slot is None:
            return
        dlg = CropInputDialog(self, slot.width, slot.height,
                              slot.display_crop)
        if dlg.exec() == QDialog.Accepted:
            self.commit_crop(slot, dlg.value())
            self.app.toast("裁切区域已更新", "success")
        dlg.deleteLater()

    # ---------- 导出 ----------
    def _export_current(self):
        slot = self.active_slot()
        if slot is None:
            return
        crop = slot.crop
        if crop.width <= 0 or crop.height <= 0:
            self.app.toast("裁切区域无效", "warning")
            return
        base = os.path.splitext(slot.name)[0] or "image"
        default = os.path.join(os.path.dirname(slot.path), f"{base}_cropped{slot.ext}")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出裁切图片", default,
            "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp)")
        if not path:
            return
        image = self._active_original
        if image is None:
            return
        saved = save_cropped(image, crop, path)
        if saved:
            self.app.toast(f"导出成功：{saved}", "success")
        else:
            self.app.toast("导出失败：图片保存出错", "error")

    def _copy_current(self):
        """把当前槽位的裁切结果以无损形式复制到剪贴板（不写文件）。"""
        slot = self.active_slot()
        if slot is None:
            self.app.toast("请先导入图片", "warning")
            return
        crop = slot.crop
        if crop.width <= 0 or crop.height <= 0:
            self.app.toast("裁切区域无效", "warning")
            return
        if self._active_original is None:
            return
        img = self._active_original.copy(QRect(crop.left, crop.top, crop.width, crop.height))
        if img.isNull():
            self.app.toast("复制失败：裁切结果为空", "error")
            return
        QApplication.clipboard().setImage(img)
        self.app.toast(f"已复制到剪贴板（{img.width()}×{img.height()}）", "success")

    def _batch_export(self):
        if self._batch_running:
            return
        if not self._slots:
            self.app.toast("请先导入图片", "warning")
            return
        from toolbox.config_manager import get_export_dir
        out_dir = get_export_dir() / "图片裁切"
        os.makedirs(out_dir, exist_ok=True)
        slots = tuple(replace(s, crop=s.crop.copy(), history=[], profiles=None) for s in self._slots)
        total = len(slots)
        cancel = threading.Event()
        dlg = QProgressDialog("正在导出...", "取消", 0, total, self)
        dlg.setWindowTitle("批量导出")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.canceled.connect(cancel.set)
        dlg.show()
        self._batch_running = True
        self.btn_batch.setEnabled(False)

        def job():
            ok = 0
            fails = []
            for i, slot in enumerate(slots):
                if cancel.is_set():
                    break
                post_to_main(dlg.setValue, i)
                post_to_main(dlg.setLabelText, f"{i + 1}/{total} · {slot.name}")
                crop = slot.crop
                if crop.width <= 0 or crop.height <= 0:
                    fails.append(slot.name)
                    continue
                base = os.path.splitext(slot.name)[0] or f"image_{i + 1}"
                out = os.path.join(out_dir, f"{base}_cropped{slot.ext}")
                n = 2
                while os.path.exists(out):
                    out = os.path.join(out_dir, f"{base}_cropped_{n}{slot.ext}")
                    n += 1
                try:
                    image = read_original(slot)
                    saved = save_cropped(image, crop, out)
                    del image
                    if saved:
                        ok += 1
                    else:
                        fails.append(slot.name)
                except Exception:  # noqa: BLE001
                    fails.append(slot.name)
            return ok, fails, cancel.is_set()

        def done(res, err):
            self._batch_running = False
            self.btn_batch.setEnabled(True)
            dlg.close()
            dlg.deleteLater()
            if err:
                self.app.toast(f"批量导出失败：{err}", "error")
                return
            ok, fails, cancelled = res
            msg = f"批量导出完成：{ok}/{total} 张 → {out_dir}"
            if fails:
                msg += f"（失败 {len(fails)} 张）"
            if cancelled:
                msg = f"已取消：导出 {ok}/{total} 张 → {out_dir}"
            self.app.toast(msg, "success" if not fails else "warning")

        run_bg(job, done)

    # ---------- 状态刷新 ----------
    def _sync_ui(self):
        slot = self.active_slot()
        has = slot is not None
        ready = has and self._active_original is not None
        self.btn_undo.setEnabled(has and slot.history_index > 0)
        self.btn_redo.setEnabled(has and slot.history_index < len(slot.history) - 1)
        self.btn_auto.setEnabled(ready and not self._auto_running)
        self.btn_export.setEnabled(ready)
        self.btn_copy.setEnabled(ready)
        self.btn_clear.setEnabled(bool(self._slots))
        self.list_count_label.setText(f"{len(self._slots)} 张")
        if has:
            self.btn_size.setText(f"{slot.crop.width} × {slot.crop.height} px")
            self.file_label.setText(slot.name)
            self.file_label.setToolTip(slot.path)
            self.status_label.setText(
                self._load_error or (f"原图 {slot.width}×{slot.height} · "
                f"裁切 {slot.crop.width}×{slot.crop.height}" + (" · 正在读取原图…" if not ready else "")))
            self.status_label.setToolTip(
                "拖动参考线调整裁切 · Ctrl+Z 撤销 · Ctrl+Y 重做 · Shift 加速拖动")
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == slot.sid:
                    self._apply_item_text(self.list_widget.item(i), slot)
        else:
            self.btn_size.setText("— × — px")
            self.file_label.setText("")
            self.file_label.setToolTip("")
            self.status_label.setText(
                "选择左侧图片开始裁切" if self._slots else
                "将图片拖入窗口，或双击左侧图片列表导入图片或文件夹")
            self.status_label.setToolTip(self.status_label.text())
