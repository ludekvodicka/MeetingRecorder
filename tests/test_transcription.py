from audiorecorder.transcription.markdown import (
    detect_dominant_language,
    format_timestamp,
    group_tokens_by_speaker,
    language_name,
    render_transcript,
    render_transcript_with_translation,
)


def token(text, speaker=0, start=0, end=0, language="", status=None):
    tok = {"text": text, "speaker": speaker, "start_ms": start, "end_ms": end}
    if language:
        tok["language"] = language
    if status:
        tok["translation_status"] = status
    return tok


class TestFormatTimestamp:
    def test_under_a_minute(self):
        assert format_timestamp(5_400) == "00:00:05"

    def test_minutes_and_seconds(self):
        assert format_timestamp(125_000) == "00:02:05"

    def test_over_an_hour(self):
        assert format_timestamp(3_725_000) == "01:02:05"

    def test_zero(self):
        assert format_timestamp(0) == "00:00:00"


class TestGroupTokensBySpeaker:
    def test_consecutive_tokens_of_one_speaker_join(self):
        segments = group_tokens_by_speaker([
            token("Hello", speaker=1, start=0, end=500),
            token(" there", speaker=1, start=500, end=900),
        ])
        assert len(segments) == 1
        assert segments[0]["text"] == "Hello there"
        assert segments[0]["start"] == 0
        assert segments[0]["end"] == 900

    def test_speaker_change_starts_a_segment(self):
        segments = group_tokens_by_speaker([
            token("Hi", speaker=1),
            token("Bye", speaker=2),
        ])
        assert [s["speaker"] for s in segments] == [1, 2]

    def test_same_speaker_returning_starts_another_segment(self):
        segments = group_tokens_by_speaker([
            token("a", speaker=1), token("b", speaker=2), token("c", speaker=1),
        ])
        assert [s["speaker"] for s in segments] == [1, 2, 1]

    def test_empty_input(self):
        assert group_tokens_by_speaker([]) == []

    def test_translation_status_filters(self):
        tokens = [
            token("original", status="original"),
            token("translated", status="translation"),
        ]
        segments = group_tokens_by_speaker(tokens, "translation")
        assert len(segments) == 1
        assert segments[0]["text"] == "translated"

    def test_missing_speaker_defaults_to_zero(self):
        segments = group_tokens_by_speaker([{"text": "x"}])
        assert segments[0]["speaker"] == 0


class TestDetectDominantLanguage:
    def test_counts_characters_not_tokens(self):
        tokens = [
            token("a", language="en"), token("b", language="en"),
            token("dlouhy cesky text", language="cs"),
        ]
        assert detect_dominant_language(tokens) == "cs"

    def test_empty_falls_back(self):
        assert detect_dominant_language([], fallback="cs") == "cs"

    def test_fallback_defaults_to_english(self):
        assert detect_dominant_language([]) == "en"

    def test_whitespace_only_tokens_do_not_count(self):
        tokens = [token("   ", language="de"), token("hello", language="en")]
        assert detect_dominant_language(tokens) == "en"

    def test_tokens_without_a_language_are_ignored(self):
        assert detect_dominant_language([token("hello")], fallback="sk") == "sk"


class TestLanguageName:
    def test_known_code(self):
        assert language_name("cs") == "Czech"

    def test_unknown_code_is_passed_through_rather_than_guessed(self):
        assert language_name("xx") == "xx"


class TestRenderTranscript:
    def test_header_and_body(self):
        tokens = [token("Hello", speaker=0, start=0, end=1000, language="en")]
        out = render_transcript(tokens, source_name="call.m4a", language="en")
        assert out.startswith("# Transcript\n\n")
        assert "**Source:** call.m4a\n" in out
        assert "**Language:** English\n" in out
        assert "**Speakers:** 1\n" in out
        assert "**[00:00:00] Speaker 0:**\nHello\n" in out

    def test_speaker_count_is_distinct_speakers(self):
        tokens = [token("a", speaker=1), token("b", speaker=2), token("c", speaker=1)]
        assert "**Speakers:** 2\n" in render_transcript(tokens, "x.m4a", "en")

    def test_empty_tokens_still_render_a_document(self):
        out = render_transcript([], source_name="x.m4a", language="en")
        assert "# Transcript" in out
        assert "**Speakers:** 0" in out


class TestRenderTranscriptWithTranslation:
    def test_translation_comes_before_the_original(self):
        original = [token("Hello", language="en", status="original")]
        translated = [token("Ahoj", status="translation")]
        out = render_transcript_with_translation(
            original, translated, source_name="call.m4a",
            original_lang="en", translation_lang="cs",
        )
        assert out.index("## Czech") < out.index("## English (original)")

    def test_header_names_both_languages(self):
        out = render_transcript_with_translation(
            [token("Hello", status="original")], [token("Ahoj", status="translation")],
            source_name="call.m4a", original_lang="en", translation_lang="cs",
        )
        assert "**Original language:** English\n" in out
        assert "**Translation:** Czech\n" in out

    def test_only_translated_tokens_land_in_the_translation_section(self):
        tokens = [token("Hello", status="original"), token("Ahoj", status="translation")]
        out = render_transcript_with_translation(
            tokens, tokens, source_name="c.m4a", original_lang="en", translation_lang="cs",
        )
        czech = out[out.index("## Czech"):out.index("## English (original)")]
        assert "Ahoj" in czech
        assert "Hello" not in czech
