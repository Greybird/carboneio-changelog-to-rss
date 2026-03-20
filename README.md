# Carbone Changelog → RSS

Auto-generated RSS feeds from [carbone.io/changelog.html](https://carbone.io/changelog.html), designed for Slack's `/feed` integration.

## Feeds

Available at **https://greybird.github.io/carboneio-changelog-to-rss/**

| Feed | Contents |
|------|----------|
| `feed-all.xml` | All versions (stable + beta/alpha) |
| `feed-stable.xml` | Stable releases only |
| `feed-v{N}.xml` | Per major version (v0–v5, …) |
| `feed-v{N}-stable.xml` | Stable only, per major version |

## Slack

```
/feed subscribe https://greybird.github.io/carboneio-changelog-to-rss/feed-stable.xml
```

## How it works

A GitHub Actions workflow runs daily at 09:00 UTC, fetches the changelog page, parses version entries with BeautifulSoup, and generates RSS 2.0 feeds published to the `gh-pages` branch via GitHub Pages.

## License

[MIT](LICENSE)
