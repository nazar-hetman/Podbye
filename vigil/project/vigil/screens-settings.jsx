/* Vigil — expanded Settings screen.
   Local-first workstation configuration. Inner left rail + scrollable
   right pane with multiple grouped panels per section. */

const SET_NAV = [
  { id:"general",   label:"General",   ico:"home" },
  { id:"ai",        label:"AI",        ico:"scan" },
  { id:"scan",      label:"Scan",      ico:"list" },
  { id:"interface", label:"Interface", ico:"power" },
  { id:"about",     label:"About",     ico:"gear" },
];

function SettingRow({ label, desc, children, hint }) {
  return (
    <div className="row" style={{ gap: 18, padding: "10px 0", borderBottom: "1px dashed var(--border)", alignItems: "flex-start" }}>
      <div className="col gap-4" style={{ width: 220, flex: "0 0 220px", paddingTop: 4 }}>
        <span style={{ fontSize: 12.5, color: "var(--text)" }}>{label}</span>
        {desc ? <span className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>{desc}</span> : null}
      </div>
      <div className="grow" style={{ minWidth: 0 }}>{children}</div>
      {hint ? <div className="mono faint" style={{ fontSize: 10, width: 110, textAlign: "right" }}>{hint}</div> : null}
    </div>
  );
}

function Toggle({ on }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span style={{
        width: 32, height: 18, position: "relative",
        background: on ? "var(--accent)" : "var(--panel-2)",
        border: "1px solid " + (on ? "var(--accent)" : "var(--border-2)"),
        borderRadius: 2, cursor: "pointer",
      }}>
        <span style={{
          position: "absolute", top: 1, left: on ? 15 : 1,
          width: 14, height: 14,
          background: on ? "var(--bg-deep)" : "var(--text-faint)",
          transition: "left .15s",
          borderRadius: 1,
        }}></span>
      </span>
      <span className="mono faint" style={{ fontSize: 10 }}>{on ? "ON" : "OFF"}</span>
    </span>
  );
}

function Seg({ options, value }) {
  return (
    <div className="row" style={{ display: "inline-flex", border: "1px solid var(--border-2)", background: "var(--panel-2)" }}>
      {options.map((o, i) => (
        <span key={o} className={value === o ? "mono" : "mono faint"}
          style={{
            padding: "6px 12px", fontSize: 11, letterSpacing: ".02em",
            background: value === o ? "var(--accent-soft)" : "transparent",
            color: value === o ? "var(--text)" : "var(--text-dim)",
            borderLeft: i === 0 ? "none" : "1px solid var(--border-2)",
            cursor: "pointer", userSelect: "none",
            borderTop: value === o ? "1px solid var(--accent)" : "1px solid transparent",
            marginTop: -1,
          }}>
          {o}
        </span>
      ))}
    </div>
  );
}

function Range({ min, max, step, value, unit, width = 200 }) {
  return (
    <div className="row gap-10" style={{ alignItems: "center" }}>
      <input className="vg-range" type="range" min={min} max={max} step={step || 1} defaultValue={value} style={{ width }}/>
      <span className="mono" style={{ fontSize: 12, width: 64, textAlign: "right" }}>{value}{unit}</span>
    </div>
  );
}

function PathRow({ p }) {
  return (
    <div className="row" style={{ background: "var(--panel-2)", border: "1px solid var(--border-2)", padding: "6px 8px" }}>
      <span className="mono" style={{ fontSize: 11, flex: 1, color: "var(--text)" }}>{p}</span>
      <span className="mono faint" style={{ fontSize: 10, cursor: "pointer" }}>×</span>
    </div>
  );
}

// ============== sections ==============

