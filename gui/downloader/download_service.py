import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from PySide6.QtCore import QObject, Signal

from core.app_logging import get_logger
from core.app_paths import data_path
from stores.download_history_store import get_instance as get_download_history
from gui.downloader.helpers import (
    SUPPORTED_IMAGE_EXTENSIONS,
    detect_url_type,
    extract_episode_number,
    sanitize_webtoon_name,
)
from library.library_manager import build_webtoon_from_folder, preferred_thumbnail_path
from scrapers.base import ScraperError
from scrapers.registry import get_scraper
from stores.webtoon_settings_store import get_instance as get_webtoon_settings

logger = get_logger(__name__)


class DownloadCancelled(Exception):
    pass


class DownloadJob:

    def __init__(self, initial_name: str, source_url: str):
        self.initial_name = initial_name
        self.active_name = initial_name
        self.source_url = source_url
        self.cancel_requested = False
        self.process = None
        self.thread = None
        self.executor = None
        self.progress_current = 0
        self.progress_total = 0
        self.temp_dir = str(data_path("_download_temp", f"job-{uuid.uuid4().hex}"))
        self.session_local = threading.local()
        self.sessions: list[requests.Session] = []
        self.sessions_lock = threading.Lock()


class DownloadService(QObject):
    status_changed = Signal(str, str)
    name_resolved = Signal(str, str)
    progress_changed = Signal(str, int, int)
    thumbnail_resolved = Signal(str, str)
    download_started = Signal(str)
    download_finished = Signal(str, str)
    library_changed = Signal(str)

    def __init__(self, parent=None, history_kind: str = "download"):
        super().__init__(parent)
        self.settings_store = get_webtoon_settings()
        self.history_store = get_download_history()
        self.history_kind = history_kind
        self._jobs: dict[str, DownloadJob] = {}
        self._jobs_lock = threading.Lock()

        temp_root = data_path("_download_temp")
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
    ) -> str | None:
        url = (url or "").strip().strip("'\"")
        if not url:
            return "Please enter a URL."

        initial_name = (
            sanitize_webtoon_name(job_name)
            or sanitize_webtoon_name(preferred_name)
            or sanitize_webtoon_name(url.rstrip("/").split("/")[-1])
            or "download"
        )
        if self.has_active_download(initial_name):
            return f"'{initial_name}' is already downloading."

        logger.info("Starting download url=%s preferred_name=%s output=%s", url, preferred_name, output_path)
        job = DownloadJob(initial_name, self._normalized_source_url(url))
        self.history_store.upsert(self.history_kind, job.initial_name, "Downloading", job.source_url)
        with self._jobs_lock:
            self._jobs[job.initial_name] = job
        self.download_started.emit(job.initial_name)

        thread = threading.Thread(
            target=self._run_download,
            args=(job, url, output_path, preferred_name),
            daemon=True,
        )
        job.thread = thread
        thread.start()
        return None

    def cancel_download(self, name: str | None = None):
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        if not jobs:
            return
        for job in jobs:
            if name and job.active_name != sanitize_webtoon_name(name):
                continue
            logger.warning("Cancelling active download for %s", job.active_name)
            job.cancel_requested = True
            if job.process and job.process.poll() is None:
                job.process.terminate()
            if job.executor is not None:
                try:
                    job.executor.shutdown(wait=False, cancel_futures=True)
                except Exception as e:
                    logger.warning("Failed to stop executor for %s", job.active_name, exc_info=e)

    def shutdown(self, wait_timeout: float = 5.0):
        logger.info("Shutting down DownloadService")
        self._save_active_source_urls()
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

    def _run_download(self, job: DownloadJob, url: str, output_path: str, preferred_name: str | None):
        name = sanitize_webtoon_name(preferred_name) or job.initial_name or "download"
        status = "Failed"

        try:
            os.makedirs(job.temp_dir, exist_ok=True)

            if preferred_name:
                self.history_store.rename(self.history_kind, job.initial_name, name, job.source_url, "Downloading")
                self.name_resolved.emit(job.initial_name, name)
            else:
                name = self._resolve_name(url)
                self.history_store.rename(self.history_kind, job.initial_name, name, job.source_url, "Downloading")
                self.name_resolved.emit(job.initial_name, name)

            job.active_name = name
            logger.info("Resolved download name: %s", name)

            try:
                scraper = get_scraper(url)
            except Exception as e:
                logger.warning("Custom scraper resolution failed for %s", url, exc_info=e)
                scraper = None

            if scraper is not None:
                logger.info("Using custom scraper for %s", url)
                saved_name = self._custom_download(job, url, output_path, target_name=preferred_name)
            else:
                logger.info("Using gallery-dl fallback for %s", url)
                saved_name = self._gallery_dl_download(job, url, output_path, name)

            self._save_source_url(saved_name, job.source_url)
            status = "Completed"
            self.library_changed.emit(saved_name)
        except DownloadCancelled:
            self._save_source_url(job.active_name or name, job.source_url)
            status = "Cancelled"
        except FileNotFoundError:
            logger.error("Download failed because required file/tool was missing")
            status = "Failed"
        except Exception as e:
            logger.error("Download failed for %s", url, exc_info=e)
            status = "Failed"
        finally:
            self._close_job_sessions(job)
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            with self._jobs_lock:
                self._jobs.pop(job.initial_name, None)
            self.history_store.upsert(self.history_kind, job.active_name or name, status, job.source_url)
            logger.info("Download finished for %s with status=%s", job.active_name or name, status)
            self.status_changed.emit(job.active_name or name, status)
            self.download_finished.emit(job.active_name or name, status)

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
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return sanitize_webtoon_name(og_title["content"].strip())

            if soup.title and soup.title.string:
                return sanitize_webtoon_name(soup.title.string.strip())
        except Exception as e:
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
                with session.get(url, headers=headers, stream=True, timeout=30) as response:
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

    def _get_job_session(self, job: DownloadJob) -> requests.Session:
        session = getattr(job.session_local, "session", None)
        if session is not None:
            return session

        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

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

    def _save_source_url(self, webtoon_name: str, source_url: str):
        if not webtoon_name or not source_url:
            return
        try:
            self.settings_store.set_source_url(webtoon_name, source_url)
        except Exception as e:
            logger.warning("Failed to save source URL for '%s'", webtoon_name, exc_info=e)

    def _save_active_source_urls(self):
        with self._jobs_lock:
            jobs = list(self._jobs.values())

        for job in jobs:
            name = sanitize_webtoon_name(job.active_name) or sanitize_webtoon_name(job.initial_name)
            if not name or not job.source_url:
                continue
            self._save_source_url(name, job.source_url)

    def _normalized_source_url(self, url: str) -> str:
        normalized_url = (url or "").strip()
        if not normalized_url:
            return normalized_url

        try:
            scraper = get_scraper(normalized_url)
        except Exception as e:
            logger.warning("Source URL normalization scraper lookup failed for %s", normalized_url, exc_info=e)
            scraper = None

        if scraper is not None and scraper.is_chapter_url(normalized_url):
            try:
                return scraper.series_url_from_chapter_url(normalized_url)
            except Exception as e:
                logger.warning("Failed to normalize chapter URL %s", normalized_url, exc_info=e)

        return normalized_url

    def _custom_download(self, job: DownloadJob, url: str, output_path: str, target_name: str | None = None):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        scraper = get_scraper(url)
        scraper_session = self._get_job_session(job)
        headers = scraper.get_request_headers(url)
        url_type = "chapter" if scraper.is_chapter_url(url) else "series"

        if url_type == "chapter":
            series_url = scraper.series_url_from_chapter_url(url)
            series = self._scraper_get_series_info(scraper, series_url, session=scraper_session)
            chapter_list = [c for c in series.chapters if c.url.rstrip("/") == url.rstrip("/")]
            if not chapter_list:
                raise ScraperError(f"Could not match chapter URL: {url}")
        else:
            series = self._scraper_get_series_info(scraper, url, session=scraper_session)
            chapter_list = series.chapters

        series_name = sanitize_webtoon_name(target_name or series.title) or "download"
        previous_name = job.active_name or job.initial_name
        self.name_resolved.emit(previous_name, series_name)
        job.active_name = series_name
        logger.info("Custom scraper resolved series name %s", series_name)

        if getattr(series, "cover_url", None):
            ok, result = self.settings_store.set_from_url(series_name, series.cover_url)
            if ok:
                self.thumbnail_resolved.emit(series_name, result)

        target_base = os.path.join(output_path, series_name)
        os.makedirs(target_base, exist_ok=True)

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

            if chapter_num is not None:
                chapter_dir_name = f"Chapter {chapter_num}"
            else:
                chapter_dir_name = sanitize_webtoon_name(chapter.title) or "Chapter"

            chapter_dir = os.path.join(target_base, chapter_dir_name)
            os.makedirs(chapter_dir, exist_ok=True)

            success_count = 0
            failure_count = 0
            max_workers = min(8, max(1, len(pages)))

            executor = ThreadPoolExecutor(max_workers=max_workers)
            job.executor = executor
            try:
                future_to_page = {}
                for page in pages:
                    raw_url = page.image_url.split("?", 1)[0]
                    ext = raw_url.rsplit(".", 1)[-1].lower() if "." in raw_url else "jpg"
                    if f".{ext}" not in SUPPORTED_IMAGE_EXTENSIONS:
                        ext = "jpg"

                    filename = f"{page.index:03d}.{ext}"
                    dest_path = os.path.join(chapter_dir, filename)
                    if os.path.exists(dest_path):
                        success_count += 1
                        continue

                    future = executor.submit(self._download_file, job, page.image_url, dest_path, headers)
                    future_to_page[future] = page.image_url

                for future in as_completed(future_to_page):
                    try:
                        future.result()
                        success_count += 1
                    except DownloadCancelled:
                        shutil.rmtree(chapter_dir, ignore_errors=True)
                        raise
                    except Exception as e:
                        failure_count += 1
                        logger.warning(
                            "Page download failed for %s",
                            future_to_page[future],
                            exc_info=e,
                        )
            finally:
                try:
                    executor.shutdown(wait=not job.cancel_requested, cancel_futures=job.cancel_requested)
                finally:
                    if job.executor is executor:
                        job.executor = None

            if success_count == 0:
                shutil.rmtree(chapter_dir, ignore_errors=True)
                raise ScraperError(f"Chapter download failed completely: {chapter.title}")

            any_chapter_succeeded = True
            latest_new_chapter_name = chapter_dir_name
            completed_chapters += 1
            self._emit_progress(job, series_name, completed_chapters, total_chapters)
            self.library_changed.emit(series_name)

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
            response = requests.get(url, headers=headers, timeout=15)
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
