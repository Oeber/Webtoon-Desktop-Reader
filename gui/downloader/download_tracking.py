from core.app_logging import get_logger
from scrapers.registry import get_scraper


logger = get_logger(__name__)


class DownloadTrackingStore:

    def __init__(self, settings_store, history_store):
        self.settings_store = settings_store
        self.history_store = history_store

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
            )
        except Exception as exc:
            logger.warning("Failed to save source metadata for '%s'", webtoon_name, exc_info=exc)

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
