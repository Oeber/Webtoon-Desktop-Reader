import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_root()))


def data_path(*parts: str) -> Path:
    return app_root().joinpath("data", *parts)


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def default_library_path() -> Path:
    return app_root().joinpath("webtoons")


def external_scrapers_path() -> Path:
    return app_root().joinpath("scrapers", "sites")
