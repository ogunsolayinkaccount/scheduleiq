from datetime import date, timedelta
from collections import defaultdict
from typing import Optional, List, Dict, Any


def _pct(v: int, t: int) -> int:
    return round((v / t) * 100) if t > 0 else 0


def _fmt_short(d: date) -> str:
    return d.strftime('%b %y') if d else '—'


def _end_of_month(m: date) -> date:
    if m.month == 12:
        return date(m.year + 1, 1, 1) - timedelta(days=1)
    return date(m.year, m.month + 1, 1) - timedelta(days=1)


def _next_month(m: date) -> date:
    if m.month == 12:
        return date(m.year + 1, 1, 1)
    return date(m.year, m.month + 1, 1)


# ── Health score ──────────────────────────────────────────────────────────────

def calc_health(p: dict) -> dict:
    score = 100
    if p['overduePct'] > 10:
        score -= 25
    elif p['overduePct'] > 0:
        score -= 10
    if p['criticalPct'] > 30:
        score -= 20
    elif p['criticalPct'] > 15:
        score -= 10
    if p['negFloat'] > 0:
        score -= 15

    if score >= 80:
        return {'label': 'On Track', 'color': '#00e5a0', 'score': score}
    elif score >= 60:
        return {'label': 'Monitor', 'color': '#ffb547', 'score': score}
    return {'label': 'At Risk', 'color': '#ff5757', 'score': score}


# ── Main metrics ──────────────────────────────────────────────────────────────

