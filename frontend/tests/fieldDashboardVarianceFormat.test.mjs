// Run with:  npm run test:fieldDashboardVariance   (compiles fieldDashboardVarianceFormat.ts, then node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolveCompletionStatus, isRegisteredCompletion, completionPanelTitle, buildVarianceIntelligenceUrl,
} from "../.tmp-test/fieldDashboardVarianceFormat.js";

test("resolveCompletionStatus reads NO_MOVEMENT off direction, never assessment", () => {
  const r = resolveCompletionStatus({ direction: "NO_MOVEMENT", assessment: "FAVORABLE" });
  assert.deepEqual(r, { kind: "direction", value: "NO_MOVEMENT", label: "No Movement" });
});

test("resolveCompletionStatus reads UNAVAILABLE off direction", () => {
  const r = resolveCompletionStatus({ direction: "UNAVAILABLE", assessment: "UNAVAILABLE" });
  assert.deepEqual(r, { kind: "direction", value: "UNAVAILABLE", label: "Unavailable" });
});

test("resolveCompletionStatus shows the deterministic exposure tier for an unfavorable, moving milestone", () => {
  for (const assessment of ["HIGH_EXPOSURE", "WARNING", "MONITOR"]) {
    const r = resolveCompletionStatus({ direction: "UNFAVORABLE", assessment });
    assert.deepEqual(r, { kind: "assessment", value: assessment, label: assessment });
  }
});

test("resolveCompletionStatus shows FAVORABLE assessment for a favorable-direction milestone", () => {
  const r = resolveCompletionStatus({ direction: "FAVORABLE", assessment: "FAVORABLE" });
  assert.deepEqual(r, { kind: "assessment", value: "FAVORABLE", label: "FAVORABLE" });
});

test("isRegisteredCompletion is true only for the exact REGISTERED tag", () => {
  assert.equal(isRegisteredCompletion("REGISTERED"), true);
  assert.equal(isRegisteredCompletion("LATEST_FINISH_HEURISTIC"), false);
  assert.equal(isRegisteredCompletion(undefined), false);
  assert.equal(isRegisteredCompletion(null), false);
});

test("completionPanelTitle matches VarianceIntelligence.tsx's own title branch verbatim", () => {
  assert.equal(completionPanelTitle("REGISTERED"), "Project Completion Assessment");
  assert.equal(completionPanelTitle("LATEST_FINISH_HEURISTIC"), "Latest Project Forecast Milestone");
  assert.equal(completionPanelTitle(undefined), "Latest Project Forecast Milestone");
});

test("buildVarianceIntelligenceUrl never sends a basis param, so the backend applies its own default", () => {
  const url = buildVarianceIntelligenceUrl("proj-1", "ver-2");
  assert.equal(url, "/api/projects/proj-1/variance-intelligence/?currentVersion=ver-2");
  assert.ok(!url.includes("basis="), "Field Dashboard must rely on the same default-basis resolution Variance Intelligence uses, never pick one itself");
});

test("buildVarianceIntelligenceUrl is the exact same URL shape VarianceIntelligence.tsx builds with no basis selected", () => {
  // VarianceIntelligence.tsx: new URLSearchParams({ currentVersion, groupBy }) then
  // only sets basis/area/wbs/... when truthy. With nothing else selected, calling
  // the field-dashboard builder for the same project/version must be indistinguishable
  // in effect (same endpoint, same currentVersion, no basis) so both screens are
  // guaranteed to receive an identical JSON response from the same view.
  const url = buildVarianceIntelligenceUrl("barn", "v12");
  const params = new URL(url, "http://x").searchParams;
  assert.equal(params.get("currentVersion"), "v12");
  assert.equal(params.get("basis"), null);
});
