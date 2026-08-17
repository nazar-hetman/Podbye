"""Golden-tree tests for detect_entities() — the categorization pipeline.

A synthetic directory tree is fed through the detector and the resulting
SmartEntities are checked for the expected type / category / risk, plus the
Containment Rule (no file left ungrouped).
"""
from app.services.entity_detector import detect_entities
from tests.treebuild import mkdir, mkfile, rich_tree

ROOT = "T:/scan"


def _golden_tree():
    """Four independent sibling folders under a synthetic scan root."""
    return [
        mkdir(f"{ROOT}/node_modules"),
        mkfile(f"{ROOT}/node_modules/index.js"),
        mkfile(f"{ROOT}/node_modules/util.js"),
        mkfile(f"{ROOT}/node_modules/package.json"),

        mkdir(f"{ROOT}/photos"),
        mkfile(f"{ROOT}/photos/img1.jpg"),
        mkfile(f"{ROOT}/photos/img2.jpg"),
        mkfile(f"{ROOT}/photos/img3.png"),

        mkdir(f"{ROOT}/cache"),
        mkfile(f"{ROOT}/cache/blob1.bin"),
        mkfile(f"{ROOT}/cache/blob2.bin"),

        mkdir(f"{ROOT}/logs"),
        mkfile(f"{ROOT}/logs/run.log"),
        mkfile(f"{ROOT}/logs/error.log"),
    ]


def test_empty_input_returns_no_entities():
    assert detect_entities([], ROOT) == []


def test_empty_placeholder_folders_are_suppressed():
    logs: list[str] = []
    tree = [
        mkdir(f"{ROOT}/Saved Games"),
    ]

    entities = detect_entities(tree, ROOT, log_fn=logs.append)

    assert entities == []
    assert any("suppressed" in line and "contains no files" in line for line in logs)


def test_tiny_unknown_findings_are_suppressed():
    logs: list[str] = []
    tree = [
        mkdir(f"{ROOT}/fixtures"),
        mkfile(f"{ROOT}/fixtures/.placeholder", 282),
    ]

    entities = detect_entities(tree, ROOT, log_fn=logs.append)

    assert entities == []
    assert any("suppressed" in line and "placeholder" in line for line in logs)


