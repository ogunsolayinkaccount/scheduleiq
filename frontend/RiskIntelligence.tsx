import { useEffect, useRef, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import { formatDataDate, versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

const LEVEL_COLOR: Record<string, string> = { Healthy: C.green, Watch: C.amber, "At Risk": C.orange, Critical: C.red };
const SEV_COLOR: Record<string, string> = { CRITICAL: C.red, HIGH: C.orange, MEDIUM: C.amber, LOW: C.muted2 };
const SCENARIO_STATUS_COLOR: Record<string, string> = { DRAFT: C.muted2, UNDER_REVIEW: C.amber, ACCEPTED: C.green, REJECTED: C.red, ARCHIVED: C.muted };
const RISK_STATUS_OPTIONS = ["OPEN", "UNDER_REVIEW", "MITIGATION_PLANNED", "MITIGATION_IN_PROGRESS", "MONITORING", "CLOSED"];
const SCENARIO_STATUS_OPTIONS = ["DRAFT", "UNDER_REVIEW", "ACCEPTED", "REJECTED", "ARCHIVED"];
const MITIGATION_STATUS_OPTIONS = ["OPEN", "IN_PROGRESS", "BLOCKED", "COMPLETE", "CANCELLED"];

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 150 }}>
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
  // Consumed at most once — a quick-link's requested version should not
  // keep overriding the user's own later version picks in this session.
  const initialVersionConsumed = useRef(false);

  useEffect(() => {
    fetch(`${API}/api/projects/?withVersions=true`).then(r => r.json()).then(d => { const list = d.projects || []; setProjects(list); setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur)); }).catch(() => {});
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

  // Switching project clears the previous project's versions/versionId in the SAME
  // render, and only a versionId that belongs to the CURRENT project's loaded
  // versions is ever exposed - so a fetch can never be issued as
  // /projects/NEW/...?version=OLD ("No schedule version available for this project").
  const selectProject = (id: string) => { setVersions([]); setVersionId(""); setProjectId(id); };
  const safeVersionId = versions.some((v: any) => v.id === versionId) ? versionId : "";
  return { projects, projectId, setProjectId: selectProject, versions, versionId: safeVersionId, setVersionId };
}

// ─── VIRTUALIZED ROW LIST ───────────────────────────────────────────────────
// Dependency-free windowed rendering: only the rows scrolled into view are
// mounted. This is purely a rendering optimization — the full `items` array
// passed in is always the complete, unfiltered-by-count analyzed population;
// nothing is ever capped before it reaches this component.

