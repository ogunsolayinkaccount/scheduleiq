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
