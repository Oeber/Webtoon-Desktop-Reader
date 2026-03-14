# Creating Discovery Scrapers

This file explains how to add a new discovery provider to the project.

## Goal

The discovery system exists so the Discover page can browse site catalogs without putting site-specific logic inside the GUI.

The discovery page should never contain provider-specific parsing, naming, search, or matching rules. All site-specific behaviour belongs inside a discovery provider.

## Current Architecture

```text
reader/
├── scrapers/
│   ├── discovery_base.py
│   ├── discovery_registry.py
│   ├── discovery_support.py
│   ├── models.py
│   └── discovery_sites/
│       ├── __init__.py    ← intentionally empty, do not edit
│       └── your_site.py
└── gui/
    └── discovery/
        ├── cover_loader.py
        ├── detail_page.py
        └── site_browser_page.py
```

### Responsibilities

- `scrapers/discovery_base.py`: defines the discovery provider contract and shared defaults
- `scrapers/models.py`: defines normalized discovery data models like `CatalogSeries` and `CatalogPage`
- `scrapers/discovery_support.py`: shared helpers for matching discovery entries against the local library (see **Library Matching** section below)
- `scrapers/discovery_registry.py`: auto-discovers providers from `scrapers/discovery_sites/` using `pkgutil.iter_modules` — dropping a file in the folder is enough, no registry edit is needed
- `scrapers/discovery_sites/<site>.py`: contains the site-specific discovery implementation
- `gui/discovery/site_browser_page.py`: renders provider results and calls provider APIs only

## How Discovery Uses a Provider

```text
open Discover
→ discovery_registry.get_all_discovery_providers()
→ provider.get_catalog_page(page=1, search_query="")
→ render CatalogSeries cards
→ provider matching/search helpers used for local filtering and Downloaded Only mode
```

## Required Base Interface

Every discovery provider must inherit from `BaseDiscoveryProvider`.

```python
class BaseDiscoveryProvider(ABC):
    site_name: str = "unknown"
    site_display_name: str = ""
    site_hosts: tuple[str, ...] = ()
    site_base_url: str = ""
    site_required_cookie_names: tuple[str, ...] = ()
    site_session_cookie_names: tuple[str, ...] = ()

    def get_display_name(self) -> str:
        return self.site_name.replace("_", " ").title()

    def entry_key(self, entry: CatalogSeries) -> str:
        return entry.identity_key()

    def matches_search(self, entry: CatalogSeries, query: str) -> bool:
        return entry.matches_query(query)

    def match_entry_to_library(
        self,
        entry: CatalogSeries,
        source_matches: dict[tuple[str, str], dict],
        title_matches: dict[str, dict],
    ) -> dict | None:
        ...

    def downloaded_entries(self, entries_by_site: dict[str, list[CatalogSeries]]) -> list[CatalogSeries]:
        ...

    @abstractmethod
    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        pass
```

## Site Session Config

For any discovery provider that requires authentication or session cookies, declare these class-level attributes. `site_session.py` reads them automatically — do not override `get_site_session_config()`:

```python
class ExampleDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "example_site"
    site_display_name = "Example Site"           # shown in the auth UI and site dropdown
    site_hosts = ("example.org", "www.example.org")
    site_base_url = "https://example.org/"
    site_required_cookie_names = ("cf_clearance",)   # see warning below
    site_session_cookie_names = (
        "cf_clearance",
        "PHPSESSID",
        "session",
    )
```

**Warning — `site_required_cookie_names` controls when the auth UI considers a session valid.** If you list `cf_clearance` here but the site does not actively serve a Cloudflare challenge interstitial, the browser window will open and loop indefinitely because the cookie is never issued. Only include `cf_clearance` in `required_cookie_names` if the site actually challenges visitors with a `Just a moment...` page. If the site loads directly but still uses Cloudflare cookies internally, leave `required_cookie_names` empty and put the cookie names only in `session_cookie_names`.

