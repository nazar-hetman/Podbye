# Vigil Semantic Pipeline Architecture

## Overview

The Vigil scanning system transforms raw filesystem enumeration into **semantic storage intelligence**. Instead of dumping millions of files into the UI, Vigil detects and presents **meaningful entities** organized in a clear hierarchy.

## Core Philosophy

**Golden Rule: Every scanned item MUST belong to a semantic entity.**

```
OLD: 500,000 loose files
NEW: 45 Semantic Entities
     ├── Applications (Installed: 34, Portable: 12)
     ├── Games (Steam: 8, Epic: 3)
     ├── Media (Images: 12 GB, Videos: 140 GB, Projects: 35 GB)
     ├── Development (Projects: 15, Dependencies: 8)
     ├── AI / ML (Models: 90 GB, Checkpoints: 220 GB)
     ├── Archives (Backups: 180 GB, Archives: 220 GB)
     └── System & Cache (45 GB)
```

**Hierarchy Principles:**
1. **Applications** → Installed / Portable / Installers
2. **Games** → Steam / Epic / GOG / Ubisoft / Battle.net / Xbox
3. **Media** → Images / Videos / Audio / Creative Projects
4. **Development** → Projects / Build Artifacts / Dependencies
5. **AI / ML** → Models / Checkpoints / Datasets / Cache
6. **Archives & Backups** → ZIP/RAR / ISO / Backup Folders
7. **Documents** → PDFs / Work Documents / Exports
8. **Cache & Temp** → Browser Cache / Shader Cache / Windows Temp
9. **Protected Data** → Game Saves / Databases / User Profiles
10. **System** → Protected paths (read-only)
11. **Unknown** → Auto-grouped (never loose files)

**Key Requirements:**
- **NO loose files remain invisible** — Everything is grouped
- **AI analyzes entities, not files** — Context-rich explanations
- **Findings shows entities immediately** — After grouping completes
- **Windows registry integration** — Verifies installed applications
- **Platform-aware game detection** — Steam, Epic, GOG, etc.

---

## Pipeline Stages

### Stage 1: Streaming Filesystem Enumeration

**File:** `app/services/scanner.py` (`ScanWorker`)

- Enumerates files in batches of 500
- Progress updates every 2,000 items
- Logs milestones every 25,000 items
- **Does not store every file** — streams to entity detector

**Key improvements:**
- Directories only recorded at depth ≤ 1 (top-level)
- No `os.scandir` size calculation during walk
- Batched UI updates (not per-file)

---

### Stage 2: Semantic Entity Detection (Two-Phase, Container-First)

**File:** `app/services/entity_detector.py` (`detect_entities`)

Transforms raw file findings into **semantic storage entities** using a two-phase container-first algorithm.

**Core Rule:** Every scanned item MUST belong to:
1. Known semantic entity
2. Parent folder entity  
3. Content bucket
4. Unknown grouped entity

**NO loose files remain invisible in Findings.**

#### The Containment Rule (non-negotiable)

Any file inside a known container belongs to that container unconditionally:

```
if any(file_path.startswith(root_path) for root_path in entity_roots):
    file belongs to that root, FULL STOP.
    No content classification.
    No "photo_collection inside QGIS".
    No exceptions.
```

#### Phase 1 — Discovery (shallow, fast)

Runs **before any content classification**. Walks top 2–4 levels of the scan target and claims every known monolith root using name-pattern matching.

**Known Monolith List** (`_KNOWN_MONOLITH_PATTERNS`):

| Pattern | Matches | Entity Type |
|---------|---------|-------------|
| `texlive` | TeX Live distributions | dev_artifacts |
| `miktex` | MiKTeX | dev_artifacts |
| `matlab` | MATLAB | application |
| `qgis ` | QGIS 3.x, QGIS 4.x (trailing space = strict prefix) | application |
| `paraview ` | ParaView 5.x | application |
| `cygwin`, `msys64`, `msys2` | UNIX layers on Windows | dev_artifacts |
| `vcpkg` | vcpkg package manager | dev_artifacts |
| `androidsdk`, `android-sdk` | Android SDK | dev_artifacts |
| `javasoft` | Java runtime trees | dev_artifacts |
| `jetbrains` | JetBrains IDE family root | application |
| `blender foundation` | Blender Foundation | application |
| `krita`, `gimp`, `inkscape` | Creative tools | application |
| `unity hub` | Unity Hub | application |
| `python3`, `python 3` | Python versioned installs | dev_artifacts |
| `r-` | R statistical computing (R-4.3.0 etc.) | dev_artifacts |
| `microsoft visual studio` | Visual Studio | application |
| `microsoft sdks`, `windows kits` | MS developer toolchains | dev_artifacts |
| `ffmpeg`, `imagemagick`, `wireshark` | Media/network tools | application |
| `postgresql`, `mysql`, `mongodb`, `redis` | Database servers | database |
| `docker`, `podman` | Container platforms | application |

