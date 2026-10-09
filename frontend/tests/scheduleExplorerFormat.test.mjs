// Run with:  npm run test:scheduleExplorer   (compiles scheduleExplorerFormat.ts, then node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildWbsTree, wbsTreeRoots, wbsSubtreeActivityCount, longestPathStatusLabel, isLongestPathVerified,
  longestPathCoverageMessage, isLongestPathFilterDisabled, bothViewWarning, wbsHierarchyLimitationMessage,
} from "../.tmp-test/scheduleExplorerFormat.js";

test("buildWbsTree gives similarly-named WBS branches under different parents distinct keys", () => {
  const rows = [
    { wbs: "Foundations", wbsPath: "Area A > Foundations", wbsIdPath: ["AREA_A", "FOUND_A"], wbsSortKey: "0000000001.0000000001" },
    { wbs: "Foundations", wbsPath: "Area B > Foundations", wbsIdPath: ["AREA_B", "FOUND_B"], wbsSortKey: "0000000002.0000000001" },
  ];
  const tree = buildWbsTree(rows);
  assert.equal(tree.size, 4); // AREA_A, FOUND_A, AREA_B, FOUND_B — never merged
  assert.equal(tree.get("FOUND_A").label, "Foundations");
  assert.equal(tree.get("FOUND_B").label, "Foundations");
  assert.notEqual(tree.get("FOUND_A").key, tree.get("FOUND_B").key);
  assert.equal(tree.get("FOUND_A").parentKey, "AREA_A");
  assert.equal(tree.get("FOUND_B").parentKey, "AREA_B");
});

test("buildWbsTree builds correct multi-level parent/child structure", () => {
  const rows = [
    { wbs: "Foundations", wbsPath: "Area A > Foundations", wbsIdPath: ["AREA_A", "FOUND_A"], wbsSortKey: "0000000001.0000000001" },
    { wbs: "Sitework", wbsPath: "Area A > Sitework", wbsIdPath: ["AREA_A", "SITE_A"], wbsSortKey: "0000000001.0000000002" },
  ];
  const tree = buildWbsTree(rows);
  const areaA = tree.get("AREA_A");
  assert.equal(areaA.level, 0);
  assert.deepEqual(areaA.children, ["FOUND_A", "SITE_A"]); // sorted by sortKey
  assert.equal(tree.get("FOUND_A").level, 1);
});

test("buildWbsTree counts activities at the leaf, and aggregates up the subtree", () => {
  const rows = [
    { wbs: "Foundations", wbsPath: "Area A > Foundations", wbsIdPath: ["AREA_A", "FOUND_A"], wbsSortKey: "0000000001.0000000001" },
    { wbs: "Foundations", wbsPath: "Area A > Foundations", wbsIdPath: ["AREA_A", "FOUND_A"], wbsSortKey: "0000000001.0000000001" },
  ];
  const tree = buildWbsTree(rows);
  assert.equal(tree.get("FOUND_A").activityCount, 2);
  assert.equal(tree.get("AREA_A").activityCount, 0); // no activity is directly AT the Area A node itself
  assert.equal(wbsSubtreeActivityCount(tree, "AREA_A"), 2); // but its subtree total includes the descendant
});

test("buildWbsTree falls back to a flat single level when wbsIdPath is absent (non-XER import)", () => {
  const rows = [
    { wbs: "General" }, { wbs: "General" }, { wbs: "Electrical" },
  ];
  const tree = buildWbsTree(rows);
  assert.equal(tree.size, 2);
  assert.equal(tree.get("General").level, 0);
  assert.equal(tree.get("General").parentKey, null);
  assert.equal(tree.get("General").activityCount, 2);
});

test("buildWbsTree treats a missing wbs string as 'Unassigned', never silently dropped", () => {
  const tree = buildWbsTree([{ wbs: null }]);
  assert.equal(tree.get("Unassigned").activityCount, 1);
});

test("buildWbsTree keys the flat fallback by the real wbsId, not the display name, when one exists", () => {
  // The exact shape a real schedule has when it was imported before
  // wbsIdPath existed: wbsId is populated, wbsIdPath is empty. Two
  // DIFFERENT wbs ids that happen to share a display name must not merge.
  const rows = [
    { wbs: "Foundations", wbsId: "100", wbsIdPath: [] },
    { wbs: "Foundations", wbsId: "200", wbsIdPath: [] },
  ];
  const tree = buildWbsTree(rows);
  assert.equal(tree.size, 2);
  assert.equal(tree.get("100").label, "Foundations");
  assert.equal(tree.get("200").label, "Foundations");
  assert.equal(tree.get("100").activityCount, 1);
  assert.equal(tree.get("200").activityCount, 1);
});

test("wbsTreeRoots returns only top-level nodes, sorted by their P6 sort key", () => {
  const rows = [
    { wbs: "B", wbsPath: "Area B", wbsIdPath: ["AREA_B"], wbsSortKey: "0000000002" },
    { wbs: "A", wbsPath: "Area A", wbsIdPath: ["AREA_A"], wbsSortKey: "0000000001" },
  ];
  const tree = buildWbsTree(rows);
  assert.deepEqual(wbsTreeRoots(tree), ["AREA_A", "AREA_B"]);
});