def compute_metrics(acts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    today = date.today()
    total = len(acts)
    if not total:
        return None

    # ── Counts ────────────────────────────────────────────────────────────────
    not_started = sum(
        1 for a in acts
        if not a.get('start') or a.get('status') == 'TK_NotStart'
    )
    in_progress = sum(
        1 for a in acts
        if a.get('start') and (a.get('pctComplete') or 0) < 100
    )
    completed = sum(1 for a in acts if (a.get('pctComplete') or 0) >= 100)
    critical = sum(1 for a in acts if a.get('isCritical') and not a.get('isMilestone'))
    milestones = sum(1 for a in acts if a.get('isMilestone'))
    near_critical = sum(
        1 for a in acts
        if 0 < (a.get('totalFloat') or 0) <= 5 and not a.get('isMilestone')
    )

    def is_overdue(a) -> bool:
        f = a.get('bFinish') or a.get('finish')
        return (
            isinstance(f, date) and f < today
            and (a.get('pctComplete') or 0) < 100
            and not a.get('isMilestone')
        )

    overdue = sum(1 for a in acts if is_overdue(a))

    # ── Durations ─────────────────────────────────────────────────────────────
    total_dur = sum(a.get('dur') or 0 for a in acts)
    earned_dur = sum(
        (a.get('dur') or 0) * ((a.get('pctComplete') or 0) / 100)
        for a in acts
    )
    sched_pct = (earned_dur / total_dur * 100) if total_dur > 0 else 0.0

    # ── Float buckets ─────────────────────────────────────────────────────────
    fb = {'negative': 0, 'zero': 0, 'low': 0, 'medium': 0, 'high': 0}
    for a in acts:
        f = a.get('totalFloat') or 0
        if f < 0:
            fb['negative'] += 1
        elif f == 0:
            fb['zero'] += 1
        elif f <= 5:
            fb['low'] += 1
        elif f <= 15:
            fb['medium'] += 1
        else:
            fb['high'] += 1

    # ── S-Curve ───────────────────────────────────────────────────────────────
    s_curve = []
    valid_dates = [
        a.get('bStart') or a.get('bFinish')
        for a in acts
        if isinstance(a.get('bStart') or a.get('bFinish'), date)
    ]
    finished = [a for a in acts if isinstance(a.get('bFinish'), date)]

    if valid_dates and finished:
        min_d = min(valid_dates)
        max_d = max(a['bFinish'] for a in finished)
        cur = date(min_d.year, min_d.month, 1)
        end_m = date(max_d.year, max_d.month, 1)

        while cur <= end_m:
            m_end = _end_of_month(cur)
            planned = sum(
                1 for a in acts
                if isinstance(a.get('bFinish'), date) and a['bFinish'] <= m_end
            )
            actual = sum(
                1 for a in acts
                if (
                    isinstance((a.get('finish') or a.get('bFinish')), date) and
                    (a.get('finish') or a.get('bFinish')) <= m_end and
                    (a.get('pctComplete') or 0) >= 100
                )
            )
            s_curve.append({
                'month': _fmt_short(cur),
                'planned': planned,
                'actual': actual,
                'pctPlanned': _pct(planned, total),
                'pctActual': _pct(actual, total),
            })
            cur = _next_month(cur)

    # ── Monthly Trend ─────────────────────────────────────────────────────────
    m_map: Dict[str, dict] = {}
    for a in acts:
        d = a.get('bFinish') or a.get('finish')
        if not isinstance(d, date):
            continue
        k = _fmt_short(d)
        if k not in m_map:
            m_map[k] = {'month': k, '_date': d, 'notStarted': 0, 'inProgress': 0, 'complete': 0}
        if (a.get('pctComplete') or 0) >= 100:
            m_map[k]['complete'] += 1
        elif a.get('start'):
            m_map[k]['inProgress'] += 1
        else:
            m_map[k]['notStarted'] += 1

    monthly_trend = [
        {k: v for k, v in item.items() if k != '_date'}
        for item in sorted(m_map.values(), key=lambda x: x['_date'])
    ]

    # ── Per-project metrics ───────────────────────────────────────────────────
    pj_map: Dict[str, dict] = {}
    for a in acts:
        pid = a.get('projectId') or 'Unknown'
        if pid not in pj_map:
            pj_map[pid] = {
                'id': pid,
                'name': a.get('projectName') or pid,
                'file': a.get('sourceFile') or '',
                'total': 0, 'critical': 0, 'complete': 0, 'overdue': 0,
                'notStarted': 0, 'inProgress': 0, 'milestones': 0,
                'nearCritical': 0, 'totalDur': 0.0, 'earnedDur': 0.0, 'negFloat': 0,
            }
        p = pj_map[pid]
        p['total'] += 1
        p['totalDur'] += a.get('dur') or 0
        p['earnedDur'] += (a.get('dur') or 0) * ((a.get('pctComplete') or 0) / 100)
        if a.get('isCritical') and not a.get('isMilestone'):
            p['critical'] += 1
        if a.get('isMilestone'):
            p['milestones'] += 1
        if (a.get('pctComplete') or 0) >= 100:
            p['complete'] += 1
        elif a.get('start'):
            p['inProgress'] += 1
        else:
            p['notStarted'] += 1
        if 0 < (a.get('totalFloat') or 0) <= 5 and not a.get('isMilestone'):
            p['nearCritical'] += 1
        f_d = a.get('bFinish') or a.get('finish')
        if isinstance(f_d, date) and f_d < today and (a.get('pctComplete') or 0) < 100 and not a.get('isMilestone'):
            p['overdue'] += 1
        if (a.get('totalFloat') or 0) < 0:
            p['negFloat'] += 1

    projects = []
    for p in pj_map.values():
        pct_done = round((p['earnedDur'] / p['totalDur']) * 100) if p['totalDur'] > 0 else 0
        crit_pct = _pct(p['critical'], p['total'])
        over_pct = _pct(p['overdue'], p['total'])
        bei = (
            p['complete'] / (p['complete'] + p['overdue'])
            if p['complete'] > 0 else (0.0 if p['overdue'] > 0 else 1.0)
        )
        p_out = {**p, 'pctComplete': pct_done, 'criticalPct': crit_pct, 'overduePct': over_pct, 'BEI': bei}
        p_out['health'] = calc_health(p_out)
        projects.append(p_out)

    # ── WBS distribution ──────────────────────────────────────────────────────
    wbs_count: Dict[str, int] = defaultdict(int)
    for a in acts:
        wbs_count[a.get('wbs') or 'Unassigned'] += 1
    wbs_dist = [
        {'name': n, 'count': c}
        for n, c in sorted(wbs_count.items(), key=lambda x: -x[1])[:12]
    ]

    # ── Portfolio BEI ─────────────────────────────────────────────────────────
    bei_total = (
        completed / (completed + overdue)
        if completed > 0 else (0.0 if overdue > 0 else 1.0)
    )

    neg_float_acts = sorted(
        [a for a in acts if (a.get('totalFloat') or 0) < 0],
        key=lambda a: a.get('totalFloat') or 0,
    )[:30]

    # Color constants (kept here so Python owns the palette too)
    RED, AMBER, ORANGE, GREEN, ACCENT, MUTED2, PURPLE = (
        '#ff5757', '#ffb547', '#fb923c', '#00e5a0', '#00c8f0', '#60778f', '#a78bfa',
    )

    return {
        'total': total,
        'notStarted': not_started,
        'inProgress': in_progress,
        'completed': completed,
        'critical': critical,
        'nearCritical': near_critical,
        'milestones': milestones,
        'overdue': overdue,
        'schedPct': sched_pct,
        'totalDur': total_dur,
        'earnedDur': earned_dur,
        'fb': fb,
        'BEI': bei_total,
        'floatHist': [
            {'range': '< 0',  'count': fb['negative'], 'fill': RED},
            {'range': '= 0',  'count': fb['zero'],     'fill': AMBER},
            {'range': '1–5',  'count': fb['low'],      'fill': ORANGE},
            {'range': '6–15', 'count': fb['medium'],   'fill': GREEN},
            {'range': '> 15', 'count': fb['high'],     'fill': ACCENT},
        ],
        'durHist': [
            {'range': '0–5d',   'count': sum(1 for a in acts if (a.get('dur') or 0) <= 5)},
            {'range': '6–10d',  'count': sum(1 for a in acts if 5  < (a.get('dur') or 0) <= 10)},
            {'range': '11–20d', 'count': sum(1 for a in acts if 10 < (a.get('dur') or 0) <= 20)},
            {'range': '21–40d', 'count': sum(1 for a in acts if 20 < (a.get('dur') or 0) <= 40)},
            {'range': '> 40d',  'count': sum(1 for a in acts if      (a.get('dur') or 0) > 40)},
        ],
        'pctHist': [
            {'range': '0%',    'count': sum(1 for a in acts if (a.get('pctComplete') or 0) == 0)},
            {'range': '1–25%', 'count': sum(1 for a in acts if 0  < (a.get('pctComplete') or 0) <= 25)},
            {'range': '26–50%','count': sum(1 for a in acts if 25 < (a.get('pctComplete') or 0) <= 50)},
            {'range': '51–75%','count': sum(1 for a in acts if 50 < (a.get('pctComplete') or 0) <= 75)},
            {'range': '76–99%','count': sum(1 for a in acts if 75 < (a.get('pctComplete') or 0) < 100)},
            {'range': '100%',  'count': sum(1 for a in acts if      (a.get('pctComplete') or 0) >= 100)},
        ],
        'statusPie': [x for x in [
            {'name': 'Not Started', 'value': not_started, 'fill': MUTED2},
            {'name': 'In Progress', 'value': in_progress, 'fill': ACCENT},
            {'name': 'Complete',    'value': completed,   'fill': GREEN},
        ] if x['value'] > 0],
        'criticalPie': [x for x in [
            {'name': 'Critical',      'value': critical,                                            'fill': RED},
            {'name': 'Near-Critical', 'value': near_critical,                                       'fill': AMBER},
            {'name': 'Non-Critical',  'value': max(0, total - critical - near_critical - milestones),'fill': GREEN},
            {'name': 'Milestones',    'value': milestones,                                          'fill': PURPLE},
        ] if x['value'] > 0],
        'sCurve': s_curve,
        'monthlyTrend': monthly_trend,
        'projects': projects,
        'wbsDist': wbs_dist,
        'negFloatActs': neg_float_acts,
    }
