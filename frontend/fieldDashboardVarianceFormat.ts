// Pure, testable helpers for the Field Dashboard's "Schedule Variance &
// Completion" panel (FieldDashboard.tsx). These extract the only real
// branching logic in that panel — everything else is a direct, unmodified
// read of fields already computed by scheduler/variance_intelligence.py
// via the shared /api/projects/<id>/variance-intelligence/ endpoint.

export type CompletionStatusResult = { kind: "direction" | "assessment"; value: string; label: string };

const DIRECTION_LABEL: Record<string, string> = { NO_MOVEMENT: "No Movement", UNAVAILABLE: "Unavailable" };

// direction and assessment are two distinct axes already computed by
// classify_direction()/classify_exposure() — NO_MOVEMENT and UNAVAILABLE
// only ever appear on direction, never on assessment (a NO_MOVEMENT or
// FAVORABLE direction always maps to a FAVORABLE assessment), so the
// completion milestone's own status must be read off whichever axis
// actually carries it. This never recalculates either value — it only
// picks which already-computed field to display.
export function resolveCompletionStatus(cm: { direction: string; assessment: string }): CompletionStatusResult {
  if (cm.direction === "NO_MOVEMENT" || cm.direction === "UNAVAILABLE") {
    return { kind: "direction", value: cm.direction, label: DIRECTION_LABEL[cm.direction] || cm.direction };
  }
  return { kind: "assessment", value: cm.assessment, label: cm.assessment };
}

// Mirrors VarianceIntelligence.tsx's own completion-panel title/disclaimer
// branch exactly, so the two screens never describe the same
// selectionBasis differently.
export function isRegisteredCompletion(selectionBasis: string | null | undefined): boolean {
  return selectionBasis === "REGISTERED";
}

export function completionPanelTitle(selectionBasis: string | null | undefined): string {
  return isRegisteredCompletion(selectionBasis) ? "Project Completion Assessment" : "Latest Project Forecast Milestone";
}

// Deliberately NEVER includes a `basis` param — the backend resolves the
// same default (approvedBaseline if designated, else embeddedBaseline)
// that Variance Intelligence itself gets when its own basis selector is
// unset, so both screens call an identical URL shape for the same
// project/version and therefore always receive an identical response.
export function buildVarianceIntelligenceUrl(projectId: string, versionId: string): string {
  return `/api/projects/${projectId}/variance-intelligence/?currentVersion=${versionId}`;
}
