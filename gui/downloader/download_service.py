import json
import os
import re
import shutil
import subprocess
import threading
import time
from html import escape
from concurrent.futures import CancelledError
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from PySide6.QtCore import QObject, Signal

from core.app_logging import get_logger
from core.http_client import create_session, get as http_get
from core.app_paths import data_path
from stores.download_history_store import get_instance as get_download_history
from gui.downloader.download_queue import get_global_download_queue
from gui.downloader.download_runtime import DownloadCancelled, DownloadJob
from gui.downloader.download_tracking import DownloadTrackingStore
from gui.downloader.helpers import (
    SUPPORTED_IMAGE_EXTENSIONS,
    detect_url_type,
    extract_episode_number,
    sanitize_webtoon_name,
)
from library.library_manager import build_webtoon_from_folder, preferred_thumbnail_path
from scrapers.base import BaseScraper, ScraperDisabledError, ScraperError
from scrapers.registry import get_scraper
from stores.webtoon_settings_store import get_instance as get_webtoon_settings

logger = get_logger(__name__)

class DownloadService(QObject):
    status_changed = Signal(str, str)
    name_resolved = Signal(str, str)
    progress_changed = Signal(str, int, int)
    thumbnail_resolved = Signal(str, str)
    download_started = Signal(str)
    download_finished = Signal(str, str)
    library_changed = Signal(str)
    auth_required = Signal(str, str, object, object)

    def __init__(self, parent=None, history_kind: str = "download"):
        super().__init__(parent)
        self.settings_store = get_webtoon_settings()
        self.history_store = get_download_history()
        self.history_kind = history_kind
        self._jobs: dict[str, DownloadJob] = {}
        self._jobs_lock = threading.Lock()
        self._restored_pending_jobs = False
        self._queue = get_global_download_queue()
        self._tracking = DownloadTrackingStore(self.settings_store, self.history_store)
        self.browser_fetcher = None
        self._last_library_changed_at: dict[str, float] = {}
        self._browser_fetch_sites: set[str] = set()

        temp_root = self._temp_root()
        if os.path.exists(temp_root):
            shutil.rmtree(temp_root, ignore_errors=True)
        logger.info("DownloadService initialized")

    def is_busy(self) -> bool:
        with self._jobs_lock:
            return bool(self._jobs)

    def has_active_download(self, name: str) -> bool:
        normalized = sanitize_webtoon_name(name or "")
        if not normalized:
            return False
        with self._jobs_lock:
            return any(job.active_name == normalized for job in self._jobs.values())

    def start_download(
        self,
        url: str,
        output_path: str,
        preferred_name: str | None = None,
        job_name: str | None = None,
        chapter_urls: list[str] | None = None,
    ) -> str | None:
        url = (url or "").strip().strip("'\"")
        if not url:
            return "Please enter a URL."

        normalized_source_url = self._tracking.normalized_source_url(url)
        initial_name = (
            sanitize_webtoon_name(job_name)
            or sanitize_webtoon_name(preferred_name)
            or sanitize_webtoon_name(url.rstrip("/").split("/")[-1])
            or "download"
        )
        if self._queue.has_conflict(source_url=normalized_source_url):
            resolved_name = sanitize_webtoon_name(preferred_name) or initial_name
            return f"'{resolved_name}' is already downloading."
        if self._queue.has_conflict(name=initial_name):
            return f"'{initial_name}' is already downloading."

        logger.info("Starting download url=%s preferred_name=%s output=%s", url, preferred_name, output_path)
        job = DownloadJob(
            initial_name,
            normalized_source_url,
            service=self,
            history_kind=self.history_kind,
            url=url,
            output_path=output_path,
            preferred_name=preferred_name,
            chapter_urls=chapter_urls,
        )
        with self._jobs_lock:
            self._jobs[job.initial_name] = job
        self._tracking.persist_queued(job)
        if self._queue.enqueue(job):
            self._begin_job(job)
        else:
            self.status_changed.emit(job.initial_name, "Queued")
        return None

    def cancel_download(self, name: str | None = None):
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        if not jobs:
            return
        for job in jobs:
            if name and job.active_name != sanitize_webtoon_name(name):
                continue
            job.cancel_requested = True
            if job.state == "Queued":
                logger.warning("Cancelling queued download for %s", job.active_name)
                self._finalize_queued_cancellation(job)
                continue
            logger.warning("Cancelling active download for %s", job.active_name)
            if job.process and job.process.poll() is None:
                job.process.terminate()
            self._shutdown_job_executor(job, wait=False, cancel_futures=True)

    def shutdown(self, wait_timeout: float = 5.0):
        logger.info("Shutting down DownloadService")
        self._tracking.save_active_source_urls(list(self._jobs.values()) if self._jobs else [])
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            self._tracking.mark_shutdown_resume(job)
        self.cancel_download()

        with self._jobs_lock:
            threads = [job.thread for job in self._jobs.values() if job.thread is not None]

        deadline = time.monotonic() + max(0.0, wait_timeout)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                thread.join(timeout=remaining)
            except Exception as e:
                logger.warning("Failed while joining download thread", exc_info=e)

    def set_browser_fetcher(self, fetcher):
        self.browser_fetcher = fetcher

    def active_download_names(self) -> list[str]:
        with self._jobs_lock:
            return [job.active_name or job.initial_name for job in self._jobs.values()]

    def active_download_count(self) -> int:
        with self._jobs_lock:
            return len(self._jobs)

    def get_progress(self, name: str) -> tuple[int, int]:
        normalized = sanitize_webtoon_name(name or "")
        if not normalized:
            return 0, 0
        with self._jobs_lock:
            for job in self._jobs.values():
                if job.active_name == normalized or job.initial_name == normalized:
                    return int(job.progress_current), int(job.progress_total)
        return 0, 0

    def get_status(self, name: str) -> str:
        normalized = sanitize_webtoon_name(name or "")
        if not normalized:
            return ""
        with self._jobs_lock:
            for job in self._jobs.values():
                if job.active_name == normalized or job.initial_name == normalized:
                    return str(job.state or "")
        return ""

    def restore_pending_jobs(self) -> list[dict]:
        if self._restored_pending_jobs:
            return []
        self._restored_pending_jobs = True

        restored = []
        for entry in self._tracking.load_resumable_entries(self.history_kind):
            payload = entry.get("resume_payload") or {}
            url = str(payload.get("url") or "").strip()
            output_path = str(payload.get("output_path") or "").strip()
            if not url or not output_path:
                logger.warning("Discarding invalid persisted download job for %s", entry.get("name", ""))
                self._tracking.clear_invalid_resume(self.history_kind, entry.get("name", ""))
                continue

            restored_name = sanitize_webtoon_name(entry.get("name") or payload.get("job_name") or "")
            preferred_name = payload.get("preferred_name")
            if restored_name and not preferred_name:
                preferred_name = restored_name

            error = self.start_download(
                url,
                output_path,
                preferred_name=preferred_name or None,
                job_name=restored_name or None,
                chapter_urls=list(payload.get("chapter_urls") or []),
            )
            if error:
                logger.warning("Failed to restore persisted job %s: %s", entry.get("name", ""), error)
                continue

            active_name = restored_name or sanitize_webtoon_name(preferred_name or "") or sanitize_webtoon_name(url.rstrip("/").split("/")[-1]) or "download"
            restored.append(
                {
                    "name": active_name,
                    "source_url": entry.get("source_url", ""),
                    "status": self.get_status(active_name) or "Queued",
                }
            )
        return restored

    def _begin_job(self, job: DownloadJob):
        job.state = "Downloading"
        self._tracking.mark_downloading(job)
        self.status_changed.emit(job.active_name or job.initial_name, "Downloading")
        self.download_started.emit(job.active_name or job.initial_name)
        thread = threading.Thread(
            target=self._run_download,
            args=(job,),
            daemon=True,
        )
        job.thread = thread
        thread.start()

    def _finalize_queued_cancellation(self, job: DownloadJob):
        removed, next_jobs = self._queue.remove(job)
        if not removed:
            return
        self._close_job_sessions(job)
        shutil.rmtree(job.temp_dir, ignore_errors=True)
        with self._jobs_lock:
            self._jobs.pop(job.initial_name, None)
        final_status = self._tracking.finalize_queued_cancellation(job)
        self.status_changed.emit(job.active_name or job.initial_name, final_status)
        self.download_finished.emit(job.active_name or job.initial_name, final_status)
        for next_job in next_jobs:
            next_job_service = getattr(next_job, "service", None)
            if next_job_service is not None:
                next_job_service._begin_job(next_job)

    def _run_download(self, job: DownloadJob):
        url = job.url
        output_path = job.output_path
        preferred_name = job.preferred_name
        chapter_urls = list(job.chapter_urls or [])
        name = sanitize_webtoon_name(preferred_name) or job.initial_name or "download"
        status = "Failed"
        last_error = ""

        try:
            os.makedirs(job.temp_dir, exist_ok=True)

            if preferred_name:
                self._ensure_job_name_available(job, name)
                self._tracking.rename_history(job, job.initial_name, name, "Downloading")
                self.name_resolved.emit(job.initial_name, name)
            else:
                name = self._resolve_name(url)
                self._ensure_job_name_available(job, name)
                self._tracking.rename_history(job, job.initial_name, name, "Downloading")
                self.name_resolved.emit(job.initial_name, name)

            job.active_name = name
            logger.info("Resolved download name: %s", name)

            try:
                scraper = get_scraper(url)
            except ScraperDisabledError:
                raise
            except Exception as e:
                logger.warning("Custom scraper resolution failed for %s", url, exc_info=e)
                scraper = None

            if scraper is not None:
                logger.info("Using custom scraper for %s", url)
                saved_name = self._custom_download(
                    job,
                    url,
                    output_path,
                    target_name=preferred_name,
                    selected_chapter_urls=chapter_urls or None,
                )
            else:
                if chapter_urls:
                    raise ScraperError("Chapter selection is only available for supported scraper sites.")
                logger.info("Using gallery-dl fallback for %s", url)
                saved_name = self._gallery_dl_download(job, url, output_path, name)

            self._tracking.save_source_url(saved_name, job.source_url)
            status = "Completed"
            self._emit_library_changed(saved_name, force=True)
        except DownloadCancelled:
            self._tracking.save_source_url(job.active_name or name, job.source_url)
            status = "Cancelled"
        except FileNotFoundError:
            logger.error("Download failed because required file/tool was missing")
            last_error = "A required download tool was not found."
            status = "Failed"
        except ScraperError as e:
            if self._is_expected_access_block(e):
                logger.info("Download blocked for %s: %s", url, e)
                site_name = ""
                try:
                    site_name = getattr(get_scraper(url), "site_name", "") or ""
                except Exception:
                    site_name = ""
                if site_name:
                    self.auth_required.emit(site_name, url, preferred_name, list(chapter_urls or []))
            else:
                logger.error("Download failed for %s: %s", url, e)
            last_error = str(e) or "Download failed."
            status = "Failed"
        except Exception as e:
            logger.error("Download failed for %s", url, exc_info=e)
            last_error = str(e) or "Unexpected download failure."
            status = "Failed"
        finally:
            self._shutdown_job_executor(job, wait=not job.cancel_requested, cancel_futures=job.cancel_requested)
            self._close_job_sessions(job)
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            with self._jobs_lock:
                self._jobs.pop(job.initial_name, None)
            final_name = job.active_name or name
            final_status = self._tracking.finalize_job(job, final_name, status, error=last_error)
            logger.info("Download finished for %s with status=%s", final_name, final_status)
            self.status_changed.emit(final_name, final_status)
            self.download_finished.emit(final_name, final_status)
            next_jobs = self._queue.finish(job)
            for next_job in next_jobs:
                next_job_service = getattr(next_job, "service", None)
                if next_job_service is not None:
                    next_job_service._begin_job(next_job)

    def _ensure_job_name_available(self, job: DownloadJob, name: str):
        normalized_name = sanitize_webtoon_name(name or "")
        if not normalized_name:
            return
        if self._queue.has_conflict(name=normalized_name, exclude_job=job):
            raise ScraperError(f"'{normalized_name}' is already downloading.")

    def _format_chapter_number(self, chapter_number: float | None) -> str | None:
        if chapter_number is None:
            return None
        if float(chapter_number).is_integer():
            return str(int(chapter_number))
        return format(chapter_number, "g")

    def _emit_progress(self, job: DownloadJob, name: str, current: int, total: int):
        job.progress_current = max(0, int(current))
        job.progress_total = max(0, int(total))
        self.progress_changed.emit(name, job.progress_current, job.progress_total)

    def _emit_library_changed(self, name: str, *, force: bool = False, min_interval: float = 1.5):
        normalized = sanitize_webtoon_name(name or "")
        if not normalized:
            return
        now = time.monotonic()
        last = self._last_library_changed_at.get(normalized, 0.0)
        if not force and (now - last) < max(0.0, float(min_interval)):
            return
        self._last_library_changed_at[normalized] = now
        self.library_changed.emit(normalized)

    def _get_existing_chapters(self, webtoon_dir: str) -> set[str]:
        existing = set()
        if not os.path.isdir(webtoon_dir):
            return existing
        for folder in os.listdir(webtoon_dir):
            match = re.match(r"^Chapter (\d+(?:\.\d+)?)$", folder)
            if match:
                existing.add(match.group(1))
        return existing

    def _resolve_name(self, url: str) -> str:
        try:
            scraper = get_scraper(url)
            series_url = url if not scraper.is_chapter_url(url) else scraper.series_url_from_chapter_url(url)
            series = self._scraper_get_series_info(scraper, series_url)
            return sanitize_webtoon_name(series.title)
        except Exception:
            pass

        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = http_get(url, headers=headers, timeout=10, log_label="download-name-resolve")
            if self._looks_like_block_page(response):
                raise ScraperError("Blocked by anti-bot challenge")
            soup = BeautifulSoup(response.text, "html.parser")

            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = sanitize_webtoon_name(og_title["content"].strip())
                if title and not self._looks_like_placeholder_name(title):
                    return title

            if soup.title and soup.title.string:
                title = sanitize_webtoon_name(soup.title.string.strip())
                if title and not self._looks_like_placeholder_name(title):
                    return title
        except Exception as e:
            if self._is_expected_access_block(e):
                logger.info("Name resolve blocked for %s: %s", url, e)
            else:
                logger.warning("Name resolve fallback failed for %s", url, exc_info=e)

        slug = url.rstrip("/").split("/")[-1]
        return sanitize_webtoon_name(slug) or "download"

    def _download_file(self, job: DownloadJob, url: str, dest_path: str, headers: dict, retries: int = 2):
        url = url.strip().rstrip("\\").rstrip("/")
        last_error = None

        for attempt in range(retries + 1):
            if job.cancel_requested:
                raise DownloadCancelled()
            try:
                session = self._get_job_session(job)
                with http_get(url, session=session, headers=headers, stream=True, timeout=30, log_label="download-asset") as response:
                    response.raise_for_status()
                    with open(dest_path, "wb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 64):
                            if job.cancel_requested:
                                raise DownloadCancelled()
                            if chunk:
                                handle.write(chunk)
                return
            except DownloadCancelled:
                if os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                raise
            except Exception as e:
                last_error = e
                if os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                if attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue

        raise last_error

    def _download_page_asset(self, scraper, job: DownloadJob, url: str, dest_path: str, headers: dict):
        download_asset = getattr(type(scraper), "download_asset", None)
        if callable(download_asset) and download_asset is not BaseScraper.download_asset:
            ok = scraper.download_asset(url, dest_path)
            if ok is False:
                raise ScraperError(f"Asset download failed: {url}")
            return
        self._download_file(job, url, dest_path, headers)

    def _get_job_session(self, job: DownloadJob) -> requests.Session:
        session = getattr(job.session_local, "session", None)
        if session is not None:
            return session

        session = create_session(pool_connections=8, pool_maxsize=8)

        with job.sessions_lock:
            job.sessions.append(session)

        job.session_local.session = session
        return session

    def _close_job_sessions(self, job: DownloadJob):
        with job.sessions_lock:
            sessions = list(job.sessions)
            job.sessions.clear()

        for session in sessions:
            try:
                session.close()
            except Exception as e:
                logger.warning("Failed to close download session for %s", job.active_name or job.initial_name, exc_info=e)

        thread_local = getattr(job, "session_local", None)
        if thread_local is not None and hasattr(thread_local, "session"):
            delattr(thread_local, "session")

    def _get_job_executor(self, job: DownloadJob, max_workers: int = 8):
        with job.executor_lock:
            executor = job.executor
            if executor is None:
                from concurrent.futures import ThreadPoolExecutor

                executor = ThreadPoolExecutor(max_workers=max(1, max_workers))
                job.executor = executor
            return executor

    def _shutdown_job_executor(self, job: DownloadJob, *, wait: bool, cancel_futures: bool) -> None:
        with job.executor_lock:
            executor = job.executor
            job.executor = None

        if executor is None:
            return

        try:
            executor.shutdown(wait=wait, cancel_futures=cancel_futures)
        except Exception as e:
            logger.warning("Failed to stop executor for %s", job.active_name, exc_info=e)

    def _resume_payload_for_job(self, job: DownloadJob) -> dict:
        return {
            "url": str(job.url or ""),
            "output_path": str(job.output_path or ""),
            "preferred_name": str(job.preferred_name or job.active_name or ""),
            "chapter_urls": [str(chapter_url) for chapter_url in (job.chapter_urls or []) if chapter_url],
            "job_name": str(job.active_name or job.initial_name or ""),
        }

    def _clear_resume_payload(self, job: DownloadJob, final_name: str):
        self.history_store.clear_resume_payload(job.history_kind, final_name)
        initial_name = str(job.initial_name or "").strip()
        if initial_name and initial_name != final_name:
            self.history_store.clear_resume_payload(job.history_kind, initial_name)

    def _temp_root(self) -> str:
        return str(data_path("_download_temp"))

    def _custom_download(
        self,
        job: DownloadJob,
        url: str,
        output_path: str,
        target_name: str | None = None,
        selected_chapter_urls: list[str] | None = None,
    ):
        from concurrent.futures import as_completed

        scraper = get_scraper(url)
        scraper_session = self._get_job_session(job)
        headers = scraper.get_request_headers(url)
        url_type = "chapter" if scraper.is_chapter_url(url) else "series"

        selected_url_set = {
            str(chapter_url).rstrip("/")
            for chapter_url in (selected_chapter_urls or [])
            if chapter_url
        }

        if url_type == "chapter":
            series_url = scraper.series_url_from_chapter_url(url)
            series = self._scraper_get_series_info(scraper, series_url, session=scraper_session)
            chapter_list = [c for c in series.chapters if c.url.rstrip("/") == url.rstrip("/")]
            if not chapter_list:
                raise ScraperError(f"Could not match chapter URL: {url}")
        else:
            series = self._scraper_get_series_info(scraper, url, session=scraper_session)
            chapter_list = series.chapters

        if selected_url_set:
            chapter_list = [chapter for chapter in chapter_list if chapter.url.rstrip("/") in selected_url_set]
            if not chapter_list:
                raise ScraperError("None of the selected chapters could be matched for download.")

        series_name = sanitize_webtoon_name(target_name or series.title) or "download"
        previous_name = job.active_name or job.initial_name
        self.name_resolved.emit(previous_name, series_name)
        job.active_name = series_name
        logger.info("Custom scraper resolved series name %s", series_name)

        if getattr(series, "cover_url", None):
            cover_fetcher = getattr(scraper, "fetch_cover", None)
            fetcher = cover_fetcher if callable(cover_fetcher) else None
            ok, result = self.settings_store.set_from_url(
                series_name,
                series.cover_url,
                headers=headers,
                fetcher=fetcher,
            )
            if ok:
                self.thumbnail_resolved.emit(series_name, result)
        self._tracking.save_series_source_metadata(series_name, series, job.source_url)

        target_base = os.path.join(output_path, series_name)
        os.makedirs(target_base, exist_ok=True)

        content_type = str(getattr(scraper, "content_type", "webtoon") or "webtoon").strip().casefold()
        if content_type == "webnovel":
            return self._custom_webnovel_download(
                scraper,
                job,
                series,
                chapter_list,
                target_base,
                series_name,
                url_type,
            )

        existing = self._get_existing_chapters(target_base)
        had_existing_chapters = bool(existing)
        total_chapters = len(chapter_list)
        completed_chapters = 0
        any_chapter_succeeded = False
        latest_new_chapter_name = None

        if url_type == "series":
            completed_chapters = sum(
                1 for chapter in chapter_list
                if self._format_chapter_number(chapter.number) in existing
            )

        use_page_progress = content_type == "manga" and len(chapter_list) == 1
        if not use_page_progress:
            self._emit_progress(job, series_name, completed_chapters, total_chapters)

        for chapter in chapter_list:
            if job.cancel_requested:
                raise DownloadCancelled()

            chapter_num = self._format_chapter_number(chapter.number)
            if chapter_num is not None and chapter_num in existing and url_type == "series":
                logger.info("Skipping existing chapter %s for %s", chapter_num, series_name)
                continue

            try:
                pages = self._scraper_get_chapter_pages(scraper, chapter.url, session=scraper_session)
            except ScraperError as e:
                if url_type == "series":
                    logger.warning(
                        "Skipping chapter %s for %s because page extraction failed",
                        chapter.url,
                        series_name,
                        exc_info=e,
                    )
                    continue
                raise
            if not pages:
                continue

            if use_page_progress:
                self._emit_progress(job, series_name, 0, len(pages))

            if chapter_num is not None:
                chapter_dir_name = f"Chapter {chapter_num}"
            else:
                chapter_dir_name = sanitize_webtoon_name(chapter.title) or "Chapter"

            chapter_dir = os.path.join(target_base, chapter_dir_name)
            os.makedirs(chapter_dir, exist_ok=True)

            success_count = 0
            failure_count = 0
            executor = self._get_job_executor(job, max_workers=8)
            future_to_page = {}
            for page in pages:
                if job.cancel_requested:
                    raise DownloadCancelled()

                raw_url = page.image_url.split("?", 1)[0]
                ext = raw_url.rsplit(".", 1)[-1].lower() if "." in raw_url else "jpg"
                if f".{ext}" not in SUPPORTED_IMAGE_EXTENSIONS:
                    ext = "jpg"

                filename = f"{page.index:03d}.{ext}"
                dest_path = os.path.join(chapter_dir, filename)
                if os.path.exists(dest_path):
                    success_count += 1
                    continue

                try:
                    future = executor.submit(
                        self._download_page_asset,
                        scraper,
                        job,
                        page.image_url,
                        dest_path,
                        headers,
                    )
                except RuntimeError:
                    if job.cancel_requested:
                        shutil.rmtree(chapter_dir, ignore_errors=True)
                        raise DownloadCancelled()
                    raise
                future_to_page[future] = page.image_url

            if use_page_progress:
                self._emit_progress(job, series_name, success_count, len(pages))

            for future in as_completed(future_to_page):
                try:
                    future.result()
                    success_count += 1
                    if use_page_progress:
                        self._emit_progress(job, series_name, success_count, len(pages))
                except DownloadCancelled:
                    shutil.rmtree(chapter_dir, ignore_errors=True)
                    raise
                except CancelledError:
                    if job.cancel_requested:
                        shutil.rmtree(chapter_dir, ignore_errors=True)
                        raise DownloadCancelled()
                    failure_count += 1
                except Exception as e:
                    failure_count += 1
                    logger.warning(
                        "Page download failed for %s",
                        future_to_page[future],
                        exc_info=e,
                    )

            if success_count == 0:
                shutil.rmtree(chapter_dir, ignore_errors=True)
                raise ScraperError(f"Chapter download failed completely: {chapter.title}")

            any_chapter_succeeded = True
            latest_new_chapter_name = chapter_dir_name
            completed_chapters += 1
            if not use_page_progress:
                self._emit_progress(job, series_name, completed_chapters, total_chapters)
            self._emit_library_changed(series_name)

            if failure_count > 0:
                logger.warning(
                    "Chapter partially downloaded: %s (%d ok, %d failed)",
                    chapter.title,
                    success_count,
                    failure_count,
                )

        if job.cancel_requested:
            raise DownloadCancelled()
        if not any_chapter_succeeded and completed_chapters == 0:
            raise ScraperError("No chapters were downloaded")

        snapshot = build_webtoon_from_folder(output_path, series_name, self.settings_store)
        thumb_path = snapshot.thumbnail if snapshot is not None else None
        if thumb_path:
            self.thumbnail_resolved.emit(series_name, thumb_path)

        if latest_new_chapter_name:
            self.settings_store.set_latest_new_chapter(series_name, latest_new_chapter_name)

        return series_name

    def _custom_webnovel_download(
        self,
        scraper,
        job: DownloadJob,
        series,
        chapter_list: list,
        target_base: str,
        series_name: str,
        url_type: str,
    ) -> str:
        existing = self._get_existing_chapters(target_base)
        total_chapters = len(chapter_list)
        completed_chapters = 0
        any_chapter_succeeded = False
        latest_new_chapter_name = None

        if url_type == "series":
            completed_chapters = sum(
                1 for chapter in chapter_list
                if self._format_chapter_number(chapter.number) in existing
            )

        self._emit_progress(job, series_name, completed_chapters, total_chapters)

        for chapter in chapter_list:
            if job.cancel_requested:
                raise DownloadCancelled()

            chapter_num = self._format_chapter_number(chapter.number)
            if chapter_num is not None and chapter_num in existing and url_type == "series":
                logger.info("Skipping existing webnovel chapter %s for %s", chapter_num, series_name)
                continue

            content = self._scraper_get_chapter_content(scraper, chapter.url)
            if content is None:
                if url_type == "series":
                    logger.warning("Skipping empty chapter content for %s", chapter.url)
                    continue
                raise ScraperError(f"No chapter content found: {chapter.url}")

            if chapter_num is not None:
                chapter_dir_name = f"Chapter {chapter_num}"
            else:
                chapter_dir_name = sanitize_webtoon_name(chapter.title) or "Chapter"

            chapter_dir = os.path.join(target_base, chapter_dir_name)
            os.makedirs(chapter_dir, exist_ok=True)

            chapter_payload_path = os.path.join(chapter_dir, "chapter.json")
            html_body = str(getattr(content, "html", "") or "").strip()
            text_body = str(getattr(content, "text", "") or "").strip()
            title = str(getattr(content, "title", "") or chapter.title or chapter_dir_name).strip()

            if not html_body and not text_body:
                shutil.rmtree(chapter_dir, ignore_errors=True)
                if url_type == "series":
                    logger.warning("Skipping blank webnovel chapter %s", chapter.url)
                    continue
                raise ScraperError(f"Chapter content was blank: {chapter.url}")

            if not html_body:
                paragraphs = [
                    f"<p>{escape(line)}</p>"
                    for line in text_body.splitlines()
                    if line.strip()
                ]
                html_body = "\n".join(paragraphs)

            if not text_body:
                text_body = BeautifulSoup(html_body, "html.parser").get_text("\n", strip=True)

            payload = {
                "format": "webnovel_chapter_v1",
                "series_title": series_name,
                "chapter_title": title,
                "source_url": chapter.url,
                "html": html_body,
                "text": text_body.rstrip(),
            }
            with open(chapter_payload_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

            any_chapter_succeeded = True
            latest_new_chapter_name = chapter_dir_name
            completed_chapters += 1
            self._emit_progress(job, series_name, completed_chapters, total_chapters)
            self._emit_library_changed(series_name)

        if job.cancel_requested:
            raise DownloadCancelled()
        if not any_chapter_succeeded and completed_chapters == 0:
            raise ScraperError("No chapters were downloaded")

        snapshot = build_webtoon_from_folder(os.path.dirname(target_base), series_name, self.settings_store)
        thumb_path = snapshot.thumbnail if snapshot is not None else None
        if thumb_path:
            self.thumbnail_resolved.emit(series_name, thumb_path)

        if latest_new_chapter_name:
            self.settings_store.set_latest_new_chapter(series_name, latest_new_chapter_name)

        return series_name

    def _build_webnovel_chapter_document(self, series_name: str, chapter_title: str, html_body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(series_name)} - {escape(chapter_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6efe5;
      --panel: #fffaf4;
      --text: #2d221d;
      --muted: #7b675c;
      --line: rgba(82, 54, 45, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px 18px 48px;
      background: linear-gradient(180deg, #efe3d3 0%, var(--bg) 100%);
      color: var(--text);
      font: 18px/1.75 Georgia, 'Times New Roman', serif;
    }}
    main {{
      max-width: 860px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 28px 24px;
      box-shadow: 0 18px 48px rgba(61, 39, 31, 0.08);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 32px;
      line-height: 1.2;
    }}
    h2 {{
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 16px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    p {{ margin: 0 0 1.1em; }}
    br + br {{ display: block; content: ""; margin-top: 0.9em; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(chapter_title)}</h1>
    <h2>{escape(series_name)}</h2>
    {html_body}
  </main>
</body>
</html>
"""

    def _gallery_dl_download(self, job: DownloadJob, url: str, output_path: str, name: str):
        os.makedirs(job.temp_dir, exist_ok=True)
        logger.info("Starting gallery-dl download for %s into %s", name, job.temp_dir)

        url_type = detect_url_type(url)
        target_base = os.path.join(output_path, name)
        existing = self._get_existing_chapters(target_base)
        had_existing_chapters = bool(existing)
        cmd = ["gallery-dl", "--verbose", "-D", job.temp_dir]
        missing_chapters = []

        if url_type == "series":
            if existing:
                existing_str = ", ".join(str(e) for e in sorted(existing))
                cmd += ["--filter", f"episode_no not in [{existing_str}]"]
            guessed_last_chapter = self._guess_gallery_dl_last_chapter(url)
            if guessed_last_chapter is not None and guessed_last_chapter > 0:
                missing_chapters = sorted(set(range(1, guessed_last_chapter + 1)) - set(existing))
                if missing_chapters:
                    self._emit_progress(job, name, 0, len(missing_chapters))
        else:
            episode_no = extract_episode_number(url)
            missing_chapters = [episode_no] if episode_no is not None else [1]
            self._emit_progress(job, name, 0, 1)

        cmd.append(url)

        job.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
        )

        def watch_progress():
            last_current = -1
            last_total = -1

            while job.process and job.process.poll() is None:
                try:
                    if missing_chapters:
                        total = len(missing_chapters)
                        temp_numbers = self._chapter_numbers_in_temp_dir(job.temp_dir)
                        current = sum(1 for chapter in missing_chapters if chapter in temp_numbers)
                        if current != last_current or total != last_total:
                            self._emit_progress(job, name, current, total)
                            last_current = current
                            last_total = total
                except Exception as e:
                    logger.warning("Progress watcher error", exc_info=e)
                time.sleep(0.4)

        watcher_thread = threading.Thread(target=watch_progress, daemon=True)
        watcher_thread.start()

        if job.process.stdout is not None:
            for line in job.process.stdout:
                logger.info("gallery-dl: %s", line.strip())

        job.process.wait()

        if job.cancel_requested:
            raise DownloadCancelled()
        if job.process.returncode != 0:
            raise RuntimeError("gallery-dl exited with a non-zero status")

        all_files = sorted(
            f for f in os.listdir(job.temp_dir)
            if os.path.isfile(os.path.join(job.temp_dir, f))
        )

        if not all_files:
            return name

        os.makedirs(target_base, exist_ok=True)
        completed_now = set()
        latest_new_chapter_name = None
        if url_type == "chapter":
            episode_no = extract_episode_number(url) or 1
            chapter_dir = os.path.join(target_base, f"Chapter {episode_no}")
            os.makedirs(chapter_dir, exist_ok=True)
            for filename in all_files:
                src = os.path.join(job.temp_dir, filename)
                if os.path.isfile(src):
                    shutil.move(src, os.path.join(chapter_dir, filename))
            completed_now.add(episode_no)
            latest_new_chapter_name = f"Chapter {episode_no}"
        else:
            for filename in all_files:
                match = re.match(r"^(\d+)", filename)
                if not match:
                    continue
                chapter_num = int(match.group(1))
                chapter_dir = os.path.join(target_base, f"Chapter {chapter_num}")
                os.makedirs(chapter_dir, exist_ok=True)
                src = os.path.join(job.temp_dir, filename)
                if os.path.isfile(src):
                    shutil.move(src, os.path.join(chapter_dir, filename))
                completed_now.add(chapter_num)
                latest_new_chapter_name = f"Chapter {chapter_num}"

        if missing_chapters:
            final_current = sum(1 for chapter in missing_chapters if chapter in completed_now)
            self._emit_progress(job, name, final_current, len(missing_chapters))

        snapshot = build_webtoon_from_folder(output_path, name, self.settings_store)
        thumb_path = snapshot.thumbnail if snapshot is not None else None
        if thumb_path:
            self.thumbnail_resolved.emit(name, thumb_path)

        if latest_new_chapter_name:
            self.settings_store.set_latest_new_chapter(name, latest_new_chapter_name)

        return name

    def _preferred_thumbnail_for(self, webtoon_name: str) -> str | None:
        return preferred_thumbnail_path(webtoon_name, self.settings_store)

    def build_webtoon_from_folder(self, library_path: str, webtoon_name: str):
        return build_webtoon_from_folder(library_path, webtoon_name, self.settings_store)

    def preferred_thumbnail_for(self, webtoon_name: str) -> str | None:
        return self._preferred_thumbnail_for(webtoon_name)

    def _guess_gallery_dl_last_chapter(self, url: str) -> int | None:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = http_get(url, headers=headers, timeout=15, log_label="gallery-last-chapter")
            response.raise_for_status()
            html = response.text

            candidates = []
            for match in re.finditer(r'episode[_\- ]?no["\':\s=]+(\d+)', html, re.IGNORECASE):
                candidates.append(int(match.group(1)))
            for match in re.finditer(r"chapter[_\- ]?(\d+)", html, re.IGNORECASE):
                candidates.append(int(match.group(1)))
            for match in re.finditer(r"/chapter[-/ ]?(\d+)", html, re.IGNORECASE):
                candidates.append(int(match.group(1)))

            og_url = re.search(
                r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            if og_url:
                for match in re.finditer(r"chapter[-/ ]?(\d+)", og_url.group(1), re.IGNORECASE):
                    candidates.append(int(match.group(1)))

            if candidates:
                return max(candidates)
        except Exception as e:
            logger.warning("Last chapter guess failed from HTML for %s", url, exc_info=e)

        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            if "episode_no" in qs:
                return int(qs["episode_no"][0])

            path_matches = re.findall(r"chapter[-/ ]?(\d+)", parsed.path, re.IGNORECASE)
            if path_matches:
                return max(int(value) for value in path_matches)
        except Exception as e:
            logger.warning("Last chapter guess failed from URL for %s", url, exc_info=e)

        return None

    def _chapter_numbers_in_temp_dir(self, temp_dir: str) -> set[int]:
        found = set()
        if not os.path.isdir(temp_dir):
            return found

        for filename in os.listdir(temp_dir):
            full = os.path.join(temp_dir, filename)
            if not os.path.isfile(full):
                continue
            match = re.match(r"^(\d+)", filename)
            if match:
                found.add(int(match.group(1)))

        return found

    def _looks_like_block_page(self, response) -> bool:
        status_code = getattr(response, "status_code", 0)
        if status_code == 403:
            return True
        text = str(getattr(response, "text", "") or "").casefold()
        return "just a moment" in text and "cloudflare" in text

    def _looks_like_placeholder_name(self, value: str) -> bool:
        normalized = " ".join(str(value or "").casefold().split())
        return normalized in {"just a moment...", "just a moment", "attention required!"}

    def _is_expected_access_block(self, error: Exception) -> bool:
        text = " ".join(str(error or "").casefold().split())
        if not text:
            return False
        markers = (
            "cloudflare",
            "anti-bot",
            "blocked the request",
            "blocked the catalog request",
            "blocked the chapter request",
        )
        return any(marker in text for marker in markers)

    def _scraper_get_series_info(self, scraper, url: str, session: requests.Session | None = None):
        if session is not None:
            try:
                return scraper.get_series_info(url, session=session)
            except TypeError:
                pass
        return scraper.get_series_info(url)

    def _scraper_get_chapter_pages(self, scraper, chapter_url: str, session: requests.Session | None = None):
        if session is not None:
            try:
                return scraper.get_chapter_pages(chapter_url, session=session)
            except TypeError:
                pass
        return scraper.get_chapter_pages(chapter_url)

    def _scraper_get_chapter_content(self, scraper, chapter_url: str):
        getter = getattr(scraper, "get_chapter_content", None)
        if not callable(getter):
            raise ScraperError("This scraper does not support text chapter downloads.")

        parser = getattr(scraper, "parse_chapter_content_html", None)
        site_name = str(getattr(scraper, "site_name", "") or "").strip()
        browser_ready = self.browser_fetcher is not None and callable(parser) and bool(site_name)

        if browser_ready and site_name in self._browser_fetch_sites:
            logger.info("Using browser-backed chapter fetch for %s", chapter_url)
            html = self.browser_fetcher.fetch_html(chapter_url, site_name)
            return parser(chapter_url, html)

        try:
            return getter(chapter_url)
        except ScraperError as exc:
            if browser_ready and self._is_expected_access_block(exc):
                logger.info("Attempting browser-backed chapter fetch for %s", chapter_url)
                try:
                    html = self.browser_fetcher.fetch_html(chapter_url, site_name)
                    self._browser_fetch_sites.add(site_name)
                    logger.info("Browser-backed chapter mode enabled for site=%s", site_name)
                    return parser(chapter_url, html)
                except Exception as browser_exc:
                    logger.warning("Browser-backed chapter fetch failed for %s", chapter_url, exc_info=browser_exc)
            raise




