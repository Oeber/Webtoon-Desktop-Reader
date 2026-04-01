import json
import sys

ACCENT = "#ff8a7a"
ACCENT_HOVER = "#ff9e90"
ACCENT_MUTED = "#ffc2b8"
BG = "#101010"
BG_ALT = "#151010"
SURFACE = "#171111"
SURFACE_ALT = "#1c1413"
SURFACE_SOFT = "#221615"
BORDER = "#4b302c"
BORDER_STRONG = "#704540"
TEXT = "#fff0ec"
TEXT_SOFT = "#ffd7cf"
TEXT_MUTED = "#d8b7b0"
TEXT_DIM = "#b18b84"

PAGE_BG_STYLE = f"background-color: {BG};"
PAGE_TITLE_STYLE = f"color: {TEXT}; font-size: 20px; font-weight: bold; background: transparent;"
SECTION_LABEL_STYLE = f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;"
ERROR_LABEL_STYLE = "color: #f44336; font-size: 12px; background: transparent;"
STATUS_LABEL_STYLE = f"color: {TEXT_DIM}; font-size: 12px; background: transparent;"
TRANSPARENT_BG_STYLE = "background: transparent;"
TEXT_MUTED_LABEL_STYLE = f"color: {TEXT_MUTED}; font-size: 12px;"
TEXT_DIM_LABEL_STYLE = f"color: {ACCENT_MUTED}; font-size: 12px;"

SURFACE_PANEL_STYLE = f"""
    QWidget {{
        background: {SURFACE};
        border: none;
        border-radius: 14px;
    }}
"""

PILL_LABEL_STYLE = f"""
    QLabel {{
        color: {TEXT_SOFT};
        background: {SURFACE_SOFT};
        border: none;
        border-radius: 11px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 700;
    }}
"""

CHECKBOX_STYLE = f"""
    QCheckBox {{
        color: {TEXT};
        font-size: 13px;
        spacing: 10px;
        background: transparent;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid #5a3834;
        background: #130f0f;
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT};
        border: 1px solid {ACCENT_MUTED};
    }}
"""

SLIDER_STYLE = f"""
    QSlider::groove:horizontal {{
        height: 8px;
        border-radius: 4px;
        background: {SURFACE_SOFT};
    }}
    QSlider::sub-page:horizontal {{
        border-radius: 4px;
        background: {ACCENT};
    }}
    QSlider::add-page:horizontal {{
        border-radius: 4px;
        background: {SURFACE_ALT};
    }}
    QSlider::handle:horizontal {{
        width: 18px;
        margin: -6px 0;
        border-radius: 9px;
        border: 1px solid {ACCENT_MUTED};
        background: #ffd4cb;
    }}
"""

VERTICAL_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: transparent;
        width: 18px;
        margin: 8px 4px 8px 4px;
        border: none;
        border-radius: 9px;
    }
    QScrollBar::handle:vertical {
        margin: 1px 2px 1px 2px;
        background: qlineargradient(
            x1: 0, y1: 0, x2: 1, y2: 0,
            stop: 0 rgba(255, 138, 122, 0.78),
            stop: 1 rgba(255, 194, 184, 0.92)
        );
        min-height: 52px;
        border-radius: 7px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    QScrollBar::handle:vertical:hover {
        background: qlineargradient(
            x1: 0, y1: 0, x2: 1, y2: 0,
            stop: 0 rgba(255, 158, 144, 0.92),
            stop: 1 rgba(255, 222, 216, 0.98)
        );
        border: 1px solid rgba(255, 255, 255, 0.14);
    }
    QScrollBar::handle:vertical:pressed {
        background: rgba(255, 212, 203, 0.98);
        border: 1px solid rgba(255, 255, 255, 0.2);
    }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {
        background: transparent;
        border: none;
        height: 0px;
    }
    QScrollBar:horizontal,
    QScrollBar::handle:horizontal,
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {
        border: none;
        background: transparent;
        height: 0px;
    }
"""

SCROLL_AREA_STYLE = f"""
    QScrollArea {{ border: none; background-color: {BG}; }}
""" + VERTICAL_SCROLLBAR_STYLE

CHAPTER_SCROLL_AREA_STYLE = f"""
    QScrollArea {{ border: none; background: {BG}; }}
""" + VERTICAL_SCROLLBAR_STYLE

INPUT_STYLE = f"""
    QLineEdit {{
        background: #181212;
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        color: {TEXT};
        font-size: 13px;
    }}
    QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
    QComboBox {{
        background: #181212;
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        color: {TEXT};
    }}
    QComboBox:focus {{ border: 1px solid {ACCENT}; }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        outline: none;
        padding: 4px;
        selection-background-color: #2b1c1b;
        selection-color: {TEXT};
    }}
"""

SEARCH_INPUT_STYLE = f"""
    QLineEdit {{
        background: #181212;
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding-left: 10px;
        color: {TEXT};
    }}
    QLineEdit:focus {{
        border: 1px solid {ACCENT};
    }}
