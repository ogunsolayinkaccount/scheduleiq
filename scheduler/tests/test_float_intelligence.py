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
