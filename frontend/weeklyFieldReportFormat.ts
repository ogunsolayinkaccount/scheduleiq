// Pure, testable helpers for the Field Dashboard's "Weekly Field Operations
// Review" panel (FieldDashboard.tsx). The only real calculation this
// feature introduces anywhere is Cost Utilization — a presentation-layer
// ratio of two numbers (Actual Cost, Current Budget) the Field Dashboard
// already fetches from compute_cost_summary/_compute_budget_breakdown via
// /field-dashboard-summary/. Nothing here recalculates AC or Budget, and
// nothing here ever derives a headcount from P6 resource hours.

/**
 * Cost Utilization % = Actual Cost / Current Budget x 100. Returns null
 * (never 0, never a fabricated number) when budget is missing, zero, or
 * negative, or when actualCost is missing — "Unavailable" is always the
 * honest result for an unsupported schedule, matching every other ratio
 * in this codebase (e.g. SPI/CPI's own null-on-missing-input discipline).
 */
export function computeCostUtilizationPercent(
  actualCost: number | null | undefined,
  budget: number | null | undefined,
): number | null {
  if (actualCost == null || budget == null || budget <= 0) return null;
  return Math.round((actualCost / budget) * 1000) / 10;
}

export function formatCostUtilizationPercent(pct: number | null): string {
  return pct == null ? "Unavailable" : `${pct}%`;
}

/** Headcount fields are literal counts of people, never hours — this only
 * ever renders a number ScheduleIQ was explicitly told, never one it
 * inferred from compute_productivity's budgetedHours/actualHours. */
export function formatHeadcount(n: number | null | undefined): string {
  return n == null ? "Unavailable" : `${n}`;
}

/**
 * The reporting week's start — Monday — for a given date, so a weekly
 * report always identifies the same calendar week regardless of which day
 * within it a user happens to pick. Enforced here (snap-on-change in the
 * create form) AND independently on the backend (project_weekly_field_
 * reports rejects a non-Monday weekStartDate) — the same defense-in-depth
 * convention this codebase uses everywhere else (never trust the client
 * alone for a rule the server can enforce).
 */
export function mondayOfWeek(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00`);
  const day = d.getDay(); // 0=Sunday .. 6=Saturday
  const diff = day === 0 ? 6 : day - 1;
  d.setDate(d.getDate() - diff);
  return d.toISOString().slice(0, 10);
}

export function isMonday(dateStr: string): boolean {
  return mondayOfWeek(dateStr) === dateStr;
}

export type WeeklyReportHistoryRow = {
  weekStartDate: string;
  actualHeadcount: number | null;
  nextWeekForecastHeadcount: number | null;
};

export type ForecastAccuracyPoint = {
  forecastWeek: string; actualWeek: string;
  forecastHeadcount: number; actualHeadcount: number;
  // actual − forecast, in workers. Sign convention: POSITIVE means actual
  // headcount EXCEEDED the forecast (the forecast was too LOW — an
  // under-forecast); NEGATIVE means actual came in BELOW the forecast (the
  // forecast was too HIGH — an over-forecast); zero is an exact match.
  varianceHeadcount: number;
  // (actual − forecast) / forecast × 100 — same sign convention as
  // varianceHeadcount above. null only when the forecast itself was 0
  // (division is undefined, never shown as a fabricated 0% or Infinity).
  errorPercent: number | null;
  // 100 − |errorPercent|, FLOORED at 0 — never negative. A large miss
  // (error magnitude ≥ 100%) reads as 0%, not as a confusing negative
  // number; errorPercent is the primary, signed metric for over/under-
  // forecasting — accuracyPercent is a secondary, bounded-for-display one.
  accuracyPercent: number | null;
};

/**
 * Forecast accuracy = how close week N's "next week" forecast was to week
 * N+1's verified actual headcount. Three figures are always derived
 * together from the same two numbers — Forecast Variance (workers) and
 * Forecast Error (%) are the primary, signed metrics (see field comments
 * above for the sign convention); Accuracy (%) is a secondary, bounded
 * [0, 100] figure for display, never negative.
 *
 * Only pairs EXACTLY 7 days apart are compared — a missing week (any gap)
 * is skipped entirely rather than compared across the gap, and a pair
 * missing either value (forecast not entered, or the next week's actual
 * not yet verified) is also skipped. This never invents a value for a
 * week that wasn't reported, matching every other "Unavailable, not 0"
 * rule in this codebase.
 */
export function computeForecastAccuracy(reports: WeeklyReportHistoryRow[]): ForecastAccuracyPoint[] {
  const sorted = [...reports].sort((a, b) => a.weekStartDate.localeCompare(b.weekStartDate));
  const points: ForecastAccuracyPoint[] = [];

  for (let i = 0; i < sorted.length - 1; i++) {
    const week = sorted[i];
    const nextWeek = sorted[i + 1];
    if (week.nextWeekForecastHeadcount == null || nextWeek.actualHeadcount == null) continue;

    const weekMs = new Date(`${week.weekStartDate}T00:00:00`).getTime();
    const nextMs = new Date(`${nextWeek.weekStartDate}T00:00:00`).getTime();
    const daysApart = Math.round((nextMs - weekMs) / 86_400_000);
    if (daysApart !== 7) continue; // a missing week in between — never approximate across it

    const forecast = week.nextWeekForecastHeadcount;
    const actual = nextWeek.actualHeadcount;
    const variance = actual - forecast;
    const rawErrorPercent = forecast > 0 ? (variance / forecast) * 100 : null;
    const errorPercent = rawErrorPercent == null ? null : Math.round(rawErrorPercent * 10) / 10;
    const accuracyPercent = rawErrorPercent == null ? null : Math.max(0, Math.round((100 - Math.abs(rawErrorPercent)) * 10) / 10);

    points.push({
      forecastWeek: week.weekStartDate, actualWeek: nextWeek.weekStartDate,
      forecastHeadcount: forecast, actualHeadcount: actual,
      varianceHeadcount: variance, errorPercent, accuracyPercent,
    });
  }

  return points;
}
