"""Local model stores must be recognised, and must never look disposable.

Vigil is aimed at people running local LLMs, and the two runners its own README
names store weights in ways the extension-driven rules could not see:

  Ollama       ~/.ollama/models/blobs/sha256-<hash>          (no extension)
  HuggingFace  ~/.cache/huggingface/hub/models--*/blobs/<hash>  (no extension,
                                                                 under "cache")

The first meant a 6.7 GB model was categorised "Unknown" and grouped as "Misc
files in blobs". The second was worse: the cache-keyword rule matched on
"cache" and returned before any AI/ML rule ran, so a 4 GB model — a plain
.gguf included — came back "Cache & Temp" at risk Safe, which is the tier
Quick Cleanup offers to bulk-select.
"""
import os

import pytest

from app.models.finding import Finding
from app.services.entity_detector import detect_entities

HOME = os.path.expanduser("~").replace("\\", "/")
GB = 1024 ** 3


def _f(path: str, size: int) -> Finding:
    return Finding(path=path, name=os.path.basename(path), is_dir=False,
                   size_bytes=size, extension=os.path.splitext(path)[1],
                   modified=0, accessed=0, parent=os.path.dirname(path))


# ── File-level classification ────────────────────────────────────

@pytest.mark.parametrize("path,size", [
    (f"{HOME}/.ollama/models/blobs/sha256-4e30e2665218", 7 * GB),
    (f"{HOME}/.cache/huggingface/hub/models--meta-llama/blobs/a1b2c3", 4 * GB),
    (f"{HOME}/.cache/huggingface/hub/models--x/snapshots/w.safetensors", 9 * GB),
    (f"{HOME}/.cache/huggingface/hub/models--x/blobs/model.gguf", 4 * GB),
    (f"{HOME}/.lmstudio/models/TheBloke/llama.gguf", 4 * GB),
    ("D:/ComfyUI/models/checkpoints/sdxl.safetensors", 6 * GB),
])
def test_model_weights_are_ai_ml(path, size):
    assert _f(path, size).category == "AI / ML"


@pytest.mark.parametrize("path,size", [
    (f"{HOME}/.cache/huggingface/hub/models--meta-llama/blobs/a1b2c3", 4 * GB),
    (f"{HOME}/.cache/huggingface/hub/models--x/blobs/model.gguf", 4 * GB),
])
def test_a_model_under_a_cache_path_is_never_marked_safe(path, size):
    """Safe is the tier bulk cleanup selects. Weights cost bandwidth to replace."""
    finding = _f(path, size)
    assert finding.category != "Cache & Temp"
    assert finding.risk != "Safe", (
        f"{finding.category}/{finding.risk}: a model the user would have to "
        f"re-download is offered as safe to delete"
    )


@pytest.mark.parametrize("path,size,expected", [
    # Small blobs are manifests, configs and tokenizers — not weights.
    (f"{HOME}/.ollama/models/blobs/sha256-tiny", 1024, "Unknown"),
    # A big extensionless file with nothing model-ish about its path.
    ("C:/Users/n/randomstuff/bigfile", 7 * GB, "Unknown"),
])
def test_the_blob_rule_does_not_over_reach(path, size, expected):
    assert _f(path, size).category == expected


@pytest.mark.parametrize("path,size,expected", [
    (f"{HOME}/AppData/Local/Google/Chrome/User Data/Default/Cache/f_00a1",
     80_000_000, "Browser Data"),
    ("C:/Windows/Temp/tmp1234.tmp", 90_000_000, "Cache & Temp"),
    # Extensionless, large, under a cache path — but no model keyword.
    (f"{HOME}/AppData/Local/pip/cache/http/0/1/abcdef", 90_000_000, "Cache & Temp"),
])
def test_real_caches_are_still_caches(path, size, expected):
    """Hoisting the model check above the cache rule must not shadow it."""
    assert _f(path, size).category == expected


# ── Entity-level grouping ────────────────────────────────────────

def test_an_ollama_blob_store_groups_as_ai_models():
    """Grouping keys on extensions too, so the file-level fix is not enough."""
    root = f"{HOME}/.ollama"
    blobs = f"{root}/models/blobs"
    files = [
        _f(f"{blobs}/sha256-{i:02d}", 3 * GB) for i in range(3)
    ] + [
        _f(f"{blobs}/sha256-manifest{i}", 900) for i in range(4)
    ]

    entities = detect_entities(files, root)
    models = [e for e in entities if e.entity_type == "ai_models"]
    assert models, (
        "the blob store produced no AI model entity — "
        f"got {sorted({e.entity_type for e in entities})}"
    )
    assert models[0].category == "AI / ML"
    # The weights, not the manifests, are what the entity accounts for.
    assert models[0].size_bytes >= 9 * GB
