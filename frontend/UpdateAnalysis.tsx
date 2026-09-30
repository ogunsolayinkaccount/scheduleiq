import { useEffect, useMemo, useRef, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

type ProjectSummary = { id: string; name: string; versionCount: number };
type VersionSummary = {
  id: string; filename: string; versionLabel: string; role: string;
  classification: string; dataDate: string | null; activityCount: number;
  criticalCount: number; negativeFloatCount: number; projectFinish: string | null;
  sourceDataDate?: string | null; dataDateOverridden?: boolean;
};

function fmtDate(d: string | null): string {
  if (!d) return "—";
  return d;
}

function SeverityDot({ severity }: { severity: string }) {
  const color = severity === "high" ? C.red : severity === "medium" ? C.amber : severity === "low" ? C.green : C.muted2;
  return <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: color, marginRight: 6 }} />;
}

function StatCard({ label, value, color, sub }: { label: string; value: number | string; color?: string; sub?: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px", minWidth: 140, flex: "1 1 140px" }}>
      <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: C.muted2, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function Select({ value, onChange, options, placeholder }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; placeholder?: string }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 160 }}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

export default function UpdateAnalysis({ initialProjectId, onManageVersions, onAnalyzeRecovery }: { initialProjectId?: string; onManageVersions?: (projectId: string) => void; onAnalyzeRecovery?: (projectId: string, versionId?: string, riskKey?: string) => void } = {}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState(initialProjectId || "");
  setProjectScope(projectId);   // stale responses from a previously selected project are dropped (see projectScope.ts)
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const [previousId, setPreviousId] = useState("");
  const [currentId, setCurrentId] = useState("");
  const [baselineId, setBaselineId] = useState("");

  const [subTab, setSubTab] = useState<"compare" | "whatChanged" | "progress" | "updateIntelligence">("compare");

  const [compareResult, setCompareResult] = useState<any>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<{ area: string; discipline: string; contractor: string; changeType: string; severity: string }>({
    area: "", discipline: "", contractor: "", changeType: "", severity: "",
  });

  const [progressResult, setProgressResult] = useState<any>(null);
  const [progressLoading, setProgressLoading] = useState(false);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [weighting, setWeighting] = useState<"duration" | "count">("duration");
  const [period, setPeriod] = useState<"monthly" | "weekly">("monthly");

  // ── Update Intelligence (What-Changed-Since-Last-Data-Date phase) ────────
  const [uiResult, setUiResult] = useState<any>(null);
  const [uiLoading, setUiLoading] = useState(false);
  const [uiError, setUiError] = useState<string | null>(null);
  const [uiGroupBy, setUiGroupBy] = useState("discipline");
  const [uiLookaheadWeeks, setUiLookaheadWeeks] = useState("4");

  useEffect(() => {
    fetch(`${API}/api/projects/?withVersions=true`).then((r) => r.json()).then((d) => { const list = d.projects || []; setProjects(list); setProjectId((cur: string) => (cur && !list.some((p: any) => p.id === cur) ? "" : cur)); }).catch(() => {});
  }, [projectsTick]);

  useEffect(() => {
    if (!projectId) { setVersions([]); return; }
    setVersionsLoading(true);
    sfetch(`${API}/api/projects/${projectId}/versions/`)
      .then((r) => r.json())
      .then((d) => {
        const vs: VersionSummary[] = d.versions || [];
        setVersions(dedupeVersionsById(vs));
        const cur = vs.find((v) => v.role === "CURRENT");
        const prev = vs.find((v) => v.role === "PREVIOUS");
        const base = vs.find((v) => v.role === "BASELINE");
        setCurrentId(cur?.id || vs[0]?.id || "");
        setPreviousId(prev?.id || vs[1]?.id || "");
        setBaselineId(base?.id || "");
      })
      .catch(() => setVersions([]))
      .finally(() => setVersionsLoading(false));
  }, [projectId, projectsTick]);

  const runCompare = () => {
    if (!projectId || !previousId || !currentId) return;
    setCompareLoading(true);
    setCompareError(null);
    sfetch(`${API}/api/projects/${projectId}/compare/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        previousVersionId: previousId, currentVersionId: currentId,
        page, pageSize: 50,
        filters: {
          area: filters.area || undefined, discipline: filters.discipline || undefined,
          contractor: filters.contractor || undefined,
          changeType: filters.changeType || undefined, severity: filters.severity || undefined,
        },
      }),
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then(setCompareResult)
      .catch((e: any) => setCompareError(e.message || String(e)))
      .finally(() => setCompareLoading(false));
  };

  const runProgressCurve = () => {
    if (!projectId || !currentId) return;
    setProgressLoading(true);
    setProgressError(null);
    const params = new URLSearchParams({ currentVersionId: currentId, weighting, period });
    if (baselineId) params.set("baselineVersionId", baselineId);
    if (previousId) params.set("previousVersionId", previousId);
    sfetch(`${API}/api/projects/${projectId}/progress-curve/?${params.toString()}`)
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then(setProgressResult)
      .catch((e: any) => setProgressError(e.message || String(e)))
      .finally(() => setProgressLoading(false));
  };

  useEffect(() => { setPage(1); }, [filters, previousId, currentId]);
  useEffect(() => {
    if (subTab === "compare" || subTab === "whatChanged") { if (previousId && currentId) runCompare(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subTab, page, filters, previousId, currentId]);
  useEffect(() => {
    if (subTab === "progress") { if (currentId) runProgressCurve(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subTab, currentId, baselineId, previousId, weighting, period]);

  // Reuses the SAME Previous/Current version selectors as the rest of this
  // workspace — the backend's own auto-resolution (immediately-preceding
  // version by upload order) only kicks in when these are left unset, but
  // since the selectors already default to role-based Previous/Current on
  // project load, an explicit override here always wins and both stay in
  // sync with what's displayed. No calculation happens in this effect —
  // it only calls the authoritative /update-intelligence/ endpoint.
  useEffect(() => {
    if (subTab !== "updateIntelligence") return;
    if (!projectId || !currentId) return;
    setUiLoading(true); setUiError(null);
    const params = new URLSearchParams({ currentVersion: currentId, group_by: uiGroupBy, lookaheadWeeks: uiLookaheadWeeks });
    if (previousId) params.set("previousVersion", previousId);
    sfetch(`${API}/api/projects/${projectId}/update-intelligence/?${params.toString()}`)
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) { let msg = text; try { msg = JSON.parse(text).error || text; } catch {} throw new Error(msg); }
        return JSON.parse(text);
      })
      .then(setUiResult)
      .catch((e: any) => setUiError(e.message || String(e)))
      .finally(() => setUiLoading(false));
  }, [subTab, projectId, currentId, previousId, uiGroupBy, uiLookaheadWeeks]);

  const summary = compareResult?.summary;

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Update Analysis</div>
        <div style={{ fontSize: 13, color: C.muted }}>
          Compare schedule versions imported through the Import Center. Only projects with saved versions appear here.
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
          <Select value={projectId} onChange={(id: string) => { setVersions([]); setPreviousId(""); setCurrentId(""); setBaselineId(""); setProjectId(id); }} placeholder="Select a project…" options={projects.map((p) => ({ value: p.id, label: `${p.name} (${p.versionCount} version${p.versionCount === 1 ? "" : "s"})` }))} />
        </div>

        {projectId && onManageVersions && (
          <button onClick={() => onManageVersions(projectId)} style={{ alignSelf: "flex-end", background: "transparent", border: `1px solid ${C.border}`, color: C.accent, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
            Manage Versions
          </button>
        )}

        {projectId && onAnalyzeRecovery && (
          <button onClick={() => onAnalyzeRecovery(projectId, currentId || undefined)} style={{ alignSelf: "flex-end", background: `${C.purple}12`, border: `1px solid ${C.purple}`, color: C.purple, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
            Analyze Recovery →
          </button>
        )}

        {projectId && versions.length > 0 && (
          <>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Previous</div>
              <Select value={previousId} onChange={setPreviousId} options={versions.map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Current</div>
              <Select value={currentId} onChange={setCurrentId} options={versions.map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
            </div>
            {subTab === "progress" && (
              <div>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Baseline (optional)</div>
                <Select value={baselineId} onChange={setBaselineId} placeholder="None" options={versions.map((v) => ({ value: v.id, label: versionSelectLabel(v) }))} />
              </div>
            )}
            <button
              onClick={() => (subTab === "progress" ? runProgressCurve() : runCompare())}
              style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "7px 18px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit", marginLeft: 4 }}
            >
              {subTab === "progress" ? "Update Curve" : "Compare"}
            </button>
          </>
        )}

        {projectId && !versionsLoading && versions.length === 0 && (
          <div style={{ fontSize: 12, color: C.muted }}>This project has no saved schedule versions yet — import one through the Import Center first.</div>
        )}
        {projectId && versions.length === 1 && (
          <div style={{ fontSize: 12, color: C.amber }}>Only one version exists — import another update to compare.</div>
        )}
      </div>

      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 16 }}>
        {[
          { id: "updateIntelligence", label: "Update Intelligence" },
          { id: "compare", label: "Compare Updates" },
          { id: "whatChanged", label: "What Changed" },
          { id: "progress", label: "Progress Curve" },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setSubTab(t.id as any)}
            style={{ background: "transparent", border: "none", borderBottom: `2px solid ${subTab === t.id ? C.accent : "transparent"}`, color: subTab === t.id ? C.accent : C.muted2, padding: "8px 14px", cursor: "pointer", fontSize: 13, fontFamily: "inherit", fontWeight: subTab === t.id ? 800 : 600 }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}

      {projectId && subTab !== "progress" && compareLoading && <div style={{ color: C.accent, padding: 20 }}>Comparing versions…</div>}
      {projectId && compareError && <div style={{ color: C.red, background: `${C.red}12`, border: `1px solid ${C.red}40`, borderRadius: 8, padding: 12, marginBottom: 12 }}>⚠ {compareError}</div>}

      {subTab === "compare" && compareResult && summary && (
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
            <StatCard label="Activities Added" value={summary.activitiesAdded} />
            <StatCard label="Activities Removed" value={summary.activitiesRemoved} />
            <StatCard label="Activities Changed" value={summary.activitiesChanged} />
            <StatCard label="Logic Changes" value={summary.relationshipsAdded + summary.relationshipsRemoved + summary.relationshipsChanged} />
            <StatCard label="Moved Later" value={summary.movedLater} color={summary.movedLater > 0 ? C.red : undefined} />
            <StatCard label="Moved Earlier" value={summary.movedEarlier} color={summary.movedEarlier > 0 ? C.green : undefined} />
            <StatCard label="Newly Critical" value={summary.newlyCritical} color={summary.newlyCritical > 0 ? C.red : undefined} />
            <StatCard label="Newly Negative Float" value={summary.newlyNegativeFloat} color={summary.newlyNegativeFloat > 0 ? C.red : undefined} />
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            <Select value={filters.severity} onChange={(v) => setFilters((f) => ({ ...f, severity: v }))} placeholder="All Severities" options={[{ value: "high", label: "High" }, { value: "medium", label: "Medium" }, { value: "low", label: "Low" }, { value: "info", label: "Info" }]} />
            <Select value={filters.changeType} onChange={(v) => setFilters((f) => ({ ...f, changeType: v }))} placeholder="All Change Types" options={[
              { value: "Total Float", label: "Total Float" }, { value: "Finish", label: "Finish" }, { value: "Start", label: "Start" },
              { value: "Percent Complete", label: "Percent Complete" }, { value: "Original Duration", label: "Original Duration" },
              { value: "Constraint Type", label: "Constraint Type" },
            ]} />
            <input
              placeholder="Filter by Area…" value={filters.area} onChange={(e) => setFilters((f) => ({ ...f, area: e.target.value }))}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 140 }}
            />
            <input
              placeholder="Filter by Discipline…" value={filters.discipline} onChange={(e) => setFilters((f) => ({ ...f, discipline: e.target.value }))}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 150 }}
            />
            <input
              placeholder="Filter by Contractor…" value={filters.contractor} onChange={(e) => setFilters((f) => ({ ...f, contractor: e.target.value }))}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 150 }}
            />
          </div>

          <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
              <thead>
                <tr style={{ background: C.card2 }}>
                  {["Activity ID", "Activity Name", "Change", "Previous", "Current", "Delta", "Risk"].map((h) => (
                    <th key={h} style={{ padding: "8px 12px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {compareResult.changes.map((c: any, i: number) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td style={{ padding: "7px 12px", fontFamily: "monospace" }}>{c.activityId}</td>
                    <td style={{ padding: "7px 12px" }}>{c.activityName}</td>
                    <td style={{ padding: "7px 12px" }}>{c.field}</td>
                    <td style={{ padding: "7px 12px", color: C.muted2 }}>{c.previous === null || c.previous === undefined || c.previous === "" ? "—" : String(c.previous)}</td>
                    <td style={{ padding: "7px 12px", fontWeight: 600 }}>{c.current === null || c.current === undefined || c.current === "" ? "—" : String(c.current)}</td>
                    <td style={{ padding: "7px 12px" }}>
                      {c.delta ?? c.deltaDays ?? "—"}{c.deltaUnit === "calendar_days" && c.deltaDays !== undefined ? "d" : ""}
                      {c.deltaUnit === "calendar_days" && c.deltaDays !== undefined && (
                        <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>
                          {c.deltaWorkingDays !== null && c.deltaWorkingDays !== undefined
                            ? `${c.deltaWorkingDays}w working`
                            : "Working-day variance: unavailable"}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "7px 12px" }}><SeverityDot severity={c.severity} />{c.severity}</td>
                  </tr>
                ))}
                {compareResult.changes.length === 0 && (
                  <tr><td colSpan={7} style={{ padding: 20, textAlign: "center", color: C.muted }}>No changes match the current filters.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {compareResult.changesPagination.totalPages > 1 && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "center", marginTop: 12 }}>
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 12px", cursor: page <= 1 ? "default" : "pointer", fontSize: 12 }}>← Prev</button>
              <span style={{ fontSize: 12, color: C.muted }}>Page {compareResult.changesPagination.page} of {compareResult.changesPagination.totalPages} ({compareResult.changesPagination.totalCount} changes)</span>
              <button disabled={page >= compareResult.changesPagination.totalPages} onClick={() => setPage((p) => p + 1)} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 12px", cursor: page >= compareResult.changesPagination.totalPages ? "default" : "pointer", fontSize: 12 }}>Next →</button>
            </div>
          )}
        </div>
      )}

      {subTab === "whatChanged" && compareResult && (
        <div>
          <div style={{ background: C.card, border: `1px solid ${C.accent}40`, borderRadius: 12, padding: "20px 24px", marginBottom: 20 }}>
            <div style={{ fontSize: 11, color: C.accent, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 }}>What Changed This Update?</div>
            <div style={{ fontSize: 15, color: C.text, lineHeight: 1.7 }}>{compareResult.summaryNarrative}</div>
          </div>

          <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Key Insights</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
            {compareResult.insights.map((ins: any, i: number) => (
              <div key={i} style={{ background: C.card, border: `1px solid ${C.border}`, borderLeft: `3px solid ${ins.severity === "high" ? C.red : ins.severity === "medium" ? C.amber : C.green}`, borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>{ins.type.replace(/_/g, " ")}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{ins.activityName || ins.activityId}</div>
                <div style={{ fontSize: 11, color: C.muted2, marginTop: 3 }}>
                  {ins.deltaDays !== undefined && `${Math.abs(ins.deltaDays)} calendar days ${ins.deltaDays > 0 ? "later" : "earlier"}`}
                  {ins.delta !== undefined && ins.previousFloat !== undefined && `Float ${ins.previousFloat} → ${ins.currentFloat}`}
                  {ins.previousDuration !== undefined && `Duration ${ins.previousDuration} → ${ins.currentDuration}`}
                </div>
              </div>
            ))}
            {compareResult.insights.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No material insights for this comparison.</div>}
          </div>
        </div>
      )}

      {subTab === "progress" && (
        <div>
          <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "center" }}>
            <Select value={weighting} onChange={(v) => setWeighting(v as any)} options={[{ value: "duration", label: "Duration-Weighted" }, { value: "count", label: "Activity-Count-Weighted" }]} />
            <Select value={period} onChange={(v) => setPeriod(v as any)} options={[{ value: "monthly", label: "Monthly" }, { value: "weekly", label: "Weekly" }]} />
          </div>
          {progressLoading && <div style={{ color: C.accent, padding: 20 }}>Computing progress curve…</div>}
          {progressError && <div style={{ color: C.red, padding: 12 }}>⚠ {progressError}</div>}
          {progressResult && <ProgressCurveCharts result={progressResult} />}
        </div>
      )}

      {subTab === "updateIntelligence" && (
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 16, alignItems: "center" }}>
            <Select value={uiGroupBy} onChange={setUiGroupBy} options={["wbs", "area", "discipline", "contractor", "system"].map((v) => ({ value: v, label: `Group by ${v[0].toUpperCase() + v.slice(1)}` }))} />
            <Select value={uiLookaheadWeeks} onChange={setUiLookaheadWeeks} options={[{ value: "2", label: "2 Week Plan Change" }, { value: "4", label: "4 Week Plan Change" }, { value: "6", label: "6 Week Plan Change" }, { value: "8", label: "8 Week Plan Change" }, { value: "12", label: "12 Week Plan Change" }]} />
          </div>
          {uiLoading && <div style={{ color: C.accent, padding: 20 }}>Computing Update Intelligence…</div>}
          {uiError && <div style={{ color: C.red, background: `${C.red}12`, border: `1px solid ${C.red}40`, borderRadius: 8, padding: 12, marginBottom: 12 }}>⚠ {uiError}</div>}
          {!uiLoading && !uiError && uiResult && <UpdateIntelligencePanel result={uiResult} />}
        </div>
      )}
    </div>
  );
}

function ProgressCurveTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#1c1410", border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px", fontSize: 12, color: "#fff" }}>
      <div style={{ color: "#ccc", marginBottom: 4 }}>{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color }}>{p.name}: <strong>{typeof p.value === "number" ? `${p.value.toFixed(1)}%` : p.value}</strong></div>
      ))}
    </div>
  );
}

function ProgressCurveCharts({ result }: { result: any }) {
  const periods = result.periods || [];
  return (
    <div>
      <div style={{ fontSize: 11, color: C.muted, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 14px", marginBottom: 16 }}>
        ℹ {result.methodologyNote}
      </div>

      <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 18px", marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 10 }}>Cumulative Progress Curve ({result.weighting === "duration" ? "Duration-Weighted" : "Count-Weighted"})</div>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={periods}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} />
            <Tooltip content={<ProgressCurveTooltip />} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {result.hasBaseline && <Area type="monotone" dataKey="baselinePlanned" name="Baseline Planned %" stroke="#8b5cf6" fill="#8b5cf633" />}
            <Area type="monotone" dataKey="currentPlanned" name="Current Planned %" stroke="#3b82f6" fill="#3b82f633" />
            <Area type="monotone" dataKey="currentUpdateActual" name="Actual %" stroke={C.green} fill={`${C.green}33`} />
            {result.hasPreviousUpdate && <Area type="monotone" dataKey="previousUpdateActual" name="Previous Update %" stroke={C.amber} fill={`${C.amber}22`} />}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 400px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 18px" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 10 }}>Monthly Incremental Progress</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={periods}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis dataKey="period" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip content={<ProgressCurveTooltip />} />
              <Bar dataKey="incrementalPlanned" name="Planned" fill="#3b82f6" />
              <Bar dataKey="incrementalActual" name="Actual" fill={C.green} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div style={{ flex: "1 1 400px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 18px" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 10 }}>Progress Variance Trend</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={periods}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis dataKey="period" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip content={<ProgressCurveTooltip />} />
              <Line type="monotone" dataKey="variance" name="Variance (Actual − Planned)" stroke={C.red} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Update Intelligence — "What changed since the previous Data Date?"
//
// Every number rendered here comes straight from the authoritative backend
// /update-intelligence/ payload (update_intelligence.py) — nothing is
// recalculated in React. Tables use VirtualizedTable so the DOM never
// mounts more than a viewport's worth of rows at once, but the underlying
// `rows` array passed in always holds the COMPLETE analyzed population —
// virtualization changes what's rendered, never what's analyzed.
// ─────────────────────────────────────────────────────────────────────────

const UI_ROW_HEIGHT = 32;
const UI_TABLE_VIEWPORT_HEIGHT = 380;
const UI_BUFFER_ROWS = 15;

type UiCol = { key: string; label: string; width: number; render?: (r: any) => React.ReactNode };

function VirtualizedTable({ rows, columns, emptyMessage }: { rows: any[]; columns: UiCol[]; emptyMessage?: string }) {
  const [scrollTop, setScrollTop] = useState(0);
  const startIdx = Math.max(0, Math.floor(scrollTop / UI_ROW_HEIGHT) - UI_BUFFER_ROWS);
  const visibleCount = Math.ceil(UI_TABLE_VIEWPORT_HEIGHT / UI_ROW_HEIGHT) + UI_BUFFER_ROWS * 2;
  const endIdx = Math.min(rows.length, startIdx + visibleCount);
  const topSpacer = startIdx * UI_ROW_HEIGHT;
  const bottomSpacer = (rows.length - endIdx) * UI_ROW_HEIGHT;
  const totalWidth = columns.reduce((s, c) => s + c.width, 0);

  if (rows.length === 0) {
    return <div style={{ padding: 18, textAlign: "center", color: C.muted2, fontSize: 12, border: `1px solid ${C.border}`, borderRadius: 10, background: C.card, marginBottom: 16 }}>{emptyMessage || "No rows."}</div>;
  }

  const viewportH = Math.min(UI_TABLE_VIEWPORT_HEIGHT, rows.length * UI_ROW_HEIGHT + 4);

  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, background: C.card, overflow: "hidden", marginBottom: 16 }}>
      <div style={{ fontSize: 10, color: C.muted2, padding: "5px 12px", borderBottom: `1px solid ${C.border}`, background: C.card2 }}>
        {rows.length.toLocaleString()} row{rows.length === 1 ? "" : "s"} analyzed (complete population) — virtualized viewport, not truncated
      </div>
      <div onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)} style={{ height: viewportH, overflowY: "auto", overflowX: "auto" }}>
        <div style={{ minWidth: totalWidth }}>
          <div style={{ display: "flex", position: "sticky", top: 0, zIndex: 2, background: C.card2, borderBottom: `1px solid ${C.border}` }}>
            {columns.map((c) => (
              <div key={c.key} style={{ width: c.width, flexShrink: 0, padding: "6px 10px", fontSize: 9, fontWeight: 700, color: C.muted, textTransform: "uppercase", whiteSpace: "nowrap" }}>{c.label}</div>
            ))}
          </div>
          <div style={{ height: topSpacer }} />
          {rows.slice(startIdx, endIdx).map((r, i) => (
            <div key={(r.activityId || "") + "-" + (r.predecessorId || "") + "-" + (r.successorId || "") + "-" + (startIdx + i)}
              style={{ display: "flex", height: UI_ROW_HEIGHT, alignItems: "center", borderBottom: `1px solid ${C.border}` }}>
              {columns.map((c) => (
                <div key={c.key} style={{ width: c.width, flexShrink: 0, padding: "0 10px", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: C.text }}>
                  {c.render ? c.render(r) : (r[c.key] ?? "—")}
                </div>
              ))}
            </div>
          ))}
          <div style={{ height: bottomSpacer }} />
        </div>
      </div>
    </div>
  );
}

function movementDisplay(days: number | null | undefined, workingDays: number | null | undefined, calAvailable: boolean): string {
  if (days === null || days === undefined) return "—";
  if (calAvailable && workingDays !== null && workingDays !== undefined) return `${workingDays > 0 ? "+" : ""}${workingDays}wd`;
  return `${days > 0 ? "+" : ""}${days}cd`;
}

function reliabilityColor(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return C.muted2;
  return pct >= 90 ? C.green : pct >= 70 ? C.amber : C.red;
}

const FLAG_COLOR: Record<string, string> = {
  SLIPPED: C.red, IMPROVED: C.green, UNCHANGED: C.muted2, NEW: C.gold, REMOVED: C.muted,
  STARTED_THIS_PERIOD: C.accent, COMPLETED_THIS_PERIOD: C.green,
};

function UiSectionTitle({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 800, color: C.text, marginTop: 22, marginBottom: 10 }}>{children}</div>;
}

function uiLookaheadWeeksLabel(result: any): string {
  const weeks = (result.lookaheadChange || {}).currentWindow?.weeks;
  return weeks ? `${weeks}-Week` : "Near-Term";
}

function UpdateIntelligencePanel({ result }: { result: any }) {
  const [movementFilter, setMovementFilter] = useState<string | null>(null);
  const [movementSearch, setMovementSearch] = useState("");

  const movementRows: any[] = result.movementRows || [];
  const filteredMovement = useMemo(() => {
    let out = movementRows;
    if (movementFilter) {
      out = out.filter((r) => (movementFilter === "NEW" || movementFilter === "REMOVED") ? r.matchStatus === movementFilter : (r.flags || []).includes(movementFilter));
    }
    if (movementSearch.trim()) {
      const q = movementSearch.toLowerCase();
      out = out.filter((r) => (r.activityId || "").toLowerCase().includes(q) || (r.activityName || "").toLowerCase().includes(q));
    }
    return out;
  }, [movementRows, movementFilter, movementSearch]);

  if (!result.available) {
    return (
      <div style={{ padding: 30, textAlign: "center", color: C.muted, background: C.card, border: `1px solid ${C.border}`, borderRadius: 10 }}>
        {result.reason || "Update Intelligence is unavailable for this project."}
      </div>
    );
  }

  const mc = result.movementCounts || {};
  const reliability = result.reliability || {};
  const floatMovement = result.floatMovement || {};
  const cp = result.criticalPathMovement || {};
  const narrative = result.narrative || {};
  const logic = result.logicChanges || {};
  const duration = result.durationChanges || {};
  const milestones = result.milestoneMovement || {};
  const drivers = result.driverAnalysis || {};
  const lookaheadChange = result.lookaheadChange || {};

  const deteriorated = (floatMovement.deteriorated || []).filter((d: any) => !(floatMovement.newlyNegativeFloat || []).some((n: any) => n.activityId === d.activityId));

  return (
    <div>
      {/* 1. Update Header */}
      <div style={{ background: C.card, border: `1px solid ${C.accent}40`, borderRadius: 12, padding: "16px 20px", marginBottom: 16, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em" }}>Previous Update</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{result.previousVersionLabel || result.previousDataDate || "Unavailable"}</div>
          <div style={{ fontSize: 12, color: C.muted2 }}>{result.previousDataDate || "Unavailable"}</div>
        </div>
        <div style={{ fontSize: 22, color: C.accent }}>→</div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em" }}>Current Update</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{result.currentVersionLabel || result.currentDataDate || "Unavailable"}</div>
          <div style={{ fontSize: 12, color: C.muted2 }}>{result.currentDataDate || "Unavailable"}</div>
        </div>
        <div style={{ marginLeft: "auto", textAlign: "right" }}>
          <div style={{ fontSize: 10, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em" }}>Update Period</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: C.accent }}>{result.periodDays != null ? `${result.periodDays} calendar days` : "Unavailable"}</div>
        </div>
        {result.baselineVersionLabel && (
          <div style={{ flexBasis: "100%", fontSize: 11, color: C.muted2 }}>Baseline: {result.baselineVersionLabel} (available separately for Baseline Variance below)</div>
        )}
      </div>

      {/* 2. Executive Change Summary */}
      <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "18px 22px", marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: C.accent, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>What Changed Since the Previous Data Date?</div>
        {["overview", "executionReliability", "criticalPath", "milestones", "primaryDriver"].map((k) => narrative[k] && (
          <div key={k} style={{ fontSize: 14, color: C.text, lineHeight: 1.7, marginBottom: 4 }}>{narrative[k]}</div>
        ))}
      </div>

      {/* 4. Plan Reliability */}
      <UiSectionTitle>Plan Reliability</UiSectionTitle>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginBottom: 8 }}>
        <div style={{ flex: "1 1 260px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Starts</div>
          {reliability.plannedStarts ? (
            <>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
                <StatCard label="Planned" value={reliability.plannedStarts} />
                <StatCard label="Achieved" value={reliability.actualStarts} color={C.green} />
                <StatCard label="Missed" value={reliability.plannedStarts - reliability.actualStarts} color={reliability.plannedStarts - reliability.actualStarts > 0 ? C.red : undefined} />
              </div>
              <div style={{ fontSize: 26, fontWeight: 800, color: reliabilityColor(reliability.startReliabilityPct) }}>{reliability.startReliabilityPct}%</div>
              <div style={{ fontSize: 10, color: C.muted2 }}>Start Reliability</div>
            </>
          ) : (
            <div style={{ color: C.muted2, fontSize: 12, fontStyle: "italic" }}>Unavailable — no starts were forecast during this update period.</div>
          )}
        </div>
        <div style={{ flex: "1 1 260px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Finishes</div>
          {reliability.plannedFinishes ? (
            <>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
                <StatCard label="Planned" value={reliability.plannedFinishes} />
                <StatCard label="Achieved" value={reliability.actualFinishes} color={C.green} />
                <StatCard label="Missed" value={reliability.plannedFinishes - reliability.actualFinishes} color={reliability.plannedFinishes - reliability.actualFinishes > 0 ? C.red : undefined} />
              </div>
              <div style={{ fontSize: 26, fontWeight: 800, color: reliabilityColor(reliability.finishReliabilityPct) }}>{reliability.finishReliabilityPct}%</div>
              <div style={{ fontSize: 10, color: C.muted2 }}>Finish Reliability</div>
            </>
          ) : (
            <div style={{ color: C.muted2, fontSize: 12, fontStyle: "italic" }}>Unavailable — no finishes were forecast during this update period.</div>
          )}
        </div>
      </div>

      {/* 5. Activity Movement */}
      <UiSectionTitle>Activity Movement</UiSectionTitle>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 8 }}>
        {[
          { key: "SLIPPED", label: "Slipped", value: mc.slipped, color: C.red },
          { key: "IMPROVED", label: "Improved", value: mc.improved, color: C.green },
          { key: "UNCHANGED", label: "Unchanged", value: mc.unchanged, color: C.muted2 },
          { key: "NEW", label: "New", value: mc.new, color: C.gold },
          { key: "REMOVED", label: "Removed", value: mc.removed, color: C.muted },
          { key: "STARTED_THIS_PERIOD", label: "Started This Period", value: mc.startedThisPeriod, color: C.accent },
          { key: "COMPLETED_THIS_PERIOD", label: "Completed This Period", value: mc.completedThisPeriod, color: C.green },
        ].map((k) => (
          <div key={k.key} onClick={() => setMovementFilter(movementFilter === k.key ? null : k.key)}
            style={{ cursor: "pointer", background: movementFilter === k.key ? `${k.color}18` : C.card, border: `1px solid ${movementFilter === k.key ? k.color : C.border}`, borderRadius: 10, padding: "10px 16px", minWidth: 120 }}>
            <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>{k.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: k.color }}>{k.value ?? 0}</div>
          </div>
        ))}
        {movementFilter && (
          <button onClick={() => setMovementFilter(null)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 8, padding: "0 14px", cursor: "pointer", fontSize: 11 }}>Clear filter</button>
        )}
      </div>
      <div style={{ fontSize: 11, color: C.muted2, marginBottom: 4 }}>Click a KPI to filter the Detailed Activity Movement table below.</div>

      {/* 6. Missed Forecast Commitments */}
      <UiSectionTitle>Missed Forecast Starts ({(reliability.missedStarts || []).length})</UiSectionTitle>
      <VirtualizedTable
        rows={reliability.missedStarts || []}
        emptyMessage="No forecast starts were missed this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 220 },
          { key: "wbs", label: "WBS", width: 120 },
          { key: "previousForecastStart", label: "Previous Forecast Start", width: 150 },
          { key: "status", label: "Status", width: 180, render: () => <span style={{ color: C.red, fontWeight: 700 }}>MISSED</span> },
        ]}
      />
      <UiSectionTitle>Missed Forecast Finishes ({(reliability.missedFinishes || []).length})</UiSectionTitle>
      <VirtualizedTable
        rows={reliability.missedFinishes || []}
        emptyMessage="No forecast finishes were missed this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 220 },
          { key: "wbs", label: "WBS", width: 120 },
          { key: "previousForecastFinish", label: "Previous Forecast Finish", width: 150 },
          { key: "status", label: "Status", width: 180, render: () => <span style={{ color: C.red, fontWeight: 700 }}>MISSED</span> },
        ]}
      />

      {/* 7. Float Movement */}
      <UiSectionTitle>Float Movement</UiSectionTitle>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 10 }}>
        <StatCard label="Float Improved" value={floatMovement.improvedCount ?? 0} color={C.green} />
        <StatCard label="Float Deteriorated" value={floatMovement.deterioratedCount ?? 0} color={(floatMovement.deterioratedCount ?? 0) > 0 ? C.red : undefined} />
        <StatCard label="Newly Negative Float" value={floatMovement.newlyNegativeFloatCount ?? 0} color={(floatMovement.newlyNegativeFloatCount ?? 0) > 0 ? C.red : undefined} />
        <StatCard label="Recovered From Negative" value={floatMovement.recoveredFromNegativeFloatCount ?? 0} color={C.green} />
        <StatCard label="Newly Critical" value={floatMovement.newlyCriticalCount ?? 0} color={(floatMovement.newlyCriticalCount ?? 0) > 0 ? C.red : undefined} />
        <StatCard label="Left Critical Path" value={floatMovement.leftCriticalPathCount ?? 0} color={C.green} />
      </div>
      <div style={{ fontSize: 10, color: C.muted2, marginBottom: 6 }}>Total Float shown here is the imported P6 value — never independently recalculated for this comparison.</div>
      <VirtualizedTable
        rows={[...(floatMovement.newlyNegativeFloat || []), ...deteriorated]}
        emptyMessage="No float deterioration this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 220 },
          { key: "wbs", label: "WBS", width: 110 },
          { key: "float", label: "Previous TF → Current TF", width: 200, render: (r) => `${r.previousTotalFloat}d → ${r.currentTotalFloat}d` },
          { key: "floatMovement", label: "Movement", width: 100, render: (r) => <span style={{ color: r.floatMovement < 0 ? C.red : C.green, fontWeight: 700 }}>{r.floatMovement > 0 ? "+" : ""}{r.floatMovement}d</span> },
        ]}
      />

      {/* 8. Critical / Driving Path Movement */}
      <UiSectionTitle>Critical / Driving Path Movement</UiSectionTitle>
      <div style={{ fontSize: 10, color: C.purple, fontWeight: 700, textTransform: "uppercase", marginBottom: 8 }}>{cp.methodologyNote}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
        <StatCard label="Stayed Critical" value={cp.stayedCriticalCount ?? 0} />
        <StatCard label="Became Critical" value={cp.becameCriticalCount ?? 0} color={(cp.becameCriticalCount ?? 0) > 0 ? C.red : undefined} />
        <StatCard label="Left Critical Path" value={cp.leftCriticalPathCount ?? 0} color={C.green} />
        <StatCard label="Critical Activities Slipped" value={cp.criticalActivitySlippedCount ?? 0} color={(cp.criticalActivitySlippedCount ?? 0) > 0 ? C.red : undefined} />
        {cp.criticalPathForecastDelayDays != null && <StatCard label="Critical Path Forecast Delay" value={`${cp.criticalPathForecastDelayDays > 0 ? "+" : ""}${cp.criticalPathForecastDelayDays}d`} color={cp.criticalPathForecastDelayDays > 0 ? C.red : C.green} />}
      </div>
      {cp.pathDivergence && (
        <div style={{ background: `${C.purple}10`, border: `1px solid ${C.purple}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12, fontSize: 12, color: C.text }}>
          Path divergence begins at <strong>{cp.pathDivergence.activityId} — {cp.pathDivergence.activityName}</strong>. <span style={{ color: C.muted2, fontStyle: "italic" }}>{cp.pathDivergence.note}</span>
        </div>
      )}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 300px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Previous Critical/Driving Path Trace ({(cp.stayedCritical || []).length + (cp.leftCriticalPath || []).length})</div>
          <VirtualizedTable
            rows={[...(cp.stayedCritical || []), ...(cp.leftCriticalPath || [])]}
            emptyMessage="No critical activities in the previous update."
            columns={[
              { key: "activityId", label: "ID", width: 90 },
              { key: "activityName", label: "Name", width: 200 },
              { key: "status", label: "Status", width: 130, render: (r) => (cp.leftCriticalPath || []).some((x: any) => x.activityId === r.activityId) ? <span style={{ color: C.green }}>Left this update</span> : <span style={{ color: C.muted2 }}>Still critical</span> },
            ]}
          />
        </div>
        <div style={{ flex: "1 1 300px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Current Critical/Driving Path Trace ({(cp.stayedCritical || []).length + (cp.becameCritical || []).length})</div>
          <VirtualizedTable
            rows={[...(cp.stayedCritical || []), ...(cp.becameCritical || [])]}
            emptyMessage="No critical activities in the current update."
            columns={[
              { key: "activityId", label: "ID", width: 90 },
              { key: "activityName", label: "Name", width: 200 },
              { key: "status", label: "Status", width: 130, render: (r) => (cp.becameCritical || []).some((x: any) => x.activityId === r.activityId) ? <span style={{ color: C.red }}>New this update</span> : <span style={{ color: C.muted2 }}>Still critical</span> },
            ]}
          />
        </div>
      </div>

      {/* 9. Logic Changes */}
      <UiSectionTitle>Logic Changes ({logic.addedCount ?? 0} added / {logic.removedCount ?? 0} removed / {logic.changedCount ?? 0} changed)</UiSectionTitle>
      <VirtualizedTable
        rows={[
          ...(logic.added || []).map((e: any) => ({ ...e, changeType: "PREDECESSOR ADDED" })),
          ...(logic.removed || []).map((e: any) => ({ ...e, changeType: "PREDECESSOR REMOVED" })),
          ...(logic.changed || []).map((e: any) => ({ ...e, changeType: e.field === "relationshipType" ? "RELATIONSHIP TYPE CHANGED" : "LAG CHANGED" })),
        ]}
        emptyMessage="No logic (relationship) changes this update."
        columns={[
          { key: "successorId", label: "Activity ID", width: 100, render: (r) => r.successorId || "Name unavailable" },
          { key: "changeType", label: "Change Type", width: 190 },
          { key: "predecessorId", label: "Predecessor", width: 130, render: (r) => r.predecessorId || "Name unavailable" },
          { key: "relationship", label: "Previous → Current", width: 220, render: (r) => r.field ? `${r.previous ?? "—"} → ${r.current ?? "—"}` : `${r.relType || "FS"}${r.lagDays ? ` ${r.lagDays > 0 ? "+" : ""}${r.lagDays}d` : ""}` },
        ]}
      />

      {/* 10. Duration Changes */}
      <UiSectionTitle>Original Duration Changes ({(duration.originalDurationChanges || []).length})</UiSectionTitle>
      <VirtualizedTable
        rows={duration.originalDurationChanges || []}
        emptyMessage="No material Original Duration changes this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 220 },
          { key: "wbs", label: "WBS", width: 120 },
          { key: "change", label: "Original Duration", width: 220, render: (r) => `${r.previousDuration}d → ${r.currentDuration}d (${r.delta > 0 ? "+" : ""}${r.delta}d)` },
        ]}
      />
      <UiSectionTitle>Remaining Duration Changes ({(duration.remainingDurationChanges || []).length})</UiSectionTitle>
      <VirtualizedTable
        rows={duration.remainingDurationChanges || []}
        emptyMessage="No material Remaining Duration changes this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 220 },
          { key: "wbs", label: "WBS", width: 120 },
          { key: "change", label: "Remaining Duration", width: 220, render: (r) => `${r.previousDuration}d → ${r.currentDuration}d (${r.delta > 0 ? "+" : ""}${r.delta}d)` },
        ]}
      />

      {/* 11. Constraint Changes */}
      <UiSectionTitle>Constraint Changes ({(result.constraintChanges || []).length})</UiSectionTitle>
      <VirtualizedTable
        rows={result.constraintChanges || []}
        emptyMessage="No constraint changes this update."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 200 },
          { key: "changeType", label: "Change Type", width: 130 },
          { key: "description", label: "Description", width: 340 },
        ]}
      />

      {/* 12. Milestone Movement */}
      <UiSectionTitle>Milestone Movement ({(milestones.rows || []).length})</UiSectionTitle>
      <div style={{ fontSize: 10, color: C.muted2, marginBottom: 6 }}>Baseline Variance = Current vs Approved Baseline. This Update Movement = Current vs Previous Update. Never mixed.</div>
      <VirtualizedTable
        rows={milestones.rows || []}
        emptyMessage="No milestone movement this update."
        columns={[
          { key: "activityId", label: "ID", width: 90 },
          { key: "activityName", label: "Milestone", width: 200 },
          { key: "baselineDate", label: "Baseline Date", width: 110 },
          { key: "previousDate", label: "Previous Date", width: 110 },
          { key: "currentDate", label: "Current Date", width: 110 },
          { key: "baselineVarianceDays", label: "Baseline Variance", width: 130, render: (r) => r.baselineVarianceDays != null ? `${r.baselineVarianceDays > 0 ? "+" : ""}${r.baselineVarianceDays}d` : "—" },
          { key: "movement", label: "This Update Movement", width: 160, render: (r) => movementDisplay(r.movementDays, r.movementWorkingDays, r.workingDayCalendarAvailable) },
          { key: "previousTotalFloat", label: "Previous TF", width: 90, render: (r) => r.previousTotalFloat != null ? `${r.previousTotalFloat}d` : "—" },
          { key: "currentTotalFloat", label: "Current TF", width: 90, render: (r) => r.currentTotalFloat != null ? `${r.currentTotalFloat}d` : "—" },
          { key: "classification", label: "Status", width: 170, render: (r) => <span style={{ color: FLAG_COLOR[r.classification] || C.text, fontWeight: 700 }}>{String(r.classification).replace(/_/g, " ")}</span> },
        ]}
      />

      {/* 13. Driver Analysis */}
      <UiSectionTitle>Update Driver Analysis</UiSectionTitle>
      <div style={{ fontSize: 11, color: C.muted2, fontStyle: "italic", marginBottom: 8 }}>{drivers.methodologyNote}</div>
      <VirtualizedTable
        rows={drivers.drivers || []}
        emptyMessage="No slipped activities to rank."
        columns={[
          { key: "group", label: drivers.groupBy ? drivers.groupBy[0].toUpperCase() + drivers.groupBy.slice(1) : "Group", width: 160 },
          { key: "slippedCount", label: "Slipped", width: 90 },
          { key: "cumulativeFinishMovementDays", label: "Cumulative Movement", width: 190, render: (r) => `${r.cumulativeFinishMovementDays > 0 ? "+" : ""}${r.cumulativeFinishMovementDays} activity-days` },
          { key: "averageFinishMovementDays", label: "Average Movement", width: 150, render: (r) => r.averageFinishMovementDays != null ? `${r.averageFinishMovementDays}d` : "—" },
          { key: "newlyNegativeFloatCount", label: "Newly Neg. Float", width: 130 },
          { key: "newlyCriticalCount", label: "Newly Critical", width: 120 },
          { key: "missedForecastStartsCount", label: "Missed Starts", width: 120 },
          { key: "missedForecastFinishesCount", label: "Missed Finishes", width: 130 },
        ]}
      />

      {/* 14. 4-Week Plan Change */}
      <UiSectionTitle>{uiLookaheadWeeksLabel(result)} Plan Change</UiSectionTitle>
      {!lookaheadChange.available ? (
        <div style={{ padding: 16, color: C.muted2, fontSize: 12, fontStyle: "italic" }}>{lookaheadChange.reason}</div>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 10 }}>
            <StatCard label="Carryover Unfinished Work" value={lookaheadChange.carryoverCount ?? 0} />
            <StatCard label="Newly Entering" value={lookaheadChange.newlyEnteringCount ?? 0} color={C.gold} />
            <StatCard label="Pushed Out" value={lookaheadChange.pushedOutCount ?? 0} color={(lookaheadChange.pushedOutCount ?? 0) > 0 ? C.red : undefined} />
            <StatCard label="Newly Critical Near-Term" value={lookaheadChange.newlyCriticalNearTermCount ?? 0} color={(lookaheadChange.newlyCriticalNearTermCount ?? 0) > 0 ? C.red : undefined} />
          </div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 300px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Pushed Out of Look-Ahead</div>
              <VirtualizedTable rows={lookaheadChange.pushedOut || []} emptyMessage="Nothing was pushed out this update." columns={[
                { key: "activityId", label: "ID", width: 90 }, { key: "activityName", label: "Name", width: 220 }, { key: "status", label: "Status", width: 160 },
              ]} />
            </div>
            <div style={{ flex: "1 1 300px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Newly Entering Look-Ahead</div>
              <VirtualizedTable rows={lookaheadChange.newlyEntering || []} emptyMessage="Nothing new entered this update." columns={[
                { key: "activityId", label: "ID", width: 90 }, { key: "activityName", label: "Name", width: 220 }, { key: "status", label: "Status", width: 160 },
              ]} />
            </div>
          </div>
        </>
      )}

      {/* 15. Detailed Activity Movement — complete analyzed population, virtualized, KPI-filterable */}
      <UiSectionTitle>
        Detailed Activity Movement {movementFilter ? `— filtered to ${movementFilter.replace(/_/g, " ")}` : ""} ({filteredMovement.length.toLocaleString()} of {movementRows.length.toLocaleString()} analyzed)
      </UiSectionTitle>
      <div style={{ fontSize: 10, color: C.muted2, marginBottom: 6 }}>Baseline Variance = Current vs Approved Baseline. This Update Movement = Current vs Previous Update. Never mixed.</div>
      <div style={{ marginBottom: 8 }}>
        <input value={movementSearch} onChange={(e) => setMovementSearch(e.target.value)} placeholder="Search Activity ID or Name…"
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 240 }} />
      </div>
      <VirtualizedTable
        rows={filteredMovement}
        emptyMessage="No activities match the current filter."
        columns={[
          { key: "activityId", label: "Activity ID", width: 100 },
          { key: "activityName", label: "Activity Name", width: 200 },
          { key: "wbs", label: "WBS", width: 100 },
          { key: "baselineFinish", label: "Baseline Finish", width: 110, render: (r) => r.baselineFinish || "—" },
          { key: "previousFinish", label: "Previous Finish", width: 110 },
          { key: "currentFinish", label: "Current Finish", width: 110 },
          { key: "baselineVarianceDays", label: "Baseline Variance", width: 130, render: (r) => r.baselineVarianceDays != null ? `${r.baselineVarianceDays > 0 ? "+" : ""}${r.baselineVarianceDays}d` : "—" },
          { key: "movement", label: "This Update Movement", width: 160, render: (r) => movementDisplay(r.finishMovementDays, r.finishMovementWorkingDays, r.workingDayCalendarAvailable) },
          { key: "previousTotalFloat", label: "Previous TF", width: 90, render: (r) => r.previousTotalFloat != null ? `${r.previousTotalFloat}d` : "—" },
          { key: "currentTotalFloat", label: "Current TF", width: 90, render: (r) => r.currentTotalFloat != null ? `${r.currentTotalFloat}d` : "—" },
          { key: "currentPctComplete", label: "% Complete", width: 90, render: (r) => r.currentPctComplete != null ? `${r.currentPctComplete}%` : "—" },
          { key: "flags", label: "Status / Flags", width: 260, render: (r) => (r.flags || []).map((f: string) => <span key={f} style={{ color: FLAG_COLOR[f] || C.text, fontWeight: 700, marginRight: 6 }}>{f.replace(/_/g, " ")}</span>) },
        ]}
      />
    </div>
  );
}
