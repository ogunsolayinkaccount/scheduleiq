"""
Project Issue Tracking — unit tests for the pure issue_register.py engine.
No DB, no Django test client — exercises the exposure/overdue/priority
functions directly against plain activity dicts, matching the pattern used
for driving_chain.py / risk_register.py's own pure-function tests.
"""
import unittest
from datetime import date

from scheduler.issue_register import (
    compute_issue_schedule_exposure, is_issue_overdue, priority_rank,
)


def _act(code, tf=5.0, critical=False, driving=False, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': f'Activity {code}', 'wbs': 'Area A', 'area': 'Area A',
        'discipline': 'ELE', 'status': 'TK_NotStart', 'pctComplete': 0.0,
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'totalFloat': tf,
        'isCritical': critical, 'onLongestPath': driving, 'isMilestone': milestone,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class NoLinkedActivitiesTests(unittest.TestCase):
    def test_no_linked_activities_reports_unavailable_but_not_an_error(self):
        result = compute_issue_schedule_exposure([], [_act('A1')], date(2026, 1, 1))
        self.assertFalse(result['hasLinkedActivities'])
        self.assertEqual(result['activities'], [])


class ActivityResolutionTests(unittest.TestCase):
    def test_linked_activity_found_reports_its_live_fields(self):
        result = compute_issue_schedule_exposure(['A1'], [_act('A1', tf=3.0)], date(2026, 1, 1))
        a = result['activities'][0]
        self.assertTrue(a['available'])
        self.assertEqual(a['activityId'], 'A1')
        self.assertEqual(a['totalFloat'], 3.0)
        self.assertEqual(a['floatCondition'], 'NEAR_CRITICAL')

    def test_linked_activity_missing_from_current_version_is_reported_honestly(self):
        result = compute_issue_schedule_exposure(['GONE'], [_act('A1')], date(2026, 1, 1))
        a = result['activities'][0]
        self.assertFalse(a['available'])
        self.assertEqual(a['reason'], 'Activity GONE not found in selected schedule version.')
        self.assertTrue(result['anyNotFound'])

    def test_multiple_linked_activities_are_all_reported(self):
        acts = [_act('A1', tf=-2.0), _act('A2', tf=50.0), _act('A3', tf=0.0)]
        result = compute_issue_schedule_exposure(['A1', 'A2', 'A3', 'MISSING'], acts, date(2026, 1, 1))
        self.assertEqual(len(result['activities']), 4)
        by_id = {a['activityId']: a for a in result['activities']}
        self.assertEqual(by_id['A1']['floatCondition'], 'NEGATIVE')
        self.assertEqual(by_id['A2']['floatCondition'], 'NORMAL')
        self.assertEqual(by_id['A3']['floatCondition'], 'ZERO')
        self.assertFalse(by_id['MISSING']['available'])


class FloatConditionClassificationTests(unittest.TestCase):
    def test_negative_zero_near_critical_normal_and_unavailable(self):
        acts = [
            _act('NEG', tf=-5.0), _act('ZERO', tf=0.0), _act('NEARC', tf=10.0),
            _act('NORM', tf=25.0), _act('NOFLOAT'),
        ]
        acts[-1]['totalFloat'] = None
        result = compute_issue_schedule_exposure(['NEG', 'ZERO', 'NEARC', 'NORM', 'NOFLOAT'], acts, date(2026, 1, 1))
        by_id = {a['activityId']: a for a in result['activities']}
        self.assertEqual(by_id['NEG']['floatCondition'], 'NEGATIVE')
        self.assertEqual(by_id['ZERO']['floatCondition'], 'ZERO')
        self.assertEqual(by_id['NEARC']['floatCondition'], 'NEAR_CRITICAL')  # exactly the 10.0 threshold
        self.assertEqual(by_id['NORM']['floatCondition'], 'NORMAL')
        self.assertEqual(by_id['NOFLOAT']['floatCondition'], 'UNAVAILABLE')


class NegativeFloatAndDrivingPathExposureTests(unittest.TestCase):
    def test_any_negative_float_rolls_up_correctly(self):
        acts = [_act('A1', tf=5.0), _act('A2', tf=-1.0)]
        result = compute_issue_schedule_exposure(['A1', 'A2'], acts, date(2026, 1, 1))
        self.assertTrue(result['anyNegativeFloat'])

    def test_no_negative_float_among_linked_activities(self):
        acts = [_act('A1', tf=5.0), _act('A2', tf=20.0)]
        result = compute_issue_schedule_exposure(['A1', 'A2'], acts, date(2026, 1, 1))
        self.assertFalse(result['anyNegativeFloat'])

    def test_driving_path_membership_rolls_up(self):
        acts = [_act('A1', driving=True)]
        result = compute_issue_schedule_exposure(['A1'], acts, date(2026, 1, 1))
        self.assertTrue(result['anyOnDrivingPath'])
        self.assertTrue(result['activities'][0]['onDrivingPath'])

    def test_a_not_found_activity_never_counts_toward_negative_float_or_driving_path(self):
        result = compute_issue_schedule_exposure(['GHOST'], [_act('A1', tf=-5.0, driving=True)], date(2026, 1, 1))
        self.assertFalse(result['anyNegativeFloat'])
        self.assertFalse(result['anyOnDrivingPath'])


class ContractualMilestoneExposureTests(unittest.TestCase):
    def test_linked_activity_reaching_a_contractual_milestone_is_flagged(self):
        # A1 -> MS1 (a milestone activity), MS1 is in the contractual set.
        acts = [
            _act('A1', tf=5.0, successors=[{'actId': 'MS1', 'relType': 'FS', 'lagDays': 0}]),
            _act('MS1', milestone=True, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = compute_issue_schedule_exposure(['A1'], acts, date(2026, 1, 1), contractual_milestone_activity_ids={'MS1'})
        a = result['activities'][0]
        self.assertIn('MS1', a['reachesContractualMilestoneActivityIds'])
        self.assertTrue(result['anyReachesContractualMilestone'])

    def test_reachable_milestone_not_in_the_contractual_set_is_not_flagged_as_contractual(self):
        acts = [
            _act('A1', tf=5.0, successors=[{'actId': 'MS1', 'relType': 'FS', 'lagDays': 0}]),
            _act('MS1', milestone=True, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        # MS1 reachable, but NOT in contractual_milestone_activity_ids (e.g. it's an informational milestone).
        result = compute_issue_schedule_exposure(['A1'], acts, date(2026, 1, 1), contractual_milestone_activity_ids=set())
        a = result['activities'][0]
        self.assertEqual(a['reachableMilestoneCount'], 1)  # still reachable...
        self.assertEqual(a['reachesContractualMilestoneActivityIds'], [])  # ...but not flagged contractual
        self.assertFalse(result['anyReachesContractualMilestone'])


class OverdueLogicTests(unittest.TestCase):
    def test_open_issue_with_past_target_date_is_overdue(self):
        self.assertTrue(is_issue_overdue('OPEN', date(2026, 1, 1), today=date(2026, 2, 1)))

    def test_open_issue_with_future_target_date_is_not_overdue(self):
        self.assertFalse(is_issue_overdue('OPEN', date(2026, 3, 1), today=date(2026, 2, 1)))

    def test_issue_with_no_target_date_is_never_overdue(self):
        self.assertFalse(is_issue_overdue('OPEN', None, today=date(2026, 2, 1)))

    def test_resolved_issue_is_never_overdue_regardless_of_target_date(self):
        self.assertFalse(is_issue_overdue('RESOLVED', date(2026, 1, 1), today=date(2026, 2, 1)))

    def test_closed_issue_is_never_overdue_regardless_of_target_date(self):
        self.assertFalse(is_issue_overdue('CLOSED', date(2026, 1, 1), today=date(2026, 2, 1)))

    def test_monitoring_and_mitigating_are_still_active_for_overdue_purposes(self):
        self.assertTrue(is_issue_overdue('MONITORING', date(2026, 1, 1), today=date(2026, 2, 1)))
        self.assertTrue(is_issue_overdue('MITIGATING', date(2026, 1, 1), today=date(2026, 2, 1)))


class PriorityRankTests(unittest.TestCase):
    def test_overdue_outranks_non_overdue_regardless_of_severity(self):
        overdue_low = {'severity': 'LOW', 'overdue': True, 'exposure': {}, 'createdAt': ''}
        not_overdue_critical = {'severity': 'CRITICAL', 'overdue': False, 'exposure': {}, 'createdAt': ''}
        self.assertGreater(priority_rank(overdue_low), priority_rank(not_overdue_critical))

    def test_contractual_milestone_exposure_outranks_plain_severity_when_overdue_is_equal(self):
        with_exposure = {'severity': 'LOW', 'overdue': False, 'exposure': {'anyReachesContractualMilestone': True}, 'createdAt': ''}
        without_exposure = {'severity': 'CRITICAL', 'overdue': False, 'exposure': {}, 'createdAt': ''}
        self.assertGreater(priority_rank(with_exposure), priority_rank(without_exposure))

    def test_severity_breaks_ties_when_overdue_and_exposure_are_equal(self):
        critical = {'severity': 'CRITICAL', 'overdue': False, 'exposure': {}, 'createdAt': ''}
        low = {'severity': 'LOW', 'overdue': False, 'exposure': {}, 'createdAt': ''}
        self.assertGreater(priority_rank(critical), priority_rank(low))