The monolith list is overridable via `settings/scan.monolith_patterns` (list of pattern strings).

Logs: `[smart] phase 1: discovery — found N entity roots · Xms`

#### Phase 2 — Assignment (streaming, deep)

After Phase 1, all existing detection passes run on the **unclaimed** pool only. Files inside Phase 1 roots are already claimed and skipped by all passes below.

| Pass | Detection Target | Method |
|------|-------------------|--------|
| **0** | Vendor update caches | NVIDIA update/cache staging merged into one root |
| **0b** | AppData/Local/Packages | Each UWP package folder → named `application_data` entity (`_humanize_package_name`: "SpotifyAB.SpotifyMusic_…" → "Spotify (app data)") |
| **pre** | Heterogeneous user roots | Documents/Downloads/etc. with diverse subfolders are claimed as pass-through nodes (`_pass_explode_user_roots`) so they break into per-subfolder entities instead of one blob; loose files become "straggler" buckets |
| **1** | Known directories | `node_modules`, `.venv`, `cache`, `models`, etc. |
| **2** | Installed applications | Windows registry lookup |
| **2b** | Portable applications | Marker files (`.exe`, `steam.exe`, etc.) |
| **3** | Browser profiles | Path keywords (chrome, firefox, edge) |
| **3b** | Installed games | Platform detection (Steam, Epic, GOG, etc.) |
| **4** | Cache & temp folders | Keywords (`cache`, `tmp`, `shadercache`) |
| **5** | Protected system paths | System directory names |
| **6** | Content-homogeneous folders | Extension analysis (images, videos, etc.) — **untracked pool only** |
| **7** | Recursive sub-folder grouping | Remaining unclaimed top-level directories |
| **8** | Large loose files | Collect remaining items as entity |

Logs: `[smart] phase 2: assignment — claimed X files, Y untracked`

#### Semantic Categories & Hierarchy

```
Applications
├── Installed (verified via Windows registry)
│   └── OBS Studio, Discord, Steam, etc.
├── Portable (has .exe structure, not in registry)
│   └── Standalone tools, unpacked utilities
└── Installers
    └── setup.exe, .msi, install packages

Games (platform-verified)
├── Steam Games
├── Epic Games
├── GOG Games
├── Ubisoft
├── Battle.net
└── Xbox Games

Media
├── Images (.jpg, .png, .raw, .psd)
├── Videos (.mp4, .mkv, .mov)
├── Audio (.mp3, .flac, .wav)
└── Creative Projects (.prproj, .blend, .aep)

Development
├── Projects (has .git, package.json, etc.)
├── Build Artifacts (dist/, build/, target/)
└── Dependencies (node_modules/, venv/)

AI / ML
├── Models (.gguf, .safetensors, .pt)
├── Checkpoints (.ckpt)
├── Datasets
└── Cache (ComfyUI, Ollama, HuggingFace)

Archives & Backups
├── ZIP/RAR archives
├── ISO images
└── Backup folders (backup/, old/, archive/)

Documents
├── PDFs, Office documents
├── Exports, Downloads
└── Text, Markdown, CSV

Cache & Temp
├── Browser cache
├── Shader cache (games)
├── Windows temp
└── Log files

Protected Data
├── Game saves
├── Databases (.sqlite, .db)
└── User profiles

System
└── Protected paths (read-only)

Unknown (fallback)
└── Auto-grouped, never loose files
```

#### Entity Types

**Applications:**
- `application` — Installed app (registry verified)
- `portable_app` — Portable/unpacked software
- `installer` / `installer_group` — Setup packages

