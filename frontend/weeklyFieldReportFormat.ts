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
