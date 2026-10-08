import { useEffect, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";
import { formatDataDate, versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

const ASSESSMENT_COLOR: Record<string, string> = {
  HIGH_EXPOSURE: C.red, WARNING: C.orange, MONITOR: C.amber, FAVORABLE: C.green, UNAVAILABLE: C.muted2,
};
const ASSESSMENT_LABEL: Record<string, string> = {
  HIGH_EXPOSURE: "High Exposure", WARNING: "Warning", MONITOR: "Monitor", FAVORABLE: "Favorable", UNAVAILABLE: "Unavailable",
};
const DIRECTION_COLOR: Record<string, string> = {
  FAVORABLE: C.green, UNFAVORABLE: C.red, NO_MOVEMENT: C.muted2, UNAVAILABLE: C.muted2,
};

function useProjectsAndVersions(initialProjectId?: string, initialVersionId?: string) {
  const [projects, setProjects] = useState<any[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);
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

  const selectProject = (id: string) => { setVersions([]); setVersionId(""); setProjectId(id); };
  const safeVersionId = versions.some((v: any) => v.id === versionId) ? versionId : "";
  return { projects, projectId, setProjectId: selectProject, versions, versionId: safeVersionId, setVersionId };
}

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 130 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function Panel({ title, sub, action, children }: { title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: sub ? 2 : 10, gap: 8, flexWrap: "wrap" }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: C.text }}>{title}</div>
        {action && <div style={{ marginLeft: "auto" }}>{action}</div>}
      </div>
      {sub && <div style={{ fontSize: 11, color: C.muted, marginBottom: 10 }}>{sub}</div>}
      {children}
    </div>
  );
}

function MetricCard({ label, value, color, sub }: { label: string; value: any; color?: string; sub?: string }) {
  return (
    <div style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 9, padding: "10px 12px", minWidth: 0 }}>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 800, color: color || C.text }}>{value ?? "Unavailable"}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function AssessmentBadge({ assessment }: { assessment: string }) {
  const color = ASSESSMENT_COLOR[assessment] || C.muted2;
  return <span style={{ background: `${color}18`, color, borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700, whiteSpace: "nowrap" }}>{ASSESSMENT_LABEL[assessment] || assessment}</span>;
}

function fmtVar(days: number | null | undefined): string {
  if (days == null) return "Unavailable";
  const sign = days > 0 ? "+" : "";
  return `${sign}${days}d`;
}

// ─── Variance Exposure by Area/WBS ──────────────────────────────────────────

