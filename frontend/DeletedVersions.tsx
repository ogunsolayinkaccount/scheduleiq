import { useEffect, useState } from "react";
import { notifyProjectsChanged, useProjectsChangedTick } from "./projectEvents";
import { formatDataDate } from "./dateFormat";
import { parseErrorMessage, restoreConfirmationBody, daysRemaining } from "./importProtection";

// ─── THEME (mirrors App.tsx's palette so this reads as the same product) ─────
const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

type AuditEntry = {
  timestamp: string; action: string; outcome: string;
  user: string | null; reason: string | null; requestId: string | null;
};

type DeletedVersion = {
  id: string; versionLabel: string; originalFilename: string; dataDate: string | null;
  activityCount: number | null; schedule_classification: string | null;
  deletedAt: string | null; deletedBy: string | null; deleteReason: string | null;
  retentionDays: number; retentionDeadline: string | null;
  auditHistory: AuditEntry[];
};

type ProjectOption = { id: string; name: string; projectNumber?: string; versionCount: number };

// Deliberately NOT filtered to withVersions=true (the Intelligence-view
// convention) — a project whose every version was soft-deleted must still
// be selectable here, otherwise its deleted versions become unreachable.
function useAllProjects() {
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const tick = useProjectsChangedTick();
  useEffect(() => {
    fetch(`${API}/api/projects/`).then((r) => r.json()).then((d) => setProjects(d.projects || [])).catch(() => {});
  }, [tick]);
  return projects;
}

const OUTCOME_COLOR: Record<string, string> = {
  SUCCESS: C.green, REFUSED: C.amber, FAILED: C.red, UNAUTHORIZED: C.red,
};