"""

BUTTON_STYLE = f"""
    QPushButton {{
        background-color: {SURFACE_SOFT};
        color: {TEXT_SOFT};
        border: 1px solid #5a3834;
        border-radius: 6px;
        padding: 6px 16px;
        font-size: 13px;
    }}
    QPushButton:hover {{ background-color: #2b1c1b; border-color: {ACCENT}; color: {TEXT}; }}
    QPushButton:pressed {{ background-color: #352120; }}
    QPushButton:disabled {{
        background-color: #161111;
        color: #7a625d;
        border-color: #2a1c1a;
    }}
"""
BUTTON_STYLE_DISABLED = BUTTON_STYLE
EMPTY_STATE_LABEL_STYLE = "color: #9b7670; font-size: 13px; background: transparent;"

TAB_STYLE = f"""
    QTabWidget::pane {{
        border: none;
        background: {BG};
        border-radius: 0px;
        top: -2px;
        padding: 10px 0 0 0;
    }}
    QTabBar::tab {{
        background: #171212;
        color: #c09992;
        border: none;
        padding: 10px 18px;
        margin-right: 8px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        font-size: 12px;
        font-weight: 700;
    }}
    QTabBar::tab:selected {{
        background: #261716;
        color: {TEXT};
    }}
    QTabBar::tab:hover:!selected {{
        background: #1f1413;
        color: {TEXT_SOFT};
    }}
"""

LOG_META_STYLE = f"""
    QLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 12px;
    }}
"""

LOG_VIEW_STYLE = f"""
    QTextEdit {{
        background: {SURFACE_ALT};
        color: {TEXT_SOFT};
        border: 1px solid {BORDER};
        border-radius: 14px;
        padding: 10px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
    }}
""" + VERTICAL_SCROLLBAR_STYLE

TOP_BAR_STYLE = "background-color: #151010; border-bottom: 1px solid #35211f;"
HERO_PANEL_STYLE = "background-color: #151010;"
SECTION_HEADER_PANEL_STYLE = f"background: {BG};"
CHAPTER_LIST_WIDGET_STYLE = f"background: {BG};"
SUBTLE_META_LABEL_STYLE = f"color: {TEXT_MUTED}; font-size: 13px;"
SECONDARY_META_LABEL_STYLE = f"color: {TEXT_DIM}; font-size: 12px;"
WARNING_META_LABEL_STYLE = f"color: {ACCENT_HOVER}; font-size: 12px; font-weight: 600;"
DETAIL_TITLE_STYLE = f"color: {TEXT}; font-size: 28px; font-weight: 700;"
SECTION_CAPTION_STYLE = "color: #9b7670; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
BATCH_BAR_STYLE = """
    QWidget {
        background: #171111;
        border-top: 1px solid #30201e;
        border-bottom: 1px solid #30201e;
    }
"""
BATCH_LABEL_STYLE = f"color: {TEXT_SOFT}; font-size: 12px;"
TOOLBAR_TEXT_BUTTON_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: #d1aba4;
        border: none;
        font-size: 14px;
    }}
    QPushButton:hover {{ color: {TEXT}; }}
    QPushButton:disabled {{ color: #6d5551; }}
"""
PRIMARY_ACTION_BUTTON_STYLE = f"""
    QPushButton {{ background: {ACCENT}; color: #140d0d; border: none; border-radius: 6px;
                  font-size: 13px; font-weight: 600; }}
    QPushButton:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton:disabled {{ background: #2a1b19; color: #8c6e68; }}
"""
SECONDARY_ACTION_BUTTON_STYLE = f"""
    QPushButton {{ background: {SURFACE_SOFT}; color: {TEXT}; border: none; border-radius: 6px;
                  font-size: 13px; font-weight: 600; }}
    QPushButton:hover {{ background: #2b1d1b; }}
    QPushButton:disabled {{ background: #1b1413; color: #7d615c; }}
"""
MINIMAL_FILTER_BUTTON_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: #c09992;
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
    }}
    QPushButton:hover {{
        background: #1c1312;
        color: {TEXT};
    }}
    QPushButton:disabled {{
        background: transparent;
        color: #735955;
        border-color: #2a1c1a;
    }}
"""
MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE = MINIMAL_FILTER_BUTTON_STYLE + f"""
    QPushButton:checked {{
        background: #2a1716;
        color: {ACCENT};
        border-color: {ACCENT};
    }}
"""
MINIMAL_FILTER_BUTTON_GOLD_CHECKED_STYLE = MINIMAL_FILTER_BUTTON_STYLE + """
    QPushButton:checked {
        background: #2f2815;
        color: #f5c451;
        border-color: #f5c451;
    }
"""
CARD_ACTION_BUTTON_STYLE = """
    QPushButton {
        background: rgba(0,0,0,0.65);
        color: #fff;
        border: none;
        border-radius: 14px;
        padding: 0;
    }
    QPushButton:hover { background: rgba(80,80,80,0.90); }
"""
CARD_ACTION_BUTTON_DISABLED_STYLE = CARD_ACTION_BUTTON_STYLE + """
    QPushButton:disabled {
        background: rgba(0,0,0,0.45);
        color: #777;
    }
"""
CARD_CANCEL_BUTTON_STYLE = """
    QPushButton {
        background: rgba(104,26,26,0.92);
        color: #fff;
        border: none;
        border-radius: 14px;
        font-size: 10px;
        font-weight: 700;
        padding: 0;
    }
    QPushButton:hover { background: rgba(136,34,34,0.98); }
"""
CARD_DOTS_BUTTON_STYLE = """
    QPushButton {
        background: rgba(0,0,0,0.65);
        color: #fff;
        border: none;
        border-radius: 14px;
        font-size: 14px;
        padding: 0;
        text-align: center;
    }
    QPushButton:hover { background: rgba(80,80,80,0.90); }
"""
CARD_PROGRESS_OVERLAY_STYLE = """
    QWidget {
        background: rgba(0, 0, 0, 0.55);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 42px;
    }
