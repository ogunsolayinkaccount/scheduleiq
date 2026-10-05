import { useEffect, useMemo, useRef, useState } from "react";
import { parseErrorMessage, buildImportConfirmationFields } from "./importProtection";

// ─── THEME (mirrors App.tsx's palette so this reads as the same product) ─────
const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};

const API = "";

type DataDateDetection = {
  detectedDataDate: string | null;
  source: string | null;
  confidence: string | null;
};

type Destination = {
  projectId: string;
  projectName: string;
  projectNumber: string;
  currentVersionId: string | null;
  currentVersionLabel: string | null;
  currentDataDate: string | null;
  versionCount: number;
  existingP6ProjectId: string | null;
};

type IdentityClassification = "SAME_PROJECT" | "LIKELY_SAME_PROJECT" | "UNCERTAIN" | "LIKELY_DIFFERENT_PROJECT";

type OverlapSignal = {
  available: boolean;
  referenceCount: number;
  uploadedCount: number;
  overlapCount: number;
  referenceOverlapRatio: number | null;
  uploadedOverlapRatio: number | null;
  jaccard: number | null;
};

type VersionLineageCompatibility = "NOT_APPLICABLE" | "COMPATIBLE" | "SCOPE_DIVERGENT";

type ScopeDivergence = {
  available: boolean;
  referenceCount: number;
  uploadedCount: number;
  populationRatio: number | null;
  diverges: boolean;
};

type ScheduleIdentity = {
  classification: IdentityClassification;
  confirmationRequired: boolean;
  referenceVersionLabel: string | null;
  versionLineageCompatibility: VersionLineageCompatibility;
  scopeDivergence: ScopeDivergence;
  signals: {
    p6ProjectId: { match: boolean | null; reference: string | null; uploaded: string | null };
    projectName: { match: boolean | null; reference: string | null; uploaded: string | null };
    activityIdOverlap: OverlapSignal;
    wbsOverlap: OverlapSignal;
    milestoneOverlap: OverlapSignal;
  };
  warnings: string[];
  explanation: string;
};

// Import Identity Guard — proactive half. Only populated when the file was
// previewed with no destination chosen yet (the "Create New Project"
// path); surfaces the single best existing-project match, if any, so that
// path is no longer unconditional. Same evaluate_schedule_identity engine
// as ScheduleIdentity above — no second, frontend-side identity algorithm.
type IdentityCandidate = {
  projectId: string;
  projectName: string;
  currentVersionId: string | null;
  currentVersionLabel: string | null;
  currentDataDate: string | null;
  identity: ScheduleIdentity;
};

type Preview = {
  fileName: string;
  fileType?: string;
  rowCount: number;
  activityCount?: number;
  relationshipCount?: number;
  milestoneCount?: number;
  detectedColumns?: string[];
  mappedFields?: Record<string, string>;
  unmappedColumns?: string[];
  missingRequiredFields?: string[];
  missingRecommendedFields?: string[];
  duplicateActivityIds: string[];
  invalidDateCount: number;
  invalidDateSamples?: { row: number; field: string; column: string; value: string }[];
  warnings: string[];
  sampleRows: { row: number; mapped: Record<string, any> }[];
  canImport: boolean;
  dataDateDetection?: DataDateDetection;
  projectMeta?: { p6ProjectId: string | null; p6ProjectName: string | null } | null;
  destination?: Destination | null;
  dataDateWarning?: { reason: string; currentDataDate: string; uploadedDataDate: string } | null;
  p6ProjectIdMismatch?: { existingP6ProjectId: string; uploadedP6ProjectId: string } | null;
  possibleDuplicate?: { matchingVersionId: string; matchingVersionLabel: string; reason: string } | null;
  scheduleIdentity?: ScheduleIdentity | null;
  identityCandidate?: IdentityCandidate | null;
  proposedVersionLabel?: string;
  proposedClassification?: string;
};

type ProjectOption = {
  id: string; name: string; projectNumber: string; versionCount: number;
  currentVersionLabel: string | null; currentDataDate: string | null;
};

type FileEntry = {
  file: File;
  status: "loading" | "ready" | "error";
  preview?: Preview;
  error?: string;
  dataDateValue?: string;      // editable field, defaults to the detected date
  versionLabel?: string;       // editable, defaults to preview.proposedVersionLabel
  classification?: string;     // editable, defaults to preview.proposedClassification
  confirmDuplicate?: boolean;  // deliberate "import anyway" acknowledgements
  confirmIdentity?: boolean;   // required when scheduleIdentity.confirmationRequired is true
  identityCandidateDismissed?: boolean;  // "Create New Project Anyway" was clicked for this file's suggestion
};

type ImportResult = {
  projectId: string; projectName: string; scheduleUploadId: string;
  versionLabel: string; dataDate: string | null; activityCount: number; role: string;
};

const CLASSIFICATION_LABEL: Record<string, string> = {
  CURRENT_UPDATE: "Current Update",
  APPROVED_BASELINE: "Approved Baseline",
  PREVIOUS_UPDATE: "Previous / Historical Update",
  RECOVERY_SCHEDULE: "Recovery Schedule",
  REVISED_BASELINE: "Revised Baseline",
  WHAT_IF: "What-If Schedule",
};

