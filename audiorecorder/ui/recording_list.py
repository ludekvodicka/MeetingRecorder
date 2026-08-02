from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class RecordingItemWidget(QWidget):
    play_clicked = pyqtSignal(str)
    stop_clicked = pyqtSignal(str)
    transcribe_clicked = pyqtSignal(str)
    cleanup_clicked = pyqtSignal(str)
    move_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)
    open_transcript_clicked = pyqtSignal(str)

    def __init__(self, recording):
        super().__init__()
        self.recording = recording
        self._is_playing = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        top = QHBoxLayout()
        self._name_label = QLabel(self.recording.name)
        self._name_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #e0e0e0;")
        top.addWidget(self._name_label)

        top.addStretch()

        duration = self._format_duration(self.recording.duration_seconds)
        self._duration_label = QLabel(duration)
        self._duration_label.setStyleSheet("color: #aaa; font-size: 12px;")
        top.addWidget(self._duration_label)

        layout.addLayout(top)

        bottom = QHBoxLayout()

        if self.recording.is_transcribed:
            status = QLabel("Transcribed")
            status.setStyleSheet("color: #66bb6a; font-size: 11px;")
        else:
            status = QLabel("Not transcribed")
            status.setStyleSheet("color: #888; font-size: 11px;")
        bottom.addWidget(status)

        date_str = self.recording.date.strftime("%Y-%m-%d %H:%M")
        date_label = QLabel(date_str)
        date_label.setStyleSheet("color: #888; font-size: 11px;")
        bottom.addWidget(date_label)

        bottom.addStretch()

        self._btn_play = QPushButton("Play")
        self._btn_play.setFixedWidth(50)
        self._btn_play.clicked.connect(self._on_play_clicked)
        bottom.addWidget(self._btn_play)

        if self.recording.is_transcribed:
            btn_open = QPushButton("Open")
            btn_open.setFixedWidth(50)
            btn_open.clicked.connect(
                lambda: self.open_transcript_clicked.emit(self.recording.transcript_path))
            bottom.addWidget(btn_open)

            btn_cleanup = QPushButton("Cleanup")
            btn_cleanup.setFixedWidth(60)
            btn_cleanup.setStyleSheet("QPushButton { color: #64b5f6; }")
            btn_cleanup.clicked.connect(lambda: self.cleanup_clicked.emit(self.recording.path))
            bottom.addWidget(btn_cleanup)
        else:
            btn_transcribe = QPushButton("Transcribe")
            btn_transcribe.setFixedWidth(75)
            btn_transcribe.clicked.connect(
                lambda: self.transcribe_clicked.emit(self.recording.path))
            bottom.addWidget(btn_transcribe)

        btn_move = QPushButton("Move")
        btn_move.setFixedWidth(50)
        btn_move.clicked.connect(lambda: self.move_clicked.emit(self.recording.path))
        bottom.addWidget(btn_move)

        btn_delete = QPushButton("Delete")
        btn_delete.setFixedWidth(55)
        btn_delete.setStyleSheet("QPushButton { color: #ef5350; }")
        btn_delete.clicked.connect(lambda: self.delete_clicked.emit(self.recording.path))
        bottom.addWidget(btn_delete)

        layout.addLayout(bottom)

    def _on_play_clicked(self):
        if self._is_playing:
            self.stop_clicked.emit(self.recording.path)
        else:
            self.play_clicked.emit(self.recording.path)

    def set_playing(self, playing):
        self._is_playing = playing
        if playing:
            self._btn_play.setText("Stop")
            self._btn_play.setStyleSheet("QPushButton { color: #ff9800; font-weight: bold; }")
        else:
            self._btn_play.setText("Play")
            self._btn_play.setStyleSheet("")

    def highlight(self):
        self.setStyleSheet("background: #2b3a4a;")
        self._fade_step = 0
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_timer.start(80)

    def _fade_tick(self):
        self._fade_step += 1
        if self._fade_step >= 15:
            self.setStyleSheet("")
            self._fade_timer.stop()
            return
        t = self._fade_step / 15
        r = int(0x2b + (0x2b - 0x2b) * t)
        g = int(0x3a + (0x2b - 0x3a) * t)
        b = int(0x4a + (0x2b - 0x4a) * t)
        self.setStyleSheet(f"background: #{r:02x}{g:02x}{b:02x};")

    def _format_duration(self, seconds):
        if seconds <= 0:
            return "0:00"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


class RecordingListWidget(QWidget):
    play_requested = pyqtSignal(str)
    stop_requested = pyqtSignal(str)
    transcribe_requested = pyqtSignal(str)
    cleanup_requested = pyqtSignal(str)
    move_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    open_transcript_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setStyleSheet("""
            QListWidget {
                border: 1px solid #444;
                border-radius: 4px;
                background: #2b2b2b;
            }
            QListWidget::item {
                border-bottom: 1px solid #3a3a3a;
                background: #2b2b2b;
            }
            QListWidget::item:alternate {
                background: #323232;
            }
            QListWidget::item:selected {
                background: #3d3d3d;
            }
        """)
        layout.addWidget(self._list)

        self._empty_label = QLabel("No recordings yet.\nClick Record to start.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
        layout.addWidget(self._empty_label)

        self._item_widgets = []

    def set_recordings(self, recordings):
        self._list.clear()
        self._item_widgets = []
        self._list.setVisible(bool(recordings))
        self._empty_label.setVisible(not recordings)

        for rec in recordings:
            item_widget = RecordingItemWidget(rec)
            item_widget.play_clicked.connect(self.play_requested.emit)
            item_widget.stop_clicked.connect(self.stop_requested.emit)
            item_widget.transcribe_clicked.connect(self.transcribe_requested.emit)
            item_widget.cleanup_clicked.connect(self.cleanup_requested.emit)
            item_widget.move_clicked.connect(self.move_requested.emit)
            item_widget.delete_clicked.connect(self.delete_requested.emit)
            item_widget.open_transcript_clicked.connect(self.open_transcript_requested.emit)

            item = QListWidgetItem(self._list)
            item.setSizeHint(item_widget.sizeHint())
            self._list.setItemWidget(item, item_widget)
            self._item_widgets.append(item_widget)

    def set_playing_path(self, path):
        for w in self._item_widgets:
            w.set_playing(w.recording.path == path)

    def clear_playing(self):
        for w in self._item_widgets:
            w.set_playing(False)

    def highlight_path(self, path):
        for w in self._item_widgets:
            if w.recording.path == path:
                w.highlight()
                break
