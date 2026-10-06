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

const SEV_COLOR: Record<string, string> = { CRITICAL: C.red, HIGH: C.orange, MEDIUM: C.amber, LOW: C.muted2 };
const STATUS_COLOR: Record<string, string> = { OPEN: C.red, MONITORING: C.amber, MITIGATING: C.accent, RESOLVED: C.green, CLOSED: C.muted2 };

const CATEGORY_LABELS: Record<string, string> = {
  ENGINEERING: "Engineering", PROCUREMENT: "Procurement", CONSTRUCTION: "Construction",
  PREFABRICATION: "Prefabrication", EQUIPMENT_DELIVERY: "Equipment Delivery", QA_QC: "QA/QC",
  COMMISSIONING: "Commissioning", PRODUCTIVITY: "Productivity", MANPOWER: "Manpower",
  DESIGN_COORDINATION: "Design/Coordination", OWNER_GC: "Owner/GC", OTHER: "Other",
};
const STATUS_LABELS: Record<string, string> = {
  OPEN: "Open", MONITORING: "Monitoring", MITIGATING: "Mitigating", RESOLVED: "Resolved", CLOSED: "Closed",
};
const SEVERITY_LABELS: Record<string, string> = { LOW: "Low", MEDIUM: "Medium", HIGH: "High", CRITICAL: "Critical" };

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

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 130 }}>
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

const fieldStyle = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 9px", fontSize: 12, fontFamily: "inherit", color: C.text };

// ─── summary cards ──────────────────────────────────────────────────────────