## Data Models

### CatalogSeries

```python
@dataclass
class CatalogSeries:
    site: str
    series_id: str
    title: str
    url: str
    cover_url: str | None = None
    cover_headers: dict[str, str] = field(default_factory=dict)
    author: str | None = None
    description: str | None = None
    latest_chapter: str | None = None
    total_chapters: int | None = None
```

Important helpers already live on the model:

```python
entry.normalized_title()    # lowercased, punctuation-stripped title for matching
entry.source_key()          # (site, series_id) tuple
entry.identity_key()        # stable string key for deduplication
entry.search_text()         # combined text blob for search matching
entry.matches_query(query)  # default search match against title + author
```

### CatalogPage

```python
@dataclass
class CatalogPage:
    site: str
    page: int
    entries: list[CatalogSeries] = field(default_factory=list)
    has_next_page: bool = False
```

## Library Matching

The Discover page matches remote catalog entries back to series already in the local library. This is used for the "Downloaded Only" filter and for highlighting owned series in the catalog.

Matching is handled by `scrapers/discovery_support.py`. The two dicts passed to `match_entry_to_library()` are built by the discovery UI from the local library:

- `source_matches`: keyed by `(site_name, series_id)` — exact match on provider + ID
- `title_matches`: keyed by `normalized_title` — fuzzy fallback when IDs don't match

You do not need to build these dicts yourself. They are passed in by the discovery page when it calls your provider. The default implementation in `BaseDiscoveryProvider` covers most cases. Only override `match_entry_to_library()` if your site uses non-standard IDs or needs custom matching logic.

To test matching manually:

```python
from scrapers.discovery_registry import get_all_discovery_providers
from scrapers.discovery_support import match_catalog_series_to_library

providers = {p.site_name: p for p in get_all_discovery_providers()}
provider = providers["your_site"]
page = provider.get_catalog_page(page=1)
entry = page.entries[0]

# Build minimal test dicts from your local library
source_matches = {}   # populate from your local library if testing matching
title_matches = {}

match = provider.match_entry_to_library(entry, source_matches, title_matches)
print(match)
```

## Creating a New Discovery Provider

Create a new file inside:

```text
scrapers/discovery_sites/
```

Example:

```text
scrapers/discovery_sites/asura_scans.py
```

Once the file exists, `discovery_registry.py` auto-loads it. No registry edit is needed.

### Minimal skeleton

```python
import requests
from urllib.parse import urlparse

from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries


class AsuraScansDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "asura_scans"
    site_display_name = "Asura Scans"
    site_hosts = ("asurascans.example",)
    site_base_url = "https://asurascans.example/"
    BASE = "https://asurascans.example"
    API_BASE = "https://api.asurascans.example"

    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE + "/",
    }

    def get_display_name(self) -> str:
        return "Asura Scans"

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        params = {
            "page": max(1, int(page)),
            "query": str(search_query or "").strip(),
        }
        response = requests.get(
            f"{self.API_BASE}/series",
            params=params,
            headers=self.HEADERS,
            timeout=20,
        )
        if response.status_code != 200:
            raise ScraperError(f"Failed to load catalog page {page}")

        payload = response.json()
        entries = []
        for raw in payload.get("data", []):
            slug = str(raw.get("slug") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not slug or not title:
                continue
            entries.append(
                CatalogSeries(
                    site=self.site_name,
                    series_id=slug,
                    title=title,
                    url=f"{self.BASE}/series/{slug}",
                    cover_url=raw.get("cover_url"),
                    cover_headers=dict(self.HEADERS),
                    description=raw.get("description"),
                    author=raw.get("author"),
                    total_chapters=raw.get("chapters_count"),
                )
            )

        return CatalogPage(
            site=self.site_name,
            page=page,
            entries=entries,
            has_next_page=bool(payload.get("has_next_page")),
        )
```