def test_fixture_folders_are_development_artifacts_before_unknown():
    tree = [
        mkdir(f"{ROOT}/fixtures"),
        mkfile(f"{ROOT}/fixtures/catalog.json", 80_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert len(entities) == 1
    assert entities[0].entity_type == "dev_artifacts"
    assert entities[0].category == "Dev Artifacts"


def test_development_asset_folders_are_classified_before_unknown():
    tree = [
        mkdir(f"{ROOT}/assets"),
        mkfile(f"{ROOT}/assets/bundle.js", 80_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert len(entities) == 1
    assert entities[0].entity_type == "dev_artifacts"
    assert entities[0].category == "Dev Artifacts"


def test_project_marker_folders_are_development_projects():
    tree = [
        mkdir(f"{ROOT}/WidgetTool"),
        mkfile(f"{ROOT}/WidgetTool/package.json", 80_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert len(entities) == 1
    assert entities[0].entity_type == "dev_project"
    assert entities[0].category == "Dev Artifacts"


def test_application_support_paths_are_application_data():
    tree = [
        mkdir(f"{ROOT}/Application Support"),
        mkdir(f"{ROOT}/Application Support/WidgetApp"),
        mkfile(f"{ROOT}/Application Support/WidgetApp/config.db", 80_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert len(entities) == 1
    assert entities[0].entity_type == "application_data"
    assert entities[0].category == "Application Data"


def test_configuration_folders_do_not_fall_to_unknown():
    tree = [
        mkdir(f"{ROOT}/config"),
        mkfile(f"{ROOT}/config/settings.json", 80_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert len(entities) == 1
    assert entities[0].entity_type == "dev_artifacts"
    assert entities[0].category == "Dev Artifacts"


def test_meaningful_small_known_findings_still_surface():
    tree = [
        mkdir(f"{ROOT}/logs"),
        mkfile(f"{ROOT}/logs/error.log", 12_000),
    ]

    entities = detect_entities(tree, ROOT)

    assert any(e.entity_type == "log_folder" for e in entities)


def test_known_directory_types_detected():
    by_type = {e.entity_type for e in detect_entities(_golden_tree(), ROOT)}
    assert "node_modules" in by_type
    assert "photo_collection" in by_type
    assert "cache_folder" in by_type
    assert "log_folder" in by_type


def test_categories_and_risk():
    by_type = {e.entity_type: e for e in detect_entities(_golden_tree(), ROOT)}
    assert by_type["node_modules"].category == "Dev Artifacts"
    assert by_type["node_modules"].risk == "Safe"
    assert by_type["cache_folder"].category == "Cache & Temp"
    assert by_type["cache_folder"].risk == "Safe"
    assert by_type["log_folder"].category == "System Logs"
    assert by_type["photo_collection"].category == "Images"


def test_containment_rule_no_loose_files():
    """Every file in the tree must be claimed by some entity."""
    tree = _golden_tree()
    entities = detect_entities(tree, ROOT)
    total_files = sum(1 for f in tree if not f.is_dir)
    grouped = sum(e.file_count for e in entities)
    assert grouped == total_files, (
        f"{total_files - grouped} files were left ungrouped"
    )


def test_loose_files_in_subfolder_are_bucketed():
    """A folder of uncategorizable files still becomes an entity."""
    tree = _golden_tree()
    tree += [
        mkdir(f"{ROOT}/misc"),
        mkfile(f"{ROOT}/misc/thing.xyz"),
        mkfile(f"{ROOT}/misc/other.qqq"),
    ]
    entities = detect_entities(tree, ROOT)
    total_files = sum(1 for f in tree if not f.is_dir)
    grouped = sum(e.file_count for e in entities)
    assert grouped == total_files


def test_root_level_bucket_does_not_absorb_the_drive():
    """A Pass-8 loose-file bucket created at the scan root (e.g. 'Loose AI
    model files', an ai_models absorber type) must not absorb every other
    entity on the drive — the bug behind a C: scan showing only 4 categories.
    """
    tree = [
        mkdir(f"{ROOT}/Vacation"),
        mkfile(f"{ROOT}/Vacation/a.jpg"),
        mkfile(f"{ROOT}/Vacation/b.jpg"),
        mkfile(f"{ROOT}/Vacation/c.jpg"),
        mkdir(f"{ROOT}/node_modules"),
        mkfile(f"{ROOT}/node_modules/x.js"),
        # a loose model file at the root -> Pass 8 'ai_models' bucket at target_root
        mkfile(f"{ROOT}/big.safetensors", 9_000_000),
    ]
    types = {e.entity_type for e in detect_entities(tree, ROOT)}
    assert "photo_collection" in types, "sibling entity absorbed by root bucket"
    assert "node_modules" in types
    assert "ai_models" in types, "the loose-model bucket should still appear"


def test_confidence_scores_are_graded():
    """Each pass stamps a confidence reflecting its signal strength:
    strong for known structure, weak for the unknown-folder fallback."""
    by_type = {e.entity_type: e
               for e in detect_entities(rich_tree("T:/snap"), "T:/snap")}
    assert by_type["application"].confidence_score == 0.9     # ffmpeg monolith
    assert by_type["node_modules"].confidence_score == 0.85   # known dir name
    assert by_type["game"].confidence_score == 0.9            # platform layout
    assert by_type["photo_collection"].confidence_score == 0.6  # content heuristic

    unknown_tree = [
        mkdir(f"{ROOT}/mystery"),
        mkfile(f"{ROOT}/mystery/blob.one", 80_000),
        mkfile(f"{ROOT}/mystery/blob.two", 90_000),
    ]
    unknown = {
        e.entity_type: e for e in detect_entities(unknown_tree, ROOT)
    }["unknown_folder"]
    assert unknown.confidence_score == 0.2  # weakest signal
    # the unknown sweep must never out-rank a structural detection
    assert unknown.confidence_score < by_type["node_modules"].confidence_score


# ── content one level down ────────────────────────────────────────


def test_a_folder_whose_content_sits_in_subfolders_is_classified():
    """Classification only ever looked at a folder's DIRECT files, so a folder
    holding nothing but subfolders was handed an empty list and fell through
    to Unknown. That is how people organise almost everything — Videos/2024/,
    Music/Artist/Album/, one subfolder per survey flight — and on a real scan
    it was 83 rows and 420 GB, the largest category by size, all unlabelled.
    """
    import os
    from app.models.finding import Finding
    from app.services.entity_detector import detect_entities
    from app.models.smart_entity import _CATEGORY_BY_TYPE

    findings = []

    def add(path, is_dir, ext="", size=0):
        findings.append(Finding(
            path=path, name=os.path.basename(path) or path, is_dir=is_dir,
            size_bytes=size, extension=ext, modified=1.0, accessed=1.0,
            parent=os.path.dirname(path)))

    # A photo library with NO files at its own level.
    add(r"D:\Trip", True)
    for day in ("Day1", "Day2"):
        add(rf"D:\Trip\{day}", True)
        for i in range(40):
            add(rf"D:\Trip\{day}\IMG_{i:04}.jpg", False, ".jpg", 5 * 1024 ** 2)

    entities = detect_entities(findings, "D:/")
    assert entities, "nothing detected at all"
    categories = {_CATEGORY_BY_TYPE.get(e.entity_type, "Unknown") for e in entities}
    assert categories != {"Unknown"}, (
        f"a folder of photos one level down stayed Unknown: "
        f"{[(e.entity_type, e.path) for e in entities]}")
    assert "Images" in categories


def test_direct_files_still_win_over_the_subfolder_sample():
    """The recursive look is a fallback, not an override — a folder with its
    own content must still be classified on that content."""
    import os
    from app.models.finding import Finding
    from app.services.entity_detector import _classify_by_content

    def f(name, ext):
        return Finding(path=rf"D:\x\{name}", name=name, is_dir=False,
                       size_bytes=1024, extension=ext, modified=1.0,
                       accessed=1.0, parent=r"D:\x")

    direct = [f(f"a{i}.jpg", ".jpg") for i in range(10)]
    assert _classify_by_content(direct) == "photo_collection"
