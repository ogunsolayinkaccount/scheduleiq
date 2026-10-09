// Pure, testable helpers for the Critical Path & Longest Path Schedule
// Explorer (ScheduleExplorer.tsx). Filtering, counts, and Longest Path
// provenance classification all happen server-side (activity_analysis.py
// already computes longestPathStatus; the schedule-explorer/activities/
// endpoint already applies WBS/area/status/search/path filters) — nothing
// here recalculates a date, Total Float, or path classification. This
// file builds the WBS TREE STRUCTURE for the hierarchical filter/grouping
// UI from the already-fetched flat row list, and maps the four Longest
// Path states to display text.

export type WbsSourceRow = {
  wbs?: string | null;
  wbsId?: string | null;
  wbsPath?: string | null;
  wbsIdPath?: string[] | null;
  wbsSortKey?: string | null;
};

export type WbsTreeNode = {
  key: string;
  label: string;
  level: number;
  sortKey: string;
  parentKey: string | null;
  children: string[];
  activityCount: number;
};

/**
 * Builds a WBS tree keyed by the real P6 wbs_id at every level (never by
 * display name), from the flat row list the Explorer already fetched —
 * no second server round-trip. Two WBS nodes sharing a leaf name under
 * different parents (e.g. "Foundations" under both Area A and Area B)
 * always get distinct keys here, because the key is the real id, not the
 * name — a name-based prefix match could not make this guarantee.
 *
 * A row with no `wbsIdPath` falls back to a single flat level — keyed by
 * the row's own `wbsId` when one exists (a real schedule imported before
 * `wbsIdPath` existed still HAS a real wbsId per activity, just not its
 * full ancestor chain; keying by that id — not the display name — keeps
 * the exact-match filter correct), or by the plain `wbs` display string
 * only when no id exists at all (a genuinely non-hierarchical, non-XER
 * import), matching GanttView.tsx's own long-established fallback.
 */
export function buildWbsTree(rows: WbsSourceRow[]): Map<string, WbsTreeNode> {
  const nodes = new Map<string, WbsTreeNode>();

  for (const row of rows) {
    const idPath = row.wbsIdPath && row.wbsIdPath.length > 0 ? row.wbsIdPath : null;

    if (idPath) {
      const namePath = (row.wbsPath || "").split(" > ");
      const sortSegments = (row.wbsSortKey || "").split(".");
      let parentKey: string | null = null;
      for (let i = 0; i < idPath.length; i++) {
        const key = idPath[i];
        if (!nodes.has(key)) {
          nodes.set(key, {
            key, label: namePath[i] || key, level: i,
            sortKey: sortSegments.slice(0, i + 1).join("."),
            parentKey, children: [], activityCount: 0,
          });
          if (parentKey) {
            const parent = nodes.get(parentKey);
            if (parent && !parent.children.includes(key)) parent.children.push(key);
          }
        }
        parentKey = key;
      }
      const leaf = nodes.get(idPath[idPath.length - 1]);
      if (leaf) leaf.activityCount++;
    } else {
      const key = row.wbsId || row.wbs || "Unassigned";
      const label = row.wbs || row.wbsId || "Unassigned";
      if (!nodes.has(key)) {
        nodes.set(key, { key, label, level: 0, sortKey: label, parentKey: null, children: [], activityCount: 0 });
      }
      const flat = nodes.get(key);
      if (flat) flat.activityCount++;
    }
  }

  for (const node of nodes.values()) {
    node.children.sort((a, b) => {
      const na = nodes.get(a), nb = nodes.get(b);
      return (na?.sortKey || a).localeCompare(nb?.sortKey || b);
    });
  }

  return nodes;
}

export function wbsTreeRoots(nodes: Map<string, WbsTreeNode>): string[] {
  return Array.from(nodes.values())
    .filter(n => n.parentKey === null)
    .sort((a, b) => a.sortKey.localeCompare(b.sortKey))
    .map(n => n.key);
}

/** Total activity count for a node INCLUDING every descendant — the
 * number the WBS filter UI should show next to a collapsed parent node,
 * not just its own direct activityCount. */
export function wbsSubtreeActivityCount(nodes: Map<string, WbsTreeNode>, key: string): number {
  const node = nodes.get(key);
  if (!node) return 0;
  let total = node.activityCount;
  for (const childKey of node.children) total += wbsSubtreeActivityCount(nodes, childKey);
  return total;
}