## Step-by-Step Process

### 0. Reconnaissance

Before writing any code, open the site's catalog page in a browser with DevTools open (F12 → Network tab):

- Does the catalog load from a public JSON API? (Filter XHR/Fetch, reload)
- Does the site use the same API the website calls internally?
- Is there a stable HTML listing page?
- Is there a search endpoint?
- Does the site require Cloudflare auth?

Quick Cloudflare check:

```python
import requests
r = requests.get("https://example.org/manga", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
print(r.status_code, r.text[:200])
```

If you see `Just a moment...` and status 403, the site requires session handling. See **Cloudflare / Site Authorization** below.

### 1. Confirm what the site exposes

Look for one of these:

- public JSON API
- same API the website calls from its catalog page
- stable HTML listing pages
- searchable endpoint used by the site itself

Use the most stable source available. Prefer APIs over fragile card scraping when possible.

### 2. Implement `get_catalog_page()`

This should return one `CatalogPage` with:

- provider site key
- current page number
- a list of `CatalogSeries`
- `has_next_page`

Each `CatalogSeries` should include as much of this as possible:

- stable `series_id`
- human title
- canonical series URL
- cover URL
- `cover_headers` if the cover host requires a referer or origin header (see below)
- author if available
- description if available
- latest chapter if available
- total chapters if available

Prefer source-side filtering when the site already exposes it. For example, if you only want manhwa, use the site's native manhwa taxonomy route instead of loading the full mixed catalog and filtering titles after the fact.

### 3. Prefer stable identity fields

The Discover page matches local library entries back to remote results.

For that reason, each provider should try to supply:

- a stable `site_name` — use the same value as the download scraper for this site if one exists
- a stable `series_id` — slug or numeric ID, whichever the site uses consistently
- a canonical `url`
- the real source title when possible

Matching order is:

1. provider/site + `series_id` (exact)
2. normalized title fallback

If a site needs a custom rule, override `match_entry_to_library()` in the provider instead of adding UI logic.

### 4. Keep provider-specific display naming in the provider

If a site should be shown as `OmegaScans`, `MangaDex`, or any name that cannot be generated cleanly from `site_name`, override:

```python
def get_display_name(self) -> str:
    return "OmegaScans"
```

Do not push branding rules into the GUI.

### 5. Keep provider-specific search behaviour in the provider

The Discover page can do two kinds of search:

- remote search through `get_catalog_page(..., search_query=...)`
- local filtering for Downloaded Only mode

If local filtering needs provider-specific fields like aliases, tags, or alternate titles, override:

```python
def matches_search(self, entry: CatalogSeries, query: str) -> bool:
    haystack = " ".join([
        entry.title,
        entry.description or "",
        entry.author or "",
    ])
    return normalize_catalog_text(query) in normalize_catalog_text(haystack)
```

Do not hardcode provider search fields in the page.

### 6. Keep Downloaded Only reconstruction provider-safe

Downloaded Only mode rebuilds discovery entries from saved source metadata in the local library.

The shared helper in `scrapers/discovery_support.py` handles the default case. If your provider needs extra metadata or sorting rules, override:

```python
def downloaded_entries(self, entries_by_site: dict[str, list[CatalogSeries]]) -> list[CatalogSeries]:
    entries = list(entries_by_site.get(self.site_name, []))
    entries.sort(key=lambda entry: entry.normalized_title())
    return entries
```

### 7. Reuse saved site sessions when needed

If a discovery site is protected by Cloudflare or similar anti-bot layers, the provider should reuse the browser-captured session from `core/site_session.py` instead of inventing provider-specific GUI behavior.

See the **Cloudflare / Site Authorization** section below for the full pattern.

### 8. Use correct cover headers

If a cover host blocks direct requests unless the right headers are present, attach them to `CatalogSeries.cover_headers`:

```python
CatalogSeries(
    ...,
    cover_url=cover_url,
    cover_headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": self.BASE + "/",
    },
)
```

