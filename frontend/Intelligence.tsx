import { useEffect, useState } from "react";
import { useProjectsChangedTick } from "./projectEvents";
import { sfetch, setProjectScope } from "./projectScope";
import { versionSelectLabel, dedupeVersionsById } from "./dateFormat";

const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};
const API = "";

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 150 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function useProjectsAndVersions() {
  const [projects, setProjects] = useState<any[]>([]);
  const projectsTick = useProjectsChangedTick();
  const [projectId, setProjectId] = useState("");
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

const AI_ERROR_LABELS: Record<string, string> = {
  authentication: "The AI provider rejected the API key (authentication error).",
  billing_quota: "The AI provider account has no remaining credits or quota (billing).",
  rate_limit: "The AI provider is rate-limiting requests — try again shortly.",
  model: "The configured AI model is unavailable to this account.",
  timeout: "The AI provider timed out.",
  network: "The AI provider could not be reached (network error).",
  provider: "The AI provider returned an error.",
  bad_response: "The AI provider returned an unexpected response.",
};
function aiErrorText(j: any): string {
  const label = j?.errorType ? AI_ERROR_LABELS[j.errorType] : undefined;
  return label ? `${label} Schedule analysis remains available.` : String(j?.error || "AI request failed.");
}

function NotConfiguredNotice() {
  return (
    <div style={{ background: `${C.amber}12`, border: `1px solid ${C.amber}50`, borderRadius: 10, padding: "14px 18px", marginBottom: 16 }}>
      <div style={{ fontWeight: 700, color: C.amber, marginBottom: 4 }}>AI Chat is not configured</div>
      <div style={{ fontSize: 12, color: C.muted }}>
        Schedule analysis remains available. An administrator must configure the AI provider to enable conversational analysis.
        The deterministic facts below are still fully available without it.
      </div>
    </div>
  );
}

function ContextFactsPanel({ context }: { context: any }) {
  if (!context) return null;
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Deterministic Facts Available</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10, fontSize: 12 }}>
        <div><div style={{ color: C.muted, fontSize: 10 }}>Overall Risk</div><div style={{ fontWeight: 700 }}>{context.riskOverall?.score} — {context.riskOverall?.level}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10 }}>Milestones</div><div style={{ fontWeight: 700 }}>{context.milestoneCount}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10 }}>Slipped Milestones</div><div style={{ fontWeight: 700 }}>{context.slippedMilestones?.length || 0}</div></div>
        <div><div style={{ color: C.muted, fontSize: 10 }}>Top Finish Slips</div><div style={{ fontWeight: 700 }}>{context.topFinishSlips?.length || 0}</div></div>
      </div>
      {context.comparisonNarrative && <div style={{ marginTop: 10, fontSize: 12, color: C.text, fontStyle: "italic" }}>{context.comparisonNarrative}</div>}
    </div>
  );
}

// ─── AI SCHEDULE REVIEW ─────────────────────────────────────────────────────

const REPORT_TYPES = [
  { value: "executive", label: "Executive" }, { value: "senior_scheduler", label: "Senior Scheduler" },
  { value: "weekly", label: "Weekly Update" }, { value: "monthly", label: "Monthly Report" },
  { value: "big_room", label: "Big Room" }, { value: "risk_review", label: "Risk Review" },
];

const REVIEW_SECTIONS: [string, string][] = [
  ["executiveSummary", "Executive Summary"], ["overallCondition", "Overall Schedule Condition"],
  ["criticalPath", "Critical Path"], ["progress", "Progress"], ["majorVariance", "Major Variance"],
  ["milestones", "Milestones"], ["engineeringRisks", "Engineering Risks"],
  ["procurementRisks", "Procurement Risks"], ["constructionRisks", "Construction Risks"],
];
const REVIEW_LISTS: [string, string][] = [
  ["topAreasOfConcern", "Top Areas of Concern"], ["positiveTrends", "Positive Trends"],
  ["recoveryOpportunities", "Recovery Opportunities"], ["questionsForProjectTeam", "Questions for Project Team"],
  ["recommendedMeetingDiscussionPoints", "Recommended Meeting Discussion Points"],
];

