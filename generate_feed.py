"""
Carbone Changelog → RSS Feed Generator

Fetches https://carbone.io/changelog.html, parses versioned changelog entries,
and generates multiple RSS 2.0 feeds:
  - feed-all.xml      (all versions)
  - feed-stable.xml   (no beta/alpha/rc)
  - feed-v{N}.xml     (per major version)
"""

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent, parse as parse_xml

import requests
from bs4 import BeautifulSoup, Tag

CHANGELOG_URL = "https://carbone.io/changelog.html"
PAGES_BASE_URL = os.environ.get("PAGES_BASE_URL", "")
REPO_NAME = PAGES_BASE_URL.rstrip("/").rsplit("/", 1)[-1] if PAGES_BASE_URL else "PageToRss"
USER_AGENT = f"{REPO_NAME}/1.0 (+{PAGES_BASE_URL or 'https://github.com/' + REPO_NAME})"
OUTPUT_DIR = Path(__file__).parent / "docs"
MAX_ITEMS = 30
MAX_DESC_LEN = 2000
TTL_MINUTES = 1440  # 24 hours

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.\d+(.*)$")
DATE_RE = re.compile(r"Release:?\s*(.+)", re.IGNORECASE)
ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b")
PRERELEASE_RE = re.compile(r"-(beta|alpha|rc)")

# Versions known to lack a parseable release date (old/cancelled entries).
# These are silently skipped — only NEW failures produce an error item.
KNOWN_DATELESS_VERSIONS = {
    "v4.23.3", "v4.21.0", "v4.0.0-alpha.1", "v4.0.0-alpha.0",
    "v3.0.4", "v3.0.3", "v3.0.2", "v3.0.1", "v3.0.0",
    "v0.12.4", "v0.12.3", "v0.12.2", "v0.12.1", "v0.12.0",
    "v0.11.3", "v0.11.2", "v0.11.1", "v0.10.1", "v0.10.0",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ChangelogEntry:
    version: str
    major: int
    is_stable: bool
    date: datetime
    description_html: str
    link: str
    guid: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def fetch_changelog(url: str = CHANGELOG_URL) -> str:
    """Fetch the changelog HTML. Raises on failure."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_version(text: str) -> tuple[str, int, bool] | None:
    """Parse a version heading text. Returns (version, major, is_stable) or None."""
    text = text.strip()
    m = VERSION_RE.match(text)
    if not m:
        return None
    major = int(m.group(1))
    is_stable = not PRERELEASE_RE.search(m.group(3))
    return text, major, is_stable


def parse_release_date(text: str) -> datetime | None:
    """Parse a release date string like 'Release: February 23rd 2026'.
    Returns a timezone-aware UTC datetime, or None on failure."""
    m = DATE_RE.search(text)
    if not m:
        return None
    date_str = m.group(1).strip()
    # Remove ordinal suffixes: 23rd → 23, 1st → 1, etc.
    date_str = ORDINAL_RE.sub(r"\1", date_str)
    # Remove trailing punctuation
    date_str = date_str.rstrip(" .,;")
    for fmt in ("%B %d %Y", "%B %d, %Y", "%B %Y"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def truncate_description(html: str, max_len: int = MAX_DESC_LEN, link: str = "") -> str:
    """Truncate HTML description to max_len characters, appending a read-more link."""
    if len(html) <= max_len:
        return html
    truncated = html[: max_len - 1]
    # Try to break at a word boundary
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    suffix = f"… (read more: {link})" if link else "…"
    return truncated + suffix


def _collect_description_text(heading: Tag) -> str:
    """Collect a plain-text description from siblings between this heading and the next h2.
    Skips download-link blocks and the release-date line."""
    lines: list[str] = []
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and sibling.name == "h2":
            break
        if isinstance(sibling, Tag):
            # Skip collapsed download-links blocks
            if sibling.name == "details":
                continue
            # Process list items as bullet points
            if sibling.name == "ul":
                for li in sibling.find_all("li", recursive=False):
                    text = li.get_text(" ", strip=True)
                    # Skip the release date line — it's already in pubDate
                    if DATE_RE.match(text):
                        continue
                    if text:
                        lines.append(f"• {text}")
            else:
                text = sibling.get_text(" ", strip=True)
                if text:
                    lines.append(text)
    return "<br/>".join(lines)


def _find_release_date_in_siblings(heading: Tag) -> datetime | None:
    """Find the release date in the siblings following a heading."""
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and sibling.name == "h2":
            break
        if isinstance(sibling, Tag):
            # Search inside individual child elements (e.g. <li> in a <ul>)
            for el in sibling.find_all(string=True, recursive=True):
                text = el.strip()
                if text:
                    dt = parse_release_date(text)
                    if dt:
                        return dt
    return None


def parse_changelog(html: str) -> tuple[list[ChangelogEntry], list[str]]:
    """Parse the changelog HTML into a list of entries.
    Returns (entries, warnings) where warnings lists versions that failed to parse."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[ChangelogEntry] = []
    warnings: list[str] = []

    for h2 in soup.find_all("h2"):
        heading_text = h2.get_text(strip=True)
        parsed = parse_version(heading_text)
        if not parsed:
            continue

        version, major, is_stable = parsed
        date = _find_release_date_in_siblings(h2)
        if date is None:
            warnings.append(version)
            log.warning("Could not parse date for %s, skipping", version)
            continue

        anchor = h2.get("id", "")
        link = f"{CHANGELOG_URL}#{anchor}" if anchor else CHANGELOG_URL
        desc = _collect_description_text(h2)
        desc = truncate_description(desc, MAX_DESC_LEN, link)

        entries.append(ChangelogEntry(
            version=version,
            major=major,
            is_stable=is_stable,
            date=date,
            description_html=desc,
            link=link,
            guid=f"carbone-{version}",
        ))

    # Sort by date descending
    entries.sort(key=lambda e: e.date, reverse=True)
    return entries, warnings


# ---------------------------------------------------------------------------
# RSS generation
# ---------------------------------------------------------------------------

def _format_rfc822(dt: datetime) -> str:
    """Format a datetime as RFC 822 for RSS pubDate."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _make_error_item(title: str, description: str) -> ChangelogEntry:
    """Create a synthetic error entry that will appear at the top of feeds."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    return ChangelogEntry(
        version="",
        major=-1,
        is_stable=True,
        date=now,
        description_html=description,
        link=CHANGELOG_URL,
        guid=f"error-{date_str}",
    )


