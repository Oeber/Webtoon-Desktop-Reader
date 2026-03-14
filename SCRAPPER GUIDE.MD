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
│       ├── __init__.py    ← intentionally empty, do not edit
│       └── your_site.py
```

### Responsibilities

- `base.py`: defines the scraper contract and shared defaults
- `models.py`: defines normalized data models
- `registry.py`: auto-discovers scraper classes from `scrapers/sites/` using `pkgutil.iter_modules` — dropping a file in the folder is enough, no registry edit is needed
- `sites/<site_name>.py`: contains the site-specific scraper implementation

## How the Downloader Uses a Scraper

```text
paste URL
→ registry.get_scraper(url)
→ scraper.get_series_info(url)
→ scraper.get_chapter_pages(chapter_url)
→ for each page:
    → scraper.download_asset(image_url, dest_path)  ← if implemented
    → otherwise: downloader fetches with requests.Session + get_request_headers()
→ save images into local chapter folder
```

If no scraper supports the URL, the downloader falls back to `gallery-dl`.

## Required Base Interface

Every scraper must inherit from `BaseScraper` and implement these methods:

```python
class BaseScraper(ABC):
    site_name: str = "unknown"
    site_display_name: str = ""
    site_hosts: tuple[str, ...] = ()
    site_base_url: str = ""
    site_required_cookie_names: tuple[str, ...] = ()
    site_session_cookie_names: tuple[str, ...] = ()

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

    @abstractmethod
    def get_request_headers(self, url: str) -> dict:
        pass
```

`BaseScraper` also provides `get_site_session_config()`, `is_chapter_url()`, `series_url_from_chapter_url()`, and `extract_chapter_number()` as concrete helpers. Do not override `get_site_session_config()` — it reads the class-level attributes automatically.

## Site Session Config

For any site requiring authentication or session cookies, declare these class-level attributes. `site_session.py` reads them automatically via `get_site_session_config()`:

```python
class ExampleScraper(BaseScraper):
    site_name = "example_site"
    site_display_name = "Example Site"          # shown in the auth UI
    site_hosts = ("example.org", "www.example.org")
    site_base_url = "https://example.org/"
    site_required_cookie_names = ("cf_clearance",)   # see warning below
    site_session_cookie_names = (
        "cf_clearance",
        "PHPSESSID",
        "session",
    )
```

**Warning — `site_required_cookie_names` controls when the auth UI considers a session valid.** If you list `cf_clearance` here but the site does not actually serve a Cloudflare challenge page, the browser window will open and loop indefinitely because the cookie is never issued. Only include `cf_clearance` in `required_cookie_names` if the site actively challenges visitors with a Cloudflare interstitial. If the site loads directly but still uses Cloudflare cookies in the background, leave `required_cookie_names` empty and put the cookie names only in `session_cookie_names`.

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

Once the file exists, `registry.py` auto-loads it. No registry edit is needed.

### Minimal skeleton (plain requests, no Cloudflare)

```python
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from ..base import BaseScraper, ScraperError
from ..models import SeriesInfo, ChapterInfo, PageInfo


class AsuraScansScraper(BaseScraper):
    site_name = "asura_scans"
    site_display_name = "Asura Scans"
    site_hosts = ("asurascans.example",)
    site_base_url = "https://asurascans.example/"
    BASE = "https://asurascans.example"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "asurascans.example" in (urlparse(url).netloc or "").lower()

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

## Step-by-Step Process

### 0. Reconnaissance

Before writing any code, open the target site in a browser and use DevTools (F12 → Network tab) to understand the site structure:

- What do series page URLs look like? (`/series/<slug>`, `/manhua/<slug>`, `/title/<id>`)
- What do chapter page URLs look like? (`/chapter-12`, `/chapter/12`, `/read/<slug>/12`)
- Does the chapter list load from static HTML or via an AJAX/API call? (Filter Network tab by XHR/Fetch and reload the page)
- Are images lazy-loaded via `data-src` attributes?
- Does the site return a Cloudflare challenge page (`Just a moment...`) to plain HTTP clients?

