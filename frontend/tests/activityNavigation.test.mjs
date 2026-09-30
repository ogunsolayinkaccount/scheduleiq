// Run with:  npm run test:activityNav   (compiles activityNavigation.ts, then node --test)
//
// The legacy Activities register was retired in favor of Activity Analysis
// (Phase 1 consolidation, item #3). These tests pin the translation table
// that keeps Portfolio's KPI drill-through, global search "jump to
// activity", and the "view this project" flow all landing on the same
// filtered result they used to show in the old register.
import test from "node:test";
import assert from "node:assert/strict";
import { legacyFilterToActivityAnalysis, activityJumpFilter } from "../.tmp-test/activityNavigation.js";

test("ALL maps to no filter (Activity Analysis's own default view)", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("ALL"), {});
});

test("COMPLETE maps to status: COMPLETE", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("COMPLETE"), { status: "COMPLETE" });
});

test("CRITICAL maps to criticality: CRITICAL (Activity Analysis's actionable-critical bucket)", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("CRITICAL"), { criticality: "CRITICAL" });
});

test("OVERDUE maps to status: OVERDUE", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("OVERDUE"), { status: "OVERDUE" });
});

test("NOTSTART maps to status: NOT_STARTED", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("NOTSTART"), { status: "NOT_STARTED" });
});

test("INPROG maps to status: IN_PROGRESS", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("INPROG"), { status: "IN_PROGRESS" });
});

test("MILESTON maps to milestonesOnly: true", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("MILESTON"), { milestonesOnly: true });
});

test("NEGFLOAT maps to tf: NEGATIVE", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("NEGFLOAT"), { tf: "NEGATIVE" });
});

test("NEARCRIT maps to criticality: NEAR_CRITICAL", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("NEARCRIT"), { criticality: "NEAR_CRITICAL" });
});

test("an unrecognized code falls back to no filter rather than throwing", () => {
  assert.deepEqual(legacyFilterToActivityAnalysis("SOMETHING_NEW"), {});
});

test("activityJumpFilter seeds a search from the activity's code", () => {
  assert.deepEqual(activityJumpFilter("A1050"), { search: "A1050" });
});

test("activityJumpFilter seeds a search from the activity's name when code is absent", () => {
  assert.deepEqual(activityJumpFilter("Pour Foundation"), { search: "Pour Foundation" });
});

test("activityJumpFilter with nothing to search on applies no filter", () => {
  assert.deepEqual(activityJumpFilter(""), {});
  assert.deepEqual(activityJumpFilter(undefined), {});
  assert.deepEqual(activityJumpFilter(null), {});
});