**Games:**
- `game` — Platform-verified game installation
- `game_cache` — Shader/cache data
- `game_saves` — Save data / profiles. In post-processing, `_enrich_game_saves()`
  resolves the **owning game** from the save path and cross-references it
  against games/apps detected in the same scan + the Windows registry, so the
  entity is named e.g. "Skyrim Saves — game still installed" or
  "Game Saves — Witcher 3, Portal 2 (+2)" instead of a bare "Saves".

**Development:**
- `dev_project` — Source code project
- `dev_artifacts` — Build outputs
- `venv`, `node_modules` — Dependencies
- `build_folder` — Compiled output

**AI / ML:**
- `ai_models` — Model files
- `ai_cache` — Runtime cache

**Media:**
- `photo_collection` — Images/photos
- `video_collection` — Videos/movies
- `audio_collection` — Audio/music
- `creative_project` — Creative project files
- `media_collection` — Mixed media

**Storage:**
- `archive_group` — Archives (ZIP, RAR, ISO)
- `backup_group` — Backup files
- `dataset` — Data files
- `document_folder` — Documents

**Cache & Temp:**
- `cache_folder`, `temp_folder`
- `shader_cache` — GPU shaders
- `log_folder` — Log files

**Protected:**
- `database` — Database files
- `game_saves` — Save data
- `protected_system` — System paths

**Fallback:**
- `mixed_folder` — Mixed content
- `unknown_folder` — Unclassified
- `loose_files` — Loose file bucket

**Duplicates (post-scan phase):**
- `duplicate_group` — Two or more files with identical content (SHA-256 / BLAKE3 hash match)
  - `dup_reclaimable` bytes = total size minus the newest copy (keeper)
  - Detected by `DuplicateDetector(QThread)` in `app/services/duplicate_detector.py`
  - Only files ≥ `scan/dedup_threshold_mb` (default 10 MB) are hashed
  - Emitted as live entities after entity detection; shown in "Duplicates" category

---

### Age-Based Heuristic Scoring

Applied to eligible entity types (dev_artifacts, installer_group, archive_group, build_folder, venv, node_modules, temp_folder, cache_folder, log_folder, ai_cache) during the final post-processing step of `detect_entities()`.

**Formula:**
- `age_boost = 0.0` (default — entity is recent or ineligible)
- `age_boost = 0.2` — entity last modified 2–5 years ago
- `age_boost = 0.4` — entity last modified 5+ years ago

**Effect on reclaimable_bytes:**
- Safe entities: 100% reclaimable regardless of age (already maximal)
- Review entities + `age_boost > 0`: reclaimable = `size_bytes × age_boost`
  (e.g., a 5 GB Review archive not touched in 5 years → 2 GB counted as reclaimable)

**Ineligible types** (never boosted): photo_collection, video_collection, audio_collection, document_folder, game, game_saves, browser_profile, creative_project — user data is never penalized for being old.

---

### Stage 3: Install Verification

**File:** `app/services/installed_software.py` (`InstalledSoftwareValidator`)

**Validates:**
- Windows Registry (Uninstall keys)
- Steam library manifests (`appmanifest_*.acf`)
- Epic Games manifests (`.item` files)
- GOG Galaxy database
- Uninstaller presence (`unins000.exe`)

**Result:**
```python
entity.install_verified = True  # Registry found
entity.entity_type = "application_orphaned"  # No install found
entity.risk = "Review"  # Lower risk for orphans
entity.reclaimable_bytes = entity.size_bytes  # Safe to remove
```

---

### Stage 4: Media Hierarchy

**File:** `app/services/media_hierarchy.py` (`MediaHierarchyBuilder`)

**Transforms flat media folders into structured hierarchies:**

```
Pictures (50 GB)
├── Photos (20 GB, 8,500 files)
│   ├── 2023 (12 GB)
│   └── 2024 (8 GB)
├── Screenshots (2 GB, 340 files) ← Safe to clean
├── RAW/ProRAW (15 GB, 420 files)
└── Memes/Downloads (500 MB) ← Safe to clean
```

**Sub-category Detection:**
- Path keywords: `screenshots`, `photos`, `camera`, `downloads`
- Extension analysis: RAW formats, compressed, video
- Risk assignment: Screenshots → Safe, Photos → Review

---

### Stage 5: Smart AI Queue

**File:** `app/services/smart_ai_queue.py` (`SmartAIQueue`)

**Only analyzes entities that need it:**

