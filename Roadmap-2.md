# Vigil — Roadmap 2

Working plan. Not meant to be done in one sitting — phases are ordered so the
riskiest correctness work lands before polish, and language work lands last.

**Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[?]` needs a
product decision before starting · `[!]` verified bug with evidence

Update the checkbox and add a short `→ note` when something lands.

---

## Phase 1 — Data safety & correctness ✅ COMPLETE

These are bugs where the UI can mislead someone into deleting the wrong thing.

- [x] **Loose media / documents showed the drive root as their path.** *(fixed)*
  Reproduced on this machine: scanning `C:\Users\Nazar\Desktop` produced
  `Loose documents` (15 files) and `Loose media files` (2 files) whose `path`
  was `C:/`.
  → Cause: only the *misc* bucket split by parent folder; every other bucket
  (documents, media, archives, databases, models, logs) emitted one entity
  rooted at the scan target. Beyond the wrong label this **merged unrelated
  folders into a single deletable bucket**, so one click could recycle files
  from all over the disk.
  → Fix: every loose bucket now splits by parent directory and is named for it
  ("Loose documents in Desktop"). Verified on a full C:/ scan — 1.6M files, no
  entity misreports its location, and no entity lists a file outside its own
  folder. Entity count went **down** (904 → 741), so the split did not add
  noise. Files genuinely sitting in `C:\` still use the root, correctly.

- [x] **Rule 1 — App boundaries.** *(verified — already held; now pinned)*
  Probed against the real `C:\Program Files` and `Program Files (x86)`
  (290k findings): no media/database/logo inside an app install folder was
  emitted as a standalone media entity — the Containment Rule was doing its
  job. Locked in with tests so a future change can't regress it.

- [x] **Rule 2 — System protection.** *(was broken — fixed)*
  Reproduced on the real `C:\Windows` (339,338 findings): **52 of 53 entities
  were not Protected**, including `Cache – Cortana.Ui` marked **Safe** and six
  `Images – …` collections of OS app assets marked **Review**, plus
  `Packages – servicing` (Safe) and `Backup – WinSxS` (Optional).
  → Cause: risk is assigned by whichever pass claims an entity first, so cache
  and image passes labelled OS content before protection was considered.
  → Fix: a final `_enforce_system_protection` pass — one choke point covering
  every pass — forces Protected inside `Windows\{SystemApps, WinSxS,
  servicing, assembly}`. Deliberately narrow: `Windows\Temp`, `Windows\Logs`
  and the Windows Update download cache **stay cleanable**, verified by a
  test, because over-protecting would gut the product's core value.
  → Re-verified on real data: all 24 entities in those subtrees are now
  Protected, 0 unprotected.

- [x] **Downloads folder.** *(fixed)*
  Probed on the real Downloads: top-level files were already handled well
  (installers became individual entities), but a *downloaded folder* shattered —
  one extracted Qt build produced "Misc files in release", ".cache",
  "Misc files in translations", "Misc files in QtQml" and more, none meaningful
  on its own.
  → Fix: a `_pass_downloads` pre-pass claims each direct child folder of a
  Downloads folder as one `download_item` entity before any generic pass can
  break it up, and claims Downloads itself as a pass-through node so loose
  files still bucket individually rather than collapsing into one blob.
  → Verified on the real folder: the extracted download is now **1 entity**
  (was 3), installers stay individual, loose files still bucket per type, and
  the accounting ratio is 1.000. Risk is Review — a download may be precious or
  disposable, so the user decides.

- [x] **User directories (Videos / Documents / Images).** *(done)*
  Individual files were already shown cleanly once loose buckets carried their
  real folder (first item above). What remained was the sub-folders: probed on
  the real profile, `Documents\Klei` — 58 MB of Don't Starve save data — showed
  as **"Klei · documents and code & config"** typed `unknown_folder`, i.e. as
  noise, and it counted toward the Unknown pile.
  → Fix: a direct child of Documents / Videos / Pictures / Music / Saved Games
  is application data. Those entities now carry the clean folder name, the
  `application_data` type and the "Application Data" category. Grouping, sizes
  and containment are untouched — only the label and type change.
  → Verified on the real profile: `Klei`, `grassdata` and `Mission Planner` are
  now App Data instead of Unknown, which also chips away at Phase 3's target.
  → Deliberately **not** decided here: whether the owning app is still
  installed. That needs alias handling and multiple evidence sources to be
  safe (see the knowledge-base section) — a wrong "orphaned" verdict would
  invite deleting live data. A test asserts this rule never claims an app is
  missing or the folder is deletable.

## Phase 2 — Quick wins ✅ COMPLETE

- [x] **History limit 5 → 10** for both Cleanup and Analyze sessions. *(done)*

- [x] **AI off by default for bulk scanning.** *(done)* `ai_findings_enabled`
  now defaults to `False`, so a scan never auto-queues hundreds of entities at
  a local model. Per-item **Ask AI** still works — `explain_item` passes
  `force=True` to bypass the toggle — and a test pins that, since without it
  the flip would leave no way to ask about anything. The setting is relabelled
  "Explain all findings automatically" with a note that it suits long
  background runs.

- [x] **Deep Uninstall** — *(done)* The handler already explained "no
  uninstaller found", but the button stayed **enabled** for every application,
  so it was a dead end that could only ever fail. It is now enabled only when
  an uninstall command actually exists, and otherwise carries a tooltip saying
  to use Move to Recycle Bin instead. This matters more since the Program Files
  rule: vendor folders like gstreamer and Fortinet are now applications with no
  registered uninstaller.

- [x] **Analyze screen hover** — *(could not reproduce; pinned)* Driving real
  mouse events through the table found correct behaviour in every case: hover,
  moving between rows, hover while another row is selected, leaving the table,
  and a mid-scan repopulate (the table rebuilds constantly while scanning, and
  the highlight survives). A prior fix is visible in the code — the highlight
  used `tint_bg`, which was almost identical to the panel background, and now
  uses `panel_hover`. Seven regression tests lock all of it in.
  → If it still looks wrong on your machine, say what you see and I will dig
  again — it may be a different surface (e.g. the Findings category cards).

## Phase 3 — Categorization quality (highest user value)

- [x] **Reduce "Unknown".** *(done — measured, not estimated)*
  Baseline from a full C:/ scan: **130 entities / 87 GB — 19% of the disk**,
  the second-largest category. Re-measured on the same disk after the Phase 1
  fixes, the knowledge base and the Program Files rule:

  | | entities | bytes | share of disk |
  |---|---|---|---|
  | baseline | 130 | 87.0 GB | 19% |
  | after Phase 1 + knowledge base | 108 | 15.2 GB | ~3% |
  | **after the Program Files rule** | **66** | **814 MB** | **0.17%** |

  → **99% of the Unknown bytes are now classified**, with the accounting ratio
  still 0.921 (no double-counting).
  → The last big pattern was the plainest one: a top-level folder in Program
  Files is an installed application, even when the uninstall registry does not
  name it. That alone moved ~14 GB (gstreamer 4.2 GB, Fortinet 3.3 GB, SQL
  Server 2.1 GB, Razer, Google, draw.io, OpenVPN …) out of Unknown and replaced
  descriptor-noise names like "gstreamer · documents" with the real folder name.
  → Follow-up: software installed **outside** Program Files is now recognised
  too — a drive-root folder naming a confirmed application is that install
  (`C:\PyCharm…` stays whole instead of shedding its bundled JRE as a stray
  "jbr", `C:\AMD` is identified). Final measurement:
  **63 entities / 157 MB — 99.8% of the baseline Unknown bytes classified.**
  → What remains is genuinely miscellaneous: loose "Misc files in …" buckets in
  Downloads/Documents/profile, and a small `bin` folder.

- [x] **Browser caches split from profile data.** *(done)*
  A browser profile is mostly irreplaceable — passwords, cookies, history,
  bookmarks — so the whole tree sat at Review and its cache went unnoticed
  inside it. Measured: **794 MB of the 6.35 GB Chrome profile is pure cache**,
  613 MB of that under Service Worker alone.
  → Caches are now separated before the profile is claimed, so **776 MB across
  Chrome + Edge is offered as Safe** while 5.7 GB of profile data stays Review.
  Precise about what is disposable: `Service Worker/CacheStorage` and
  `ScriptCache` are taken, its `Database` (registrations) is not.

### Built-in knowledge base (rules + AI hybrid)

The single highest-leverage way to shrink "Unknown". Deterministic, fast, and
testable — no AI needed. Two halves, and **the pattern half is the strong one**:

- [x] **Pattern rules — dotfolder support data.** *(done)*
  A single rule — "a dotfolder in the user profile is support data for
  `<name>`" — already covers **34 folders / 14.25 GB** on this machine, and it
  works for apps no curated list would ever contain (`.irizi`, `.nexe`,
  `.node-red`). Measured top entries: `.ollama` 6.8 GB, `.vscode` 2.8 GB,
  `.lmstudio` 2.65 GB, `.codex` 757 MB, `.claude` 376 MB.

- [x] **Curated list (for what patterns can't express).** *(done)*
  e.g. `C:\Program Files (x86)\Microsoft` = Edge / Copilot / OneDrive —
  **Windows-installed components the user never chose**. Label them as such and
  keep them Protected/Review; removing Edge components breaks the OS.
  → `app/services/classification_rules.json` already ships in the build, so
  this has a home and can be updated without a code change.

- [x] **Precedence is explicit.** *(done)* Implemented as: a curated rule may
  relabel a *generic* classification, and may **always raise caution** — but it
  can never talk a specific classification down. That last direction matters
  both ways: `Package Cache` looks like a plain cache by content, yet removing
  it costs offline repair, so the rule lifts it Safe → Optional; conversely a
  rule cannot downgrade something a pass identified specifically.

- [x] **Owner detection — SAFETY-CRITICAL.** *(done, and deliberately limited)*
  Built `app/services/app_presence.py`: an alias table plus five evidence
  sources (uninstall registry, installed folders, Start Menu, PATH, running
  processes), returning **PRESENT / UNKNOWN / GENERIC — never "absent"**.
  → Measured on the real profile (34 dotfolders): **18 present, 9 unknown,
  7 generic**, and *every one of the five sources was required* — `.vscode`
  resolves only via the Start Menu, `.lmstudio` only via installed folders,
  `.claude` only via a running process. Registry alone would have found ~4.
  → The ~5.6 GB in `.vscode` + `.lmstudio` that the naive check called
  "orphaned, safe to delete" is now correctly reported as installed.
  → UNKNOWN surfaces as *"Could not confirm whether X is still installed —
  check before removing"*. The "orphaned, safe to delete" verdict is **not**
  implemented and should stay unimplemented: `.aws`, `.azure` and `.gemini`
  prove absence is undetectable for pip/npm/winget tools.

> **⚠ Verified risk — do not ship the naive version.**
> Matching a dotfolder name against the uninstall registry gets it wrong most
> of the time. Probed on this machine: `.vscode` (2.8 GB) and `.lmstudio`
> (2.65 GB) are **not** found by name, yet both apps are installed —
> **7 of 11 probed apps would be mislabelled "orphaned, safe to delete"**,
> risking ~5.4 GB of real data. Modern apps install per-user, as MSIX, or
> portable, and never touch the classic uninstall key.
>
> Requirements before this ships:
> 1. **Alias table** (`.vscode` → "Visual Studio Code"). This is where a
>    curated list genuinely earns its keep — as *aliases*, not paths.
> 2. **Multiple evidence sources**: uninstall registry, `%LOCALAPPDATA%\Programs`,
>    Start-Menu shortcuts, PATH executables, running processes.
> 3. **Fail safe** — absence of evidence is not evidence of absence. If the app
>    cannot be confirmed missing, mark **Review / "could not verify"**, never Safe.
> 4. **Never auto-mark large payload folders Safe.** `.ollama` is 6.8 GB of
>    downloaded models; deleting it is a re-download decision, not a cleanup.

## Phase 4 — AI backends ✅ (pending live check)

**Scope (confirmed):** local models only, running on this machine or on a
mini-PC / home server on the **same network**. No cloud APIs — the existing
LAN-only guard and the "no cloud processing" promise stay exactly as they are.
The Local/Server toggle already provides the endpoint; what's left is speaking
each backend's API.

- [x] **Support the three popular local runtimes: Ollama, LM Studio, llama.cpp.**
  *(implemented; awaiting a live LM Studio check)*
  → The backend is **auto-detected**: `/api/tags` identifies Ollama, `/v1/models`
  identifies an OpenAI-compatible server, so the user just enters an address.
  Detection is cached per endpoint so a scan does not re-probe on every call.
  → Generation dispatches per backend: Ollama keeps `/api/generate` with
  settings nested under `options`; LM Studio and llama.cpp use
  `/v1/chat/completions` with settings at the top level. Both response shapes
  are read, including the `{"text": …}` variant some llama.cpp builds return.
  → `strip_reasoning()` applies to both, which matters because these servers
  commonly host reasoning models.
  → The LAN-only guard is unchanged and covered by tests: a cloud address is
  refused before any request is made.
  → 17 tests cover detection, listing, generation and both response shapes
  against a fake server. **Still to do: one live run against LM Studio's
  server** (it was installed but its local server was not started).

## Phase 5 — Languages (last, by agreement)

- [ ] **UI + AI prompts in English, Ukrainian, Spanish, German, French.**
  Ukrainian already works end to end.
  → Not just translation: every language needs **layout QA**. Ukrainian
  already overflowed three controls (sidebar nav, scan button, mode combo) and
  had to be shortened. Before adding languages, add an automated check that
  measures rendered string width against each control's width, so overflow is
  caught by tests instead of by eye.

## Phase 6 — Cross-platform (recommend deferring)

Agreed as a good addition, but **not urgent** — parked here deliberately.

- [ ] **Linux support — worth doing eventually; bigger than it looks.**
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
