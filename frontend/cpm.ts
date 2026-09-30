// Client-side CPM (critical path method) engine.
// Only runs against projects the user has actually edited relationship logic on —
// untouched projects keep the early/late dates and float computed by the source
// schedule file (P6/MSP) exactly as-is (see scheduler/parsers.py).

export type RelType = 'FS' | 'SS' | 'FF' | 'SF';

export interface LogicEdit {
  relType?: RelType;
  lagDays?: number;
  deleted?: boolean;
}

// Key = `${predId}::${succId}`
export type LogicEditMap = Record<string, LogicEdit>;

const MS_DAY = 86400000;

export function linkKey(predId: string, succId: string): string {
  return `${predId}::${succId}`;
}

function actKey(a: any): string {
  return a.id || a.code;
}

function cloneRelArray(list: any[] | undefined): any[] {
  return (list || []).map(r => ({ ...r }));
}

// Rebuilds predecessors/successors on every activity from the raw parsed data plus
// pending edits. The parser denormalizes each relationship onto both endpoints
// (scheduler/parsers.py:340-343), so edits/deletes/additions are applied on both sides
// to keep them in sync. Must always be called against the raw (unedited) activities —
// it is not cumulative.
export function applyLogicEdits(activities: any[], logicEdits: LogicEditMap): any[] {
  if (!logicEdits || !Object.keys(logicEdits).length) return activities;

  const byId: Record<string, any> = {};
  activities.forEach(a => { byId[actKey(a)] = a; });

  // Edits whose predId/succId pair isn't present in the original successors array
  // are brand-new links added via the UI.
  const added: { predId: string; succId: string; relType: RelType; lagDays: number }[] = [];
  for (const key in logicEdits) {
    const edit = logicEdits[key];
    if (edit.deleted) continue;
    const sep = key.indexOf('::');
    const predId = key.slice(0, sep), succId = key.slice(sep + 2);
    const predAct = byId[predId];
    const alreadyLinked = predAct && (predAct.successors || []).some((r: any) => r.actId === succId);
    if (!alreadyLinked) added.push({ predId, succId, relType: edit.relType || 'FS', lagDays: edit.lagDays ?? 0 });
  }

  return activities.map(a => {
    const id = actKey(a);
    let touched = false;

    let preds = cloneRelArray(a.predecessors).map(r => {
      const e = logicEdits[linkKey(r.actId, id)];
      if (!e) return r;
      touched = true;
      return e.deleted ? null : { ...r, relType: e.relType ?? r.relType, lagDays: e.lagDays ?? r.lagDays };
    }).filter(Boolean);

    let succs = cloneRelArray(a.successors).map(r => {
      const e = logicEdits[linkKey(id, r.actId)];
      if (!e) return r;
      touched = true;
      return e.deleted ? null : { ...r, relType: e.relType ?? r.relType, lagDays: e.lagDays ?? r.lagDays };
    }).filter(Boolean);

    for (const add of added) {
      if (add.succId === id) { preds.push({ actId: add.predId, relType: add.relType, lagDays: add.lagDays }); touched = true; }
      if (add.predId === id) { succs.push({ actId: add.succId, relType: add.relType, lagDays: add.lagDays }); touched = true; }
    }

    if (!touched) return a;
    return { ...a, predecessors: preds, successors: succs, predCount: preds.length, succCount: succs.length, isLogicEdited: true };
  });
}

// Returns true if adding predId -> succId would close a cycle, i.e. predId is
// already reachable by walking forward from succId. Only additions can create a
// cycle (edits/deletes to existing links can't change the topology).
export function wouldCreateCycle(activities: any[], predId: string, succId: string): boolean {
  if (predId === succId) return true;
  const byId: Record<string, any> = {};
  activities.forEach(a => { byId[actKey(a)] = a; });

  const seen = new Set<string>([succId]);
  const stack = [succId];
  while (stack.length) {
    const cur = stack.pop()!;
    if (cur === predId) return true;
    const a = byId[cur];
    if (!a) continue;
    for (const s of (a.successors || [])) {
      if (!seen.has(s.actId)) { seen.add(s.actId); stack.push(s.actId); }
    }
  }
  return false;
}

function parseISODate(s: string | null | undefined): Date | null {
  if (!s) return null;
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3]);
}

function durationOf(a: any): number {
  const isComplete = (a.pctComplete || 0) >= 100 || a.status === 'TK_Complete';
  if (isComplete) return 0;
  return a.remainDur ?? a.dur ?? 0;
}

function anchorStart(a: any, dd: Date): number {
  if (a.start instanceof Date) return a.start.getTime();
  return dd.getTime();
}

