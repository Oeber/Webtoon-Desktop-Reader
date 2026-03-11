import json
import os

PROGRESS_JSON = "data/progress.json"


class ProgressStore:

    def __init__(self):
        os.makedirs("data", exist_ok=True)
        self._path = PROGRESS_JSON
        self._data: dict = {}
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

    def get(self, webtoon_name: str) -> dict | None:
        """Return { chapter, scroll } or None if never read."""
        return self._data.get(webtoon_name)

    def save(self, webtoon_name: str, chapter: str, scroll_pct: float):
        """Save progress. scroll_pct is 0.0–1.0."""
        scroll_pct = max(0.0, min(1.0, scroll_pct))
        self._data[webtoon_name] = {"chapter": chapter, "scroll": scroll_pct}
        self._save()

    def clear(self, webtoon_name: str):
        if webtoon_name in self._data:
            del self._data[webtoon_name]
            self._save()