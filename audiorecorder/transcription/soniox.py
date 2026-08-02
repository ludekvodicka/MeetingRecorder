import os
import time

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from audiorecorder.transcription.markdown import (
    detect_dominant_language,
    language_name,
    render_transcript,
    render_transcript_with_translation,
)

API_BASE = "https://api.soniox.com/v1"


class SonioxWorker(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_key, audio_path, language_hints=None,
                 primary_language="en", enable_translation=False):
        super().__init__()
        self.api_key = api_key
        self.audio_path = audio_path
        self.language_hints = language_hints or []
        self.primary_language = primary_language
        self.enable_translation = enable_translation

    def run(self):
        try:
            self.progress.emit("Uploading audio...")
            file_id = self._upload_file()

            # First pass: transcribe with language identification
            self.progress.emit("Transcribing...")
            trans_id = self._create_transcription(file_id, translate=False)
            self._poll_status(trans_id)

            self.progress.emit("Fetching transcript...")
            tokens = self._get_transcript(trans_id)

            detected_lang = detect_dominant_language(tokens, fallback=self.primary_language)
            self.progress.emit(f"Detected language: {detected_lang}")

            output_path = os.path.splitext(self.audio_path)[0] + ".md"
            target = self.primary_language

            if self.enable_translation and detected_lang != target:
                self.progress.emit(f"Translating to {language_name(target)}...")
                trans_id_target = self._create_transcription(file_id, translate=True, target=target)
                self._poll_status(trans_id_target)
                tokens_translated = self._get_transcript(trans_id_target)
                document = render_transcript_with_translation(
                    tokens, tokens_translated,
                    source_name=os.path.basename(self.audio_path),
                    original_lang=detected_lang, translation_lang=target,
                )
            else:
                document = render_transcript(
                    tokens, source_name=os.path.basename(self.audio_path),
                    language=detected_lang,
                )
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(document)

            self.progress.emit("Done")
            self.completed.emit(output_path)

        except Exception as e:
            self.error.emit(str(e))

    def _upload_file(self):
        with open(self.audio_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": f},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()["id"]

    def _create_transcription(self, file_id, translate=False, target="cs"):
        body = {
            "model": "stt-async-v4",
            "file_id": file_id,
            "enable_language_identification": True,
            "enable_speaker_diarization": True,
        }
        # No hints at all means "identify it yourself", which is what the auto setting wants.
        if self.language_hints:
            body["language_hints"] = self.language_hints
        if translate:
            body["translation"] = {
                "type": "one_way",
                "target_language": target,
            }

        r = requests.post(
            f"{API_BASE}/transcriptions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["id"]

    def _poll_status(self, transcription_id, timeout=1800):
        start = time.time()
        while True:
            r = requests.get(
                f"{API_BASE}/transcriptions/{transcription_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            self.progress.emit(f"Transcribing... ({status})")
            if status == "completed":
                return
            if status == "error":
                raise RuntimeError(f"Transcription failed: {data}")
            if time.time() - start > timeout:
                raise TimeoutError("Transcription timed out")
            time.sleep(3)

    def _get_transcript(self, transcription_id):
        r = requests.get(
            f"{API_BASE}/transcriptions/{transcription_id}/transcript",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("tokens", [])
