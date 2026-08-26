"""No screen may cut its own text off.

test_layout_fits.py asks a narrower question: does a *fixed-width* control fit
its label. That misses the failures that actually shipped, because none of them
involved a fixed width:

  * History's detail panel had a committed 178px height for content that needs
    195-310px, so the metrics eyebrows were sliced off the top and the last
    category rows off the bottom;
  * the sidebar gave its nav label ~119px and "Автозавантаження" wanted 129px,
    cut mid-glyph with no ellipsis;
  * table headings were sized against the English word, so "ITEMS" fitted and
    "ЕЛЕМЕНТИ" rendered as "ЛЕМЕНТ".

So this file measures three things on a real, populated widget tree, in every
shipped language, at both the largest and the smallest window Podbye allows:

  CLIP-H    a container whose committed height is under what its layout needs
  CLIP-W    a squeezed label/button whose text is wider than the widget
  CLIP-HDR  a header section too narrow for its own heading

Anything that must shorten to fit is expected to elide (ElidedLabel) or wrap,
and both are exempt — the rule is that text is never silently cut.
"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QCheckBox, QComboBox, QHeaderView,
    QLabel, QPushButton, QScrollArea, QWidget,
)

from app.config.settings_store import SettingsStore
from app.i18n import available_languages, get_language, set_language
from app.models.smart_entity import SmartEntity
from app.state.scan_state import ScanState
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel

_QWIDGETSIZE_MAX = 16777215

# Content area beside the 196px sidebar, at Podbye's largest realistic window
# and at the 1100x700 minimum enforced in main.py.
_SIZES = [(1724, 1000), (884, 620)]


@pytest.fixture(scope="module")
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    # NB this stylesheet stays applied for the rest of the session — restoring
    # it re-polishes every widget alive in other test files and faults.
    qapp.setStyleSheet(build_qss("forest"))
    original = get_language()
    yield qapp
    set_language(original)


# ── the sample content every screen is measured against ───────────


def _entities() -> list[SmartEntity]:
    """Long-but-real values: a publisher, a version, a deep install path."""
    specs = [
        ("C:/Program Files/Adobe/Adobe Photoshop 2024", "Adobe Photoshop 2024",
         "application", "Review", 12 * 1024 ** 3),
        ("C:/Users/n/AppData/Local/Google/Chrome/User Data/Default/Cache",
         "Chrome cache", "browser_cache", "Safe", 2 * 1024 ** 3),
        ("C:/Users/n/Downloads", "Loose installers", "installer_group",
         "Optional", 9 * 1024 ** 3),
        ("C:/dev/podbye/node_modules", "node_modules", "dev_artifact",
         "Optional", 3 * 1024 ** 3),
        ("C:/Windows/SoftwareDistribution/Download", "Windows update cache",
         "system_cache", "Protected", 6 * 1024 ** 3),
    ]
    out = []
    for path, name, etype, risk, size in specs:
        e = SmartEntity(path=path, name=name, entity_type=etype)
        e.size_bytes, e.file_count, e.risk = size, 4210, risk
        e.risk_reason = ("Installed application with an uninstaller registered "
                         "in Windows; removing the folder by hand would leave "
                         "registry entries behind.")
        e.app_publisher = "Adobe Systems Incorporated"
        e.app_version = "25.3.1.20240220"
        e.removable_file_paths = [f"{path}/file_{i}.tmp" for i in range(6)]
        out.append(e)
    return out


_NOW = time.time()

_CLEANUP_RECORDS = [{
    "timestamp": _NOW - 3600 * (i + 1),
    "mode": "recycle_bin" if i % 2 == 0 else "permanent",
    "succeeded_count": 274, "in_use_count": 19, "failed_count": 6,
    "skipped_protected_count": 170, "total_bytes_freed": 170 * 1024 ** 2,
    "session_id": "s0",
    "items": (
        [{"path": r"C:\Users\n\AppData\Local\Temp\a%d" % k, "size_bytes": 1024 ** 2}
         for k in range(296)]
        + [{"path": r"C:\Windows\Temp\b%d" % k, "size_bytes": 900} for k in range(99)]
        + [{"path": r"C:\Windows\SoftwareDistribution\c%d" % k, "size_bytes": 500}
           for k in range(56)]
    ),
} for i in range(10)]

_SESSIONS = [{
    "session_id": "s%d" % i, "target": "C:/", "scan_mode": "smart",
    "status": "completed", "start_time": _NOW - 86400 * (i + 1),
    "saved_at": _NOW - 86400 * (i + 1) + 134,
    "display_count": 1158, "scanned_count": 785,
    "total_size": 371 * 1024 ** 3, "reclaimable_bytes": 29 * 1024 ** 3,
    "risk_totals": {"Safe": 300, "Review": 740, "Protected": 45, "Optional": 52},
    "category_totals": {
        "Applications":     {"count": 100, "size_bytes": 129 * 1024 ** 3},
        "Application Data": {"count": 80,  "size_bytes": 53 * 1024 ** 3},
        "Dev Artifacts":    {"count": 60,  "size_bytes": 47 * 1024 ** 3},
    },
} for i in range(10)]


def _scan_state() -> ScanState:
    st = ScanState()
    st.set_settings_store(SettingsStore())
    st.set_scan_mode("smart")
    st._entities = _entities()
    st._entity_dict_dirty = True
    return st


def _build_screens() -> list[tuple[str, QWidget]]:
    """One populated instance of every screen that renders variable text."""
    from app.screens.analyze import AnalyzeScreen
    from app.screens.findings_dashboard import (
        CategoryDetailView, FindingsDashboard, FolderTreeView,
    )
    from app.screens.history import HistoryScreen
    from app.screens.home import HomeScreen
    from app.screens.settings import SettingsScreen
    from app.screens.startups import StartupsScreen
    from app.widgets.sidebar import Sidebar

    store = SettingsStore()
    screens: list[tuple[str, QWidget]] = []

    home = HomeScreen()
    home.set_scan_state(_scan_state())
    home.refresh()
    screens.append(("Home", home))

    analyze = AnalyzeScreen()
    analyze.set_scan_state(_scan_state())
    screens.append(("Analyze", analyze))

    st = _scan_state()
    findings = FindingsDashboard()
    findings.set_scan_state(st)
    st.entities_ready.emit()
    screens.append(("Findings", findings))

    detail = CategoryDetailView()
    detail_state = _scan_state()
    detail.set_scan_state(detail_state)
    detail.set_category("Applications", detail_state.entities_as_dicts())
    screens.append(("CategoryDetail", detail))

    tree = FolderTreeView()
    tree.set_entities(_scan_state().entities_as_dicts())
    screens.append(("FolderTree", tree))

    startups = StartupsScreen()
    startups.set_settings_store(store)
    screens.append(("Startups", startups))

    history = HistoryScreen()
    history._sessions = list(_SESSIONS)
    history._cleanup_records = list(_CLEANUP_RECORDS)
    screens.append(("History", history))

    screens.append(("Settings", SettingsScreen(theme_callback=lambda _k: None,
                                               settings_store=store)))
    screens.append(("Sidebar", Sidebar()))
    return screens


def _expand(name: str, screen: QWidget):
    """Open the panels that are hidden until the user clicks something."""
    if name == "History":
        screen._build_content()          # pick up the injected records
        screen._toggle_cleanup_detail(0)
        screen._toggle_sess_detail(0)
    elif name == "CategoryDetail":
        screen._select_source_row(0)


def _destroy(built, qapp):
    """Free the screen trees for real.

    deleteLater() only *posts* a DeferredDelete, and a plain processEvents()
    outside a running event loop does not deliver those — so every screen this
    file built stayed alive. Six parametrisations × eight screens is 48 full
    widget trees, which exhausted Qt's window/GDI resources part-way through
    the suite and took the process down with a segfault.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    for _name, screen in built:
        screen.close()
        screen.setParent(None)
        screen.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _settle(root: QWidget, qapp):
    """Land any queued work, then force every layout to compute its geometry."""
    for _ in range(40):
        qapp.processEvents()
    for w in root.findChildren(QWidget) + [root]:
        lay = w.layout()
        if lay is not None:
            lay.activate()
    qapp.processEvents()


