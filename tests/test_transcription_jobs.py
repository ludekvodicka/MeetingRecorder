import json
import os
import subprocess
import sys

import pytest

from audiorecorder.transcription.jobs import TranscriptionJob, atomic_write
from audiorecorder.transcription.soniox import SonioxWorker


@pytest.fixture
def setup_worker(tmp_path, monkeypatch):
    audio = tmp_path / 'meeting.m4a'
    audio.write_bytes(b'fake audio')
    calls = []
    monkeypatch.setattr(SonioxWorker, '_upload_file', lambda self: calls.append('upload') or 'file')
    monkeypatch.setattr(SonioxWorker, '_create_transcription',
                        lambda self, file_id, **kw: calls.append('submit') or 'job')
    monkeypatch.setattr(SonioxWorker, '_poll_status', lambda self, job: None)
    monkeypatch.setattr(SonioxWorker, '_get_transcript', lambda self, job: [
        {'text': 'Hello', 'speaker': 1, 'start_ms': 0, 'language': 'en'},
    ])
    def make(**kwargs):
        worker = SonioxWorker('test-secret', str(audio), **kwargs)
        worker.errors, worker.results = [], []
        worker.error.connect(worker.errors.append)
        worker.completed.connect(worker.results.append)
        return worker
    return audio, calls, make


def test_recovery_reuses_remote_job_after_poll_failure(setup_worker, monkeypatch):
    audio, calls, make = setup_worker
    def fail(self, job):
        raise TimeoutError('offline')
    with monkeypatch.context() as patch:
        patch.setattr(SonioxWorker, '_poll_status', fail)
        first = make()
        first.run()
    assert first.errors == ['offline']
    state = json.loads(audio.with_suffix('.transcription.json').read_text())
    assert state['original_id'] == 'job'
    assert 'test-secret' not in json.dumps(state)
    second = make()
    second.run()
    assert second.results == [str(audio.with_suffix('.md'))]
    assert calls == ['upload', 'submit']


def test_uncertain_submission_never_silently_submits_again(setup_worker, monkeypatch):
    audio, calls, make = setup_worker
    def uncertain(*args, **kwargs):
        calls.append('uncertain submit')
        raise TimeoutError('response lost')
    monkeypatch.setattr(SonioxWorker, '_create_transcription', uncertain)
    first = make()
    first.run()
    second = make()
    second.run()
    assert calls == ['upload', 'uncertain submit']
    assert 'may have reached Soniox' in second.errors[0]
    assert not audio.with_suffix('.md').exists()


def test_changed_settings_or_audio_cannot_reuse_wrong_job(setup_worker, monkeypatch):
    audio, calls, make = setup_worker
    monkeypatch.setattr(SonioxWorker, '_get_transcript', lambda *args: [])
    make().run()
    changed = make(primary_language='cs')
    changed.run()
    assert 'different audio or settings' in changed.errors[0]
    audio.write_bytes(b'different audio')
    changed = make()
    changed.run()
    assert 'different audio or settings' in changed.errors[0]
    assert calls == ['upload', 'submit']


def test_atomic_publication_preserves_existing_file(tmp_path):
    path = tmp_path / 'meeting.md'
    path.write_text('existing')
    with pytest.raises(FileExistsError):
        atomic_write(path, 'new', replace=False)
    assert path.read_text() == 'existing'
    assert not list(tmp_path.glob('.transcript-*'))


def test_os_releases_lock_when_owner_process_crashes(tmp_path):
    audio = tmp_path / 'meeting.m4a'
    audio.write_bytes(b'fake audio')
    script = '''
import sys, time
from audiorecorder.transcription.jobs import TranscriptionJob
with TranscriptionJob(sys.argv[1]).locked():
    print('locked', flush=True)
    time.sleep(30)
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(audio)], stdout=subprocess.PIPE,
                             text=True, env={**os.environ})
    try:
        assert child.stdout.readline().strip() == 'locked'
        with (
            pytest.raises(RuntimeError, match='another process'),
            TranscriptionJob(audio).locked(),
        ):
            pytest.fail('competing lock acquired')
        child.kill()
        child.wait(timeout=3)
        with TranscriptionJob(audio).locked():
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stdout.close()


def test_translation_recovery_reuses_both_remote_jobs(setup_worker, monkeypatch):
    audio, calls, make = setup_worker
    monkeypatch.setattr(SonioxWorker, '_create_transcription',
                        lambda self, file_id, **kw: calls.append('submit') or
                        ('translated' if kw['translate'] else 'original'))
    def tokens(self, job):
        if job == 'translated':
            raise ConnectionError('offline')
        return [{'text': 'Hello', 'start_ms': 0, 'language': 'en', 'speaker': 1}]
    with monkeypatch.context() as patch:
        patch.setattr(SonioxWorker, '_get_transcript', tokens)
        first = make(primary_language='cs', enable_translation=True)
        first.run()
    assert first.errors == ['offline']
    state = json.loads(audio.with_suffix('.transcription.json').read_text())
    assert state['translation_id'] == 'translated'
    second = make(primary_language='cs', enable_translation=True)
    second.run()
    assert second.results
    assert calls == ['upload', 'submit', 'submit']


def test_checkpoint_follows_renamed_audio_and_can_resume(setup_worker, monkeypatch):
    from audiorecorder.recordings.manager import RecordingManager

    audio, calls, make = setup_worker
    with monkeypatch.context() as patch:
        patch.setattr(SonioxWorker, '_get_transcript', lambda *args: [])
        make().run()
    renamed = RecordingManager(str(audio.parent)).rename(str(audio), 'renamed')
    worker = SonioxWorker('test-secret', renamed)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert not errors
    assert audio.with_name('renamed.md').exists()
    assert calls == ['upload', 'submit']
