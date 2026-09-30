// Shared Data Date formatting — used everywhere a schedule file/version's
// Data Date is displayed, so the format (and the "Unavailable" fallback,
// the override suffix, and the CURRENT/PREVIOUS/BASELINE selector label)
// stays identical across every table/list/selector in the app rather than
// drifting per-component.

export type VersionLike = {
  id?: string;
  versionLabel?: string;
  dataDate?: string | null;
  sourceDataDate?: string | null;
  dataDateOverridden?: boolean;
  role?: string;
  dataDateConflict?: boolean;
  previousUnresolved?: { reason: string } | null;
};

/** 'YYYY-MM-DD' -> 'Aug 25, 2026'. Parses date parts manually (not
 * `new Date(iso)`) so a UTC-midnight interpretation never shifts the
 * displayed day backward in a negative-UTC-offset timezone. Never shows a
 * timestamp — Data Date is a day-granularity concept throughout this app. */
export function formatDataDate(iso: string | null | undefined): string {
  if (!iso) return "Unavailable";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return "Unavailable";
  const date = new Date(y, m - 1, d);
  if (isNaN(date.getTime())) return "Unavailable";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** The full "Update 03 — Aug 25, 2026 · CURRENT · Overridden" line used in
 * version selector dropdown options. Never derives the date from anything
 * but this exact version's own dataDate field. */
/** Defensive belt-and-suspenders guard for every version selector: even if
 * a version list a screen received ever contained the same version id twice
 * (a duplicate network response, a race between two overlapping fetches,
 * etc.), this collapses it back to one entry per id — first occurrence
 * wins — before it is ever rendered as <option>s. This is applied where
 * each screen stores the fetched version list, so "two rows for the same
 * schedule version" cannot reach the screen even transiently. */
export function dedupeVersionsById<T extends { id?: string }>(versions: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const v of versions) {
    const id = v.id || "";
    if (id && seen.has(id)) continue;
    if (id) seen.add(id);
    out.push(v);
  }
  return out;
}

export function versionSelectLabel(v: VersionLike): string {
  const label = v.versionLabel || "Untitled version";
  const datePart = formatDataDate(v.dataDate);
  const rolePart = v.role && v.role !== "OTHER" ? ` · ${v.role}` : "";
  const overriddenPart = v.dataDateOverridden ? " · Overridden" : "";
  // Two different schedules share this Data Date: chronology is not guessed.
  const conflictPart = v.dataDateConflict ? " · ⚠ Data Date conflict" : "";
  const unresolvedPart = v.previousUnresolved ? " · ⚠ Previous unresolved" : "";
  return `${label} — ${datePart}${rolePart}${overriddenPart}${conflictPart}${unresolvedPart}`;
}

/** Tooltip text for the source Data Date, shown only when an override is
 * in effect — never replaces the effective date, only supplements it. */
export function sourceDataDateTooltip(v: VersionLike): string | undefined {
  if (!v.dataDateOverridden) return undefined;
  return v.sourceDataDate
    ? `Source file Data Date: ${formatDataDate(v.sourceDataDate)}`
    : "Manually entered — no Data Date was detected in the source file";
}
