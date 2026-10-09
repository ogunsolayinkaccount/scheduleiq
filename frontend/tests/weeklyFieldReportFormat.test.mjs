// Run with:  npm run test:weeklyFieldReport   (compiles weeklyFieldReportFormat.ts, then node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computeCostUtilizationPercent, formatCostUtilizationPercent, formatHeadcount,
  mondayOfWeek, isMonday, computeForecastAccuracy,
} from "../.tmp-test/weeklyFieldReportFormat.js";

test("computeCostUtilizationPercent divides actual cost by budget, as a percentage", () => {
  assert.equal(computeCostUtilizationPercent(28873, 66680), 43.3);
});

test("computeCostUtilizationPercent is null when budget is missing", () => {
  assert.equal(computeCostUtilizationPercent(1000, null), null);
  assert.equal(computeCostUtilizationPercent(1000, undefined), null);
});

test("computeCostUtilizationPercent is null when budget is zero", () => {
  assert.equal(computeCostUtilizationPercent(1000, 0), null);
});

test("computeCostUtilizationPercent is null when budget is negative", () => {
  assert.equal(computeCostUtilizationPercent(1000, -500), null);
});

test("computeCostUtilizationPercent is null when actual cost is missing, never fabricated as 0%", () => {
  assert.equal(computeCostUtilizationPercent(null, 66680), null);
});

test("computeCostUtilizationPercent handles a project at exactly 100%", () => {
  assert.equal(computeCostUtilizationPercent(50000, 50000), 100);
});

test("computeCostUtilizationPercent handles zero actual cost as a real 0%, not Unavailable", () => {
  assert.equal(computeCostUtilizationPercent(0, 50000), 0);
});

test("formatCostUtilizationPercent renders Unavailable for null, never 0%", () => {
  assert.equal(formatCostUtilizationPercent(null), "Unavailable");
  assert.equal(formatCostUtilizationPercent(43.3), "43.3%");
});

test("formatHeadcount renders Unavailable for null/undefined, never 0", () => {
  assert.equal(formatHeadcount(null), "Unavailable");
  assert.equal(formatHeadcount(undefined), "Unavailable");
  assert.equal(formatHeadcount(0), "0");
  assert.equal(formatHeadcount(39), "39");
});

test("mondayOfWeek leaves an actual Monday unchanged", () => {
  assert.equal(mondayOfWeek("2026-10-05"), "2026-10-05");
});

test("mondayOfWeek snaps a mid-week date back to that week's Monday", () => {
  assert.equal(mondayOfWeek("2026-10-06"), "2026-10-05"); // Tuesday
  assert.equal(mondayOfWeek("2026-10-09"), "2026-10-05"); // Friday
});

test("mondayOfWeek snaps Sunday back to the Monday that started its own week, not the next one", () => {
  assert.equal(mondayOfWeek("2026-10-11"), "2026-10-05"); // Sunday belongs to the week starting 10-05
});

test("isMonday is true only for an actual Monday", () => {
  assert.equal(isMonday("2026-09-14"), true);
  assert.equal(isMonday("2026-09-21"), true);
  assert.equal(isMonday("2026-09-16"), false);
  assert.equal(isMonday("2026-09-20"), false); // Sunday
});

test("computeForecastAccuracy pairs week N's forecast with week N+1's actual", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 35 },
    { weekStartDate: "2026-09-21", actualHeadcount: 39, nextWeekForecastHeadcount: 40 },
  ]);
  assert.equal(points.length, 1);
  assert.equal(points[0].forecastWeek, "2026-09-14");
  assert.equal(points[0].actualWeek, "2026-09-21");
  assert.equal(points[0].forecastHeadcount, 35);
  assert.equal(points[0].actualHeadcount, 39);
  assert.equal(points[0].varianceHeadcount, 4); // 39 - 35 — positive: under-forecasted (actual exceeded plan)
  assert.equal(points[0].errorPercent, Math.round((4 / 35) * 1000) / 10);
  assert.equal(points[0].accuracyPercent, Math.round((1 - 4 / 35) * 1000) / 10);
});

test("computeForecastAccuracy is exact at 100% when actual matches the forecast exactly", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 35 },
    { weekStartDate: "2026-09-21", actualHeadcount: 35, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points[0].accuracyPercent, 100);
});

test("computeForecastAccuracy skips a pair across a missing week rather than approximating", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 35 },
    // 2026-09-21 is missing entirely
    { weekStartDate: "2026-09-28", actualHeadcount: 39, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points.length, 0);
});

test("computeForecastAccuracy skips a pair when the forecast was never entered", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: null },
    { weekStartDate: "2026-09-21", actualHeadcount: 39, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points.length, 0);
});

test("computeForecastAccuracy skips a pair when the following week's actual isn't verified yet", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 35 },
    { weekStartDate: "2026-09-21", actualHeadcount: null, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points.length, 0);
});

test("computeForecastAccuracy sorts input first, independent of the order reports were passed in", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-21", actualHeadcount: 39, nextWeekForecastHeadcount: 40 },
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 35 },
  ]);
  assert.equal(points.length, 1);
  assert.equal(points[0].forecastWeek, "2026-09-14");
});

test("computeForecastAccuracy never divides by a zero or negative forecast", () => {
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 5, nextWeekForecastHeadcount: 0 },
    { weekStartDate: "2026-09-21", actualHeadcount: 5, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points[0].errorPercent, null);
  assert.equal(points[0].accuracyPercent, null);
});

test("computeForecastAccuracy signs the error negative when the forecast was too HIGH (over-forecast)", () => {
  // Forecast 50, actual only 40 — actual fell short of plan.
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 50 },
    { weekStartDate: "2026-09-21", actualHeadcount: 40, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points[0].varianceHeadcount, -10);
  assert.equal(points[0].errorPercent, -20); // (40-50)/50*100
  assert.equal(points[0].accuracyPercent, 80);
});

test("computeForecastAccuracy signs the error positive when the forecast was too LOW (under-forecast)", () => {
  // Forecast 20, actual 30 — actual exceeded plan.
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 20 },
    { weekStartDate: "2026-09-21", actualHeadcount: 30, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points[0].varianceHeadcount, 10);
  assert.equal(points[0].errorPercent, 50); // (30-20)/20*100
  assert.equal(points[0].accuracyPercent, 50);
});

test("computeForecastAccuracy floors accuracyPercent at 0 instead of going negative on a severe miss", () => {
  // Forecast 10, actual 40 — a 300% error. accuracyPercent must never read
  // as a negative number (e.g. -200%), which would misleadingly suggest
  // "negative accuracy" rather than simply "a very large miss."
  const points = computeForecastAccuracy([
    { weekStartDate: "2026-09-14", actualHeadcount: 30, nextWeekForecastHeadcount: 10 },
    { weekStartDate: "2026-09-21", actualHeadcount: 40, nextWeekForecastHeadcount: null },
  ]);
  assert.equal(points[0].errorPercent, 300); // the signed error itself is NOT floored — only accuracyPercent is
  assert.equal(points[0].accuracyPercent, 0);
});
