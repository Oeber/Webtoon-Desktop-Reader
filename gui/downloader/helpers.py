import os
import re
from urllib.parse import parse_qs, urlparse


SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def sanitize_webtoon_name(name: str | None) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name or "").strip()


def detect_url_type(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "episode_no" in qs:
        return "chapter"
    if "/chapter-" in parsed.path.rstrip("/").lower():
        return "chapter"
    return "series"


def series_url_from_chapter_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if "/chapter-" in path:
        path = path.rsplit("/chapter-", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def extract_episode_number(url: str) -> int | None:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "episode_no" in qs:
        try:
            return int(qs["episode_no"][0])
        except Exception:
            return None

    match = re.search(r"chapter[-/ ]?(\d+)", url, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def extract_chapter_number(value: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def chapter_sort_key(value: str) -> tuple[int, float, str]:
    chapter_number = extract_chapter_number(value)
    if chapter_number is not None:
        return (0, chapter_number, value.lower())
    return (1, float("inf"), value.lower())


def chapter_path_sort_key(path: str) -> tuple[int, float, str]:
    return chapter_sort_key(os.path.basename(path))
