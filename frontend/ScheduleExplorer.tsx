// Unified Critical Path & Longest Path Schedule Explorer — a presentation
// layer only. Filtering, WBS hierarchy, path-classification counts, and
// Longest Path provenance are ALL computed server-side (activity_analysis.py
// + the schedule-explorer/activities/ endpoint); nothing here recalculates
// a date, Total Float, or path classification. WBS/area/status/search
// filters change what's displayed, never the underlying CPM-derived
// values — see the view's own docstring for the same rule.
//
// Deliberately takes projectId/versionId as plain CONTROLLED props from
// its caller (the same pattern VarianceCompletionPanel/WeeklyFieldReport-
// Panel already use in FieldDashboard.tsx) rather than running its own
// independent project/version selector — "use the SELECTED project's
// current schedule version" means the Field Dashboard's own selection,
// never a second, competing one.
import { useEffect, useMemo, useRef, useState } from "react";
import { sfetch } from "./projectScope";
import { formatDataDate, versionSelectLabel } from "./dateFormat";
import {
  buildWbsTree, wbsTreeRoots, wbsSubtreeActivityCount, longestPathStatusLabel,
  longestPathCoverageMessage, isLongestPathFilterDisabled, bothViewWarning, wbsHierarchyLimitationMessage,
} from "./scheduleExplorerFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";
const LONGEST_PATH_ONLY_COLOR = "#0891b2";

type Zoom = "day" | "week" | "month" | "quarter";
const PX_PER_DAY: Record<Zoom, number> = { day: 32, week: 10, month: 3.4, quarter: 1.15 };
const ROW_HEIGHT = 30;
const HEADER_HEIGHT = 44;
const VIEWPORT_HEIGHT = 560;
const BUFFER_ROWS = 8;

type Filters = {
  wbsId: string; includeDescendants: boolean; area: string; status: string;
  search: string; pathClassification: "" | "critical" | "longestPath" | "both";
};

function buildQuery(versionId: string, filters: Filters): string {
  const params = new URLSearchParams({ currentVersion: versionId });
  if (filters.wbsId) { params.set("wbsId", filters.wbsId); params.set("includeDescendants", String(filters.includeDescendants)); }
  if (filters.area) params.set("area", filters.area);
  if (filters.status) params.set("status", filters.status);
  if (filters.search) params.set("search", filters.search);
  if (filters.pathClassification) params.set("pathClassification", filters.pathClassification);
  return params.toString();
}

function useScheduleExplorerData(projectId: string, versionId: string, filters: Filters) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !versionId) { setData(null); return; }
    setLoading(true); setError(null);
    sfetch(`${API}/api/projects/${projectId}/schedule-explorer/activities/?${buildQuery(versionId, filters)}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, versionId, filters.wbsId, filters.includeDescendants, filters.area, filters.status, filters.search, filters.pathClassification]);

  return { data, loading, error };
}

function parseD(v: any): Date | null {
  if (!v) return null;
  const s = String(v).slice(0, 10);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}
function daysBetween(a: Date, b: Date): number { return Math.round((b.getTime() - a.getTime()) / 86400000); }
function fmtShort(d: Date | null): string { return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—"; }

function barColorFor(r: any): string {
  const crit = !!r.criticalActionable;
  const longest = r.longestPathStatus === "YES";
  if (crit && longest) return C.red;
  if (longest) return LONGEST_PATH_ONLY_COLOR;
  if (crit) return C.orange;
  if (r.isMilestone) return C.purple;
  return "#3b82f6";
}

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 140 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

// ─── Driving Path Trace (on-demand, heuristic — see driving_chain.py) ───────
function useDrivingChainTrace(projectId: string, versionId: string, activityId: string | null) {
  const [trace, setTrace] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!activityId || !projectId || !versionId) { setTrace(null); return; }
    setLoading(true);
    sfetch(`${API}/api/projects/${projectId}/risk-register/${encodeURIComponent(activityId)}/driving-chain/?version=${versionId}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setTrace).catch(() => setTrace(null)).finally(() => setLoading(false));
  }, [projectId, versionId, activityId]);
  return { trace, loading };
}

type DisplayRow = { type: "wbs"; key: string; label: string; level: number; count: number } | { type: "act"; activity: any };