function GeneralSettings() {
  return (
    <div className="col gap-16">
      <Panel title="Appearance" num="// theme & density">
        <SettingRow label="Theme" desc="Color palette for the entire workstation. Affects all panes.">
          <div className="row gap-6">
            {["forest","amber","mono","paper"].map((t,i) => (
              <Chip key={t} on={i === 0} dot={["#7cc596","#e8b169","#a8a8a8","#3d6b48"][i]}>{t}</Chip>
            ))}
          </div>
        </SettingRow>
        <SettingRow label="UI scale" desc="Scales every element. Useful on high-DPI displays.">
          <Seg options={["90%","100%","110%","125%"]} value="100%"/>
        </SettingRow>
        <SettingRow label="Density" desc="Tighter rows show more at once; comfortable is more scannable.">
          <Seg options={["compact","comfortable"]} value="comfortable"/>
        </SettingRow>
        <SettingRow label="Animation intensity" desc="Reduce or disable transitions for low-spec hardware.">
          <Seg options={["off","minimal","standard"]} value="standard"/>
        </SettingRow>
        <SettingRow label="CRT scanlines" desc="Subtle terminal overlay. Always off in light themes.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>

      <Panel title="Language & Locale" num="// strings">
        <SettingRow label="Language" desc="Affects UI strings and locally-generated AI explanations.">
          <select className="vg-select" defaultValue="en">
            <option value="en">English (US)</option>
            <option>English (UK)</option>
            <option>Deutsch</option>
            <option>Français</option>
            <option>Español</option>
            <option>日本語</option>
            <option>한국어</option>
            <option>简体中文</option>
          </select>
        </SettingRow>
        <SettingRow label="Date format" desc="">
          <Seg options={["YYYY-MM-DD","DD MMM YYYY","MM/DD/YYYY"]} value="DD MMM YYYY"/>
        </SettingRow>
        <SettingRow label="Time format" desc="">
          <Seg options={["24-hour","12-hour"]} value="24-hour"/>
        </SettingRow>
        <SettingRow label="Numbering" desc="Thousands separator and decimal style.">
          <Seg options={["1,234.56","1.234,56","1 234.56"]} value="1,234.56"/>
        </SettingRow>
      </Panel>

      <Panel title="Session" num="// startup behavior">
        <SettingRow label="Restore last session" desc="On launch, reopen the last-viewed pane and selection state.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Auto-open report after scan" desc="Switch to Findings automatically once analysis finishes.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Default landing page" desc="Where Vigil opens on launch.">
          <select className="vg-select" style={{ maxWidth: 220 }}>
            <option>Home</option><option>Quick Cleanup</option><option>Last Findings report</option><option>History</option>
          </select>
        </SettingRow>
        <SettingRow label="Confirm on quit" desc="Prompt if a scan or cleanup is still in progress.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>
    </div>
  );
}

