import { useEffect, useMemo, useRef, useState } from "react";
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

// ─── SHARED PRIMITIVES ──────────────────────────────────────────────────────

function Select({ value, onChange, options, placeholder }: any) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", color: C.text, minWidth: 130 }}>
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

// Dependency-free windowed rendering — same proven pattern already used in
// RiskIntelligence.tsx. Purely a rendering optimization: the full `items`
// array is always the complete, unfiltered-by-count analyzed population.
function VirtualRows({ items, rowHeight, height, renderRow }: {
  items: any[]; rowHeight: number; height: number; renderRow: (item: any, index: number) => React.ReactNode;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const overscan = 12;
  const total = items.length;
  const totalHeight = total * rowHeight;
  const startIdx = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visibleCount = Math.ceil(height / rowHeight) + overscan * 2;
  const endIdx = Math.min(total, startIdx + visibleCount);
  const visible = items.slice(startIdx, endIdx);
  return (
    <div onScroll={e => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      style={{ height, overflow: "auto", border: `1px solid ${C.border}`, borderRadius: 10, background: C.card }}>
      <div style={{ height: totalHeight, position: "relative", minWidth: "max-content" }}>
        <div style={{ position: "absolute", top: startIdx * rowHeight, left: 0, right: 0 }}>
          {visible.map((item, i) => renderRow(item, startIdx + i))}
        </div>
      </div>
    </div>
  );
}

// ─── COLUMN LIBRARY ─────────────────────────────────────────────────────────
// One definition per column, shared by the table, the Column Manager, and
// presets — never a second, differently-named column set per page.

type ColDef = {
  key: string; label: string; category: string; width: number;
  type?: "text" | "number" | "date" | "bool" | "signal";
  frozen?: boolean;
};

const CATEGORIES = [
  "Identity", "Status", "Dates", "Duration", "Variance", "Float", "Logic",
  "Constraints", "Calendar", "Milestones", "Update Intelligence", "Risk & Recovery",
  "Resources", "Cost", "Traceability",
];

const COLUMNS: ColDef[] = [
  // Identity
  { key: "activityId", label: "Activity ID", category: "Identity", width: 130, frozen: true },
  { key: "activityName", label: "Activity Name", category: "Identity", width: 260, frozen: true },
  { key: "project", label: "Project", category: "Identity", width: 140 },
  { key: "wbs", label: "WBS", category: "Identity", width: 140 },
  { key: "wbsPath", label: "WBS Path", category: "Identity", width: 220 },
  { key: "area", label: "Area", category: "Identity", width: 110 },
  { key: "discipline", label: "Discipline", category: "Identity", width: 120 },
  { key: "contractor", label: "Contractor", category: "Identity", width: 140 },
  { key: "system", label: "System", category: "Identity", width: 120 },
  { key: "activityType", label: "Activity Type", category: "Identity", width: 110 },
  { key: "activityStatus", label: "Activity Status", category: "Identity", width: 110 },
  // Status
  { key: "pctComplete", label: "% Complete", category: "Status", width: 100, type: "number" },
  { key: "pctCompleteType", label: "% Complete Type", category: "Status", width: 120 },
  { key: "physPctComplete", label: "Physical % Complete", category: "Status", width: 130, type: "number" },
  { key: "started", label: "Started", category: "Status", width: 80, type: "bool" },
  { key: "finished", label: "Finished", category: "Status", width: 80, type: "bool" },
  { key: "inProgress", label: "In Progress", category: "Status", width: 90, type: "bool" },
  { key: "shouldHaveStarted", label: "Should Have Started", category: "Status", width: 140, type: "bool" },
  { key: "shouldHaveFinished", label: "Should Have Finished", category: "Status", width: 140, type: "bool" },
  { key: "overdue", label: "Overdue", category: "Status", width: 80, type: "bool" },
  // Duration
  { key: "originalDuration", label: "Original Duration", category: "Duration", width: 120, type: "number" },
  { key: "originalDurationSource", label: "Original Duration Source", category: "Duration", width: 160 },
  { key: "remainingDuration", label: "Remaining Duration", category: "Duration", width: 130, type: "number" },
  { key: "actualDuration", label: "Actual Duration", category: "Duration", width: 120, type: "number" },
  { key: "atCompletionDuration", label: "At Completion Duration", category: "Duration", width: 150, type: "number" },
  { key: "baselineOriginalDuration", label: "Baseline Original Duration", category: "Duration", width: 160, type: "number" },
  { key: "previousRemainingDuration", label: "Previous Remaining Duration", category: "Duration", width: 170, type: "number" },
  { key: "currentRemainingDuration", label: "Current Remaining Duration", category: "Duration", width: 170, type: "number" },
  { key: "remainingDurationChange", label: "Remaining Duration Change", category: "Duration", width: 160, type: "number" },
  { key: "remainingDurationGrowth", label: "Remaining Duration Growth", category: "Duration", width: 160, type: "number" },
  { key: "remainingDurationReduction", label: "Remaining Duration Reduction", category: "Duration", width: 170, type: "number" },
  { key: "durationVariance", label: "Duration Variance", category: "Duration", width: 130, type: "number" },
  // Dates
  { key: "baselineStart", label: "Baseline Start", category: "Dates", width: 110, type: "date" },
  { key: "baselineFinish", label: "Baseline Finish", category: "Dates", width: 110, type: "date" },
  { key: "currentStart", label: "Current Start", category: "Dates", width: 110, type: "date" },
  { key: "currentFinish", label: "Current Finish", category: "Dates", width: 110, type: "date" },
  { key: "forecastStart", label: "Forecast Start", category: "Dates", width: 110, type: "date" },
  { key: "forecastFinish", label: "Forecast Finish", category: "Dates", width: 110, type: "date" },
  { key: "earlyStart", label: "Early Start", category: "Dates", width: 110, type: "date" },
  { key: "earlyFinish", label: "Early Finish", category: "Dates", width: 110, type: "date" },
  { key: "lateStart", label: "Late Start", category: "Dates", width: 110, type: "date" },
  { key: "lateFinish", label: "Late Finish", category: "Dates", width: 110, type: "date" },
  { key: "currentRemainingStart", label: "Current Remaining Start", category: "Dates", width: 150, type: "date" },
  { key: "currentRemainingFinish", label: "Current Remaining Finish", category: "Dates", width: 150, type: "date" },
  { key: "expectedFinish", label: "Expected Finish", category: "Dates", width: 120, type: "date" },
  { key: "actualStart", label: "Actual Start", category: "Dates", width: 110, type: "date" },
  { key: "actualFinish", label: "Actual Finish", category: "Dates", width: 110, type: "date" },
  // Variance
  { key: "startVarianceDays", label: "Start Variance vs Baseline (d)", category: "Variance", width: 170, type: "number" },
  { key: "finishVarianceDays", label: "Finish Variance vs Baseline (d)", category: "Variance", width: 175, type: "number" },
  { key: "startVarianceWorkingDays", label: "Start Variance (wd)", category: "Variance", width: 140, type: "number" },
  { key: "finishVarianceWorkingDays", label: "Finish Variance (wd)", category: "Variance", width: 140, type: "number" },
  { key: "startMovementDays", label: "Previous→Current Start Movement", category: "Variance", width: 190, type: "number" },
  { key: "finishMovementDays", label: "Previous→Current Finish Movement", category: "Variance", width: 195, type: "number" },
  { key: "updateMovementDays", label: "Update Movement", category: "Variance", width: 130, type: "number" },
  // Float
  { key: "baselineTotalFloat", label: "Baseline Total Float", category: "Float", width: 140, type: "number" },
  { key: "previousTotalFloat", label: "Previous Total Float", category: "Float", width: 140, type: "number" },
  { key: "currentTotalFloat", label: "Current Total Float", category: "Float", width: 140, type: "number" },
  { key: "floatChangeVsBaseline", label: "Float Change vs Baseline", category: "Float", width: 160, type: "number" },
  { key: "floatChangeVsPrevious", label: "Float Change vs Previous", category: "Float", width: 160, type: "number" },
  { key: "importedCurrentTotalFloat", label: "Imported P6 TF (audit)", category: "Float", width: 150, type: "number" },
  { key: "freeFloat", label: "Free Float", category: "Float", width: 100, type: "number" },
  { key: "importedFreeFloat", label: "Imported P6 Free Float (audit)", category: "Float", width: 170, type: "number" },
  { key: "negativeFloat", label: "Negative Float", category: "Float", width: 110, type: "bool" },
  { key: "newlyNegativeFloat", label: "Newly Negative", category: "Float", width: 110, type: "bool" },
  { key: "recoveredFromNegativeFloat", label: "Recovered From Negative", category: "Float", width: 150, type: "bool" },
  { key: "critical", label: "Critical (Imported P6)", category: "Float", width: 140, type: "bool" },
  { key: "criticalActionable", label: "Critical (Current, Actionable)", category: "Float", width: 190, type: "bool" },
  { key: "newlyCritical", label: "Newly Critical", category: "Float", width: 110, type: "bool" },
  { key: "noLongerCritical", label: "No Longer Critical", category: "Float", width: 130, type: "bool" },
  { key: "nearCritical", label: "Near Critical", category: "Float", width: 100, type: "bool" },
  { key: "driving", label: "Driving", category: "Float", width: 80, type: "bool" },
  { key: "onLongestPath", label: "Longest Path (P6)", category: "Float", width: 130, type: "bool" },
  // Logic
  { key: "predecessorCount", label: "Predecessor Count", category: "Logic", width: 130, type: "number" },
  { key: "successorCount", label: "Successor Count", category: "Logic", width: 130, type: "number" },
  { key: "relationshipCount", label: "Relationship Count", category: "Logic", width: 140, type: "number" },
  { key: "fsCount", label: "FS Count", category: "Logic", width: 90, type: "number" },
  { key: "ssCount", label: "SS Count", category: "Logic", width: 90, type: "number" },
  { key: "ffCount", label: "FF Count", category: "Logic", width: 90, type: "number" },
  { key: "sfCount", label: "SF Count", category: "Logic", width: 90, type: "number" },
  { key: "positiveLag", label: "Positive Lag", category: "Logic", width: 100, type: "bool" },
  { key: "negativeLag", label: "Negative Lag", category: "Logic", width: 100, type: "bool" },
  { key: "maximumLag", label: "Maximum Lag", category: "Logic", width: 110, type: "number" },
  { key: "openStart", label: "Open Start", category: "Logic", width: 90, type: "bool" },
  { key: "openFinish", label: "Open Finish", category: "Logic", width: 90, type: "bool" },
  { key: "logicChanged", label: "Logic Changed", category: "Logic", width: 110, type: "bool" },
  { key: "relationshipAdded", label: "Relationship Added", category: "Logic", width: 140, type: "bool" },
  { key: "relationshipRemoved", label: "Relationship Removed", category: "Logic", width: 150, type: "bool" },
  { key: "drivingPredecessorId", label: "Driving Predecessor", category: "Logic", width: 160 },
  // Constraints
  { key: "constraintType", label: "Constraint Type", category: "Constraints", width: 120 },
  { key: "constraintDate", label: "Constraint Date", category: "Constraints", width: 110, type: "date" },
  { key: "constraint2Type", label: "Secondary Constraint Type", category: "Constraints", width: 160 },
  { key: "constraint2Date", label: "Secondary Constraint Date", category: "Constraints", width: 160, type: "date" },
  { key: "constraintChanged", label: "Constraint Changed", category: "Constraints", width: 140, type: "bool" },
  // Calendar
  { key: "calendarName", label: "Calendar Name", category: "Calendar", width: 140 },
  { key: "calendarAvailable", label: "Calendar Confidence", category: "Calendar", width: 140, type: "bool" },
  { key: "hoursPerDay", label: "Hours/Day", category: "Calendar", width: 100, type: "number" },
  { key: "workingDaysPerWeek", label: "Working Days/Week", category: "Calendar", width: 140, type: "number" },
  { key: "workweekDescription", label: "Workweek", category: "Calendar", width: 160 },
  // Milestones
  { key: "isMilestone", label: "Is Milestone", category: "Milestones", width: 100, type: "bool" },
  { key: "milestoneRiskLevel", label: "Milestone Risk Level", category: "Milestones", width: 140 },
  { key: "milestoneVarianceDays", label: "Milestone Variance (d)", category: "Milestones", width: 150, type: "number" },
  // Update Intelligence
  { key: "slipped", label: "Slipped", category: "Update Intelligence", width: 80, type: "bool" },
  { key: "improved", label: "Improved", category: "Update Intelligence", width: 80, type: "bool" },
  { key: "startedThisUpdate", label: "Started This Update", category: "Update Intelligence", width: 140, type: "bool" },
  { key: "completedThisUpdate", label: "Completed This Update", category: "Update Intelligence", width: 150, type: "bool" },
  { key: "addedSinceBaseline", label: "Added", category: "Update Intelligence", width: 80, type: "bool" },
  { key: "removedFromCurrent", label: "Removed", category: "Update Intelligence", width: 90, type: "bool" },
  { key: "failedForecastStart", label: "Failed Forecast Start", category: "Update Intelligence", width: 150, type: "bool" },
  { key: "failedForecastFinish", label: "Failed Forecast Finish", category: "Update Intelligence", width: 155, type: "bool" },
  { key: "floatDeteriorated", label: "Float Deteriorated", category: "Update Intelligence", width: 130, type: "bool" },
  { key: "floatImproved", label: "Float Improved", category: "Update Intelligence", width: 120, type: "bool" },
  { key: "durationChanged", label: "Duration Changed", category: "Update Intelligence", width: 130, type: "bool" },
  { key: "criticalityChanged", label: "Criticality Changed", category: "Update Intelligence", width: 140, type: "bool" },
  // Risk & Recovery
  { key: "scheduleRisk", label: "Schedule Risk", category: "Risk & Recovery", width: 110, type: "bool" },
  { key: "riskSeverity", label: "Risk Severity", category: "Risk & Recovery", width: 110 },
  { key: "riskUrgency", label: "Risk Urgency", category: "Risk & Recovery", width: 110 },
  { key: "riskStatus", label: "Risk Status", category: "Risk & Recovery", width: 130 },
  // Resources
  { key: "budgetedLaborUnits", label: "Budgeted Labor Units", category: "Resources", width: 150, type: "number" },
  { key: "actualLaborUnits", label: "Actual Labor Units", category: "Resources", width: 140, type: "number" },
  { key: "remainingLaborUnits", label: "Remaining Labor Units", category: "Resources", width: 155, type: "number" },
  { key: "atCompletionLaborUnits", label: "At Completion Labor Units", category: "Resources", width: 170, type: "number" },
  { key: "resourceLoaded", label: "Resource Loaded", category: "Resources", width: 130, type: "bool" },
  // Cost
  { key: "budgetedCost", label: "Budgeted Cost", category: "Cost", width: 120, type: "number" },
  { key: "actualCost", label: "Actual Cost", category: "Cost", width: 110, type: "number" },
  { key: "remainingCost", label: "Remaining Cost", category: "Cost", width: 130, type: "number" },
  { key: "atCompletionCost", label: "At Completion Cost", category: "Cost", width: 150, type: "number" },
  { key: "costLoaded", label: "Cost Loaded", category: "Cost", width: 100, type: "bool" },
];

const COL_BY_KEY: Record<string, ColDef> = Object.fromEntries(COLUMNS.map(c => [c.key, c]));

// ─── PRESETS ────────────────────────────────────────────────────────────────

const PRESETS: Record<string, { label: string; cols: string[] }> = {
  scheduler: {
    label: "Scheduler", cols: [
      "activityId", "activityName", "wbs", "activityStatus", "pctComplete",
      "baselineStart", "baselineFinish", "currentStart", "currentFinish",
      "originalDuration", "remainingDuration", "currentTotalFloat", "critical", "driving",
      "predecessorCount", "successorCount", "constraintType",
    ],
  },
  progress: {
    label: "Progress", cols: [
      "activityId", "activityName", "wbs", "pctComplete", "started", "finished", "inProgress",
      "originalDuration", "remainingDuration", "actualStart", "actualFinish",
      "currentFinish", "forecastFinish", "finishVarianceDays", "overdue",
    ],
  },
  floatAnalysis: {
    label: "Float Analysis", cols: [
      "activityId", "activityName", "wbs", "baselineTotalFloat", "previousTotalFloat", "currentTotalFloat",
      "floatChangeVsBaseline", "floatChangeVsPrevious", "negativeFloat", "newlyNegativeFloat",
      "critical", "nearCritical", "driving",
    ],
  },
  logic: {
    label: "Logic", cols: [
      "activityId", "activityName", "predecessorCount", "successorCount", "fsCount", "ssCount", "ffCount", "sfCount",
      "positiveLag", "negativeLag", "maximumLag", "openStart", "openFinish",
      "constraintType", "constraintDate", "logicChanged",
    ],
  },
  recovery: {
    label: "Recovery", cols: [
      "activityId", "activityName", "remainingDuration", "currentFinish", "currentTotalFloat",
      "negativeFloat", "driving", "scheduleRisk", "riskSeverity", "riskUrgency", "riskStatus",
    ],
  },
  executive: {
    label: "Executive", cols: [
      "activityId", "activityName", "wbs", "pctComplete", "currentFinish", "finishVarianceDays",
      "currentTotalFloat", "critical", "overdue", "isMilestone",
    ],
  },
};

const VISIBLE_COLS_STORAGE_KEY = "scheduleiq.activityAnalysis.visibleCols.v1";

function loadVisibleCols(): string[] {
  try {
    const raw = sessionStorage.getItem(VISIBLE_COLS_STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* per-viewer convenience only — ignore read failures */ }
  return PRESETS.scheduler.cols;
}

// ─── COLUMN MANAGER ─────────────────────────────────────────────────────────

function ColumnManagerDialog({ visibleCols, onApply, onClose }: {
  visibleCols: string[]; onApply: (cols: string[]) => void; onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(visibleCols));
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");

  const filtered = COLUMNS.filter(c =>
    (!category || c.category === category) &&
    (!search || c.label.toLowerCase().includes(search.toLowerCase()))
  );

  const toggle = (key: string) => setSelected(s => {
    const n = new Set(s);
    n.has(key) ? n.delete(key) : n.add(key);
    return n;
  });
  const selectCategory = (cat: string, on: boolean) => setSelected(s => {
    const n = new Set(s);
    COLUMNS.filter(c => c.category === cat).forEach(c => on ? n.add(c.key) : n.delete(c.key));
    return n;
  });

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}>
      <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, width: 720, maxHeight: "80vh", display: "flex", flexDirection: "column", boxShadow: "0 12px 48px rgba(0,0,0,0.3)" }}
        onClick={e => e.stopPropagation()}>
        <div style={{ padding: "16px 20px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>Column Manager</div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 16 }}>✕</button>
        </div>
        <div style={{ padding: "12px 20px", display: "flex", gap: 8 }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search fields…"
            style={{ flex: 1, background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit" }} />
          <Select value={category} onChange={setCategory} placeholder="All categories" options={CATEGORIES.map(c => ({ value: c, label: c }))} />
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 20px 12px" }}>
          {CATEGORIES.filter(cat => !category || cat === category).map(cat => {
            const catCols = filtered.filter(c => c.category === cat);
            if (catCols.length === 0) return null;
            const allOn = catCols.every(c => selected.has(c.key));
            return (
              <div key={cat} style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>{cat}</div>
                  <button onClick={() => selectCategory(cat, !allOn)} style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontSize: 10, fontWeight: 700 }}>
                    {allOn ? "Deselect All" : "Select All"}
                  </button>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 10px" }}>
                  {catCols.map(c => (
                    <label key={c.key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text, padding: "3px 0", cursor: "pointer" }}>
                      <input type="checkbox" checked={selected.has(c.key)} onChange={() => toggle(c.key)} disabled={!!c.frozen} />
                      {c.label}{c.frozen && <span style={{ fontSize: 9, color: C.muted }}> (frozen)</span>}
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "12px 20px", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <button onClick={() => setSelected(new Set(PRESETS.scheduler.cols))}
            style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontSize: 12 }}>
            Reset to Default
          </button>
          <div style={{ display: "flex", gap: 8 }}>
            <span style={{ fontSize: 11, color: C.muted, alignSelf: "center" }}>{selected.size} columns selected</span>
            <button onClick={onClose} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 7, padding: "7px 14px", cursor: "pointer", fontSize: 12 }}>Cancel</button>
            <button onClick={() => onApply(COLUMNS.filter(c => selected.has(c.key)).map(c => c.key))}
              style={{ background: C.accent, border: "none", color: "#fff", borderRadius: 7, padding: "7px 16px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>
              OK
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── VALUE FORMATTING ───────────────────────────────────────────────────────

function formatCellValue(row: any, col: ColDef): { text: string; color?: string } {
  const v = row[col.key];
  if (col.key === "drivingPredecessorId") {
    const dp = row.drivingPredecessor;
    return { text: dp ? `${dp.activityId} (${dp.totalFloat ?? "—"}d)` : "—" };
  }
  if (v === null || v === undefined || v === "") return { text: "—", color: C.muted2 };
  if (col.type === "bool") return v ? { text: "Yes", color: C.text } : { text: "—", color: C.muted2 };
  if (col.type === "date") return { text: formatDataDate(v) };
  if (col.type === "number") {
    const n = Number(v);
    if (Number.isNaN(n)) return { text: "—", color: C.muted2 };
    const rounded = Math.round(n * 100) / 100;
    let color: string | undefined;
    if (col.key.toLowerCase().includes("float") && !col.key.toLowerCase().includes("change")) color = rounded < 0 ? C.red : undefined;
    if (col.key.toLowerCase().includes("variance") || col.key.toLowerCase().includes("movement") || col.key.toLowerCase().includes("change")) {
      color = rounded > 0 ? C.amber : rounded < 0 ? C.green : undefined;
    }
    return { text: `${rounded}`, color };
  }
  return { text: String(v) };
}

// ─── FILTERS ────────────────────────────────────────────────────────────────

type Filters = {
  search: string;
  wbs: string; area: string; discipline: string; contractor: string; system: string;
  status: string; tf: string;
  newlyNegative: boolean; deteriorated: boolean; improved: boolean;
  criticality: string;
  openStart: boolean; openFinish: boolean; negativeLag: boolean; positiveLag: boolean; logicChanged: boolean;
  hasConstraint: boolean; constraintChanged: boolean;
  milestonesOnly: boolean; drivesMilestone: boolean;
  riskSeverity: string;
  minFinishVariance: string;
};

const DEFAULT_FILTERS: Filters = {
  search: "", wbs: "", area: "", discipline: "", contractor: "", system: "",
  status: "", tf: "",
  newlyNegative: false, deteriorated: false, improved: false,
  criticality: "",
  openStart: false, openFinish: false, negativeLag: false, positiveLag: false, logicChanged: false,
  hasConstraint: false, constraintChanged: false,
  milestonesOnly: false, drivesMilestone: false,
  riskSeverity: "", minFinishVariance: "",
};

function applyFilters(rows: any[], f: Filters): any[] {
  return rows.filter(r => {
    if (f.search) {
      const s = f.search.toLowerCase();
      if (!r.activityId.toLowerCase().includes(s) && !(r.activityName || "").toLowerCase().includes(s)) return false;
    }
    if (f.wbs && r.wbs !== f.wbs) return false;
    if (f.area && r.area !== f.area) return false;
    if (f.discipline && r.discipline !== f.discipline) return false;
    if (f.contractor && r.contractor !== f.contractor) return false;
    if (f.system && r.system !== f.system) return false;
    if (f.status === "NOT_STARTED" && r.started) return false;
    if (f.status === "IN_PROGRESS" && !r.inProgress) return false;
    if (f.status === "COMPLETE" && !r.finished) return false;
    if (f.status === "SHOULD_HAVE_STARTED" && !r.shouldHaveStarted) return false;
    if (f.status === "SHOULD_HAVE_FINISHED" && !r.shouldHaveFinished) return false;
    if (f.status === "OVERDUE" && !r.overdue) return false;
    if (f.tf === "NEGATIVE" && !(r.currentTotalFloat != null && r.currentTotalFloat < 0)) return false;
    if (f.tf === "ZERO" && r.currentTotalFloat !== 0) return false;
    if (f.tf === "POSITIVE" && !(r.currentTotalFloat != null && r.currentTotalFloat > 0)) return false;
    if (f.newlyNegative && !r.newlyNegativeFloat) return false;
    if (f.deteriorated && !r.floatDeteriorated) return false;
    if (f.improved && !r.floatImproved) return false;
    if (f.criticality === "CRITICAL" && !r.criticalActionable) return false;
    if (f.criticality === "NEAR_CRITICAL" && !r.nearCritical) return false;
    if (f.criticality === "DRIVING" && !r.driving) return false;
    if (f.criticality === "LONGEST_PATH" && !r.onLongestPath) return false;
    if (f.criticality === "NEWLY_CRITICAL" && !r.newlyCritical) return false;
    if (f.criticality === "NO_LONGER_CRITICAL" && !r.noLongerCritical) return false;
    if (f.openStart && !r.openStart) return false;
    if (f.openFinish && !r.openFinish) return false;
    if (f.negativeLag && !r.negativeLag) return false;
    if (f.positiveLag && !r.positiveLag) return false;
    if (f.logicChanged && !r.logicChanged) return false;
    if (f.hasConstraint && !r.constraintType) return false;
    if (f.constraintChanged && !r.constraintChanged) return false;
    if (f.milestonesOnly && !r.isMilestone) return false;
    if (f.drivesMilestone && !r.drivingPredecessor) return false;
    if (f.riskSeverity && r.riskSeverity !== f.riskSeverity) return false;
    if (f.minFinishVariance) {
      const min = Number(f.minFinishVariance);
      if (!(r.finishVarianceDays != null && r.finishVarianceDays >= min)) return false;
    }
    return true;
  });
}

function activeFilterChips(f: Filters): { key: string; label: string }[] {
  const chips: { key: string; label: string }[] = [];
  if (f.search) chips.push({ key: "search", label: `"${f.search}"` });
  (["wbs", "area", "discipline", "contractor", "system"] as const).forEach(k => {
    if (f[k]) chips.push({ key: k, label: `${k[0].toUpperCase()}${k.slice(1)}: ${f[k]}` });
  });
  if (f.status) chips.push({ key: "status", label: f.status.replace(/_/g, " ") });
  if (f.tf) chips.push({ key: "tf", label: `TF ${f.tf === "NEGATIVE" ? "< 0" : f.tf === "ZERO" ? "= 0" : "> 0"}` });
  if (f.newlyNegative) chips.push({ key: "newlyNegative", label: "Newly Negative" });
  if (f.deteriorated) chips.push({ key: "deteriorated", label: "Float Deteriorated" });
  if (f.improved) chips.push({ key: "improved", label: "Float Improved" });
  if (f.criticality) chips.push({ key: "criticality", label: f.criticality.replace(/_/g, " ") });
  if (f.openStart) chips.push({ key: "openStart", label: "Open Start" });
  if (f.openFinish) chips.push({ key: "openFinish", label: "Open Finish" });
  if (f.negativeLag) chips.push({ key: "negativeLag", label: "Negative Lag" });
  if (f.positiveLag) chips.push({ key: "positiveLag", label: "Positive Lag" });
  if (f.logicChanged) chips.push({ key: "logicChanged", label: "Logic Changed" });
  if (f.hasConstraint) chips.push({ key: "hasConstraint", label: "Has Constraint" });
  if (f.constraintChanged) chips.push({ key: "constraintChanged", label: "Constraint Changed" });
  if (f.milestonesOnly) chips.push({ key: "milestonesOnly", label: "Milestones Only" });
  if (f.drivesMilestone) chips.push({ key: "drivesMilestone", label: "Drives Milestone" });
  if (f.riskSeverity) chips.push({ key: "riskSeverity", label: `Risk: ${f.riskSeverity}` });
  if (f.minFinishVariance) chips.push({ key: "minFinishVariance", label: `Finish Var ≥ ${f.minFinishVariance}d` });
  return chips;
}

// ─── GROUPING ───────────────────────────────────────────────────────────────

const GROUP_FIELDS = [
  { value: "", label: "None" }, { value: "area", label: "Area" }, { value: "wbs", label: "WBS" },
  { value: "discipline", label: "Discipline" }, { value: "contractor", label: "Contractor" },
  { value: "system", label: "System" }, { value: "riskSeverity", label: "Risk Severity" },
  { value: "phase", label: "Phase (EPC)" },
];

// Ported from the legacy Activities register's PhasesSidebar (App.tsx) so the
// same EPC-phase categorization survives that view's retirement. Order matters:
// first keyword match wins, milestone check takes priority same as legacy.
const PHASE_ORDER: { label: string; test: (a: any) => boolean }[] = [
  { label: "Milestones", test: (a) => !!a.isMilestone },
  { label: "Design", test: (a) => matchPhaseKeywords(a, ["design"]) },
  { label: "Engineering", test: (a) => matchPhaseKeywords(a, ["engineer", "engr"]) },
  { label: "Submittals", test: (a) => matchPhaseKeywords(a, ["submittal", "submit", "rfi", "rfq"]) },
  { label: "Construction", test: (a) => matchPhaseKeywords(a, ["construct", "civil", "install", "erect", "fabricat"]) },
  { label: "Testing & Commissioning", test: (a) => matchPhaseKeywords(a, ["test", "commission", "startup", "start-up", "t&c"]) },
  { label: "Close Out", test: (a) => matchPhaseKeywords(a, ["close", "closeout", "punch", "handov", "turnov", "complet"]) },
];

function matchPhaseKeywords(a: any, keywords: string[]): boolean {
  const haystack = [a.activityName || "", a.wbs || "", a.wbsPath || ""].join(" ").toLowerCase();
  return keywords.some(k => haystack.includes(k));
}

function phaseOf(a: any): string {
  const hit = PHASE_ORDER.find(p => p.test(a));
  return hit ? hit.label : "Unassigned";
}

function floatBand(tf: number | null): string {
  if (tf == null) return "Unavailable";
  if (tf < -20) return "Severe Negative";
  if (tf < 0) return "Negative";
  if (tf === 0) return "Zero";
  if (tf <= 10) return "Near Critical";
  if (tf <= 20) return "Low";
  if (tf <= 40) return "Moderate";
  return "High";
}

function groupValue(r: any, field: string): string {
  if (field === "floatBand") return floatBand(r.currentTotalFloat);
  if (field === "phase") return phaseOf(r);
  return r[field] || "Unassigned";
}

function median(nums: number[]): number | null {
  if (nums.length === 0) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function summarizeGroup(rows: any[]) {
  const tfs = rows.map(r => r.currentTotalFloat).filter((v): v is number => v != null);
  const finishVars = rows.map(r => r.finishVarianceDays).filter((v): v is number => v != null);
  return {
    count: rows.length,
    inProgress: rows.filter(r => r.inProgress).length,
    delayed: rows.filter(r => r.finishVarianceDays != null && r.finishVarianceDays > 0).length,
    negativeFloat: rows.filter(r => r.negativeFloat).length,
    critical: rows.filter(r => r.criticalActionable).length,
    minTF: tfs.length ? Math.min(...tfs) : null,
    medianTF: median(tfs),
    maxFinishVariance: finishVars.length ? Math.max(...finishVars) : null,
    milestoneCount: rows.filter(r => r.isMilestone).length,
  };
}

type FlatRow =
  | { kind: "group"; level: number; label: string; summary: ReturnType<typeof summarizeGroup> }
  | { kind: "row"; row: any };

function buildGroupedRows(rows: any[], groupBy: string[]): FlatRow[] {
  const activeGroups = groupBy.filter(Boolean);
  if (activeGroups.length === 0) return rows.map(row => ({ kind: "row", row }));

  function recurse(rowSet: any[], levelIdx: number): FlatRow[] {
    if (levelIdx >= activeGroups.length) return rowSet.map(row => ({ kind: "row", row }));
    const field = activeGroups[levelIdx];
    const buckets = new Map<string, any[]>();
    for (const r of rowSet) {
      const key = groupValue(r, field);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key)!.push(r);
    }
    const out: FlatRow[] = [];
    for (const key of Array.from(buckets.keys()).sort()) {
      const groupRows = buckets.get(key)!;
      out.push({ kind: "group", level: levelIdx, label: key, summary: summarizeGroup(groupRows) });
      out.push(...recurse(groupRows, levelIdx + 1));
    }
    return out;
  }
  return recurse(rows, 0);
}

// ─── MULTI-SORT ─────────────────────────────────────────────────────────────

type SortRule = { key: string; dir: "asc" | "desc" };

const SORTABLE_FIELDS = COLUMNS.filter(c => c.type === "number" || c.type === "date" || c.type === undefined).map(c => ({ value: c.key, label: c.label }));

function applySort(rows: any[], chain: SortRule[]): any[] {
  if (chain.length === 0) return rows;
  return [...rows].sort((a, b) => {
    for (const rule of chain) {
      const av = a[rule.key], bv = b[rule.key];
      const aNull = av === null || av === undefined || av === "";
      const bNull = bv === null || bv === undefined || bv === "";
      if (aNull && bNull) continue;
      if (aNull) return 1;   // null/unavailable always sorts last, never treated as 0
      if (bNull) return -1;
      let cmp = 0;
      if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      if (cmp !== 0) return rule.dir === "asc" ? cmp : -cmp;
    }
    return 0;
  });
}

// ─── QUICK VIEWS ────────────────────────────────────────────────────────────

const QUICK_VIEWS: { label: string; filters: Partial<Filters>; sort?: SortRule[] }[] = [
  { label: "Negative Float", filters: { tf: "NEGATIVE" }, sort: [{ key: "currentTotalFloat", dir: "asc" }] },
  { label: "Newly Negative", filters: { newlyNegative: true } },
  { label: "Critical / Driving", filters: { criticality: "DRIVING" } },
  { label: "Near Critical", filters: { criticality: "NEAR_CRITICAL" } },
  { label: "Biggest Float Deterioration", filters: {}, sort: [{ key: "floatChangeVsBaseline", dir: "asc" }] },
  { label: "Biggest Finish Slips", filters: {}, sort: [{ key: "finishVarianceDays", dir: "desc" }] },
  { label: "Missed Forecast Starts", filters: {}, sort: [] },
  { label: "Should Have Started", filters: { status: "SHOULD_HAVE_STARTED" } },
  { label: "Should Have Finished", filters: { status: "SHOULD_HAVE_FINISHED" } },
  { label: "Overdue Incomplete", filters: { status: "OVERDUE" } },
  { label: "Duration Growth", filters: {}, sort: [{ key: "remainingDurationChange", dir: "desc" }] },
  { label: "Logic Changes", filters: { logicChanged: true } },
  { label: "Constraint Changes", filters: { constraintChanged: true } },
  { label: "Milestone Exposure", filters: { milestonesOnly: true } },
  { label: "High/Critical Risks", filters: { riskSeverity: "CRITICAL" } },
];

// ─── MAIN TABLE ─────────────────────────────────────────────────────────────

function frozenLeftOffset(cols: ColDef[], idx: number): number {
  let left = 0;
  for (let i = 0; i < idx; i++) if (cols[i].frozen) left += cols[i].width;
  return left;
}

function ActivityTable({ visibleCols, rows, onSelect, rowHeight = 34 }: {
  visibleCols: string[]; rows: FlatRow[]; onSelect: (row: any) => void; rowHeight?: number;
}) {
  const cols = visibleCols.map(k => COL_BY_KEY[k]).filter(Boolean);
  const totalWidth = cols.reduce((s, c) => s + c.width, 0);

  const headerRow = (
    <div style={{ display: "flex", height: rowHeight, background: C.card2, borderBottom: `2px solid ${C.border}`, position: "sticky", top: 0, zIndex: 3, minWidth: totalWidth }}>
      {cols.map((c, i) => (
        <div key={c.key} style={{
          width: c.width, flexShrink: 0, padding: "0 10px", display: "flex", alignItems: "center",
          fontSize: 10, fontWeight: 800, color: C.muted, textTransform: "uppercase", whiteSpace: "nowrap", overflow: "hidden",
          position: c.frozen ? "sticky" : "static", left: c.frozen ? frozenLeftOffset(cols, i) : undefined,
          zIndex: c.frozen ? 4 : undefined, background: c.frozen ? C.card2 : undefined,
          borderRight: c.frozen && (i === cols.length - 1 || !cols[i + 1]?.frozen) ? `2px solid ${C.border}` : undefined,
        }}>
          {c.label}
        </div>
      ))}
    </div>
  );

  return (
    <div>
      {headerRow}
      <VirtualRows items={rows} rowHeight={rowHeight} height={560} renderRow={(fr: FlatRow, i) => {
        if (fr.kind === "group") {
          return (
            <div key={`g${i}`} style={{
              height: rowHeight, display: "flex", alignItems: "center", minWidth: totalWidth,
              paddingLeft: 14 + fr.level * 18, background: fr.level === 0 ? C.panel : C.card2,
              borderBottom: `1px solid ${C.border}`, fontSize: 11, fontWeight: 800, color: C.text, gap: 14,
            }}>
              <span>{fr.label} <span style={{ color: C.muted, fontWeight: 600 }}>({fr.summary.count})</span></span>
              <span style={{ fontSize: 10, color: C.muted2, fontWeight: 600 }}>
                In-Progress {fr.summary.inProgress} · Delayed {fr.summary.delayed} · Neg Float {fr.summary.negativeFloat} ·
                Critical {fr.summary.critical} · Min TF {fr.summary.minTF ?? "—"} · Median TF {fr.summary.medianTF ?? "—"} ·
                Max Finish Var {fr.summary.maxFinishVariance ?? "—"} · Milestones {fr.summary.milestoneCount}
              </span>
            </div>
          );
        }
        const r = fr.row;
        return (
          <div key={r.activityId} onClick={() => onSelect(r)}
            style={{ display: "flex", height: rowHeight, minWidth: totalWidth, borderBottom: `1px solid ${C.border}`, cursor: "pointer", background: C.card }}>
            {cols.map((c, ci) => {
              const { text, color } = formatCellValue(r, c);
              return (
                <div key={c.key} style={{
                  width: c.width, flexShrink: 0, padding: "0 10px", display: "flex", alignItems: "center",
                  fontSize: 11, color: color || C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  fontFamily: c.key === "activityId" ? "monospace" : undefined,
                  position: c.frozen ? "sticky" : "static", left: c.frozen ? frozenLeftOffset(cols, ci) : undefined,
                  zIndex: c.frozen ? 2 : undefined, background: c.frozen ? C.card : undefined,
                  borderRight: c.frozen && (ci === cols.length - 1 || !cols[ci + 1]?.frozen) ? `2px solid ${C.border}` : undefined,
                }}>
                  {text}
                </div>
              );
            })}
          </div>
        );
      }} />
    </div>
  );
}

// ─── DATA FETCHING ──────────────────────────────────────────────────────────

function useActivityAnalysis(ctx: ReturnType<typeof useProjectsAndVersions>) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    if (!ctx.projectId || !ctx.versionId) return;
    setLoading(true); setError(null);
    sfetch(`${API}/api/projects/${ctx.projectId}/activity-analysis/?currentVersion=${ctx.versionId}`)
      .then(async r => { const t = await r.text(); if (!r.ok) throw new Error(t); return JSON.parse(t); })
      .then(setData).catch((e: any) => setError(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(load, [ctx.projectId, ctx.versionId]);

  return { data, loading, error, reload: load };
}

// ─── ACTIVITY DETAIL DRAWER ─────────────────────────────────────────────────

function DetailField({ label, value }: { label: string; value: any }) {
  return (
    <div style={{ fontSize: 11 }}>
      <div style={{ color: C.muted, fontSize: 9, textTransform: "uppercase" }}>{label}</div>
      <div style={{ color: C.text, fontWeight: 600 }}>{value === null || value === undefined || value === "" ? "Unavailable" : String(value)}</div>
    </div>
  );
}

function ActivityDetailDrawer({ row, traceability, onClose, onOpenFloatAnalysis, onOpenRiskRecovery }: {
  row: any; traceability: any; onClose: () => void;
  onOpenFloatAnalysis?: (activityId: string) => void; onOpenRiskRecovery?: (riskKey: string) => void;
}) {
  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 460, background: C.bg, borderLeft: `1px solid ${C.border}`, boxShadow: "-6px 0 24px rgba(0,0,0,0.15)", zIndex: 1000, overflowY: "auto", padding: 22 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 11, fontFamily: "monospace", color: C.muted2 }}>{row.activityId}</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: C.text }}>{row.activityName}</div>
          <div style={{ fontSize: 11, color: C.muted }}>{row.wbs}{row.area ? ` · ${row.area}` : ""}{row.discipline ? ` · ${row.discipline}` : ""}</div>
        </div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 18 }}>✕</button>
      </div>

      {(onOpenFloatAnalysis || (onOpenRiskRecovery && row.scheduleRisk)) && (
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          {onOpenFloatAnalysis && (
            <button onClick={() => onOpenFloatAnalysis(row.activityId)}
              style={{ flex: 1, background: `${C.accent}12`, border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 7, padding: "7px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
              View Float Trend →
            </button>
          )}
          {onOpenRiskRecovery && row.scheduleRisk && (
            <button onClick={() => onOpenRiskRecovery(row.activityId)}
              style={{ flex: 1, background: `${C.purple}12`, border: `1px solid ${C.purple}`, color: C.purple, borderRadius: 7, padding: "7px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
              Analyze Recovery →
            </button>
          )}
        </div>
      )}

      {(row.critical || row.driving || row.negativeFloat || row.finished) && (
        <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
          {row.finished && <span style={{ background: `${C.muted2}20`, color: C.muted, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Complete</span>}
          {row.criticalActionable && <span style={{ background: `${C.red}15`, color: C.red, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Critical</span>}
          {!row.criticalActionable && row.critical && <span style={{ background: `${C.red}10`, color: C.muted, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Critical (imported P6, not actionable — Complete)</span>}
          {row.driving && <span style={{ background: `${C.purple}15`, color: C.purple, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Driving</span>}
          {row.negativeFloat && <span style={{ background: `${C.red}15`, color: C.red, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Negative Float</span>}
          {row.isMilestone && <span style={{ background: `${C.gold}15`, color: C.gold, fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "3px 8px" }}>Milestone</span>}
        </div>
      )}

      {[
        { title: "Status", fields: [["% Complete", row.pctComplete], ["% Complete Type", row.pctCompleteType], ["Physical %", row.physPctComplete], ["Status", row.activityStatus]] },
        { title: "Duration", fields: [["Original Duration", row.originalDuration], ["Original Duration Source", row.originalDurationSource], ["Remaining Duration", row.remainingDuration], ["Actual Duration", row.actualDuration]] },
        { title: "Dates", fields: [["Baseline Start", row.baselineStart], ["Baseline Finish", row.baselineFinish], ["Current Start", row.currentStart], ["Current Finish", row.currentFinish], ["Actual Start", row.actualStart], ["Actual Finish", row.actualFinish]] },
        { title: "Variance", fields: [["Start Var (Baseline, d)", row.startVarianceDays], ["Finish Var (Baseline, d)", row.finishVarianceDays], ["Finish Var (working d)", row.calendarConfident ? row.finishVarianceWorkingDays : "Unavailable"], ["Update Movement (d)", row.updateMovementDays]] },
        {
          title: "Float", fields: [
            ["Baseline TF", row.baselineTotalFloat], ["Previous TF", row.previousTotalFloat],
            ["Current Total Float", row.finished ? "— (Activity Complete)" : row.currentTotalFloat],
            ["Free Float", row.finished ? "— (Activity Complete)" : row.freeFloat],
            ["Float Change vs Baseline", row.floatChangeVsBaseline],
          ],
        },
        { title: "Logic", fields: [["Predecessors", row.predecessorCount], ["Successors", row.successorCount], ["Driving Predecessor", row.drivingPredecessor ? `${row.drivingPredecessor.activityId} (${row.drivingPredecessor.totalFloat ?? "—"}d, ${row.drivingPredecessor.relationshipType})` : "—"], ["Open Start", row.openStart ? "Yes" : "No"], ["Open Finish", row.openFinish ? "Yes" : "No"]] },
        { title: "Constraints", fields: [["Constraint Type", row.constraintType], ["Constraint Date", row.constraintDate], ["Secondary Type", row.constraint2Type], ["Secondary Date", row.constraint2Date]] },
        { title: "Calendar", fields: [["Calendar", row.calendarName], ["Confidence", row.calendarAvailable ? "Available" : "Unavailable"], ["Hours/Day", row.hoursPerDay], ["Workweek", row.workweekDescription]] },
        ...(row.isMilestone ? [{ title: "Milestone", fields: [["Risk Level", row.milestoneRiskLevel], ["Variance (d)", row.milestoneVarianceDays]] }] : []),
        ...(row.scheduleRisk ? [{ title: "Risk", fields: [["Severity", row.riskSeverity], ["Urgency", row.riskUrgency], ["Status", row.riskStatus]] }] : []),
      ].map(section => (
        <div key={section.title} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6, borderBottom: `1px solid ${C.border}`, paddingBottom: 4 }}>{section.title}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {section.fields.map(([label, value]) => <DetailField key={label as string} label={label as string} value={value} />)}
          </div>
        </div>
      ))}

      <div style={{ marginTop: 14, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px" }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>Source Traceability</div>
        <div style={{ fontSize: 10, color: C.muted2, lineHeight: 1.7 }}>
          Current: {traceability?.currentVersionLabel || "—"} (Data Date {traceability?.currentDataDate || "Unavailable"})<br />
          Previous: {traceability?.previousVersionLabel || "Unavailable"}<br />
          Baseline: {traceability?.baselineVersionLabel || "Unavailable"}<br />
          Total Float source: Imported P6 value — never recalculated by ScheduleIQ.
          {row.finished && <>
            <br />Imported P6 TF at this version: {row.importedCurrentTotalFloat ?? "Unavailable"} (display blanked above — Complete)<br />
            Imported P6 Free Float at this version: {row.importedFreeFloat ?? "Unavailable"}
          </>}
        </div>
      </div>
    </div>
  );
}

// ─── ROOT ───────────────────────────────────────────────────────────────────

export default function ActivityAnalysis({ initialProjectId, initialVersionId, initialFilters, onOpenFloatAnalysis, onOpenRiskRecovery }: {
  initialProjectId?: string; initialVersionId?: string;
  // One-shot filter seed for cross-workspace deep links (Portfolio KPI
  // drill-through, global search "jump to activity") - applied once at
  // mount, same lifecycle as initialProjectId/initialVersionId. See
  // activityNavigation.ts for the legacy-filter-code translation table.
  initialFilters?: Partial<Filters>;
  onOpenFloatAnalysis?: (projectId: string, versionId: string, activityId: string) => void;
  onOpenRiskRecovery?: (projectId: string, versionId: string, riskKey: string) => void;
} = {}) {
  const ctx = useProjectsAndVersions(initialProjectId, initialVersionId);
  const { data, loading, error } = useActivityAnalysis(ctx);
  const [visibleCols, setVisibleCols] = useState<string[]>(loadVisibleCols);
  const [showColManager, setShowColManager] = useState(false);
  const [filters, setFilters] = useState<Filters>(() => (initialFilters ? { ...DEFAULT_FILTERS, ...initialFilters } : DEFAULT_FILTERS));
  const [groupBy, setGroupBy] = useState<string[]>(["", "", ""]);
  const [sortChain, setSortChain] = useState<SortRule[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [activePreset, setActivePreset] = useState<string>("scheduler");

  const applyColumns = (cols: string[]) => {
    setVisibleCols(cols);
    setActivePreset("");
    try { sessionStorage.setItem(VISIBLE_COLS_STORAGE_KEY, JSON.stringify(cols)); } catch { /* per-viewer only */ }
    setShowColManager(false);
  };

  const applyPreset = (key: string) => {
    setActivePreset(key);
    setVisibleCols(PRESETS[key].cols);
    try { sessionStorage.setItem(VISIBLE_COLS_STORAGE_KEY, JSON.stringify(PRESETS[key].cols)); } catch { /* ignore */ }
  };

  const applyQuickView = (qv: typeof QUICK_VIEWS[number]) => {
    setFilters({ ...DEFAULT_FILTERS, ...qv.filters });
    setSortChain(qv.sort || []);
  };

  const removeChip = (key: string) => setFilters(f => ({ ...f, [key]: (DEFAULT_FILTERS as any)[key] }));

  const allRows: any[] = data?.rows || [];
  const filtered = useMemo(() => applyFilters(allRows, filters), [allRows, filters]);
  const sorted = useMemo(() => applySort(filtered, sortChain), [filtered, sortChain]);
  const grouped = useMemo(() => buildGroupedRows(sorted, groupBy), [sorted, groupBy]);

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Activity Analysis</div>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>
        The master analytical dataset — one authoritative row per activity, shared by Float Analysis, Progress &amp; Milestones, Update Intelligence, and Risk &amp; Recovery.
      </div>

      <ProjectVersionBar ctx={ctx} />

      {!ctx.projectId && <div style={{ textAlign: "center", color: C.muted, padding: 60 }}>Select a project above to begin.</div>}
      {ctx.projectId && loading && <div style={{ color: C.accent, padding: 20 }}>Building activity analysis…</div>}
      {ctx.projectId && error && <div style={{ color: C.red, padding: 12 }}>⚠ {error}</div>}

      {ctx.projectId && data && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10, alignItems: "center" }}>
            {Object.entries(PRESETS).map(([key, p]) => (
              <button key={key} onClick={() => applyPreset(key)}
                style={{ background: activePreset === key ? `${C.accent}18` : C.card, border: `1px solid ${activePreset === key ? C.accent : C.border}`, color: activePreset === key ? C.accent : C.muted2, borderRadius: 7, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
                {p.label}
              </button>
            ))}
            <button onClick={() => setShowColManager(true)}
              style={{ background: C.card, border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 7, padding: "5px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
              ⚙ Columns{!activePreset && " (Custom)"}
            </button>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
            {QUICK_VIEWS.map(qv => (
              <button key={qv.label} onClick={() => applyQuickView(qv)}
                style={{ background: C.card2, border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 14, padding: "4px 11px", cursor: "pointer", fontSize: 10.5, fontFamily: "inherit" }}>
                {qv.label}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8, alignItems: "center", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 12px" }}>
            <input value={filters.search} onChange={e => setFilters(f => ({ ...f, search: e.target.value }))} placeholder="Search Activity ID or name…"
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 180 }} />
            <Select value={filters.status} onChange={(v: string) => setFilters(f => ({ ...f, status: v }))} placeholder="Status"
              options={["NOT_STARTED", "IN_PROGRESS", "COMPLETE", "SHOULD_HAVE_STARTED", "SHOULD_HAVE_FINISHED", "OVERDUE"].map(s => ({ value: s, label: s.replace(/_/g, " ") }))} />
            <Select value={filters.tf} onChange={(v: string) => setFilters(f => ({ ...f, tf: v }))} placeholder="Total Float"
              options={[{ value: "NEGATIVE", label: "TF < 0" }, { value: "ZERO", label: "TF = 0" }, { value: "POSITIVE", label: "TF > 0" }]} />
            <Select value={filters.criticality} onChange={(v: string) => setFilters(f => ({ ...f, criticality: v }))} placeholder="Criticality"
              options={["CRITICAL", "NEAR_CRITICAL", "DRIVING", "LONGEST_PATH", "NEWLY_CRITICAL", "NO_LONGER_CRITICAL"].map(s => ({ value: s, label: s.replace(/_/g, " ") }))} />
            {(["area", "discipline", "contractor", "system"] as const).map(f => (
              <input key={f} value={(filters as any)[f]} onChange={e => setFilters(p => ({ ...p, [f]: e.target.value }))} placeholder={f[0].toUpperCase() + f.slice(1)}
                style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 7, padding: "6px 10px", fontSize: 12, fontFamily: "inherit", width: 100 }} />
            ))}
            {([["openStart", "Open Start"], ["openFinish", "Open Finish"], ["negativeLag", "Neg Lag"], ["logicChanged", "Logic Changed"], ["hasConstraint", "Constrained"], ["milestonesOnly", "Milestones"]] as const).map(([key, label]) => (
              <button key={key} onClick={() => setFilters(f => ({ ...f, [key]: !(f as any)[key] }))}
                style={{ background: (filters as any)[key] ? `${C.accent}18` : C.card, border: `1px solid ${(filters as any)[key] ? C.accent : C.border}`, color: (filters as any)[key] ? C.accent : C.muted2, borderRadius: 7, padding: "5px 10px", cursor: "pointer", fontSize: 11, fontWeight: 600 }}>
                {label}
              </button>
            ))}
          </div>

          {activeFilterChips(filters).length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10, alignItems: "center" }}>
              {activeFilterChips(filters).map(chip => (
                <span key={chip.key} onClick={() => removeChip(chip.key)}
                  style={{ background: `${C.accent}12`, border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 12, padding: "3px 10px", fontSize: 10, cursor: "pointer" }}>
                  {chip.label} ✕
                </span>
              ))}
              <button onClick={() => setFilters(DEFAULT_FILTERS)} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 10, textDecoration: "underline" }}>Clear All</button>
            </div>
          )}

          <div style={{ display: "flex", gap: 14, marginBottom: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ fontSize: 11, color: C.muted }}>Group by:</div>
            {[0, 1, 2].map(level => (
              <Select key={level} value={groupBy[level]} onChange={(v: string) => setGroupBy(g => { const n = [...g]; n[level] = v; return n; })}
                options={GROUP_FIELDS} />
            ))}
            <div style={{ fontSize: 11, color: C.muted, marginLeft: 10 }}>Sort:</div>
            {sortChain.map((rule, i) => (
              <span key={i} style={{ display: "flex", alignItems: "center", gap: 4, background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 8px", fontSize: 10 }}>
                {COL_BY_KEY[rule.key]?.label || rule.key} {rule.dir === "asc" ? "↑" : "↓"}
                <button onClick={() => setSortChain(c => c.filter((_, j) => j !== i))} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer" }}>✕</button>
              </span>
            ))}
            <select onChange={e => { if (e.target.value) { setSortChain(c => [...c, { key: e.target.value, dir: "asc" }]); e.target.value = ""; } }}
              style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 11, fontFamily: "inherit" }}>
              <option value="">+ Add sort…</option>
              {SORTABLE_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>

          <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>
            {filtered.length.toLocaleString()} displayed / {allRows.length.toLocaleString()} analyzed
          </div>

          <ActivityTable visibleCols={visibleCols} rows={grouped} onSelect={setSelected} />

          {selected && (
            <ActivityDetailDrawer row={selected} traceability={data} onClose={() => setSelected(null)}
              onOpenFloatAnalysis={onOpenFloatAnalysis ? (aid) => onOpenFloatAnalysis(ctx.projectId, ctx.versionId, aid) : undefined}
              onOpenRiskRecovery={onOpenRiskRecovery ? (rk) => onOpenRiskRecovery(ctx.projectId, ctx.versionId, rk) : undefined} />
          )}
          {showColManager && <ColumnManagerDialog visibleCols={visibleCols} onApply={applyColumns} onClose={() => setShowColManager(false)} />}
        </>
      )}
    </div>
  );
}