def build_feed_xml(
    title: str,
    description: str,
    items: list[ChangelogEntry],
    max_items: int = MAX_ITEMS,
) -> Element:
    """Build an RSS 2.0 XML Element tree."""
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "link").text = CHANGELOG_URL
    SubElement(channel, "description").text = description
    SubElement(channel, "lastBuildDate").text = _format_rfc822(datetime.now(timezone.utc))
    SubElement(channel, "ttl").text = str(TTL_MINUTES)

    for entry in items[:max_items]:
        item = SubElement(channel, "item")
        item_title = entry.version if entry.version else entry.guid
        SubElement(item, "title").text = item_title
        SubElement(item, "link").text = entry.link
        SubElement(item, "pubDate").text = _format_rfc822(entry.date)
        SubElement(item, "guid", isPermaLink="false").text = entry.guid
        desc = SubElement(item, "description")
        desc.text = entry.description_html

    return rss


def _load_previous_items(feed_path: Path) -> list[Element]:
    """Load <item> elements from a previously generated feed file."""
    if not feed_path.exists():
        return []
    try:
        tree = parse_xml(str(feed_path))
        return tree.getroot().findall(".//item")
    except Exception:
        return []


def _write_feed(rss: Element, path: Path) -> None:
    """Write an RSS Element to a file with XML declaration."""
    indent(rss, space="  ")
    tree = ElementTree(rss)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# Feed definitions
# ---------------------------------------------------------------------------

def _feed_configs(major_versions: set[int]) -> list[dict]:
    """Return the list of feed configurations to generate."""
    configs = [
        {
            "filename": "feed-all.xml",
            "title": "Carbone Changelog — All",
            "description": "All Carbone releases (stable, beta, alpha)",
            "filter": lambda e: True,
        },
        {
            "filename": "feed-stable.xml",
            "title": "Carbone Changelog — Stable",
            "description": "Stable Carbone releases only",
            "filter": lambda e: e.is_stable,
        },
    ]
    for v in sorted(major_versions):
        configs.append({
            "filename": f"feed-v{v}.xml",
            "title": f"Carbone Changelog — v{v}",
            "description": f"Carbone v{v}.x releases",
            "filter": lambda e, mv=v: e.major == mv,
        })
        configs.append({
            "filename": f"feed-v{v}-stable.xml",
            "title": f"Carbone Changelog — v{v} Stable",
            "description": f"Stable Carbone v{v}.x releases only",
            "filter": lambda e, mv=v: e.major == mv and e.is_stable,
        })
    return configs


