from django.test import SimpleTestCase

from scheduler import activity_analysis, float_intelligence as fi
from .fixtures import make_activity


def _rows(current, previous=None, baseline=None):
    return activity_analysis.build_activity_analysis(
        current, previous_activities=previous, baseline_activities=baseline,
    )['rows']


class FloatBandTests(SimpleTestCase):
    def test_bands_never_override_imported_critical_status(self):
        # A row's isCritical (imported P6-derived) is untouched by band
        # assignment — bands are a purely analytical bucket.
        self.assertEqual(fi.assign_float_band(-25), 'Severe Negative')
        self.assertEqual(fi.assign_float_band(-5), 'Negative')
        self.assertEqual(fi.assign_float_band(0), 'Zero')
        self.assertEqual(fi.assign_float_band(5), 'Near Critical')
        self.assertEqual(fi.assign_float_band(15), 'Low')
        self.assertEqual(fi.assign_float_band(30), 'Moderate')
        self.assertEqual(fi.assign_float_band(50), 'High')
        self.assertIsNone(fi.assign_float_band(None))


class FloatSummaryTests(SimpleTestCase):
    def test_summary_counts_and_missing_is_unavailable(self):
        current = [make_activity('A1', total_float=-3.0), make_activity('A2', total_float=5.0)]
        rows = _rows(current)
        summary = fi.compute_float_summary(rows)
        self.assertEqual(summary['activitiesAnalyzed'], 2)
        self.assertEqual(summary['negativeFloatCount'], 1)
        self.assertIsNone(summary['baselineMedianTotalFloat'])  # no baseline supplied


class CompletedActivityExclusionTests(SimpleTestCase):
    """A completed activity's currentTotalFloat/freeFloat are already
    blanked by activity_analysis.build_activity_analysis — these tests
    confirm that blanking correctly propagates into every Float
    Intelligence aggregation (summary KPIs, histogram, heat map) without
    float_intelligence.py needing its own completion-awareness."""

    def test_complete_activities_excluded_from_zero_float_kpi(self):
        current = [
            make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20'),  # complete, TF 0
            make_activity('A2', total_float=0.0, pct_complete=40.0, start='2026-01-05'),     # incomplete, TF 0
        ]
        rows = _rows(current)
        summary = fi.compute_float_summary(rows)
        self.assertEqual(summary['zeroFloatCount'], 1)
        self.assertEqual(summary['completedActivitiesExcluded'], 1)

    def test_complete_activities_excluded_from_critical_and_near_critical_kpi(self):
        current = [
            make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20'),   # complete + critical-by-TF
            make_activity('A2', total_float=5.0, pct_complete=100.0, finish='2026-01-20', is_critical=False),  # complete, near-critical range
            make_activity('A3', total_float=0.0, pct_complete=40.0, start='2026-01-05'),      # incomplete, still critical
        ]
        rows = _rows(current)
        summary = fi.compute_float_summary(rows)
        self.assertEqual(summary['criticalCount'], 1)      # only A3
        self.assertEqual(summary['nearCriticalCount'], 0)  # A2's near-critical range doesn't count while complete

    def test_complete_activities_excluded_from_current_float_histogram_by_default(self):
        current = [
            make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20'),
            make_activity('A2', total_float=0.0, pct_complete=40.0, start='2026-01-05'),
        ]
        rows = _rows(current)
        hist = fi.compute_float_distribution(rows)
        total_counted = sum(b['current'] for b in hist['buckets'])
        self.assertEqual(total_counted, 1)  # only the incomplete activity

    def test_complete_activities_excluded_from_heatmap_actionable_population(self):
        current = [
            make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20', area='Area A'),
            make_activity('A2', total_float=0.0, pct_complete=40.0, start='2026-01-05', area='Area A'),
        ]
        rows = _rows(current)
        hm = fi.compute_float_heatmap(rows, group_by='area')
        cell = next(c for c in hm['cells'] if c['group'] == 'Area A')
        self.assertEqual(cell['criticalCount'], 1)     # only A2 (A1 is complete)
        self.assertEqual(cell['medianTotalFloat'], 0.0)  # only A2's value is counted


