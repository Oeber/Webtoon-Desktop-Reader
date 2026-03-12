import os
import re

from PIL import Image
from app_logging import get_logger

logger = get_logger(__name__)

class Webtoon:
    def __init__(self, name, path, chapters, thumbnail):
        self.name = name
        self.path = path
        self.chapters = chapters
        self.thumbnail = thumbnail


def scan_library(library_path: str, settings_store) -> list[Webtoon]:
    logger.info("Scanning library at %s", library_path)

    webtoons = []

    if not os.path.isdir(library_path):
        logger.warning("Library path does not exist: %s", library_path)
        return []

    for webtoon_name in sorted(os.listdir(library_path)):

        webtoon_path = os.path.join(library_path, webtoon_name)

        if not os.path.isdir(webtoon_path):
            continue

        chapters = sorted([
            c for c in os.listdir(webtoon_path)
            if os.path.isdir(os.path.join(webtoon_path, c))
        ], key=natural_sort_key)

        if not chapters:
            continue

        first_chapter = os.path.join(webtoon_path, chapters[0])

        images = sorted([
            f for f in os.listdir(first_chapter)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ])

        if not images:
            continue

        # Resolve thumbnail: custom override → auto-generated
        custom = settings_store.get(webtoon_name)
        if custom and os.path.exists(custom):
            thumbnail = custom
        else:
            thumbnail = get_or_create_auto_thumbnail(
                os.path.join(first_chapter, images[0]),
                webtoon_name
            )

        webtoons.append(
            Webtoon(webtoon_name, webtoon_path, chapters, thumbnail)
        )

    logger.info("Library scan completed with %d webtoons", len(webtoons))
    return webtoons


# ---------------------------------------------------------------------------
# Auto-thumbnail generation
# ---------------------------------------------------------------------------

THUMB_FOLDER = "data/thumbnails"
THUMB_W = 360
THUMB_H = 540

# Row-scan search window: only look between these pixel rows for a separator
SCAN_MIN_Y = 200     # don't trigger on top edge/header
SCAN_MAX_Y = 3000    # stop searching after this — treat as single tall image

# A row is "blank" if every pixel is within this distance from pure black/white
BLANK_THRESHOLD = 12


def get_or_create_auto_thumbnail(image_path: str, webtoon_name: str) -> str:

    os.makedirs(THUMB_FOLDER, exist_ok=True)
    thumb_path = os.path.join(THUMB_FOLDER, f"{webtoon_name}.jpg")

    if os.path.exists(thumb_path):
        return thumb_path

    return _generate_auto_thumbnail(image_path, thumb_path)


def _generate_auto_thumbnail(image_path: str, thumb_path: str) -> str:

    img = Image.open(image_path).convert("RGB")
    src_w, src_h = img.size

    crop_y = _detect_page_break(img, src_w, src_h)

    # Crop from top to the detected page break
    cropped = img.crop((0, 0, src_w, crop_y))

    # Scale + center-crop to portrait card dimensions (cover style)
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


def _detect_page_break(img: Image.Image, src_w: int, src_h: int) -> int:
    """
    Scan rows from SCAN_MIN_Y downward.
    Return the y-coordinate of the first row that is entirely black or white.
    Falls back to min(1000, src_h) if nothing is found.
    """
    scan_end = min(SCAN_MAX_Y, src_h)

    # For wide images, sample every few pixels instead of every pixel
    step = max(1, src_w // 200)

    for y in range(SCAN_MIN_Y, scan_end):
        if _is_blank_row(img, y, src_w, step):
            return y

    # Fallback
    return min(1000, src_h)


def _is_blank_row(img: Image.Image, y: int, width: int, step: int) -> bool:
    """Return True if every sampled pixel in row y is near pure black or white."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def natural_sort_key(s: str):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', s)
    ]