| Entity Type | AI Action | Reason |
|-------------|-----------|--------|
| `cache_folder` | Skip | Template summary |
| `temp_folder` | Skip | Template summary |
| `application` + `install_verified` | Light AI | Verify understanding |
| `needs_ai_analysis` | Full AI | Ambiguity score > 0.6 |
| `mixed_folder` + >100 files | Consider AI | Unclear purpose |
| `media_collection` + >1000 files | Light AI | Generate summary |

**Templates (no AI needed):**
```python
ENTITY_TEMPLATES = {
    "cache_folder": {
        "summary": "Application cache files that can be safely cleared...",
        "recommendation": "Safe to remove — will be regenerated",
    },
    "venv": {
        "summary": "Python virtual environment...",
        "recommendation": "Safe to remove — can recreate with pip",
    },
}
```

**Priority Calculation:**
- Ambiguity score (0-1) × 100
- Risk boost (Risk=-30, Review=-10, Safe=+20)
- Size boost (10GB+=-20, 1GB+=-10)
- Type adjustment (unknown=-20, cache=0)

---

## Data Model

### `SemanticEntity` (`app/models/semantic_entity.py`)

```python
@dataclass
class SemanticEntity:
    # Identity
    path: str                    # Root folder path
    name: str                    # Display name
    entity_type: str             # Classification key

    # Metrics
    size_bytes: int
    file_count: int
    folder_count: int

    # Classification
    risk: str                    # Protected/Risk/Review/Safe
    confidence: str              # heuristic | verified | ai
    ambiguity_score: float       # 0-1, higher = needs AI

    # Install verification
    install_verified: bool
    install_check_method: str    # registry | file_marker | exe_found
    related_paths: list[str]

    # Hierarchy
    child_entities: list[SemanticEntity]
    parent_entity: SemanticEntity | None
    depth: int

    # AI
    ai_status: str               # none | pending | analyzing | ready | failed
    ai_explanation: str
    ai_recommendation: str

    # Reclaimable space
    reclaimable_bytes: int

    # Content breakdown
    content_types: dict[str, int]  # {extension: count}
    sample_files: list[str]
```

**Key Methods:**
- `needs_ai_analysis` — Property, True if ambiguity > 0.6 or unknown type
- `estimate_reclaimable()` — Calculate safe-to-remove space
- `add_child()` — Build hierarchy
- `mark_ai_complete()` — Finalize AI results

---

## Usage Example

```python
from app.services import StreamingEntityDetector
from app.services import InstalledSoftwareValidator
from app.services import SmartAIQueue

# Stage 1 & 2: Streaming detection
detector = StreamingEntityDetector("C:\\Users")
detector.on_new_entity(lambda e: ui.show_entity_discovered(e))

for finding in scan_stream:
    entity = detector.process_finding(finding)

entities = detector.finalize()

# Stage 3: Install verification
validator = InstalledSoftwareValidator()
validator.batch_validate([e for e in entities if e.entity_type in ("application", "game")])

# Stage 4: Media hierarchy (for media folders)
media_entities = [e for e in entities if e.entity_type.startswith("media")]
for media in media_entities:
    hierarchy = MediaHierarchyBuilder()
    # ... add files ...
    structured = hierarchy.build_entity(media.path, media.name)

# Stage 5: Smart AI
ai_queue = SmartAIQueue(ai_explainer, settings_store)
for entity in entities:
    ai_queue.add_entity(entity)

ai_queue.process_queue(max_items=10)
```

---

## UI Integration

### Findings Dashboard — Semantic Storage Map

**File:** `app/screens/findings_dashboard.py`

The Findings experience has been transformed from a "giant raw filesystem table" into a **visual semantic storage dashboard**.

#### View States

