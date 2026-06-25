/* Vigil — screens B: Findings, Startups, History, Settings */

const FIND_CATS = [
  { id:"cache",  label:"Cache & Temp",   count: 6218, dot:"var(--accent)" },
  { id:"media",  label:"Media",          count:  412, dot:"var(--review)" },
  { id:"arch",   label:"Archives",       count:   88, dot:"var(--review)" },
  { id:"brows",  label:"Browser Data",   count: 2401, dot:"var(--accent)" },
  { id:"dup",    label:"Duplicates",     count:  874, dot:"var(--review)" },
  { id:"dev",    label:"Dev Artifacts",  count:  331, dot:"var(--risk)"   },
  { id:"logs",   label:"System Logs",    count:  712, dot:"var(--accent)" },
];

const FIND_ROWS = [
  { sel:true,  cat:"Cache & Temp",  path:"C:\\Users\\op\\AppData\\Local\\Temp\\nvidia-cache-2025\\*", size:"2.41 GB", age:"312d", conf:99, risk:"safe",   why:"Driver-managed shader cache. Regenerated automatically on next launch. No personal data; not referenced by any installed app's manifest." },
  { sel:true,  cat:"Browser Data", path:"C:\\Users\\op\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\Cache\\*", size:"1.84 GB", age:"7d",   conf:98, risk:"safe",   why:"HTTP cache. Removing it logs you out of nothing and clears no cookies. Edge will rebuild as you browse." },
  { sel:false, cat:"Duplicates",   path:"D:\\Backups\\photos-2024.zip  ↔  D:\\Photos\\2024-archive.zip", size:"3.12 GB", age:"148d", conf:88, risk:"review", why:"Identical sha256. The two paths suggest one is an older backup. Vigil keeps the most-recently-accessed copy by default; flip to choose manually." },
  { sel:false, cat:"Dev Artifacts",path:"D:\\Projects\\— recursive\\node_modules\\* (47 folders)",      size:"5.62 GB", age:"30d",  conf:74, risk:"risk",   why:"Reinstalling is straightforward but slow. Removing during an active build will break it. Last access on 12 of 47 folders is < 7d ago." },
  { sel:true,  cat:"System Logs", path:"C:\\Windows\\Logs\\CBS\\CBS.log.* (older than 30d)",            size:"212 MB",  age:"45d",  conf:96, risk:"safe",   why:"Component Servicing logs older than the active patch cycle. Windows rotates these on its own; manual removal accelerates that." },
  { sel:false, cat:"Archives",     path:"E:\\Downloads\\windows10-1909-iso.iso",                        size:"4.40 GB", age:"412d", conf:81, risk:"review", why:"Large unaccessed installer. Old enough that the ISO is no longer the current build. Worth keeping only if used for offline imaging." },
  { sel:true,  cat:"Cache & Temp", path:"%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\thumbcache_*",   size:"186 MB",  age:"3d",   conf:100,risk:"safe",   why:"Thumbnail cache. Will rebuild on next folder browse. No content, only image previews." },
];

