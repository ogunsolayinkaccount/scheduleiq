import { useEffect, useMemo, useRef, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { formatDataDate, versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

const STATUS_COLOR: Record<string, string> = {
  ON_PLAN: C.green, DELAYED_START: C.amber, DELAYED_FINISH: C.red, AHEAD: C.purple,
  IN_PROGRESS: C.accent, SHOULD_HAVE_STARTED: C.orange, SHOULD_HAVE_FINISHED: C.red,
  COMPLETE: C.muted2, ADDED_SINCE_BASELINE: C.gold, REMOVED_FROM_CURRENT: C.muted,
};
const STATUS_LABEL: Record<string, string> = {
  ON_PLAN: "On Plan", DELAYED_START: "Delayed Start", DELAYED_FINISH: "Delayed Finish", AHEAD: "Ahead",
  IN_PROGRESS: "In Progress", SHOULD_HAVE_STARTED: "Should Have Started", SHOULD_HAVE_FINISHED: "Should Have Finished",
  COMPLETE: "Complete", ADDED_SINCE_BASELINE: "Added Since Baseline", REMOVED_FROM_CURRENT: "Removed / Missing",
};
const BEHIND_STATUSES = new Set(["DELAYED_START", "DELAYED_FINISH", "SHOULD_HAVE_STARTED", "SHOULD_HAVE_FINISHED"]);
const LEVEL_COLOR: Record<string, string> = { Healthy: C.green, Watch: C.amber, "At Risk": C.orange, Critical: C.red };

function Select({ value, onChange, options, placeholder, title }: any) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} title={title}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 140 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: checked ? C.accent : C.muted2, cursor: "pointer", background: checked ? `${C.accent}14` : "transparent", border: `1px solid ${checked ? C.accent : C.border}`, borderRadius: 6, padding: "4px 8px" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ margin: 0 }} />
      {label}
    </label>
  );
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "11px 14px", minWidth: 130, flex: "1 1 130px" }}>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 19, fontWeight: 800, color: color || C.text, fontFamily: "'DM Mono',monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const LOOKAHEAD_OPTIONS = [
  { value: "2", label: "2 Weeks" }, { value: "4", label: "4 Weeks" }, { value: "6", label: "6 Weeks" },
  { value: "8", label: "8 Weeks" }, { value: "12", label: "12 Weeks" }, { value: "custom", label: "Custom" },
  { value: "all", label: "All Activities" },
];

