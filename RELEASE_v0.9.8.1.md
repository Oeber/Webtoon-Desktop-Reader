# Webtoon Desktop Reader v0.9.8.1

Focused desktop UI polish release.

## What Changed

- fixed the authorization/captcha flow so closing the browser window no longer forces the main app back through `showNormal()`
- fixed the app's vertical resize limit by making the Settings General tab scroll instead of forcing the whole window to inherit the page's tall minimum height
- fixed the Discover grid so scrolling and auto-loading more results no longer causes the visible card column count to drop after relayouts
- kept the packaged installer version metadata in sync with `data/app_version.txt`

## Notes

- this release is focused on windowing and discovery-layout regressions rather than new features
- Discover should now keep a stable grid width even after repeated append/refresh cycles

## Main Documentation

- [README.md](/f:/reader/README.md)
- [SCRAPPER GUIDE.MD](/f:/reader/SCRAPPER%20GUIDE.MD)
- [DISCOVERY SCRAPER GUIDE.MD](/f:/reader/DISCOVERY%20SCRAPER%20GUIDE.MD)