function FindingsScreen({ activeChips = ["cache","brows","dup","dev","logs"], expandedIdx = 2 }) {
  return (
    <>
      <Topbar
        crumb="FINDINGS"
        sub="2,418 items · 14.6 GB across 7 categories"
        right={
          <div className="row gap-8">
            <div className="row" style={{ border: "1px solid var(--border-2)", height: 30 }}>
              <input placeholder="filter…  path / pattern / hash" className="mono"
                style={{ background: "var(--panel-2)", border: "none", color: "var(--text)", padding: "0 12px",
                         outline: "none", width: 240, fontSize: 12, height: "100%" }} />
              <span className="mono faint" style={{ padding: "0 10px", borderLeft: "1px solid var(--border-2)", display: "flex", alignItems: "center", fontSize: 10 }}>⌘F</span>
            </div>
            <button className="btn btn-ghost">Export…</button>
            <button className="btn btn-primary">Clean 4 selected · 4.81 GB</button>
          </div>
        }
      />
      <div className="content" style={{ paddingBottom: 14 }}>
        {/* filter chips */}
        <div className="row gap-8" style={{ flexWrap: "wrap", marginBottom: 14 }}>
          <span className="label-eyebrow" style={{ marginRight: 4 }}>Categories</span>
          {FIND_CATS.map(c => (
            <Chip key={c.id} on={activeChips.includes(c.id)} count={c.count.toLocaleString()} dot={c.dot}>
              {c.label}
            </Chip>
          ))}
          <span style={{ width: 1, height: 16, background: "var(--border)", margin: "0 4px" }}></span>
          <Chip dot="var(--safe)">Safe</Chip>
          <Chip on dot="var(--review)" count="412">Review</Chip>
          <Chip dot="var(--risk)" count="78">Risky</Chip>
          <span style={{ width: 1, height: 16, background: "var(--border)", margin: "0 4px" }}></span>
          <Chip>≥ 100 MB</Chip>
          <Chip on>≥ 30d unused</Chip>
          <Chip>has duplicates</Chip>
        </div>

        {/* table */}
        <div className="panel">
          <div className="panel-h">
            <div className="row gap-10" style={{ alignItems: "baseline" }}>
              <span className="pt">Items</span>
              <span className="pn">// {FIND_ROWS.length} of 2,418 — sorted by size</span>
            </div>
            <div className="row gap-8 mono faint" style={{ fontSize: 10 }}>
              <span>SORT: SIZE ▾</span>
              <span>·</span>
              <span>VIEW: ROWS</span>
            </div>
          </div>
          <div style={{ overflow: "hidden" }}>
            <table className="tbl">
              <colgroup>
                <col style={{ width: 28 }}/><col style={{ width: 28 }}/>
                <col style={{ width: 130 }}/><col/><col style={{ width: 90 }}/><col style={{ width: 60 }}/><col style={{ width: 110 }}/><col style={{ width: 80 }}/>
              </colgroup>
              <thead>
                <tr>
                  <th></th>
                  <th></th>
                  <th>Category</th>
                  <th>Path</th>
                  <th style={{ textAlign: "right" }}>Size</th>
                  <th style={{ textAlign: "right" }}>Age</th>
                  <th>Risk</th>
                  <th style={{ textAlign: "right" }}>Conf</th>
                </tr>
              </thead>
              <tbody>
                {FIND_ROWS.map((r, i) => {
                  const exp = i === expandedIdx;
                  return (
                    <React.Fragment key={i}>
                      <tr className={exp ? "expanded" : ""}>
                        <td><Cb on={r.sel} /></td>
                        <td className="mono faint" style={{ fontSize: 10, textAlign: "center" }}>{exp ? "▾" : "▸"}</td>
                        <td>
                          <span className="row gap-6">
                            <span style={{ width: 6, height: 6, background: r.risk === "safe" ? "var(--safe)" : r.risk === "review" ? "var(--review)" : "var(--risk)" }}></span>
                            <span style={{ fontSize: 12 }}>{r.cat}</span>
                          </span>
                        </td>
                        <td className="path">{r.path}</td>
                        <td className="size">{r.size}</td>
                        <td className="size faint">{r.age}</td>
                        <td><Pill kind={r.risk}>{r.risk}</Pill></td>
                        <td className="size mono">{r.conf}%</td>
                      </tr>
                      {exp && (
                        <tr className="expanded">
                          <td></td>
                          <td colSpan={7} style={{ paddingTop: 0, paddingBottom: 16 }}>
                            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 18, paddingTop: 4 }}>
                              <div className="col gap-10">
                                <div>
                                  <Eyebrow>Why flagged</Eyebrow>
                                  <div style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 6, color: "var(--text)" }}>
                                    {r.why}
                                  </div>
                                </div>
                                <div className="row gap-14" style={{ borderTop: "1px dashed var(--border-2)", paddingTop: 10 }}>
                                  <div className="col gap-4"><Eyebrow>Last access</Eyebrow><span className="mono" style={{ fontSize: 12 }}>2025-12-12 · 09:14</span></div>
                                  <div className="col gap-4"><Eyebrow>First seen</Eyebrow><span className="mono" style={{ fontSize: 12 }}>2024-06-30</span></div>
                                  <div className="col gap-4"><Eyebrow>Owner</Eyebrow><span className="mono" style={{ fontSize: 12 }}>op · BUILTIN\Users</span></div>
                                  <div className="col gap-4"><Eyebrow>Backed by</Eyebrow><span className="mono" style={{ fontSize: 12 }}>2 duplicates</span></div>
                                </div>
                              </div>
                              <div className="col gap-10">
                                <div>
                                  <Eyebrow>Recommendation</Eyebrow>
                                  <div className="row gap-8" style={{ marginTop: 6, alignItems: "center" }}>
                                    <Pill kind="review">keep newest · remove older</Pill>
                                  </div>
                                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6, lineHeight: 1.55 }}>
                                    Frees <span className="mono" style={{ color: "var(--text)" }}>3.12 GB</span> · zero unique data lost.
                                  </div>
                                </div>
                                <div className="row gap-8" style={{ marginTop: 4, flexWrap: "wrap" }}>
                                  <button className="btn">Apply recommendation</button>
                                  <button className="btn btn-ghost">Open in Explorer</button>
                                  <button className="btn btn-ghost">Compare</button>
                                  <button className="btn btn-ghost">Add to ignore list</button>
                                </div>
                                <div className="mono faint" style={{ fontSize: 10.5, lineHeight: 1.55, marginTop: 4 }}>
                                  sha256 · a91f…7c02 · 04ce…2b18<br/>
                                  ai-model · llama3.2:3b · explanation 0.22s
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="mono faint" style={{ fontSize: 10 }}>showing 7 of 2,418 · scroll to load more</span>
            <span className="mono faint" style={{ fontSize: 10 }}>4 selected · 4.81 GB</span>
          </div>
        </div>
      </div>
    </>
  );
}

