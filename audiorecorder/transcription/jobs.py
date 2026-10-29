"""Crash-safe checkpoints and a process-shared lock for batch transcription."""

import json
import os
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path

from audiorecorder.atomic_files import publish_new


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
            # Publication without overwriting a transcript produced elsewhere.
            publish_new(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class TranscriptionJob:
    def __init__(self, audio_path):
        self.audio = Path(audio_path).resolve()
        self.path = self.audio.with_suffix('.transcription.json')
        self.lock_path = self.audio.with_suffix('.transcription.lock')

    @contextmanager
    def locked(self):
        # Never unlink this inode while work depends on it: unlinking an advisory lock
        # allows a third process to lock a replacement file while another process still
        # owns the original. discard_lock() runs only once that work is final.
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

    def discard_lock(self):
        """Remove the lock file after the recording was deleted, moved or transcribed.

        A process still holding the old inode then finds no recording or an existing
        transcript, so it has nothing left to do. A lock another process holds open on
        Windows cannot be removed and stays until the next discard.
        """
        with suppress(OSError):
            self.lock_path.unlink(missing_ok=True)

    def load(self, options):
        stat = self.audio.stat()
        identity = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        if self.path.exists():
            data = read_checkpoint(self.path)
            if data['audio'] != identity or data['options'] != options:
                raise RuntimeError(
                    'A saved transcription job has different audio or settings. '
                    'Restore the settings or review the .transcription.json before retrying.')
            if data['stage'].startswith('submitting_'):
                raise RuntimeError(
                    'The previous request may have reached Soniox before it was saved. '
                    'Check Soniox before retrying to avoid a duplicate paid job. '
                    'The .transcription.json file records the uncertain stage.')
            return data
        data = {'version': 1, 'audio': identity, 'options': options, 'stage': 'new'}
        validate_checkpoint(data)
        return data

    def save(self, data, **changes):
        updated = {**data, **changes}
        validate_checkpoint(updated)
        atomic_write(self.path, json.dumps(updated, indent=2) + '\n')
        data.update(changes)


class CheckpointError(RuntimeError):
    """The checkpoint cannot safely identify a resumable provider job."""


def validate_checkpoint(data):
    stages = {
        'new', 'uploaded', 'submitting_original_id', 'polling_original_id',
        'submitting_translation_id', 'polling_translation_id', 'completed',
    }
    invalid = CheckpointError(
        'Saved job state is invalid; review the .transcription.json before retrying.')
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise invalid
    stage = data.get('stage')
    if not isinstance(stage, str) or stage not in stages:
        raise invalid
    audio, options = data.get('audio'), data.get('options')
    if not isinstance(audio, dict) or any(
        type(audio.get(key)) is not int for key in ('size', 'mtime_ns')
    ):
        raise invalid
    if not isinstance(options, dict):
        raise invalid
    if (
        not isinstance(options.get('primary_language'), str) or not options['primary_language']
        or type(options.get('enable_translation')) is not bool
        or not isinstance(options.get('language_hints'), list)
        or any(not isinstance(hint, str) or not hint for hint in options['language_hints'])
    ):
        raise invalid
    for key in ('file_id', 'original_id', 'translation_id'):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise invalid
    if stage != 'new' and not data.get('file_id'):
        raise invalid
    if stage in {'polling_original_id', 'submitting_translation_id',
                 'polling_translation_id', 'completed'} and not data.get('original_id'):
        raise invalid
    if stage == 'polling_translation_id' and not data.get('translation_id'):
        raise invalid
    if ('translation' in stage or 'translation_id' in data) and not options['enable_translation']:
        raise invalid
    if ('original_id' in data and 'file_id' not in data) or (
        'translation_id' in data and 'original_id' not in data
    ):
        raise invalid


def read_checkpoint(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise CheckpointError(
            'Saved job state is unreadable; '
            'review the .transcription.json before retrying.') from error
    validate_checkpoint(data)
    return data


def checkpoint_status(audio_path):
    """Read-only presentation of the same validated checkpoint used by recovery."""
    path = TranscriptionJob(audio_path).path
    if not path.exists():
        return ''
    try:
        data = read_checkpoint(path)
    except CheckpointError:
        return 'Job state unreadable — review before retrying'
    if data['stage'].startswith('submitting_'):
        return 'Request status unknown — review before retrying'
    return 'Saved job — click Resume'
