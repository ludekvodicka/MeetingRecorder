import collections

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from audiorecorder.ui import theme
from audiorecorder.ui.widgets import IconLabel, set_prop

N_BARS = 30
DECAY = 0.85


def _mix(low, high, t):
    return QColor(
        int(low.red() + (high.red() - low.red()) * t),
        int(low.green() + (high.green() - low.green()) * t),
        int(low.blue() + (high.blue() - low.blue()) * t),
    )


class BarWidget(QWidget):
    """The last 1.5 s of level as 30 bars on a rounded track; new levels enter at the right."""

    def __init__(self, low="meter.micLow", high="meter.micHigh", bar_count=N_BARS):
        super().__init__()
        self._bar_count = bar_count
        self._low = low
        self._high = high
        self._levels = collections.deque([0.0] * bar_count, maxlen=bar_count)
        self._current_level = 0.0
        self._active = False
        self.setFixedHeight(36)
        theme.notifier.changed.connect(self.update)

    def set_level(self, level):
        self._current_level = max(0.0, min(1.0, level))

    def set_active(self, active):
        self._active = active
        self.update()

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
        painter.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        painter.setBrush(QBrush(theme.color("bg.meter")))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)

        gap = 3.0
        inner = w - 16
        bar_w = max(1.0, (inner - gap * (self._bar_count - 1)) / self._bar_count)
        low, high, flat = theme.color(self._low), theme.color(self._high), theme.color("meter.flat")
        for i, level in enumerate(self._levels):
            if self._active:
                bar_h = 4 + min(1.0, level * 1.4) * 26
                color = _mix(low, high, min(1.0, level * 1.5))
            else:
                bar_h, color = 4, flat
            x = 8 + i * (bar_w + gap)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRectF(x, (h - bar_h) / 2, bar_w, bar_h), 2, 2)


class MeterBlock(QWidget):
    """An icon, a label and the device name above one meter."""

    def __init__(self, icon, label, low, high):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.icon = IconLabel(icon, "text.secondary", 14)
        self.label = QLabel(label)
        self.label.setObjectName("meterLabel")
        self.device = QLabel("")
        self.device.setObjectName("meterDevice")
        row.addWidget(self.icon)
        row.addWidget(self.label)
        row.addWidget(self.device, 1)
        layout.addLayout(row)
        self.bars = BarWidget(low, high)
        layout.addWidget(self.bars)


class DualLevelBars(QWidget):
    """System audio and microphone meters side by side."""

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self._system = MeterBlock("volume-2", "System audio", "meter.sysLow", "meter.sysHigh")
        self._mic = MeterBlock("mic", "Microphone", "meter.micLow", "meter.micHigh")
        layout.addWidget(self._system, 1)
        layout.addWidget(self._mic, 1)
        # Kept for callers that address the bars directly.
        self._system_bars = self._system.bars
        self._mic_bars = self._mic.bars
        self._system_label = self._system.device
        self._mic_label = self._mic.device
        self._muted = False
        self._mic_name = ""

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._system_bars.reset()
        self._mic_bars.reset()
        self._system_bars.set_active(True)
        self._mic_bars.set_active(not self._muted)
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self._system_bars.reset()
        self._mic_bars.reset()
        self._system_bars.set_active(False)
        self._mic_bars.set_active(False)
        self.set_device_names("", "")
        self.set_muted(False)

    def set_system_level(self, level):
        self._system_bars.set_level(level)

    def set_mic_level(self, level):
        self._mic_bars.set_level(level)

    def set_device_names(self, system_name, mic_name):
        self._system.device.setText(system_name or "")
        self._mic_name = mic_name or ""
        if not self._muted:
            self._mic.device.setText(self._mic_name)

    def set_muted(self, muted):
        self._muted = muted
        self._mic.icon.set_icon("mic-off" if muted else "mic", "rec" if muted else "text.secondary")
        set_prop(self._mic.label, "muted", muted)
        set_prop(self._mic.device, "muted", muted)
        self._mic.device.setText("Muted" if muted else self._mic_name)
        self._mic_bars.set_active(self._timer.isActive() and not muted)

    def set_dictation(self, on):
        """Dictation listens to the microphone alone."""
        self._system.setVisible(not on)
        self._mic.label.setText("Dictation mic" if on else "Microphone")

    def _tick(self):
        self._system_bars.advance()
        self._mic_bars.advance()
