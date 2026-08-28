"""Keep is the user's own whitelist, and the screen has to say so at once.

Marking something Keep left the inspector showing "Keep" over a path that was
already kept; the state only appeared after closing and reopening the item.
populate() short-circuits on a signature of fifteen entity fields, and the keep
state is not one of them - it is read live from the whitelist rather than baked
into the dict, so marking a path changed nothing the guard was watching.

The wording carried its own confusion: the badge said KEEP and the button said
Keep, so an indicator reporting a state and a control performing an action read
identically.
"""
import pytest

import app.screens.findings_dashboard as fd
from app.themes.theme_manager import build_qss

PATH = r"C:\Users\n\AppData\Roaming\Contoso"


def _entity(path=PATH, name="Contoso"):
    return {"path": path, "name": name, "size": "400B", "size_bytes": 400,
            "risk": "Safe", "entity_type": "cache_folder",
            "category": "Cache & Temp", "file_count": 1, "folder_count": 0,
            "reclaimable_bytes": 400, "ai_status": "none"}


@pytest.fixture
def kept(monkeypatch):
    """An in-memory whitelist, so nothing touches the real one."""
    store = set()

    def norm(p):
        return (p or "").replace("\\", "/").rstrip("/").lower()

    monkeypatch.setattr(fd, "is_kept", lambda p: any(
        norm(p) == k or norm(p).startswith(k + "/") for k in store))
    monkeypatch.setattr(fd, "keep_path", lambda p: (store.add(norm(p)), True)[1])
    monkeypatch.setattr(fd, "unkeep_path", lambda p: (store.discard(norm(p)), True)[1])
    monkeypatch.setattr(fd, "kept_root_for", lambda p: p)
    monkeypatch.setattr(fd, "can_keep", lambda p: True)
    return store


@pytest.fixture
def view(qapp, kept):
    qapp.setStyleSheet(build_qss("forest"))
    v = fd.CategoryDetailView()
    v._app_index_cache = {}
    v.resize(1400, 800)
    v.show()
    v.set_category("Cache & Temp", [_entity()])
    qapp.processEvents()
    v._show_detail_sidebar(v._model.get_entity(0))
    qapp.processEvents()
    yield v
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()


def _row(view):
    return next(r for r in view._row_pool if not r.isHidden())


def _panel(view):
    return view._right_sidebar.detail_widget


# -- the state appears without reopening anything ------------------

def test_the_inspector_updates_the_moment_keep_is_pressed(view, qapp):
    """This is the whole bug: the button kept saying "Keep" over a kept path
    until the item was closed and opened again."""
    assert _panel(view)._btn_keep.text() == "Keep"
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert _panel(view)._btn_keep.text() == "Remove from Keep"


def test_removing_from_keep_updates_just_as_fast(view, qapp):
    view._toggle_keep(PATH)
    qapp.processEvents()
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert _panel(view)._btn_keep.text() == "Keep"


def test_the_keep_state_is_part_of_what_makes_a_render_stale(view, qapp):
    """Pinned at the mechanism, not the symptom: the guard has to watch the
    live actionability, or any later field added to the signature will not
    help and this regresses silently."""
    panel = _panel(view)
    before = panel._current_signature
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert panel._current_signature != before


# -- state and action stop sharing a word --------------------------

def test_the_badge_reports_a_state(view, qapp):
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert _panel(view)._risk_badge.text() == "KEPT"


def test_the_button_names_an_action(view, qapp):
    """A button labelled with a state would do the opposite of what it says."""
    view._toggle_keep(PATH)
    qapp.processEvents()
    text = _panel(view)._btn_keep.text()
    assert text == "Remove from Keep"
    assert text != "Kept"


def test_the_button_says_what_it_will_do_when_not_kept(view):
    assert _panel(view)._btn_keep.text() == "Keep"


# -- the lock, beside the name ------------------------------------

def test_no_lock_until_something_is_kept(view):
    assert not _row(view)._lock_lbl.isVisible()


def test_a_kept_thing_wears_a_lock(view, qapp):
    view._toggle_keep(PATH)
    qapp.processEvents()
    row = _row(view)
    assert row._lock_lbl.isVisible()
    assert not row._lock_lbl.pixmap().isNull()


def test_the_lock_replaces_the_counted_text(view, qapp):
    """"1 kept" sat in the metrics slot, next to "3 selected" - a standing
    instruction and a transient selection taking turns in one label."""
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert "kept" not in _row(view)._armed_lbl.text().lower()


def test_the_lock_says_what_it_means(view, qapp):
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert "keeping" in _row(view)._lock_lbl.toolTip().lower()


def test_the_lock_goes_when_the_mark_does(view, qapp):
    view._toggle_keep(PATH)
    qapp.processEvents()
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert not _row(view)._lock_lbl.isVisible()


# -- classification is untouched ----------------------------------

def test_keeping_does_not_change_the_risk_tier(view, qapp):
    """Safe / Optional / Review / Protected are Podbye's judgement; Keep is
    the user's. The row badge goes on reporting the classification."""
    before = _row(view)._risk_badge.text()
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert _row(view)._risk_badge.text() == before == "SAFE"


def test_a_kept_item_still_cannot_be_armed(view, qapp):
    """The safety behaviour this whole feature exists for."""
    view._toggle_keep(PATH)
    qapp.processEvents()
    assert fd._entity_actionability(_entity()) == "kept"


# -- the glyph itself ---------------------------------------------

def test_the_lock_is_drawn_not_typed(qapp):
    """An emoji renders in the platform's emoji font, in its own colours, at
    its own weight. This is drawn in the palette colour it is handed."""
    from app.widgets.logo import lock_pixmap
    px = lock_pixmap("#7aa88a", 12)
    assert not px.isNull()
    assert px.width() == 12
    image = px.toImage()
    opaque = [image.pixelColor(x, y) for x in range(px.width())
              for y in range(px.height())
              if image.pixelColor(x, y).alpha() > 200]
    assert opaque, "nothing was drawn"
    # Within a couple of levels per channel: antialiasing moves edge pixels off
    # the exact value, and the point of the check is that the colour handed in
    # is the colour used - not that every pixel is identical.
    assert all(abs(c.red() - 0x7a) <= 2 and abs(c.green() - 0xa8) <= 2
               and abs(c.blue() - 0x8a) <= 2 for c in opaque), (
        "the glyph ignored the colour it was given")


def test_the_lock_follows_the_theme(qapp):
    from app.widgets.logo import lock_pixmap
    a = lock_pixmap("#7aa88a", 12).toImage()
    b = lock_pixmap("#c67a69", 12).toImage()
    assert a != b
