# Webtoon Desktop Reader

Webtoon Desktop Reader is a Windows desktop app for collecting, reading, and updating webtoons from your own local library.

It gives you a library view, a reader, download/update tools, reading progress tracking, bookmarks, categories, and a discovery screen for browsing supported sources without leaving the app.

## Chapter 1: Installation and Running

This project is intended to be released as a packaged executable.

### Windows

Download the packaged build from releases and run it.

### Linux

Linux users cannot use the Windows `.exe` build directly.

I do not currently provide a Linux build. If you want Linux support, a separate build would need to be created on Linux and released for Linux users.

## Chapter 2: Overview

### Key Features

- chapter-based reading with saved progress, quick resume, and new-chapter tracking
- `Ctrl+K` quick command menu for fast searching, navigation, downloads, and common actions
- built-in Discover and Download pages so you can browse supported sources and save series into your library
- support for custom scrapers, so new sites can be added over time
- desktop reading tools designed to make long vertical webtoons feel better away from a phone screen
- quick skip support to move past large separator gaps more comfortably while reading

### Why Quick Skip Exists

Most webtoons are designed to be read on a phone.

The black or white gaps between panels are usually intentional. They help control pacing, timing, and reveal as you swipe downward on a small screen.

On desktop, that same reading style is harder to reproduce because scrolling with a mouse wheel or scrollbar is not as natural as a phone swipe. Those long spacing sections can make reading feel slower than it should.

Quick skip helps with that by letting you move through those large empty gaps more comfortably while still reading the same chapter in order.

### What This App Does

This project is meant for people who want a local, desktop-first way to manage webtoons.

With it, you can:

- keep your comics in a normal folder on your computer
- browse them in a visual library
- continue reading from where you left off
- download supported series or chapters into your library
- update saved series when new chapters are available
- organize titles with categories, bookmarks, and custom thumbnails
- browse supported sites from inside the app and start downloads there

## Chapter 3: Using the App

### Main Parts of the App

### Library

The Library page is your home screen.

It shows all webtoons found inside your configured library folder and lets you:

- search your collection
- open a series page
- see reading progress
- spot newly added chapters
- bookmark titles
- organize titles into categories
- delete titles you no longer want

### Series Detail Page

When you open a title, you get a series detail page.

This page shows the chapter list and lets you:

- start reading
- resume where you left off
- hide filler chapters if the title supports them
- bookmark individual chapters
- select multiple chapters for batch actions
- start an update if the series has a saved source

### Chapters

Chapters are shown on the series detail page as your main reading list.

From there, you can:

- open any chapter directly
- resume a chapter from your saved position
- see which chapters are new
- bookmark individual chapters
- select multiple chapters for batch actions
- hide filler chapters when that option is available for the series

The app also remembers reading progress per chapter, so coming back later is easy.

### Reader

The Reader page displays chapter images from your local files.

It remembers your progress and supports:

- resume reading
- zoom options
- smooth scrolling through image-based chapters
- automatic progress saving

### Discover

The Discover page lets you browse supported remote catalogs from inside the app.

You can:

- switch between supported providers
- browse catalog pages
- search supported remote catalogs
- compare remote results with what you already downloaded
- open a remote series detail page before downloading

### Download

The Download page is for one-off downloads.

Paste a supported series or chapter URL and the app will try to download it into your library. Recent activity is kept in the app so you can see what finished, failed, or is still running.

### Updates

The Updates page is for titles already in your library that have a saved source URL.

It scans your library for updateable series and lets you pull in new chapters without redownloading everything.

### Settings

The Settings page is where you configure app behavior.

This includes things like:

- your library folder
- reader behavior
- category-related options
- log viewing

### Ctrl+K Menu

Press `Ctrl+K` anywhere in the app to open the quick command menu.

This is a fast way to move around the app and trigger common actions without digging through pages first.

You can use it to:

- search your library
- open titles quickly
- jump to pages like Library, Updates, Settings, and logs
- start downloads
- trigger search and reading actions
- get help on available commands

If you use keyboard navigation often, `Ctrl+K` is one of the fastest ways to work inside the app.

## Chapter 4: Library and Files

### How Your Files Are Organized

The app expects a local library folder with one folder per series and one folder per chapter.

Example:

```text
webtoons/
  Series Name/
    Chapter 1/
      001.jpg
      002.jpg
    Chapter 2/
      001.jpg
```

Supported image formats include:

- `jpg`
- `jpeg`
- `png`
- `webp`
- `avif`

### Data the App Saves

The app stores its own data in the `data/` folder.

That includes:

