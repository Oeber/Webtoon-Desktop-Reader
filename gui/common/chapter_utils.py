import re


SPECIAL_CHAPTER_RE = re.compile(r"\b\d+\.\d+\b")


def chapter_sort_key(name: str):
    match = re.search(r"(\d+(?:\.\d+)?)", name)
    if match:
        try:
            return (0, float(match.group(1)), name.lower())
        except Exception:
            pass
    return (1, float("inf"), name.lower())