```
┌─────────────────────────────────────────────────────────────┐
│  EMPTY STATE                                                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           ◈                                         │   │
│  │     ANALYSIS NOT STARTED                            │   │
│  │  Run an analysis to see a visual breakdown...      │   │
│  │              [ Start Analysis → ]                   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DASHBOARD (Primary View)                                   │
│  ┌─────────────────────────────────────────────────────────┐
│  │  STORAGE MAP                          // 2.4 TB total   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  │ ▣            │  │ ◉            │  │ ▦            │   │
│  │  │ APPLICATIONS │  │ MEDIA        │  │ ARCHIVES     │   │
│  │  │ 35%          │  │ 24%          │  │ 18%          │   │
│  │  │ 856 GB       │  │ 576 GB       │  │ 432 GB       │   │
│  │  │ 12 items     │  │ 45,230 items │  │ 1,203 items  │   │
│  │  │ ↳ 45 GB safe │  │              │  │              │   │
│  │  │ AI: ✓ 10     │  │ AI: ✓ 23     │  │ AI: ◐ 5      │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │
│  └─────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  CATEGORY DETAIL (Drill-down)                               │
│  ← Back to Storage Map   MEDIA         // 45 entities       │
│  AI: 23 analyzed · 2 pending · 0 failed                     │
│  Filter: [_______]  [Safe ✓] [Review ✓] [Risk ✓]           │
│  ┌─────────────────────────────────────────────────────────┐
│  │  ☐  ▸  Photos Library    245 GB   8,500   Review  ✓   │
│  │  ☐  ▸  Videos Folder     180 GB   340     Review  ✓   │
│  │  ☐  ▸  RAW Files         89 GB    420     Safe    ✓   │
│  │  ☐  ▸  Screenshots       12 GB    2,100   Safe    —   │
│  └─────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

#### Components

**StorageBlock** (`StorageBlock` class):
- **Proportional sizing**: Height scales with storage percentage (70px - 280px)
- **Adaptive layout modes**: 
  - `detailed` (≥15%): Full info — category, %, size, items, reclaimable, AI status
  - `compact` (1-15%): Essential — category, %, size
  - `tiny` (<1%): Minimal — category, % only
- **Dynamic contrast**: Automatic text color (black/white) based on background luminance
- **Solid color backgrounds**: Category colors with 2px border
- **Hover effect**: Border changes to text color on hover
- Displays: category name, percentage, size, item count, reclaimable space (>100 MB), AI status

**StorageMapWidget** (`StorageMapWidget` class):
- **Treemap layout**: Proportional stock-market heatmap style
- **Responsive grid**: 1-4 columns based on window width
- **Smart grouping**: Categories >20 grouped into "Other" block
- **Debounced resize**: 50ms delay prevents layout thrashing
- **Performance caching**: Skips re-render if data hash unchanged
- Sorted by size descending (largest first)
- Scrollable with tight 4px spacing (stock heatmap aesthetic)

**CategoryDetailView** (`CategoryDetailView` class):
- Back navigation to dashboard
- AI summary bar (analyzed/pending/failed counts)
- Filter bar with search and risk toggles
- Table view with: checkbox, expand, name, size, items, risk badge, AI status
- Sort options: largest/smallest, AI status, risk, safe cleanup, reclaimable

**EmptyStateWidget** (`EmptyStateWidget` class):
- Centered illustration (◈ icon)
- Clear call-to-action
- "Start Analysis" button → emits `navigate_to_analyze` signal → switches to Analyze screen

#### Navigation Flow

```
┌─────────┐    Start Scan     ┌───────────┐
│  Empty  │ ────────────────→ │ Dashboard │
│  State  │                   │ (Blocks)  │
└─────────┘                   └─────┬─────┘
      ↑                           │
      │                           │ Click Block
      │                           ↓
      │                    ┌─────────────┐
      │                    │  Category   │
      └────────────────────│   Detail    │
           Back Button     │  (Table)    │
                          └─────────────┘
