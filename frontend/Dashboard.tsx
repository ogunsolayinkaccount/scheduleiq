import { useEffect, useRef, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { formatDataDate, versionSelectLabel, dedupeVersionsById } from "./dateFormat";
import { fmtDays, fmtHours, fmtFloat } from "./dashboardFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

// ─── data hook — same project/version resolution convention as every other
// backend-driven workspace (ActivityAnalysis.tsx / FloatAnalysis.tsx / etc). ──
function useProjectsAndVersions(initialProjectId?: string, initialVersionId?: string) {
  const [projects, setProjects] = useState<any[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);
  const [versions, setVersions] = useState<any[]>([]);
  const [versionId, setVersionId] = useState("");
  const initialVersionConsumed = useRef(false);

  useEffect(() => {
    fetch(`${API}/api/projects/?withVersions=true`).then(r => r.json()).then(d => {
      const list = d.projects || [];
      setProjects(list);
      setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur));
    }).catch(() => {});
  }, [projectsTick]);

  useEffect(() => {
    if (!projectId) { setVersions([]); setVersionId(""); return; }
    let cancelled = false;
    sfetch(`${API}/api/projects/${projectId}/versions/`).then(r => r.json()).then(d => {
      if (cancelled) return;
      const vs = d.versions || [];
      setVersions(dedupeVersionsById(vs));
      if (!initialVersionConsumed.current && initialVersionId && vs.some((v: any) => v.id === initialVersionId)) {
        initialVersionConsumed.current = true;
        setVersionId(initialVersionId);
        return;
      }
      const cur = vs.find((v: any) => v.role === "CURRENT");
      setVersionId(cur?.id || vs[0]?.id || "");
    }).catch(() => { if (!cancelled) setVersions([]); });
    return () => { cancelled = true; };
  }, [projectId, projectsTick]);

  const selectProject = (id: string) => { setVersions([]); setVersionId(""); setProjectId(id); };
  const safeVersionId = versions.some((v: any) => v.id === versionId) ? versionId : "";
  return { projects, projectId, setProjectId: selectProject, versions, versionId: safeVersionId, setVersionId };
}

function useDashboardSummary(ctx: ReturnType<typeof useProjectsAndVersions>) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/dashboard-summary/?currentVersion=${ctx.versionId}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(load, [ctx.projectId, ctx.versionId]);

  return { data, loading, error, reload: load };
}

// ─── small primitives ──────────────────────────────────────────────────────

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 150 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function Panel({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: C.text }}>{title}</div>
        {action && <div style={{ marginLeft: "auto" }}>{action}</div>}
      </div>
      {children}
    </div>
  );
}

function Unavailable({ reason }: { reason?: string | null }) {
  return <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>{reason || "Unavailable"}</div>;
}

function LinkButton({ label, onClick }: { label: string; onClick?: () => void }) {
  if (!onClick) return null;
  return (
    <button onClick={onClick} style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", padding: 0 }}>
      {label}
    </button>
  );
}

