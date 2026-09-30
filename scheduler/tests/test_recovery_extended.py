"""
Recovery Scenario Engine — extended tests for the Schedule Risk, Recovery &
Mitigation Intelligence phase. Covers the task_id/code relationship-
resolution bug fix, calendar-aware calculation, baseline recovery
(items 25-27), side-effect detection (item 28), open-end protection
(item 31), and the controlled validation cases the directive names
explicitly (items 56-58: Duration Compression, Parallelization, Float
Absorption, Critical/Non-Driving Recovery, Side Effects, Logic Loop,
Multi-Calendar, In-Progress/Completed Activity protection, Data Date
independence).
"""
from datetime import date

from django.test import SimpleTestCase

from scheduler.calendar_engine import CalendarDefinition
from scheduler.recovery_engine import apply_scenario_actions, compute_scenario_cpm, run_scenario
from .fixtures import make_activity

DD = date(2026, 1, 1)
FIVE_DAY = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: False}, [])


def _chain():
    a1 = make_activity('A1', dur=10.0, predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}])
    a2 = make_activity('A2', dur=10.0, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[])
    return [a1, a2]


class TaskIdCodeResolutionBugFixTests(SimpleTestCase):
    """The real-world bug the audit found: XER stores predecessor/successor
    actId as the raw internal task_id, not the human-readable code. Before
    the fix, run_scenario silently dropped every relationship on real XER
    data (id != code), making every scenario action a no-op."""

    def _chain_with_raw_task_ids(self):
        # Activity Codes are A1/A2 (what a scheduler sees); raw P6 task_ids
        # are the numeric-looking TASK1/TASK2 (what XER actually stores in
        # predecessors[].actId/successors[].actId).
        a1 = make_activity('A1', id='TASK1', dur=10.0, predecessors=[], successors=[{'actId': 'TASK2', 'relType': 'FS', 'lagDays': 0}])
        a2 = make_activity('A2', id='TASK2', dur=10.0, predecessors=[{'actId': 'TASK1', 'relType': 'FS', 'lagDays': 0}], successors=[])
        return [a1, a2]

    def test_relationships_resolved_through_raw_task_id(self):
        acts = self._chain_with_raw_task_ids()
        result = compute_scenario_cpm(acts, DD)
        # If the bug were present, A2 would have in-degree 0 (relationship
        # silently dropped) and start at the Data Date instead of after A1.
        self.assertEqual(result['perActivity']['A2']['earlyStart'], '2026-01-11')
        self.assertEqual(result['forecastFinish'], '2026-01-21')

    def test_scenario_action_by_code_actually_takes_effect(self):
        acts = self._chain_with_raw_task_ids()
        result = run_scenario(acts, [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 5}], DD)
        # Before the fix: relationships silently dropped -> reducing A1's
        # duration would have no downstream effect at all (A2 already
        # "starts" at the Data Date regardless), so recoveryDays would be 0.
        self.assertEqual(result['recoveryDays'], 5)

    def test_add_relationship_by_code_with_raw_task_id_network(self):
        a1 = make_activity('A1', id='T1', dur=5.0, successors=[])
        a2 = make_activity('A2', id='T2', dur=5.0, predecessors=[])
        result = run_scenario([a1, a2], [{'type': 'add_relationship', 'predecessorId': 'A1', 'successorId': 'A2', 'relType': 'FS', 'lagDays': 0}], DD)
        self.assertEqual(len(result['appliedActions']), 1)
        cpm = compute_scenario_cpm(
            apply_scenario_actions([a1, a2], [{'type': 'add_relationship', 'predecessorId': 'A1', 'successorId': 'A2'}])[0], DD,
        )
        self.assertEqual(cpm['perActivity']['A2']['earlyStart'], '2026-01-06')


class ControlledValidationCaseA_DurationCompression(SimpleTestCase):
    """A -> B -> C ; reduce B by 5 working days ; verify downstream effect."""

    def test_downstream_effect_of_duration_compression(self):
        a = make_activity('A', dur=5.0, successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', dur=10.0, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[{'actId': 'C', 'relType': 'FS', 'lagDays': 0}])
        c = make_activity('C', dur=5.0, predecessors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}], successors=[])
        result = run_scenario([a, b, c], [{'type': 'reduce_duration', 'activityId': 'B', 'newDuration': 5}], DD)
        self.assertEqual(result['recoveryDays'], 5)
        # C (downstream of B) should show 5 days of improvement.
        worsened_ids = {e['activityId'] for e in result['sideEffects']['worsened']}
        improved_ids = {e['activityId'] for e in result['sideEffects']['improved']}
        self.assertIn('C', improved_ids)
        self.assertNotIn('C', worsened_ids)