```

#### Performance Optimizations

**Rendering Performance:**
1. **Lazy Loading**: Table rows only rendered when drilling into category
2. **No Giant Lists**: Dashboard shows ~10-20 category blocks, not 500k files
3. **Debounced Refresh**: 100ms debounce on UI updates prevents freeze during scan
4. **Data Hash Caching**: Skips re-render if category data unchanged
5. **Deferred Resize**: 50ms delay on window resize prevents layout thrashing
6. **Block Limit**: Maximum 20 blocks (smallest grouped into "Other")

**Layout Performance:**
- Proportional treemap (stock heatmap style) — height scales with percentage
- Responsive columns: 1-4 columns based on window width
- Tight 4px spacing between blocks
- Widget visibility toggled before deletion to prevent flicker

**Navigation Performance:**
- `navigate_to_analyze` signal connected to `_navigate("Analyze")`
- No blocking operations on UI thread
- All heavy work done via `QTimer.singleShot`

#### Scan Progress Behavior

**Weighted Progress (Analyze Screen):**
- **Filesystem scan**: 0% → 30% (logarithmic scale based on file count)
  - 0-99 files: linear 0-1%
  - 100+ files: logarithmic growth (1k≈15%, 10k≈22%, 100k≈27%, 1M≈30%)
- **Entity grouping**: 30% → 100% (real progress from entity detection)
  - Progress updates from actual work: `processed / estimated_total`
  - Phase-by-phase updates with entity counts
  - Chip shows: "{processed}/{total} · {current_phase}"
- **AI classification**: Separate AI progress bar (does not block scan completion)

**Status Badge Flow:**
1. **IDLE** → **SCANNING** (filesystem phase)
2. **SCANNING** → **GROUPING** (entity detection phase)
3. **GROUPING** → **AI CLASSIFYING** (AI queue running)
4. **AI CLASSIFYING** → **COMPLETE** (all phases done)

**Start/Stop Button State:**
- **Before scan**: "Start scan" (green, enabled when target selected)
- **During any phase**: "Stop scan" (red, stops filesystem + entity + AI)
- **After complete**: "Start scan" (green, ready for new scan)
- **User stops**: Immediately resets to "Start scan"

**Entity Detection Real Progress:**
- `detect_entities()` accepts `progress_fn(phase, processed, total)` callback
- Emits progress after each pass: indexing → known_dirs → applications → browser_profiles → cache_folders → protected_paths → content_grouping → folder_grouping → complete
- Long-running passes (content_grouping) report every 50 entities
- Estimated total based on file count heuristic (~1 entity per 100 items)
- Streaming log output: `[smart] pass N: {description}...` + `[smart]   → found X entities`

**AI Queue Timing (Fixed):**
- AI classification starts ONLY after entity grouping is complete (not streaming)
- AI queue is built from final SemanticEntity objects
- UI clearly shows "AI starting..." when entities are ready
- No overlap - grouping completes before AI begins

**Pipeline State Machine:**
States: `idle` → `scanning_filesystem` → `grouping_entities` → `ai_classifying` → `complete`
- `_pipeline_state` tracks current phase
- `_filesystem_complete`, `_grouping_complete`, `_ai_complete` boolean flags
- Elapsed timer runs until ALL phases complete (not just filesystem)
- Button remains "Stop scan" during all active phases

**Finalization Fail-Safe:**
- If finalizing takes >5s: log warning "finalizing taking longer than expected..."
- If finalizing takes >15s: log ERROR and show visible warning
- Never leave UI in infinite "finalizing" state
- Entity Detection chip marked as "done" (not "active") when complete

**Scan Progress Label:**
- Shows PERCENTAGE ONLY (no extra text)
- During scan: estimated percentage 0-30%
- During grouping: weighted percentage 31-100%
- After grouping: "100%"

**Findings Readiness Rules:**

Findings switches from loading to Storage Map when:
1. **Entity grouping is complete** (phase = "ai_classification" or "complete")
2. **semantic_entities count > 0**
3. **Dashboard data is available**

**KEY:** Findings does NOT wait for AI classification to finish. AI may continue in background while Storage Map is already visible.

Transition logic:
```
if phase in ("filesystem", "entity_detection"):
    show_loading()
elif has_entities or phase in ("ai_classification", "complete"):
    show_dashboard()  # AI may still be running
