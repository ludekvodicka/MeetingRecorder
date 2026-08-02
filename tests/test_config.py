import json

import pytest

from audiorecorder import config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the module at a throwaway directory so tests never touch the real settings."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "AudioRecorder")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "AudioRecorder" / "config.json")
    return config.CONFIG_PATH


class TestLoadConfig:
    def test_missing_file_gives_the_defaults(self, isolated_config):
        assert config.load_config() == config.DEFAULTS

    def test_defaults_are_not_shared_between_calls(self, isolated_config):
        first = config.load_config()
        first["output_dir"] = "changed"
        assert config.load_config()["output_dir"] == config.DEFAULTS["output_dir"]

    def test_stored_values_win_over_defaults(self, isolated_config):
        isolated_config.parent.mkdir(parents=True)
        isolated_config.write_text(json.dumps({"language": "cs"}), encoding="utf-8")
        assert config.load_config()["language"] == "cs"

    def test_keys_added_since_the_file_was_written_get_their_default(self, isolated_config):
        isolated_config.parent.mkdir(parents=True)
        isolated_config.write_text(json.dumps({"language": "cs"}), encoding="utf-8")
        loaded = config.load_config()
        assert loaded["summary_prompt"] == config.DEFAULT_SUMMARY_PROMPT
        assert loaded["system_source_name"] is None


class TestSaveConfig:
    def test_creates_the_directory(self, isolated_config):
        config.save_config({"language": "cs"})
        assert isolated_config.exists()

    def test_round_trip(self, isolated_config):
        config.save_config({**config.DEFAULTS, "language": "sk", "mic_volume": 0.5})
        loaded = config.load_config()
        assert loaded["language"] == "sk"
        assert loaded["mic_volume"] == 0.5

    def test_non_ascii_is_written_readably(self, isolated_config):
        config.save_config({**config.DEFAULTS, "output_dir": "C:/Nahrávky"})
        assert "Nahrávky" in isolated_config.read_text(encoding="utf-8")


class TestDefaults:
    def test_public_default_is_english_without_translation(self):
        assert config.DEFAULTS["language"] == "en"
        assert config.DEFAULTS["enable_translation"] is False

    def test_summary_prompt_carries_both_placeholders(self):
        formatted = config.DEFAULT_SUMMARY_PROMPT.format(
            transcript_name="a.md", summary_name="a.summary.md")
        assert "a.md" in formatted
        assert "a.summary.md" in formatted

    def test_devices_are_unset_by_default(self):
        assert config.DEFAULTS["system_source_name"] is None
        assert config.DEFAULTS["mic_source_name"] is None
