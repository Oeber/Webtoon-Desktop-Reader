import inspect
from gui.discovery import site_browser_page
src = inspect.getsource(site_browser_page)
for i, line in enumerate(src.split('\n')):
    if 'unknown' in line.lower() or 'total_chapter' in line.lower() or 'author' in line.lower():
        print(i, repr(line))