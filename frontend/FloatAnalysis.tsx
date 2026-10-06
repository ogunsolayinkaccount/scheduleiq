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

const QUADRANT_COLOR: Record<string, string> = {
  HIGH_EXPOSURE: C.red, FLOAT_CRITICAL: C.orange, VARIANCE_CRITICAL: C.amber, CONTROLLED_MONITOR: C.green,
};
const QUADRANT_LABEL: Record<string, string> = {
  HIGH_EXPOSURE: "High Exposure", FLOAT_CRITICAL: "Float Critical",
  VARIANCE_CRITICAL: "Variance Critical", CONTROLLED_MONITOR: "Controlled / Monitor",
};

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

function MetricCard({ label, value, color, sub, onClick, active }: {
  label: string; value: any; color?: string; sub?: string; onClick?: () => void; active?: boolean;
}) {
  return (
    <div onClick={onClick} style={{
      background: active ? `${color || C.accent}12` : C.card, border: `1px solid ${active ? (color || C.accent) : C.border}`,
      borderRadius: 10, padding: "10px 16px", flex: "1 1 140px", cursor: onClick ? "pointer" : undefined,
    }}>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || C.text }}>{value ?? "Unavailable"}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2 }}>{sub}</div>}
    </div>
  );
}

function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginTop: 26, marginBottom: 10 }}>
      <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{title}</div>
      {sub && <div style={{ fontSize: 11, color: C.muted }}>{sub}</div>}
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

function floatColor(tf: number | null | undefined): string {
  if (tf == null) return C.muted2;
  if (tf < 0) return C.red;
  if (tf === 0) return C.amber;
  return C.green;
}

// ─── A. SCHEDULE EXPOSURE BY AREA / WBS ────────────────────────────────────
// Reuses float_intelligence.compute_float_heatmap's cells exactly as
// returned by /float-analysis/ (already sorted worst-first, already
// count/min/median only — Total Float is never summed across a group).

