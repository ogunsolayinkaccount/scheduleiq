import { useState } from "react";
import { notifyProjectsChanged } from "./projectEvents";

const C = {
  bg: "#f5f2ec", card: "#ffffff", border: "#d4ccc0", accent: "#d97000", green: "#00936b",
  amber: "#c47c00", red: "#d93030", text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};

const CLS_COLOR: Record<string, string> = { KEEP: C.green, REVIEW: C.amber, NOT_LOADED: C.muted };

// Compares the backend Project/ScheduleUpload rows (what the Intelligence
// Portal shows) with the files THIS browser's main application holds.
// "Not loaded in this browser" is information only - it NEVER means a
// schedule is safe to delete. The only deletion path is the consolidation
// plan below, which proposes exact content duplicates whose retained copy is
// verified.
export default function IntelligenceSync({ files }: { files: any[] }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [diffs, setDiffs] = useState<Record<string, any>>({});
  const [confirmText, setConfirmText] = useState("");
  const [applyResult, setApplyResult] = useState<any>(null);

  const payload = () => JSON.stringify({
    mainAppFiles: (files || []).map((f: any) => ({ projectId: f.projectId || null, scheduleUploadId: f.scheduleUploadId || null, name: f.name || "" })),
  });

  const call = () => {
    setBusy(true); setError(null);
    return fetch("/api/projects/reconcile/", { method: "POST", headers: { "Content-Type": "application/json" }, body: payload() })
      .then(async (r) => { const t = await r.text(); if (!r.ok) { let m = t; try { m = JSON.parse(t).error || t; } catch {} throw new Error(m); } return JSON.parse(t); })
      .finally(() => setBusy(false));
  };

  // READ-ONLY: the server has no apply mode for consolidation. Sends this
  // browser's real projectId/scheduleUploadId pairs so the table can show
  // which backend version each main-app file actually points at.
  const runPlan = () => {
    setBusy(true); setError(null); setPlan(null);
    fetch("/api/projects/consolidation-plan/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nameContains: "barn", validate: true, mainAppFiles: appFiles() }),
    })
      .then(async (r) => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setPlan).catch((e: any) => setError(e.message || String(e))).finally(() => setBusy(false));
  };

  const appFiles = () => (files || []).map((f: any) => ({ projectId: f.projectId || null, scheduleUploadId: f.scheduleUploadId || null, name: f.name || "" }));

  // READ-ONLY difference report between the two files that share a Data Date.
  const showDiff = (key: string, a: string, b: string) => {
    fetch("/api/projects/version-difference-report/", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ versionA: a, versionB: b, sample: 10 }),
    }).then(async (r) => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then((d) => setDiffs((m) => ({ ...m, [key]: d }))).catch((e: any) => setError(e.message || String(e)));
  };

  // Confirmation-gated. Sends the exact plan fingerprint that is on screen; the server
  // refuses if the data changed, if the typed confirmation is wrong, or on any conflict.
  const applyConsolidation = () => {
    if (!plan) return;
    const s = plan.summary;
    if (!window.confirm(`Apply this consolidation?\n\n• ${s.versionsDeleteCandidate} exact duplicate version(s) will be deleted (a copy of each remains)\n• ${s.projectsDeleteCandidate} emptied project(s) will be removed\n• REVIEW versions are moved, never deleted\n\nThis cannot be undone.`)) return;
    setBusy(true); setError(null);
    fetch("/api/projects/consolidation-apply/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nameContains: "barn", mainAppFiles: appFiles(), expectedPlanFingerprint: plan.planFingerprint, confirmation: confirmText }),
    }).then(async (r) => { const t = await r.text(); if (!r.ok) { let m = t; try { m = JSON.parse(t).error || t; } catch {} throw new Error(m); } return JSON.parse(t); })
      .then((d) => { setApplyResult(d); setPlan(null); setConfirmText(""); notifyProjectsChanged(); })
      .catch((e: any) => setError(e.message || String(e))).finally(() => setBusy(false));
  };

  const start = () => {
    setOpen(true); setResult(null); setPlan(null); setDiffs({}); setApplyResult(null); setConfirmText("");
    call().then(setResult).catch((e: any) => setError(e.message || String(e)));
  };

  const group = (cls: string) => (result?.projects || []).filter((p: any) => p.classification === cls);

  return (
    <>
      <button onClick={start} title="Compare the Intelligence Portal's projects with the files loaded in this application"
        style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12 }}>
        Sync Intelligence
      </button>
      {open && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", zIndex: 3000, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, width: 760, maxWidth: "94vw", maxHeight: "86vh", overflowY: "auto", padding: 22 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: C.text }}>Sync Intelligence Portal with this application</div>
              <button onClick={() => setOpen(false)} style={{ background: "transparent", border: "none", cursor: "pointer", fontSize: 18, color: C.muted }}>✕</button>
            </div>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 12 }}>
              Compares the Intelligence Portal's projects with the {files?.length ?? 0} file(s) currently loaded in <b>this browser</b>. Nothing here deletes anything: "not loaded in this browser" is not a reason to delete a schedule.
            </div>
            {busy && <div style={{ color: C.accent, fontSize: 12 }}>Working…</div>}
            {error && <div style={{ color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 8, padding: 10, fontSize: 12, marginBottom: 10 }}>{error}</div>}
            {result && (
              <>
                <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", fontSize: 12 }}>
                  {(["KEEP", "NOT_LOADED", "REVIEW"] as const).map((c) => (
                    <span key={c} style={{ border: `1px solid ${CLS_COLOR[c]}`, color: CLS_COLOR[c], borderRadius: 6, padding: "3px 10px", fontWeight: 700 }}>{c === "NOT_LOADED" ? "NOT LOADED IN THIS BROWSER" : c}: {result.summary[c]}</span>
                  ))}
                  <span style={{ color: C.muted2, alignSelf: "center" }}>{result.totalProjects} projects / {result.totalVersions} versions on the backend</span>
                </div>
                {(["NOT_LOADED", "REVIEW", "KEEP"] as const).map((c) => group(c).length > 0 && (
                  <div key={c} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 11, fontWeight: 800, color: CLS_COLOR[c], textTransform: "uppercase", marginBottom: 4 }}>
                      {c === "NOT_LOADED" ? "Not loaded in this browser — REVIEW (this is NOT a deletion list)" : c === "REVIEW" ? "Review — identity cannot be established safely" : "Referenced by this application"} ({group(c).length})
                    </div>
                    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8 }}>
                      {group(c).map((p: any) => (
                        <div key={p.projectId} style={{ padding: "6px 10px", borderBottom: `1px solid ${C.border}`, fontSize: 12 }}>
                          <b>{p.name}</b> <span style={{ color: C.muted2 }}>· {p.versions.length} version(s): {p.versions.map((v: any) => v.label).join(", ") || "none"}</span>
                          <div style={{ fontSize: 10, color: C.muted2 }}>{p.reason}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 10, marginTop: 6, marginBottom: 10 }}>
                  <button onClick={runPlan} disabled={busy}
                    style={{ background: "transparent", border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
                    Consolidation plan for Barn (read-only dry-run)
                  </button>
                  <span style={{ fontSize: 10, color: C.muted2, marginLeft: 8 }}>Shows how duplicate single-version projects would collapse into one project. Nothing can be deleted from this view.</span>
                </div>
                {plan && (
                  <div style={{ marginBottom: 12, fontSize: 11 }}>
                    <div style={{ marginBottom: 6, color: C.text }}>
                      <b>{plan.summary.lineages}</b> schedule histor{plan.summary.lineages === 1 ? "y" : "ies"} ·
                      versions: <b style={{ color: C.green }}>{plan.summary.versionsKeep} keep</b>, <b style={{ color: C.red }}>{plan.summary.versionsDeleteCandidate} duplicate (delete candidate)</b>, <b style={{ color: C.amber }}>{plan.summary.versionsReview} review</b> ·
                      projects: {plan.summary.projectsKeep} keep, {plan.summary.projectsDeleteCandidate} would be empty, {plan.summary.projectsReview} review · plan {plan.planFingerprint.slice(0, 10)} · writes performed: {plan.writesPerformed}
                    </div>
                    {plan.validation && (
                      <div style={{ marginBottom: 6, color: plan.validation.allRetainedVersionsResolve && !plan.validation.crossProjectLeakage ? C.green : C.red }}>
                        Validation (rolled back, nothing saved): {plan.validation.checks} endpoint checks — every retained version resolves with its explicit version id: {String(plan.validation.allRetainedVersionsResolve)}; cross-project leakage: {String(plan.validation.crossProjectLeakage)}
                      </div>
                    )}
                    <div style={{ overflowX: "auto", background: C.card, border: `1px solid ${C.border}`, borderRadius: 8 }}>
                      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 10 }}>
                        <thead><tr style={{ background: "#f0ece4" }}>
                          {["Shown as", "App projectId", "App uploadId", "Backend project", "Version", "Role", "Data Date", "Acts", "P6", "Identity", "Status"].map((h) => <th key={h} style={{ padding: "4px 6px", textAlign: "left", color: C.muted, whiteSpace: "nowrap" }}>{h}</th>)}
                        </tr></thead>
                        <tbody>
                          {plan.rows.map((r: any) => (
                            <tr key={r.versionId} style={{ borderTop: `1px solid ${C.border}` }} title={r.reason}>
                              <td style={{ padding: "3px 6px" }}>{r.displayName}</td>
                              <td style={{ padding: "3px 6px", fontFamily: "monospace" }}>{r.frontendProjectId ? r.frontendProjectId.slice(0, 8) : "not in app"}</td>
                              <td style={{ padding: "3px 6px", fontFamily: "monospace" }}>{r.frontendScheduleUploadId ? r.frontendScheduleUploadId.slice(0, 8) : "not in app"}</td>
                              <td style={{ padding: "3px 6px", fontFamily: "monospace" }}>{r.backendProjectId.slice(0, 8)}</td>
                              <td style={{ padding: "3px 6px", fontFamily: "monospace" }}>{r.versionId.slice(0, 8)}</td>
                              <td style={{ padding: "3px 6px" }}>{r.roleTodayInOwnProject}</td>
                              <td style={{ padding: "3px 6px", whiteSpace: "nowrap" }}>{r.dataDate}</td>
                              <td style={{ padding: "3px 6px" }}>{r.activityCount}</td>
                              <td style={{ padding: "3px 6px" }}>{r.p6ProjectId}</td>
                              <td style={{ padding: "3px 6px", whiteSpace: "nowrap" }}>{r.identityVsAnchor}{r.activityIdOverlapVsAnchor != null ? ` (${r.activityIdOverlapVsAnchor})` : ""}</td>
                              <td style={{ padding: "3px 6px", fontWeight: 700, whiteSpace: "nowrap", color: r.status === "KEEP" ? C.green : r.status === "REVIEW" ? C.amber : C.red }}>{r.status}{r.moveToCanonical ? " · move" : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {plan.lineages.map((l: any) => (
                      <div key={l.lineageId} style={{ marginTop: 8, color: C.muted }}>
                        <b>{l.lineageId}</b> canonical project <span style={{ fontFamily: "monospace" }}>{l.canonicalProject.projectId.slice(0, 8)}</span>
                        {l.canonicalProject.suggestedName ? ` (suggested name: "${l.canonicalProject.suggestedName}")` : ""} ·
                        chain: {l.retainedChain.map((v: any) => `${v.dataDate}${v.proposedRole && v.proposedRole !== "OTHER" ? ` ${v.proposedRole}` : ""}`).join(" → ")}
                        {!l.baselineDesignated && <div style={{ color: C.amber }}>{l.baselineNote}</div>}
                        {l.previousUnresolved && <div style={{ color: C.amber }}>⚠ {l.previousUnresolved.reason}</div>}
                        {l.pendingDecisions.map((pd: any) => {
                          const key = `${l.lineageId}-${pd.dataDate}`;
                          const d = diffs[key];
                          return (
                            <div key={pd.dataDate} style={{ color: C.amber, marginTop: 4 }}>
                              Decision needed for {pd.dataDate}: {pd.candidates.map((c: any) => `${c.filename} (${c.activityCount} acts, P6 ${c.p6ProjectId}${c.inMainApp ? ", IN YOUR APP" : ""})`).join("  vs  ")}
                              {pd.resolvedByMainAppReference && <span style={{ color: C.green }}> — the file your app holds is retained; the other stays REVIEW.</span>}
                              {pd.candidates.length === 2 && (
                                <button onClick={() => showDiff(key, pd.candidates[0].versionId, pd.candidates[1].versionId)}
                                  style={{ marginLeft: 8, background: "transparent", border: `1px solid ${C.amber}`, color: C.amber, borderRadius: 5, padding: "1px 8px", cursor: "pointer", fontSize: 10 }}>
                                  View differences
                                </button>
                              )}
                              {d && (
                                <div style={{ background: "#fffaf0", border: `1px solid ${C.border}`, borderRadius: 6, padding: 8, marginTop: 4, color: C.text }}>
                                  {d.summary.map((line: string, i: number) => <div key={i}>{line}</div>)}
                                  <div style={{ marginTop: 4 }}>
                                    Dates: {d.dates.changeCount} · Durations: {d.durations.changeCount} · Float: {d.float.changeCount} · Constraints: {d.constraints.changeCount} · Relationships: +{d.relationships.addedInB}/-{d.relationships.removedFromA}/~{d.relationships.changed}
                                  </div>
                                  {d.metadata.differences.length > 0 && <div style={{ color: C.muted }}>Metadata differences: {d.metadata.differences.map((x: any) => x.field).join(", ")}</div>}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                )}
                {plan && (
                  <div style={{ border: `1px solid ${C.red}55`, borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 11, background: "#fff8f6" }}>
                    <b style={{ color: C.red }}>Apply this consolidation</b> (plan {plan.planFingerprint.slice(0, 10)})
                    <div style={{ color: C.muted, margin: "4px 0" }}>
                      Deletes ONLY exact duplicate versions the plan lists as DELETE CANDIDATE (each with a re-verified retained copy) and the projects that become empty. Versions absent from this browser, and versions marked REVIEW, are never deleted - REVIEW versions are moved into the canonical project.
                      {!plan.frontendStateProvided && <span style={{ color: C.red }}> Your app's file list was not received, so this cannot be applied.</span>}
                    </div>
                    <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder='Type CONSOLIDATE to enable'
                      style={{ border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 11, marginRight: 8, width: 180 }} />
                    <button onClick={applyConsolidation} disabled={busy || confirmText !== "CONSOLIDATE" || !plan.frontendStateProvided}
                      style={{ background: confirmText === "CONSOLIDATE" ? C.red : "#ccc", border: "none", color: "#fff", borderRadius: 6, padding: "5px 12px", cursor: confirmText === "CONSOLIDATE" ? "pointer" : "not-allowed", fontSize: 11, fontWeight: 700 }}>
                      Apply consolidation
                    </button>
                  </div>
                )}
                {applyResult && (
                  <div style={{ color: C.green, fontSize: 11, marginBottom: 10 }}>
                    Consolidation applied: {applyResult.projectsBefore} → {applyResult.projectsAfter} projects, {applyResult.versionsBefore} → {applyResult.versionsAfter} versions. Selectors have refreshed.
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button onClick={() => setOpen(false)} style={{ background: "transparent", border: `1px solid ${C.border}`, borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontSize: 12, color: C.muted }}>Close</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
