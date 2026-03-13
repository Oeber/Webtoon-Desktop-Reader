import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from core.app_logging import get_logger
from gui.common.styles import INPUT_STYLE
from rapidfuzz import fuzz

logger = get_logger(__name__)

ITEM_ACTION_ROLE = Qt.UserRole
ITEM_WEBTOON_ROLE = Qt.UserRole + 1
ITEM_COMMAND_ROLE = Qt.UserRole + 2

COMMANDS = [
    {
        "name": "/download",
        "template": "/download ",
        "summary": "Start a manual download from a link.",
        "mode": "download",
        "requires_argument": True,
    },
    {
        "name": "/update",
        "template": "/update ",
        "summary": "Find a saved title and start an update.",
        "mode": "update",
        "requires_argument": True,
    },
    {
        "name": "/open",
        "template": "/open ",
        "summary": "Open the last-read or first chapter, or jump with /open <title> <number>.",
        "mode": "open",
        "requires_argument": True,
    },
    {
        "name": "/read",
        "template": "/read ",
        "summary": "Continue reading a title.",
        "mode": "read",
        "requires_argument": True,
    },
    {
        "name": "/search",
        "template": "/search ",
        "summary": "Search Discover with /search <scraper> <title>.",
        "mode": "discover",
        "requires_argument": True,
    },
    {
        "name": "/library",
        "template": "/library",
        "summary": "Go to the library page.",
        "mode": "library",
        "requires_argument": False,
    },
    {
        "name": "/updates",
        "template": "/updates",
        "summary": "Open the updates page.",
        "mode": "updates",
        "requires_argument": False,
    },
    {
        "name": "/settings",
        "template": "/settings",
        "summary": "Open the settings page.",
        "mode": "settings",
        "requires_argument": False,
    },
    {
        "name": "/logs",
        "template": "/logs",
        "summary": "Open the settings logs tab.",
        "mode": "logs",
        "requires_argument": False,
    },
    {
        "name": "/help",
        "template": "/help",
        "summary": "Show available commands.",
        "mode": "help",
        "requires_argument": False,
    },
]
COMMANDS_BY_NAME = {command["name"]: command for command in COMMANDS}


def rank_webtoons(webtoons: list, query: str) -> list[tuple[int, object]]:
    text = (query or "").strip().lower()
    if not text:
        return [(100, webtoon) for webtoon in webtoons]

    scored = []
    for webtoon in webtoons:
        name = webtoon.name.lower()
        score = max(
            fuzz.WRatio(text, name),
            fuzz.partial_ratio(text, name),
            fuzz.token_set_ratio(text, name),
        )
        if score >= 60:
            scored.append((int(score), webtoon))

    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    return scored


