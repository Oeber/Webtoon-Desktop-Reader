import shutil
import threading
from pathlib import Path

from core.app_logging import get_logger
from core.chapter_storage import (
    SUPPORTED_ARCHIVE_EXTENSIONS,
    chapter_has_text_payload,
    count_chapter_images,
    list_series_chapters,
)
from stores.settings_store import (
    DEFAULT_LIBRARY_FOLDER_NAMES,
    load_library_content_paths,
    load_library_reserved_folder_names,
)


logger = get_logger(__name__)
_LIBRARY_LAYOUT_LOCK = threading.Lock()


def normalize_content_type(content_type: str | None, default: str = "webtoon") -> str:
    normalized = str(content_type or "").strip().casefold()
    if normalized in DEFAULT_LIBRARY_FOLDER_NAMES:
        return normalized
    return default


def configured_content_type_paths(
    library_path: str | None = None,
    content_paths: dict[str, str] | None = None,
) -> dict[str, str]:
    if content_paths is None:
        return load_library_content_paths(library_path)
    return {
        content_type: str((content_paths or {}).get(content_type) or "").strip()
        for content_type in DEFAULT_LIBRARY_FOLDER_NAMES
    }


def reserved_library_folder_names(
    library_path: str,
    content_paths: dict[str, str] | None = None,
) -> set[str]:
    names = set(DEFAULT_LIBRARY_FOLDER_NAMES.values())
    names.update(load_library_reserved_folder_names())
    root = Path(library_path)
    for path_str in configured_content_type_paths(library_path, content_paths).values():
        try:
            content_root = Path(path_str)
            if content_root.parent.resolve() == root.resolve():
                names.add(content_root.name)
        except OSError:
            if str(content_root.parent).casefold() == str(root).casefold():
                names.add(content_root.name)
    return {str(name or "").strip() for name in names if str(name or "").strip()}


def infer_content_type_from_folder(webtoon_path: str, stored_content_type: str | None = None) -> str:
    normalized_stored = str(stored_content_type or "").strip().casefold()
    if normalized_stored in DEFAULT_LIBRARY_FOLDER_NAMES:
        return normalized_stored

    path = Path(webtoon_path)
    if not path.exists() or not path.is_dir():
        return "webtoon"

    saw_text = False
    saw_images = False
    try:
        for chapter_name in list_series_chapters(str(path)):
            if chapter_has_text_payload(str(path), chapter_name):
                saw_text = True
            if count_chapter_images(str(path), chapter_name) > 0:
                saw_images = True
            if saw_text and saw_images:
                return "webtoon"
    except OSError:
        return "webtoon"

    if saw_text and not saw_images:
        return "webnovel"
    return "webtoon"


def infer_content_type_from_path(webtoon_path: str) -> str:
    path = Path(webtoon_path)
    try:
        parent = path.resolve().parent
    except OSError:
        parent = path.parent
    for content_type, root_path in configured_content_type_paths().items():
        try:
            if parent == Path(root_path).resolve():
                return content_type
        except OSError:
            if str(parent).casefold() == str(Path(root_path)).casefold():
                return content_type
    return infer_content_type_from_folder(webtoon_path)


def content_type_root(
    library_path: str,
    content_type: str,
    *,
    create: bool = False,
    content_paths: dict[str, str] | None = None,
) -> str:
    normalized_type = normalize_content_type(content_type)
    current_paths = configured_content_type_paths(library_path, content_paths)
    root = Path(current_paths[normalized_type])
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _matching_archive_file(root: Path, title_name: str) -> Path | None:
    normalized = str(title_name or "").strip().casefold()
    if not normalized or not root.exists() or not root.is_dir():
        return None
    try:
        for entry in root.iterdir():
            if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_ARCHIVE_EXTENSIONS:
                continue
            if entry.stem.casefold() == normalized:
                return entry
    except OSError:
        return None
    return None


def _append_library_entry(names: list[str], seen: set[str], entry: Path) -> None:
    title_name = entry.name if entry.is_dir() else entry.stem
    if title_name in seen:
        logger.warning("Duplicate library title detected across content folders: %s", title_name)
        return
    seen.add(title_name)
    names.append(title_name)


def resolve_existing_webtoon_storage_path(
    library_path: str,
    webtoon_name: str,
    *,
    settings_store=None,
    settings_row: dict | None = None,
    content_type: str | None = None,
) -> str | None:
    normalized_name = str(webtoon_name or "").strip()
    if not normalized_name:
        return None

    ensure_library_content_layout(library_path, settings_store)

    preferred_type = normalize_content_type(content_type or (settings_row or {}).get("content_type"), default="webtoon")
    candidate_roots: list[Path] = [Path(content_type_root(library_path, preferred_type, create=False))]

    if settings_store is not None and (not settings_row or "content_type" not in settings_row):
        stored_type = normalize_content_type(settings_store.get_content_type(normalized_name), default="webtoon")
        stored_root = Path(content_type_root(library_path, stored_type, create=False))
        if all(str(existing) != str(stored_root) for existing in candidate_roots):
            candidate_roots.append(stored_root)

    for type_name in DEFAULT_LIBRARY_FOLDER_NAMES:
        root = Path(content_type_root(library_path, type_name, create=False))
        if all(str(existing) != str(root) for existing in candidate_roots):
            candidate_roots.append(root)

    legacy_root = Path(library_path)
    if all(str(existing) != str(legacy_root) for existing in candidate_roots):
        candidate_roots.append(legacy_root)

    for root in candidate_roots:
        candidate = root / normalized_name
        if candidate.is_dir():
            return str(candidate)
        archive = _matching_archive_file(root, normalized_name)
        if archive is not None:
            return str(archive)

    return None


