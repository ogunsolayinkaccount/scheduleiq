import { useEffect, useState } from "react";
import { notifyProjectsChanged, useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { formatDataDate, sourceDataDateTooltip, versionSelectLabel, dedupeVersionsById } from "./dateFormat";
import { deleteConfirmationBody } from "./importProtection";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

type ProjectSummary = { id: string; name: string; versionCount: number };
type VersionSummary = {
  id: string; versionLabel: string; role: string; dataDate: string | null; dataDateOverridden?: boolean; sourceDataDate?: string | null;
  filename?: string; fileType?: string; classification?: string; p6ProjectId?: string | null; p6ProjectName?: string | null;
  uploadedAt?: string; activityCount?: number; relationshipCount?: number; wbsNodeCount?: number; calendarCount?: number;
};

const EV_METHOD_LABELS: Record<string, string> = {
  DURATION_PCT_COMPLETE: "Duration % Complete",
  PHYSICAL_PCT_COMPLETE: "Physical % Complete",
  UNITS_PROGRESS: "Units / Labor Progress",
};
const EV_METHOD_EXPLANATIONS: Record<string, string> = {
  DURATION_PCT_COMPLETE: "Earned value = (Original Duration − Remaining Duration) / Original Duration, applied to each activity's budget.",
  PHYSICAL_PCT_COMPLETE: "Earned value = P6's own physical % complete field, applied to each activity's budget. Only used for activities P6 itself flagged as physical-%-tracked (complete_pct_type = CP_Phys) — other activities are excluded from this method's total, not defaulted to it.",
  UNITS_PROGRESS: "Earned value = (Budgeted Units − Remaining Units) / Budgeted Units, applied to each activity's budget — sourced from P6's own remaining-units field, independent of % complete.",
};

const ENTRY_TYPE_LABELS: Record<string, string> = {
  APPROVED_BUDGET_CHANGE: "Budget Change",
  COMMITMENT: "Commitment",
  ORIGINAL_BUDGET_OVERRIDE: "Budget Override",
  APPROVED_EAC: "Approved EAC",
};

function Select({ value, onChange, options, placeholder, title }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; placeholder?: string; title?: string }) {
  return (
    <select
      value={value} onChange={(e) => onChange(e.target.value)} title={title}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 160 }}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function Metric({ label, value, sub, color, tooltip }: { label: string; value: string; sub?: string; color?: string; tooltip?: string }) {
  return (
    <div title={tooltip} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 15px", minWidth: 150, flex: "1 1 150px", cursor: tooltip ? "help" : "default" }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
        {label}{tooltip && <span style={{ marginLeft: 4, opacity: 0.6 }}>ⓘ</span>}
      </div>
      <div style={{ fontSize: 21, fontWeight: 800, color: color || C.text, fontFamily: "'DM Mono',monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: C.muted2, marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function Unavailable({ label, reason }: { label: string; reason: string }) {
  return (
    <div style={{ background: C.card, border: `1px dashed ${C.border}`, borderRadius: 10, padding: "12px 15px", minWidth: 150, flex: "1 1 150px" }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: C.muted2 }}>Unavailable</div>
      <div style={{ fontSize: 11, color: C.muted2, marginTop: 3 }}>{reason}</div>
    </div>
  );
}

function Input({ value, onChange, placeholder, type = "text", width }: { value: string; onChange: (v: string) => void; placeholder?: string; type?: string; width?: number }) {
  return (
    <input
      value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} type={type}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, width: width || 130 }}
    />
  );
}

const ROLE_COLOR: Record<string, string> = { CURRENT: C.accent, BASELINE: C.purple, PREVIOUS: C.green, OTHER: C.muted2 };

