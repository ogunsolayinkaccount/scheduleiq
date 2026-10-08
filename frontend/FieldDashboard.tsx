import { Fragment, useEffect, useRef, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { formatDataDate, versionSelectLabel, dedupeVersionsById } from "./dateFormat";
import { resolveCompletionStatus, isRegisteredCompletion, completionPanelTitle, buildVarianceIntelligenceUrl } from "./fieldDashboardVarianceFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

const STATUS_COLOR: Record<string, string> = { GREEN: C.green, YELLOW: C.amber, RED: C.red, UNVERIFIED: C.muted2 };
const CATEGORY_OPTIONS = [
  { value: "CONTRACTUAL_COMPLETION", label: "Contractual Completion" },
  { value: "CONTRACTUAL_INTERIM", label: "Contractual Interim Milestone" },
];

// ─── Schedule Variance & Completion (reuses /variance-intelligence/ verbatim —
// see VarianceIntelligence.tsx for the authoritative engine; nothing below
// recalculates a date, Total Float, variance, or exposure tier) ───────────
const ASSESSMENT_COLOR: Record<string, string> = {
  HIGH_EXPOSURE: C.red, WARNING: C.orange, MONITOR: C.amber, FAVORABLE: C.green, UNAVAILABLE: C.muted2,
};
const ASSESSMENT_LABEL: Record<string, string> = {
  HIGH_EXPOSURE: "High Exposure", WARNING: "Warning", MONITOR: "Monitor", FAVORABLE: "Favorable", UNAVAILABLE: "Unavailable",
};
const DIRECTION_COLOR: Record<string, string> = {
  FAVORABLE: C.green, UNFAVORABLE: C.red, NO_MOVEMENT: C.muted2, UNAVAILABLE: C.muted2,
};
function AssessmentBadge({ assessment }: { assessment: string }) {
  const color = ASSESSMENT_COLOR[assessment] || C.muted2;
  return <span style={{ background: `${color}18`, color, borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700, whiteSpace: "nowrap" }}>{ASSESSMENT_LABEL[assessment] || assessment}</span>;
}
function fmtVarDays(days: number | null | undefined): string {
  if (days == null) return "Unavailable";
  const sign = days > 0 ? "+" : "";
  return `${sign}${days}d`;
}

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

function useFieldDashboardSummary(ctx: ReturnType<typeof useProjectsAndVersions>, warningThresholdDays: number) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/field-dashboard-summary/?currentVersion=${ctx.versionId}&warningThresholdDays=${warningThresholdDays}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(load, [ctx.projectId, ctx.versionId, warningThresholdDays]);

  return { data, loading, error, reload: load };
}

// Fetches the SAME /variance-intelligence/ endpoint Variance Intelligence
// itself calls, with no basis param — the backend resolves the identical
// default (approvedBaseline if designated, else embeddedBaseline) so both
// screens always agree without the Field Dashboard choosing a basis itself.
function useVarianceIntelligenceSummary(ctx: ReturnType<typeof useProjectsAndVersions>) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ctx.projectId || !ctx.versionId) { setData(null); return; }
    setLoading(true); setError(null);
    sfetch(`${API}${buildVarianceIntelligenceUrl(ctx.projectId, ctx.versionId)}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  }, [ctx.projectId, ctx.versionId]);

  return { data, loading, error };
}

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

function KpiCard({ label, value, sub, color, onClick }: { label: string; value: React.ReactNode; sub?: string; color?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick} style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 9, padding: "10px 12px", minWidth: 0, cursor: onClick ? "pointer" : "default" }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function fmtMoney(v: { value: number | null; source: string } | null | undefined): string {
  if (!v || v.value == null) return "Unavailable";
  const n = v.value;
  return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `$${(n / 1000).toFixed(0)}K` : `$${n.toFixed(0)}`;
}
function fmtHours(n: number | null | undefined): string {
  if (n == null) return "Unavailable";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K h` : `${Math.round(n)} h`;
}
function fmtRatio(n: number | null | undefined): string {
  return n == null ? "Unavailable" : n.toFixed(2);
}

// ─── Contractual Milestone Tracker ─────────────────────────────────────────