const DATA_DATE_SOURCE_LABEL: Record<string, string> = {
  P6_XER_PROJECT: "P6 XER project metadata (authoritative)",
  EXCEL_METADATA: "Excel — detected label/value",
  CSV_METADATA: "CSV — detected label/value",
  PDF_METADATA: "PDF — detected label/value",
  USER_ENTERED: "Entered manually",
};

function fieldLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function StatChip({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", minWidth: 90 }}>
      <div style={{ fontSize: 9, color: C.muted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || C.text }}>{value}</div>
    </div>
  );
}

function WarningBanner({ color, title, children }: { color: string; title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: `${color}12`, border: `1px solid ${color}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color, marginBottom: 4 }}>⚠ {title}</div>
      <div style={{ fontSize: 12, color: C.text }}>{children}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Import Preview panel — Destination / Uploaded File / Detected Schedule /
// Detected Data Date / Current Project Data Date / Proposed Version /
// Proposed Role, plus the three deliberately-non-blocking warnings.
// ─────────────────────────────────────────────────────────────────────────
const IDENTITY_LABEL: Record<IdentityClassification, string> = {
  SAME_PROJECT: "Same Project",
  LIKELY_SAME_PROJECT: "Likely Same Project",
  UNCERTAIN: "Schedule Identity Uncertain",
  LIKELY_DIFFERENT_PROJECT: "Possible Different Project",
};

const IDENTITY_COLOR: Record<IdentityClassification, string> = {
  SAME_PROJECT: C.green,
  LIKELY_SAME_PROJECT: C.green,
  UNCERTAIN: C.amber,
  LIKELY_DIFFERENT_PROJECT: C.red,
};

function overlapText(signal?: OverlapSignal | null): string {
  if (!signal || !signal.available || signal.referenceOverlapRatio == null) return "Unavailable";
  return `${signal.overlapCount} / ${signal.referenceCount} (${(signal.referenceOverlapRatio * 100).toFixed(1)}%)`;
}

// ─────────────────────────────────────────────────────────────────────────
// Schedule Identity panel — factual, evidence-based (never "AI confidence"
// or similarity-magic language). Raw P6 identity is always shown, even
// when the assessment concludes Likely Same Project, per the governing
// principle: P6 Project ID is evidence, not identity.
// ─────────────────────────────────────────────────────────────────────────
function ScheduleIdentityPanel({ identity, onSwitchToNewProject }: { identity: ScheduleIdentity; onSwitchToNewProject: () => void }) {
  const color = IDENTITY_COLOR[identity.classification];
  const s = identity.signals;
  return (
    <div style={{ background: `${color}0c`, border: `1px solid ${color}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 800, color, marginBottom: 8 }}>
        Schedule Identity — Assessment: {IDENTITY_LABEL[identity.classification]}
      </div>
      <div style={{ fontSize: 12, color: C.text, marginBottom: 10 }}>{identity.explanation}</div>

      <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>Evidence</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 12, color: C.text, marginBottom: 10 }}>
        <div>P6 Project ID: {s.p6ProjectId.reference || "—"} {s.p6ProjectId.match === false ? "→" : s.p6ProjectId.match === true ? "=" : "vs"} {s.p6ProjectId.uploaded || "—"} {s.p6ProjectId.match === false && <span style={{ color: C.gold, fontWeight: 700 }}>(changed)</span>}{s.p6ProjectId.match === true && <span style={{ color: C.green }}> (matched)</span>}</div>
        <div>Project Name: {s.projectName.match === true ? <span style={{ color: C.green }}>Matched</span> : s.projectName.match === false ? <span style={{ color: C.gold }}>Differs</span> : "Unavailable"}</div>
        <div>Activity IDs: <strong>{overlapText(s.activityIdOverlap)}</strong> overlap</div>
        <div>WBS structure: <strong>{overlapText(s.wbsOverlap)}</strong> overlap</div>
        <div>Milestones: <strong>{overlapText(s.milestoneOverlap)}</strong> matched</div>
      </div>

      {identity.versionLineageCompatibility === "SCOPE_DIVERGENT" && (
        <WarningBanner color={C.amber} title="Potentially incompatible schedule scope">
          The activity population size differs substantially ({identity.scopeDivergence.referenceCount.toLocaleString()} vs {identity.scopeDivergence.uploadedCount.toLocaleString()} activities) even though this upload matches the selected project's identity.
          This can mean a scoped subset/superset of the same underlying project (e.g. a discipline-filtered export) rather than the next chronological update — review before importing.
        </WarningBanner>
      )}

      {identity.classification === "LIKELY_DIFFERENT_PROJECT" && (
        <div style={{ marginBottom: 10 }}>
          <button onClick={onSwitchToNewProject} style={{ background: "transparent", border: `1px solid ${C.red}`, color: C.red, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 12, fontWeight: 700, fontFamily: "inherit" }}>
            Create as New Project instead
          </button>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Import Identity Guard — proactive panel. Shown on the "Create New
// Project" path (the previous unconditional default) when the upload
// structurally matches an existing project's current version. Never
// silently attaches the file — both outcomes require an explicit click,
// same non-blocking pattern as every other Import Preview notice.
// ─────────────────────────────────────────────────────────────────────────
function IdentityCandidatePanel({ candidate, onAccept, onDismiss }: {
  candidate: IdentityCandidate;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const identity = candidate.identity;
  const s = identity.signals;
  const scopeDivergent = identity.versionLineageCompatibility === "SCOPE_DIVERGENT";
  const color = scopeDivergent ? C.amber : C.green;

  return (
    <div style={{ background: `${color}0c`, border: `1px solid ${color}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 800, color, marginBottom: 6 }}>
        {scopeDivergent ? "Related project found — but scope looks different" : "Likely existing project found"}
      </div>
      <div style={{ fontSize: 13, color: C.text, marginBottom: 8 }}>
        This schedule appears to be {scopeDivergent ? "related to" : "an update to"}: <strong>{candidate.projectName}</strong>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 12, color: C.text, marginBottom: 10 }}>
        <div>Current Data Date: {candidate.currentDataDate || "—"} {candidate.currentVersionLabel ? `(${candidate.currentVersionLabel})` : ""}</div>
        <div>P6 Project ID: {s.p6ProjectId.reference || "—"} {s.p6ProjectId.match === false ? "→" : "="} {s.p6ProjectId.uploaded || "—"}</div>
        <div>Activity IDs: <strong>{overlapText(s.activityIdOverlap)}</strong> overlap</div>
        <div>WBS structure: <strong>{overlapText(s.wbsOverlap)}</strong> overlap</div>
        <div>Milestones: <strong>{overlapText(s.milestoneOverlap)}</strong> matched</div>
      </div>

      {scopeDivergent && (
        <WarningBanner color={C.amber} title="Scope divergence detected">
          The activity population size differs substantially ({identity.scopeDivergence.referenceCount.toLocaleString()} vs {identity.scopeDivergence.uploadedCount.toLocaleString()} activities) even though the schedules appear related.
          This can mean a scoped subset/superset of the same underlying project rather than the next chronological update — review before deciding.
        </WarningBanner>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={onAccept}
          style={{
            background: scopeDivergent ? C.card : color, color: scopeDivergent ? color : "#fff",
            border: `1px solid ${color}`, borderRadius: 6, padding: "6px 14px", cursor: "pointer",
            fontSize: 12, fontWeight: 700, fontFamily: "inherit",
          }}
        >
          Add as Schedule Version{!scopeDivergent && " — recommended"}
        </button>
        <button
          onClick={onDismiss}
          style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "6px 14px", cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: "inherit" }}
        >
          Create New Project Anyway
        </button>
      </div>
    </div>
  );
}

function ImportPreviewPanel({ entry, onLabelChange, onClassificationChange, onConfirmDuplicate, onConfirmIdentity, onSwitchToNewProject, onAcceptIdentityCandidate, onDismissIdentityCandidate }: {
  entry: FileEntry;
  onLabelChange: (v: string) => void;
  onClassificationChange: (v: string) => void;
  onConfirmDuplicate: (v: boolean) => void;
  onConfirmIdentity: (v: boolean) => void;
  onSwitchToNewProject: () => void;
  onAcceptIdentityCandidate: (projectId: string) => void;
  onDismissIdentityCandidate: () => void;
}) {
  const p = entry.preview;
  if (!p) return null;
  const dest = p.destination;

  return (
    <div style={{ background: C.card, border: `1px solid ${C.accent}30`, borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: C.accent, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>Import Preview</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Destination Project</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{dest ? dest.projectName : "New Project"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Uploaded File</div>
          <div style={{ fontSize: 13, color: C.text }}>{entry.file.name}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Detected Schedule</div>
          <div style={{ fontSize: 13, color: C.text }}>{p.projectMeta?.p6ProjectName || <span style={{ color: C.muted2 }}>Unavailable</span>}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Detected Data Date</div>
          <div style={{ fontSize: 13, color: C.text }}>{p.dataDateDetection?.detectedDataDate || <span style={{ color: C.red }}>Data Date required</span>}</div>
        </div>
        {dest && (
          <div>
            <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Current Project Data Date</div>
            <div style={{ fontSize: 13, color: C.text }}>{dest.currentDataDate || "—"} {dest.currentVersionLabel ? `(${dest.currentVersionLabel})` : ""}</div>
          </div>
        )}
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Activities / Relationships</div>
          <div style={{ fontSize: 13, color: C.text }}>{p.activityCount?.toLocaleString() ?? "—"} / {p.relationshipCount?.toLocaleString() ?? "—"}</div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12, alignItems: "flex-end" }}>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Proposed Version Label (edit if you like)</div>
          <input
            value={entry.versionLabel ?? p.proposedVersionLabel ?? ""}
            onChange={(e) => onLabelChange(e.target.value)}
            style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 13, fontFamily: "inherit", color: C.text, minWidth: 200 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Proposed Role</div>
          <select
            value={entry.classification ?? p.proposedClassification ?? "CURRENT_UPDATE"}
            onChange={(e) => onClassificationChange(e.target.value)}
            style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 13, fontFamily: "inherit", color: C.text }}
          >
            {Object.entries(CLASSIFICATION_LABEL).map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      {p.dataDateWarning && (
        <WarningBanner color={C.amber} title="This schedule's Data Date is earlier than the project's current schedule.">
          Current project Data Date is <strong>{p.dataDateWarning.currentDataDate}</strong>; this file's Data Date is <strong>{p.dataDateWarning.uploadedDataDate}</strong>.
          It will be imported as a historical version — the Proposed Role above defaults to "{CLASSIFICATION_LABEL['PREVIOUS_UPDATE']}" so it does not replace the current schedule. Change the role above only if you deliberately want this to become current.
        </WarningBanner>
      )}

      {p.scheduleIdentity && (
        <>
          <ScheduleIdentityPanel identity={p.scheduleIdentity} onSwitchToNewProject={onSwitchToNewProject} />
          {p.scheduleIdentity.confirmationRequired && (
            <div style={{ background: `${IDENTITY_COLOR[p.scheduleIdentity.classification]}12`, border: `1px solid ${IDENTITY_COLOR[p.scheduleIdentity.classification]}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, color: C.text }}>
                <input type="checkbox" checked={!!entry.confirmIdentity} onChange={(e) => onConfirmIdentity(e.target.checked)} />
                I verified that this schedule belongs to the selected project.
              </label>
            </div>
          )}
        </>
      )}

      {p.identityCandidate && !entry.identityCandidateDismissed && (
        <IdentityCandidatePanel
          candidate={p.identityCandidate}
          onAccept={() => onAcceptIdentityCandidate(p.identityCandidate!.projectId)}
          onDismiss={onDismissIdentityCandidate}
        />
      )}

      {p.possibleDuplicate && (
        <WarningBanner color={C.amber} title="Possible duplicate schedule version">
          <div style={{ marginBottom: 6 }}>{p.possibleDuplicate.reason}</div>
          <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={!!entry.confirmDuplicate} onChange={(e) => onConfirmDuplicate(e.target.checked)} />
            Import anyway — this is a deliberate re-import, not a mistake.
          </label>
        </WarningBanner>
      )}
    </div>
  );
}

