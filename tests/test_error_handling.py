"""Tests for error handling: errors reported as RSS items."""

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from generate_feed import (
    ChangelogEntry,
    REPO_NAME,
    _make_error_item,
    generate_error_only_feeds,
    generate_feeds,
    parse_changelog,
    build_feed_xml,
    _write_feed,
)


class TestMakeErrorItem:
    def test_guid_is_date_based(self):
        item = _make_error_item("title", "desc")
        assert item.guid.startswith("error-")
        # YYYY-MM-DD format
        date_part = item.guid.removeprefix("error-")
        assert len(date_part) == 10
        assert date_part[4] == "-" and date_part[7] == "-"

    def test_error_item_fields(self):
        item = _make_error_item("⚠️ Test error", "Something went wrong")
        assert item.version == ""
        assert item.major == -1
        assert item.is_stable is True
        assert item.description_html == "Something went wrong"
        assert item.link == "https://carbone.io/changelog.html"


class TestFetchFailureProducesErrorItem:
    def test_error_item_in_feed_on_fetch_failure(self, tmp_path):
        """When fetch fails and no previous feeds exist, generate minimal feeds with error."""
        error_item = _make_error_item(
            f"⚠️ {REPO_NAME}: Fetch error", "Could not fetch changelog"
        )
        paths = generate_error_only_feeds(error_item, output_dir=tmp_path)
        assert len(paths) >= 1

        # Check that feed-all.xml has the error item
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        items = tree.findall(".//item")
        assert len(items) == 1
        assert items[0].findtext("description") == "Could not fetch changelog"


class TestEmptyPageProducesErrorItem:
    def test_no_versions_found(self):
        html = "<html><body><h1>Change Log</h1><p>Nothing here</p></body></html>"
        entries, warnings = parse_changelog(html)
        assert len(entries) == 0


class TestPartialParseError:
    def test_bad_date_skipped_with_warning(self):
        html = """
        <html><body>
        <h2>v5.3.0</h2>
        <ul><li>Release: February 23rd 2026</li><li>Some change</li></ul>
        <h2>v5.2.0</h2>
        <ul><li>Release: Smarch 32nd 2026</li><li>Another change</li></ul>
        <h2>v5.1.0</h2>
        <ul><li>Release: January 10th 2026</li><li>Third change</li></ul>
        </body></html>
        """
        entries, warnings = parse_changelog(html)
        assert len(entries) == 2  # v5.2.0 skipped
        assert "v5.2.0" in warnings

    def test_error_item_added_on_partial_failure(self, tmp_path):
        html = """
        <html><body>
        <h2>v5.3.0</h2>
        <ul><li>Release: February 23rd 2026</li><li>Some change</li></ul>
        <h2>v5.2.0</h2>
        <ul><li>Release: Smarch 32nd 2026</li><li>Bad date</li></ul>
        </body></html>
        """
        entries, warnings = parse_changelog(html)
        error_item = _make_error_item(
            "⚠️ Partial error", f"Failed for: {', '.join(warnings)}"
        )
        generate_feeds(entries, error_item=error_item, output_dir=tmp_path)

        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        items = tree.findall(".//item")
        # 1 valid entry + 1 error item
        assert len(items) == 2
        titles = [it.findtext("title") for it in items]
        assert any("error-" in t for t in titles)


class TestPreviousFeedsPreservedOnFailure:
    def test_existing_items_kept(self, tmp_path):
        # Create a previous feed with 3 items
        entries = []
        for i, ver in enumerate(["v5.3.0", "v5.2.0", "v5.1.0"]):
            from datetime import datetime, timezone
            entries.append(ChangelogEntry(
                version=ver, major=5, is_stable=True,
                date=datetime(2026, 1, 10 + i, tzinfo=timezone.utc),
                description_html=f"Changes for {ver}",
                link="https://carbone.io/changelog.html",
                guid=f"carbone-{ver}",
            ))
        rss = build_feed_xml("Test", "Test feed", entries)
        feed_path = tmp_path / "feed-all.xml"
        _write_feed(rss, feed_path)

        # Verify the file has 3 items
        tree = ET.parse(str(feed_path))
        assert len(tree.findall(".//item")) == 3

        # Now simulate a fetch failure → error item prepended
        error_item = _make_error_item("⚠️ Fetch failed", "HTTP 503")
        generate_error_only_feeds(error_item, output_dir=tmp_path)

        # Check: 3 original items + 1 error item = 4
        tree = ET.parse(str(feed_path))
        items = tree.findall(".//item")
        assert len(items) == 4

        # Error item should be first
        first_guid = items[0].findtext("guid")
        assert first_guid.startswith("error-")


class TestErrorItemGuid:
    def test_unique_per_day(self):
        item1 = _make_error_item("err1", "desc1")
        item2 = _make_error_item("err2", "desc2")
        # Same day → same GUID
        assert item1.guid == item2.guid

    def test_format(self):
        item = _make_error_item("err", "desc")
        assert item.guid.startswith("error-")
        # Should match error-YYYY-MM-DD
        import re
        assert re.match(r"error-\d{4}-\d{2}-\d{2}", item.guid)