function AddMilestoneForm({ projectId, onSaved }: { projectId: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ activityId: "", description: "", category: "CONTRACTUAL_COMPLETION", contractRequiredDate: "", sourceDocumentReference: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = () => {
    if (!form.activityId.trim() || !form.contractRequiredDate) { setErr("Activity ID and Contractual Finish Date are required."); return; }
    setSaving(true); setErr(null);
    fetch(`${API}/api/projects/${projectId}/contractual-milestones/`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form),
    })
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(() => { setForm({ activityId: "", description: "", category: "CONTRACTUAL_COMPLETION", contractRequiredDate: "", sourceDocumentReference: "" }); setOpen(false); onSaved(); })
      .catch((e: any) => setErr(e.message || String(e)))
      .finally(() => setSaving(false));
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
        + Add Contractual Milestone
      </button>
    );
  }

  const fieldStyle = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 9px", fontSize: 12, fontFamily: "inherit", color: C.text };
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 8, marginBottom: 8 }}>
        <input placeholder="P6 Activity ID (code)" value={form.activityId} onChange={e => setForm(f => ({ ...f, activityId: e.target.value }))} style={fieldStyle} />
        <input placeholder="Milestone description" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} style={fieldStyle} />
        <Select value={form.category} onChange={(v: string) => setForm(f => ({ ...f, category: v }))} options={CATEGORY_OPTIONS} />
        <input type="date" value={form.contractRequiredDate} onChange={e => setForm(f => ({ ...f, contractRequiredDate: e.target.value }))} style={fieldStyle} />
        <input placeholder="Source document reference" value={form.sourceDocumentReference} onChange={e => setForm(f => ({ ...f, sourceDocumentReference: e.target.value }))} style={fieldStyle} />
      </div>
      {err && <div style={{ color: C.red, fontSize: 11, marginBottom: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={save} disabled={saving} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>{saving ? "Saving…" : "Save"}</button>
        <button onClick={() => setOpen(false)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontFamily: "inherit" }}>Cancel</button>
      </div>
    </div>
  );
}

