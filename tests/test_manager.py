import re

import pytest

from audiorecorder.recordings.manager import RecordingManager


def make_recording(directory, stem, transcript=False, summary=False):
    """A stand-in recording. Duration reads as 0 because it is not a real container,
    which is exactly what the scanner has to survive."""
    (directory / f"{stem}.m4a").write_bytes(b"not really audio")
    if transcript:
        (directory / f"{stem}.md").write_text("# Transcript", encoding="utf-8")
    if summary:
        (directory / f"{stem}.summary.md").write_text("# Summary", encoding="utf-8")


class TestScan:
    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert RecordingManager(str(tmp_path / "nope")).scan() == []

    def test_empty_directory(self, tmp_path):
        assert RecordingManager(str(tmp_path)).scan() == []

    def test_finds_recordings(self, tmp_path):
        make_recording(tmp_path, "one")
        make_recording(tmp_path, "two")
        assert {r.name for r in RecordingManager(str(tmp_path)).scan()} == {"one", "two"}

    def test_transcript_sidecar_sets_the_status(self, tmp_path):
        make_recording(tmp_path, "with", transcript=True)
        make_recording(tmp_path, "without")
        by_name = {r.name: r for r in RecordingManager(str(tmp_path)).scan()}
        assert by_name["with"].is_transcribed
        assert by_name["with"].transcript_path.endswith("with.md")
        assert not by_name["without"].is_transcribed
        assert by_name["without"].transcript_path == ""

    def test_other_file_types_are_ignored(self, tmp_path):
        make_recording(tmp_path, "audio")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        (tmp_path / "song.mp3").write_bytes(b"x")
        assert [r.name for r in RecordingManager(str(tmp_path)).scan()] == ["audio"]

    def test_unreadable_container_reports_zero_duration_instead_of_raising(self, tmp_path):
        make_recording(tmp_path, "broken")
        assert RecordingManager(str(tmp_path)).scan()[0].duration_seconds == 0.0


class TestGenerateFilename:
    def test_shape(self, tmp_path):
        name = RecordingManager(str(tmp_path)).generate_filename()
        assert re.fullmatch(r"Recording \d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\.m4a", name)


class TestDelete:
    def test_removes_the_audio_and_its_transcript(self, tmp_path):
        make_recording(tmp_path, "gone", transcript=True)
        manager = RecordingManager(str(tmp_path))

        manager.delete(str(tmp_path / "gone.m4a"))

        assert not (tmp_path / "gone.m4a").exists()
        assert not (tmp_path / "gone.md").exists()

    def test_missing_file_is_not_an_error(self, tmp_path):
        RecordingManager(str(tmp_path)).delete(str(tmp_path / "never.m4a"))


class TestRename:
    def test_renames_the_audio_and_its_transcript(self, tmp_path):
        make_recording(tmp_path, "old", transcript=True)
        manager = RecordingManager(str(tmp_path))

        new_path = manager.rename(str(tmp_path / "old.m4a"), "new")

        assert new_path.endswith("new.m4a")
        assert (tmp_path / "new.m4a").exists()
        assert (tmp_path / "new.md").exists()
        assert not (tmp_path / "old.m4a").exists()

    def test_recording_without_a_transcript(self, tmp_path):
        make_recording(tmp_path, "old")
        RecordingManager(str(tmp_path)).rename(str(tmp_path / "old.m4a"), "new")
        assert (tmp_path / "new.m4a").exists()

    def test_refuses_to_overwrite(self, tmp_path):
        make_recording(tmp_path, "one")
        make_recording(tmp_path, "two")
        manager = RecordingManager(str(tmp_path))

        with pytest.raises(FileExistsError):
            manager.rename(str(tmp_path / "one.m4a"), "two")

        assert (tmp_path / "one.m4a").exists()