# ── the three measurements ────────────────────────────────────────


def _describe(w: QWidget, root: QWidget) -> str:
    names, cur = [], w
    while cur is not None and cur is not root and len(names) < 4:
        names.append(type(cur).__name__
                     + (f"#{cur.objectName()}" if cur.objectName() else ""))
        cur = cur.parentWidget()
    return " < ".join(names + [type(root).__name__])


def _clipped(root: QWidget) -> list[str]:
    bad: list[str] = []
    for w in root.findChildren(QWidget) + [root]:
        if not w.isVisibleTo(root):
            continue

        # CLIP-H — a committed height under what the layout needs.
        lay = w.layout()
        if lay is not None and w.maximumHeight() < _QWIDGETSIZE_MAX:
            need = lay.minimumSize().height()
            if lay.hasHeightForWidth() and w.width() > 0:
                need = max(need, lay.heightForWidth(w.width()))
            if need > w.maximumHeight() + 1:
                bad.append(f"CLIP-H {_describe(w, root)}: needs {need}px, "
                           f"capped at {w.maximumHeight()}px")

        # CLIP-W — text wider than the width the widget was squeezed into.
        if not isinstance(w, (QLabel, QPushButton, QComboBox, QCheckBox)):
            continue
        if isinstance(w, ElidedLabel):
            continue            # elides, and keeps the full text as a tooltip
        text = w.currentText() if isinstance(w, QComboBox) else w.text()
        if not text.strip():
            continue
        if isinstance(w, QLabel) and (w.wordWrap()
                                      or w.textFormat() == Qt.RichText):
            continue
        chrome = 30 if isinstance(w, QCheckBox) else (
            20 if isinstance(w, (QPushButton, QComboBox)) else 2)
        # A label handed its natural width reports needed == width, which is
        # normal. Only a widget squeezed below what it asked for is cut.
        squeezed = w.width() + 2 < w.sizeHint().width()
        need = QFontMetrics(w.font()).horizontalAdvance(text) + chrome
        if w.width() > 0 and squeezed and need > w.width():
            bad.append(f"CLIP-W {_describe(w, root)}: {text[:44]!r} needs "
                       f"{need}px, has {w.width()}px")
    return bad