class DistributionAndScatterTests(SimpleTestCase):
    def test_histogram_never_truncates_population(self):
        current = [make_activity(f'A{i}', total_float=float(i % 30 - 10)) for i in range(500)]
        rows = _rows(current)
        hist = fi.compute_float_distribution(rows)
        total_counted = sum(b['current'] for b in hist['buckets'])
        self.assertEqual(total_counted, 500)

    def test_scatter_requires_both_baseline_and_current(self):
        baseline = [make_activity('A1', total_float=10.0), make_activity('A2', total_float=5.0)]
        current = [make_activity('A1', total_float=-2.0)]  # A2 has no current counterpart
        rows = _rows(current, baseline=baseline)
        scatter = fi.compute_baseline_vs_current_scatter(rows)
        self.assertEqual(len(scatter), 1)
        self.assertEqual(scatter[0]['activityId'], 'A1')


class HeatMapTests(SimpleTestCase):
    def test_heatmap_never_sums_total_float(self):
        current = [
            make_activity('A1', total_float=10.0, area='Area A'),
            make_activity('A2', total_float=20.0, area='Area A'),
        ]
        rows = _rows(current)
        hm = fi.compute_float_heatmap(rows, group_by='area')
        cell = next(c for c in hm['cells'] if c['group'] == 'Area A')
        # A naive "sum" implementation would show 30 here — must not.
        self.assertNotIn('totalFloatSum', cell)
        self.assertEqual(cell['medianTotalFloat'], 15.0)
        self.assertEqual(cell['minimumTotalFloat'], 10.0)

    def test_heatmap_cells_report_zero_float_and_driving_counts(self):
        current = [
            make_activity('A1', total_float=0.0, area='Area B'),
            make_activity('A2', total_float=-5.0, area='Area B', onLongestPath=True),
            make_activity('A3', total_float=30.0, area='Area B'),
        ]
        rows = _rows(current)
        hm = fi.compute_float_heatmap(rows, group_by='area')
        cell = next(c for c in hm['cells'] if c['group'] == 'Area B')
        self.assertEqual(cell['zeroFloatCount'], 1)
        self.assertEqual(cell['drivingCount'], 1)

    def test_heatmap_cells_sorted_worst_first(self):
        current = [
            make_activity('A1', total_float=5.0, area='Area Healthy'),
            make_activity('A2', total_float=-50.0, area='Area Critical'),
            make_activity('A3', total_float=-2.0, area='Area Watch'),
        ]
        rows = _rows(current)
        hm = fi.compute_float_heatmap(rows, group_by='area')
        self.assertEqual(hm['cells'][0]['group'], 'Area Critical')


class RankingTests(SimpleTestCase):
    def test_deterioration_and_improvement_rankings(self):
        baseline = [make_activity('A1', total_float=20.0), make_activity('A2', total_float=5.0)]
        current = [make_activity('A1', total_float=-5.0), make_activity('A2', total_float=15.0)]
        rows = _rows(current, baseline=baseline)
        det = fi.rank_float_deterioration(rows)
        imp = fi.rank_float_improvement(rows)
        self.assertEqual(det[0]['activityId'], 'A1')
        self.assertEqual(imp[0]['activityId'], 'A2')

    def test_top_n_vs_all(self):
        baseline = [make_activity(f'A{i}', total_float=20.0) for i in range(20)]
        current = [make_activity(f'A{i}', total_float=20.0 - i) for i in range(20)]
        rows = _rows(current, baseline=baseline)
        top5 = fi.rank_float_deterioration(rows, top_n=5)
        all_rows = fi.rank_float_deterioration(rows)
        self.assertEqual(len(top5), 5)
        self.assertEqual(len(all_rows), 19)  # all with negative change (A0 unchanged)


