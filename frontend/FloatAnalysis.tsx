import { useEffect, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  BarChart, Bar, ScatterChart, Scatter, LineChart, Line, XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell, Legend,
} from "recharts";
import { versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 130 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function useProjectsAndVersions(initialProjectId?: string, initialVersionId?: string) {
  const [projects, setProjects] = useState<any[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);   // stale responses from a previously selected project are dropped (see projectScope.ts)
  const [versions, setVersions] = useState<any[]>([]);
  const [versionId, setVersionId] = useState("");

  useEffect(() => { fetch(`${API}/api/projects/?withVersions=true`).then(r => r.json()).then(d => { const list = d.projects || []; setProjects(list); setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur)); }).catch(() => {}); }, [projectsTick]);
  useEffect(() => {
    if (!projectId) { setVersions([]); setVersionId(""); return; }
    let cancelled = false;
    sfetch(`${API}/api/projects/${projectId}/versions/`).then(r => r.json()).then(d => {
      if (cancelled) return;
      const vs = d.versions || [];
      setVersions(dedupeVersionsById(vs));
      if (initialVersionId && vs.some((v: any) => v.id === initialVersionId)) { setVersionId(initialVersionId); return; }
      const cur = vs.find((v: any) => v.role === "CURRENT");
      setVersionId(cur?.id || vs[0]?.id || "");
    }).catch(() => { if (!cancelled) setVersions([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projectsTick]);

  // Switching project clears the previous project's versions/versionId in the SAME
  // render, and only a versionId that belongs to the CURRENT project's loaded
  // versions is ever exposed - so a fetch can never be issued as
  // /projects/NEW/...?version=OLD ("No schedule version available for this project").
  const selectProject = (id: string) => { setVersions([]); setVersionId(""); setProjectId(id); };
  const safeVersionId = versions.some((v: any) => v.id === versionId) ? versionId : "";
  return { projects, projectId, setProjectId: selectProject, versions, versionId: safeVersionId, setVersionId };
}

function ProjectVersionBar({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 16 }}>
      <div>
        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
        <Select value={ctx.projectId} onChange={ctx.setProjectId} placeholder="Select a project…"
          options={ctx.projects.map((p: any) => ({ value: p.id, label: `${p.name} (${p.versionCount})` }))} />
      </div>
      {ctx.projectId && ctx.versions.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Version</div>
          <Select value={ctx.versionId} onChange={ctx.setVersionId}
            options={ctx.versions.map((v: any) => ({ value: v.id, label: versionSelectLabel(v) }))} />
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, color, sub }: { label: string; value: any; color?: string; sub?: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 16px", flex: "1 1 140px" }}>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || C.text }}>{value ?? "Unavailable"}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2 }}>{sub}</div>}
    </div>
  );
}

function TT({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px", fontSize: 11 }}>
      <div style={{ fontWeight: 700, marginBottom: 2 }}>{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color }}>{p.name}: {p.value}</div>
      ))}
    </div>
  );
}

// ─── DETERIORATION / IMPROVEMENT / MILESTONE TABLES ────────────────────────