function AiReview({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  const [reportType, setReportType] = useState("executive");
  const [focusArea, setFocusArea] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null); setResult(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/ai-review/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: ctx.versionId, reportType, focusArea: focusArea || undefined }),
    })
      .then(async r => { const t = await r.text(); const j = JSON.parse(t); if (!r.ok && r.status !== 502) throw new Error(t); return j; })
      .then(setResult).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  };

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <Select value={reportType} onChange={setReportType} options={REPORT_TYPES} />
        <input value={focusArea} onChange={e => setFocusArea(e.target.value)} placeholder="Focus on one Area (optional)…"
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 200 }} />
        <button onClick={generate} disabled={!ctx.versionId || loading}
          style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "7px 18px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
          {loading ? "Generating…" : "Generate Review"}
        </button>
      </div>

      {error && <div style={{ color: C.red, marginBottom: 12 }}>⚠ {error}</div>}
      {result && !result.configured && <NotConfiguredNotice />}
      {result && result.error && <div style={{ color: C.red, background: `${C.red}10`, border: `1px solid ${C.red}40`, borderRadius: 8, padding: 12, marginBottom: 12 }}>⚠ {result.error}</div>}
      {result && result.context && !result.review && <ContextFactsPanel context={result.context} />}

      {result?.review && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {REVIEW_SECTIONS.filter(([k]) => result.review[k]).map(([k, label]) => (
            <div key={k} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.accent, textTransform: "uppercase", marginBottom: 6 }}>{label}</div>
              <div style={{ fontSize: 13, color: C.text, lineHeight: 1.6 }}>{result.review[k]}</div>
            </div>
          ))}
          {REVIEW_LISTS.filter(([k]) => result.review[k]?.length).map(([k, label]) => (
            <div key={k} style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 18px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>{label}</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: C.text, lineHeight: 1.8 }}>
                {result.review[k].map((item: string, i: number) => <li key={i}>{item}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── AI CHAT ────────────────────────────────────────────────────────────────

const SAMPLE_QUESTIONS = [
  "Why is this project delayed?", "What is driving project completion?", "What changed this week?",
  "Which area has the most risk?", "What are the top schedule risks?", "What milestones moved?",
];

function AiChat({ ctx }: { ctx: ReturnType<typeof useProjectsAndVersions> }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<{ role: "user" | "assistant"; text: string; references?: any[]; configured?: boolean; error?: string }[]>([]);
  const [sending, setSending] = useState(false);

  const send = (q?: string) => {
    const text = (q ?? question).trim();
    if (!text || !ctx.projectId || !ctx.versionId) return;
    setMessages(m => [...m, { role: "user", text }]);
    setQuestion("");
    setSending(true);
    sfetch(`${API}/api/projects/${ctx.projectId}/ai-chat/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, version: ctx.versionId }),
    })
      .then(async r => { const t = await r.text(); const j = JSON.parse(t); if (!r.ok && r.status !== 502) throw new Error(t); return j; })
      .then(j => {
        if (!j.configured) { setMessages(m => [...m, { role: "assistant", text: "", configured: false }]); return; }
        if (j.error) { setMessages(m => [...m, { role: "assistant", text: "", error: aiErrorText(j) }]); return; }
        setMessages(m => [...m, { role: "assistant", text: j.answer, references: j.references }]);
      })
      .catch((e: any) => setMessages(m => [...m, { role: "assistant", text: "", error: e.message || String(e) }]))
      .finally(() => setSending(false));
  };

  return (
    <div>
      <div style={{ minHeight: 300, maxHeight: 460, overflowY: "auto", background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12 }}>
        {messages.length === 0 && (
          <div>
            <div style={{ color: C.muted, fontSize: 13, marginBottom: 10 }}>Ask a question about this project's schedule.</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {SAMPLE_QUESTIONS.map(q => (
                <button key={q} onClick={() => send(q)} disabled={!ctx.versionId}
                  style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 14, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontFamily: "inherit" }}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", marginBottom: 10 }}>
            <div style={{ maxWidth: "80%", background: m.role === "user" ? C.accent : C.card2, color: m.role === "user" ? "#fff" : C.text, borderRadius: 10, padding: "8px 14px", fontSize: 13 }}>
              {m.role === "user" && m.text}
              {m.role === "assistant" && m.configured === false && <NotConfiguredNotice />}
              {m.role === "assistant" && m.error && <span style={{ color: C.red }}>⚠ {m.error}</span>}
              {m.role === "assistant" && m.text && (
                <>
                  <div>{m.text}</div>
                  {m.references && m.references.length > 0 && (
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {m.references.map((r: any, ri: number) => (
                        <span key={ri} title={r.note} style={{ background: `${C.accent}18`, color: C.accent, borderRadius: 6, padding: "2px 8px", fontSize: 10, fontFamily: "monospace", cursor: "default" }}>
                          {r.activityId}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => e.key === "Enter" && send()}
          placeholder={ctx.versionId ? "Ask about this schedule…" : "Select a project and version first…"} disabled={!ctx.versionId}
          style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 14px", fontSize: 13, fontFamily: "inherit" }} />
        <button onClick={() => send()} disabled={!question.trim() || sending || !ctx.versionId}
          style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 8, padding: "9px 20px", cursor: "pointer", fontSize: 13, fontWeight: 700, fontFamily: "inherit" }}>
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

// ─── ROOT ───────────────────────────────────────────────────────────────────

export default function Intelligence({ onOpenRecovery }: { onOpenRecovery?: (projectId: string, versionId?: string) => void } = {}) {
  const ctx = useProjectsAndVersions();
  const [tab, setTab] = useState<"review" | "chat" | "recovery">("review");

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Intelligence</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        AI analysis is always grounded in ScheduleIQ's deterministic engines — it never invents schedule facts.
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Project</div>
          <Select value={ctx.projectId} onChange={ctx.setProjectId} placeholder="Select a project…" options={ctx.projects.map((p: any) => ({ value: p.id, label: p.name }))} />
        </div>
        {ctx.projectId && ctx.versions.length > 0 && (
          <div>
            <div style={{ fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase" }}>Version</div>
            <Select value={ctx.versionId} onChange={ctx.setVersionId} options={ctx.versions.map((v: any) => ({ value: v.id, label: versionSelectLabel(v) }))} />
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 16 }}>
        {[{ id: "review", label: "AI Schedule Review" }, { id: "chat", label: "AI Chat" }, { id: "recovery", label: "Recovery Planner" }].map(t => (
          <button key={t.id} onClick={() => setTab(t.id as any)}
            style={{ background: "transparent", border: "none", borderBottom: `2px solid ${tab === t.id ? C.accent : "transparent"}`, color: tab === t.id ? C.accent : C.muted2, padding: "8px 14px", cursor: "pointer", fontSize: 13, fontFamily: "inherit", fontWeight: tab === t.id ? 800 : 600 }}>
            {t.label}
          </button>
        ))}
      </div>

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && tab === "review" && <AiReview ctx={ctx} />}
      {ctx.projectId && tab === "chat" && <AiChat ctx={ctx} />}
      {ctx.projectId && tab === "recovery" && (
        <div style={{ textAlign: "center", padding: 60 }}>
          <div style={{ fontSize: 14, color: C.text, marginBottom: 6, fontWeight: 700 }}>Recovery Planning moved to Risk & Recovery</div>
          <div style={{ fontSize: 13, color: C.muted, marginBottom: 18, maxWidth: 480, marginLeft: "auto", marginRight: "auto" }}>
            Scenario creation, comparison, side effects, mitigation actions, and recovery tracking now live in one workspace
            alongside the Risk Register and Driving Chain analysis, so a scenario is always traceable back to the risk it addresses.
          </div>
          <button onClick={() => onOpenRecovery && onOpenRecovery(ctx.projectId, ctx.versionId || undefined)} disabled={!onOpenRecovery}
            style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 8, padding: "10px 22px", cursor: onOpenRecovery ? "pointer" : "default", fontSize: 13, fontWeight: 700, fontFamily: "inherit" }}>
            Open Risk & Recovery →
          </button>
        </div>
      )}
    </div>
  );
}