class ScheduleExposureRankingTests(SimpleTestCase):
    """Improve Float Analysis Visualizations — Top Schedule Exposure
    Activities. Deterministic, explainable, NOT a weighted composite score:
    most-negative Total Float first, driving-path as a tie-break, finish
    variance as a further tie-break."""

    def test_only_negative_float_activities_qualify(self):
        current = [make_activity('A1', total_float=-5.0), make_activity('A2', total_float=10.0)]
        rows = _rows(current)
        ranked = fi.rank_schedule_exposure(rows)
        self.assertEqual([r['activityId'] for r in ranked], ['A1'])

    def test_completed_activity_never_qualifies_despite_imported_negative_float(self):
        current = [make_activity('A1', total_float=-5.0, pct_complete=100.0, finish='2026-01-20')]
        rows = _rows(current)
        ranked = fi.rank_schedule_exposure(rows)
        self.assertEqual(ranked, [])

    def test_sorted_most_negative_first(self):
        current = [
            make_activity('A1', total_float=-5.0), make_activity('A2', total_float=-50.0),
            make_activity('A3', total_float=-20.0),
        ]
        rows = _rows(current)
        ranked = fi.rank_schedule_exposure(rows)
        self.assertEqual([r['activityId'] for r in ranked], ['A2', 'A3', 'A1'])

    def test_driving_breaks_ties_at_equal_float(self):
        current = [
            make_activity('A1', total_float=-10.0, onLongestPath=False),
            make_activity('A2', total_float=-10.0, onLongestPath=True),
        ]
        rows = _rows(current)
        ranked = fi.rank_schedule_exposure(rows)
        self.assertEqual(ranked[0]['activityId'], 'A2')  # driving wins the tie

    def test_top_n_slices_without_truncating_the_underlying_ranking(self):
        current = [make_activity(f'A{i}', total_float=-float(i)) for i in range(1, 11)]
        rows = _rows(current)
        top3 = fi.rank_schedule_exposure(rows, top_n=3)
        all_ranked = fi.rank_schedule_exposure(rows)
        self.assertEqual(len(top3), 3)
        self.assertEqual(len(all_ranked), 10)

    def test_never_a_composite_score_field(self):
        current = [make_activity('A1', total_float=-5.0)]
        rows = _rows(current)
        ranked = fi.rank_schedule_exposure(rows)
        self.assertNotIn('exposureScore', ranked[0])
        self.assertNotIn('riskScore', ranked[0])


class ExposureMatrixTests(SimpleTestCase):
    """Schedule Exposure Matrix — quadrant boundaries reuse EXISTING
    thresholds (negativeFloat flag, BASELINE_VARIANCE_THRESHOLD_DAYS),
    never arbitrary frontend values. Operates directly on row-shaped dicts
    since approvedBaselineVarianceDays requires a real baseline-version
    match that the lighter _rows() helper here doesn't construct — this
    module's own documented contract is to consume already-built rows,
    never to re-derive them.

    Final Baseline Variance Architecture Review: `baseline_designated`
    means exactly "a baseline VERSION is designated for this project" —
    deliberately NOT coupled to whether a previous update exists (that
    pairing is only required for update-to-update movement, a separate
    concept this matrix never reads)."""

    def _row(self, **over):
        base = {
            'activityId': 'A1', 'activityName': 'Test', 'wbs': 'W1', 'area': 'Area A',
            'currentTotalFloat': -5.0, 'negativeFloat': True, 'approvedBaselineVarianceDays': 5.0,
            'critical': True, 'driving': False, 'activityStatus': 'TK_Active',
        }
        base.update(over)
        return base

    def test_unavailable_when_no_baseline_designated(self):
        result = fi.compute_exposure_matrix([self._row()], baseline_designated=False)
        self.assertFalse(result['available'])
        self.assertIn('unavailable', result['reason'].lower())
        self.assertEqual(result['points'], [])

    def test_row_missing_either_figure_is_excluded_not_fabricated(self):
        rows = [
            self._row(activityId='A1', approvedBaselineVarianceDays=None),
            self._row(activityId='A2', currentTotalFloat=None, negativeFloat=False),
            self._row(activityId='A3', approvedBaselineVarianceDays=8.0),
        ]
        result = fi.compute_exposure_matrix(rows, baseline_designated=True)
        self.assertEqual([p['activityId'] for p in result['points']], ['A3'])

    def test_high_exposure_quadrant(self):
        row = self._row(negativeFloat=True, currentTotalFloat=-15.0, approvedBaselineVarianceDays=25.0)
        result = fi.compute_exposure_matrix([row], baseline_designated=True)
        self.assertEqual(result['points'][0]['quadrant'], 'HIGH_EXPOSURE')

    def test_float_critical_quadrant(self):
        row = self._row(negativeFloat=True, currentTotalFloat=-15.0, approvedBaselineVarianceDays=2.0)
        result = fi.compute_exposure_matrix([row], baseline_designated=True)
        self.assertEqual(result['points'][0]['quadrant'], 'FLOAT_CRITICAL')

    def test_variance_critical_quadrant(self):
        row = self._row(negativeFloat=False, currentTotalFloat=5.0, approvedBaselineVarianceDays=25.0)
        result = fi.compute_exposure_matrix([row], baseline_designated=True)
        self.assertEqual(result['points'][0]['quadrant'], 'VARIANCE_CRITICAL')

    def test_controlled_monitor_quadrant(self):
        row = self._row(negativeFloat=False, currentTotalFloat=15.0, approvedBaselineVarianceDays=3.0)
        result = fi.compute_exposure_matrix([row], baseline_designated=True)
        self.assertEqual(result['points'][0]['quadrant'], 'CONTROLLED_MONITOR')

    def test_thresholds_reported_match_module_constant(self):
        result = fi.compute_exposure_matrix([], baseline_designated=True)
        self.assertEqual(result['thresholds']['varianceDays'], fi.BASELINE_VARIANCE_THRESHOLD_DAYS)

    def test_quadrant_boundary_values_match_risk_register_thresholds(self):
        # Pins these values to risk_register.py's own existing thresholds —
        # a change to one without the other should fail this test.
        from scheduler import risk_register
        self.assertEqual(fi.BASELINE_VARIANCE_THRESHOLD_DAYS, risk_register._BASELINE_VARIANCE_THRESHOLD)
        self.assertEqual(fi.SEVERE_NEGATIVE_FLOAT_THRESHOLD, risk_register._SEVERE_NEGATIVE_FLOAT_THRESHOLD)


