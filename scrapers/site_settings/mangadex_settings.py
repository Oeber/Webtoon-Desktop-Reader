from scrapers.models import ScraperConfigField, ScraperConfigOption


SITE_NAME = "mangadex"
SITE_DISPLAY_NAME = "MangaDex"
CONTENT_TYPE = "manga"
SITE_HOSTS = ("mangadex.org", "www.mangadex.org")
SITE_BASE_URL = "https://mangadex.org/"

LANGUAGE_OPTIONS = (
    ScraperConfigOption("en", "English"),
    ScraperConfigOption("pt-br", "Portuguese (Brazil)"),
    ScraperConfigOption("pt", "Portuguese"),
    ScraperConfigOption("es-la", "Spanish (LATAM)"),
    ScraperConfigOption("es", "Spanish"),
    ScraperConfigOption("fr", "French"),
    ScraperConfigOption("de", "German"),
    ScraperConfigOption("it", "Italian"),
    ScraperConfigOption("ja-ro", "Japanese (Romanized)"),
    ScraperConfigOption("ja", "Japanese"),
)

SOURCE_CONFIG_FIELDS = (
    ScraperConfigField(
        key="translated_language",
        label="Language",
        control="select",
        options=list(LANGUAGE_OPTIONS),
        default="en",
        description="Pick the translated chapter language this saved MangaDex source should use.",
    ),
)

DEFAULT_LANGUAGE_PRIORITY = tuple(
    str(option.value or "").strip().casefold()
    for option in LANGUAGE_OPTIONS
    if str(option.value or "").strip()
)


def normalize_config(config: dict | None, normalize_base) -> dict:
    incoming = dict(config or {}) if isinstance(config, dict) else {}
    if "translated_language" not in incoming:
        legacy = incoming.get("translated_languages")
        if isinstance(legacy, list) and legacy:
            incoming["translated_language"] = str(legacy[0] or "").strip()
        elif isinstance(legacy, str) and legacy.strip():
            incoming["translated_language"] = legacy.strip()
    return normalize_base(incoming)


def selected_language_priority(config: dict | None) -> tuple[str, ...]:
    incoming = dict(config or {}) if isinstance(config, dict) else {}
    if "translated_language" not in incoming:
        legacy = incoming.get("translated_languages")
        if isinstance(legacy, list) and legacy:
            incoming["translated_language"] = str(legacy[0] or "").strip()
        elif isinstance(legacy, str) and legacy.strip():
            incoming["translated_language"] = legacy.strip()
    configured = str(incoming.get("translated_language", "en") or "").strip().casefold()
    if configured and configured not in DEFAULT_LANGUAGE_PRIORITY:
        configured = "en"
    if configured:
        return (configured,)
    return DEFAULT_LANGUAGE_PRIORITY
