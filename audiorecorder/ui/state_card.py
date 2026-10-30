"""The card at the top of the main window: what the recorder is doing right now.

Idle, recording, stop failed and dictation each give the card its own colour, so the state
reads from across the room and not only from a button label.
"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from audiorecorder.ui import icons, theme
from audiorecorder.ui.level_bars import DualLevelBars
from audiorecorder.ui.widgets import (
    Chip,
    Dot,
    IconLabel,
    ProgressDots,
    RecChip,
    keycap,
    set_prop,
    spaced,
)

STATES = ("idle", "starting", "recording", "stopping", "failed", "dictation")


def _row(spacing=8):
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def _holder(layout):
    widget = QWidget()
    widget.setLayout(layout)
    return widget


class StateCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stateCard")
        self.setProperty("state", "idle")
        self._state = "idle"
        self._muted = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        top = _row(16)
        left = QVBoxLayout()
        left.setSpacing(4)

        # One status line per state; only the line for the current state is visible.
        self._idle_line = _row(6)
        self._idle_dot = Dot("text.tertiary", 8)
        self._idle_text = QLabel("Ready to record")
        self._idle_text.setObjectName("stateLine")
        self._idle_line.addWidget(self._idle_dot)
        self._idle_line.addWidget(self._idle_text)
        self._idle_line.addStretch()
        self._idle_holder = _holder(self._idle_line)

        rec_line = _row(8)
        self._rec_chip = RecChip()
        self._muted_chip = Chip("muted", "mic-off", "Mic muted")
        self._rec_text = QLabel("Recording system audio and microphone")
        self._rec_text.setObjectName("recLine")
        rec_line.addWidget(self._rec_chip)
        rec_line.addWidget(self._muted_chip)
        rec_line.addWidget(self._rec_text, 1)
        self._rec_holder = _holder(rec_line)

        alert_line = _row(6)
        self._alert_icon = IconLabel("triangle-alert", "error", 14)
        self._alert_text = QLabel("Stop failed")
        self._alert_text.setObjectName("stateLine")
        self._alert_text.setProperty("tone", "error")
        alert_line.addWidget(self._alert_icon)
        alert_line.addWidget(self._alert_text)
        alert_line.addStretch()
        self._alert_holder = _holder(alert_line)

        dict_line = _row(6)
        dict_line.addWidget(IconLabel("keyboard", "live", 14))
        dict_title = QLabel("Dictation on: types into any app")
        dict_title.setObjectName("stateLine")
        dict_title.setProperty("tone", "live")
        dict_line.addWidget(dict_title)
        dict_line.addStretch()
        self._dict_holder = _holder(dict_line)

        for holder in (self._idle_holder, self._rec_holder, self._alert_holder, self._dict_holder):
            left.addWidget(holder)

        self.timer = QLabel("00:00:00")
        self.timer.setObjectName("timer")
        left.addWidget(self.timer)

        self._shortcuts = QWidget()
        shortcuts = QVBoxLayout(self._shortcuts)
        shortcuts.setContentsMargins(0, 6, 0, 0)
        shortcuts.setSpacing(8)
        for keys, meaning in ((("Ctrl", "Space"), "Dictate"),
                              (("Ctrl", "Shift", "Space"), "Dictate and translate")):
            line = _row(4)
            for index, key in enumerate(keys):
                if index:
                    plus = QLabel("+")
                    plus.setObjectName("plus")
                    line.addWidget(plus)
                line.addWidget(keycap(key))
            hint = QLabel(meaning)
            hint.setObjectName("keyHint")
            line.addSpacing(8)
            line.addWidget(hint)
            line.addStretch()
            shortcuts.addLayout(line)
        left.addWidget(self._shortcuts)

        top.addLayout(left, 1)

        buttons = _row(8)
        self.details_button = QPushButton("Details")
        self.details_button.setObjectName("detailsButton")
        self.mute_button = QPushButton("Mute mic")
        self.mute_button.setObjectName("muteButton")
        self.mute_button.setCheckable(True)
        self.record_button = QPushButton("Record")
        self.record_button.setObjectName("recordButton")
        for button in (self.details_button, self.mute_button, self.record_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            buttons.addWidget(button)
        top.addLayout(buttons)
        outer.addLayout(top)

        self._failure = QWidget()
        failure = QVBoxLayout(self._failure)
        failure.setContentsMargins(0, 0, 0, 0)
        failure.setSpacing(4)
        self._fail_title = QLabel()
        self._fail_title.setObjectName("failTitle")
        self._fail_title.setWordWrap(True)
        self._fail_detail = QLabel()
        self._fail_detail.setObjectName("failDetail")
        self._fail_detail.setWordWrap(True)
        failure.addWidget(self._fail_title)
        failure.addWidget(self._fail_detail)
        outer.addWidget(self._failure)

        self._preview = QFrame()
        self._preview.setObjectName("dictPreview")
        preview = QVBoxLayout(self._preview)
        preview.setContentsMargins(12, 10, 12, 10)
        preview.setSpacing(6)
        tag = _row(6)
        live_tag = QLabel("LIVE")
        live_tag.setObjectName("liveTag")
        tag.addWidget(live_tag)
        tag.addWidget(ProgressDots())
        tag.addStretch()
        preview.addLayout(tag)
        self._preview_text = QLabel()
        self._preview_text.setObjectName("previewText")
        self._preview_text.setWordWrap(True)
        self._preview_text.setTextFormat(Qt.TextFormat.PlainText)
        preview.addWidget(self._preview_text)
        outer.addWidget(self._preview)

        self._pasted = _holder(_row(8))
        self._pasted.layout().addWidget(IconLabel("check", "success", 14))
        pasted_tag = QLabel("Pasted")
        pasted_tag.setObjectName("pastedTag")
        self._pasted_text = QLabel()
        self._pasted_text.setObjectName("pastedText")
        self._pasted_text.setTextFormat(Qt.TextFormat.PlainText)
        self._pasted.layout().addWidget(pasted_tag)
        self._pasted.layout().addWidget(self._pasted_text, 1)
        outer.addWidget(self._pasted)

        self.meters = DualLevelBars()
        outer.addWidget(self.meters)

        self._dict_hint = _holder(_row(6))
        self._dict_hint.layout().addWidget(IconLabel("info", "text.secondary", 13))
        hint = QLabel(
            "Recording is unavailable while Dictation is on. Turn Dictation off to record.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self._dict_hint.layout().addWidget(hint, 1)
        outer.addWidget(self._dict_hint)

        theme.notifier.changed.connect(self._render_buttons)
        self.set_state("idle")

    @property
    def state(self):
        return self._state

    def set_state(self, state, muted=False, recording_line=None, failure=None):
        if state not in STATES:
            raise ValueError(f"unknown recorder state: {state}")
        self._state = state
        self._muted = muted and state == "recording"
        card_state = {"starting": "idle", "stopping": "recording"}.get(state, state)
        set_prop(self, "state", card_state)
        set_prop(self.timer, "state", card_state)

        recording = state in ("recording", "stopping")
        self._idle_holder.setVisible(state in ("idle", "starting"))
        self._idle_text.setText("Starting capture…" if state == "starting" else "Ready to record")
        self._rec_holder.setVisible(recording)
        self._muted_chip.setVisible(self._muted)
        if recording_line is not None:
            self._rec_text.setText(recording_line)
        if state == "stopping":
            self._rec_text.setText("Saving the recording…")
        if recording:
            self._rec_chip.start()
        else:
            self._rec_chip.stop()
        self._alert_holder.setVisible(state == "failed")
        self._dict_holder.setVisible(state == "dictation")
        self.timer.setVisible(state != "dictation")
        self._shortcuts.setVisible(state == "dictation")

        self.details_button.setVisible(state == "failed")
        self.mute_button.setVisible(recording)
        self.mute_button.setEnabled(state == "recording")
        self.mute_button.setChecked(self._muted)
        self.mute_button.setText(spaced("Unmute mic" if self._muted else "Mute mic", True))
        self.record_button.setEnabled(state in ("idle", "recording", "failed"))
        self.record_button.setText(spaced({
            "idle": "Record", "starting": "Record", "recording": "Stop",
            "stopping": "Stop", "failed": "Retry stop", "dictation": "Record",
        }[state], True))
        self.record_button.setToolTip(
            "Turn off Dictation to record" if state == "dictation" else "")

        self._failure.setVisible(state == "failed")
        if failure is not None:
            title, detail = failure
            self._fail_title.setText(title)
            self._fail_detail.setText(detail)

        dictation = state == "dictation"
        self._preview.setVisible(dictation and bool(self._preview_text.text()))
        self._pasted.setVisible(dictation and bool(self._pasted_text.text()))
        self._dict_hint.setVisible(dictation)
        self.meters.set_dictation(dictation)
        self.meters.setVisible(state != "failed")
        self.meters.set_muted(self._muted)
        if not dictation:
            self._preview_text.setText("")
            self._pasted_text.setText("")
        self._render_buttons()

    def set_preview(self, text):
        self._preview_text.setText(text)
        self._preview.setVisible(self._state == "dictation" and bool(text))

    def set_pasted(self, text):
        self._pasted_text.setText(text)
        self._pasted.setVisible(self._state == "dictation" and bool(text))

    def _render_buttons(self):
        enabled = self.record_button.isEnabled()
        tint = theme.solid("text.onFill" if enabled else "text.disabled")
        glyph, size = {
            "recording": ("square", 12), "stopping": ("square", 12),
            "failed": ("rotate-ccw", 16), "dictation": ("lock", 15),
        }.get(self._state, ("circle", 12))
        self.record_button.setIcon(icons.icon(glyph, tint, size))
        self.record_button.setIconSize(QSize(size, size))
        mute_tint = theme.solid("text.onFill" if self._muted else "text.primary")
        self.mute_button.setIcon(icons.icon("mic-off" if self._muted else "mic", mute_tint, 16))
