// Run with:  npm run test:weeklyFieldReport   (compiles weeklyFieldReportFormat.ts, then node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computeCostUtilizationPercent, formatCostUtilizationPercent, formatHeadcount,
  mondayOfWeek, isMonday,
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
