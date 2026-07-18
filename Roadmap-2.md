# Vigil — Roadmap 2

Working plan. Not meant to be done in one sitting — phases are ordered so the
riskiest correctness work lands before polish, and language work lands last.

**Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[?]` needs a
product decision before starting · `[!]` verified bug with evidence

Update the checkbox and add a short `→ note` when something lands.

---

## Phase 1 — Data safety & correctness (do first)

These are bugs where the UI can mislead someone into deleting the wrong thing.

- [!] **Loose media / documents show the drive root as their path.**
  Reproduced on this machine: scanning `C:\Users\Nazar\Desktop` produced
  `Loose documents` (15 files) and `Loose media files` (2 files) whose `path`
  is `C:/`. A user reviewing them sees "drive root junk" when the files are
  actually on the Desktop. Loose-file buckets must carry their real parent
  folder, and the UI must show it.
  → Highest priority item on this roadmap.

- [ ] **Rule 1 — App boundaries.** A database/image/video/logo inside a
  recognised application's install folder is part of that app, never loose
  media. *Partly exists* as the Containment Rule; needs an explicit test that
  media inside an app folder is never emitted as a standalone media entity.

- [ ] **Rule 2 — System protection.** `C:\Windows\SystemApps\...` and similar
  must stay protected; OS app assets must never appear as "deletable images".
  *Partly exists* (`_is_protected_path`); needs the same explicit test.

- [ ] **Downloads folder.** Treat strictly as a collection of standalone files;
  never parse as a unified application directory.

- [ ] **User directories (Videos / Documents / Images).** Show individual files
  cleanly; group sub-folders as app-specific media instead of breaking the
  layout.

## Phase 2 — Quick wins

- [ ] **History limit 5 → 10** for both Cleanup and Analyze sessions.
  → One-line each: `MAX_ANALYZE_HISTORY` / `MAX_CLEANUP_HISTORY` in
  `app/state/session_store.py`.

- [ ] **AI off by default for bulk scanning.** Set `ai_findings_enabled` to
  `False` so a scan never auto-queues every entity; keep global AI as an
  opt-in for long/overnight runs.
  → The per-item **"Ask AI" button already exists** and now shows an in-place
  "Asking AI…" state, so only the default flip + wording is left.

- [ ] **Deep Uninstall** — fix failures on directories with no uninstaller
  executable (e.g. `C:\Program Files (x86)\Microsoft`). Should degrade to a
  clear "no uninstaller found — review manually" instead of failing.

- [ ] **Analyze screen hover** — broken hover effect when navigating under
  categories.

## Phase 3 — Categorization quality (highest user value)

- [ ] **Reduce "Unknown".** Measured baseline from a full C:/ scan:
  **130 entities / 87 GB — 19% of the disk, the second-largest category.**
  This is the most valuable classification work and it is measurable: re-run
  the C:/ scan and compare the Unknown total.

- [ ] **Static path rules (rules + AI hybrid).** Local JSON of known
  application paths (Chrome, Edge, VS Code, …), e.g. recognising
  `C:\Program Files (x86)\Microsoft` as built-in Windows/Edge components.
  → `app/services/classification_rules.json` already exists and ships in the
  build, so this has a home. Keep it *complementary* to registry detection,
  which stays authoritative — a hardcoded list drifts and can't be complete.

- [ ] **Orphaned app data detection.** For config folders like
  `C:\Users\<user>\.something`: if the parent app is installed → "Active app
  configuration files"; if missing → "Orphaned files (safe to delete if the
  app was uninstalled)".
  → Reuses the installed-check already built for game saves
  (`Installed: True/False`), so this is mostly wiring, not new logic.

## Phase 4 — AI backends

- [ ] **LM Studio and llama.cpp support.** Both expose OpenAI/Ollama-compatible
  HTTP APIs, so this is mostly endpoint + response-shape handling on top of
  the Local/Server toggle that already exists.

- [?] **Remote API servers — needs a product decision.**
  The client deliberately refuses anything that is not loopback/LAN
  (`is_local_endpoint`), and the About screen promises *"No cloud processing.
  Decisions stay on your machine."* A home-server or Mini-PC on the **LAN** is
  already supported by the Server toggle. Allowing a genuinely **remote/cloud**
  endpoint would break that promise, so it should be an explicit, consented
  choice with clear UI — not a quiet feature flag. Decide the scope before
  building.

## Phase 5 — Languages (last, by agreement)

- [ ] **UI + AI prompts in English, Ukrainian, Spanish, German, French.**
  Ukrainian already works end to end.
  → Not just translation: every language needs **layout QA**. Ukrainian
  already overflowed three controls (sidebar nav, scan button, mode combo) and
  had to be shortened. Before adding languages, add an automated check that
  measures rendered string width against each control's width, so overflow is
  caught by tests instead of by eye.

## Phase 6 — Cross-platform (recommend deferring)

- [?] **Linux support — bigger than it looks; suggest not doing it yet.**
  This is not "add Linux paths". Windows-specific APIs span 8 modules:
  `winreg` (installed apps, startup entries), `win32com` (shortcuts),
  `shell32` (Recycle Bin), `%APPDATA%` layout, and Task Scheduler. Startup
  detection has no Linux equivalent at all (systemd user units / autostart
  `.desktop` files are a separate implementation).
  → Recommendation: finish the Windows product first. If Linux stays a goal,
  the prerequisite is extracting a platform layer (paths, trash, installed-apps,
  startup) behind interfaces — worth doing as its own project, not a bullet.

---

## Done

Landed in `1.0.0-beta.2`:

- [x] Per-item **Ask AI** button with in-place feedback.
- [x] **Local/Server AI endpoint** toggle that remembers the server address.
- [x] **Game-save recognition** — clean names, install status, created date.
- [x] **Scan all fixed drives** (multi-root scan + `ALL` chip in the target field).
- [x] **History**: freed GB/files shown per analyze session.
- [x] Registered install roots stay one entity (`C:\Qt` no longer fragments).
- [x] Sorting/quantity fixes: "Oldest first", one canonical risk order,
      size-unit boundaries, mtime preserved across resume.
- [x] Stability: shutdown no longer destroys a running scan thread; session
      writes are atomic; ~18% less scan memory.