function AuditHistoryList({ entries }: { entries: AuditEntry[] }) {
  if (!entries.length) return <div style={{ fontSize: 11, color: C.muted2 }}>No audit record found.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {entries.map((a, i) => (
        <div key={i} style={{ fontSize: 11, color: C.text, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
          <span style={{ color: C.muted2, whiteSpace: "nowrap" }}>{a.timestamp ? a.timestamp.replace("T", " ").slice(0, 19) : "—"}</span>
          <span style={{ fontWeight: 700 }}>{a.action}</span>
          <span style={{ fontWeight: 800, color: OUTCOME_COLOR[a.outcome] || C.muted2 }}>{a.outcome}</span>
          {/* The acting user — ScheduleIQ has no authentication system today,
              so this is whatever (if anything) the caller supplied; never
              fabricated as "Unknown" or a guessed identity. */}
          <span style={{ color: C.muted }}>{a.user ? `by ${a.user}` : "acting user not recorded"}</span>
          {a.reason && <span style={{ color: C.muted }}>— {a.reason}</span>}
        </div>
      ))}
    </div>
  );
}

function DeletedVersionCard({ projectId, version, onRestored }: {
  projectId: string; version: DeletedVersion; onRestored: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAudit, setShowAudit] = useState(false);
  const remaining = daysRemaining(version.retentionDeadline);

  const restore = () => {
    setRestoring(true); setError(null);
    fetch(`${API}/api/projects/${projectId}/versions/${version.id}/restore/`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: restoreConfirmationBody(),
    })
      .then(async (r) => { const t = await r.text(); if (!r.ok) throw new Error(parseErrorMessage(t)); return JSON.parse(t); })
      .then(() => { setArmed(false); notifyProjectsChanged(); onRestored(); })
      .catch((e: any) => setError(e.message || String(e)))
      .finally(() => setRestoring(false));
  };

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 10 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 260px" }}>
          <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>{version.versionLabel || version.originalFilename}</div>
          <div style={{ fontSize: 11, color: C.muted2 }}>{version.originalFilename}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Effective Data Date</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{formatDataDate(version.dataDate)}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Activities</div>
          <div style={{ fontSize: 13, color: C.text }}>{version.activityCount?.toLocaleString() ?? "—"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Classification</div>
          <div style={{ fontSize: 13, color: C.text }}>{version.schedule_classification || "—"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Deleted</div>
          <div style={{ fontSize: 13, color: C.text }}>{version.deletedAt ? version.deletedAt.replace("T", " ").slice(0, 19) : "—"}</div>
          {/* Acting user — never fabricated; blank is expected since
              ScheduleIQ has no authentication system today. */}
          <div style={{ fontSize: 11, color: C.muted2 }}>{version.deletedBy ? `by ${version.deletedBy}` : "Acting user not recorded"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Retention</div>
          <div style={{ fontSize: 13, color: remaining == null ? C.red : C.text }}>
            {remaining == null ? "Retention period has passed" : `${remaining} day${remaining === 1 ? "" : "s"} left`}
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "flex-start", gap: 8 }}>
          {armed ? (
            <span style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", maxWidth: 320 }}>
              <span style={{ fontSize: 11, color: C.green }}>
                Restore <strong>{version.versionLabel || version.originalFilename}</strong> (Data Date: {formatDataDate(version.dataDate)})? Roles (CURRENT/PREVIOUS/BASELINE) recalculate automatically; nothing existing is overwritten.
              </span>
              <button disabled={restoring} onClick={restore}
                style={{ background: C.green, border: "none", color: "#fff", borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
                {restoring ? "Restoring…" : "Confirm restore"}
              </button>
              <button onClick={() => setArmed(false)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11 }}>Cancel</button>
            </span>
          ) : (
            <button onClick={() => setArmed(true)}
              style={{ background: "transparent", border: `1px solid ${C.green}`, color: C.green, borderRadius: 5, padding: "4px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>
              Restore
            </button>
          )}
        </div>
      </div>

      {version.deleteReason && (
        <div style={{ fontSize: 12, color: C.text, marginTop: 8, background: C.card2, borderRadius: 6, padding: "6px 10px" }}>
          <span style={{ color: C.muted, fontWeight: 700 }}>Reason given: </span>{version.deleteReason}
        </div>
      )}

      {error && <div style={{ fontSize: 12, color: C.red, marginTop: 8 }}>⚠ {error}</div>}

      <div style={{ marginTop: 8 }}>
        <button onClick={() => setShowAudit((s) => !s)} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 11, padding: 0, textDecoration: "underline" }}>
          {showAudit ? "Hide audit record" : `Audit record (${version.auditHistory.length})`}
        </button>
        {showAudit && (
          <div style={{ marginTop: 6, borderTop: `1px solid ${C.border}`, paddingTop: 6 }}>
            <AuditHistoryList entries={version.auditHistory} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function DeletedVersions({ initialProjectId }: { initialProjectId?: string } = {}) {
  const projects = useAllProjects();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  const [versions, setVersions] = useState<DeletedVersion[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!projectId) { setVersions(null); return; }
    setLoading(true); setError(null);
    fetch(`${API}/api/projects/${projectId}/versions/deleted/`)
      .then(async (r) => { const t = await r.text(); if (!r.ok) throw new Error(parseErrorMessage(t)); return JSON.parse(t); })
      .then((d) => setVersions(d.deletedVersions || []))
      .catch((e: any) => { setError(e.message || String(e)); setVersions(null); })
      .finally(() => setLoading(false));
  };

  useEffect(load, [projectId]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", marginBottom: 6, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 900, color: C.text, letterSpacing: "-0.01em" }}>ScheduleIQ | Deleted Versions</div>
          <div style={{ fontSize: 12, color: C.muted, fontWeight: 600 }}>
            Soft-deleted schedule versions — recoverable until an authorized restore or the retention period. Never appear in normal selection, dashboards, or comparisons.
          </div>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 220 }}
          >
            <option value="">Select a project…</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}{p.projectNumber ? ` (${p.projectNumber})` : ""} — {p.versionCount} active version{p.versionCount === 1 ? "" : "s"}</option>
            ))}
          </select>
        </div>
      </div>

      {!projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to review its deleted schedule versions.</div>}
      {projectId && loading && <div style={{ color: C.accent, padding: 20 }}>Loading…</div>}
      {projectId && error && <div style={{ color: C.red, padding: 20 }}>⚠ {error}</div>}

      {projectId && versions && !loading && (
        versions.length === 0 ? (
          <div style={{ textAlign: "center", color: C.muted, padding: 40, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>
            No deleted versions for this project.
          </div>
        ) : (
          <>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>
              {versions.length} Deleted Version{versions.length === 1 ? "" : "s"}
            </div>
            {versions.map((v) => (
              <DeletedVersionCard key={v.id} projectId={projectId} version={v} onRestored={load} />
            ))}
          </>
        )
      )}
    </div>
  );
}
