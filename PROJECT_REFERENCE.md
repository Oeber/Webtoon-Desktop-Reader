!IMPORTANT
This is the project reference, make no comments about it, just remember the information.
Before making suggestions about changes ask the user to provide the necessary files.

Project Summary

This project is a Python desktop webtoon reader built with PySide6 (Qt).
It manages a local library of webtoons, tracks reading progress in SQLite, supports downloader/update flows, and uses a shared set of GUI helpers to reduce repeated code across pages.
Library update checks can now run from Settings and on application startup when enabled and due, store the last check result/time, and persist per-title remote update counts so the library can react without re-checking immediately.
Library cards now keep the normal hover sync action for saved sources, but show an always-visible exclamation badge only when a completed remote update check has actually found new chapters for that title.
Manual downloads and saved-source updates now share one app-wide scheduler that allows up to three titles to download in parallel while still preventing duplicate jobs for the same title or source across the Downloader and Updates pages.
The library now keeps active manual download cards visible even when the downloads-section setting was turned off, and when categories are disabled those in-progress cards are folded into the flat library grid instead of creating their own section header.
Update-page availability scans now batch-load saved metadata, skip overlapping refresh requests, and write detected remote update counts back into settings rows for reuse across the library UI.
Library scans and live refreshes now batch-load saved settings rows instead of issuing repeated per-title lookups, and multi-title deletion now clears progress/settings in grouped passes after folder removal to reduce database churn.
The thumbnail dialog now uses the shared `TEXT_DIM` status color directly for in-progress save and download states after the remaining legacy `MUTED` reference was removed.
Settings now separate scraper/source availability controls into their own dedicated tab instead of mixing them into the General tab.
The app-update flow now treats the update dialog as the single confirmation point before automatic install begins.
Viewer zoom now supports a 15% minimum in both the viewer toolbar and Settings, and resume restore is cancelled once the user begins navigating manually so late image loads do not jump the scroll position backward.
Library detail remote chapter checks now treat request timeouts as expected lookup failures with a short user-facing timeout message instead of surfacing a full traceback.
Latest-progress lookups now include `total_images`, allowing continue actions like Resume Reading and `/read` to advance to the next chapter when the saved chapter was already completed.

Current high-level modules