function ContractualMilestoneTracker({ projectId, panel, warningThresholdDays, onThresholdChange, onChanged }: {
  projectId: string; panel: any; warningThresholdDays: number; onThresholdChange: (n: number) => void; onChanged: () => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDate, setEditDate] = useState("");
  const [editReason, setEditReason] = useState("");
  const [editSourceDoc, setEditSourceDoc] = useState("");

  const saveRevision = (milestoneId: string) => {
    fetch(`${API}/api/projects/${projectId}/contractual-milestones/${milestoneId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contractRequiredDate: editDate, revisionReason: editReason, revisionSourceDocumentReference: editSourceDoc }),
    }).then(() => { setEditingId(null); onChanged(); });
  };
  const remove = (milestoneId: string) => {
    fetch(`${API}/api/projects/${projectId}/contractual-milestones/${milestoneId}/`, { method: "DELETE" }).then(() => onChanged());
  };

  return (
    <Panel title="Contractual Milestone Tracker"
      action={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: C.muted }}>Warning threshold (days of float):</span>
          <input type="number" min={0} value={warningThresholdDays} onChange={e => onThresholdChange(Math.max(0, parseInt(e.target.value) || 0))}
            style={{ width: 50, background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 6px", fontSize: 12, fontFamily: "inherit" }} />
        </div>
      }>
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <>
          {!panel.configured ? (
            <div style={{ background: `${C.gold}10`, border: `1px solid ${C.gold}40`, borderRadius: 8, padding: "14px 16px", marginBottom: 12, color: C.text, fontSize: 13 }}>
              <strong>Contractual Dates Not Configured.</strong> No contractual milestones have been registered for this project yet. Add one below — the contractual date must come from an authorized contract or milestone register, never the P6 baseline.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginBottom: 12 }}>
              <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
                <th style={{ padding: "4px 6px" }}>Milestone</th>
                <th style={{ padding: "4px 6px" }}>Contract</th>
                <th style={{ padding: "4px 6px" }}>P6 Forecast</th>
                <th style={{ padding: "4px 6px", textAlign: "right" }}>Variance</th>
                <th style={{ padding: "4px 6px", textAlign: "right" }}>Driving Float</th>
                <th style={{ padding: "4px 6px" }}>Status</th>
                <th style={{ padding: "4px 6px" }}>Explanation</th>
                <th style={{ padding: "4px 6px" }}></th>
              </tr></thead>
              <tbody>
                {panel.milestones.map((m: any) => (
                  <Fragment key={m.milestoneId}>
                    <tr style={{ borderTop: `1px solid ${C.border}` }}>
                      <td style={{ padding: "6px", color: C.text, fontWeight: 600 }}>
                        {m.milestoneName}
                        {m.sourceDocumentReference && <div style={{ fontSize: 10, color: C.muted2 }}>{m.sourceDocumentReference}</div>}
                      </td>
                      <td style={{ padding: "6px", color: C.text }}>{formatDataDate(m.contractRequiredDate)}</td>
                      <td style={{ padding: "6px", color: C.text }}>{formatDataDate(m.p6ForecastFinish)}</td>
                      <td style={{ padding: "6px", textAlign: "right", color: m.varianceCalendarDays > 0 ? C.red : C.green }}>
                        {m.varianceCalendarDays == null ? "Unavailable" : `${m.varianceCalendarDays > 0 ? "+" : ""}${m.varianceCalendarDays}d cal.${m.varianceWorkingDays != null ? ` / ${m.varianceWorkingDays > 0 ? "+" : ""}${m.varianceWorkingDays}d wd` : ""}`}
                      </td>
                      <td style={{ padding: "6px", textAlign: "right", color: m.currentTotalFloat != null && m.currentTotalFloat <= warningThresholdDays ? C.amber : C.text }}>
                        {m.currentTotalFloat == null ? "Unavailable" : `${m.currentTotalFloat}d`}
                      </td>
                      <td style={{ padding: "6px" }}>
                        <span style={{ background: `${STATUS_COLOR[m.status]}18`, color: STATUS_COLOR[m.status], borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>{m.status}</span>
                      </td>
                      <td style={{ padding: "6px", color: C.muted, maxWidth: 280 }}>{m.explanation}</td>
                      <td style={{ padding: "6px", whiteSpace: "nowrap" }}>
                        <button onClick={() => { setEditingId(m.milestoneId); setEditDate(m.contractRequiredDate || ""); setEditReason(""); setEditSourceDoc(""); }}
                          style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontSize: 11, fontWeight: 700, marginRight: 8 }}>Revise</button>
                        <button onClick={() => remove(m.milestoneId)} style={{ background: "transparent", border: "none", color: C.muted2, cursor: "pointer", fontSize: 11 }}>Remove</button>
                      </td>
                    </tr>
                    {editingId === m.milestoneId && (
                      <tr>
                        <td colSpan={8} style={{ padding: "8px 6px", background: C.panel }}>
                          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                            <span style={{ fontSize: 11, color: C.muted }}>New contractual date:</span>
                            <input type="date" value={editDate} onChange={e => setEditDate(e.target.value)} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 12 }} />
                            <input placeholder="Reason (e.g. owner-approved extension)" value={editReason} onChange={e => setEditReason(e.target.value)} style={{ flex: "1 1 200px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 12 }} />
                            <input placeholder="Source document reference" value={editSourceDoc} onChange={e => setEditSourceDoc(e.target.value)} style={{ flex: "1 1 200px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 12 }} />
                            <button onClick={() => saveRevision(m.milestoneId)} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>Save Revision</button>
                            <button onClick={() => setEditingId(null)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11 }}>Cancel</button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
          <AddMilestoneForm projectId={projectId} onSaved={onChanged} />
        </>
      )}
    </Panel>
  );
}

// ─── other panels ───────────────────────────────────────────────────────────

function SpendVsBudgetPanel({ financials, spendTrend }: { financials: any; spendTrend: any }) {
  return (
    <Panel title="Spend vs Budget">
      {!spendTrend?.available ? <Unavailable reason="No persisted versions with a Data Date to trend against." /> : (
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={spendTrend.points} margin={{ left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="versionLabel" tick={{ fill: C.muted, fontSize: 10 }} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={(v: number) => v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(0)}M` : `$${(v / 1000).toFixed(0)}K`} />
            <Tooltip />
            <Legend iconSize={9} wrapperStyle={{ fontSize: 10 }} />
            <Area type="monotone" dataKey="bac" name="Budget" stroke={C.accent} fill={`${C.accent}18`} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="ac" name="Spend" stroke={C.red} strokeWidth={2} dot={{ r: 3 }} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

function EarnedVsSpendPanel({ spendTrend }: { spendTrend: any }) {
  return (
    <Panel title="Earned vs Spend">
      {!spendTrend?.available ? <Unavailable reason="No persisted versions with a Data Date to trend against." /> : (
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={spendTrend.points} margin={{ left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="versionLabel" tick={{ fill: C.muted, fontSize: 10 }} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={(v: number) => v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(0)}M` : `$${(v / 1000).toFixed(0)}K`} />
            <Tooltip />
            <Legend iconSize={9} wrapperStyle={{ fontSize: 10 }} />
            <Area type="monotone" dataKey="ev" name="Earned" stroke={C.green} fill={`${C.green}18`} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="ac" name="Spend" stroke={C.red} strokeWidth={2} dot={{ r: 3 }} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

function ScheduleHealthPanel({ panel }: { panel: any }) {
  return (
    <Panel title="Schedule Health">
      {!panel?.available ? <Unavailable reason={panel?.reason} /> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8 }}>
          <KpiCard label="Critical Activities" value={panel.criticalCount} color={C.red} />
          <KpiCard label="Driving (Longest Path)" value={panel.drivingCount} color={C.orange} />
          <KpiCard label="Negative Float" value={panel.negativeFloatCount} color={panel.negativeFloatCount > 0 ? C.red : C.green} />
          <KpiCard label="SPI (Cost Basis)" value={fmtRatio(panel.spi)} color={panel.spi == null ? C.muted2 : panel.spi >= 1 ? C.green : panel.spi >= 0.9 ? C.amber : C.red} />
        </div>
      )}
    </Panel>
  );
}

function ManpowerPanel({ panel, curve }: { panel: any; curve: any }) {
  return (
    <Panel title="Manpower">
      {!panel?.available ? (
        <div style={{ color: C.gold, fontSize: 13, fontWeight: 700, padding: "6px 4px", marginBottom: 10 }}>{panel?.reason}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8, marginBottom: 12 }}>
          <KpiCard label="Budgeted Hours" value={fmtHours(panel.budgetedHours)} />
          <KpiCard label="Earned Hours" value={fmtHours(panel.earnedHours)} color={C.green} />
          <KpiCard label="Actual Hours" value={fmtHours(panel.actualHours)} color={C.amber} />
          <KpiCard label="Remaining Hours" value={fmtHours(panel.remainingHours)} />
          <KpiCard label="SPI" value={fmtRatio(panel.spi)} />
          <KpiCard label="CPI" value={fmtRatio(panel.cpi)} />
        </div>
      )}
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>Planned vs Actual Manpower Curve</div>
      {!curve?.available ? <Unavailable reason={curve?.reason || "Display a real curve only when time-phased workforce data is available."} /> : (
        <ResponsiveContainer width="100%" height={180}>
          <ComposedChart data={curve.periods} margin={{ left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="period" tick={{ fill: C.muted, fontSize: 10 }} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} unit="%" />
            <Tooltip />
            <Legend iconSize={9} wrapperStyle={{ fontSize: 10 }} />
            <Area type="monotone" dataKey="baselinePlanned" name="Planned" stroke={C.muted2} fill="none" strokeWidth={2} strokeDasharray="5 3" dot={false} />
            <Line type="monotone" dataKey="currentUpdateActual" name="Actual" stroke={C.green} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

function IntegrationSourcesPanel({ sources }: { sources: any }) {
  if (!sources) return null;
  const rows = [
    { key: "primaveraP6", label: "Primavera P6" },
    { key: "shelby", label: "Shelby" },
    { key: "fieldData", label: "Field Data" },
  ];
  return (
    <Panel title="Integration Sources">
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {rows.map(r => {
          const s = sources[r.key];
          const connected = s?.status === "CONNECTED";
          return (
            <div key={r.key} style={{ display: "flex", alignItems: "center", gap: 8, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px" }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: connected ? C.green : C.muted2, display: "inline-block" }} />
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{r.label}</div>
                <div style={{ fontSize: 10, color: C.muted2 }}>{connected ? "Connected" : "Not Connected"}</div>
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

const ISSUE_SEV_COLOR: Record<string, string> = { CRITICAL: C.red, HIGH: C.orange, MEDIUM: C.amber, LOW: C.muted2 };

function ProjectIssuesPanel({ issues, onOpenIssueRegister }: { issues: any; onOpenIssueRegister?: () => void }) {
  return (
    <Panel title="Project Issues" action={onOpenIssueRegister && (
      <button onClick={onOpenIssueRegister} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 6, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>
        View Project Issues
      </button>
    )}>
      {!issues?.available ? <Unavailable reason={issues?.reason} /> : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(90px,1fr))", gap: 8, marginBottom: 12 }}>
            <KpiCard label="Open" value={issues.openCount} color={issues.openCount > 0 ? C.red : C.green} />
            <KpiCard label="Critical" value={issues.criticalCount} color={issues.criticalCount > 0 ? C.red : C.green} />
            <KpiCard label="High" value={issues.highCount} color={issues.highCount > 0 ? C.orange : C.green} />
            <KpiCard label="Overdue" value={issues.overdueCount} color={issues.overdueCount > 0 ? C.amber : C.green} />
          </div>
          {!issues.topIssues?.length ? (
            <div style={{ color: C.muted2, fontSize: 12 }}>No active issues recorded for this project.</div>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", fontSize: 12 }}>
              {issues.topIssues.map((it: any) => (
                <li key={it.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderTop: `1px solid ${C.border}` }}>
                  <span style={{ background: `${ISSUE_SEV_COLOR[it.severity]}18`, color: ISSUE_SEV_COLOR[it.severity], borderRadius: 5, padding: "2px 7px", fontSize: 10, fontWeight: 700 }}>{it.severity}</span>
                  <span style={{ color: C.text, fontWeight: 600, flex: 1 }}>{it.title}</span>
                  {it.overdue && <span style={{ color: C.red, fontSize: 10, fontWeight: 700 }}>OVERDUE</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

function CompletionStatusBadge({ cm }: { cm: any }) {
  const status = resolveCompletionStatus(cm);
  if (status.kind === "direction") {
    return <span style={{ background: `${C.muted2}18`, color: C.muted2, borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>{status.label}</span>;
  }
  return <AssessmentBadge assessment={status.value} />;
}

function VarianceCompletionPanel({ vi, loading, error, projectId, versionId, onOpenVarianceIntelligence, onOpenFloatAnalysis }: {
  vi: any; loading: boolean; error: string | null; projectId: string; versionId: string;
  onOpenVarianceIntelligence?: (projectId: string, versionId?: string, assessmentFilter?: string) => void;
  onOpenFloatAnalysis?: (projectId: string, versionId: string, filter?: "negative" | "zero" | "nearCritical") => void;
}) {
  const action = onOpenVarianceIntelligence && (
    <button onClick={() => onOpenVarianceIntelligence(projectId, versionId)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 6, padding: "5px 11px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>
      View Full Variance Intelligence →
    </button>
  );

  if (loading && !vi) return <Panel title="Schedule Variance & Completion" action={action}><div style={{ color: C.accent, fontSize: 12, padding: "6px 4px" }}>Loading…</div></Panel>;
  if (error) return <Panel title="Schedule Variance & Completion" action={action}><Unavailable reason={error} /></Panel>;
  if (!vi || !vi.available) return <Panel title="Schedule Variance & Completion" action={action}><Unavailable reason={vi?.reason} /></Panel>;

  const s = vi.summary;
  const cm = vi.completionMilestone;
  const completionIsRegistered = isRegisteredCompletion(cm?.selectionBasis);

  return (
    <Panel title="Schedule Variance & Completion" action={action}>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 10 }}>
        Comparison basis: <strong style={{ color: C.text }}>{vi.basisLabel}</strong> — sourced directly from ScheduleIQ's Variance Intelligence engine; nothing on this panel is independently recalculated.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 8, marginBottom: 8 }}>
        <KpiCard label="Current Finish" value={s?.currentProjectFinish ? formatDataDate(s.currentProjectFinish) : "Unavailable"}
          sub={completionIsRegistered ? "Registered completion milestone" : "Latest milestone — not registered as completion"} />
        <KpiCard label="Finish Variance" value={fmtVarDays(s?.projectFinishVarianceDays)} color={s?.projectFinishVarianceDays > 0 ? C.red : s?.projectFinishVarianceDays < 0 ? C.green : undefined} />
        <KpiCard label="Total Float" value={s?.projectFinishTotalFloat ?? "Unavailable"} />
        <KpiCard label="Comparison" value={vi.basisLabel} sub={s?.comparisonProjectFinish ? `Finish: ${formatDataDate(s.comparisonProjectFinish)}` : "Unavailable"} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 8, marginBottom: 8 }}>
        <KpiCard label="Negative Float" value={s?.negativeFloatCount} color={s?.negativeFloatCount > 0 ? C.red : undefined}
          onClick={onOpenFloatAnalysis ? () => onOpenFloatAnalysis(projectId, versionId, "negative") : undefined} />
        <KpiCard label="Zero Float" value={s?.zeroFloatCount}
          onClick={onOpenFloatAnalysis ? () => onOpenFloatAnalysis(projectId, versionId, "zero") : undefined} />
        <KpiCard label="Near-Critical" value={s?.nearCriticalCount} color={C.amber}
          onClick={onOpenFloatAnalysis ? () => onOpenFloatAnalysis(projectId, versionId, "nearCritical") : undefined} />
        <KpiCard label="Driving" value={s?.drivingCount} color={C.purple} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 8, marginBottom: 14 }}>
        <KpiCard label="Behind Comparison" value={s?.milestonesBehindComparison} color={s?.milestonesBehindComparison > 0 ? C.red : undefined} />
        <KpiCard label="Ahead of Comparison" value={s?.milestonesAheadOfComparison} color={C.green} />
        <KpiCard label="High Exposure" value={s?.highExposureCount} color={C.red}
          onClick={onOpenVarianceIntelligence ? () => onOpenVarianceIntelligence(projectId, versionId, "HIGH_EXPOSURE") : undefined} />
        <KpiCard label="Warning" value={s?.warningCount} color={C.orange}
          onClick={onOpenVarianceIntelligence ? () => onOpenVarianceIntelligence(projectId, versionId, "WARNING") : undefined} />
      </div>

      <div style={{ fontSize: 12, fontWeight: 800, color: C.text, marginBottom: 2 }}>
        {completionPanelTitle(cm?.selectionBasis)}
      </div>
      {!completionIsRegistered && cm && (
        <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>
          No milestone is registered in ScheduleIQ as the Contractual Completion milestone for this project — this is the latest-finishing milestone activity, shown for reference only. It is NOT confirmed as the project's completion milestone.
        </div>
      )}
      {!cm ? (
        <div style={{ color: C.muted2, fontSize: 12, marginTop: 6 }}>{vi.completionInterpretation}</div>
      ) : (
        <>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
              <th style={{ padding: "4px 6px" }}>Milestone</th>
              <th style={{ padding: "4px 6px" }}>Current Finish</th>
              <th style={{ padding: "4px 6px" }}>Comparison Finish</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>Finish Variance</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>Total Float</th>
              <th style={{ padding: "4px 6px" }}>Driving</th>
              <th style={{ padding: "4px 6px" }}>Status</th>
            </tr></thead>
            <tbody>
              <tr style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={{ padding: "6px", color: C.text, fontWeight: 600 }}>{cm.activityId} — {cm.activityName}</td>
                <td style={{ padding: "6px", color: C.text }}>{cm.currentFinish ? formatDataDate(cm.currentFinish) : "Unavailable"}</td>
                <td style={{ padding: "6px", color: C.text }}>{cm.comparisonFinish ? formatDataDate(cm.comparisonFinish) : "Unavailable"}</td>
                <td style={{ padding: "6px", textAlign: "right", color: DIRECTION_COLOR[cm.direction], fontWeight: 700 }}>{fmtVarDays(cm.finishVarianceDays)}</td>
                <td style={{ padding: "6px", textAlign: "right", color: C.text }}>{cm.currentTotalFloat ?? "Unavailable"}</td>
                <td style={{ padding: "6px", color: C.text }}>{cm.driving ? "Yes" : "No"}</td>
                <td style={{ padding: "6px" }}><CompletionStatusBadge cm={cm} /></td>
              </tr>
            </tbody>
          </table>
          <div style={{ fontSize: 12, color: C.text, background: C.panel, borderRadius: 8, padding: 10, marginTop: 10 }}>{vi.completionInterpretation}</div>
        </>
      )}
    </Panel>
  );
}

// ─── root ───────────────────────────────────────────────────────────────

export default function FieldDashboard({ initialProjectId, initialVersionId, onOpenIssueRegister, onOpenVarianceIntelligence, onOpenFloatAnalysis }: {
  initialProjectId?: string; initialVersionId?: string; onOpenIssueRegister?: (projectId: string, versionId?: string) => void;
  onOpenVarianceIntelligence?: (projectId: string, versionId?: string, assessmentFilter?: string) => void;
  onOpenFloatAnalysis?: (projectId: string, versionId: string, filter?: "negative" | "zero" | "nearCritical") => void;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const [warningThresholdDays, setWarningThresholdDays] = useState(10);
  const { data, loading, error, reload } = useFieldDashboardSummary(ctx, warningThresholdDays);
  const vi = useVarianceIntelligenceSummary(ctx);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", marginBottom: 6, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 900, color: C.text, letterSpacing: "-0.01em" }}>ScheduleIQ | Field Dashboard</div>
          <div style={{ fontSize: 12, color: C.muted, fontWeight: 600 }}>Integrated Field, Financial &amp; Schedule Performance</div>
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
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 16px", marginBottom: 16 }}>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Data Date</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{formatDataDate(data.context.currentDataDate)}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Current</span><div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{data.context.currentVersionLabel || "—"}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Previous</span><div style={{ fontSize: 13, fontWeight: 700, color: data.context.previousVersionLabel ? C.text : C.muted2 }}>{data.context.previousVersionLabel || "No Previous Update"}</div></div>
          <div><span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Baseline</span><div style={{ fontSize: 13, fontWeight: 700, color: data.context.baselineDesignated ? C.text : C.muted2 }}>{data.context.baselineDesignated ? data.context.baselineVersionLabel : "Not Designated"}</div></div>
        </div>
      )}

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && <div style={{ color: C.accent, padding: 20 }}>Loading Field Dashboard…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 20 }}>{error}</div>}

      {data && !loading && (
        <>
          <VarianceCompletionPanel vi={vi.data} loading={vi.loading} error={vi.error} projectId={ctx.projectId} versionId={ctx.versionId}
            onOpenVarianceIntelligence={onOpenVarianceIntelligence} onOpenFloatAnalysis={onOpenFloatAnalysis} />

          <ContractualMilestoneTracker projectId={ctx.projectId} panel={data.contractualMilestones}
            warningThresholdDays={warningThresholdDays} onThresholdChange={setWarningThresholdDays} onChanged={reload} />

          <Panel title="Project Financials & Manpower">
            {!data.financials?.available && !data.manpower?.available ? <Unavailable reason="Unavailable for this schedule." /> : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 9 }}>
                <KpiCard label="Current Spend" value={fmtMoney(data.financials?.currentSpend)} color={C.amber} />
                <KpiCard label="Budget" value={fmtMoney(data.financials?.budget)} />
                <KpiCard label="Earned Value" value={fmtMoney(data.financials?.earnedValue)} color={C.green} />
                <KpiCard label="Total Manpower (Headcount)" value="Unavailable" sub={data.manpower?.headcount?.reason} color={C.muted2} />
              </div>
            )}
          </Panel>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <SpendVsBudgetPanel financials={data.financials} spendTrend={data.spendTrend} />
            <EarnedVsSpendPanel spendTrend={data.spendTrend} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <ScheduleHealthPanel panel={data.scheduleHealth} />
            <ManpowerPanel panel={data.manpower} curve={data.manpowerCurve} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <IntegrationSourcesPanel sources={data.integrationSources} />
            <ProjectIssuesPanel issues={data.projectIssues} onOpenIssueRegister={onOpenIssueRegister ? () => onOpenIssueRegister(ctx.projectId, ctx.versionId) : undefined} />
          </div>
        </>
      )}
    </div>
  );
}
