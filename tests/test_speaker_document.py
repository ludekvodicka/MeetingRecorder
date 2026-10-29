import json

from audiorecorder.subtitles.speaker_document import (
    render_speaker_json,
    render_speaker_markdown,
    speaker_sidecar_paths,
    write_speaker_sidecars,
)

LINES = [
    ("10:14:02", "Me", "I will send the export."),
    ("10:14:06", "Remote 1", "Thank you."),
    ("10:14:09", "Remote 2", "I agree."),
]
NAMES = {"Remote 1": "Radek Švarz", "Remote 2": "Petr Klein"}


class TestMarkdown:
    def test_final_names_apply_to_every_line(self):
        out = render_speaker_markdown(LINES, "call.m4a", NAMES)
        assert "Radek Švarz" in out
        assert "Petr Klein" in out
        assert "I will send the export." in out

    def test_the_local_microphone_remains_me(self):
        assert "[10:14:02] Me" in render_speaker_markdown(LINES, "call.m4a", NAMES)

    def test_anonymous_ids_remain_explicit(self):
        out = render_speaker_markdown(LINES, "call.m4a")
        assert "Remote 1" in out and "Remote 2" in out

    def test_names_keep_the_original_id_for_auditability(self):
        out = render_speaker_markdown(LINES, "call.m4a", NAMES)
        assert "Radek Švarz** (`Remote 1`)" in out


class TestJson:
    def test_it_is_structured_and_unicode_safe(self):
        data = json.loads(render_speaker_json(LINES, "call.m4a", NAMES))
        assert data["schema_version"] == 1
        assert data["speaker_names"]["Remote 1"] == "Radek Švarz"
        assert data["lines"][1]["speaker"] == "Radek Švarz"
        assert data["lines"][1]["speaker_id"] == "Remote 1"


class TestWriting:
    def test_both_sidecars_are_written_beside_the_recording(self, tmp_path):
        recording = tmp_path / "call.m4a"
        recording.write_bytes(b"audio")
        written = write_speaker_sidecars(recording, LINES, NAMES)
        assert written == (
            tmp_path / "call.speakers.md", tmp_path / "call.speakers.json")
        assert all(path.exists() for path in written)

    def test_no_speech_creates_no_noise(self, tmp_path):
        recording = tmp_path / "quiet.m4a"
        assert write_speaker_sidecars(recording, [], NAMES) is None
        assert not any(tmp_path.iterdir())

    def test_paths_preserve_a_compound_recording_name(self):
        markdown, structured = speaker_sidecar_paths("/tmp/weekly.sync.m4a")
        assert markdown.name == "weekly.sync.speakers.md"
        assert structured.name == "weekly.sync.speakers.json"