function RankTable({ title, rows, colorField, onSelect }: { title: string; rows: any[]; colorField: string; onSelect?: (id: string) => void }) {
  const [limit, setLimit] = useState<number | "all">(10);
  const shown = limit === "all" ? rows : rows.slice(0, limit);
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{title} ({rows.length})</div>
        <div style={{ display: "flex", gap: 4 }}>
          {[10, 25, 50, "all"].map(n => (
            <button key={n} onClick={() => setLimit(n as any)}
              style={{ background: limit === n ? `${C.accent}18` : "transparent", border: `1px solid ${limit === n ? C.accent : C.border}`, color: limit === n ? C.accent : C.muted2, borderRadius: 5, padding: "2px 8px", cursor: "pointer", fontSize: 10, fontWeight: 700 }}>
              {n === "all" ? "All" : n}
            </button>
          ))}
        </div>
      </div>
      <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 8, background: C.card, maxHeight: 260, overflowY: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
          <thead><tr style={{ background: C.card2, position: "sticky", top: 0 }}>
            {["Activity ID", "Name", "WBS", "Baseline TF", "Previous TF", "Current TF", "Δ vs Baseline", "Finish Var", "Finish", "Critical", "Driving"].map(h => (
              <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {shown.map((r: any) => (
              <tr key={r.activityId} onClick={() => onSelect && onSelect(r.activityId)} style={{ borderBottom: `1px solid ${C.border}`, cursor: onSelect ? "pointer" : undefined }}>
                <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{r.activityId}</td>
                <td style={{ padding: "4px 8px", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.activityName}</td>
                <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.wbs || "—"}</td>
                <td style={{ padding: "4px 8px" }}>{r.baselineTotalFloat ?? "—"}</td>
                <td style={{ padding: "4px 8px" }}>{r.previousTotalFloat ?? "—"}</td>
                <td style={{ padding: "4px 8px", color: r.currentTotalFloat != null && r.currentTotalFloat < 0 ? C.red : C.text, fontWeight: 700 }}>{r.currentTotalFloat ?? "—"}</td>
                <td style={{ padding: "4px 8px", color: r[colorField] > 0 ? C.green : r[colorField] < 0 ? C.red : C.muted2 }}>{r.floatChangeVsBaseline ?? "—"}</td>
                <td style={{ padding: "4px 8px" }}>{r.finishVarianceDays ?? "—"}</td>
                <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.currentFinish || "—"}</td>
                <td style={{ padding: "4px 8px" }}>{r.critical ? "Yes" : "—"}</td>
                <td style={{ padding: "4px 8px" }}>{r.driving ? "Yes" : "—"}</td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan={11} style={{ padding: 14, textAlign: "center", color: C.muted }}>None.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── FLOAT TREND (separate fetch — needs every project version) ───────────

function FloatTrendPanel({ ctx, activityId }: { ctx: ReturnType<typeof useProjectsAndVersions>; activityId: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    if (!ctx.projectId || !activityId) return;
    sfetch(`${API}/api/projects/${ctx.projectId}/float-trend/?activityId=${encodeURIComponent(activityId)}`)
      .then(r => r.json()).then(setData);
  }, [ctx.projectId, activityId]);

  if (!data) return null;
  const points = (data.points || []).filter((p: any) => p.found).map((p: any) => ({
    ...p, label: `${p.versionLabel}${p.complete ? " (Complete)" : ""}`,
  }));
  const hasCompletePoint = points.some((p: any) => p.complete);
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 6 }}>Float Trend Across Versions — {activityId}</div>
      {points.length === 0 ? (
        <div style={{ fontSize: 12, color: C.muted }}>Activity not found in any version, or no versions available.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={points} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis dataKey="label" tick={{ fill: C.muted, fontSize: 10 }} />
              <YAxis tick={{ fill: C.muted, fontSize: 10 }} />
              <Tooltip content={({ active, payload, label }: any) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                return (
                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px", fontSize: 11 }}>
                    <div style={{ fontWeight: 700, marginBottom: 2 }}>{label}</div>
                    {p.complete
                      ? <div style={{ color: C.muted2 }}>Current Total Float: — (Activity Complete)<br />Imported P6 TF at this version: {p.importedTotalFloat ?? "Unavailable"}</div>
                      : <div style={{ color: C.accent }}>Total Float (imported P6): {p.totalFloat ?? "Unavailable"}</div>}
                  </div>
                );
              }} />
              <ReferenceLine y={0} stroke={C.red} strokeDasharray="3 3" />
              <Line type="monotone" dataKey="totalFloat" name="Total Float (imported P6)" stroke={C.accent} strokeWidth={2} dot={{ r: 4 }} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
          {hasCompletePoint && (
            <div style={{ fontSize: 10, color: C.muted2, marginTop: 4 }}>
              A gap in the line marks a version where the activity is Complete — Current Total Float displays as unavailable ("—"), not its stored/imported value, so it is never misread as critical. Hover a version for its imported P6 figure.
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── ROOT ───────────────────────────────────────────────────────────────────

type FloatBandFilter = "" | "negative" | "zero" | "nearCritical";
const FLOAT_BAND_PARAM: Record<Exclude<FloatBandFilter, "">, string> = {
  negative: "negativeFloatOnly", zero: "zeroFloatOnly", nearCritical: "nearCriticalOnly",
};

export default function FloatAnalysis({ initialProjectId, initialVersionId, initialActivityId, initialFloatFilter }: {
  initialProjectId?: string; initialVersionId?: string; initialActivityId?: string;
  // One-shot deep-link seed (Main Dashboard's Float Health panel). Maps
  // straight onto the matching backend row filter — see
  // _filter_analysis_rows in views.py — never a client-side float-band
  // calculation.
  initialFloatFilter?: "negative" | "zero" | "nearCritical";
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [heatmapGroupBy, setHeatmapGroupBy] = useState("area");
  const [trendActivityId, setTrendActivityId] = useState<string>(initialActivityId || "");
  const [crossFilters, setCrossFilters] = useState<{ area: string; wbs: string; discipline: string; contractor: string; system: string }>({
    area: "", wbs: "", discipline: "", contractor: "", system: "",
  });
  const [includeCompleted, setIncludeCompleted] = useState(false);
  const [floatBandFilter, setFloatBandFilter] = useState<FloatBandFilter>(initialFloatFilter || "");

  useEffect(() => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    const params = new URLSearchParams({ currentVersion: ctx.versionId, heatmapGroupBy });
    Object.entries(crossFilters).forEach(([k, v]) => { if (v) params.set(k, v); });
    if (includeCompleted) params.set("includeCompleted", "true");
    if (floatBandFilter) params.set(FLOAT_BAND_PARAM[floatBandFilter], "true");
    sfetch(`${API}/api/projects/${ctx.projectId}/float-analysis/?${params.toString()}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  }, [ctx.projectId, ctx.versionId, heatmapGroupBy, crossFilters, includeCompleted, floatBandFilter]);

  const s = data?.summary;

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Float Analysis</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        Graphical float intelligence built on Activity Analysis's master rows. Imported P6 Total Float is source truth throughout — nothing here recalculates it.
      </div>

      <ProjectVersionBar ctx={ctx} />

      {ctx.projectId && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: C.muted }}>Filter:</span>
          {(["area", "wbs", "discipline", "contractor", "system"] as const).map(f => (
            <input key={f} value={crossFilters[f]} onChange={e => setCrossFilters(p => ({ ...p, [f]: e.target.value }))}
              placeholder={f[0].toUpperCase() + f.slice(1)}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "5px 9px", fontSize: 11, fontFamily: "inherit", width: 100 }} />
          ))}
          {Object.values(crossFilters).some(Boolean) && (
            <button onClick={() => setCrossFilters({ area: "", wbs: "", discipline: "", contractor: "", system: "" })}
              style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 10, textDecoration: "underline" }}>
              Clear filters
            </button>
          )}
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: C.muted, cursor: "pointer", marginLeft: 6 }}>
            <input type="checkbox" checked={includeCompleted} onChange={e => setIncludeCompleted(e.target.checked)} />
            Include Completed Activities (audit)
          </label>
          <Select value={floatBandFilter} onChange={(v: string) => setFloatBandFilter(v as FloatBandFilter)}
            placeholder="All Float"
            options={[
              { value: "negative", label: "Negative Float Only" },
              { value: "zero", label: "Zero Float Only" },
              { value: "nearCritical", label: `Near Critical Only (≤${data?.nearCriticalThresholdDays ?? "…"}d)` },
            ]} />
          <span style={{ fontSize: 10, color: C.muted2 }}>Every chart and table below recalculates for the filtered scope — never a stale whole-project view.</span>
        </div>
      )}

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && <div style={{ color: C.accent, padding: 20 }}>Computing float analysis…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>}

      {ctx.projectId && data && (
        <>
          <div style={{ fontSize: 11, color: C.muted, marginBottom: 10 }}>
            Data Date {data.currentDataDate || "Unavailable"} · Baseline: {data.baselineVersionLabel || "Unavailable"} · Previous: {data.previousVersionLabel || "Unavailable"}
            {!data.calendarConfidence?.available && <span style={{ color: C.amber }}> · ⚠ Working-day figures unavailable — no confidently decoded calendar for {data.calendarConfidence?.totalActivities ?? "?"} activities.</span>}
          </div>

          {/* 1. KPI Summary */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
            <MetricCard label="Activities Analyzed" value={s?.activitiesAnalyzed} />
            <MetricCard label="Negative Float" value={s?.negativeFloatCount} color={s?.negativeFloatCount > 0 ? C.red : undefined} />
            <MetricCard label="Zero Float" value={s?.zeroFloatCount} />
            <MetricCard label="Near Critical" value={s?.nearCriticalCount} color={C.amber} />
            <MetricCard label="Critical" value={s?.criticalCount} color={C.red} />
            <MetricCard label="Driving" value={s?.drivingCount} color={C.purple} />
            <MetricCard label="Newly Negative" value={s?.newlyNegativeCount} color={s?.newlyNegativeCount > 0 ? C.red : undefined} />
            <MetricCard label="Recovered From Negative" value={s?.recoveredFromNegativeCount} color={C.green} />
            <MetricCard label="Median Current TF" value={s?.medianCurrentTotalFloat} />
            <MetricCard label="Minimum Current TF" value={s?.minimumCurrentTotalFloat} />
            <MetricCard label="Baseline Median TF" value={s?.baselineMedianTotalFloat} />
            <MetricCard label="Median Float Change" value={s?.medianFloatChange} />
            <MetricCard label="Completed (Excluded)" value={s?.completedActivitiesExcluded} sub={includeCompleted ? undefined : "TF displays — for these"} />
          </div>
          {!includeCompleted && (s?.completedActivitiesExcluded ?? 0) > 0 && (
            <div style={{ fontSize: 10, color: C.muted2, marginTop: -12, marginBottom: 16 }}>
              {s.completedActivitiesExcluded} completed {s.completedActivitiesExcluded === 1 ? "activity displays" : "activities display"} Current Total Float as "—" and {s.completedActivitiesExcluded === 1 ? "is" : "are"} excluded from the counts, histogram, heat map and rankings above — their imported P6 float is unchanged and available via Source Traceability. Check "Include Completed Activities" to audit them with their stored value restored.
            </div>
          )}

          {/* 2. Float Distribution Histogram */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Float Distribution</div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={data.distribution.buckets} margin={{ left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                <XAxis dataKey="range" tick={{ fill: C.muted, fontSize: 9 }} angle={-35} textAnchor="end" height={50} />
                <YAxis tick={{ fill: C.muted, fontSize: 10 }} />
                <Tooltip content={<TT />} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                <Bar dataKey="current" name="Current" fill={C.accent} radius={[3, 3, 0, 0]} />
                {data.distribution.hasBaseline && <Bar dataKey="baseline" name="Baseline" fill={C.muted2} radius={[3, 3, 0, 0]} />}
                {data.distribution.hasPrevious && <Bar dataKey="previous" name="Previous" fill={C.gold} radius={[3, 3, 0, 0]} />}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* 3. Baseline vs Current Scatter */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Baseline vs Current Float</div>
            <ResponsiveContainer width="100%" height={260}>
              <ScatterChart margin={{ left: -10, top: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                <XAxis type="number" dataKey="baselineTotalFloat" name="Baseline TF" tick={{ fill: C.muted, fontSize: 10 }} />
                <YAxis type="number" dataKey="currentTotalFloat" name="Current TF" tick={{ fill: C.muted, fontSize: 10 }} />
                <ZAxis range={[40, 40]} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload;
                  return <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px", fontSize: 11 }}>{p.activityId} — {p.activityName}<br />Baseline {p.baselineTotalFloat}d → Current {p.currentTotalFloat}d</div>;
                }} />
                <ReferenceLine y={0} stroke={C.red} strokeDasharray="3 3" />
                <ReferenceLine x={0} stroke={C.red} strokeDasharray="3 3" />
                <Scatter data={data.baselineVsCurrentScatter} fill={C.accent}>
                  {data.baselineVsCurrentScatter.map((p: any, i: number) => (
                    <Cell key={i} fill={p.critical ? C.red : C.accent} fillOpacity={0.6} />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
            <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>Below the diagonal (Current &lt; Baseline) = deterioration. Above = improvement. Red = currently critical.</div>
          </div>

          {/* 4. Float Change Distribution */}
          {data.floatChangeDistribution && (
            <div style={{ display: "flex", gap: 10, marginBottom: 18 }}>
              <MetricCard label="Deteriorated" value={data.floatChangeDistribution.deterioratedCount} color={C.red} />
              <MetricCard label="Stable" value={data.floatChangeDistribution.stableCount} />
              <MetricCard label="Improved" value={data.floatChangeDistribution.improvedCount} color={C.green} />
            </div>
          )}

          {/* 5 & 6. Float Movement / Trend for a selected activity */}
          <div style={{ marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 6 }}>Float Trend — select an activity</div>
            <input value={trendActivityId} onChange={e => setTrendActivityId(e.target.value)} placeholder="Activity ID (e.g. from a table row below)…"
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 260, marginBottom: 10 }} />
            {trendActivityId && <FloatTrendPanel ctx={ctx} activityId={trendActivityId} />}
          </div>

          {/* 7. Float Heat Map */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>Float Heat Map</div>
              <Select value={heatmapGroupBy} onChange={setHeatmapGroupBy} options={[
                { value: "wbs", label: "WBS" }, { value: "area", label: "Area" }, { value: "discipline", label: "Discipline" },
                { value: "contractor", label: "Contractor" }, { value: "system", label: "System" },
              ]} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
              {data.heatmap.cells.map((cell: any) => {
                const minTf = cell.minimumTotalFloat;
                const bg = minTf == null ? C.muted2 : minTf < 0 ? C.red : minTf <= 10 ? C.amber : C.green;
                return (
                  <div key={cell.group} style={{ background: `${bg}12`, border: `1px solid ${bg}`, borderRadius: 8, padding: "8px 12px" }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: C.text, marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cell.group}</div>
                    <div style={{ fontSize: 10, color: C.muted2, lineHeight: 1.6 }}>
                      {cell.activityCount} activities · Neg Float {cell.negativeFloatCount} · Critical {cell.criticalCount}<br />
                      Min TF {cell.minimumTotalFloat ?? "—"} · Median TF {cell.medianTotalFloat ?? "—"}
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>Total Float is never summed across a group — cells show count, minimum, and median only.</div>
          </div>

          {/* 8. Float vs Finish Variance */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Float vs Finish Variance</div>
            <ResponsiveContainer width="100%" height={220}>
              <ScatterChart margin={{ left: -10, top: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                <XAxis type="number" dataKey="finishVarianceDays" name="Finish Variance (d)" tick={{ fill: C.muted, fontSize: 10 }} />
                <YAxis type="number" dataKey="currentTotalFloat" name="Current TF" tick={{ fill: C.muted, fontSize: 10 }} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload;
                  return <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px", fontSize: 11 }}>{p.activityId} — Finish Var {p.finishVarianceDays}d, TF {p.currentTotalFloat}d</div>;
                }} />
                <ReferenceLine y={0} stroke={C.red} strokeDasharray="3 3" />
                <Scatter data={data.floatVsFinishVarianceScatter} fill={C.orange} fillOpacity={0.6} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          {/* 9. Float vs Remaining Duration */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Float vs Remaining Duration</div>
            <ResponsiveContainer width="100%" height={220}>
              <ScatterChart margin={{ left: -10, top: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                <XAxis type="number" dataKey="remainingDuration" name="Remaining Duration" tick={{ fill: C.muted, fontSize: 10 }} />
                <YAxis type="number" dataKey="currentTotalFloat" name="Current TF" tick={{ fill: C.muted, fontSize: 10 }} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload;
                  return <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px", fontSize: 11 }}>{p.activityId} — Remaining {p.remainingDuration}d, TF {p.currentTotalFloat}d</div>;
                }} />
                <ReferenceLine y={0} stroke={C.red} strokeDasharray="3 3" />
                <Scatter data={data.floatVsRemainingDurationScatter} fill={C.purple} fillOpacity={0.6} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          {/* 10/11. Deterioration / Improvement / Newly Negative / Recovered */}
          <RankTable title="Top Float Deterioration" rows={data.topDeterioration} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Largest Float Improvement" rows={data.topImprovement} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Newly Negative Float (Previous ≥ 0, Current < 0)" rows={data.newlyNegative} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Recovered From Negative Float (factual — not implying the issue is resolved)" rows={data.recoveredFromNegative} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />

          {/* 12. Milestone Float Analysis */}
          <div style={{ marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 6 }}>Milestone Float Analysis</div>
            <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 8, background: C.card }}>
              <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
                <thead><tr style={{ background: C.card2 }}>
                  {["Milestone", "Baseline Finish", "Current Finish", "Var (d)", "Baseline TF", "Previous TF", "Current TF", "Driving Preds", "Neg-Float Preds", "Risk"].map(h => (
                    <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {data.milestoneFloatAnalysis.map((m: any) => (
                    <tr key={m.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <td style={{ padding: "4px 8px" }}>{m.activityId} — {m.activityName}</td>
                      <td style={{ padding: "4px 8px" }}>{m.baselineFinish || "—"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.currentFinish || "—"}</td>
                      <td style={{ padding: "4px 8px", color: m.finishVarianceDays > 0 ? C.red : C.muted2 }}>{m.finishVarianceDays ?? "—"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.baselineTotalFloat ?? "—"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.previousTotalFloat ?? "—"}</td>
                      <td style={{ padding: "4px 8px", color: m.currentTotalFloat != null && m.currentTotalFloat < 0 ? C.red : C.text, fontWeight: 700 }}>{m.currentTotalFloat ?? "—"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.drivingPredecessorCount ?? "Unavailable"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.negativeFloatPredecessorExposure ?? "Unavailable"}</td>
                      <td style={{ padding: "4px 8px" }}>{m.riskFlagged ? "Flagged" : "—"}</td>
                    </tr>
                  ))}
                  {data.milestoneFloatAnalysis.length === 0 && <tr><td colSpan={10} style={{ padding: 14, textAlign: "center", color: C.muted }}>No milestones in this schedule.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
