"""Small dialog for assigning participant names to observed remote speaker IDs."""

import re

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


def _speaker_sort_key(label):
    match = re.search(r"(\d+)$", label)
    return (label[:match.start()] if match else label, int(match.group()) if match else -1)


class SpeakerMappingDialog(QDialog):
    def __init__(self, speakers, mappings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Name meeting speakers")
        self.setMinimumWidth(380)
        self._edits = {}

        layout = QVBoxLayout(self)
        guidance = QLabel(
            "Match each live remote label to a participant. You can reopen this dialog "
            "as more speakers appear.")
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        form = QFormLayout()
        for speaker in sorted(set(speakers), key=_speaker_sort_key):
            edit = QLineEdit((mappings or {}).get(speaker, ""))
            edit.setPlaceholderText("Participant name")
            self._edits[speaker] = edit
            form.addRow(f"{speaker}:", edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mappings(self):
        return {speaker: edit.text().strip() for speaker, edit in self._edits.items()
                if edit.text().strip()}
