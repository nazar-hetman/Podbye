/* Vigil — design canvas wrapper, theme tweak panel, screen frame */

const VG_THEMES = [
  { id: "forest", label: "Forest", hint: "dark green tactical", swatch: ["#0e1612","#7cc596","#d8b46a","#d68a78"] },
  { id: "amber",  label: "Amber",  hint: "warm terminal",        swatch: ["#14100a","#e8b169","#b9c66e","#d27a5c"] },
  { id: "mono",   label: "Mono",   hint: "high-contrast ink",   swatch: ["#0a0a0a","#ffffff","#a8a8a8","#707070"] },
  { id: "paper",  label: "Paper",  hint: "light workstation",    swatch: ["#f1ece1","#3d6b48","#8e6a2c","#985041"] },
];

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "palette": "forest"
}/*EDITMODE-END*/;

function VigilApp({ initialActive = "home", theme = "forest", crt = true, focusScreen, children }) {
  const [active, setActive] = React.useState(initialActive);
  React.useEffect(() => { setActive(initialActive); }, [initialActive]);
  return (
    <div className={"vigil-app" + (crt ? " crt" : "")} data-theme={theme}>
      <TitleBar
        session={focusScreen ? focusScreen.session : "0008-2605"}
        scan={focusScreen ? focusScreen.scan : "llama3.2:3b"}
        theme={theme.toUpperCase()}
      />
      <div className="shell">
        <Sidebar active={active} onNav={setActive} />
        <div className="main">{children}</div>
      </div>
    </div>
  );
}

// Each artboard hosts a fixed-size Vigil app instance with one screen showing.
function ScreenArtboard({ active, theme, crt, render }) {
  return (
    <VigilApp initialActive={active} theme={theme} crt={crt}>
      {render(active)}
    </VigilApp>
  );
}

function LogoArtboard({ name, desc, mark, color, theme }) {
  return (
    <div className="logo-card" data-theme={theme} style={{ background: "var(--bg)" }}>
      <div className="logo-stage">
        {React.createElement(mark, { size: 110, color: color || "var(--accent)" })}
      </div>
      <div className="logo-meta">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <div className="logo-name">{name}</div>
          <div className="mono faint" style={{ fontSize: 10 }}>16 · 24 · 110</div>
        </div>
        <div className="logo-desc">{desc}</div>
        <div className="logo-row">
          {[16, 24, 36].map(s => (
            <div key={s} className="logo-mini">
              <span style={{ width: s, height: s, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                {React.createElement(mark, { size: s, color: color || "var(--accent)" })}
              </span>
              <span>{s}px</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function VigilCanvas() {
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const theme = tweaks.palette || "forest";

  // Render helpers per screen
  const renderScreen = (id) => {
    switch (id) {
      case "home":    return <HomeScreen />;
      case "quick":   return <QuickCleanupScreen />;
      case "analyze": return <AnalyzeScreen progress={64} />;
      case "find":    return <FindingsScreen />;
      case "start":   return <StartupsScreen />;
      case "hist":    return <HistoryScreen />;
      case "set":     return <SettingsScreen />;
      default:        return null;
    }
  };

  // Artboard size — all screens use the same 1280×820 frame
  const W = 1280, H = 820;

  return (
    <>
      <DesignCanvas
        title="Vigil — Local AI Analysis Workstation"
        subtitle="Stylized Qt6 desktop · forest default · amber / mono / paper alternates available via Tweaks"
      >
        <DCSection id="brand" title="Brand" subtitle="Four emblem candidates · grid-friendly · readable at 16px">
          <DCArtboard id="b-sentry" label="01 · Sentry" width={300} height={300}>
            <LogoArtboard
              name="SENTRY"
              desc="Square aperture enclosing a centered observer node. Reads as 'watching' without weapons or radar."
              mark={SentryMark}
              theme={theme}
            />
          </DCArtboard>
          <DCArtboard id="b-aperture" label="02 · Aperture" width={300} height={300}>
            <LogoArtboard
              name="APERTURE"
              desc="Corner registration marks frame an opening. Suggests calibration, system observation."
              mark={ApertureMark}
              theme={theme}
            />
          </DCArtboard>
          <DCArtboard id="b-sigil" label="03 · Sigil V" width={300} height={300}>
            <LogoArtboard
              name="SIGIL V"
              desc="Pixel-stacked V with a sentinel dot above. Letterform-as-mark; pairs with the wordmark."
              mark={SigilMark}
              theme={theme}
            />
          </DCArtboard>
          <DCArtboard id="b-lattice" label="04 · Lattice" width={300} height={300}>
            <LogoArtboard
              name="LATTICE"
              desc="3×3 node grid with one core node lit. Reads as system / network / locality."
              mark={LatticeMark}
              theme={theme}
            />
          </DCArtboard>
        </DCSection>

        <DCSection id="screens" title="Screens" subtitle="Seven panes of the application — sidebar nav consistent across">
          <DCArtboard id="s-home"    label="01 · Home"          width={W} height={H}><ScreenArtboard active="home"    theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-quick"   label="02 · Quick Cleanup" width={W} height={H}><ScreenArtboard active="quick"   theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-analyze" label="03 · Analyze (running)" width={W} height={H}><ScreenArtboard active="analyze" theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-find"    label="04 · Findings"      width={W} height={H}><ScreenArtboard active="find"    theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-start"   label="05 · Startups"      width={W} height={H}><ScreenArtboard active="start"   theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-hist"    label="06 · History"       width={W} height={H}><ScreenArtboard active="hist"    theme={theme} crt render={renderScreen} /></DCArtboard>
          <DCArtboard id="s-set"     label="07 · Settings"      width={W} height={H}><ScreenArtboard active="set"     theme={theme} crt render={renderScreen} /></DCArtboard>
        </DCSection>
      </DesignCanvas>

      <TweaksPanel title="Tweaks">
        <TweakSection title="Palette">
          {VG_THEMES.map(t => (
            <button
              key={t.id}
              onClick={() => setTweak("palette", t.id)}
              style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "10px 12px",
                width: "100%",
                textAlign: "left",
                background: theme === t.id ? "rgba(0,0,0,0.05)" : "transparent",
                border: "1px solid " + (theme === t.id ? "rgba(0,0,0,0.25)" : "rgba(0,0,0,0.08)"),
                borderRadius: 4,
                cursor: "pointer",
                marginBottom: 6,
                fontFamily: "inherit",
              }}
            >
              <span style={{
                display: "inline-flex", flexDirection: "column",
                width: 28, height: 28, borderRadius: 3, overflow: "hidden", flex: "0 0 28px",
                border: "1px solid rgba(0,0,0,0.08)",
              }}>
                <span style={{ height: 14, background: t.swatch[0] }}></span>
                <span style={{ display: "flex", height: 14 }}>
                  <span style={{ flex: 1, background: t.swatch[1] }}></span>
                  <span style={{ flex: 1, background: t.swatch[2] }}></span>
                  <span style={{ flex: 1, background: t.swatch[3] }}></span>
                </span>
              </span>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{t.label}</span>
                <span style={{ fontSize: 11, opacity: 0.6 }}>{t.hint}</span>
              </span>
              {theme === t.id && (
                <span style={{ marginLeft: "auto", fontFamily: "IBM Plex Mono, monospace", fontSize: 10, opacity: 0.6 }}>active</span>
              )}
            </button>
          ))}
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<VigilCanvas />);
