"""Unit tests for parsing logic in generate_feed.py."""

from datetime import datetime, timezone

from generate_feed import parse_version, parse_release_date, truncate_description


class TestParseVersion:
    def test_stable_version(self):
        result = parse_version("v5.3.0")
        assert result == ("v5.3.0", 5, True)

    def test_beta_version(self):
        result = parse_version("v5.0.0-beta.14")
        assert result == ("v5.0.0-beta.14", 5, False)

    def test_alpha_version(self):
        result = parse_version("v4.0.0-alpha.0")
        assert result == ("v4.0.0-alpha.0", 4, False)

    def test_rc_version(self):
        result = parse_version("v5.0.0-rc.1")
        assert result == ("v5.0.0-rc.1", 5, False)

    def test_v3_stable(self):
        result = parse_version("v3.8.1")
        assert result == ("v3.8.1", 3, True)

    def test_v4_stable(self):
        result = parse_version("v4.26.2")
        assert result == ("v4.26.2", 4, True)

    def test_not_a_version(self):
        assert parse_version("Change Log") is None

    def test_empty_string(self):
        assert parse_version("") is None

    def test_with_whitespace(self):
        result = parse_version("  v5.3.0  ")
        assert result == ("v5.3.0", 5, True)

    def test_ee_suffix(self):
        result = parse_version("v3.5.6 [EE]")
        assert result is not None
        assert result[0] == "v3.5.6 [EE]"
        assert result[1] == 3
        assert result[2] is True


class TestParseReleaseDate:
    def test_standard_format_with_colon(self):
        dt = parse_release_date("Release: February 23rd 2026")
        assert dt == datetime(2026, 2, 23, tzinfo=timezone.utc)

    def test_format_without_colon(self):
        dt = parse_release_date("Release January 14th 2026")
        assert dt == datetime(2026, 1, 14, tzinfo=timezone.utc)

    def test_wrong_ordinal_suffix(self):
        # "31th" instead of "31st"
        dt = parse_release_date("Release: October 31th 2025")
        assert dt == datetime(2025, 10, 31, tzinfo=timezone.utc)

    def test_1st_ordinal(self):
        dt = parse_release_date("Release December 1st 2025")
        assert dt == datetime(2025, 12, 1, tzinfo=timezone.utc)

    def test_2nd_ordinal(self):
        dt = parse_release_date("Release: March 2nd 2025")
        assert dt == datetime(2025, 3, 2, tzinfo=timezone.utc)

    def test_no_release_keyword(self):
        assert parse_release_date("Some random text") is None

    def test_garbage_date(self):
        assert parse_release_date("Release: not a date at all") is None

    def test_empty_string(self):
        assert parse_release_date("") is None

    def test_date_with_mars_typo(self):
        # From actual changelog: "Release Mars 16th 2024" — this will fail
        # because "Mars" is not a valid English month
        assert parse_release_date("Release Mars 16th 2024") is None


class TestTruncateDescription:
    def test_short_text_unchanged(self):
        text = "Short description"
        assert truncate_description(text, 2000) == text

    def test_exactly_max_len(self):
        text = "x" * 2000
        assert truncate_description(text, 2000) == text

    def test_long_text_truncated(self):
        text = "word " * 500  # 2500 chars
        result = truncate_description(text, 2000, "https://example.com")
        assert len(result) <= 2100  # some overhead from the link
        assert result.endswith("… (read more: https://example.com)")

    def test_truncation_at_word_boundary(self):
        text = "hello world " * 200
        result = truncate_description(text, 100, "https://example.com")
        # Should not cut in the middle of "world"
        before_ellipsis = result.split("…")[0]
        assert not before_ellipsis.endswith("wor")

    def test_no_link_provided(self):
        text = "x" * 3000
        result = truncate_description(text, 2000)
        assert result.endswith("…")
        assert "read more" not in result