function AiSettings() {
  return (
    <div className="col gap-16">
      <Panel title="Local Model Server" num="// endpoint" right={<Pill kind="safe">connected</Pill>}>
        <SettingRow label="Endpoint" desc="Ollama-compatible HTTP server on this machine. Vigil never reaches the public network.">
          <div className="row gap-8" style={{ alignItems: "center" }}>
            <input className="vg-input mono" defaultValue="http://127.0.0.1:11434" style={{ maxWidth: 300 }}/>
            <button className="btn btn-ghost">Test</button>
          </div>
        </SettingRow>
        <SettingRow label="Connection" desc="Last contacted server.">
          <div className="row gap-10" style={{ alignItems: "center" }}>
            <span className="dot-led" style={{ display: "inline-block", width: 6, height: 6, background: "var(--safe)", boxShadow: "0 0 6px var(--safe)" }}></span>
            <span className="mono" style={{ fontSize: 11.5 }}>online · 11ms · ollama 0.4.6</span>
          </div>
        </SettingRow>
        <SettingRow label="Library" desc="Local model catalog read from the server.">
          <div className="row gap-8">
            <button className="btn btn-ghost">Refresh models</button>
            <span className="mono faint" style={{ fontSize: 11, alignSelf: "center" }}>5 models · 14.6 GB on disk</span>
          </div>
        </SettingRow>
        <div style={{ marginTop: 4, padding: "10px 12px", background: "var(--bg-deep)", border: "1px solid var(--border)", fontSize: 11, color: "var(--text-dim)", lineHeight: 1.55 }}>
          <span className="label-eyebrow" style={{ marginRight: 8 }}>Local-only</span>
          Vigil refuses to connect to non-loopback or non-LAN endpoints. There is no cloud fallback, no API key field, no analytics.
        </div>
      </Panel>

      <Panel title="Model Selection" num="// active model">
        <SettingRow label="Active model" desc="Smaller models are faster but produce shorter explanations.">
          <select className="vg-select mono" defaultValue="llama3.2:3b">
            <option>llama3.2:3b · 2.0 GB · light</option>
            <option>qwen2.5:7b · 4.4 GB · balanced</option>
            <option>mistral:7b · 4.1 GB · balanced</option>
            <option>gemma2:9b · 5.5 GB · heavier</option>
            <option>llama3.1:8b · 4.7 GB · balanced</option>
            <option>—— custom ——</option>
          </select>
        </SettingRow>
        <div style={{ background: "var(--panel-2)", border: "1px solid var(--border-2)", padding: 12, marginTop: 4 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <span className="pixel" style={{ fontSize: 10, letterSpacing: ".14em", color: "var(--text)" }}>LLAMA3.2:3B</span>
            <span className="mono faint" style={{ fontSize: 10 }}>quantization · q4_K_M</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
            <div className="col gap-4"><Eyebrow>Size on disk</Eyebrow><span className="mono" style={{ fontSize: 13 }}>2.0 GB</span></div>
            <div className="col gap-4"><Eyebrow>RAM at load</Eyebrow><span className="mono" style={{ fontSize: 13 }}>~ 3.4 GB</span></div>
            <div className="col gap-4"><Eyebrow>Avg response</Eyebrow><span className="mono" style={{ fontSize: 13 }}>0.21 s / prompt</span></div>
            <div className="col gap-4"><Eyebrow>Context</Eyebrow><span className="mono" style={{ fontSize: 13 }}>8,192 tok</span></div>
          </div>
        </div>
      </Panel>

      <Panel title="Explanation" num="// style & length">
        <SettingRow label="Explanation style" desc="Affects terminology and tone, not personality.">
          <Seg options={["friendly","neutral","professional","technical"]} value="neutral"/>
        </SettingRow>
        <SettingRow label="Explanation length" desc="Controls how much the model writes per finding.">
          <Seg options={["compact","standard","detailed"]} value="standard"/>
        </SettingRow>
        <SettingRow label="Enable AI explanations" desc="Master switch. When off, findings show only heuristic risk.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Explain only risky findings" desc="Skip the model for findings flagged as safe with ≥ 95% confidence.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Explain duplicate groups" desc="Generate a single explanation per cluster instead of per file.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Skip AI during quick cleanup" desc="Quick cleanup uses heuristics only; AI runs only in full Analyze.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>

      <Panel title="Performance" num="// runtime">
        <SettingRow label="Per-prompt timeout" desc="Skip explanation if the model exceeds this. Finding still shows heuristics.">
          <Range min={2} max={30} value={8} unit=" s" />
        </SettingRow>
        <SettingRow label="Max concurrent explanations" desc="More concurrency speeds up reports; uses more VRAM/RAM.">
          <Range min={1} max={8} value={2} unit="" />
        </SettingRow>
        <SettingRow label="Low VRAM mode" desc="Loads layers in chunks. Recommended for ≤ 6 GB GPUs.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="CPU-only mode" desc="Disable GPU offload entirely. Slower but predictable.">
          <Toggle on={false} />
        </SettingRow>
      </Panel>
    </div>
  );
}

function ScanSettings() {
  return (
    <div className="col gap-16">
      <Panel title="Scan Depth" num="// breadth & recursion">
        <SettingRow label="Default depth" desc="Quick scans only top-level cache paths; Deep recurses through dev folders.">
          <Seg options={["quick","standard","deep"]} value="standard"/>
        </SettingRow>
        <SettingRow label="Max recursion" desc="Hard cap on directory depth.">
          <Range min={2} max={20} value={12} unit=" levels" />
        </SettingRow>
        <SettingRow label="Follow symlinks" desc="Off by default to avoid loops and cross-volume drift.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Cross volumes" desc="Allow recursive scans to descend into other mounted drives.">
          <Toggle on={false} />
        </SettingRow>
      </Panel>

      <Panel title="Safeguards" num="// Vigil never touches these">
        <SettingRow label="Protected paths" desc="Vigil refuses to flag anything beneath these paths, ever.">
          <div className="col gap-4" style={{ alignItems: "stretch", maxWidth: 420 }}>
            <PathRow p="C:\Users\op\Documents" />
            <PathRow p="C:\Users\op\Desktop" />
            <PathRow p="C:\Users\op\Pictures" />
            <PathRow p="D:\Photos" />
            <PathRow p="D:\Projects\client-x" />
            <button className="btn btn-ghost" style={{ alignSelf: "flex-start", marginTop: 4 }}>+ add path</button>
          </div>
        </SettingRow>
        <SettingRow label="Ignore patterns" desc="Glob patterns excluded from every scan.">
          <input className="vg-input mono" defaultValue="**/.git/**, **/venv/**, **/.cache/**, **/node_modules/.bin/**" style={{ maxWidth: 460 }}/>
        </SettingRow>
        <SettingRow label="Lock system-critical paths" desc="C:\Windows, Program Files and registry-tracked installs are never selectable.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Confirm risky cleanup" desc="Show a confirmation dialog before removing items flagged 'review' or 'risk'.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>

      <Panel title="Detection" num="// what counts as a finding">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0 }}>
          {[
            ["Detect temporary files",  true,  "OS temp, app caches, thumbnail caches"],
            ["Detect duplicates",       true,  "Identical sha256 across all scanned paths"],
            ["Detect old logs",         true,  "Plain-text logs older than 30 days"],
            ["Detect large media",      true,  "Single files ≥ 1 GB, unaccessed for 90+ days"],
            ["Detect dev artifacts",    true,  "node_modules, build/, target/, __pycache__"],
            ["Detect empty folders",    false, "Directories with zero files after scan completes"],
            ["Detect orphaned installers", true,"Setup/.msi/.dmg files older than the app they installed"],
            ["Detect crash dumps",      false, "*.dmp, hs_err_*, app-specific dumps"],
          ].map(([l, on, d], i) => (
            <div key={i} className="row gap-12" style={{ padding: "10px 12px", borderBottom: "1px dashed var(--border)", alignItems: "flex-start", borderRight: i % 2 === 0 ? "1px dashed var(--border)" : "none" }}>
              <Toggle on={on} />
              <div className="col gap-4 grow">
                <span style={{ fontSize: 12.5 }}>{l}</span>
                <span className="muted" style={{ fontSize: 11, lineHeight: 1.45 }}>{d}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Performance" num="// scanner runtime">
        <SettingRow label="Max CPU usage" desc="Cap on scanner process across all threads.">
          <Range min={10} max={100} value={70} unit=" %" />
        </SettingRow>
        <SettingRow label="Scan threads" desc="Higher is faster on SSDs; spinning disks plateau at 2.">
          <Range min={1} max={16} value={6} unit="" />
        </SettingRow>
        <SettingRow label="Pause on battery" desc="Suspend scans when the device unplugs.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Low-power mode" desc="Lower priority, single-threaded, throttled I/O.">
          <Toggle on={false} />
        </SettingRow>
      </Panel>

      <Panel title="File Handling" num="// what happens when you clean">
        <SettingRow label="Send removals to recycle bin" desc="Default. Items can be restored by the OS for the retention window.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Permanent delete" desc="Skip the recycle bin entirely. Cannot be undone.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Keep scan cache" desc="Cache hashes between scans to skip re-reading unchanged files.">
          <Toggle on={true} hint="~ 84 MB"/>
        </SettingRow>
        <SettingRow label="Auto-clean old reports" desc="Delete report NDJSON older than the threshold.">
          <Range min={7} max={365} value={90} unit=" days" />
        </SettingRow>
      </Panel>

      <Panel title="Session & Export" num="// audit trail">
        <SettingRow label="Save scan history" desc="Keep summaries available in the History pane.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Save AI explanations" desc="Persist generated text alongside reports for offline review.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Export NDJSON automatically" desc="Write a machine-readable copy of each report next to the scan.">
          <Toggle on={false} />
        </SettingRow>
      </Panel>
    </div>
  );
}

function InterfaceSettings() {
  return (
    <div className="col gap-16">
      <Panel title="Visibility" num="// what to show">
        <SettingRow label="Show AI explanations" desc="Inline with each finding's expanded view.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Show confidence bars" desc="Per-finding heuristic confidence (0–100%) in the table.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Show advanced metadata" desc="sha256, owner, ACL, first-seen — visible in expanded rows.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Show operator feed" desc="Live stdout panel during Analyze. Disable to hide for screen-sharing.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Show timeline charts" desc="History pane month-bar visualization.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>

      <Panel title="Tables" num="// row & header behavior">
        <SettingRow label="Compact rows" desc="Reduces row height by ~25%. Useful for ≥ 1080p displays.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Alternate row shading" desc="Stripe every other row for legibility on long lists.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Sticky headers" desc="Pin column headers when scrolling long tables.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Auto-expand recommendations" desc="Open the recommendation block on every selected finding by default.">
          <Toggle on={false} />
        </SettingRow>
      </Panel>

      <Panel title="Visual Style" num="// terminal flavor">
        <SettingRow label="Pixel header intensity" desc="How loud the Silkscreen-style headers appear. Lower for long sessions.">
          <Seg options={["off","subtle","standard","strong"]} value="standard"/>
        </SettingRow>
        <SettingRow label="Mono accent" desc="Use monochrome accent instead of theme color for chrome.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Border glow intensity" desc="Subtle inner highlight on active panels.">
          <Range min={0} max={100} value={20} unit=" %" />
        </SettingRow>
        <SettingRow label="Scanline intensity" desc="CRT overlay opacity. 0 disables.">
          <Range min={0} max={100} value={35} unit=" %" />
        </SettingRow>
      </Panel>

      <Panel title="Layout" num="// workspace memory">
        <SettingRow label="Remember sidebar width" desc="Restore the sidebar to its last-resized width across sessions.">
          <Toggle on={true} />
        </SettingRow>
        <SettingRow label="Multi-column findings" desc="Split Findings into category columns when the window is ≥ 1600px wide.">
          <Toggle on={false} />
        </SettingRow>
        <SettingRow label="Detail expansion" desc="Whether finding details open inline or in a side modal.">
          <Seg options={["inline","modal"]} value="inline"/>
        </SettingRow>
        <SettingRow label="Keyboard shortcuts" desc="Show the shortcut overlay (?) when held.">
          <Toggle on={true} />
        </SettingRow>
      </Panel>
    </div>
  );
}

function AboutSettings() {
  return (
    <div className="col gap-16">
      <Panel title="Build" num="// 0.4.2">
        <SettingRow label="Version" desc="">
          <span className="mono" style={{ fontSize: 12 }}>0.4.2 · stable</span>
        </SettingRow>
        <SettingRow label="Build" desc="">
          <span className="mono" style={{ fontSize: 12 }}>2026-05-08 · #14821 · sha c4f1·a902</span>
        </SettingRow>
        <SettingRow label="Qt runtime" desc="">
          <span className="mono" style={{ fontSize: 12 }}>Qt 6.7.2 · MSVC 2022 · 64-bit</span>
        </SettingRow>
        <SettingRow label="Runtime mode" desc="">
          <span className="row gap-8">
            <span className="pill safe">local-only</span>
            <span className="mono faint" style={{ fontSize: 11 }}>no network · no telemetry · no daemon</span>
          </span>
        </SettingRow>
        <SettingRow label="Platform" desc="">
          <span className="mono" style={{ fontSize: 12 }}>Windows 11 · 26100.2152 · x64</span>
        </SettingRow>
      </Panel>

      <Panel title="Paths" num="// where Vigil lives on this machine">
        <SettingRow label="Configuration" desc="">
          <span className="mono" style={{ fontSize: 11.5 }}>%APPDATA%\Vigil\config.json</span>
        </SettingRow>
        <SettingRow label="Logs" desc="">
          <span className="mono" style={{ fontSize: 11.5 }}>%LOCALAPPDATA%\Vigil\logs\</span>
        </SettingRow>
        <SettingRow label="Reports" desc="">
          <span className="mono" style={{ fontSize: 11.5 }}>%LOCALAPPDATA%\Vigil\reports\</span>
        </SettingRow>
        <SettingRow label="Scan cache" desc="">
          <span className="mono" style={{ fontSize: 11.5 }}>%LOCALAPPDATA%\Vigil\cache\hashes.db</span>
        </SettingRow>
        <SettingRow label="Disk usage" desc="Total Vigil footprint on this machine.">
          <span className="mono" style={{ fontSize: 12 }}>184 MB</span>
        </SettingRow>
      </Panel>

      <Panel title="Diagnostics" num="// support">
        <div className="row gap-8" style={{ flexWrap: "wrap" }}>
          <button className="btn">Export diagnostics bundle</button>
          <button className="btn btn-ghost">Open logs folder ↗</button>
          <button className="btn btn-ghost">Open config ↗</button>
          <button className="btn btn-ghost">Export configuration</button>
          <button className="btn btn-ghost">Import configuration…</button>
          <button className="btn btn-ghost" style={{ color: "var(--risk)", borderColor: "var(--border-2)" }}>Reset all settings</button>
        </div>
        <div className="div"></div>
        <div className="mono faint" style={{ fontSize: 10.5, lineHeight: 1.65 }}>
          Diagnostics bundle is a single .zip containing config.json, the last 7 logs, and a manifest of the scan cache (hashes only — never file contents). Nothing is uploaded; the file lands in your Downloads folder.
        </div>
      </Panel>

      <Panel title="License" num="// utility build">
        <div className="col gap-10" style={{ fontSize: 12, lineHeight: 1.6 }}>
          <div className="row gap-14">
            <div className="col gap-4" style={{ width: 160 }}><Eyebrow>License</Eyebrow><span className="mono" style={{ fontSize: 12 }}>VIGIL-PRO · seat</span></div>
            <div className="col gap-4" style={{ width: 200 }}><Eyebrow>Issued to</Eyebrow><span className="mono" style={{ fontSize: 12 }}>op@workstation</span></div>
            <div className="col gap-4"><Eyebrow>Renewal</Eyebrow><span className="mono" style={{ fontSize: 12 }}>perpetual</span></div>
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            Vigil ships as a single signed binary. No accounts, no servers, no licensing callback. Validation happens locally against the bundled key.
          </div>
        </div>
      </Panel>
    </div>
  );
}

// ============== screen ==============

function SettingsScreen({ tab = "ai" }) {
  return (
    <>
      <Topbar
        crumb="SETTINGS"
        sub="local · per-user · stored at %APPDATA%\\Vigil\\config.json"
        right={
          <div className="row gap-8">
            <span className="mono faint" style={{ fontSize: 11 }}>3 unsaved changes</span>
            <button className="btn btn-ghost">Discard</button>
            <button className="btn btn-primary">Save changes</button>
          </div>
        }
      />
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* Inner settings nav */}
        <aside style={{
          width: 220, flex: "0 0 220px",
          borderRight: "1px solid var(--border)",
          background: "var(--bg-deep)",
          padding: "18px 0",
          display: "flex", flexDirection: "column",
        }}>
          <div className="label-eyebrow" style={{ padding: "0 18px 8px" }}>Configuration</div>
          {SET_NAV.map(s => (
            <div key={s.id}
              className={"nav-item" + (s.id === tab ? " active" : "")}
              style={{ padding: "8px 18px" }}>
              <span className="nav-ico"><NavIco name={s.ico}/></span>
              <span>{s.label}</span>
            </div>
          ))}
          <div style={{ marginTop: "auto", padding: "12px 18px", borderTop: "1px solid var(--border)" }}>
            <div className="label-eyebrow" style={{ marginBottom: 6 }}>Operator</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--text)" }}>op@workstation</div>
            <div className="mono faint" style={{ fontSize: 10, marginTop: 2 }}>local · perpetual</div>
          </div>
        </aside>

        {/* Right pane */}
        <div className="content" style={{ flex: 1, padding: "22px 24px" }}>
          <div className="row" style={{ alignItems: "baseline", marginBottom: 14, gap: 12 }}>
            <span className="pixel" style={{ fontSize: 14, letterSpacing: ".14em", color: "var(--text)" }}>{tab.toUpperCase()}</span>
            <span className="mono faint" style={{ fontSize: 11 }}>// {sectionSub(tab)}</span>
          </div>
          {tab === "general"   && <GeneralSettings/>}
          {tab === "ai"        && <AiSettings/>}
          {tab === "scan"      && <ScanSettings/>}
          {tab === "interface" && <InterfaceSettings/>}
          {tab === "about"     && <AboutSettings/>}
        </div>
      </div>
    </>
  );
}

function sectionSub(id) {
  switch (id) {
    case "general":   return "appearance, language, session";
    case "ai":        return "local model · explanation · performance";
    case "scan":      return "depth · safeguards · detection · file handling";
    case "interface": return "visibility · tables · style · layout";
    case "about":     return "build · paths · diagnostics · license";
    default: return "";
  }
}

// inline form CSS once (idempotent)
if (typeof document !== "undefined" && !document.getElementById("vigil-form-style")) {
  const s = document.createElement("style");
  s.id = "vigil-form-style";
  s.textContent = `
    .vg-input, .vg-select {
      background: var(--panel-2);
      border: 1px solid var(--border-2);
      color: var(--text);
      padding: 7px 10px;
      font: 12px "IBM Plex Sans", sans-serif;
      border-radius: 2px;
      outline: none;
      width: 100%;
      max-width: 320px;
    }
    .vg-input:focus, .vg-select:focus { border-color: var(--accent); }
    .vg-range { accent-color: var(--accent); }
  `;
  document.head.appendChild(s);
}

Object.assign(window, { SettingsScreen });