export default function BaselineProgress({ initialProjectId, onManageVersions }: { initialProjectId?: string; onManageVersions?: (projectId: string) => void } = {}) {
  const [projects, setProjects] = useState<any[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);   // stale responses from a previously selected project are dropped (see projectScope.ts)
  const [versions, setVersions] = useState<any[]>([]);
  const [currentVersionId, setCurrentVersionId] = useState("");
  const [baselineVersionId, setBaselineVersionId] = useState("");

  const [lookaheadWeeks, setLookaheadWeeks] = useState("4");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [filters, setFilters] = useState<Record<string, any>>({});
  const [scurveMetric, setScurveMetric] = useState("");
  const [histogramMetric, setHistogramMetric] = useState("activities");
  const [varianceField, setVarianceField] = useState<"finishVarianceDays" | "startVarianceDays">("finishVarianceDays");
  const [varianceTopN, setVarianceTopN] = useState("10");

  const [full, setFull] = useState<any>(null);
  const [lookahead, setLookahead] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Milestone markers on the S-Curve — reuses the canonical Milestone
  // Dashboard's own /milestones/ endpoint (RiskIntelligence.tsx's
  // MilestoneDashboard data source), never a second milestone calculation.
  const [milestones, setMilestones] = useState<any[]>([]);
  const [showMarkers, setShowMarkers] = useState(true);
  const [showBaselineMarkers, setShowBaselineMarkers] = useState(false);
  const [selectedMilestoneIds, setSelectedMilestoneIds] = useState<Set<string> | null>(null); // null = "all"
  const [expandedMarkerId, setExpandedMarkerId] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/projects/?withVersions=true`).then((r) => r.json()).then((d) => { const list = d.projects || []; setProjects(list); setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur)); }).catch(() => {});
  }, [projectsTick]);

  useEffect(() => {
    if (!projectId) { setVersions([]); return; }
    sfetch(`${API}/api/projects/${projectId}/versions/`).then((r) => r.json()).then((d) => {
      const vs = d.versions || [];
      setVersions(dedupeVersionsById(vs));
      const cur = vs.find((v: any) => v.role === "CURRENT");
      const base = vs.find((v: any) => v.role === "BASELINE");
      setCurrentVersionId(cur?.id || vs[0]?.id || "");
      setBaselineVersionId(base?.id || "");
    }).catch(() => setVersions([]));
  }, [projectId, projectsTick]);

  const buildParams = (extra: Record<string, string> = {}) => {
    const p = new URLSearchParams();
    if (currentVersionId) p.set("currentVersion", currentVersionId);
    if (baselineVersionId) p.set("baselineVersion", baselineVersionId);
    Object.entries(filters).forEach(([k, v]) => { if (v) p.set(k, String(v)); });
    Object.entries(extra).forEach(([k, v]) => p.set(k, v));
    return p;
  };

  const fetchAll = () => {
    if (!projectId || !currentVersionId) return;
    setLoading(true); setError(null);

    const fullParams = buildParams({ histogramMetric });
    if (scurveMetric) fullParams.set("scurveMetric", scurveMetric);

    // "All Activities" means no Look-Ahead window is applied — the chart
    // falls back to the unwindowed /baseline-progress/ row set, and the
    // Look-Ahead-specific summary cards are simply not fetched (never a
    // fabricated "window" spanning everything).
    const skipLookahead = lookaheadWeeks === "all";
    const laParams = buildParams({ histogramMetric });
    if (lookaheadWeeks === "custom") {
      if (customFrom) laParams.set("fromDate", customFrom);
      if (customTo) laParams.set("toDate", customTo);
    } else if (!skipLookahead) {
      laParams.set("lookaheadWeeks", lookaheadWeeks);
    }

    Promise.all([
      sfetch(`${API}/api/projects/${projectId}/baseline-progress/?${fullParams.toString()}`).then((r) => r.json()),
      skipLookahead ? Promise.resolve(null) : sfetch(`${API}/api/projects/${projectId}/lookahead/?${laParams.toString()}`).then((r) => r.json()),
    ])
      .then(([f, l]) => {
        if (f.error) throw new Error(f.error);
        setFull(f); setLookahead(l);
      })
      .catch((e: any) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentVersionId, baselineVersionId, lookaheadWeeks, customFrom, customTo, filters, scurveMetric, histogramMetric]);

  useEffect(() => {
    if (!projectId || !currentVersionId) { setMilestones([]); return; }
    const p = new URLSearchParams({ version: currentVersionId });
    if (baselineVersionId) p.set("baselineVersion", baselineVersionId);
    sfetch(`${API}/api/projects/${projectId}/milestones/?${p.toString()}`)
      .then((r) => r.json()).then((d) => setMilestones(d.milestones || [])).catch(() => setMilestones([]));
  }, [projectId, currentVersionId, baselineVersionId]);

  // Maps a milestone's date onto the nearest S-Curve period bucket so it can
  // be plotted as a ReferenceLine on the same categorical X-axis the curve
  // itself uses — never a separate date axis that could visually disagree
  // with the curve's own period boundaries.
  const periodForDate = (dateStr: string | null | undefined): string | null => {
    if (!dateStr || !full?.scurve?.periods?.length) return null;
    const target = new Date(dateStr).getTime();
    let best: any = null, bestDiff = Infinity;
    for (const p of full.scurve.periods) {
      if (!p.periodEnd) continue;
      const diff = Math.abs(new Date(p.periodEnd).getTime() - target);
      if (diff < bestDiff) { bestDiff = diff; best = p; }
    }
    return best ? best.period : null;
  };

  const dataDate = full?.dataDate;
  const cards = lookahead?.summaryCards;
  const rows: any[] = full?.rows || [];
  const currentVersionMeta = useMemo(() => versions.find((v: any) => v.id === currentVersionId), [versions, currentVersionId]);
  const baselineVersionMeta = useMemo(() => versions.find((v: any) => v.id === baselineVersionId), [versions, baselineVersionId]);

  // Every activity's name, keyed by ID — used to resolve predecessor/
  // successor names in the detail drawer even when they fall outside the
  // active look-ahead window or filters.
  const nameById = useMemo(() => {
    const m: Record<string, string> = {};
    rows.forEach((r) => { m[r.activityId] = r.activityName; });
    return m;
  }, [rows]);

  const varianceRanked = (full?.[varianceField === "finishVarianceDays" ? "topFinishVariance" : "topStartVariance"] || [])
    .slice(0, varianceTopN === "all" ? undefined : parseInt(varianceTopN, 10));

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Baseline & Progress</div>
        <div style={{ fontSize: 13, color: C.muted }}>
          What was planned vs. what has happened vs. what is now forecast — computed from imported baseline and current schedule data, never fabricated.
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
          <Select value={projectId} onChange={(id: string) => { setVersions([]); setCurrentVersionId(""); setBaselineVersionId(""); setProjectId(id); }} placeholder="Select a project…" options={projects.map((p: any) => ({ value: p.id, label: p.name }))} />
        </div>
        {projectId && onManageVersions && (
          <button onClick={() => onManageVersions(projectId)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
            Manage Versions
          </button>
        )}
        {projectId && versions.length > 0 && (
          <>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Current</div>
              <Select value={currentVersionId} onChange={setCurrentVersionId} options={versions.map((v: any) => ({ value: v.id, label: versionSelectLabel(v) }))} />
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Baseline</div>
              <Select value={baselineVersionId} onChange={setBaselineVersionId} placeholder="None designated"
                options={versions.map((v: any) => ({ value: v.id, label: versionSelectLabel(v) }))} />
            </div>
          </>
        )}
      </div>

      {!projectId && <div style={{ padding: 30, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>Select a project to view its baseline vs. progress picture.</div>}
      {error && <div style={{ padding: 12, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 14 }}>{error}</div>}
      {loading && <div style={{ padding: 20, textAlign: "center", color: C.muted }}>Loading…</div>}

      {projectId && full && !loading && (
        <>
          {full.baselineMessage && (
            <div style={{ background: `${C.gold}14`, border: `1px solid ${C.gold}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 14, fontSize: 13, color: C.text }}>
              ⚠ {full.baselineMessage} Designate a version with classification <strong>Approved Baseline</strong> (or <strong>Revised Baseline</strong>) at import, or pick one from the Baseline dropdown above.
            </div>
          )}

          <div style={{ fontSize: 12, color: C.muted2, marginBottom: 14 }}>
            <span title="Same effective Data Date used by the main toolbar">DATA DATE — <strong style={{ color: C.text }}>{formatDataDate(dataDate)}</strong></span>
            {currentVersionMeta?.dataDateOverridden && (
              <span style={{ marginLeft: 6, color: C.gold, cursor: "help" }}
                title={currentVersionMeta.sourceDataDate ? `Source file Data Date: ${formatDataDate(currentVersionMeta.sourceDataDate)}` : "Manually entered — no Data Date was detected in the source file"}>
                · Overridden ⓘ
              </span>
            )}
            {lookahead?.window?.available && (
              <span style={{ marginLeft: 14 }}>LOOK-AHEAD — <strong style={{ color: C.text }}>{formatDataDate(lookahead.window.fromDate)} – {formatDataDate(lookahead.window.toDate)}</strong></span>
            )}
          </div>

          {/* 1. Summary cards */}
          {lookaheadWeeks === "all" && (
            <div style={{ fontSize: 11, color: C.muted2, fontStyle: "italic", marginBottom: 14 }}>Summary KPI cards reflect a Look-Ahead window — select 2/4/6/8/12 Weeks or Custom above to see them.</div>
          )}
          {cards && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
              <Metric label="Activities in Look-Ahead" value={String(cards.activitiesInWindow)} />
              <Metric label="Planned Starts" value={String(cards.plannedStarts)} />
              <Metric label="Forecast Starts" value={String(cards.forecastStarts)} />
              <Metric label="Planned Finishes" value={String(cards.plannedFinishes)} />
              <Metric label="Forecast Finishes" value={String(cards.forecastFinishes)} />
              <Metric label="Behind Baseline" value={String(cards.behindBaseline)} color={cards.behindBaseline > 0 ? C.red : C.green} />
              <Metric label="Should Have Started" value={String(cards.shouldHaveStarted)} color={cards.shouldHaveStarted > 0 ? C.orange : C.muted2} />
              <Metric label="Should Have Finished" value={String(cards.shouldHaveFinished)} color={cards.shouldHaveFinished > 0 ? C.red : C.muted2} />
              <Metric label="Critical Activities" value={String(cards.criticalActivities)} />
              <Metric label="Avg Finish Variance" value={cards.averageFinishVarianceDays != null ? `${cards.averageFinishVarianceDays > 0 ? "+" : ""}${cards.averageFinishVarianceDays}d` : "Unavailable"} color={cards.averageFinishVarianceDays > 0 ? C.red : C.green} />
              {cards.plannedHours != null && <Metric label="Planned Hours" value={cards.plannedHours.toFixed(0)} />}
              {cards.forecastHours != null && <Metric label="Forecast Hours" value={cards.forecastHours.toFixed(0)} />}
              {cards.actualHours != null && <Metric label="Actual Hours" value={cards.actualHours.toFixed(0)} />}
            </div>
          )}

          {/* 2. S-Curve */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Baseline vs. Progress S-Curve</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <Check label="Milestone Markers" checked={showMarkers} onChange={setShowMarkers} />
                {showMarkers && <Check label="+ Baseline Position" checked={showBaselineMarkers} onChange={setShowBaselineMarkers} />}
                <Select value={scurveMetric} onChange={setScurveMetric} placeholder={`Auto (${full.scurve?.metric || "duration"})`}
                  options={["activities", "duration", "hours", "cost"].map((m) => ({ value: m, label: m[0].toUpperCase() + m.slice(1) }))} />
              </div>
            </div>
            {full.scurve?.available ? (
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={full.scurve.periods}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: C.muted }} />
                  <YAxis tick={{ fontSize: 10, fill: C.muted }} unit="%" domain={[0, 100]} />
                  <Tooltip formatter={(v: any) => (v == null ? "Unavailable" : `${v}%`)} />
                  <Legend />
                  {dataDate && <ReferenceLine x={full.scurve.periods.find((p: any) => !p.isPast)?.period} stroke={C.purple} strokeDasharray="4 4" label={{ value: "Data Date", fontSize: 10, fill: C.purple, position: "insideTopLeft" }} />}
                  {showMarkers && milestones
                    .filter((m: any) => !selectedMilestoneIds || selectedMilestoneIds.has(m.activityId))
                    .map((m: any) => {
                      const curPeriod = periodForDate(m.currentFinish);
                      const basPeriod = showBaselineMarkers ? periodForDate(m.baselineFinish) : null;
                      const color = LEVEL_COLOR[m.riskLevel] || C.gold;
                      return (
                        <>
                          {curPeriod && (
                            <ReferenceLine key={`cur-${m.activityId}`} x={curPeriod} stroke={color} strokeWidth={1.5}
                              label={{ value: m.activityName, fontSize: 9, fill: color, angle: -90, position: "top", offset: 8 }} />
                          )}
                          {basPeriod && basPeriod !== curPeriod && (
                            <ReferenceLine key={`bas-${m.activityId}`} x={basPeriod} stroke={C.muted2} strokeDasharray="2 2" />
                          )}
                        </>
                      );
                    })}
                  <Line type="monotone" dataKey="baselinePlanned" name="Baseline Planned" stroke={C.muted2} strokeWidth={2} dot={false} connectNulls />
                  <Line type="monotone" dataKey="currentUpdateActual" name="Actual / Earned Progress" stroke={C.green} strokeWidth={2.5} dot={false} connectNulls />
                  <Line type="monotone" dataKey="currentPlanned" name="Current Forecast" stroke={C.accent} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ padding: 30, textAlign: "center", color: C.muted2, fontSize: 12 }}>{full.scurve?.reason || "Unavailable for this metric."}</div>
            )}
            {full.scurve?.methodologyNote && <div style={{ fontSize: 10, color: C.muted2, marginTop: 8, fontStyle: "italic" }}>{full.scurve.methodologyNote}</div>}

            {showMarkers && milestones.length > 0 && (
              <div style={{ marginTop: 14, borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Milestone Markers ({milestones.length})</div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={() => setSelectedMilestoneIds(null)} style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontSize: 10, fontWeight: 700 }}>All Major Milestones</button>
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {milestones.map((m: any) => {
                    const isOpen = expandedMarkerId === m.activityId;
                    const varianceDays = m.varianceDays;
                    return (
                      <div key={m.activityId}>
                        <div onClick={() => setExpandedMarkerId(isOpen ? null : m.activityId)}
                          style={{ display: "flex", alignItems: "center", gap: 10, padding: "5px 8px", background: isOpen ? C.card2 : "transparent", borderRadius: 6, cursor: "pointer", fontSize: 11 }}>
                          <span style={{ width: 8, height: 8, borderRadius: "50%", background: LEVEL_COLOR[m.riskLevel] || C.gold, flexShrink: 0 }} />
                          <span style={{ fontFamily: "monospace", color: C.muted2 }}>{m.activityId}</span>
                          <span style={{ fontWeight: 600, color: C.text, flex: 1 }}>{m.activityName}</span>
                          <span style={{ color: C.muted2 }}>{m.currentFinish || "—"}</span>
                          {varianceDays != null && <span style={{ color: varianceDays > 0 ? C.red : C.green, fontWeight: 700 }}>{varianceDays > 0 ? "+" : ""}{varianceDays}d</span>}
                        </div>
                        {isOpen && (
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, padding: "8px 12px 10px 26px", fontSize: 10, background: C.card, borderRadius: 6, marginTop: 2 }}>
                            <div><div style={{ color: C.muted }}>Baseline Date</div><div style={{ color: C.text, fontWeight: 700 }}>{m.baselineFinish || "Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Current Forecast</div><div style={{ color: C.text, fontWeight: 700 }}>{m.currentFinish || "Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Variance</div><div style={{ color: varianceDays > 0 ? C.red : C.text, fontWeight: 700 }}>{varianceDays != null ? `${varianceDays > 0 ? "+" : ""}${varianceDays}d` : "Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Current Total Float</div><div style={{ color: m.totalFloat != null && m.totalFloat < 0 ? C.red : C.text, fontWeight: 700 }}>{m.totalFloat ?? "Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Risk Level</div><div style={{ color: LEVEL_COLOR[m.riskLevel] || C.text, fontWeight: 700 }}>{m.riskLevel || "—"}</div></div>
                            <div><div style={{ color: C.muted }}>Driving Predecessor</div><div style={{ color: C.text, fontWeight: 700 }}>{m.drivingPredecessor ? `${m.drivingPredecessor.activityId} (${m.drivingPredecessor.totalFloat ?? "—"}d)` : "None / Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Movement Since Last Update</div><div style={{ color: C.text, fontWeight: 700 }}>{m.movementSinceLastUpdateDays != null ? `${m.movementSinceLastUpdateDays > 0 ? "+" : ""}${m.movementSinceLastUpdateDays}d` : "Unavailable"}</div></div>
                            <div><div style={{ color: C.muted }}>Status</div><div style={{ color: C.text, fontWeight: 700 }}>{m.status}</div></div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* 3. Histogram */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Weekly Progress Histogram</div>
              <Select value={histogramMetric} onChange={setHistogramMetric} options={["activities", "hours", "cost"].map((m) => ({ value: m, label: m[0].toUpperCase() + m.slice(1) }))} />
            </div>
            {full.histogram?.available ? (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={full.histogram.buckets}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: C.muted }} />
                  <YAxis tick={{ fontSize: 10, fill: C.muted }} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="baseline" name="Baseline Planned" fill={C.muted2} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="currentForecast" name="Current / Forecast" fill={C.accent} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="actual" name="Actual" fill={C.green} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ padding: 30, textAlign: "center", color: C.muted2, fontSize: 12 }}>{full.histogram?.reason || "Unavailable for this metric."}</div>
            )}
          </div>

          {/* 4. Look Ahead controls + global filter panel */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 14px", marginBottom: 16 }}>
            <Select value={lookaheadWeeks} onChange={setLookaheadWeeks} options={LOOKAHEAD_OPTIONS} />
            {lookaheadWeeks === "custom" && (
              <>
                <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 12, fontFamily: "inherit" }} />
                <span style={{ color: C.muted2, fontSize: 12 }}>to</span>
                <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 12, fontFamily: "inherit" }} />
              </>
            )}
            {["wbs", "area", "discipline", "contractor", "system"].map((f) => (
              <Select key={f} value={filters[f] || ""} onChange={(v: string) => setFilters({ ...filters, [f]: v })} placeholder={f[0].toUpperCase() + f.slice(1)}
                options={Array.from(new Set(rows.map((r) => r[f]).filter(Boolean))).map((v: any) => ({ value: v, label: v }))} />
            ))}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[
                { key: "criticalOnly", label: "Critical" }, { key: "nearCritical", label: "Near Critical" },
                { key: "inProgress", label: "In Progress" }, { key: "notStarted", label: "Not Started" },
                { key: "completed", label: "Completed" }, { key: "delayedOnly", label: "Delayed Only" },
                { key: "milestonesOnly", label: "Milestones" },
              ].map((f) => (
                <Check key={f.key} label={f.label} checked={!!filters[f.key]} onChange={(v) => setFilters({ ...filters, [f.key]: v || undefined })} />
              ))}
            </div>
            {Object.values(filters).some(Boolean) && (
              <button onClick={() => setFilters({})} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 11 }}>Clear Filters</button>
            )}
          </div>

          {/* 5. Baseline vs Forecast Activity Chart */}
          <BaselineForecastChart
            full={full} lookahead={lookahead} dataDate={dataDate} nameById={nameById}
            lookaheadWeeks={lookaheadWeeks} currentVersionMeta={currentVersionMeta} baselineVersionMeta={baselineVersionMeta}
            projectId={projectId} currentVersionId={currentVersionId} baselineVersionId={baselineVersionId}
            filters={filters} customFrom={customFrom} customTo={customTo}
          />

          {/* 6. Top variance ranking */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Which activities are furthest behind baseline?</div>
              <div style={{ display: "flex", gap: 8 }}>
                <Select value={varianceField} onChange={(v: any) => setVarianceField(v)} options={[{ value: "finishVarianceDays", label: "Finish Variance" }, { value: "startVarianceDays", label: "Start Variance" }]} />
                <Select value={varianceTopN} onChange={setVarianceTopN} options={[{ value: "10", label: "Top 10" }, { value: "25", label: "Top 25" }, { value: "50", label: "Top 50" }, { value: "all", label: "All" }]} />
              </div>
            </div>
            {varianceRanked.length > 0 ? (
              <ResponsiveContainer width="100%" height={Math.max(120, varianceRanked.length * 26)}>
                <BarChart data={varianceRanked} layout="vertical" margin={{ left: 16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                  <XAxis type="number" tick={{ fontSize: 10, fill: C.muted }} unit="d" />
                  <YAxis type="category" dataKey="activityId" tick={{ fontSize: 10, fill: C.muted }} width={90} />
                  <Tooltip formatter={(v: any) => `${v > 0 ? "+" : ""}${v} days`} labelFormatter={(l: any) => varianceRanked.find((r: any) => r.activityId === l)?.activityName || l} />
                  <Bar dataKey={varianceField} radius={[0, 4, 4, 0]}>
                    {varianceRanked.map((r: any, i: number) => <Cell key={i} fill={r[varianceField] > 0 ? C.red : C.green} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ padding: 20, textAlign: "center", color: C.muted2, fontSize: 12 }}>No variance data available for the current filters.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Baseline vs Current/Forecast Activity Chart
// ─────────────────────────────────────────────────────────────────────────

type Zoom = "day" | "week" | "month" | "quarter";
const PX_PER_DAY: Record<Zoom, number> = { day: 26, week: 8.5, month: 3, quarter: 1.1 };
const ROW_HEIGHT = 34;
const HEADER_HEIGHT = 42;
const VIEWPORT_HEIGHT = 560;
const BUFFER_ROWS = 10;
const GROUP_FIELDS = [
  { value: "", label: "No grouping" }, { value: "wbs", label: "Group by WBS" }, { value: "area", label: "Group by Area" },
  { value: "discipline", label: "Group by Discipline" }, { value: "contractor", label: "Group by Contractor" }, { value: "system", label: "Group by System" },
];
const SORT_OPTIONS = [
  { value: "default", label: "Sort: WBS → Baseline Start" },
  { value: "activityId", label: "Sort: Activity ID" },
  { value: "activityName", label: "Sort: Activity Name" },
  { value: "baselineStart", label: "Sort: Baseline Start" },
  { value: "baselineFinish", label: "Sort: Baseline Finish" },
  { value: "currentStart", label: "Sort: Current Start" },
  { value: "currentFinish", label: "Sort: Current Finish" },
  { value: "finishVarianceDays", label: "Sort: Finish Variance" },
  { value: "totalFloat", label: "Sort: Total Float" },
  { value: "status", label: "Sort: Status" },
];
const VARIANCE_VIEWS = [
  { value: "all", label: "All Activities" }, { value: "behind", label: "Behind Baseline" },
  { value: "ahead", label: "Ahead of Baseline" }, { value: "onplan", label: "On Plan" },
  { value: "critical", label: "Critical / Near-Critical" },
];

// Always visible, never scrolled away — section 8's "never lose track of
// which activity you're looking at" requirement.
const FROZEN_COLS: { key: string; label: string; width: number }[] = [
  { key: "activityId", label: "Activity ID", width: 90 },
  { key: "activityName", label: "Activity Name", width: 200 },
];
// Everything else is optional — shown/hidden via the Columns panel and
// scrolled horizontally alongside the timeline, so the timeline itself
// gets far more usable width by default than a 12-column frozen table did.
const OPTIONAL_COLS: { key: string; label: string; width: number }[] = [
  { key: "wbs", label: "WBS", width: 100 },
  { key: "baselineStart", label: "Baseline Start", width: 92 },
  { key: "baselineFinish", label: "Baseline Finish", width: 92 },
  { key: "currentStart", label: "Current Start", width: 92 },
  { key: "currentFinish", label: "Current Finish", width: 92 },
  { key: "startVarianceDays", label: "Start Variance", width: 88 },
  { key: "finishVarianceDays", label: "Finish Variance", width: 88 },
  { key: "pctComplete", label: "% Complete", width: 78 },
  { key: "totalFloat", label: "Total Float", width: 74 },
  { key: "status", label: "Status", width: 132 },
  { key: "discipline", label: "Discipline", width: 110 },
  { key: "contractor", label: "Contractor", width: 110 },
  { key: "area", label: "Area", width: 96 },
  { key: "system", label: "System", width: 96 },
  { key: "calendarName", label: "Calendar", width: 130 },
];
const DEFAULT_VISIBLE_COLS = ["baselineFinish", "currentFinish", "finishVarianceDays", "pctComplete", "totalFloat", "status"];
const VISIBLE_COLS_STORAGE_KEY = "scheduleiq_bfc_visible_cols";

const CHART_PRESETS: Record<string, { label: string; cols: string[] }> = {
  field: { label: "Field View", cols: ["currentStart", "currentFinish", "status", "pctComplete"] },
  scheduler: { label: "Scheduler View", cols: ["baselineStart", "baselineFinish", "currentStart", "currentFinish", "startVarianceDays", "finishVarianceDays", "totalFloat", "status"] },
  executive: { label: "Executive View", cols: ["baselineFinish", "currentFinish", "finishVarianceDays"] },
};

function parseISO(v: any): Date | null {
  if (!v) return null;
  const s = String(v).slice(0, 10);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}
function daysBetween(a: Date, b: Date): number { return Math.round((b.getTime() - a.getTime()) / 86400000); }
function fmtShort(d: Date | null): string { return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—"; }

// Explicit sign + explicit unit (wd/cd), always — "0" reads as on-plan at a
// glance without relying on color, and a reader can never mistake a
// working-day figure for a calendar-day one or vice versa.
function varianceCell(days: number | null, workingDays: number | null, calAvail: boolean): string {
  if (days == null) return "—";
  if (calAvail && workingDays != null) return workingDays === 0 ? "0 wd" : `${workingDays > 0 ? "+" : ""}${workingDays} wd`;
  return days === 0 ? "0 cd" : `${days > 0 ? "+" : ""}${days} cd`;
}
function varianceTooltip(days: number | null, workingDays: number | null, calAvail: boolean): string {
  if (days == null) return "Unavailable";
  if (calAvail && workingDays != null) return workingDays === 0 ? "0 working days (on plan)" : `${workingDays > 0 ? "+" : ""}${workingDays} working days`;
  return days === 0 ? "0 calendar days (on plan)" : `${days > 0 ? "+" : ""}${days} calendar days`;
}

function cellText(r: any, key: string): string {
  switch (key) {
    case "activityId": return r.activityId;
    case "activityName": return r.activityName || "—";
    case "wbs": return r.wbs || "—";
    case "baselineStart": return formatDataDate(r.baselineStart);
    case "baselineFinish": return formatDataDate(r.baselineFinish);
    case "currentStart": return formatDataDate(r.currentStart);
    case "currentFinish": return formatDataDate(r.currentFinish);
    case "startVarianceDays": return varianceCell(r.startVarianceDays, r.startVarianceWorkingDays, r.workingDayCalendarAvailable);
    case "finishVarianceDays": return varianceCell(r.finishVarianceDays, r.finishVarianceWorkingDays, r.workingDayCalendarAvailable);
    case "pctComplete": return r.pctComplete != null ? `${r.pctComplete}%` : "—";
    case "totalFloat": return r.totalFloat != null ? `${r.totalFloat}d` : "—";
    case "status": return STATUS_LABEL[r.status] || r.status || "—";
    case "discipline": return r.discipline || "—";
    case "contractor": return r.contractor || "—";
    case "area": return r.area || "—";
    case "system": return r.system || "—";
    case "calendarName": return r.calendarName || r.calendarId || "—";
    default: return "—";
  }
}

// Splits the Current/Forecast bar into an "actual" (statused, left of the
// activity's real actual dates or fully complete) segment and a "forecast"
// (remaining, not yet real) segment — split exactly at the effective Data
// Date, never estimated from % complete. A not-started activity's entire
// bar is forecast; a complete activity's entire bar is actual.
function currentBarSegments(r: any, ddDate: Date | null): { from: Date; to: Date; kind: "actual" | "forecast" }[] {
  const cS = parseISO(r.currentStart), cF = parseISO(r.currentFinish);
  if (!cS || !cF) return [];
  if (r.status === "COMPLETE") return [{ from: cS, to: cF, kind: "actual" }];
  const hasActualStart = !!r.actualStart;
  if (hasActualStart && ddDate) {
    const splitAt = ddDate < cS ? cS : ddDate > cF ? cF : ddDate;
    const segs: { from: Date; to: Date; kind: "actual" | "forecast" }[] = [];
    if (splitAt > cS) segs.push({ from: cS, to: splitAt, kind: "actual" });
    if (cF > splitAt) segs.push({ from: splitAt, to: cF, kind: "forecast" });
    return segs.length ? segs : [{ from: cS, to: cF, kind: "actual" }];
  }
  return [{ from: cS, to: cF, kind: "forecast" }];
}

function tooltipFor(r: any): string {
  const parts = [
    `${r.activityId} — ${r.activityName || ""}`,
    `Baseline: ${formatDataDate(r.baselineStart)} – ${formatDataDate(r.baselineFinish)}`,
    `Current: ${formatDataDate(r.currentStart)} – ${formatDataDate(r.currentFinish)}`,
    `Finish Variance: ${varianceTooltip(r.finishVarianceDays, r.finishVarianceWorkingDays, r.workingDayCalendarAvailable)}`,
    `Progress: ${r.pctComplete != null ? `${r.pctComplete}%` : "Unavailable"}`,
    `Total Float: ${r.totalFloat != null ? `${r.totalFloat}d` : "Unavailable"}`,
    `Status: ${STATUS_LABEL[r.status] || r.status}`,
  ];
  return parts.join("\n");
}

function defaultSortRows(rows: any[]): any[] {
  return [...rows].sort((a, b) => {
    const w = String(a.wbs || "").localeCompare(String(b.wbs || ""));
    if (w !== 0) return w;
    const as = a.baselineStart || "9999-99-99", bs = b.baselineStart || "9999-99-99";
    return as < bs ? -1 : as > bs ? 1 : 0;
  });
}

function loadStoredVisibleCols(): Set<string> {
  try {
    const raw = sessionStorage.getItem(VISIBLE_COLS_STORAGE_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length) return new Set(arr);
    }
  } catch { /* sessionStorage unavailable — fall through to the default */ }
  return new Set(DEFAULT_VISIBLE_COLS);
}

function matchesPreset(visible: Set<string>, presetCols: string[]): boolean {
  if (visible.size !== presetCols.length) return false;
  return presetCols.every((c) => visible.has(c));
}

// Compact "Aug 28 – Sep 25, 2026" range label — year shown once, on the
// later date, matching the format requested for the Look-Ahead range.
function rangeLabel(fromIso: string | null, toIso: string | null): string {
  const f = parseISO(fromIso), t = parseISO(toIso);
  if (!f || !t) return "Unavailable";
  const fromStr = f.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const toStr = t.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  return `${fromStr} – ${toStr}`;
}

function BaselineForecastChart({
  full, lookahead, dataDate, nameById, lookaheadWeeks, currentVersionMeta, baselineVersionMeta,
  projectId, currentVersionId, baselineVersionId, filters, customFrom, customTo,
}: any) {
  const [zoom, setZoom] = useState<Zoom>("week");
  const [groupField, setGroupField] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState("default");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [varianceView, setVarianceView] = useState<"all" | "behind" | "ahead" | "onplan" | "critical">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [visibleCols, setVisibleCols] = useState<Set<string>>(() => loadStoredVisibleCols());
  const [colPanelOpen, setColPanelOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { sessionStorage.setItem(VISIBLE_COLS_STORAGE_KEY, JSON.stringify(Array.from(visibleCols))); } catch { /* non-fatal */ }
  }, [visibleCols]);

  const applyPreset = (key: string) => setVisibleCols(new Set(CHART_PRESETS[key].cols));
  const activePreset = Object.keys(CHART_PRESETS).find((k) => matchesPreset(visibleCols, CHART_PRESETS[k].cols)) || null;

  // "All Activities" is now a Look-Ahead preset (section 9) — the chart's
  // data source follows it directly rather than a separate scope toggle.
  const scope: "lookahead" | "all" = lookaheadWeeks === "all" ? "all" : "lookahead";
  const baseRows: any[] = (scope === "lookahead" ? lookahead?.rows : full?.rows) || [];

  const viewFiltered = useMemo(() => {
    if (varianceView === "all") return baseRows;
    if (varianceView === "behind") return baseRows.filter((r) => BEHIND_STATUSES.has(r.status));
    if (varianceView === "ahead") return baseRows.filter((r) => r.status === "AHEAD");
    if (varianceView === "onplan") return baseRows.filter((r) => r.status === "ON_PLAN");
    if (varianceView === "critical") return baseRows.filter((r) => r.isCritical || (r.totalFloat != null && r.totalFloat > 0 && r.totalFloat <= 10));
    return baseRows;
  }, [baseRows, varianceView]);

  const sorted = useMemo(() => {
    if (sortKey === "default") {
      const d = defaultSortRows(viewFiltered);
      // "Behind Baseline" quick view defaults to worst Finish Variance first.
      return varianceView === "behind" ? [...viewFiltered].sort((a, b) => (b.finishVarianceDays ?? -Infinity) - (a.finishVarianceDays ?? -Infinity)) : d;
    }
    const dir = sortDir === "asc" ? 1 : -1;
    return [...viewFiltered].sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "string") va = va.toLowerCase();
      if (typeof vb === "string") vb = vb.toLowerCase();
      return va < vb ? -1 * dir : va > vb ? 1 * dir : 0;
    });
  }, [viewFiltered, sortKey, sortDir, varianceView]);

  const groups = useMemo(() => {
    if (!groupField) return null;
    const m = new Map<string, any[]>();
    for (const r of sorted) {
      const key = r[groupField] || "Unassigned";
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(r);
    }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [sorted, groupField]);

  type DisplayRow = { type: "group"; key: string; count: number } | { type: "act"; row: any };
  const displayRows: DisplayRow[] = useMemo(() => {
    if (!groups) return sorted.map((row) => ({ type: "act", row }));
    const out: DisplayRow[] = [];
    for (const [key, acts] of groups) {
      out.push({ type: "group", key, count: acts.length });
      if (!collapsedGroups.has(key)) for (const row of acts) out.push({ type: "act", row });
    }
    return out;
  }, [groups, sorted, collapsedGroups]);

  const { rangeStart, rangeEnd } = useMemo(() => {
    let min: Date | null = null, max: Date | null = null;
    for (const r of viewFiltered) {
      for (const k of ["baselineStart", "baselineFinish", "currentStart", "currentFinish"]) {
        const d = parseISO(r[k]);
        if (d && (!min || d < min)) min = d;
        if (d && (!max || d > max)) max = d;
      }
    }
    const dd = parseISO(dataDate);
    if (!min) min = dd || new Date();
    if (!max) max = dd || min;
    min = new Date(min.getFullYear(), min.getMonth(), min.getDate() - 10);
    max = new Date(max.getFullYear(), max.getMonth(), max.getDate() + 10);
    return { rangeStart: min, rangeEnd: max };
  }, [viewFiltered, dataDate]);

  const pxPerDay = PX_PER_DAY[zoom];
  const totalWidth = Math.max(600, daysBetween(rangeStart, rangeEnd) * pxPerDay);
  const xFor = (d: Date | null) => (d ? daysBetween(rangeStart, d) * pxPerDay : 0);

  const headerTicks = useMemo(() => {
    const ticks: { label: string; left: number; width: number }[] = [];
    if (zoom === "day" || zoom === "week") {
      let cur = new Date(rangeStart.getFullYear(), rangeStart.getMonth(), 1);
      while (cur < rangeEnd) {
        const next = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
        const left = Math.max(0, daysBetween(rangeStart, cur)) * pxPerDay;
        const width = daysBetween(cur < rangeStart ? rangeStart : cur, next > rangeEnd ? rangeEnd : next) * pxPerDay;
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
    return ticks;
  }, [rangeStart, rangeEnd, zoom, pxPerDay]);

  const ddDate = parseISO(dataDate);
  const ddX = ddDate ? xFor(ddDate) : null;

  // ── Virtualization — only the rows within the scrolled viewport (+ buffer)
  // are ever mounted, so this stays smooth against thousands of activities. ──
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - BUFFER_ROWS);
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + BUFFER_ROWS * 2;
  const endIdx = Math.min(displayRows.length, startIdx + visibleCount);
  const topSpacer = startIdx * ROW_HEIGHT;
  const bottomSpacer = (displayRows.length - endIdx) * ROW_HEIGHT;

  const selectedRow = selectedId ? viewFiltered.find((r) => r.activityId === selectedId) || baseRows.find((r: any) => r.activityId === selectedId) : null;

  const visibleOptionalCols = OPTIONAL_COLS.filter((c) => visibleCols.has(c.key));
  const middleWidth = visibleOptionalCols.reduce((s, c) => s + c.width, 0);
  const frozenWidth = FROZEN_COLS.reduce((s, c) => s + c.width, 0);

  const exportUrl = useMemo(() => {
    if (!projectId) return "";
    const p = new URLSearchParams();
    if (currentVersionId) p.set("currentVersion", currentVersionId);
    if (baselineVersionId) p.set("baselineVersion", baselineVersionId);
    Object.entries(filters || {}).forEach(([k, v]) => { if (v) p.set(k, String(v)); });
    p.set("scope", scope);
    if (scope === "lookahead") {
      if (lookaheadWeeks === "custom") {
        if (customFrom) p.set("fromDate", customFrom);
        if (customTo) p.set("toDate", customTo);
      } else {
        p.set("lookaheadWeeks", lookaheadWeeks);
      }
    }
    return `${API}/api/projects/${projectId}/baseline-progress/export/?${p.toString()}`;
  }, [projectId, currentVersionId, baselineVersionId, filters, scope, lookaheadWeeks, customFrom, customTo]);

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Baseline vs. Current / Forecast Activity Chart</div>
          <div style={{ fontSize: 11, color: C.muted2 }}>{displayRows.filter((r) => r.type === "act").length.toLocaleString()} of {baseRows.length.toLocaleString()} activities shown</div>
        </div>
        <a href={exportUrl} download style={{ background: C.green, color: "#fff", border: "none", borderRadius: 7, padding: "6px 13px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", textDecoration: "none" }}>⬇ Export to Excel</a>
      </div>

      {/* Data Date — unmistakable, matches the toolbar/main workspace value */}
      <div style={{ fontSize: 12, fontWeight: 700, color: C.purple, marginBottom: 10 }}>
        DATA DATE — {formatDataDate(dataDate)}
        {currentVersionMeta?.dataDateOverridden && (
          <span style={{ marginLeft: 6, fontWeight: 600, color: C.gold, cursor: "help" }}
            title={currentVersionMeta.sourceDataDate ? `Source file Data Date: ${formatDataDate(currentVersionMeta.sourceDataDate)}` : "Manually entered — no Data Date was detected in the source file"}>
            · Overridden ⓘ
          </span>
        )}
        {lookahead?.window?.available && (
          <span style={{ marginLeft: 14, fontWeight: 600, color: C.muted2 }}>LOOK-AHEAD {rangeLabel(lookahead.window.fromDate, lookahead.window.toDate)}</span>
        )}
      </div>

      {/* View presets — display-only column selections, no calculation change */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", marginBottom: 10 }}>
        {Object.entries(CHART_PRESETS).map(([key, preset]) => (
          <button key={key} onClick={() => applyPreset(key)} style={{ background: activePreset === key ? C.accent : C.card2, color: activePreset === key ? "#fff" : C.muted2, border: `1px solid ${activePreset === key ? C.accent : C.border}`, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>{preset.label}</button>
        ))}
        <div style={{ position: "relative" }}>
          <button onClick={() => setColPanelOpen((o) => !o)} style={{ background: activePreset ? C.card2 : `${C.accent}18`, color: activePreset ? C.muted2 : C.accent, border: `1px solid ${activePreset ? C.border : C.accent}`, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>⚙ Columns{!activePreset ? " (Custom)" : ""}</button>
          {colPanelOpen && (
            <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, zIndex: 100, minWidth: 200, maxHeight: 320, overflowY: "auto", boxShadow: "0 8px 24px rgba(0,0,0,0.15)" }}>
              {OPTIONAL_COLS.map((c) => (
                <label key={c.key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "3px 0", cursor: "pointer" }}>
                  <input type="checkbox" checked={visibleCols.has(c.key)}
                    onChange={() => setVisibleCols((prev) => { const n = new Set(prev); n.has(c.key) ? n.delete(c.key) : n.add(c.key); return n; })} />
                  {c.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Toolbar: zoom / group / sort / variance view */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
        {(["day", "week", "month", "quarter"] as Zoom[]).map((z) => (
          <button key={z} onClick={() => setZoom(z)} style={{ background: zoom === z ? C.accent : C.card2, color: zoom === z ? "#fff" : C.muted2, border: `1px solid ${zoom === z ? C.accent : C.border}`, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", textTransform: "capitalize" }}>{z}</button>
        ))}
        <Select value={groupField} onChange={setGroupField} options={GROUP_FIELDS} />
        <Select value={sortKey} onChange={setSortKey} options={SORT_OPTIONS} />
        {sortKey !== "default" && (
          <button onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))} style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 7, padding: "5px 9px", cursor: "pointer", fontSize: 12 }}>{sortDir === "asc" ? "↑" : "↓"}</button>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
        {VARIANCE_VIEWS.map((v) => (
          <button key={v.value} onClick={() => setVarianceView(v.value as any)} style={{ background: varianceView === v.value ? `${C.accent}18` : "transparent", border: `1px solid ${varianceView === v.value ? C.accent : C.border}`, color: varianceView === v.value ? C.accent : C.muted2, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>{v.label}</button>
        ))}
      </div>

      {baseRows.length === 0 ? (
        <div style={{ padding: 30, textAlign: "center", color: C.muted2, fontSize: 12 }}>No activities in the current scope/filters.</div>
      ) : (
        <div ref={scrollRef} onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
          style={{ display: "flex", alignItems: "flex-start", height: VIEWPORT_HEIGHT, overflowY: "auto", overflowX: "hidden", border: `1px solid ${C.border}`, borderRadius: 8 }}>
          {/* Frozen columns — Activity ID + Activity Name only stay fixed
              while everything else (optional data columns, then the
              timeline) scrolls horizontally together in the region below. */}
          <div style={{ width: frozenWidth, flexShrink: 0, borderRight: `2px solid ${C.border}` }}>
            <div style={{ position: "sticky", top: 0, zIndex: 3, display: "flex", height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}`, alignItems: "center" }}>
              {FROZEN_COLS.map((c) => (
                <div key={c.key} style={{ width: c.width, padding: "0 7px", fontSize: 9, fontWeight: 700, color: C.muted, textTransform: "uppercase", whiteSpace: "nowrap", overflow: "hidden" }}>{c.label}</div>
              ))}
            </div>
            <div style={{ height: topSpacer }} />
            {displayRows.slice(startIdx, endIdx).map((dr, i) => {
              if (dr.type === "group") {
                const collapsed = collapsedGroups.has(dr.key);
                return (
                  <div key={`g-${dr.key}-${startIdx + i}`} onClick={() => setCollapsedGroups((prev) => { const n = new Set(prev); n.has(dr.key) ? n.delete(dr.key) : n.add(dr.key); return n; })}
                    style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", gap: 6, padding: "0 8px", background: C.panel, cursor: "pointer", fontSize: 11, fontWeight: 700, color: C.text, borderBottom: `1px solid ${C.border}` }}>
                    <span style={{ fontSize: 10 }}>{collapsed ? "▶" : "▼"}</span>{dr.key} <span style={{ color: C.muted, fontWeight: 400 }}>({dr.count})</span>
                  </div>
                );
              }
              const r = dr.row;
              const isSel = selectedId === r.activityId;
              return (
                <div key={`a-${r.activityId}-${startIdx + i}`} onClick={() => setSelectedId(r.activityId)}
                  style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", cursor: "pointer", background: isSel ? `${C.accent}12` : "transparent", borderBottom: `1px solid ${C.border}` }}>
                  {FROZEN_COLS.map((c) => (
                    <div key={c.key} title={cellText(r, c.key)} style={{ width: c.width, padding: "0 7px", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: C.text, fontWeight: c.key === "activityId" ? 600 : 400, fontFamily: c.key === "activityId" ? "monospace" : "inherit" }}>
                      {cellText(r, c.key)}
                    </div>
                  ))}
                </div>
              );
            })}
            <div style={{ height: bottomSpacer }} />
          </div>

          {/* Optional data columns + timeline, sharing ONE horizontal scroll
              region — scrolling right first reveals hidden columns, then
              the timeline, giving the timeline far more usable space by
              default (only Activity ID/Name are permanently frozen). */}
          <div style={{ flex: 1, overflowX: "auto", overflowY: "hidden" }}>
            <div style={{ width: middleWidth + totalWidth, position: "relative" }}>
              <div style={{ position: "sticky", top: 0, zIndex: 2, display: "flex", height: HEADER_HEIGHT, background: C.card2, borderBottom: `1px solid ${C.border}`, alignItems: "center" }}>
                {visibleOptionalCols.map((c) => (
                  <div key={c.key} style={{ width: c.width, flexShrink: 0, padding: "0 7px", fontSize: 9, fontWeight: 700, color: C.muted, textTransform: "uppercase", whiteSpace: "nowrap", overflow: "hidden", borderRight: `1px solid ${C.border}` }}>{c.label}</div>
                ))}
                <div style={{ width: totalWidth, position: "relative", height: "100%" }}>
                  {headerTicks.map((t, i) => (
                    <div key={i} style={{ position: "absolute", left: t.left, width: t.width, top: 0, height: "100%", borderLeft: `1px solid ${C.border}`, fontSize: 10, color: C.muted2, padding: "4px 4px", boxSizing: "border-box", whiteSpace: "nowrap", overflow: "hidden" }}>{t.label}</div>
                  ))}
                </div>
              </div>

              {/* Data Date line + label — spans the full scrollable region,
                  drawn once (not per-row), aligned exactly with ddX. */}
              {ddX !== null && (
                <>
                  <div style={{ position: "absolute", left: middleWidth + ddX, top: HEADER_HEIGHT, bottom: 0, width: 1.5, background: C.purple, zIndex: 1 }} title={`Data Date — ${formatDataDate(dataDate)}`} />
                  <div style={{ position: "absolute", left: middleWidth + ddX + 4, top: HEADER_HEIGHT + 2, fontSize: 9, fontWeight: 700, color: C.purple, background: `${C.card}dd`, padding: "1px 4px", borderRadius: 3, whiteSpace: "nowrap", zIndex: 1, pointerEvents: "none" }}>
                    DATA DATE — {formatDataDate(dataDate)}
                  </div>
                </>
              )}

              <div style={{ height: topSpacer }} />
              {displayRows.slice(startIdx, endIdx).map((dr, i) => {
                if (dr.type === "group") {
                  return <div key={`tg-${startIdx + i}`} style={{ height: ROW_HEIGHT, display: "flex", borderBottom: `1px solid ${C.border}`, background: C.panel }}>
                    <div style={{ width: middleWidth + totalWidth }} />
                  </div>;
                }
                const r = dr.row;
                const isSel = selectedId === r.activityId;
                const color = STATUS_COLOR[r.status] || C.accent;
                const bS = parseISO(r.baselineStart), bF = parseISO(r.baselineFinish);

                const rowBody = r.isMilestone ? (() => {
                  const bX = bF || bS ? xFor(bF || bS) : null;
                  const cDate = parseISO(r.currentFinish) || parseISO(r.currentStart);
                  const cX = cDate ? xFor(cDate) : null;
                  return (
                    <>
                      {bX !== null && cX !== null && Math.abs(bX - cX) > 2 && (
                        <div style={{ position: "absolute", left: Math.min(bX, cX), width: Math.abs(bX - cX), top: ROW_HEIGHT / 2 - 0.5, height: 1, borderTop: `1px dashed ${C.muted2}` }} />
                      )}
                      {bX !== null && <div style={{ position: "absolute", left: bX - 5, top: 6, width: 10, height: 10, background: C.card, border: `2px solid ${C.muted2}`, transform: "rotate(45deg)" }} title="Baseline milestone date" />}
                      {cX !== null && <div style={{ position: "absolute", left: cX - 5, top: 18, width: 10, height: 10, background: color, transform: "rotate(45deg)", border: isSel ? `2px solid ${C.text}` : "none" }} title="Current milestone date" />}
                    </>
                  );
                })() : (() => {
                  const segs = currentBarSegments(r, ddDate);
                  return (
                    <>
                      {bS && bF && (
                        <div style={{ position: "absolute", left: xFor(bS), width: Math.max(2, xFor(bF) - xFor(bS)), top: 6, height: 6, background: C.border, border: `1px solid ${C.muted2}`, borderRadius: 2 }} />
                      )}
                      {segs.map((seg, si) => (
                        <div key={si} style={{
                          position: "absolute", left: xFor(seg.from), width: Math.max(2, xFor(seg.to) - xFor(seg.from)), top: 16, height: 10, borderRadius: 2,
                          background: seg.kind === "actual" ? color : `repeating-linear-gradient(135deg, ${color} 0, ${color} 3px, ${color}30 3px, ${color}30 7px)`,
                          border: `1.5px solid ${color}`, borderStyle: seg.kind === "actual" ? "solid" : "dashed",
                        }} />
                      ))}
                    </>
                  );
                })();

                return (
                  <div key={`row-${r.activityId}-${startIdx + i}`} title={tooltipFor(r)} onClick={() => setSelectedId(r.activityId)}
                    style={{ height: ROW_HEIGHT, display: "flex", alignItems: "center", borderBottom: `1px solid ${C.border}`, background: isSel ? `${C.accent}08` : "transparent", cursor: "pointer" }}>
                    {visibleOptionalCols.map((c) => {
                      const varianceColor = (c.key === "startVarianceDays" || c.key === "finishVarianceDays") && r[c.key] != null ? (r[c.key] > 0 ? C.red : r[c.key] < 0 ? C.green : C.text) : undefined;
                      const floatColor = c.key === "totalFloat" && r.totalFloat != null && r.totalFloat < 0 ? C.red : undefined;
                      return (
                        <div key={c.key} title={cellText(r, c.key)} style={{ width: c.width, flexShrink: 0, padding: "0 7px", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: c.key === "status" ? STATUS_COLOR[r.status] : (varianceColor || floatColor || C.text), fontWeight: c.key === "status" || varianceColor ? 700 : 400, borderRight: `1px solid ${C.border}` }}>
                          {cellText(r, c.key)}
                        </div>
                      );
                    })}
                    <div style={{ width: totalWidth, position: "relative", height: "100%" }}>{rowBody}</div>
                  </div>
                );
              })}
              <div style={{ height: bottomSpacer }} />
            </div>
          </div>
        </div>
      )}

      <div style={{ fontSize: 10, color: C.muted2, marginTop: 10, display: "flex", gap: 16, flexWrap: "wrap" }}>
        <span><span style={{ display: "inline-block", width: 12, height: 5, background: C.border, border: `1px solid ${C.muted2}`, borderRadius: 2, marginRight: 4 }} />Baseline</span>
        <span><span style={{ display: "inline-block", width: 12, height: 8, background: C.accent, borderRadius: 2, marginRight: 4 }} />Actual / statused (solid)</span>
        <span><span style={{ display: "inline-block", width: 12, height: 8, background: `repeating-linear-gradient(135deg, ${C.accent} 0, ${C.accent} 3px, ${C.accent}30 3px, ${C.accent}30 7px)`, border: `1.5px dashed ${C.accent}`, borderRadius: 2, marginRight: 4 }} />Remaining / forecast (hatched, colored by status)</span>
        <span><span style={{ display: "inline-block", width: 9, height: 9, border: `2px solid ${C.muted2}`, transform: "rotate(45deg)", marginRight: 6 }} />Baseline milestone</span>
        <span><span style={{ display: "inline-block", width: 9, height: 9, background: C.purple, transform: "rotate(45deg)", marginRight: 6 }} />Current milestone</span>
        <span><span style={{ display: "inline-block", width: 1.5, height: 10, background: C.purple, marginRight: 4 }} />Data Date</span>
      </div>

      {selectedRow && (
        <ActivityDetailDrawer
          row={selectedRow} dataDate={dataDate} nameById={nameById} onClose={() => setSelectedId(null)}
          currentVersionMeta={currentVersionMeta} baselineVersionMeta={baselineVersionMeta}
          currentVersionLabel={full?.currentVersionLabel} baselineVersionLabel={full?.baselineVersionLabel}
        />
      )}
    </div>
  );
}

function ActivityDetailDrawer({
  row, dataDate, nameById, onClose, currentVersionMeta, baselineVersionMeta, currentVersionLabel, baselineVersionLabel,
}: {
  row: any; dataDate: string; nameById: Record<string, string>; onClose: () => void;
  currentVersionMeta?: any; baselineVersionMeta?: any; currentVersionLabel?: string; baselineVersionLabel?: string;
}) {
  const r = row;
  const relLabel = (rel: any) => {
    const name = nameById[rel.actId];
    return `${rel.actId}${name ? ` — ${name}` : ""} (${rel.relType || "FS"}${rel.lagDays ? ` ${rel.lagDays > 0 ? "+" : ""}${rel.lagDays}d` : ""})`;
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(28,20,16,0.45)", zIndex: 1500, display: "flex", justifyContent: "flex-end" }} onClick={onClose}>
      <div style={{ width: 440, maxWidth: "92vw", background: C.bg, borderLeft: `1px solid ${C.border}`, height: "100%", overflowY: "auto", padding: "20px 22px" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 11, color: C.accent, fontFamily: "monospace", fontWeight: 700 }}>{r.activityId}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{r.activityName}</div>
            <div style={{ fontSize: 11, color: C.muted2, marginTop: 2 }}>{r.wbs} · <span style={{ color: STATUS_COLOR[r.status], fontWeight: 700 }}>{STATUS_LABEL[r.status] || r.status}</span></div>
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, fontSize: 20, cursor: "pointer" }}>×</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 14px", marginBottom: 16 }}>
          {([
            ["Baseline Start", formatDataDate(r.baselineStart)],
            ["Baseline Finish", formatDataDate(r.baselineFinish)],
            ["Actual Start", formatDataDate(r.actualStart)],
            ["Actual Finish", formatDataDate(r.actualFinish)],
            ["Current / Remaining Start", formatDataDate(r.remainingStart || r.currentStart)],
            ["Current / Forecast Finish", formatDataDate(r.currentFinish)],
            ["% Complete", r.pctComplete != null ? `${r.pctComplete}%` : "—"],
            ["Remaining Duration", r.remainingDuration != null ? `${r.remainingDuration}d` : "—"],
            ["Total Float", r.totalFloat != null ? `${r.totalFloat}d` : "—"],
            ["Assigned Calendar", r.calendarName || r.calendarId || "Unavailable"],
            ["Effective Data Date", formatDataDate(dataDate)],
          ] as [string, string][]).map(([label, value]) => (
            <div key={label}>
              <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
              <div style={{ fontSize: 12, color: label === "Total Float" && r.totalFloat < 0 ? C.red : C.text, fontWeight: 600 }}>{value}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 14px", marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Start Variance</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: r.startVarianceDays > 0 ? C.red : r.startVarianceDays < 0 ? C.green : C.text }}>
              {r.startVarianceDays != null ? `${r.startVarianceDays > 0 ? "+" : ""}${r.startVarianceDays} calendar days` : "Unavailable"}
            </div>
            {r.workingDayCalendarAvailable && r.startVarianceWorkingDays != null ? (
              <div style={{ fontSize: 10, color: C.muted2 }}>{r.startVarianceWorkingDays > 0 ? "+" : ""}{r.startVarianceWorkingDays} working days</div>
            ) : (
              <div style={{ fontSize: 10, color: C.muted2, fontStyle: "italic" }}>Working-day variance unavailable — no detailed P6 calendar decoded for this activity.</div>
            )}
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Finish Variance</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: r.finishVarianceDays > 0 ? C.red : r.finishVarianceDays < 0 ? C.green : C.text }}>
              {r.finishVarianceDays != null ? `${r.finishVarianceDays > 0 ? "+" : ""}${r.finishVarianceDays} calendar days` : "Unavailable"}
            </div>
            {r.workingDayCalendarAvailable && r.finishVarianceWorkingDays != null ? (
              <div style={{ fontSize: 10, color: C.muted2 }}>{r.finishVarianceWorkingDays > 0 ? "+" : ""}{r.finishVarianceWorkingDays} working days</div>
            ) : (
              <div style={{ fontSize: 10, color: C.muted2, fontStyle: "italic" }}>Working-day variance unavailable — no detailed P6 calendar decoded for this activity.</div>
            )}
          </div>
        </div>

        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", marginBottom: 6 }}>Source Traceability</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 14px", marginBottom: 16, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Baseline Version</div>
            <div style={{ fontSize: 11, color: C.text, fontWeight: 600 }}>{baselineVersionLabel || "None designated"}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Current Version</div>
            <div style={{ fontSize: 11, color: C.text, fontWeight: 600 }}>{currentVersionLabel || "—"}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Effective Data Date</div>
            <div style={{ fontSize: 11, color: C.text, fontWeight: 600 }}>{formatDataDate(dataDate)}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Source Data Date</div>
            <div style={{ fontSize: 11, color: C.text, fontWeight: 600 }}>{currentVersionMeta?.sourceDataDate ? formatDataDate(currentVersionMeta.sourceDataDate) : "Same as effective"}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Data Date Override</div>
            <div style={{ fontSize: 11, color: currentVersionMeta?.dataDateOverridden ? C.gold : C.text, fontWeight: 600 }}>{currentVersionMeta?.dataDateOverridden ? "Overridden by user" : "Not overridden"}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Total Float</div>
            <div style={{ fontSize: 11, color: r.totalFloat < 0 ? C.red : C.text, fontWeight: 600 }}>{r.totalFloat != null ? `${r.totalFloat}d (imported P6 value)` : "Unavailable"}</div>
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>Variance Unit</div>
            <div style={{ fontSize: 11, color: C.text, fontWeight: 600 }}>
              {r.workingDayCalendarAvailable ? "Working days (decoded P6 activity calendar)" : "Calendar days (no detailed P6 calendar decoded for this activity)"}
            </div>
          </div>
        </div>

        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", marginBottom: 6 }}>Predecessors ({(r.predecessors || []).length})</div>
        {(r.predecessors || []).length === 0 && <div style={{ fontSize: 11, color: C.muted2, marginBottom: 10 }}>None recorded.</div>}
        {(r.predecessors || []).slice(0, 12).map((p: any, i: number) => <div key={i} style={{ fontSize: 11, color: C.text, marginBottom: 3 }}>← {relLabel(p)}</div>)}

        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", margin: "12px 0 6px" }}>Successors ({(r.successors || []).length})</div>
        {(r.successors || []).length === 0 && <div style={{ fontSize: 11, color: C.muted2 }}>None recorded.</div>}
        {(r.successors || []).slice(0, 12).map((s: any, i: number) => <div key={i} style={{ fontSize: 11, color: C.text, marginBottom: 3 }}>→ {relLabel(s)}</div>)}
      </div>
    </div>
  );
}
