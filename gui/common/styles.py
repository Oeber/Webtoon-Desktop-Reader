PAGE_BG_STYLE = "background-color: #121212;"
PAGE_TITLE_STYLE = "color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;"
SECTION_LABEL_STYLE = "color: #aaaaaa; font-size: 12px; background: transparent;"
ERROR_LABEL_STYLE = "color: #f44336; font-size: 12px; background: transparent;"
STATUS_LABEL_STYLE = "color: #666; font-size: 12px; background: transparent;"

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