function FilePreviewCard({ entry, onRemove, onDateChange, onLabelChange, onClassificationChange, onConfirmDuplicate, onConfirmIdentity, onSwitchToNewProject, onAcceptIdentityCandidate, onDismissIdentityCandidate }: {
  entry: FileEntry;
  onRemove: () => void;
  onDateChange: (value: string) => void;
  onLabelChange: (v: string) => void;
  onClassificationChange: (v: string) => void;
  onConfirmDuplicate: (v: boolean) => void;
  onConfirmIdentity: (v: boolean) => void;
  onSwitchToNewProject: () => void;
  onAcceptIdentityCandidate: (projectId: string) => void;
  onDismissIdentityCandidate: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const p = entry.preview;
  const detection = p?.dataDateDetection;
  const detected = detection?.detectedDataDate || "";
  const overridden = !!entry.dataDateValue && entry.dataDateValue !== detected;

  return (
    <div style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 10, marginBottom: 12, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((e) => !e)}
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", cursor: "pointer" }}
      >
        <span style={{ fontSize: 14, transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s", color: C.muted }}>▶</span>
        <span style={{ fontWeight: 700, fontSize: 13, color: C.text, flex: "0 0 auto" }}>{entry.file.name}</span>
        <span style={{ fontSize: 11, color: C.muted }}>{(entry.file.size / 1024).toFixed(0)} KB</span>

        {entry.status === "loading" && <span style={{ color: C.accent, fontSize: 12, marginLeft: "auto" }}>Analyzing…</span>}
        {entry.status === "error" && <span style={{ color: C.red, fontSize: 12, marginLeft: "auto" }}>⚠ Could not parse</span>}
        {entry.status === "ready" && p && (
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <span
              title={detected ? `Detected: ${DATA_DATE_SOURCE_LABEL[detection?.source || ""] || detection?.source}` : "No Data Date detected — enter one before importing"}
              style={{
                background: entry.dataDateValue ? (overridden ? `${C.gold}18` : `${C.accent}14`) : `${C.red}14`,
                color: entry.dataDateValue ? (overridden ? C.gold : C.accent) : C.red,
                borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600,
              }}
            >
              Data Date: {entry.dataDateValue || "not detected"}{overridden ? " (overridden)" : ""}
            </span>
            <span style={{ fontSize: 11, color: C.muted2 }}>{p.rowCount.toLocaleString()} rows</span>
            {p.scheduleIdentity && p.scheduleIdentity.classification !== "SAME_PROJECT" && (
              <span style={{ background: `${IDENTITY_COLOR[p.scheduleIdentity.classification]}18`, color: IDENTITY_COLOR[p.scheduleIdentity.classification], borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>
                {IDENTITY_LABEL[p.scheduleIdentity.classification]}
              </span>
            )}
            {p.identityCandidate && !entry.identityCandidateDismissed && (
              <span style={{ background: `${C.green}18`, color: C.green, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>
                Existing project match
              </span>
            )}
            {(p.dataDateWarning || p.possibleDuplicate) && (
              <span style={{ background: `${C.amber}18`, color: C.amber, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>
                {[p.dataDateWarning, p.possibleDuplicate].filter(Boolean).length} notice(s)
              </span>
            )}
            {p.warnings.length > 0 && (
              <span style={{ background: `${C.amber}18`, color: C.amber, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>
                {p.warnings.length} warning{p.warnings.length > 1 ? "s" : ""}
              </span>
            )}
            {!p.canImport && (
              <span style={{ background: `${C.red}18`, color: C.red, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>
                Cannot import
              </span>
            )}
            {p.canImport && p.warnings.length === 0 && (
              <span style={{ background: `${C.green}18`, color: C.green, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600 }}>✓ Clean</span>
            )}
          </div>
        )}

        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          title="Remove this file from the import batch"
          style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 15, padding: "0 4px", lineHeight: 1 }}
        >
          ✕
        </button>
      </div>

      {expanded && entry.status === "error" && (
        <div style={{ padding: "0 14px 14px", color: C.red, fontSize: 12 }}>{entry.error}</div>
      )}

      {expanded && p && (
        <div style={{ padding: "0 14px 16px" }}>
          <ImportPreviewPanel
            entry={entry}
            onLabelChange={onLabelChange}
            onClassificationChange={onClassificationChange}
            onConfirmDuplicate={onConfirmDuplicate}
            onConfirmIdentity={onConfirmIdentity}
            onSwitchToNewProject={onSwitchToNewProject}
            onAcceptIdentityCandidate={onAcceptIdentityCandidate}
            onDismissIdentityCandidate={onDismissIdentityCandidate}
          />

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
            <StatChip label="Rows" value={p.rowCount} />
            {p.mappedFields && <StatChip label="Mapped Fields" value={Object.keys(p.mappedFields).length} color={C.green} />}
            {p.unmappedColumns && <StatChip label="Unmapped Columns" value={p.unmappedColumns.length} color={p.unmappedColumns.length ? C.amber : undefined} />}
            <StatChip label="Duplicate IDs" value={p.duplicateActivityIds.length} color={p.duplicateActivityIds.length ? C.red : undefined} />
            <StatChip label="Invalid Dates" value={p.invalidDateCount} color={p.invalidDateCount ? C.red : undefined} />
          </div>

          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Data Date</div>
            {detected ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Source Data Date</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{detected}</div>
                  <div style={{ fontSize: 10, color: C.muted2, marginTop: 1 }}>Source: {DATA_DATE_SOURCE_LABEL[detection?.source || ""] || detection?.source} · Confidence: {detection?.confidence}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Effective Data Date (edit to override)</div>
                  <input
                    type="date"
                    value={entry.dataDateValue || ""}
                    onChange={(e) => onDateChange(e.target.value)}
                    style={{ background: C.card2, border: `1px solid ${overridden ? C.gold : C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 13, fontFamily: "inherit", color: C.text }}
                  />
                  {overridden && (
                    <button onClick={() => onDateChange(detected)} style={{ marginLeft: 8, background: "transparent", border: "none", color: C.accent, fontSize: 11, cursor: "pointer", textDecoration: "underline" }}>
                      Restore detected
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div>
                <div style={{ fontSize: 12, color: C.red, marginBottom: 8, fontWeight: 700 }}>Data Date required — could not be detected from this file.</div>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Enter the Data Date before importing</div>
                <input
                  type="date"
                  value={entry.dataDateValue || ""}
                  onChange={(e) => onDateChange(e.target.value)}
                  style={{ background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", fontSize: 13, fontFamily: "inherit", color: C.text }}
                />
              </div>
            )}
          </div>

          {p.warnings.length > 0 && (
            <div style={{ background: `${C.amber}12`, border: `1px solid ${C.amber}40`, borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
              {p.warnings.map((w, i) => (
                <div key={i} style={{ fontSize: 12, color: C.amber, marginBottom: i < p.warnings.length - 1 ? 4 : 0 }}>⚠ {w}</div>
              ))}
            </div>
          )}

          {p.mappedFields && Object.keys(p.mappedFields).length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                Detected Field Mapping
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {Object.entries(p.mappedFields).map(([field, col]) => (
                  <span key={field} title={`Source column: ${col}`} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", fontSize: 11, color: C.text }}>
                    <span style={{ color: C.green, fontWeight: 700 }}>{fieldLabel(field)}</span>
                    <span style={{ color: C.muted }}> ← </span>
                    {col}
                  </span>
                ))}
              </div>
            </div>
          )}

          {p.unmappedColumns && p.unmappedColumns.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                Unmapped Columns (not recognised — will not be imported)
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {p.unmappedColumns.map((col) => (
                  <span key={col} style={{ background: "transparent", border: `1px dashed ${C.border}`, borderRadius: 6, padding: "3px 9px", fontSize: 11, color: C.muted2 }}>
                    {col}
                  </span>
                ))}
              </div>
            </div>
          )}

          {p.duplicateActivityIds.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: C.red, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                Duplicate Activity IDs
              </div>
              <div style={{ fontSize: 12, color: C.text }}>{p.duplicateActivityIds.join(", ")}</div>
            </div>
          )}

          {p.sampleRows && p.sampleRows.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                Preview (first {p.sampleRows.length} rows)
              </div>
              <div style={{ overflowX: "auto", border: `1px solid ${C.border}`, borderRadius: 8 }}>
                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>
                  <thead>
                    <tr style={{ background: C.card }}>
                      <th style={{ padding: "6px 10px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}` }}>Row</th>
                      {Object.keys(p.sampleRows[0].mapped).map((k) => (
                        <th key={k} style={{ padding: "6px 10px", textAlign: "left", color: C.muted, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>
                          {fieldLabel(k)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {p.sampleRows.map((r) => (
                      <tr key={r.row}>
                        <td style={{ padding: "5px 10px", color: C.muted2, borderBottom: `1px solid ${C.border}` }}>{r.row}</td>
                        {Object.keys(p.sampleRows[0].mapped).map((k) => (
                          <td key={k} style={{ padding: "5px 10px", color: C.text, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>
                            {r.mapped[k] === null || r.mapped[k] === undefined || r.mapped[k] === "" ? <span style={{ color: C.muted }}>—</span> : String(r.mapped[k])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Success screen — item 13: turn a successful import directly into
// project-controls analysis via quick-action navigation.
// ─────────────────────────────────────────────────────────────────────────
function ImportSuccessScreen({ results, onNavigate, onDone }: {
  results: ImportResult[];
  onNavigate: (view: string, projectId: string) => void;
  onDone: () => void;
}) {
  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ fontSize: 15, fontWeight: 800, color: C.green, marginBottom: 4 }}>✓ Schedule Version Imported</div>
      {results.map((r) => (
        <div key={r.scheduleUploadId} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Project</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{r.projectName}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Version</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{r.versionLabel}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Data Date</div>
              <div style={{ fontSize: 14, color: C.text }}>{r.dataDate || "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Activities</div>
              <div style={{ fontSize: 14, color: C.text }}>{r.activityCount?.toLocaleString() ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Role</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: r.role === "CURRENT" ? C.accent : r.role === "BASELINE" ? C.purple : C.text }}>{r.role}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button onClick={() => onNavigate("updateAnalysis", r.projectId)} style={navBtnStyle}>View Update Intelligence</button>
            <button onClick={() => onNavigate("baselineProgress", r.projectId)} style={navBtnStyle}>View Baseline vs Forecast</button>
            <button onClick={() => onNavigate("baselineProgress", r.projectId)} style={navBtnStyle}>View 4-Week Look Ahead</button>
            <button onClick={() => onNavigate("activities", r.projectId)} style={navBtnStyle}>View Activities</button>
          </div>
        </div>
      ))}
      <button onClick={onDone} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 8, padding: "8px 18px", cursor: "pointer", fontSize: 13, fontFamily: "inherit" }}>
        Close
      </button>
    </div>
  );
}

const navBtnStyle: React.CSSProperties = {
  background: C.card2, border: `1px solid ${C.border}`, color: C.text, borderRadius: 7,
  padding: "7px 12px", cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: "inherit",
};

export default function ImportCenter({
  files,
  onCancel,
  onConfirm,
  onNavigate,
}: {
  files: File[];
  onCancel: () => void;
  onConfirm: (results: any[]) => void;
  onNavigate?: (view: string, projectId: string) => void;
}) {
  // "Import As" — Create New Project (default, safest existing behavior) or
  // Add Schedule Version to an Existing Project.
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [existingProjects, setExistingProjects] = useState<ProjectOption[]>([]);
  const [projectSearch, setProjectSearch] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");

  const [entries, setEntries] = useState<FileEntry[]>(files.map((file) => ({ file, status: "loading" as const })));
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [successResults, setSuccessResults] = useState<ImportResult[] | null>(null);

  useEffect(() => {
    if (mode !== "existing") return;
    fetch(`${API}/api/projects/`).then((r) => r.json()).then((d) => setExistingProjects(d.projects || [])).catch(() => {});
  }, [mode]);

  const filteredProjects = useMemo(() => {
    const q = projectSearch.trim().toLowerCase();
    if (!q) return existingProjects;
    return existingProjects.filter((p) => p.name.toLowerCase().includes(q) || (p.projectNumber || "").toLowerCase().includes(q));
  }, [existingProjects, projectSearch]);

  const fetchPreview = (file: File, idx: number) => {
    const fd = new FormData();
    fd.append("file", file);
    if (mode === "existing" && selectedProjectId) fd.append("projectId", selectedProjectId);
    fetch(`${API}/api/import/preview/`, { method: "POST", body: fd })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) throw new Error(parseErrorMessage(text));
        return JSON.parse(text) as Preview;
      })
      .then((preview) => {
        setEntries((prev) => prev.map((e, i) => (i === idx ? {
          ...e, status: "ready", preview,
          dataDateValue: preview.dataDateDetection?.detectedDataDate || "",
          versionLabel: preview.proposedVersionLabel,
          classification: preview.proposedClassification,
        } : e)));
      })
      .catch((err: any) => {
        setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, status: "error", error: err.message || String(err) } : e)));
      });
  };

  // Initial analyze — once per file, on mount.
  useEffect(() => {
    entries.forEach((entry, idx) => {
      if (entry.status === "loading") fetchPreview(entry.file, idx);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-check every already-analyzed file whenever the destination changes
  // (switching Create New <-> Add to Existing, or picking a different
  // existing project) — the destination-aware warnings depend on it.
  useEffect(() => {
    entries.forEach((entry, idx) => {
      if (entry.status !== "loading") fetchPreview(entry.file, idx);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, selectedProjectId]);

  const remove = (idx: number) => setEntries((prev) => prev.filter((_, i) => i !== idx));
  const setDataDate = (idx: number, value: string) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, dataDateValue: value } : e)));
  const setVersionLabel = (idx: number, value: string) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, versionLabel: value } : e)));
  const setClassification = (idx: number, value: string) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, classification: value } : e)));
  const setConfirmDuplicate = (idx: number, value: boolean) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, confirmDuplicate: value } : e)));
  const setConfirmIdentity = (idx: number, value: boolean) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, confirmIdentity: value } : e)));
  const dismissIdentityCandidate = (idx: number) => setEntries((prev) => prev.map((e, i) => (i === idx ? { ...e, identityCandidateDismissed: true } : e)));
  // Accepting a proactively-suggested project switches the whole batch into
  // "Add to Existing Project" mode — the existing mode/selectedProjectId
  // effect below re-previews every file against that destination, which is
  // exactly the already-built confirmatory scheduleIdentity flow. Nothing
  // is attached here; the user still confirms on the next screen.
  const acceptIdentityCandidate = (projectId: string) => { setMode("existing"); setSelectedProjectId(projectId); };

  const analyzing = entries.some((e) => e.status === "loading");
  const blockedByDataDate = (e: FileEntry) => !e.dataDateValue;
  const blockedByUnconfirmedWarning = (e: FileEntry) =>
    (!!e.preview?.possibleDuplicate && !e.confirmDuplicate) ||
    (!!e.preview?.scheduleIdentity?.confirmationRequired && !e.confirmIdentity);
  const importableEntries = entries.filter((e) => e.status === "ready" && e.preview?.canImport && !blockedByDataDate(e) && !blockedByUnconfirmedWarning(e));
  const blockedCount = entries.filter((e) => e.status === "ready" && (!e.preview?.canImport || blockedByDataDate(e))).length;
  const needsConfirmationCount = entries.filter((e) => e.status === "ready" && e.preview?.canImport && !blockedByDataDate(e) && blockedByUnconfirmedWarning(e)).length;

  // Holds raw commit responses across the async import loop and into the
  // success screen — a plain local variable would be reset to [] on the
  // re-render triggered by setSuccessResults, so this must be a ref.
  const pendingConfirmResults = useRef<any[]>([]);

  const confirmImport = async () => {
    setImporting(true);
    setImportError(null);
    const results: ImportResult[] = [];
    const errors: string[] = [];
    for (const entry of importableEntries) {
      const fd = new FormData();
      fd.append("file", entry.file);
      if (entry.dataDateValue) fd.append("dataDate", entry.dataDateValue);
      if (mode === "existing" && selectedProjectId) fd.append("projectId", selectedProjectId);
      if (entry.classification) fd.append("classification", entry.classification);
      if (entry.versionLabel) fd.append("versionLabel", entry.versionLabel);
      // Carry the explicit acknowledgements already captured above through to
      // the commit itself. The backend enforces every one of these
      // server-side (Phase 2: Import Protection) regardless of what preview
      // showed — a direct API call can't bypass it, and neither can this UI
      // skipping the flag: omitting any of these when the matching warning
      // was shown would make the commit a guaranteed 409.
      for (const [field, value] of Object.entries(buildImportConfirmationFields(entry))) fd.append(field, value);
      try {
        const r = await fetch(`${API}/api/import/commit/`, { method: "POST", body: fd });
        const text = await r.text();
        // The Phase 2 protection responses (EXACT_DUPLICATE_IMPORT,
        // MATCHING_PROJECT_FOUND, SCHEDULE_IDENTITY_UNCERTAIN) carry both a
        // machine-readable `error` code and a human-readable `message` —
        // parseErrorMessage prefers the message so this banner never shows
        // a raw code.
        if (!r.ok) throw new Error(parseErrorMessage(text));
        const data = JSON.parse(text);
        if (data.error) throw new Error(data.error);
        results.push({
          projectId: data.projectId, projectName: data.projectName, scheduleUploadId: data.scheduleUploadId,
          versionLabel: data.versionLabel, dataDate: data.dataDate, activityCount: data.actCount,
          role: "CURRENT", // refined below once the version list is re-fetched
        });
        pendingConfirmResults.current.push(data);
      } catch (e: any) {
        errors.push(`${entry.file.name}: ${e.message || e}`);
      }
    }
    setImporting(false);
    if (errors.length) setImportError(errors.join(" · "));

    // Resolve each result's real role from the backend's version list so the
    // success screen never guesses (single-version imports resolve as
    // CURRENT by the same convention _assign_version_roles already uses).
    for (const r of results) {
      try {
        const vresp = await fetch(`${API}/api/projects/${r.projectId}/versions/`);
        const vdata = await vresp.json();
        const match = (vdata.versions || []).find((v: any) => v.id === r.scheduleUploadId);
        if (match) r.role = match.role;
      } catch {}
    }
    if (results.length) setSuccessResults(results);
  };

  const handleDone = () => {
    if (pendingConfirmResults.current.length) onConfirm(pendingConfirmResults.current);
    onCancel();
  };

  const handleNavigate = (view: string, projectId: string) => {
    if (pendingConfirmResults.current.length) onConfirm(pendingConfirmResults.current);
    if (onNavigate) onNavigate(view, projectId);
    onCancel();
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(28,20,16,0.55)", zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }} onClick={successResults ? undefined : onCancel}>
      <div
        style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 14, width: "min(960px, 100%)", maxHeight: "88vh", display: "flex", flexDirection: "column", boxShadow: "0 24px 64px rgba(0,0,0,0.45)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ padding: "16px 22px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 12 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, color: C.text }}>Import Center</div>
            <div style={{ fontSize: 12, color: C.muted }}>
              {successResults ? "Import complete." : "Review detected columns, mappings, and data-quality issues before anything is saved."}
            </div>
          </div>
          {!successResults && <button onClick={onCancel} style={{ marginLeft: "auto", background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: 22, lineHeight: 1 }}>×</button>}
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "16px 22px" }}>
          {successResults ? (
            <ImportSuccessScreen results={successResults} onNavigate={handleNavigate} onDone={handleDone} />
          ) : (
            <>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>Import As</div>
                <div style={{ display: "flex", gap: 8, marginBottom: mode === "existing" ? 12 : 0 }}>
                  <button
                    onClick={() => setMode("new")}
                    style={{
                      flex: 1, padding: "10px 14px", borderRadius: 8, cursor: "pointer", fontFamily: "inherit", fontSize: 13, fontWeight: 700,
                      background: mode === "new" ? C.accent : C.card, color: mode === "new" ? "#fff" : C.text,
                      border: `1px solid ${mode === "new" ? C.accent : C.border}`,
                    }}
                  >
                    Create New Project
                  </button>
                  <button
                    onClick={() => setMode("existing")}
                    style={{
                      flex: 1, padding: "10px 14px", borderRadius: 8, cursor: "pointer", fontFamily: "inherit", fontSize: 13, fontWeight: 700,
                      background: mode === "existing" ? C.accent : C.card, color: mode === "existing" ? "#fff" : C.text,
                      border: `1px solid ${mode === "existing" ? C.accent : C.border}`,
                    }}
                  >
                    Add Schedule Version to Existing Project
                  </button>
                </div>

                {mode === "existing" && (
                  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px" }}>
                    <input
                      value={projectSearch}
                      onChange={(e) => setProjectSearch(e.target.value)}
                      placeholder="Search projects by name or number…"
                      style={{ width: "100%", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 6, padding: "7px 10px", fontSize: 13, fontFamily: "inherit", color: C.text, marginBottom: 8, boxSizing: "border-box" }}
                    />
                    <div style={{ maxHeight: 180, overflowY: "auto" }}>
                      {filteredProjects.length === 0 && <div style={{ fontSize: 12, color: C.muted2, padding: 8 }}>No projects found.</div>}
                      {filteredProjects.map((p) => (
                        <div
                          key={p.id}
                          onClick={() => setSelectedProjectId(p.id)}
                          style={{
                            padding: "8px 10px", borderRadius: 6, cursor: "pointer", marginBottom: 4,
                            background: selectedProjectId === p.id ? `${C.accent}18` : "transparent",
                            border: `1px solid ${selectedProjectId === p.id ? C.accent : "transparent"}`,
                          }}
                        >
                          <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{p.name}{p.projectNumber ? ` (${p.projectNumber})` : ""}</div>
                          <div style={{ fontSize: 11, color: C.muted2 }}>
                            Current: {p.currentVersionLabel || "—"} · Data Date: {p.currentDataDate || "—"} · {p.versionCount} schedule version{p.versionCount === 1 ? "" : "s"}
                          </div>
                        </div>
                      ))}
                    </div>
                    {!selectedProjectId && <div style={{ fontSize: 11, color: C.amber, marginTop: 6 }}>Select a project above before importing.</div>}
                  </div>
                )}
              </div>

              {entries.length === 0 && <div style={{ textAlign: "center", color: C.muted, padding: 40 }}>No files remaining in this batch.</div>}
              {entries.map((entry, idx) => (
                <FilePreviewCard
                  key={entry.file.name + idx}
                  entry={entry}
                  onRemove={() => remove(idx)}
                  onDateChange={(v) => setDataDate(idx, v)}
                  onLabelChange={(v) => setVersionLabel(idx, v)}
                  onClassificationChange={(v) => setClassification(idx, v)}
                  onConfirmDuplicate={(v) => setConfirmDuplicate(idx, v)}
                  onConfirmIdentity={(v) => setConfirmIdentity(idx, v)}
                  onSwitchToNewProject={() => setMode("new")}
                  onAcceptIdentityCandidate={acceptIdentityCandidate}
                  onDismissIdentityCandidate={() => dismissIdentityCandidate(idx)}
                />
              ))}
            </>
          )}
        </div>

        {!successResults && (
          <div style={{ padding: "14px 22px", borderTop: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ fontSize: 12, color: C.muted }}>
              {analyzing
                ? "Analyzing files…"
                : `${importableEntries.length} file${importableEntries.length === 1 ? "" : "s"} ready to import` +
                  (blockedCount ? ` · ${blockedCount} blocked (missing required fields or Data Date)` : "") +
                  (needsConfirmationCount ? ` · ${needsConfirmationCount} need${needsConfirmationCount === 1 ? "s" : ""} confirmation above` : "")}
            </div>
            {importError && <div style={{ fontSize: 12, color: C.red, marginLeft: 12 }}>⚠ {importError}</div>}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button onClick={onCancel} disabled={importing} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted2, borderRadius: 8, padding: "8px 18px", cursor: importing ? "default" : "pointer", fontSize: 13, fontFamily: "inherit" }}>
                Cancel
              </button>
              <button
                onClick={confirmImport}
                disabled={importing || analyzing || importableEntries.length === 0 || (mode === "existing" && !selectedProjectId)}
                style={{
                  background: importing || analyzing || importableEntries.length === 0 || (mode === "existing" && !selectedProjectId) ? C.card2 : C.accent,
                  border: "none",
                  color: importing || analyzing || importableEntries.length === 0 || (mode === "existing" && !selectedProjectId) ? C.muted : "#fff",
                  borderRadius: 8,
                  padding: "8px 20px",
                  cursor: importing || analyzing || importableEntries.length === 0 || (mode === "existing" && !selectedProjectId) ? "default" : "pointer",
                  fontSize: 13,
                  fontWeight: 700,
                  fontFamily: "inherit",
                }}
              >
                {importing ? "Importing…" : `Import ${importableEntries.length || ""} File${importableEntries.length === 1 ? "" : "s"}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
