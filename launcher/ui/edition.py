"""One feature set, two visual profiles; public builds contain no personal art."""
import json
from pathlib import Path

PROFILE_PATH = Path(__file__).resolve().parent.parent / "assets" / "edition.json"
try:
    IS_PERSONAL = json.loads(PROFILE_PATH.read_text("utf-8")).get("edition") == "personal"
except (OSError, ValueError, AttributeError):
    IS_PERSONAL = False