reader/
|
|-- main.py
|-- clear-site-cookies.ps1
|-- profile.ps1
|-- update_helper.py
|-- core/
|   |-- app_logging.py
|   |-- app_update.py
|   |-- app_paths.py
|   |-- profiler.py
|   |-- site_session.py
|   `-- update_utils.py
|-- stores/
|   |-- db.py
|   |-- app_settings_store.py
|   |-- download_history_store.py
|   |-- progress_store.py
|   `-- webtoon_settings_store.py
|-- library/
|   |-- library_manager.py
|   `-- library_categories.py
|
|-- scrapers/
|   |-- base.py
|   |-- discovery_base.py
|   |-- discovery_registry.py
|   |-- discovery_support.py
|   |-- models.py
|   |-- registry.py
|   |-- discovery_sites/
|   |   |-- __init__.py
|   `-- sites/
|       |-- __init__.py
|
`-- gui/
    |-- main_window.py
    |
    |-- common/
    |   |-- styles.py
    |   |-- chapter_utils.py
    |   |-- card_utils.py
    |   |-- chapter_selection.py
    |   |-- detail_shared.py
    |   `-- site_auth_dialog.py
    |
    |-- discovery/
    |   |-- __init__.py
    |   |-- cover_loader.py
    |   |-- detail_page.py
    |   `-- site_browser_page.py
    |
    |-- library/
    |   |-- library_page.py
    |   |-- webtoon_card.py
    |   |-- thumbnail_dialog.py
    |   |-- edit_webtoon_dialog.py
    |   `-- detail_page.py
    |
    |-- viewer/
    |   `-- viewer_page.py
    |
    |-- search/
    |   `-- global_search.py
    |
    |-- settings/
    |   `-- settings_page.py
    |
    `-- downloader/
        |-- __init__.py
        |-- helpers.py
        |-- page_base.py
        |-- download_widgets.py
        |-- download_service.py
        |-- downloader_page.py
        `-- update_page.py

Downloader service notes

DownloadService supports both the shared requests-based page downloader and scraper-specific asset downloading for sites that need custom media fetch logic.

It only uses scraper-specific asset downloading when a scraper overrides BaseScraper.download_asset. Scrapers that inherit the base no-op implementation continue through the standard HTTP downloader path.

Main application flow

MainWindow owns a QStackedWidget and swaps between:

LibraryPage
DetailPage
ViewerPage
SiteBrowserPage
DiscoveryDetailPage
DownloaderPage
UpdatePage
SettingsPage

It also owns:

collapsible sidebar navigation
Ctrl+K global search dialog
shared update wiring between library/detail pages and the UpdatePage service
shared chapter-opening overlay shown before the viewer page becomes visible
download-close confirmation and background worker shutdown on app exit

Important navigation methods

open_detail(webtoon)
open_chapter(webtoon, chapter_index, scroll_pct=0.0)
open_chapter_with_prompt(webtoon, chapter_index)
open_library()
open_discovery()
open_site_authorization(site_name, url='')

The authorization window opens as an application-modal top-level window and reactivates the main window after closing.
When the authorization window closes, MainWindow now restores the pre-dialog top-level window state instead of forcing a normal-state show call, so the main app keeps its resizable state after authorization completes.
open_updates()
toggle_sidebar()
suppress_detail_open(seconds)

Sidebar behaviour

Collapsed width: 50px
Expanded width: 200px
Default state: collapsed
Collapsed sidebar buttons center their icons inside the active highlight state

Sidebar buttons

Library
Discover
Download
Updates
Settings

Icons use qtawesome.

Download sidebar behaviour

when any manual download or saved-source update is active, the Download sidebar button switches from the static download icon to a circular progress indicator
the expanded sidebar shows aggregated progress text in the form downloaded / left when totals are known
the collapsed sidebar keeps only the progress icon and exposes the same state via tooltip text
the active download icon uses an animated spinner state while work is running

Application metadata

Current window title base:

Webtoon Desktop Reader

Python runtime target:

Python 3.14

Window title behaviour:

library / downloader / updates / settings pages use:

Webtoon Desktop Reader

detail / viewer pages use:

Webtoon Desktop Reader | <webtoon title>

Application icon:

imgs/logo.png

Packaged Windows executable name:

Webtoon Desktop Reader.exe

Application version source:

core/app_update.py loads the packaged app version from data/app_version.txt and falls back to a default version string during source runs when that file is not present.
For packaged builds, core/app_update.py also owns the GitHub release-checking, release asset download, and updater-helper handoff used by the self-update flow.
For packaged GitHub releases, core/app_update.py now prefers release assets whose names end with:

*-portable.zip

for automatic in-app self-updates, ranks other non-installer zip assets after that, and avoids choosing installer-packaged zip assets for the updater-helper extraction flow.
The packaged updater helper executable is named:

Webtoon Desktop Reader Updater.exe

During self-update, the main app stages the updater helper into a temporary launch folder, writes data/last_update_launch.txt, then closes so the helper can show a console window, wait for the parent process to exit, replace the installed files from the downloaded ZIP, relaunch the app, and write progress/error details into data/last_update_trace.txt and data/last_update_error.txt.

Startup icon loading:

main.py loads imgs/logo.png as the QApplication and main window icon.
main.py also sets QApplication.setApplicationVersion from core/app_update.APP_VERSION.
On Windows, startup also sets an explicit AppUserModelID for taskbar grouping.
After the main window is shown, main.py also asynchronously prewarms the shared SQLite connection so the first database-backed UI interaction does not pay the full open/migration cost on demand.`r`nmain.py now wraps application startup inside a main() entrypoint guarded by __name__ == "__main__" so the module can be imported without launching the Qt app immediately.

Packaging/build scripts

main.spec builds a onefile PyInstaller executable named:

Webtoon Desktop Reader

The PyInstaller build embeds imgs/logo.png as the packaged executable icon.
main.spec also bundles data/app_version.txt so packaged builds can report the release version at runtime.

update_helper.spec builds a onefile PyInstaller executable named:

Webtoon Desktop Reader Updater

The updater helper build also embeds imgs/logo.png as its executable icon and keeps a visible console window so self-update progress is visible while the main app is closed.

installer.iss defines the Inno Setup Windows installer build for the packaged application.

The installer script now includes:

the main packaged executable
the packaged updater helper executable
dist/data
dist/scrapers
dist/webtoons

The installer executable icon is generated from imgs/logo.png during the build so the setup binary and packaged app share the same source icon artwork.

build.ps1 responsibilities

normalize the requested release version from -v
write the normalized release version into data/app_version.txt before packaging
rewrite installer.iss AppVersion and OutputBaseFilename to match the requested version before compiling the installer
install or upgrade PyInstaller inside the project virtual environment
clear previous build output
generate a temporary installer .ico from imgs/logo.png for Inno Setup
build the onefile packaged executable through main.spec
build the updater helper executable through update_helper.spec
compile the Windows installer through installer.iss using ISCC.exe
verify dist/Webtoon Desktop Reader.exe exists after the build
verify dist/Webtoon Desktop Reader Updater.exe exists after the build
copy shared scraper package modules into dist/scrapers for post-build plugin support
copy download scraper modules into dist/scrapers/sites for post-build editing
copy discovery provider modules into dist/scrapers/discovery_sites for post-build editing
create dist/webtoons as an output-friendly library root
create a portable release archive named:

Webtoon-Desktop-Reader-v<version>-portable.zip

create an installer executable named:

Webtoon-Desktop-Reader-Setup-v<version>.exe

create an installer release archive named:

Webtoon-Desktop-Reader-v<version>-installer.zip

run.ps1 responsibilities

launch dist/Webtoon Desktop Reader.exe
show the packaged log path and tail the last 40 log lines if launch fails

Repository documentation

README.md is currently the user-facing guide for:

installation/running as the first chapter
packaged app overview without a glossary
key features such as chapters, Ctrl+K, discovery/download, quick skip, and scraper support
packaged Windows runtime expectations and Linux note
gallery-dl fallback expectations and installation guidance
source/build steps for scraper testing
PowerShell helper script usage
profiler usage through profile.ps1

Additional internal scraper documentation now lives in:

SCRAPPER GUIDE.MD
DISCOVERY SCRAPER GUIDE.MD

Those guides now include shared session / Cloudflare authorization examples for download scrapers and discovery providers.

Shared refactor structure

The codebase now centralizes repeated logic into small shared modules instead of duplicating it across pages.

library/library_categories.py

Stores shared app-setting helpers for:

loading custom library categories
saving normalized custom library categories

core/update_utils.py

Stores shared update timing helpers for:

the 30 second update cooldown constant
computing remaining cooldown time from last_update_at

core/site_session.py

Stores shared site-session helpers for:

loading and saving per-site browser-captured cookies
loading and saving per-site browser user agents
building Cookie headers for session-aware cover and scraper requests
applying saved cookies into requests.Session instances
resolving per-site display names, host names, base urls, and required-cookie metadata from scraper/provider-owned metadata for browser authorization flows
mapping urls and hosts back to known site names so shared thumbnail and authorization helpers can reuse the correct saved browser session

gui/common/styles.py

Stores reusable stylesheet constants for:

page backgrounds
page titles
section labels
error/status labels
scroll areas
shared modern vertical scrollbar styling
inputs
buttons
search fields
settings tabs, surfaces, pills, sliders, and checkboxes
library section headers, empty states, batch bars, and delete buttons
card overlays, menus, title/info labels, chips, and action buttons
detail-page top bar, hero, filters, chapter rows, and action bars
main-window sidebar chrome
viewer resume / zoom controls
download entry frames, thumbnails, labels, and status text helpers

Also stores small shared style helper functions for:

dynamic card badge button variants
dynamic section empty-state variants
dynamic card border colors
dynamic detail thumbnail chrome
dynamic chapter name colors
dynamic download status colors

gui/common/chapter_utils.py

Stores shared chapter parsing helpers for:

detecting special chapters like 1.5
sorting chapter folder names by numeric chapter value

gui/common/site_auth_dialog.py

Provides an embedded QWebEngine authorization dialog for supported sites.

Responsibilities

open a site inside an app-managed browser profile
capture usable site cookies from the cookie store
persist captured cookies and the matching browser user agent through core/site_session.py
run each authorization attempt in a fresh in-memory WebEngine profile instead of reusing persisted browser state
validate captured sessions with a live request when cookie-name heuristics are insufficient
auto-save and close once a reusable session is detected
help the user return to the target site from interstitial pages before saving
clean up WebEngine objects in the correct order when the dialog closes
support scraper/provider-owned site metadata instead of relying on a fixed site map

gui/downloader/helpers.py

Stores downloader-specific helpers for:

webtoon name sanitizing
generic URL type detection for fallback downloader paths
generic chapter and series URL normalization for fallback downloader paths
generic chapter number extraction for fallback downloader paths
chapter path sorting
supported image extension constants

gui/downloader/page_base.py

Defines DownloadHistoryPageBase, the common shell used by DownloaderPage and UpdatePage.

This base class is responsible for:

building the shared page chrome
creating the page-local DownloadService
connecting service signals
managing the active history entry

This means the downloader pages share the same layout and signal plumbing without duplicating page shell code.

scrapers/discovery_base.py

Defines BaseDiscoveryProvider, the separate contract used for site-browsing / catalog pages.

This keeps discovery logic independent from download scrapers so a site can implement:

download only
discovery only
both

scrapers/discovery_registry.py

Auto-discovers discovery provider modules from scrapers/discovery_sites and returns initialized provider instances for the discovery page.

Core persistence

SQLite database

Stored at:

data/reader.db

Tables

progress
webtoon_settings
app_settings
download_history

progress columns

webtoon_name TEXT
chapter TEXT
scroll REAL
total_images INTEGER
updated_at INTEGER

webtoon_settings columns

webtoon_name TEXT PRIMARY KEY
hide_filler INTEGER
completed INTEGER
bookmarked INTEGER
zoom_override REAL
custom_thumbnail TEXT
source_url TEXT
source_site TEXT
source_series_id TEXT
source_title TEXT
category TEXT
bookmarked_chapters TEXT
last_update_at INTEGER
latest_new_chapter TEXT

app_settings columns

key TEXT PRIMARY KEY
value TEXT
updated_at INTEGER

app_settings keys now also store:

site_session_cookies:<site_name>
site_session_user_agent:<site_name>
disabled_scraper_sites

download_history columns

kind TEXT
name TEXT
source_url TEXT
status TEXT
created_at INTEGER
updated_at INTEGER

JSON/config persistence

config.json stores:

library_path
viewer_zoom
viewer_auto_skip

Download history persistence

download history is stored in the download_history SQLite table inside data/reader.db
download_history.json is no longer used by the application

Log persistence

Application logs are stored in:

data/logs/

Log files

current.log
session-<timestamp>.log

Profiler persistence

Opt-in profiler outputs are stored in:

data/profiles/

Profiler output files

<name>.functions.txt
<name>.threads.txt
<name>.callgrind
<name>.pstat

Logging retention

The current session writes to data/logs/current.log.
On startup, the previous current.log is rotated into a timestamped session log.
Only the 5 most recent archived session logs are kept.

core/app_logging.py responsibilities

create the log directory
rotate current.log on startup
trim archived logs to the latest 5
configure root logging handlers
write structured logs to file
mirror warnings and errors to stderr
capture uncaught exceptions through sys.excepthook

clear-site-cookies.ps1 responsibilities

clear saved per-site app-session cookies from app_settings
optionally clear saved per-site user agents from app_settings
optionally remove persisted embedded-browser webengine state for one or more sites

profile.ps1 responsibilities

launch the application with opt-in profiler flags
pass through profiler clock, sort, limit, and builtins options
write profiling outputs to data/profiles
profiler output retention is enforced by core/profiler.py, which keeps only the 5 most recent profile runs

core/profiler.py responsibilities

parse opt-in profiling flags before QApplication startup
strip profiler-only flags from Qt argv handling
run a session-wide yappi capture when enabled
write function summaries, thread summaries, callgrind, and pstat outputs to data/profiles
trim stored profiler runs to the latest 5 complete output sets after each dump

stores/db.py responsibilities

opens the shared SQLite connection
guards one-time connection initialization with a lock so concurrent first-use callers do not race
creates schema
enables WAL mode and foreign keys
backfills missing SQLite columns on startup for older packaged or pre-refactor databases
exposes an asynchronous prewarm helper used at app startup to initialize SQLite in a background thread

Current schema migration behavior

on startup, stores/db.py checks existing table columns with PRAGMA table_info
missing webtoon_settings columns are added with ALTER TABLE when needed
missing app_settings.updated_at is added with ALTER TABLE when needed
missing download_history columns are added with ALTER TABLE when needed
this keeps older reader.db files compatible with newer builds without manual database resets

stores/progress_store.py responsibilities

save per-webtoon per-chapter progress
save bulk per-chapter progress updates for chapter batch actions
return latest progress for Continue reading
return full chapter progress maps for progress rings and percentages
clear a single chapter progress row
clear multiple chapter progress rows
rename progress rows when a webtoon is renamed
clear all progress for deleted webtoons

stores/settings_store.py responsibilities`r`n`r`ncentralizes app-setting keys shared across the UI`r`nloads and saves the library path through the app settings store`r`nloads and saves generic typed settings values for viewer, library, and update features`r`n`r`nstores/download_history_store.py responsibilities

persist manual-download and update activity in SQLite
list recent history entries ordered by updated_at descending
upsert history rows as downloads start and finish
rename history rows when resolved webtoon names replace temporary names
trim persisted history to the latest 200 entries

stores/webtoon_settings_store.py responsibilities

persist hide filler flags
persist completed flags
persist per-webtoon bookmarked flags
persist per-webtoon zoom overrides
persist custom thumbnails
download and cache thumbnail URLs
reuse saved site-session headers for protected remote thumbnail requests when needed
derive protected thumbnail request headers from the thumbnail url's resolved site metadata instead of a hardcoded site list
persist source URLs
persist source-site discovery metadata for downloaded titles
persist source-series ids for downloaded titles
persist source titles for downloaded titles
persist per-webtoon library category assignment
persist bookmarked chapters as JSON
persist last update timestamps
persist the latest newly-downloaded chapter marker
centralize repeated scalar get/set/clear database updates for settings columns
rename settings and thumbnail files when a webtoon is renamed
delete settings and thumbnails when a webtoon is deleted

Library scanning

library/library_manager.py scans the configured library folder and returns lightweight Webtoon objects:

name
path
chapters
thumbnail
category

scan_library() behaviour

ignores non-directory entries
collects chapter directories
sorts chapters with natural_sort_key
uses the first readable image from the first chapter to build an automatic thumbnail if no custom thumbnail exists
hydrates system-section flags for bookmarked webtoons and webtoons with a latest_new_chapter marker
skips webtoons without chapters or readable first-chapter images
exposes shared helpers to resolve preferred thumbnails and build a lightweight webtoon object from an on-disk folder

Automatic thumbnail generation

Auto thumbnails are stored in:

data/thumbnails/<webtoon>.jpg

Generation logic:

loads the first image of the first chapter
scans downward for a fully black or fully white separator row
crops at that break when possible
scales and crops to 360 x 540
saves JPEG output for card-style covers

Local library layout

Configured library root:

config.json -> library_path

Expected folder structure:

webtoons/
`-- Series Name/
    |-- Chapter 1/
    |   |-- 001.jpg
    |   `-- ...
    |-- Chapter 1.5/
    `-- Chapter 2/