function KpiCard({ label, value, sub, color, onClick }: { label: string; value: React.ReactNode; sub?: string; color?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick}
      style={{
        background: C.card2, border: `1px solid ${C.border}`, borderRadius: 9, padding: "10px 12px",
        cursor: onClick ? "pointer" : "default", minWidth: 0,
      }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const RISK_COLOR: Record<string, string> = { Critical: C.red, "At Risk": C.amber, Watch: C.gold, Healthy: C.green };

// ─── panels ─────────────────────────────────────────────────────────────

function BaselineVsForecastPanel({ panel, onOpen }: { panel: any; onOpen?: () => void }) {
  return (
    <Panel title="Baseline vs Current Forecast" action={<LinkButton label="Open Baseline & Progress →" onClick={onOpen} />}>
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={panel.scurve.periods} margin={{ left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="period" tick={{ fill: C.muted, fontSize: 10 }} interval={Math.max(0, Math.floor((panel.scurve.periods?.length || 1) / 12))} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} unit="%" domain={[0, 100]} />
            <Tooltip />
            {panel.scurve.periods?.some((p: any) => p.isPast) && (
              <ReferenceLine x={panel.scurve.periods.filter((p: any) => p.isPast).slice(-1)[0]?.period} stroke={C.purple} strokeDasharray="4 4" label={{ value: "Data Date", fontSize: 10, fill: C.purple }} />
            )}
            <Area type="monotone" dataKey="baselinePlanned" name="Baseline Planned" stroke={C.muted2} fill="none" strokeWidth={2} strokeDasharray="5 3" dot={false} />
            <Area type="monotone" dataKey="currentPlanned" name="Current Forecast" stroke={C.accent} fill={`${C.accent}18`} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="currentUpdateActual" name="Actual" stroke={C.green} strokeWidth={2.5} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

function FloatHealthPanel({ panel, onOpen }: { panel: any; onOpen?: (filter: "negative" | "zero" | "nearCritical") => void }) {
  return (
    <Panel title="Float / Schedule Health" action={<LinkButton label="Open Float Analysis →" onClick={() => onOpen?.("negative")} />}>
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8, marginBottom: 4 }}>
            <KpiCard label="Negative Float" value={panel.negativeFloatCount} color={panel.negativeFloatCount > 0 ? C.red : C.green} onClick={() => onOpen?.("negative")} />
            <KpiCard label="Zero Float" value={panel.zeroFloatCount} color={C.amber} onClick={() => onOpen?.("zero")} />
            <KpiCard label={`Near Critical (≤${panel.nearCriticalThresholdDays}d)`} value={panel.nearCriticalCount} color={C.gold} onClick={() => onOpen?.("nearCritical")} />
            <KpiCard label="Positive Float" value={panel.positiveFloatCount} color={C.green} />
          </div>
          <div style={{ fontSize: 10, color: C.muted2 }}>{panel.completedActivitiesExcluded} completed activities excluded from this population.</div>
        </>
      )}
    </Panel>
  );
}

function UpdateIntelligencePanel({ panel, context, onOpen }: { panel: any; context: any; onOpen?: () => void }) {
  return (
    <Panel title="Update Intelligence" action={<LinkButton label="Open Update Analysis →" onClick={onOpen} />}>
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <>
          <div style={{ fontSize: 12, color: C.muted, marginBottom: 10, fontWeight: 700 }}>
            {panel.previousVersionLabel} → {panel.currentVersionLabel}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8 }}>
            <KpiCard label="Moved Later" value={panel.movementCounts.slipped} color={panel.movementCounts.slipped > 0 ? C.amber : C.green} onClick={onOpen} />
            <KpiCard label="Moved Earlier" value={panel.movementCounts.improved} color={C.green} onClick={onOpen} />
            <KpiCard label="Newly Negative Float" value={panel.floatMovement.newlyNegativeFloatCount} color={panel.floatMovement.newlyNegativeFloatCount > 0 ? C.red : C.green} onClick={onOpen} />
            <KpiCard label="Newly Critical" value={panel.floatMovement.newlyCriticalCount} color={panel.floatMovement.newlyCriticalCount > 0 ? C.red : C.green} onClick={onOpen} />
            <KpiCard label="Started" value={panel.movementCounts.startedThisPeriod} />
            <KpiCard label="Completed" value={panel.movementCounts.completedThisPeriod} color={C.green} />
            <KpiCard label="Added Activities" value={panel.movementCounts.new} />
            <KpiCard label="Removed Activities" value={panel.movementCounts.removed} />
            <KpiCard label="Logic Changes" value={panel.logicChanges.addedCount + panel.logicChanges.removedCount + panel.logicChanges.changedCount} />
          </div>
        </>
      )}
    </Panel>
  );
}