class BaselineVarianceVsUpdateMovementTests(SimpleTestCase):
    """Final Baseline Variance Architecture Review — approvedBaselineVarianceDays
    (Current vs the designated Approved Baseline VERSION) and
    updateMovementDays (Current vs the PREVIOUS update) are separate
    fields computed from separate inputs; neither can substitute for the
    other, and baseline variance is available with NO previous update at
    all as long as a baseline is designated."""

    def test_baseline_variance_available_without_any_previous_version(self):
        baseline = [make_activity('A1', b_finish='2026-02-01')]
        current = [make_activity('A1', finish=None, start=None, b_finish='2026-02-01', earlyFinish='2026-03-10')]
        result = activity_analysis.build_activity_analysis(current, baseline_activities=baseline)
        row = result['rows'][0]
        self.assertIsNone(row['previousTotalFloat'])          # no previous version supplied at all
        self.assertIsNone(row['updateMovementDays'])           # update movement correctly unavailable
        self.assertEqual(row['approvedBaselineVarianceDays'], 37)  # 2026-02-01 -> 2026-03-10, independently available

    def test_baseline_variance_unavailable_without_a_designated_baseline(self):
        previous = [make_activity('A1', total_float=10.0, finish=None, earlyFinish='2026-02-20')]
        current = [make_activity('A1', total_float=5.0, finish=None, earlyFinish='2026-03-01')]
        result = activity_analysis.build_activity_analysis(
            current, previous_activities=previous,
            update_intelligence_result={'movementRows': [{'activityId': 'A1', 'finishMovementDays': 9, 'flags': []}]},
        )
        row = result['rows'][0]
        self.assertEqual(row['updateMovementDays'], 9)          # update movement available (previous supplied)
        self.assertIsNone(row['approvedBaselineVarianceDays'])  # baseline variance correctly unavailable (no baseline)

    def test_both_available_simultaneously_and_independently_correct(self):
        baseline = [make_activity('A1', b_finish='2026-01-01')]
        previous = [make_activity('A1', finish=None, earlyFinish='2026-02-20')]
        current = [make_activity('A1', finish=None, earlyFinish='2026-02-25', b_finish='2026-01-01')]
        result = activity_analysis.build_activity_analysis(
            current, previous_activities=previous, baseline_activities=baseline,
            update_intelligence_result={'movementRows': [{'activityId': 'A1', 'finishMovementDays': 5, 'flags': []}]},
        )
        row = result['rows'][0]
        self.assertEqual(row['updateMovementDays'], 5)            # Current vs Previous
        self.assertEqual(row['approvedBaselineVarianceDays'], 55)  # Current (Feb 25) vs Baseline (Jan 1) — a DIFFERENT figure
        self.assertNotEqual(row['updateMovementDays'], row['approvedBaselineVarianceDays'])


