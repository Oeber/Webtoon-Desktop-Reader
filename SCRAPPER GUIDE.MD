# Creating Custom Scrapers

This file explains how to add a new site scraper to the project.

## Goal

The scraper system exists so the downloader can support sites that are not handled by `gallery-dl`, while keeping `downloader_page.py` generic.

The downloader should never contain site-specific logic. All site-specific behaviour belongs inside a scraper plugin.

## Current Architecture

```text
reader/
├── scrapers/
│   ├── base.py
│   ├── models.py
│   ├── registry.py
│   └── sites/
│       ├── __init__.py
│       
```

### Responsibilities

- `base.py`: defines the scraper contract
- `models.py`: defines normalized data models
- `registry.py`: auto-discovers scraper classes from `scrapers/sites/`
- `sites/<site_name>.py`: contains the site-specific scraper implementation

## How the Downloader Uses a Scraper

```text
paste URL
→ registry.get_scraper(url)
→ scraper.get_series_info(url)
→ scraper.get_chapter_pages(chapter_url)
→ download images into local chapter folder
```

If no scraper supports the URL, the downloader falls back to `gallery-dl`.

## Required Base Interface

Every scraper must inherit from `BaseScraper` and implement these methods:

```python
class BaseScraper(ABC):
    site_name: str = "unknown"

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        pass

    @abstractmethod
    def get_series_info(self, url: str) -> SeriesInfo:
        pass

    @abstractmethod
    def get_chapter_pages(self, chapter_url: str) -> list[PageInfo]:
        pass

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": url,
        }
```

## Data Models

### SeriesInfo

```python
@dataclass
class SeriesInfo:
    site: str
    series_id: str
    title: str
    url: str
    cover_url: str | None = None
    author: str | None = None
    description: str | None = None
    total_chapters: int | None = None
    chapters: list[ChapterInfo] = field(default_factory=list)
```

### ChapterInfo

```python
@dataclass
class ChapterInfo:
    id: str
    number: float | None
    title: str
    url: str
    pages: list[PageInfo] | None = None
```

### PageInfo

```python
@dataclass
class PageInfo:
    index: int
    image_url: str
```

## Creating a New Scraper

Create a new file inside:

```text
scrapers/sites/
```

Example:

```text
scrapers/sites/asura_scans.py
```

### Minimal skeleton

```python
import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperError
from ..models import SeriesInfo, ChapterInfo, PageInfo


class AsuraScansScraper(BaseScraper):
    site_name = "asura_scans"
    BASE = "https://asurascans.example"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "asurascans.example" in url

    def get_request_headers(self, url: str) -> dict:
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": self.BASE + "/",
        }

    def get_series_info(self, url: str) -> SeriesInfo:
        r = requests.get(url, headers=self.get_request_headers(url), timeout=20)
        if r.status_code != 200:
            raise ScraperError(f"Failed to load page: {url}")

        soup = BeautifulSoup(r.text, "html.parser")

        title = "TODO"
        slug = "TODO"
        chapters = []

        return SeriesInfo(
            site=self.site_name,
            series_id=slug,
            title=title,
            url=url,
            chapters=chapters,
            total_chapters=len(chapters),
        )

    def get_chapter_pages(self, chapter_url: str) -> list[PageInfo]:
        r = requests.get(chapter_url, headers=self.get_request_headers(chapter_url), timeout=20)
        if r.status_code != 200:
            raise ScraperError(f"Failed to load chapter page: {chapter_url}")

        soup = BeautifulSoup(r.text, "html.parser")

        image_urls = []

        return [
            PageInfo(index=i, image_url=image_url)
            for i, image_url in enumerate(image_urls, start=1)
        ]
```

Once the file exists, `registry.py` auto-loads it. No registry edit is needed.

## Step-by-Step Process

### 1. Confirm the URL patterns

Figure out what URLs the site uses.

Common examples:

```text
/series/<slug>
/series/<slug>/chapter-12
/title/<slug>
/read/<slug>/<chapter>
```

Your `can_handle()` should be broad enough to detect the site, but not so broad that it matches unrelated domains.

### 2. Implement `get_series_info()`

This should return:

- title
- slug or series id
- cover if available
- author if available
- description if available
- total chapter count if available
- list of chapters

A good pattern is:

1. fetch the series page
2. parse stable metadata first
3. build a normalized chapter list

### 3. Implement chapter discovery

A series scraper must return a `chapters` list.

Sources for chapter discovery usually come from one of these:

- real chapter links in the HTML
- embedded JSON in a script tag
- a public API call the page uses
- generated chapter URLs based on a visible total count

Use whatever is most stable for that site.

### 4. Implement `get_chapter_pages()`

This method should return only the real reader images.

Typical steps:

1. fetch the chapter URL
2. inspect the HTML
3. inspect script tags if needed
4. normalize candidate URLs
5. filter out logos, icons, banners, covers, avatars
6. dedupe results
7. return `PageInfo[]`