```

**Findings Loading States:**
- **No scan**: "Analysis has not been started yet"
- **In progress**: "Storage map is being prepared" with real-time progress:
  - "Processing folders: 240/1030 (23%) · Entities created: 9"
- **Stopped**: "Analysis stopped before storage map was ready" with resume button
- **Complete**: Storage Map with category blocks
- **Error**: "Storage map could not be rendered: {error}" with red text
- **No entities**: "No semantic entities were created" (not infinite loading)

**Fallback:** When user opens Findings screen, if entities are already ready:
```
[findings] set_scan_state: entities already ready (9), showing dashboard
```

**Operator Feed Diagnostic Logs:**

Entity detection lifecycle:
```
[smart] entity detection started · candidate folders: 1,030
[smart] pass 1: detecting known directories...
[smart]   → processed 156/1,030 folders · created 2 entities
[smart] pass 2: detecting applications...
[smart]   → scanned 5,234 files · created 4 app entities · total: 6
...
[smart] note: 12,345 items not grouped into any entity
[smart] semantic grouping complete · 9 entities created · 45,231 files grouped in 2.34s
[smart] entities ready for dashboard
[smart] finalizing semantic entities...
[smart] semantic entities stored · 9
[smart] emitting entities_ready...
```

Findings dashboard:
```
[findings] entities_ready received · 9 entities
[findings] refreshing dashboard...
[findings] switching from loading to dashboard
[storage] Storage Map build started · 9 categories
[storage] creating 9 blocks immediately
[storage] Storage Map build complete · 45.2ms
[findings] dashboard now visible
[storage] dashboard ready · 9 blocks displayed
```

AI classification (detailed logging):
```
[ai] preparing queue from semantic entities · 9 total
[ai] explainable entities · 7
[ai] started · 7 entities
[ai] active model: llama3.2
[ai] language: english
[ai] completed Google Chrome in 28.1s
[ai] completed OBS Studio in 15.3s
[ai] AI classification finished
[smart] pipeline complete
```

**AI Skip Reasons (visible in operator feed):**
- "AI explainer not configured" - AI component not initialized
- "settings not loaded" - SettingsStore not available
- "disabled in settings" - AI disabled by user
- "no explainable entities" - All entities are Protected/Safe
- "queue already running" - AI already processing
- "no model selected" - Ollama model not configured

### Findings Widget: FindingDetail

**File:** `app/screens/findings.py` (detail widget only — not a screen)

`FindingDetail(QFrame)` renders the expanded detail panel for a single finding or entity:
- WHY FLAGGED + RECOMMENDATION two-column layout
- Metadata grid (category, size, items, risk, confidence)
- Source rule display
- AI explanation with scroll area
- Action buttons (Open in Explorer, Copy path, Re-run AI)

`FindingsScreen` (the legacy flat table) has been removed. All navigation, selection, and cleanup now live in `FindingsDashboard` / `CategoryDetailView`.

---

### Previous Design (Hierarchical Display)

**Note:** The following was the initial hierarchical entity design, now evolved into the full dashboard:

```
■ Media Collection · Photos Library · 45 GB
  ▸ Images (30 GB)
  ▸ Videos (12 GB)
  ▸ RAW Files (3 GB)

  [Expand to see child entities]
  [Clean Screenshots] ← One-click safe cleanup
```

---

---

## Cleanup Pipeline

**Files:** `app/services/cleanup_engine.py`, `app/screens/cleanup_dialog.py`

### Selection → Deletion

1. User selects `SmartEntity` rows in the Findings screen.
2. Each entity has a `path` (the root folder/file it represents) and a `risk` level.
3. Clicking **Clean selected** opens `CleanupConfirmDialog` — a modal QDialog with:
   - Risk breakdown (Protected / Risk / Review / Safe item counts and sizes)
   - Per-item path list for Risk and Review items (scrollable, expand-on-demand for >20)
   - Protected items displayed as excluded with a "will be skipped" note
   - Confirmation phrase input for Risk-level selections ("delete N items")

### Execution

`CleanupWorker(QThread)` processes each path sequentially:

```
for path in paths:
    if _is_protected_for_delete(path):   # double-checked at delete time
        skipped_protected.append(path)
        continue
    size = _get_size(path)
    err = _recycle_one(path)             # SHFileOperationW with FOF_ALLOWUNDO
    if err:
        failed.append(path); errors_by_path[path] = err
    else:
        succeeded.append(path); total_bytes_freed += size
