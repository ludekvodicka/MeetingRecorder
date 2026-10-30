"""Durable live-speaker sidecars for immediate use after a meeting."""

import json
from pathlib import Path

SPEAKER_MARKDOWN_SUFFIX = ".speakers.md"
SPEAKER_JSON_SUFFIX = ".speakers.json"


def speaker_sidecar_paths(recording_path):
    recording = Path(recording_path)
    stem = recording.stem
    return (
        recording.with_name(stem + SPEAKER_MARKDOWN_SUFFIX),
        recording.with_name(stem + SPEAKER_JSON_SUFFIX),
    )


def resolve_speaker(speaker_id, names):
    name = (names or {}).get(speaker_id, "").strip()
    return name or speaker_id


def render_speaker_markdown(lines, source_name, speaker_names=None):
    names = speaker_names or {}
    header = [
        "# Live speaker transcript\n",
        f"**Source:** {source_name}",
        "\nCreated from the separate microphone and system-audio streams while the "
        "meeting was recorded. Remote labels are best-effort live diarization.\n",
    ]
    mapped = [(speaker_id, name.strip()) for speaker_id, name in sorted(names.items())
              if name.strip()]
    if mapped:
        header.append("## Speaker names\n")
        header.extend(
            f"- **{_markdown_inline(name)}** — `{speaker_id}`"
            for speaker_id, name in mapped
        )
        header.append("")

    body = ["## Transcript\n"]
    if not lines:
        body.append("Nothing was recognised.")
    for timestamp, speaker_id, text in lines:
        speaker = resolve_speaker(speaker_id, names)
        identity = f" (`{speaker_id}`)" if speaker != speaker_id else ""
        body.append(
            f"- **[{timestamp}] {_markdown_inline(speaker)}**{identity}: "
            f"{_markdown_text(text)}")
    return "\n".join(header + body) + "\n"


def render_speaker_json(lines, source_name, speaker_names=None):
    names = {key: value.strip() for key, value in (speaker_names or {}).items()
             if value.strip()}
    document = {
        "schema_version": 1,
        "source": source_name,
        "speaker_names": names,
        "lines": [
            {
                "time": timestamp,
                "speaker_id": speaker_id,
                "speaker": resolve_speaker(speaker_id, names),
                "text": text.strip(),
            }
            for timestamp, speaker_id, text in lines
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_speaker_sidecars(recording_path, lines, speaker_names=None):
    """Write Markdown and JSON sidecars, returning both paths or ``None``."""
    if not lines:
        return None
    markdown_path, json_path = speaker_sidecar_paths(recording_path)
    source_name = Path(recording_path).name
    markdown_path.write_text(
        render_speaker_markdown(lines, source_name, speaker_names),
        encoding="utf-8",
        newline="\n",
    )
    json_path.write_text(
        render_speaker_json(lines, source_name, speaker_names),
        encoding="utf-8",
        newline="\n",
    )
    return markdown_path, json_path


def _markdown_inline(text):
    return str(text).replace("\n", " ").replace("*", r"\*")


def _markdown_text(text):
    return " ".join(str(text).splitlines()).strip()
