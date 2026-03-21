import threading
import uuid

import requests

from core.app_paths import data_path


class DownloadCancelled(Exception):
    pass


class DownloadJob:

    def __init__(
        self,
        initial_name: str,
        source_url: str,
        *,
        service,
        history_kind: str,
        url: str,
        output_path: str,
        preferred_name: str | None = None,
        chapter_urls: list[str] | None = None,
    ):
        self.service = service
        self.initial_name = initial_name
        self.active_name = initial_name
        self.source_url = source_url
        self.history_kind = history_kind
        self.url = url
        self.output_path = output_path
        self.preferred_name = preferred_name
        self.chapter_urls = list(chapter_urls or [])
        self.cancel_requested = False
        self.process = None
        self.thread = None
        self.executor = None
        self.executor_lock = threading.Lock()
        self.progress_current = 0
        self.progress_total = 0
        self.state = "Queued"
        self.temp_dir = str(data_path("_download_temp", f"job-{uuid.uuid4().hex}"))
        self.session_local = threading.local()
        self.sessions: list[requests.Session] = []
        self.sessions_lock = threading.Lock()
        self.resume_after_restart = False