- `data/reader.db`: reading progress, settings, bookmarks, download history, and saved source information
- `data/logs/`: current and archived log files
- `data/profiles/`: optional profiler output
- `data/thumbnails/`: generated or cached cover images

## Chapter 5: Advanced Downloading

### gallery-dl Fallback

The app can fall back to `gallery-dl` for sites that do not have a custom scraper yet.

If you want to rely on `gallery-dl` as your default downloader, you need to install it separately because it is not bundled with the app.

### How to Install gallery-dl

#### Windows

If Python is installed on your system, you can install it with:

```powershell
py -m pip install gallery-dl
```

You can also install it with:

```powershell
python -m pip install gallery-dl
```

#### Linux

On Linux, the usual install command is:

```bash
python3 -m pip install gallery-dl
```

After installing it, make sure the `gallery-dl` command is available on your system `PATH`.

### Why gallery-dl Is a Worse Default

`gallery-dl` is useful as a broad fallback, but it is not the best default for this app.

Reasons:

- progress reporting is only estimated, not exact
- chapter detection and naming are more generic, so results are less tailored to each site
- source matching and update behavior are not as clean as custom site scrapers
- fallback downloads are less aware of site-specific chapter structure and metadata
- when a custom scraper exists, it usually gives better reliability and a better in-app experience

So the best setup is usually:

- use built-in custom scrapers when the app supports the site
- use `gallery-dl` only as a fallback for unsupported sites

## Chapter 6: Building for Scraper Testing

This section is mainly for people who want to create or test scrapers locally.

### Run From Source

To test scraper changes quickly, running from source is usually the easiest option.

Requirements:

- Windows
- Python `3.14`

Setup:

```powershell
.\setup.ps1
```

Run the app from source with:

```powershell
.\.venv\Scripts\python.exe .\main.py
```

This is the fastest way to test scraper edits because you can change the Python files and relaunch the app without rebuilding the packaged executable each time.

### Build the Windows Executable

If you want to test the packaged app behavior too, build the Windows executable with:

```powershell
.\build.ps1
```

This produces:

- `dist\Webtoon Desktop Reader.exe`

The current build also copies scraper files into:

- `dist\scrapers\sites`

That makes it easier to inspect or adjust scraper files around the packaged build, but source testing is still the better option while actively developing a scraper.

### PowerShell Scripts

This project includes a few helper `.ps1` scripts:

- `setup.ps1`: creates the virtual environment, installs dependencies, and prepares the project for running from source
- `build.ps1`: builds the packaged Windows executable
- `run.ps1`: launches the packaged executable from `dist\`
- `profile.ps1`: runs the app with profiling enabled and writes profiler output into `data\profiles\`
- `clear-site-cookies.ps1`: clears saved site session data when you need to reset authorization for supported sites
- `activate.ps1`: convenience script for activating the local virtual environment in PowerShell

Typical usage:

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe .\main.py
.\build.ps1
.\run.ps1
```

### Using the Profiler

The project includes `profile.ps1` for profiling the app during slower flows, scraper work, or UI performance checks.

Run it with:

```powershell
.\profile.ps1
```

Profiler output is written to:

- `data\profiles\`

That folder can contain files such as:

- `*.functions.txt`
- `*.threads.txt`
- `*.callgrind`
- `*.pstat`

This is mainly useful when you want to understand where time is being spent during startup, discovery loading, downloads, image handling, or other heavy operations.

## Chapter 7: Troubleshooting

If the app fails to start, check:

- `dist\data\logs\current.log` when running the packaged build

Common things to verify:

- your library path points to a real folder
- your chapter folders contain supported image files

## Chapter 8: Project Status

The app already includes:

- local library scanning
- progress tracking
- bookmarks and categories
- manual downloads
- saved-source updates
- discovery browsing
- site authorization support for protected sites
- rotating logs and optional profiling tools

Current limitations include:

- download work is not a shared global queue
- some sites may require manual authorization when their session expires
- only supported scrapers/providers will work

## Chapter 9: Creating Scrapers

If you want to add support for new sites there are two local guides in this project:

- [SCRAPPER GUIDE.MD](/f:/reader/SCRAPPER%20GUIDE.MD): use this when creating or updating download scrapers
- [DISCOVERY SCRAPER GUIDE.MD](/f:/reader/DISCOVERY%20SCRAPER%20GUIDE.MD): use this when creating or updating discovery providers for the Discover page

If you are building support for a new site, read the download scraper guide first, then the discovery guide if you also want that site to appear in Discover.

Feel free to create a pull request with new scrappers, I will add them after I test them