You can do a quick plain-Python check:

```python
import requests
r = requests.get(
    "https://example.org/series/my-series",
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=20,
)
print(r.status_code)
print(r.text[:500])
```

If you see `Just a moment...` and status 403, the site requires Cloudflare handling. See the **Cloudflare / Site Authorization** section below.

### 1. Confirm the URL patterns

Figure out what URLs the site uses.

Common examples:

```text
/series/<slug>
/series/<slug>/chapter-12
/title/<slug>
/read/<slug>/<chapter>
```

Your `can_handle()` should match the site's domain reliably. Prefer checking `urlparse(url).netloc` over a raw `in url` string check:

```python
@classmethod
def can_handle(cls, url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "example.org" in host
```

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
2. parse stable metadata first (`og:title` before `<h1>` before `<title>`)
3. build a normalized chapter list

### 3. Implement chapter discovery

A series scraper must return a `chapters` list.

Sources for chapter discovery usually come from one of these:

- real chapter links in the HTML
- embedded JSON in a script tag
- a public API call the page makes (inspect the Network tab)
- a known framework endpoint (see below)
- generated chapter URLs based on a visible total count

Use whatever is most stable for that site.

**Important: always filter chapter links by series slug.** Series pages often include "latest updates" sidebars that show chapters from completely different series. If you scrape all `<a href>` tags containing "chapter", you will collect foreign chapters. Filter strictly:

```python
def _is_chapter_url(self, url: str, series_slug: str = "") -> bool:
    parsed = urlparse(url)
    if "example.org" not in (parsed.netloc or "").lower():
        return False
    path = parsed.path.lower()
    if "/chapter-" not in path and "/chapter/" not in path:
        return False
    if series_slug and series_slug.lower() not in path:
        return False
    return True
```

**Madara / WordPress theme sites:** Sites built on the Madara theme (common for manga/manhwa hosts) only render the 2 most recent chapters in static HTML. The full chapter list is loaded via a POST request to `<series_url>/ajax/chapters/`. No nonce is required:

```python
def _fetch_chapters_ajax(self, series_url: str) -> list[ChapterInfo]:
    ajax_url = series_url.rstrip("/") + "/ajax/chapters/"
    r = requests.post(
        ajax_url,
        headers={**self.get_request_headers(ajax_url), "X-Requested-With": "XMLHttpRequest"},
        timeout=20,
    )
    if r.status_code != 200:
        raise ScraperError(f"Chapter list request failed: {ajax_url} ({r.status_code})")
    soup = BeautifulSoup(r.text, "html.parser")
    return self._extract_chapters_from_links(soup, series_url, series_slug)
```

Signs you are on a Madara site: the page HTML contains `wp-manga`, `madara`, or `manga-chapters-holder`. The chapter holder div looks like:

```html
<div id="manga-chapters-holder" data-id="103277">
    <i class="fas fa-spinner fa-spin"></i>
</div>
```

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

The downloader calls `scraper.get_request_headers(url)` once and uses those headers for all asset downloads in that job. This means `get_request_headers()` must return headers that work for both HTML page fetches and image downloads from the site's asset host.

## Cloudflare / Site Authorization

### Standard Cloudflare (requests + session cookies)

If a site serves a Cloudflare challenge interstitial (`Just a moment...`), the user must authorize via the in-app browser. The app captures `cf_clearance` and other cookies, stores them via `core/site_session.py`, and the scraper reuses them.

Declare the session attributes on the class (see **Site Session Config** above), then use this pattern:

