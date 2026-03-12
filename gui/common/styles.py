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
"""
BUTTON_STYLE_DISABLED = BUTTON_STYLE + """
    QPushButton:disabled { color: #6f5450; border-color: #2d1d1b; }
"""
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
        background: #171111;
        border: none;
        border-radius: 10px;
        padding: 10px 12px;
    }}
"""

LOG_VIEW_STYLE = f"""
    QTextEdit {{
        background: #0d0d0d;
        color: {TEXT_SOFT};
        border: none;
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
"""
PRIMARY_ACTION_BUTTON_STYLE = f"""
    QPushButton {{ background: {ACCENT}; color: #140d0d; border: none; border-radius: 6px;
                  font-size: 13px; font-weight: 600; }}
    QPushButton:hover {{ background: {ACCENT_HOVER}; }}
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
VIEWER_ZOOM_LABEL_STYLE = "color: #d8b7b0; font-size: 12px;"
VIEWER_ZOOM_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #c09992;
        border: 1px solid #4b302c;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 11px;
    }
    QPushButton:hover { background: #241615; color: #fff0ec; }
    QPushButton:disabled { color: #624a46; border-color: #261817; }
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


def status_text_style(color: str) -> str:
    return f"color: {color}; font-size: 12px; background: transparent; border: none;"


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
