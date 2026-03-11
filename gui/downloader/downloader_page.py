import subprocess
import threading
import os
import re
import shutil
import requests
import time

from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from pathlib import Path
from scrapers.registry import get_scraper
from scrapers.base import ScraperError
from types import SimpleNamespace

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap

from thumbnail_store import ThumbnailStore
from progress_store import get_instance as get_progress_store

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
    status_changed = Signal(str, str)    # name, status
    name_resolved  = Signal(str)         # resolved name
    progress_changed = Signal(str, int, int)  # name, current, total
    thumbnail_resolved = Signal(str, str)      # name, thumbnail path


class SpinnerCircle(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._angle = 0
        self._spinning = True
        self._percent = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        if self._spinning:
            self._angle = (self._angle + 10) % 360
            self.update()

    def set_progress(self, percent: int):
        percent = max(0, min(100, int(percent)))
        self._spinning = False
        self._percent = percent
        self._timer.stop()
        self.update()

    def set_complete(self, percent: int):
        self.set_progress(percent)

    def set_failed(self):
        self._spinning = False
        self._percent = 0
        self._timer.stop()
        self.update()

    def set_spinning(self):
        self._spinning = True
        self._percent = None
        if not self._timer.isActive():
            self._timer.start(30)
        self.update()

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
            painter.setPen(QPen(QColor("#333333"), 3))
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

    def __init__(self, name: str, on_open=None):
        super().__init__()
        self.name = name
        self.on_open = on_open
        self.thumbnail_path = ""
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet("""
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
        """)
        self.setProperty("clickable", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(52, 78)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("""
            QLabel {
                background-color: #161616;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
            }
        """)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(
            "color: #eeeeee; font-size: 13px; background: transparent; border: none; font-weight: 600;"
        )
        self.name_label.setWordWrap(True)

        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            "color: #777777; font-size: 11px; background: transparent; border: none;"
        )
        self.sub_label.hide()

        text_col.addWidget(self.name_label)
        text_col.addWidget(self.sub_label)

        self.spinner = SpinnerCircle()

        self.status_label = QLabel("Downloading")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setFixedWidth(110)
        self.status_label.setStyleSheet(
            f"color: {STATUS_COLORS['Downloading']}; font-size: 12px; background: transparent; border: none;"
        )

        layout.addWidget(self.thumb_label)
        layout.addLayout(text_col, stretch=1)
        layout.addWidget(self.spinner)
        layout.addWidget(self.status_label)

    def set_progress(self, current: int, total: int):
        total = max(1, total)
        current = max(0, min(current, total))
        percent = int((current / total) * 100)

        self.spinner.set_progress(percent)
        self.status_label.setText(f"{current} / {total}")
        self.status_label.setStyleSheet(
            f"color: {STATUS_COLORS['Downloading']}; font-size: 12px; background: transparent; border: none;"
        )

    def set_status(self, status: str):
        color = STATUS_COLORS.get(status, "#cccccc")
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; background: transparent; border: none;"
        )

        clickable = status == "Completed"
        self.setProperty("clickable", clickable)
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

        if clickable:
            self.sub_label.setText("Click to open details")
            self.sub_label.show()
        else:
            self.sub_label.hide()

        if status == "Completed":
            self.spinner.set_complete(100)
        elif status in ("Failed", "Cancelled"):
            self.spinner.set_failed()
        else:
            self.spinner.set_spinning()

    def set_thumbnail(self, path: str):
        self.thumbnail_path = path or ""
        pixmap = QPixmap(self.thumbnail_path)
        if pixmap.isNull():
            self.thumb_label.clear()
            return

        target_w = self.thumb_label.width()
        target_h = self.thumb_label.height()

        pixmap = pixmap.scaled(
            target_w,
            target_h,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )
        x = max(0, (pixmap.width() - target_w) // 2)
        y = max(0, (pixmap.height() - target_h) // 2)
        pixmap = pixmap.copy(x, y, target_w, target_h)
        self.thumb_label.setPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.status_label.text() == "Completed":
            if callable(self.on_open):
                self.on_open(self.name)
            event.accept()
            return
        super().mousePressEvent(event)

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
        self._signals.progress_changed.connect(self._on_progress_changed)
        self._signals.thumbnail_resolved.connect(self._on_thumbnail_resolved)

        self.thumb_store = ThumbnailStore()
        self.progress_store = get_progress_store()

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
        """
        Returns 'chapter' or 'series' based on URL structure.
        Supports both old gallery-dl style query URLs and custom scraper URLs.
        """
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        if "episode_no" in qs:
            return "chapter"

        path = parsed.path.rstrip("/").lower()

        if "/chapter-" in path:
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
            scraper = get_scraper(url)
            series = scraper.get_series_info(url if self._detect_url_type(url) == "series"
                                            else self._series_url_from_chapter_url(url))
            return self._sanitize(series.title)
        except Exception:
            pass

        try:
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

        entry = DownloadEntry(slug, on_open=self._open_downloaded_webtoon_detail)
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
        name = "download"

        try:
            temp_dir = os.path.join("data", "_download_temp")
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

            os.makedirs(temp_dir, exist_ok=True)

            name = self._resolve_name(url)
            self._signals.name_resolved.emit(name)

            scraper = None
            try:
                scraper = get_scraper(url)
            except Exception:
                scraper = None

            if scraper is not None:
                try:
                    self._custom_download(url, output_path)
                    self._signals.status_changed.emit(
                        self._active_entry.name if self._active_entry else name,
                        "Completed"
                    )
                except Exception as custom_error:
                    print(f"Custom scraper failed: {custom_error}")
                    self._signals.status_changed.emit(
                        self._active_entry.name if self._active_entry else name,
                        "Failed"
                    )
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                return

            self._gallery_dl_download(url, output_path, name)

        except FileNotFoundError:
            self._signals.status_changed.emit(name, "Failed")
        except Exception as e:
            print(f"Download error: {e}")
            self._signals.status_changed.emit(name, "Failed")

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
                thumb_path = self._preferred_thumbnail_for(name)
                if thumb_path:
                    self._active_entry.set_thumbnail(thumb_path)

        if status == "Completed":
            self.main_window.library.load_library()

        self._reset_buttons()

    def _on_name_resolved(self, name: str):
        if self._active_entry:
            self._active_entry.name = name
            self._active_entry.name_label.setText(name)

            thumb_path = self._preferred_thumbnail_for(name)
            if thumb_path:
                self._active_entry.set_thumbnail(thumb_path)

    def _reset_buttons(self):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._process  = None
        self._active_entry = None

    def _series_url_from_chapter_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        if "/chapter-" in path:
            path = path.rsplit("/chapter-", 1)[0]

        return f"{parsed.scheme}://{parsed.netloc}{path}"


    def _download_file(self, url: str, dest_path: str, headers: dict, retries: int = 2):
        import time

        url = url.strip().rstrip("\\").rstrip("/")

        last_error = None

        for attempt in range(retries + 1):
            try:
                with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    with open(dest_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 64):
                            if chunk:
                                f.write(chunk)
                return

            except Exception as e:
                last_error = e

                if os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass

                if attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue

        raise last_error
    
    def _custom_download(self, url: str, output_path: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        scraper = get_scraper(url)
        headers = scraper.get_request_headers(url)
        url_type = self._detect_url_type(url)

        if url_type == "chapter":
            series_url = self._series_url_from_chapter_url(url)
            series = scraper.get_series_info(series_url)
            chapters = [c for c in series.chapters if c.url.rstrip("/") == url.rstrip("/")]
            if not chapters:
                raise ScraperError(f"Could not match chapter URL: {url}")
            chapter_list = chapters
        else:
            series = scraper.get_series_info(url)
            chapter_list = series.chapters

        series_name = self._sanitize(series.title) or "download"
        self._signals.name_resolved.emit(series_name)

        if getattr(series, "cover_url", None):
            ok, result = self.thumb_store.set_from_url(series_name, series.cover_url)
            if ok:
                self._signals.thumbnail_resolved.emit(series_name, result)

        target_base = os.path.join(output_path, series_name)
        os.makedirs(target_base, exist_ok=True)

        existing = self._get_existing_chapters(target_base)

        total_chapters = len(chapter_list)
        completed_chapters = 0
        any_chapter_succeeded = False

        if url_type == "series":
            completed_chapters = sum(
                1 for ch in chapter_list
                if ch.number is not None and int(ch.number) in existing
            )

        self._signals.progress_changed.emit(series_name, completed_chapters, total_chapters)

        for chapter in chapter_list:
            chapter_num = int(chapter.number) if chapter.number is not None else None

            if chapter_num is not None and chapter_num in existing and url_type == "series":
                continue

            pages = scraper.get_chapter_pages(chapter.url)
            if not pages:
                continue

            if chapter_num is not None:
                chapter_dir_name = f"Chapter {chapter_num}"
            else:
                chapter_dir_name = self._sanitize(chapter.title) or "Chapter"

            chapter_dir = os.path.join(target_base, chapter_dir_name)
            os.makedirs(chapter_dir, exist_ok=True)

            success_count = 0
            failure_count = 0

            max_workers = min(8, max(1, len(pages)))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_page = {}

                for page in pages:
                    raw_url = page.image_url.split("?", 1)[0]
                    ext = raw_url.rsplit(".", 1)[-1].lower() if "." in raw_url else "jpg"
                    if ext not in {"jpg", "jpeg", "png", "webp", "avif"}:
                        ext = "jpg"

                    filename = f"{page.index:03d}.{ext}"
                    dest_path = os.path.join(chapter_dir, filename)

                    if os.path.exists(dest_path):
                        success_count += 1
                        continue

                    future = executor.submit(
                        self._download_file,
                        page.image_url,
                        dest_path,
                        headers,
                    )
                    future_to_page[future] = page.image_url

                for future in as_completed(future_to_page):
                    try:
                        future.result()
                        success_count += 1
                    except Exception as e:
                        failure_count += 1
                        bad_url = future_to_page[future]
                        print(f"Skipping page download failed: {bad_url} -> {e}")

            if success_count == 0:
                shutil.rmtree(chapter_dir, ignore_errors=True)
                raise ScraperError(f"Chapter download failed completely: {chapter.title}")

            any_chapter_succeeded = True
            completed_chapters += 1
            self._signals.progress_changed.emit(series_name, completed_chapters, total_chapters)

            if failure_count > 0:
                print(
                    f"Chapter partially downloaded: {chapter.title} "
                    f"({success_count} ok, {failure_count} failed)"
                )

        if not any_chapter_succeeded and completed_chapters == 0:
            raise ScraperError("No chapters were downloaded")

        thumb_path = self._preferred_thumbnail_for(series_name)
        if not thumb_path:
            thumb_path = self._create_auto_thumbnail_from_webtoon_folder(series_name, target_base)
            if thumb_path:
                self._signals.thumbnail_resolved.emit(series_name, thumb_path)

    def _gallery_dl_download(self, url: str, output_path: str, name: str):
        temp_dir = os.path.join("data", "_download_temp")
        os.makedirs(temp_dir, exist_ok=True)

        url_type = self._detect_url_type(url)
        target_base = os.path.join(output_path, name)

        cmd = ["gallery-dl", "--verbose", "-D", temp_dir]

        missing_chapters = []
        guessed_last_chapter = None

        if url_type == "series":
            existing = self._get_existing_chapters(target_base)
            if existing:
                existing_str = ", ".join(str(e) for e in sorted(existing))
                cmd += ["--filter", f"episode_no not in [{existing_str}]"]
            else:
                existing = set()

            guessed_last_chapter = self._guess_gallery_dl_last_chapter(url)
            if guessed_last_chapter is not None and guessed_last_chapter > 0:
                missing_chapters = sorted(
                    set(range(1, guessed_last_chapter + 1)) - set(existing)
                )
                if missing_chapters:
                    self._signals.progress_changed.emit(name, 0, len(missing_chapters))

        else:
            qs = parse_qs(urlparse(url).query)
            episode_no = None

            if "episode_no" in qs:
                try:
                    episode_no = int(qs["episode_no"][0])
                except Exception:
                    episode_no = None

            if episode_no is None:
                match = re.search(r'chapter[-/ ]?(\d+)', url, re.IGNORECASE)
                if match:
                    episode_no = int(match.group(1))

            if episode_no is not None:
                missing_chapters = [episode_no]
            else:
                missing_chapters = [1]

            self._signals.progress_changed.emit(name, 0, 1)

        cmd.append(url)

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True
        )

        def _progress_watcher():
            last_current = -1
            last_total = -1

            while self._process and self._process.poll() is None:
                try:
                    if missing_chapters:
                        total = len(missing_chapters)
                        temp_numbers = self._chapter_numbers_in_temp_dir(temp_dir)
                        current = sum(1 for ch in missing_chapters if ch in temp_numbers)

                        if current != last_current or total != last_total:
                            self._signals.progress_changed.emit(name, current, total)
                            last_current = current
                            last_total = total
                except Exception as e:
                    print(f"Progress watcher error: {e}")

                time.sleep(0.4)

            try:
                if missing_chapters:
                    total = len(missing_chapters)
                    temp_numbers = self._chapter_numbers_in_temp_dir(temp_dir)
                    current = sum(1 for ch in missing_chapters if ch in temp_numbers)
                    if current != last_current or total != last_total:
                        self._signals.progress_changed.emit(name, current, total)
            except Exception as e:
                print(f"Final progress watcher error: {e}")

        watcher_thread = threading.Thread(target=_progress_watcher, daemon=True)
        watcher_thread.start()

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
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._signals.status_changed.emit(name, "Completed")
            return

        os.makedirs(target_base, exist_ok=True)

        completed_now = set()

        if url_type == "chapter":
            qs = parse_qs(urlparse(url).query)
            episode_no = None

            if "episode_no" in qs:
                try:
                    episode_no = int(qs["episode_no"][0])
                except Exception:
                    episode_no = None

            if episode_no is None:
                match = re.search(r'chapter[-/ ]?(\d+)', url, re.IGNORECASE)
                if match:
                    episode_no = int(match.group(1))

            if episode_no is None:
                episode_no = 1

            chapter_dir = os.path.join(target_base, f"Chapter {episode_no}")
            os.makedirs(chapter_dir, exist_ok=True)

            for filename in all_files:
                src = os.path.join(temp_dir, filename)
                if os.path.isfile(src):
                    shutil.move(src, os.path.join(chapter_dir, filename))

            completed_now.add(episode_no)

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

                completed_now.add(chapter_num)

        if missing_chapters:
            final_current = sum(1 for ch in missing_chapters if ch in completed_now)
            self._signals.progress_changed.emit(name, final_current, len(missing_chapters))

        shutil.rmtree(temp_dir, ignore_errors=True)

        thumb_path = self._create_auto_thumbnail_from_webtoon_folder(name, target_base)
        if thumb_path:
            self._signals.thumbnail_resolved.emit(name, thumb_path)

        self._signals.status_changed.emit(name, "Completed")

    def _on_progress_changed(self, name: str, current: int, total: int):
        if self._active_entry and self._active_entry.name == name:
            self._active_entry.set_progress(current, total)

    def _on_thumbnail_resolved(self, name: str, path: str):
        if self._active_entry and self._active_entry.name == name and path:
            self._active_entry.set_thumbnail(path)

    def _preferred_thumbnail_for(self, webtoon_name: str) -> str | None:
        custom = self.thumb_store.get(webtoon_name)
        if custom and os.path.exists(custom):
            return custom

        auto_path = os.path.join("data", "thumbnails", f"{webtoon_name}.jpg")
        if os.path.exists(auto_path):
            return auto_path

        return None

    def _auto_thumbnail_path(self, webtoon_name: str) -> str:
        os.makedirs(os.path.join("data", "thumbnails"), exist_ok=True)
        return os.path.join("data", "thumbnails", f"{webtoon_name}.jpg")

    def _create_auto_thumbnail_from_webtoon_folder(self, webtoon_name: str, webtoon_dir: str) -> str | None:
        try:
            from PIL import Image

            if not os.path.isdir(webtoon_dir):
                return None

            chapter_dirs = [
                os.path.join(webtoon_dir, d)
                for d in os.listdir(webtoon_dir)
                if os.path.isdir(os.path.join(webtoon_dir, d))
            ]
            if not chapter_dirs:
                return None

            def chapter_sort_key(path: str):
                name = os.path.basename(path)
                match = re.search(r'(\d+(?:\.\d+)?)', name)
                if match:
                    try:
                        return (0, float(match.group(1)), name.lower())
                    except Exception:
                        pass
                return (1, float("inf"), name.lower())

            chapter_dirs.sort(key=chapter_sort_key)
            first_chapter = chapter_dirs[0]

            image_files = [
                os.path.join(first_chapter, f)
                for f in sorted(os.listdir(first_chapter))
                if os.path.isfile(os.path.join(first_chapter, f))
                and f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))
            ]
            if not image_files:
                return None

            src = image_files[0]
            dest = self._auto_thumbnail_path(webtoon_name)

            with Image.open(src) as img:
                rgb = img.convert("RGB")
                rgb.thumbnail((360, 540))
                canvas = Image.new("RGB", (360, 540), (18, 18, 18))

                x = (360 - rgb.width) // 2
                y = (540 - rgb.height) // 2
                canvas.paste(rgb, (x, y))
                canvas.save(dest, "JPEG", quality=90)

            return dest if os.path.exists(dest) else None
        except Exception as e:
            print(f"Auto thumbnail generation failed for '{webtoon_name}': {e}")
            return None

    def _build_webtoon_from_folder(self, webtoon_name: str):
        from gui.settings.settings_page import load_library_path

        webtoon_dir = os.path.join(load_library_path(), webtoon_name)
        if not os.path.isdir(webtoon_dir):
            return None

        chapter_dirs = [
            d for d in os.listdir(webtoon_dir)
            if os.path.isdir(os.path.join(webtoon_dir, d))
        ]

        def chapter_sort_key(name: str):
            match = re.search(r'(\d+(?:\.\d+)?)', name)
            if match:
                try:
                    return (0, float(match.group(1)), name.lower())
                except Exception:
                    pass
            return (1, float("inf"), name.lower())

        chapter_dirs.sort(key=chapter_sort_key)

        thumb = self._preferred_thumbnail_for(webtoon_name)
        if not thumb:
            thumb = self._create_auto_thumbnail_from_webtoon_folder(webtoon_name, webtoon_dir)

        return SimpleNamespace(
            name=webtoon_name,
            path=webtoon_dir,
            thumbnail=thumb or "",
            chapters=chapter_dirs,
        )

    def _open_downloaded_webtoon_detail(self, webtoon_name: str):
        webtoon = self._build_webtoon_from_folder(webtoon_name)
        if webtoon is None:
            return
        self.main_window.open_detail(webtoon)

    def _guess_gallery_dl_last_chapter(self, url: str) -> int | None:
        """
        Best-effort estimate of the highest chapter number for gallery-dl sources.
        Uses page metadata first, then falls back to URL/query hints.
        """
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            html = response.text

            candidates = []

            for match in re.finditer(r'episode[_\- ]?no["\':\s=]+(\d+)', html, re.IGNORECASE):
                candidates.append(int(match.group(1)))

            for match in re.finditer(r'chapter[_\- ]?(\d+)', html, re.IGNORECASE):
                candidates.append(int(match.group(1)))

            for match in re.finditer(r'/chapter[-/ ]?(\d+)', html, re.IGNORECASE):
                candidates.append(int(match.group(1)))

            og_url = re.search(
                r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE
            )
            if og_url:
                for match in re.finditer(r'chapter[-/ ]?(\d+)', og_url.group(1), re.IGNORECASE):
                    candidates.append(int(match.group(1)))

            if candidates:
                return max(candidates)

        except Exception as e:
            print(f"Last chapter guess failed from HTML: {e}")

        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)

            if "episode_no" in qs:
                return int(qs["episode_no"][0])

            path_matches = re.findall(r'chapter[-/ ]?(\d+)', parsed.path, re.IGNORECASE)
            if path_matches:
                return max(int(x) for x in path_matches)

        except Exception as e:
            print(f"Last chapter guess failed from URL: {e}")

        return None
    
    def _chapter_numbers_in_temp_dir(self, temp_dir: str) -> set[int]:
        """
        Reads chapter numbers from gallery-dl temp filenames.
        Expected pattern starts with chapter number, e.g. '123_001.jpg'.
        """
        found = set()

        if not os.path.isdir(temp_dir):
            return found

        for filename in os.listdir(temp_dir):
            full = os.path.join(temp_dir, filename)
            if not os.path.isfile(full):
                continue

            match = re.match(r'^(\d+)', filename)
            if match:
                found.add(int(match.group(1)))

        return found
    
    def _emit_gallery_dl_estimated_progress(
        self,
        name: str,
        temp_dir: str,
        missing_chapters: list[int]
    ):
        """
        Emits progress based on which missing chapter numbers have appeared
        in gallery-dl's temp directory.
        """
        total = len(missing_chapters)
        if total <= 0:
            return

        temp_numbers = self._chapter_numbers_in_temp_dir(temp_dir)
        current = sum(1 for ch in missing_chapters if ch in temp_numbers)
        self._signals.progress_changed.emit(name, current, total)