function SummaryCards({ summary }: { summary: any }) {
  if (!summary) return null;
  const cards = [
    { key: "openCount", label: "Open Issues", color: C.red },
    { key: "criticalCount", label: "Critical Issues", color: C.red },
    { key: "highCount", label: "High Severity", color: C.orange },
    { key: "overdueCount", label: "Overdue Actions", color: C.amber },
    { key: "contractualMilestoneExposureCount", label: "Affecting Contractual Milestones", color: C.purple },
    { key: "negativeFloatExposureCount", label: "Linked to Negative-Float Activities", color: C.red },
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 10, marginBottom: 14 }}>
      {cards.map(c => (
        <div key={c.key} style={{ background: `${c.color}10`, border: `1px solid ${c.color}40`, borderRadius: 10, padding: "10px 14px" }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: c.color }}>{summary[c.key] ?? 0}</div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.03em" }}>{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ─── schedule exposure ──────────────────────────────────────────────────────

function ExposurePanel({ exposure }: { exposure: any }) {
  if (!exposure) return null;
  if (!exposure.hasLinkedActivities) {
    return <div style={{ fontSize: 12, color: C.muted2, padding: "8px 0" }}>{exposure.reason || "No P6 activities linked to this issue."}</div>;
  }
  return (
    <div>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>
        Schedule exposure as of {exposure.currentDataDate ? formatDataDate(exposure.currentDataDate) : "—"} (recomputed live, never stored)
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
          <th style={{ padding: "4px 6px" }}>Activity</th>
          <th style={{ padding: "4px 6px" }}>Status</th>
          <th style={{ padding: "4px 6px", textAlign: "right" }}>Total Float</th>
          <th style={{ padding: "4px 6px" }}>Condition</th>
          <th style={{ padding: "4px 6px" }}>Driving Path</th>
          <th style={{ padding: "4px 6px" }}>Reaches Contractual Milestone</th>
        </tr></thead>
        <tbody>
          {exposure.activities.map((a: any, i: number) => (
            <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
              {!a.available ? (
                <td colSpan={6} style={{ padding: "6px", color: C.muted2 }}>
                  <strong style={{ color: C.text }}>{a.activityId}</strong> — {a.reason}
                </td>
              ) : (
                <>
                  <td style={{ padding: "6px", color: C.text, fontWeight: 600 }}>
                    {a.activityId}
                    {a.activityName && <div style={{ fontSize: 10, color: C.muted2 }}>{a.activityName}</div>}
                  </td>
                  <td style={{ padding: "6px", color: C.muted2 }}>{a.status || "—"}</td>
                  <td style={{ padding: "6px", textAlign: "right", color: a.floatCondition === "NEGATIVE" ? C.red : a.floatCondition === "ZERO" ? C.amber : C.text }}>
                    {a.totalFloat == null ? "—" : `${a.totalFloat}d`}
                  </td>
                  <td style={{ padding: "6px" }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: a.floatCondition === "NEGATIVE" ? C.red : a.floatCondition === "ZERO" ? C.amber : a.floatCondition === "NEAR_CRITICAL" ? C.gold : C.muted2 }}>
                      {a.floatCondition}
                    </span>
                  </td>
                  <td style={{ padding: "6px", color: a.onDrivingPath ? C.orange : C.muted2, fontWeight: a.onDrivingPath ? 700 : 400 }}>{a.onDrivingPath ? "Yes" : "No"}</td>
                  <td style={{ padding: "6px", color: a.reachesContractualMilestoneActivityIds?.length ? C.purple : C.muted2, fontWeight: a.reachesContractualMilestoneActivityIds?.length ? 700 : 400 }}>
                    {a.reachesContractualMilestoneActivityIds?.length ? a.reachesContractualMilestoneActivityIds.join(", ") : "No"}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── add / edit form ────────────────────────────────────────────────────────

const EMPTY_FORM = {
  title: "", description: "", category: "OTHER", severity: "MEDIUM", status: "OPEN",
  owner: "", requiredAction: "", targetResolutionDate: "", affectedArea: "",
  linkedActivityIds: "", linkedMilestoneId: "", scheduleImpact: false, costImpact: false, sourceReference: "", notes: "",
};

function IssueForm({ projectId, issue, categories, statuses, severities, milestones, onSaved, onCancel }: {
  projectId: string; issue?: any; categories: string[]; statuses: string[]; severities: string[]; milestones: any[];
  onSaved: () => void; onCancel: () => void;
}) {
  const [form, setForm] = useState(() => issue ? {
    title: issue.title || "", description: issue.description || "", category: issue.category || "OTHER",
    severity: issue.severity || "MEDIUM", status: issue.status || "OPEN", owner: issue.owner || "",
    requiredAction: issue.requiredAction || "", targetResolutionDate: issue.targetResolutionDate || "",
    affectedArea: issue.affectedArea || "", linkedActivityIds: (issue.linkedActivityIds || []).join(", "),
    linkedMilestoneId: issue.linkedMilestoneId || "",
    scheduleImpact: !!issue.scheduleImpact, costImpact: !!issue.costImpact,
    sourceReference: issue.sourceReference || "", notes: issue.notes || "",
  } : EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = () => {
    if (!form.title.trim()) { setErr("Title is required."); return; }
    setSaving(true); setErr(null);
    const body = {
      ...form,
      linkedActivityIds: form.linkedActivityIds.split(",").map(s => s.trim()).filter(Boolean),
      targetResolutionDate: form.targetResolutionDate || null,
    };
    const url = issue ? `${API}/api/projects/${projectId}/issues/${issue.id}/` : `${API}/api/projects/${projectId}/issues/`;
    fetch(url, { method: issue ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(() => onSaved())
      .catch((e: any) => setErr(e.message || String(e)))
      .finally(() => setSaving(false));
  };

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 14, marginBottom: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8, marginBottom: 8 }}>
        <input placeholder="Title *" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} style={{ ...fieldStyle, gridColumn: "span 2" }} />
        <Select value={form.category} onChange={(v: string) => setForm(f => ({ ...f, category: v }))} options={categories.map(c => ({ value: c, label: CATEGORY_LABELS[c] || c }))} />
        <Select value={form.severity} onChange={(v: string) => setForm(f => ({ ...f, severity: v }))} options={severities.map(s => ({ value: s, label: SEVERITY_LABELS[s] || s }))} />
        {issue && <Select value={form.status} onChange={(v: string) => setForm(f => ({ ...f, status: v }))} options={statuses.map(s => ({ value: s, label: STATUS_LABELS[s] || s }))} />}
        <input placeholder="Owner" value={form.owner} onChange={e => setForm(f => ({ ...f, owner: e.target.value }))} style={fieldStyle} />
        <input placeholder="Affected Area / WBS" value={form.affectedArea} onChange={e => setForm(f => ({ ...f, affectedArea: e.target.value }))} style={fieldStyle} />
        <input type="date" placeholder="Target resolution date" value={form.targetResolutionDate} onChange={e => setForm(f => ({ ...f, targetResolutionDate: e.target.value }))} style={fieldStyle} />
        <input placeholder="Linked P6 Activity ID(s), comma-separated" value={form.linkedActivityIds} onChange={e => setForm(f => ({ ...f, linkedActivityIds: e.target.value }))} style={{ ...fieldStyle, gridColumn: "span 2" }} />
        <Select value={form.linkedMilestoneId} onChange={(v: string) => setForm(f => ({ ...f, linkedMilestoneId: v }))}
          placeholder="No linked contractual milestone"
          options={milestones.map((m: any) => ({ value: m.id, label: `${m.activityId} — ${m.activityName || m.description || "Milestone"}` }))} />
        <input placeholder="Source / reference" value={form.sourceReference} onChange={e => setForm(f => ({ ...f, sourceReference: e.target.value }))} style={fieldStyle} />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.muted }}>
          <input type="checkbox" checked={form.scheduleImpact} onChange={e => setForm(f => ({ ...f, scheduleImpact: e.target.checked }))} /> Schedule impact
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.muted }}>
          <input type="checkbox" checked={form.costImpact} onChange={e => setForm(f => ({ ...f, costImpact: e.target.checked }))} /> Cost impact
        </label>
      </div>
      <textarea placeholder="Description" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
        style={{ ...fieldStyle, width: "100%", minHeight: 50, marginBottom: 8, resize: "vertical" }} />
      <textarea placeholder="Required action" value={form.requiredAction} onChange={e => setForm(f => ({ ...f, requiredAction: e.target.value }))}
        style={{ ...fieldStyle, width: "100%", minHeight: 40, marginBottom: 8, resize: "vertical" }} />
      <textarea placeholder="Notes" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
        style={{ ...fieldStyle, width: "100%", minHeight: 40, marginBottom: 8, resize: "vertical" }} />
      {err && <div style={{ color: C.red, fontSize: 11, marginBottom: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={save} disabled={saving} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "6px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
          {saving ? "Saving…" : issue ? "Save Changes" : "Create Issue"}
        </button>
        <button onClick={onCancel} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "6px 14px", cursor: "pointer", fontSize: 12, fontFamily: "inherit" }}>Cancel</button>
      </div>
    </div>
  );
}

// ─── detail drawer ──────────────────────────────────────────────────────────

// ─── mitigation actions (reuses the existing Risk & Recovery action model/API) ─

const MITIGATION_STATUS_COLOR: Record<string, string> = { OPEN: C.muted2, IN_PROGRESS: C.accent, BLOCKED: C.red, COMPLETE: C.green, CANCELLED: C.muted };

function MitigationActionsPanel({ projectId, issueId }: { projectId: string; issueId: string }) {
  const [actions, setActions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ description: "", owner: "", dueDate: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetch(`${API}/api/projects/${projectId}/mitigation-actions/?issueId=${issueId}`)
      .then(r => r.json()).then(d => setActions(d.actions || [])).finally(() => setLoading(false));
  };
  useEffect(load, [projectId, issueId]);

  const save = () => {
    if (!form.description.trim()) { setErr("Description is required."); return; }
    setSaving(true); setErr(null);
    fetch(`${API}/api/projects/${projectId}/mitigation-actions/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, issueId }),
    })
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(() => { setForm({ description: "", owner: "", dueDate: "" }); setShowAdd(false); load(); })
      .catch((e: any) => setErr(e.message || String(e)))
      .finally(() => setSaving(false));
  };

  const setStatus = (actionId: string, status: string) => {
    fetch(`${API}/api/projects/${projectId}/mitigation-actions/${actionId}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    }).then(load);
  };

  return (
    <Panel title="Mitigation Actions" action={
      <button onClick={() => setShowAdd(s => !s)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>
        + Add Action
      </button>
    }>
      {showAdd && (
        <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, marginBottom: 10 }}>
          <input placeholder="What needs to happen?" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            style={{ ...fieldStyle, width: "100%", marginBottom: 6 }} />
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <input placeholder="Owner" value={form.owner} onChange={e => setForm(f => ({ ...f, owner: e.target.value }))} style={{ ...fieldStyle, flex: 1 }} />
            <input type="date" value={form.dueDate} onChange={e => setForm(f => ({ ...f, dueDate: e.target.value }))} style={fieldStyle} />
          </div>
          {err && <div style={{ color: C.red, fontSize: 11, marginBottom: 6 }}>{err}</div>}
          <button onClick={save} disabled={saving} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700, fontFamily: "inherit" }}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
      {loading ? (
        <div style={{ color: C.muted2, fontSize: 12 }}>Loading…</div>
      ) : actions.length === 0 ? (
        <div style={{ color: C.muted2, fontSize: 12 }}>No mitigation actions recorded for this issue yet.</div>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", fontSize: 12 }}>
          {actions.map((a: any) => (
            <li key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderTop: `1px solid ${C.border}` }}>
              <span style={{ background: `${MITIGATION_STATUS_COLOR[a.status]}18`, color: MITIGATION_STATUS_COLOR[a.status], borderRadius: 5, padding: "2px 7px", fontSize: 9, fontWeight: 700 }}>{a.status}</span>
              <div style={{ flex: 1 }}>
                <div style={{ color: C.text }}>{a.description}</div>
                <div style={{ color: C.muted2, fontSize: 10 }}>{a.owner || "Unassigned"}{a.dueDate ? ` · Due ${formatDataDate(a.dueDate)}` : ""}</div>
              </div>
              {a.status !== "COMPLETE" && a.status !== "CANCELLED" && (
                <button onClick={() => setStatus(a.id, "COMPLETE")} style={{ background: "transparent", border: "none", color: C.green, cursor: "pointer", fontSize: 10, fontWeight: 700 }}>Mark Complete</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function IssueDetailDrawer({ projectId, issue, categories, statuses, severities, milestones, onClose, onChanged }: {
  projectId: string; issue: any; categories: string[]; statuses: string[]; severities: string[]; milestones: any[];
  onClose: () => void; onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const setStatus = (status: string) => {
    setBusy(true);
    fetch(`${API}/api/projects/${projectId}/issues/${issue.id}/`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    }).then(() => { onChanged(); }).finally(() => setBusy(false));
  };

  const remove = () => {
    if (!window.confirm(`Delete ${issue.reference} — "${issue.title}"? This cannot be undone.`)) return;
    fetch(`${API}/api/projects/${projectId}/issues/${issue.id}/`, { method: "DELETE" }).then(() => { onClose(); onChanged(); });
  };

  if (editing) {
    return (
      <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 520, background: C.bg, borderLeft: `1px solid ${C.border}`, padding: 18, overflowY: "auto", zIndex: 200, boxShadow: "-6px 0 18px rgba(0,0,0,0.12)" }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>Edit {issue.reference}</div>
          <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 16 }}>✕</button>
        </div>
        <IssueForm projectId={projectId} issue={issue} categories={categories} statuses={statuses} severities={severities} milestones={milestones}
          onSaved={() => { setEditing(false); onChanged(); }} onCancel={() => setEditing(false)} />
      </div>
    );
  }

  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 520, background: C.bg, borderLeft: `1px solid ${C.border}`, padding: 18, overflowY: "auto", zIndex: 200, boxShadow: "-6px 0 18px rgba(0,0,0,0.12)" }}>
      <div style={{ display: "flex", alignItems: "flex-start", marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: C.muted2, fontWeight: 700 }}>{issue.reference}</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: C.text }}>{issue.title}</div>
        </div>
        <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 16 }}>✕</button>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ background: `${SEV_COLOR[issue.severity]}18`, color: SEV_COLOR[issue.severity], borderRadius: 5, padding: "3px 10px", fontSize: 11, fontWeight: 700 }}>{SEVERITY_LABELS[issue.severity] || issue.severity}</span>
        <span style={{ background: `${STATUS_COLOR[issue.status]}18`, color: STATUS_COLOR[issue.status], borderRadius: 5, padding: "3px 10px", fontSize: 11, fontWeight: 700 }}>{STATUS_LABELS[issue.status] || issue.status}</span>
        <span style={{ background: C.card2, color: C.muted, borderRadius: 5, padding: "3px 10px", fontSize: 11, fontWeight: 700 }}>{CATEGORY_LABELS[issue.category] || issue.category}</span>
        {issue.overdue && <span style={{ background: `${C.red}18`, color: C.red, borderRadius: 5, padding: "3px 10px", fontSize: 11, fontWeight: 700 }}>OVERDUE</span>}
      </div>

      {issue.description && <div style={{ fontSize: 12, color: C.text, marginBottom: 10, whiteSpace: "pre-wrap" }}>{issue.description}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 12, marginBottom: 12 }}>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Owner</div><div style={{ color: C.text }}>{issue.owner || "—"}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Affected Area</div><div style={{ color: C.text }}>{issue.affectedArea || "—"}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Date Identified</div><div style={{ color: C.text }}>{formatDataDate(issue.dateIdentified)}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Target Resolution</div><div style={{ color: issue.overdue ? C.red : C.text }}>{issue.targetResolutionDate ? formatDataDate(issue.targetResolutionDate) : "—"}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Actual Resolution</div><div style={{ color: C.text }}>{issue.actualResolutionDate ? formatDataDate(issue.actualResolutionDate) : "—"}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Schedule / Cost Impact</div><div style={{ color: C.text }}>{issue.scheduleImpact ? "Schedule " : ""}{issue.costImpact ? "Cost" : ""}{!issue.scheduleImpact && !issue.costImpact && "—"}</div></div>
        <div>
          <div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase" }}>Linked Contractual Milestone</div>
          <div style={{ color: C.text }}>
            {!issue.linkedMilestoneId ? "—" : (() => {
              const m = milestones.find((x: any) => x.id === issue.linkedMilestoneId);
              return m ? `${m.activityId} — ${m.activityName || m.description || "Milestone"}` : "Not Available";
            })()}
          </div>
        </div>
      </div>

      {issue.requiredAction && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase", marginBottom: 2 }}>Required Action</div>
          <div style={{ fontSize: 12, color: C.text, whiteSpace: "pre-wrap" }}>{issue.requiredAction}</div>
        </div>
      )}
      {issue.notes && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: C.muted, fontSize: 10, textTransform: "uppercase", marginBottom: 2 }}>Notes</div>
          <div style={{ fontSize: 12, color: C.text, whiteSpace: "pre-wrap" }}>{issue.notes}</div>
        </div>
      )}
      {issue.sourceReference && (
        <div style={{ marginBottom: 12, fontSize: 11, color: C.muted2 }}>Source: {issue.sourceReference}</div>
      )}

      <Panel title="Schedule Exposure (live, recomputed every load)">
        <ExposurePanel exposure={issue.exposure} />
      </Panel>

      <MitigationActionsPanel projectId={projectId} issueId={issue.id} />

      <div style={{ fontSize: 10, color: C.muted2, marginBottom: 14 }}>
        Created by {issue.createdBy || "—"} · {issue.createdAt ? new Date(issue.createdAt).toLocaleString() : "—"}
        {issue.updatedBy && <> · Last updated by {issue.updatedBy}</>}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={() => setEditing(true)} style={{ background: C.accent, color: "#fff", border: "none", borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>Edit</button>
        {issue.status !== "RESOLVED" && issue.status !== "CLOSED" && (
          <button onClick={() => setStatus("RESOLVED")} disabled={busy} style={{ background: C.green, color: "#fff", border: "none", borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>Resolve</button>
        )}
        {issue.status !== "CLOSED" && (
          <button onClick={() => setStatus("CLOSED")} disabled={busy} style={{ background: C.muted, color: "#fff", border: "none", borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>Close</button>
        )}
        {(issue.status === "RESOLVED" || issue.status === "CLOSED") && (
          <button onClick={() => setStatus("MONITORING")} disabled={busy} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontFamily: "inherit" }}>Reopen</button>
        )}
        <button onClick={remove} style={{ background: "transparent", border: `1px solid ${C.red}`, color: C.red, borderRadius: 6, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontFamily: "inherit", marginLeft: "auto" }}>Delete</button>
      </div>
    </div>
  );
}

// ─── root ───────────────────────────────────────────────────────────────────

function useIssues(ctx: ReturnType<typeof useProjectsAndVersions>, filters: Record<string, string>) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!ctx.projectId) return;
    setLoading(true); setError(null);
    const params = new URLSearchParams();
    if (ctx.versionId) params.set("currentVersion", ctx.versionId);
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    sfetch(`${API}/api/projects/${ctx.projectId}/issues/?${params.toString()}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(load, [ctx.projectId, ctx.versionId, JSON.stringify(filters)]);

  return { data, loading, error, reload: load };
}

function useContractualMilestones(projectId: string) {
  const [milestones, setMilestones] = useState<any[]>([]);
  useEffect(() => {
    if (!projectId) { setMilestones([]); return; }
    sfetch(`${API}/api/projects/${projectId}/contractual-milestones/`).then(r => r.json())
      .then(d => setMilestones(d.milestones || [])).catch(() => setMilestones([]));
  }, [projectId]);
  return milestones;
}

export default function IssueRegister({ initialProjectId, initialVersionId }: {
  initialProjectId?: string; initialVersionId?: string;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [owner, setOwner] = useState("");
  const [area, setArea] = useState("");
  const [scheduleExposure, setScheduleExposure] = useState("");
  const [milestoneImpact, setMilestoneImpact] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const milestones = useContractualMilestones(ctx.projectId);

  const filters = { status, severity, category, owner, area, scheduleExposure, milestoneImpact };
  const { data, loading, error, reload } = useIssues(ctx, filters);

  const issues: any[] = data?.issues || [];
  const filtered = search
    ? issues.filter(i => i.title.toLowerCase().includes(search.toLowerCase()) || i.reference.toLowerCase().includes(search.toLowerCase()))
    : issues;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", marginBottom: 6, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 900, color: C.text, letterSpacing: "-0.01em" }}>ScheduleIQ | Project Issue Register</div>
          <div style={{ fontSize: 12, color: C.muted, fontWeight: 600 }}>
            Conditions and events that have already occurred or currently exist — a controlled PM record, never auto-created from schedule analysis.
          </div>
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

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && !data && <div style={{ color: C.accent, padding: 20 }}>Loading Issue Register…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 20 }}>{error}</div>}

      {data && ctx.projectId && (
        <>
          <SummaryCards summary={data.summary} />

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search title or reference…"
              style={{ ...fieldStyle, width: 200 }} />
            <Select value={status} onChange={setStatus} placeholder="All statuses" options={data.statuses.map((s: string) => ({ value: s, label: STATUS_LABELS[s] || s }))} />
            <Select value={severity} onChange={setSeverity} placeholder="All severities" options={data.severities.map((s: string) => ({ value: s, label: SEVERITY_LABELS[s] || s }))} />
            <Select value={category} onChange={setCategory} placeholder="All categories" options={data.categories.map((c: string) => ({ value: c, label: CATEGORY_LABELS[c] || c }))} />
            <input value={owner} onChange={e => setOwner(e.target.value)} placeholder="Owner" style={{ ...fieldStyle, width: 120 }} />
            <input value={area} onChange={e => setArea(e.target.value)} placeholder="Area / WBS" style={{ ...fieldStyle, width: 120 }} />
            <Select value={scheduleExposure} onChange={setScheduleExposure} placeholder="Any schedule exposure"
              options={[{ value: "negativeFloat", label: "Negative Float" }, { value: "drivingPath", label: "Driving Path" }, { value: "any", label: "Negative Float or Driving Path" }]} />
            <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: C.muted }}>
              <input type="checkbox" checked={milestoneImpact === "true"} onChange={e => setMilestoneImpact(e.target.checked ? "true" : "")} /> Contractual milestone impact
            </label>
            <button onClick={() => setShowAdd(s => !s)} style={{ marginLeft: "auto", background: C.accent, color: "#fff", border: "none", borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
              + Add Issue
            </button>
          </div>

          {showAdd && (
            <IssueForm projectId={ctx.projectId} categories={data.categories} statuses={data.statuses} severities={data.severities} milestones={milestones}
              onSaved={() => { setShowAdd(false); reload(); }} onCancel={() => setShowAdd(false)} />
          )}

          <Panel title={`Issues (${filtered.length} of ${issues.length})`}>
            {filtered.length === 0 ? (
              <div style={{ color: C.muted2, fontSize: 12, padding: "14px 4px" }}>No issues match the current filters.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead><tr style={{ textAlign: "left", color: C.muted, fontSize: 10, textTransform: "uppercase" }}>
                  <th style={{ padding: "4px 6px" }}>Ref</th>
                  <th style={{ padding: "4px 6px" }}>Title</th>
                  <th style={{ padding: "4px 6px" }}>Category</th>
                  <th style={{ padding: "4px 6px" }}>Severity</th>
                  <th style={{ padding: "4px 6px" }}>Status</th>
                  <th style={{ padding: "4px 6px" }}>Owner</th>
                  <th style={{ padding: "4px 6px" }}>Target</th>
                  <th style={{ padding: "4px 6px" }}>Exposure</th>
                </tr></thead>
                <tbody>
                  {filtered.map(it => (
                    <tr key={it.id} onClick={() => setSelected(it)} style={{ borderTop: `1px solid ${C.border}`, cursor: "pointer" }}>
                      <td style={{ padding: "6px", fontFamily: "monospace", color: C.muted2 }}>{it.reference}</td>
                      <td style={{ padding: "6px", color: C.text, fontWeight: 600 }}>{it.title}</td>
                      <td style={{ padding: "6px", color: C.muted2 }}>{CATEGORY_LABELS[it.category] || it.category}</td>
                      <td style={{ padding: "6px" }}>
                        <span style={{ background: `${SEV_COLOR[it.severity]}18`, color: SEV_COLOR[it.severity], borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>{SEVERITY_LABELS[it.severity] || it.severity}</span>
                      </td>
                      <td style={{ padding: "6px" }}>
                        <span style={{ background: `${STATUS_COLOR[it.status]}18`, color: STATUS_COLOR[it.status], borderRadius: 5, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>{STATUS_LABELS[it.status] || it.status}</span>
                      </td>
                      <td style={{ padding: "6px", color: C.text }}>{it.owner || "—"}</td>
                      <td style={{ padding: "6px", color: it.overdue ? C.red : C.text, fontWeight: it.overdue ? 700 : 400 }}>
                        {it.targetResolutionDate ? formatDataDate(it.targetResolutionDate) : "—"}{it.overdue ? " (Overdue)" : ""}
                      </td>
                      <td style={{ padding: "6px" }}>
                        {it.exposure?.anyNegativeFloat && <span style={{ color: C.red, fontSize: 10, fontWeight: 700, marginRight: 6 }}>NEG FLOAT</span>}
                        {it.exposure?.anyOnDrivingPath && <span style={{ color: C.orange, fontSize: 10, fontWeight: 700, marginRight: 6 }}>DRIVING</span>}
                        {it.exposure?.anyReachesContractualMilestone && <span style={{ color: C.purple, fontSize: 10, fontWeight: 700 }}>MILESTONE</span>}
                        {!it.exposure?.hasLinkedActivities && <span style={{ color: C.muted2, fontSize: 10 }}>—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      )}

      {selected && (
        <IssueDetailDrawer projectId={ctx.projectId} issue={selected} categories={data?.categories || []} statuses={data?.statuses || []} severities={data?.severities || []} milestones={milestones}
          onClose={() => setSelected(null)} onChanged={() => { reload(); setSelected(null); }} />
      )}
    </div>
  );
}