function VarianceByGroupChart({ cells, groupBy, onGroupByChange }: { cells: any[]; groupBy: string; onGroupByChange: (v: string) => void }) {
  const chartData = cells.map((c: any) => ({ ...c, label: c.group.length > 22 ? c.group.slice(0, 21) + "…" : c.group }));
  const height = Math.max(160, chartData.length * 32 + 40);
  return (
    <Panel title={`Variance Exposure by ${groupBy[0].toUpperCase() + groupBy.slice(1)}`}
      action={<Select value={groupBy} onChange={onGroupByChange} options={[
        { value: "area", label: "Area" }, { value: "wbs", label: "WBS" }, { value: "discipline", label: "Discipline" },
        { value: "contractor", label: "Contractor" }, { value: "system", label: "System" },
      ]} />}>
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
                  <div>Worst Finish Variance: <strong>{fmtVar(c.worstFinishVarianceDays)}</strong></div>
                  <div>Median Finish Variance: {fmtVar(c.medianFinishVarianceDays)}</div>
                  <div>Worst Total Float: {c.worstTotalFloat ?? "Unavailable"}</div>
                  <div style={{ marginTop: 4, color: C.muted }}>{c.activityCount} activities</div>
                  <div>Unfavorable: {c.unfavorableCount} · Favorable: {c.favorableCount}</div>
                  <div>Negative Float: {c.negativeFloatCount} · Near Critical: {c.nearCriticalCount} · Driving: {c.drivingCount}</div>
                  <div>Exposed Milestones: {c.exposedMilestoneCount}</div>
                </div>
              );
            }} />
            <Bar dataKey="worstFinishVarianceDays" name="Worst Finish Variance" radius={[0, 3, 3, 0]}>
              {chartData.map((c: any, i: number) => (
                <Cell key={i} fill={c.worstFinishVarianceDays == null ? C.muted2 : c.worstFinishVarianceDays > 0 ? C.red : c.worstFinishVarianceDays < 0 ? C.green : C.muted2} fillOpacity={0.75} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>
        Bars show the WORST (most adverse) Finish Variance within each group — Total Float is never summed across activities. Hover a bar for supporting counts.
      </div>
    </Panel>
  );
}

// ─── Top Schedule Variance Exposure ─────────────────────────────────────────

function TopVarianceExposure({ rows, topN, onTopNChange, onSelect }: { rows: any[]; topN: number; onTopNChange: (n: number) => void; onSelect: (id: string) => void }) {
  const shown = rows.slice(0, topN);
  return (
    <Panel title={`Top Schedule Variance Exposure (${rows.length} total)`}
      action={
        <div style={{ display: "flex", gap: 4 }}>
          {[10, 20, 50].map(n => (
            <button key={n} onClick={() => onTopNChange(n)}
              style={{ background: topN === n ? `${C.accent}18` : "transparent", border: `1px solid ${topN === n ? C.accent : C.border}`, color: topN === n ? C.accent : C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
              Top {n}
            </button>
          ))}
          <button onClick={() => onTopNChange(99999)}
            style={{ background: topN >= 99999 ? `${C.accent}18` : "transparent", border: `1px solid ${topN >= 99999 ? C.accent : C.border}`, color: topN >= 99999 ? C.accent : C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
            All
          </button>
        </div>
      }>
      {shown.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No activities show unfavorable variance with reduced schedule flexibility in the current scope.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
            <thead><tr style={{ background: C.card2 }}>
              {["Rank", "Activity ID", "Name", "WBS", "Area", "Status", "Comp. Finish", "Current Finish", "Finish Var", "TF", "Crit", "Near", "Drv", "Milestone Exp.", "Assessment"].map(h => (
                <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {shown.map((r: any) => (
                <tr key={r.activityId} onClick={() => onSelect(r.activityId)} style={{ borderBottom: `1px solid ${C.border}`, cursor: "pointer" }}>
                  <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.rank}</td>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{r.activityId}</td>
                  <td style={{ padding: "4px 8px", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.activityName}</td>
                  <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.wbs || "—"}</td>
                  <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.area || "—"}</td>
                  <td style={{ padding: "4px 8px", color: C.muted2 }}>{r.activityStatus || "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.comparisonFinish ? formatDataDate(r.comparisonFinish) : "Unavailable"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.currentFinish ? formatDataDate(r.currentFinish) : "Unavailable"}</td>
                  <td style={{ padding: "4px 8px", color: DIRECTION_COLOR[r.direction], fontWeight: 700 }}>{fmtVar(r.finishVarianceDays)}</td>
                  <td style={{ padding: "4px 8px", color: r.currentTotalFloat != null && r.currentTotalFloat < 0 ? C.red : C.text }}>{r.currentTotalFloat ?? "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.criticalActionable ? "Yes" : "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.nearCritical ? "Yes" : "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.driving ? "Yes" : "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{r.reachesContractualMilestone ? "Yes" : "—"}</td>
                  <td style={{ padding: "4px 8px" }}><AssessmentBadge assessment={r.assessment} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 10, color: C.muted, marginTop: 8 }}>
        Ranked deterministically: exposure tier first (High Exposure → Warning → Monitor), then most negative Total Float, then larger adverse finish variance as a tie-break — never simply the largest raw variance. Never an opaque weighted score.
      </div>
    </Panel>
  );
}

// ─── Milestone Variance ──────────────────────────────────────────────────

function MilestoneVariancePanel({ milestones }: { milestones: any[] }) {
  return (
    <Panel title="Milestone Variance" sub="Schedule comparison variance (vs the selected basis) and Contract Variance (vs a registered contractual date) are always shown as separate figures — never conflated.">
      {milestones.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12 }}>No milestone activities in this schedule version.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
            <thead><tr style={{ background: C.card2 }}>
              {["Milestone", "Current Forecast", "Comparison Date", "Variance", "TF", "Driving", "Contract Date", "Contract Variance", "Assessment"].map(h => (
                <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {milestones.map((m: any) => (
                <tr key={m.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: "4px 8px" }}>{m.activityId} — {m.activityName}</td>
                  <td style={{ padding: "4px 8px" }}>{m.currentFinish ? formatDataDate(m.currentFinish) : "Unavailable"}</td>
                  <td style={{ padding: "4px 8px" }}>{m.comparisonFinish ? formatDataDate(m.comparisonFinish) : "Unavailable"}</td>
                  <td style={{ padding: "4px 8px", color: DIRECTION_COLOR[m.direction], fontWeight: 700 }}>{fmtVar(m.finishVarianceDays)}</td>
                  <td style={{ padding: "4px 8px" }}>{m.currentTotalFloat ?? "—"}</td>
                  <td style={{ padding: "4px 8px" }}>{m.driving ? "Yes" : "—"}</td>
                  <td style={{ padding: "4px 8px", color: C.muted2 }}>{m.contractRequiredDate ? formatDataDate(m.contractRequiredDate) : "Not Registered"}</td>
                  <td style={{ padding: "4px 8px" }}>{m.contractVarianceDays != null ? fmtVar(m.contractVarianceDays) : "—"}</td>
                  <td style={{ padding: "4px 8px" }}><AssessmentBadge assessment={m.assessment} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

// ─── Update Movement ─────────────────────────────────────────────────────

function UpdateMovementPanel({ movement }: { movement: any }) {
  if (!movement) return (
    <Panel title="Current vs Previous Update">
      <div style={{ color: C.muted2, fontSize: 12 }}>No previous schedule version resolves for this update — update-to-update movement is unavailable.</div>
    </Panel>
  );
  const rows = [
    { label: "Finish Slipped", ids: movement.finishSlipped, color: C.red },
    { label: "Finish Improved", ids: movement.finishImproved, color: C.green },
    { label: "Start Slipped", ids: movement.startSlipped, color: C.red },
    { label: "Start Improved", ids: movement.startImproved, color: C.green },
    { label: "Newly Negative Float", ids: movement.newlyNegativeFloat, color: C.red },
    { label: "Recovered From Negative Float", ids: movement.recoveredFromNegativeFloat, color: C.green },
    { label: "Newly Critical", ids: movement.newlyCritical, color: C.red },
    { label: "No Longer Critical", ids: movement.noLongerCritical, color: C.green },
    { label: "Logic Changed", ids: movement.logicChanged, color: C.amber },
  ];
  return (
    <Panel title="Current vs Previous Update" sub="Update-to-update movement — a separate concept from Baseline Variance, never labeled as such.">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 8 }}>
        {rows.map(r => (
          <MetricCard key={r.label} label={r.label} value={r.ids.length} color={r.ids.length > 0 ? r.color : undefined} />
        ))}
      </div>
    </Panel>
  );
}

// ─── Activity Drill-Down (reuses /activity-analysis/, never a duplicate engine) ─

function DrillDownField({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 12, color: C.text, fontWeight: 600 }}>{value ?? "Not Available"}</div>
    </div>
  );
}

function VarianceDrillDown({ ctx, data, activityId, onClose }: { ctx: ReturnType<typeof useProjectsAndVersions>; data: any; activityId: string; onClose: () => void }) {
  const [row, setRow] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const varianceRow = (data?.topVarianceExposure || []).find((r: any) => r.activityId === activityId)
    || (data?.milestoneVariance || []).find((r: any) => r.activityId === activityId);

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
        if (!found) setError("Linked activity not found in selected schedule version.");
      })
      .catch((e: any) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [ctx.projectId, ctx.versionId, activityId, data?.previousVersionId, data?.baselineVersionId]);

  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 480, background: C.bg, borderLeft: `1px solid ${C.border}`, padding: 18, overflowY: "auto", zIndex: 200, boxShadow: "-6px 0 18px rgba(0,0,0,0.12)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>Why is this activity exposed? — {activityId}</div>
        <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 16 }}>✕</button>
      </div>
      {varianceRow && (
        <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, marginBottom: 14, fontSize: 12 }}>
          <div>Unfavorable Finish Movement: <strong>{fmtVar(varianceRow.finishVarianceDays)}</strong></div>
          <div>Current TF: <strong>{varianceRow.currentTotalFloat ?? "Unavailable"}</strong></div>
          <div>Driving: <strong>{varianceRow.driving ? "Yes" : "No"}</strong></div>
          <div>Contractual Milestone Exposure: <strong>{varianceRow.reachesContractualMilestone ? "Yes" : "No"}</strong></div>
          <div style={{ marginTop: 6 }}>Assessment: <AssessmentBadge assessment={varianceRow.assessment} /></div>
        </div>
      )}
      {loading && <div style={{ color: C.accent, fontSize: 12 }}>Loading full activity detail…</div>}
      {error && !row && <div style={{ color: C.muted2, fontSize: 12 }}>{error}</div>}
      {row && (
        <>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 10 }}>{row.activityName}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <DrillDownField label="WBS" value={row.wbs} />
            <DrillDownField label="Area" value={row.area} />
            <DrillDownField label="Status" value={row.activityStatus} />
            <DrillDownField label="% Complete" value={row.pctComplete != null ? `${row.pctComplete}%` : null} />
            <DrillDownField label="Start" value={row.currentStart} />
            <DrillDownField label="Finish" value={row.currentFinish} />
            <DrillDownField label="Baseline Start" value={row.baselineStart} />
            <DrillDownField label="Baseline Finish" value={row.baselineFinish} />
            <DrillDownField label="Total Float" value={row.currentTotalFloat} />
            <DrillDownField label="Driving / Critical Path" value={row.driving ? "Yes" : "No"} />
            <DrillDownField label="Predecessors" value={row.predecessorCount} />
            <DrillDownField label="Successors" value={row.successorCount} />
          </div>
        </>
      )}
    </div>
  );
}

// ─── ROOT ───────────────────────────────────────────────────────────────────

export default function VarianceIntelligence({ initialProjectId, initialVersionId, initialAssessmentFilter }: {
  initialProjectId?: string; initialVersionId?: string; initialAssessmentFilter?: string;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [basis, setBasis] = useState("");
  const [groupBy, setGroupBy] = useState("area");
  const [topN, setTopN] = useState(10);
  const [selected, setSelected] = useState<string>("");
  const [area, setArea] = useState(""); const [wbs, setWbs] = useState(""); const [status, setStatus] = useState("");
  const [directionFilter, setDirectionFilter] = useState(""); const [assessmentFilter, setAssessmentFilter] = useState(initialAssessmentFilter || "");
  const [drivingOnly, setDrivingOnly] = useState(false);

  useEffect(() => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    const params = new URLSearchParams({ currentVersion: ctx.versionId, groupBy });
    if (basis) params.set("basis", basis);
    if (area) params.set("area", area);
    if (wbs) params.set("wbs", wbs);
    if (status) params.set("status", status);
    if (directionFilter) params.set("direction", directionFilter);
    if (assessmentFilter) params.set("assessment", assessmentFilter);
    if (drivingOnly) params.set("drivingOnly", "true");
    sfetch(`${API}/api/projects/${ctx.projectId}/variance-intelligence/?${params.toString()}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(d => { setData(d); if (!basis) setBasis(d.basis); })
      .catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.projectId, ctx.versionId, basis, groupBy, area, wbs, status, directionFilter, assessmentFilter, drivingOnly]);

  const s = data?.summary;

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>VARIANCE INTELLIGENCE</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        Scheduler-level interpretation of variance already computed by ScheduleIQ's authoritative engines — nothing here recalculates a date, Total Float, or path status.
      </div>

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
        {data && (
          <>
            <div><div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Data Date</div><div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{formatDataDate(data.currentDataDate)}</div></div>
            <div><div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>CURRENT</div><div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{data.currentVersionLabel || "—"}</div></div>
            <div><div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>PREVIOUS</div><div style={{ fontSize: 12, fontWeight: 700, color: data.previousVersionLabel ? C.text : C.muted2 }}>{data.previousVersionLabel || "None"}</div></div>
            <div><div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Approved Baseline</div><div style={{ fontSize: 12, fontWeight: 700, color: data.baselineVersionLabel ? C.text : C.muted2 }}>{data.baselineVersionLabel || "Not Designated"}</div></div>
          </>
        )}
      </div>

      {data?.bases && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: C.muted }}>Comparison Basis:</span>
          {data.bases.map((b: any) => (
            <button key={b.key} disabled={!b.available} onClick={() => setBasis(b.key)} title={b.available ? undefined : b.reason}
              style={{ background: basis === b.key ? `${C.accent}18` : "transparent", border: `1px solid ${basis === b.key ? C.accent : C.border}`, color: !b.available ? C.muted2 : basis === b.key ? C.accent : C.text, borderRadius: 16, padding: "5px 12px", cursor: b.available ? "pointer" : "not-allowed", fontSize: 11, fontWeight: basis === b.key ? 700 : 600, opacity: b.available ? 1 : 0.55 }}>
              {b.label}{!b.available ? " (Unavailable)" : ""}
            </button>
          ))}
        </div>
      )}

      {ctx.projectId && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: C.muted }}>Filter:</span>
          <input value={area} onChange={e => setArea(e.target.value)} placeholder="Area" style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "5px 9px", fontSize: 11, fontFamily: "inherit", width: 100 }} />
          <input value={wbs} onChange={e => setWbs(e.target.value)} placeholder="WBS" style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "5px 9px", fontSize: 11, fontFamily: "inherit", width: 100 }} />
          <Select value={directionFilter} onChange={setDirectionFilter} placeholder="All Directions"
            options={[{ value: "UNFAVORABLE", label: "Unfavorable" }, { value: "FAVORABLE", label: "Favorable" }, { value: "NO_MOVEMENT", label: "No Movement" }]} />
          <Select value={assessmentFilter} onChange={setAssessmentFilter} placeholder="All Assessments"
            options={[{ value: "HIGH_EXPOSURE", label: "High Exposure" }, { value: "WARNING", label: "Warning" }, { value: "MONITOR", label: "Monitor" }, { value: "FAVORABLE", label: "Favorable" }]} />
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: C.muted }}>
            <input type="checkbox" checked={drivingOnly} onChange={e => setDrivingOnly(e.target.checked)} /> Driving Only
          </label>
        </div>
      )}

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && !data && <div style={{ color: C.accent, padding: 20 }}>Computing variance intelligence…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>}

      {data && !data.available && (
        <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 20, color: C.muted2, fontSize: 13 }}>
          {data.reason}
        </div>
      )}

      {data && data.available && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 8, marginBottom: 16 }}>
            <MetricCard label="Current Project Finish" value={s?.currentProjectFinish ? formatDataDate(s.currentProjectFinish) : "Unavailable"}
              sub={data.completionMilestone?.selectionBasis === "REGISTERED" ? "Registered completion milestone" : "Latest milestone — not registered as completion"} />
            <MetricCard label="Comparison Finish" value={s?.comparisonProjectFinish ? formatDataDate(s.comparisonProjectFinish) : "Unavailable"} />
            <MetricCard label="Finish Variance" value={fmtVar(s?.projectFinishVarianceDays)} color={s?.projectFinishVarianceDays > 0 ? C.red : s?.projectFinishVarianceDays < 0 ? C.green : undefined} />
            <MetricCard label="Current Total Float" value={s?.projectFinishTotalFloat ?? "Unavailable"} />
            <MetricCard label="Negative Float Activities" value={s?.negativeFloatCount} color={s?.negativeFloatCount > 0 ? C.red : undefined} />
            <MetricCard label="Zero Float Activities" value={s?.zeroFloatCount} />
            <MetricCard label="Near-Critical Activities" value={s?.nearCriticalCount} color={C.amber} />
            <MetricCard label="Driving Activities" value={s?.drivingCount} color={C.purple} />
            <MetricCard label="Milestones Behind Comparison" value={s?.milestonesBehindComparison} color={s?.milestonesBehindComparison > 0 ? C.red : undefined} />
            <MetricCard label="Milestones Ahead of Comparison" value={s?.milestonesAheadOfComparison} color={C.green} />
            <MetricCard label="High Exposure" value={s?.highExposureCount} color={C.red} />
            <MetricCard label="Warning" value={s?.warningCount} color={C.orange} />
          </div>

          <Panel title={data.completionMilestone?.selectionBasis === "REGISTERED" ? "Project Completion Assessment" : "Latest Project Forecast Milestone"}
            sub={data.completionMilestone && data.completionMilestone.selectionBasis !== "REGISTERED"
              ? "No milestone is registered in ScheduleIQ as the Contractual Completion milestone for this project — this is the latest-finishing milestone activity, shown for reference only. It is NOT confirmed as the project's completion milestone."
              : undefined}>
            {!data.completionMilestone ? (
              <div style={{ color: C.muted2, fontSize: 12 }}>{data.completionInterpretation}</div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 8, marginBottom: 10 }}>
                  <DrillDownField label="Milestone" value={`${data.completionMilestone.activityId} — ${data.completionMilestone.activityName}`} />
                  <DrillDownField label="Current Finish" value={data.completionMilestone.currentFinish ? formatDataDate(data.completionMilestone.currentFinish) : null} />
                  <DrillDownField label="Comparison Finish" value={data.completionMilestone.comparisonFinish ? formatDataDate(data.completionMilestone.comparisonFinish) : null} />
                  <DrillDownField label="Finish Variance" value={fmtVar(data.completionMilestone.finishVarianceDays)} />
                  <DrillDownField label="Total Float" value={data.completionMilestone.currentTotalFloat} />
                  <DrillDownField label="Driving" value={data.completionMilestone.driving ? "Yes" : "No"} />
                  <DrillDownField label="Status" value={data.completionMilestone.activityStatus} />
                </div>
                <div style={{ fontSize: 12, color: C.text, background: C.panel, borderRadius: 8, padding: 10 }}>{data.completionInterpretation}</div>
              </>
            )}
          </Panel>

          <Panel title="ScheduleIQ Variance Assessment" sub="Deterministic findings in scheduler-level language — no AI required; this section works identically whether or not AI is configured.">
            {(data.narrative || []).map((s: string, i: number) => (
              <div key={i} style={{ fontSize: 12, color: C.text, marginBottom: 6 }}>{s}</div>
            ))}
          </Panel>

          <VarianceByGroupChart cells={data.areaSummary?.cells || []} groupBy={groupBy} onGroupByChange={setGroupBy} />

          <TopVarianceExposure rows={data.topVarianceExposure || []} topN={topN} onTopNChange={setTopN} onSelect={setSelected} />

          <MilestoneVariancePanel milestones={data.milestoneVariance || []} />

          <UpdateMovementPanel movement={data.updateMovement} />
        </>
      )}

      {selected && <VarianceDrillDown ctx={ctx} data={data} activityId={selected} onClose={() => setSelected("")} />}
    </div>
  );
}
