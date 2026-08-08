# Runtime hook: make importlib.resources and PackageLoader resolve FTMON data
# from the frozen onedir layout (issue #95).
import os
import sys

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # Ensure the extracted package tree is importable ahead of site-packages.
    meipass = sys._MEIPASS
    if meipass not in sys.path:
        sys.path.insert(0, meipass)
    os.environ.setdefault("FTMON_FROZEN", "1")