```python
from core.site_session import apply_site_cookies, load_site_user_agent

class ExampleScraper(BaseScraper):
    site_name = "example_site"
    site_required_cookie_names = ("cf_clearance",)
    # ... other class attrs ...

    def _prepare_client(self, session=None):
        if session is not None:
            apply_site_cookies(session, self.site_name)
            return session, False
        session = requests.Session()
        apply_site_cookies(session, self.site_name)
        return session, True

    def _request_headers(self, url: str) -> dict[str, str]:
        headers = dict(self.get_request_headers(url))
        # Always use the UA that was active when cf_clearance was issued.
        # Cloudflare binds the token to this exact UA string.
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

### Strict Cloudflare (TLS fingerprint validation)

Some Cloudflare zones go further and validate the TLS client hello in addition to `cf_clearance`. Plain `requests` has a different TLS fingerprint from a real browser, so it is rejected with 403 even when the cookie is valid and the UA matches.

To detect this: if `requests` returns 403 with a valid `cf_clearance` cookie and correct UA, but `curl_cffi` with `impersonate="chrome120"` returns 200, the site uses TLS fingerprint validation.

For these sites, use `curl_cffi` instead of `requests`:

```python
from curl_cffi import requests as cffi_requests
from core.site_session import load_site_cookies, load_site_user_agent

class ExampleScraper(BaseScraper):
    site_name = "example_site"
    site_required_cookie_names = ("cf_clearance",)
    IMPERSONATE = "chrome120"   # baseline; use the lowest version that works

    def _site_cookies(self) -> dict[str, str]:
        # Inject by name/value only — no domain argument — so curl_cffi
        # sends all cookies regardless of domain string format in storage.
        return {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}

    def _request_headers(self, url: str) -> dict[str, str]:
        headers = dict(self.get_request_headers(url))
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers

    def _is_cloudflare_block(self, response) -> bool:
        if response.status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _get(self, url: str, session=None) -> cffi_requests.Response:
        # session parameter accepted for interface compatibility but not used —
        # curl_cffi manages its own connection pool.
        try:
            r = cffi_requests.get(
                url,
                headers=self._request_headers(url),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=20,
            )
        except Exception as e:
            raise ScraperError(f"Request failed for {url}: {e}") from e
        if self._is_cloudflare_block(r):
            raise ScraperError("ExampleSite blocked the request with Cloudflare.")
        if r.status_code != 200:
            raise ScraperError(f"Failed to load page: {url} ({r.status_code})")
        return r
```

**When to use each approach:**

| Situation | Use |
|---|---|
| No Cloudflare | plain `requests` |
| Cloudflare challenge page, `requests` works with `cf_clearance` | `requests` + `apply_site_cookies` |
| Cloudflare, `requests` returns 403 even with valid `cf_clearance` | `curl_cffi` with `impersonate` |

## Asset Downloads and `download_asset()`

The downloader uses `scraper.get_request_headers()` and a plain `requests.Session` to download images. For most sites this is sufficient.

However, if the site's image host also requires TLS fingerprint validation (common when images are served from a subdomain like `s3.example.org` under the same Cloudflare zone), plain `requests` will fail with 403 on every image even though the scraper itself works.

In this case, implement `download_asset()` on the scraper. The downloader checks for this method before falling back to its own session:

```python
def download_asset(self, url: str, dest_path: str) -> bool:
    """
    Override to handle asset downloads requiring special HTTP clients.
    Return True on success. Raise ScraperError on failure.
    Return False to let the downloader use its default requests.Session.
    """
    try:
        r = cffi_requests.get(
            url,
            headers=self._request_headers(url),
            cookies=self._site_cookies(),
            impersonate=self.IMPERSONATE,
            timeout=30,
        )
    except Exception as e:
        raise ScraperError(f"Asset download failed: {url}: {e}") from e

    if r.status_code != 200:
        raise ScraperError(f"Asset download failed: {url} ({r.status_code})")

    with open(dest_path, "wb") as f:
        f.write(r.content)
    return True
```

**Rule:** if your scraper uses `curl_cffi` for page fetches, it almost certainly needs `download_asset()` too. The image host is usually under the same Cloudflare zone.

## Error Handling Rules

Use `ScraperError` when the scraper cannot do its job.

```python
from ..base import BaseScraper, ScraperError
```

Examples:

```python
raise ScraperError(f"Failed to load page: {url}")
raise ScraperError(f"No chapter images found: {chapter_url}")
raise ScraperError("ExampleSite blocked the request with Cloudflare.")
```

Good scraper behaviour:

- fail clearly
- do not silently return broken data
- do not leave the downloader to guess what went wrong

The downloader recognizes Cloudflare-style `ScraperError` messages and triggers the auth UI automatically. Keep the message consistent: include the word "Cloudflare" or "blocked".

## Testing a Scraper

Run tests as plain Python scripts. All paths below assume you are in the `reader/` project root with the venv active.

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

### Asset download test

```python
import tempfile, os

