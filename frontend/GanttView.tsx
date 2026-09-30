import { useMemo, useRef, useState } from "react";
import { pickDrivingRel, tracePath } from "./pathTrace";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};

type Zoom = "day" | "week" | "month" | "quarter";
const PX_PER_DAY: Record<Zoom, number> = { day: 32, week: 10, month: 3.4, quarter: 1.15 };
const ROW_HEIGHT = 30;
const HEADER_HEIGHT = 44;
const VIEWPORT_HEIGHT = 560;
const BUFFER_ROWS = 8;

const STATUS_COLORS: Record<string, string> = {
  TK_Complete: C.green, TK_Active: "#3b82f6", TK_NotStart: C.muted2,
};

function parseD(v: any): Date | null {
  if (!v) return null;
  if (v instanceof Date) return v;
  const s = String(v).slice(0, 10);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}
function daysBetween(a: Date, b: Date): number { return Math.round((b.getTime() - a.getTime()) / 86400000); }
function fmtShort(d: Date | null): string { return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—"; }

function riskColorFor(a: any): string {
  const f = a.totalFloat;
  if (a.isMilestone) return C.purple;
  if (f == null) return C.green;
  if (f < 0) return C.red;
  if (f <= 5) return C.amber;
  return "#3b82f6";
}

function colorFor(a: any, mode: string): string {
  if (mode === "status") return STATUS_COLORS[a.status] || C.muted2;
  if (mode === "risk") return riskColorFor(a);
  const key = a[mode];
  if (!key) return C.muted2;
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return `hsl(${hue}, 55%, 45%)`;
}

const ALL_COLUMNS: { key: string; label: string; default: boolean }[] = [
  { key: "code", label: "Activity ID", default: true },
  { key: "name", label: "Activity Name", default: true },
  { key: "wbs", label: "WBS", default: true },
  { key: "bStart", label: "Start", default: true },
  { key: "bFinish", label: "Finish", default: true },
  { key: "totalFloat", label: "Total Float", default: true },
  { key: "pctComplete", label: "% Complete", default: true },
  { key: "dur", label: "Original Duration", default: false },
  { key: "remainDur", label: "Remaining Duration", default: false },
  { key: "status", label: "Status", default: false },
  { key: "area", label: "Area", default: false },
  { key: "discipline", label: "Discipline", default: false },
  { key: "contractor", label: "Contractor", default: false },
  { key: "system", label: "System", default: false },
];

function cellValue(a: any, key: string): string {
  if (key === "bStart" || key === "bFinish") return fmtShort(parseD(a[key]));
  if (key === "totalFloat") return a[key] == null ? "—" : `${a[key]}d`;
  if (key === "pctComplete") return `${Math.round(a[key] || 0)}%`;
  if (key === "dur" || key === "remainDur") return a[key] != null ? `${a[key]}d` : "—";
  return a[key] || "—";
}

export default function GanttView({ allActivities, dataDate, onGoToActivity }: any) {
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState<Zoom>("week");
  const [colorBy, setColorBy] = useState<string>("risk");
  const [collapsedWbs, setCollapsedWbs] = useState<Set<string>>(new Set());
  const [visibleCols, setVisibleCols] = useState<Set<string>>(new Set(ALL_COLUMNS.filter(c => c.default).map(c => c.key)));
  const [colPickerOpen, setColPickerOpen] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [flagFilters, setFlagFilters] = useState<{ critical: boolean; nearCritical: boolean; negativeFloat: boolean; milestones: boolean }>({
    critical: false, nearCritical: false, negativeFloat: false, milestones: false,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scrollTop, setScrollTop] = useState(0);

  const actMap = useMemo(() => {
    const m: Record<string, any> = {};
    (allActivities || []).forEach((a: any) => { m[a.id || a.code] = a; });
    return m;
  }, [allActivities]);

  const distinctValues = useMemo(() => {
    const out: Record<string, Set<string>> = { wbs: new Set(), area: new Set(), discipline: new Set(), contractor: new Set(), system: new Set() };
    (allActivities || []).forEach((a: any) => {
      for (const k of Object.keys(out)) if (a[k]) out[k].add(a[k]);
    });
    return out;
  }, [allActivities]);

  const filtered = useMemo(() => {
    let pool = allActivities || [];
    if (search.trim()) {
      const q = search.toLowerCase();
      pool = pool.filter((a: any) => (a.code || "").toLowerCase().includes(q) || (a.name || "").toLowerCase().includes(q));
    }
    for (const field of ["wbs", "area", "discipline", "contractor", "system", "status"]) {
      if (filters[field]) pool = pool.filter((a: any) => a[field] === filters[field]);
    }
    if (flagFilters.critical) pool = pool.filter((a: any) => a.isCritical && !a.isMilestone);
    if (flagFilters.nearCritical) pool = pool.filter((a: any) => a.totalFloat != null && a.totalFloat > 0 && a.totalFloat <= 5);
    if (flagFilters.negativeFloat) pool = pool.filter((a: any) => a.totalFloat != null && a.totalFloat < 0);
    if (flagFilters.milestones) pool = pool.filter((a: any) => a.isMilestone);
    return pool;
  }, [allActivities, search, filters, flagFilters]);

  // Single-level WBS grouping — real hierarchy (wbsPath) is honoured when
  // present (XER imports), otherwise activities group flatly by their wbs string.
  const groups = useMemo(() => {
    const byWbs = new Map<string, any[]>();
    for (const a of filtered) {
      const key = a.wbs || "Unassigned";
      if (!byWbs.has(key)) byWbs.set(key, []);
      byWbs.get(key)!.push(a);
    }
    return Array.from(byWbs.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  // Flatten into display rows: a WBS header row followed by its activities
  // (unless collapsed) — this flat list is what gets virtualized.
  type Row = { type: "wbs"; key: string; count: number } | { type: "act"; activity: any };
  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    for (const [wbs, acts] of groups) {
      out.push({ type: "wbs", key: wbs, count: acts.length });
      if (!collapsedWbs.has(wbs)) for (const a of acts) out.push({ type: "act", activity: a });
    }
    return out;
  }, [groups, collapsedWbs]);

  const { rangeStart, rangeEnd } = useMemo(() => {
    let min: Date | null = null, max: Date | null = null;
    for (const a of filtered) {
      const s = parseD(a.bStart), f = parseD(a.bFinish);
      if (s && (!min || s < min)) min = s;
      if (f && (!max || f > max)) max = f;
    }
    const dd = parseD(dataDate) || new Date();
    if (!min) min = dd;
    if (!max) max = dd;
    min = new Date(min.getFullYear(), min.getMonth(), min.getDate() - 14);
    max = new Date(max.getFullYear(), max.getMonth(), max.getDate() + 14);
    return { rangeStart: min, rangeEnd: max };
  }, [filtered, dataDate]);

  const pxPerDay = PX_PER_DAY[zoom];
  const totalWidth = Math.max(600, daysBetween(rangeStart, rangeEnd) * pxPerDay);
  const xFor = (d: Date | null) => d ? daysBetween(rangeStart, d) * pxPerDay : 0;

  const headerTicks = useMemo(() => {
    const ticks: { label: string; left: number; width: number }[] = [];
    const totalDays = daysBetween(rangeStart, rangeEnd);
    if (zoom === "day" || zoom === "week") {
      let cur = new Date(rangeStart.getFullYear(), rangeStart.getMonth(), 1);
      while (cur < rangeEnd) {
        const next = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
        const left = Math.max(0, daysBetween(rangeStart, cur)) * pxPerDay;
        const width = (daysBetween(cur < rangeStart ? rangeStart : cur, next > rangeEnd ? rangeEnd : next)) * pxPerDay;
        ticks.push({ label: cur.toLocaleDateString("en-US", { month: "short", year: "2-digit" }), left, width });
        cur = next;
      }
    } else if (zoom === "month") {
      let cur = new Date(rangeStart.getFullYear(), rangeStart.getMonth(), 1);
      while (cur < rangeEnd) {
        const next = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
        const left = Math.max(0, daysBetween(rangeStart, cur)) * pxPerDay;
        const width = daysBetween(cur < rangeStart ? rangeStart : cur, next > rangeEnd ? rangeEnd : next) * pxPerDay;
        ticks.push({ label: cur.toLocaleDateString("en-US", { month: "short", year: "2-digit" }), left, width });
        cur = next;
      }
    } else {
      let yr = rangeStart.getFullYear();
      while (new Date(yr, 0, 1) < rangeEnd) {
        for (let q = 0; q < 4; q++) {
          const qs = new Date(yr, q * 3, 1), qe = new Date(yr, q * 3 + 3, 1);
          if (qe < rangeStart || qs > rangeEnd) continue;
          const left = Math.max(0, daysBetween(rangeStart, qs)) * pxPerDay;
          const width = daysBetween(qs < rangeStart ? rangeStart : qs, qe > rangeEnd ? rangeEnd : qe) * pxPerDay;
          ticks.push({ label: `Q${q + 1} ${yr}`, left, width });
        }
        yr++;
      }
    }
    void totalDays;
    return ticks;
  }, [rangeStart, rangeEnd, zoom, pxPerDay]);

  // The vertical line marks the active schedule's own effective Data Date —
  // never drawn at today's position when no real Data Date is available,
  // so it never misrepresents today as the statusing cutoff.
  const ddDate = parseD(dataDate);
  const todayX = ddDate ? xFor(ddDate) : null;

  // ── Virtualization: only render rows within the scrolled viewport (+ buffer) ──
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - BUFFER_ROWS);
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + BUFFER_ROWS * 2;
  const endIdx = Math.min(rows.length, startIdx + visibleCount);
  const topSpacer = startIdx * ROW_HEIGHT;
  const bottomSpacer = (rows.length - endIdx) * ROW_HEIGHT;

  const scrollRef = useRef<HTMLDivElement>(null);

  const selected = selectedId ? actMap[selectedId] : null;
  const selectedChainPreds = useMemo(() => selected ? tracePath(selectedId!, "predecessors", actMap) : [], [selectedId, actMap]);
  const selectedChainSuccs = useMemo(() => selected ? tracePath(selectedId!, "successors", actMap) : [], [selectedId, actMap]);
  void pickDrivingRel;

  const colDefs = ALL_COLUMNS.filter(c => visibleCols.has(c.key));
  const tableWidth = 110 + colDefs.reduce((s, c) => s + (c.key === "name" ? 220 : 100), 0);

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Gantt</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        {filtered.length.toLocaleString()} of {(allActivities || []).length.toLocaleString()} activities shown · rows are virtualized for large schedules.
      </div>

      {/* ── Toolbar ── */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <input
          value={search} onChange={e => setSearch(e.target.value)} placeholder="Search activity ID or name…"
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 220 }}
        />
        {(["day", "week", "month", "quarter"] as Zoom[]).map(z => (
          <button key={z} onClick={() => setZoom(z)}
            style={{ background: zoom === z ? C.accent : C.card, color: zoom === z ? "#fff" : C.muted2, border: `1px solid ${zoom === z ? C.accent : C.border}`, borderRadius: 7, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", textTransform: "capitalize" }}>
            {z}
          </button>
        ))}
        <select value={colorBy} onChange={e => setColorBy(e.target.value)}
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }}>
          <option value="risk">Color: Risk (Critical/Near/Healthy)</option>
          <option value="status">Color: Status</option>
          <option value="discipline">Color: Discipline</option>
          <option value="area">Color: Area</option>
          <option value="contractor">Color: Contractor</option>
          <option value="system">Color: System</option>
          <option value="wbs">Color: WBS</option>
        </select>
        <div style={{ position: "relative" }}>
          <button onClick={() => setColPickerOpen(o => !o)}
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 11, fontFamily: "inherit", color: C.muted2 }}>
            ⚙ Columns
          </button>
          {colPickerOpen && (
            <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, zIndex: 100, minWidth: 200, boxShadow: "0 8px 24px rgba(0,0,0,0.15)" }}>
              {ALL_COLUMNS.map(c => (
                <label key={c.key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "3px 0", cursor: "pointer" }}>
                  <input type="checkbox" checked={visibleCols.has(c.key)}
                    onChange={() => setVisibleCols(prev => { const n = new Set(prev); n.has(c.key) ? n.delete(c.key) : n.add(c.key); return n; })} />
                  {c.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Filters ── */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
        {(["wbs", "area", "discipline", "contractor", "system"] as const).map(field => (
          distinctValues[field]?.size > 0 && (
            <select key={field} value={filters[field] || ""} onChange={e => setFilters(f => ({ ...f, [field]: e.target.value }))}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "5px 9px", fontSize: 11, fontFamily: "inherit" }}>
              <option value="">All {field}</option>
              {Array.from(distinctValues[field]).sort().map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          )
        ))}
        {([
          ["critical", "Critical"], ["nearCritical", "Near-Critical"],
          ["negativeFloat", "Negative Float"], ["milestones", "Milestones"],
        ] as const).map(([key, label]) => (
          <button key={key} onClick={() => setFlagFilters(f => ({ ...f, [key]: !f[key] }))}
            style={{ background: flagFilters[key] ? `${C.accent}18` : C.card, border: `1px solid ${flagFilters[key] ? C.accent : C.border}`, color: flagFilters[key] ? C.accent : C.muted2, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>
            {label}
          </button>
        ))}
      </div>

      {/* ── Gantt grid: shared vertical scroll, independent horizontal scroll on the timeline ── */}
      <div
        ref={scrollRef}
        onScroll={e => setScrollTop((e.target as HTMLDivElement).scrollTop)}
        style={{ display: "flex", alignItems: "flex-start", height: VIEWPORT_HEIGHT, overflowY: "auto", overflowX: "hidden", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}
      >
        {/* Activity table (sticky header via position:sticky within this scroller) */}
        <div style={{ width: tableWidth, flexShrink: 0, borderRight: `1px solid ${C.border}` }}>
          <div style={{ position: "sticky", top: 0, zIndex: 3, display: "flex", height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}`, alignItems: "center" }}>
            <div style={{ width: 110, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>WBS / Activity</div>
            {colDefs.filter(c => c.key !== "code").map(c => (
              <div key={c.key} style={{ width: c.key === "name" ? 220 : 100, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase", whiteSpace: "nowrap", overflow: "hidden" }}>{c.label}</div>
            ))}
          </div>
          <div style={{ height: topSpacer }} />
          {rows.slice(startIdx, endIdx).map((row, i) => {
            if (row.type === "wbs") {
              const collapsed = collapsedWbs.has(row.key);
              return (
                <div key={`wbs-${row.key}-${startIdx + i}`} onClick={() => setCollapsedWbs(prev => { const n = new Set(prev); n.has(row.key) ? n.delete(row.key) : n.add(row.key); return n; })}
                  style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", gap: 6, padding: "0 8px", background: C.panel, cursor: "pointer", fontSize: 11, fontWeight: 700, color: C.text, borderBottom: `1px solid ${C.border}` }}>
                  <span style={{ fontSize: 10 }}>{collapsed ? "▶" : "▼"}</span>{row.key} <span style={{ color: C.muted, fontWeight: 400 }}>({row.count})</span>
                </div>
              );
            }
            const a = row.activity;
            const aid = a.id || a.code;
            return (
              <div key={`act-${aid}-${startIdx + i}`} onClick={() => setSelectedId(aid)}
                style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", cursor: "pointer", background: selectedId === aid ? `${C.accent}12` : "transparent", borderBottom: `1px solid ${C.border}` }}>
                <div style={{ width: 110, padding: "0 8px", fontSize: 11, fontFamily: "monospace", color: C.accent, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.code}</div>
                {colDefs.filter(c => c.key !== "code").map(c => (
                  <div key={c.key} style={{ width: c.key === "name" ? 220 : 100, padding: "0 8px", fontSize: 11, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={cellValue(a, c.key)}>
                    {cellValue(a, c.key)}
                  </div>
                ))}
              </div>
            );
          })}
          <div style={{ height: bottomSpacer }} />
        </div>

        {/* Timeline (own horizontal scroll) */}
        <div style={{ flex: 1, overflowX: "auto", overflowY: "hidden" }}>
          <div style={{ width: totalWidth, position: "relative" }}>
            <div style={{ position: "sticky", top: 0, zIndex: 2, height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}` }}>
              {headerTicks.map((t, i) => (
                <div key={i} style={{ position: "absolute", left: t.left, width: t.width, top: 0, height: "100%", borderLeft: `1px solid ${C.border}`, fontSize: 10, color: C.muted2, padding: "4px 4px", boxSizing: "border-box", whiteSpace: "nowrap", overflow: "hidden" }}>{t.label}</div>
              ))}
            </div>
            <div style={{ position: "relative", height: topSpacer }} />
            {/* Today / data-date line spans the whole body */}
            {todayX!==null&&<div style={{ position: "absolute", left: todayX, top: HEADER_HEIGHT, bottom: 0, width: 1, background: C.gold, zIndex: 1 }} />}
            {rows.slice(startIdx, endIdx).map((row, i) => {
              if (row.type === "wbs") {
                return <div key={`twbs-${startIdx + i}`} style={{ height: ROW_HEIGHT, borderBottom: `1px solid ${C.border}`, background: C.panel }} />;
              }
              const a = row.activity;
              const aid = a.id || a.code;
              const bs = parseD(a.bStart), bf = parseD(a.bFinish);
              const left = xFor(bs), width = Math.max(2, xFor(bf) - xFor(bs));
              const color = colorFor(a, colorBy);
              const isSel = selectedId === aid;
              return (
                <div key={`tact-${aid}-${startIdx + i}`} style={{ height: ROW_HEIGHT, position: "relative", borderBottom: `1px solid ${C.border}`, background: isSel ? `${C.accent}08` : "transparent" }}>
                  {a.isMilestone ? (
                    <div title={`${a.code} — ${a.name}`} onClick={() => setSelectedId(aid)}
                      style={{ position: "absolute", left: left - 6, top: 6, width: 12, height: 12, background: C.purple, transform: "rotate(45deg)", cursor: "pointer", border: isSel ? `2px solid ${C.text}` : "none" }} />
                  ) : (
                    <div title={`${a.code} — ${a.name}\n${fmtShort(bs)} → ${fmtShort(bf)}\nFloat: ${a.totalFloat ?? "—"}d  ·  ${Math.round(a.pctComplete || 0)}% complete`}
                      onClick={() => setSelectedId(aid)}
                      style={{ position: "absolute", left, top: 7, width, height: 16, borderRadius: 3, background: `${color}30`, border: `1.5px solid ${color}`, cursor: "pointer", overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${Math.min(100, a.pctComplete || 0)}%`, background: color }} />
                    </div>
                  )}
                  {isSel && (selectedChainPreds.length > 1 || selectedChainSuccs.length > 1) && (
                    <svg style={{ position: "absolute", left: 0, top: 0, width: totalWidth, height: ROW_HEIGHT, pointerEvents: "none", overflow: "visible" }}>
                      {[...selectedChainPreds, ...selectedChainSuccs].map((node, ni) => {
                        const other = actMap[node.activity.id || node.activity.code];
                        if (!other || (other.id || other.code) === aid) return null;
                        const ox = xFor(parseD(other.bStart));
                        return <line key={ni} x1={left} y1={ROW_HEIGHT / 2} x2={ox} y2={ROW_HEIGHT / 2} stroke={C.accent} strokeWidth={1} strokeDasharray="3,2" opacity={0.5} />;
                      })}
                    </svg>
                  )}
                </div>
              );
            })}
            <div style={{ height: bottomSpacer }} />
          </div>
        </div>
      </div>

      {selected && (
        <ActivityDetailDrawer
          activity={selected} onClose={() => setSelectedId(null)}
          onTraceInCriticalPath={onGoToActivity}
          predChain={selectedChainPreds} succChain={selectedChainSuccs}
        />
      )}
    </div>
  );
}

function ActivityDetailDrawer({ activity, onClose, onTraceInCriticalPath, predChain, succChain }: any) {
  const a = activity;
  const fields: [string, any][] = [
    ["Activity ID", a.code], ["Name", a.name], ["WBS", a.wbs],
    ["Start", fmtShort(parseD(a.bStart))], ["Finish", fmtShort(parseD(a.bFinish))],
    ["Original Duration", a.dur != null ? `${a.dur}d` : "—"], ["Remaining Duration", a.remainDur != null ? `${a.remainDur}d` : "—"],
    ["Total Float", a.totalFloat != null ? `${a.totalFloat}d` : "—"], ["% Complete", `${Math.round(a.pctComplete || 0)}%`],
    ["Status", a.status || "—"], ["Calendar", a.calendarName || a.calendar || "—"],
    ["Constraint", a.constraintType ? `${a.constraintType}${a.constraintDate ? " (" + a.constraintDate + ")" : ""}` : "—"],
    ["Area", a.area || "—"], ["Discipline", a.discipline || "—"], ["Contractor", a.contractor || "—"], ["System", a.system || "—"],
    ["Predecessor Count", (a.predecessors || []).length], ["Successor Count", (a.successors || []).length],
  ];
  const codeTypes = a.activityCodes && Object.keys(a.activityCodes).length ? a.activityCodes : null;
  const udfs = a.udfs && Object.keys(a.udfs).length ? a.udfs : null;

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(28,20,16,0.45)", zIndex: 1500, display: "flex", justifyContent: "flex-end" }} onClick={onClose}>
      <div style={{ width: 420, maxWidth: "92vw", background: C.bg, borderLeft: `1px solid ${C.border}`, height: "100%", overflowY: "auto", padding: "20px 22px" }} onClick={e => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 11, color: C.accent, fontFamily: "monospace", fontWeight: 700 }}>{a.code}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{a.name}</div>
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, fontSize: 20, cursor: "pointer" }}>×</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 14px", marginBottom: 16 }}>
          {fields.map(([label, value]) => (
            <div key={label}>
              <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
              <div style={{ fontSize: 12, color: C.text, fontWeight: 600 }}>{value === "" || value === null || value === undefined ? "—" : String(value)}</div>
            </div>
          ))}
        </div>

        {codeTypes && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", marginBottom: 4 }}>Activity Codes</div>
            {Object.entries(codeTypes).map(([k, v]) => <div key={k} style={{ fontSize: 11, color: C.text }}>{k}: <strong>{String(v)}</strong></div>)}
          </div>
        )}
        {udfs && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", marginBottom: 4 }}>UDFs</div>
            {Object.entries(udfs).map(([k, v]) => <div key={k} style={{ fontSize: 11, color: C.text }}>{k}: <strong>{String(v)}</strong></div>)}
          </div>
        )}

        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", marginBottom: 6 }}>Driving Chain (relationship trace)</div>
        <div style={{ fontSize: 11, color: C.muted2, marginBottom: 8 }}>{predChain.length} predecessor hop(s) · {succChain.length} successor hop(s) traced by lowest float — not a recalculated CPM.</div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <button onClick={() => onTraceInCriticalPath?.(a)}
            style={{ background: `${C.accent}12`, border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 7, padding: "8px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit", textAlign: "left" }}>
            🔗 Open in Critical Path → Path Tracing
          </button>
        </div>
      </div>
    </div>
  );
}
