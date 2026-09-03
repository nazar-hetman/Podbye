"""A folder's label must describe the folder the row reports on.

Every row in Findings shows a name and a recursive total. When the name is
drawn from a sample that is nothing like the tree being measured, the row is a
confident statement about something that isn't there. Three of these were
reported from one all-drives scan, and all three are the same defect:

* ``E:/Survey060626-test/coordinate_recovery_outputs`` — 187,555 files,
  97% of them PNG and 20 GB of JPEG — was labelled **Documents Folder**,
  because the only loose file at its top is one ``.md`` report.
* ``D:/`` — nine drone-survey folders and a ``RESULTS.md`` — became a
  **Documents Folder** entity for the whole drive.
* ``E:/Workbench/investigations`` — 31 of its 38 GB are ``.tif`` and ``.dmap``
  imagery — described itself as "mostly code & config", from the handful of
  ``.py`` scripts sitting at its top.

Pass 7 already guarded against this (``_direct_files_describe_folder``). Pass 6
runs first and did not, so it decided all three before pass 7 could.
"""
from app.services.entity_detector import (
    detect_entities, _looks_like_source_tree,
)
from tests.treebuild import mkdir, mkfile

MB = 1024 * 1024
ROOT = "T:/scan"


def _photo_tree_with_one_readme(root: str):
    """The shape of coordinate_recovery_outputs: a report over a photo tree."""
    f = [mkdir(root), mkfile(f"{root}/recovery_report.md", 40_000)]
    for run in range(4):
        f.append(mkdir(f"{root}/run{run}"))
        f += [mkfile(f"{root}/run{run}/frame{i}.png", 8 * MB) for i in range(20)]
    return f


def test_a_photo_tree_is_not_documents_because_of_one_report():
    entities = detect_entities(_photo_tree_with_one_readme(f"{ROOT}/outputs"),
                               ROOT, log_fn=lambda _m: None)
    subject = next(e for e in entities if e.path.endswith("outputs"))
    assert subject.entity_type != "document_folder", (
        f"{subject.file_count} files, all but one an image, labelled "
        f"{subject.entity_type!r} from the single .md at the top")
    assert subject.entity_type in ("photo_collection", "media_collection",
                                   "dataset")


def test_a_drive_root_is_not_one_content_collection():
    """D:/ held nine survey folders and a RESULTS.md and became "Documents"."""
    findings = [mkdir("D:/"), mkfile("D:/RESULTS.md", 10_300)]
    for name in ("SiteA", "SiteB", "SiteC"):
        findings.append(mkdir(f"D:/{name}"))
        findings += [mkfile(f"D:/{name}/img{i}.jpg", 12 * MB) for i in range(6)]

    entities = detect_entities(findings, "D:/", log_fn=lambda _m: None)

    roots = [e for e in entities
             if e.path.replace("\\", "/").rstrip("/").lower() == "d:"]
    assert not roots, (
        f"the drive root became an entity: "
        f"{[(e.name, e.entity_type, e.file_count) for e in roots]}")


def test_the_folders_under_it_are_still_classified():
    """Skipping the root must not cost the rows a user actually wants."""
    findings = [mkdir("D:/"), mkfile("D:/RESULTS.md", 10_300)]
    findings.append(mkdir("D:/SiteA"))
    findings += [mkfile(f"D:/SiteA/img{i}.jpg", 12 * MB) for i in range(8)]

    entities = detect_entities(findings, "D:/", log_fn=lambda _m: None)
    site_a = next(e for e in entities if e.path.lower().endswith("sitea"))
    assert site_a.entity_type == "photo_collection"


# ── source trees are a kind, not an absence of one ────────────────

def test_a_conda_environment_is_not_unclassified():
    """52 GB of the Unknown category was environments and build trees."""
    root = f"{ROOT}/cuda-env"
    f = [mkdir(root), mkdir(f"{root}/Lib"), mkdir(f"{root}/Scripts")]
    f += [mkfile(f"{root}/Lib/mod{i}.py", 40_000) for i in range(12)]
    f += [mkfile(f"{root}/Lib/conf{i}.json", 4_000) for i in range(6)]
    f += [mkfile(f"{root}/Scripts/run{i}.bat", 900) for i in range(3)]

    entities = detect_entities(f, ROOT, log_fn=lambda _m: None)
    subject = next(e for e in entities if e.path.endswith("cuda-env"))
    assert subject.entity_type != "unknown_folder"
    assert subject.category == "Dev Artifacts"


