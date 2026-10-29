"""Qt signal adapter for the synchronous transcription service."""

from PyQt6.QtCore import QObject, pyqtSignal

from audiorecorder.transcription.service import TranscriptionOptions, TranscriptionService


class SonioxWorker(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_key, audio_path, language_hints=None,
                 primary_language="en", enable_translation=False, output_path=None, options=None):
        super().__init__()
        options = options or TranscriptionOptions(
            primary_language, tuple(language_hints or ()), enable_translation)
        self.service = TranscriptionService(
            api_key, audio_path, options, output_path=output_path, progress=self.progress.emit)

    def run(self):
        try:
            result = self.service.run()
        except Exception as error:
            self.error.emit(str(error))
        else:
            self.completed.emit(result)