function ExposureByAreaChart({ cells, groupBy, onGroupByChange }: { cells: any[]; groupBy: string; onGroupByChange: (v: string) => void }) {
  const chartData = cells.map((c: any) => ({ ...c, label: c.group.length > 22 ? c.group.slice(0, 21) + "…" : c.group }));
  const height = Math.max(160, chartData.length * 34 + 40);
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>Schedule Exposure by {groupBy[0].toUpperCase() + groupBy.slice(1)}</div>
        <Select value={groupBy} onChange={onGroupByChange} options={[
          { value: "area", label: "Area" }, { value: "wbs", label: "WBS" }, { value: "discipline", label: "Discipline" },
          { value: "contractor", label: "Contractor" }, { value: "system", label: "System" },
        ]} />
      </div>
      {chartData.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No activities to group.</div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 30 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis type="number" tick={{ fill: C.muted, fontSize: 10 }} />
            <YAxis type="category" dataKey="label" width={130} tick={{ fill: C.text, fontSize: 11 }} />
            <ReferenceLine x={0} stroke={C.muted2} />
            <Tooltip content={({ active, payload }: any) => {
              if (!active || !payload?.length) return null;
              const c = payload[0].payload;
              return (
                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", fontSize: 11 }}>
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>{c.group}</div>
                  <div>Worst Total Float: <strong style={{ color: floatColor(c.minimumTotalFloat) }}>{c.minimumTotalFloat ?? "Unavailable"}</strong></div>
                  <div>Median Total Float: {c.medianTotalFloat ?? "Unavailable"}</div>
                  <div style={{ marginTop: 4, color: C.muted }}>{c.activityCount} activities</div>
                  <div>Negative Float: {c.negativeFloatCount} · Zero Float: {c.zeroFloatCount}</div>
                  <div>Near Critical: {c.nearCriticalCount} · Driving: {c.drivingCount}</div>
                </div>
              );
            }} />
            <Bar dataKey="minimumTotalFloat" name="Worst Total Float" radius={[0, 3, 3, 0]}>
              {chartData.map((c: any, i: number) => <Cell key={i} fill={floatColor(c.minimumTotalFloat)} fillOpacity={0.75} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>
        Bars show Worst Total Float within each group — Total Float is never summed across activities (not an additive quantity). Hover a bar for the supporting activity counts.
      </div>
    </div>
  );
}

// ─── B. TOP SCHEDULE EXPOSURE ACTIVITIES ───────────────────────────────────

function TopExposureChart({ rows, topN, onTopNChange, onSelect }: { rows: any[]; topN: number; onTopNChange: (n: number) => void; onSelect: (id: string) => void }) {
  const shown = rows.slice(0, topN);
  const height = Math.max(160, shown.length * 30 + 40);
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>Top Schedule Exposure Activities ({rows.length} total)</div>
        <div style={{ display: "flex", gap: 4 }}>
          {[10, 20, 50].map(n => (
            <button key={n} onClick={() => onTopNChange(n)}
              style={{ background: topN === n ? `${C.accent}18` : "transparent", border: `1px solid ${topN === n ? C.accent : C.border}`, color: topN === n ? C.accent : C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
              Top {n}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No activities with negative Total Float in the current scope.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={shown} layout="vertical" margin={{ left: 10, right: 30 }}
              onClick={(e: any) => { const id = e?.activePayload?.[0]?.payload?.activityId; if (id) onSelect(id); }}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis type="number" tick={{ fill: C.muted, fontSize: 10 }} />
              <YAxis type="category" dataKey="activityId" width={90} tick={{ fill: C.text, fontSize: 11, fontFamily: "monospace" }} />
              <Tooltip content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const r = payload[0].payload;
                return (
                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", fontSize: 11 }}>
                    <div style={{ fontWeight: 700 }}>{r.activityId} — {r.activityName}</div>
                    <div>Total Float: <strong style={{ color: C.red }}>{r.currentTotalFloat}d</strong></div>
                    <div>Finish Variance: {r.finishVarianceDays ?? "Unavailable"}{r.finishVarianceDays != null ? "d" : ""}</div>
                    <div>Driving Path: {r.driving ? "Yes" : "No"} · Status: {r.activityStatus || "—"}</div>
                    <div style={{ color: C.muted2 }}>{r.wbs}{r.area ? ` · ${r.area}` : ""}</div>
                  </div>
                );
              }} />
              <Bar dataKey="currentTotalFloat" name="Total Float" fill={C.red} radius={[0, 3, 3, 0]} fillOpacity={0.75} cursor="pointer" />
            </BarChart>
          </ResponsiveContainer>
          <div style={{ overflowX: "auto", marginTop: 10 }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
              <thead><tr style={{ background: C.card2 }}>
                {["Activity ID", "Name", "Total Float", "Finish Var", "Driving", "Status"].map(h => (
                  <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {shown.map((r: any) => (
                  <tr key={r.activityId} onClick={() => onSelect(r.activityId)} style={{ borderBottom: `1px solid ${C.border}`, cursor: "pointer" }}>
                    <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{r.activityId}</td>
                    <td style={{ padding: "4px 8px", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.activityName}</td>
                    <td style={{ padding: "4px 8px", color: C.red, fontWeight: 700 }}>{r.currentTotalFloat}d</td>
                    <td style={{ padding: "4px 8px" }}>{r.finishVarianceDays ?? "Unavailable"}</td>
                    <td style={{ padding: "4px 8px" }}>{r.driving ? "Yes" : "—"}</td>
                    <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.activityStatus || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>
        Ranked deterministically: most negative Total Float first, driving-path activities break ties, larger adverse finish variance breaks remaining ties. Never a weighted or AI-generated score.
      </div>
    </div>
  );
}

// ─── C. FLOAT DISTRIBUTION / FLOAT HEALTH ──────────────────────────────────

function FloatHealthSummary({ s, floatBandFilter, onFilterChange, nearCriticalThresholdDays }: {
  s: any; floatBandFilter: string; onFilterChange: (v: any) => void; nearCriticalThresholdDays: number;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
      <MetricCard label="Activities Analyzed" value={s?.activitiesAnalyzed} />
      <MetricCard label="Negative Float" value={s?.negativeFloatCount} color={C.red}
        active={floatBandFilter === "negative"} onClick={() => onFilterChange(floatBandFilter === "negative" ? "" : "negative")} />
      <MetricCard label="Zero Float" value={s?.zeroFloatCount} color={C.amber}
        active={floatBandFilter === "zero"} onClick={() => onFilterChange(floatBandFilter === "zero" ? "" : "zero")} />
      <MetricCard label="Near Critical" value={s?.nearCriticalCount} color={C.gold} sub={`≤${nearCriticalThresholdDays}d`}
        active={floatBandFilter === "nearCritical"} onClick={() => onFilterChange(floatBandFilter === "nearCritical" ? "" : "nearCritical")} />
      <MetricCard label="Positive / Healthy" value={s ? (s.activitiesAnalyzed - s.negativeFloatCount - s.zeroFloatCount - s.nearCriticalCount) : null} color={C.green} />
      <MetricCard label="Critical" value={s?.criticalCount} color={C.red} />
      <MetricCard label="Driving" value={s?.drivingCount} color={C.purple} />
      <MetricCard label="Newly Negative" value={s?.newlyNegativeCount} color={s?.newlyNegativeCount > 0 ? C.red : undefined} />
      <MetricCard label="Recovered From Negative" value={s?.recoveredFromNegativeCount} color={C.green} />
      <MetricCard label="Completed (Excluded)" value={s?.completedActivitiesExcluded} />
    </div>
  );
}

// ─── D. ADVANCED CORRELATION ANALYSIS (existing scatter plots, preserved) ──

function CorrelationScatter({ title, sub, data, xKey, xLabel, yKey = "currentTotalFloat", color, onSelect }: {
  title: string; sub?: string; data: any[]; xKey: string; xLabel: string; yKey?: string; color: string; onSelect: (id: string) => void;
}) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: sub ? 2 : 8 }}>{title}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted, marginBottom: 8 }}>{sub}</div>}
      {data.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No activities have both figures available in the current scope.</div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <ScatterChart margin={{ left: -10, top: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis type="number" dataKey={xKey} name={xLabel} tick={{ fill: C.muted, fontSize: 10 }} />
            <YAxis type="number" dataKey={yKey} name="Current TF" tick={{ fill: C.muted, fontSize: 10 }} />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload;
              return (
                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", fontSize: 11 }}>
                  <div style={{ fontWeight: 700 }}>{p.activityId} — {p.activityName}</div>
                  <div style={{ color: C.muted2 }}>{p.wbs}{p.area ? ` · ${p.area}` : ""}</div>
                  <div>Total Float: {p.currentTotalFloat}d</div>
                  <div>{xLabel}: {p[xKey]}{xKey !== "remainingDuration" ? "d" : ""}</div>
                  <div>Status: {p.activityStatus || "—"} · Driving: {p.driving ? "Yes" : "No"}</div>
                  <div style={{ color: C.accent, marginTop: 2 }}>Click to view activity detail</div>
                </div>
              );
            }} />
            <ReferenceLine y={0} stroke={C.red} strokeDasharray="3 3" />
            <Scatter data={data} fill={color} fillOpacity={0.6} cursor="pointer"
              onClick={(p: any) => p?.activityId && onSelect(p.activityId)}>
              {data.map((p: any, i: number) => <Cell key={i} fill={p.critical ? C.red : color} fillOpacity={0.6} />)}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ExposureMatrixChart({ matrix, onSelect }: { matrix: any; onSelect: (id: string) => void }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 2 }}>Schedule Exposure Matrix</div>
      <div style={{ fontSize: 10, color: C.muted, marginBottom: 8 }}>
        X-axis: <strong>Approved Baseline Finish Variance</strong> — Current Finish vs the designated ScheduleIQ Approved/Revised Baseline version (never the embedded P6 baseline used by the correlation scatters below).
      </div>
      {!matrix?.available ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>{matrix?.reason || "Unavailable."}</div>
      ) : matrix.points.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No activities have both Total Float and an Approved Baseline Finish Variance in the current scope.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <ScatterChart margin={{ left: -10, top: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis type="number" dataKey="approvedBaselineVarianceDays" name="Approved Baseline Finish Variance (d)" tick={{ fill: C.muted, fontSize: 10 }} />
              <YAxis type="number" dataKey="currentTotalFloat" name="Current Total Float" tick={{ fill: C.muted, fontSize: 10 }} />
              <ZAxis range={[36, 36]} />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                return (
                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", fontSize: 11 }}>
                    <div style={{ fontWeight: 700 }}>{p.activityId} — {p.activityName}</div>
                    <div style={{ color: QUADRANT_COLOR[p.quadrant], fontWeight: 700 }}>{QUADRANT_LABEL[p.quadrant]}</div>
                    <div>Total Float: {p.currentTotalFloat}d · Approved Baseline Finish Variance: {p.approvedBaselineVarianceDays}d</div>
                    <div style={{ color: C.accent, marginTop: 2 }}>Click to view activity detail</div>
                  </div>
                );
              }} />
              <ReferenceLine y={0} stroke={C.muted2} strokeDasharray="3 3" />
              <ReferenceLine x={matrix.thresholds.varianceDays} stroke={C.muted2} strokeDasharray="3 3" />
              <Scatter data={matrix.points} cursor="pointer" onClick={(p: any) => p?.activityId && onSelect(p.activityId)}>
                {matrix.points.map((p: any, i: number) => <Cell key={i} fill={QUADRANT_COLOR[p.quadrant]} fillOpacity={0.7} />)}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 6 }}>
            {Object.entries(QUADRANT_LABEL).map(([k, label]) => (
              <div key={k} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: C.muted }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: QUADRANT_COLOR[k], display: "inline-block" }} /> {label}
              </div>
            ))}
          </div>
          <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>
            Quadrant boundaries: negative Total Float (Y) and ±{matrix.thresholds.varianceDays}d finish variance vs the Approved Baseline (X) — the same thresholds Risk Register already uses, never a value invented for this chart.
          </div>
        </>
      )}
    </div>
  );
}

