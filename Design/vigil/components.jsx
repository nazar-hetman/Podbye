/* Vigil — shared chrome and primitives */

const VG_NAV = [
  { id: "home",    label: "Home",          ico: "home",     key: "1" },
  { id: "quick",   label: "Quick Cleanup", ico: "bolt",     key: "2" },
  { id: "analyze", label: "Analyze",       ico: "scan",     key: "3" },
  { id: "find",    label: "Findings",      ico: "list",     key: "4" },
  { id: "start",   label: "Startups",      ico: "power",    key: "5" },
  { id: "hist",    label: "History",       ico: "clock",    key: "6" },
  { id: "set",     label: "Settings",      ico: "gear",     key: "7" },
];

// Tiny stroke icons. 14×14 with 1.4 stroke. Calm, not military.
function NavIco({ name }) {
  const c = "currentColor";
  const sw = 1.4;
  const wrap = (k) => (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
         stroke={c} strokeWidth={sw} strokeLinecap="square" strokeLinejoin="miter">{k}</svg>
  );
  switch (name) {
    case "home":  return wrap(<><path d="M2.5 7.5 L8 3 L13.5 7.5"/><path d="M3.5 7 V13 H12.5 V7"/></>);
    case "bolt":  return wrap(<path d="M9 2 L4 9 H8 L7 14 L12 7 H8 Z"/>);
    case "scan":  return wrap(<><path d="M2.5 5V3h2"/><path d="M11.5 3h2v2"/><path d="M2.5 11v2h2"/><path d="M11.5 13h2v-2"/><path d="M2.5 8h11"/></>);
    case "list":  return wrap(<><path d="M3 4h10"/><path d="M3 8h10"/><path d="M3 12h7"/></>);
    case "power": return wrap(<><path d="M8 2v5"/><path d="M5 4.5a4.5 4.5 0 1 0 6 0"/></>);
    case "clock": return wrap(<><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.5"/></>);
    case "gear":  return wrap(<><circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4"/></>);
    default: return null;
  }
}

function TitleBar({ session, scan, theme }) {
  return (
    <div className="titlebar">
      <div className="tb-dots">
        <span className="tb-dot"></span>
        <span className="tb-dot"></span>
        <span className="tb-dot"></span>
      </div>
      <div className="tb-title pixel">VIGIL · LOCAL ANALYSIS WORKSTATION</div>
      <div className="tb-meta">
        <span><b>SESSION</b> {session}</span>
        <span><b>MODEL</b> {scan}</span>
        <span><b>THEME</b> {theme}</span>
      </div>
    </div>
  );
}

function Sidebar({ active, onNav, build = "v0.4.2 · local" }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <SentryMark size={26} />
        <div className="col gap-4">
          <div className="wordmark">VIGIL</div>
          <div className="build">{build}</div>
        </div>
      </div>
      <div className="nav-section">Workstation</div>
      {VG_NAV.slice(0, 6).map(n => (
        <div key={n.id}
             className={"nav-item" + (active === n.id ? " active" : "")}
             onClick={() => onNav && onNav(n.id)}>
          <span className="nav-ico"><NavIco name={n.ico} /></span>
          <span>{n.label}</span>
          <span className="nav-key">⌘{n.key}</span>
        </div>
      ))}
      <div className="nav-section">System</div>
      {VG_NAV.slice(6).map(n => (
        <div key={n.id}
             className={"nav-item" + (active === n.id ? " active" : "")}
             onClick={() => onNav && onNav(n.id)}>
          <span className="nav-ico"><NavIco name={n.ico} /></span>
          <span>{n.label}</span>
          <span className="nav-key">⌘{n.key}</span>
        </div>
      ))}
      <div className="sidebar-foot">
        <div><span className="dot-led"></span>OPERATOR · IDLE</div>
        <div>endpoint · 127.0.0.1:11434</div>
        <div>model · llama3.2:3b</div>
      </div>
    </aside>
  );
}

function Topbar({ crumb, sub, right }) {
  return (
    <div className="topbar">
      <div className="col gap-4">
        <div className="crumb">{crumb}</div>
        {sub ? <div className="crumb-sub">{sub}</div> : null}
      </div>
      <div className="spacer"></div>
      {right}
    </div>
  );
}

function Panel({ title, num, right, children, style, bodyStyle, className }) {
  return (
    <div className={"panel " + (className || "")} style={style}>
      {(title || num || right) && (
        <div className="panel-h">
          <div className="row gap-10" style={{ alignItems: "baseline" }}>
            {title ? <span className="pt">{title}</span> : null}
            {num ? <span className="pn">{num}</span> : null}
          </div>
          {right}
        </div>
      )}
      <div className="panel-b" style={bodyStyle}>{children}</div>
    </div>
  );
}

function Pill({ kind = "safe", children }) { return <span className={"pill " + kind}>{children}</span>; }

function Bignum({ value, unit, size = 56 }) {
  return (
    <div className="bignum" style={{ fontSize: size }}>
      {value}{unit ? <span className="unit">{unit}</span> : null}
    </div>
  );
}

function Eyebrow({ children }) { return <div className="label-eyebrow">{children}</div>; }

function Bar({ value, max = 100, color }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar"><i style={{ width: pct + "%", background: color || "var(--accent)" }}></i></div>
  );
}

