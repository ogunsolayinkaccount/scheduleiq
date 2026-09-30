"""
Driving Chain / Path Trace tests — multi-hop trace (item 8), blocking
predecessor analysis (item 9), and downstream exposure (item 10/13).
"""
from django.test import SimpleTestCase

from scheduler.driving_chain import find_blocking_predecessors, find_downstream_exposure, trace_driving_path
from .fixtures import make_activity


def _serial_chain(n, total_float_step=0.0):
    """A0 -> A1 -> ... -> A(n-1), each with descending float so the driving
    trace has an unambiguous single predecessor/successor at every hop."""
    acts = []
    for i in range(n):
        acts.append(make_activity(
            f'A{i}', dur=5.0, total_float=10.0 - i * total_float_step,
            predecessors=[{'actId': f'A{i-1}', 'relType': 'FS', 'lagDays': 0}] if i > 0 else [],
            successors=[{'actId': f'A{i+1}', 'relType': 'FS', 'lagDays': 0}] if i < n - 1 else [],
        ))
    return acts


class TraceDrivingPathTests(SimpleTestCase):
    def test_linear_chain_traced_both_directions(self):
        acts = _serial_chain(5)
        result = trace_driving_path(acts, 'A2')
        self.assertTrue(result['available'])
        self.assertEqual([e['activityId'] for e in result['upstreamChain']], ['A0', 'A1', 'A2'])
        self.assertEqual([e['activityId'] for e in result['downstreamChain']], ['A2', 'A3', 'A4'])

    def test_relationship_metadata_attached_correctly(self):
        acts = _serial_chain(3)
        result = trace_driving_path(acts, 'A1')
        # Upstream: [A0, A1] — A1's relationshipFromPrevious is the rel connecting A0->A1.
        self.assertIsNone(result['upstreamChain'][0]['relationshipFromPrevious'])
        self.assertEqual(result['upstreamChain'][1]['relationshipFromPrevious']['relType'], 'FS')
        # Downstream: [A1, A2] — A2's relationshipFromPrevious is the rel connecting A1->A2.
        self.assertIsNone(result['downstreamChain'][0]['relationshipFromPrevious'])
        self.assertEqual(result['downstreamChain'][1]['relationshipFromPrevious']['relType'], 'FS')

    def test_picks_lowest_float_neighbor_when_multiple_exist(self):
        # A has two successors: B (float 10) and C (float 2). Driving pick must be C.
        a = make_activity('A', successors=[
            {'actId': 'B', 'relType': 'FS', 'lagDays': 0}, {'actId': 'C', 'relType': 'FS', 'lagDays': 0},
        ])
        b = make_activity('B', total_float=10.0, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        c = make_activity('C', total_float=2.0, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        result = trace_driving_path([a, b, c], 'A')
        self.assertEqual(result['downstreamChain'][1]['activityId'], 'C')

    def test_unknown_activity_returns_unavailable_not_error(self):
        result = trace_driving_path(_serial_chain(3), 'DOES_NOT_EXIST')
        self.assertFalse(result['available'])
        self.assertIn('not found', result['reason'])

    def test_raw_task_id_relationships_resolved_correctly(self):
        a = make_activity('A', id='T1', successors=[{'actId': 'T2', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', id='T2', predecessors=[{'actId': 'T1', 'relType': 'FS', 'lagDays': 0}])
        result = trace_driving_path([a, b], 'A')
        self.assertEqual([e['activityId'] for e in result['downstreamChain']], ['A', 'B'])

    def test_cycle_guard_terminates(self):
        # Defensive: a malformed network where A's successor loops back to A.
        a = make_activity('A', successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', totalFloat=5.0, successors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        result = trace_driving_path([a, b], 'A', max_hops=10)
        self.assertTrue(result['available'])  # must terminate, not hang/crash
        self.assertLessEqual(len(result['downstreamChain']), 3)

    def test_max_hops_truncation_flagged(self):
        acts = _serial_chain(20)
        result = trace_driving_path(acts, 'A0', max_hops=5)
        self.assertTrue(result['downstreamTruncated'])
        self.assertLessEqual(len(result['downstreamChain']), 6)


class BlockingPredecessorTests(SimpleTestCase):
    def test_identifies_unfinished_negative_float_lagged_predecessor(self):
        pred = make_activity('P', total_float=-3.0, pct_complete=40.0, predecessors=[], successors=[{'actId': 'S', 'relType': 'FS', 'lagDays': 5}])
        succ = make_activity('S', predecessors=[{'actId': 'P', 'relType': 'FS', 'lagDays': 5}])
        result = find_blocking_predecessors([pred, succ], 'S')
        self.assertTrue(result['available'])
        entry = result['blockingPredecessors'][0]
        self.assertIn('UNFINISHED', entry['conditions'])
        self.assertIn('NEGATIVE_FLOAT', entry['conditions'])
        self.assertIn('LAG', entry['conditions'])

    def test_completed_predecessor_no_unfinished_flag(self):
        pred = make_activity('P', pct_complete=100.0, total_float=5.0, successors=[{'actId': 'S', 'relType': 'FS', 'lagDays': 0}])
        succ = make_activity('S', predecessors=[{'actId': 'P', 'relType': 'FS', 'lagDays': 0}])
        result = find_blocking_predecessors([pred, succ], 'S')
        self.assertNotIn('UNFINISHED', result['blockingPredecessors'][0]['conditions'])

    def test_never_labeled_root_cause(self):
        pred = make_activity('P', successors=[{'actId': 'S', 'relType': 'FS', 'lagDays': 0}])
        succ = make_activity('S', predecessors=[{'actId': 'P', 'relType': 'FS', 'lagDays': 0}])
        result = find_blocking_predecessors([pred, succ], 'S')
        # The methodology note explicitly DISCLAIMS root-cause status — it
        # mentions "root cause" only to deny it, never asserts one.
        self.assertIn('not an inferred root cause', result['methodologyNote'].lower())


class DownstreamExposureTests(SimpleTestCase):
    def test_finds_reachable_milestone(self):
        a = make_activity('A', successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[{'actId': 'M', 'relType': 'FS', 'lagDays': 0}])
        m = make_activity('M', is_milestone=True, predecessors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        result = find_downstream_exposure([a, b, m], 'A')
        self.assertTrue(result['available'])
        self.assertEqual(len(result['exposedMilestones']), 1)
        self.assertEqual(result['exposedMilestones'][0]['activityId'], 'M')

    def test_forecast_impacting_flag_driven_by_external_classification(self):
        a = make_activity('A', successors=[{'actId': 'M1', 'relType': 'FS', 'lagDays': 0}, {'actId': 'M2', 'relType': 'FS', 'lagDays': 0}])
        m1 = make_activity('M1', is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        m2 = make_activity('M2', is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        result = find_downstream_exposure([a, m1, m2], 'A', at_risk_milestone_ids={'M1'})
        flags = {e['activityId']: e['forecastImpacting'] for e in result['exposedMilestones']}
        self.assertTrue(flags['M1'])
        self.assertFalse(flags['M2'])

    def test_finds_all_milestones_not_just_driving_path(self):
        # A has two parallel successors, B (low float, "driving") and C
        # (high float, non-driving) — both lead to their own milestones.
        # Downstream exposure must find BOTH, unlike the single-path trace.
        a = make_activity('A', successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}, {'actId': 'C', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', total_float=0.0, successors=[{'actId': 'MB', 'relType': 'FS', 'lagDays': 0}])
        c = make_activity('C', total_float=20.0, successors=[{'actId': 'MC', 'relType': 'FS', 'lagDays': 0}])
        mb = make_activity('MB', is_milestone=True, predecessors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        mc = make_activity('MC', is_milestone=True, predecessors=[{'actId': 'C', 'relType': 'FS', 'lagDays': 0}])
        result = find_downstream_exposure([a, b, c, mb, mc], 'A')
        ids = {e['activityId'] for e in result['exposedMilestones']}
        self.assertEqual(ids, {'MB', 'MC'})

    def test_no_downstream_milestone_returns_empty_not_error(self):
        a = make_activity('A', successors=[])
        result = find_downstream_exposure([a], 'A')
        self.assertTrue(result['available'])
        self.assertEqual(result['exposedMilestones'], [])

    def test_large_network_bounded_and_terminates(self):
        acts = _serial_chain(2000)
        result = find_downstream_exposure(acts, 'A0', max_nodes=100)
        self.assertTrue(result['available'])
        self.assertTrue(result['truncated'])

    def test_full_traversal_of_large_reachable_set_is_near_linear(self):
        # Regression guard for an O(n^2) BFS (queue.pop(0) on a plain list,
        # each pop O(n)) that made a full, non-truncated traversal of a
        # large reachable set pathologically slow — fixed by using
        # collections.deque with O(1) popleft(). A full traversal (well
        # under max_nodes, so it never truncates) of a 6000-node chain must
        # complete in well under a second; the O(n^2) version took tens of
        # seconds or more at this size.
        import time
        acts = _serial_chain(6000)
        t0 = time.perf_counter()
        result = find_downstream_exposure(acts, 'A0', max_nodes=10000)
        elapsed = time.perf_counter() - t0
        self.assertTrue(result['available'])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['reachableActivityCount'], 6000)
        self.assertLess(elapsed, 2.0, f'downstream exposure BFS took {elapsed:.2f}s on 6000 nodes — likely O(n^2) regression')