If the cover host is under the same Cloudflare zone as the main domain and requires session cookies to serve images, include the cookie header too:

```python
from core.site_session import site_cookie_header

CatalogSeries(
    ...,
    cover_url=cover_url,
    cover_headers={
        "User-Agent": load_site_user_agent(self.site_name, "Mozilla/5.0"),
        "Referer": self.BASE + "/",
        "Cookie": site_cookie_header(self.site_name),
    },
)
```

## Cloudflare / Site Authorization

### Standard Cloudflare (requests + session cookies)

If a site serves a Cloudflare challenge interstitial, the user must authorize via the in-app browser. Declare session attributes on the class (see **Site Session Config** above), then use this pattern:

```python
from core.site_session import apply_site_cookies, load_site_user_agent, site_cookie_header

class ExampleDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "example_site"
    site_required_cookie_names = ("cf_clearance",)
    BASE = "https://example.org"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
    }

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.HEADERS)
        # Always use the UA that was active when cf_clearance was issued.
        # Cloudflare binds the token to this exact UA string.
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        cookie_header = site_cookie_header(self.site_name)
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    def _looks_like_cloudflare_block(self, html: str, status_code: int) -> bool:
        if status_code == 403:
            return True
        text = (html or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        session = requests.Session()
        apply_site_cookies(session, self.site_name)
        try:
            response = session.get(
                f"{self.BASE}/series/page/{page}/",
                headers=self._request_headers(),
                timeout=30,
            )
        finally:
            session.close()

        if self._looks_like_cloudflare_block(response.text, response.status_code):
            raise ScraperError("ExampleSite blocked the catalog request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load catalog page {page}: HTTP {response.status_code}")

        # parse the catalog page here
```

The exact error shape matters — the shared discovery UI already knows how to open the authorization window and retry when it sees a `ScraperError` containing "Cloudflare" or "blocked".

### Strict Cloudflare (TLS fingerprint validation)

Some Cloudflare zones validate the TLS client hello in addition to `cf_clearance`. Plain `requests` is rejected with 403 even with valid cookies because its TLS fingerprint differs from a real browser.

To detect this: if `requests` returns 403 with valid cookies and correct UA, but `curl_cffi` with `impersonate="chrome120"` returns 200, the site uses TLS fingerprint validation.

For these providers, use `curl_cffi` instead of `requests`:

```python
from curl_cffi import requests as cffi_requests
from core.site_session import load_site_cookies, load_site_user_agent

class ExampleDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "example_site"
    site_required_cookie_names = ("cf_clearance",)
    IMPERSONATE = "chrome120"

    def _site_cookies(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in load_site_cookies(self.site_name)}

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.HEADERS)
        headers["User-Agent"] = load_site_user_agent(self.site_name, headers["User-Agent"])
        return headers

    def _looks_like_cloudflare_block(self, html: str, status_code: int) -> bool:
        if status_code == 403:
            return True
        return "just a moment" in (html or "").casefold() and "cloudflare" in (html or "").casefold()

    def get_catalog_page(self, page: int = 1, search_query: str = "") -> CatalogPage:
        try:
            response = cffi_requests.get(
                f"{self.BASE}/series/page/{page}/",
                headers=self._request_headers(),
                cookies=self._site_cookies(),
                impersonate=self.IMPERSONATE,
                timeout=30,
            )
        except Exception as e:
            raise ScraperError(f"Catalog request failed for page {page}: {e}") from e

        if self._looks_like_cloudflare_block(response.text, response.status_code):
            raise ScraperError("ExampleSite blocked the catalog request with Cloudflare.")
        if response.status_code != 200:
            raise ScraperError(f"Failed to load catalog page {page}: HTTP {response.status_code}")

        # parse the catalog page here
```

**When to use each approach:**