function SegBar({ segs }) {
  // segs: [{ pct, color }]
  return (
    <div className="bar-seg">
      {segs.map((s, i) => (
        <i key={i} style={{ width: s.pct + "%", background: s.color }}></i>
      ))}
    </div>
  );
}

function Chip({ on, count, dot, children, onClick }) {
  return (
    <span className={"chip" + (on ? " on" : "")} onClick={onClick}>
      {dot ? <span className="swatch" style={{ background: dot }}></span> : null}
      <span>{children}</span>
      {count != null ? <span className="ct">{count}</span> : null}
    </span>
  );
}

function Cb({ on, onChange }) {
  return <span className={"cb" + (on ? " on" : "")} onClick={onChange}></span>;
}

// --- LOGO MARKS ---
// All marks are pixel-grid friendly and read at 16x16.

function SentryMark({ size = 32, color = "var(--accent)" }) {
  // "Sentry" — square aperture with center observer
  const s = size;
  return (
    <svg width={s} height={s} viewBox="0 0 16 16" shapeRendering="crispEdges" fill="none">
      <rect x="1" y="1" width="14" height="14" stroke={color} strokeWidth="1"/>
      <rect x="3" y="3" width="10" height="10" stroke={color} strokeWidth="1" opacity="0.55"/>
      <rect x="7" y="7" width="2" height="2" fill={color}/>
    </svg>
  );
}

function ApertureMark({ size = 32, color = "var(--accent)" }) {
  // Concentric squares + corner marks
  const s = size;
  return (
    <svg width={s} height={s} viewBox="0 0 16 16" shapeRendering="crispEdges" fill="none">
      <rect x="0" y="0" width="2" height="2" fill={color}/>
      <rect x="14" y="0" width="2" height="2" fill={color}/>
      <rect x="0" y="14" width="2" height="2" fill={color}/>
      <rect x="14" y="14" width="2" height="2" fill={color}/>
      <rect x="3" y="3" width="10" height="10" stroke={color} strokeWidth="1"/>
      <rect x="6" y="6" width="4" height="4" fill={color} opacity="0.85"/>
    </svg>
  );
}

function SigilMark({ size = 32, color = "var(--accent)" }) {
  // Pixel V with a sentry dot above
  const s = size;
  return (
    <svg width={s} height={s} viewBox="0 0 16 16" shapeRendering="crispEdges" fill="none">
      <rect x="7" y="1" width="2" height="2" fill={color}/>
      <rect x="2" y="5" width="2" height="2" fill={color}/>
      <rect x="12" y="5" width="2" height="2" fill={color}/>
      <rect x="3" y="7" width="2" height="2" fill={color}/>
      <rect x="11" y="7" width="2" height="2" fill={color}/>
      <rect x="4" y="9" width="2" height="2" fill={color}/>
      <rect x="10" y="9" width="2" height="2" fill={color}/>
      <rect x="5" y="11" width="2" height="2" fill={color}/>
      <rect x="9" y="11" width="2" height="2" fill={color}/>
      <rect x="7" y="13" width="2" height="2" fill={color}/>
    </svg>
  );
}

function LatticeMark({ size = 32, color = "var(--accent)" }) {
  // 3x3 lattice with one node lit
  const s = size;
  const cells = [];
  const positions = [[1,1],[6,1],[11,1],[1,6],[6,6],[11,6],[1,11],[6,11],[11,11]];
  positions.forEach(([x,y], i) => {
    const lit = i === 4;
    cells.push(
      <rect key={i} x={x} y={y} width="3" height="3"
        fill={lit ? color : "transparent"}
        stroke={color}
        strokeWidth="1"
        opacity={lit ? 1 : 0.55}/>
    );
  });
  return (
    <svg width={s} height={s} viewBox="0 0 16 16" shapeRendering="crispEdges" fill="none">
      {cells}
    </svg>
  );
}

Object.assign(window, {
  VG_NAV, NavIco, TitleBar, Sidebar, Topbar, Panel, Pill, Bignum, Eyebrow,
  Bar, SegBar, Chip, Cb,
  SentryMark, ApertureMark, SigilMark, LatticeMark,
});
