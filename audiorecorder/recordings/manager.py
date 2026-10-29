import shutil
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import av

from audiorecorder.transcription.jobs import TranscriptionJob, checkpoint_status

# Everything written beside a recording and named after it. One list, because delete,
# rename and move each used to carry their own idea of what belongs to a recording, and
# delete's idea left the summary behind.
SIDECAR_SUFFIXES = (
    ".md", ".summary.md", ".live.md", ".speakers.md", ".speakers.json",
    ".transcription.json",
)


def sidecars(recording_path):
    """The companion files that exist for this recording."""
    recording = Path(recording_path)
    candidates = (recording.with_name(recording.stem + suffix) for suffix in SIDECAR_SUFFIXES)
    return [path for path in candidates if path.exists()]


def _suffix_of(recording, sidecar):
    return sidecar.name[len(recording.stem):]


@dataclass
class Recording:
    path: str
    name: str
    date: datetime
    duration_seconds: float
    is_transcribed: bool
    transcript_path: str
    transcription_status: str = ""


class RecordingManager:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def scan(self):
        recordings = []
        output_path = Path(self.output_dir)
        if not output_path.exists():
            return recordings

        for f in sorted(output_path.glob("*.m4a"), key=lambda p: p.stat().st_mtime, reverse=True):
            name = f.stem
            date = datetime.fromtimestamp(f.stat().st_mtime)
            duration = self._get_duration(str(f))
            transcript = f.with_suffix(".md")
            recordings.append(Recording(
                path=str(f),
                name=name,
                date=date,
                duration_seconds=duration,
                is_transcribed=transcript.exists(),
                transcript_path=str(transcript) if transcript.exists() else "",
                transcription_status="" if transcript.exists() else checkpoint_status(f),
            ))
        return recordings

    def _get_duration(self, path):
        try:
            with av.open(path) as container:
                if container.duration is not None:
                    return float(container.duration) / 1_000_000
                stream = container.streams.audio[0]
                return float(stream.duration * stream.time_base)
        except Exception:
            return 0.0

    def generate_filename(self, when=None):
        ts = (when or datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
        return f"Recording {ts} {uuid.uuid4().hex[:8]}.m4a"

    def orphaned_captures(self):
        """Capture files a killed process left behind, as (system, mic, last_write) tuples.

        A recording writes _rec_mic_<id>.wav and, unless it is microphone-only,
        _rec_system_<id>.wav into the output directory, and the encoder deletes both once
        the M4A exists. Files still there at start-up belong to a session that was logged
        off or crashed mid-recording; their data is readable even without a final header.
        """
        directory = Path(self.output_dir)
        if not directory.exists():
            return []
        captures = []
        for mic in sorted(directory.glob("_rec_mic_*.wav")):
            system = mic.with_name("_rec_system_" + mic.name.removeprefix("_rec_mic_"))
            # Recordings are named by the time they stopped, which here is the last write.
            last_write = datetime.fromtimestamp(mic.stat().st_mtime)
            captures.append((str(system) if system.exists() else None, str(mic), last_write))
        return captures

    @staticmethod
    def set_aside_capture(system_path, mic_path):
        """Rename capture files that could not be encoded, so start-up stops retrying them.

        Returns the new paths; the audio stays for the user to inspect.
        """
        kept = []
        for path in (mic_path, system_path):
            if path is None:
                continue
            source = Path(path)
            target = source.with_name("Unrecovered" + source.name.removeprefix("_rec"))
            try:
                source.rename(target)
                kept.append(str(target))
            except OSError:
                kept.append(str(source))
        return kept

    def delete(self, recording_path):
        recording = Path(recording_path)
        if not recording.parent.exists():
            return
        job = TranscriptionJob(recording)
        with job.locked():
            recording.unlink(missing_ok=True)
            for sidecar in sidecars(recording):
                sidecar.unlink()
        job.discard_lock()

    def rename(self, recording_path, new_name):
        recording = Path(recording_path)
        target = recording.with_name(f"{new_name}.m4a")
        return self._transfer(recording, target)

    def move(self, recording_path, destination):
        recording = Path(recording_path)
        return self._transfer(recording, Path(destination) / recording.name)

    @staticmethod
    def _transfer(recording, target):
        if recording.absolute() == target.absolute():
            return str(recording)
        # A CLI transcription can outlive the GUI's in-memory busy-path set. Lock
        # both names until audio and all recovery state have moved together.
        with ExitStack() as locks:
            locks.enter_context(TranscriptionJob(recording).locked())
            locks.enter_context(TranscriptionJob(target).locked())
            pairs = [(recording, target)] + [
                (sidecar, target.with_name(target.stem + _suffix_of(recording, sidecar)))
                for sidecar in sidecars(recording)
            ]
            for _, destination in pairs:
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(f"File already exists: {destination}")
            moved = []
            try:
                for source, destination in pairs:
                    shutil.move(str(source), str(destination))
                    moved.append((source, destination))
            except Exception as error:
                # Restore completed moves before allowing a retry on either name.
                failures = []
                for source, destination in reversed(moved):
                    try:
                        if source.exists() or source.is_symlink():
                            raise FileExistsError(source)
                        shutil.move(str(destination), str(source))
                    except OSError:
                        failures.append(str(destination))
                if failures:
                    raise RuntimeError(
                        f"Move failed; files still need recovery: {', '.join(failures)}") from error
                raise
        for path in (recording, target):
            TranscriptionJob(path).discard_lock()
        return str(target)
