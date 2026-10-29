import os
import subprocess
import sys

import pytest

from audiorecorder.transcription import client
from audiorecorder.transcription.service import TranscriptionOptions


@pytest.mark.parametrize('language,translation,hints,enabled', [
    ('auto', True, (), False),
    ('en', False, ('en',), False),
    ('cs', True, ('cs',), True),
])
def test_options_share_language_rules_but_cli_can_disable_translation(
    language, translation, hints, enabled,
):
    config = {'language': language, 'enable_translation': translation}
    gui = TranscriptionOptions.from_config(config)
    cli = TranscriptionOptions.from_config(config, translate=False)
    assert gui.language_hints == hints
    assert gui.enable_translation is enabled
    assert cli.language_hints == hints
    assert not cli.enable_translation
    assert TranscriptionOptions.from_config(config, language='auto').language_hints == ()


def test_cli_success_and_dry_run_work_when_qt_is_unavailable(tmp_path):
    audio = tmp_path / 'meeting.m4a'
    audio.write_bytes(b'test audio')
    script = '''
import importlib.abc, json, sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PyQt6' or fullname.startswith('PyQt6.'):
            raise AssertionError('CLI attempted to import Qt')
sys.meta_path.insert(0, NoQt())
from audiorecorder.transcription import cli
from audiorecorder.transcription.client import SonioxClient
cli.load_config = lambda: {'language': 'en'}
cli.secrets.get_api_key_headless = lambda: 'fake-key'
SonioxClient.upload_file = lambda self, path: 'file'
SonioxClient.create_transcription = lambda self, *args, **kwargs: 'job'
SonioxClient.poll_status = lambda self, job: None
SonioxClient.get_transcript = lambda self, job: [{'text':'Hello', 'language':'en'}]
assert cli.main([sys.argv[1], '--dry-run', '--json']) == 0
assert cli.main([sys.argv[1], '--json']) == 0
assert not any(name.startswith('PyQt6') for name in sys.modules)
'''
    completed = subprocess.run([sys.executable, '-c', script, str(audio)],
                               capture_output=True, text=True, timeout=10, env={**os.environ})
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert audio.with_suffix('.md').read_text()


def test_poll_timeout_uses_elapsed_monotonic_time(monkeypatch):
    from types import SimpleNamespace

    moments = iter([0, 4])
    monkeypatch.setattr(client.time, 'monotonic', lambda: next(moments))
    monkeypatch.setattr(client.time, 'time', lambda: pytest.fail('wall clock used for duration'))
    monkeypatch.setattr(client.requests, 'get', lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'status': 'processing'},
    ))
    with pytest.raises(TimeoutError, match='timed out'):
        client.SonioxClient('test-key').poll_status('job', timeout=3)
