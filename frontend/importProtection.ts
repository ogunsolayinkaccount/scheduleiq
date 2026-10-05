// Pure logic for Phase 2 (Import Protection) and Phase 3/4 (Schedule
// Deletion Auditing) frontend wiring — pulled out of ImportCenter.tsx /
// DeletedVersions.tsx so the response-interpretation rules are
// unit-testable without a React/DOM harness, matching the
// dateFormat.ts/dashboardFormat.ts pattern.

/** A non-ok response body is either plain text or JSON with `message`
 * (human-readable, added for the Phase 2 confirmation-gate responses:
 * EXACT_DUPLICATE_IMPORT / MATCHING_PROJECT_FOUND / SCHEDULE_IDENTITY_UNCERTAIN)
 * and/or `error` (older call sites use this as the display string itself;
 * the three codes above use it as a machine-readable code instead) —
 * `message` is preferred when present so a 409 from one of those three
 * never shows a raw code like "EXACT_DUPLICATE_IMPORT" as the user-facing
 * text. Falls back to the raw response body if it isn't JSON at all. */
export function parseErrorMessage(responseText: string): string {
  if (!responseText) return "Request failed.";
  try {
    const body = JSON.parse(responseText);
    return body.message || body.error || responseText;
  } catch {
    return responseText;
  }
}

export type ImportConfirmationEntry = {
  confirmDuplicate?: boolean;
  confirmIdentity?: boolean;
  identityCandidateDismissed?: boolean;
};

/** Maps the Import Preview acknowledgements a user already made (the
 * "Import anyway" checkbox, the identity-mismatch checkbox, "Create New
 * Project Anyway") to the exact POST fields import_commit enforces
 * server-side. Omitting any of these when its matching warning was shown
 * guarantees a 409 on commit — the backend never trusts the UI state
 * alone, so every acknowledgement captured in the preview step must be
 * resent at commit time. */
export function buildImportConfirmationFields(entry: ImportConfirmationEntry): Record<string, "true"> {
  const fields: Record<string, "true"> = {};
  if (entry.confirmDuplicate) fields.confirmDuplicateImport = "true";
  if (entry.confirmIdentity) fields.confirmIdentityMismatch = "true";
  if (entry.identityCandidateDismissed) fields.confirmNewProject = "true";
  return fields;
}

/** Whole days remaining until `deadlineIso` (a retentionDeadline the
 * backend already computed — this never recomputes the deadline itself,
 * only how far away it is from "now"). null when there's nothing to count
 * down (no deadline, or it's already passed) so a caller can show
 * "retention period has passed" instead of a negative number. `now`
 * defaults to the real clock; a caller passes it explicitly to test
 * deterministically. */
export function daysRemaining(deadlineIso: string | null | undefined, now: Date = new Date()): number | null {
  if (!deadlineIso) return null;
  const deadline = new Date(deadlineIso);
  if (isNaN(deadline.getTime())) return null;
  const diffMs = deadline.getTime() - now.getTime();
  if (diffMs <= 0) return null;
  return Math.ceil(diffMs / (24 * 60 * 60 * 1000));
}

/** The exact confirmation-phrase bodies project_version_detail (DELETE)
 * and project_version_restore (POST) require — centralized so every call
 * site sends literally the same string the backend checks against
 * (DELETE_CONFIRMATION_PHRASE / RESTORE_CONFIRMATION_PHRASE in views.py). */
export function deleteConfirmationBody(reason?: string, user?: string): string {
  return JSON.stringify({ confirmation: "DELETE", ...(reason ? { reason } : {}), ...(user ? { user } : {}) });
}

export function restoreConfirmationBody(reason?: string, user?: string): string {
  return JSON.stringify({ confirmation: "RESTORE", ...(reason ? { reason } : {}), ...(user ? { user } : {}) });
}
