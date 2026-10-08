// Run with:  npm run test:dashboard   (compiles dashboardFormat.ts, then node --test)
//
// Main Dashboard's core rule: never show 0 / 1.0 for data that doesn't
// exist. These pin the formatters that enforce it.
import test from "node:test";
import assert from "node:assert/strict";
import {
  fmtDays, fmtHours, fmtFloat, formatStatusPieLabel, isWideEnoughForOutsideLabels, STATUS_PIE_WIDE_THRESHOLD_PX,
  formatPieTooltipPercent,
} from "../.tmp-test/dashboardFormat.js";

test("fmtDays shows Unavailable for null/undefined, never 0", () => {
  assert.equal(fmtDays(null), "Unavailable");
  assert.equal(fmtDays(undefined), "Unavailable");
});

test("fmtDays shows a genuine 0 as 0d, not Unavailable", () => {
  assert.equal(fmtDays(0), "0d");
});

test("fmtDays signs positive values and leaves negative values as-is", () => {
  assert.equal(fmtDays(5), "+5d");
  assert.equal(fmtDays(-5), "-5d");
});

test("fmtHours shows Unavailable for null/undefined, never 0h", () => {
  assert.equal(fmtHours(null), "Unavailable");
  assert.equal(fmtHours(undefined), "Unavailable");
});

test("fmtHours shows a genuine 0 as 0 h, not Unavailable", () => {
  assert.equal(fmtHours(0), "0 h");
});

test("fmtHours abbreviates large values to K", () => {
  assert.equal(fmtHours(91045), "91.0K h");
  assert.equal(fmtHours(500), "500 h");
});

test("fmtFloat shows Unavailable for null/undefined, never 0d", () => {
  assert.equal(fmtFloat(null), "Unavailable");
  assert.equal(fmtFloat(undefined), "Unavailable");
});

test("fmtFloat shows a genuine 0 as 0d, not Unavailable", () => {
  assert.equal(fmtFloat(0), "0d");
});

test("fmtFloat shows negative float plainly", () => {
  assert.equal(fmtFloat(-45), "-45d");
});

// Activity Status pie chart — outside label text + responsive threshold.

test("formatStatusPieLabel puts the percentage before the status name", () => {
  assert.equal(formatStatusPieLabel("Not Started", 115, 0.23), "23% Not Started");
});

test("formatStatusPieLabel rounds to a whole percentage, matching the example in the request", () => {
  assert.equal(formatStatusPieLabel("Active", 500, 0.227), "23% Active");
});

test("formatStatusPieLabel returns null for a zero-count status — no outside label is shown", () => {
  assert.equal(formatStatusPieLabel("Complete", 0, 0), null);
});

test("formatStatusPieLabel treats a genuinely empty slice (percent 0 but no value) the same way", () => {
  assert.equal(formatStatusPieLabel("Not Started", 0, 0), null);
});

test("formatStatusPieLabel never fabricates a percentage independent of the caller's own value", () => {
  // The function only formats whatever percent/value it's given — it does
  // not recompute a total itself, so passing Recharts' own per-render
  // percent (value / sum of the pie's data) is what keeps this dynamic.
  assert.equal(formatStatusPieLabel("In Progress", 50, 0.5), "50% In Progress");
  assert.equal(formatStatusPieLabel("In Progress", 50, 0.33), "33% In Progress");
});

test("isWideEnoughForOutsideLabels matches the documented threshold exactly", () => {
  assert.equal(isWideEnoughForOutsideLabels(STATUS_PIE_WIDE_THRESHOLD_PX), true);
  assert.equal(isWideEnoughForOutsideLabels(STATUS_PIE_WIDE_THRESHOLD_PX - 1), false);
});

test("isWideEnoughForOutsideLabels is true for a typical desktop card width", () => {
  assert.equal(isWideEnoughForOutsideLabels(360), true);
});

test("isWideEnoughForOutsideLabels is false for a narrow mobile/tablet card", () => {
  assert.equal(isWideEnoughForOutsideLabels(200), false);
});

// Cross-Filter Dashboard pie tooltips — one decimal place of precision,
// always computed from the FILTERED total the caller passes in (never
// recomputed from an unfiltered project total here).

test("formatPieTooltipPercent shows one decimal place, matching the request's own example", () => {
  assert.equal(formatPieTooltipPercent(538, 2299), "23.4%");
});

test("formatPieTooltipPercent reflects whatever total it is given — e.g. a filtered subset", () => {
  // Same 538 activities, but a narrower filtered total — the percentage
  // must change accordingly, never stay pinned to an original total.
  assert.equal(formatPieTooltipPercent(538, 600), "89.7%");
});

test("formatPieTooltipPercent never divides by zero", () => {
  assert.equal(formatPieTooltipPercent(0, 0), "0.0%");
});

test("formatPieTooltipPercent handles a full 100% slice", () => {
  assert.equal(formatPieTooltipPercent(42, 42), "100.0%");
});
