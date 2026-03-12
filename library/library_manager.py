import os
import re

from PIL import Image

from core.app_logging import get_logger
from core.app_paths import data_path


logger = get_logger(__name__)
SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


class Webtoon:
    def __init__(self, name, path, chapters, thumbnail, category=None, is_bookmarked=False, has_new_chapter=False):
        self.name = name
        self.path = path
        self.chapters = chapters
        self.thumbnail = thumbnail
        self.category = category
        self.is_bookmarked = bool(is_bookmarked)
        self.has_new_chapter = bool(has_new_chapter)


def scan_library(library_path: str, settings_store) -> list[Webtoon]:
    logger.info("Scanning library at %s", library_path)

    if not os.path.isdir(library_path):
        logger.warning("Library path does not exist: %s", library_path)
        return []

    webtoons = []
    for webtoon_name in sorted(os.listdir(library_path)):
        webtoon = build_webtoon_from_folder(library_path, webtoon_name, settings_store)
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


def preferred_thumbnail_path(webtoon_name: str, settings_store) -> str | None:
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


def build_webtoon_from_folder(library_path: str, webtoon_name: str, settings_store) -> Webtoon | None:
    webtoon_path = os.path.join(library_path, webtoon_name)
    if not os.path.isdir(webtoon_path):
        return None

    chapters = sorted([
        chapter
        for chapter in os.listdir(webtoon_path)
        if os.path.isdir(os.path.join(webtoon_path, chapter))
    ], key=natural_sort_key)
    if not chapters:
        return None

    first_image = _first_chapter_image_path(webtoon_path, chapters)
    if not first_image:
        return None

    thumbnail = preferred_thumbnail_path(webtoon_name, settings_store)
    if not thumbnail:
        thumbnail = get_or_create_auto_thumbnail(first_image, webtoon_name)

    return Webtoon(
        webtoon_name,
        webtoon_path,
        chapters,
        thumbnail,
        settings_store.get_category(webtoon_name),
        is_bookmarked=settings_store.get_bookmarked(webtoon_name),
        has_new_chapter=bool(settings_store.get_latest_new_chapter(webtoon_name)),
    )


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
        chapter_path = os.path.join(webtoon_path, chapter)
        try:
            images = sorted([
                filename
                for filename in os.listdir(chapter_path)
                if filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
                and os.path.isfile(os.path.join(chapter_path, filename))
            ])
        except OSError:
            continue
        if images:
            return os.path.join(chapter_path, images[0])
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
