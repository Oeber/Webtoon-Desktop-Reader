# Creating Discovery Scrapers

This file explains how to add a new discovery provider to the project.

## Goal

The discovery system exists so the Discover page can browse site catalogs without putting site-specific logic inside the GUI.

The discovery page should never contain provider-specific parsing, naming, search, or matching rules. All site-specific behaviour belongs inside a discovery provider.

## Current Architecture

```text
reader/
|-- scrapers/
|   |-- discovery_base.py
|   |-- discovery_registry.py
|   |-- discovery_support.py
|   |-- models.py
|   `-- discovery_sites/
|       |-- __init__.py
|       `-- omega_scans.py
|
`-- gui/
    `-- discovery/
        |-- cover_loader.py
        |-- detail_page.py
        `-- site_browser_page.py
```

### Responsibilities

- `scrapers/discovery_base.py`: defines the discovery provider contract and shared defaults
- `scrapers/models.py`: defines normalized discovery data models like `CatalogSeries` and `CatalogPage`
- `scrapers/discovery_support.py`: shared helpers for matching discovery entries against the local library
- `scrapers/discovery_registry.py`: auto-discovers providers from `scrapers/discovery_sites/`
- `scrapers/discovery_sites/<site>.py`: contains the site-specific discovery implementation
- `gui/discovery/site_browser_page.py`: renders provider results and calls provider APIs only

## How Discovery Uses a Provider

```text
open Discover
-> discovery_registry.get_all_discovery_providers()
-> provider.get_catalog_page(page=1, search_query="")
-> render CatalogSeries cards
-> provider matching/search helpers are used for local filtering and Downloaded Only mode
```

## Required Base Interface

Every discovery provider must inherit from `BaseDiscoveryProvider`.

```python
class BaseDiscoveryProvider(ABC):
    site_name: str = "unknown"

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
entry.normalized_title()
entry.source_key()
entry.identity_key()
entry.search_text()
entry.matches_query(query)
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

## Creating a New Discovery Provider

Create a new file inside:

```text
scrapers/discovery_sites/
```

Example:

```text
scrapers/discovery_sites/asura_scans.py
```

### Minimal skeleton

```python
import requests

from scrapers.base import ScraperError
from scrapers.discovery_base import BaseDiscoveryProvider
from scrapers.models import CatalogPage, CatalogSeries


class AsuraScansDiscoveryProvider(BaseDiscoveryProvider):
    site_name = "asura_scans"
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
        response = requests.get(f"{self.API_BASE}/series", params=params, headers=self.HEADERS, timeout=20)
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

Once the file exists, `discovery_registry.py` auto-loads it. No registry edit is needed.

## Step-by-Step Process

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
- cover headers if the host requires referer or origin
- author if available
- description if available
- latest chapter if available
- total chapters if available

### 3. Prefer stable identity fields

The Discover page matches local library entries back to remote results.

For that reason, each provider should try to supply:

- a stable `site_name`
- a stable `series_id`
- a canonical `url`
- the real source title when possible

Matching order is:

1. provider/site + `series_id`
2. normalized title fallback

If a site needs a custom rule, override `match_entry_to_library()` in the provider instead of adding UI logic.

### 4. Keep provider-specific display naming in the provider

If a site should be shown as `OmegaScans`, `MangaDex`, or something that should not be generated from `site_name`, override:

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
        " ".join(entry.tags),
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

### 7. Use correct cover headers

If a cover host blocks direct requests unless the right headers are present, attach them to `CatalogSeries.cover_headers`.

Example:

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

## Error Handling Rules

Use `ScraperError` when the provider cannot load or parse the catalog.

Examples:

```python
raise ScraperError(f"Failed to load catalog page {page}")
raise ScraperError("Catalog API returned invalid data")
```

Good discovery-provider behaviour:

- fail clearly
- do not silently return malformed pages
- do not force the GUI to guess provider-specific failure reasons

## Testing a Discovery Provider

### Direct provider test

```python
from scrapers.discovery_registry import get_all_discovery_providers

providers = {provider.site_name: provider for provider in get_all_discovery_providers()}
provider = providers["omega_scans"]
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

### Downloaded Only / matching test

```python
from scrapers.discovery_support import match_catalog_series_to_library

entry = page.entries[0]
match = provider.match_entry_to_library(entry, source_matches, title_matches)
print(match)
```

A provider is in good shape when:

- the display name is correct
- the first page loads correctly
- pagination is accurate
- search returns reasonable results
- `series_id` is stable
- local library matching works
- covers load with the provided headers

## Recommended Development Order

When adding a new discovery provider, implement in this order:

1. `site_name`
2. `get_display_name()` if needed
3. `get_catalog_page()` using the most stable source
4. `CatalogSeries` mapping
5. `has_next_page`
6. remote search support
7. provider-specific local matching override if needed
8. provider-specific local search override if needed

Do not start by patching the GUI for one provider. Extend the provider contract instead.

## Common Failure Modes

### Provider name looks wrong in the site dropdown

Cause:
- relying on `site_name` formatting for branding

Fix:
- override `get_display_name()`

### Downloaded Only cards do not match remote cards

Cause:
- unstable or missing `series_id`
- source metadata not normalized consistently

Fix:
- provide a stable `series_id`
- make sure the downloader stores the right source metadata
- override `match_entry_to_library()` if the site needs custom matching

### Search works remotely but Downloaded Only search feels wrong

Cause:
- the default `matches_query()` fields are too limited for that site

Fix:
- override `matches_search()` in the provider

### Covers fail to load in discovery

Cause:
- missing referer or other site headers

Fix:
- set `cover_headers` on the `CatalogSeries`

### Page 1 loads but pagination is broken

Cause:
- `has_next_page` derived incorrectly

Fix:
- use API pagination metadata when available
- fall back to explicit next-page link detection only if necessary

## Important Boundary Rule

If you are about to add this kind of logic to `site_browser_page.py`, stop and move it to the provider layer instead:

- site display naming
- entry identity rules
- local library matching rules
- provider-specific local search logic
- provider-specific Downloaded Only entry reconstruction
- provider-specific field normalization

The GUI should render normalized discovery data, not define how a site works.