def _generate_index_html(configs: list[dict], output_dir: Path, base_url: str) -> Path:
    """Generate an index.html listing all available feeds."""
    rows = []
    for cfg in configs:
        rows.append(
            f'      <tr><td><a href="{cfg["filename"]}">{cfg["filename"]}</a></td>'
            f'<td>{cfg["description"]}</td></tr>'
        )
    table_rows = "\n".join(rows)
    slack_example = f"{base_url}/feed-stable.xml"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carbone Changelog RSS Feeds</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
    h1 {{ font-size: 1.4rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ text-align: left; padding: 0.4rem 0.8rem; border: 1px solid #ddd; }}
    th {{ background: #f5f5f5; }}
    a {{ color: #0366d6; }}
    code {{ background: #f0f0f0; padding: 0.15rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
    .note {{ color: #666; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Carbone Changelog \u2014 RSS Feeds</h1>
  <p>Auto-generated RSS feeds from <a href="https://carbone.io/changelog.html">carbone.io/changelog.html</a>. Updated daily at 09:00 UTC.</p>

  <table>
    <thead><tr><th>Feed</th><th>Contents</th></tr></thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  <h2>Slack</h2>
  <p>Subscribe in any Slack channel:</p>
  <pre><code>/feed subscribe {slack_example}</code></pre>

  <p class="note">Feeds contain up to {MAX_ITEMS} items each, sorted by release date. Descriptions are truncated to {MAX_DESC_LEN} characters.</p>
</body>
</html>
"""
    path = output_dir / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    log.info("Wrote index.html (%d feeds listed)", len(configs))
    return path


def generate_feeds(
    entries: list[ChangelogEntry],
    error_item: ChangelogEntry | None = None,
    output_dir: Path = OUTPUT_DIR,
    base_url: str = "",
) -> list[Path]:
    """Generate all RSS feed files and an index.html. Returns list of written paths."""
    major_versions = {e.major for e in entries}
    configs = _feed_configs(major_versions)
    written: list[Path] = []

    for cfg in configs:
        filtered = [e for e in entries if cfg["filter"](e)]
        if error_item:
            filtered.insert(0, error_item)

        rss = build_feed_xml(
            title=cfg["title"],
            description=cfg["description"],
            items=filtered,
        )
        path = output_dir / cfg["filename"]
        _write_feed(rss, path)
        written.append(path)
        log.info("Wrote %s (%d items)", path.name, min(len(filtered), MAX_ITEMS))

    index_path = _generate_index_html(configs, output_dir, base_url)
    written.append(index_path)
    return written


# ---------------------------------------------------------------------------
# Error recovery: preserve previous feed data on total failure
# ---------------------------------------------------------------------------

def generate_error_only_feeds(
    error_item: ChangelogEntry,
    output_dir: Path = OUTPUT_DIR,
) -> list[Path]:
    """On total failure, re-write each existing feed with the error item prepended.
    If no previous feeds exist, generate minimal feeds with only the error item."""
    written: list[Path] = []
    existing_feeds = list(output_dir.glob("feed-*.xml"))

    if existing_feeds:
        for feed_path in existing_feeds:
            try:
                tree = parse_xml(str(feed_path))
                root = tree.getroot()
                channel = root.find("channel")
                if channel is None:
                    continue

                # Build error <item> element
                err_el = Element("item")
                SubElement(err_el, "title").text = error_item.guid
                SubElement(err_el, "link").text = error_item.link
                SubElement(err_el, "pubDate").text = _format_rfc822(error_item.date)
                SubElement(err_el, "guid", isPermaLink="false").text = error_item.guid
                SubElement(err_el, "description").text = error_item.description_html

                # Insert error item at the beginning (after channel metadata)
                items = channel.findall("item")
                if items:
                    # Insert before the first item
                    idx = list(channel).index(items[0])
                    channel.insert(idx, err_el)
                else:
                    channel.append(err_el)

                # Update lastBuildDate
                lbd = channel.find("lastBuildDate")
                if lbd is not None:
                    lbd.text = _format_rfc822(datetime.now(timezone.utc))

                _write_feed(root, feed_path)
                written.append(feed_path)
                log.info("Updated %s with error item", feed_path.name)
            except Exception as exc:
                log.error("Failed to update %s: %s", feed_path.name, exc)
    else:
        # No previous feeds — generate minimal feeds with error only
        for filename in ["feed-all.xml", "feed-stable.xml"]:
            rss = build_feed_xml(
                title="Carbone Changelog",
                description="Carbone changelog feed",
                items=[error_item],
            )
            path = output_dir / filename
            _write_feed(rss, path)
            written.append(path)
            log.info("Created minimal %s with error item", path.name)

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    error_item = None

    # 1. Fetch
    try:
        html = fetch_changelog()
    except Exception as exc:
        log.error("Failed to fetch changelog: %s", exc)
        error_item = _make_error_item(
            title=f"⚠️ {REPO_NAME}: Fetch error on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            description=f"Failed to fetch {CHANGELOG_URL}: {exc}. "
                        "Feed contains stale data from last successful run.",
        )
        generate_error_only_feeds(error_item)
        return 1

    # 2. Parse
    entries, warnings = parse_changelog(html)

    if not entries:
        log.error("No changelog entries found — page structure may have changed")
        error_item = _make_error_item(
            title=f"⚠️ {REPO_NAME}: Parse error on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            description=f"No changelog entries found at {CHANGELOG_URL}. "
                        "The page structure may have changed. Feed contains stale data.",
        )
        generate_error_only_feeds(error_item)
        return 1

    new_warnings = [w for w in warnings if w not in KNOWN_DATELESS_VERSIONS]
    if new_warnings:
        error_item = _make_error_item(
            title=f"⚠️ {REPO_NAME}: Partial parse error on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            description=f"Could not parse date for: {', '.join(new_warnings)}. "
                        "These versions are missing from the feed.",
        )

    # 3. Generate
    generate_feeds(entries, error_item=error_item, base_url=PAGES_BASE_URL)
    log.info("Done — %d entries parsed, %d warnings", len(entries), len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