// ─────────────────────────────────────────────────────────────────────────
// Schedule Versions workspace (items 14-17 of the multi-version import
// phase) — chronological timeline + detail table + deliberate role
// reassignment. Reuses the version list this component already fetches
// (GET /api/projects/<id>/versions/) rather than a second endpoint.
// ─────────────────────────────────────────────────────────────────────────
function ScheduleVersionsTab({ versions, roleChangeError, roleChangeSavingId, onSetRole, onDeleteVersion, deletingVersionId }: {
  versions: VersionSummary[];
  roleChangeError: string | null;
  roleChangeSavingId: string | null;
  onSetRole: (versionId: string, classification: string) => void;
  onDeleteVersion: (versionId: string) => void;
  deletingVersionId: string | null;
}) {
  const [armedAction, setArmedAction] = useState<{ versionId: string; classification: string } | null>(null);
  const [armedDeleteId, setArmedDeleteId] = useState<string | null>(null);

  // Chronological (oldest first) for the timeline; the table below keeps
  // the version list's own newest-first order.
  const chronological = [...versions].sort((a, b) => (a.dataDate || "").localeCompare(b.dataDate || ""));

  return (
    <div>
      {roleChangeError && <div style={{ padding: 10, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 14, fontSize: 12 }}>{roleChangeError}</div>}

      <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>Version Timeline</div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, overflowX: "auto", paddingBottom: 12, marginBottom: 20 }}>
        {chronological.map((v, i) => (
          <div key={v.id} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
            <div style={{
              background: C.card, border: `2px solid ${ROLE_COLOR[v.role] || C.border}`, borderRadius: 10,
              padding: "10px 14px", minWidth: 120, textAlign: "center",
            }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.text, whiteSpace: "nowrap" }}>{v.versionLabel}</div>
              <div style={{ fontSize: 11, color: C.muted2 }}>{v.dataDate || "—"}</div>
              <div style={{ fontSize: 10, fontWeight: 800, color: ROLE_COLOR[v.role] || C.muted2, marginTop: 3 }}>{v.role}</div>
            </div>
            {i < chronological.length - 1 && <div style={{ color: C.muted2, fontSize: 16, padding: "0 6px" }}>→</div>}
          </div>
        ))}
        {chronological.length === 0 && <div style={{ color: C.muted2, fontSize: 12 }}>No schedule versions yet.</div>}
      </div>

      <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>All Schedule Versions</div>
      <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: C.card2 }}>
              {["Version", "Source Filename", "P6 Project ID", "Source DD", "Effective DD", "Role", "Activities", "Relationships", "Imported At", ""].map((h) => (
                <th key={h} style={{ padding: "8px 10px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => {
              const isArmed = armedAction?.versionId === v.id;
              return (
                <tr key={v.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: "7px 10px", fontWeight: 700 }}>{v.versionLabel}</td>
                  <td style={{ padding: "7px 10px", color: C.muted2 }}>{v.filename || "—"}</td>
                  <td style={{ padding: "7px 10px", color: C.muted2, fontFamily: "monospace" }}>{v.p6ProjectId || "—"}</td>
                  <td style={{ padding: "7px 10px" }}>{v.sourceDataDate || "—"}</td>
                  <td style={{ padding: "7px 10px" }}>{v.dataDate || "—"}{v.dataDateOverridden && <span title="Overridden from source" style={{ color: C.gold }}> *</span>}</td>
                  <td style={{ padding: "7px 10px", fontWeight: 800, color: ROLE_COLOR[v.role] || C.muted2 }}>{v.role}</td>
                  <td style={{ padding: "7px 10px" }}>{v.activityCount?.toLocaleString() ?? "—"}</td>
                  <td style={{ padding: "7px 10px" }}>{v.relationshipCount?.toLocaleString() ?? "—"}</td>
                  <td style={{ padding: "7px 10px", color: C.muted2, whiteSpace: "nowrap" }}>{v.uploadedAt ? v.uploadedAt.slice(0, 10) : "—"}</td>
                  <td style={{ padding: "7px 10px", whiteSpace: "nowrap" }}>
                    {isArmed ? (
                      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <span style={{ fontSize: 11, color: C.red, fontWeight: 700 }}>
                          Set {v.versionLabel} as {armedAction.classification === "APPROVED_BASELINE" ? "Approved Baseline" : "Current"}?
                        </span>
                        <button
                          disabled={roleChangeSavingId === v.id}
                          onClick={() => { onSetRole(v.id, armedAction.classification); setArmedAction(null); }}
                          style={{ background: C.red, border: "none", color: "#fff", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}
                        >
                          {roleChangeSavingId === v.id ? "Applying…" : "Confirm"}
                        </button>
                        <button onClick={() => setArmedAction(null)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>Cancel</button>
                      </span>
                    ) : (
                      armedDeleteId === v.id ? (
                        <span style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", maxWidth: 380 }}>
                          <span style={{ fontSize: 11, color: C.red }}>
                            Delete <strong>{v.versionLabel}</strong> (Data Date: {v.dataDate || "—"})? It becomes recoverable from Deleted Versions — other versions are kept.
                            {v.role === "BASELINE" && " It is the Approved Baseline — no baseline will exist until you designate another."}
                          </span>
                          <button disabled={deletingVersionId === v.id} onClick={() => { onDeleteVersion(v.id); setArmedDeleteId(null); }}
                            style={{ background: C.red, border: "none", color: "#fff", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
                            {deletingVersionId === v.id ? "Deleting…" : "Confirm delete"}
                          </button>
                          <button onClick={() => setArmedDeleteId(null)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>Cancel</button>
                        </span>
                      ) : (
                      <span style={{ display: "flex", gap: 6 }}>
                        {v.role !== "CURRENT" && (
                          <button onClick={() => setArmedAction({ versionId: v.id, classification: "CURRENT_UPDATE" })}
                            style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>
                            Set as Current
                          </button>
                        )}
                        {v.role !== "BASELINE" && (
                          <button onClick={() => setArmedAction({ versionId: v.id, classification: "APPROVED_BASELINE" })}
                            style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.purple, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>
                            Set as Approved Baseline
                          </button>
                        )}
                        <button onClick={() => setArmedDeleteId(v.id)}
                          style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.red, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>
                          Delete Version
                        </button>
                      </span>
                      )
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {versions.length === 0 && (
        <div style={{ padding: 30, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, marginTop: 12 }}>
          No schedule versions imported for this project yet.
        </div>
      )}
    </div>
  );
}

function fmt$(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${v < 0 ? "-" : ""}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${v < 0 ? "-" : ""}$${(abs / 1e3).toFixed(1)}K`;
  return `${v < 0 ? "-" : ""}$${abs.toFixed(0)}`;
}
function fmtH(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}K h` : `${Math.round(v)} h`;
}
function fmtIdx(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(3);
}
function idxColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return C.muted2;
  return v >= 1.0 ? C.green : v >= 0.9 ? C.amber : C.red;
}
function idxWord(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return v >= 1.0 ? "ahead of plan" : v >= 0.9 ? "slightly behind" : "materially behind";
}

export default function ProjectControls({ initialProjectId, initialSubTab }: { initialProjectId?: string; initialSubTab?: string } = {}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);   // stale responses from a previously selected project are dropped (see projectScope.ts)
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [versionId, setVersionId] = useState("");

  const [evMethod, setEvMethod] = useState("DURATION_PCT_COMPLETE");
  const [groupBy, setGroupBy] = useState("");
  const [subTab, setSubTab] = useState<"cost" | "ev" | "productivity" | "entries" | "executive" | "trends" | "forecastTrend" | "drivers" | "narrative" | "reports" | "versions">((initialSubTab as any) || "cost");

  const [costSummary, setCostSummary] = useState<any>(null);
  const [earnedValue, setEarnedValue] = useState<any>(null);
  const [productivity, setProductivity] = useState<any>(null);
  const [costEntries, setCostEntries] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newEntry, setNewEntry] = useState({ entryType: "APPROVED_BUDGET_CHANGE", cost: "", description: "", referenceNumber: "", effectiveDate: "", wbsId: "" });
  const [entrySaving, setEntrySaving] = useState(false);
  const [entryError, setEntryError] = useState<string | null>(null);

  const fetchCostEntries = () => {
    if (!projectId) return;
    sfetch(`${API}/api/projects/${projectId}/cost-entries/`).then((r) => r.json()).then((d) => setCostEntries(d.entries || [])).catch(() => {});
  };

  const addCostEntry = () => {
    if (!projectId || !newEntry.cost) return;
    setEntrySaving(true); setEntryError(null);
    sfetch(`${API}/api/projects/${projectId}/cost-entries/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entryType: newEntry.entryType, cost: parseFloat(newEntry.cost),
        description: newEntry.description || undefined, referenceNumber: newEntry.referenceNumber || undefined,
        effectiveDate: newEntry.effectiveDate || undefined, wbsId: newEntry.wbsId || undefined,
      }),
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then(() => {
        setNewEntry({ entryType: newEntry.entryType, cost: "", description: "", referenceNumber: "", effectiveDate: "", wbsId: "" });
        fetchCostEntries();
        fetchAll();
      })
      .catch((e: any) => setEntryError(e.message || String(e)))
      .finally(() => setEntrySaving(false));
  };

  const deleteCostEntry = (id: string) => {
    if (!projectId) return;
    sfetch(`${API}/api/projects/${projectId}/cost-entries/${id}/`, { method: "DELETE" })
      .then(() => { fetchCostEntries(); fetchAll(); })
      .catch(() => {});
  };

  useEffect(() => {
    fetch(`${API}/api/projects/`).then((r) => r.json()).then((d) => { const list = d.projects || []; setProjects(list); setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur)); }).catch(() => {});
  }, [projectsTick]);

  useEffect(() => {
    if (!projectId) { setVersions([]); setVersionId(""); return; }
    sfetch(`${API}/api/projects/${projectId}/versions/`)
      .then((r) => r.json())
      .then((d) => {
        const vs: VersionSummary[] = d.versions || [];
        setVersions(dedupeVersionsById(vs));
        const cur = vs.find((v) => v.role === "CURRENT");
        setVersionId(cur?.id || vs[0]?.id || "");
      })
      .catch(() => setVersions([]));
  }, [projectId, projectsTick]);

  // ── Inline Data Date edit (Phase 7: live update, no page reload) ────────
  const [editingDataDate, setEditingDataDate] = useState(false);
  const [dataDateDraft, setDataDateDraft] = useState("");
  const [dataDateSaving, setDataDateSaving] = useState(false);
  const [dataDateError, setDataDateError] = useState<string | null>(null);

  const patchVersionDataDate = (body: { dataDate?: string; restoreSourceDate?: boolean }) => {
    if (!projectId || !versionId) return;
    setDataDateSaving(true); setDataDateError(null);
    sfetch(`${API}/api/projects/${projectId}/versions/${versionId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then((updated) => {
        // Update in place — every reader of `versions` in this view
        // (selector, Data Date line, downstream fetches keyed off
        // versionId) reflects the new value immediately, no reload.
        setVersions((prev) => prev.map((v) => (v.id === updated.id ? { ...v, ...updated } : v)));
        setEditingDataDate(false);
        fetchAll();          // downstream metrics were computed with the old date — refresh them too
        fetchInsights();
      })
      .catch((e: any) => setDataDateError(e.message || String(e)))
      .finally(() => setDataDateSaving(false));
  };

  // ── Deletion — the backend Project/ScheduleUpload rows are the single
  // source of truth for the whole app (Intelligence included), so deleting
  // here is the only step; notifyProjectsChanged() makes every open selector
  // refetch immediately.
  const [deletingVersionId, setDeletingVersionId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const deleteVersion = (targetVersionId: string) => {
    if (!projectId) return;
    setDeletingVersionId(targetVersionId); setDeleteError(null);
    // Soft delete (Phase 3/4: Import Protection and Schedule Deletion
    // Auditing) requires an explicit confirmation phrase in the body — the
    // inline arm/confirm UI above is what makes this click deliberate, but
    // the backend never trusts that alone, so the phrase goes through too.
    sfetch(`${API}/api/projects/${projectId}/versions/${targetVersionId}/`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: deleteConfirmationBody(),
    })
      .then(async (r) => { if (!r.ok) { const t = await r.text(); let m = t; try { m = JSON.parse(t).error || t; } catch {} throw new Error(m); } return r.json(); })
      .then((res) => {
        if (res.deletedRole === "BASELINE") setDeleteError("The Approved Baseline was deleted — no baseline is designated until you set one on a remaining version.");
        notifyProjectsChanged();
      })
      .catch((e: any) => setDeleteError(e.message || String(e)))
      .finally(() => setDeletingVersionId(null));
  };

  const deleteProject = () => {
    if (!projectId) return;
    const p = projects.find((x) => x.id === projectId);
    const msg = `Delete project "${p?.name || projectId}"?

This permanently removes the project, all ${versions.length} of its schedule versions, and all associated analytical data (risks, recovery scenarios, mitigation actions, saved reports, cost entries) from ScheduleIQ — including the Intelligence Portal.

This cannot be undone.`;
    if (!window.confirm(msg)) return;
    setDeleteError(null);
    sfetch(`${API}/api/projects/${projectId}/`, { method: "DELETE" })
      .then(async (r) => { if (!r.ok) throw new Error(await r.text()); })
      .then(() => { setProjectId(""); notifyProjectsChanged(); })
      .catch((e: any) => setDeleteError(e.message || String(e)));
  };

  // ── Deliberate role reassignment (Schedule Versions tab) ────────────────
  const [roleChangeError, setRoleChangeError] = useState<string | null>(null);
  const [roleChangeSavingId, setRoleChangeSavingId] = useState<string | null>(null);

  const patchVersionClassification = (targetVersionId: string, classification: string) => {
    if (!projectId) return;
    setRoleChangeSavingId(targetVersionId); setRoleChangeError(null);
    sfetch(`${API}/api/projects/${projectId}/versions/${targetVersionId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ classification }),
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then(() => {
        // Roles are computed fresh from classification + upload order, so
        // one field change can shift more than one row's badge (e.g.
        // promoting an older update to CURRENT demotes the previous
        // CURRENT to OTHER) — simplest correct refresh is to re-fetch the
        // whole version list rather than patch entries in place.
        sfetch(`${API}/api/projects/${projectId}/versions/`).then((r) => r.json()).then((d) => setVersions(dedupeVersionsById(d.versions || [])));
      })
      .catch((e: any) => setRoleChangeError(e.message || String(e)))
      .finally(() => setRoleChangeSavingId(null));
  };

  const fetchAll = () => {
    if (!projectId || !versionId) return;
    setLoading(true); setError(null);
    const params = new URLSearchParams({ version: versionId, evMethod });
    const groupParams = new URLSearchParams({ version: versionId, evMethod });
    if (groupBy) groupParams.set("group_by", groupBy);

    Promise.all([
      sfetch(`${API}/api/projects/${projectId}/cost-summary/?${params.toString()}`).then((r) => r.json()),
      sfetch(`${API}/api/projects/${projectId}/earned-value/?${groupParams.toString()}`).then((r) => r.json()),
      sfetch(`${API}/api/projects/${projectId}/productivity/?${groupParams.toString()}`).then((r) => r.json()),
    ])
      .then(([cs, ev, pr]) => {
        if (cs.error) throw new Error(cs.error);
        setCostSummary(cs); setEarnedValue(ev); setProductivity(pr);
      })
      .catch((e: any) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchCostEntries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (projectId && versionId) fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, versionId, evMethod, groupBy]);

  // ── Trends / drivers / executive summary (Forecasting & Executive Reporting phase) ──
  const [controlsTrends, setControlsTrends] = useState<any>(null);
  const [execSummary, setExecSummary] = useState<any>(null);
  const [driverDimension, setDriverDimension] = useState<"cost" | "schedule" | "productivity">("cost");
  const [driverGroupBy, setDriverGroupBy] = useState("discipline");
  const [driverResult, setDriverResult] = useState<any>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);

  const fetchInsights = () => {
    if (!projectId) return;
    setInsightsLoading(true);
    const p = new URLSearchParams({ evMethod, pvMethod: "LINEAR_BASELINE_SPREAD" });
    Promise.all([
      sfetch(`${API}/api/projects/${projectId}/controls-trends/?${p.toString()}`).then((r) => r.json()),
      sfetch(`${API}/api/projects/${projectId}/executive-summary/?${p.toString()}&group_by=${driverGroupBy}`).then((r) => r.json()),
    ])
      .then(([trends, exec]) => { setControlsTrends(trends); setExecSummary(exec); })
      .catch(() => {})
      .finally(() => setInsightsLoading(false));
  };

  useEffect(() => {
    if (projectId) fetchInsights();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, evMethod, driverGroupBy]);

  useEffect(() => {
    if (!projectId || !versionId) return;
    const p = new URLSearchParams({ evMethod, dimension: driverDimension, group_by: driverGroupBy, version: versionId });
    sfetch(`${API}/api/projects/${projectId}/controls-drivers/?${p.toString()}`)
      .then((r) => r.json()).then(setDriverResult).catch(() => {});
  }, [projectId, versionId, evMethod, driverDimension, driverGroupBy]);

  // ── Reports (Automated Project Controls Reporting phase) ────────────────
  const [reportType, setReportType] = useState<"WEEKLY_PROJECT_CONTROLS" | "MONTHLY_EXECUTIVE">("WEEKLY_PROJECT_CONTROLS");
  const [reportHistory, setReportHistory] = useState<any[]>([]);
  const [previewReport, setPreviewReport] = useState<any>(null);
  const [reportGenerating, setReportGenerating] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<{ a: string; b: string }>({ a: "", b: "" });
  const [compareResult, setCompareResult] = useState<any>(null);

  // ── 4-Week Look Ahead options (Weekly Look-Ahead Report Integration phase) ──
  const [lookaheadWeeks, setLookaheadWeeks] = useState("4");
  const [lookaheadFrom, setLookaheadFrom] = useState("");
  const [lookaheadTo, setLookaheadTo] = useState("");
  const [lookaheadDimension, setLookaheadDimension] = useState("");
  const [lookaheadValue, setLookaheadValue] = useState("");
  const [lookaheadBaselineVersion, setLookaheadBaselineVersion] = useState("");

  // ── Schedule Update Performance: optional Previous-Version override ─────
  // Default is "Automatic — immediately preceding update" (empty string =
  // let build_report_payload's own upload-order resolution decide, same as
  // every other screen). A deliberate override never changes project roles.
  const [updatePreviousVersion, setUpdatePreviousVersion] = useState("");

  const fetchReportHistory = () => {
    if (!projectId) return;
    sfetch(`${API}/api/projects/${projectId}/reports/`).then((r) => r.json()).then((d) => setReportHistory(d.reports || [])).catch(() => {});
  };

  useEffect(() => {
    if (subTab === "reports") fetchReportHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, subTab]);

  const generateReport = () => {
    if (!projectId) return;
    setReportGenerating(true); setReportError(null);
    const body: any = { reportType, version: versionId, evMethod, group_by: driverGroupBy };
    if (lookaheadWeeks === "custom") {
      if (lookaheadFrom) body.lookaheadFrom = lookaheadFrom;
      if (lookaheadTo) body.lookaheadTo = lookaheadTo;
    } else {
      body.lookaheadWeeks = parseInt(lookaheadWeeks, 10);
    }
    if (lookaheadDimension && lookaheadValue) body.lookaheadFilters = { [lookaheadDimension]: lookaheadValue };
    if (lookaheadBaselineVersion) body.baselineVersion = lookaheadBaselineVersion;
    if (updatePreviousVersion) body.updatePreviousVersion = updatePreviousVersion;
    sfetch(`${API}/api/projects/${projectId}/reports/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then((d) => { setPreviewReport(d); fetchReportHistory(); })
      .catch((e: any) => setReportError(e.message || String(e)))
      .finally(() => setReportGenerating(false));
  };

  const openSavedReport = (id: string) => {
    if (!projectId) return;
    sfetch(`${API}/api/projects/${projectId}/reports/${id}/`).then((r) => r.json()).then(setPreviewReport).catch(() => {});
  };

  const deleteSavedReport = (id: string) => {
    if (!projectId) return;
    sfetch(`${API}/api/projects/${projectId}/reports/${id}/`, { method: "DELETE" })
      .then(() => { fetchReportHistory(); if (previewReport?.id === id) setPreviewReport(null); })
      .catch(() => {});
  };

  const runReportComparison = () => {
    if (!projectId || !compareIds.a || !compareIds.b) return;
    sfetch(`${API}/api/projects/${projectId}/reports/compare/?a=${compareIds.a}&b=${compareIds.b}`)
      .then((r) => r.json()).then(setCompareResult).catch(() => {});
  };

  const evOverall = earnedValue?.overall;
  const cost = evOverall?.cost;
  const hours = evOverall?.hours;

  const DIRECTION_ARROW: Record<string, string> = { improving: "▲", deteriorating: "▼", unchanged: "—", unavailable: "" };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Project Controls</div>
        <div style={{ fontSize: 13, color: C.muted }}>
          Budget, Earned Value, and Labor Productivity — computed from imported P6 resource/cost data (TASKRSRC), never fabricated.
          Figures marked <strong>Unavailable</strong> mean the source schedule doesn't carry that data — not a zero.
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
          <Select value={projectId} onChange={(id: string) => { setVersions([]); setVersionId(""); setProjectId(id); }} placeholder="Select a project…" options={projects.map((p) => ({ value: p.id, label: p.name }))} />
        </div>
        {projectId && (
          <button onClick={deleteProject} title="Permanently delete this project and all its schedule versions"
            style={{ alignSelf: "flex-end", background: "transparent", border: `1px solid ${C.red}`, color: C.red, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12 }}>
            Delete Project
          </button>
        )}
        {projectId && versions.length > 0 && (
          <div>
            <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Version</div>
            <Select value={versionId} onChange={setVersionId} options={versions.map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
          </div>
        )}
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Earned Value Method</div>
          <Select value={evMethod} onChange={setEvMethod} title={EV_METHOD_EXPLANATIONS[evMethod]}
            options={Object.entries(EV_METHOD_LABELS).map(([value, label]) => ({ value, label }))} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Group By</div>
          <Select value={groupBy} onChange={setGroupBy} placeholder="Whole project"
            options={["wbs", "area", "discipline", "contractor", "system"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))} />
        </div>
      </div>

      {deleteError && <div style={{ padding: 10, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 12, fontSize: 12 }}>{deleteError}</div>}

      {(() => {
        const selectedVersion = versions.find((v) => v.id === versionId);
        if (!selectedVersion) return null;
        return (
          <div style={{ fontSize: 12, color: C.muted2, marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {!editingDataDate ? (
              <>
                <span>
                  Data Date: <strong style={{ color: C.text }}>{formatDataDate(selectedVersion.dataDate)}</strong>
                  {selectedVersion.dataDateOverridden && (
                    <span title={sourceDataDateTooltip(selectedVersion)} style={{ color: C.gold, cursor: "help" }}> · Overridden ⓘ</span>
                  )}
                </span>
                <button
                  onClick={() => { setDataDateDraft(selectedVersion.dataDate || ""); setEditingDataDate(true); setDataDateError(null); }}
                  style={{ background: "transparent", border: "none", color: C.accent, fontSize: 11, cursor: "pointer", textDecoration: "underline", padding: 0 }}
                >
                  Edit
                </button>
                {selectedVersion.dataDateOverridden && selectedVersion.sourceDataDate && (
                  <button
                    onClick={() => patchVersionDataDate({ restoreSourceDate: true })}
                    disabled={dataDateSaving}
                    style={{ background: "transparent", border: "none", color: C.muted2, fontSize: 11, cursor: dataDateSaving ? "default" : "pointer", textDecoration: "underline", padding: 0 }}
                  >
                    Restore Source Data Date
                  </button>
                )}
              </>
            ) : (
              <>
                <span>Data Date:</span>
                <input
                  type="date" value={dataDateDraft} onChange={(e) => setDataDateDraft(e.target.value)}
                  style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 12, fontFamily: "inherit", color: C.text }}
                />
                <button
                  onClick={() => dataDateDraft && patchVersionDataDate({ dataDate: dataDateDraft })}
                  disabled={dataDateSaving || !dataDateDraft}
                  style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 6, padding: "4px 12px", cursor: dataDateSaving ? "default" : "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit", opacity: dataDateSaving || !dataDateDraft ? 0.6 : 1 }}
                >
                  {dataDateSaving ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={() => { setEditingDataDate(false); setDataDateError(null); }}
                  disabled={dataDateSaving}
                  style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 6, padding: "4px 12px", cursor: "pointer", fontSize: 11, fontFamily: "inherit" }}
                >
                  Cancel
                </button>
              </>
            )}
            {dataDateError && <span style={{ color: C.red }}>{dataDateError}</span>}
          </div>
        );
      })()}

      <div style={{ background: `${C.accent}0a`, border: `1px solid ${C.accent}30`, borderRadius: 8, padding: "9px 14px", marginBottom: 14, fontSize: 12, color: C.text, display: "flex", gap: 8, alignItems: "flex-start" }}>
        <span style={{ fontSize: 14 }}>ⓘ</span>
        <span><strong style={{ color: C.accent }}>{EV_METHOD_LABELS[evMethod]}</strong> — {EV_METHOD_EXPLANATIONS[evMethod]}</span>
      </div>

      {!projectId && <div style={{ padding: 30, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>Select a project to view its cost and earned-value data.</div>}
      {error && <div style={{ padding: 14, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 14 }}>{error}</div>}
      {loading && <div style={{ padding: 20, textAlign: "center", color: C.muted }}>Loading…</div>}

      {projectId && !loading && !error && costSummary && (
        <>
          <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 16 }}>
            {[
              { id: "executive", label: "Executive Summary" },
              { id: "cost", label: "Cost Summary" }, { id: "ev", label: "Earned Value" }, { id: "productivity", label: "Labor Productivity" },
              { id: "trends", label: "Performance Trends" }, { id: "forecastTrend", label: "Forecast Trend" },
              { id: "drivers", label: "Variance Drivers" }, { id: "narrative", label: "Management Narrative" },
              { id: "entries", label: "Cost Entries" }, { id: "versions", label: "Schedule Versions" }, { id: "reports", label: "Reports" },
            ].map((t) => (
              <button key={t.id} onClick={() => setSubTab(t.id as any)}
                style={{ background: "transparent", border: "none", borderBottom: `2px solid ${subTab === t.id ? C.accent : "transparent"}`, color: subTab === t.id ? C.accent : C.muted2, padding: "8px 14px", cursor: "pointer", fontSize: 13, fontFamily: "inherit", fontWeight: subTab === t.id ? 800 : 600 }}>
                {t.label}
              </button>
            ))}
          </div>

          {subTab === "cost" && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
              <Metric label="Original Budget" value={fmt$(costSummary.budget.originalBudget.value)} sub={`Source: ${costSummary.budget.originalBudget.source}`} />
              <Metric label="Approved Budget Changes" value={costSummary.budget.approvedBudgetChanges.value != null ? fmt$(costSummary.budget.approvedBudgetChanges.value) : "None entered"} sub={`Source: ${costSummary.budget.approvedBudgetChanges.source}`} />
              <Metric label="Current Budget (BAC)" value={fmt$(costSummary.budget.currentBudget.value)} sub={costSummary.budget.currentBudget.formula} color={C.text} />
              <Metric label="Actual Cost" value={fmt$(costSummary.actuals.actualCost.value)} sub={`Source: ${costSummary.actuals.actualCost.source}`} color={C.amber} />
              <Metric label="Commitments" value={costSummary.commitments.value != null ? fmt$(costSummary.commitments.value) : "None entered"} sub={costSummary.commitments.note} />
            </div>
          )}

          {subTab === "cost" && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Calculated Forecast Scenarios</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 18 }}>
                {costSummary.forecast.scenarios.length === 0 && <Unavailable label="Estimate at Completion" reason="No cost-loaded activities in this schedule." />}
                {costSummary.forecast.scenarios.map((s: any) => (
                  <Metric key={s.methodology} label={s.label.split(" (")[0]} value={fmt$(s.eac)} sub={s.formula} color={s.eac > (costSummary.budget.currentBudget.value || 0) ? C.red : C.green} tooltip={s.label} />
                ))}
              </div>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Approved Forecast</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {costSummary.forecast.approvedEac.value != null
                  ? <Metric label="Approved EAC" value={fmt$(costSummary.forecast.approvedEac.value)} sub={costSummary.forecast.approvedEac.description || "Analyst-approved — enter via Cost Entries tab"} color={C.accent} />
                  : <Unavailable label="Approved EAC" reason="Not entered. Add one via the Cost Entries tab — never overwrites the calculated scenarios above." />
                }
              </div>
            </div>
          )}

          {subTab === "ev" && evOverall && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
                {cost?.bac != null
                  ? <>
                      <Metric label="BAC" value={fmt$(cost.bac)} sub="Budget at Completion" />
                      <Metric label="PV" value={fmt$(cost.pv)} sub="Planned Value (baseline-linear-spread — see warning below)" />
                      <Metric label="EV" value={fmt$(cost.ev)} sub={`Earned Value (${EV_METHOD_LABELS[evMethod]})`} color={C.green} />
                      <Metric label="AC" value={fmt$(cost.ac)} sub="Actual Cost" color={C.amber} />
                      <Metric label="CV" value={fmt$(cost.cv)} sub="Cost Variance = EV − AC" color={cost.cv >= 0 ? C.green : C.red} />
                      <Metric label="SV" value={fmt$(cost.sv)} sub="Schedule Variance = EV − PV" color={cost.sv >= 0 ? C.green : C.red} />
                      <Metric label="CPI" value={fmtIdx(cost.cpi)} color={idxColor(cost.cpi)}
                        tooltip={cost.cpi != null ? `ScheduleIQ is measuring $${cost.cpi.toFixed(2)} of earned budget value for every $1.00 of actual cost.` : undefined}
                        sub={cost.cpi != null ? `$${cost.cpi.toFixed(2)} earned per $1 spent` : "Unavailable — AC is zero"} />
                      <Metric label="SPI" value={fmtIdx(cost.spi)} color={idxColor(cost.spi)}
                        tooltip={cost.spi != null ? `The project has earned ${(cost.spi * 100).toFixed(0)}% of the value planned for this point in time.` : undefined}
                        sub={cost.spi != null ? idxWord(cost.spi) : "Unavailable — PV is zero"} />
                      <Metric label="TCPI" value={fmtIdx(cost.tcpi)} sub="Cost efficiency required for remaining work to hit BAC" />
                    </>
                  : <Unavailable label="Cost EVM" reason="No cost-loaded activities in this schedule (TASKRSRC has no target_cost). Hours-based EVM may still be available below." />
                }
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 10 }}>
                {hours?.bac != null
                  ? <>
                      <Metric label="BAC (Hours)" value={fmtH(hours.bac)} />
                      <Metric label="PV (Hours)" value={fmtH(hours.pv)} />
                      <Metric label="EV (Hours)" value={fmtH(hours.ev)} color={C.green} />
                      <Metric label="AC (Hours)" value={fmtH(hours.ac)} color={C.amber} />
                    </>
                  : <Unavailable label="Hours EVM" reason="No resource-loaded activities in this schedule." />
                }
              </div>
              <div style={{ fontSize: 11, color: C.muted2, fontStyle: "italic", marginBottom: 10 }}>{evOverall.pvWarning}</div>
              {groupBy && earnedValue.groups && (
                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "auto", marginTop: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead><tr style={{ background: C.card2 }}>{[groupBy[0].toUpperCase() + groupBy.slice(1), "BAC", "EV", "AC", "CPI", "SPI"].map((h) => <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                    <tbody>{earnedValue.groups.map((g: any) => (
                      <tr key={g.group} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: "7px 12px", fontWeight: 600 }}>{g.group}</td>
                        <td style={{ padding: "7px 12px" }}>{fmt$(g.cost.bac)}</td>
                        <td style={{ padding: "7px 12px" }}>{fmt$(g.cost.ev)}</td>
                        <td style={{ padding: "7px 12px" }}>{fmt$(g.cost.ac)}</td>
                        <td style={{ padding: "7px 12px", color: idxColor(g.cost.cpi) }}>{fmtIdx(g.cost.cpi)}</td>
                        <td style={{ padding: "7px 12px", color: idxColor(g.cost.spi) }}>{fmtIdx(g.cost.spi)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {subTab === "productivity" && productivity && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
                {productivity.overall.available
                  ? <>
                      <Metric label="Budgeted Hours" value={fmtH(productivity.overall.budgetedHours)} />
                      <Metric label="Earned Hours" value={fmtH(productivity.overall.earnedHours)} color={C.green} />
                      <Metric label="Actual Hours" value={fmtH(productivity.overall.actualHours)} color={C.amber} />
                      <Metric label="Remaining Hours" value={fmtH(productivity.overall.remainingHours)} />
                      <Metric label="Productivity Factor" value={productivity.overall.productivityFactor != null ? productivity.overall.productivityFactor.toFixed(2) : "—"}
                        color={idxColor(productivity.overall.productivityFactor)}
                        tooltip="Earned Hours ÷ Actual Hours. Above 1.0 means the crew is earning more hours of budgeted work than hours actually spent."
                        sub={productivity.overall.productivityFactor != null ? (productivity.overall.productivityFactor >= 1 ? "Better than planned rate" : "Below planned rate") : "Unavailable — no actual hours logged"} />
                      <Metric label="Hours Variance" value={`${productivity.overall.hoursVariance >= 0 ? "+" : ""}${productivity.overall.hoursVariance.toFixed(0)} h`}
                        color={productivity.overall.hoursVariance >= 0 ? C.green : C.red} sub="Earned − Actual" />
                    </>
                  : <Unavailable label="Labor Productivity" reason="No resource-loaded activities in this schedule (TASKRSRC has no target_qty)." />
                }
              </div>
              {groupBy && productivity.groups && (
                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead><tr style={{ background: C.card2 }}>{[groupBy[0].toUpperCase() + groupBy.slice(1), "Budgeted", "Earned", "Actual", "Factor"].map((h) => <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                    <tbody>{productivity.groups.map((g: any) => (
                      <tr key={g.group} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: "7px 12px", fontWeight: 600 }}>{g.group}</td>
                        <td style={{ padding: "7px 12px" }}>{g.available ? fmtH(g.budgetedHours) : "—"}</td>
                        <td style={{ padding: "7px 12px" }}>{g.available ? fmtH(g.earnedHours) : "—"}</td>
                        <td style={{ padding: "7px 12px" }}>{g.available ? fmtH(g.actualHours) : "—"}</td>
                        <td style={{ padding: "7px 12px", color: idxColor(g.productivityFactor) }}>{g.productivityFactor != null ? g.productivityFactor.toFixed(2) : "—"}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {subTab === "executive" && (
            <div>
              {insightsLoading && <div style={{ padding: 20, textAlign: "center", color: C.muted }}>Loading…</div>}
              {execSummary && execSummary.warnings && execSummary.warnings.length > 0 && (
                <div style={{ background: "#fff8ea", border: `1px solid ${C.gold}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 14, fontSize: 12, color: C.text }}>
                  {execSummary.warnings.map((w: string, i: number) => <div key={i}>⚠ {w}</div>)}
                </div>
              )}
              {execSummary && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                  <Metric label="Current Budget" value={fmt$(costSummary?.budget?.currentBudget?.value)} />
                  <Metric label="EV" value={fmt$(cost?.ev)} color={C.green} />
                  <Metric label="AC" value={fmt$(cost?.ac)} color={C.amber} />
                  <Metric label="CPI" value={fmtIdx(cost?.cpi)} color={idxColor(cost?.cpi)}
                    sub={execSummary.trends?.cpi?.observation || (execSummary.comparison?.metrics?.cpi?.direction ? `${DIRECTION_ARROW[execSummary.comparison.metrics.cpi.direction]} ${execSummary.comparison.metrics.cpi.direction}` : undefined)} />
                  <Metric label="SPI" value={fmtIdx(cost?.spi)} color={idxColor(cost?.spi)}
                    sub={execSummary.trends?.spi?.observation || (execSummary.comparison?.metrics?.spi?.direction ? `${DIRECTION_ARROW[execSummary.comparison.metrics.spi.direction]} ${execSummary.comparison.metrics.spi.direction}` : undefined)} />
                  <Metric label="Approved EAC" value={execSummary.eacDrift?.approvedEac ? fmt$(execSummary.eacDrift.approvedEac.value) : "None entered"} color={C.accent} />
                  <Metric label="Bottom-Up EAC" value={execSummary.eacDrift?.scenarios?.BOTTOM_UP ? fmt$(execSummary.eacDrift.scenarios.BOTTOM_UP.current) : "Unavailable"} />
                  <Metric label="VAC" value={fmt$(cost?.vac)} color={cost?.vac != null ? (cost.vac >= 0 ? C.green : C.red) : C.muted2} />
                  <Metric label="Productivity Factor" value={productivity?.overall?.productivityFactor != null ? productivity.overall.productivityFactor.toFixed(2) : "Unavailable"} color={idxColor(productivity?.overall?.productivityFactor)} />
                </div>
              )}
              {execSummary && execSummary.controlSignals && execSummary.controlSignals.length > 0 && (
                <div style={{ marginTop: 18 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Control Signals</div>
                  {execSummary.controlSignals.map((s: any, i: number) => (
                    <div key={i} style={{ background: s.severity === "critical" ? "#fff0f0" : "#fff8ea", border: `1px solid ${s.severity === "critical" ? C.red : C.gold}40`, borderRadius: 8, padding: "9px 14px", marginBottom: 6, fontSize: 12, color: C.text }}>
                      {s.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {subTab === "trends" && (
            <div>
              {controlsTrends && controlsTrends.series && controlsTrends.series.length > 1 ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
                  {[
                    { key: "cpi", label: "CPI", color: C.accent },
                    { key: "spi", label: "SPI", color: C.purple },
                    { key: "productivityFactor", label: "Productivity Factor", color: C.green },
                  ].map((m) => (
                    <div key={m.key} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, flex: "1 1 380px", height: 280 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>{m.label} Over Time</div>
                      <ResponsiveContainer width="100%" height={220}>
                        <LineChart data={controlsTrends.series.map((s: any) => ({ date: s.dataDate, value: s[m.key] }))}>
                          <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                          <XAxis dataKey="date" tick={{ fontSize: 10, fill: C.muted }} />
                          <YAxis tick={{ fontSize: 10, fill: C.muted }} domain={["auto", "auto"]} />
                          <Tooltip formatter={(v: any) => (v == null ? "Unavailable" : v.toFixed(3))} />
                          <Line type="monotone" dataKey="value" stroke={m.color} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} name={m.label} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ))}
                </div>
              ) : (
                <Unavailable label="Performance Trends" reason="Fewer than 2 persisted schedule versions with a data date — a trend needs at least two real points, never interpolated." />
              )}
            </div>
          )}

          {subTab === "forecastTrend" && (
            <div>
              {controlsTrends && controlsTrends.series && controlsTrends.series.length > 1 ? (
                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, height: 320 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>EAC by Methodology Over Time</div>
                  <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={controlsTrends.series.map((s: any) => ({
                      date: s.dataDate,
                      cpiBased: s.eacScenarios?.CPI_BASED?.eac ?? null,
                      bottomUp: s.eacScenarios?.BOTTOM_UP?.eac ?? null,
                      composite: s.eacScenarios?.CPI_SPI_COMPOSITE?.eac ?? null,
                    }))}>
                      <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: C.muted }} />
                      <YAxis tick={{ fontSize: 10, fill: C.muted }} tickFormatter={(v) => fmt$(v)} />
                      <Tooltip formatter={(v: any) => (v == null ? "Unavailable" : fmt$(v))} />
                      <Legend />
                      <Line type="monotone" dataKey="cpiBased" name="CPI-Based EAC" stroke={C.accent} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
                      <Line type="monotone" dataKey="bottomUp" name="Bottom-Up EAC" stroke={C.amber} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
                      <Line type="monotone" dataKey="composite" name="CPI×SPI EAC" stroke={C.purple} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <Unavailable label="Forecast Trend" reason="Fewer than 2 persisted schedule versions with cost-loaded data — no EAC drift to chart yet." />
              )}
              {execSummary?.eacDrift?.approvedEac && (
                <div style={{ marginTop: 12, fontSize: 12, color: C.muted2 }}>
                  Approved EAC: <strong style={{ color: C.accent }}>{fmt$(execSummary.eacDrift.approvedEac.value)}</strong> — a single analyst-entered point-in-time value, not itself charted as a trend.
                </div>
              )}
            </div>
          )}

          {subTab === "drivers" && (
            <div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 14 }}>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Rank By</div>
                  <Select value={driverDimension} onChange={(v) => setDriverDimension(v as any)}
                    options={[{ value: "cost", label: "Cost Variance (CV)" }, { value: "schedule", label: "Schedule Variance (SV)" }, { value: "productivity", label: "Productivity Factor" }]} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Group By</div>
                  <Select value={driverGroupBy} onChange={setDriverGroupBy}
                    options={["wbs", "area", "discipline", "contractor", "system"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))} />
                </div>
              </div>
              {driverResult && driverResult.drivers && driverResult.drivers.length > 0 ? (
                <>
                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead><tr style={{ background: C.card2 }}>
                        {[driverGroupBy[0].toUpperCase() + driverGroupBy.slice(1), driverDimension === "cost" ? "CV" : driverDimension === "schedule" ? "SV" : "Productivity Factor", "Contribution"].map((h) => <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}
                      </tr></thead>
                      <tbody>{driverResult.drivers.map((d: any) => {
                        const value = driverDimension === "cost" ? d.cv : driverDimension === "schedule" ? d.sv : d.productivityFactor;
                        const isMoneyMetric = driverDimension !== "productivity";
                        return (
                          <tr key={d.group} style={{ borderBottom: `1px solid ${C.border}` }}>
                            <td style={{ padding: "7px 12px", fontWeight: 600 }}>{d.group}</td>
                            <td style={{ padding: "7px 12px", color: (value ?? 0) < 0 ? C.red : (isMoneyMetric ? C.green : idxColor(value)) }}>{isMoneyMetric ? fmt$(value) : value?.toFixed(2)}</td>
                            <td style={{ padding: "7px 12px", color: C.muted2 }}>{d.contributionPct != null ? `${d.contributionPct}%` : "—"}</td>
                          </tr>
                        );
                      })}</tbody>
                    </table>
                  </div>
                  {driverResult.reconciliation && driverResult.reconciliation.reconciled != null && (
                    <div style={{ fontSize: 11, color: C.muted2, marginTop: 8 }}>
                      Reconciliation: {driverResult.reconciliation.reconciled ? "✓ group totals reconcile with the project figure" : `⚠ sum of groups (${fmt$(driverResult.reconciliation.sumOfIncludedGroups)}) differs from project total (${fmt$(driverResult.reconciliation.projectTotal)})`}.
                      {driverResult.excludedGroupCount > 0 && ` ${driverResult.excludedGroupCount} group(s) excluded — no usable data for this metric.`}
                    </div>
                  )}
                </>
              ) : (
                <Unavailable label="Variance Drivers" reason="No groups with usable data for this metric in the current version." />
              )}
            </div>
          )}

          {subTab === "narrative" && (
            <div>
              {execSummary && execSummary.narrative ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {[
                    { key: "overallPerformance", label: "Overall Performance" },
                    { key: "schedule", label: "Schedule" },
                    { key: "forecast", label: "Forecast" },
                    { key: "primaryDrivers", label: "Primary Drivers" },
                    { key: "productivity", label: "Productivity" },
                  ].map((s) => {
                    const text = execSummary.narrative.sections[s.key];
                    return (
                      <div key={s.key} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px" }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>{s.label}</div>
                        {text ? <div style={{ fontSize: 13, color: C.text }}>{text}</div> : <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic" }}>Unavailable — insufficient data to generate this statement.</div>}
                      </div>
                    );
                  })}
                  <div style={{ fontSize: 11, color: C.muted2, fontStyle: "italic", marginTop: 4 }}>{execSummary.narrative.note}</div>
                </div>
              ) : (
                <Unavailable label="Management Narrative" reason="No data available yet for this project." />
              )}
            </div>
          )}

          {subTab === "entries" && (
            <div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-end", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Type</div>
                  <Select value={newEntry.entryType} onChange={(v) => setNewEntry({ ...newEntry, entryType: v })}
                    options={Object.entries(ENTRY_TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Amount ($)</div>
                  <Input value={newEntry.cost} onChange={(v) => setNewEntry({ ...newEntry, cost: v })} type="number" width={110} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Description</div>
                  <Input value={newEntry.description} onChange={(v) => setNewEntry({ ...newEntry, description: v })} width={220} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Reference #</div>
                  <Input value={newEntry.referenceNumber} onChange={(v) => setNewEntry({ ...newEntry, referenceNumber: v })} width={100} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>WBS / Cost Code</div>
                  <Input value={newEntry.wbsId} onChange={(v) => setNewEntry({ ...newEntry, wbsId: v })} width={100} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Effective Date</div>
                  <Input value={newEntry.effectiveDate} onChange={(v) => setNewEntry({ ...newEntry, effectiveDate: v })} type="date" width={140} />
                </div>
                <button onClick={addCostEntry} disabled={entrySaving || !newEntry.cost}
                  style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "7px 16px", cursor: entrySaving ? "default" : "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit", opacity: entrySaving || !newEntry.cost ? 0.6 : 1 }}>
                  {entrySaving ? "Adding…" : "+ Add"}
                </button>
              </div>
              {entryError && <div style={{ padding: 10, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 14, fontSize: 12 }}>{entryError}</div>}

              {(["APPROVED_BUDGET_CHANGE", "COMMITMENT", "ORIGINAL_BUDGET_OVERRIDE", "APPROVED_EAC"] as const).map((type) => {
                const rows = costEntries.filter((e) => e.entryType === type);
                if (!rows.length) return null;
                return (
                  <div key={type} style={{ marginBottom: 18 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>{ENTRY_TYPE_LABELS[type]}s</div>
                    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead><tr style={{ background: C.card2 }}>{["Reference", "Description", "Amount", "WBS/Cost Code", "Effective Date", ""].map((h) => <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                        <tbody>{rows.map((e) => (
                          <tr key={e.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                            <td style={{ padding: "7px 12px", fontFamily: "monospace" }}>{e.referenceNumber || "—"}</td>
                            <td style={{ padding: "7px 12px" }}>{e.description || "—"}</td>
                            <td style={{ padding: "7px 12px", fontWeight: 700 }}>{fmt$(e.cost)}</td>
                            <td style={{ padding: "7px 12px", color: C.muted2 }}>{e.wbsId || e.costCode || "—"}</td>
                            <td style={{ padding: "7px 12px", color: C.muted2 }}>{e.effectiveDate || "—"}</td>
                            <td style={{ padding: "7px 12px" }}><button onClick={() => deleteCostEntry(e.id)} style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "3px 8px", cursor: "pointer", fontSize: 11, color: C.red }}>Delete</button></td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </div>
                );
              })}
              {costEntries.length === 0 && (
                <div style={{ padding: 30, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>
                  No manual cost entries yet. Add a budget change, commitment, or approved EAC above — these are facts P6 XER cannot supply, so they're never inferred.
                </div>
              )}
            </div>
          )}

          {subTab === "versions" && (
            <ScheduleVersionsTab
              versions={versions}
              roleChangeError={roleChangeError}
              roleChangeSavingId={roleChangeSavingId}
              onSetRole={patchVersionClassification}
              onDeleteVersion={deleteVersion}
              deletingVersionId={deletingVersionId}
            />
          )}

          {subTab === "reports" && (
            <div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 10 }}>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Report Type</div>
                  <Select value={reportType} onChange={(v) => setReportType(v as any)}
                    options={[{ value: "WEEKLY_PROJECT_CONTROLS", label: "Weekly Project Controls Report" }, { value: "MONTHLY_EXECUTIVE", label: "Monthly Executive Report" }]} />
                </div>
                <button onClick={generateReport} disabled={reportGenerating}
                  style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "8px 18px", cursor: reportGenerating ? "default" : "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit", opacity: reportGenerating ? 0.6 : 1 }}>
                  {reportGenerating ? "Generating…" : "Generate & Save Report"}
                </button>
                <div style={{ fontSize: 11, color: C.muted2 }}>Uses the current Earned Value Method and Group By selected above. A generated report is a frozen snapshot — it won't change if the schedule is re-imported later.</div>
              </div>

              {reportType === "WEEKLY_PROJECT_CONTROLS" && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", flexBasis: "100%" }}>4-Week Look Ahead Options</div>
                  <div>
                    <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Look Ahead Range</div>
                    <Select value={lookaheadWeeks} onChange={setLookaheadWeeks}
                      options={[{ value: "2", label: "2 Weeks" }, { value: "4", label: "4 Weeks" }, { value: "6", label: "6 Weeks" }, { value: "8", label: "8 Weeks" }, { value: "12", label: "12 Weeks" }, { value: "custom", label: "Custom Range" }]} />
                  </div>
                  {lookaheadWeeks === "custom" && (
                    <>
                      <div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>From</div>
                        <Input type="date" value={lookaheadFrom} onChange={setLookaheadFrom} />
                      </div>
                      <div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>To</div>
                        <Input type="date" value={lookaheadTo} onChange={setLookaheadTo} />
                      </div>
                    </>
                  )}
                  <div>
                    <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Scope</div>
                    <Select value={lookaheadDimension} onChange={setLookaheadDimension} placeholder="All Activities"
                      options={["wbs", "area", "discipline", "contractor", "system"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))} />
                  </div>
                  {lookaheadDimension && (
                    <div>
                      <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Value</div>
                      <Input value={lookaheadValue} onChange={setLookaheadValue} placeholder={`e.g. ${lookaheadDimension === "wbs" ? "Area C" : "..."}`} width={150} />
                    </div>
                  )}
                  <div>
                    <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Baseline (optional)</div>
                    <Select value={lookaheadBaselineVersion} onChange={setLookaheadBaselineVersion} placeholder="Auto-detect"
                      options={versions.map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Previous Schedule Version (optional)</div>
                    <Select value={updatePreviousVersion} onChange={setUpdatePreviousVersion} placeholder="Automatic — immediately preceding update"
                      options={versions.filter((v) => v.id !== versionId).map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
                  </div>
                  {updatePreviousVersion && (
                    <div style={{ fontSize: 11, color: C.muted2, flexBasis: "100%" }}>
                      Schedule Update Performance will compare {versions.find((v) => v.id === updatePreviousVersion)?.dataDate || "—"} → {versions.find((v) => v.id === versionId)?.dataDate || "—"}.
                      This is a one-off report comparison — it never changes project roles.
                    </div>
                  )}
                </div>
              )}
              {reportError && <div style={{ padding: 10, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, marginBottom: 14, fontSize: 12 }}>{reportError}</div>}

              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                {/* Saved report history */}
                <div style={{ flex: "1 1 280px", minWidth: 260 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Saved Reports</div>
                  {reportHistory.length === 0 && <div style={{ fontSize: 12, color: C.muted2, padding: 12, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8 }}>No reports generated yet.</div>}
                  {reportHistory.map((r) => (
                    <div key={r.id} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 12px", marginBottom: 8, fontSize: 12 }}>
                      <div style={{ fontWeight: 700, color: C.text }}>{r.reportType === "WEEKLY_PROJECT_CONTROLS" ? "Weekly" : "Monthly"} — {formatDataDate(r.dataDate)}</div>
                      <div style={{ color: C.muted2, fontSize: 11, marginBottom: 6 }}>Generated {new Date(r.generatedAt).toLocaleString()}</div>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button onClick={() => openSavedReport(r.id)} style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "3px 8px", cursor: "pointer", fontSize: 11 }}>Preview</button>
                        <a href={`${API}/api/projects/${projectId}/reports/${r.id}/pdf/`} target="_blank" rel="noreferrer" style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "3px 8px", fontSize: 11, color: C.accent, textDecoration: "none" }}>PDF</a>
                        <a href={`${API}/api/projects/${projectId}/reports/${r.id}/excel/`} target="_blank" rel="noreferrer" style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "3px 8px", fontSize: 11, color: C.green, textDecoration: "none" }}>Excel</a>
                        <button onClick={() => deleteSavedReport(r.id)} style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "3px 8px", cursor: "pointer", fontSize: 11, color: C.red }}>Delete</button>
                      </div>
                    </div>
                  ))}

                  {reportHistory.length >= 2 && (
                    <div style={{ marginTop: 16 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Compare Two Reports</div>
                      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                        <Select value={compareIds.a} onChange={(v) => setCompareIds({ ...compareIds, a: v })} placeholder="Earlier report"
                          options={reportHistory.map((r) => ({ value: r.id, label: `${formatDataDate(r.dataDate)} (${r.reportType === "WEEKLY_PROJECT_CONTROLS" ? "Weekly" : "Monthly"})` }))} />
                        <Select value={compareIds.b} onChange={(v) => setCompareIds({ ...compareIds, b: v })} placeholder="Later report"
                          options={reportHistory.map((r) => ({ value: r.id, label: `${formatDataDate(r.dataDate)} (${r.reportType === "WEEKLY_PROJECT_CONTROLS" ? "Weekly" : "Monthly"})` }))} />
                      </div>
                      <button onClick={runReportComparison} disabled={!compareIds.a || !compareIds.b}
                        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, opacity: !compareIds.a || !compareIds.b ? 0.5 : 1 }}>
                        Compare
                      </button>
                      {compareResult && (
                        <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "auto", marginTop: 10 }}>
                          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                            <thead><tr style={{ background: C.card2 }}>{["KPI", "Earlier", "Later", "Direction"].map((h) => <th key={h} style={{ padding: "6px 10px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                            <tbody>{Object.entries(compareResult.kpiMovement || {}).map(([k, m]: any) => (
                              <tr key={k} style={{ borderBottom: `1px solid ${C.border}` }}>
                                <td style={{ padding: "6px 10px", fontWeight: 600 }}>{k.toUpperCase()}</td>
                                <td style={{ padding: "6px 10px" }}>{m.previous != null ? m.previous : "Unavailable"}</td>
                                <td style={{ padding: "6px 10px" }}>{m.current != null ? m.current : "Unavailable"}</td>
                                <td style={{ padding: "6px 10px", color: idxColor(m.direction === "improving" ? 1 : m.direction === "deteriorating" ? 0 : undefined) }}>{DIRECTION_ARROW[m.direction] || ""} {m.direction}</td>
                              </tr>
                            ))}</tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Preview panel */}
                <div style={{ flex: "2 1 480px", minWidth: 320 }}>
                  {!previewReport && <div style={{ padding: 40, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>Generate a report or click "Preview" on a saved report to view it here.</div>}
                  {previewReport && (() => {
                    const p = previewReport.payload || previewReport;
                    const info = p.projectInfo || {};
                    const sections = (p.executiveSummary?.narrative?.sections) || {};
                    const perf = p.currentPerformance || {};
                    return (
                      <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 18 }}>
                        <div style={{ fontSize: 16, fontWeight: 800, color: C.text }}>{info.projectName}</div>
                        <div style={{ fontSize: 12, color: C.muted2, marginBottom: 14 }}>
                          {p.reportType === "WEEKLY_PROJECT_CONTROLS" ? "Weekly Project Controls Report" : "Monthly Executive Report"} · Data Date: {formatDataDate(info.dataDate)}
                        </div>

                        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Executive Summary</div>
                        <div style={{ fontSize: 13, color: C.text, marginBottom: 4 }}>{sections.overallPerformance || <span style={{ color: C.muted2, fontStyle: "italic" }}>Unavailable</span>}</div>
                        {sections.schedule && <div style={{ fontSize: 13, color: C.text, marginBottom: 4 }}>{sections.schedule}</div>}
                        {sections.forecast && <div style={{ fontSize: 13, color: C.text, marginBottom: 4 }}>{sections.forecast}</div>}
                        {sections.primaryDrivers && <div style={{ fontSize: 13, color: C.text, marginBottom: 4 }}>{sections.primaryDrivers}</div>}
                        {sections.productivity && <div style={{ fontSize: 13, color: C.text, marginBottom: 10 }}>{sections.productivity}</div>}

                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "14px 0" }}>
                          <Metric label="CPI" value={fmtIdx(perf.cpi?.current)} color={idxColor(perf.cpi?.current)} />
                          <Metric label="SPI" value={fmtIdx(perf.spi?.current)} color={idxColor(perf.spi?.current)} />
                          <Metric label="CV" value={fmt$(perf.cv?.current)} />
                          <Metric label="SV" value={fmt$(perf.sv?.current)} />
                          <Metric label="VAC" value={fmt$(perf.vac?.current)} />
                        </div>

                        {p.lookAhead && (() => {
                          const la = p.lookAhead;
                          if (!la.available) {
                            return (
                              <div style={{ margin: "14px 0" }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>4-Week Look Ahead</div>
                                <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic" }}>{la.reason || "Unavailable"}</div>
                              </div>
                            );
                          }
                          const cards = la.summaryCards || {};
                          const win = la.window || {};
                          const showHours = cards.plannedHours != null || cards.forecastHours != null || cards.actualHours != null;
                          return (
                            <div style={{ margin: "14px 0" }}>
                              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>
                                {la.scopeLabel || "4-Week Look Ahead"}
                              </div>
                              <div style={{ fontSize: 12, color: C.muted2, marginBottom: 10 }}>
                                Data Date: <strong style={{ color: C.text }}>{formatDataDate(la.dataDate)}</strong> · Look Ahead: {formatDataDate(win.fromDate)} → {formatDataDate(win.toDate)} · Baseline: {la.baselineVersionLabel || "—"} · Current: {la.currentVersionLabel || "—"}
                              </div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                                <Metric label="In Window" value={String(cards.activitiesInWindow ?? 0)} />
                                <Metric label="Behind Baseline" value={String(cards.behindBaseline ?? 0)} color={cards.behindBaseline > 0 ? C.red : C.green} />
                                <Metric label="Should Start" value={String(cards.shouldHaveStarted ?? 0)} color={cards.shouldHaveStarted > 0 ? C.amber : C.text} />
                                <Metric label="Should Finish" value={String(cards.shouldHaveFinished ?? 0)} color={cards.shouldHaveFinished > 0 ? C.amber : C.text} />
                                <Metric label="In Progress" value={String(cards.inProgress ?? 0)} />
                                <Metric label="Critical" value={String(cards.criticalActivities ?? 0)} />
                                <Metric label="Avg Finish Variance" value={cards.averageFinishVarianceDays != null ? `${cards.averageFinishVarianceDays > 0 ? "+" : ""}${cards.averageFinishVarianceDays} d` : "Unavailable"} />
                                {showHours && <Metric label="Planned Hours" value={fmtH(cards.plannedHours)} />}
                                {showHours && <Metric label="Forecast Hours" value={fmtH(cards.forecastHours)} />}
                                {showHours && <Metric label="Actual Hours" value={fmtH(cards.actualHours)} />}
                              </div>

                              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>
                                Top Delayed Activities {la.delayedActivities?.length > la.topDelayedN ? `(showing top ${la.topDelayedN} of ${la.delayedActivities.length})` : ""}
                              </div>
                              {(!la.delayedActivities || la.delayedActivities.length === 0) && <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic", marginBottom: 10 }}>No delayed activities in the look-ahead window.</div>}
                              {la.delayedActivities && la.delayedActivities.length > 0 && (
                                <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "auto", marginBottom: 12 }}>
                                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                                    <thead><tr style={{ background: C.card2 }}>{["ID", "Name", "WBS", "Baseline Fin.", "Current Fin.", "Variance", "Status"].map((h) => <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                                    <tbody>{la.delayedActivities.slice(0, la.topDelayedN).map((d: any) => (
                                      <tr key={d.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                                        <td style={{ padding: "5px 8px" }}>{d.activityId}</td>
                                        <td style={{ padding: "5px 8px" }}>{d.activityName}</td>
                                        <td style={{ padding: "5px 8px" }}>{d.wbs}</td>
                                        <td style={{ padding: "5px 8px" }}>{d.baselineFinish || "—"}</td>
                                        <td style={{ padding: "5px 8px" }}>{d.currentFinish || "—"}</td>
                                        <td style={{ padding: "5px 8px", color: C.red }}>
                                          {d.workingDayCalendarAvailable && d.finishVarianceWorkingDays != null
                                            ? `+${d.finishVarianceWorkingDays} working d`
                                            : `+${d.finishVarianceDays} calendar d`}
                                        </td>
                                        <td style={{ padding: "5px 8px" }}>{d.status}</td>
                                      </tr>
                                    ))}</tbody>
                                  </table>
                                </div>
                              )}

                              {(la.shouldHaveStarted?.length > 0 || la.shouldHaveFinished?.length > 0) && (
                                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
                                  {la.shouldHaveStarted?.length > 0 && (
                                    <div style={{ flex: "1 1 220px" }}>
                                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Should Have Started ({la.shouldHaveStartedTotalCount})</div>
                                      {la.shouldHaveStarted.map((d: any) => (
                                        <div key={d.activityId} style={{ fontSize: 12, color: C.text, marginBottom: 3 }}>- {d.activityId} {d.activityName} ({d.daysOverdue}d overdue)</div>
                                      ))}
                                    </div>
                                  )}
                                  {la.shouldHaveFinished?.length > 0 && (
                                    <div style={{ flex: "1 1 220px" }}>
                                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Should Have Finished ({la.shouldHaveFinishedTotalCount})</div>
                                      {la.shouldHaveFinished.map((d: any) => (
                                        <div key={d.activityId} style={{ fontSize: 12, color: C.text, marginBottom: 3 }}>- {d.activityId} {d.activityName} ({d.daysOverdue}d overdue)</div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}

                              {la.milestones && la.milestones.length > 0 && (
                                <>
                                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Milestones in Window</div>
                                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "auto", marginBottom: 10 }}>
                                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                                      <thead><tr style={{ background: C.card2 }}>{["Milestone", "Baseline", "Current", "Float", "Flags"].map((h) => <th key={h} style={{ padding: "5px 8px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>)}</tr></thead>
                                      <tbody>{la.milestones.map((m: any) => (
                                        <tr key={m.activityId} style={{ borderBottom: `1px solid ${C.border}` }}>
                                          <td style={{ padding: "5px 8px" }}>{m.activityName}</td>
                                          <td style={{ padding: "5px 8px" }}>{m.baselineFinish || "—"}</td>
                                          <td style={{ padding: "5px 8px" }}>{m.currentFinish || "—"}</td>
                                          <td style={{ padding: "5px 8px" }}>{m.totalFloat ?? "—"}</td>
                                          <td style={{ padding: "5px 8px" }}>
                                            {[m.isDelayed && <span key="d" style={{ color: C.red }}>DELAYED</span>, m.isNegativeFloat && <span key="n" style={{ color: C.red }}>NEG FLOAT</span>, m.isApproaching && <span key="a" style={{ color: C.amber }}>APPROACHING</span>].filter(Boolean).reduce((acc: any[], el, i) => i === 0 ? [el] : [...acc, " / ", el], [] as any[])}
                                            {!m.isDelayed && !m.isNegativeFloat && !m.isApproaching && "—"}
                                          </td>
                                        </tr>
                                      ))}</tbody>
                                    </table>
                                  </div>
                                </>
                              )}
                            </div>
                          );
                        })()}

                        {p.updateComparison?.available && (
                          <>
                            <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Update Changes</div>
                            <div style={{ fontSize: 12, color: C.text, marginBottom: 10 }}>{p.updateComparison.summaryNarrative}</div>
                          </>
                        )}

                        {p.updateIntelligence && (() => {
                          const ui = p.updateIntelligence;
                          if (!ui.available) {
                            return (
                              <div style={{ margin: "14px 0" }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Schedule Update Performance</div>
                                <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic" }}>{ui.reason || "Unavailable"}</div>
                              </div>
                            );
                          }
                          const mc = ui.movementCounts || {};
                          const rel = ui.reliability || {};
                          const cp = ui.criticalPathMovement || {};
                          const fm = ui.floatMovement || {};
                          return (
                            <div style={{ margin: "14px 0" }}>
                              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Schedule Update Performance</div>
                              <div style={{ fontSize: 12, color: C.muted2, marginBottom: 10 }}>
                                Previous: <strong style={{ color: C.text }}>{formatDataDate(ui.previousDataDate)}</strong> → Current: <strong style={{ color: C.text }}>{formatDataDate(ui.currentDataDate)}</strong> · Update Period: {ui.periodDays != null ? `${ui.periodDays} calendar days` : "Unavailable"}
                              </div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
                                <Metric label="Start Reliability" value={rel.startReliabilityPct != null ? `${rel.startReliabilityPct}%` : "Unavailable"} color={rel.startReliabilityPct == null ? undefined : rel.startReliabilityPct >= 90 ? C.green : rel.startReliabilityPct >= 70 ? C.amber : C.red} />
                                <Metric label="Finish Reliability" value={rel.finishReliabilityPct != null ? `${rel.finishReliabilityPct}%` : "Unavailable"} color={rel.finishReliabilityPct == null ? undefined : rel.finishReliabilityPct >= 90 ? C.green : rel.finishReliabilityPct >= 70 ? C.amber : C.red} />
                                <Metric label="Slipped" value={String(mc.slipped ?? 0)} color={mc.slipped > 0 ? C.red : C.text} />
                                <Metric label="Improved" value={String(mc.improved ?? 0)} color={C.green} />
                                <Metric label="Started" value={String(mc.startedThisPeriod ?? 0)} />
                                <Metric label="Completed" value={String(mc.completedThisPeriod ?? 0)} color={C.green} />
                                <Metric label="Missed Starts" value={String((rel.missedStarts || []).length)} color={(rel.missedStarts || []).length > 0 ? C.red : C.text} />
                                <Metric label="Missed Finishes" value={String((rel.missedFinishes || []).length)} color={(rel.missedFinishes || []).length > 0 ? C.red : C.text} />
                                <Metric label="Newly Critical" value={String(cp.becameCriticalCount ?? 0)} color={cp.becameCriticalCount > 0 ? C.red : C.text} />
                                <Metric label="Newly Negative Float" value={String(fm.newlyNegativeFloatCount ?? 0)} color={fm.newlyNegativeFloatCount > 0 ? C.red : C.text} />
                              </div>
                              {(ui.narrative?.overview || ui.narrative?.criticalPath) && (
                                <div style={{ fontSize: 12, color: C.text, marginBottom: 4 }}>
                                  {ui.narrative.overview}{ui.narrative.criticalPath ? ` ${ui.narrative.criticalPath}` : ""}
                                </div>
                              )}
                            </div>
                          );
                        })()}

                        {p.scheduleRiskRecovery && (() => {
                          const srr = p.scheduleRiskRecovery;
                          if (!srr.available) {
                            return (
                              <div style={{ margin: "14px 0" }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Schedule Risk & Recovery</div>
                                <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic" }}>{srr.reason || "Unavailable"}</div>
                              </div>
                            );
                          }
                          const sum = srr.summary || {};
                          const bySev = sum.bySeverity || {};
                          const accepted = (srr.acceptedScenarios || []).length;
                          return (
                            <div style={{ margin: "14px 0" }}>
                              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Schedule Risk & Recovery</div>
                              <div style={{ fontSize: 12, color: C.muted2, marginBottom: 10 }}>Data Date: <strong style={{ color: C.text }}>{formatDataDate(srr.currentDataDate)}</strong></div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
                                <Metric label="Critical Risks" value={String(bySev.CRITICAL ?? 0)} color={(bySev.CRITICAL ?? 0) > 0 ? C.red : C.text} />
                                <Metric label="High Risks" value={String(bySev.HIGH ?? 0)} color={(bySev.HIGH ?? 0) > 0 ? C.orange : C.text} />
                                <Metric label="Driving" value={String(sum.drivingRiskCount ?? 0)} />
                                <Metric label="Milestone-Exposed" value={String(sum.milestoneExposureCount ?? 0)} />
                                <Metric label="Accepted Scenarios" value={String(accepted)} color={C.green} />
                                <Metric label="Open Mitigation Actions" value={String(srr.openMitigationActionCount ?? 0)} />
                                <Metric label="Overdue Actions" value={String(srr.overdueMitigationActionCount ?? 0)} color={(srr.overdueMitigationActionCount ?? 0) > 0 ? C.red : C.text} />
                              </div>
                            </div>
                          );
                        })()}

                        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Management Attention</div>
                        {(p.managementAttention || []).length === 0 && <div style={{ fontSize: 12, color: C.muted2, fontStyle: "italic", marginBottom: 10 }}>No items identified from available data.</div>}
                        {(p.managementAttention || []).map((a: any, i: number) => (
                          <div key={i} style={{ fontSize: 12, color: C.text, marginBottom: 4 }}>- {a.action}</div>
                        ))}

                        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", margin: "14px 0 6px" }}>Data Limitations</div>
                        {(p.dataQuality || []).map((d: string, i: number) => (
                          <div key={i} style={{ fontSize: 11, color: C.muted2, marginBottom: 3 }}>- {d}</div>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
