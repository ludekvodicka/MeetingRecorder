"""Soniox HTTP transport; independent of Qt and transcript storage."""

import time

import requests

API_BASE = "https://api.soniox.com/v1"


class SonioxClient:
    def __init__(self, api_key, language_hints=(), progress=lambda message: None):
        self.api_key = api_key
        self.language_hints = list(language_hints)
        self.progress = progress

    def upload_file(self, audio_path):
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": f},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()["id"]

    def create_transcription(self, file_id, translate=False, target="cs"):
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

    def poll_status(self, transcription_id, timeout=1800):
        start = time.monotonic()
        while True:
            r = requests.get(
                f"{API_BASE}/transcriptions/{transcription_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            self.progress(f"Transcribing... ({status})")
            if status == "completed":
                return
            if status == "error":
                raise RuntimeError(f"Transcription failed: {data}")
            if time.monotonic() - start > timeout:
                raise TimeoutError("Transcription timed out")
            time.sleep(3)

    def get_transcript(self, transcription_id):
        r = requests.get(
            f"{API_BASE}/transcriptions/{transcription_id}/transcript",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("tokens", [])
