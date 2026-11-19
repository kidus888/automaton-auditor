"""Loads the machine-readable "Constitution" (rubric/week2_rubric.json)."""

import json
import os
from typing import Dict, List, Optional

RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "..", "rubric", "week2_rubric.json")
KNOWN_ARTIFACTS = {"github_repo", "pdf_report", "pdf_images"}


def load_rubric(path: Optional[str] = None) -> Dict:
    """Reads the rubric JSON. Raises FileNotFoundError if it is missing: auditing without a
    constitution would silently produce an empty report."""
    with open(path or RUBRIC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_default_rubric(path: Optional[str] = None) -> List[Dict]:
    """Returns only the rubric dimensions (kept for backwards compatibility)."""
    return load_rubric(path).get("dimensions", [])