export const LONGEST_PATH_STATUS_LABEL: Record<string, string> = {
  YES: "Yes", NO: "No", UNAVAILABLE: "Unavailable", UNKNOWN_LEGACY: "Unknown (legacy import)",
};

export function longestPathStatusLabel(status: string | null | undefined): string {
  return LONGEST_PATH_STATUS_LABEL[status || ""] || "Unavailable";
}

/** Whether a Longest-Path status represents a genuinely VERIFIED Yes/No
 * (as opposed to UNAVAILABLE or UNKNOWN_LEGACY) — used to decide whether
 * the "Longest Path" filter pill should even be enabled/meaningful for
 * the current schedule version. */
export function isLongestPathVerified(status: string | null | undefined): boolean {
  return status === 'YES' || status === 'NO';
}

export type LongestPathCoverage = { yes: number; no: number; unavailable: number; unknownLegacy: number; total: number };

/**
 * One honest sentence describing this version's Longest Path data
 * quality — never implying verified membership exists when it doesn't,
 * and distinguishing "this file genuinely lacks the flag" from "this
 * schedule predates verification tracking" (the two states that used to
 * be indistinguishable, per the Schedule Explorer audit's central finding).
 */
export function longestPathCoverageMessage(coverage: LongestPathCoverage): string {
  if (coverage.total === 0) return 'No activities to evaluate.';
  const verified = coverage.yes + coverage.no;
  if (verified > 0) {
    const unverified = coverage.unavailable + coverage.unknownLegacy;
    return unverified > 0
      ? `Longest Path verified for ${verified} of ${coverage.total} activities; ${unverified} unverified.`
      : `Longest Path verified for all ${coverage.total} activities.`;
  }
  if (coverage.unknownLegacy === coverage.total) {
    return 'This schedule was imported before Longest Path verification tracking existed — re-import the source file to enable it.';
  }
  if (coverage.unavailable === coverage.total) {
    return "This schedule's source file did not include P6's Longest Path flag — Longest Path filtering is unavailable for this version.";
  }
  return 'Longest Path is not verified for any activity in this version.';
}

/**
 * Only the Longest Path pill should ever be disabled — Critical Path and
 * Both must stay usable regardless of Longest Path data quality (the
 * review's explicit correction: a team must never lose visibility into
 * known Critical Path activities just because Longest Path verification
 * is unavailable or incomplete for this schedule version).
 */
export function isLongestPathFilterDisabled(coverage: LongestPathCoverage): boolean {
  return coverage.yes + coverage.no === 0;
}

export type WbsHierarchySupport = { hasHierarchy: boolean; rowsWithHierarchy: number; total: number };

/**
 * Null when this version has a real, multi-level PROJWBS ancestor path
 * for at least one activity (genuine parent + descendants filtering is
 * possible); otherwise an explicit caveat that WBS filtering can only
 * match the EXACT node selected, never its descendants — because no
 * ancestor-path data exists to walk. A schedule imported before
 * wbsIdPath existed (confirmed live against the real Project Barn
 * version) has this shape: a real wbsId per activity, but no path —
 * selecting a WBS here must never be presented as full hierarchical
 * filtering when it's actually exact-match only.
 */
export function wbsHierarchyLimitationMessage(support: WbsHierarchySupport): string | null {
  if (support.total === 0 || support.hasHierarchy) return null;
  return 'This schedule version has no multi-level WBS hierarchy data — WBS filtering matches the exact node selected only, not its descendants. Re-import the source XER to enable full parent + descendants filtering.';
}

/**
 * A prominent, honest caveat for the "Both" view specifically — null when
 * Longest Path is fully verified for every activity (nothing to warn
 * about), otherwise a sentence making clear "Both" is showing known
 * Critical Path activities but may be missing Longest Path activities
 * among the unverified ones. Never silently presents "Both" as complete
 * when it isn't.
 */
export function bothViewWarning(coverage: LongestPathCoverage): string | null {
  if (coverage.total === 0) return null;
  const verified = coverage.yes + coverage.no;
  if (verified === coverage.total) return null;
  if (verified === 0) {
    return 'Longest Path data is unavailable for this schedule version — "Both" currently shows known Critical Path activities only.';
  }
  const unverified = coverage.total - verified;
  return `Longest Path coverage is incomplete (${unverified} of ${coverage.total} activities unverified) — "Both" may be missing Longest Path activities among them.`;
}
