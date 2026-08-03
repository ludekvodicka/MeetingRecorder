"""The sidecar written from what the subtitles showed.

Costs nothing extra: the text is already on screen when the recording stops. It is not the
transcript of record, which the batch pass writes with speaker labels and timestamps. It is
what was on screen, kept so that something is readable the moment a call ends.
"""

from pathlib import Path

from audiorecorder.transcription.markdown import language_name

LIVE_SUFFIX = ".live.md"


def live_transcript_path(recording_path):
    """The sidecar beside a recording, `.live.md` next to the `.m4a`."""
    recording = Path(recording_path)
    return recording.with_name(recording.stem + LIVE_SUFFIX)


def render_live_transcript(lines, source_name, translation_language=None):
    """Markdown for the lines the subtitle engine collected.

    Each carries the time it was said, the original is quoted, and the translation sits
    under it, so the eye can skip the quotes and read only the language it wants. With no
    translation, only what was said.
    """
    header = [
        "# Live subtitles\n",
        f"**Source:** {source_name}",
    ]
    if translation_language:
        header.append(f"**Translated into:** {language_name(translation_language)}")
    header.append(
        "\nWhat the subtitles showed while the call was running. The transcript beside it "
        "is the fuller record.\n"
    )

    if not lines:
        return "\n".join(header) + "\n---\n\nNothing was recognised.\n"

    # Oldest first here, unlike the window. A file is read from the top down, and a call
    # only makes sense in the order it happened.
    body = []
    for timestamp, original, translation in lines:
        # The time belongs to whichever half opens the line. Speech already in the target
        # language has no quoted original, and its line used to lose its timestamp.
        if original:
            body.append(f"> **[{timestamp}]** {original}\n")
            if translation:
                body.append(f"{translation}\n")
        elif translation:
            body.append(f"**[{timestamp}]** {translation}\n")

    return "\n".join(header) + "\n---\n\n" + "\n".join(body)


def write_live_transcript(recording_path, lines, translation_language=None):
    """Write the sidecar, or nothing at all when there is nothing to say.

    Returns the path written, or None. An empty file beside every recording made with the
    toggle left on would be noise.
    """
    if not lines:
        return None
    target = live_transcript_path(recording_path)
    target.write_text(
        render_live_transcript(lines, Path(recording_path).name, translation_language),
        encoding="utf-8",
        newline="\n",
    )
    return target
