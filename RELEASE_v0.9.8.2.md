# Webtoon Desktop Reader v0.9.8.2

Combined follow-up release that includes the v0.9.8.1 UI polish fixes and the updater package selection fix.

## What Changed

- includes the v0.9.8.1 fixes for the authorization window state, Settings page vertical resizing, and Discover grid relayout stability
- changed automatic self-update package selection to only consider release assets whose names end with `-portable.zip`
- updated self-update wording and timeout/error text so the portable package flow no longer refers to itself as an installer
- kept packaged installer metadata aligned with `data/app_version.txt` for v0.9.8.2

## Notes

- automatic in-app updates now require the GitHub release to include a `-portable.zip` asset
- releases that only upload installer assets or generic zip names will fall back to manual download instead of attempting the wrong package

## Main Documentation

- [README.md](/f:/reader/README.md)
- [SCRAPPER GUIDE.MD](/f:/reader/SCRAPPER%20GUIDE.MD)
- [DISCOVERY SCRAPER GUIDE.MD](/f:/reader/DISCOVERY%20SCRAPER%20GUIDE.MD)
