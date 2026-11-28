"""透明录屏画布：直播时钟、礼物榜、弹幕和可编辑备注。"""
import html
import json
import math
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QTextDocument, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QDialog, QFormLayout, QSpinBox, QSlider,
    QPlainTextEdit, QDialogButtonBox, QLabel, QMenu, QMessageBox)
from toolbox.config_manager import get_config_dir
from toolbox.durable_io import atomic_write_json
from gui_qt.gui_text import strip_inline_emoji

BEIJING = timezone(timedelta(hours=8))
OPTIONS = dict(font_size=16, rows=8, background_alpha=235, footer="使用信息请标明来源")


def timestamp_ms(message):
    try:
        value = float(message.get("ts") or message.get("timestamp") or 0)
        return value if math.isfinite(value) and 0 < value < 253402185600000 else None
    except (TypeError, ValueError):
        return None


class GiftLedger:
    """录制窗口累计榜；弹幕姬与回放直接同步其已有榜单。"""
    def __init__(self, platform="douyin"):
        self.platform = platform
        self.rank, self.sessions = {}, {}
        self.seen = deque(maxlen=500)

    def add(self, m):
        if not m.get("giftName"):
            return
        user = str(m.get("nickname") or "未知用户")
        try:
            count, price = max(0, int(m.get("giftCount") or 1)), max(0, int(m.get("giftPrice") or 0))
        except (TypeError, ValueError):
            return
        key, ts = (user, str(m["giftName"])), timestamp_ms(m)
        event_id = m.get("message_id") or m.get("msgId")
        signature = (key, str(event_id)) if event_id else (key, ts, count, bool(m.get("repeatEnd"))) if ts and self.platform not in ("bili", "bilibili") else None
        if signature and signature in self.seen:
            return
        if signature:
            self.seen.append(signature)
        now = ts / 1000 if ts else time.monotonic()
        sess = self.sessions.get(key)
        if self.platform in ("bili", "bilibili"):
            delta = count
        else:
            if sess and (now - sess["time"] > 15 or now < sess["time"]):
                sess = None
            if sess and sess["ended"] and (not m.get("repeatEnd") or count < sess["count"] or count == 1):
                sess = None
            settled = sess["count"] if sess else 0
            delta = max(0, count - settled)
            if count == 1 and sess and not sess["ended"]:
                delta = 1
            self.sessions[key] = dict(count=max(count, settled), time=now, ended=bool(m.get("repeatEnd")))
        if delta:
            row = self.rank.setdefault(user, dict(giftCount=0, totalValue=0))
            row["giftCount"] += delta
            row["totalValue"] += price * delta
        if len(self.sessions) > 2000:
            self.sessions = {k: v for k, v in self.sessions.items() if now - v["time"] <= 15}


class ChatCanvas(QWidget):
    COLORS = ("#7dd3fc", "#c4b5fd", "#f9a8d4", "#86efac", "#fde68a")
    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages = deque(maxlen=80)
        self.font_size, self.rows, self.scale = 16, 8, 1.0

    def add_message(self, name, text, message=None):
        m = message or {}
        if name not in ("弹幕", "表情弹幕", "礼物", "SC", "上舰") and not m.get("giftName"):
            return
        nick = str(m.get("nickname") or "观众")[:80]
        body = str(m.get("content") or text or "")[:600]
        gift = bool(m.get("giftName"))
        if gift:
            body = f"送出了 {m['giftName']} ×{m.get('giftCount') or 1}"
        if body:
            self.messages.append((nick, body, gift))
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bottom = self.height() - 8 * self.scale
        for nick, body, gift in reversed(list(self.messages)[-self.rows:]):
            color = self.COLORS[sum(map(ord, nick)) % len(self.COLORS)]
            display_nick = strip_inline_emoji(nick)
            display_body = strip_inline_emoji(body)
            doc = QTextDocument()
            doc.setDocumentMargin(0)
            doc.setHtml(f'<div style="font-family:Microsoft YaHei;font-size:{self.font_size * self.scale}px;'
                f'color:{"#facc15" if gift else "#ffffff"}"><b style="color:{color}">{html.escape(display_nick)}：</b>{html.escape(display_body)}</div>')
            doc.setTextWidth(max(30, self.width() - 24 * self.scale))
            bottom -= doc.size().height()
            if bottom < 0:
                break
            p.save()
            p.translate(12 * self.scale, bottom)
            doc.drawContents(p)
            p.restore()
            bottom -= 14 * self.scale


