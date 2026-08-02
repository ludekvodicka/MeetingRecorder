import math

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QWidget

PILL_W = 110
PILL_H = 32
IDLE_LINE_W = 44
IDLE_LINE_H = 3
N_BARS = 14


class OverlayWidget(QWidget):
    state_changed = pyqtSignal(str)
    level_changed = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.state = "idle"
        self.level = 0.0
        self._bar_phases = [i * 0.5 for i in range(N_BARS)]
        self._t = 0.0

        self.resize(PILL_W, PILL_H)
        self._position_bottom_center()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.state_changed.connect(self._on_state)
        self.level_changed.connect(self._on_level)

    def _position_bottom_center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.center().x() - self.width() // 2
        y = screen.bottom() - self.height() - 60
        self.move(x, y)

    def _on_state(self, s):
        self.state = s
        if s == "recording":
            if not self._timer.isActive():
                self._timer.start(33)
        elif s == "idle":
            self.update()
        self.update()

    def _on_level(self, lvl):
        self.level = max(0.0, min(1.0, lvl))

    def _tick(self):
        self._t += 0.033
        if self.state == "recording":
            self.update()

    def set_state(self, s):
        self.state_changed.emit(s)

    def set_level(self, lvl):
        self.level_changed.emit(lvl)

    def activate(self):
        self._position_bottom_center()
        self.state = "idle"
        self._timer.start(33)
        self.show()

    def deactivate(self):
        self._timer.stop()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.state == "idle":
            self._paint_idle(painter)
        else:
            self._paint_recording(painter)

    def _paint_idle(self, painter):
        w, h = self.width(), self.height()
        line_x = (w - IDLE_LINE_W) / 2
        line_y = (h - IDLE_LINE_H) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(180, 180, 180, 200)))
        rect = QRectF(line_x, line_y, IDLE_LINE_W, IDLE_LINE_H)
        painter.drawRoundedRect(rect, IDLE_LINE_H / 2, IDLE_LINE_H / 2)

    def _paint_recording(self, painter):
        w, h = self.width(), self.height()

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        painter.fillPath(path, QBrush(QColor(20, 20, 20, 240)))

        bar_area_pad_x = 18
        bar_spacing = (w - bar_area_pad_x * 2) / N_BARS
        bar_width = max(2.0, bar_spacing * 0.55)
        center_y = h / 2

        base_level = 0.20
        amp = min(1.1, base_level + self.level * 1.1)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 240)))

        for i in range(N_BARS):
            dist_from_center = abs(i - (N_BARS - 1) / 2) / ((N_BARS - 1) / 2)
            envelope = 1.0 - dist_from_center * 0.4

            phase = self._t * 6 + self._bar_phases[i]
            wave = (math.sin(phase) + 1) / 2

            bar_h = max(3.0, amp * envelope * (0.4 + wave * 0.6) * (h - 10))

            x = bar_area_pad_x + i * bar_spacing + (bar_spacing - bar_width) / 2
            y = center_y - bar_h / 2

            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), bar_width / 2, bar_width / 2)