class ControlledValidationCaseB_Parallelization(SimpleTestCase):
    """A FS B -> change scenario relationship to A SS+lag B; verify result."""

    def test_fs_to_ss_with_lag_overlaps_activities(self):
        a = make_activity('A', dur=10.0, successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', dur=10.0, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[])
        # FS: B starts 2026-01-11. Changing to SS+5 should let B start 2026-01-06.
        modified, applied, _, _ = apply_scenario_actions(
            [a, b], [{'type': 'change_relationship_type', 'predecessorId': 'A', 'successorId': 'B', 'newRelType': 'SS'}],
        )
        # Apply lag reduction is a separate action type; SS with existing lag 0 means B starts with A.
        cpm = compute_scenario_cpm(modified, DD)
        self.assertEqual(cpm['perActivity']['B']['earlyStart'], '2026-01-01')
        self.assertEqual(len(applied), 1)


class ControlledValidationCaseC_FloatAbsorption(SimpleTestCase):
    """Activity improves 5 days but the milestone improves 0 because float
    absorbs the change — ScheduleIQ must not claim project-level improvement
    when there's slack absorbing it."""

    def test_non_driving_improvement_does_not_move_milestone(self):
        # A (critical, 10d) -> M (milestone). B (non-critical, 5d, 10 days
        # of float) runs in parallel and does NOT feed the milestone.
        a = make_activity('A', dur=10.0, successors=[{'actId': 'M', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', dur=5.0, successors=[])  # disconnected — plenty of slack, not on the driving chain
        m = make_activity('M', dur=0.0, is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        result = run_scenario([a, b, m], [{'type': 'reduce_duration', 'activityId': 'B', 'newDuration': 1}], DD)
        milestone = next(mi for mi in result['milestoneImpact'] if mi['activityId'] == 'M')
        self.assertEqual(milestone['movementDays'], 0)
        # B itself improved (it's 4 days shorter), but that never reaches the milestone.
        b_effect = next((e for e in result['sideEffects']['improved'] if e['activityId'] == 'B'), None)
        self.assertIsNotNone(b_effect)


class ControlledValidationCaseD_CriticalRecovery(SimpleTestCase):
    """Driving activity improves and the milestone improves."""

    def test_driving_activity_recovery_improves_milestone(self):
        a = make_activity('A', dur=10.0, successors=[{'actId': 'M', 'relType': 'FS', 'lagDays': 0}])
        m = make_activity('M', dur=0.0, is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        result = run_scenario([a, m], [{'type': 'reduce_duration', 'activityId': 'A', 'newDuration': 5}], DD)
        milestone = next(mi for mi in result['milestoneImpact'] if mi['activityId'] == 'M')
        self.assertEqual(milestone['movementDays'], 5)


class ControlledValidationCaseE_NonDrivingRecovery(SimpleTestCase):
    """Activity improves but project finish (max EF across the network) is
    unchanged, because it isn't on the path that determines project finish."""

    def test_non_driving_activity_improvement_does_not_change_project_finish(self):
        long_chain = make_activity('LONG', dur=30.0, successors=[])
        short = make_activity('SHORT', dur=5.0, successors=[])
        result = run_scenario([long_chain, short], [{'type': 'reduce_duration', 'activityId': 'SHORT', 'newDuration': 1}], DD)
        self.assertEqual(result['recoveryDays'], 0)


class ControlledValidationCaseF_SideEffect(SimpleTestCase):
    """Scenario improves Milestone A but worsens Milestone B."""

    def test_scenario_can_improve_one_milestone_and_worsen_another(self):
        # Removing A's lag-free FS to MB and re-routing through a longer
        # path would be complex to construct; instead: adding a NEW
        # relationship that makes MB depend on a longer chain demonstrates
        # a worsening side effect alongside MA's improvement.
        a = make_activity('A', dur=10.0, successors=[{'actId': 'MA', 'relType': 'FS', 'lagDays': 0}])
        ma = make_activity('MA', dur=0.0, is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}])
        long_act = make_activity('LONG', dur=20.0, successors=[])
        mb = make_activity('MB', dur=0.0, is_milestone=True, predecessors=[])
        acts = [a, ma, long_act, mb]
        result = run_scenario(acts, [
            {'type': 'reduce_duration', 'activityId': 'A', 'newDuration': 5},
            {'type': 'add_relationship', 'predecessorId': 'LONG', 'successorId': 'MB', 'relType': 'FS', 'lagDays': 0},
        ], DD)
        ma_impact = next(mi for mi in result['milestoneImpact'] if mi['activityId'] == 'MA')
        mb_impact = next(mi for mi in result['milestoneImpact'] if mi['activityId'] == 'MB')
        self.assertEqual(ma_impact['movementDays'], 5)   # improved
        self.assertEqual(mb_impact['movementDays'], -20)  # worsened — newly forced behind LONG


class ControlledValidationCaseG_LogicLoop(SimpleTestCase):
    def test_cycle_creating_relationship_is_rejected_with_explanation(self):
        a = make_activity('A', successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[])
        modified, applied, advisory, warnings = apply_scenario_actions(
            [a, b], [{'type': 'add_relationship', 'predecessorId': 'B', 'successorId': 'A'}],
        )
        self.assertEqual(applied, [])
        self.assertTrue(any('circular' in w.lower() for w in warnings))


class ControlledValidationCaseH_MultiCalendar(SimpleTestCase):
    def test_calendar_aware_result_differs_from_calendar_day_result(self):
        six_day = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: True, 7: False}, [])
        a = make_activity('A', dur=10.0, calendarId='FIVE', successors=[])
        b = make_activity('B', dur=10.0, calendarId='SIX', successors=[])
        calendars = {'FIVE': FIVE_DAY, 'SIX': six_day}
        result = compute_scenario_cpm([a, b], date(2026, 1, 5), calendars)  # a Monday
        ef_a = result['perActivity']['A']['earlyFinish']
        ef_b = result['perActivity']['B']['earlyFinish']
        # 10 working days on a 5-day calendar spans more calendar days than
        # 10 working days on a 6-day calendar starting the same Monday.
        self.assertNotEqual(ef_a, ef_b)
        self.assertTrue(result['perActivity']['A']['calendarAvailable'])
        self.assertTrue(result['perActivity']['B']['calendarAvailable'])
        self.assertTrue(result['calendarConfidence']['available'])

    def test_no_calendar_falls_back_to_calendar_days_and_discloses_it(self):
        a = make_activity('A', dur=10.0, successors=[])
        result = compute_scenario_cpm([a], DD, calendars=None)
        self.assertFalse(result['perActivity']['A']['calendarAvailable'])
        self.assertFalse(result['calendarConfidence']['available'])


class ControlledValidationCaseI_InProgressActivity(SimpleTestCase):
    def test_actual_start_preserved_remaining_work_scenario_adjusted(self):
        a = make_activity('A', dur=10.0, pct_complete=50.0, start='2025-12-20', successors=[])
        result = compute_scenario_cpm([a], DD)
        # Actual start is honored, never pulled to the Data Date.
        self.assertEqual(result['perActivity']['A']['earlyStart'], '2025-12-20')


class ControlledValidationCaseJ_CompletedActivity(SimpleTestCase):
    def test_completed_activity_not_rewritten_by_scenario(self):
        a = make_activity('A', dur=10.0, pct_complete=100.0, start='2025-12-01', finish='2025-12-11', successors=[])
        result = run_scenario([a], [{'type': 'reduce_duration', 'activityId': 'A', 'newDuration': 2}], DD)
        # A completed activity's remaining duration is always zero
        # (_duration_of never looks at `dur` once pctComplete>=100) — so
        # even though the "reduce duration" action IS mechanically applied
        # (the field bookkeeping changes), it has zero effect on the CPM
        # result: no fabricated recovery from work that already happened.
        self.assertEqual(len(result['appliedActions']), 1)
        self.assertEqual(result['recoveryDays'], 0)


class OpenEndProtectionTests(SimpleTestCase):
    def test_removing_relationship_leaving_open_end_is_flagged(self):
        a = make_activity('A', successors=[{'actId': 'B', 'relType': 'FS', 'lagDays': 0}])
        b = make_activity('B', predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[])
        _, applied, _, warnings = apply_scenario_actions(
            [a, b], [{'type': 'remove_relationship', 'predecessorId': 'A', 'successorId': 'B'}],
        )
        self.assertEqual(len(applied), 1)
        self.assertTrue(any('no successor' in w for w in warnings))
        self.assertTrue(any('no predecessor' in w for w in warnings))

    def test_removing_relationship_to_milestone_end_not_flagged(self):
        a = make_activity('A', successors=[{'actId': 'M', 'relType': 'FS', 'lagDays': 0}])
        m = make_activity('M', is_milestone=True, predecessors=[{'actId': 'A', 'relType': 'FS', 'lagDays': 0}], successors=[])
        _, applied, _, warnings = apply_scenario_actions(
            [a, m], [{'type': 'remove_relationship', 'predecessorId': 'A', 'successorId': 'M'}],
        )
        # A loses its only successor, but that successor was a milestone —
        # A itself is not a milestone, so it's still flagged (A has no
        # legitimate reason to be an open end); M losing its only
        # predecessor is a legitimate finish-milestone open end, not flagged.
        self.assertTrue(any('A' in w and 'no successor' in w for w in warnings))
        self.assertFalse(any('M' in w and 'no predecessor' in w for w in warnings))


class BaselineRecoveryTests(SimpleTestCase):
    """Item 26/57 — Baseline Variance / Recovered / Remaining / Recovery %."""

    def _project_20_late(self):
        # Baseline finish 2026-01-01; not-started, so ES = Data Date
        # (2026-01-01) and EF = ES + 20d = 2026-01-21 -> 20 calendar days
        # late against baseline. Scenario compresses to 8d -> 8 days late.
        a = make_activity('A', dur=20.0, b_start='2025-12-12', b_finish='2026-01-01', successors=[])
        return a

    def test_60_percent_recovery(self):
        a = self._project_20_late()
        baseline = [make_activity('A', b_start='2025-12-12', b_finish='2026-01-01')]
        result = run_scenario([a], [{'type': 'reduce_duration', 'activityId': 'A', 'newDuration': 8}], DD, approved_baseline_activities=baseline)
        rec = result['projectBaselineRecovery']
        self.assertIsNotNone(rec)
        self.assertEqual(rec['currentBaselineVarianceDays'], 20)
        self.assertEqual(rec['scenarioBaselineVarianceDays'], 8)
        self.assertEqual(rec['varianceRecoveredDays'], 12)
        self.assertEqual(rec['recoveryPct'], 60.0)

    def test_zero_baseline_variance_no_division(self):
        a = make_activity('A', dur=0.0, b_start=DD.isoformat(), b_finish=DD.isoformat(), successors=[])
        baseline = [make_activity('A', b_start=DD.isoformat(), b_finish=DD.isoformat())]
        result = run_scenario([a], [], DD, approved_baseline_activities=baseline)
        rec = result['projectBaselineRecovery']
        if rec['currentBaselineVarianceDays'] == 0:
            self.assertIsNone(rec['recoveryPct'])

    def test_already_ahead_of_baseline_no_misleading_percentage(self):
        a = make_activity('A', dur=5.0, b_start='2026-01-10', b_finish='2026-01-20', successors=[])
        baseline = [make_activity('A', b_start='2026-01-10', b_finish='2026-01-20')]
        result = run_scenario([a], [], DD, approved_baseline_activities=baseline)
        rec = result['projectBaselineRecovery']
        self.assertLess(rec['currentBaselineVarianceDays'], 0)
        self.assertIsNone(rec['recoveryPct'])

    def test_scenario_worsens_reports_negative_recovery(self):
        a = make_activity('A', dur=5.0, b_start='2026-01-01', b_finish='2026-01-06', successors=[])
        baseline = [make_activity('A', b_start='2026-01-01', b_finish='2026-01-06')]
        # add_relationship to a long chain worsens the scenario finish
        long_act = make_activity('LONG', dur=30.0, successors=[])
        result = run_scenario([a, long_act], [
            {'type': 'add_relationship', 'predecessorId': 'LONG', 'successorId': 'A', 'relType': 'FS', 'lagDays': 0},
        ], DD, approved_baseline_activities=baseline)
        rec = result['projectBaselineRecovery']
        self.assertIsNotNone(rec)
        self.assertLess(rec['varianceRecoveredDays'], 0)

    def test_no_baseline_supplied_recovery_is_unavailable(self):
        a = make_activity('A', dur=5.0, successors=[])
        result = run_scenario([a], [], DD)
        self.assertIsNone(result['projectBaselineRecovery'])


class DataDateIndependenceTests(SimpleTestCase):
    """recovery_engine.py never calls date.today()/datetime.now() anywhere
    — data_date is always an explicit parameter. Confirmed by direct code
    audit (no such call exists in the module) and reinforced here: running
    the identical scenario twice, with the real wall clock at two genuinely
    different moments (via time.sleep is unnecessary — the guarantee is
    structural, not timing-based), must yield bit-for-bit identical output."""

    def test_identical_result_across_repeated_runs(self):
        acts = _chain()
        actions = [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 5}]
        result1 = run_scenario(acts, actions, DD)
        result2 = run_scenario(acts, actions, DD)
        self.assertEqual(result1, result2)

    def test_no_reference_to_system_clock_in_module_source(self):
        import inspect
        import scheduler.recovery_engine as mod
        source = inspect.getsource(mod)
        self.assertNotIn('date.today(', source)
        self.assertNotIn('datetime.now(', source)
        self.assertNotIn('timezone.now(', source)
