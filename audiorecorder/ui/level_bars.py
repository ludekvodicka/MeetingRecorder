import collections

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

N_BARS = 30
DECAY = 0.85


class BarWidget(QWidget):
    """Animated bar visualizer showing audio level as vertical bars."""

    def __init__(self, color_low="#4caf50", color_high="#ff9800", bar_count=N_BARS):
        super().__init__()
        self._bar_count = bar_count
        self._color_low = QColor(color_low)
        self._color_high = QColor(color_high)
        self._levels = collections.deque([0.0] * bar_count, maxlen=bar_count)
        self._current_level = 0.0
        self.setMinimumHeight(32)
        self.setMaximumHeight(48)

    def set_level(self, level):
        self._current_level = max(0.0, min(1.0, level))

    def advance(self):
        smoothed = self._levels[-1] * DECAY + self._current_level * (1 - DECAY)
        self._levels.append(max(self._current_level, smoothed))
        self.update()

    def reset(self):
        self._levels = collections.deque([0.0] * self._bar_count, maxlen=self._bar_count)
        self._current_level = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        spacing = w / self._bar_count
        bar_w = max(2.0, spacing * 0.7)
        gap = (spacing - bar_w) / 2

        for i, level in enumerate(self._levels):
            x = i * spacing + gap
            bar_h = max(2.0, level * (h - 4))
            y = h - bar_h - 2

            t = min(1.0, level * 1.5)
            r = int(self._color_low.red() + (self._color_high.red() - self._color_low.red()) * t)
            g = int(self._color_low.green()
                    + (self._color_high.green() - self._color_low.green()) * t)
            b = int(self._color_low.blue() + (self._color_high.blue() - self._color_low.blue()) * t)
            color = QColor(r, g, b)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 1.5, 1.5)


class DualLevelBars(QWidget):
    """Two rows of level bars with labels — one for system audio, one for mic."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._system_bars = BarWidget(color_low="#2196f3", color_high="#e91e63")
        layout.addWidget(self._system_bars)

        self._system_label = QLabel("System audio")
        self._system_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._system_label)

        self._mic_bars = BarWidget(color_low="#4caf50", color_high="#ff9800")
        layout.addWidget(self._mic_bars)

        self._mic_label = QLabel("Microphone")
        self._mic_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._mic_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._system_bars.reset()
        self._mic_bars.reset()
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self._system_bars.reset()
        self._mic_bars.reset()

    def set_system_level(self, level):
        self._system_bars.set_level(level)

    def set_mic_level(self, level):
        self._mic_bars.set_level(level)

    def set_device_names(self, system_name, mic_name):
        self._system_label.setText(f"System: {system_name}")
        self._mic_label.setText(f"Mic: {mic_name}")

    def _tick(self):
        self._system_bars.advance()
        self._mic_bars.advance()
