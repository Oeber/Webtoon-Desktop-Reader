PAGE_BG_STYLE = "background-color: #121212;"
PAGE_TITLE_STYLE = "color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;"
SECTION_LABEL_STYLE = "color: #aaaaaa; font-size: 12px; background: transparent;"
ERROR_LABEL_STYLE = "color: #f44336; font-size: 12px; background: transparent;"
STATUS_LABEL_STYLE = "color: #666; font-size: 12px; background: transparent;"
TRANSPARENT_BG_STYLE = "background: transparent;"
TEXT_MUTED_LABEL_STYLE = "color: #aaaaaa; font-size: 12px;"
TEXT_DIM_LABEL_STYLE = "color: #cccccc; font-size: 12px;"

SURFACE_PANEL_STYLE = """
    QWidget {
        background: #161616;
        border: none;
        border-radius: 14px;
    }
"""

PILL_LABEL_STYLE = """
    QLabel {
        color: #d8d8d8;
        background: #202020;
        border: none;
        border-radius: 11px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 700;
    }
"""

CHECKBOX_STYLE = """
    QCheckBox {
        color: #e6e6e6;
        font-size: 13px;
        spacing: 10px;
        background: transparent;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid #3a3a3a;
        background: #151515;
    }
    QCheckBox::indicator:checked {
        background: #d9d9d9;
        border: 1px solid #e5e5e5;
    }
"""