Supported reader/download image types

jpg
jpeg
png
webp
avif

Main window wiring

gui/main_window.py creates each page once and keeps them alive in the stacked widget.

Important service wiring:

DownloaderPage gets its own DownloadService through DownloadHistoryPageBase.
UpdatePage gets its own DownloadService through DownloadHistoryPageBase.
LibraryPage is attached to UpdatePage.service.
DetailPage is attached to UpdatePage.service.
DetailPage is also attached to DownloaderPage.service for hero progress visibility during manual downloads.
SiteBrowserPage is attached to both downloader services so its cached library snapshot can be invalidated on library_changed.

That wiring matters because update state, cooldowns, and refreshed library data are driven from the UpdatePage service, while manual downloads remain isolated to the Downloader page.

Application logging

The app now uses Python's logging module instead of scattered print() diagnostics.

Important logging behaviour

startup initializes logging before MainWindow is created
startup can optionally enable a process-wide yappi session with:

--profile
--profile-clock <wall|cpu>
--profile-sort <ttot|tsub|tavg|ncall|name>
--profile-limit <count>
--profile-name <label>
--profile-builtins

runtime modules request loggers with app_logging.get_logger(__name__)
uncaught crashes are written with logger name app.crash
major UI flows, persistence actions, downloads, updates, viewer transitions, and scraper operations log key state changes
viewer image decode logs include per-image path, pixel dimensions, on-disk file size, and decode time in milliseconds
viewer panel-analysis logs now also include per-image detected content ranges plus sampled row brightness / variance / chroma metrics to diagnose auto-skip behaviour
viewer auto-skip logs now distinguish carryover, next-panel, generic-target, and fallback-scroll down-navigation decisions

