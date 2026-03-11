"""
thumbnail_store.py
Manages custom thumbnail overrides stored in data/thumbnails.json.
Format: { "Webtoon Name": "/absolute/or/relative/path/to/image.jpg" }
"""

import json
import os

THUMBNAILS_JSON = "data/thumbnails.json"


class ThumbnailStore:

    def __init__(self):
        os.makedirs("data", exist_ok=True)
        self._path = THUMBNAILS_JSON
        self._data: dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, webtoon_name: str) -> str | None:
        """Return custom thumbnail path if set, else None."""
        return self._data.get(webtoon_name)

    def set(self, webtoon_name: str, image_path: str):
        """Set a custom thumbnail path and persist to JSON."""
        self._data[webtoon_name] = image_path
        self._save()

    def clear(self, webtoon_name: str):
        """Remove custom thumbnail override."""
        if webtoon_name in self._data:
            del self._data[webtoon_name]
            self._save()