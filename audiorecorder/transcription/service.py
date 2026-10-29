"""Synchronous batch-transcription use case shared by GUI and headless callers."""

import os
from dataclasses import dataclass

from audiorecorder.transcription.client import SonioxClient
from audiorecorder.transcription.jobs import TranscriptionJob, atomic_write
from audiorecorder.transcription.markdown import (
    detect_dominant_language,
    language_name,
    render_transcript,
    render_transcript_with_translation,
)


@dataclass(frozen=True)
class TranscriptionOptions:
    primary_language: str = "en"
    language_hints: tuple[str, ...] = ()
    enable_translation: bool = False

    @classmethod
    def from_config(cls, config, *, language=None, translate=True):
        lang = language or config.get("language", "en")
        return cls(
            primary_language=lang,
            language_hints=() if lang == "auto" else (lang,),
            enable_translation=bool(translate and config.get("enable_translation", False)
                                    and lang != "auto"),
        )


class TranscriptionService:
    def __init__(self, api_key, audio_path, options, *, output_path=None,
                 progress=lambda message: None, client=None):
        self.audio_path = str(audio_path)
        self.output_path = output_path
        self.options = options
        self.progress = progress
        self.client = client or SonioxClient(api_key, options.language_hints, progress)

    def run(self):
        with TranscriptionJob(self.audio_path).locked() as job:
            output_path = self.output_path or os.path.splitext(self.audio_path)[0] + ".md"
            if os.path.exists(output_path):
                raise RuntimeError("Transcript already exists; existing output was preserved")
            options = {
                "language_hints": list(self.options.language_hints),
                "primary_language": self.options.primary_language,
                "enable_translation": self.options.enable_translation,
            }
            state = job.load(options)
            file_id = state.get("file_id")
            if not file_id:
                self.progress("Uploading audio...")
                file_id = self.client.upload_file(self.audio_path)
                job.save(state, file_id=file_id, stage="uploaded")

            def transcribe(key, **kwargs):
                transcription_id = state.get(key)
                if not transcription_id:
                    # A crash/timeout during POST is ambiguous. Persist intent first;
                    # recovery must never silently submit the same paid job twice.
                    job.save(state, stage=f"submitting_{key}")
                    transcription_id = self.client.create_transcription(file_id, **kwargs)
                    job.save(state, **{key: transcription_id}, stage=f"polling_{key}")
                else:
                    self.progress("Resuming saved transcription...")
                self.client.poll_status(transcription_id)
                self.progress("Fetching transcript...")
                tokens = self.client.get_transcript(transcription_id)
                if not any(t.get("text", "").strip() for t in tokens):
                    raise RuntimeError("Soniox returned an empty transcript")
                return tokens

            self.progress("Transcribing...")
            tokens = transcribe("original_id", translate=False)
            detected_lang = detect_dominant_language(tokens, fallback=self.options.primary_language)
            target = self.options.primary_language
            if self.options.enable_translation and detected_lang != target:
                self.progress(f"Translating to {language_name(target)}...")
                translated = transcribe("translation_id", translate=True, target=target)
                document = render_transcript_with_translation(
                    tokens, translated, source_name=os.path.basename(self.audio_path),
                    original_lang=detected_lang, translation_lang=target,
                )
            else:
                document = render_transcript(
                    tokens, source_name=os.path.basename(self.audio_path),
                    language=detected_lang,
                )
            self.progress("Saving transcript...")
            atomic_write(output_path, document, replace=False)
            try:
                job.save(state, stage="completed")
            except OSError:
                # Publication is the commit point. Earlier IDs still permit recovery;
                # a failed status update must not turn a saved transcript into a failure.
                self.progress("Transcript saved; job status update failed")
        self.progress("Done")
        return output_path
