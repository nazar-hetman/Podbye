"""The analysis pipeline must complete cleanly with AI explanations disabled,
and the on-demand path lookup used by "Ask AI" must stay correct.
"""
import os

from app.state.scan_state import ScanState
from app.services.ai_explainer import AIExplainer
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity

# QApplication + offscreen platform come from the session fixture in conftest.py


class _FakeStore:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _finding(path=r"C:\x\f.txt"):
    return Finding(path=path, name=os.path.basename(path), is_dir=False,
                   size_bytes=100, extension=".txt", modified=0, accessed=0,
                   parent=os.path.dirname(path))


def _state(ai_enabled: bool):
    store = _FakeStore({"ai_findings_enabled": ai_enabled, "ai_model": "llama3"})
    ss = ScanState()
    ss.set_settings_store(store)
    ss.set_ai_explainer(AIExplainer(store))
    ss.clear()
    return ss


def test_pipeline_completes_with_ai_disabled():
    """Entity results must finish the run (phase 'complete' + entities_ready)
    instead of hanging in an ai_classification phase that never starts."""
    ss = _state(ai_enabled=False)
    entity = SmartEntity(path="C:/x", name="x", entity_type="cache_folder",
                         file_count=1, size_bytes=1024, risk="Optional")

    phases, ready = [], []
    ss.scan_phase_changed.connect(lambda p, m: phases.append(p))
    ss.entities_ready.connect(lambda: ready.append(True))

    ss._pending_entities = [entity]
    ss._apply_entity_results()

    assert ss.current_phase == "complete", f"phases seen: {phases}"
    assert ready, "entities_ready must fire so the UI leaves the loading state"
    assert not ss.ai_explainer.is_running, "AI must not run when disabled"
    assert ss.entity_count == 1


def test_start_ai_queue_is_a_noop_when_disabled():
    ss = _state(ai_enabled=False)
    ss.add_findings([_finding()])
    ss.start_ai_queue()
    assert not ss.ai_explainer.is_running


def test_find_by_path_returns_live_objects():
    ss = _state(ai_enabled=False)
    f = _finding()
    ss.add_findings([f])
    # Same instance back — the explainer must mutate what the UI reads.
    assert ss.find_by_path(r"C:\x\f.txt") is f
    # Case- and separator-insensitive.
    assert ss.find_by_path("c:/X/F.TXT") is f
    assert ss.find_by_path(r"C:\nope") is None


def test_entity_shadows_finding_at_same_path():
    ss = _state(ai_enabled=False)
    f = _finding(path="C:/x")
    ss.add_findings([f])
    e = SmartEntity(path="C:/x", name="x", entity_type="cache_folder",
                    file_count=1, size_bytes=1024)
    ss.add_entities([e])
    assert ss.find_by_path("C:/x") is e


def test_path_index_invalidated_when_findings_change():
    ss = _state(ai_enabled=False)
    a = _finding(path=r"C:\x\a.txt")
    ss.add_findings([a])
    assert ss.find_by_path(r"C:\x\a.txt") is a  # builds the index

    b = _finding(path=r"C:\x\b.txt")
    ss.add_findings([b])  # must invalidate, not serve a stale index
    assert ss.find_by_path(r"C:\x\b.txt") is b