Library page

File:

gui/library/library_page.py

Purpose:

Shows the full local library as a responsive grid of WebtoonCard widgets.

Responsibilities

loads library data from disk with scan_library()
creates and lays out cards based on available width
handles inline fuzzy search with debounce
groups titles into inline collapsible category sections
lays category sections out in a responsive outer grid so smaller sections can share a row
supports built-in Uncategorized and user-created custom categories
supports built-in system categories for New and Bookmarked webtoons
supports a built-in system category for Active Downloads
supports settings toggles for enabling custom categories and hiding the built-in New and Active Downloads sections
creates categories from the library background context menu
renames and deletes custom categories from section header menus
supports drag-and-drop moving of titles between category sections
supports multi-select drag-and-drop category moves
supports drag-and-drop reordering of both system and custom category sections
supports batch Move to Category popup actions
supports batch comic bookmarking
attaches to the shared update service used by UpdatePage
attaches to the manual downloader service so active manual downloads can appear in a dedicated Active Downloads section
updates card button state during update cooldowns
refreshes only the changed webtoon after an update when possible
refreshes only the changed real webtoon during incremental manual downloads when possible
temporarily blocks click-through after update actions to avoid accidental opens
supports single and batch webtoon deletion with confirmation

Search behaviour

The page uses rank_webtoons() from gui/search/global_search.py so inline search and Ctrl+K share the same ranking rules.

Ranking uses rapidfuzz:

WRatio
partial_ratio
token_set_ratio

Search debounce:

150 ms

Card update flow

Each card can trigger an update if the webtoon has a saved source URL.
LibraryPage checks last_update_at and enforces a 30 second cooldown.
While an update is running:

the active card shows downloading state
other cards are disabled from starting another update
library reload is deferred or partially refreshed depending on visibility and update target
live progress is polled on the visible page so card overlays keep updating even while incremental refreshes are happening
the active webtoon can still be opened into DetailPage during the update
if an update is started from DetailPage, the matching library card still reflects active update state when the user returns to LibraryPage

Webtoon card

File:

gui/library/webtoon_card.py

Responsibilities

display thumbnail and title
show reading progress badges
show a NEW chip beside the latest chapter quick action when latest_new_chapter exists and matches the latest local chapter
open detail view
open edit dialog
offer per-webtoon bookmark toggle controls from both the context menu and a bottom-right card icon
offer quick update action when a source URL exists
show cooldown text and in-progress update state
offer Delete in the context menu
show live update progress on the existing overlay
show live manual-download progress on the existing overlay
offer cancel-download controls for active manual downloads
supports drag start for single-card and multi-selected category moves
supports download placeholder cards for in-progress manual downloads
download placeholder cards stay embedded in the library layout instead of opening as top-level windows
download placeholder cards can be clicked to open DetailPage once the webtoon folder exists on disk
the bottom-right bookmark icon uses the same circular overlay style as the card selection control

Detail page

File:

gui/library/detail_page.py

Purpose:

Shows a hero header for one webtoon and the full chapter list.

Responsibilities

load the selected webtoon and its progress/bookmark state
show Continue reading and Start from beginning actions
toggle a per-webtoon bookmark state
show Update when a source URL exists
check saved-source series for remote chapters not yet downloaded locally
show inline Download New and per-chapter remote download actions without navigating away from the page
show live update progress beside the hero metadata while an update is running
show the same hero progress indicator for active manual downloads when the title is opened during an in-progress download
allow edit dialog access
toggle latest/oldest chapter ordering
toggle hide filler
toggle bookmarked-only filter
toggle per-chapter bookmarks
support per-chapter multi-select
support Select All for the currently visible chapter list
support batch Mark Read, Mark Unread, and Delete chapter actions
support a separate multi-select flow for remote-only new chapters with Select All New, Download Selected, and Clear actions
refresh chapter folders from disk before opening a chapter
gracefully handle removed chapters
enforce the same 30 second update cooldown used elsewhere

Important detail page behaviour

Hide filler uses gui/common/chapter_utils.SPECIAL_CHAPTER_RE.
Bookmarked chapter names are stored in webtoon_settings.bookmarked_chapters.
Per-webtoon bookmark state is stored in webtoon_settings.bookmarked.
Chapter rows can show:

progress ring
last-read bookmark marker
bookmark toggle star
selection toggle
NEW chip for the latest newly-downloaded chapter

When a source URL exists, the page also performs a background remote chapter check for the current series.
Remote-only chapters render in a separate NEW CHAPTERS AVAILABLE block below the local chapter list.
Those remote rows support hover-based selection, one-off Download actions, and batch downloading for the currently visible remote-new set.
Queued remote chapters are hidden immediately from that block before the on-disk refresh completes so they do not still appear downloadable after the user starts a download.

If an update completes for the current webtoon, the page refreshes the chapter list from disk immediately.
If the current webtoon is actively updating while DetailPage is open, newly-downloaded chapters are appended to the end of the currently visible chapter order as they arrive.
Those appended chapters are re-sorted only when the user toggles sort or leaves and reopens the page.
live update progress is polled on the visible page so the hero progress indicator keeps updating during incremental chapter refreshes
if the current title is being downloaded through the manual downloader service, the hero progress indicator is driven from that service instead of the shared update service

Chapter batch actions:

the batch action bar appears at the bottom of the detail page when one or more chapters are selected
the bottom batch action buttons use larger touch targets than the app's default secondary buttons
Select All selects the currently visible chapter set after hide-filler / bookmark-only / sort filters are applied
Mark Read writes completed progress for the selected chapters in one bulk database transaction
Mark Unread clears progress for the selected chapters in one bulk database transaction
Delete removes selected chapter folders from disk and clears related progress

NEW chip behaviour:

latest_new_chapter is stored in webtoon_settings
the chip is not set during the first initial series download into an empty folder
the chip is set only for the most recently added chapter during later updates/downloads
opening that chapter clears the marker immediately
saving read progress for that marked chapter also clears the marker

Edit webtoon dialog

File:

gui/library/edit_webtoon_dialog.py

Responsibilities

rename webtoons
change saved source URL
set or clear zoom override
change custom thumbnail
assign or clear a custom library category
toggle hide filler
delete the webtoon from disk and persistence

Rename behaviour must keep data consistent across:

folder names on disk
progress rows
webtoon_settings row
custom thumbnail path
auto thumbnail path

Thumbnail dialog

File:

gui/library/thumbnail_dialog.py

Responsibilities

choose local image
accept drag and drop
accept remote image URL
preview the selected thumbnail

Viewer page

File:

gui/viewer/viewer_page.py

Behaviour highlights:

viewer middle mouse auto-scroll now uses the same simplified directional custom cursor as discovery
viewer auto-scroll event handling now tracks the viewport, container, preview strip, and image labels so middle-click scrolling remains available while hovering chapter images
viewer auto-scroll pointer normalization now maps through global coordinates before converting back to the scroll viewport so hover handling does not emit repeated Qt mapTo parent-hierarchy warnings

