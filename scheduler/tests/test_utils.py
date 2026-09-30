from datetime import date

from django.test import SimpleTestCase

from scheduler.utils import (
    _finish_variance,
    _proj_finish,
    _proj_start,
    _start_variance,
    compute_metrics,
    normalize_key,
    parse_date,
)
from .fixtures import make_activity

DD = date(2026, 6, 1)


class ParseDateTests(SimpleTestCase):
    def test_iso_format(self):
        self.assertEqual(parse_date('2026-06-01'), date(2026, 6, 1))

    def test_us_slash_format(self):
        self.assertEqual(parse_date('06/01/2026'), date(2026, 6, 1))

    def test_dash_month_abbrev_format(self):
        self.assertEqual(parse_date('01-Jun-2026'), date(2026, 6, 1))

    def test_none_returns_none(self):
        self.assertIsNone(parse_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_date(''))

    def test_already_a_date_passthrough(self):
        d = date(2026, 6, 1)
        self.assertEqual(parse_date(d), d)

    def test_garbage_string_returns_none(self):
        self.assertIsNone(parse_date('not a date'))


class NormalizeKeyTests(SimpleTestCase):
    def test_spaces_and_punctuation_become_underscores(self):
        self.assertEqual(normalize_key('Activity ID'), 'activity_id')
        self.assertEqual(normalize_key('% Complete'), '_complete')

    def test_none_returns_none(self):
        self.assertIsNone(normalize_key(None))


class ProjectedDateHelperTests(SimpleTestCase):
    def test_complete_activity_finish_is_locked_to_actual(self):
        a = {'pctComplete': 100.0, 'finish': date(2026, 1, 15), 'bStart': date(2026, 1, 1)}
        self.assertEqual(_proj_finish(a, DD), date(2026, 1, 15))

    def test_in_progress_activity_projects_from_data_date(self):
        a = {'pctComplete': 50.0, 'start': date(2026, 5, 1), 'remainDur': 10, 'bStart': date(2026, 5, 1)}
        # In-progress: projected finish = Data Date + remaining duration.
        self.assertEqual(_proj_finish(a, DD), date(2026, 6, 11))

    def test_not_started_activity_cannot_forecast_before_data_date(self):
        # Baseline start is in the past — the projected start must clamp to DD.
        a = {'pctComplete': 0.0, 'bStart': date(2026, 1, 1), 'dur': 5}
        self.assertEqual(_proj_start(a, DD), DD)

    def test_not_started_activity_with_future_baseline_start_uses_baseline(self):
        a = {'pctComplete': 0.0, 'bStart': date(2026, 9, 1), 'dur': 5}
        self.assertEqual(_proj_start(a, DD), date(2026, 9, 1))

    def test_finish_variance_positive_when_late(self):
        a = {'pctComplete': 0.0, 'bStart': date(2026, 1, 1), 'bFinish': date(2026, 1, 10), 'dur': 5}
        var = _finish_variance(a, DD)
        self.assertIsNotNone(var)
        self.assertGreater(var, 0)

    def test_finish_variance_none_without_baseline_finish(self):
        a = {'pctComplete': 0.0, 'dur': 5}
        self.assertIsNone(_finish_variance(a, DD))

    def test_start_variance_none_without_baseline_start(self):
        a = {'pctComplete': 0.0}
        self.assertIsNone(_start_variance(a, DD))


class ComputeMetricsEdgeCaseTests(SimpleTestCase):
    def test_empty_activity_list_returns_zeroed_shape(self):
        result = compute_metrics([])
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['projects'], [])
        self.assertEqual(result['dataDate'], date.today().isoformat())

    def test_single_activity(self):
        acts = [make_activity('A1', pct_complete=100.0)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['completed'], 1)

    def test_negative_float_bucketed_correctly(self):
        acts = [make_activity('A1', total_float=-5.0, pct_complete=10.0)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['fb']['negative'], 1)

    def test_positive_float_bucketed_correctly(self):
        acts = [make_activity('A1', total_float=20.0, pct_complete=10.0)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['fb']['high'], 1)

    def test_completed_activities_counted(self):
        # A completed activity needs an actual start date set, matching every
        # real parser — compute_metrics uses start IS None as its "not
        # started" signal, independent of pctComplete.
        acts = [
            make_activity('A1', pct_complete=100.0, start='2026-01-01', finish='2026-01-05'),
            make_activity('A2', pct_complete=0.0),
        ]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['completed'], 1)
        self.assertEqual(result['notStarted'], 1)

    def test_milestones_excluded_from_critical_count(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, total_float=-5.0, is_critical=True)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['critical'], 0)
        self.assertEqual(result['milestones'], 1)

    def test_unusually_long_duration_falls_into_top_duration_bucket(self):
        acts = [make_activity('A1', dur=100.0)]
        result = compute_metrics(acts, data_date=DD)
        top_bucket = result['durHist'][-1]
        self.assertEqual(top_bucket['range'], '41+d')
        self.assertEqual(top_bucket['count'], 1)

    def test_missing_dates_do_not_crash_monthly_trend(self):
        acts = [make_activity('A1', b_start=None, b_finish=None)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['monthlyTrend'], [])

    def test_no_critical_activities_evm_still_computed(self):
        acts = [make_activity(f'A{i}', total_float=20.0, is_critical=False, pct_complete=50.0) for i in range(3)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['critical'], 0)
        self.assertIn('evm', result)

    def test_evm_falls_back_to_duration_when_no_cost_loaded(self):
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0)]
        result = compute_metrics(acts, data_date=DD)
        self.assertFalse(result['isCostLoaded'])
        self.assertEqual(result['evmDataQuality'], 'duration_only')

    def test_multi_project_rollup(self):
        acts = [
            {**make_activity('A1'), 'projectId': 'P1', 'projectName': 'Project One'},
            {**make_activity('A2'), 'projectId': 'P2', 'projectName': 'Project Two'},
        ]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(len(result['projects']), 2)

    def test_overdue_excludes_milestones(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, b_finish='2026-01-01', pct_complete=0.0)]
        result = compute_metrics(acts, data_date=DD)
        self.assertEqual(result['overdue'], 0)
