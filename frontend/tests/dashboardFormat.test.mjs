// Run with:  npm run test:dashboard   (compiles dashboardFormat.ts, then node --test)
//
// Main Dashboard's core rule: never show 0 / 1.0 for data that doesn't
// exist. These pin the formatters that enforce it.
import test from "node:test";
import assert from "node:assert/strict";
import { fmtDays, fmtHours, fmtFloat } from "../.tmp-test/dashboardFormat.js";

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