Purpose:

Large-image chapter reader optimized for vertical webtoon pages.

Important viewer behaviour

loads and decodes images in background threads
lazy-loads full images near the viewport
reads image dimensions up front to size placeholders before full decode
loads small previews first to stabilize preview-strip rendering and now uses scaled QImageReader decode for preview thumbnails when possible
reuses viewer image widgets across chapter changes instead of rebuilding the full scroll container every time
stores progress using packed scroll positions
supports resume prompts
supports per-webtoon zoom override
debounces zoom-override persistence so slider drags do not write settings on every tick
supports auto panel skip and standard scrolling
supports previous/next chapter navigation
supports keyboard navigation and middle-click auto scroll
handles missing or empty chapter folders safely
shows a loading overlay with spinner and decoded-image progress while a chapter is opening
emits chapter-loading start/finish signals so MainWindow can show a temporary loading overlay when opening a chapter from Library, Detail, search, or quick-read actions
panel-skip detection now caches per-image content ranges instead of only separator starts, so large blank runs can be excluded from panel bodies during auto-skip decisions
panel-skip blank-row detection now treats low-detail low-chroma fade rows as skippable separators even when they are not pure white or black
auto-skip target selection now mixes panel-range scoring, carryover handling for panels already entering at the bottom of the viewport, and next-panel handoff logic that accounts for content already consumed in the current viewport
auto-skip target scoring now penalizes large internal blank gaps, repeated-content windows, and edge-hugging frames so skips land closer to centered reading beats when possible`r`nviewer support types now live in gui/viewer/viewer_support.py, while the pure auto-skip scoring and target-selection helpers now live in gui/viewer/viewer_skip_logic.py so viewer_page.py can focus on page state and event wiring

Resume prompt behaviour:

if the resume dialog is cancelled or closed, the viewer does not open the chapter
chapter loads stop the pending deferred progress-save timer before switching chapters so resume state is not disturbed by a previous chapter's delayed save
previous / next chapter actions inside the viewer also use the same continue / restart prompt when the target chapter already has saved progress

Progress format

scroll = image_index + fractional_offset_inside_image

Special sentinel:

scroll == total_images

Meaning:

the chapter was finished

Search

File:

gui/search/global_search.py

Two responsibilities live here:

GlobalSearchDialog
rank_webtoons()

rank_webtoons() is the shared fuzzy ranking helper used by:

Ctrl+K global search
LibraryPage inline search

GlobalSearchDialog behaviour

opens with Ctrl+K
shows up to 20 ranked results
uses the current webtoon thumbnail when available
opens the selected result in DetailPage on single click or keyboard activation
supports slash-command discovery when typing /
filters available commands as the user types a partial command name
supports Tab and Shift+Tab command-name completion and cycling
supports Tab and Shift+Tab result cycling after a slash command has an argument
supports Tab and Shift+Tab scraper cycling for the discovery search command before a title is entered
supports Space accepting the highlighted command or title result and appending a trailing space
keeps command previews visible while cycling ambiguous command matches
updates the highlighted preview row to match the currently Tab-selected command
supports command previews that can fill the input with the command template
supports scraper preview rows that can fill the input with a selected discovery scraper
supports direct commands for:

/download <link>
/search <scraper> <title>
/update <title>
/open <title> <number>
/read <title>
/library
/updates
/settings
/logs
/help

/open behaviour

when no chapter number is supplied, /open opens the last-read chapter for the matched title
if no progress exists yet, /open opens the first chapter
when a trailing chapter number is supplied, /open jumps to that matching chapter if found

/download behaviour

/download starts a manual download without navigating away from the current page

/download starts a manual download through DownloaderPage
/search opens Discover, selects the matching discovery provider, and applies the title search
/update starts the shared update flow without navigating away from the current page
/open opens the matched title detail page
/read opens the matched title at saved progress or chapter 1 if no progress exists
/logs opens Settings and switches directly to the Logs tab

Settings page

File:

gui/settings/settings_page.py

Responsibilities

edit the library folder path
edit default viewer auto-skip
edit default viewer zoom
enable or disable supported scraper/discovery sources per site
toggle library categories on or off
toggle visibility of the built-in New section
toggle visibility of the built-in Active Downloads section
check GitHub for newer packaged app releases
start automatic packaged app updates when the release provides a zip asset and the app is running as a packaged Windows build
fall back to opening the latest packaged release download or releases page when automatic self-update is not supported
toggle startup app-update checks
reset settings to defaults
show a live log viewer in a separate tab
apply setting changes live to the current viewer when it exists

Settings page structure

General tab
is wrapped in a scroll area so the page can shrink vertically without forcing the main window to keep a tall minimum height
contains separate Library, App Updates, Sources, and Reader Defaults panels
Library panel now includes category/section visibility toggles
App Updates panel shows the current app version, the latest release check result, release download actions, and a visible download progress bar plus byte-progress text during automatic app-update downloads
Sources panel lists supported sites and can disable them for downloads, updates, and Discover
keeps Reset to Defaults as a page-level action below the settings panels
Logs tab

App update behaviour

SettingsPage performs GitHub release checks on a background QThread worker so the UI stays responsive.
SettingsPage also performs release asset downloads on a separate QThread worker when automatic app installation is supported.
The page persists the last check timestamp, latest release metadata, last error, and last notified version in app_settings.
When enabled, MainWindow schedules a startup app-update check and SettingsPage only shows the update prompt once per discovered release version.
The startup update prompt is now a single custom in-app modal instead of a two-step QMessageBox flow.
That startup modal shows the release and current version together, asks for the update decision directly, and reuses the same inline progress states as the Settings app-update panel while the package downloads and installation begins.
For packaged Windows builds with a zip release asset, the update button now downloads the new package, shows live percentage and byte progress in the Settings UI, launches a detached PowerShell installer, closes the app, replaces the installed files in place, and relaunches the executable automatically.
Automatic in-app updates now explicitly prefer the portable release zip naming used by build.ps1 and avoid installer-oriented zip assets during release asset selection.
The detached Windows installer now also unwraps a single top-level folder inside the downloaded zip before copying files into the install directory, so release archives that expand into a versioned root folder still replace the installed executable and bundled files correctly.
The app-update flow now logs release checks, package downloads, installer-launch attempts, and UI-side update failures through the shared application logger so failed self-updates are easier to diagnose from data/logs/current.log.
The Settings app-update panel now depends on the shared APP_UPDATE_PROGRESS_STYLE constant from gui/common/styles.py for its progress-bar chrome.

Logs tab behaviour

shows the current session log
live-refreshes while the Logs tab is active
color-codes lines by log level
can hide non-warning and non-error lines
shows the current log path and archived session count

Current settings page styling

uses a simplified black-on-black look
avoids explanatory helper copy inside the page
uses borderless surface panels instead of heavy outlined cards
now pulls its reusable tab, card, checkbox, slider, and log-view styles from gui/common/styles.py

Current shared application styling

