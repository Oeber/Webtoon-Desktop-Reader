import os
from PIL import Image
import re

class Webtoon:
    def __init__(self, name, path, chapters, thumbnail):
        self.name = name
        self.path = path
        self.chapters = chapters
        self.thumbnail = thumbnail


def scan_library(library_path):

    webtoons = []

    for webtoon_name in os.listdir(library_path):

        webtoon_path = os.path.join(library_path, webtoon_name)

        if not os.path.isdir(webtoon_path):
            continue

        # ← FIXED: only include actual subdirectories as chapters
        chapters = sorted([
            c for c in os.listdir(webtoon_path)
            if os.path.isdir(os.path.join(webtoon_path, c))
        ], key=natural_sort_key)

        if not chapters:  # ← skip webtoons with no chapters
            continue

        first_chapter = os.path.join(webtoon_path, chapters[0])

        images = sorted([
            f for f in os.listdir(first_chapter)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ])

        if not images:  # ← skip if no valid images found
            continue

        thumbnail = create_thumbnail(os.path.join(first_chapter, images[0]), webtoon_name)

        webtoons.append(
            Webtoon(
                webtoon_name,
                webtoon_path,
                chapters,
                thumbnail
            )
        )

    return webtoons

THUMB_FOLDER = "data/thumbnails"

def create_thumbnail(image_path, webtoon_name):

    os.makedirs(THUMB_FOLDER, exist_ok=True)

    thumb_path = os.path.join(THUMB_FOLDER, f"{webtoon_name}.jpg")

    # do not regenerate if exists
    if os.path.exists(thumb_path):
        return thumb_path

    img = Image.open(image_path)

    width, height = img.size

    crop_height = min(1000, height)

    cropped = img.crop((0, 0, width, crop_height))

    # resize to nicer library card size
    cropped.thumbnail((300, 300))

    cropped.save(thumb_path, "JPEG", quality=85)

    return thumb_path

def natural_sort_key(s):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', s)]