class DanmakuOverlay(QWidget):
    def __init__(self, title="实时弹幕", parent=None, *, platform="douyin"):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title + " · 弹幕渲染")
        self.setMinimumSize(800, 450)
        self.resize(1280, 720)
        self.options = dict(OPTIONS)
        try:
            saved = json.loads((get_config_dir() / "danmaku_overlay.json").read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.options.update({k: saved[k] for k in OPTIONS if k in saved})
        except (OSError, ValueError):
            pass
        for key, low, high in (("font_size", 12, 40), ("rows", 2, 20), ("background_alpha", 0, 255)):
            try:
                self.options[key] = max(low, min(high, int(self.options[key])))
            except (ValueError, TypeError):
                self.options[key] = OPTIONS[key]
        self.options["footer"] = str(self.options["footer"])[:600]
        self.canvas = ChatCanvas(self)
        self.platform, self.ledger = platform, GiftLedger(platform)
        self.active, self.online = False, None
        self._epoch_ms, self._clock_start = None, 0.0
        self._clock_running, self._external_clock = False, False
        self._external_rank = None
        self._configure_canvas()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.shortcut.activated.connect(self.edit_settings)

    def _configure_canvas(self):
        for key in ("font_size", "rows", "background_alpha"):
            setattr(self.canvas, key, self.options[key])
        self.canvas.update()
        self.update()

    def reset_session(self, platform=None):
        self.platform = platform or self.platform
        self.ledger = GiftLedger(self.platform)
        self.canvas.messages.clear()
        self._external_rank, self.online = None, None
        self._epoch_ms, self._external_clock = None, False
        self.update()

    def set_active(self, active):
        if not self._external_clock:
            current = self.clock_ms()
            if current is not None:
                self._epoch_ms = current
            self._clock_start = time.monotonic()
            self._clock_running = bool(active)
        self.active = bool(active)
        self.update()

    def set_clock(self, epoch_ms, *, running=False, external=True):
        self._epoch_ms = timestamp_ms({"ts": epoch_ms})
        self._clock_start = time.monotonic()
        self._clock_running, self._external_clock = bool(running), bool(external)
        self.update()

    def clock_ms(self):
        if self._epoch_ms is None:
            return None
        return self._epoch_ms + ((time.monotonic() - self._clock_start) * 1000 if self._clock_running else 0)

    def clock_text(self):
        value = self.clock_ms()
        return datetime.fromtimestamp(value / 1000, BEIJING).strftime("%Y-%m-%d %H:%M:%S") if value else "等待直播时间"

    def set_gift_rank(self, rank):
        self._external_rank = {user: dict(data) for user, data in rank.items()}
        self.update()

    def top_gifts(self):
        rank = self._external_rank if self._external_rank is not None else self.ledger.rank
        return sorted(rank.items(), key=lambda item: (-item[1].get("totalValue", 0), -item[1].get("giftCount", 0), item[0]))[:3]

    def add_message(self, name, text, message=None, *, rank=True):
        m = message or {}
        if not self._external_clock and self._epoch_ms is None:
            ts = timestamp_ms(m)
            if ts:
                self.set_clock(ts, running=self.active, external=False)
        if rank:
            self.ledger.add(m)
        for key in ("memberCount", "roomTotal"):
            if m.get(key) is not None:
                try:
                    self.online = max(0, int(m[key]))
                    break
                except (TypeError, ValueError):
                    pass
        self.canvas.add_message(name, text, m)
        self.update()

    def resizeEvent(self, event):
        self.scale = min(self.width() / 1280, self.height() / 720)
        self.sidebar_width = max(220, int(self.width() * .21))
        self.sidebar_x = self.width() - self.sidebar_width
        top, footer = int(200 * self.scale), int(85 * self.scale)
        self.canvas.scale = self.scale
        self.canvas.setGeometry(self.sidebar_x, top, self.sidebar_width, max(20, self.height() - top - footer))
        super().resizeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scale = getattr(self, "scale", 1.0)
        width = getattr(self, "sidebar_width", 270)
        x = self.width() - width
        p.fillRect(QRectF(x, 0, width, self.height()), QColor(36, 37, 48, self.options["background_alpha"]))
        p.fillRect(QRectF(x, 0, width, 200 * scale), QColor(55, 56, 69, self.options["background_alpha"]))
        def text(rect, value, size=16, color="#ffffff", align=Qt.AlignLeft | Qt.AlignVCenter, shadow=False):
            font = QFont("Microsoft YaHei")
            font.setPixelSize(max(10, int(size * scale)))
            p.setFont(font)
            if shadow:
                p.setPen(QColor(0, 0, 0, 190))
                p.drawText(rect.translated(1, 1), align, value)
            p.setPen(QColor(color))
            p.drawText(rect, align, value)
        text(QRectF(20 * scale, 10 * scale, x - 40 * scale, 30 * scale), "北京时间（UTC+8）", 24, shadow=True)
        text(QRectF(20 * scale, 43 * scale, x - 40 * scale, 32 * scale), self.clock_text(), 24, shadow=True)
        text(QRectF(x - 255 * scale, 18 * scale, 235 * scale, 36 * scale), "引用录屏请标明来源", 16, "#e5e7eb", shadow=True)
        online = f"{self.online:,}" if self.online is not None else "—"
        text(QRectF(x + 12 * scale, 6 * scale, width - 24 * scale, 24 * scale), "在线观众：" + online, 14, "#e5e7eb")
        text(QRectF(x + 12 * scale, 34 * scale, width - 24 * scale, 24 * scale), "礼物贡献榜", 18)
        unit = ("电池" if self.platform in ("bili", "bilibili")
                else "抖币" if self.platform == "douyin" else "")
        ranks = self.top_gifts()
        if not ranks:
            text(QRectF(x + 12 * scale, 95 * scale, width - 24 * scale, 30 * scale), "等待礼物", 14, "#a1a1aa")
        for i, (user, data) in enumerate(ranks):
            y = (65 + i * 43) * scale
            text(QRectF(x + 12 * scale, y, 18 * scale, 36 * scale), str(i + 1), 18, ("#fb7185", "#fb923c", "#facc15")[i])
            p.setBrush(QColor(ChatCanvas.COLORS[sum(map(ord, user)) % 5]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x + 34 * scale, y + 3 * scale, 30 * scale, 30 * scale))
            text(QRectF(x + 34 * scale, y + 3 * scale, 30 * scale, 30 * scale), user[:1], 15, "#22232c", Qt.AlignCenter)
            font = QFont("Microsoft YaHei")
            font.setPixelSize(max(10, int(15 * scale)))
            p.setFont(font)
            nick = p.fontMetrics().elidedText(user, Qt.ElideRight, max(20, int(width - 87 * scale)))
            text(QRectF(x + 74 * scale, y, width - 84 * scale, 20 * scale), nick, 15)
            value = (f" · {data.get('totalValue', 0):,} {unit}" if unit else "")
            text(QRectF(x + 74 * scale, y + 20 * scale, width - 84 * scale, 16 * scale),
                 f"{data.get('giftCount', 0):,} 件{value}", 11, "#d4d4d8")
        text(QRectF(x + 12 * scale, self.height() - 78 * scale, width - 24 * scale, 66 * scale),
             self.options["footer"], 14, "#d4d4d8", Qt.AlignLeft | Qt.AlignBottom | Qt.TextWordWrap)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("画布设置 / 编辑右下角文字", self.edit_settings)
        menu.exec(event.globalPos())

    def edit_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("弹幕画布设置")
        dialog.resize(430, 330)
        form = QFormLayout(dialog)
        size, rows = QSpinBox(), QSpinBox()
        size.setRange(12, 40)
        rows.setRange(2, 20)
        size.setValue(self.options["font_size"])
        rows.setValue(self.options["rows"])
        alpha = QSlider(Qt.Horizontal)
        alpha.setRange(0, 255)
        alpha.setValue(self.options["background_alpha"])
        footer = QPlainTextEdit(self.options["footer"])
        form.addRow("弹幕字号", size)
        form.addRow("显示条数", rows)
        form.addRow("右侧背景透明度", alpha)
        form.addRow("右下角文字", footer)
        tip = QLabel("中间保持透明。右键或 Ctrl+, 可重新打开设置。")
        tip.setWordWrap(True)
        form.addRow(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.Accepted:
            options = dict(font_size=size.value(), rows=rows.value(), background_alpha=alpha.value(), footer=footer.toPlainText()[:600])
            try:
                atomic_write_json(get_config_dir() / "danmaku_overlay.json", options)
            except OSError:
                QMessageBox.warning(self, "未保存", "画布设置保存失败，请检查目录权限")
                return
            self.options = options
            self._configure_canvas()

    def showEvent(self, event):
        self.timer.start(200)
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
