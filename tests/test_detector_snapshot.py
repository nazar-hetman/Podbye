"""Characterization test for detect_entities().

Runs a rich synthetic tree through the detector and asserts the full output
matches a stored snapshot. This is the regression net for the detect_entities()
refactor: any behavior change shows up as a snapshot mismatch.

To intentionally re-baseline after a deliberate behavior change, delete
tests/fixtures/detector_snapshot.json and regenerate it.
"""
import json
from pathlib import Path

from app.services.entity_detector import detect_entities
from tests.treebuild import rich_tree

ROOT = "T:/snap"
_FIXTURE = Path(__file__).with_name("fixtures") / "detector_snapshot.json"


def _canonical(entities):
    rows = [{
        "entity_type": e.entity_type,
        "name": e.name,
        "category": e.category,
        "risk": e.risk,
        "file_count": e.file_count,
        "folder_count": e.folder_count,
        "size_bytes": e.size_bytes,
    } for e in entities]
    rows.sort(key=lambda r: (r["entity_type"], r["name"], r["size_bytes"]))
    return rows


def test_detector_output_matches_snapshot():
    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    actual = _canonical(detect_entities(rich_tree(ROOT), ROOT))
    assert actual == expected


def test_snapshot_covers_multiple_passes():
    """Sanity check that the fixture exercises a broad set of passes."""
    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    types = {r["entity_type"] for r in expected}
    # Phase 1 (monolith), Pass 1 (known dirs), Pass 3b (games),
    # Pass 6 (content), and low-value suppression.
    for t in ("application", "node_modules", "game",
              "photo_collection", "venv"):
        assert t in types, f"snapshot no longer covers {t}"