function MilestoneForecastPanel({ panel, onOpen }: { panel: any; onOpen?: () => void }) {
  return (
    <Panel title="Milestone Forecast" action={<LinkButton label="View All Milestones →" onClick={onOpen} />}>
      {!panel?.available || !panel.topMilestones?.length ? <Unavailable reason={panel?.available ? "No milestones in this schedule." : panel?.reason} /> : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
            <th style={{ padding: "4px 6px" }}>Milestone</th>
            <th style={{ padding: "4px 6px" }}>Baseline</th>
            <th style={{ padding: "4px 6px" }}>Current Forecast</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>Baseline Variance</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>Total Float</th>
            <th style={{ padding: "4px 6px" }}>Risk</th>
          </tr></thead>
          <tbody>
            {panel.topMilestones.map((m: any) => (
              <tr key={m.activityId} style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={{ padding: "6px", color: C.text, fontWeight: 600 }}>{m.activityName}</td>
                <td style={{ padding: "6px", color: C.muted }}>{formatDataDate(m.baselineFinish)}</td>
                <td style={{ padding: "6px", color: C.text }}>{formatDataDate(m.currentForecast)}</td>
                <td style={{ padding: "6px", textAlign: "right", color: m.varianceDays > 0 ? C.amber : C.green }}>{fmtDays(m.varianceDays)}</td>
                <td style={{ padding: "6px", textAlign: "right", color: m.totalFloat != null && m.totalFloat < 0 ? C.red : C.text }}>{fmtFloat(m.totalFloat)}</td>
                <td style={{ padding: "6px" }}>
                  <span style={{ background: `${RISK_COLOR[m.riskLevel] || C.muted2}18`, color: RISK_COLOR[m.riskLevel] || C.muted2, borderRadius: 5, padding: "2px 7px", fontSize: 10, fontWeight: 700 }}>{m.riskLevel || "—"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function TopDriversPanel({ panel, onOpen }: { panel: any; onOpen?: () => void }) {
  return (
    <Panel title="Top Schedule Drivers" action={<LinkButton label="View Driving Chain →" onClick={onOpen} />}>
      {!panel?.available || !panel.drivers?.length ? <Unavailable reason={panel?.available ? "No critical, driving or negative-float activities." : panel?.reason} /> : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
            <th style={{ padding: "4px 6px" }}>Activity ID</th>
            <th style={{ padding: "4px 6px" }}>Name</th>
            <th style={{ padding: "4px 6px" }}>WBS / Area</th>
            <th style={{ padding: "4px 6px" }}>Forecast Finish</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>Total Float</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>Movement</th>
            <th style={{ padding: "4px 6px" }}>Risk</th>
          </tr></thead>
          <tbody>
            {panel.drivers.map((d: any) => (
              <tr key={d.activityId} style={{ borderTop: `1px solid ${C.border}`, cursor: onOpen ? "pointer" : "default" }} onClick={onOpen}>
                <td style={{ padding: "6px", fontFamily: "monospace", color: C.muted2 }}>{d.activityId}</td>
                <td style={{ padding: "6px", color: C.text }}>{d.activityName}</td>
                <td style={{ padding: "6px", color: C.muted }}>{d.wbs}{d.area ? ` / ${d.area}` : ""}</td>
                <td style={{ padding: "6px", color: C.text }}>{formatDataDate(d.forecastFinish)}</td>
                <td style={{ padding: "6px", textAlign: "right", color: d.currentTotalFloat != null && d.currentTotalFloat < 0 ? C.red : C.text }}>{fmtFloat(d.currentTotalFloat)}</td>
                <td style={{ padding: "6px", textAlign: "right", color: d.updateMovementDays > 0 ? C.amber : C.green }}>{fmtDays(d.updateMovementDays)}</td>
                <td style={{ padding: "6px", color: C.muted2 }}>{d.riskSeverity || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function LookAheadPanel({ panel, onOpen }: { panel: any; onOpen?: (filter: string) => void }) {
  return (
    <Panel title="4-Week Look Ahead" action={<LinkButton label="Open Look Ahead →" onClick={() => onOpen?.("")} />}>
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <>
          <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>
            {formatDataDate(panel.window.fromDate)} → {formatDataDate(panel.window.toDate)} ({panel.window.weeks} weeks from Data Date)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8 }}>
            <KpiCard label="Starting" value={panel.summaryCards.forecastStarts} onClick={() => onOpen?.("forecastStarts")} />
            <KpiCard label="In Progress" value={panel.summaryCards.inProgress} onClick={() => onOpen?.("inProgress")} />
            <KpiCard label="Should Have Started" value={panel.summaryCards.shouldHaveStarted} color={panel.summaryCards.shouldHaveStarted > 0 ? C.amber : C.green} onClick={() => onOpen?.("shouldHaveStarted")} />
            <KpiCard label="Behind Baseline" value={panel.summaryCards.behindBaseline} color={panel.summaryCards.behindBaseline > 0 ? C.amber : C.green} onClick={() => onOpen?.("behindBaseline")} />
            <KpiCard label="Critical in Window" value={panel.summaryCards.criticalActivities} color={C.red} onClick={() => onOpen?.("criticalActivities")} />
            <KpiCard label="Completing" value={panel.summaryCards.forecastFinishes} onClick={() => onOpen?.("forecastFinishes")} />
          </div>
        </>
      )}
    </Panel>
  );
}

function ManpowerPanel({ panel, onOpen }: { panel: any; onOpen?: () => void }) {
  return (
    <Panel title="Manpower & Productivity" action={<LinkButton label="Open Project Controls →" onClick={onOpen} />}>
      {!panel?.available ? (
        <div style={{ color: C.gold, fontSize: 13, fontWeight: 700, padding: "14px 4px" }}>{panel?.reason || "Unavailable — Schedule is not resource loaded."}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8 }}>
          <KpiCard label="Budgeted Hours" value={fmtHours(panel.budgetedHours)} />
          <KpiCard label="Earned Hours" value={fmtHours(panel.earnedHours)} color={C.green} />
          <KpiCard label="Actual Hours" value={fmtHours(panel.actualHours)} color={C.amber} />
          <KpiCard label="Remaining Hours" value={fmtHours(panel.remainingHours)} />
          <KpiCard label="SPI" value={panel.spi != null ? panel.spi.toFixed(2) : "Unavailable"} color={panel.spi != null ? (panel.spi >= 1 ? C.green : panel.spi >= 0.9 ? C.amber : C.red) : C.muted2} />
          <KpiCard label="CPI" value={panel.cpi != null ? panel.cpi.toFixed(2) : "Unavailable"} color={panel.cpi != null ? (panel.cpi >= 1 ? C.green : panel.cpi >= 0.9 ? C.amber : C.red) : C.muted2} />
          <KpiCard label="EAC" value={fmtHours(panel.eac)} />
        </div>
      )}
    </Panel>
  );
}

function IntelligencePanel({ panel, onExplain, onOpenIntelligence, onGenerateReport }: {
  panel: any; onExplain?: () => void; onOpenIntelligence?: () => void; onGenerateReport?: () => void;
}) {
  return (
    <Panel title="ScheduleIQ Intelligence">
      {!panel?.available || !panel.bullets?.length ? (
        <Unavailable reason={panel?.available ? "No notable changes to summarize yet." : panel?.reason} />
      ) : (
        <ul style={{ margin: "0 0 10px", paddingLeft: 18, fontSize: 13, color: C.text, lineHeight: 1.8 }}>
          {panel.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}
        </ul>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={onExplain} style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>Explain Changes</button>
        <button onClick={onOpenIntelligence} style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>Open Intelligence</button>
        <button onClick={onGenerateReport} style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>Generate Weekly Report</button>
      </div>
    </Panel>
  );
}

// ─── root ───────────────────────────────────────────────────────────────

export default function Dashboard({
  initialProjectId, initialVersionId,
  onOpenFloatAnalysis, onOpenActivityAnalysis, onOpenUpdateAnalysis,
  onOpenRiskMilestones, onOpenProjectControls, onOpenBaselineProgress, onOpenIntelligence,
}: {
  initialProjectId?: string; initialVersionId?: string;
  onOpenFloatAnalysis?: (projectId: string, versionId: string, filter?: "negative" | "zero" | "nearCritical") => void;
  onOpenActivityAnalysis?: (projectId: string, versionId: string, filters?: Record<string, any>) => void;
  onOpenUpdateAnalysis?: (projectId: string, versionId: string) => void;
  onOpenRiskMilestones?: (projectId: string, versionId: string) => void;
  onOpenProjectControls?: (projectId: string, versionId: string, subTab?: string) => void;
  onOpenBaselineProgress?: (projectId: string, versionId: string) => void;
  onOpenIntelligence?: (projectId: string, versionId: string) => void;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const { data, loading, error } = useDashboardSummary(ctx);

  const go = (fn?: (...a: any[]) => void, ...args: any[]) => {
    if (!fn || !ctx.projectId || !ctx.versionId) return;
    fn(ctx.projectId, ctx.versionId, ...args);
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", marginBottom: 6, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 900, color: C.text, letterSpacing: "-0.01em" }}>ScheduleIQ</div>
          <div style={{ fontSize: 12, color: C.muted, fontWeight: 600 }}>Project Controls Intelligence</div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "flex-end" }}>
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
      </div>

      {data?.context && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 16px", marginBottom: 16, position: "sticky", top: 0, zIndex: 5 }}>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Data Date</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{formatDataDate(data.context.currentDataDate)}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Current</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{data.context.currentVersionLabel || "—"}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Previous</span><div style={{ fontSize: 13, fontWeight: 700, color: data.context.previousVersionLabel ? C.text : C.muted2 }}>{data.context.previousVersionLabel || (data.context.previousUnresolved?.reason || "No Previous Update")}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Baseline</span><div style={{ fontSize: 13, fontWeight: 700, color: data.context.baselineDesignated ? C.text : C.muted2 }}>{data.context.baselineDesignated ? data.context.baselineVersionLabel : "Not Designated"}</div></div>
        </div>
      )}

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && <div style={{ color: C.accent, padding: 20 }}>Loading dashboard…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 20 }}>{error}</div>}

      {data && !loading && (
        <>
          <Panel title="Project Health">
            {!data.kpis?.available ? <Unavailable reason={data.kpis?.reason} /> : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 9 }}>
                <KpiCard label="Total Activities" value={data.kpis.totalActivities} />
                <KpiCard label="Finish Variance" value={fmtDays(data.kpis.finishVarianceDays)} color={data.kpis.finishVarianceDays == null ? C.muted2 : data.kpis.finishVarianceDays > 0 ? C.red : C.green}
                  onClick={() => go(onOpenBaselineProgress)} />
                <KpiCard label="Current Total Float (min)" value={fmtFloat(data.kpis.minimumCurrentTotalFloat)} color={(data.kpis.minimumCurrentTotalFloat ?? 0) < 0 ? C.red : C.text}
                  onClick={() => go(onOpenFloatAnalysis, "negative")} />
                <KpiCard label="Critical Activities" value={data.kpis.criticalActivities} color={C.red} onClick={() => go(onOpenActivityAnalysis, { criticality: "CRITICAL" })} />
                <KpiCard label="Overdue Activities" value={data.kpis.overdueActivities} color={data.kpis.overdueActivities > 0 ? C.amber : C.green} onClick={() => go(onOpenActivityAnalysis, { status: "OVERDUE" })} />
                <KpiCard label="Current Forecast Finish" value={formatDataDate(data.kpis.currentForecastFinish)} onClick={() => go(onOpenBaselineProgress)} />
                <KpiCard label="Baseline Finish" value={data.kpis.baselineFinish ? formatDataDate(data.kpis.baselineFinish) : "Not Designated"} color={data.kpis.baselineFinish ? C.text : C.muted2} />
                <KpiCard label="Milestones at Risk" value={`${data.kpis.milestonesAtRisk} / ${data.kpis.totalMilestones ?? "—"}`} color={data.kpis.milestonesAtRisk > 0 ? C.amber : C.green} onClick={() => go(onOpenRiskMilestones)} />
                <KpiCard label="Schedule/Data Quality" value={data.kpis.dataQuality?.available ? `${data.kpis.dataQuality.activitiesWithCalendar}/${data.kpis.dataQuality.totalActivities} calendar-linked` : "Unavailable"} color={data.kpis.dataQuality?.available ? C.green : C.muted2} />
              </div>
            )}
          </Panel>

          <BaselineVsForecastPanel panel={data.baselineVsForecast} onOpen={() => go(onOpenBaselineProgress)} />
          <FloatHealthPanel panel={data.floatHealth} onOpen={(f) => go(onOpenFloatAnalysis, f)} />
          <UpdateIntelligencePanel panel={data.updateIntelligence} context={data.context} onOpen={() => go(onOpenUpdateAnalysis)} />
          <MilestoneForecastPanel panel={data.milestoneForecast} onOpen={() => go(onOpenRiskMilestones)} />
          <TopDriversPanel panel={data.topDrivers} onOpen={() => go(onOpenActivityAnalysis, { criticality: "DRIVING" })} />
          <LookAheadPanel panel={data.lookAhead} onOpen={() => go(onOpenActivityAnalysis)} />
          <ManpowerPanel panel={data.manpower} onOpen={() => go(onOpenProjectControls, "productivity")} />
          <IntelligencePanel panel={data.intelligence}
            onExplain={() => go(onOpenIntelligence)}
            onOpenIntelligence={() => go(onOpenIntelligence)}
            onGenerateReport={() => go(onOpenProjectControls, "reports")} />
        </>
      )}
    </div>
  );
}
