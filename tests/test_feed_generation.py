"""Integration tests for feed generation using an HTML fixture."""

import xml.etree.ElementTree as ET
from pathlib import Path

from generate_feed import parse_changelog, generate_feeds

FIXTURE = Path(__file__).parent / "fixtures" / "changelog.html"


def _read_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _generate_to_tmp(tmp_path: Path) -> list[Path]:
    html = _read_fixture()
    entries, warnings = parse_changelog(html)
    return generate_feeds(entries, output_dir=tmp_path)


class TestGenerateAllFeeds:
    def test_produces_expected_files(self, tmp_path):
        paths = _generate_to_tmp(tmp_path)
        names = {p.name for p in paths}
        assert "feed-all.xml" in names
        assert "feed-stable.xml" in names
        assert "feed-v3.xml" in names
        assert "feed-v4.xml" in names
        assert "feed-v5.xml" in names
        assert "index.html" in names

    def test_files_are_valid_xml(self, tmp_path):
        paths = _generate_to_tmp(tmp_path)
        for p in paths:
            if p.suffix != ".xml":
                continue
            tree = ET.parse(str(p))
            root = tree.getroot()
            assert root.tag == "rss"
            assert root.attrib["version"] == "2.0"


class TestFeedAllContents:
    def test_contains_all_versions(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        # Fixture has 10 versions
        assert len(titles) == 10

    def test_includes_beta_versions(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        beta_titles = [t for t in titles if "beta" in t]
        assert len(beta_titles) == 2  # beta.20 and beta.21


class TestFeedStable:
    def test_excludes_betas(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-stable.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        for t in titles:
            assert "beta" not in t
            assert "alpha" not in t
            assert "rc" not in t

    def test_contains_stable_versions(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-stable.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        assert len(titles) == 8  # 10 total minus 2 betas


class TestFeedPerMajor:
    def test_v5_only_contains_v5(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-v5.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        for t in titles:
            assert t.startswith("v5.")

    def test_v4_only_contains_v4(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-v4.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        for t in titles:
            assert t.startswith("v4.")

    def test_v3_only_contains_v3(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-v3.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        for t in titles:
            assert t.startswith("v3.")

    def test_v5_count(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-v5.xml"))
        titles = [item.findtext("title") for item in tree.findall(".//item")]
        # v5.3.0, v5.2.0, v5.1.5, v5.1.4, v5.0.0-beta.21, v5.0.0-beta.20
        assert len(titles) == 6


class TestFeedsSortedByDate:
    def test_all_feed_sorted_descending(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        dates = [item.findtext("pubDate") for item in tree.findall(".//item")]
        # Parse dates back and check order
        from email.utils import parsedate_to_datetime
        parsed = [parsedate_to_datetime(d) for d in dates]
        assert parsed == sorted(parsed, reverse=True)


class TestDescriptionMaxLength:
    def test_no_description_exceeds_limit(self, tmp_path):
        _generate_to_tmp(tmp_path)
        for feed_file in tmp_path.glob("feed-*.xml"):
            tree = ET.parse(str(feed_file))
            for item in tree.findall(".//item"):
                desc = item.findtext("description") or ""
                # Allow some overhead for the "read more" link
                assert len(desc) <= 2200, (
                    f"{feed_file.name}: description too long ({len(desc)} chars)"
                )


class TestIndexHtml:
    def test_lists_all_feeds(self, tmp_path):
        _generate_to_tmp(tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "feed-all.xml" in html
        assert "feed-stable.xml" in html
        assert "feed-v3.xml" in html
        assert "feed-v4.xml" in html
        assert "feed-v5.xml" in html

    def test_no_hardcoded_missing_feeds(self, tmp_path):
        """Index should not reference feeds that don't exist."""
        paths = _generate_to_tmp(tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        xml_files = {p.name for p in paths if p.suffix == ".xml"}
        # Every feed-*.xml link in the HTML should correspond to a generated file
        import re
        linked = set(re.findall(r'href="(feed-[^"]+\.xml)"', html))
        assert linked == xml_files


class TestFeedMetadata:
    def test_has_ttl(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        ttl = tree.findtext(".//ttl")
        assert ttl == "1440"

    def test_has_last_build_date(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        lbd = tree.findtext(".//lastBuildDate")
        assert lbd is not None

    def test_items_have_guid(self, tmp_path):
        _generate_to_tmp(tmp_path)
        tree = ET.parse(str(tmp_path / "feed-all.xml"))
        for item in tree.findall(".//item"):
            guid = item.find("guid")
            assert guid is not None
            assert guid.attrib.get("isPermaLink") == "false"
