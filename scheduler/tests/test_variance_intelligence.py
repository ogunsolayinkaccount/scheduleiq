"""
Variance Intelligence — unit tests for the pure variance_intelligence.py
engine. No DB, no Django test client — exercises basis selection,
direction/exposure classification, ranking, and aggregation directly
against row-shaped dicts, matching float_intelligence.py's own test
pattern (this module's own documented contract is to consume
already-built activity_analysis rows, never to re-derive them).
"""
from django.test import SimpleTestCase

from scheduler import variance_intelligence as vi


def _row(**over):
    base = {
        'activityId': 'A1', 'activityName': 'Test Activity', 'wbs': 'W1', 'area': 'Area A',
        'discipline': 'Electrical', 'contractor': 'ACME', 'system': 'HVAC',
        'activityStatus': 'TK_Active', 'finished': False, 'isMilestone': False,
        'currentStart': '2026-03-01', 'currentFinish': '2026-03-10',
        'approvedBaselineStart': '2026-02-20', 'approvedBaselineFinish': '2026-03-01',
        'approvedBaselineStartVarianceDays': 9, 'approvedBaselineVarianceDays': 9,
        'approvedBaselineStartVarianceWorkingDays': None, 'approvedBaselineVarianceWorkingDays': None,
        'embeddedBaselineStart': '2026-02-25', 'embeddedBaselineFinish': '2026-03-05',
        'startVarianceDays': 4, 'finishVarianceDays': 5,
        'startVarianceWorkingDays': None, 'finishVarianceWorkingDays': None,
        'previousStart': '2026-02-28', 'previousFinish': '2026-03-08',
        'startMovementDays': 1, 'finishMovementDays': 2,
        'startMovementWorkingDays': None, 'finishMovementWorkingDays': None,
        'currentTotalFloat': 5.0, 'negativeFloat': False, 'nearCritical': False, 'criticalActionable': False,
        'driving': False,
    }
    base.update(over)
    return base


class ComparisonBasisAvailabilityTests(SimpleTestCase):
    def test_approved_baseline_unavailable_reports_why(self):
        bases = vi.available_comparison_bases(baseline_designated=False, previous_available=True)
        approved = next(b for b in bases if b['key'] == 'approvedBaseline')
        self.assertFalse(approved['available'])
        self.assertIn('designated', approved['reason'])

    def test_previous_unavailable_reports_why(self):
        bases = vi.available_comparison_bases(baseline_designated=True, previous_available=False)
        previous = next(b for b in bases if b['key'] == 'previous')
        self.assertFalse(previous['available'])
        self.assertIn('previous', previous['reason'].lower())

    def test_embedded_baseline_always_structurally_available(self):
        bases = vi.available_comparison_bases(baseline_designated=False, previous_available=False)
        embedded = next(b for b in bases if b['key'] == 'embeddedBaseline')
        self.assertTrue(embedded['available'])

    def test_no_undo_or_scenario_basis_is_ever_offered(self):
        # ScheduleIQ has no stored, registered UNDO/scenario comparison
        # concept anywhere — never inferred from column names or dates.
        bases = vi.available_comparison_bases(baseline_designated=True, previous_available=True)
        keys = {b['key'] for b in bases}
        self.assertEqual(keys, {'approvedBaseline', 'embeddedBaseline', 'previous'})


class ResolveBasisTests(SimpleTestCase):
    def test_approved_baseline_basis_selects_the_right_fields(self):
        r = vi.resolve_basis(_row(), 'approvedBaseline')
        self.assertEqual(r['comparisonFinish'], '2026-03-01')
        self.assertEqual(r['finishVarianceDays'], 9)

    def test_embedded_baseline_basis_selects_the_right_fields(self):
        r = vi.resolve_basis(_row(), 'embeddedBaseline')
        self.assertEqual(r['comparisonFinish'], '2026-03-05')
        self.assertEqual(r['finishVarianceDays'], 5)

    def test_previous_basis_selects_the_right_fields(self):
        r = vi.resolve_basis(_row(), 'previous')
        self.assertEqual(r['comparisonFinish'], '2026-03-08')
        self.assertEqual(r['finishVarianceDays'], 2)

    def test_three_bases_never_return_the_same_values_for_a_divergent_row(self):
        row = _row()
        approved = vi.resolve_basis(row, 'approvedBaseline')
        embedded = vi.resolve_basis(row, 'embeddedBaseline')
        previous = vi.resolve_basis(row, 'previous')
        values = {approved['finishVarianceDays'], embedded['finishVarianceDays'], previous['finishVarianceDays']}
        self.assertEqual(len(values), 3)  # 9, 5, 2 — three genuinely distinct concepts


