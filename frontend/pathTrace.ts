// Shared relationship-trace utilities — used by both the Critical Path tab's
// Path Tracing panel (App.tsx) and the Gantt's activity detail drawer
// (GanttView.tsx), so the "driving neighbour" heuristic lives in exactly one
// place. This walks the imported P6/MSP relationship network using each
// activity's own total float — it is NOT a recalculated CPM (see cpm.ts /
// computeCPM for the one place ScheduleIQ actually recalculates dates).

export function pickDrivingRel(rels: any[], actMap: Record<string, any>) {
  let best: any = null, bestFloat = Infinity;
  for (const r of (rels || [])) {
    const act = actMap[r.actId];
    if (!act) continue;
    const f = (act.totalFloat === null || act.totalFloat === undefined) ? Infinity : act.totalFloat;
    if (f < bestFloat) { bestFloat = f; best = { rel: r, activity: act }; }
  }
  return best;
}

export function tracePath(startId: string, direction: "predecessors" | "successors", actMap: Record<string, any>, maxSteps = 40) {
  const start = actMap[startId];
  if (!start) return [] as { activity: any; relFromPrev: any | null }[];
  const walk: { activity: any; rel: any | null }[] = [{ activity: start, rel: null }];
  const visited = new Set([startId]);
  let current = start;
  for (let i = 0; i < maxSteps && current; i++) {
    const pick = pickDrivingRel(current[direction], actMap);
    if (!pick) break;
    const nextId = pick.activity.id || pick.activity.code;
    if (visited.has(nextId)) break;
    visited.add(nextId);
    walk.push({ activity: pick.activity, rel: pick.rel });
    current = pick.activity;
  }
  if (direction === "successors") {
    return walk.map(n => ({ activity: n.activity, relFromPrev: n.rel }));
  }
  const rev = [...walk].reverse();
  return rev.map((n, idx) => ({
    activity: n.activity,
    relFromPrev: idx === 0 ? null : walk[walk.length - idx].rel,
  }));
}
