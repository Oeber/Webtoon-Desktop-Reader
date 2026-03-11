import subprocess
import threading
import os
import re
import shutil
from urllib.parse import urlparse, parse_qs

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QPainter, QPen, QColor

BTN_STYLE = """
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
    QPushButton:disabled { color: #555; border-color: #222; }
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

STATUS_COLORS = {
    "Downloading": "#f0a500",
    "Completed":   "#4caf50",
    "Failed":      "#f44336",
    "Cancelled":   "#888888",
}


class _Signals(QObject):
    status_changed = Signal(str, str)   # name, status
    name_resolved  = Signal(str)        # resolved name


class SpinnerCircle(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._angle    = 0
        self._spinning = True
        self._percent  = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        if self._spinning:
            self._angle = (self._angle + 10) % 360
            self.update()

    def set_complete(self, percent: int):
        self._spinning = False
        self._percent  = percent
        self._timer.stop()
        self.update()

    def set_failed(self):
        self.set_complete(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(3, 3, -3, -3)

        if self._spinning:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            pen = QPen(QColor("#f0a500"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, -self._angle * 16, 270 * 16)

        elif self._percent == 0:
            painter.setPen(QPen(QColor("#555555"), 3))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("#888888"), 2))
            m = 8
            r = self.rect().adjusted(m, m, -m, -m)
            painter.drawLine(r.topLeft(), r.bottomRight())
            painter.drawLine(r.topRight(), r.bottomLeft())

        else:
            painter.setPen(QPen(QColor("#333333"), 3))
            painter.drawEllipse(rect)
            pen = QPen(QColor("#22c55e"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            span = int(self._percent / 100.0 * 360 * 16)
            painter.drawArc(rect, 90 * 16, -span)
            font = painter.font()
            font.setPixelSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._percent}%")


class DownloadEntry(QFrame):

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(
            "color: #eeeeee; font-size: 13px; background: transparent; border: none;"
        )
        self.name_label.setWordWrap(True)

        self.spinner = SpinnerCircle()

        self.status_label = QLabel("Downloading")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setFixedWidth(90)
        self.status_label.setStyleSheet(
            f"color: {STATUS_COLORS['Downloading']}; font-size: 12px; background: transparent; border: none;"
        )

        layout.addWidget(self.name_label, stretch=1)
        layout.addWidget(self.spinner)
        layout.addWidget(self.status_label)

    def set_status(self, status: str):
        color = STATUS_COLORS.get(status, "#cccccc")
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; background: transparent; border: none;"
        )
        if status == "Completed":
            self.spinner.set_complete(100)
        elif status in ("Failed", "Cancelled"):
            self.spinner.set_failed()


class DownloaderPage(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #121212;")

        self._process      = None
        self._active_entry = None
        self._signals      = _Signals()
        self._signals.status_changed.connect(self._on_status_changed)
        self._signals.name_resolved.connect(self._on_name_resolved)

        # Clean up any leftover temp folder from a previous crash
        _temp = os.path.join("data", "_download_temp")
        if os.path.exists(_temp):
            shutil.rmtree(_temp, ignore_errors=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Downloader")
        title.setStyleSheet(
            "color: #ffffff; font-size: 20px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title)

        # URL row
        row = QHBoxLayout()
        row.setSpacing(8)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste URL...")
        self.url_input.setStyleSheet(INPUT_STYLE)
        self.url_input.returnPressed.connect(self._start_download)

        self.download_btn = QPushButton("Download")
        self.download_btn.setStyleSheet(BTN_STYLE)
        self.download_btn.setFixedWidth(100)
        self.download_btn.clicked.connect(self._start_download)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(BTN_STYLE)
        self.cancel_btn.setFixedWidth(80)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_download)

        row.addWidget(self.url_input)
        row.addWidget(self.download_btn)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #f44336; font-size: 12px; background: transparent;"
        )
        layout.addWidget(self.error_label)

        # History
        history_label = QLabel("History")
        history_label.setStyleSheet(
            "color: #aaaaaa; font-size: 12px; background: transparent;"
        )
        layout.addWidget(history_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
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
        """)

        self.history_container = QWidget()
        self.history_container.setStyleSheet("background-color: #121212;")
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.history_container)
        layout.addWidget(self.scroll)

    # ------------------------------------------------------------------ #

    def _detect_url_type(self, url: str) -> str:
        """Returns 'chapter' or 'series' based on URL structure."""
        qs = parse_qs(urlparse(url).query)
        if "episode_no" in qs:
            return "chapter"
        return "series"

    def _get_existing_chapters(self, webtoon_dir: str) -> set:
        """Returns set of chapter numbers already downloaded."""
        existing = set()
        if not os.path.isdir(webtoon_dir):
            return existing
        for folder in os.listdir(webtoon_dir):
            match = re.match(r'^Chapter (\d+)$', folder)
            if match:
                existing.add(int(match.group(1)))
        return existing

    def _resolve_name(self, url: str) -> str:
        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return self._sanitize(og_title["content"].strip())

            if soup.title and soup.title.string:
                return self._sanitize(soup.title.string.strip())

        except Exception as e:
            print(f"Name resolve error: {e}")

        slug = url.rstrip("/").split("/")[-1]
        return self._sanitize(slug) or "download"

    def _sanitize(self, name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "", name).strip()

    def _start_download(self):
        url = self.url_input.text().strip().strip("'\"")
        if not url:
            self.error_label.setText("⚠ Please enter a URL.")
            return

        if self._process and self._process.poll() is None:
            self.error_label.setText("⚠ A download is already in progress.")
            return

        self.error_label.setText("")

        from gui.settings.settings_page import load_library_path
        output_path = load_library_path()

        # Use sanitized slug as placeholder — real name resolved in thread
        slug = self._sanitize(url.rstrip("/").split("/")[-1]) or "download"

        entry = DownloadEntry(slug)
        self.history_layout.insertWidget(0, entry)
        self._active_entry = entry

        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.url_input.clear()

        thread = threading.Thread(
            target=self._run,
            args=(url, output_path),
            daemon=True
        )
        thread.start()

    def _run(self, url: str, output_path: str):
        try:
            temp_dir = os.path.join("data", "_download_temp")
            os.makedirs(temp_dir, exist_ok=True)

            # Resolve real name in background — no main thread hang
            name = self._resolve_name(url)
            self._signals.name_resolved.emit(name)

            url_type    = self._detect_url_type(url)
            target_base = os.path.join(output_path, name)

            cmd = ["gallery-dl", "--verbose", "-D", temp_dir]

            if url_type == "series":
                # Skip episodes already on disk before downloading
                existing = self._get_existing_chapters(target_base)
                if existing:
                    # gallery-dl filter expression — episode_no not in existing set
                    existing_str = ", ".join(str(e) for e in existing)
                    cmd += ["--filter", f"episode_no not in [{existing_str}]"]

            cmd.append(url)

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True
            )

            for line in self._process.stdout:
                print(line.strip())

            self._process.wait()

            if self._process.returncode != 0:
                self._signals.status_changed.emit(name, "Failed")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return

            all_files = sorted([
                f for f in os.listdir(temp_dir)
                if os.path.isfile(os.path.join(temp_dir, f))
            ])

            if not all_files:
                # Nothing new to download — already up to date
                shutil.rmtree(temp_dir, ignore_errors=True)
                self._signals.status_changed.emit(name, "Completed")
                return

            os.makedirs(target_base, exist_ok=True)

            if url_type == "chapter":
                qs          = parse_qs(urlparse(url).query)
                episode_no  = int(qs.get("episode_no", ["1"])[0])
                chapter_dir = os.path.join(target_base, f"Chapter {episode_no}")
                os.makedirs(chapter_dir, exist_ok=True)
                for filename in all_files:
                    src = os.path.join(temp_dir, filename)
                    if os.path.isfile(src):
                        shutil.move(src, os.path.join(chapter_dir, filename))

            else:
                for filename in all_files:
                    match = re.match(r'^(\d+)', filename)
                    if not match:
                        continue
                    chapter_num = int(match.group(1))
                    chapter_dir = os.path.join(target_base, f"Chapter {chapter_num}")
                    os.makedirs(chapter_dir, exist_ok=True)
                    src = os.path.join(temp_dir, filename)
                    if os.path.isfile(src):
                        shutil.move(src, os.path.join(chapter_dir, filename))

            shutil.rmtree(temp_dir, ignore_errors=True)
            self._signals.status_changed.emit(name, "Completed")

        except FileNotFoundError:
            self._signals.status_changed.emit(name if 'name' in dir() else "download", "Failed")
        except Exception as e:
            print(f"Download error: {e}")
            self._signals.status_changed.emit(name if 'name' in dir() else "download", "Failed")

    def _cancel_download(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            if self._active_entry:
                self._active_entry.set_status("Cancelled")
            self._reset_buttons()

    def _on_status_changed(self, name: str, status: str):
        if self._active_entry and self._active_entry.name == name:
            self._active_entry.set_status(status)
        if status == "Completed":
            self.main_window.library.load_library()
        self._reset_buttons()

    def _on_name_resolved(self, name: str):
        if self._active_entry:
            self._active_entry.name = name
            self._active_entry.name_label.setText(name)

    def _reset_buttons(self):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._process  = None
        self._active_entry = None