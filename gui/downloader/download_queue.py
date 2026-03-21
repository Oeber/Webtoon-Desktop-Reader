import threading
from collections import deque

from gui.downloader.helpers import sanitize_webtoon_name


class GlobalDownloadQueue:

    def __init__(self, max_active_jobs: int = 3):
        self._lock = threading.Lock()
        self._max_active_jobs = max(1, int(max_active_jobs))
        self._active_jobs = []
        self._pending_jobs = deque()

    def enqueue(self, job) -> bool:
        with self._lock:
            if len(self._active_jobs) < self._max_active_jobs:
                self._active_jobs.append(job)
                return True
            self._pending_jobs.append(job)
            return False

    def remove(self, job) -> tuple[bool, list]:
        with self._lock:
            if job in self._active_jobs:
                self._active_jobs = [item for item in self._active_jobs if item is not job]
                return True, self._advance_locked()

            remaining = deque(item for item in self._pending_jobs if item is not job)
            removed = len(remaining) != len(self._pending_jobs)
            self._pending_jobs = remaining
            return removed, []

    def finish(self, job) -> list:
        with self._lock:
            if job in self._active_jobs:
                self._active_jobs = [item for item in self._active_jobs if item is not job]
            else:
                self._pending_jobs = deque(item for item in self._pending_jobs if item is not job)
                return []
            return self._advance_locked()

    def has_conflict(self, *, source_url: str = "", name: str = "", exclude_job=None) -> bool:
        normalized_source_url = (source_url or "").strip()
        normalized_name = sanitize_webtoon_name(name or "")
        with self._lock:
            jobs = [*self._active_jobs, *self._pending_jobs]

        for existing in jobs:
            if existing is exclude_job:
                continue
            if normalized_source_url and existing.source_url == normalized_source_url:
                return True
            if normalized_name:
                existing_names = {
                    sanitize_webtoon_name(existing.initial_name),
                    sanitize_webtoon_name(existing.active_name),
                    sanitize_webtoon_name(existing.preferred_name),
                }
                if normalized_name in existing_names:
                    return True
        return False

    def _advance_locked(self) -> list:
        ready_jobs = []
        while self._pending_jobs and len(self._active_jobs) < self._max_active_jobs:
            next_job = self._pending_jobs.popleft()
            if next_job.cancel_requested:
                continue
            self._active_jobs.append(next_job)
            ready_jobs.append(next_job)
        return ready_jobs


_global_download_queue = GlobalDownloadQueue()


def get_global_download_queue() -> GlobalDownloadQueue:
    return _global_download_queue