SLIDER_STYLE = """
    QSlider::groove:horizontal {
        height: 8px;
        border-radius: 4px;
        background: #222222;
    }
    QSlider::sub-page:horizontal {
        border-radius: 4px;
        background: #cfcfcf;
    }
    QSlider::add-page:horizontal {
        border-radius: 4px;
        background: #2d2d2d;
    }
    QSlider::handle:horizontal {
        width: 18px;
        margin: -6px 0;
        border-radius: 9px;
        border: 1px solid #d8d8d8;
        background: #f2f2f2;
    }
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
            stop: 0 rgba(146, 146, 146, 0.78),
            stop: 1 rgba(196, 196, 196, 0.92)
        );
        min-height: 52px;
        border-radius: 7px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    QScrollBar::handle:vertical:hover {
        background: qlineargradient(
            x1: 0, y1: 0, x2: 1, y2: 0,
            stop: 0 rgba(174, 174, 174, 0.92),
            stop: 1 rgba(232, 232, 232, 0.98)
        );
        border: 1px solid rgba(255, 255, 255, 0.14);
    }
    QScrollBar::handle:vertical:pressed {
        background: rgba(245, 245, 245, 0.98);
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

SCROLL_AREA_STYLE = """
    QScrollArea { border: none; background-color: #121212; }
""" + VERTICAL_SCROLLBAR_STYLE

CHAPTER_SCROLL_AREA_STYLE = """
    QScrollArea { border: none; background: #121212; }
""" + VERTICAL_SCROLLBAR_STYLE

INPUT_STYLE = """
    QLineEdit {
        background: #1a1a1a;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 6px 10px;
        color: #eeeeee;
        font-size: 13px;
    }
    QLineEdit:focus { border: 1px solid #555; }
"""

SEARCH_INPUT_STYLE = """
    QLineEdit {
        background: #1a1a1a;
        border: 1px solid #333;
        border-radius: 6px;
        padding-left: 10px;
        color: #eee;
    }
    QLineEdit:focus {
        border: 1px solid #666;
    }
"""

BUTTON_STYLE = """
    QPushButton {
        background-color: #2a2a2a;
        color: #cccccc;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 6px 16px;
        font-size: 13px;
    }
    QPushButton:hover { background-color: #333; }
    QPushButton:pressed { background-color: #3a3a3a; }
"""
BUTTON_STYLE_DISABLED = BUTTON_STYLE + """
    QPushButton:disabled { color: #555; border-color: #222; }
"""
EMPTY_STATE_LABEL_STYLE = "color: #777777; font-size: 13px; background: transparent;"

TAB_STYLE = """
    QTabWidget::pane {
        border: none;
        background: #121212;
        border-radius: 0px;
        top: -2px;
        padding: 10px 0 0 0;
    }
    QTabBar::tab {
        background: #171717;
        color: #8f959e;
        border: none;
        padding: 10px 18px;
        margin-right: 8px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        font-size: 12px;
        font-weight: 700;
    }
    QTabBar::tab:selected {
        background: #202020;
        color: #f1f1f1;
    }
    QTabBar::tab:hover:!selected {
        background: #1c1c1c;
        color: #cfcfcf;
    }
"""

LOG_META_STYLE = """
    QLabel {
        color: #a9b0b9;
        font-size: 12px;
        background: #1a1a1a;
        border: none;
        border-radius: 10px;
        padding: 10px 12px;
    }
"""

LOG_VIEW_STYLE = """
    QTextEdit {
        background: #101010;
        color: #d8d8d8;
        border: none;
        border-radius: 14px;
        padding: 10px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
    }
""" + VERTICAL_SCROLLBAR_STYLE

TOP_BAR_STYLE = "background-color: #181818; border-bottom: 1px solid #222;"
HERO_PANEL_STYLE = "background-color: #181818;"
SECTION_HEADER_PANEL_STYLE = "background: #121212;"
CHAPTER_LIST_WIDGET_STYLE = "background: #121212;"
SUBTLE_META_LABEL_STYLE = "color: #aaa; font-size: 13px;"
SECONDARY_META_LABEL_STYLE = "color: #888; font-size: 12px;"
WARNING_META_LABEL_STYLE = "color: #f0a500; font-size: 12px; font-weight: 600;"
DETAIL_TITLE_STYLE = "color: #fff; font-size: 28px; font-weight: 700;"
SECTION_CAPTION_STYLE = "color: #555; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
BATCH_BAR_STYLE = """
    QWidget {
        background: #171717;
        border-top: 1px solid #242424;
        border-bottom: 1px solid #242424;
    }
"""
BATCH_LABEL_STYLE = "color: #d0d0d0; font-size: 12px;"
TOOLBAR_TEXT_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #aaa;
        border: none;
        font-size: 14px;
    }
    QPushButton:hover { color: #fff; }
"""
PRIMARY_ACTION_BUTTON_STYLE = """
    QPushButton { background: #2979ff; color: #fff; border: none; border-radius: 6px;
                  font-size: 13px; font-weight: 600; }
    QPushButton:hover { background: #448aff; }
"""
SECONDARY_ACTION_BUTTON_STYLE = """
    QPushButton { background: #2a2a2a; color: #fff; border: none; border-radius: 6px;
                  font-size: 13px; font-weight: 600; }
    QPushButton:hover { background: #333333; }
    QPushButton:disabled { background: #232323; color: #777; }
"""
MINIMAL_FILTER_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #888;
        border: 1px solid #2a2a2a;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
    }
    QPushButton:hover {
        background: #1a1a1a;
        color: #fff;
    }
"""
MINIMAL_FILTER_BUTTON_BLUE_CHECKED_STYLE = MINIMAL_FILTER_BUTTON_STYLE + """
    QPushButton:checked {
        background: #1a2a3a;
        color: #2979ff;
        border-color: #2979ff;
    }
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
        padding-bottom: 2px;
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
CARD_TITLE_LABEL_STYLE = """
    QLabel {
        color: #e0e0e0;
        font-size: 12px;
        background: transparent;
        border: none;
        padding: 0;
    }
"""
CARD_INFO_LABEL_STYLE = """
    QLabel {
        color: #9a9a9a;
        font-size: 10px;
        background: transparent;
        border: none;
        padding: 0 2px;
    }
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
CARD_MENU_STYLE = """
    QMenu {
        background: #1e1e1e;
        color: #e0e0e0;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 4px;
    }
    QMenu::item {
        padding: 6px 20px;
        border-radius: 4px;
    }
    QMenu::item:selected { background: #2e2e2e; }
"""
SECTION_HEADER_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #f0f0f0;
        border: none;
        padding: 6px 0;
        font-size: 13px;
        font-weight: 700;
        text-align: left;
    }
    QPushButton:hover { color: #ffffff; }
"""
SECTION_MENU_BUTTON_STYLE = """
    QPushButton {
        background: #202020;
        color: #cccccc;
        border: 1px solid #303030;
        border-radius: 6px;
        padding-bottom: 2px;
    }
    QPushButton:hover { background: #282828; }
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
    QWidget { background: transparent; border-bottom: 1px solid #1e1e1e; }
    QWidget:hover { background: #1a1a1a; }
"""
CHAPTER_SELECT_SLOT_STYLE = "background: transparent; border: none;"
LAST_READ_ICON_STYLE = "padding-right: 4px;"
SIDEBAR_STYLE = "background-color: #1e1e1e;"
SIDEBAR_BUTTON_STYLE = """
    QPushButton {
        background-color: transparent;
        color: #cccccc;
        border: none;
        padding: 8px;
        text-align: left;
        border-radius: 6px;
    }
    QPushButton:hover {
        background-color: #2a2a2a;
    }
    QPushButton:pressed {
        background-color: #333333;
    }
"""
VIEWER_RESUME_DIALOG_STYLE = """
    QDialog { background: #1e1e1e; color: #e0e0e0; }
    QLabel  { color: #e0e0e0; font-size: 13px; background: transparent; }
    QPushButton { padding: 8px 20px; border-radius: 6px;
                  font-size: 13px; font-weight: 600; border: none; }
"""
VIEWER_RESUME_RESTART_BUTTON_STYLE = "QPushButton{background:#2e2e2e;color:#ccc;} QPushButton:hover{background:#3a3a3a;}"
VIEWER_RESUME_CONTINUE_BUTTON_STYLE = "QPushButton{background:#2979ff;color:#fff;} QPushButton:hover{background:#448aff;}"
VIEWER_ZOOM_LABEL_STYLE = "color: #aaa; font-size: 12px;"
VIEWER_ZOOM_BUTTON_STYLE = """
    QPushButton {
        background: transparent;
        color: #888;
        border: 1px solid #333;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 11px;
    }
    QPushButton:hover { background: #2a2a2a; color: #fff; }
    QPushButton:disabled { color: #444; border-color: #222; }
"""
DOWNLOAD_ENTRY_FRAME_STYLE = """
    QFrame {
        background-color: #1a1a1a;
        border: 1px solid #2a2a2a;
        border-radius: 8px;
    }
    QFrame[clickable="true"] {
        border: 1px solid #3a3a3a;
    }
    QFrame[clickable="true"]:hover {
        background-color: #202020;
        border: 1px solid #4a4a4a;
    }
"""
DOWNLOAD_ENTRY_THUMB_STYLE = """
    QLabel {
        background-color: #161616;
        border: 1px solid #2a2a2a;
        border-radius: 6px;
    }
"""
DOWNLOAD_ENTRY_NAME_STYLE = "color: #eeeeee; font-size: 13px; background: transparent; border: none; font-weight: 600;"
DOWNLOAD_ENTRY_SUB_LABEL_STYLE = "color: #777777; font-size: 11px; background: transparent; border: none;"
TRANSPARENT_BORDERLESS_STYLE = "background: transparent; border: none;"


def status_text_style(color: str) -> str:
    return f"color: {color}; font-size: 12px; background: transparent; border: none;"


EDIT_DIALOG_STYLE = """
    QDialog { background: #141414; color: #e8e8e8; }
    QLabel { background: transparent; }
    QLineEdit, QDoubleSpinBox, QComboBox {
        background: #1f1f1f;
        color: #e8e8e8;
        border: 1px solid #333333;
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 13px;
    }
    QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {
        border-color: #2979ff;
    }
    QCheckBox {
        color: #d0d0d0;
        font-size: 13px;
    }
    QPushButton {
        background: #252525;
        color: #e8e8e8;
        border: 1px solid #343434;
        border-radius: 6px;
        padding: 8px 14px;
        font-size: 13px;
    }
    QPushButton:hover { background: #303030; }
"""
EDIT_DIALOG_TITLE_STYLE = "font-size: 18px; font-weight: 700; color: #ffffff;"
EDIT_DIALOG_THUMB_PREVIEW_STYLE = """
    QLabel {
        background: #1c1c1c;
        border: 1px solid #2f2f2f;
        border-radius: 12px;
        color: #777777;
        font-size: 11px;
    }
"""
EDIT_DIALOG_FORM_FRAME_STYLE = """
    QFrame {
        background: #191919;
        border: 1px solid #262626;
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
FORM_LABEL_STYLE = "color: #b8b8b8; font-size: 13px;"


def card_badge_button_style(accent: bool = False) -> str:
    color = "#2979ff" if accent else "#888"
    bg_hover = "#1a2a4a" if accent else "#2a2a2a"
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
            background-color: #1e1e1e;
            border-radius: {radius}px;
            border: 1px solid {color};
        }}
    """


def detail_thumb_style(radius: int) -> str:
    return f"""
        QLabel {{
            background: #1e1e1e;
            border-radius: {radius}px;
            border: 1px solid #2a2a2a;
        }}
    """


def chapter_name_style(color: str) -> str:
    return f"color: {color}; font-size: 14px; border: none;"