class GlobalSearchDialog(QDialog):

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Search")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Quick Search (Ctrl+K)")
        self.input.setStyleSheet(INPUT_STYLE)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        self.results = QListWidget()
        self.results.setIconSize(self.main_window.iconSizeHint())
        self.results.setSpacing(6)
        layout.addWidget(self.results)

        self.input.textChanged.connect(self._update_results)
        self.input.returnPressed.connect(self._activate_from_input)
        self.results.itemClicked.connect(self._activate_item)
        self.results.itemActivated.connect(self._activate_item)

        self._tab_shortcut = QShortcut(QKeySequence(Qt.Key_Tab), self.input)
        self._tab_shortcut.setContext(Qt.WidgetShortcut)
        self._tab_shortcut.activated.connect(lambda: self._handle_tab_completion(backward=False))

        self._backtab_shortcut = QShortcut(QKeySequence("Shift+Tab"), self.input)
        self._backtab_shortcut.setContext(Qt.WidgetShortcut)
        self._backtab_shortcut.activated.connect(lambda: self._handle_tab_completion(backward=True))

        self._tab_completion_matches = []
        self._tab_completion_index = -1
        self._tab_completion_prefix = ""
        self._applying_tab_completion = False
        self._preserve_command_preview_matches = False

    def open_dialog(self):
        logger.info("Opening global search dialog")
        self.input.clear()
        self._update_results("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _update_results(self, text: str):
        query = (text or "").strip()
        logger.info("Updating global search results for query='%s'", query)
        self.results.clear()
        if not self._applying_tab_completion:
            self._reset_tab_completion()
            self._preserve_command_preview_matches = False

        command_name, has_space, remainder = self._split_command_query(text)
        if command_name:
            if (
                self._preserve_command_preview_matches
                and not has_space
                and self._tab_completion_matches
                and command_name in self._tab_completion_matches
            ):
                self._show_command_previews(self._tab_completion_prefix, matches=self._tab_completion_matches)
                return
            command = COMMANDS_BY_NAME.get(command_name)
            if command is None or (command["requires_argument"] and not has_space):
                self._show_command_previews(command_name)
                return
            self._show_command_results(command, remainder)
            return

        for score, webtoon in rank_webtoons(self.main_window.library._webtoons, query)[:20]:
            item = self._build_webtoon_item(webtoon)
            item.setData(ITEM_ACTION_ROLE, "open_detail")
            item.setToolTip(f"Match score: {score}")
            self.results.addItem(item)

    def _split_command_query(self, text: str) -> tuple[str, bool, str]:
        raw = (text or "").lstrip()
        if not raw.startswith("/"):
            return "", False, ""

        command_name, separator, remainder = raw.partition(" ")
        return command_name.strip(), bool(separator), remainder.strip()

    def _matching_command_names(self, prefix: str) -> list[str]:
        typed = (prefix or "").strip().lower()
        return [
            command["name"]
            for command in COMMANDS
            if not typed or command["name"].lower().startswith(typed)
        ]

    def _reset_tab_completion(self):
        self._tab_completion_matches = []
        self._tab_completion_index = -1
        self._tab_completion_prefix = ""
        self._preserve_command_preview_matches = False

    def _show_command_previews(self, query: str, matches: list[str] | None = None):
        typed = (query or "").strip().lower()
        if matches is None:
            matches = [
                command["name"]
                for command in COMMANDS
                if not typed or command["name"].lower().startswith(typed)
            ]
        if not matches:
            self._add_message_item("No commands match.")
            return

        for command_name in matches:
            command = COMMANDS_BY_NAME[command_name]
            item = QListWidgetItem(f"{command['name']}  {command['summary']}")
            item.setData(ITEM_ACTION_ROLE, "command_preview")
            item.setData(ITEM_COMMAND_ROLE, command["template"])
            item.setToolTip(command["summary"])
            self.results.addItem(item)

        selected_row = 0
        if matches == self._tab_completion_matches and 0 <= self._tab_completion_index < len(matches):
            selected_row = self._tab_completion_index
        self.results.setCurrentRow(selected_row)

    def _show_command_results(self, command: dict, remainder: str):
        mode = command["mode"]
        if mode == "download":
            self._show_download_results(command, remainder)
            return
        if mode == "update":
            self._show_update_results(command, remainder)
            return
        if mode == "open":
            title_query, chapter_query = self._split_title_and_chapter_query(remainder)
            self._show_title_results(
                command,
                title_query,
                action="open_detail",
                chapter_query=chapter_query,
            )
            return
        if mode == "read":
            self._show_title_results(command, remainder, action="read_title")
            return
        if mode == "discover":
            self._show_discovery_results(command, remainder)
            return
        if mode == "help":
            self._show_help_results()
            return

        item = QListWidgetItem(f"{command['name']}  {command['summary']}")
        item.setData(ITEM_ACTION_ROLE, f"navigate:{mode}")
        item.setData(ITEM_COMMAND_ROLE, command["name"])
        item.setToolTip(command["summary"])
        self.results.addItem(item)
        self.results.setCurrentItem(item)

    def _show_help_results(self):
        for command in COMMANDS:
            item = QListWidgetItem(f"{command['name']}  {command['summary']}")
            item.setData(ITEM_ACTION_ROLE, "command_preview")
            item.setData(ITEM_COMMAND_ROLE, command["template"])
            item.setToolTip(command["summary"])
            self.results.addItem(item)
        if self.results.count() > 0:
            self.results.setCurrentRow(0)

    def _show_download_results(self, command: dict, remainder: str):
        if not remainder:
            self._add_command_preview(command)
            self._add_message_item("Type /download {link} to start a download.")
            self.results.setCurrentRow(0)
            return

        item = QListWidgetItem(f"Download: {remainder}")
        item.setData(ITEM_ACTION_ROLE, "download")
        item.setData(ITEM_WEBTOON_ROLE, remainder)
        self.results.addItem(item)
        self.results.setCurrentItem(item)

    def _show_update_results(self, command: dict, remainder: str):
        candidates = []
        for webtoon in self.main_window.library._webtoons:
            if self.main_window.settings_store.get_completed(webtoon.name):
                continue
            if self.main_window.settings_store.get_source_url(webtoon.name):
                candidates.append(webtoon)

        ranked = rank_webtoons(candidates, remainder)[:20]
        if not ranked:
            self._add_command_preview(command)
            message = "No saved-source titles available to update."
            if remainder:
                message = "No update entries match your search."
            self._add_message_item(message)
            self.results.setCurrentRow(0)
            return

        for score, webtoon in ranked:
            item = self._build_webtoon_item(webtoon)
            item.setData(ITEM_ACTION_ROLE, "update")
            item.setToolTip(f"Update '{webtoon.name}' (match score: {score})")
            self.results.addItem(item)

        self.results.setCurrentRow(0)

    def _show_title_results(
        self,
        command: dict,
        remainder: str,
        action: str,
        chapter_query: str | None = None,
    ):
        ranked = rank_webtoons(self.main_window.library._webtoons, remainder)[:20]
        if not ranked:
            self._add_command_preview(command)
            self._add_message_item("No titles match your search.")
            self.results.setCurrentRow(0)
            return

        for score, webtoon in ranked:
            item = self._build_webtoon_item(webtoon)
            item.setData(ITEM_ACTION_ROLE, action)
            if chapter_query:
                chapter_index = self._find_chapter_index(webtoon, chapter_query)
                item.setData(ITEM_COMMAND_ROLE, chapter_index)
            if action == "read_title":
                progress = self.main_window.library.progress_store.get(webtoon.name)
                hint = "Continue reading" if progress else "Start from beginning"
                item.setToolTip(f"{hint} '{webtoon.name}' (match score: {score})")
            else:
                if chapter_query:
                    chapter_index = item.data(ITEM_COMMAND_ROLE)
                    if chapter_index is not None:
                        chapter_name = webtoon.chapters[chapter_index]
                        item.setToolTip(
                            f"Open '{webtoon.name}' at '{chapter_name}' (match score: {score})"
                        )
                    else:
                        item.setToolTip(
                            f"Open '{webtoon.name}' at the last-read or first chapter; no chapter matched '{chapter_query}' "
                            f"(match score: {score})"
                        )
                else:
                    item.setToolTip(
                        f"Open '{webtoon.name}' at the last-read or first chapter (match score: {score})"
                    )
            self.results.addItem(item)

        self.results.setCurrentRow(0)

    def _show_discovery_results(self, command: dict, remainder: str):
        scraper_query, title_query, provider_key = self._split_discovery_query(remainder)
        scraper_matches = self.main_window.discovery.matching_provider_labels(scraper_query)

        if not remainder.strip():
            self._add_command_preview(command)
            self._add_message_item("Type /search <scraper> <title> to search Discover.")
            self._add_discovery_scraper_items(scraper_matches)
            self.results.setCurrentRow(0)
            return

        if provider_key is None:
            self._add_command_preview(command)
            if scraper_matches:
                self._add_message_item("Choose a scraper, then type a title.")
                self._add_discovery_scraper_items(scraper_matches)
            else:
                self._add_message_item(f"No scrapers match '{scraper_query}'.")
                providers = self.main_window.discovery.available_provider_labels()
                if providers:
                    self._add_message_item(f"Available scrapers: {', '.join(providers)}")
            self.results.setCurrentRow(0)
            return

        if not title_query:
            self._add_command_preview(command)
            self._add_message_item(f"Type a title after '{scraper_query}'.")
            self._add_discovery_scraper_items(scraper_matches)
            self.results.setCurrentRow(0)
            return

        item = QListWidgetItem(f"Discover: {scraper_query} -> {title_query}")
        item.setData(ITEM_ACTION_ROLE, "discovery_search")
        item.setData(ITEM_WEBTOON_ROLE, {"query": title_query, "scraper": scraper_query})
        item.setToolTip(f"Search Discover for '{title_query}' using '{scraper_query}'")
        self.results.addItem(item)
        self.results.setCurrentItem(item)

    def _add_discovery_scraper_items(self, labels: list[str]):
        for label in labels[:20]:
            item = QListWidgetItem(f"Scraper: {label}")
            item.setData(ITEM_ACTION_ROLE, "discovery_scraper_preview")
            item.setData(ITEM_COMMAND_ROLE, self._discovery_scraper_replacement(label))
            item.setToolTip(f"Use scraper '{label}'")
            self.results.addItem(item)

    def _split_discovery_query(self, text: str) -> tuple[str, str, str | None]:
        query = " ".join(str(text or "").split()).strip()
        if not query:
            return "", "", None

        parts = query.split(" ")
        discovery_page = self.main_window.discovery

        for end in range(len(parts), 0, -1):
            scraper_candidate = " ".join(parts[:end]).strip()
            provider_key = discovery_page.resolve_provider_key(scraper_candidate)
            if provider_key is None:
                continue
            title_query = " ".join(parts[end:]).strip()
            return scraper_candidate, title_query, provider_key

        return query, "", None

    def _discovery_scraper_replacement(self, scraper_label: str) -> str:
        return f"/search {scraper_label} "

    def _handle_discovery_scraper_tab_completion(self, command_name: str, remainder: str, backward: bool) -> bool:
        scraper_query, title_query, _provider_key = self._split_discovery_query(remainder)
        if title_query:
            return False

        session_prefix = f"{command_name}|discover"
        current_text = self.input.text().strip()
        existing_replacements = {
            self._discovery_scraper_replacement(label).strip()
            for label in self._tab_completion_matches
        }
        active_session = (
            self._tab_completion_matches
            and self._tab_completion_prefix == session_prefix
            and current_text in existing_replacements
        )

        if active_session:
            matches = list(self._tab_completion_matches)
        else:
            matches = self.main_window.discovery.matching_provider_labels(scraper_query)
            if not matches:
                return False
            self._tab_completion_prefix = session_prefix
            self._tab_completion_matches = matches
            self._tab_completion_index = len(matches) - 1 if backward else 0

        if active_session:
            step = -1 if backward else 1
            self._tab_completion_index = (self._tab_completion_index + step) % len(matches)

        replacement = self._discovery_scraper_replacement(matches[self._tab_completion_index])
        self._preserve_command_preview_matches = False
        self._applying_tab_completion = True
        try:
            self.input.setText(replacement)
            self.input.setCursorPosition(len(replacement))
        finally:
            self._applying_tab_completion = False
        return True

    def _split_title_and_chapter_query(self, text: str) -> tuple[str, str | None]:
        query = (text or "").strip()
        if not query:
            return "", None

        match = re.match(r"^(?P<title>.+?)\s+(?P<chapter>\d+(?:\.\d+)?)$", query)
        if not match:
            return query, None

        title = match.group("title").strip()
        chapter = match.group("chapter").strip()
        if not title:
            return query, None
        return title, chapter

    def _find_chapter_index(self, webtoon, chapter_query: str) -> int | None:
        target = self._parse_chapter_number(chapter_query)
        if target is None:
            return None

        fallback_index = None
        for index, chapter_name in enumerate(getattr(webtoon, "chapters", []) or []):
            number = self._extract_chapter_number(chapter_name)
            if number is None:
                continue
            if abs(number - target) < 1e-9:
                return index
            if fallback_index is None and str(chapter_query) in chapter_name:
                fallback_index = index
        return fallback_index

    def _extract_chapter_number(self, chapter_name: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)", chapter_name or "")
        if not match:
            return None
        return self._parse_chapter_number(match.group(1))

    def _parse_chapter_number(self, value: str) -> float | None:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _build_webtoon_item(self, webtoon) -> QListWidgetItem:
        item = QListWidgetItem(webtoon.name)
        item.setData(ITEM_WEBTOON_ROLE, webtoon)

        thumb_path = webtoon.thumbnail
        if thumb_path:
            pixmap = QPixmap(thumb_path)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
        return item

    def _add_command_preview(self, command: dict):
        item = QListWidgetItem(f"{command['name']}  {command['summary']}")
        item.setData(ITEM_ACTION_ROLE, "command_preview")
        item.setData(ITEM_COMMAND_ROLE, command["template"])
        item.setToolTip(command["summary"])
        self.results.addItem(item)

    def _add_message_item(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        self.results.addItem(item)

    def _activate_from_input(self):
        item = self.results.currentItem()
        if item is None and self.results.count() > 0:
            item = self.results.item(0)
        if item is not None:
            self._activate_item(item)

    def _handle_tab_completion(self, backward: bool) -> None:
        text = self.input.text()
        command_name, has_space, remainder = self._split_command_query(text)
        if not command_name:
            return

        command = COMMANDS_BY_NAME.get(command_name)
        if has_space and command is not None and command["mode"] == "discover":
            if self._handle_discovery_scraper_tab_completion(command_name, remainder, backward):
                return

        if has_space:
            self._cycle_result_selection(backward=backward)
            return

        active_session = (
            self._tab_completion_matches
            and text.strip() in self._tab_completion_matches
            and not has_space
        )

        matches = (
            list(self._tab_completion_matches)
            if active_session
            else self._matching_command_names(command_name)
        )
        if not matches:
            return

        if not active_session and (
            self._tab_completion_prefix != command_name or self._tab_completion_matches != matches
        ):
            self._tab_completion_prefix = command_name
            self._tab_completion_matches = matches
            self._tab_completion_index = len(matches) - 1 if backward else 0
        else:
            step = -1 if backward else 1
            self._tab_completion_index = (self._tab_completion_index + step) % len(matches)

        completed = self._tab_completion_matches[self._tab_completion_index]
        command = COMMANDS_BY_NAME.get(completed)
        if command is None:
            return

        replacement = completed if len(matches) > 1 and command_name != completed else command["template"]
        self._preserve_command_preview_matches = len(matches) > 1 and not replacement.endswith(" ")
        self._applying_tab_completion = True
        try:
            self.input.setText(replacement)
            self.input.setCursorPosition(len(replacement))
        finally:
            self._applying_tab_completion = False

    def _cycle_result_selection(self, backward: bool) -> None:
        count = self.results.count()
        if count <= 0:
            return

        current_row = self.results.currentRow()
        if current_row < 0:
            current_row = 0 if not backward else count - 1
        else:
            step = -1 if backward else 1
            current_row = (current_row + step) % count

        start_row = current_row
        while not (self.results.item(current_row).flags() & Qt.ItemIsEnabled):
            step = -1 if backward else 1
            current_row = (current_row + step) % count
            if current_row == start_row:
                return

        self.results.setCurrentRow(current_row)

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                if self._try_accept_command_preview():
                    return True
                if self._try_accept_result_selection():
                    return True
        return super().eventFilter(watched, event)

    def _try_accept_command_preview(self) -> bool:
        text = self.input.text()
        command_name, has_space, _ = self._split_command_query(text)
        if not command_name or has_space:
            return False

        item = self.results.currentItem()
        if item is None or item.data(ITEM_ACTION_ROLE) != "command_preview":
            return False

        command_text = item.data(ITEM_COMMAND_ROLE)
        if not command_text:
            return False

        if not str(command_text).endswith(" "):
            command_text = f"{command_text} "
        self.input.setText(command_text)
        self.input.setFocus()
        self.input.setCursorPosition(len(command_text))
        return True

    def _try_accept_result_selection(self) -> bool:
        text = self.input.text()
        command_name, has_space, remainder = self._split_command_query(text)
        if not command_name or not has_space:
            return False

        command = COMMANDS_BY_NAME.get(command_name)
        if command is None:
            return False

        item = self.results.currentItem()
        if item is None:
            return False

        if command["mode"] == "discover" and item.data(ITEM_ACTION_ROLE) == "discovery_scraper_preview":
            replacement = item.data(ITEM_COMMAND_ROLE)
            if not replacement:
                return False
            self.input.setText(str(replacement))
            self.input.setFocus()
            self.input.setCursorPosition(len(str(replacement)))
            return True

        if command["mode"] not in {"open", "read", "update"}:
            return False

        if item.data(ITEM_ACTION_ROLE) not in {"open_detail", "read_title", "update"}:
            return False

        webtoon = item.data(ITEM_WEBTOON_ROLE)
        if webtoon is None:
            return False

        replacement = f"{command_name} {webtoon.name}"
        if command["mode"] == "open":
            _, chapter_query = self._split_title_and_chapter_query(remainder)
            if chapter_query:
                replacement = f"{replacement} {chapter_query}"

        if not replacement.endswith(" "):
            replacement = f"{replacement} "
        self.input.setText(replacement)
        self.input.setFocus()
        self.input.setCursorPosition(len(replacement))
        return True

    def _activate_item(self, item: QListWidgetItem):
        action = item.data(ITEM_ACTION_ROLE)
        if not action:
            return

        if action == "command_preview":
            command_text = item.data(ITEM_COMMAND_ROLE) or "/"
            self.input.setText(command_text)
            self.input.setFocus()
            self.input.setCursorPosition(len(command_text))
            return

        if action == "discovery_scraper_preview":
            command_text = item.data(ITEM_COMMAND_ROLE) or "/search "
            self.input.setText(str(command_text))
            self.input.setFocus()
            self.input.setCursorPosition(len(str(command_text)))
            return

        if action == "download":
            url = item.data(ITEM_WEBTOON_ROLE)
            logger.info("Global search command selected download for %s", url)
            error = self.main_window.downloader.start_download_from_url(url)
            if error is None:
                self.close()
            return

        if action == "discovery_search":
            payload = item.data(ITEM_WEBTOON_ROLE) or {}
            query = str(payload.get("query", "")).strip()
            scraper = str(payload.get("scraper", "")).strip()
            logger.info("Global search command selected discovery query=%r scraper=%r", query, scraper)
            if self.main_window.open_discovery_search(query=query, scraper=scraper):
                self.close()
            return

        if action.startswith("navigate:"):
            destination = action.split(":", 1)[1]
            logger.info("Global search command selected navigation to %s", destination)
            if destination == "library":
                self.main_window.open_library()
            elif destination == "updates":
                self.main_window.open_updates()
            elif destination == "settings":
                self.main_window.open_settings()
            elif destination == "logs":
                self.main_window.open_settings()
                self.main_window.settings.open_logs_tab()
            self.close()
            return

        webtoon = item.data(ITEM_WEBTOON_ROLE)
        if webtoon is None:
            return

        if action == "update":
            logger.info("Global search command selected update for %s", webtoon.name)
            error = self.main_window.updates.start_update_for_webtoon(webtoon.name)
            if error is None:
                self.close()
            return

        if action == "read_title":
            logger.info("Global search command selected read for %s", webtoon.name)
            self._open_for_reading(webtoon)
            self.close()
            return

        chapter_index = item.data(ITEM_COMMAND_ROLE)
        if isinstance(chapter_index, int):
            logger.info(
                "Global search selected %s chapter index=%d",
                webtoon.name,
                chapter_index,
            )
            self.main_window.open_chapter_with_prompt(webtoon, chapter_index)
        else:
            logger.info("Global search selected %s for resume-or-first open", webtoon.name)
            self._open_for_reading(webtoon)
        self.close()

    def _open_for_reading(self, webtoon):
        if not getattr(webtoon, "chapters", None):
            self.main_window.open_detail(webtoon)
            return

        progress = self.main_window.library.progress_store.get(webtoon.name)
        if progress:
            chapter = progress.get("chapter")
            scroll_pct = progress.get("scroll", 0.0)
            if chapter in webtoon.chapters:
                self.main_window.open_chapter(
                    webtoon,
                    webtoon.chapters.index(chapter),
                    scroll_pct,
                )
                return

        self.main_window.open_chapter(webtoon, 0, 0.0)
