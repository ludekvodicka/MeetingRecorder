"""Crash-safe checkpoints and a process-shared lock for batch transcription."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def atomic_write(path, text, *, replace=True):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.transcript-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Atomic publication without overwriting a transcript produced elsewhere.
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class TranscriptionJob:
    def __init__(self, audio_path):
        self.audio = Path(audio_path).resolve()
        self.path = self.audio.with_suffix('.transcription.json')
        self.lock_path = self.audio.with_suffix('.transcription.lock')

    @contextmanager
    def locked(self):
        # Never unlink this inode: unlinking an advisory lock allows a third process
        # to lock a replacement file while another process still owns the original.
        if self.audio.with_suffix('.md.transcribing').exists():
            raise RuntimeError(
                'A legacy CLI lock exists. Check that the old job has stopped before removing it.')
        with self.lock_path.open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise RuntimeError(
                        'This recording is being transcribed in another process') from None
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError(
                        'This recording is being transcribed in another process') from None
            try:
                yield self
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def load(self, options):
        stat = self.audio.stat()
        identity = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('audio') != identity or data.get('options') != options:
                raise RuntimeError(
                    'A saved transcription job has different audio or settings. '
                    'Restore the settings or review the .transcription.json before retrying.')
            if data.get('stage', '').startswith('submitting_'):
                raise RuntimeError(
                    'The previous request may have reached Soniox before it was saved. '
                    'Check Soniox before retrying to avoid a duplicate paid job. '
                    'The .transcription.json file records the uncertain stage.')
            return data
        return {'version': 1, 'audio': identity, 'options': options, 'stage': 'new'}

    def save(self, data, **changes):
        data.update(changes)
        atomic_write(self.path, json.dumps(data, indent=2) + '\n')
