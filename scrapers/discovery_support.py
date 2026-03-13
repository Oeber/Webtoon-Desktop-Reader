from dataclasses import dataclass, field

from scrapers.models import CatalogSeries, normalize_catalog_text


@dataclass
class DiscoveryLibrarySnapshot:
    title_matches: dict[str, dict] = field(default_factory=dict)
    source_matches: dict[tuple[str, str], dict] = field(default_factory=dict)
    entries_by_site: dict[str, list[CatalogSeries]] = field(default_factory=dict)


def build_discovery_library_snapshot(webtoons, settings_store) -> DiscoveryLibrarySnapshot:
    title_matches = {}
    source_matches = {}
    entries_by_site = {}

    for webtoon in webtoons:
        info = {
            "name": webtoon.name,
            "webtoon": webtoon,
            "chapters": len(getattr(webtoon, "chapters", []) or []),
            "source_url": settings_store.get_source_url(webtoon.name),
            "source_title": settings_store.get_source_title(webtoon.name),
            "source_site": settings_store.get_source_site(webtoon.name),
            "source_series_id": settings_store.get_source_series_id(webtoon.name),
        }
        title_matches[normalize_catalog_text(webtoon.name)] = info

        source_title = info["source_title"]
        if source_title:
            title_matches.setdefault(normalize_catalog_text(source_title), info)

        source_site = str(info["source_site"] or "").strip()
        source_series_id = str(info["source_series_id"] or "").strip()
        if source_site and source_series_id:
            source_matches[(source_site, source_series_id)] = info

        library_entry = build_catalog_series_from_library(info)
        if library_entry is not None:
            entries_by_site.setdefault(source_site, []).append(library_entry)

    return DiscoveryLibrarySnapshot(
        title_matches=title_matches,
        source_matches=source_matches,
        entries_by_site=entries_by_site,
    )


def build_catalog_series_from_library(info: dict) -> CatalogSeries | None:
    source_site = str(info.get("source_site") or "").strip()
    source_url = str(info.get("source_url") or "").strip()
    if not source_site or not source_url:
        return None

    source_series_id = str(info.get("source_series_id") or info.get("name") or "").strip()
    return CatalogSeries(
        site=source_site,
        series_id=source_series_id,
        title=str(info.get("source_title") or info.get("name") or ""),
        url=source_url,
        total_chapters=info.get("chapters"),
    )


def match_catalog_series_to_library(
    entry: CatalogSeries,
    source_matches: dict[tuple[str, str], dict],
    title_matches: dict[str, dict],
) -> dict | None:
    source_key = entry.source_key()
    if source_key is not None:
        match = source_matches.get(source_key)
        if match is not None:
            return match
    return title_matches.get(entry.normalized_title())
