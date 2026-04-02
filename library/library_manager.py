import os
import re

from PIL import Image

from core.app_logging import get_logger
from core.app_paths import data_path
from core.chapter_storage import list_chapter_image_paths, list_series_chapters
from core.library_layout import (
    infer_content_type_from_path,
    list_library_entries,
    normalize_content_type,
    resolve_existing_webtoon_storage_path,
    ensure_library_content_layout,
)


logger = get_logger(__name__)


class Webtoon:
    def __init__(
        self,
        name,
        path,
        chapters,
        thumbnail,
        category=None,
        is_bookmarked=False,
        has_new_chapter=False,
        content_type="webtoon",
        storage_path=None,
    ):
        self.name = name
        self.path = path
        self.chapters = chapters
        self.thumbnail = thumbnail or ""
        self.category = category
        self.is_bookmarked = bool(is_bookmarked)
        self.has_new_chapter = bool(has_new_chapter)
        self.content_type = content_type
        self.storage_path = storage_path or path


def scan_library(library_path: str, settings_store) -> list[Webtoon]:
    logger.info("Scanning library at %s", library_path)
    ensure_library_content_layout(library_path, settings_store)

    if not os.path.isdir(library_path):
        logger.warning("Library path does not exist: %s", library_path)
        return []

    library_entries = list_library_entries(library_path)
    settings_rows = settings_store.get_rows(
        library_entries,
        columns=("custom_thumbnail", "category", "bookmarked", "latest_new_chapter", "content_type"),
    )

    webtoons = []
    for webtoon_name in library_entries:
        webtoon = build_webtoon_from_folder(
            library_path,
            webtoon_name,
            settings_store,
            settings_row=settings_rows.get(webtoon_name),
        )
        if webtoon is not None:
            webtoons.append(webtoon)

    logger.info("Library scan completed with %d webtoons", len(webtoons))
    return webtoons


THUMB_FOLDER = data_path("thumbnails")
THUMB_W = 360
THUMB_H = 540
SCAN_MIN_Y = 200
SCAN_MAX_Y = 3000
BLANK_THRESHOLD = 12


def preferred_thumbnail_path(webtoon_name: str, settings_store, settings_row: dict | None = None) -> str | None:
    custom = (settings_row or {}).get("custom_thumbnail")
    if custom is None:
        custom = settings_store.get(webtoon_name)
    if custom and os.path.exists(custom):
        return custom

    thumb_path = THUMB_FOLDER / f"{webtoon_name}.jpg"
    if thumb_path.exists():
        return str(thumb_path)
    return None


def get_or_create_auto_thumbnail(image_path: str, webtoon_name: str) -> str:
    THUMB_FOLDER.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_FOLDER / f"{webtoon_name}.jpg"
    if thumb_path.exists():
        return str(thumb_path)
    return _generate_auto_thumbnail(image_path, str(thumb_path))


def build_webtoon_from_folder(
    library_path: str,
    webtoon_name: str,
    settings_store,
    settings_row: dict | None = None,
) -> Webtoon | None:
    storage_path = resolve_existing_webtoon_storage_path(
        library_path,
        webtoon_name,
        settings_store=settings_store,
        settings_row=settings_row,
    )
    if not storage_path:
        return None

    if os.path.isdir(storage_path):
        webtoon_path = storage_path
        chapters = sorted(list_series_chapters(webtoon_path), key=natural_sort_key)
    elif os.path.isfile(storage_path):
        webtoon_path = os.path.dirname(storage_path)
        chapters = [os.path.basename(storage_path)]
    else:
        return None
    if not chapters:
        return None

    settings_row = dict(settings_row or {})
    if not settings_row or "content_type" not in settings_row:
        settings_row.update(
            settings_store.get_rows(
                [webtoon_name],
                columns=("custom_thumbnail", "category", "bookmarked", "latest_new_chapter", "content_type"),
            ).get(webtoon_name, {})
        )
    stored_content_type = normalize_content_type(settings_row.get("content_type"), default="")
    inferred_content_type = infer_content_type_from_path(storage_path)
    if not stored_content_type:
        stored_content_type = inferred_content_type
    first_image = _first_chapter_image_path(webtoon_path, chapters)
    if first_image:
        thumbnail = resolve_thumbnail_path(
            webtoon_name,
            first_image,
            settings_store,
            settings_row=settings_row,
        )
        if stored_content_type in {"manga", "webtoon"}:
            content_type = stored_content_type
        else:
            content_type = "webtoon"
    else:
        thumbnail = preferred_thumbnail_path(webtoon_name, settings_store, settings_row=settings_row) or ""
        if stored_content_type in {"webnovel", "manga", "webtoon"}:
            content_type = stored_content_type
        else:
            content_type = inferred_content_type

    return Webtoon(
        webtoon_name,
        webtoon_path,
        chapters,
        thumbnail,
        settings_row.get("category"),
        is_bookmarked=bool(settings_row.get("bookmarked", 0)),
        has_new_chapter=bool(settings_row.get("latest_new_chapter")),
        content_type=content_type,
        storage_path=storage_path,
    )


def resolve_thumbnail_path(
    webtoon_name: str,
    fallback_image_path: str,
    settings_store,
    settings_row: dict | None = None,
) -> str:
    thumbnail = preferred_thumbnail_path(webtoon_name, settings_store, settings_row=settings_row)
    if thumbnail:
        return thumbnail
    return fallback_image_path


def _generate_auto_thumbnail(image_path: str, thumb_path: str) -> str:
    img = Image.open(image_path).convert("RGB")
    src_w, src_h = img.size
    crop_y = _detect_page_break(img, src_w, src_h)

    cropped = img.crop((0, 0, src_w, crop_y))
    cw, ch = cropped.size
    scale = max(THUMB_W / cw, THUMB_H / ch)
    new_w = int(cw * scale)
    new_h = int(ch * scale)
    cropped = cropped.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - THUMB_W) // 2
    top = (new_h - THUMB_H) // 2
    cropped = cropped.crop((left, top, left + THUMB_W, top + THUMB_H))
    cropped.save(thumb_path, "JPEG", quality=88)
    return thumb_path


def _first_chapter_image_path(webtoon_path: str, chapters: list[str]) -> str | None:
    for chapter in chapters:
        images = list_chapter_image_paths(webtoon_path, chapter)
        if images:
            return images[0]
    return None


def _detect_page_break(img: Image.Image, src_w: int, src_h: int) -> int:
    scan_end = min(SCAN_MAX_Y, src_h)
    step = max(1, src_w // 200)
    for y in range(SCAN_MIN_Y, scan_end):
        if _is_blank_row(img, y, src_w, step):
            return y
    return min(1000, src_h)


def _is_blank_row(img: Image.Image, y: int, width: int, step: int) -> bool:
    pixels = [img.getpixel((x, y)) for x in range(0, width, step)]

    all_black = all(
        r <= BLANK_THRESHOLD and g <= BLANK_THRESHOLD and b <= BLANK_THRESHOLD
        for r, g, b in pixels
    )
    if all_black:
        return True

    all_white = all(
        r >= 255 - BLANK_THRESHOLD and
        g >= 255 - BLANK_THRESHOLD and
        b >= 255 - BLANK_THRESHOLD
        for r, g, b in pixels
    )
    return all_white


def natural_sort_key(s: str):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", s)
    ]