function VirtualRows({ items, rowHeight, height, renderRow }: {
  items: any[]; rowHeight: number; height: number; renderRow: (item: any, index: number) => React.ReactNode;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const overscan = 10;
  const total = items.length;
  const totalHeight = total * rowHeight;
  const startIdx = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visibleCount = Math.ceil(height / rowHeight) + overscan * 2;
  const endIdx = Math.min(total, startIdx + visibleCount);
  const visible = items.slice(startIdx, endIdx);
  return (
    <div onScroll={e => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      style={{ height, overflowY: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
      <div style={{ height: totalHeight, position: "relative" }}>
        <div style={{ position: "absolute", top: startIdx * rowHeight, left: 0, right: 0 }}>
          {visible.map((item, i) => renderRow(item, startIdx + i))}
        </div>
      </div>
    </div>
  );
}

function ProjectVersionBar({ ctx, extra }: { ctx: ReturnType<typeof useProjectsAndVersions>; extra?: React.ReactNode }) {
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
      {extra}
    </div>
  );
}

// ─── RISK HEAT MAP ──────────────────────────────────────────────────────────

function RiskHeatMap({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  // Dashboard Consolidation Phase 1: re-sourced from risk_register.py (the
  // SAME engine the Risk Register tab in this workspace uses) instead of
  // the older schedule_risk.py — so the two tabs of one workspace can never
  // disagree about which activities are risks or how severe they are.
  // Cells are colored by discrete severity counts (CRITICAL/HIGH/MEDIUM/LOW,
  // the register's own SEV_COLOR tiers), not a second, independent
  // continuous score.
  const [groupBy, setGroupBy] = useState("area");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drillKey, setDrillKey] = useState<string | null>(null);

  useEffect(() => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null); setDrillKey(null);
    let cancelled = false;
    sfetch(`${API}/api/projects/${ctx.projectId}/risk-register/?currentVersion=${ctx.versionId}&group_by=${groupBy}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(d => { if (!cancelled) setData(d); }).catch((e: any) => { if (!cancelled) setError(e.message || String(e)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ctx.projectId, ctx.versionId, groupBy]);

  const cellColor = (c: any) => c.criticalCount > 0 ? SEV_COLOR.CRITICAL : c.highCount > 0 ? SEV_COLOR.HIGH
    : c.mediumCount > 0 ? SEV_COLOR.MEDIUM : c.riskCount > 0 ? SEV_COLOR.LOW : C.muted2;

  const drillActivities = drillKey ? (data?.risks || []).filter((r: any) => (r[groupBy] || "Unassigned") === drillKey) : [];

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center" }}>
        <Select value={groupBy} onChange={(v: string) => setGroupBy(v)} options={[
          { value: "area", label: "Group: Area" }, { value: "wbs", label: "Group: WBS" },
          { value: "contractor", label: "Group: Contractor" }, { value: "discipline", label: "Group: Discipline" },
          { value: "system", label: "Group: System" },
        ]} />
      </div>

      {loading && <div style={{ color: C.accent, padding: 20 }}>Computing risk…</div>}
      {error && <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>}
      {data && data.available === false && <div style={{ color: C.muted, padding: 20 }}>{data.reason}</div>}

      {data && data.available !== false && (
        <>
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px", marginBottom: 16, display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: C.text }}>{data.summary.totalRisks}</div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Total Risks</div>
              <div style={{ fontSize: 11, color: C.muted }}>
                {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map(lvl => `${data.summary.bySeverity[lvl]} ${lvl.toLowerCase()}`).join(" · ")}
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
            {data.heatmap.cells.map((c: any) => {
              const color = cellColor(c);
              return (
                <div key={c.group} onClick={() => setDrillKey(c.group)}
                  style={{ background: `${color}12`, border: `1px solid ${color}`, borderRadius: 10, padding: "12px 14px", cursor: "pointer" }}
                  title="Click to drill into the risks in this group">
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.group}</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 4 }}>
                    <span style={{ fontSize: 22, fontWeight: 800, color }}>{c.riskCount}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color }}>risk{c.riskCount === 1 ? "" : "s"}</span>
                  </div>
                  <div style={{ fontSize: 10, color: C.muted2, lineHeight: 1.6 }}>
                    {c.criticalCount > 0 && <div>{c.criticalCount} critical</div>}
                    {c.highCount > 0 && <div>{c.highCount} high</div>}
                    {c.negativeFloatCount > 0 && <div>{c.negativeFloatCount} negative float</div>}
                    <div title="Never summed — min/median only">Min TF {c.minimumTotalFloat ?? "—"} · Median TF {c.medianTotalFloat ?? "—"}</div>
                  </div>
                </div>
              );
            })}
            {data.heatmap.cells.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No risks found — this schedule may not have {groupBy} metadata assigned, or is genuinely healthy.</div>}
          </div>

          {drillKey && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 8 }}>Risks in "{drillKey}"</div>
              <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
                  <thead><tr style={{ background: C.card2 }}>
                    {["Activity ID", "Activity Name", "Severity", "Urgency", "Total Float"].map(h => (
                      <th key={h} style={{ padding: "7px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {drillActivities.map((a: any) => (
                      <tr key={a.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: "6px 12px", fontFamily: "monospace" }}>{a.activityId}</td>
                        <td style={{ padding: "6px 12px" }}>{a.activityName}</td>
                        <td style={{ padding: "6px 12px", fontWeight: 700, color: SEV_COLOR[a.severity] }}>{a.severity}</td>
                        <td style={{ padding: "6px 12px", color: SEV_COLOR[a.urgency] }}>{a.urgency}</td>
                        <td style={{ padding: "6px 12px", color: a.totalFloat != null && a.totalFloat < 0 ? C.red : C.text }}>{a.totalFloat ?? "—"}</td>
                      </tr>
                    ))}
                    {drillActivities.length === 0 && (
                      <tr><td colSpan={5} style={{ padding: 16, textAlign: "center", color: C.muted }}>No at-risk activities in this group.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── MILESTONES ─────────────────────────────────────────────────────────────

function MilestoneDashboard({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  const [horizon, setHorizon] = useState("");
  const [flags, setFlags] = useState({ slipped: false, negativeFloat: false, movedThisUpdate: false, newlyCritical: false });
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    const params = new URLSearchParams({ version: ctx.versionId });
    if (horizon) params.set("horizonDays", horizon);
    for (const [k, v] of Object.entries(flags)) if (v) params.set(k, "true");
    let cancelled = false;
    sfetch(`${API}/api/projects/${ctx.projectId}/milestones/?${params.toString()}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(d => { if (!cancelled) setData(d); }).catch((e: any) => { if (!cancelled) setError(e.message || String(e)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ctx.projectId, ctx.versionId, horizon, flags]);

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.muted }}>
          Upcoming within:
          <select value={horizon} onChange={e => setHorizon(e.target.value)}
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", fontSize: 12, fontFamily: "inherit" }}>
            <option value="">Any time</option>
            <option value="14">14 days</option>
            <option value="30">30 days</option>
            <option value="60">60 days</option>
            <option value="90">90 days</option>
          </select>
        </div>
        {([
          ["slipped", "Slipped"], ["negativeFloat", "Negative Float"],
          ["movedThisUpdate", "Moved This Update"], ["newlyCritical", "Newly Critical"],
        ] as const).map(([key, label]) => (
          <button key={key} onClick={() => setFlags(f => ({ ...f, [key]: !f[key] }))}
            style={{ background: flags[key] ? `${C.accent}18` : C.card, border: `1px solid ${flags[key] ? C.accent : C.border}`, color: flags[key] ? C.accent : C.muted2, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>
            {label}
          </button>
        ))}
      </div>

      {loading && <div style={{ color: C.accent, padding: 20 }}>Loading milestones…</div>}
      {error && <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>}

      {data && (
        <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
            <thead><tr style={{ background: C.card2 }}>
              {["ID", "Milestone", "Current Finish", "Baseline Finish", "Variance", "Float", "Status", "Risk", "Driving Predecessor", "Movement"].map(h => (
                <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {data.milestones.map((m: any) => (
                <tr key={m.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: "7px 12px", fontFamily: "monospace" }}>{m.activityId}</td>
                  <td style={{ padding: "7px 12px", fontWeight: 600 }}>{m.activityName}</td>
                  <td style={{ padding: "7px 12px" }}>{m.currentFinish || "—"}</td>
                  <td style={{ padding: "7px 12px", color: C.muted2 }}>{m.baselineFinish || "—"}</td>
                  <td style={{ padding: "7px 12px", color: m.varianceDays > 0 ? C.red : m.varianceDays < 0 ? C.green : C.muted2 }}>
                    {m.varianceDays != null ? `${m.varianceDays > 0 ? "+" : ""}${m.varianceDays}d` : "—"}
                  </td>
                  <td style={{ padding: "7px 12px", color: m.totalFloat != null && m.totalFloat < 0 ? C.red : C.muted2 }}>{m.totalFloat != null ? `${m.totalFloat}d` : "—"}</td>
                  <td style={{ padding: "7px 12px" }}>{m.status}</td>
                  <td style={{ padding: "7px 12px", color: LEVEL_COLOR[m.riskLevel], fontWeight: 700 }}>{m.riskLevel}</td>
                  <td style={{ padding: "7px 12px", fontSize: 11, color: C.muted2 }}>
                    {m.drivingPredecessor ? `${m.drivingPredecessor.activityId} (${m.drivingPredecessor.totalFloat ?? "—"}d)` : "—"}
                  </td>
                  <td style={{ padding: "7px 12px", color: m.movementSinceLastUpdateDays > 0 ? C.red : m.movementSinceLastUpdateDays < 0 ? C.green : C.muted2 }}>
                    {m.movementSinceLastUpdateDays != null ? `${m.movementSinceLastUpdateDays > 0 ? "+" : ""}${m.movementSinceLastUpdateDays}d` : "—"}
                  </td>
                </tr>
              ))}
              {data.milestones.length === 0 && (
                <tr><td colSpan={10} style={{ padding: 20, textAlign: "center", color: C.muted }}>No milestones match the current filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── DRIVING CHAIN (embedded in Risk Detail) ───────────────────────────────

function ChainNode({ node, highlight }: { node: any; highlight?: boolean }) {
  return (
    <div style={{ background: highlight ? `${C.accent}12` : C.card2, border: `1px solid ${highlight ? C.accent : C.border}`, borderRadius: 8, padding: "8px 12px", minWidth: 160 }}>
      <div style={{ fontSize: 11, fontFamily: "monospace", color: C.muted2 }}>{node.activityId}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.activityName || "—"}</div>
      <div style={{ fontSize: 10, color: node.totalFloat != null && node.totalFloat < 0 ? C.red : C.muted2, marginTop: 2 }}>
        Float {node.totalFloat != null ? `${node.totalFloat}d` : "—"} · {node.currentFinish || "—"}
        {node.isCritical && <span style={{ color: C.red, fontWeight: 700 }}> · Critical</span>}
      </div>
      {node.relationshipFromPrevious && (
        <div style={{ fontSize: 9, color: C.muted, marginTop: 2 }}>{node.relationshipFromPrevious.relType} lag {node.relationshipFromPrevious.lagDays}d</div>
      )}
    </div>
  );
}

function DrivingChainPanel({ ctx, riskKey }: { ctx: ReturnType<typeof useProjectsAndVersions>; riskKey: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ctx.projectId || !riskKey) return;
    setLoading(true); setError(null); setData(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/risk-register/${encodeURIComponent(riskKey)}/driving-chain/?version=${ctx.versionId}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  }, [ctx.projectId, ctx.versionId, riskKey]);

  if (loading) return <div style={{ color: C.accent, padding: 12 }}>Tracing driving chain…</div>;
  if (error) return <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>;
  if (!data) return null;

  const path = data.drivingPath;
  const blocking = data.blockingPredecessors;
  const exposure = data.downstreamExposure;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {path?.available && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Driving/Critical Path Trace</div>
          <div style={{ fontSize: 10, color: C.muted, fontStyle: "italic", marginBottom: 8 }}>{path.methodologyNote}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            {path.upstreamChain.map((n: any, i: number) => (
              <div key={`u${i}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <ChainNode node={n} highlight={i === path.upstreamChain.length - 1} />
                {i < path.upstreamChain.length - 1 && <span style={{ color: C.muted }}>→</span>}
              </div>
            ))}
            {path.upstreamChain.length > 0 && <span style={{ color: C.muted }}>→</span>}
            {path.downstreamChain.map((n: any, i: number) => (
              <div key={`d${i}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <ChainNode node={n} highlight={i === 0} />
                {i < path.downstreamChain.length - 1 && <span style={{ color: C.muted }}>→</span>}
              </div>
            ))}
          </div>
          {(path.upstreamTruncated || path.downstreamTruncated) && (
            <div style={{ fontSize: 10, color: C.amber, marginTop: 6 }}>⚠ Chain truncated at the hop limit — the trace continues beyond what's shown.</div>
          )}
        </div>
      )}

      {blocking?.available && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Blocking / Driving Predecessors</div>
          <div style={{ fontSize: 10, color: C.muted, fontStyle: "italic", marginBottom: 8 }}>{blocking.methodologyNote}</div>
          {blocking.blockingPredecessors.length === 0 && <div style={{ fontSize: 12, color: C.muted }}>No predecessors on this activity.</div>}
          {blocking.blockingPredecessors.map((p: any) => (
            <div key={p.activityId} style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", marginBottom: 6, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700 }}>{p.activityId} — {p.activityName}</div>
                <div style={{ fontSize: 10, color: C.muted2 }}>{p.relationshipType} lag {p.lagDays}d · Float {p.totalFloat ?? "—"}d · {p.pctComplete}% complete</div>
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {p.conditions.map((c: string) => (
                  <span key={c} style={{ fontSize: 9, fontWeight: 700, color: C.amber, background: `${C.amber}15`, borderRadius: 5, padding: "2px 6px" }}>{c.replace(/_/g, " ")}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {exposure?.available && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Downstream Exposure</div>
          <div style={{ fontSize: 10, color: C.muted, fontStyle: "italic", marginBottom: 8 }}>{exposure.methodologyNote}</div>
          <div style={{ fontSize: 12, color: C.text, marginBottom: 6 }}>{exposure.reachableActivityCount} downstream activities reachable{exposure.truncated ? " (bounded — network larger than the trace limit)" : ""}.</div>
          {exposure.exposedMilestones.length === 0 && <div style={{ fontSize: 12, color: C.muted }}>No downstream milestones connected.</div>}
          {exposure.exposedMilestones.map((m: any) => (
            <div key={m.activityId} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: m.forecastImpacting ? `${C.red}0a` : C.card2, border: `1px solid ${m.forecastImpacting ? C.red : C.border}`, borderRadius: 8, padding: "6px 12px", marginBottom: 4 }}>
              <div style={{ fontSize: 12 }}>{m.activityId} — {m.activityName}</div>
              <div style={{ fontSize: 10, fontWeight: 700, color: m.forecastImpacting ? C.red : C.muted2 }}>
                {m.forecastImpacting ? "Forecast-impacting" : "Connected downstream"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── RISK DETAIL DRAWER ─────────────────────────────────────────────────────

function RiskDetailDrawer({ ctx, risk, onClose, onWorkflowSaved, onCreateScenario }: {
  ctx: ReturnType<typeof useProjectsAndVersions>; risk: any; onClose: () => void;
  onWorkflowSaved: (riskKey: string, workflow: any) => void; onCreateScenario: (riskKey: string) => void;
}) {
  const [wf, setWf] = useState(risk.workflow || { status: "OPEN", owner: "", mitigationNotes: "", targetDate: null });
  const [saving, setSaving] = useState(false);
  const [subTab, setSubTab] = useState<"signals" | "chain">("signals");

  useEffect(() => { setWf(risk.workflow || { status: "OPEN", owner: "", mitigationNotes: "", targetDate: null }); }, [risk.riskKey]);

  const save = () => {
    setSaving(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/risk-register/${encodeURIComponent(risk.riskKey)}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: wf.status, owner: wf.owner, mitigationNotes: wf.mitigationNotes, targetDate: wf.targetDate }),
    }).then(r => r.json()).then(d => onWorkflowSaved(risk.riskKey, d)).finally(() => setSaving(false));
  };

  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 480, background: C.bg, borderLeft: `1px solid ${C.border}`, boxShadow: "-6px 0 24px rgba(0,0,0,0.15)", zIndex: 1000, overflowY: "auto", padding: 22 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 11, fontFamily: "monospace", color: C.muted2 }}>{risk.activityId}</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: C.text }}>{risk.activityName}</div>
        </div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 18 }}>✕</button>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <span style={{ background: `${SEV_COLOR[risk.severity]}15`, color: SEV_COLOR[risk.severity], fontWeight: 800, fontSize: 12, borderRadius: 6, padding: "3px 10px" }}>{risk.severity}</span>
        <span style={{ background: `${SEV_COLOR[risk.urgency]}15`, color: SEV_COLOR[risk.urgency], fontWeight: 700, fontSize: 12, borderRadius: 6, padding: "3px 10px" }}>Urgency: {risk.urgency}</span>
        {risk.driving && <span style={{ background: `${C.purple}15`, color: C.purple, fontWeight: 700, fontSize: 12, borderRadius: 6, padding: "3px 10px" }}>Driving</span>}
      </div>

      <div style={{ fontSize: 12, color: C.text, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 8 }}>
        <strong>Why:</strong> {risk.severityReason}
      </div>
      <div style={{ fontSize: 12, color: C.text, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 14 }}>
        <strong>Urgency:</strong> {risk.urgencyReason}
      </div>

      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 14 }}>
        {[{ id: "signals", label: "Risk Signals" }, { id: "chain", label: "Driving Chain" }].map(t => (
          <button key={t.id} onClick={() => setSubTab(t.id as any)}
            style={{ background: "transparent", border: "none", borderBottom: `2px solid ${subTab === t.id ? C.accent : "transparent"}`, color: subTab === t.id ? C.accent : C.muted2, padding: "6px 10px", cursor: "pointer", fontSize: 12, fontFamily: "inherit", fontWeight: subTab === t.id ? 800 : 600 }}>
            {t.label}
          </button>
        ))}
      </div>

      {subTab === "signals" && (
        <div style={{ marginBottom: 18 }}>
          {(risk.riskSignals || []).map((s: any, i: number) => (
            <div key={i} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", marginBottom: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.accent }}>{s.type.replace(/_/g, " ")}</div>
              <div style={{ fontSize: 12, color: C.text }}>{s.description}</div>
            </div>
          ))}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 11, color: C.muted2, marginTop: 10 }}>
            <div>Baseline Finish: {risk.baselineFinish || "—"}</div>
            <div>Baseline Variance: {risk.baselineVarianceDays != null ? `${risk.baselineVarianceDays}d` : "—"}</div>
            <div>Current Finish: {risk.currentFinish || "—"}</div>
            <div>Total Float: {risk.totalFloat != null ? `${risk.totalFloat}d` : "—"}</div>
            <div>Update Movement: {risk.updateMovementDays != null ? `${risk.updateMovementDays > 0 ? "+" : ""}${risk.updateMovementDays}d` : "—"}</div>
            <div>Discipline: {risk.discipline || "—"}</div>
            <div>Area: {risk.area || "—"}</div>
          </div>
          {risk.milestoneExposure && (
            <div style={{ marginTop: 10, fontSize: 11, color: C.muted2 }}>
              Connected to {risk.milestoneExposure.connectedMilestoneCount ?? 0} downstream milestone(s)
              {risk.milestoneExposure.forecastImpactingMilestones?.length > 0 && (
                <span style={{ color: C.red, fontWeight: 700 }}> — {risk.milestoneExposure.forecastImpactingMilestones.length} forecast-impacting</span>
              )}
            </div>
          )}
        </div>
      )}
      {subTab === "chain" && <div style={{ marginBottom: 18 }}><DrivingChainPanel ctx={ctx} riskKey={risk.riskKey} /></div>}

      <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 14 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Workflow (persisted, never recalculated)</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Select value={wf.status} onChange={(v: string) => setWf((p: any) => ({ ...p, status: v }))}
            options={RISK_STATUS_OPTIONS.map(s => ({ value: s, label: s.replace(/_/g, " ") }))} />
          <input value={wf.owner || ""} onChange={e => setWf((p: any) => ({ ...p, owner: e.target.value }))} placeholder="Owner"
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <textarea value={wf.mitigationNotes || ""} onChange={e => setWf((p: any) => ({ ...p, mitigationNotes: e.target.value }))} placeholder="Mitigation notes" rows={3}
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", resize: "vertical" }} />
          <input type="date" value={wf.targetDate || ""} onChange={e => setWf((p: any) => ({ ...p, targetDate: e.target.value || null }))}
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <button onClick={save} disabled={saving} style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "8px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
            {saving ? "Saving…" : "Save Workflow"}
          </button>
          {risk.workflow?.firstIdentifiedDataDate && (
            <div style={{ fontSize: 10, color: C.muted }}>First identified {formatDataDate(risk.workflow.firstIdentifiedDataDate)}</div>
          )}
        </div>
        <button onClick={() => onCreateScenario(risk.riskKey)}
          style={{ marginTop: 14, width: "100%", background: `${C.purple}12`, border: `1px solid ${C.purple}`, color: C.purple, borderRadius: 7, padding: "9px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
          Analyze Recovery →
        </button>
      </div>
    </div>
  );
}

// ─── RISK REGISTER ──────────────────────────────────────────────────────────

function useRiskRegister(ctx: ReturnType<typeof useProjectsAndVersions>) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSeq = useRef(0);
  const load = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    const mine = ++loadSeq.current;          // only the newest request may update state
    setLoading(true); setError(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/risk-register/?currentVersion=${ctx.versionId}&group_by=discipline`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(d => { if (mine === loadSeq.current) setData(d); }).catch((e: any) => { if (mine === loadSeq.current) setError(e.message || String(e)); }).finally(() => { if (mine === loadSeq.current) setLoading(false); });
  };
  useEffect(load, [ctx.projectId, ctx.versionId]);

  return { data, loading, error, reload: load, setData };
}

const GROUP_FIELDS = [
  { value: "none", label: "No Grouping" }, { value: "discipline", label: "Group: Discipline" },
  { value: "area", label: "Group: Area" }, { value: "wbs", label: "Group: WBS" },
  { value: "contractor", label: "Group: Contractor" }, { value: "system", label: "Group: System" },
];
const SORT_FIELDS = [
  { value: "default", label: "Sort: Severity/Urgency" }, { value: "activityId", label: "Sort: Activity ID" },
  { value: "float", label: "Sort: Total Float" }, { value: "movement", label: "Sort: Update Movement" },
];

function RiskRegisterTab({ ctx, onAnalyzeRecovery }: { ctx: ReturnType<typeof useProjectsAndVersions>; onAnalyzeRecovery: (riskKey: string) => void }) {
  const { data, loading, error, setData } = useRiskRegister(ctx);
  const [search, setSearch] = useState("");
  const [sevFilter, setSevFilter] = useState<Set<string>>(new Set());
  const [drivingOnly, setDrivingOnly] = useState(false);
  const [negFloatOnly, setNegFloatOnly] = useState(false);
  const [milestoneOnly, setMilestoneOnly] = useState(false);
  const [groupBy, setGroupBy] = useState("none");
  const [sortBy, setSortBy] = useState("default");
  const [selected, setSelected] = useState<any | null>(null);

  const onWorkflowSaved = (riskKey: string, workflow: any) => {
    setData((d: any) => d && { ...d, risks: d.risks.map((r: any) => r.riskKey === riskKey ? { ...r, workflow } : r) });
    setSelected((s: any) => s && s.riskKey === riskKey ? { ...s, workflow } : s);
  };

  if (!ctx.projectId || !ctx.versionId) return <div style={{ color: C.muted, padding: 20 }}>Select a project and version above.</div>;
  if (loading) return <div style={{ color: C.accent, padding: 20 }}>Building risk register…</div>;
  if (error) return <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>;
  if (data && data.available === false) return <div style={{ color: C.muted, padding: 20, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>{data.reason}</div>;
  if (!data) return null;

  const allRisks: any[] = data.risks || [];
  let filtered = allRisks.filter(r => {
    if (sevFilter.size > 0 && !sevFilter.has(r.severity)) return false;
    if (drivingOnly && !r.driving) return false;
    if (negFloatOnly && !(r.totalFloat != null && r.totalFloat < 0)) return false;
    if (milestoneOnly && !(r.milestoneExposure?.forecastImpactingMilestones?.length > 0)) return false;
    if (search) {
      const s = search.toLowerCase();
      if (!r.activityId.toLowerCase().includes(s) && !(r.activityName || "").toLowerCase().includes(s)) return false;
    }
    return true;
  });
  if (sortBy === "activityId") filtered = [...filtered].sort((a, b) => a.activityId.localeCompare(b.activityId));
  else if (sortBy === "float") filtered = [...filtered].sort((a, b) => (a.totalFloat ?? Infinity) - (b.totalFloat ?? Infinity));
  else if (sortBy === "movement") filtered = [...filtered].sort((a, b) => (b.updateMovementDays ?? -Infinity) - (a.updateMovementDays ?? -Infinity));

  // Build a flat row list (group headers + risk rows) for the virtualized list —
  // grouping/sorting/filtering all run over the FULL analyzed population above;
  // this only decides how that complete result is laid out for scrolling.
  const rows: any[] = [];
  if (groupBy === "none") {
    for (const r of filtered) rows.push({ kind: "risk", risk: r });
  } else {
    const groups: Record<string, any[]> = {};
    for (const r of filtered) {
      const g = r[groupBy] || "Unassigned";
      (groups[g] = groups[g] || []).push(r);
    }
    for (const g of Object.keys(groups).sort()) {
      rows.push({ kind: "group", label: g, count: groups[g].length });
      for (const r of groups[g]) rows.push({ kind: "risk", risk: r });
    }
  }

  const summary = data.summary;

  return (
    <div>
      {summary && (
        <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
          {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map(lvl => (
            <div key={lvl} style={{ background: `${SEV_COLOR[lvl]}10`, border: `1px solid ${SEV_COLOR[lvl]}`, borderRadius: 10, padding: "8px 16px", minWidth: 90 }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: SEV_COLOR[lvl] }}>{summary.bySeverity[lvl]}</div>
              <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>{lvl}</div>
            </div>
          ))}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "8px 16px" }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: C.purple }}>{summary.drivingRiskCount}</div>
            <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Driving</div>
          </div>
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "8px 16px" }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: C.red }}>{summary.negativeFloatCount}</div>
            <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Negative Float</div>
          </div>
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "8px 16px" }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: C.orange }}>{summary.milestoneExposureCount}</div>
            <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Milestone-Exposed</div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search Activity ID or name…"
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 200 }} />
        {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map(lvl => (
          <button key={lvl} onClick={() => setSevFilter(s => { const n = new Set(s); n.has(lvl) ? n.delete(lvl) : n.add(lvl); return n; })}
            style={{ background: sevFilter.has(lvl) ? `${SEV_COLOR[lvl]}18` : C.card, border: `1px solid ${sevFilter.has(lvl) ? SEV_COLOR[lvl] : C.border}`, color: sevFilter.has(lvl) ? SEV_COLOR[lvl] : C.muted2, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>
            {lvl}
          </button>
        ))}
        {([["driving", "Driving", drivingOnly, setDrivingOnly], ["neg", "Negative Float", negFloatOnly, setNegFloatOnly], ["ms", "Milestone-Exposed", milestoneOnly, setMilestoneOnly]] as const).map(([key, label, val, setter]) => (
          <button key={key} onClick={() => (setter as any)((v: boolean) => !v)}
            style={{ background: val ? `${C.accent}18` : C.card, border: `1px solid ${val ? C.accent : C.border}`, color: val ? C.accent : C.muted2, borderRadius: 7, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 600, fontFamily: "inherit" }}>
            {label}
          </button>
        ))}
        <Select value={groupBy} onChange={setGroupBy} options={GROUP_FIELDS} />
        <Select value={sortBy} onChange={setSortBy} options={SORT_FIELDS} />
        <div style={{ fontSize: 11, color: C.muted }}>{filtered.length} of {allRisks.length} risks</div>
      </div>

      <VirtualRows items={rows} rowHeight={54} height={520} renderRow={(row, i) => {
        if (row.kind === "group") {
          return (
            <div key={`g${i}`} style={{ height: 54, display: "flex", alignItems: "center", padding: "0 14px", background: C.panel, borderBottom: `1px solid ${C.border}`, fontSize: 12, fontWeight: 800, color: C.text }}>
              {row.label} <span style={{ color: C.muted, fontWeight: 600, marginLeft: 8 }}>({row.count})</span>
            </div>
          );
        }
        const r = row.risk;
        return (
          <div key={r.riskKey} onClick={() => setSelected(r)}
            style={{ height: 54, display: "grid", gridTemplateColumns: "110px 1fr 90px 90px 90px 1fr 110px", alignItems: "center", padding: "0 14px", borderBottom: `1px solid ${C.border}`, cursor: "pointer", background: C.card }}>
            <div style={{ fontSize: 11, fontFamily: "monospace", color: C.muted2 }}>{r.activityId}</div>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.activityName}</div>
            <div style={{ fontSize: 11, fontWeight: 800, color: SEV_COLOR[r.severity] }}>{r.severity}</div>
            <div style={{ fontSize: 11, fontWeight: 700, color: SEV_COLOR[r.urgency] }}>{r.urgency}</div>
            <div style={{ fontSize: 11, color: r.totalFloat != null && r.totalFloat < 0 ? C.red : C.muted2 }}>{r.totalFloat != null ? `${r.totalFloat}d` : "—"}</div>
            <div style={{ fontSize: 11, color: C.muted2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.riskSignals?.[0]?.type.replace(/_/g, " ") || "—"}</div>
            <div style={{ fontSize: 10, fontWeight: 700, color: C.muted2 }}>{r.workflow?.status || "OPEN"}</div>
          </div>
        );
      }} />

      {selected && (
        <RiskDetailDrawer ctx={ctx} risk={selected} onClose={() => setSelected(null)}
          onWorkflowSaved={onWorkflowSaved} onCreateScenario={(riskKey) => { setSelected(null); onAnalyzeRecovery(riskKey); }} />
      )}
    </div>
  );
}

// ─── RECOVERY SCENARIOS ─────────────────────────────────────────────────────

const ACTION_TYPES = [
  { value: "reduce_duration", label: "Reduce Duration (calculated)" },
  { value: "remove_lag", label: "Remove Lag (calculated)" },
  { value: "reduce_lag", label: "Reduce Lag (calculated)" },
  { value: "change_relationship_type", label: "Change Relationship Type (calculated)" },
  { value: "add_relationship", label: "Add Relationship (calculated)" },
  { value: "remove_relationship", label: "Remove Relationship (calculated)" },
  { value: "increase_crew", label: "Increase Crew (advisory)" },
  { value: "accelerate_engineering", label: "Accelerate Engineering Release (advisory)" },
  { value: "accelerate_procurement", label: "Accelerate Procurement (advisory)" },
  { value: "accelerate_prefab", label: "Accelerate Prefab Release (advisory)" },
  { value: "split_activity", label: "Split Activity (advisory)" },
  { value: "other", label: "Other (advisory)" },
];

function buildAction(type: string, f: Record<string, string>): any {
  const num = (v: string) => (v === "" ? undefined : Number(v));
  switch (type) {
    case "reduce_duration": return { type, activityId: f.activityId, newDuration: num(f.value) };
    case "remove_lag": return { type, predecessorId: f.predecessorId, successorId: f.successorId };
    case "reduce_lag": return { type, predecessorId: f.predecessorId, successorId: f.successorId, newLagDays: num(f.value) };
    case "change_relationship_type": return { type, predecessorId: f.predecessorId, successorId: f.successorId, newRelType: f.relType };
    case "add_relationship": return { type, predecessorId: f.predecessorId, successorId: f.successorId, relType: f.relType || "FS", lagDays: num(f.value) || 0 };
    case "remove_relationship": return { type, predecessorId: f.predecessorId, successorId: f.successorId };
    default: return { type, activityId: f.activityId, note: f.note };
  }
}

function MetricCard({ label, value, color, sub }: { label: string; value: React.ReactNode; color?: string; sub?: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 18px", flex: "1 1 160px" }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2 }}>{sub}</div>}
    </div>
  );
}

function SideEffectsActivityList({ items, color }: { items: any[]; color: string }) {
  if (!items || items.length === 0) return null;
  return (
    <div style={{ maxHeight: 110, overflowY: "auto", marginTop: 4, marginBottom: 6 }}>
      {items.map((a: any, i: number) => (
        <div key={i} style={{ fontSize: 10, color: C.muted2, padding: "2px 0" }}>
          <span style={{ fontFamily: "monospace", color }}>{a.activityId}</span> {a.activityName || ""}
          {a.movementDays != null && <span> · {a.movementDays > 0 ? "+" : ""}{a.movementDays}d</span>}
          {a.currentTotalFloat !== undefined && <span> · float {a.currentTotalFloat ?? "—"}d → {a.scenarioTotalFloat ?? "—"}d</span>}
        </div>
      ))}
    </div>
  );
}

function SideEffectsPanel({ effects }: { effects: any }) {
  if (!effects) return null;
  const groups: [string, string, string][] = [
    ["improved", "improvedCount", "Improved"], ["worsened", "worsenedCount", "Worsened"],
    ["newlyCritical", "newlyCriticalCount", "Newly Critical"], ["leftCriticalPath", "leftCriticalPathCount", "Left Critical Path"],
    ["newlyNegativeFloat", "newlyNegativeFloatCount", "Newly Negative Float"], ["recoveredFromNegativeFloat", "recoveredFromNegativeFloatCount", "Recovered From Negative Float"],
  ];
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>
        Side Effects (always shown, not only positive outcomes)
      </div>
      {groups.filter(([key]) => key !== "unchanged" && effects[key]?.length > 0).map(([key, countKey, label]) => {
        const color = key === "worsened" || key === "newlyCritical" || key === "newlyNegativeFloat" ? C.red : key === "improved" || key === "recoveredFromNegativeFloat" ? C.green : C.text;
        return (
          <div key={key} style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 12px", marginBottom: 6 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color }}>{effects[countKey] || 0}</span>
            <span style={{ fontSize: 10, color: C.muted, marginLeft: 6 }}>{label}</span>
            <SideEffectsActivityList items={effects[key]} color={color} />
          </div>
        );
      })}
      {groups.every(([key]) => !(effects[key]?.length > 0)) && (
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>No activities changed by this scenario.</div>
      )}
      {(effects.worsened?.length > 0 || effects.newlyNegativeFloat?.length > 0) && (
        <div style={{ fontSize: 11, color: C.red, background: `${C.red}0a`, border: `1px solid ${C.red}30`, borderRadius: 8, padding: "8px 12px" }}>
          ⚠ This scenario negatively affects {effects.worsenedCount || 0} activit{(effects.worsenedCount || 0) === 1 ? "y" : "ies"} even where it recovers others — review before accepting.
        </div>
      )}
    </div>
  );
}

function MilestoneImpactTable({ impact }: { impact: any[] }) {
  if (!impact || impact.length === 0) return <div style={{ fontSize: 12, color: C.muted }}>No milestone impact data.</div>;
  return (
    <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
        <thead><tr style={{ background: C.card2 }}>
          {["Milestone", "Current Forecast", "Scenario Forecast", "Movement", "Baseline Var (Current)", "Baseline Var (Scenario)", "Recovered", "Recovery %"].map(h => (
            <th key={h} style={{ padding: "6px 10px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {impact.map((m: any) => (
            <tr key={m.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
              <td style={{ padding: "6px 10px" }}>{m.activityId} — {m.activityName}</td>
              <td style={{ padding: "6px 10px" }}>{m.currentForecastFinish || "—"}</td>
              <td style={{ padding: "6px 10px" }}>{m.scenarioForecastFinish || "—"}</td>
              <td style={{ padding: "6px 10px", color: m.movementDays > 0 ? C.green : m.movementDays < 0 ? C.red : C.muted2 }}>{m.movementDays != null ? `${m.movementDays > 0 ? "-" : "+"}${Math.abs(m.movementDays)}d` : "—"}</td>
              <td style={{ padding: "6px 10px" }}>{m.currentBaselineVarianceDays != null ? `${m.currentBaselineVarianceDays}d` : "—"}</td>
              <td style={{ padding: "6px 10px" }}>{m.scenarioBaselineVarianceDays != null ? `${m.scenarioBaselineVarianceDays}d` : "—"}</td>
              <td style={{ padding: "6px 10px", color: (m.varianceRecoveredDays || 0) > 0 ? C.green : C.muted2 }}>{m.varianceRecoveredDays != null ? `${m.varianceRecoveredDays}d` : "—"}</td>
              <td style={{ padding: "6px 10px" }}>{m.recoveryPct != null ? `${m.recoveryPct}%` : "Unavailable"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecoveryTrackingPanel({ ctx, scenarioId }: { ctx: ReturnType<typeof useProjectsAndVersions>; scenarioId: string }) {
  const [tracking, setTracking] = useState<any>(null);
  useEffect(() => {
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${scenarioId}/`).then(r => r.json()).then(d => setTracking(d.recoveryTracking));
  }, [ctx.projectId, scenarioId]);
  if (!tracking) return null;
  if (!tracking.available) return <div style={{ fontSize: 11, color: C.muted, fontStyle: "italic" }}>Recovery tracking: {tracking.reason}</div>;
  const CLASS_COLOR: Record<string, string> = { ON_TRACK: C.green, PARTIALLY_REALIZED: C.amber, NOT_REALIZED: C.red, UNAVAILABLE: C.muted2 };
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>
        Recovery Tracking vs {tracking.nextVersionLabel} ({tracking.nextDataDate})
      </div>
      {tracking.milestoneComparisons.map((m: any) => (
        <div key={m.activityId} style={{ display: "flex", justifyContent: "space-between", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 12px", marginBottom: 4 }}>
          <div style={{ fontSize: 11 }}>{m.activityId} — {m.activityName}</div>
          <div style={{ fontSize: 10, fontWeight: 700, color: CLASS_COLOR[m.classification] }}>{m.classification.replace(/_/g, " ")}</div>
        </div>
      ))}
    </div>
  );
}

function RecoveryScenariosTab({ ctx, pendingRiskKey, onConsumePendingRiskKey }: {
  ctx: ReturnType<typeof useProjectsAndVersions>; pendingRiskKey?: string; onConsumePendingRiskKey: () => void;
}) {
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [newName, setNewName] = useState("");
  const [actionType, setActionType] = useState("reduce_duration");
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [compareMode, setCompareMode] = useState(false);
  const [compareBId, setCompareBId] = useState<string>("");
  const [compareB, setCompareB] = useState<any>(null);

  const loadScenarios = () => {
    if (!ctx.projectId) return;
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/`).then(r => r.json()).then(d => setScenarios(d.scenarios || []));
  };
  useEffect(loadScenarios, [ctx.projectId]);

  const loadDetail = (id: string) => {
    setActiveId(id); setCompareMode(false);
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${id}/`).then(r => r.json()).then(setDetail);
  };

  // One-shot: a risk was opened via "Analyze Recovery" — create a fresh
  // scenario for it against the currently selected version. Guarded by a
  // ref (not just the pendingRiskKey prop) because React 18 StrictMode
  // double-invokes effects on mount in development — without this guard
  // that fires this non-idempotent POST twice, creating two duplicate
  // scenarios from a single "Analyze Recovery" click.
  const pendingRiskKeyStarted = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!pendingRiskKey || !ctx.projectId || !ctx.versionId) return;
    if (pendingRiskKeyStarted.current === pendingRiskKey) return;
    pendingRiskKeyStarted.current = pendingRiskKey;
    setBusy(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: `Recovery for ${pendingRiskKey}`, scheduleUploadId: ctx.versionId, riskKey: pendingRiskKey, assumptions: [] }),
    }).then(r => r.json()).then(s => { loadScenarios(); loadDetail(s.id); onConsumePendingRiskKey(); }).finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingRiskKey, ctx.projectId, ctx.versionId]);

  const createScenario = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    setBusy(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName || undefined, scheduleUploadId: ctx.versionId, assumptions: [] }),
    }).then(r => r.json()).then(s => { setNewName(""); loadScenarios(); loadDetail(s.id); }).finally(() => setBusy(false));
  };

  const addAction = () => {
    if (!detail) return;
    const action = buildAction(actionType, form);
    const assumptions = [...(detail.assumptions || []), action];
    recalculate(assumptions);
    setForm({});
  };

  const recalculate = (assumptions: any[]) => {
    if (!activeId) return;
    setBusy(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${activeId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assumptions }),
    }).then(r => r.json()).then(d => { setDetail(d); loadScenarios(); }).finally(() => setBusy(false));
  };

  const setStatus = (status: string) => {
    if (!activeId) return;
    setBusy(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${activeId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    }).then(r => r.json()).then(d => { setDetail(d); loadScenarios(); }).finally(() => setBusy(false));
  };

  const deleteScenario = (id: string) => {
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${id}/`, { method: "DELETE" })
      .then(() => { if (activeId === id) { setActiveId(null); setDetail(null); } loadScenarios(); });
  };

  useEffect(() => {
    if (!compareMode || !compareBId) { setCompareB(null); return; }
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/${compareBId}/`).then(r => r.json()).then(setCompareB);
  }, [compareMode, compareBId, ctx.projectId]);

  const result = detail?.result;

  return (
    <div style={{ display: "flex", gap: 18 }}>
      <div style={{ width: 260, flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="New scenario name…"
            style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <button onClick={createScenario} disabled={!ctx.versionId || busy}
            style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>+</button>
        </div>
        {scenarios.map(s => (
          <div key={s.id} onClick={() => loadDetail(s.id)}
            style={{ background: activeId === s.id ? `${C.accent}12` : C.card, border: `1px solid ${activeId === s.id ? C.accent : C.border}`, borderRadius: 8, padding: "8px 12px", marginBottom: 6, cursor: "pointer" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{s.name}</div>
              <button onClick={e => { e.stopPropagation(); deleteScenario(s.id); }} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 13 }}>✕</button>
            </div>
            <div style={{ fontSize: 10, fontWeight: 700, color: SCENARIO_STATUS_COLOR[s.status] }}>{s.status}{s.riskKey ? ` · ${s.riskKey}` : ""}</div>
            <div style={{ fontSize: 11, color: C.muted2 }}>{s.actionCount} action(s){s.recoveryDays != null ? ` · ${s.recoveryDays}d recovery` : ""}</div>
          </div>
        ))}
        {scenarios.length === 0 && <div style={{ color: C.muted, fontSize: 12 }}>No scenarios yet.</div>}
        {scenarios.length >= 2 && (
          <button onClick={() => setCompareMode(m => !m)}
            style={{ marginTop: 10, width: "100%", background: compareMode ? `${C.accent}18` : "transparent", border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 7, padding: "6px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
            {compareMode ? "Exit Comparison" : "Compare Two Scenarios"}
          </button>
        )}
      </div>

      <div style={{ flex: 1 }}>
        {compareMode ? (
          <div>
            <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center" }}>
              <div style={{ fontSize: 12, color: C.muted }}>Scenario A: <strong>{detail?.name || "(select on the left)"}</strong></div>
              <div style={{ fontSize: 12, color: C.muted }}>vs Scenario B:</div>
              <Select value={compareBId} onChange={setCompareBId} placeholder="Select…" options={scenarios.filter(s => s.id !== activeId).map(s => ({ value: s.id, label: s.name }))} />
            </div>
            {detail && compareB && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                {[["A", detail], ["B", compareB]].map(([label, s]: any) => (
                  <div key={label} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 8 }}>{label}: {s.name}</div>
                    <div style={{ fontSize: 11, color: C.muted2, marginBottom: 4 }}>Forecast Finish: {s.result?.scenarioForecastFinish || "—"}</div>
                    <div style={{ fontSize: 11, color: C.muted2, marginBottom: 4 }}>Recovery: {s.result?.recoveryDays != null ? `${s.result.recoveryDays}d` : "—"}</div>
                    <div style={{ fontSize: 11, color: C.muted2, marginBottom: 4 }}>Baseline Recovery: {s.result?.projectBaselineRecovery?.varianceRecoveredDays != null ? `${s.result.projectBaselineRecovery.varianceRecoveredDays}d` : "Unavailable"}</div>
                    <div style={{ fontSize: 11, color: C.muted2, marginBottom: 4 }}>Improved / Worsened: {s.result?.sideEffects?.improvedCount || 0} / {s.result?.sideEffects?.worsenedCount || 0}</div>
                    <div style={{ fontSize: 11, color: C.muted2 }}>Actions: {s.assumptions?.length ?? s.actionCount ?? 0}</div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: 14, fontSize: 11, color: C.muted, fontStyle: "italic" }}>
              Both options are calculated independently — ScheduleIQ never recommends a "winner"; the scheduler decides.
            </div>
          </div>
        ) : (
          <>
            {!detail && <div style={{ color: C.muted, textAlign: "center", padding: 60 }}>Create or select a scenario to begin.</div>}
            {detail && (
              <>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div style={{ fontSize: 11, color: C.muted }}>
                    Source: {detail.sourceVersionLabel} (Data Date {detail.sourceDataDate || "—"})
                    {detail.riskKey && <span> · Linked to risk <strong>{detail.riskKey}</strong></span>}
                  </div>
                  <Select value={detail.status} onChange={setStatus} options={SCENARIO_STATUS_OPTIONS.map(s => ({ value: s, label: s.replace(/_/g, " ") }))} />
                </div>

                {result && (
                  <div style={{ display: "flex", gap: 14, marginBottom: 16, flexWrap: "wrap" }}>
                    <MetricCard label="Imported P6 Current Forecast" value={result.currentForecastFinish || "—"} />
                    <MetricCard label="ScheduleIQ Recovery Scenario" value={result.scenarioForecastFinish || "—"} />
                    <MetricCard label="Estimated Recovery" value={result.recoveryDays != null ? `${result.recoveryDays}d` : "—"} color={(result.recoveryDays || 0) > 0 ? C.green : undefined} />
                    {result.projectBaselineRecovery && (
                      <MetricCard label="Baseline Recovery %" value={result.projectBaselineRecovery.recoveryPct != null ? `${result.projectBaselineRecovery.recoveryPct}%` : "Unavailable"}
                        sub={result.projectBaselineRecovery.varianceRecoveredDays != null ? `${result.projectBaselineRecovery.varianceRecoveredDays}d of ${result.projectBaselineRecovery.currentBaselineVarianceDays}d` : undefined} />
                    )}
                  </div>
                )}

                {result?.calendarConfidence && !result.calendarConfidence.available && (
                  <div style={{ fontSize: 11, color: C.amber, background: `${C.amber}0a`, border: `1px solid ${C.amber}40`, borderRadius: 8, padding: "8px 12px", marginBottom: 12 }}>
                    ⚠ Limited calendar confidence: {result.calendarConfidence.activitiesWithCalendar} of {result.calendarConfidence.totalActivities} activities resolved a working calendar — dates fall back to calendar-day math where unresolved.
                  </div>
                )}

                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px", marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 10 }}>Add Assumption (ScheduleIQ Scenario Calculation — not imported P6 CPM)</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
                    <Select value={actionType} onChange={setActionType} options={ACTION_TYPES} />
                    {["activityId", "predecessorId", "successorId"].map(f => (
                      <input key={f} value={form[f] || ""} onChange={e => setForm(p => ({ ...p, [f]: e.target.value }))} placeholder={f}
                        style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", fontSize: 12, fontFamily: "inherit", width: 110 }} />
                    ))}
                    <input value={form.value || ""} onChange={e => setForm(p => ({ ...p, value: e.target.value }))} placeholder="value / days"
                      style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", fontSize: 12, fontFamily: "inherit", width: 90 }} />
                    <input value={form.relType || ""} onChange={e => setForm(p => ({ ...p, relType: e.target.value }))} placeholder="FS/SS/FF/SF"
                      style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", fontSize: 12, fontFamily: "inherit", width: 80 }} />
                    <input value={form.note || ""} onChange={e => setForm(p => ({ ...p, note: e.target.value }))} placeholder="note (advisory actions)"
                      style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", fontSize: 12, fontFamily: "inherit", width: 160 }} />
                    <button onClick={addAction} disabled={busy}
                      style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "6px 16px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>Add</button>
                  </div>
                  <button onClick={() => recalculate([])} disabled={busy}
                    style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 7, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontFamily: "inherit" }}>
                    ↺ Reset Scenario
                  </button>
                </div>

                {result && (
                  <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
                    <div style={{ flex: "1 1 260px" }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Applied (Calculated) Actions</div>
                      {result.appliedActions.length === 0 && <div style={{ fontSize: 12, color: C.muted }}>None yet.</div>}
                      {result.appliedActions.map((a: any, i: number) => (
                        <div key={i} style={{ fontSize: 11, color: C.text, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 9px", marginBottom: 4 }}>
                          {a.type} {a.activityId || `${a.predecessorId}→${a.successorId}`}
                        </div>
                      ))}
                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", margin: "12px 0 6px" }}>Advisory Actions (not included in day count)</div>
                      {result.advisoryActions.length === 0 && <div style={{ fontSize: 12, color: C.muted }}>None.</div>}
                      {result.advisoryActions.map((a: any, i: number) => (
                        <div key={i} style={{ fontSize: 11, color: C.amber, background: `${C.amber}10`, border: `1px solid ${C.amber}40`, borderRadius: 6, padding: "5px 9px", marginBottom: 4 }}>
                          {a.type}{a.note ? `: ${a.note}` : ""}
                        </div>
                      ))}
                    </div>
                    <div style={{ flex: "1 1 260px" }}>
                      <SideEffectsPanel effects={result.sideEffects} />
                      {result.warnings.length > 0 && (
                        <>
                          <div style={{ fontSize: 11, fontWeight: 700, color: C.red, textTransform: "uppercase", margin: "12px 0 6px" }}>Warnings</div>
                          {result.warnings.map((w: string, i: number) => <div key={i} style={{ fontSize: 11, color: C.red }}>{w}</div>)}
                        </>
                      )}
                    </div>
                  </div>
                )}

                {result?.milestoneImpact && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Milestone Impact</div>
                    <MilestoneImpactTable impact={result.milestoneImpact} />
                  </div>
                )}

                {detail.status === "ACCEPTED" && (
                  <div style={{ marginBottom: 16 }}>
                    <RecoveryTrackingPanel ctx={ctx} scenarioId={detail.id} />
                  </div>
                )}

                {result && (
                  <div style={{ fontSize: 11, color: C.muted, fontStyle: "italic", borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
                    {result.disclaimer}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ─── MITIGATION ACTIONS ─────────────────────────────────────────────────────

function MitigationActionsTab({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  const [actions, setActions] = useState<any[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = () => {
    if (!ctx.projectId) return;
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    sfetch(`${API}/api/projects/${ctx.projectId}/mitigation-actions/?${params.toString()}`).then(r => r.json()).then(d => setActions(d.actions || []));
  };
  useEffect(load, [ctx.projectId, statusFilter]);

  const create = () => {
    if (!form.description?.trim()) return;
    setBusy(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/mitigation-actions/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: form.description, owner: form.owner, dueDate: form.dueDate || undefined, riskKey: form.riskKey || undefined }),
    }).then(r => r.json()).then(() => { setForm({}); load(); }).finally(() => setBusy(false));
  };

  const update = (id: string, patch: any) => {
    sfetch(`${API}/api/projects/${ctx.projectId}/mitigation-actions/${id}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    }).then(load);
  };

  const remove = (id: string) => {
    sfetch(`${API}/api/projects/${ctx.projectId}/mitigation-actions/${id}/`, { method: "DELETE" }).then(load);
  };

  if (!ctx.projectId) return <div style={{ color: C.muted, padding: 20 }}>Select a project above.</div>;

  return (
    <div>
      <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px", marginBottom: 16 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 10 }}>New Mitigation Action</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <input value={form.description || ""} onChange={e => setForm(p => ({ ...p, description: e.target.value }))} placeholder="Description (required)"
            style={{ flex: "1 1 220px", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <input value={form.owner || ""} onChange={e => setForm(p => ({ ...p, owner: e.target.value }))} placeholder="Owner"
            style={{ width: 140, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <input type="date" value={form.dueDate || ""} onChange={e => setForm(p => ({ ...p, dueDate: e.target.value }))}
            style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <input value={form.riskKey || ""} onChange={e => setForm(p => ({ ...p, riskKey: e.target.value }))} placeholder="Linked risk key (optional)"
            style={{ width: 160, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <button onClick={create} disabled={busy || !form.description?.trim()}
            style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "6px 16px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>Add</button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <Select value={statusFilter} onChange={setStatusFilter} placeholder="All statuses" options={MITIGATION_STATUS_OPTIONS.map(s => ({ value: s, label: s.replace(/_/g, " ") }))} />
      </div>

      <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
          <thead><tr style={{ background: C.card2 }}>
            {["Description", "Risk", "Owner", "Due Date", "Status", "Notes", ""].map(h => (
              <th key={h} style={{ padding: "7px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {actions.map(a => (
              <tr key={a.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                <td style={{ padding: "6px 12px" }}>{a.description}</td>
                <td style={{ padding: "6px 12px", fontFamily: "monospace" }}>{a.riskKey || "—"}</td>
                <td style={{ padding: "6px 12px" }}>{a.owner || "—"}</td>
                <td style={{ padding: "6px 12px" }}>{a.dueDate || "—"}</td>
                <td style={{ padding: "6px 12px" }}>
                  <select value={a.status} onChange={e => update(a.id, { status: e.target.value })}
                    style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 6px", fontSize: 11, fontFamily: "inherit" }}>
                    {MITIGATION_STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
                  </select>
                </td>
                <td style={{ padding: "6px 12px", color: C.muted2, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.notes || "—"}</td>
                <td style={{ padding: "6px 12px" }}>
                  <button onClick={() => remove(a.id)} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer" }}>✕</button>
                </td>
              </tr>
            ))}
            {actions.length === 0 && <tr><td colSpan={7} style={{ padding: 20, textAlign: "center", color: C.muted }}>No mitigation actions yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── RECOVERY OVERVIEW ──────────────────────────────────────────────────────

function RecoveryOverviewTab({ ctx, onOpenRegister }: { ctx: ReturnType<typeof useProjectsAndVersions>; onOpenRegister: () => void }) {
  const { data, loading, error } = useRiskRegister(ctx);
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [actions, setActions] = useState<any[]>([]);

  useEffect(() => {
    if (!ctx.projectId) return;
    sfetch(`${API}/api/projects/${ctx.projectId}/recovery-scenarios/`).then(r => r.json()).then(d => setScenarios(d.scenarios || []));
    sfetch(`${API}/api/projects/${ctx.projectId}/mitigation-actions/`).then(r => r.json()).then(d => setActions(d.actions || []));
  }, [ctx.projectId]);

  if (!ctx.projectId || !ctx.versionId) return <div style={{ color: C.muted, padding: 20 }}>Select a project and version above.</div>;
  if (loading) return <div style={{ color: C.accent, padding: 20 }}>Loading overview…</div>;
  if (error) return <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>;
  if (data?.available === false) return <div style={{ color: C.muted, padding: 20, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>{data.reason}</div>;

  const summary = data?.summary;
  const accepted = scenarios.filter(s => s.status === "ACCEPTED");
  const openActions = actions.filter(a => a.status !== "COMPLETE" && a.status !== "CANCELLED");
  const overdueActions = openActions.filter(a => a.dueDate && data?.currentDataDate && a.dueDate < data.currentDataDate);
  const acceptedMilestoneImpacts = accepted.flatMap(s => (s.projectBaselineRecovery ? [{ scenario: s.name, ...s.projectBaselineRecovery }] : []));

  return (
    <div>
      <div style={{ fontSize: 12, color: C.muted, marginBottom: 14 }}>
        Data Date {data?.currentDataDate || "—"} · {data?.previousVersionLabel} → {data?.currentVersionLabel}
        {data?.baselineVersionLabel && <> · Baseline: {data.baselineVersionLabel}</>}
      </div>
      {summary && (
        <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
          <MetricCard label="Total Risks" value={summary.totalRisks} color={C.text} />
          <MetricCard label="Critical" value={summary.bySeverity.CRITICAL} color={SEV_COLOR.CRITICAL} />
          <MetricCard label="High" value={summary.bySeverity.HIGH} color={SEV_COLOR.HIGH} />
          <MetricCard label="Driving" value={summary.drivingRiskCount} color={C.purple} />
          <MetricCard label="Negative Float" value={summary.negativeFloatCount} color={C.red} />
          <MetricCard label="Milestone-Exposed" value={summary.milestoneExposureCount} color={C.orange} />
          <MetricCard label="Accepted Scenarios" value={accepted.length} color={C.green} />
          <MetricCard label="Open Mitigation Actions" value={openActions.length} color={overdueActions.length > 0 ? C.red : C.text} sub={overdueActions.length > 0 ? `${overdueActions.length} overdue` : undefined} />
        </div>
      )}
      <button onClick={onOpenRegister} style={{ marginBottom: 18, background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "8px 16px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
        Open Risk Register →
      </button>

      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 8 }}>Milestone Recovery — Accepted Scenarios</div>
      {acceptedMilestoneImpacts.length === 0 && <div style={{ fontSize: 12, color: C.muted, marginBottom: 18 }}>No accepted scenarios with project baseline recovery data yet.</div>}
      {acceptedMilestoneImpacts.length > 0 && (
        <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card, marginBottom: 18 }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
            <thead><tr style={{ background: C.card2 }}>
              {["Scenario", "Baseline Finish", "Current Variance", "Scenario Variance", "Recovered", "Recovery %"].map(h => (
                <th key={h} style={{ padding: "7px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {acceptedMilestoneImpacts.map((m: any, i: number) => (
                <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: "6px 12px" }}>{m.scenario}</td>
                  <td style={{ padding: "6px 12px" }}>{m.baselineFinish || "—"}</td>
                  <td style={{ padding: "6px 12px" }}>{m.currentBaselineVarianceDays != null ? `${m.currentBaselineVarianceDays}d` : "—"}</td>
                  <td style={{ padding: "6px 12px" }}>{m.scenarioBaselineVarianceDays != null ? `${m.scenarioBaselineVarianceDays}d` : "—"}</td>
                  <td style={{ padding: "6px 12px", color: (m.varianceRecoveredDays || 0) > 0 ? C.green : C.muted2 }}>{m.varianceRecoveredDays != null ? `${m.varianceRecoveredDays}d` : "—"}</td>
                  <td style={{ padding: "6px 12px" }}>{m.recoveryPct != null ? `${m.recoveryPct}%` : "Unavailable"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── ROOT ───────────────────────────────────────────────────────────────────

export default function RiskIntelligence({ initialProjectId, initialVersionId, initialSubTab, initialRiskKey }: {
  initialProjectId?: string; initialVersionId?: string; initialSubTab?: string; initialRiskKey?: string;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  // A riskKey-carrying quick-link ("Analyze Recovery" on a specific
  // activity) is more specific than a bare subtab request — it always wins
  // the initial tab so the scheduler lands directly on scenario creation.
  const [tab, setTab] = useState<"overview" | "risk" | "milestones" | "register" | "scenarios" | "mitigation">(
    initialRiskKey ? "scenarios" : (initialSubTab as any) || "risk"
  );
  const [pendingRiskKey, setPendingRiskKey] = useState<string | undefined>(initialRiskKey);

  const TABS = [
    { id: "overview", label: "Recovery Overview" }, { id: "risk", label: "Risk Heat Map" },
    { id: "milestones", label: "Milestones" }, { id: "register", label: "Risk Register" },
    { id: "scenarios", label: "Recovery Scenarios" }, { id: "mitigation", label: "Mitigation Actions" },
  ];

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Risk & Recovery</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        Deterministic risk scoring, driving-chain analysis, and recovery scenario planning for projects imported through the Import Center. Recovery scenarios are ScheduleIQ Scenario Calculations — they never modify the official imported P6 schedule.
      </div>

      {/* Concept banner — RISK is a distinct pillar from STATUS's internal
          "Off-Track Factor" (visible on the Status tab) and from QUALITY
          (Logic Check). Same three-pillar explainer shown there. */}
      <div style={{ background: `${C.red}0a`, border: `1px solid ${C.red}30`, borderRadius: 10, padding: "10px 16px", marginBottom: 14, fontSize: 12, color: C.muted, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 14 }}>🌡️</span>
        <span><strong style={{ color: C.red }}>RISK</strong> answers "where is future schedule exposure concentrated?" — scored per Area/WBS/Contractor/Discipline/System from negative float, criticality, and deterioration. This is a different calculation from the "Off-Track Factor" shown on the <strong>Status</strong> tab, which measures current classification confidence, not exposure concentration.</span>
      </div>

      <ProjectVersionBar ctx={ctx} />

      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 16, flexWrap: "wrap" }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id as any)}
            style={{ background: "transparent", border: "none", borderBottom: `2px solid ${tab === t.id ? C.accent : "transparent"}`, color: tab === t.id ? C.accent : C.muted2, padding: "8px 14px", cursor: "pointer", fontSize: 13, fontFamily: "inherit", fontWeight: tab === t.id ? 800 : 600 }}>
            {t.label}
          </button>
        ))}
      </div>

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && tab === "overview" && <RecoveryOverviewTab ctx={ctx} onOpenRegister={() => setTab("register")} />}
      {ctx.projectId && tab === "risk" && <RiskHeatMap ctx={ctx} />}
      {ctx.projectId && tab === "milestones" && <MilestoneDashboard ctx={ctx} />}
      {ctx.projectId && tab === "register" && <RiskRegisterTab ctx={ctx} onAnalyzeRecovery={(riskKey) => { setPendingRiskKey(riskKey); setTab("scenarios"); }} />}
      {ctx.projectId && tab === "scenarios" && <RecoveryScenariosTab ctx={ctx} pendingRiskKey={pendingRiskKey} onConsumePendingRiskKey={() => setPendingRiskKey(undefined)} />}
      {ctx.projectId && tab === "mitigation" && <MitigationActionsTab ctx={ctx} />}
    </div>
  );
}