"""
CARD_TITLE_LABEL_STYLE = f"""
    QLabel {{
        color: {TEXT_SOFT};
        font-size: 12px;
        background: transparent;
        border: none;
        padding: 0;
    }}
"""
CARD_INFO_LABEL_STYLE = f"""
    QLabel {{
        color: {TEXT_DIM};
        font-size: 10px;
        background: transparent;
        border: none;
        padding: 0 2px;
    }}
"""
NEW_CHIP_STYLE = """
    QLabel {
        color: #ffffff;
        background: #c62828;
        border: 1px solid #e53935;
        border-radius: 6px;
        padding: 0 5px;
        font-size: 8px;
        font-weight: 700;
    }
"""
CARD_MENU_STYLE = f"""
    QMenu {{
        background: #1a1211;
        color: {TEXT_SOFT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{ background: #2b1b1a; }}
"""
SECTION_HEADER_BUTTON_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: {TEXT};
        border: none;
        padding: 6px 0;
        font-size: 13px;
        font-weight: 700;
        text-align: left;
    }}
    QPushButton:hover {{ color: {TEXT}; }}
"""
SECTION_MENU_BUTTON_STYLE = f"""
    QPushButton {{
        background: #1c1413;
        color: {TEXT_SOFT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding-bottom: 2px;
    }}
    QPushButton:hover {{ background: #261918; border-color: {ACCENT}; }}
"""
DELETE_BUTTON_STYLE = """
    QPushButton {
        background: #4a1f1f;
        color: #ffffff;
        border: 1px solid #703030;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover { background: #5a2727; }
"""
CHAPTER_TOOL_BUTTON_STYLE = """
    QToolButton {
        border: none;
        padding: 4px;
        background: transparent;
    }
    QToolButton:hover {
        background: #222222;
        border-radius: 8px;
    }
"""
CHAPTER_ROW_STYLE = """
    QWidget { background: transparent; border-bottom: 1px solid #211615; }
    QWidget:hover { background: #171111; }
"""
CHAPTER_SELECT_SLOT_STYLE = "background: transparent; border: none;"
LAST_READ_ICON_STYLE = "padding-right: 4px;"
SIDEBAR_STYLE = "background-color: #140f0f; border-right: 1px solid #2b1b1a;"
SIDEBAR_BUTTON_STYLE = """
    QPushButton {
        background-color: transparent;
        color: #d8b7b0;
        border: 1px solid transparent;
        padding: 8px;
        text-align: left;
        border-radius: 6px;
    }
    QPushButton:hover {
        background-color: #241615;
    }
    QPushButton:pressed {
        background-color: #2d1b1a;
    }
    QPushButton[active="true"] {
        background-color: #2b1716;
        color: #fff0ec;
        border: 1px solid #5f322d;
    }
    QPushButton[active="true"]:hover {
        background-color: #341c1b;
    }
"""
VIEWER_RESUME_DIALOG_STYLE = f"""
    QDialog {{ background: #1a1211; color: {TEXT_SOFT}; }}
    QLabel  {{ color: {TEXT_SOFT}; font-size: 13px; background: transparent; }}
    QPushButton {{ padding: 8px 20px; border-radius: 6px;
                  font-size: 13px; font-weight: 600; border: none; }}
"""
VIEWER_RESUME_RESTART_BUTTON_STYLE = "QPushButton{background:#231716;color:#ffd7cf;} QPushButton:hover{background:#2c1c1b;}"
VIEWER_RESUME_CONTINUE_BUTTON_STYLE = "QPushButton{background:#ff8a7a;color:#140d0d;} QPushButton:hover{background:#ff9e90;}"
VIEWER_TOOLBAR_STYLE = """
    QWidget#viewerToolbar {
        background: rgba(17, 12, 12, 236);
        border: 1px solid rgba(112, 69, 64, 220);
        border-radius: 12px;
    }
"""
VIEWER_TOOLBAR_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #d8b7b0;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 0;
    }
    QPushButton:hover {
        background: rgba(43, 28, 27, 220);
        color: #fff0ec;
        border-color: rgba(255, 138, 122, 180);
    }
    QPushButton:checked {
        background: rgba(255, 138, 122, 34);
        color: #fff0ec;
        border-color: rgba(255, 138, 122, 220);
    }
    QPushButton:disabled {
        color: #624a46;
        border-color: transparent;
    }
"""
VIEWER_TOOLBAR_COMBO_STYLE = f"""
    QComboBox {{
        background: rgba(24, 18, 18, 236);
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 4px 28px 4px 10px;
        color: {TEXT};
        min-width: 170px;
        font-size: 12px;
    }}
    QComboBox:hover {{
        border-color: {ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        outline: none;
        selection-background-color: #2b1c1b;
        selection-color: {TEXT};
    }}
"""
VIEWER_ZOOM_LABEL_STYLE = "color: #d8b7b0; font-size: 12px;"
VIEWER_ZOOM_BUTTON_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 11px;
    }}
    QPushButton:hover {{ background: {SURFACE_ALT}; color: {TEXT}; border-color: {ACCENT}; }}
    QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {SURFACE}; }}
"""
DOWNLOAD_ENTRY_FRAME_STYLE = """
    QFrame {
        background-color: #171111;
        border: 1px solid #2d1d1b;
        border-radius: 8px;
    }
    QFrame[clickable="true"] {
        border: 1px solid #5a3834;
    }
    QFrame[clickable="true"]:hover {
        background-color: #1f1514;
        border: 1px solid #704540;
    }
"""
DOWNLOAD_ENTRY_THUMB_STYLE = """
    QLabel {
        background-color: #151010;
        border: 1px solid #2d1d1b;
        border-radius: 6px;
    }
"""
DOWNLOAD_ENTRY_NAME_STYLE = "color: #fff0ec; font-size: 13px; background: transparent; border: none; font-weight: 600;"
DOWNLOAD_ENTRY_SUB_LABEL_STYLE = "color: #b18b84; font-size: 11px; background: transparent; border: none;"
TRANSPARENT_BORDERLESS_STYLE = "background: transparent; border: none;"
APP_UPDATE_PROGRESS_STYLE = f"""
    QProgressBar {{
        background: #120f0f;
        color: {TEXT_SOFT};
        border: 1px solid {BORDER};
        border-radius: 7px;
        text-align: center;
        min-height: 14px;
    }}
    QProgressBar::chunk {{
        border-radius: 6px;
        background: qlineargradient(
            x1: 0, y1: 0, x2: 1, y2: 0,
            stop: 0 {ACCENT},
            stop: 1 {ACCENT_MUTED}
        );
    }}
"""

STACK_BG_STYLE = PAGE_BG_STYLE
MAIN_WINDOW_CHAPTER_OVERLAY_STYLE = "background-color: rgba(0, 0, 0, 140);"
VIEWER_LOADING_OVERLAY_STYLE = "background-color: rgba(0, 0, 0, 150);"
LOADING_TITLE_LABEL_STYLE = "color: #f2f2f2; font-size: 16px; font-weight: 600;"
LOADING_DETAIL_LABEL_STYLE = "color: #bdbdbd; font-size: 12px;"
SITE_AUTH_INFO_LABEL_STYLE = f"color: {TEXT}; font-size: 13px; background: transparent;"
SITE_AUTH_TOKEN_LABEL_STYLE = f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;"
STARTUP_UPDATE_DIALOG_STYLE = (
    "QDialog { background: #100c0c; color: #ffe7e2; }"
    "QWidget#updateDialogPanel { background: #171111; border: 1px solid #4b302c; border-radius: 18px; }"
    "QLabel { background: transparent; color: inherit; }"
)
SECTION_LABEL_EMPHASIS_STYLE = SECTION_LABEL_STYLE + " letter-spacing: 0.12em; font-weight: 700;"
PAGE_TITLE_LARGE_STYLE = PAGE_TITLE_STYLE + " font-size: 24px;"
TEXT_MUTED_BODY_STYLE = TEXT_MUTED_LABEL_STYLE + " background: transparent; font-size: 13px;"
TEXT_MUTED_TRANSPARENT_STYLE = TEXT_MUTED_LABEL_STYLE + " background: transparent;"
SECTION_LABEL_TRANSPARENT_STYLE = SECTION_LABEL_STYLE + " background: transparent;"
TRANSPARENT_SCROLL_AREA_STYLE = (
    "QScrollArea { background: transparent; border: none; }"
    "QWidget { background: transparent; }"
    + VERTICAL_SCROLLBAR_STYLE
)
DROP_INDICATOR_STYLE = "background: rgba(255, 138, 122, 0.95); border-radius: 2px;"
LIBRARY_CONTROLS_BAR_STYLE = f"background: {BG_ALT}; border-bottom: 1px solid {BORDER};"
LIBRARY_SCALE_PANEL_STYLE = f"background: {BG_ALT}; border: none; border-radius: 12px;"
LIBRARY_SCALE_VALUE_LABEL_STYLE = (
    f"color: {ACCENT_MUTED}; font-size: 12px; font-weight: 700;"
    f"background: {BG_ALT}; border: none; padding: 2px 0;"
)
DISCOVERY_COMBO_STYLE = f"""
    QComboBox {{
        background: #181212;
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        color: {TEXT};
        min-width: 180px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        selection-background-color: #2b1c1b;
    }}
"""
DISCOVERY_CARD_TITLE_STYLE = f"""
    QLabel {{
        color: {TEXT};
        font-size: 12px;
        font-weight: 500;
        background: transparent;
        border: none;
        padding: 0;
    }}
"""
DISCOVERY_CARD_COUNT_STYLE = TEXT_DIM_LABEL_STYLE
DISCOVERY_FILTER_BUTTON_STYLE = BUTTON_STYLE + f"""
    QPushButton:checked {{
        background-color: #2a1716;
        border-color: {ACCENT};
        color: {TEXT};
    }}
"""
DISCOVERY_LOADING_LABEL_STYLE = f"""
    QLabel {{
        color: {TEXT_SOFT};
        background: {SURFACE_ALT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 16px;
        font-size: 12px;
        font-weight: 600;
    }}
"""
THUMBNAIL_DIALOG_STYLE = f"""
    QDialog {{
        background: #120e0e;
        color: {TEXT};
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QLineEdit {{
        background: {SURFACE_ALT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus {{
        border-color: {ACCENT};
    }}
"""
THUMBNAIL_DIALOG_TITLE_STYLE = f"color: {TEXT}; font-size: 16px; font-weight: 700;"
THUMBNAIL_PREVIEW_STYLE = f"""
    QLabel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
        color: {TEXT_DIM};
        font-size: 11px;
    }}
"""
THUMBNAIL_DROPZONE_ICON_STYLE = f"color: {TEXT_DIM}; font-size: 32px; background: transparent; border: none;"
THUMBNAIL_DROPZONE_ICON_HOVER_STYLE = f"color: {ACCENT}; font-size: 32px; background: transparent; border: none;"
THUMBNAIL_DROPZONE_TITLE_STYLE = f"color: {TEXT}; font-size: 14px; font-weight: 600; background: transparent; border: none;"
THUMBNAIL_DROPZONE_SUBTITLE_STYLE = f"color: {TEXT_DIM}; font-size: 11px; background: transparent; border: none;"
THUMBNAIL_DIVIDER_LINE_STYLE = f"color: {BORDER};"
THUMBNAIL_STATUS_IDLE_STYLE = f"color: {TEXT_DIM}; font-size: 11px;"


def status_text_style(color: str) -> str:
    return f"color: {color}; font-size: 12px; background: transparent; border: none;"


def reliability_badge_style(color: str, background: str, border: str) -> str:
    return f"""
        QLabel {{
            color: {color};
            background: {background};
            border: 1px solid {border};
            border-radius: 7px;
            padding: 1px 8px;
            font-size: 10px;
            font-weight: 700;
        }}
    """


def reliability_badge_button_style(color: str, background: str, border: str) -> str:
    return f"""
        QPushButton {{
            color: {color};
            background: {background};
            border: 1px solid {border};
            border-radius: 7px;
            padding: 3px 10px;
            font-size: 10px;
            font-weight: 700;
            text-align: center;
        }}
        QPushButton:hover {{
            border-color: {color};
        }}
        QPushButton:disabled {{
            color: #8e706a;
            border-color: #5a423e;
        }}
    """


EDIT_DIALOG_STYLE = """
    QDialog { background: #120e0e; color: #ffe7e2; }
    QLabel { background: transparent; }
    QLineEdit, QDoubleSpinBox, QComboBox {
        background: #1a1312;
        color: #ffe7e2;
        border: 1px solid #4b302c;
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 13px;
    }
    QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {
        border-color: #ff8a7a;
    }
    QCheckBox {
        color: #ffd7cf;
        font-size: 13px;
    }
    QPushButton {
        background: #211615;
        color: #ffe7e2;
        border: 1px solid #4b302c;
        border-radius: 6px;
        padding: 8px 14px;
        font-size: 13px;
    }
    QPushButton:hover { background: #2b1c1b; }
"""
EDIT_DIALOG_TITLE_STYLE = "font-size: 18px; font-weight: 700; color: #fff0ec;"
EDIT_DIALOG_THUMB_PREVIEW_STYLE = """
    QLabel {
        background: #171111;
        border: 1px solid #3c2522;
        border-radius: 12px;
        color: #9b7670;
        font-size: 11px;
    }
"""
EDIT_DIALOG_FORM_FRAME_STYLE = """
    QFrame {
        background: #161010;
        border: 1px solid #30201e;
        border-radius: 10px;
    }
"""
EDIT_DIALOG_DELETE_BOX_STYLE = """
    QFrame {
        background: #1a1313;
        border: 1px solid #3a2020;
        border-radius: 10px;
    }
"""
EDIT_DIALOG_DELETE_TEXT_STYLE = "color: #d2b2b2;"
FORM_LABEL_STYLE = "color: #ffd7cf; font-size: 13px;"


def card_badge_button_style(accent: bool = False) -> str:
    color = ACCENT if accent else "#c09992"
    bg_hover = "#2a1716" if accent else "#241615"
    return f"""
        QPushButton {{
            color: {color};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
            border: none;
            text-align: left;
            padding: 0 2px;
        }}
        QPushButton:hover {{
            background: {bg_hover};
            border-radius: 4px;
        }}
    """


def section_empty_state_style(border: str, background: str, text: str) -> str:
    return f"""
        QLabel {{
            color: {text};
            background: {background};
            border: 1px dashed {border};
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
            padding: 12px;
        }}
    """


def card_image_border_style(color: str, radius: int) -> str:
    return f"""
        QLabel {{
            background-color: #171111;
            border-radius: {radius}px;
            border: 1px solid {color};
        }}
    """


def detail_thumb_style(radius: int) -> str:
    return f"""
        QLabel {{
            background: #171111;
            border-radius: {radius}px;
            border: 1px solid #3c2522;
        }}
    """


def chapter_name_style(color: str) -> str:
    return f"color: {color}; font-size: 14px; border: none;"


def status_label_color_style(color: str) -> str:
    return f"color: {color}; font-size: 11px;"


def sidebar_button_style(expanded: bool) -> str:
    extra_style = (
        """
            QPushButton {
                padding: 8px 10px;
                text-align: left;
            }
        """
        if expanded else
        """
            QPushButton {
                padding: 8px 0;
                text-align: center;
            }
        """
    )
    return SIDEBAR_BUTTON_STYLE + extra_style


def library_scale_slider_style() -> str:
    return f"""
        QSlider {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 4px 6px;
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            border-radius: 3px;
            background: {SURFACE_SOFT};
        }}
        QSlider::sub-page:horizontal {{
            border-radius: 3px;
            background: {ACCENT_MUTED};
        }}
        QSlider::add-page:horizontal {{
            border-radius: 3px;
            background: {BG_ALT};
        }}
        QSlider::handle:horizontal {{
            width: 12px;
            margin: -3px 0;
            border-radius: 6px;
            border: 1px solid #ffe5de;
            background: #ffd4cb;
        }}
    """


def thumbnail_dropzone_style(hovered: bool) -> str:
    border = ACCENT if hovered else BORDER
    background = "#241615" if hovered else SURFACE
    return f"""
        QFrame {{
            background: {background};
            border: 2px dashed {border};
            border-radius: 12px;
        }}
    """


def thumbnail_action_button_style(primary: bool = False) -> str:
    if primary:
        return f"""
            QPushButton {{
                background: {ACCENT};
                color: #fff;
                border: none;
                border-radius: 6px;
                padding: 8px 0;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #d86f60; }}
            QPushButton:disabled {{ background: #2a2a2a; color: {TEXT_DIM}; }}
        """
    return f"""
        QPushButton {{
            background: {SURFACE_ALT};
            color: {TEXT};
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 8px 0;
            font-size: 13px;
        }}
        QPushButton:hover {{ background: {BORDER}; }}
    """


def action_button_checked_style(color: str) -> str:
    return CARD_ACTION_BUTTON_STYLE + f"""
        QPushButton:checked {{ background: {color}; }}
    """


def sized_button_style(base_style: str, min_height: int, *, padding: str = "0 16px", font_size: int = 14) -> str:
    return base_style + f"""
        QPushButton {{
            min-height: {min_height}px;
            padding: {padding};
            font-size: {font_size}px;
        }}
    """


_STYLE_EXPORT_NAMES = tuple(
    name
    for name, value in globals().items()
    if name.isupper() and isinstance(value, str)
)
_BASE_STYLE_VALUES = {name: globals()[name] for name in _STYLE_EXPORT_NAMES}

THEME_PRESET_KEY = "app_theme_preset"
THEME_CUSTOM_COLORS_KEY = "app_theme_custom_colors"
DEFAULT_THEME_PRESET = "ember"
CUSTOM_THEME_PRESET = "custom"
THEME_BASE_KEYS = ("accent", "bg", "surface", "border", "text")
THEME_PRESETS = {
    "ember": {
        "label": "Ember",
        "accent": "#ff8a7a",
        "bg": "#101010",
        "surface": "#171111",
        "border": "#4b302c",
        "text": "#fff0ec",
    },
    "ocean": {
        "label": "Ocean",
        "accent": "#4db6ff",
        "bg": "#08131b",
        "surface": "#10202d",
        "border": "#2f5f80",
        "text": "#e7f7ff",
    },
    "forest": {
        "label": "Forest",
        "accent": "#7ddc8b",
        "bg": "#0b140f",
        "surface": "#132018",
        "border": "#35583c",
        "text": "#edf9ef",
    },
    "light": {
        "label": "Light",
        "accent": "#d0674f",
        "bg": "#f5eee9",
        "surface": "#fffaf7",
        "border": "#c79f93",
        "text": "#241917",
    },
}
_CURRENT_THEME_PRESET = DEFAULT_THEME_PRESET
_CURRENT_THEME_COLORS = dict(THEME_PRESETS[DEFAULT_THEME_PRESET])


def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _normalize_hex(color: str, fallback: str) -> str:
    value = str(color or "").strip()
    if len(value) == 4 and value.startswith("#"):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            return fallback.lower()
    return fallback.lower()


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    normalized = _normalize_hex(color, "#000000")
    return tuple(int(normalized[index:index + 2], 16) for index in (1, 3, 5))


def _rgb_to_hex(red: int, green: int, blue: int) -> str:
    return f"#{_clamp_channel(red):02x}{_clamp_channel(green):02x}{_clamp_channel(blue):02x}"


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    alpha_value = max(0.0, min(1.0, float(alpha)))
    return f"rgba({red}, {green}, {blue}, {alpha_value:.2f})"


def _mix(color_a: str, color_b: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, float(ratio)))
    a_r, a_g, a_b = _hex_to_rgb(color_a)
    b_r, b_g, b_b = _hex_to_rgb(color_b)
    return _rgb_to_hex(
        a_r + (b_r - a_r) * ratio,
        a_g + (b_g - a_g) * ratio,
        a_b + (b_b - a_b) * ratio,
    )


def _build_theme_colors(theme: dict | None = None) -> dict[str, str]:
    source = dict(THEME_PRESETS[DEFAULT_THEME_PRESET])
    source.update({key: value for key, value in dict(theme or {}).items() if key in THEME_BASE_KEYS})
    accent = _normalize_hex(source["accent"], THEME_PRESETS[DEFAULT_THEME_PRESET]["accent"])
    bg = _normalize_hex(source["bg"], THEME_PRESETS[DEFAULT_THEME_PRESET]["bg"])
    surface = _normalize_hex(source["surface"], THEME_PRESETS[DEFAULT_THEME_PRESET]["surface"])
    border = _normalize_hex(source["border"], THEME_PRESETS[DEFAULT_THEME_PRESET]["border"])
    text = _normalize_hex(source["text"], THEME_PRESETS[DEFAULT_THEME_PRESET]["text"])
    return {
        "accent": accent,
        "accent_hover": _mix(accent, text, 0.18),
        "accent_muted": _mix(accent, text, 0.46),
        "bg": bg,
        "bg_alt": _mix(bg, surface, 0.35),
        "surface": surface,
        "surface_alt": _mix(surface, border, 0.22),
        "surface_soft": _mix(surface, bg, 0.22),
        "border": border,
        "border_strong": _mix(border, accent, 0.42),
        "text": text,
        "text_soft": _mix(text, accent, 0.12),
        "text_muted": _mix(text, surface, 0.22),
        "text_dim": _mix(text, border, 0.35),
    }


def _literal_replacements(colors: dict[str, str]) -> dict[str, str]:
    return {
        "#ff8a7a": colors["accent"],
        "#ff9e90": colors["accent_hover"],
        "#ffc2b8": colors["accent_muted"],
        "#101010": colors["bg"],
        "#151010": colors["bg_alt"],
        "#171111": colors["surface"],
        "#1c1413": colors["surface_alt"],
        "#221615": colors["surface_soft"],
        "#4b302c": colors["border"],
        "#704540": colors["border_strong"],
        "#fff0ec": colors["text"],
        "#ffd7cf": colors["text_soft"],
        "#d8b7b0": colors["text_muted"],
        "#b18b84": colors["text_dim"],
        "#9b7670": _mix(colors["text"], colors["border"], 0.45),
        "#181212": _mix(colors["surface"], colors["bg"], 0.28),
        "#130f0f": _mix(colors["bg"], colors["surface"], 0.08),
        "#2b1c1b": _mix(colors["surface"], colors["accent"], 0.18),
        "#2a1716": _mix(colors["surface"], colors["accent"], 0.22),
        "#2f2815": _mix(colors["surface_soft"], colors["accent"], 0.18),
        "#2b1d1b": _mix(colors["surface"], colors["border"], 0.14),
        "#30201e": _mix(colors["border"], colors["surface"], 0.26),
        "#35211f": _mix(colors["border"], colors["accent"], 0.16),
        "#2d1d1b": _mix(colors["surface"], colors["border"], 0.18),
        "#3c2522": _mix(colors["border"], colors["surface"], 0.18),
        "#f6ddd6": _mix(colors["text"], colors["accent"], 0.10),
        "#b8948d": _mix(colors["text"], colors["border"], 0.42),
        "#140f0f": _mix(colors["bg"], colors["surface"], 0.18),
        "#140e0c": _mix(colors["bg"], colors["surface_soft"], 0.12),
        "#f6ece5": _mix(colors["text"], colors["surface"], 0.05),
        "#171212": _mix(colors["surface"], colors["bg"], 0.20),
        "#c09992": _mix(colors["text"], colors["border"], 0.30),
        "#261716": _mix(colors["surface"], colors["accent"], 0.20),
        "#1f1413": _mix(colors["surface"], colors["border"], 0.12),
        "#241615": _mix(colors["surface"], colors["accent"], 0.14),
        "#2d1b1a": _mix(colors["surface"], colors["accent"], 0.24),
        "#2b1716": _mix(colors["surface"], colors["accent"], 0.22),
        "#341c1b": _mix(colors["surface"], colors["accent"], 0.30),
        "#5f322d": _mix(colors["border"], colors["accent"], 0.34),
        "#5a3834": _mix(colors["border"], colors["accent"], 0.18),
        "#2a1c1a": _mix(colors["surface"], colors["border"], 0.20),
        "#6d5551": _mix(colors["text_muted"], colors["border"], 0.44),
        "#7a625d": _mix(colors["text_muted"], colors["border"], 0.52),
        "#8c6e68": _mix(colors["text"], colors["border"], 0.58),
        "#7d615c": _mix(colors["text_muted"], colors["border"], 0.56),
        "#735955": _mix(colors["text_muted"], colors["border"], 0.50),
        "#2b1b1a": _mix(colors["surface"], colors["border"], 0.16),
        "#352120": _mix(colors["surface"], colors["accent"], 0.30),
        "#161111": _mix(colors["surface"], colors["bg"], 0.10),
        "#0d0d0d": _mix(colors["bg"], colors["surface"], 0.06),
        "#211615": _mix(colors["surface"], colors["border"], 0.12),
        "#1c1312": _mix(colors["surface"], colors["border"], 0.10),
        "#1f1514": _mix(colors["surface"], colors["border"], 0.14),
        "#261918": _mix(colors["surface"], colors["accent"], 0.18),
        "#222222": _mix(colors["surface"], colors["border"], 0.24),
        "#d1aba4": _mix(colors["text"], colors["border"], 0.24),
        "#d8d8d8": colors["text"],
        "rgba(255, 138, 122, 0.78)": _rgba(colors["accent"], 0.78),
        "rgba(255, 194, 184, 0.92)": _rgba(colors["accent_muted"], 0.92),
        "rgba(255, 158, 144, 0.92)": _rgba(colors["accent_hover"], 0.92),
        "rgba(255, 222, 216, 0.98)": _rgba(colors["text_soft"], 0.98),
        "rgba(255, 212, 203, 0.98)": _rgba(colors["text_soft"], 0.98),
        "rgba(112, 69, 64, 220)": _rgba(colors["border_strong"], 220/255),
        "rgba(43, 28, 27, 220)": _rgba(_mix(colors["surface"], colors["accent"], 0.16), 220/255),
        "rgba(255, 138, 122, 180)": _rgba(colors["accent"], 180/255),
        "rgba(255, 138, 122, 34)": _rgba(colors["accent"], 34/255),
        "rgba(255, 138, 122, 220)": _rgba(colors["accent"], 220/255),
        "rgba(24, 18, 18, 236)": _rgba(_mix(colors["surface"], colors["bg"], 0.28), 236/255),
        "rgba(17, 12, 12, 236)": _rgba(_mix(colors["bg"], colors["surface"], 0.12), 236/255),
        "rgba(255, 138, 122, 0.95)": _rgba(colors["accent"], 0.95),
        "#666666": _mix(colors["text_muted"], colors["border"], 0.40),
        "#666": _mix(colors["text_muted"], colors["border"], 0.40),
    }


def _replace_theme_literals(value: str, replacements: dict[str, str]) -> str:
    themed = str(value)
    for old, new in replacements.items():
        themed = themed.replace(old, new)
    return themed


def _propagate_theme_globals() -> None:
    themed_values = {name: globals()[name] for name in _STYLE_EXPORT_NAMES}
    for module in list(sys.modules.values()):
        module_name = getattr(module, "__name__", "")
        if not module_name.startswith("gui.") and not module_name.startswith("core."):
            continue
        for name, value in themed_values.items():
            if hasattr(module, name):
                try:
                    setattr(module, name, value)
                except Exception:
                    continue


def current_theme_preset() -> str:
    return _CURRENT_THEME_PRESET


def current_theme_colors() -> dict[str, str]:
    return {key: _CURRENT_THEME_COLORS.get(key, "") for key in THEME_BASE_KEYS}


def save_theme_selection(preset: str, custom_colors: dict | None = None) -> None:
    from stores.app_settings_store import get_instance as get_app_settings_store

    store = get_app_settings_store()
    normalized_preset = str(preset or DEFAULT_THEME_PRESET).strip().casefold() or DEFAULT_THEME_PRESET
    payload = {
        key: _normalize_hex(value, THEME_PRESETS[DEFAULT_THEME_PRESET][key])
        for key, value in dict(custom_colors or {}).items()
        if key in THEME_BASE_KEYS
    }
    store.set_many({
        THEME_PRESET_KEY: normalized_preset,
        THEME_CUSTOM_COLORS_KEY: payload,
    })


def load_theme_selection() -> tuple[str, dict[str, str]]:
    from stores.app_settings_store import get_instance as get_app_settings_store

    store = get_app_settings_store()
    preset = str(store.get(THEME_PRESET_KEY, DEFAULT_THEME_PRESET) or DEFAULT_THEME_PRESET).strip().casefold()
    if preset not in THEME_PRESETS and preset != CUSTOM_THEME_PRESET:
        preset = DEFAULT_THEME_PRESET
    custom = store.get(THEME_CUSTOM_COLORS_KEY, {})
    if not isinstance(custom, dict):
        custom = {}
    return preset, {
        key: _normalize_hex(custom.get(key), THEME_PRESETS[DEFAULT_THEME_PRESET][key])
        for key in THEME_BASE_KEYS
        if custom.get(key)
    }


def set_theme(preset: str, custom_colors: dict | None = None, *, persist: bool = False, propagate: bool = True) -> None:
    global _CURRENT_THEME_PRESET, _CURRENT_THEME_COLORS

    normalized_preset = str(preset or DEFAULT_THEME_PRESET).strip().casefold() or DEFAULT_THEME_PRESET
    if normalized_preset not in THEME_PRESETS and normalized_preset != CUSTOM_THEME_PRESET:
        normalized_preset = DEFAULT_THEME_PRESET

    base_preset = DEFAULT_THEME_PRESET if normalized_preset == CUSTOM_THEME_PRESET else normalized_preset
    resolved_colors = dict(THEME_PRESETS[base_preset])
    resolved_colors.update({
        key: value
        for key, value in dict(custom_colors or {}).items()
        if key in THEME_BASE_KEYS and str(value or "").strip()
    })
    derived = _build_theme_colors(resolved_colors)
    replacements = _literal_replacements(derived)

    globals()["ACCENT"] = derived["accent"]
    globals()["ACCENT_HOVER"] = derived["accent_hover"]
    globals()["ACCENT_MUTED"] = derived["accent_muted"]
    globals()["BG"] = derived["bg"]
    globals()["BG_ALT"] = derived["bg_alt"]
    globals()["SURFACE"] = derived["surface"]
    globals()["SURFACE_ALT"] = derived["surface_alt"]
    globals()["SURFACE_SOFT"] = derived["surface_soft"]
    globals()["BORDER"] = derived["border"]
    globals()["BORDER_STRONG"] = derived["border_strong"]
    globals()["TEXT"] = derived["text"]
    globals()["TEXT_SOFT"] = derived["text_soft"]
    globals()["TEXT_MUTED"] = derived["text_muted"]
    globals()["TEXT_DIM"] = derived["text_dim"]

    for name, value in _BASE_STYLE_VALUES.items():
        if name in {
            "ACCENT",
            "ACCENT_HOVER",
            "ACCENT_MUTED",
            "BG",
            "BG_ALT",
            "SURFACE",
            "SURFACE_ALT",
            "SURFACE_SOFT",
            "BORDER",
            "BORDER_STRONG",
            "TEXT",
            "TEXT_SOFT",
            "TEXT_MUTED",
            "TEXT_DIM",
        }:
            continue
        globals()[name] = _replace_theme_literals(value, replacements)

    _CURRENT_THEME_PRESET = normalized_preset
    _CURRENT_THEME_COLORS = {
        "accent": resolved_colors["accent"],
        "bg": resolved_colors["bg"],
        "surface": resolved_colors["surface"],
        "border": resolved_colors["border"],
        "text": resolved_colors["text"],
    }

    if persist:
        save_theme_selection(normalized_preset, _CURRENT_THEME_COLORS)
    if propagate:
        _propagate_theme_globals()


def initialize_from_settings() -> None:
    preset, custom_colors = load_theme_selection()
    set_theme(preset, custom_colors, persist=False, propagate=False)
