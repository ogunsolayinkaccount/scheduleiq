// Main Dashboard's own small formatting helpers — pulled out of Dashboard.tsx
// so the "never show 0/1.0 for missing data" rule is unit-testable without a
// React/DOM harness, matching the dateFormat.ts/projectScope.ts pattern.

export function fmtDays(n: number | null | undefined): string {
  if (n == null) return "Unavailable";
  return `${n > 0 ? "+" : ""}${n}d`;
}

export function fmtHours(n: number | null | undefined): string {
  if (n == null) return "Unavailable";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K h` : `${Math.round(n)} h`;
}

export function fmtFloat(n: number | null | undefined): string {
  if (n == null) return "Unavailable";
  return `${n}d`;
}

export const RISK_COLOR_KEY: Record<string, string> = {
  Critical: "red", "At Risk": "amber", Watch: "gold", Healthy: "green",
};

// Activity Status pie chart (App.tsx) — outside-label text and the
// narrow-card responsive fallback threshold, pulled out so both are
// unit-testable without a React/DOM harness, same reasoning as the rest
// of this file. The percentage itself is never computed here — callers
// always pass Recharts' own `percent` (value / total of the pie's data
// array, recomputed every render), this only formats it.
export function formatStatusPieLabel(name: string, value: number, percent: number): string | null {
  if (!value) return null; // zero-count statuses get no outside label
  return `${Math.round(percent * 100)}% ${name}`;
}

// Below this rendered pixel width, outside labels + leader lines cannot
// reliably fit without overlapping the pie, each other, or the card edge
// — the chart falls back to the original inside-percentage-only style.
export const STATUS_PIE_WIDE_THRESHOLD_PX = 260;
export function isWideEnoughForOutsideLabels(width: number): boolean {
  return width >= STATUS_PIE_WIDE_THRESHOLD_PX;
}

// Cross-Filter Dashboard pie charts (Activity Status, Criticality
// Distribution) — the tooltip shows one decimal place of precision
// ("23.4%") while the outside label stays a whole number, per that
// dashboard's own request. `total` is always the pie's own currently
// FILTERED count (every slicer/cross-filter already narrows the data
// array before it reaches the chart), never the unfiltered project
// total — this function only formats whatever total it's given.
export function formatPieTooltipPercent(value: number, total: number): string {
  if (total <= 0) return "0.0%";
  return `${((value / total) * 100).toFixed(1)}%`;
}