// Forward + backward pass over one project's network. `group` must already have
// predecessors/successors rebuilt by applyLogicEdits.
function computeProjectCPM(group: any[], dd: Date | null): any[] {
  if (!dd) {
    // A genuine CPM recompute must anchor not-yet-started activities to the
    // schedule's own effective Data Date — never today's date. Without one,
    // skip the recompute rather than silently anchoring to today.
    return group.map(a => ({ ...a, cpmError: 'Data Date unavailable — recompute skipped' }));
  }
  const byId: Record<string, any> = {};
  group.forEach(a => { byId[actKey(a)] = a; });

  const succOf: Record<string, { actId: string; relType: RelType; lagDays: number }[]> = {};
  const predOf: Record<string, { actId: string; relType: RelType; lagDays: number }[]> = {};
  group.forEach(a => {
    const id = actKey(a);
    succOf[id] = (a.successors || []).filter((r: any) => byId[r.actId]);
    predOf[id] = (a.predecessors || []).filter((r: any) => byId[r.actId]);
  });

  // Kahn's algorithm topological sort
  const indeg: Record<string, number> = {};
  group.forEach(a => { indeg[actKey(a)] = predOf[actKey(a)].length; });
  const q: string[] = group.filter(a => indeg[actKey(a)] === 0).map(actKey);
  const order: string[] = [];
  while (q.length) {
    const id = q.shift()!;
    order.push(id);
    for (const s of succOf[id]) {
      indeg[s.actId]--;
      if (indeg[s.actId] === 0) q.push(s.actId);
    }
  }

  if (order.length !== group.length) {
    // Circular logic — P6 would refuse to schedule this too. Leave dates untouched.
    return group.map(a => ({ ...a, cpmError: 'circular logic detected — recompute skipped' }));
  }

  const ES: Record<string, number> = {};
  const EF: Record<string, number> = {};

  for (const id of order) {
    const a = byId[id];
    let es = anchorStart(a, dd);
    for (const p of predOf[id]) {
      const lagMs = (p.lagDays || 0) * MS_DAY;
      const pES = ES[p.actId], pEF = EF[p.actId];
      const dA = durationOf(a) * MS_DAY;
      let constraint: number;
      if (p.relType === 'SS') constraint = pES + lagMs;
      else if (p.relType === 'FF') constraint = pEF + lagMs - dA;
      else if (p.relType === 'SF') constraint = pES + lagMs - dA;
      else constraint = pEF + lagMs; // FS
      if (constraint > es) es = constraint;
    }
    const notStarted = !(a.start instanceof Date);
    if (notStarted && dd && es < dd.getTime()) es = dd.getTime();
    ES[id] = es;
    EF[id] = es + durationOf(a) * MS_DAY;
  }

  let projectFinish = -Infinity;
  for (const id of order) if (EF[id] > projectFinish) projectFinish = EF[id];

  const LS: Record<string, number> = {};
  const LF: Record<string, number> = {};
  for (let i = order.length - 1; i >= 0; i--) {
    const id = order[i];
    const a = byId[id];
    const dA = durationOf(a) * MS_DAY;
    let lf = succOf[id].length === 0 ? projectFinish : Infinity;
    for (const s of succOf[id]) {
      const lagMs = (s.lagDays || 0) * MS_DAY;
      const sLS = LS[s.actId], sLF = LF[s.actId];
      let constraint: number;
      if (s.relType === 'SS') constraint = sLS - lagMs + dA;
      else if (s.relType === 'FF') constraint = sLF - lagMs;
      else if (s.relType === 'SF') constraint = sLF - lagMs + dA;
      else constraint = sLS - lagMs; // FS
      if (constraint < lf) lf = constraint;
    }
    if (lf === Infinity) lf = projectFinish;
    LF[id] = lf;
    LS[id] = lf - dA;
  }

  return group.map(a => {
    const id = actKey(a);
    const es = ES[id], ef = EF[id], ls = LS[id], lf = LF[id];
    const totalFloat = Math.round((ls - es) / MS_DAY);
    return {
      ...a,
      earlyStart: new Date(es),
      earlyFinish: new Date(ef),
      lateStart: new Date(ls),
      lateFinish: new Date(lf),
      totalFloat,
      isCritical: totalFloat <= 0,
      onLongestPath: totalFloat <= 0,
    };
  });
}

// Recomputes early/late dates, total float and critical-path status for every
// project that has at least one activity flagged `isLogicEdited` by
// applyLogicEdits(). Projects with no edits pass through untouched.
export function computeCPM(activities: any[], dataDateStr?: string | null): any[] {
  const touchedProjects = new Set(activities.filter(a => a.isLogicEdited).map(a => a.projectId));
  if (!touchedProjects.size) return activities;

  const dd = parseISODate(dataDateStr);
  const byProject: Record<string, any[]> = {};
  activities.forEach(a => {
    const pid = a.projectId || '__none__';
    (byProject[pid] || (byProject[pid] = [])).push(a);
  });

  const results: any[] = [];
  for (const pid in byProject) {
    const group = byProject[pid];
    results.push(...(touchedProjects.has(pid) ? computeProjectCPM(group, dd) : group));
  }
  return results;
}