def ensure_library_content_layout(library_path: str, settings_store=None) -> None:
    root = Path(library_path)
    if not root.exists() or not root.is_dir():
        return

    with _LIBRARY_LAYOUT_LOCK:
        settings_rows = {}
        reserved_names = reserved_library_folder_names(library_path)
        if settings_store is not None:
            try:
                flat_names = [
                    entry.name
                    for entry in root.iterdir()
                    if entry.is_dir() and entry.name not in reserved_names
                ]
                if flat_names:
                    settings_rows = settings_store.get_rows(flat_names, columns=("content_type",))
            except Exception:
                logger.exception("Failed to load saved content types for library migration")
                settings_rows = {}

        for entry in list(root.iterdir()):
            if not entry.is_dir() or entry.name in reserved_names:
                continue
            row = settings_rows.get(entry.name, {})
            target_type = infer_content_type_from_folder(str(entry), row.get("content_type"))
            destination_root = Path(content_type_root(library_path, target_type, create=True))
            destination = destination_root / entry.name
            if destination.exists():
                logger.warning(
                    "Skipping library migration for %s because destination already exists at %s",
                    entry,
                    destination,
                )
                continue
            try:
                shutil.move(str(entry), str(destination))
                logger.info("Migrated library folder %s -> %s", entry, destination)
            except OSError:
                logger.exception("Failed to migrate library folder %s", entry)


def resolve_webtoon_path(
    library_path: str,
    webtoon_name: str,
    *,
    settings_store=None,
    settings_row: dict | None = None,
    content_type: str | None = None,
    create_parent: bool = False,
) -> str:
    normalized_name = str(webtoon_name or "").strip()
    if not normalized_name:
        return str(Path(library_path))

    ensure_library_content_layout(library_path, settings_store)

    preferred_type = normalize_content_type(content_type or (settings_row or {}).get("content_type"), default="webtoon")
    preferred_path = Path(content_type_root(library_path, preferred_type, create=create_parent)) / normalized_name
    return str(preferred_path)


def list_library_entries(library_path: str) -> list[str]:
    ensure_library_content_layout(library_path)
    root = Path(library_path)

    names: list[str] = []
    seen: set[str] = set()
    for content_type in DEFAULT_LIBRARY_FOLDER_NAMES:
        content_root = Path(content_type_root(library_path, content_type, create=False))
        if not content_root.exists() or not content_root.is_dir():
            continue
        for entry in sorted(content_root.iterdir(), key=lambda item: item.name.casefold()):
            if not entry.is_dir() and (not entry.is_file() or entry.suffix.lower() not in SUPPORTED_ARCHIVE_EXTENSIONS):
                continue
            _append_library_entry(names, seen, entry)

    if root.exists() and root.is_dir():
        reserved_names = reserved_library_folder_names(library_path)
        for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if entry.is_dir():
                if entry.name in reserved_names:
                    continue
            elif not entry.is_file() or entry.suffix.lower() not in SUPPORTED_ARCHIVE_EXTENSIONS:
                continue
            _append_library_entry(names, seen, entry)
    return names


def move_library_contents(
    source_root: str,
    destination_root: str,
    *,
    source_content_paths: dict[str, str] | None = None,
    destination_content_paths: dict[str, str] | None = None,
) -> None:
    source_paths = configured_content_type_paths(source_root, source_content_paths)
    destination_paths = configured_content_type_paths(destination_root, destination_content_paths)

    destination_root_path = Path(destination_root)
    destination_root_path.mkdir(parents=True, exist_ok=True)

    source_root_path = Path(source_root)
    if source_root_path.exists() and source_root_path.is_dir():
        reserved_names = reserved_library_folder_names(source_root, source_paths)
        for entry in list(source_root_path.iterdir()):
            if entry.name in reserved_names:
                continue
            _merge_move_path(entry, destination_root_path / entry.name)

    for content_type in DEFAULT_LIBRARY_FOLDER_NAMES:
        source_path = Path(source_paths[content_type])
        destination_path = Path(destination_paths[content_type])
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        _merge_move_path(source_path, destination_path)


def _merge_move_path(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    try:
        if source.resolve() == destination.resolve():
            return
    except OSError:
        if str(source).casefold() == str(destination).casefold():
            return

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.is_dir() and destination.is_dir():
            for child in list(source.iterdir()):
                _merge_move_path(child, destination / child.name)
            try:
                source.rmdir()
            except OSError:
                pass
            return
        raise FileExistsError(f"Destination already exists: {destination}")

    shutil.move(str(source), str(destination))
