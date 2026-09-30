// Legacy Activities register filter codes -> Activity Analysis's own Filters
// model, so Portfolio's KPI drill-through and global search keep working the
// same way after the legacy Activities page was retired (Phase 1
// consolidation, item #3). Pulled out as a pure function so the mapping
// itself is unit-testable without any React/DOM harness, matching the
// projectScope.ts pattern already used in this repo.
export type LegacyFilterCode =
  | "ALL" | "COMPLETE" | "CRITICAL" | "OVERDUE" | "NOTSTART"
  | "INPROG" | "MILESTON" | "NEGFLOAT" | "NEARCRIT";

export interface ActivityAnalysisFilterPatch {
  search?: string;
  status?: string;
  criticality?: string;
  tf?: string;
  milestonesOnly?: boolean;
}

// Mirrors ActivityRegister's old `filt` predicates (App.tsx) one for one:
//   CRITICAL  -> x.isCritical && !x.isMilestone        -> criticality: CRITICAL (Activity Analysis's criticalActionable)
//   OVERDUE   -> bFinish < DD && pctComplete < 100      -> status: OVERDUE
//   NOTSTART  -> !start && pctComplete < 100            -> status: NOT_STARTED
//   INPROG    -> start && not complete                 -> status: IN_PROGRESS
//   NEGFLOAT  -> totalFloat < 0                         -> tf: NEGATIVE
//   COMPLETE  -> pctComplete>=100 || totalFloat==null   -> status: COMPLETE
//   MILESTON  -> isMilestone                            -> milestonesOnly: true
//   NEARCRIT  -> 1 <= totalFloat <= 5                   -> criticality: NEAR_CRITICAL
//   ALL       -> no filter
export function legacyFilterToActivityAnalysis(code: string): ActivityAnalysisFilterPatch {
  switch (code) {
    case "COMPLETE": return { status: "COMPLETE" };
    case "CRITICAL": return { criticality: "CRITICAL" };
    case "OVERDUE": return { status: "OVERDUE" };
    case "NOTSTART": return { status: "NOT_STARTED" };
    case "INPROG": return { status: "IN_PROGRESS" };
    case "MILESTON": return { milestonesOnly: true };
    case "NEGFLOAT": return { tf: "NEGATIVE" };
    case "NEARCRIT": return { criticality: "NEAR_CRITICAL" };
    case "ALL":
    default: return {};
  }
}

// GlobalSearch's onGoToActivity(activity) jump - land directly on that
// activity by its code/name text, the same way the old Activities register's
// initialSearch prop worked.
export function activityJumpFilter(codeOrName: string | undefined | null): ActivityAnalysisFilterPatch {
  return codeOrName ? { search: codeOrName } : {};
}