// ─── ACTIVITY DRILL-DOWN ────────────────────────────────────────────────────

function DrillDownField({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 12, color: C.text, fontWeight: 600 }}>{value ?? "Not Available"}</div>
    </div>
  );
}

function ActivityDrillDown({ ctx, data, activityId, onClose, onJumpToTrend }: {
  ctx: ReturnType<typeof useProjectsAndVersions>; data: any; activityId: string; onClose: () => void; onJumpToTrend: (id: string) => void;
}) {
  const [row, setRow] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ctx.projectId || !activityId) return;
    setLoading(true); setError(null); setRow(null);
    const params = new URLSearchParams({ currentVersion: ctx.versionId });
    if (data?.previousVersionId) params.set("previousVersion", data.previousVersionId);
    if (data?.baselineVersionId) params.set("baselineVersion", data.baselineVersionId);
    sfetch(`${API}/api/projects/${ctx.projectId}/activity-analysis/?${params.toString()}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(d => {
        const found = (d.rows || []).find((r: any) => r.activityId === activityId);
        setRow(found || null);
        if (!found) setError(`Linked activity not found in selected schedule version.`);
      })
      .catch((e: any) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [ctx.projectId, ctx.versionId, activityId, data?.previousVersionId, data?.baselineVersionId]);

  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 480, background: C.bg, borderLeft: `1px solid ${C.border}`, padding: 18, overflowY: "auto", zIndex: 200, boxShadow: "-6px 0 18px rgba(0,0,0,0.12)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>Activity Detail — {activityId}</div>
        <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 16 }}>✕</button>
      </div>
      {loading && <div style={{ color: C.accent, fontSize: 12 }}>Loading…</div>}
      {error && !row && <div style={{ color: C.muted2, fontSize: 12 }}>{error}</div>}
      {row && (
        <>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 2 }}>{row.activityName}</div>
          <div style={{ fontSize: 11, color: C.muted2, marginBottom: 14 }}>
            Data Date {data?.currentDataDate || "Unavailable"} · {data?.currentVersionLabel || "—"}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 16 }}>
            <DrillDownField label="WBS" value={row.wbs} />
            <DrillDownField label="Area" value={row.area} />
            <DrillDownField label="Status" value={row.activityStatus} />
            <DrillDownField label="% Complete" value={row.pctComplete != null ? `${row.pctComplete}%` : null} />
            <DrillDownField label="Start" value={row.currentStart} />
            <DrillDownField label="Finish" value={row.currentFinish} />
            <DrillDownField label="Baseline Start" value={row.baselineStart} />
            <DrillDownField label="Baseline Finish" value={row.baselineFinish} />
            <DrillDownField label="Start Variance (d)" value={row.startVarianceDays} />
            <DrillDownField label="Finish Variance (d)" value={row.finishVarianceDays} />
            <DrillDownField label="Original Duration" value={row.originalDuration} />
            <DrillDownField label="Remaining Duration" value={row.remainingDuration} />
            <DrillDownField label="Total Float" value={row.currentTotalFloat} />
            <DrillDownField label="Float Condition" value={row.negativeFloat ? "Negative" : row.currentTotalFloat === 0 ? "Zero" : row.nearCritical ? "Near Critical" : row.currentTotalFloat != null ? "Normal" : null} />
            <DrillDownField label="Driving / Critical Path" value={row.driving ? "Yes" : "No"} />
            <DrillDownField label="Predecessors" value={row.predecessorCount} />
            <DrillDownField label="Successors" value={row.successorCount} />
            <DrillDownField label="Data Date" value={data?.currentDataDate} />
            <DrillDownField label="Schedule Version" value={data?.currentVersionLabel} />
          </div>
          <button onClick={() => onJumpToTrend(activityId)}
            style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
            View Float Trend for this Activity
          </button>
        </>
      )}
    </div>
  );
}

// ─── DETERIORATION / IMPROVEMENT TABLES ────────────────────────────────────

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
  const [exposureTopN, setExposureTopN] = useState(10);
  const [trendActivityId, setTrendActivityId] = useState<string>(initialActivityId || "");
  const [drillDownId, setDrillDownId] = useState<string>("");
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
  const openDrillDown = (id: string) => setDrillDownId(id);
  const jumpToTrend = (id: string) => { setTrendActivityId(id); setDrillDownId(""); };

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Float Analysis</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        Where is the schedule exposure, what activities are causing it, and what should the scheduler investigate first? Imported P6 Total Float is source truth throughout — nothing here recalculates it.
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
            Project: {ctx.projects.find((p: any) => p.id === ctx.projectId)?.name || "—"} · Version: {data.currentVersionLabel || "—"} (CURRENT)
            {data.previousVersionLabel && <> · Previous: {data.previousVersionLabel}</>}
            {data.baselineVersionLabel && <> · Baseline: {data.baselineVersionLabel}</>}
            <br />Data Date {data.currentDataDate || "Unavailable"}
            {!data.calendarConfidence?.available && <span style={{ color: C.amber }}> · ⚠ Working-day figures unavailable — no confidently decoded calendar for {data.calendarConfidence?.totalActivities ?? "?"} activities.</span>}
          </div>

          <FloatHealthSummary s={s} floatBandFilter={floatBandFilter} onFilterChange={setFloatBandFilter} nearCriticalThresholdDays={data.thresholds?.nearCriticalFloatDays ?? data.nearCriticalThresholdDays} />
          {!includeCompleted && (s?.completedActivitiesExcluded ?? 0) > 0 && (
            <div style={{ fontSize: 10, color: C.muted2, marginTop: -12, marginBottom: 16 }}>
              {s.completedActivitiesExcluded} completed {s.completedActivitiesExcluded === 1 ? "activity displays" : "activities display"} Current Total Float as "—" and {s.completedActivitiesExcluded === 1 ? "is" : "are"} excluded from the counts, histogram, heat map and rankings above — their imported P6 float is unchanged and available via Source Traceability. Check "Include Completed Activities" to audit them with their stored value restored.
            </div>
          )}

          {/* A. Schedule Exposure by Area / WBS */}
          <SectionHeader title="A. Schedule Exposure by Area / WBS" sub="Where negative-float exposure is concentrated." />
          <ExposureByAreaChart cells={data.heatmap.cells} groupBy={heatmapGroupBy} onGroupByChange={setHeatmapGroupBy} />

          {/* B. Top Schedule Exposure Activities */}
          <SectionHeader title="B. Top Schedule Exposure Activities" sub="The activities requiring the most immediate schedule attention." />
          <TopExposureChart rows={data.topScheduleExposure} topN={exposureTopN} onTopNChange={setExposureTopN} onSelect={openDrillDown} />

          {/* C. Float Distribution / Float Health */}
          <SectionHeader title="C. Float Distribution / Float Health" />
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
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Baseline vs Current Float</div>
            <ResponsiveContainer width="100%" height={240}>
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
                <Scatter data={data.baselineVsCurrentScatter} fill={C.accent} cursor="pointer" onClick={(p: any) => p?.activityId && openDrillDown(p.activityId)}>
                  {data.baselineVsCurrentScatter.map((p: any, i: number) => (
                    <Cell key={i} fill={p.critical ? C.red : C.accent} fillOpacity={0.6} />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
            <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>Below the diagonal (Current &lt; Baseline) = deterioration. Above = improvement. Red = currently critical.</div>
          </div>
          {data.floatChangeDistribution && (
            <div style={{ display: "flex", gap: 10, marginBottom: 18 }}>
              <MetricCard label="Deteriorated" value={data.floatChangeDistribution.deterioratedCount} color={C.red} />
              <MetricCard label="Stable" value={data.floatChangeDistribution.stableCount} />
              <MetricCard label="Improved" value={data.floatChangeDistribution.improvedCount} color={C.green} />
            </div>
          )}

          {/* Schedule Exposure Matrix — optional visual classification aid */}
          <ExposureMatrixChart matrix={data.exposureMatrix} onSelect={openDrillDown} />

          {/* D. Advanced Correlation Analysis */}
          <SectionHeader title="D. Advanced Correlation Analysis" sub="Deeper investigation — the original Float Analysis scatter plots, preserved." />
          <CorrelationScatter title="Float vs Finish Variance"
            sub="Embedded P6 Baseline Finish Variance — compares against this activity's own P6-assigned baseline snapshot (target_end_date), not the Schedule Exposure Matrix's Approved Baseline version above."
            data={data.floatVsFinishVarianceScatter}
            xKey="finishVarianceDays" xLabel="Finish Variance (d)" color={C.orange} onSelect={openDrillDown} />
          <CorrelationScatter title="Float vs Remaining Duration" data={data.floatVsRemainingDurationScatter}
            xKey="remainingDuration" xLabel="Remaining Duration" color={C.purple} onSelect={openDrillDown} />

          {/* Float Trend for a selected activity */}
          <SectionHeader title="Float Trend" sub="Select an activity (click any chart/table row above, or enter an ID) to see its float history across versions." />
          <input value={trendActivityId} onChange={e => setTrendActivityId(e.target.value)} placeholder="Activity ID…"
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 260, marginBottom: 10 }} />
          {trendActivityId && <FloatTrendPanel ctx={ctx} activityId={trendActivityId} />}

          {/* Additional detail — existing rank tables and milestone table, preserved */}
          <SectionHeader title="Additional Detail" />
          <RankTable title="Top Float Deterioration" rows={data.topDeterioration} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Largest Float Improvement" rows={data.topImprovement} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Newly Negative Float (Previous ≥ 0, Current < 0)" rows={data.newlyNegative} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />
          <RankTable title="Recovered From Negative Float (factual — not implying the issue is resolved)" rows={data.recoveredFromNegative} colorField="floatChangeVsBaseline" onSelect={setTrendActivityId} />

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

      {drillDownId && (
        <ActivityDrillDown ctx={ctx} data={data} activityId={drillDownId} onClose={() => setDrillDownId("")} onJumpToTrend={jumpToTrend} />
      )}
    </div>
  );
}