with tempfile.TemporaryDirectory() as tmp:
    dest = os.path.join(tmp, "test.jpg")
    result = scraper.download_asset(pages[0].image_url, dest)
    print("download_asset returned:", result)
    print("file size:", os.path.getsize(dest) if os.path.exists(dest) else "MISSING")
```

A scraper is in good shape when:

- title is correct
- total chapter count is reasonable and matches what the site shows
- chapter URLs all belong to the correct series (not other series from sidebars)
- page URLs are real reader images
- logos and non-reader assets are excluded
- asset download test returns a non-zero file size

## Downloader Integration Expectations

The downloader expects:

- `get_series_info()` to return stable metadata
- `get_chapter_pages()` to return download-ready image URLs
- `get_request_headers()` to return headers suitable for both HTML page fetches and image downloads
- `download_asset(url, dest_path)` implemented and returning `True` for sites where the image host rejects plain `requests`

The scraper should not directly save files except inside `download_asset()`. Everything else is the downloader's job.

## Packaging Note

If you build the app with PyInstaller, make sure `scrapers/sites/` is included:

```python
# in main.spec
collect_submodules("scrapers.sites")
```

Without this, auto-discovery will find no scrapers in a packaged build.

## Recommended Development Order

When adding a new site, implement in this order:

1. reconnaissance: URL patterns, Cloudflare check, chapter list source
2. `can_handle()`
3. class-level session attributes (`site_name`, `site_hosts`, `site_required_cookie_names`, etc.)
4. `get_request_headers()` and HTTP helpers (`_get`, `_request_headers`)
5. `get_series_info()` title/slug/metadata
6. chapter list discovery
7. `get_chapter_pages()`
8. URL normalization and filtering
9. `download_asset()` if image host requires TLS impersonation
10. trim known junk pages if needed

Do not start with downloader integration before the scraper passes direct tests.

## Common Failure Modes

### Entire page text becomes the title

Cause: bad fallback logic

Fix: prefer stable metadata such as `og:title`, then try visible `<h1>`

### Chapter list only shows the 2 most recent chapters

Cause: Madara/WordPress theme — chapter list is loaded dynamically via AJAX

Fix: POST to `<series_url>/ajax/chapters/` to get the full list. No nonce required. See step 3 above.

### Chapter list contains chapters from other series

Cause: scraping all chapter links on the page without filtering by series slug

Fix: filter `_is_chapter_url()` by the current series slug

### Image list contains only the site logo

Cause: scraped DOM `<img>` tags only

Fix: inspect raw HTML and script blobs too; decode image wrapper URLs

### URLs end with junk like `%5C`

Cause: escaped backslashes from scripts

Fix: strip trailing escaped characters during normalization

### Downloader creates an empty chapter folder

Cause: folder created before all page downloads fail

Fix: count successful page downloads; delete folder if success count is zero

### Auth browser loops and never closes

Cause: `cf_clearance` listed in `site_required_cookie_names` but the site does not serve a Cloudflare challenge — the cookie is never issued so the condition is never satisfied

Fix: remove `cf_clearance` from `required_cookie_names`. Only include it if the site actively shows a Cloudflare interstitial to unauthenticated visitors.

### Scraper works but all image downloads are 403

Cause: image host (often a CDN subdomain) is under the same strict Cloudflare zone as the main domain, and the downloader uses plain `requests` for asset fetches

Fix: implement `download_asset()` using `curl_cffi` with the same `IMPERSONATE` target as the page scraper

### Scraper not picked up after adding the file

Cause: auto-discovery failure — usually a syntax error in the new file, or the file is in the wrong directory

Fix: check for import errors with `python -c "from scrapers.sites.your_site import YourScraper"`, and confirm the file is in `scrapers/sites/` not `scrapers/`