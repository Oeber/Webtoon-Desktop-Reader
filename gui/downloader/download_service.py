import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from PySide6.QtCore import QObject, Signal

from app_logging import get_logger
from gui.downloader.helpers import (
    SUPPORTED_IMAGE_EXTENSIONS,
    chapter_path_sort_key,
    chapter_sort_key,
    detect_url_type,
    extract_episode_number,
    sanitize_webtoon_name,
)
from scrapers.base import ScraperError
from scrapers.registry import get_scraper
from webtoon_settings_store import get_instance as get_webtoon_settings

logger = get_logger(__name__)


class DownloadCancelled(Exception):
    pass


class DownloadJob:

    def __init__(self, initial_name: str):
        self.initial_name = initial_name
        self.active_name = initial_name
        self.cancel_requested = False
        self.process = None
        self.temp_dir = os.path.join("data", "_download_temp", f"job-{uuid.uuid4().hex}")


class DownloadService(QObject):
    status_changed = Signal(str, str)
    name_resolved = Signal(str, str)
    progress_changed = Signal(str, int, int)
    thumbnail_resolved = Signal(str, str)
    download_started = Signal(str)
    download_finished = Signal(str, str)
    library_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings_store = get_webtoon_settings()
        self._jobs: dict[str, DownloadJob] = {}
        self._jobs_lock = threading.Lock()

        temp_root = os.path.join("data", "_download_temp")
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
        job = DownloadJob(initial_name)
        with self._jobs_lock:
            self._jobs[job.initial_name] = job
        self.download_started.emit(job.initial_name)

        thread = threading.Thread(
            target=self._run_download,
            args=(job, url, output_path, preferred_name),
            daemon=True,
        )
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

    def _run_download(self, job: DownloadJob, url: str, output_path: str, preferred_name: str | None):
        name = sanitize_webtoon_name(preferred_name) or job.initial_name or "download"
        status = "Failed"

        try:
            os.makedirs(job.temp_dir, exist_ok=True)

            if preferred_name:
                self.name_resolved.emit(job.initial_name, name)
            else:
                name = self._resolve_name(url)
                self.name_resolved.emit(job.initial_name, name)

            job.active_name = name
            logger.info("Resolved download name: %s", name)

            try:
                scraper = get_scraper(url)
            except Exception:
                scraper = None

            if scraper is not None:
                logger.info("Using custom scraper for %s", url)
                saved_name = self._custom_download(job, url, output_path, target_name=preferred_name)
            else:
                logger.info("Using gallery-dl fallback for %s", url)
                saved_name = self._gallery_dl_download(job, url, output_path, name)

            self._save_source_url(saved_name, url)
            status = "Completed"
            self.library_changed.emit(saved_name)
        except DownloadCancelled:
            status = "Cancelled"
        except FileNotFoundError:
            logger.error("Download failed because required file/tool was missing")
            status = "Failed"
        except Exception as e:
            logger.error("Download failed for %s", url, exc_info=e)
            status = "Failed"
        finally:
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            with self._jobs_lock:
                self._jobs.pop(job.initial_name, None)
            logger.info("Download finished for %s with status=%s", job.active_name or name, status)
            self.status_changed.emit(job.active_name or name, status)
            self.download_finished.emit(job.active_name or name, status)

    def _get_existing_chapters(self, webtoon_dir: str) -> set[int]:
        existing = set()
        if not os.path.isdir(webtoon_dir):
            return existing
        for folder in os.listdir(webtoon_dir):
            match = re.match(r"^Chapter (\d+)$", folder)
            if match:
                existing.add(int(match.group(1)))
        return existing

    def _resolve_name(self, url: str) -> str:
        try:
            scraper = get_scraper(url)
            series = scraper.get_series_info(
                url if not scraper.is_chapter_url(url) else scraper.series_url_from_chapter_url(url)
            )
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
                with requests.get(url, headers=headers, stream=True, timeout=30) as response:
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

    def _save_source_url(self, webtoon_name: str, source_url: str):
        if not webtoon_name or not source_url:
            return
        try:
            self.settings_store.set_source_url(webtoon_name, source_url)
        except Exception as e:
            logger.warning("Failed to save source URL for '%s'", webtoon_name, exc_info=e)

    def _custom_download(self, job: DownloadJob, url: str, output_path: str, target_name: str | None = None):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        scraper = get_scraper(url)
        headers = scraper.get_request_headers(url)
        url_type = "chapter" if scraper.is_chapter_url(url) else "series"

        if url_type == "chapter":
            series_url = scraper.series_url_from_chapter_url(url)
            series = scraper.get_series_info(series_url)
            chapter_list = [c for c in series.chapters if c.url.rstrip("/") == url.rstrip("/")]
            if not chapter_list:
                raise ScraperError(f"Could not match chapter URL: {url}")
        else:
            series = scraper.get_series_info(url)
            chapter_list = series.chapters

        series_name = sanitize_webtoon_name(target_name or series.title) or "download"
        self.name_resolved.emit(job.initial_name, series_name)
        job.active_name = series_name
        logger.info("Custom scraper resolved series name %s", series_name)

        if getattr(series, "cover_url", None):
            ok, result = self.settings_store.set_from_url(series_name, series.cover_url)
            if ok:
                self.thumbnail_resolved.emit(series_name, result)

        target_base = os.path.join(output_path, series_name)
        os.makedirs(target_base, exist_ok=True)

        existing = self._get_existing_chapters(target_base)
        total_chapters = len(chapter_list)
        completed_chapters = 0
        any_chapter_succeeded = False

        if url_type == "series":
            completed_chapters = sum(
                1 for chapter in chapter_list
                if chapter.number is not None and int(chapter.number) in existing
            )

        self.progress_changed.emit(series_name, completed_chapters, total_chapters)

        for chapter in chapter_list:
            if job.cancel_requested:
                raise DownloadCancelled()

            chapter_num = int(chapter.number) if chapter.number is not None else None
            if chapter_num is not None and chapter_num in existing and url_type == "series":
                logger.info("Skipping existing chapter %s for %s", chapter_num, series_name)
                continue

            pages = scraper.get_chapter_pages(chapter.url)
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

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                        executor.shutdown(wait=False, cancel_futures=True)
                        shutil.rmtree(chapter_dir, ignore_errors=True)
                        raise
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
            completed_chapters += 1
            self.progress_changed.emit(series_name, completed_chapters, total_chapters)

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

        thumb_path = self._preferred_thumbnail_for(series_name)
        if not thumb_path:
            thumb_path = self._create_auto_thumbnail_from_webtoon_folder(series_name, target_base)
            if thumb_path:
                self.thumbnail_resolved.emit(series_name, thumb_path)

        return series_name

    def _gallery_dl_download(self, job: DownloadJob, url: str, output_path: str, name: str):
        os.makedirs(job.temp_dir, exist_ok=True)
        logger.info("Starting gallery-dl download for %s into %s", name, job.temp_dir)

        url_type = detect_url_type(url)
        target_base = os.path.join(output_path, name)
        cmd = ["gallery-dl", "--verbose", "-D", job.temp_dir]
        missing_chapters = []

        if url_type == "series":
            existing = self._get_existing_chapters(target_base)
            if existing:
                existing_str = ", ".join(str(e) for e in sorted(existing))
                cmd += ["--filter", f"episode_no not in [{existing_str}]"]
            guessed_last_chapter = self._guess_gallery_dl_last_chapter(url)
            if guessed_last_chapter is not None and guessed_last_chapter > 0:
                missing_chapters = sorted(set(range(1, guessed_last_chapter + 1)) - set(existing))
                if missing_chapters:
                    self.progress_changed.emit(name, 0, len(missing_chapters))
        else:
            episode_no = extract_episode_number(url)
            missing_chapters = [episode_no] if episode_no is not None else [1]
            self.progress_changed.emit(name, 0, 1)

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
                            self.progress_changed.emit(name, current, total)
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

        if url_type == "chapter":
            episode_no = extract_episode_number(url) or 1
            chapter_dir = os.path.join(target_base, f"Chapter {episode_no}")
            os.makedirs(chapter_dir, exist_ok=True)
            for filename in all_files:
                src = os.path.join(job.temp_dir, filename)
                if os.path.isfile(src):
                    shutil.move(src, os.path.join(chapter_dir, filename))
            completed_now.add(episode_no)
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

        if missing_chapters:
            final_current = sum(1 for chapter in missing_chapters if chapter in completed_now)
            self.progress_changed.emit(name, final_current, len(missing_chapters))

        thumb_path = self._create_auto_thumbnail_from_webtoon_folder(name, target_base)
        if thumb_path:
            self.thumbnail_resolved.emit(name, thumb_path)

        return name

    def _preferred_thumbnail_for(self, webtoon_name: str) -> str | None:
        custom = self.settings_store.get(webtoon_name)
        if custom and os.path.exists(custom):
            return custom

        auto_path = os.path.join("data", "thumbnails", f"{webtoon_name}.jpg")
        if os.path.exists(auto_path):
            return auto_path
        return None

    def _auto_thumbnail_path(self, webtoon_name: str) -> str:
        os.makedirs(os.path.join("data", "thumbnails"), exist_ok=True)
        return os.path.join("data", "thumbnails", f"{webtoon_name}.jpg")

    def _create_auto_thumbnail_from_webtoon_folder(self, webtoon_name: str, webtoon_dir: str) -> str | None:
        try:
            from PIL import Image

            if not os.path.isdir(webtoon_dir):
                return None

            chapter_dirs = [
                os.path.join(webtoon_dir, folder)
                for folder in os.listdir(webtoon_dir)
                if os.path.isdir(os.path.join(webtoon_dir, folder))
            ]
            if not chapter_dirs:
                return None

            chapter_dirs.sort(key=chapter_path_sort_key)
            first_chapter = chapter_dirs[0]
            image_files = [
                os.path.join(first_chapter, filename)
                for filename in sorted(os.listdir(first_chapter))
                if os.path.isfile(os.path.join(first_chapter, filename))
                and filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
            ]
            if not image_files:
                return None

            src = image_files[0]
            dest = self._auto_thumbnail_path(webtoon_name)
            with Image.open(src) as image:
                rgb = image.convert("RGB")
                rgb.thumbnail((360, 540))
                canvas = Image.new("RGB", (360, 540), (18, 18, 18))
                x = (360 - rgb.width) // 2
                y = (540 - rgb.height) // 2
                canvas.paste(rgb, (x, y))
                canvas.save(dest, "JPEG", quality=90)
            return dest if os.path.exists(dest) else None
        except Exception as e:
            logger.warning("Auto thumbnail generation failed for '%s'", webtoon_name, exc_info=e)
            return None

    def build_webtoon_from_folder(self, library_path: str, webtoon_name: str):
        webtoon_dir = os.path.join(library_path, webtoon_name)
        if not os.path.isdir(webtoon_dir):
            return None

        chapter_dirs = [
            folder for folder in os.listdir(webtoon_dir)
            if os.path.isdir(os.path.join(webtoon_dir, folder))
        ]

        chapter_dirs.sort(key=chapter_sort_key)
        thumb = self._preferred_thumbnail_for(webtoon_name)
        if not thumb:
            thumb = self._create_auto_thumbnail_from_webtoon_folder(webtoon_name, webtoon_dir)

        return SimpleNamespace(
            name=webtoon_name,
            path=webtoon_dir,
            thumbnail=thumb or "",
            chapters=chapter_dirs,
        )

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
