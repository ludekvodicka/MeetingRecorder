"""Application settings.

The settings file lives in the per-user configuration directory of the operating system,
not next to the executable, so an installed build never writes into its own program
directory and two accounts on one machine keep separate settings. The Soniox API key is
not part of this file, it belongs to the keyring (see audiorecorder.secrets).
"""

import json
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("AudioRecorder", appauthor=False))
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_SUMMARY_PROMPT = (
    'Summarize the call transcript in the file "{transcript_name}" and write the summary '
    'to "{summary_name}", in the language of the transcript.'
)

DEFAULTS = {
    "output_dir": str(Path.home() / "AudioRecordings"),
    # Primary language of the transcript. When translation is on and the recording turns
    # out to be in another language, it is translated into this one.
    "language": "en",
    "enable_translation": False,
    # Target language of the dictation translate hotkey, independent of the setting above.
    "translation_target": "en",
    # Device names, not indices, which are renumbered whenever hardware comes and goes.
    # None means the platform default, an empty string means "microphone only".
    "system_source_name": None,
    "mic_source_name": None,
    "system_volume": 1.0,
    "mic_volume": 1.0,
    "summary_prompt": DEFAULT_SUMMARY_PROMPT,
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        return {**DEFAULTS, **stored}
    return dict(DEFAULTS)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