If the site wraps images in optimization URLs like `_next/image`, decode those back into the real image URL.

## Filtering Rules

A scraper should aggressively reject non-reader images.

Typical things to reject:

- logo
- icon
- favicon
- avatar
- banner
- cover
- navigation graphics
- site chrome assets

For some sites, the chapter also contains a first-page cover and last-page end card. In that case it is valid to trim them after extraction.

Example:

```python
if len(image_urls) > 2:
    image_urls = image_urls[1:-1]
```

## URL Normalization

A lot of image URLs are hidden inside escaped strings.

Useful cleanup steps:

```python
raw = raw.strip()
raw = raw.replace("\\u002F", "/")
raw = raw.replace("\\/", "/")
raw = raw.replace("&amp;", "&")
raw = raw.rstrip("\\").rstrip(",").rstrip(";").strip("'\"")
```

If the site uses `_next/image`, decode its `url=` query parameter and use the real asset URL instead.

## Site Headers

The downloader is generic. Site-specific headers belong inside the scraper.

Example:

```python
def get_request_headers(self, url: str) -> dict:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": self.BASE + "/",
    }
```

This matters because some sites reject direct asset downloads unless the correct referer is present.

## Cloudflare / Site Authorization

If a download scraper is protected by Cloudflare or another anti-bot page, reuse the shared browser-captured session from `core/site_session.py`.

Typical pattern:

```python
from core.site_session import apply_site_cookies, load_site_user_agent

class ExampleScraper(BaseScraper):
    site_name = "example_site"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://example.org/",
        "Origin": "https://example.org",
    }

    def _prepare_client(self, session=None):
        if session is not None:
            apply_site_cookies(session, self.site_name)
            return session, False
        session = requests.Session()
        apply_site_cookies(session, self.site_name)
        return session, True

    def _request_headers(self, url: str) -> dict[str, str]:
        headers = dict(self.HEADERS)
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _get(self, url: str, session=None) -> requests.Response:
        client, should_close = self._prepare_client(session)
        try:
            response = client.get(url, headers=self._request_headers(url), timeout=20)
        finally:
            if should_close:
                client.close()
        if self._is_cloudflare_block(response):
            raise ScraperError("ExampleSite blocked the request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load page: {url} ({response.status_code})")
        return response
```

Why this matters:

- the downloader reuses one shared session across the job
- the shared UI can open authorization and retry when it sees a clear Cloudflare-style `ScraperError`
- sites like HiperCool and ToonGod already follow this pattern

## Error Handling Rules

Use `ScraperError` when the scraper cannot do its job.

Examples:

```python
raise ScraperError(f"Failed to load page: {url}")
raise ScraperError(f"No chapter images found: {chapter_url}")
```

Good scraper behaviour:

- fail clearly
- do not silently return broken data
- do not leave the downloader to guess what went wrong

## Testing a Scraper

### Series test

```python
from scrapers.registry import get_scraper

url = "https://example.org/series/my-series"
scraper = get_scraper(url)
series = scraper.get_series_info(url)

print(series.title)
print(series.author)
print(series.total_chapters)
print(len(series.chapters))
print(series.chapters[0].url)
print(series.chapters[-1].url)
```

### Chapter test

```python
chapter_url = series.chapters[0].url
pages = scraper.get_chapter_pages(chapter_url)

print("pages:", len(pages))
for p in pages[:5]:
    print(p.index, p.image_url)
```

A scraper is in good shape when:

- title is correct
- total chapter count is reasonable
- chapter URLs are valid
- page URLs are real reader images
- logos and non-reader assets are excluded

## Downloader Integration Expectations

The downloader expects:

- `get_series_info()` to return stable metadata
- `get_chapter_pages()` to return download-ready image URLs
- `get_request_headers()` to return headers suitable for both page fetches and asset downloads

The scraper should not directly save files. That is the downloader’s job.

## Recommended Development Order

When adding a new site, implement in this order:

1. `can_handle()`
2. `get_series_info()` title/slug/metadata
3. chapter list discovery
4. `get_chapter_pages()`
5. URL normalization and filtering
6. site-specific headers
7. trim known junk pages if needed

Do not start with downloader integration before the scraper passes direct tests.

## Common Failure Modes

### Entire page text becomes the title

Cause:
- bad fallback logic

Fix:
- prefer stable metadata such as `og:title`
- then try visible `<h1>`

### Chapter list is empty

Cause:
- site does not render chapters in static HTML

Fix:
- inspect embedded JSON or public API calls
- if necessary, generate chapter URLs from visible metadata

### Image list contains only the site logo

Cause:
- you scraped DOM `<img>` tags only

Fix:
- inspect raw HTML and script blobs too
- decode image wrapper URLs

### URLs end with junk like `%5C`

Cause:
- escaped backslashes from scripts

Fix:
- strip trailing escaped characters during normalization

### Downloader creates an empty chapter folder

Cause:
- folder created before all page downloads fail

Fix:
- count successful page downloads
- delete folder if success count is zero
 
