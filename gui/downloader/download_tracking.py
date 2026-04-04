from core.app_logging import get_logger
from core.chapter_identity import build_remote_chapter_key
from stores.chapter_ref_store import get_instance as get_chapter_ref_store
from stores.progress_store import get_instance as get_progress_store
from stores.tracked_titles_store import get_instance as get_tracked_titles_store
from scrapers.registry import get_scraper
from gui.downloader.helpers import sanitize_webtoon_name


logger = get_logger(__name__)


class DownloadTrackingStore:

    def __init__(self, settings_store, history_store):
        self.settings_store = settings_store
        self.history_store = history_store
        self.chapter_ref_store = get_chapter_ref_store()
        self.progress_store = get_progress_store()
        self.tracked_titles_store = get_tracked_titles_store()

    def persist_queued(self, job) -> None:
        self.history_store.upsert(job.history_kind, job.initial_name, "Queued", job.source_url)
        self.history_store.set_resume_payload(
            job.history_kind,
            job.initial_name,
            self._resume_payload_for_job(job),
            job.source_url,
        )

    def mark_shutdown_resume(self, job) -> None:
        job.resume_after_restart = True
        self.history_store.set_resume_payload(
            job.history_kind,
            job.active_name or job.initial_name,
            self._resume_payload_for_job(job),
            job.source_url,
        )

    def load_resumable_entries(self, history_kind: str) -> list[dict]:
        return self.history_store.list_resumable_entries(history_kind)

    def clear_invalid_resume(self, history_kind: str, name: str) -> None:
        self.history_store.clear_resume_payload(history_kind, name)

    def mark_downloading(self, job) -> None:
        self.history_store.upsert(job.history_kind, job.active_name or job.initial_name, "Downloading", job.source_url)

    def rename_history(self, job, old_name: str, new_name: str, status: str = "Downloading") -> None:
        self.history_store.rename(job.history_kind, old_name, new_name, job.source_url, status)

    def finalize_queued_cancellation(self, job) -> str:
        final_name = job.active_name or job.initial_name
        if job.resume_after_restart:
            job.state = "Queued"
            self.history_store.upsert(job.history_kind, final_name, "Queued", job.source_url)
            self.history_store.set_resume_payload(
                job.history_kind,
                final_name,
                self._resume_payload_for_job(job),
                job.source_url,
            )
            return "Queued"

        job.state = "Cancelled"
        self.history_store.upsert(job.history_kind, final_name, "Cancelled", job.source_url)
        self.clear_resume_payload(job, final_name)
        return "Cancelled"

    def finalize_job(self, job, final_name: str, status: str, error: str = "") -> str:
        final_status = "Queued" if status == "Cancelled" and job.resume_after_restart else status
        job.state = final_status
        self.history_store.upsert(
            job.history_kind,
            final_name,
            final_status,
            job.source_url,
            last_error=error if final_status == "Failed" else "",
        )
        if final_status == "Queued":
            self.history_store.set_resume_payload(
                job.history_kind,
                final_name,
                self._resume_payload_for_job(job),
                job.source_url,
            )
        else:
            self.clear_resume_payload(job, final_name)
        return final_status

    def clear_resume_payload(self, job, final_name: str) -> None:
        self.history_store.clear_resume_payload(job.history_kind, final_name)
        initial_name = str(job.initial_name or "").strip()
        if initial_name and initial_name != final_name:
            self.history_store.clear_resume_payload(job.history_kind, initial_name)

    def save_source_url(self, webtoon_name: str, source_url: str) -> None:
        if not webtoon_name or not source_url:
            return
        try:
            self.settings_store.set_source_url(webtoon_name, source_url)
        except Exception as exc:
            logger.warning("Failed to save source URL for '%s'", webtoon_name, exc_info=exc)

    def save_series_source_metadata(self, webtoon_name: str, series, source_url: str) -> None:
        if not webtoon_name:
            return
        try:
            self.settings_store.save_source_metadata(
                webtoon_name,
                source_url=source_url or None,
                source_site=getattr(series, "site", None),
                source_series_id=getattr(series, "series_id", None),
                source_title=getattr(series, "title", None),
                content_type=getattr(series, "content_type", None),
            )
            self._bind_tracked_title_to_local(webtoon_name, series, source_url)
        except Exception as exc:
            logger.warning("Failed to save source metadata for '%s'", webtoon_name, exc_info=exc)

    def _bind_tracked_title_to_local(self, webtoon_name: str, series, source_url: str) -> None:
        tracked = self.tracked_titles_store.find_matching_title(
            site_name=str(getattr(series, "site", "") or "").strip(),
            series_id=str(getattr(series, "series_id", "") or "").strip(),
            source_url=str(source_url or getattr(series, "url", "") or "").strip(),
        )
        if not tracked:
            return

        track_id = str(tracked.get("track_id") or "").strip()
        if not track_id:
            return

        chapter_map = self.chapter_ref_store.bind_series_to_local(
            track_id,
            webtoon_name,
            series,
            local_name_builder=self._local_chapter_name,
        )
        site_name = str(getattr(series, "site", "") or tracked.get("site_name") or "").strip()
        series_id = str(getattr(series, "series_id", "") or tracked.get("series_id") or "").strip()
        for chapter in list(getattr(series, "chapters", []) or []):
            local_chapter = self._local_chapter_name(chapter)
            if not local_chapter:
                continue
            remote_key = build_remote_chapter_key(
                site_name,
                series_id,
                str(getattr(chapter, "id", "") or "").strip(),
                str(getattr(chapter, "url", "") or "").strip(),
            )
            chapter_map.setdefault(remote_key, local_chapter)

        promoted = self.progress_store.promote_remote_progress(webtoon_name, chapter_map)
        self.tracked_titles_store.bind_local_title(track_id, webtoon_name)
        logger.info(
            "Bound tracked title %s to local webtoon %s with %d promoted chapter progress rows",
            track_id,
            webtoon_name,
            promoted,
        )
    @staticmethod
    def _local_chapter_name(chapter) -> str:
        chapter_number = getattr(chapter, "number", None)
        if chapter_number is not None:
            try:
                numeric = float(chapter_number)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None:
                label = str(int(numeric)) if numeric.is_integer() else format(numeric, "g")
                return f"Chapter {label}"
        title = sanitize_webtoon_name(str(getattr(chapter, "title", "") or "").strip())
        return title or "Chapter"

    def save_active_source_urls(self, jobs: list) -> None:
        for job in jobs:
            name = str(job.active_name or job.initial_name or "").strip()
            if not name or not job.source_url:
                continue
            self.save_source_url(name, job.source_url)

    def normalized_source_url(self, url: str) -> str:
        normalized_url = (url or "").strip()
        if not normalized_url:
            return normalized_url

        try:
            scraper = get_scraper(normalized_url)
        except Exception as exc:
            logger.warning("Source URL normalization scraper lookup failed for %s", normalized_url, exc_info=exc)
            scraper = None

        if scraper is not None and scraper.is_chapter_url(normalized_url):
            try:
                return scraper.series_url_from_chapter_url(normalized_url)
            except Exception as exc:
                logger.warning("Failed to normalize chapter URL %s", normalized_url, exc_info=exc)

        return normalized_url

    def _resume_payload_for_job(self, job) -> dict:
        return {
            "url": str(job.url or ""),
            "output_path": str(job.output_path or ""),
            "preferred_name": str(job.preferred_name or job.active_name or ""),
            "chapter_urls": [str(chapter_url) for chapter_url in (job.chapter_urls or []) if chapter_url],
            "job_name": str(job.active_name or job.initial_name or ""),
        }





