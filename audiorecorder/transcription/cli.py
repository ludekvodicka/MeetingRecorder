"""Noninteractive Soniox transcription; no QApplication or capture devices required."""

import argparse
import json
import sys
from pathlib import Path

from audiorecorder import secrets
from audiorecorder.config import load_config
from audiorecorder.transcription.soniox import SonioxWorker
from audiorecorder.version import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="Transcribe a local recording with Soniox.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--language", help="Language hint, or auto (default: app setting)")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate without credentials or uploading",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    audio = args.audio.expanduser().absolute()
    output = audio.with_suffix(".md")

    def result(status, code=0, message=None):
        data = {"status": status, "audio": str(audio), "transcript": str(output)}
        if message:
            data["message"] = message
        stream = sys.stderr if code else sys.stdout
        print(json.dumps(data) if args.json else f"{status}: {message or output}", file=stream)
        return code

    if not audio.is_file() or not audio.stat().st_size or audio.suffix.lower() not in {
        ".m4a", ".wav", ".mp3", ".flac", ".ogg", ".mp4", ".webm",
    }:
        return result("error", 2, "Expected a nonempty supported audio file")
    if output.exists() or output.is_symlink():
        if output.is_file() and output.stat().st_size:
            return result("skipped")
        return result("error", 2, "Output exists but is empty or invalid; move it aside first")
    if args.dry_run:
        return result("dry-run")

    try:
        config = load_config()
        key = secrets.get_api_key_headless()
        if not key:
            return result("error", 1, "Soniox key missing; configure Keychain or SONIOX_API_KEY")
        lang = args.language or config.get("language", "en")
        worker = SonioxWorker(
            api_key=key, audio_path=str(audio), language_hints=[] if lang == "auto" else [lang],
            primary_language=lang, enable_translation=False,
        )
        errors, completed = [], []
        worker.error.connect(errors.append)
        worker.completed.connect(completed.append)
        worker.progress.connect(lambda message: print(message, file=sys.stderr))
        worker.run()
        if errors or not completed or not output.exists() or not output.stat().st_size:
            # Provider exceptions may include sensitive response data. Keep diagnostics bounded.
            return result("error", 1, "Soniox transcription failed; no output published")
        return result("completed")
    except secrets.SecretsError as exc:
        return result("error", 1, str(exc))
    except KeyboardInterrupt:
        return result("interrupted", 130, "Interrupted; remote job may still exist")
    except Exception as exc:
        return result("error", 1, type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
