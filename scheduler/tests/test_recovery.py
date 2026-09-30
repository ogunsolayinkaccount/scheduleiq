import copy
from datetime import date

from django.test import SimpleTestCase

from scheduler.recovery_engine import apply_scenario_actions, compute_scenario_cpm, run_scenario
from .fixtures import make_activity

DD = date(2026, 1, 1)


def _chain():
    """A1 (10d) -> A2 (10d), FS, no lag. Both not started.

    Relationships are set on BOTH endpoints, matching how every real parser
    (and cpm.ts, which this engine's CPM pass mirrors) denormalizes them —
    the CPM pass walks successors[] to propagate the topological sort, so a
    predecessors-only fixture would look artificially circular/disconnected.
    """
    a1 = make_activity('A1', dur=10.0, predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}])
    a2 = make_activity('A2', dur=10.0, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[])
    return [a1, a2]


class ApplyScenarioActionsTests(SimpleTestCase):
    def test_original_activities_never_mutated(self):
        original = _chain()
        original_snapshot = copy.deepcopy(original)
        apply_scenario_actions(original, [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 2}])
        self.assertEqual(original, original_snapshot)

    def test_reduce_duration_applied(self):
        modified, applied, advisory, warnings = apply_scenario_actions(
            _chain(), [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 5}],
        )
        a1 = next(a for a in modified if a['code'] == 'A1')
        self.assertEqual(a1['dur'], 5)
        self.assertEqual(len(applied), 1)
        self.assertEqual(warnings, [])

    def test_reduce_duration_to_larger_value_is_rejected(self):
        modified, applied, advisory, warnings = apply_scenario_actions(
            _chain(), [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 99}],
        )
        self.assertEqual(applied, [])
        self.assertTrue(warnings)

    def test_unknown_activity_produces_warning_not_crash(self):
        modified, applied, advisory, warnings = apply_scenario_actions(
            _chain(), [{'type': 'reduce_duration', 'activityId': 'DOES_NOT_EXIST', 'newDuration': 1}],
        )
        self.assertEqual(applied, [])
        self.assertIn('DOES_NOT_EXIST', warnings[0])

    def test_remove_lag_zeroes_both_sides(self):
        acts = [
            make_activity('A1'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 5}]),
        ]
        modified, applied, advisory, warnings = apply_scenario_actions(
            acts, [{'type': 'remove_lag', 'predecessorId': 'A1', 'successorId': 'A2'}],
        )
        a2 = next(a for a in modified if a['code'] == 'A2')
        self.assertEqual(a2['predecessors'][0]['lagDays'], 0.0)

    def test_advisory_action_recorded_but_not_applied(self):
        modified, applied, advisory, warnings = apply_scenario_actions(
            _chain(), [{'type': 'increase_crew', 'activityId': 'A1', 'note': 'Add second crew'}],
        )
        self.assertEqual(applied, [])
        self.assertEqual(len(advisory), 1)
        self.assertEqual(advisory[0]['type'], 'increase_crew')

    def test_add_relationship_rejects_cycle(self):
        acts = _chain()   # A1 -> A2 already exists
        modified, applied, advisory, warnings = apply_scenario_actions(
            acts, [{'type': 'add_relationship', 'predecessorId': 'A2', 'successorId': 'A1', 'relType': 'FS'}],
        )
        self.assertEqual(applied, [])
        self.assertIn('circular', warnings[0])

    def test_unknown_action_type_becomes_advisory_with_warning(self):
        modified, applied, advisory, warnings = apply_scenario_actions(
            _chain(), [{'type': 'totally_made_up', 'activityId': 'A1'}],
        )
        self.assertEqual(len(advisory), 1)
        self.assertTrue(warnings)


class ComputeScenarioCpmTests(SimpleTestCase):
    def test_forecast_finish_serial_chain(self):
        result = compute_scenario_cpm(_chain(), DD)
        self.assertEqual(result['forecastFinish'], '2026-01-21')
        self.assertEqual(result['unresolvedGroups'], 0)

    def test_circular_logic_group_is_unresolved_not_guessed(self):
        acts = [
            make_activity('A1', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = compute_scenario_cpm(acts, DD)
        self.assertEqual(result['unresolvedGroups'], 1)
        self.assertIsNone(result['forecastFinish'])


class RunScenarioTests(SimpleTestCase):
    def test_reducing_duration_on_serial_chain_yields_expected_recovery(self):
        result = run_scenario(_chain(), [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 5}], DD)
        self.assertEqual(result['currentForecastFinish'], '2026-01-21')
        self.assertEqual(result['scenarioForecastFinish'], '2026-01-16')
        self.assertEqual(result['recoveryDays'], 5)
        self.assertIn('A1', result['affectedActivityIds'])
        self.assertIn('disclaimer', result)

    def test_advisory_only_scenario_has_no_recovery_days_claimed_beyond_logic(self):
        result = run_scenario(_chain(), [{'type': 'increase_crew', 'activityId': 'A1', 'note': 'test'}], DD)
        # Nothing was actually applied to the network, so the schedule is unchanged.
        self.assertEqual(result['recoveryDays'], 0)
        self.assertEqual(len(result['advisoryActions']), 1)
        self.assertEqual(result['appliedActions'], [])
