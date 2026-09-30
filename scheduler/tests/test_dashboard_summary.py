"""
Main Dashboard — GET /api/projects/<id>/dashboard-summary/ tests.

This endpoint is a presentation/orchestration layer only: every field must
trace to an existing authoritative engine's own output. These tests pin
that contract — real HTTP round trip (real DB rows, not mocked engines) —
plus the "never invent a favorable number" and "one failed panel doesn't
break the dashboard" requirements the Main Dashboard directive calls for.
"""
from unittest.mock import patch

from django.test import TestCase

from scheduler.models import Project, ScheduleUpload
from .fixtures import make_activity


def _version(project, activities, data_date, version_label, classification='CURRENT_UPDATE', **overrides):
    defaults = dict(
        original_filename=f'{version_label}.xer', sanitized_filename=f'{version_label}.xer',
        file_type='XER', data_date=data_date, version_label=version_label,
        schedule_classification=classification, activities_json=activities,
        project_id_in_file='1', project_name_in_file=project.name,
    )
    defaults.update(overrides)
    return ScheduleUpload.objects.create(project=project, **defaults)


class DashboardSummaryBasicsTests(TestCase):
    def test_project_not_found_is_404(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/dashboard-summary/')
        self.assertEqual(resp.status_code, 404)

    def test_project_with_no_schedule_version_is_404(self):
        p = Project.objects.create(name='Empty')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        self.assertEqual(resp.status_code, 404)

    def test_response_is_a_presentation_layer_not_massive_row_payloads(self):
        # Guards against the exact regression caught during development:
        # passing update_intelligence's full per-activity floatMovement
        # lists straight through balloons the payload ~17x. 300 activities
        # with real movement should still stay well under 100KB.
        p = Project.objects.create(name='Barn-like')
        prev_acts = [make_activity(f'A{i:04d}', total_float=5.0) for i in range(300)]
        curr_acts = [make_activity(f'A{i:04d}', total_float=-5.0 if i < 100 else 5.0) for i in range(300)]
        _version(p, prev_acts, '2026-09-16', 'Prev')
        _version(p, curr_acts, '2026-09-23', 'Curr')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        self.assertEqual(resp.status_code, 200)
        self.assertLess(len(resp.content), 150_000, 'dashboard-summary payload grew unexpectedly large')


class CurrentPreviousBaselineResolutionTests(TestCase):
    def test_current_and_previous_resolve_by_chronology(self):
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1')]
        _version(p, acts, '2026-08-01', 'v1')
        v2 = _version(p, acts, '2026-09-01', 'v2')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        body = resp.json()
        self.assertEqual(body['context']['currentVersionId'], str(v2.id))
        self.assertEqual(body['context']['currentDataDate'], '2026-09-01')
        self.assertEqual(body['context']['previousDataDate'], '2026-08-01')

    def test_no_baseline_designated_shows_not_designated_not_a_number(self):
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1', b_finish='2026-06-01')]
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        body = resp.json()
        self.assertFalse(body['context']['baselineDesignated'])
        self.assertIsNone(body['kpis']['baselineFinish'])
        self.assertIsNone(body['kpis']['finishVarianceDays'])
        self.assertFalse(body['baselineVsForecast']['available'])
        self.assertIn('No baseline', body['baselineVsForecast']['reason'])

    def test_baseline_finish_never_borrowed_from_an_undesignated_versions_own_activity_dates(self):
        # Regression for the bug caught during development: the CURRENT
        # version's own stored baseline_finish field (derived from ITS
        # activities' bFinish, independent of project-level designation)
        # must never leak into the KPI when no version is actually
        # designated BASELINE.
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1', b_finish='2026-06-01')]
        v = _version(p, acts, '2026-09-01', 'v1')
        v.baseline_finish = __import__('datetime').date(2027, 1, 1)  # populated anyway, per real P6 exports
        v.forecast_finish = __import__('datetime').date(2026, 12, 1)
        v.save(update_fields=['baseline_finish', 'forecast_finish'])
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        body = resp.json()
        self.assertIsNone(body['kpis']['baselineFinish'])
        self.assertEqual(body['kpis']['currentForecastFinish'], '2026-12-01')

    def test_designated_baseline_populates_finish_and_variance_from_the_baseline_version(self):
        import datetime
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1')]
        bl = _version(p, acts, '2026-01-01', 'baseline', classification='APPROVED_BASELINE')
        bl.forecast_finish = datetime.date(2026, 12, 1)
        bl.save(update_fields=['forecast_finish'])
        cur = _version(p, acts, '2026-09-01', 'current')
        cur.forecast_finish = datetime.date(2027, 1, 15)
        cur.save(update_fields=['forecast_finish'])
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        body = resp.json()
        self.assertTrue(body['context']['baselineDesignated'])
        self.assertEqual(body['kpis']['baselineFinish'], '2026-12-01')
        self.assertEqual(body['kpis']['finishVarianceDays'], 45)  # Dec 1 -> Jan 15

    def test_no_previous_version_degrades_update_intelligence_but_not_everything_else(self):
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1'), make_activity('A2', is_milestone=True)]
        _version(p, acts, '2026-09-01', 'only')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        body = resp.json()
        self.assertFalse(body['updateIntelligence']['available'])
        self.assertIn('Previous', body['updateIntelligence']['reason'])
        self.assertTrue(body['kpis']['available'])
        self.assertTrue(body['floatHealth']['available'])
        self.assertTrue(body['topDrivers']['available'])
        self.assertTrue(body['lookAhead']['available'])
        self.assertEqual(body['intelligence']['bullets'], [])


class ManpowerTests(TestCase):
    def test_not_resource_loaded_shows_unavailable_never_fake_zeros(self):
        p = Project.objects.create(name='Proj')
        acts = [make_activity(f'A{i}') for i in range(5)]  # no isResourceLoaded/budgetedHours anywhere
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        m = resp.json()['manpower']
        self.assertFalse(m['available'])
        self.assertEqual(m['reason'], 'Unavailable — Schedule is not resource loaded.')
        self.assertIsNone(m['budgetedHours'])
        self.assertIsNone(m['actualHours'])
        self.assertIsNone(m['cpi'])
        self.assertIsNone(m['eac'])

    def test_resource_loaded_schedule_populates_real_numbers(self):
        p = Project.objects.create(name='Proj')
        acts = [
            make_activity('A1', isResourceLoaded=True, budgetedHours=100.0, actualHours=40.0, remainingHours=60.0, pct_complete=40.0),
            make_activity('A2', isResourceLoaded=True, budgetedHours=200.0, actualHours=80.0, remainingHours=120.0, pct_complete=40.0),
        ]
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        m = resp.json()['manpower']
        self.assertTrue(m['available'])
        self.assertIsNone(m['reason'])
        self.assertEqual(m['budgetedHours'], 300.0)
        self.assertEqual(m['actualHours'], 120.0)
        self.assertEqual(m['remainingHours'], 180.0)


class CompletedActivityFloatTests(TestCase):
    def test_completed_activity_with_negative_imported_float_excluded_from_negative_population(self):
        # Canonical is_activity_complete rule: an actual finish date makes
        # it complete regardless of a stale/negative raw totalFloat. A
        # completed activity must not inflate the dashboard's negative/
        # critical counts.
        p = Project.objects.create(name='Proj')
        acts = [
            make_activity('DONE', total_float=-30.0, finish='2026-08-01', pct_complete=100.0),
            make_activity('LIVE', total_float=-5.0),
        ]
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        fh = resp.json()['floatHealth']
        # Only the genuinely-incomplete negative-float activity counts.
        self.assertEqual(fh['negativeFloatCount'], 1)


class MilestoneAndDriverTests(TestCase):
    def test_milestone_forecast_lists_a_milestone(self):
        p = Project.objects.create(name='Proj')
        acts = [
            make_activity('A1', total_float=-10.0),
            make_activity('MS1', is_milestone=True, total_float=-2.0, b_finish='2026-10-01', name='Substantial Completion'),
        ]
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        mf = resp.json()['milestoneForecast']
        self.assertTrue(mf['available'])
        self.assertIn('MS1', [m['activityId'] for m in mf['topMilestones']])

    def test_top_drivers_only_includes_critical_driving_or_negative_float_incomplete_rows(self):
        p = Project.objects.create(name='Proj')
        acts = [
            make_activity('CRIT', total_float=-5.0),
            make_activity('HEALTHY', total_float=40.0, is_critical=False),
            make_activity('DONE_CRIT', total_float=-5.0, finish='2026-01-01', pct_complete=100.0),
        ]
        _version(p, acts, '2026-09-01', 'v1')
        resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')
        ids = [d['activityId'] for d in resp.json()['topDrivers']['drivers']]
        self.assertIn('CRIT', ids)
        self.assertNotIn('HEALTHY', ids)
        self.assertNotIn('DONE_CRIT', ids)


class PanelErrorIsolationTests(TestCase):
    def test_one_panel_raising_does_not_break_the_whole_dashboard(self):
        p = Project.objects.create(name='Proj')
        acts = [make_activity('A1'), make_activity('A2', is_milestone=True)]
        _version(p, acts, '2026-08-01', 'v1')
        _version(p, acts, '2026-09-01', 'v2')

        # compute_productivity backs ONLY the Manpower panel in this
        # endpoint (unlike baseline_progress.build_lookahead, which
        # update_intelligence.py also calls internally for its own
        # look-ahead comparison — patching that would legitimately break
        # two panels at once, not demonstrate isolation).
        with patch('scheduler.views.compute_productivity', side_effect=RuntimeError('boom')):
            resp = self.client.get(f'/api/projects/{p.id}/dashboard-summary/')

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body['manpower']['available'])
        self.assertIn('boom', body['manpower']['reason'])
        # Every other panel — computed independently — still came through.
        self.assertTrue(body['kpis']['available'])
        self.assertTrue(body['floatHealth']['available'])
        self.assertTrue(body['updateIntelligence']['available'])
        self.assertTrue(body['topDrivers']['available'])
        self.assertTrue(body['lookAhead']['available'])