def _clipped_headers(root: QWidget) -> list[str]:
    bad: list[str] = []
    for view in root.findChildren(QAbstractItemView):
        hdr = getattr(view, "horizontalHeader", lambda: None)()
        if hdr is None or not hdr.isVisibleTo(root):
            continue
        model, fm = hdr.model(), QFontMetrics(hdr.font())
        for col in range(hdr.count()):
            if hdr.isSectionHidden(col):
                continue
            text = str(model.headerData(col, hdr.orientation()) or "")
            if not text.strip():
                continue
            # QSS `QHeaderView::section` padding is not part of sectionSize.
            need = fm.horizontalAdvance(text) + 12
            if need > hdr.sectionSize(col):
                bad.append(f"CLIP-HDR {type(view).__name__}#{view.objectName()} "
                           f"col {col}: {text!r} needs {need}px, section is "
                           f"{hdr.sectionSize(col)}px")
    return bad


# ── the tests ─────────────────────────────────────────────────────


@pytest.mark.parametrize("language", available_languages())
def test_no_screen_cuts_its_own_text_off(app, language):
    """Both window extremes are checked on ONE set of screens.

    Deliberately not parametrised over size as well: each parametrisation
    builds eight full screen trees, and doing that twice per language put ~48
    of them in one session. Qt ran out of window/GDI handles part-way through
    the suite and the process died with a segfault rather than a failure.
    Resizing in place measures the same thing for half the resources.
    """
    set_language(language)
    problems: list[str] = []
    built = _build_screens()
    for name, screen in built:
        screen.show()
        app.processEvents()
        _expand(name, screen)
        for width, height in _SIZES:
            screen.resize(196 if name == "Sidebar" else width, height)
            _settle(screen, app)
            problems += [f"{name} at {width}x{height}: {p}"
                         for p in _clipped(screen) + _clipped_headers(screen)]
    _destroy(built, app)
    assert not problems, (
        f"{language}: text is cut off —\n  " + "\n  ".join(problems))


def test_the_harness_can_actually_detect_clipping(app):
    """A check that never fails is worthless — prove each rule catches a case."""
    from PySide6.QtWidgets import QVBoxLayout

    host = QWidget()
    lay = QVBoxLayout(host)
    lay.addWidget(QLabel("x\ny\nz\nw\nv"))
    host.setFixedHeight(4)
    host.resize(200, 4)
    host.show()
    app.processEvents()
    assert any(p.startswith("CLIP-H") for p in _clipped(host)), \
        "height check failed to flag a container smaller than its content"

    label = QLabel("A" * 200)
    label.setFixedWidth(30)
    label.show()
    app.processEvents()
    assert any(p.startswith("CLIP-W") for p in _clipped(label)), \
        "width check failed to flag obviously overflowing text"

    for w in (host, label):
        w.close()
        w.deleteLater()
    app.processEvents()


def test_an_elided_label_is_not_reported(app):
    """ElidedLabel shortening to '…' is the intended behaviour, not a defect."""
    label = ElidedLabel("A" * 200)
    label.setFixedWidth(30)
    label.show()
    app.processEvents()
    assert not _clipped(label)
    label.close()
    label.deleteLater()
    app.processEvents()


# ── History: the two explanation panels sit side by side ─────────


def test_history_detail_panels_share_a_height(app):
    """Two panels of different heights leave a ragged bottom edge and read as
    one being unfinished — the cleanup panel runs to ~300px and the scan panel
    to ~245px purely because of how many rows each happens to have."""
    from app.screens.history import HistoryScreen

    set_language("English")
    screen = HistoryScreen()
    screen._sessions = list(_SESSIONS)
    screen._cleanup_records = list(_CLEANUP_RECORDS)
    screen.resize(1724, 1000)          # wide enough to stay side by side
    screen.show()
    app.processEvents()
    screen._build_content()
    screen._toggle_cleanup_detail(0)
    screen._toggle_sess_detail(0)
    _settle(screen, app)

    left = screen._cleanup_detail_area
    right = screen._sess_detail_area
    assert left is not None and right is not None
    assert left.isVisible() and right.isVisible()
    assert abs(left.height() - right.height()) <= 2, (
        f"panels differ: {left.height()}px vs {right.height()}px")

    screen.close()
    screen.deleteLater()
    app.processEvents()