def test_configuration_alone_is_not_a_project():
    """Half of AppData is .json and .xml; none of it is a source tree."""
    files = [mkfile(f"{ROOT}/state/entry{i}.json", 2_000) for i in range(20)]
    assert _looks_like_source_tree(files) is False


def test_a_source_tree_needs_a_majority_too():
    files = ([mkfile(f"{ROOT}/mix/a{i}.py", 2_000) for i in range(3)]
             + [mkfile(f"{ROOT}/mix/b{i}.jpg", 2_000) for i in range(17)])
    assert _looks_like_source_tree(files) is False


# ── the sample that decides is the sample that represents ─────────

def test_the_content_sample_reaches_every_branch():
    """Depth-first spent the whole budget in one subfolder.

    Built so that a depth-first walk sees only ``deep`` — which is code — while
    the tree is overwhelmingly images. The label follows whichever the sample
    shows, so the sample has to show the tree.
    """
    from app.services.entity_detector import _DetectionContext

    root = f"{ROOT}/mixed"
    findings = [mkdir(root), mkdir(f"{root}/deep"), mkdir(f"{root}/photos")]
    findings += [mkfile(f"{root}/deep/src{i}.py", 1_000) for i in range(900)]
    findings += [mkfile(f"{root}/photos/p{i}.jpg", 3 * MB) for i in range(900)]

    ctx = _DetectionContext(findings, ROOT, lambda _m: None,
                            lambda *_a, **_k: None, lambda _e: None)
    sample = ctx.sample(root.lower(), limit=200)
    kinds = {(f.extension or "").lower() for f in sample if not f.is_dir}
    assert ".jpg" in kinds and ".py" in kinds, (
        f"the sample only reached {kinds} — one branch decided the label")


# ── the description and the classification must agree ─────────────

def test_a_description_counts_every_file_not_only_the_known_ones():
    """"mostly code & config" over a folder that is two-thirds .dll.

    The share used to be taken over the files a kind claimed, so the ones no
    rule matched simply left the denominator — and the row then described
    itself as code while the classifier, which counts everything, filed it
    under Unknown. That contradiction is what was reported.
    """
    from app.services.entity_detector import _content_descriptor
    files = ([mkfile(f"{ROOT}/env/h{i}.hpp", 1_000) for i in range(20)]
             + [mkfile(f"{ROOT}/env/lib{i}.dll", 4_000_000) for i in range(80)])
    assert "mostly" not in _content_descriptor(files)


def test_a_folder_of_unknown_extensions_says_so():
    """77% of E:/Workbench/investigations is .dat, .dmap, .result and .exif."""
    from app.services.entity_detector import _descriptive_folder_name
    files = ([mkfile(f"{ROOT}/runs/x{i}.dat", 1_000) for i in range(80)]
             + [mkfile(f"{ROOT}/runs/s{i}.py", 1_000) for i in range(20)])
    name = _descriptive_folder_name("runs", f"{ROOT}/runs", files)
    assert "unrecognized" in name, name


def test_a_loose_file_bucket_never_absorbs_the_drive_under_it():
    """Pass 8's "Misc files in X" buckets carry X as their path.

    A folder-backed mixed folder absorbs its noisy children, the way an
    unknown one does. A bucket standing for five stray files is not that
    folder, and treating it as one would swallow every entity below it.
    """
    findings = [mkdir("E:/Workbench")]
    findings += [mkfile(f"E:/Workbench/note{i}.qqq", 400) for i in range(5)]
    findings.append(mkdir("E:/Workbench/Photos"))
    findings += [mkfile(f"E:/Workbench/Photos/p{i}.jpg", 9 * MB) for i in range(9)]

    entities = detect_entities(findings, "E:/", log_fn=lambda _m: None)
    paths = {e.path.replace("\\", "/").lower() for e in entities}
    assert "e:/workbench/photos" in paths, (
        f"the photo folder was absorbed by a loose-file bucket: {sorted(paths)}")