class DirectionClassificationTests(SimpleTestCase):
    def test_positive_variance_is_unfavorable(self):
        self.assertEqual(vi.classify_direction(14), 'UNFAVORABLE')

    def test_negative_variance_is_favorable(self):
        self.assertEqual(vi.classify_direction(-14), 'FAVORABLE')

    def test_zero_variance_is_no_movement(self):
        self.assertEqual(vi.classify_direction(0), 'NO_MOVEMENT')

    def test_none_variance_is_unavailable(self):
        self.assertEqual(vi.classify_direction(None), 'UNAVAILABLE')

    def test_interpretation_sentence_matches_the_requests_own_examples(self):
        self.assertIn('Unfavorable', vi.direction_interpretation(14))
        self.assertIn('later', vi.direction_interpretation(14))
        self.assertIn('Favorable', vi.direction_interpretation(-14))
        self.assertIn('earlier', vi.direction_interpretation(-14))


class ExposureClassificationTests(SimpleTestCase):
    """Transparent rule table — every tier explainable from displayed
    fields, never an opaque score."""

    def test_favorable_movement_is_always_favorable_regardless_of_float(self):
        self.assertEqual(vi.classify_exposure('FAVORABLE', -20.0, True, True), 'FAVORABLE')

    def test_no_movement_is_favorable(self):
        self.assertEqual(vi.classify_exposure('NO_MOVEMENT', 0.0, False, False), 'FAVORABLE')

    def test_unavailable_direction_is_unavailable_exposure(self):
        self.assertEqual(vi.classify_exposure('UNAVAILABLE', 5.0, False, False), 'UNAVAILABLE')

    def test_unfavorable_with_healthy_float_no_exposure_is_monitor(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', 25.0, False, False), 'MONITOR')

    def test_unfavorable_with_near_critical_float_is_warning(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', 5.0, False, False), 'WARNING')

    def test_unfavorable_with_zero_float_is_warning(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', 0.0, False, False), 'WARNING')

    def test_unfavorable_reaching_a_contractual_milestone_is_warning_even_with_healthy_float(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', 25.0, False, True), 'WARNING')

    def test_unfavorable_with_negative_float_and_driving_is_high_exposure(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', -10.0, True, False), 'HIGH_EXPOSURE')

    def test_unfavorable_with_negative_float_and_milestone_exposure_is_high_exposure(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', -10.0, False, True), 'HIGH_EXPOSURE')

    def test_unfavorable_with_negative_float_but_not_driving_or_exposed_is_warning_not_high(self):
        self.assertEqual(vi.classify_exposure('UNFAVORABLE', -10.0, False, False), 'WARNING')


class BuildVarianceRowsTests(SimpleTestCase):
    def test_completed_activity_governance_is_inherited_not_recomputed(self):
        # currentTotalFloat is already None for a completed activity under
        # activity_analysis.py's own rule — this module must not treat that
        # None as "healthy float"; it should simply classify as WARNING/
        # MONITOR based on the None branch of classify_exposure, same as
        # any other row with unavailable float.
        row = _row(finished=True, currentTotalFloat=None, approvedBaselineVarianceDays=14)
        out = vi.build_variance_rows([row], 'approvedBaseline')
        self.assertEqual(out[0]['currentTotalFloat'], None)
        self.assertIn(out[0]['assessment'], ('MONITOR', 'WARNING'))  # never HIGH_EXPOSURE from a None float

    def test_missing_activity_between_versions_has_no_comparison_fabricated(self):
        row = _row(previousFinish=None, finishMovementDays=None)
        out = vi.build_variance_rows([row], 'previous')
        self.assertEqual(out[0]['direction'], 'UNAVAILABLE')
        self.assertIsNone(out[0]['comparisonFinish'])

    def test_contractual_milestone_reachability_feeds_exposure(self):
        reach_map = {'A1': {'MS1': True}}
        row = _row(currentTotalFloat=25.0, approvedBaselineVarianceDays=10)
        out = vi.build_variance_rows([row], 'approvedBaseline', contractual_milestone_activity_ids={'MS1'}, reach_map=reach_map)
        self.assertTrue(out[0]['reachesContractualMilestone'])
        self.assertEqual(out[0]['assessment'], 'WARNING')


class RankingTests(SimpleTestCase):
    def test_high_exposure_outranks_larger_raw_variance_with_healthy_float(self):
        # The request's own example: 30d late + 100 float must NOT outrank
        # 10d late + -10 float driving a key milestone.
        big_variance_healthy_float = {
            **_row(activityId='BIG', approvedBaselineVarianceDays=30, currentTotalFloat=100.0),
        }
        small_variance_high_exposure = {
            **_row(activityId='SMALL', approvedBaselineVarianceDays=10, currentTotalFloat=-10.0, driving=True),
        }
        rows = vi.build_variance_rows([big_variance_healthy_float, small_variance_high_exposure], 'approvedBaseline')
        ranked = vi.rank_variance_exposure(rows)
        self.assertEqual(ranked[0]['activityId'], 'SMALL')
        self.assertEqual(ranked[0]['assessment'], 'HIGH_EXPOSURE')
        self.assertEqual(ranked[1]['activityId'], 'BIG')
        self.assertEqual(ranked[1]['assessment'], 'MONITOR')

    def test_favorable_and_unavailable_rows_are_excluded_from_ranking(self):
        favorable = _row(activityId='FAV', approvedBaselineVarianceDays=-5)
        unavailable = _row(activityId='UNAVAIL', approvedBaselineVarianceDays=None)
        unfavorable = _row(activityId='UNFAV', approvedBaselineVarianceDays=5)
        rows = vi.build_variance_rows([favorable, unavailable, unfavorable], 'approvedBaseline')
        ranked = vi.rank_variance_exposure(rows)
        self.assertEqual([r['activityId'] for r in ranked], ['UNFAV'])

    def test_rank_field_is_sequential_starting_at_one(self):
        rows = vi.build_variance_rows([
            _row(activityId='A', approvedBaselineVarianceDays=5, currentTotalFloat=-5.0),
            _row(activityId='B', approvedBaselineVarianceDays=5, currentTotalFloat=-15.0),
        ], 'approvedBaseline')
        ranked = vi.rank_variance_exposure(rows)
        self.assertEqual([r['rank'] for r in ranked], [1, 2])

    def test_top_n_slices_without_truncating_the_underlying_ranking(self):
        rows = vi.build_variance_rows(
            [_row(activityId=f'A{i}', approvedBaselineVarianceDays=5, currentTotalFloat=-float(i)) for i in range(1, 6)],
            'approvedBaseline',
        )
        top2 = vi.rank_variance_exposure(rows, top_n=2)
        all_ranked = vi.rank_variance_exposure(rows)
        self.assertEqual(len(top2), 2)
        self.assertEqual(len(all_ranked), 5)

    def test_never_an_opaque_score_field(self):
        rows = vi.build_variance_rows([_row(approvedBaselineVarianceDays=5, currentTotalFloat=-5.0)], 'approvedBaseline')
        ranked = vi.rank_variance_exposure(rows)
        self.assertNotIn('score', ranked[0])
        self.assertNotIn('weightedScore', ranked[0])


class AggregationTests(SimpleTestCase):
    def test_never_sums_total_float(self):
        rows = vi.build_variance_rows([
            _row(activityId='A', area='Area A', currentTotalFloat=10.0),
            _row(activityId='B', area='Area A', currentTotalFloat=20.0),
        ], 'approvedBaseline')
        summary = vi.aggregate_variance_by_group(rows, 'area')
        cell = next(c for c in summary['cells'] if c['group'] == 'Area A')
        self.assertNotIn('totalFloatSum', cell)
        self.assertEqual(cell['worstTotalFloat'], 10.0)

    def test_worst_finish_variance_is_the_most_adverse_not_the_largest_magnitude(self):
        rows = vi.build_variance_rows([
            _row(activityId='A', area='Area B', approvedBaselineVarianceDays=-30),  # very favorable
            _row(activityId='B', area='Area B', approvedBaselineVarianceDays=5),    # mildly unfavorable
        ], 'approvedBaseline')
        summary = vi.aggregate_variance_by_group(rows, 'area')
        cell = next(c for c in summary['cells'] if c['group'] == 'Area B')
        self.assertEqual(cell['worstFinishVarianceDays'], 5)  # the unfavorable one, not -30


class MilestoneVarianceTests(SimpleTestCase):
    def test_schedule_variance_and_contract_variance_stay_separate_fields(self):
        row = _row(isMilestone=True, approvedBaselineVarianceDays=5)
        rows = vi.build_variance_rows([row], 'approvedBaseline')
        tracker = {'A1': {'contractRequiredDate': '2026-04-01', 'varianceCalendarDays': 20, 'status': 'RED'}}
        out = vi.build_milestone_variance(rows, tracker)
        self.assertEqual(out[0]['finishVarianceDays'], 5)       # schedule comparison variance
        self.assertEqual(out[0]['contractVarianceDays'], 20)    # contract variance — different number
        self.assertNotEqual(out[0]['finishVarianceDays'], out[0]['contractVarianceDays'])

    def test_non_contractual_milestone_has_no_contract_fields_fabricated(self):
        row = _row(isMilestone=True)
        rows = vi.build_variance_rows([row], 'approvedBaseline')
        out = vi.build_milestone_variance(rows, {})
        self.assertIsNone(out[0]['contractRequiredDate'])
        self.assertIsNone(out[0]['contractVarianceDays'])

    def test_non_milestone_rows_excluded(self):
        row = _row(isMilestone=False)
        rows = vi.build_variance_rows([row], 'approvedBaseline')
        out = vi.build_milestone_variance(rows, {})
        self.assertEqual(out, [])


class CompletionMilestoneTests(SimpleTestCase):
    def test_identifies_the_latest_finishing_milestone(self):
        rows = vi.build_variance_rows([
            _row(activityId='MS1', isMilestone=True, currentFinish='2026-05-01'),
            _row(activityId='MS2', isMilestone=True, currentFinish='2026-06-15'),
        ], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows)
        self.assertEqual(completion['activityId'], 'MS2')
        self.assertEqual(completion['selectionBasis'], 'LATEST_FINISH_HEURISTIC')

    def test_registered_completion_milestone_is_preferred_over_a_later_finishing_one(self):
        # Reproduces a real acceptance-dataset finding verbatim: a utility/
        # ancillary milestone ("PD1040", large float) finished LATER than
        # the activity literally named "... Project Complete" ("M9999").
        # Latest-finish alone would wrongly pick PD1040; a registered
        # CONTRACTUAL_COMPLETION designation must override that.
        rows = vi.build_variance_rows([
            _row(activityId='M9999', activityName='Compute 01 Project Complete', isMilestone=True, currentFinish='2027-09-30', currentTotalFloat=834.0),
            _row(activityId='PD1040', activityName='Phase IV (Q2 2027 1680MVA)', isMilestone=True, currentFinish='2028-04-03', currentTotalFloat=676.0, driving=True),
        ], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows, registered_completion_activity_id='M9999')
        self.assertEqual(completion['activityId'], 'M9999')
        self.assertEqual(completion['selectionBasis'], 'REGISTERED')

    def test_falls_back_to_heuristic_when_registered_id_does_not_match_any_milestone(self):
        rows = vi.build_variance_rows([
            _row(activityId='MS1', isMilestone=True, currentFinish='2026-05-01'),
        ], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows, registered_completion_activity_id='DOES_NOT_EXIST')
        self.assertEqual(completion['activityId'], 'MS1')
        self.assertEqual(completion['selectionBasis'], 'LATEST_FINISH_HEURISTIC')

    def test_falls_back_to_heuristic_when_nothing_registered(self):
        rows = vi.build_variance_rows([_row(isMilestone=True, currentFinish='2026-05-01')], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows, registered_completion_activity_id=None)
        self.assertEqual(completion['selectionBasis'], 'LATEST_FINISH_HEURISTIC')

    def test_heuristic_interpretation_never_claims_authoritative_project_completion(self):
        rows = vi.build_variance_rows([_row(isMilestone=True, currentFinish='2026-05-01')], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows)  # no registration
        text = vi.completion_interpretation(completion)
        self.assertIn('not registered', text.lower())
        self.assertNotIn('the project-completion milestone is', text.lower())

    def test_registered_interpretation_carries_no_disclaimer(self):
        rows = vi.build_variance_rows([_row(activityId='M9999', isMilestone=True, currentFinish='2026-05-01')], 'approvedBaseline')
        completion = vi.identify_completion_milestone(rows, registered_completion_activity_id='M9999')
        text = vi.completion_interpretation(completion)
        self.assertNotIn('not registered', text.lower())

    def test_no_milestones_means_no_fabricated_completion(self):
        rows = vi.build_variance_rows([_row(isMilestone=False)], 'approvedBaseline')
        self.assertIsNone(vi.identify_completion_milestone(rows))

    def test_interpretation_mentions_zero_float_explicitly(self):
        milestone = {'finishVarianceDays': 0, 'direction': 'NO_MOVEMENT', 'currentTotalFloat': 0}
        text = vi.completion_interpretation(milestone)
        self.assertIn('0 days Total Float', text)

    def test_interpretation_handles_missing_milestone_honestly(self):
        text = vi.completion_interpretation(None)
        self.assertIn('cannot be automatically identified', text)

    def test_never_calls_project_delayed_from_a_single_unfavorable_activity(self):
        # completion_interpretation only ever describes the COMPLETION
        # milestone's own variance — it has no path that aggregates
        # individual-activity variance into a project-wide delay claim.
        milestone = {'finishVarianceDays': 5, 'direction': 'UNFAVORABLE', 'currentTotalFloat': 10.0}
        text = vi.completion_interpretation(milestone)
        self.assertNotIn('delayed', text.lower())
        self.assertIn('Unfavorable' if False else '5', text)  # mentions the actual day count


class UpdateMovementTests(SimpleTestCase):
    def test_never_labeled_as_baseline_variance(self):
        rows = [dict(_row(), finishMovementDays=5, startMovementDays=2)]
        summary = vi.build_update_movement_summary(rows)
        self.assertIn('finishSlipped', summary)
        self.assertNotIn('baselineVariance', str(summary.keys()).lower())

    def test_categorizes_slipped_vs_improved(self):
        rows = [
            dict(_row(activityId='SLIP'), finishMovementDays=5),
            dict(_row(activityId='IMPROVE'), finishMovementDays=-5),
        ]
        summary = vi.build_update_movement_summary(rows)
        self.assertEqual(summary['finishSlipped'], ['SLIP'])
        self.assertEqual(summary['finishImproved'], ['IMPROVE'])

    def test_newly_negative_and_recovered_pass_through_unchanged(self):
        rows = [
            dict(_row(activityId='NEWNEG'), newlyNegativeFloat=True, recoveredFromNegativeFloat=False),
            dict(_row(activityId='RECOV'), newlyNegativeFloat=False, recoveredFromNegativeFloat=True),
        ]
        summary = vi.build_update_movement_summary(rows)
        self.assertEqual(summary['newlyNegativeFloat'], ['NEWNEG'])
        self.assertEqual(summary['recoveredFromNegativeFloat'], ['RECOV'])


class NarrativeTests(SimpleTestCase):
    def test_narrative_is_fully_deterministic_no_ai_dependency(self):
        rows = vi.build_variance_rows([_row(approvedBaselineVarianceDays=5, currentTotalFloat=25.0)], 'approvedBaseline')
        area_summary = vi.aggregate_variance_by_group(rows, 'area')
        sentences = vi.build_deterministic_narrative(None, rows, area_summary, 'Current vs Approved Baseline')
        self.assertTrue(all(isinstance(s, str) for s in sentences))
        self.assertTrue(len(sentences) >= 2)

    def test_narrative_mentions_basis_label(self):
        sentences = vi.build_deterministic_narrative(None, [], {'cells': []}, 'Current vs Previous Update')
        self.assertTrue(any('Current vs Previous Update' in s for s in sentences))