the shared gui/common/styles.py palette now uses a salmon-on-black direction instead of the older gray-on-black look
the Settings app-update progress bar now uses the same shared salmon accent styling as the rest of the application chrome
main window sidebar buttons use salmon active-state highlighting for the current destination icon/button
collapsed sidebar buttons use centered icon alignment while expanded sidebar buttons keep left-aligned text labels
library, detail, downloader, settings, and related dialogs now mostly inherit the salmon accent palette through shared style constants
the downloader history widgets now use the same salmon accent palette for Ready / Downloading / Cancelled states and spinner chrome instead of the older yellow-and-gray variants
the thumbnail dialog now uses the same salmon-on-black palette instead of its earlier blue-accent styling
the library page top controls now render inside an explicit themed header bar instead of relying on default Qt background inheritance
the library size control now uses a flat header section with a dedicated slider track surface instead of a bordered nested panel

Shared helper functions in this file

load_library_path()
save_library_path()
load_setting()
save_setting()
open_logs_tab()

Downloader architecture

The downloader code is now split into:

shared helper functions
shared page shell
shared widgets
shared download engine
two thin page-specific UIs

Files

gui/downloader/helpers.py
gui/downloader/page_base.py
gui/downloader/download_widgets.py
gui/downloader/download_service.py
gui/downloader/downloader_page.py
gui/downloader/update_page.py

download_widgets.py responsibilities

shared button/input styles
spinner/progress ring
download history card widget
update history card widget
last-updated formatting helper

The UpdateEntry widget extends DownloadEntry and adds:

source URL subtitle
last updated timestamp
Update button

The downloader page also shows a persisted Recent activity list that includes both manual downloads and updates.

Downloader page

File:

gui/downloader/downloader_page.py

Purpose:

Manual one-off download screen.

Responsibilities

accept URL input
start a download
cancel the active download
cancel all active manual downloads from a top-level button
append a history entry
open detail page for completed downloads
automatically open site authorization when a supported download is blocked and retry after the user saves a session

Update page

File:

gui/downloader/update_page.py

Purpose:

Bulk update screen for already-saved library entries that have source URLs.

Responsibilities

scan the current library for webtoons with source URLs
check each eligible source asynchronously against the matching scraper before showing it
show only titles whose remote chapter list contains chapters not already present locally
compare remote chapters against local folder names using the same chapter-name normalization used by the detail page instead of relying only on raw chapter totals
reuse short-lived cached update-check results and recent failures so reopening the page shortly after a refresh does not immediately refetch every title
reuse per-worker HTTP sessions during update checks so repeated requests can keep remote connections warm
allow scraper-specific lightweight update snapshots when a scraper can prove the current new-chapter count without fetching the full remote chapter history, while falling back to the full series check when that shortcut is not conclusive
render update candidates as library-style cards instead of history rows
show per-title new-chapter counts on the cards
support card-level selection plus a bottom batch bar for Update Selected and Clear actions
reuse the shared DownloadHistoryPageBase service wiring while rebuilding the page shell so the bottom batch bar can span the full width
enforce 30 second per-webtoon cooldowns
refresh the list after successful updates
show last update timestamps
skip disabled or unsupported scraper matches without surfacing them as update-page errors
keep the local webtoon name as preferred_name during updates so local renames do not create duplicate folders

Update page refresh behaviour

defers the first refresh until the page is first shown instead of starting remote checks during main-window construction
refreshes immediately when the page becomes visible and no update is running
refreshes again shortly after a finished update
refreshes immediately on library_changed when visible only if no update is still running

Download service

File:

gui/downloader/download_service.py

Purpose:

Owns one active download job and emits UI-friendly signals.

Signals

status_changed(name, status)
name_resolved(name)
progress_changed(name, current, total)
thumbnail_resolved(name, path)
download_started()
download_finished(name, status)
auth_required(site_name, url, preferred_name, selected_chapter_urls)
library_changed()

Core responsibilities

validate and start a single active download
cancel the active subprocess/request work
resolve a display name
choose custom scraper versus gallery-dl fallback
download chapter pages in parallel for custom scrapers
estimate progress for gallery-dl downloads
save source URLs after successful downloads
normalize chapter URLs back to series URLs before persisting source_url
save active source URLs during shutdown before cancelling in-flight downloads
persist latest_new_chapter when an existing webtoon receives new chapters
resolve preferred thumbnail
generate auto thumbnails when needed
emit site-authorization requests when a supported scraper is blocked by an anti-bot challenge
build a lightweight webtoon object from a folder for immediate UI refreshes
store per-job progress counters so library/detail UI can query live progress state directly
track worker thread / executor lifetime so shutdown is explicit instead of relying on interpreter teardown
emit library_changed incrementally during custom chapter downloads so library/detail views can refresh before the full job completes

Download decision flow

start_download(url, output_path, preferred_name=None)
-> normalize and validate URL
-> resolve scraper with registry.get_scraper(url)
-> custom scraper path if supported
-> gallery-dl subprocess path otherwise

Custom scraper path

Uses scrapers registered in scrapers/registry.py.

Flow:

resolve series info
if downloading a single chapter, map the chapter URL back to its series using scraper-defined URL handling
skip already-downloaded chapters for series downloads
download each page file in parallel with retry logic
emit progress per chapter
save scraper-provided cover_url as a custom thumbnail when available
save the normalized series URL instead of the chapter URL as source_url
record the newest added chapter as latest_new_chapter only when the webtoon already existed locally

gallery-dl fallback path

Flow:

download files into data/_download_temp/
guess highest chapter number when possible
estimate progress by watching filenames appear in the temp directory
move files into Chapter N folders after the process completes
emit final progress after move
generate auto thumbnail from the earliest downloaded chapter
record the newest added chapter as latest_new_chapter only when the webtoon already existed locally

Temporary folder

data/_download_temp/

Download concurrency

Custom page downloads use ThreadPoolExecutor with up to 8 workers.

Cancellation

DownloadService tracks _cancel_requested.
Custom downloads stop ongoing file writes and clean partial files.
gallery-dl downloads terminate the subprocess.

Shutdown behaviour

MainWindow prompts before closing if either downloader service still has active work.
If the user confirms close, the app saves the active jobs' normalized source URLs, cancels downloader/update work, shuts down viewer loader workers, and then exits.

Scraper plugin system

Files:

scrapers/base.py
scrapers/__init__.py
scrapers/models.py
scrapers/registry.py
scrapers/sites/__init__.py

Behaviour

the scrapers package extends its package path to include an external dist/scrapers folder when running as a packaged app
registry auto-discovers scraper modules from both the packaged package path and dist/scrapers/sites
registry now caches discovered builtin and external scraper classes in process memory and invalidates the external cache when the external scraper files change on disk
external scraper modules can import shared helper modules from dist/scrapers and sibling modules from the external scraper folder
get_scraper(url) chooses the first scraper that can_handle(url)
custom scrapers implement the BaseScraper interface

BaseScraper interface

can_handle(url)
get_series_info(url)
get_chapter_pages(url)
get_request_headers(url)
is_chapter_url(url)
series_url_from_chapter_url(url)
extract_chapter_number(url)

