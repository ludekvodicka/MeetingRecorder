from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


class AudioPlayerWidget(QWidget):
    playback_stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._seeking = False
        self._current_path = ""

        self._build_ui()

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def _build_ui(self):
        # Shown inside the row that plays, under its meta line.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(10)

        self._time_label = QLabel("0:00")
        self._time_label.setObjectName("playerTime")
        layout.addWidget(self._time_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._on_seek_start)
        self._slider.sliderReleased.connect(self._on_seek_end)
        self._slider.setEnabled(False)
        layout.addWidget(self._slider, stretch=1)

        self._duration_label = QLabel("0:00")
        self._duration_label.setObjectName("playerTotal")
        layout.addWidget(self._duration_label)

    @property
    def current_path(self):
        return self._current_path

    @property
    def is_playing(self):
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play_file(self, path):
        self._current_path = path
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

        self._slider.setEnabled(True)

    def stop_playback(self):
        self._player.stop()
        self._player.setSource(QUrl())
        self._reset_ui()

    def _reset_ui(self):
        self._slider.setValue(0)
        self._slider.setEnabled(False)
        self._time_label.setText("0:00")
        self._duration_label.setText("0:00")
        self._current_path = ""

    def _on_position_changed(self, position):
        if not self._seeking:
            self._slider.setValue(position)
        self._time_label.setText(self._format_ms(position))

    def _on_duration_changed(self, duration):
        self._slider.setRange(0, duration)
        self._duration_label.setText(self._format_ms(duration))

    def _on_state_changed(self, state):
        if (state == QMediaPlayer.PlaybackState.StoppedState
                and self._player.position() >= self._player.duration() - 100):
                self._reset_ui()
                self.playback_stopped.emit()

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_end(self):
        self._player.setPosition(self._slider.value())
        self._seeking = False

    def _format_ms(self, ms):
        s = ms // 1000
        m = s // 60
        s = s % 60
        if m >= 60:
            h = m // 60
            m = m % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
