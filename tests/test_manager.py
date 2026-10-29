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
        assert re.fullmatch(r"Recording \d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} [a-f0-9]{8}\.m4a", name)


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


@pytest.mark.parametrize('stage,label', [
    ('polling_original_id', 'Saved job'),
    ('submitting_original_id', 'Request status unknown'),
])
def test_scan_shows_recoverable_or_uncertain_job(tmp_path, stage, label):
    import json

    make_recording(tmp_path, 'meeting')
    audio = tmp_path / 'meeting.m4a'
    (tmp_path / 'meeting.transcription.json').write_text(json.dumps({
        'version': 1, 'stage': stage, 'file_id': 'file', 'original_id': 'job',
        'audio': {'size': audio.stat().st_size, 'mtime_ns': audio.stat().st_mtime_ns},
        'options': {'primary_language': 'en', 'language_hints': [], 'enable_translation': False},
    }))
    recording = RecordingManager(str(tmp_path)).scan()[0]
    assert label in recording.transcription_status


@pytest.mark.parametrize('action', ['delete', 'rename', 'move'])
def test_file_operations_refuse_a_transcription_owned_by_another_worker(tmp_path, action):
    from audiorecorder.transcription.jobs import TranscriptionJob

    make_recording(tmp_path, 'meeting', transcript=True)
    audio = tmp_path / 'meeting.m4a'
    destination = tmp_path / 'destination'
    destination.mkdir()
    manager = RecordingManager(str(tmp_path))
    with TranscriptionJob(audio).locked(), pytest.raises(RuntimeError, match='another process'):
        if action == 'delete':
            manager.delete(str(audio))
        elif action == 'rename':
            manager.rename(str(audio), 'renamed')
        else:
            manager.move(str(audio), str(destination))
    assert audio.read_bytes() == b'not really audio'
    assert audio.with_suffix('.md').read_text() == '# Transcript'


def test_move_takes_all_sidecars_without_overwriting_an_orphan_destination(tmp_path):
    from audiorecorder.recordings.manager import SIDECAR_SUFFIXES

    make_recording(tmp_path, 'meeting')
    audio = tmp_path / 'meeting.m4a'
    for suffix in SIDECAR_SUFFIXES:
        audio.with_suffix(suffix).write_text(suffix)
    destination = tmp_path / 'destination'
    destination.mkdir()
    orphan = destination / 'meeting.transcription.json'
    orphan.write_text('existing recovery state')
    manager = RecordingManager(str(tmp_path))
    with pytest.raises(FileExistsError):
        manager.move(str(audio), str(destination))
    assert audio.exists()
    assert orphan.read_text() == 'existing recovery state'
    orphan.unlink()
    result = manager.move(str(audio), str(destination))
    assert result == str(destination / audio.name)
    assert not audio.exists()
    for suffix in SIDECAR_SUFFIXES:
        assert (destination / audio.with_suffix(suffix).name).read_text() == suffix


def test_move_restores_completed_files_when_a_later_sidecar_fails(tmp_path, monkeypatch):
    from audiorecorder.recordings import manager as module

    make_recording(tmp_path, 'meeting', transcript=True)
    audio = tmp_path / 'meeting.m4a'
    checkpoint = audio.with_suffix('.transcription.json')
    checkpoint.write_text('job state')
    destination = tmp_path / 'destination'
    destination.mkdir()
    move = module.shutil.move
    def fail_checkpoint(source, target):
        if source == str(checkpoint):
            raise OSError('injected failure')
        return move(source, target)
    monkeypatch.setattr(module.shutil, 'move', fail_checkpoint)
    with pytest.raises(OSError, match='injected failure'):
        RecordingManager(str(tmp_path)).move(str(audio), str(destination))
    assert audio.read_bytes() == b'not really audio'
    assert audio.with_suffix('.md').read_text() == '# Transcript'
    assert checkpoint.read_text() == 'job state'
    assert not (destination / audio.name).exists()


def test_scan_corrupt_checkpoint_is_actionable_and_does_not_crash(tmp_path):
    make_recording(tmp_path, 'meeting')
    (tmp_path / 'meeting.transcription.json').write_text('[1,2,3]')
    recording = RecordingManager(str(tmp_path)).scan()[0]
    assert recording.transcription_status == 'Job state unreadable — review before retrying'


@pytest.mark.parametrize('action', ['rename', 'move'])
def test_transfer_cannot_overwrite_destination_with_active_transcription(tmp_path, action):
    from audiorecorder.transcription.jobs import TranscriptionJob

    make_recording(tmp_path, 'meeting', transcript=True)
    source = tmp_path / 'meeting.m4a'
    destination = tmp_path / 'destination'
    destination.mkdir()
    target = tmp_path / 'renamed.m4a' if action == 'rename' else destination / source.name
    with (
        TranscriptionJob(target).locked(),
        pytest.raises(RuntimeError, match='another process'),
    ):
        manager = RecordingManager(str(tmp_path))
        if action == 'rename':
            manager.rename(str(source), target.stem)
        else:
            manager.move(str(source), str(destination))
    assert source.exists()
    assert source.with_suffix('.md').exists()
    assert not target.exists()