Core models

SeriesInfo
ChapterInfo
PageInfo
CatalogSeries
CatalogPage

CatalogSeries fields

site
series_id
title
url
cover_url
cover_headers
author
description
latest_chapter
total_chapters

CatalogPage fields

site
page
entries
has_next_page

Discovery provider system

Files:

scrapers/discovery_base.py
scrapers/discovery_registry.py
scrapers/discovery_sites/__init__.py

Behaviour

discovery providers are separate from download scrapers
get_all_discovery_providers() auto-discovers provider modules from both the packaged package path and dist/scrapers/discovery_sites
the discovery registry filters out providers for sites disabled in Settings
the discovery page lists only providers registered through the discovery registry
providers return CatalogPage / CatalogSeries models instead of SeriesInfo / ChapterInfo
cover requests can provide provider-specific HTTP headers through CatalogSeries.cover_headers

Discovery page

File:

gui/discovery/site_browser_page.py

Purpose:

Shows site-provided series catalogs and lets the user browse into a discovery-detail view from remote results.

Responsibilities

list available discovery providers in a site selector
delay the first catalog fetch until the page is actually shown
load catalog pages asynchronously so Discover navigation does not block the UI thread
append additional provider pages when the provider reports has_next_page
render each result with:

cover image
title
chapter count when available
visible +N New chip when discovered chapter counts exceed local chapter counts

load cover thumbnails asynchronously through a bounded shared requests-based loader instead of QNetworkAccessManager
decode oversized discovery covers through a scaled QImageReader path so very large remote images do not trip Qt allocation limits
cache the normalized local-library snapshot instead of rescanning on every discovery refresh
invalidate the discovery library snapshot when downloader/update services report library_changed
use discovery-provider helpers for display naming, entry identity, local library matching, Downloaded Only reconstruction, and local search filtering
support a Downloaded Only toggle backed by saved source metadata for the selected provider instead of only the currently loaded remote page set
support a discovery search field that can use provider-backed remote search when implemented and provider-backed local filtering for Downloaded Only mode
support middle-mouse auto-scroll inside the discovery scroll area
show an inline loading-more footer during incremental discovery prefetches
automatically open site authorization when a supported provider hits an anti-bot block and retry after the user saves a session
hand successful card clicks off to MainWindow.open_discovery_detail()

Discovery detail page

File:

gui/discovery/detail_page.py

Purpose:

Shows a detail-style remote series page for discovery results and lets the user choose which chapters to download.

Responsibilities

load full remote series metadata and chapter lists on demand from the site scraper
show remote cover, title, author, description, and chapter counts
support whole-comic downloads
support per-chapter and multi-select chapter downloads
support the same bottom batch-action layout used by the local detail page
use detail-style chapter row selection chrome instead of native checkboxes
automatically open site authorization and retry when a supported remote series load is blocked
keep the user on the same discovery-detail page when a download starts and show inline status instead of forcing a navigation to DownloaderPage

Discovery cover loader

File:

gui/discovery/cover_loader.py

Purpose:

Shared asynchronous cover-image loader used by discovery UI.

Responsibilities

fetch discovery cover images on a bounded shared thread pool
dedupe in-flight requests for the same cover url plus headers
cache recent request results in memory

Discovery support helpers

File:

scrapers/discovery_support.py

Purpose:

Shared discovery-domain helpers that keep provider matching and local snapshot reconstruction out of the UI.

Responsibilities

build normalized local-library snapshots for discovery matching
reconstruct CatalogSeries entries from saved source metadata for Downloaded Only mode
match remote discovery entries to local library entries by source metadata and normalized titles

Discovery provider contract

File:

scrapers/discovery_base.py

Current shared defaults

providers expose display names through get_display_name()
providers expose entry identity through entry_key()
providers expose Downloaded Only local filtering through matches_search()
providers expose local-library matching through match_entry_to_library()
providers expose Downloaded Only reconstruction ordering through downloaded_entries()
providers can also expose site-session metadata used by the shared authorization/session helpers

Discovery page current behaviour

the page shell is created at app startup but the initial provider fetch is deferred until first show
opening Discover from the sidebar no longer starts a request before the page is visible
catalog requests are dispatched on worker threads and stale responses are ignored
refresh resets the discovery list back to page 1 for the current provider
the Omega discovery API now fetches 100 results per page instead of 20
page 2+ discovery results are appended incrementally into the existing card grid while skipping duplicate urls/series ids instead of rebuilding the full grid on every append
automatic discovery prefetch now arms once the user reaches an early scroll threshold instead of waiting for a manual Load More click
discovery prefetch keeps the user anchored when appending near the bottom and no longer treats a zero-range first layout as being at the bottom
an inline loading-more footer appears during incremental page fetches
the discovery search box now issues provider-backed remote searches when supported; the Omega provider forwards search text through query_string on the API request
Downloaded Only now builds its entries from saved source metadata through shared discovery helpers and applies provider-backed local filtering/searching on top of that local-backed list
middle mouse button toggles auto-scroll inside the discovery viewport, matching the viewer-style drag scroll behavior
discovery cover loading now uses a bounded shared thread pool with pooled requests sessions plus in-memory request/result reuse instead of spawning one thread per cover
discovery cards defer cover requests until they are in or near the current viewport instead of requesting every visible page result during card construction
discovery catalog requests are deduped by provider, page, and search query so repeated refresh/search/site-change actions do not refetch the same remote page unnecessarily
discovery cover requests are throttled behind a short timer, limited to the nearest visible cards, continue issuing follow-up passes while visible cards still need covers, and now clear failed in-flight card state so later visible covers can retry instead of stalling after scroll or relayout changes
discovery Downloaded Only cards fall back to matched local library thumbnails when remote discovery metadata does not provide a cover url or the remote cover request fails
discovery grid relayout now uses the scroll area's stable inner width instead of the fluctuating viewport width so appended pages and refreshes do not drop a visible card column when the vertical scrollbar appears
discovery switching between providers resets the scroll position to the top, and toggling Downloaded Only off forces a fresh remote refresh for the selected provider
the discovery middle-mouse auto-scroll handler now tracks events from the viewport, content widget, and entry widgets so it remains responsive while hovering cards
discovery auto-scroll now uses a simplified directional custom cursor: up arrow when moving up, down arrow when moving down, and both arrows while idle inside the deadzone
discovery viewport hit-testing and auto-scroll pointer normalization now map through global coordinates before converting back to the viewport so hover interactions do not emit repeated Qt mapTo parent-hierarchy warnings
discovery card clicks now open a discovery-detail page instead of jumping directly to the downloader
the discovery-detail page now loads remote chapters on demand and supports whole-series, per-chapter, and selected-chapter downloads
disabling a site in Settings removes it from the Discover provider list immediately and triggers a fresh provider reload
catalog loads for supported protected providers automatically open the embedded browser authorization flow when Cloudflare or similar anti-bot blocks are detected, then retry after the session is saved
the current implementation matches local titles through provider matching helpers backed by saved source site/series id first and normalized local titles second
chapter comparison currently uses discovered total_chapters versus len(local chapters)

Current behaviour summary

