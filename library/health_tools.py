from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core.app_logging import get_logger
from core.chapter_storage import chapter_has_text_payload, count_chapter_images, chapter_storage_path
from core.library_layout import list_library_entries, resolve_existing_webtoon_storage_path, resolve_webtoon_path
from library.library_manager import THUMB_FOLDER, build_webtoon_from_folder, preferred_thumbnail_path
from stores.db import get_connection


logger = get_logger(__name__)


@dataclass
class LibraryHealthReport:
    library_path: str
    series_folders: list[str] = field(default_factory=list)
    readable_titles: list[str] = field(default_factory=list)
    invalid_series_folders: list[str] = field(default_factory=list)
    duplicate_titles: list[str] = field(default_factory=list)
    titles_without_saved_source: list[str] = field(default_factory=list)
    titles_without_cached_cover: list[str] = field(default_factory=list)
    empty_chapter_folders: list[str] = field(default_factory=list)
    orphaned_settings: list[str] = field(default_factory=list)
    orphaned_progress: list[str] = field(default_factory=list)
    orphaned_thumbnail_files: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        return [
            f"Series folders: {len(self.series_folders)}",
            f"Readable series: {len(self.readable_titles)}",
            f"Empty or invalid series folders: {len(self.invalid_series_folders)}",
            f"Possible duplicate titles: {len(self.duplicate_titles)}",
            f"Titles without saved source URL: {len(self.titles_without_saved_source)}",
            f"Titles without cached cover: {len(self.titles_without_cached_cover)}",
            f"Empty chapter folders: {len(self.empty_chapter_folders)}",
            f"Orphaned settings rows: {len(self.orphaned_settings)}",
            f"Orphaned progress rows: {len(self.orphaned_progress)}",
            f"Orphaned thumbnail files: {len(self.orphaned_thumbnail_files)}",
        ]

    def details_text(self) -> str:
        sections = ["Library Health", "", *self.summary_lines()]
        self._append_section(sections, "Invalid folders", self.invalid_series_folders)
        self._append_section(sections, "Possible duplicates", self.duplicate_titles)
        self._append_section(sections, "Missing saved sources", self.titles_without_saved_source)
        self._append_section(sections, "Missing cached covers", self.titles_without_cached_cover)
        self._append_section(sections, "Empty chapter folders", self.empty_chapter_folders)
        self._append_section(sections, "Orphaned settings rows", self.orphaned_settings)
        self._append_section(sections, "Orphaned progress rows", self.orphaned_progress)
        self._append_section(sections, "Orphaned thumbnail files", self.orphaned_thumbnail_files)
        return "\n".join(sections)

    @staticmethod
    def _append_section(lines: list[str], title: str, items: list[str]):
        if not items:
            return
        lines.extend(["", f"{title}:"])
        lines.extend(items)


def analyze_library_health(library_path: str, settings_store) -> LibraryHealthReport:
    report = LibraryHealthReport(library_path=str(library_path or ""))
    library_root = Path(library_path)
    if not library_root.exists() or not library_root.is_dir():
        return report

    report.series_folders = list_library_entries(str(library_root))
    settings_rows = settings_store.get_rows(
        report.series_folders,
        columns=("custom_thumbnail", "category", "bookmarked", "latest_new_chapter", "source_url", "content_type"),
    )

    readable_names: list[str] = []
    invalid_names: list[str] = []
    missing_sources: list[str] = []
    missing_cached_covers: list[str] = []
    empty_chapters: list[str] = []

    for name in report.series_folders:
        webtoon = build_webtoon_from_folder(
            str(library_root),
            name,
            settings_store,
            settings_row=settings_rows.get(name),
        )
        if webtoon is None:
            invalid_names.append(name)
            continue
        readable_names.append(name)
        row = settings_rows.get(name, {})
        if not str(row.get("source_url") or "").strip():
            missing_sources.append(name)
        if not preferred_thumbnail_path(name, settings_store, settings_row=row):
            missing_cached_covers.append(name)
        empty_chapters.extend(_empty_chapter_folders(Path(webtoon.path), webtoon.chapters))

    report.readable_titles = readable_names
    report.invalid_series_folders = invalid_names
    report.titles_without_saved_source = missing_sources
    report.titles_without_cached_cover = missing_cached_covers
    report.empty_chapter_folders = empty_chapters
    report.duplicate_titles = _duplicate_titles(report.series_folders)

    library_names = set(report.series_folders)
    conn = get_connection()
    report.orphaned_settings = sorted(
        row[0] for row in conn.execute("SELECT webtoon_name FROM webtoon_settings").fetchall()
        if str(row[0] or "").strip() not in library_names
    )
    report.orphaned_progress = sorted(
        row[0] for row in conn.execute("SELECT DISTINCT webtoon_name FROM progress").fetchall()
        if str(row[0] or "").strip() not in library_names
    )
    report.orphaned_thumbnail_files = _orphaned_thumbnail_files(library_names, settings_store)
    return report


