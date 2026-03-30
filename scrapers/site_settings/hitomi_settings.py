from scrapers.models import ScraperConfigField, ScraperConfigOption


SITE_NAME = "hitomi"
SITE_DISPLAY_NAME = "Hitomi"
CONTENT_TYPE = "manga"
SITE_HOSTS = ("hitomi.la", "www.hitomi.la")
SITE_BASE_URL = "https://hitomi.la/"

LANGUAGE_OPTIONS = (
    ScraperConfigOption("all", "All Languages"),
    ScraperConfigOption("english", "English"),
    ScraperConfigOption("japanese", "Japanese"),
    ScraperConfigOption("chinese", "Chinese"),
    ScraperConfigOption("korean", "Korean"),
    ScraperConfigOption("spanish", "Spanish"),
    ScraperConfigOption("french", "French"),
    ScraperConfigOption("portuguese", "Portuguese"),
    ScraperConfigOption("thai", "Thai"),
    ScraperConfigOption("vietnamese", "Vietnamese"),
    ScraperConfigOption("german", "German"),
    ScraperConfigOption("italian", "Italian"),
    ScraperConfigOption("russian", "Russian"),
)

ALLOWED_LANGUAGES = {
    str(option.value or "").strip().casefold()
    for option in LANGUAGE_OPTIONS
    if str(option.value or "").strip()
}

SOURCE_CONFIG_FIELDS = (
    ScraperConfigField(
        key="languages",
        label="Languages",
        control="multi_select",
        options=list(LANGUAGE_OPTIONS),
        default=["all"],
        description="Choose which Hitomi gallery languages should appear in discovery.",
    ),
)


def normalize_config(config: dict | None, normalize_base) -> dict:
    normalized = normalize_base(config)
    languages = [
        str(language or "").strip().casefold()
        for language in normalized.get("languages", [])
        if str(language or "").strip()
    ]
    if not languages or "all" in languages:
        normalized["languages"] = ["all"]
    else:
        normalized["languages"] = list(dict.fromkeys(languages))
    return normalized


def selected_languages(config: dict | None) -> list[str]:
    raw = (config or {}).get("languages", ["all"]) if isinstance(config, dict) else ["all"]
    if not isinstance(raw, list):
        return ["all"]
    normalized = [
        str(language or "").strip().casefold()
        for language in raw
        if str(language or "").strip()
        and str(language or "").strip().casefold() in ALLOWED_LANGUAGES
    ]
    if not normalized or "all" in normalized:
        return ["all"]
    return list(dict.fromkeys(normalized))
