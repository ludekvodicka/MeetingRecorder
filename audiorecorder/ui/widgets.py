"""Small themed building blocks shared by the main window and its dialogs."""

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from audiorecorder.ui import icons, theme

CHIP_TEXT = {
    "neutral": "text.secondary",
    "success": "success",
    "accent": "accent",
    "warning": "warning",
    "error": "error",
    "live": "live",
    "muted": "rec",
    "rec": "text.onFill",
}


def repolish(widget):
    """Re-read the style sheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget, name, value):
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    repolish(widget)
    for child in widget.findChildren(QWidget):
        repolish(child)


class IconLabel(QLabel):
    """A tinted icon that follows the theme."""

    def __init__(self, name, tint, size=16, parent=None):
        super().__init__(parent)
        self._name = name
        self._tint = tint
        self._size = size
        self._angle = 0
        self.setFixedSize(size, size)
        theme.notifier.changed.connect(self._render)
        self._render()

    def set_icon(self, name, tint=None):
        self._name = name
        if tint is not None:
            self._tint = tint
        self._render()

    def set_angle(self, angle):
        self._angle = angle
        self._render()

    def _render(self):
        if not self._name:
            self.clear()
            return
        tint = theme.solid(self._tint) if not self._tint.startswith("#") else self._tint
        pixmap = icons.pixmap(self._name, tint, self._size)
        if self._angle:
            ratio = pixmap.devicePixelRatio()
            pixmap = pixmap.transformed(
                QTransform().rotate(self._angle), Qt.TransformationMode.SmoothTransformation)
            # Rotation grows the bounding box; crop back to the centre.
            side = int(self._size * ratio)
            x = (pixmap.width() - side) // 2
            y = (pixmap.height() - side) // 2
            pixmap = pixmap.copy(x, y, side, side)
            pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)


class Chip(QFrame):
    """A rounded status label with an icon: 20 px high, tinted by kind."""

    def __init__(self, kind="neutral", icon=None, text="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)
        self._icon = IconLabel(icon or "", CHIP_TEXT[kind], 12)
        self._text = QLabel(text)
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._spin = QTimer(self)
        self._spin.setInterval(50)
        self._spin.timeout.connect(self._turn)
        self._angle = 0
        self.set(kind, icon, text)

    def set(self, kind, icon, text, spinning=False):
        self.setProperty("chip", kind)
        repolish(self)
        repolish(self._text)
        self._icon.setVisible(bool(icon))
        self._icon.set_icon(icon or "", CHIP_TEXT[kind])
        self._text.setText(text)
        if spinning:
            self._spin.start()
        else:
            self._spin.stop()
            self._angle = 0
            self._icon.set_angle(0)

    def text(self):
        return self._text.text()

    def _turn(self):
        # One turn every 900 ms.
        self._angle = (self._angle + 20) % 360
        self._icon.set_angle(self._angle)


class RecChip(QFrame):
    """The filled REC pill with a pulsing dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("chip", "rec")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        self._dot = Dot("text.onFill", 7)
        layout.addWidget(self._dot)
        label = QLabel("REC")
        font = label.font()
        font.setLetterSpacing(font.SpacingType.AbsoluteSpacing, 0.6)
        label.setFont(font)
        layout.addWidget(label)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pulse = Pulse(self._dot)

    def start(self):
        self._pulse.start()

    def stop(self):
        self._pulse.stop()


class Pulse:
    """Opacity 1 → 0.35 → 1 every 1.2 s."""

    def __init__(self, widget):
        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(1.0)
        widget.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", widget)
        self._animation.setDuration(1200)
        self._animation.setStartValue(1.0)
        self._animation.setKeyValueAt(0.5, 0.35)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)

    def start(self):
        if self._animation.state() != QPropertyAnimation.State.Running:
            self._animation.start()

    def stop(self):
        self._animation.stop()
        self._effect.setOpacity(1.0)


class Dot(QWidget):
    def __init__(self, tint, size=8, parent=None):
        super().__init__(parent)
        self._tint = tint
        self.setFixedSize(size, size)
        theme.notifier.changed.connect(self.update)

    def set_tint(self, tint):
        self._tint = tint
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.color(self._tint)))
        painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))


class ElidedLabel(QLabel):
    """One line that ends in an ellipsis instead of widening its layout."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def setText(self, text):
        self._full = text
        self.setToolTip("")
        self._elide()

    def text(self):
        return self._full

    def minimumSizeHint(self):
        return QSize(10, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        elided = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(10, self.width()))
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


class BusyBar(QWidget):
    """A 3 px indeterminate progress line: a segment sweeps the track."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance)

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _advance(self):
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width, height = self.width(), self.height()
        painter.setBrush(QBrush(theme.color("accent.soft")))
        painter.drawRoundedRect(QRectF(0, 0, width, height), 1.5, 1.5)
        segment = width * 0.3
        x = -segment + (width + segment) * self._phase
        left, right = max(0.0, x), min(float(width), x + segment)
        if right > left:
            painter.setBrush(QBrush(theme.color("accent")))
            painter.drawRoundedRect(QRectF(left, 0, right - left, height), 1.5, 1.5)


class ProgressDots(QWidget):
    """Three 5 px dots; the solid one walks along every 300 ms."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(25, 19)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._advance)

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _advance(self):
        self._step = (self._step + 1) % 3
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            painter.setBrush(QBrush(theme.color("live" if i == self._step else "live.border")))
            painter.drawEllipse(QRectF(i * 8, 7, 5, 5))


class CountdownRing(QWidget):
    """A ring that empties as the countdown runs, with the seconds left in the middle."""

    def __init__(self, size=44, tint="rec", parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._tint = tint
        self._remaining = 0
        self._total = 1

    def set_remaining(self, remaining, total=None):
        self._remaining = max(0, remaining or 0)
        if total is not None:
            self._total = max(1, total)
        self._total = max(self._total, self._remaining)
        self.update()

    def set_tint(self, tint):
        self._tint = tint
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = self.width()
        stroke = 3
        rect = QRectF(stroke / 2 + 1, stroke / 2 + 1, side - stroke - 2, side - stroke - 2)
        pen = QPen(theme.color("border.default"), stroke)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen = QPen(theme.color(self._tint), stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        span = int(-360 * 16 * self._remaining / self._total)
        painter.drawArc(rect, 90 * 16, span)
        font = painter.font()
        font.setFamily(theme.MONO)
        font.setPixelSize(16 if side >= 40 else 14)
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.color("text.primary"))
        painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter,
                         str(self._remaining))


def fade(owner, apply, duration=1200):
    """Call apply with a strength from 1 down to 0, easing out, then with None."""
    animation = QVariantAnimation(owner)
    animation.setDuration(duration)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.valueChanged.connect(apply)
    animation.finished.connect(lambda: apply(None))
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def spaced(text, wide=False):
    """Button text set off from its icon.

    Qt draws a button's icon and text with a fixed small gap that style sheets cannot
    widen, so the gap the design asks for (6 px, 8 to 10 px on pills) comes from spaces.
    """
    return ("  " if wide else " ") + text


def keycap(text):
    label = QLabel(text)
    label.setProperty("keycap", True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def with_alpha(key, factor):
    value = QColor(theme.color(key))
    value.setAlphaF(value.alphaF() * factor)
    return value