| Situation | Use |
|---|---|
| No Cloudflare | plain `requests` |
| Cloudflare challenge, `requests` works with `cf_clearance` | `requests` + `apply_site_cookies` |
| Cloudflare, `requests` returns 403 even with valid `cf_clearance` | `curl_cffi` with `impersonate` |

## Filtering Guidance

When a site needs discovery filtered to one content type, prefer this order:

1. use the site's own filtered route or API parameter
2. use stable metadata already present on the catalog payload
3. only fall back to per-series detail lookups if there is no better source

The third option is usually the slowest and should be treated as a last resort, especially on sites that already require authenticated sessions.

## Error Handling Rules

Use `ScraperError` when the provider cannot load or parse the catalog.

```python
from scrapers.base import ScraperError
```

Examples:

```python
raise ScraperError(f"Failed to load catalog page {page}")
raise ScraperError("Catalog API returned invalid data")
raise ScraperError("ExampleSite blocked the catalog request with Cloudflare.")
```

Good discovery-provider behaviour:

- fail clearly
- do not silently return malformed pages
- do not force the GUI to guess provider-specific failure reasons
- include "Cloudflare" or "blocked" in the message when raising on an auth block — the discovery UI uses this to trigger authorization and retry

## Testing a Discovery Provider

Run tests as plain Python scripts from the `reader/` project root with the venv active.

### Direct provider test

```python
from scrapers.discovery_registry import get_all_discovery_providers

providers = {p.site_name: p for p in get_all_discovery_providers()}
provider = providers["your_site"]
page = provider.get_catalog_page(page=1, search_query="")

print(page.site)
print(page.page)
print(page.has_next_page)
print(len(page.entries))
print(page.entries[0].title)
print(page.entries[0].url)
print(page.entries[0].series_id)
```

### Search test

```python
page = provider.get_catalog_page(page=1, search_query="solo")
for entry in page.entries[:5]:
    print(entry.title)
```

### Pagination test

```python
page1 = provider.get_catalog_page(page=1)
page2 = provider.get_catalog_page(page=2)
print("page 1 has_next:", page1.has_next_page)
print("page 2 entries:", len(page2.entries))
# confirm entries differ between pages
titles1 = {e.title for e in page1.entries}
titles2 = {e.title for e in page2.entries}
print("overlap:", titles1 & titles2)  # should be empty
```

### Cover headers test

```python
import requests
entry = page.entries[0]
if entry.cover_url:
    r = requests.get(entry.cover_url, headers=entry.cover_headers, timeout=10)
    print("cover status:", r.status_code, "size:", len(r.content))
```

### Downloaded Only / matching test

```python
from scrapers.discovery_support import match_catalog_series_to_library

entry = page.entries[0]
# source_matches and title_matches are normally built by the discovery UI
# from the local library — pass empty dicts for a basic smoke test
match = provider.match_entry_to_library(entry, {}, {})
print(match)
```

A provider is in good shape when:

- the display name is correct
- the first page loads and has a reasonable number of entries
- pagination is accurate — `has_next_page` is True when more pages exist
- search returns reasonable results
- `series_id` is stable across multiple fetches
- covers load successfully with the provided headers
- local library matching works when `series_id` matches

## Packaging Note

If you build the app with PyInstaller, make sure `scrapers/discovery_sites/` is included:

```python
# in main.spec
collect_submodules("scrapers.discovery_sites")
```

Without this, auto-discovery will find no providers in a packaged build.

## Recommended Development Order

When adding a new discovery provider, implement in this order:

1. reconnaissance: catalog endpoint, API vs HTML, Cloudflare check
2. `site_name` and class-level session attributes
3. `get_display_name()` if the site name cannot be derived from `site_name`
4. `get_catalog_page()` using the most stable source
5. `CatalogSeries` mapping — fill in as many fields as the API provides
6. `has_next_page`
7. remote search support via `search_query` parameter
8. `cover_headers` if the cover host requires auth headers
9. provider-specific local matching override if needed
10. provider-specific local search override if needed

