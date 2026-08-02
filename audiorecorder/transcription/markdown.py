"""Turning Soniox tokens into a transcript document.

Pure functions with no network and no file system, which is what makes the transcript
format testable at all. The Soniox worker does the talking to the API and hands the tokens
here.
"""

from collections import Counter

# Only for the headings. Soniox identifies far more languages than this, and an unknown
# code is written out as the code itself rather than being guessed at.
_LANGUAGE_NAMES = {
    "cs": "Czech", "sk": "Slovak", "en": "English", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pl": "Polish", "pt": "Portuguese", "nl": "Dutch",
    "ru": "Russian", "uk": "Ukrainian",
}


def language_name(code):
    return _LANGUAGE_NAMES.get(code, code)


def format_timestamp(ms):
    seconds = ms / 1000
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def group_tokens_by_speaker(tokens, translation_status=None):
    """Consecutive tokens of one speaker joined into a segment.

    ``translation_status`` keeps only the tokens carrying it, which is how the translated
    half of a bilingual job is separated from the original half.
    """
    segments = []
    current = None
    for tok in tokens:
        if translation_status and tok.get("translation_status") != translation_status:
            continue
        speaker = tok.get("speaker", 0)
        text = tok.get("text", "")
        start = tok.get("start_ms", 0)
        end = tok.get("end_ms", 0)
        if current is None or current["speaker"] != speaker:
            if current:
                segments.append(current)
            current = {"speaker": speaker, "start": start, "end": end, "text": text}
        else:
            current["end"] = end
            current["text"] += text
    if current:
        segments.append(current)
    return segments


def detect_dominant_language(tokens, fallback="en"):
    """The language most of the spoken text is in, by character count."""
    counts = Counter()
    for tok in tokens:
        language = tok.get("language", "")
        text = tok.get("text", "")
        if language and text.strip():
            counts[language] += len(text)
    if not counts:
        return fallback
    return counts.most_common(1)[0][0]


def _speaker_count(segments):
    return len({segment["speaker"] for segment in segments})


def _render_segments(segments):
    lines = []
    for segment in segments:
        lines.append(f"**[{format_timestamp(segment['start'])}] Speaker {segment['speaker']}:**")
        lines.append(f"{segment['text'].strip()}\n")
    return "\n".join(lines)


def render_transcript(tokens, source_name, language):
    segments = group_tokens_by_speaker(tokens)
    return (
        "# Transcript\n\n"
        f"**Source:** {source_name}\n"
        f"**Language:** {language_name(language)}\n"
        f"**Speakers:** {_speaker_count(segments)}\n\n"
        "---\n\n"
        f"{_render_segments(segments)}\n"
    )


def render_transcript_with_translation(original_tokens, translated_tokens, source_name,
                                       original_lang, translation_lang):
    """Both halves of a translated job, the translation first because that is what is read."""
    original_segments = group_tokens_by_speaker(original_tokens)
    translated_segments = group_tokens_by_speaker(translated_tokens, "translation")
    original_name = language_name(original_lang)
    translation_name = language_name(translation_lang)
    return (
        "# Transcript\n\n"
        f"**Source:** {source_name}\n"
        f"**Original language:** {original_name}\n"
        f"**Translation:** {translation_name}\n"
        f"**Speakers:** {_speaker_count(original_segments)}\n\n"
        f"---\n\n## {translation_name}\n\n"
        f"{_render_segments(translated_segments)}\n"
        f"---\n\n## {original_name} (original)\n\n"
        f"{_render_segments(original_segments)}\n"
    )
