from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

import gui.common.styles as app_styles
from core.app_logging import get_logger
from gui.common.strings import available_locales, get_locale, set_locale, t
from gui.common.styles import (
    BUTTON_STYLE,
    CHECKBOX_STYLE,
    INPUT_STYLE,
    PAGE_BG_STYLE,
    PAGE_TITLE_LARGE_STYLE,
    SECTION_LABEL_TRANSPARENT_STYLE,
    STATUS_LABEL_STYLE,
    SURFACE_PANEL_STYLE,
    TEXT_MUTED_BODY_STYLE,
    TEXT_MUTED_TRANSPARENT_STYLE,
)
from scrapers.discovery_registry import get_all_discovery_providers_including_disabled
from scrapers.registry import get_all_scrapers_including_disabled
from scrapers.site_availability import (
    MODE_ALL_DISABLED,
    MODE_ENABLED,
    get_site_availability_mode,
    save_site_availability,
)
from stores.settings_store import (
    APP_LOCALE_KEY,
    DEFAULT_LIBRARY_PATH,
    LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY,
    load_default_discovery_provider,
    load_setting,
    save_default_discovery_provider,
    save_library_content_paths,
    save_library_path,
    save_setting,
)


logger = get_logger(__name__)


class FirstRunSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._open_target = "library"
        self._step_index = 0
        self._provider_site_names: list[str] = []
        self._source_mode_boxes: dict[str, dict] = {}
        self._active_locale = str(load_setting(APP_LOCALE_KEY, get_locale()) or get_locale()).strip() or "en"

        self.setModal(True)
        self.setMinimumWidth(680)
        self.setStyleSheet(PAGE_BG_STYLE)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(0)

        self.panel = QWidget(self)
        self.panel.setStyleSheet(SURFACE_PANEL_STYLE)
        root_layout.addWidget(self.panel)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self.step_label = QLabel("")
        self.step_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(self.step_label)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet(PAGE_TITLE_LARGE_STYLE)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel("")
        self.subtitle_label.setStyleSheet(TEXT_MUTED_BODY_STYLE)
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        self.pages = QStackedWidget(self.panel)
        layout.addWidget(self.pages, 1)

        self.pages.addWidget(self._build_appearance_page())
        self.pages.addWidget(self._build_sources_page())
        self.pages.addWidget(self._build_library_page())

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()

        self.back_btn = QPushButton()
        self.back_btn.setStyleSheet(BUTTON_STYLE)
        self.back_btn.clicked.connect(self._go_back)
        button_row.addWidget(self.back_btn)

        self.next_btn = QPushButton()
        self.next_btn.setStyleSheet(BUTTON_STYLE)
        self.next_btn.clicked.connect(self._go_next)
        button_row.addWidget(self.next_btn)

        self.finish_btn = QPushButton()
        self.finish_btn.setStyleSheet(BUTTON_STYLE)
        self.finish_btn.clicked.connect(lambda: self._finish(open_target="library"))
        button_row.addWidget(self.finish_btn)

        self.finish_discover_btn = QPushButton()
        self.finish_discover_btn.setStyleSheet(BUTTON_STYLE)
        self.finish_discover_btn.clicked.connect(lambda: self._finish(open_target="discover"))
        button_row.addWidget(self.finish_discover_btn)

        layout.addLayout(button_row)

        self._set_locale(self._active_locale, update_combo=False)
        self.apply_theme()
        self._refresh_step_ui()
        self._refresh_library_preview()

    def open_target(self) -> str:
        return self._open_target

    def reject(self) -> None:
        return

    def _build_appearance_page(self) -> QWidget:
        page = QWidget(self.panel)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        language_label = QLabel("")
        language_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        self.language_section_label = language_label
        layout.addWidget(language_label)

        language_help = QLabel("")
        language_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        language_help.setWordWrap(True)
        self.language_help_label = language_help
        layout.addWidget(language_help)

        self.language_combo = QComboBox()
        self.language_combo.setStyleSheet(INPUT_STYLE)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self.language_combo)

        theme_label = QLabel("")
        theme_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        self.theme_section_label = theme_label
        layout.addWidget(theme_label)

        theme_help = QLabel("")
        theme_help.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        theme_help.setWordWrap(True)
        self.theme_help_label = theme_help
        layout.addWidget(theme_help)

        self.theme_combo = QComboBox()
        self.theme_combo.setStyleSheet(INPUT_STYLE)
        for preset_key, preset_meta in app_styles.THEME_PRESETS.items():
            self.theme_combo.addItem(str(preset_meta.get("label", preset_key.title())), preset_key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(app_styles.current_theme_preset())))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        layout.addWidget(self.theme_combo)

        self.theme_status_label = QLabel("")
        self.theme_status_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.theme_status_label.setWordWrap(True)
        layout.addWidget(self.theme_status_label)
        layout.addStretch()
        return page

    def _build_sources_page(self) -> QWidget:
        page = QWidget(self.panel)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.scrapers_section_label = QLabel("")
        self.scrapers_section_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(self.scrapers_section_label)

        self.scrapers_help_label = QLabel("")
        self.scrapers_help_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.scrapers_help_label.setWordWrap(True)
        layout.addWidget(self.scrapers_help_label)

        self.scrapers_list = QWidget(page)
        self.scrapers_list.setStyleSheet("background: transparent;")
        self.scrapers_list_layout = QVBoxLayout(self.scrapers_list)
        self.scrapers_list_layout.setContentsMargins(0, 0, 0, 0)
        self.scrapers_list_layout.setSpacing(10)
        layout.addWidget(self.scrapers_list)
        layout.addStretch()
        return page

    def _build_library_page(self) -> QWidget:
        page = QWidget(self.panel)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.library_section_label = QLabel("")
        self.library_section_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(self.library_section_label)

        self.library_help_label = QLabel("")
        self.library_help_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.library_help_label.setWordWrap(True)
        layout.addWidget(self.library_help_label)

        library_row = QHBoxLayout()
        library_row.setSpacing(8)
        self.library_root_input = QLineEdit()
        self.library_root_input.setStyleSheet(INPUT_STYLE)
        self.library_root_input.setText(DEFAULT_LIBRARY_PATH)
        self.library_root_input.textChanged.connect(self._refresh_library_preview)
        library_row.addWidget(self.library_root_input, 1)

        self.browse_btn = QPushButton()
        self.browse_btn.setStyleSheet(BUTTON_STYLE)
        self.browse_btn.setFixedWidth(100)
        self.browse_btn.clicked.connect(self._browse_library_root)
        library_row.addWidget(self.browse_btn)
        layout.addLayout(library_row)

        self.library_status_label = QLabel("")
        self.library_status_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.library_status_label.setWordWrap(True)
        layout.addWidget(self.library_status_label)

        self.source_section_label = QLabel("")
        self.source_section_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(self.source_section_label)

        self.source_help_label = QLabel("")
        self.source_help_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.source_help_label.setWordWrap(True)
        layout.addWidget(self.source_help_label)

        self.provider_combo = QComboBox()
        self.provider_combo.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.provider_combo)

        self.updates_section_label = QLabel("")
        self.updates_section_label.setStyleSheet(SECTION_LABEL_TRANSPARENT_STYLE)
        layout.addWidget(self.updates_section_label)

        self.updates_help_label = QLabel("")
        self.updates_help_label.setStyleSheet(TEXT_MUTED_TRANSPARENT_STYLE)
        self.updates_help_label.setWordWrap(True)
        layout.addWidget(self.updates_help_label)

        self.library_updates_checkbox = QCheckBox("")
        self.library_updates_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.library_updates_checkbox.setChecked(bool(load_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, False)))
        layout.addWidget(self.library_updates_checkbox)
        layout.addStretch()
        return page

    def _locale_display_name(self, locale: str) -> str:
        normalized = str(locale or "").strip()
        if normalized == "pt-BR":
            return t("settings.locale.pt_br")
        if normalized == "en":
            return t("settings.locale.english")
        return normalized

    def _populate_language_combo(self, selected_locale: str) -> None:
        locales = list(available_locales())
        preferred = ["en", "pt-BR"]
        ordered = [locale for locale in preferred if locale in locales]
        ordered.extend(locale for locale in locales if locale not in ordered)

        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for locale in ordered:
            self.language_combo.addItem(self._locale_display_name(locale), locale)
        index = self.language_combo.findData(selected_locale)
        if index < 0:
            index = self.language_combo.findData("en")
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.blockSignals(False)

    def _rebuild_provider_combo(self) -> None:
        current_site = str(self.provider_combo.currentData() or load_default_discovery_provider() or "").strip()
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self._provider_site_names = [""]
        self.provider_combo.addItem(t("first_run.source.auto"), "")
        for row in self._source_rows():
            if not row.get("discover"):
                continue
            site_name = str(row.get("site_name") or "").strip()
            if not site_name:
                continue
            if self._source_mode_for_row(site_name, row) != MODE_ENABLED:
                continue
            self._provider_site_names.append(site_name)
            label = str(row.get("label") or site_name)
            self.provider_combo.addItem(label, site_name)
        index = self.provider_combo.findData(current_site)
        if index < 0:
            index = 0
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(False)

    def _source_rows(self) -> list[dict]:
        rows_by_site = {}

        for scraper in get_all_scrapers_including_disabled():
            site_name = getattr(scraper, "site_name", "") or ""
            if not site_name:
                continue
            row = rows_by_site.setdefault(
                site_name,
                {"site_name": site_name, "label": site_name.replace("_", " ").title(), "download": False, "discover": False},
            )
            row["download"] = True

        for provider in get_all_discovery_providers_including_disabled():
            site_name = getattr(provider, "site_name", "") or ""
            if not site_name:
                continue
            row = rows_by_site.setdefault(
                site_name,
                {"site_name": site_name, "label": provider.get_display_name(), "download": False, "discover": False},
            )
            row["label"] = provider.get_display_name() or row["label"]
            row["discover"] = True

        return sorted(rows_by_site.values(), key=lambda row: str(row["label"]).casefold())

    def _source_label(self, row: dict) -> str:
        capabilities = []
        if row.get("download"):
            capabilities.append(t("first_run.scrapers.capability.download"))
        if row.get("discover"):
            capabilities.append(t("first_run.scrapers.capability.discover"))
        suffix = f" ({', '.join(capabilities)})" if capabilities else ""
        return f"{row['label']}{suffix}"

    def _source_mode_for_row(self, site_name: str, row: dict) -> str:
        widgets = self._source_mode_boxes.get(site_name)
        if widgets is not None:
            return MODE_ENABLED if widgets["checkbox"].isChecked() else MODE_ALL_DISABLED
        return MODE_ENABLED if get_site_availability_mode(site_name) == MODE_ENABLED else MODE_ALL_DISABLED

    def _rebuild_scraper_controls(self) -> None:
        self._source_mode_boxes = {}
        while self.scrapers_list_layout.count():
            item = self.scrapers_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for row in self._source_rows():
            site_name = str(row.get("site_name") or "").strip()
            if not site_name:
                continue

            row_widget = QWidget(self.scrapers_list)
            row_widget.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            label = QLabel(self._source_label(row))
            label.setStyleSheet(TEXT_MUTED_BODY_STYLE)
            row_layout.addWidget(label, 1)

            checkbox = QCheckBox(t("first_run.scrapers.enabled"))
            checkbox.setStyleSheet(CHECKBOX_STYLE)
            checkbox.setChecked(self._source_mode_for_row(site_name, row) == MODE_ENABLED)
            checkbox.toggled.connect(lambda _checked, site_name=site_name: self._on_source_mode_changed(site_name))
            row_layout.addWidget(checkbox)

            self._source_mode_boxes[site_name] = {
                "checkbox": checkbox,
                "row": dict(row),
                "label": label,
            }
            self.scrapers_list_layout.addWidget(row_widget)

        self.scrapers_list_layout.addStretch()

    def _on_source_mode_changed(self, site_name: str) -> None:
        widgets = self._source_mode_boxes.get(site_name)
        if widgets is None:
            return
        row = dict(widgets["row"])
        if row.get("discover"):
            current_provider = str(self.provider_combo.currentData() or "").strip()
            self._rebuild_provider_combo()
            if current_provider == site_name and self._source_mode_for_row(site_name, row) != MODE_ENABLED:
                self.provider_combo.setCurrentIndex(0)

    def _collect_site_availability(self) -> dict[str, str]:
        values = {}
        for site_name, widgets in self._source_mode_boxes.items():
            mode = MODE_ENABLED if widgets["checkbox"].isChecked() else MODE_ALL_DISABLED
            if mode != MODE_ENABLED:
                values[site_name] = mode
        return values

    def _set_locale(self, locale: str, *, update_combo: bool = True) -> None:
        normalized = str(locale or "").strip() or "en"
        self._active_locale = normalized
        save_setting(APP_LOCALE_KEY, normalized)
        set_locale(normalized)
        if update_combo:
            self._populate_language_combo(normalized)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(t("first_run.window"))
        self._populate_language_combo(self._active_locale)
        self._rebuild_provider_combo()

        self.language_section_label.setText(t("first_run.language.title"))
        self.language_help_label.setText(t("first_run.language.help"))
        self.theme_section_label.setText(t("first_run.theme.title"))
        self.theme_help_label.setText(t("first_run.theme.help"))
        self.theme_status_label.setText(t("first_run.theme.preview"))

        self.library_section_label.setText(t("first_run.library.title"))
        self.library_help_label.setText(t("first_run.library.help"))
        self.browse_btn.setText(t("first_run.browse"))
        self.source_section_label.setText(t("first_run.source.title"))
        self.source_help_label.setText(t("first_run.source.help"))
        self.scrapers_section_label.setText(t("first_run.scrapers.title"))
        self.scrapers_help_label.setText(t("first_run.scrapers.help"))
        self.updates_section_label.setText(t("first_run.updates.title"))
        self.updates_help_label.setText(t("first_run.updates.help"))
        self.library_updates_checkbox.setText(t("first_run.updates.checkbox"))

        self.back_btn.setText(t("first_run.back"))
        self.next_btn.setText(t("first_run.next"))
        self.finish_btn.setText(t("first_run.save"))
        self.finish_discover_btn.setText(t("first_run.save_discover"))

        self._rebuild_scraper_controls()
        self._refresh_library_preview()
        self._refresh_step_ui()

    def _refresh_step_ui(self) -> None:
        last_step_index = self.pages.count() - 1
        if self._step_index <= 0:
            self._step_index = 0
        elif self._step_index > last_step_index:
            self._step_index = last_step_index

        self.pages.setCurrentIndex(self._step_index)

        if self._step_index == 0:
            self.step_label.setText(t("first_run.step.appearance"))
            self.title_label.setText(t("first_run.appearance.title"))
            self.subtitle_label.setText(t("first_run.appearance.subtitle"))
        elif self._step_index == 1:
            self.step_label.setText(t("first_run.step.sources"))
            self.title_label.setText(t("first_run.sources_step.title"))
            self.subtitle_label.setText(t("first_run.sources_step.subtitle"))
        else:
            self.step_label.setText(t("first_run.step.library"))
            self.title_label.setText(t("first_run.library_step.title"))
            self.subtitle_label.setText(t("first_run.library_step.subtitle"))

        is_last = self._step_index == last_step_index
        self.back_btn.setVisible(self._step_index > 0)
        self.next_btn.setVisible(not is_last)
        self.finish_btn.setVisible(is_last)
        self.finish_discover_btn.setVisible(is_last)

    def _go_back(self) -> None:
        self._step_index = max(0, self._step_index - 1)
        self._refresh_step_ui()

    def _go_next(self) -> None:
        self._step_index = min(self.pages.count() - 1, self._step_index + 1)
        self._refresh_step_ui()

    def _on_language_changed(self, _index: int) -> None:
        locale = str(self.language_combo.currentData() or "en").strip() or "en"
        if locale == self._active_locale:
            return
        self._set_locale(locale, update_combo=False)

    def _on_theme_changed(self, _index: int) -> None:
        preset = str(self.theme_combo.currentData() or app_styles.DEFAULT_THEME_PRESET).strip() or app_styles.DEFAULT_THEME_PRESET
        if preset not in app_styles.THEME_PRESETS:
            preset = app_styles.DEFAULT_THEME_PRESET
        app_styles.set_theme(preset, dict(app_styles.THEME_PRESETS[preset]), persist=True, propagate=True)
        if self.parent() is not None and hasattr(self.parent(), "apply_theme"):
            self.parent().apply_theme()
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(app_styles.PAGE_BG_STYLE)
        self.panel.setStyleSheet(app_styles.SURFACE_PANEL_STYLE)
        self.step_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.title_label.setStyleSheet(app_styles.PAGE_TITLE_LARGE_STYLE)
        self.subtitle_label.setStyleSheet(app_styles.TEXT_MUTED_BODY_STYLE)
        self.status_label.setStyleSheet(app_styles.STATUS_LABEL_STYLE)
        self.library_status_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.language_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.language_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.theme_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.theme_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.theme_status_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.library_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.library_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.source_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.source_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.scrapers_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.scrapers_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.updates_section_label.setStyleSheet(app_styles.SECTION_LABEL_TRANSPARENT_STYLE)
        self.updates_help_label.setStyleSheet(app_styles.TEXT_MUTED_TRANSPARENT_STYLE)
        self.library_updates_checkbox.setStyleSheet(app_styles.CHECKBOX_STYLE)
        self.library_root_input.setStyleSheet(app_styles.INPUT_STYLE)
        self.language_combo.setStyleSheet(app_styles.INPUT_STYLE)
        self.theme_combo.setStyleSheet(app_styles.INPUT_STYLE)
        self.provider_combo.setStyleSheet(app_styles.INPUT_STYLE)
        for widgets in self._source_mode_boxes.values():
            widgets["checkbox"].setStyleSheet(app_styles.CHECKBOX_STYLE)
            widgets["label"].setStyleSheet(app_styles.TEXT_MUTED_BODY_STYLE)
        for button in (self.back_btn, self.next_btn, self.finish_btn, self.finish_discover_btn, self.browse_btn):
            button.setStyleSheet(app_styles.BUTTON_STYLE)

    def _browse_library_root(self) -> None:
        current = str(self.library_root_input.text() or "").strip() or DEFAULT_LIBRARY_PATH
        folder = QFileDialog.getExistingDirectory(self, t("first_run.library.browse"), current)
        if folder:
            self.library_root_input.setText(folder)

    def _refresh_library_preview(self) -> None:
        root_text = str(self.library_root_input.text() or "").strip() or DEFAULT_LIBRARY_PATH
        root = Path(root_text)
        self.library_status_label.setText(
            t(
                "first_run.library.preview",
                webtoon_path=str(root / "webtoon"),
                manga_path=str(root / "manga"),
                webnovel_path=str(root / "webnovel"),
            )
        )

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _finish(self, open_target: str) -> None:
        root_text = str(self.library_root_input.text() or "").strip()
        if not root_text:
            self._set_status(t("first_run.validation.library_required"))
            return

        root = Path(root_text)
        if not root.is_absolute():
            self._set_status(t("first_run.validation.library_absolute"))
            return

        content_paths = {
            "webtoon": str(root / "webtoon"),
            "manga": str(root / "manga"),
            "webnovel": str(root / "webnovel"),
        }
        try:
            root.mkdir(parents=True, exist_ok=True)
            for path in content_paths.values():
                Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.exception("Failed to prepare first-run library folders")
            self._set_status(t("first_run.validation.library_create_failed", error=exc))
            return

        provider_site_name = str(self.provider_combo.currentData() or "").strip()
        save_library_path(str(root))
        save_library_content_paths(content_paths)
        save_site_availability(self._collect_site_availability())
        save_default_discovery_provider(provider_site_name)
        save_setting(LIBRARY_UPDATE_CHECK_ON_STARTUP_KEY, bool(self.library_updates_checkbox.isChecked()))
        self._open_target = "discover" if open_target == "discover" else "library"
        self.accept()