export default function ScheduleExplorer({ projectId, versionId, versions }: {
  projectId: string; versionId: string; versions: any[];
}) {
  const [filters, setFilters] = useState<Filters>({ wbsId: "", includeDescendants: true, area: "", status: "", search: "", pathClassification: "" });
  const { data, loading, error } = useScheduleExplorerData(projectId, versionId, filters);

  const [zoom, setZoom] = useState<Zoom>("week");
  const [collapsedWbs, setCollapsedWbs] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows: any[] = data?.rows || [];

  // WBS tree is built from THIS fetch's own rows — so the hierarchical
  // filter/grouping always reflects the real P6 WBS structure present in
  // the current schedule version, never a separate computation.
  const wbsTree = useMemo(() => buildWbsTree(rows), [rows]);
  const wbsOptions = useMemo(() => {
    const opts: { value: string; label: string }[] = [];
    const visit = (key: string, depth: number) => {
      const node = wbsTree.get(key);
      if (!node) return;
      const count = wbsSubtreeActivityCount(wbsTree, key);
      opts.push({ value: key, label: `${"— ".repeat(depth)}${node.label} (${count})` });
      for (const childKey of node.children) visit(childKey, depth + 1);
    };
    for (const rootKey of wbsTreeRoots(wbsTree)) visit(rootKey, 0);
    return opts;
  }, [wbsTree]);

  const distinctAreas = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.area) s.add(r.area);
    return Array.from(s).sort();
  }, [rows]);
  const distinctStatuses = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.activityStatus) s.add(r.activityStatus);
    return Array.from(s).sort();
  }, [rows]);

  const activitiesByWbsKey = useMemo(() => {
    const map = new Map<string, any[]>();
    for (const r of rows) {
      const key = (r.wbsIdPath && r.wbsIdPath.length > 0) ? r.wbsIdPath[r.wbsIdPath.length - 1] : (r.wbs || "Unassigned");
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(r);
    }
    return map;
  }, [rows]);

  const displayRows: DisplayRow[] = useMemo(() => {
    const out: DisplayRow[] = [];
    const visit = (key: string, level: number) => {
      const node = wbsTree.get(key);
      if (!node) return;
      const subtreeCount = wbsSubtreeActivityCount(wbsTree, key);
      if (subtreeCount === 0) return;
      out.push({ type: "wbs", key, label: node.label, level, count: subtreeCount });
      if (collapsedWbs.has(key)) return;
      for (const a of (activitiesByWbsKey.get(key) || [])) out.push({ type: "act", activity: a });
      for (const childKey of node.children) visit(childKey, level + 1);
    };
    for (const rootKey of wbsTreeRoots(wbsTree)) visit(rootKey, 0);
    return out;
  }, [wbsTree, activitiesByWbsKey, collapsedWbs]);

  const { rangeStart, rangeEnd } = useMemo(() => {
    let min: Date | null = null, max: Date | null = null;
    for (const r of rows) {
      const s = parseD(r.currentStart), f = parseD(r.currentFinish);
      if (s && (!min || s < min)) min = s;
      if (f && (!max || f > max)) max = f;
    }
    const dd = parseD(data?.currentDataDate) || new Date();
    if (!min) min = dd;
    if (!max) max = dd;
    min = new Date(min.getFullYear(), min.getMonth(), min.getDate() - 14);
    max = new Date(max.getFullYear(), max.getMonth(), max.getDate() + 14);
    return { rangeStart: min, rangeEnd: max };
  }, [rows, data?.currentDataDate]);

  const pxPerDay = PX_PER_DAY[zoom];
  const totalWidth = Math.max(600, daysBetween(rangeStart, rangeEnd) * pxPerDay);
  const xFor = (d: Date | null) => d ? daysBetween(rangeStart, d) * pxPerDay : 0;
  const ddDate = parseD(data?.currentDataDate);
  const todayX = ddDate ? xFor(ddDate) : null;

  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - BUFFER_ROWS);
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + BUFFER_ROWS * 2;
  const endIdx = Math.min(displayRows.length, startIdx + visibleCount);
  const topSpacer = startIdx * ROW_HEIGHT;
  const bottomSpacer = (displayRows.length - endIdx) * ROW_HEIGHT;

  const { trace: drivingTrace, loading: drivingLoading } = useDrivingChainTrace(projectId, versionId, selectedId);
  const drivingChainActivityIds = useMemo(() => {
    if (!drivingTrace?.available) return new Set<string>();
    const ids = new Set<string>();
    for (const n of (drivingTrace.upstreamChain || [])) ids.add(n.activityId);
    for (const n of (drivingTrace.downstreamChain || [])) ids.add(n.activityId);
    return ids;
  }, [drivingTrace]);

  const coverage = data?.longestPathCoverage;
  const wbsHierarchySupport = data?.wbsHierarchySupport;
  const counts = data?.counts;

  const exportUrl = projectId && versionId
    ? `${API}/api/projects/${projectId}/schedule-explorer/export/?${buildQuery(versionId, filters)}`
    : null;

  const versionLabel = versions.find((v: any) => v.id === versionId);

  return (
    <div>
      {data && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 16px", marginBottom: 12 }}>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Data Date</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{formatDataDate(data.currentDataDate)}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Schedule Version</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{data.currentVersionLabel || (versionLabel ? versionSelectLabel(versionLabel) : "—")}</div></div>
        </div>
      )}

      {!projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {projectId && loading && !data && <div style={{ color: C.accent, padding: 20 }}>Loading schedule…</div>}
      {error && <div style={{ color: C.red, padding: 20 }}>{error}</div>}

      {data && (
        <>
          {coverage && (
            <div style={{
              background: (coverage.yes + coverage.no) > 0 ? `${C.green}10` : `${C.gold}10`,
              border: `1px solid ${(coverage.yes + coverage.no) > 0 ? C.green : C.gold}40`,
              borderRadius: 8, padding: "8px 12px", marginBottom: 10, fontSize: 11, color: C.text,
            }}>
              <strong>Longest Path: </strong>{longestPathCoverageMessage(coverage)}
            </div>
          )}

          {/* ── Filters ── */}
          {/* Only Longest Path is ever disabled — Critical Path and Both
              must stay usable regardless of Longest Path data quality, so
              a team never loses visibility into known Critical Path
              activities just because this version's Longest Path
              verification is unavailable or incomplete. */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10, alignItems: "center" }}>
            {(["", "critical", "longestPath", "both"] as const).map(pc => {
              const longestDisabled = pc === "longestPath" && !!coverage && isLongestPathFilterDisabled(coverage);
              return (
                <button key={pc || "all"} onClick={() => setFilters(f => ({ ...f, pathClassification: pc }))}
                  disabled={longestDisabled}
                  title={pc === "longestPath" && coverage ? longestPathCoverageMessage(coverage) : undefined}
                  style={{
                    background: filters.pathClassification === pc ? C.accent : C.card,
                    color: filters.pathClassification === pc ? "#fff" : C.muted2,
                    border: `1px solid ${filters.pathClassification === pc ? C.accent : C.border}`,
                    borderRadius: 7, padding: "6px 13px", cursor: longestDisabled ? "not-allowed" : "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit",
                    opacity: longestDisabled ? 0.45 : 1,
                  }}>
                  {pc === "" ? "All" : pc === "critical" ? `Critical${counts ? ` (${counts.critical})` : ""}` : pc === "longestPath" ? `Longest Path${counts ? ` (${counts.longestPath})` : ""}` : `Both${counts ? ` (${counts.both})` : ""}`}
                </button>
              );
            })}
            <span title={wbsHierarchySupport ? wbsHierarchyLimitationMessage(wbsHierarchySupport) || "Full parent + descendants WBS filtering is available for this schedule version." : undefined}>
              <Select value={filters.wbsId} onChange={(v: string) => setFilters(f => ({ ...f, wbsId: v }))} placeholder="All WBS" options={wbsOptions} />
            </span>
            {distinctAreas.length > 0 && (
              <Select value={filters.area} onChange={(v: string) => setFilters(f => ({ ...f, area: v }))} placeholder="All Areas" options={distinctAreas.map(a => ({ value: a, label: a }))} />
            )}
            {distinctStatuses.length > 0 && (
              <Select value={filters.status} onChange={(v: string) => setFilters(f => ({ ...f, status: v }))} placeholder="All Status" options={distinctStatuses.map(s => ({ value: s, label: s }))} />
            )}
            <input value={filters.search} onChange={e => setFilters(f => ({ ...f, search: e.target.value }))} placeholder="Search activity ID or name…"
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 200 }} />
            {(["day", "week", "month", "quarter"] as Zoom[]).map(z => (
              <button key={z} onClick={() => setZoom(z)}
                style={{ background: zoom === z ? C.accent : C.card, color: zoom === z ? "#fff" : C.muted2, border: `1px solid ${zoom === z ? C.accent : C.border}`, borderRadius: 7, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", textTransform: "capitalize" }}>
                {z}
              </button>
            ))}
            {exportUrl && (
              <a href={exportUrl} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 7, padding: "6px 13px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", textDecoration: "none", marginLeft: "auto" }}>
                ⬇ Export to Excel
              </a>
            )}
          </div>

          {filters.pathClassification === "both" && coverage && bothViewWarning(coverage) && (
            <div style={{ background: `${C.gold}18`, border: `1px solid ${C.gold}60`, borderRadius: 8, padding: "8px 12px", marginBottom: 10, fontSize: 11, fontWeight: 700, color: C.text }}>
              ⚠ {bothViewWarning(coverage)}
            </div>
          )}

          {wbsHierarchySupport && wbsHierarchyLimitationMessage(wbsHierarchySupport) && (
            <div style={{ background: `${C.gold}18`, border: `1px solid ${C.gold}60`, borderRadius: 8, padding: "8px 12px", marginBottom: 10, fontSize: 11, fontWeight: 700, color: C.text }}>
              ⚠ {wbsHierarchyLimitationMessage(wbsHierarchySupport)}
            </div>
          )}

          <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>
            {rows.length.toLocaleString()} activities shown · rows are virtualized for large schedules.
          </div>

          {/* ── Legend ── */}
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 10, fontSize: 10, color: C.muted2 }}>
            <span><span style={{ display: "inline-block", width: 10, height: 10, background: C.red, borderRadius: 2, marginRight: 4 }} />Critical &amp; Longest Path</span>
            <span><span style={{ display: "inline-block", width: 10, height: 10, background: C.orange, borderRadius: 2, marginRight: 4 }} />Critical only (Total Float ≤ 0)</span>
            <span><span style={{ display: "inline-block", width: 10, height: 10, background: LONGEST_PATH_ONLY_COLOR, borderRadius: 2, marginRight: 4 }} />Longest Path only (P6-verified)</span>
            <span><span style={{ display: "inline-block", width: 10, height: 10, background: C.purple, transform: "rotate(45deg)", marginRight: 4 }} />Milestone</span>
          </div>

          {/* ── L-shaped Gantt grid ── */}
          <div
            ref={scrollRef}
            onScroll={e => setScrollTop((e.target as HTMLDivElement).scrollTop)}
            style={{ display: "flex", alignItems: "flex-start", height: VIEWPORT_HEIGHT, overflowY: "auto", overflowX: "hidden", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}
          >
            {/* LEFT — sticky activity info */}
            <div style={{ width: 430, flexShrink: 0, borderRight: `1px solid ${C.border}` }}>
              <div style={{ position: "sticky", top: 0, zIndex: 3, display: "flex", height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}`, alignItems: "center" }}>
                <div style={{ width: 140, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>WBS / Activity ID</div>
                <div style={{ width: 170, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Name / Dates</div>
                <div style={{ width: 60, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Float</div>
                <div style={{ width: 60, padding: "0 8px", fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Status</div>
              </div>
              <div style={{ height: topSpacer }} />
              {displayRows.slice(startIdx, endIdx).map((row, i) => {
                if (row.type === "wbs") {
                  const collapsed = collapsedWbs.has(row.key);
                  return (
                    <div key={`wbs-${row.key}-${startIdx + i}`} onClick={() => setCollapsedWbs(prev => { const n = new Set(prev); n.has(row.key) ? n.delete(row.key) : n.add(row.key); return n; })}
                      style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", gap: 6, padding: `0 8px 0 ${8 + row.level * 14}px`, background: C.panel, cursor: "pointer", fontSize: 11, fontWeight: 700, color: C.text, borderBottom: `1px solid ${C.border}` }}>
                      <span style={{ fontSize: 10 }}>{collapsed ? "▶" : "▼"}</span>{row.label} <span style={{ color: C.muted, fontWeight: 400 }}>({row.count})</span>
                    </div>
                  );
                }
                const a = row.activity;
                const isSel = selectedId === a.activityId;
                const inChain = drivingChainActivityIds.has(a.activityId);
                return (
                  <div key={`act-${a.activityId}-${startIdx + i}`} onClick={() => setSelectedId(isSel ? null : a.activityId)}
                    style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", cursor: "pointer", background: isSel ? `${C.accent}12` : inChain ? `${C.gold}10` : "transparent", borderBottom: `1px solid ${C.border}` }}>
                    <div style={{ width: 140, padding: "0 8px", fontSize: 11, fontFamily: "monospace", color: C.accent, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.activityId}</div>
                    <div style={{ width: 170, padding: "0 8px", overflow: "hidden" }}>
                      <div style={{ fontSize: 11, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.activityName}>{a.activityName}</div>
                      <div style={{ fontSize: 9, color: C.muted2 }}>{fmtShort(parseD(a.currentStart))} → {fmtShort(parseD(a.currentFinish))}</div>
                    </div>
                    <div style={{ width: 60, padding: "0 8px", fontSize: 11, color: a.currentTotalFloat != null && a.currentTotalFloat < 0 ? C.red : C.text }}>{a.currentTotalFloat == null ? "—" : `${a.currentTotalFloat}d`}</div>
                    <div style={{ width: 60, padding: "0 8px", fontSize: 10, color: C.muted2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.activityStatus}</div>
                  </div>
                );
              })}
              <div style={{ height: bottomSpacer }} />
            </div>

            {/* RIGHT — time-scaled, horizontally scrolling Gantt */}
            <div style={{ flex: 1, overflowX: "auto", overflowY: "hidden" }}>
              <div style={{ width: totalWidth, position: "relative" }}>
                <div style={{ position: "sticky", top: 0, zIndex: 2, height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}` }} />
                <div style={{ position: "relative", height: topSpacer }} />
                {todayX !== null && <div title="Data Date" style={{ position: "absolute", left: todayX, top: HEADER_HEIGHT, bottom: 0, width: 1, background: C.gold, zIndex: 1 }} />}
                {displayRows.slice(startIdx, endIdx).map((row, i) => {
                  if (row.type === "wbs") {
                    return <div key={`twbs-${startIdx + i}`} style={{ height: ROW_HEIGHT, borderBottom: `1px solid ${C.border}`, background: C.panel }} />;
                  }
                  const a = row.activity;
                  const bs = parseD(a.currentStart), bf = parseD(a.currentFinish);
                  const left = xFor(bs), width = Math.max(2, xFor(bf) - xFor(bs));
                  const color = barColorFor(a);
                  const isSel = selectedId === a.activityId;
                  const inChain = drivingChainActivityIds.has(a.activityId);
                  return (
                    <div key={`tact-${a.activityId}-${startIdx + i}`} style={{ height: ROW_HEIGHT, position: "relative", borderBottom: `1px solid ${C.border}`, background: isSel ? `${C.accent}08` : "transparent" }}>
                      {a.isMilestone ? (
                        <div title={`${a.activityId} — ${a.activityName}`} onClick={() => setSelectedId(isSel ? null : a.activityId)}
                          style={{ position: "absolute", left: left - 6, top: 6, width: 12, height: 12, background: C.purple, transform: "rotate(45deg)", cursor: "pointer", border: isSel ? `2px solid ${C.text}` : "none" }} />
                      ) : (
                        <div title={`${a.activityId} — ${a.activityName}\nFloat: ${a.currentTotalFloat ?? "—"}d · Critical: ${a.criticalActionable ? "Yes" : "No"} · Longest Path: ${longestPathStatusLabel(a.longestPathStatus)}`}
                          onClick={() => setSelectedId(isSel ? null : a.activityId)}
                          style={{ position: "absolute", left, top: 7, width, height: 16, borderRadius: 3, background: `${color}30`, border: `1.5px solid ${color}`, cursor: "pointer", outline: inChain ? `2px solid ${C.gold}` : "none" }} />
                      )}
                    </div>
                  );
                })}
                <div style={{ height: bottomSpacer }} />
              </div>
            </div>
          </div>

          {selectedId && (
            <div style={{ marginTop: 10, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: C.text, marginBottom: 4 }}>
                Driving Path Trace — {selectedId} <span style={{ color: C.muted2, fontWeight: 400 }}>(heuristic — lowest Total Float wins at each hop; NOT a confirmed P6 Longest Path recalculation)</span>
              </div>
              {drivingLoading && <div style={{ fontSize: 11, color: C.accent }}>Tracing…</div>}
              {drivingTrace && !drivingTrace.available && <div style={{ fontSize: 11, color: C.muted2 }}>{drivingTrace.reason}</div>}
              {drivingTrace?.available && (
                <div style={{ fontSize: 11, color: C.text }}>
                  {drivingTrace.methodologyNote}
                  <div style={{ marginTop: 4, color: C.muted2 }}>
                    {drivingTrace.upstreamChain?.length || 0} upstream hop(s) · {drivingTrace.downstreamChain?.length || 0} downstream hop(s) highlighted above.
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