Do not start by patching the GUI for one provider. Extend the provider contract instead.

## Common Failure Modes

### Provider name looks wrong in the site dropdown

Cause: relying on `site_name` formatting for branding

Fix: override `get_display_name()`

### Downloaded Only cards do not match remote cards

Cause: unstable or missing `series_id`, or source metadata not saved consistently

Fix: provide a stable `series_id`; make sure the download scraper for the same site uses the same `site_name` and stores consistent source metadata; override `match_entry_to_library()` if the site needs custom matching

### Search works remotely but Downloaded Only search feels wrong

Cause: the default `matches_query()` fields are too limited for that site

Fix: override `matches_search()` in the provider

### Covers fail to load in discovery

Cause: missing referer, origin, or cookie headers on the cover host

Fix: set `cover_headers` on the `CatalogSeries`; if the cover host requires session cookies, include them via `site_cookie_header(self.site_name)`:

```python
CatalogSeries(
    ...,
    cover_headers={
        "User-Agent": load_site_user_agent(self.site_name, "Mozilla/5.0"),
        "Referer": self.BASE + "/",
        "Cookie": site_cookie_header(self.site_name),
    },
)
```

However, if the cover host is under a strict Cloudflare zone that validates TLS fingerprints, `cover_headers` alone will not work — plain `requests` will always get 403 regardless of cookies. In this case, implement `fetch_cover()` on the provider. The cover loader checks for this method before falling back to its own `requests.Session`:

```python
def fetch_cover(self, url: str, headers: dict[str, str]) -> bytes | None:
    """
    Override to handle cover fetches that require special HTTP clients.
    Return bytes on success, None to let the cover loader use its default
    requests.Session.
    """
    try:
        r = cffi_requests.get(
            url,
            headers=self._request_headers(),
            cookies=self._site_cookies(),
            impersonate=self.IMPERSONATE,
            timeout=20,
        )
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None
```

**Rule:** if your provider uses `curl_cffi` for catalog requests, it almost certainly needs `fetch_cover()` too. The cover image host is usually under the same Cloudflare zone as the main domain.

Note: for `fetch_cover()` to be called, `cover_loader.py` must pass the provider through to `_fetch_cover()`, and `site_browser_page.py` must pass the provider when creating entry widgets. Both of these changes are required in the GUI layer when adding a TLS-impersonating provider for the first time.

### Protected site never loads in discovery

Cause: provider is not reusing the saved browser session, or challenge detection is missing

Fix: reuse `core/site_session.py` helpers for cookies and user agent; detect challenge pages explicitly and raise `ScraperError` with a clear access-block message; let the shared discovery UI handle authorization and retry

### Auth browser loops and never closes

Cause: `cf_clearance` listed in `site_required_cookie_names` but the site does not serve a Cloudflare challenge — the cookie is never issued so the condition is never satisfied

Fix: remove `cf_clearance` from `required_cookie_names`. Only include it if the site actively shows a `Just a moment...` interstitial to unauthenticated visitors.

### Page 1 loads but pagination is broken

Cause: `has_next_page` derived incorrectly

Fix: use API pagination metadata when available; fall back to explicit next-page link detection only if necessary

### Provider not picked up after adding the file

Cause: auto-discovery failure — usually a syntax error in the new file, or the file is in the wrong directory

Fix: check for import errors with `python -c "from scrapers.discovery_sites.your_site import YourProvider"`, and confirm the file is in `scrapers/discovery_sites/` not `scrapers/`

## Important Boundary Rule

If you are about to add this kind of logic to `site_browser_page.py`, stop and move it to the provider layer instead:

- site display naming
- entry identity rules
- local library matching rules
- provider-specific local search logic
- provider-specific Downloaded Only entry reconstruction
- provider-specific field normalization

The GUI should render normalized discovery data, not define how a site works.