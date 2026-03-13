# scraper package
import sys
from pathlib import Path


def _external_scrapers_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.joinpath("scrapers")
    return Path(__file__).resolve().parent


_external_root = _external_scrapers_root()
if _external_root.is_dir():
    external_root_str = str(_external_root)
    if external_root_str not in __path__:
        __path__.append(external_root_str)