OK Shared style constants for repeated page chrome
OK Shared modern vertical scrollbar styling
OK Shared chapter parsing utilities
OK Shared fuzzy ranking helper for global search and library search
OK Shared downloader page base class
OK Shared downloader helper functions
OK Shared download/update entry widgets
OK Centralized application logging with rotating session files
OK Manual downloader page
OK Saved-source updates page
OK Single active download per page-local service
OK Shared update service wiring for library/detail/update UI
OK SQLite-backed progress and settings persistence
OK Per-webtoon bookmarks
OK Per-webtoon hide filler
OK Per-webtoon completed state
OK Per-webtoon library bookmark flag
OK Per-webtoon zoom override
OK Source URL persistence
OK Last update timestamp persistence
OK Latest-new-chapter persistence
OK Auto thumbnail generation
OK Custom thumbnail download/caching
OK Partial library refresh after updates
OK Ctrl+K global search
OK Ctrl+K slash command discovery and previews
OK Ctrl+K Tab and Shift+Tab command completion cycling
OK Ctrl+K Tab and Shift+Tab result cycling inside slash commands
OK Ctrl+K /search supports scraper-first discovery navigation with scraper Tab cycling
OK Ctrl+K Space accepts highlighted command and title results
OK Ctrl+K commands for download, search, update, open, read, library, updates, settings, logs, and help
OK Ctrl+K /download starts downloads without navigating to the Downloader page
OK Embedded browser-based site authorization can persist per-site cookies and user agents for protected scrapers
OK Authorization runs use a fresh in-memory browser session and can auto-close once a reusable session is detected
OK Authorization close no longer forces the main window back through showNormal, preserving the existing top-level window state
OK Shared scraper / discovery provider metadata can drive browser-session reuse and authorization without a hardcoded site map
OK Protected discovery, downloader cover loads, and series/chapter requests can reuse the saved browser session when available
OK Blocked discovery and download flows can auto-open the authorization browser and retry after session save when the scraper/provider raises the expected Cloudflare-style error
OK Inline library fuzzy search
OK Live in-app log viewer in Settings
OK Resume reading
OK Resume dialog cancel keeps the viewer closed
OK Packed scroll progress
OK Background image decoding and lazy loading
OK Auto panel skip and standard scroll modes
OK NEW chip on detail and library views for newly added chapters
OK Chapter multi-select batch actions in DetailPage
OK Close confirmation when downloads are active
OK Explicit downloader / viewer worker shutdown on app exit
OK Source URL persistence even when closing mid-download
OK Sidebar download progress indicator with aggregated counts
OK Top-level cancel button on the manual downloader page
OK Updates page shows only titles with confirmed remote updates
OK Updates page uses library-style cards with new-chapter chips
OK Updates page supports multi-select batch actions from a bottom bar
OK Library card context-menu delete action
OK Library cards show live manual download progress and can cancel active manual downloads
OK Active Downloads placeholder cards appear inside the library instead of as detached windows
OK Active Downloads placeholder cards can open DetailPage during manual downloads once on-disk data exists
OK Active Downloads placeholders update live for immediate card appearance, covers, and partial-cancel retention
OK Library cards show live update progress even when updates are started from DetailPage
OK Detail page shows live update progress and live chapter arrival during updates
OK Detail page hero progress also appears for active manual downloads opened mid-download
OK Ctrl+K search opens titles on single click
OK Reader Back can return to DetailPage during updates
OK Library and Detail remain navigable while updates are running
OK Settings can enable or disable supported scraper sites for downloads, updates, remote chapter checks, and Discover
OK Settings General tab now scrolls instead of forcing the whole main window to keep the Settings page's old tall minimum height
OK Inline collapsible library categories
OK Responsive multi-column library category sections
OK Expanding a collapsed library category after a window resize recalculates its responsive column layout instead of reopening as a single stuck column
OK Custom category creation, rename, delete, batch move, drag-and-drop move, and edit-dialog assignment
OK Library category creation keeps new sections embedded in the main library window instead of surfacing detached white/focus-stealing windows
OK Settings can disable custom library categories entirely and hide the built-in New and Active Downloads sections
OK No-categories mode shows the main library as a flat grid without a replacement Library category header
OK Drag-and-drop reordering for system and custom library sections
OK Batch comic bookmarking from the library page
OK Discovery grid keeps a stable column count across append/refresh relayouts when the vertical scrollbar appears
OK OmegaScans filters paid chapters out of discovery-detail chapter lists and custom scraper downloads so premium-only chapters are not shown or downloaded
OK OmegaScans discovery cards use the latest free chapter label when the catalog's chapter total is unreliable because it includes premium chapters
OK Library size slider handle tuned to a smaller circular knob with a dedicated track surface
OK Library card overflow menu button now uses a centered ellipsis icon instead of text glyphs
OK Collapsed sidebar navigation icons are centered inside the active highlight state
OK Windows release builds can now produce both installer and portable artifacts from build.ps1
OK Automatic in-app app updates now prefer the portable release zip asset over installer-packaged zip assets

Known limitations / future work

Cloudflare-protected sites still require a manual in-app browser authorization step when the saved session expires, even though the auth dialog can now auto-save and close once the session becomes reusable
only one active download per DownloadService instance
gallery-dl progress is estimated, not exact
scraper coverage is limited to implemented site modules
Inno Setup must be installed on the build machine, or ISCC_PATH must point to ISCC.exe, for installer builds to succeed
reference file should be kept aligned with code after future refactors

Recent shared GUI cleanup notes

The GUI now keeps more repeated presentation logic inside gui/common/styles.py instead of per-page inline styles.

styles.py now also centralizes shared helpers for:

main-window and viewer loading overlays
site authorization labels
startup update dialog chrome
transparent section/text label variants used by Settings
library controls-bar and size-slider chrome
shared discovery combo / filter / loading styles
thumbnail dialog drop-zone, preview, buttons, and status helpers
sized action-button variants for repeated batch bars
shared checked-state overlay button variants for card actions

New shared GUI helper modules

gui/common/card_utils.py

Stores reusable card/view helpers for:

retaining hidden widget size for stacked card badges
single-line elided labels
rounded cover pixmap loading into QLabel targets
shared select-toggle icon updates used by library and update cards

gui/common/detail_shared.py

Stores shared detail-page layout constants for:

thumbnail size
hero action button size
batch action button height
shared thumbnail corner radius

gui/common/chapter_selection.py

Stores shared chapter-selection helpers for:

drawing checked / unchecked chapter selector icons
showing or hiding selector affordances on hover / active selection
finding selector buttons by widget property inside chapter lists
refreshing selector visibility across rebuilt lists
synchronizing checked state for bulk chapter selection updates

Recent refactor outcomes

WebtoonCard and UpdateCard now reuse the shared card helper module for rounded covers, elided labels, and selection icons instead of duplicating those implementations.
Library and discovery detail pages now reuse shared chapter-selection helper functions for selector icons, hover visibility, and bulk selection state synchronization.
Discovery detail no longer imports shared sizing constants from the library detail page directly; both pages now read those values from gui/common/detail_shared.py.

Last synced: 2026-03-20