class NewlyNegativeRecoveredTests(SimpleTestCase):
    def test_newly_negative_and_recovered_lists(self):
        previous = [make_activity('A1', total_float=2.0), make_activity('A2', total_float=-3.0)]
        current = [make_activity('A1', total_float=-1.0), make_activity('A2', total_float=4.0)]
        rows = _rows(current, previous=previous)
        newly_neg = fi.list_newly_negative(rows)
        recovered = fi.list_recovered_from_negative(rows)
        self.assertEqual([r['activityId'] for r in newly_neg], ['A1'])
        self.assertEqual([r['activityId'] for r in recovered], ['A2'])


class FloatTrendTests(SimpleTestCase):
    def test_trend_uses_each_versions_own_data_date_and_value(self):
        version_points = [
            {'versionId': 'v1', 'versionLabel': 'Baseline', 'dataDate': '2026-01-01', 'role': 'BASELINE',
             'activities': [make_activity('A1', total_float=32.0)]},
            {'versionId': 'v2', 'versionLabel': 'Update 01', 'dataDate': '2026-02-01', 'role': 'OTHER',
             'activities': [make_activity('A1', total_float=24.0)]},
            {'versionId': 'v3', 'versionLabel': 'Current', 'dataDate': '2026-03-01', 'role': 'CURRENT',
             'activities': [make_activity('A1', total_float=-6.0)]},
        ]
        trend = fi.compute_float_trend(version_points, 'A1')
        values = [p['totalFloat'] for p in trend['points']]
        self.assertEqual(values, [32.0, 24.0, -6.0])
        self.assertEqual(trend['points'][0]['dataDate'], '2026-01-01')

    def test_activity_not_found_in_a_version_is_unavailable_not_zero(self):
        version_points = [
            {'versionId': 'v1', 'versionLabel': 'V1', 'dataDate': '2026-01-01', 'role': 'OTHER', 'activities': []},
        ]
        trend = fi.compute_float_trend(version_points, 'A1')
        self.assertFalse(trend['points'][0]['found'])
        self.assertIsNone(trend['points'][0]['totalFloat'])

    def test_historical_points_retained_when_activity_completes_in_current_version(self):
        # Baseline +18 -> Update 01 +7 -> Update 02 -3 -> Current Complete (—)
        version_points = [
            {'versionId': 'v1', 'versionLabel': 'Baseline', 'dataDate': '2026-01-01', 'role': 'BASELINE',
             'activities': [make_activity('A1', total_float=18.0, pct_complete=0.0)]},
            {'versionId': 'v2', 'versionLabel': 'Update 01', 'dataDate': '2026-02-01', 'role': 'OTHER',
             'activities': [make_activity('A1', total_float=7.0, pct_complete=30.0, start='2026-01-05')]},
            {'versionId': 'v3', 'versionLabel': 'Update 02', 'dataDate': '2026-03-01', 'role': 'OTHER',
             'activities': [make_activity('A1', total_float=-3.0, pct_complete=60.0, start='2026-01-05')]},
            {'versionId': 'v4', 'versionLabel': 'Current', 'dataDate': '2026-04-01', 'role': 'CURRENT',
             'activities': [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-03-25')]},
        ]
        trend = fi.compute_float_trend(version_points, 'A1')
        values = [p['totalFloat'] for p in trend['points']]
        self.assertEqual(values, [18.0, 7.0, -3.0, None])  # only the completed point is blanked
        # The imported figure is retained even for the blanked point.
        self.assertEqual(trend['points'][3]['importedTotalFloat'], 0.0)
        self.assertTrue(trend['points'][3]['complete'])
        self.assertFalse(trend['points'][0]['complete'])


class MilestoneFloatAnalysisTests(SimpleTestCase):
    def test_milestone_only_rows_included(self):
        current = [make_activity('A1', total_float=5.0), make_activity('M1', is_milestone=True, total_float=2.0)]
        rows = _rows(current)
        result = fi.compute_milestone_float_analysis(rows)
        self.assertEqual([m['activityId'] for m in result], ['M1'])

    def test_driving_exposure_unavailable_without_relationship_maps(self):
        current = [make_activity('M1', is_milestone=True, total_float=2.0)]
        rows = _rows(current)
        result = fi.compute_milestone_float_analysis(rows)
        self.assertIsNone(result[0]['negativeFloatPredecessorExposure'])
        self.assertIsNone(result[0]['drivingPredecessorCount'])
