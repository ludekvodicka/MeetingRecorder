from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import av

# Everything written beside a recording and named after it. One list, because delete,
# rename and move each used to carry their own idea of what belongs to a recording, and
# delete's idea left the summary behind.
SIDECAR_SUFFIXES = (".md", ".summary.md", ".live.md")


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

    def generate_filename(self):
        ts = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        return f"Recording {ts}.m4a"

    def delete(self, recording_path):
        p = Path(recording_path)
        if p.exists():
            p.unlink()
        for sidecar in sidecars(recording_path):
            sidecar.unlink()

    def rename(self, recording_path, new_name):
        p = Path(recording_path)
        new_path = p.parent / f"{new_name}.m4a"
        if new_path.exists():
            raise FileExistsError(f"File already exists: {new_path}")
        companions = sidecars(recording_path)
        p.rename(new_path)
        for sidecar in companions:
            sidecar.rename(new_path.with_name(new_name + _suffix_of(p, sidecar)))
        return str(new_path)