```

`_recycle_one` uses `SHFileOperationW` with flags:
- `FOF_ALLOWUNDO` — item goes to Recycle Bin, fully recoverable by the user
- `FOF_NOCONFIRMATION` + `FOF_SILENT` + `FOF_NOERRORUI` — no shell dialogs

### Post-Cleanup

After `CleanupWorker.finished`:

1. `ScanState.remove_entities_by_path(succeeded_paths)` — removes entities, purges findings, emits `ui_refresh`
2. All connected Findings screens rebuild via the existing throttled `ui_refresh` signal
3. Toast message shown in the selection bar: "✓ N items moved to Recycle Bin · X freed"
4. Cleanup record written to `%APPDATA%/Vigil/sessions/cleanup_{timestamp}.json`

### The Protection Invariant

`ProtectedPathError` is raised (not silently skipped) for permanent-delete calls on protected paths. For Recycle Bin mode, protected paths are silently skipped and counted in `skipped_protected`. This means:

- Protected paths can **never** be permanently deleted — the engine rejects the call at the service layer, not the UI layer.
- Protected paths **can** land in `_show_cleanup_preview` selection — the dialog shows them as "will be skipped" and excludes them from the action count.

---

## Performance Characteristics

| Aspect | Old System | Semantic Pipeline |
|--------|-----------|---------------------|
| Memory (500k files) | ~500MB (all Findings) | ~50MB (entities only) |
| UI Rows | 500,000 | ~500 entities |
| AI Calls | 500 (naive) | 10-50 (smart) |
| Scan Freeze | Common | Eliminated |
| Entity Detection | Post-scan O(n) | Streaming O(1) per file |
| Hierarchy | Flat | 2-3 levels deep |

---

## Files Added/Modified

> ⚠️ **Accuracy note.** This document was written during an earlier design phase
> and describes some modules (`semantic_entity.py`, `streaming_entity_detector.py`,
> `installed_software.py`, `media_hierarchy.py`, `smart_ai_queue.py`) that were
> never built. The shipping implementation lives in **`app/services/entity_detector.py`**
> (the multi-pass `detect_entities` pipeline) and **`app/models/smart_entity.py`**
> (the `SmartEntity` model). Treat the file list below as historical intent, not
> the current layout.

### Aspirational files (NOT in the codebase)

| File | Intended purpose | Reality |
|------|---------|---------|
| `app/models/semantic_entity.py` | Hierarchical entity model | → `app/models/smart_entity.py` |
| `app/services/streaming_entity_detector.py` | Real-time entity detection | → `app/services/entity_detector.py` |
| `app/services/installed_software.py` | Install verification | folded into `entity_detector.py` (registry walk) |
| `app/services/media_hierarchy.py` | Media sub-categorization | content passes in `entity_detector.py` |
| `app/services/smart_ai_queue.py` | Intelligent AI selection | `app/services/ai_explainer.py` |

### Modified Files

| File | Changes |
|------|---------|
| `app/services/scanner.py` | Reduced file storage, batching |
| `app/state/scan_state.py` | Streaming entity support |
| `app/models/__init__.py` | Export SemanticEntity |
| `app/services/__init__.py` | Export new services |

---

## Migration Path

**From Smart Scan:**
```python
# Old
detect_entities(findings, target)  # Batch after scan

# New
detector = StreamingEntityDetector(target)
for finding in streaming_findings:
    detector.process_finding(finding)  # Real-time
entities = detector.finalize()
```

**Backward Compatibility:**
- `SmartEntity` → `SemanticEntity` (different models)
- Both supported during transition
- UI adapts to entity type available

---

## Cloud-Sync Safety (✅ Implemented 2026-05-15)

After all detection passes complete, `detect_entities()` calls `detect_cloud_roots()` from `app/services/cloud_detector.py` to locate provider roots under `%USERPROFILE%`:

| Provider | Patterns |
|----------|----------|
| OneDrive | `OneDrive`, `OneDrive - *` (Business) |
| Dropbox | `Dropbox`, `Dropbox (*)` |
| Google Drive | `Google Drive`, `GoogleDrive` |
| iCloud | `iCloudDrive` |
| Box | `Box` |
| pCloud | `pCloud Drive` |
| MEGA | `Mega` |

**`SmartEntity.cloud_sync_provider: str`** — set to provider name or `""`.

**Risk enforcement:** cloud entities are forced to minimum `Review`; `Safe` is overridden. This is applied both at entity construction time and in the cloud annotation pass.

**UI signals:**
- `☁` overlay on risk badge in the findings table (blue tint, `#7ab8d4`)
- Cleanup modal shows a separate cloud-warning frame with provider name, item count, and a mandatory acknowledgment checkbox before confirm is enabled

## Future Enhancements

1. **User Pattern Learning** — "User always keeps RAW files" → adjust recommendations
2. **Cross-drive Analysis** — "Same game installed on C: and D:"

---

## Design Principles

1. **Meaning over files** — Show what the storage represents
2. **Hierarchy over flat lists** — Navigate by meaning, not scroll
3. **Intelligence over volume** — AI only where helpful
4. **Safety over aggression** — Verify before suggesting removal
5. **Streaming over batch** — UI never freezes