test("longestPathStatusLabel maps all four states to clear text", () => {
  assert.equal(longestPathStatusLabel("YES"), "Yes");
  assert.equal(longestPathStatusLabel("NO"), "No");
  assert.equal(longestPathStatusLabel("UNAVAILABLE"), "Unavailable");
  assert.equal(longestPathStatusLabel("UNKNOWN_LEGACY"), "Unknown (legacy import)");
  assert.equal(longestPathStatusLabel(null), "Unavailable");
  assert.equal(longestPathStatusLabel(undefined), "Unavailable");
});

test("isLongestPathVerified is true only for a genuine YES or NO", () => {
  assert.equal(isLongestPathVerified("YES"), true);
  assert.equal(isLongestPathVerified("NO"), true);
  assert.equal(isLongestPathVerified("UNAVAILABLE"), false);
  assert.equal(isLongestPathVerified("UNKNOWN_LEGACY"), false);
  assert.equal(isLongestPathVerified(null), false);
});

test("longestPathCoverageMessage reports full verification cleanly", () => {
  const msg = longestPathCoverageMessage({ yes: 5, no: 3, unavailable: 0, unknownLegacy: 0, total: 8 });
  assert.equal(msg, "Longest Path verified for all 8 activities.");
});

test("longestPathCoverageMessage reports partial verification with a count", () => {
  const msg = longestPathCoverageMessage({ yes: 5, no: 3, unavailable: 2, unknownLegacy: 0, total: 10 });
  assert.equal(msg, "Longest Path verified for 8 of 10 activities; 2 unverified.");
});

test("longestPathCoverageMessage distinguishes a legacy-import schedule from a genuinely unsupported one", () => {
  const legacy = longestPathCoverageMessage({ yes: 0, no: 0, unavailable: 0, unknownLegacy: 6, total: 6 });
  assert.match(legacy, /imported before Longest Path verification tracking/);
  const unsupported = longestPathCoverageMessage({ yes: 0, no: 0, unavailable: 6, unknownLegacy: 0, total: 6 });
  assert.match(unsupported, /did not include P6's Longest Path flag/);
  assert.notEqual(legacy, unsupported);
});

test("longestPathCoverageMessage handles an empty schedule without dividing by zero", () => {
  assert.equal(longestPathCoverageMessage({ yes: 0, no: 0, unavailable: 0, unknownLegacy: 0, total: 0 }), "No activities to evaluate.");
});

test("isLongestPathFilterDisabled is true only when there is zero verified coverage", () => {
  assert.equal(isLongestPathFilterDisabled({ yes: 0, no: 0, unavailable: 5, unknownLegacy: 0, total: 5 }), true);
  assert.equal(isLongestPathFilterDisabled({ yes: 0, no: 0, unavailable: 0, unknownLegacy: 5, total: 5 }), true);
  assert.equal(isLongestPathFilterDisabled({ yes: 1, no: 0, unavailable: 4, unknownLegacy: 0, total: 5 }), false);
  assert.equal(isLongestPathFilterDisabled({ yes: 0, no: 1, unavailable: 4, unknownLegacy: 0, total: 5 }), false);
});

test("bothViewWarning is null when Longest Path is fully verified — nothing to warn about", () => {
  assert.equal(bothViewWarning({ yes: 4, no: 6, unavailable: 0, unknownLegacy: 0, total: 10 }), null);
});

test("bothViewWarning is null for an empty schedule", () => {
  assert.equal(bothViewWarning({ yes: 0, no: 0, unavailable: 0, unknownLegacy: 0, total: 0 }), null);
});

test("bothViewWarning warns plainly when coverage is entirely unavailable", () => {
  const msg = bothViewWarning({ yes: 0, no: 0, unavailable: 0, unknownLegacy: 10, total: 10 });
  assert.match(msg, /unavailable for this schedule version/);
  assert.match(msg, /known Critical Path activities only/);
});

test("bothViewWarning reports the exact unverified count for partial coverage", () => {
  const msg = bothViewWarning({ yes: 4, no: 4, unavailable: 2, unknownLegacy: 0, total: 10 });
  assert.match(msg, /incomplete \(2 of 10 activities unverified\)/);
});

test("wbsHierarchyLimitationMessage is null when any activity carries a real ancestor path", () => {
  assert.equal(wbsHierarchyLimitationMessage({ hasHierarchy: true, rowsWithHierarchy: 2, total: 10 }), null);
});

test("wbsHierarchyLimitationMessage warns about exact-match-only filtering when no hierarchy exists", () => {
  const msg = wbsHierarchyLimitationMessage({ hasHierarchy: false, rowsWithHierarchy: 0, total: 10 });
  assert.match(msg, /exact node selected only, not its descendants/);
});

test("wbsHierarchyLimitationMessage is null for an empty schedule", () => {
  assert.equal(wbsHierarchyLimitationMessage({ hasHierarchy: false, rowsWithHierarchy: 0, total: 0 }), null);
});