function StartupsScreen() {
  const items = [
    { name:"Adobe Creative Cloud",  pub:"Adobe Inc.",            cpu: 4.2, ram:"  214 MB", boot: 2.1, status:"on",       rec:"disable",  why:"Adds 2.1s to boot. Updates run on a daily check that doesn't need to be at startup." },
    { name:"NVIDIA Container",      pub:"NVIDIA Corporation",    cpu: 1.1, ram:"  84 MB",  boot: 0.4, status:"on",       rec:"keep",     why:"Driver service. Required for GPU control panel and overlay features." },
    { name:"Steam Client Bootstrap",pub:"Valve Corporation",     cpu: 6.8, ram:"  312 MB", boot: 3.4, status:"on",       rec:"delay",    why:"Largest startup contributor. Delaying by 30s keeps Steam available without blocking login." },
    { name:"Spotify",               pub:"Spotify AB",            cpu: 0.8, ram:"  168 MB", boot: 1.2, status:"on",       rec:"disable",  why:"Launches on-demand quickly enough; auto-start mostly serves their telemetry." },
    { name:"Synaptics TouchPad",    pub:"Synaptics Inc.",        cpu: 0.2, ram:"   24 MB", boot: 0.1, status:"on",       rec:"keep",     why:"Driver helper for trackpad gestures. Small footprint." },
    { name:"OneDrive",              pub:"Microsoft Corporation", cpu: 1.6, ram:"  142 MB", boot: 0.9, status:"on",       rec:"keep",     why:"Sync client; disabling stops cloud backup. Recommend keep unless you've moved to another tool." },
    { name:"Discord",               pub:"Discord Inc.",          cpu: 2.4, ram:"  256 MB", boot: 1.6, status:"disabled", rec:"keep off", why:"Already disabled. Self-launch on demand is faster than auto-start." },
  ];
  const totalBoot = items.filter(i => i.status === "on").reduce((a,b) => a + b.boot, 0).toFixed(1);
  return (
    <>
      <Topbar
        crumb="STARTUPS"
        sub="recommendation only · Vigil never modifies startup state"
        right={
          <div className="row gap-8">
            <button className="btn btn-ghost">Re-scan</button>
            <button className="btn">Open Task Manager ↗</button>
          </div>
        }
      />
      <div className="content">
        {/* Top stats */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1.2fr", gap: 16, marginBottom: 16 }}>
          <Panel>
            <Eyebrow>Boot impact</Eyebrow>
            <div style={{ height: 6 }}></div>
            <Bignum value={totalBoot} unit="s added at login" size={36} />
          </Panel>
          <Panel>
            <Eyebrow>Active entries</Eyebrow>
            <div style={{ height: 6 }}></div>
            <Bignum value="6" unit={"of " + items.length} size={36} />
          </Panel>
          <Panel>
            <Eyebrow>Memory at idle</Eyebrow>
            <div style={{ height: 6 }}></div>
            <Bignum value="924" unit="MB cumulative" size={36} />
          </Panel>
          <Panel>
            <Eyebrow>Recommendations</Eyebrow>
            <div style={{ height: 6 }}></div>
            <div className="row gap-10" style={{ marginTop: 6 }}>
              <div className="col gap-4"><span className="mono" style={{ fontSize: 22, color: "var(--review)" }}>2</span><span className="mono faint" style={{ fontSize: 10 }}>disable</span></div>
              <div className="col gap-4"><span className="mono" style={{ fontSize: 22, color: "var(--accent)" }}>1</span><span className="mono faint" style={{ fontSize: 10 }}>delay</span></div>
              <div className="col gap-4"><span className="mono" style={{ fontSize: 22, color: "var(--safe)" }}>3</span><span className="mono faint" style={{ fontSize: 10 }}>keep</span></div>
              <div className="col gap-4"><span className="mono faint" style={{ fontSize: 22 }}>1</span><span className="mono faint" style={{ fontSize: 10 }}>off</span></div>
            </div>
          </Panel>
        </div>

        <Panel title="Startup Entries" num="// recommendation only">
          <table className="tbl">
            <colgroup>
              <col/><col style={{ width: 100 }}/><col style={{ width: 100 }}/><col style={{ width: 110 }}/><col style={{ width: 130 }}/><col style={{ width: 110 }}/>
            </colgroup>
            <thead>
              <tr>
                <th>Program</th>
                <th>CPU @ start</th>
                <th>RAM idle</th>
                <th>Boot delay</th>
                <th>Recommendation</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it,i) => {
                const recKind = it.rec === "disable" ? "review" : it.rec === "delay" ? "review" : it.rec === "keep" ? "safe" : "safe";
                return (
                  <React.Fragment key={i}>
                    <tr>
                      <td>
                        <div className="col gap-4">
                          <div className="row gap-8">
                            <span style={{ fontWeight: 500 }}>{it.name}</span>
                            {it.status === "disabled" && <Pill kind="safe">disabled</Pill>}
                          </div>
                          <span className="mono faint" style={{ fontSize: 11 }}>{it.pub}</span>
                        </div>
                      </td>
                      <td>
                        <div className="col gap-4">
                          <span className="mono" style={{ fontSize: 12 }}>{it.cpu}%</span>
                          <Bar value={it.cpu * 12} max={100} />
                        </div>
                      </td>
                      <td className="size">{it.ram}</td>
                      <td>
                        <div className="col gap-4">
                          <span className="mono" style={{ fontSize: 12 }}>+{it.boot}s</span>
                          <Bar value={it.boot * 25} max={100} color={it.boot > 2 ? "var(--review)" : "var(--accent)"} />
                        </div>
                      </td>
                      <td><Pill kind={recKind}>{it.rec}</Pill></td>
                      <td><button className="btn btn-ghost" style={{ padding: "5px 10px", fontSize: 11 }}>Why?</button></td>
                    </tr>
                    {i === 2 && (
                      <tr className="expanded">
                        <td colSpan={6}>
                          <div className="row gap-14" style={{ alignItems: "flex-start", padding: "4px 0" }}>
                            <div className="col gap-4 grow">
                              <Eyebrow>Why this recommendation</Eyebrow>
                              <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--text)" }}>{it.why}</div>
                            </div>
                            <div className="col gap-6" style={{ minWidth: 220 }}>
                              <button className="btn">Copy command for Task Manager</button>
                              <button className="btn btn-ghost">Mark as keep</button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </Panel>

        <div className="row gap-10" style={{ marginTop: 14, fontSize: 11.5, color: "var(--text-dim)" }}>
          <span style={{ color: "var(--accent)", fontFamily: "IBM Plex Mono, monospace" }}>ℹ</span>
          <span>Vigil only reads startup state. Use Task Manager → Startup or Settings → Apps → Startup to apply changes.</span>
        </div>
      </div>
    </>
  );
}

function HistoryScreen({ expandedIdx = 1 }) {
  const sessions = [
    { d:"08 May 2026 · 17:42", target:"C:\\Users\\op\\AppData",     dur:"04:28", model:"llama3.2:3b", mode:"standard", findings:"2,418", cleaned:"—",     dist:[58,27,15] },
    { d:"06 May 2026 · 09:12", target:"D:\\Projects (recursive)",  dur:"02:04", model:"qwen2.5:7b",  mode:"technical",findings:"  812", cleaned:"3.1 GB", dist:[71,22, 7] },
    { d:"03 May 2026 · 22:08", target:"C:\\ (full system)",        dur:"08:51", model:"llama3.2:3b", mode:"standard", findings:"5,094", cleaned:"21.8 GB", dist:[64,24,12] },
    { d:"28 Apr 2026 · 14:31", target:"%TEMP% (quick)",            dur:"00:32", model:"—",            mode:"compact",  findings:"  140", cleaned:"1.9 GB",  dist:[100,0,0] },
    { d:"22 Apr 2026 · 11:09", target:"E:\\Downloads",              dur:"01:18", model:"llama3.2:3b", mode:"guided",   findings:"  287", cleaned:"6.4 GB",  dist:[40,45,15] },
  ];
  return (
    <>
      <Topbar
        crumb="HISTORY"
        sub="5 sessions · 33.2 GB cleaned cumulatively"
        right={
          <div className="row gap-8">
            <button className="btn btn-ghost">Compare selected</button>
            <button className="btn btn-ghost">Export log…</button>
          </div>
        }
      />
      <div className="content">
        {/* timeline strip */}
        <Panel title="Timeline" num="// last 30 days" className="mb">
          <div className="row gap-4" style={{ height: 64, alignItems: "flex-end" }}>
            {Array.from({ length: 30 }).map((_, i) => {
              const h = [4,4,4,4,18,4,4,4,4,4,4,4,4,4,30,4,4,4,4,4,4,52,4,4,4,4,4,40,4,86][i] || 4;
              const has = h > 4;
              return (
                <div key={i} title={"day " + (i+1)} style={{
                  flex: 1, height: h, background: has ? "var(--accent)" : "var(--panel-2)",
                  border: "1px solid " + (has ? "var(--accent)" : "var(--border)"),
                  opacity: has ? 1 : 0.5,
                }}></div>
              );
            })}
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <span className="mono faint" style={{ fontSize: 10 }}>30d ago</span>
            <span className="mono faint" style={{ fontSize: 10 }}>today</span>
          </div>
        </Panel>

        <div style={{ height: 16 }}></div>

        <Panel title="Sessions" num={"// " + sessions.length + " entries"}>
          <table className="tbl">
            <colgroup>
              <col style={{ width: 28 }}/><col style={{ width: 170 }}/><col/><col style={{ width: 80 }}/><col style={{ width: 130 }}/><col style={{ width: 100 }}/><col style={{ width: 200 }}/><col style={{ width: 110 }}/>
            </colgroup>
            <thead>
              <tr>
                <th></th>
                <th>When</th>
                <th>Target</th>
                <th>Duration</th>
                <th>Model</th>
                <th>Findings</th>
                <th>Distribution</th>
                <th style={{ textAlign: "right" }}>Cleaned</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s,i) => {
                const exp = i === expandedIdx;
                return (
                  <React.Fragment key={i}>
                    <tr className={exp ? "expanded" : ""}>
                      <td className="mono faint" style={{ fontSize: 10, textAlign: "center" }}>{exp ? "▾" : "▸"}</td>
                      <td className="mono" style={{ fontSize: 11.5 }}>{s.d}</td>
                      <td className="path">{s.target}</td>
                      <td className="size">{s.dur}</td>
                      <td className="mono faint" style={{ fontSize: 11 }}>{s.model}</td>
                      <td className="size">{s.findings}</td>
                      <td>
                        <div className="row" style={{ height: 6, border: "1px solid var(--border)", background: "var(--bg-deep)" }}>
                          <i style={{ height: "100%", width: s.dist[0] + "%", background: "var(--safe)" }}></i>
                          <i style={{ height: "100%", width: s.dist[1] + "%", background: "var(--review)" }}></i>
                          <i style={{ height: "100%", width: s.dist[2] + "%", background: "var(--risk)" }}></i>
                        </div>
                      </td>
                      <td className="size">{s.cleaned}</td>
                    </tr>
                    {exp && (
                      <tr className="expanded">
                        <td></td>
                        <td colSpan={7} style={{ paddingTop: 4, paddingBottom: 16 }}>
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 22, paddingTop: 4 }}>
                            <div className="col gap-4"><Eyebrow>Scanned paths</Eyebrow><span className="mono" style={{ fontSize: 12 }}>D:\Projects/<br/>D:\Projects\client-x/<br/>D:\Projects\sandbox/</span></div>
                            <div className="col gap-4"><Eyebrow>Explanation mode</Eyebrow><span className="mono" style={{ fontSize: 12 }}>{s.mode}</span><Eyebrow>AI processing</Eyebrow><span className="mono" style={{ fontSize: 12 }}>00:48 · 312 prompts</span></div>
                            <div className="col gap-4"><Eyebrow>Distribution</Eyebrow><span className="mono" style={{ fontSize: 12 }}>safe {s.dist[0]}% · review {s.dist[1]}% · risk {s.dist[2]}%</span><Eyebrow>Cleanup size</Eyebrow><span className="mono" style={{ fontSize: 12 }}>{s.cleaned}</span></div>
                            <div className="col gap-6">
                              <button className="btn">Re-open report</button>
                              <button className="btn btn-ghost">Re-run with same target</button>
                              <button className="btn btn-ghost">Export NDJSON</button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}

function SettingsScreen({ tab = "general" }) {
  const TABS = [
    { id:"general",   label:"General"   },
    { id:"ai",        label:"AI"        },
    { id:"scan",      label:"Scan"      },
    { id:"interface", label:"Interface" },
    { id:"about",     label:"About"     },
  ];
  return (
    <>
      <Topbar
        crumb="SETTINGS"
        sub="local · per-user · stored at %APPDATA%\\Vigil\\config.json"
        right={<button className="btn btn-ghost">Save changes</button>}
      />
      <div className="content">
        <div className="tabs" style={{ marginBottom: 18 }}>
          {TABS.map(t => (
            <div key={t.id} className={"tab" + (t.id === tab ? " active" : "")}>{t.label}</div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <Panel title="Appearance" num="// general">
            <SettingRow label="Theme" desc="Color palette for the entire workstation.">
              <div className="row gap-6">
                <Chip on>forest</Chip>
                <Chip>amber</Chip>
                <Chip>mono ink</Chip>
                <Chip>paper</Chip>
              </div>
            </SettingRow>
            <SettingRow label="Language" desc="UI strings and AI explanation language.">
              <select className="vg-select"><option>English (US)</option><option>Deutsch</option><option>Français</option><option>日本語</option></select>
            </SettingRow>
            <SettingRow label="Compact mode" desc="Tighter row spacing throughout tables.">
              <Toggle on={false} />
            </SettingRow>
            <SettingRow label="CRT scanlines" desc="Subtle terminal overlay. Off in light themes.">
              <Toggle on={true} />
            </SettingRow>
          </Panel>

          <Panel title="Local AI" num="// ai">
            <SettingRow label="Endpoint" desc="Ollama-compatible HTTP server. Local only.">
              <input className="vg-input mono" defaultValue="http://127.0.0.1:11434" />
            </SettingRow>
            <SettingRow label="Model" desc="Smaller models are faster but less nuanced.">
              <select className="vg-select"><option>llama3.2:3b · 2.0 GB</option><option>qwen2.5:7b · 4.4 GB</option><option>mistral:7b · 4.1 GB</option></select>
            </SettingRow>
            <SettingRow label="Explanation style" desc="Affects verbosity and terminology, not personality.">
              <div className="row gap-6">
                <Chip>compact</Chip>
                <Chip on>standard</Chip>
                <Chip>technical</Chip>
                <Chip>guided</Chip>
              </div>
            </SettingRow>
            <SettingRow label="Per-prompt timeout" desc="Skip explanation if model exceeds this.">
              <div className="row gap-10" style={{ alignItems: "center" }}>
                <input className="vg-range" type="range" min="2" max="30" defaultValue="8" style={{ width: 180 }}/>
                <span className="mono" style={{ fontSize: 12, width: 50, textAlign: "right" }}>8.0 s</span>
              </div>
            </SettingRow>
          </Panel>

          <Panel title="Scan Behavior" num="// scan">
            <SettingRow label="Protected paths" desc="Vigil refuses to flag anything beneath these.">
              <div className="col gap-4" style={{ alignItems: "stretch" }}>
                <div className="mono" style={{ background: "var(--panel-2)", border: "1px solid var(--border-2)", padding: "6px 8px", fontSize: 11 }}>C:\Users\op\Documents</div>
                <div className="mono" style={{ background: "var(--panel-2)", border: "1px solid var(--border-2)", padding: "6px 8px", fontSize: 11 }}>C:\Users\op\Desktop</div>
                <div className="mono" style={{ background: "var(--panel-2)", border: "1px solid var(--border-2)", padding: "6px 8px", fontSize: 11 }}>D:\Photos</div>
                <button className="btn btn-ghost" style={{ alignSelf: "flex-start", marginTop: 4 }}>+ add path</button>
              </div>
            </SettingRow>
            <SettingRow label="Ignore patterns" desc="Glob patterns excluded from every scan.">
              <input className="vg-input mono" defaultValue="**/.git/**, **/venv/**, **/.cache/**" />
            </SettingRow>
            <SettingRow label="Hidden / system files" desc="Include OS-protected entries in scans.">
              <Toggle on={false} />
            </SettingRow>
          </Panel>

          <Panel title="About" num="// about">
            <SettingRow label="Version" desc="">
              <span className="mono" style={{ fontSize: 12 }}>0.4.2 · build 2026-05-08</span>
            </SettingRow>
            <SettingRow label="Source" desc="">
              <a className="mono" style={{ color: "var(--accent)", fontSize: 12, textDecoration: "underline", textUnderlineOffset: 3 }}>github.com/vigil/vigil</a>
            </SettingRow>
            <SettingRow label="Diagnostics" desc="">
              <div className="row gap-6">
                <button className="btn btn-ghost">Export config</button>
                <button className="btn btn-ghost">View logs</button>
                <button className="btn btn-ghost">Reset settings</button>
              </div>
            </SettingRow>
            <div className="div"></div>
            <div className="mono faint" style={{ fontSize: 10.5, lineHeight: 1.6 }}>
              Vigil is local. No cloud. No telemetry. No background daemon.<br/>
              Settings live in a single JSON file you can copy between machines.
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

function SettingRow({ label, desc, children }) {
  return (
    <div className="row" style={{ gap: 18, padding: "10px 0", borderBottom: "1px dashed var(--border)", alignItems: "flex-start" }}>
      <div className="col gap-4" style={{ width: 200, flex: "0 0 200px", paddingTop: 4 }}>
        <span style={{ fontSize: 12.5, color: "var(--text)" }}>{label}</span>
        {desc ? <span className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>{desc}</span> : null}
      </div>
      <div className="grow" style={{ minWidth: 0 }}>{children}</div>
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

// inline form CSS once
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

Object.assign(window, { FindingsScreen, StartupsScreen, HistoryScreen, SettingsScreen });