def cleanup_orphaned_metadata(report: LibraryHealthReport, settings_store, progress_store) -> int:
    removed = 0
    if report.orphaned_settings:
        settings_store.delete_webtoons(report.orphaned_settings)
        removed += len(report.orphaned_settings)
    if report.orphaned_progress:
        progress_store.clear_many(report.orphaned_progress)
        removed += len(report.orphaned_progress)
    return removed


def delete_invalid_series_folders(report: LibraryHealthReport) -> int:
    removed = 0
    for name in report.invalid_series_folders:
        path_str = resolve_existing_webtoon_storage_path(report.library_path, name)
        if not path_str:
            continue
        path = Path(path_str)
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            else:
                continue
            removed += 1
        except OSError as exc:
            logger.warning("Could not delete invalid library folder %s", path, exc_info=exc)
    return removed


def delete_empty_chapter_folders(report: LibraryHealthReport) -> int:
    removed = 0
    for entry in report.empty_chapter_folders:
        series_name, _, chapter_name = entry.partition(" / ")
        if not series_name or not chapter_name:
            continue
        webtoon_path = Path(resolve_webtoon_path(report.library_path, series_name))
        path = webtoon_path / chapter_name
        if not path.exists() or not path.is_dir():
            continue
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            logger.warning("Could not delete empty chapter folder %s", path, exc_info=exc)
    return removed


def remove_orphaned_thumbnail_files(report: LibraryHealthReport) -> int:
    removed = 0
    for path_str in report.orphaned_thumbnail_files:
        path = Path(path_str)
        if not path.exists() or not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Could not delete orphaned thumbnail %s", path, exc_info=exc)
    return removed


def _duplicate_titles(names: list[str]) -> list[str]:
    normalized: dict[str, str] = {}
    duplicates: list[str] = []
    for name in names:
        key = " ".join(str(name).casefold().split())
        if key in normalized:
            duplicates.append(f"{normalized[key]} / {name}")
        else:
            normalized[key] = name
    return duplicates


def _empty_chapter_folders(webtoon_path: Path, chapters: list[str]) -> list[str]:
    empty: list[str] = []
    for chapter in chapters:
        chapter_path = chapter_storage_path(str(webtoon_path), chapter)
        try:
            has_image = count_chapter_images(str(webtoon_path), chapter) > 0 or chapter_has_text_payload(str(webtoon_path), chapter)
        except OSError:
            has_image = False
        if not has_image and chapter_path.exists():
            empty.append(f"{webtoon_path.name} / {chapter}")
    return empty


def _orphaned_thumbnail_files(library_names: set[str], settings_store) -> list[str]:
    thumbs_dir = Path(THUMB_FOLDER)
    if not thumbs_dir.exists() or not thumbs_dir.is_dir():
        return []

    live_custom_paths = {
        str(path).strip()
        for path in (
            settings_store.get_rows(list(library_names), columns=("custom_thumbnail",)).get(name, {}).get("custom_thumbnail")
            for name in library_names
        )
        if str(path or "").strip()
    }
    orphaned: list[str] = []
    for entry in thumbs_dir.iterdir():
        if not entry.is_file():
            continue
        entry_path = str(entry)
        stem = entry.stem
        if entry_path in live_custom_paths:
            continue
        if stem.endswith("_custom"):
            orphaned.append(entry_path)
            continue
        if stem not in library_names:
            orphaned.append(entry_path)
    return sorted(orphaned)
