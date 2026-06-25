/* Vigil — screens A: Home, Quick Cleanup, Analyze */

function HomeScreen() {
  return (
    <>
      <Topbar
        crumb="HOME"
        sub="last session · 2026-05-08 17:42 · 4m 28s · llama3.2:3b"
        right={
          <div className="row gap-8">
            <button className="btn btn-ghost">Open last report</button>
            <button className="btn btn-primary">Start quick cleanup</button>
          </div>
        }
      />
      <div className="content">
        {/* Hero summary */}
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-h">
            <div className="row gap-10" style={{ alignItems: "baseline" }}>
              <span className="pt">Previous Session</span>
              <span className="pn">// 0008-2605</span>
            </div>
            <div className="row gap-10">
              <span className="pill safe">analysis complete</span>
              <span className="mono faint" style={{ fontSize: 11 }}>4m 28s · 187,420 items scanned</span>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 0 }}>
            <div style={{ padding: "22px 24px", borderRight: "1px solid var(--border)" }}>
              <Eyebrow>Reclaimable</Eyebrow>
              <div style={{ height: 8 }}></div>
              <Bignum value="14.6" unit="GB" size={64} />
              <div style={{ height: 14 }}></div>
              <SegBar segs={[
                { pct: 58, color: "var(--safe)" },
                { pct: 27, color: "var(--review)" },
                { pct: 15, color: "var(--risk)" },
              ]} />
              <div className="row gap-14" style={{ marginTop: 10, fontSize: 11 }}>
                <span className="row gap-6"><span style={{ width: 8, height: 8, background: "var(--safe)" }}></span><span className="muted">Safe</span><span className="mono faint">8.4 GB</span></span>
                <span className="row gap-6"><span style={{ width: 8, height: 8, background: "var(--review)" }}></span><span className="muted">Review</span><span className="mono faint">3.9 GB</span></span>
                <span className="row gap-6"><span style={{ width: 8, height: 8, background: "var(--risk)" }}></span><span className="muted">Risky</span><span className="mono faint">2.3 GB</span></span>
              </div>
            </div>
            <div style={{ padding: "22px 24px", borderRight: "1px solid var(--border)" }}>
              <Eyebrow>Findings</Eyebrow>
              <div style={{ height: 8 }}></div>
              <Bignum value="2,418" size={48} />
              <div className="muted" style={{ marginTop: 12, fontSize: 12, lineHeight: 1.6 }}>
                across <span className="mono">7</span> categories<br/>
                <span className="mono faint">Cache · Media · Archives · Browser · Duplicates · Dev · Logs</span>
              </div>
            </div>
            <div style={{ padding: "22px 24px" }}>
              <Eyebrow>AI Processing</Eyebrow>
              <div style={{ height: 8 }}></div>
              <Bignum value="1,083" size={48} />
              <div className="muted" style={{ marginTop: 12, fontSize: 12, lineHeight: 1.6 }}>
                explanations generated<br/>
                <span className="mono faint">avg 0.21s · standard mode</span>
              </div>
            </div>
          </div>
        </div>

        {/* Continue / Quick actions */}
        <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16 }}>
          <Panel title="Continue" num="// 03 sessions">
            <div className="col gap-10">
              {[
                { d:"08 May · 17:42", p:"C:\\Users\\op\\AppData\\Local",  size:"14.6 GB", state:"awaiting review", k:"review" },
                { d:"06 May · 09:12", p:"D:\\Projects\\— recursive",     size:"3.1 GB",  state:"cleaned",         k:"safe"   },
                { d:"03 May · 22:08", p:"C:\\ — full system scan",       size:"21.8 GB", state:"partial · halted", k:"risk"   },
              ].map((r, i) => (
                <div key={i} className="row gap-14" style={{ padding: "10px 12px", border: "1px solid var(--border)", background: "var(--panel-2)" }}>
                  <div style={{ width: 78 }} className="mono faint" >{r.d}</div>
                  <div className="grow mono" style={{ fontSize: 12, color: "var(--text)" }}>{r.p}</div>
                  <div className="mono" style={{ width: 80, textAlign: "right" }}>{r.size}</div>
                  <Pill kind={r.k}>{r.state}</Pill>
                  <button className="btn btn-ghost" style={{ padding: "5px 10px" }}>Open ▸</button>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Begin Analysis" num="// pick a target">
            <div className="col gap-10">
              <button className="btn btn-primary" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
                <span className="row gap-10">
                  <span style={{ width: 16, height: 16, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                    <NavIco name="bolt"/>
                  </span>
                  Quick Cleanup
                </span>
                <span className="mono" style={{ fontSize: 10, opacity: .8 }}>~ 30s</span>
              </button>
              <button className="btn" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
                <span className="row gap-10"><span style={{ width: 16, height: 16, display: "inline-flex", alignItems: "center", justifyContent: "center" }}><NavIco name="scan"/></span>Analyze a folder…</span>
                <span className="mono faint" style={{ fontSize: 10 }}>⌘O</span>
              </button>
              <button className="btn" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
                <span className="row gap-10"><span style={{ width: 16, height: 16, display: "inline-flex", alignItems: "center", justifyContent: "center" }}><NavIco name="list"/></span>Analyze a drive…</span>
                <span className="mono faint" style={{ fontSize: 10 }}>⌘D</span>
              </button>
              <button className="btn btn-ghost" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
                <span>Custom paths…</span>
                <span className="mono faint" style={{ fontSize: 10 }}>3 saved</span>
              </button>
              <div className="div"></div>
              <div className="mono faint" style={{ fontSize: 10.5, lineHeight: 1.55 }}>
                Vigil runs only when launched.<br/>
                No background daemon · no scheduled scans.
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

function QuickCleanupScreen() {
  const cats = [
    { id:"tmp",  title:"Temp Files",        path:"%TEMP%, C:\\Windows\\Temp",         items:14728, size:"4.2 GB",   conf:99 },
    { id:"brc",  title:"Browser Cache",     path:"Edge · Chrome · Firefox",            items: 8412, size:"2.7 GB",   conf:98 },
    { id:"rec",  title:"Recycle Bin",       path:"$Recycle.Bin (all drives)",          items:  127, size:"1.4 GB",   conf:100 },
    { id:"thm",  title:"Thumbnail Cache",   path:"C:\\Users\\…\\Explorer",              items:  482, size:"  186 MB", conf:100 },
    { id:"log",  title:"Old Logs (30d+)",   path:"%PROGRAMDATA%, app logs",            items: 1294, size:"  342 MB", conf:96  },
    { id:"upd",  title:"Windows Update Cache", path:"C:\\Windows\\SoftwareDistribution",  items:   38, size:"1.8 GB",   conf:97 },
  ];
  return (
    <>
      <Topbar
        crumb="QUICK CLEANUP"
        sub="confidence-based · safe categories only"
        right={
          <div className="row gap-8">
            <span className="mono faint" style={{ fontSize: 11 }}>5 of 6 selected</span>
            <button className="btn btn-ghost">Customize</button>
            <button className="btn btn-primary">Clean 10.6 GB</button>
          </div>
        }
      />
      <div className="content">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16 }}>
          <Panel title="Safe Categories" num="// 6 detected · 10.6 GB ready">
            <div className="col" style={{ gap: 1, background: "var(--border)" }}>
              {cats.map((c, i) => (
                <div key={c.id} className="row gap-14" style={{ background: "var(--panel)", padding: "13px 14px", alignItems: "center" }}>
                  <Cb on={c.id !== "upd"} />
                  <div className="col gap-4 grow">
                    <div className="row gap-8" style={{ alignItems: "baseline" }}>
                      <span style={{ fontWeight: 500 }}>{c.title}</span>
                      <span className="mono faint" style={{ fontSize: 11 }}>· {c.items.toLocaleString()} items</span>
                    </div>
                    <div className="mono faint" style={{ fontSize: 11 }}>{c.path}</div>
                  </div>
                  <div className="col gap-4" style={{ width: 120, alignItems: "flex-end" }}>
                    <span className="mono" style={{ fontSize: 13 }}>{c.size}</span>
                    <span className="mono faint" style={{ fontSize: 10 }}>confidence {c.conf}%</span>
                  </div>
                  <div style={{ width: 100 }}>
                    <Bar value={c.conf} max={100} color={c.conf >= 99 ? "var(--safe)" : "var(--accent)"} />
                  </div>
                </div>
              ))}
            </div>
          </Panel>

          <div className="col gap-16">
            <Panel title="Will Clean" num="// summary">
              <Eyebrow>Total Reclaimable</Eyebrow>
              <div style={{ height: 6 }}></div>
              <Bignum value="10.6" unit="GB" size={56} />
              <div className="div"></div>
              <div className="col gap-8" style={{ fontSize: 12 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="muted">items removed</span><span className="mono">25,041</span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="muted">categories</span><span className="mono">5 of 6</span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="muted">est. duration</span><span className="mono">~ 28s</span>
                </div>
              </div>
            </Panel>

            <Panel title="Guarantees" num="">
              <div className="col gap-10" style={{ fontSize: 12, lineHeight: 1.55 }}>
                <div className="row gap-8">
                  <span style={{ color: "var(--safe)", fontFamily: "IBM Plex Mono, monospace" }}>✓</span>
                  <span>Universally safe categories only — no app data, no documents.</span>
                </div>
                <div className="row gap-8">
                  <span style={{ color: "var(--safe)", fontFamily: "IBM Plex Mono, monospace" }}>✓</span>
                  <span>Protected paths cannot be selected here, ever.</span>
                </div>
                <div className="row gap-8">
                  <span style={{ color: "var(--safe)", fontFamily: "IBM Plex Mono, monospace" }}>✓</span>
                  <span>Removed items go to the Recycle Bin where the OS allows.</span>
                </div>
                <div className="div"></div>
                <div className="muted" style={{ fontSize: 11 }}>
                  Need deeper review? <span style={{ color: "var(--accent)", textDecoration: "underline", textUnderlineOffset: 2 }}>Switch to Analyze ▸</span>
                </div>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </>
  );
}

function AnalyzeScreen({ progress = 64 }) {
  const stages = [
    { id:"enum",  label:"Enumerate paths",     state:"done",      pct:100 },
    { id:"scan",  label:"Scan & hash",         state:"done",      pct:100 },
    { id:"group", label:"Group & cluster",     state:"running",   pct:72 },
    { id:"risk",  label:"Risk estimation",     state:"running",   pct:38 },
    { id:"ai",    label:"AI explanation pass", state:"queued",    pct:0 },
    { id:"rep",   label:"Compose report",     state:"queued",    pct:0 },
  ];
  const partial = [
    { cat:"Cache & Temp",   n:"6,218",  size:"3.9 GB" },
    { cat:"Browser Data",   n:"2,401",  size:"2.1 GB" },
    { cat:"Duplicates",     n:"  874",  size:"1.4 GB" },
    { cat:"Dev Artifacts",  n:"  331",  size:"  908 MB" },
    { cat:"Archives",       n:"   88",  size:"  612 MB" },
    { cat:"System Logs",    n:"  712",  size:"  204 MB" },
  ];
  return (
    <>
      <Topbar
        crumb="ANALYZE"
        sub={"target · C:\\Users\\op\\AppData · recursive · " + progress + "% complete"}
        right={
          <div className="row gap-8">
            <span className="pill review">running</span>
            <button className="btn btn-ghost">Pause</button>
            <button className="btn">Halt</button>
          </div>
        }
      />
      <div className="content" style={{ paddingBottom: 14 }}>
        {/* Pipeline header */}
        <div className="panel" style={{ marginBottom: 16 }}>
          <div style={{ padding: "16px 18px", display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 22, alignItems: "center" }}>
            <div className="col gap-4">
              <Eyebrow>Pipeline</Eyebrow>
              <div className="bignum mono" style={{ fontSize: 28 }}>{progress}<span className="unit">%</span></div>
            </div>
            <div className="col gap-10" style={{ minWidth: 0 }}>
              <Bar value={progress} max={100} />
              <div className="row gap-4" style={{ flexWrap: "wrap" }}>
                {stages.map((s) => (
                  <div key={s.id} className="row gap-8"
                       style={{ flex: "1 1 130px", padding: "8px 10px",
                                background: s.state === "running" ? "var(--accent-soft)" : "var(--panel-2)",
                                border: "1px solid " + (s.state === "running" ? "var(--accent)" : "var(--border)") }}>
                    <span className="mono faint" style={{ fontSize: 10, width: 18, textAlign: "right" }}>
                      {s.state === "done" ? "✓" : s.state === "running" ? "▸" : "·"}
                    </span>
                    <div className="col gap-4 grow" style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 11.5, color: s.state === "queued" ? "var(--text-faint)" : "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.label}</div>
                      <Bar value={s.pct} max={100} color={s.state === "done" ? "var(--safe)" : "var(--accent)"} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="col gap-6" style={{ alignItems: "flex-end" }}>
              <Eyebrow>elapsed</Eyebrow>
              <div className="mono" style={{ fontSize: 16 }}>00:02:47</div>
              <Eyebrow>remaining</Eyebrow>
              <div className="mono faint" style={{ fontSize: 13 }}>~ 01:38</div>
            </div>
          </div>
        </div>

        {/* Lower pane: partial findings + live log */}
        <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 16 }}>
          <Panel title="Partial Findings" num="// updating live" right={<span className="mono faint" style={{ fontSize: 11 }}>{partial.reduce((a,c)=>a+parseInt(c.n.replace(/[, ]/g,"")),0).toLocaleString()} items</span>}>
            <table className="tbl">
              <thead>
                <tr><th>Category</th><th style={{ textAlign: "right" }}>Items</th><th style={{ textAlign: "right" }}>Size</th><th>Confidence</th></tr>
              </thead>
              <tbody>
                {partial.map((p,i) => (
                  <tr key={i}>
                    <td>{p.cat}</td>
                    <td className="size">{p.n}</td>
                    <td className="size">{p.size}</td>
                    <td>
                      <div className="row gap-8" style={{ alignItems: "center" }}>
                        <div style={{ width: 90 }}><Bar value={70 + (i * 4)} max={100}/></div>
                        <span className="mono faint" style={{ fontSize: 10 }}>{70 + (i * 4)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <Panel title="Operator Feed" num="// stdout" bodyStyle={{ padding: 0 }} style={{ display: "flex", flexDirection: "column" }}>
            <div className="feed" id="vigil-feed">
              <div><span className="ts">02:47.114</span><span className="lv-i">cluster-engine</span> · grouped 874 candidate duplicates (sha256)</div>
              <div><span className="ts">02:47.342</span><span className="lv-i">risk-est</span> · evaluating C:\Users\op\AppData\Local\Packages</div>
              <div><span className="ts">02:47.408</span><span className="lv-w">protected</span> · skipped 14 items in keychain.db</div>
              <div><span className="ts">02:47.611</span><span className="lv-i">enum</span> · ndjson flush · 4,096 records</div>
              <div><span className="ts">02:47.802</span><span className="lv-ok">cleared</span> · cache.firefox/* (2.1 GB safe)</div>
              <div><span className="ts">02:47.918</span><span className="lv-i">ai/llama3.2:3b</span> · prompt 312 ▸ explanation 0.21s</div>
              <div><span className="ts">02:48.114</span><span className="lv-i">ai/llama3.2:3b</span> · prompt 313 ▸ explanation 0.18s</div>
              <div><span className="ts">02:48.224</span><span className="lv-i">cluster-engine</span> · merged 88 archive shards into 1 group</div>
              <div><span className="ts">02:48.337</span><span className="lv-i">risk-est</span> · low-confidence: ProgramData/intel/...  (review)</div>
              <div><span className="ts">02:48.501</span><span className="lv-i">enum</span> · descended D:\Projects\node_modules (depth 6)</div>
              <div><span className="ts">02:48.612</span><span className="lv-w">large file</span> · 2.4 GB iso · last access 412d ago</div>
              <div><span className="ts">02:48.800</span><span className="lv-i">ai/llama3.2:3b</span> · explanation queued · 312 pending</div>
              <div><span className="ts">02:48.972</span><span className="lv-ok">cleared</span> · thumbcache_*.db · 186 MB</div>
              <div><span className="ts">02:49.103</span><span className="lv-i">cluster-engine</span> · running similarity pass on media/*</div>
              <div><span className="ts">02:49.221</span><span className="lv-i">ai/llama3.2:3b</span> · explanation 0.22s · safe</div>
              <div><span className="ts">02:49.380</span><span className="lv-i">enum</span> · 187,420 / ~ 213,000 entries</div>
              <div><span className="ts">02:49.501</span><span className="lv-i">risk-est</span> · risk score 0.18 → safe (browser cache)</div>
              <div><span className="ts">02:49.660</span><span className="lv-i">▸</span> waiting for chunk…</div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

Object.assign(window, { HomeScreen, QuickCleanupScreen, AnalyzeScreen });
