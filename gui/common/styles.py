PAGE_BG_STYLE = "background-color: #121212;"
PAGE_TITLE_STYLE = "color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;"
SECTION_LABEL_STYLE = "color: #aaaaaa; font-size: 12px; background: transparent;"
ERROR_LABEL_STYLE = "color: #f44336; font-size: 12px; background: transparent;"
STATUS_LABEL_STYLE = "color: #666; font-size: 12px; background: transparent;"

SCROLL_AREA_STYLE = """
    QScrollArea { border: none; background-color: #121212; }
    QScrollBar:vertical {
        background: #1a1a1a; width: 8px; border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #444; border-radius: 4px; min-height: 30px;
    }
    QScrollBar::handle:vertical:hover { background: #666; }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical { height: 0px; }
"""

CHAPTER_SCROLL_AREA_STYLE = """
    QScrollArea { border: none; background: #121212; }
    QScrollBar:vertical { background: #1a1a1a; width: 6px; border-radius: 3px; }
    QScrollBar::handle:vertical { background: #333; border-radius: 3px; min-height: 20px; }
    QScrollBar::handle:vertical:hover { background: #555; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

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
