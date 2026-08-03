"""The sidecar written from what the subtitles showed, and the companions of a recording.

The sidecar is free, the text is already on screen when the recording stops. What it must
not do is appear when nothing was recognised, or get left behind when the recording it
belongs to is renamed, moved or deleted.
"""

import pytest

from audiorecorder.recordings.manager import SIDECAR_SUFFIXES, RecordingManager, sidecars
from audiorecorder.subtitles.document import (
    live_transcript_path,
    render_live_transcript,
    write_live_transcript,
)

PAIRS = [
    ("10:14:02", "Hello everyone.", "Ahoj všichni."),
    ("10:14:09", "Thank you for joining.", "Děkujeme, že jste se připojili."),
]


class TestRendering:
    def test_both_languages_are_present(self):
        out = render_live_transcript(PAIRS, "call.m4a", translation_language="cs")
        assert "Hello everyone." in out
        assert "Ahoj všichni." in out

    def test_the_original_is_quoted_so_one_language_can_be_skimmed(self):
        out = render_live_transcript(PAIRS, "call.m4a", translation_language="cs")
        assert "> **[10:14:02]** Hello everyone." in out
        assert "> Ahoj" not in out

    def test_each_line_carries_the_time_it_was_said(self):
        out = render_live_transcript(PAIRS, "call.m4a", translation_language="cs")
        assert "10:14:02" in out and "10:14:09" in out

    def test_the_header_names_the_source_and_the_language(self):
        out = render_live_transcript(PAIRS, "call.m4a", translation_language="cs")
        assert "**Source:** call.m4a" in out
        assert "**Translated into:** Czech" in out

    def test_without_a_translation_the_language_line_is_absent(self):
        out = render_live_transcript([("10:00:00", "Ahoj.", "")], "call.m4a")
        assert "Translated into" not in out
        assert "> **[10:00:00]** Ahoj." in out

    def test_an_empty_transcript_still_renders_something_readable(self):
        out = render_live_transcript([], "call.m4a")
        assert "Nothing was recognised." in out

    def test_it_says_the_other_transcript_is_the_fuller_one(self):
        """So nobody mistakes the subtitles for the transcript of record."""
        assert "fuller record" in render_live_transcript(PAIRS, "call.m4a")


class TestWriting:
    def test_the_sidecar_sits_beside_the_recording(self, tmp_path):
        recording = tmp_path / "Recording 2026-08-03 10-00-00.m4a"
        recording.write_bytes(b"audio")

        written = write_live_transcript(str(recording), PAIRS, "cs")

        assert written == tmp_path / "Recording 2026-08-03 10-00-00.live.md"
        assert "Ahoj všichni." in written.read_text(encoding="utf-8")

    def test_nothing_recognised_writes_no_file(self, tmp_path):
        """An empty file beside every recording made with the toggle on would be noise."""
        recording = tmp_path / "quiet.m4a"
        recording.write_bytes(b"audio")

        assert write_live_transcript(str(recording), []) is None
        assert not (tmp_path / "quiet.live.md").exists()

    def test_the_path_is_derived_from_the_recording(self):
        assert live_transcript_path("/tmp/a b.m4a").name == "a b.live.md"


class TestSidecars:
    def make(self, directory, stem, suffixes):
        (directory / f"{stem}.m4a").write_bytes(b"audio")
        for suffix in suffixes:
            (directory / f"{stem}{suffix}").write_text("x", encoding="utf-8")
        return str(directory / f"{stem}.m4a")

    def test_every_companion_is_found(self, tmp_path):
        recording = self.make(tmp_path, "call", SIDECAR_SUFFIXES)
        assert {p.name for p in sidecars(recording)} == {
            "call.md", "call.summary.md", "call.live.md"}

    def test_missing_companions_are_not_invented(self, tmp_path):
        recording = self.make(tmp_path, "call", [".md"])
        assert [p.name for p in sidecars(recording)] == ["call.md"]

    def test_delete_takes_all_of_them(self, tmp_path):
        """The summary used to be left behind, because delete only knew about .md."""
        recording = self.make(tmp_path, "call", SIDECAR_SUFFIXES)

        RecordingManager(str(tmp_path)).delete(recording)

        assert list(tmp_path.iterdir()) == []

    def test_rename_takes_all_of_them(self, tmp_path):
        recording = self.make(tmp_path, "old", SIDECAR_SUFFIXES)

        RecordingManager(str(tmp_path)).rename(recording, "new")

        assert {p.name for p in tmp_path.iterdir()} == {
            "new.m4a", "new.md", "new.summary.md", "new.live.md"}

    def test_rename_keeps_the_compound_suffixes_intact(self, tmp_path):
        recording = self.make(tmp_path, "old", [".summary.md", ".live.md"])

        RecordingManager(str(tmp_path)).rename(recording, "new")

        assert (tmp_path / "new.summary.md").exists()
        assert (tmp_path / "new.live.md").exists()

    def test_rename_still_refuses_to_overwrite(self, tmp_path):
        self.make(tmp_path, "one", [])
        self.make(tmp_path, "two", [])

        with pytest.raises(FileExistsError):
            RecordingManager(str(tmp_path)).rename(str(tmp_path / "one.m4a"), "two")
