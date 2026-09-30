"""
Master Schedule Analysis, Float, Progress & Milestone Intelligence — API
layer tests. Covers the Activity Analysis, Float Analysis, and Float Trend
endpoints end-to-end via the Django test client.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from scheduler.models import Project, ScheduleUpload


def _act(code, name='Activity', b_start='2026-01-01', b_finish='2026-01-20', **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'wbs': 'Area A', 'area': 'Area A', 'discipline': 'Electrical',
        'bStart': b_start, 'bFinish': b_finish, 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class ActivityAnalysisApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Activity Analysis API Test')
        now = timezone.now()
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', totalFloat=20.0, dur=20.0, remainDur=20.0)],
            upload_timestamp=now - timedelta(days=30),
        )
        self.previous = ScheduleUpload.objects.create(
            project=self.project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-01',
            activities_json=[_act('A1', totalFloat=10.0)],
            upload_timestamp=now - timedelta(days=7),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', totalFloat=-3.0, earlyFinish='2026-09-01', isCritical=True)],
            upload_timestamp=now,
        )

    def test_returns_full_row_population_with_traceability(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/activity-analysis/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body['available'])
        self.assertEqual(body['rowCount'], 1)
        self.assertEqual(len(body['rows']), 1)
        self.assertEqual(body['baselineVersionLabel'], self.baseline.version_label or self.baseline.original_filename)
        self.assertIsNotNone(body['currentDataDate'])

    def test_baseline_previous_current_float_distinct_in_response(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/activity-analysis/'
            f'?currentVersion={self.current.id}&previousVersion={self.previous.id}&baselineVersion={self.baseline.id}'
        )
        row = resp.json()['rows'][0]
        self.assertEqual(row['baselineTotalFloat'], 20.0)
        self.assertEqual(row['previousTotalFloat'], 10.0)
        self.assertEqual(row['currentTotalFloat'], -3.0)
        self.assertTrue(row['newlyNegativeFloat'])

    def test_missing_version_returns_404_not_error_crash(self):
        empty_project = Project.objects.create(name='No Versions')
        resp = self.client.get(f'/api/projects/{empty_project.id}/activity-analysis/')
        self.assertEqual(resp.status_code, 404)

    def test_include_risk_param_does_not_break_when_no_risk_data(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/activity-analysis/'
            f'?currentVersion={self.current.id}&previousVersion={self.previous.id}&includeRisk=true'
        )
        self.assertEqual(resp.status_code, 200, resp.content)


class FloatAnalysisApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Float Analysis API Test')
        now = timezone.now()
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[
                _act('A1', totalFloat=20.0),
                _act('M1', isMilestone=True, totalFloat=5.0, name='Substantial Completion'),
            ],
            upload_timestamp=now - timedelta(days=30),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('A1', totalFloat=-3.0, isCritical=True),
                _act('M1', isMilestone=True, totalFloat=-1.0, name='Substantial Completion'),
            ],
            upload_timestamp=now,
        )

    def test_float_analysis_returns_all_sections(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        for key in (
            'summary', 'distribution', 'baselineVsCurrentScatter', 'floatChangeDistribution', 'heatmap',
            'floatVsFinishVarianceScatter', 'floatVsRemainingDurationScatter', 'topDeterioration',
            'topImprovement', 'newlyNegative', 'recoveredFromNegative', 'milestoneFloatAnalysis',
            'calendarConfidence',
        ):
            self.assertIn(key, body, f'missing key: {key}')

    def test_summary_reflects_real_deterioration(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        )
        summary = resp.json()['summary']
        self.assertEqual(summary['negativeFloatCount'], 2)
        self.assertGreaterEqual(summary['activitiesAnalyzed'], 2)

    def test_heatmap_never_sums_total_float(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}&heatmapGroupBy=area'
        )
        cells = resp.json()['heatmap']['cells']
        for c in cells:
            self.assertNotIn('totalFloatSum', c)

    def test_milestone_float_analysis_includes_driving_exposure(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        )
        milestones = resp.json()['milestoneFloatAnalysis']
        self.assertEqual(len(milestones), 1)
        self.assertIn('negativeFloatPredecessorExposure', milestones[0])
        self.assertIn('drivingPredecessorCount', milestones[0])

    def test_top_n_vs_all_via_query_param(self):
        resp_all = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        )
        resp_top1 = self.client.get(
            f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}&topN=1'
        )
        all_det = resp_all.json()['topDeterioration']
        top1_det = resp_top1.json()['topDeterioration']
        self.assertLessEqual(len(top1_det), 1)
        self.assertGreaterEqual(len(all_det), len(top1_det))


class CompletedActivityFloatApiTests(TestCase):
    """P6 behavior: a completed activity's Current Total Float displays as
    Unavailable ('—'), not 0, and is excluded from actionable current-float
    KPIs/filters by default — end-to-end through the API layer."""

    def setUp(self):
        self.project = Project.objects.create(name='Completed Float API Test')
        now = timezone.now()
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', totalFloat=18.0), _act('A2', totalFloat=18.0)],
            upload_timestamp=now - timedelta(days=30),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                # Complete, stored TF 0 — must display '—', not 0.
                _act('A1', totalFloat=0.0, pctComplete=100.0, start='2026-01-05', finish='2026-01-20'),
                # Incomplete, stored TF 0 — must remain a real, actionable 0.
                _act('A2', totalFloat=0.0, pctComplete=40.0, start='2026-01-05'),
            ],
            upload_timestamp=now,
        )

    def _url(self, path, **params):
        base = f'/api/projects/{self.project.id}/{path}?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        return base + ''.join(f'&{k}={v}' for k, v in params.items())

    def test_activity_analysis_blanks_completed_current_float(self):
        resp = self.client.get(self._url('activity-analysis/'))
        rows = {r['activityId']: r for r in resp.json()['rows']}
        self.assertIsNone(rows['A1']['currentTotalFloat'])
        self.assertEqual(rows['A1']['importedCurrentTotalFloat'], 0.0)  # source traceability preserved
        self.assertEqual(rows['A2']['currentTotalFloat'], 0.0)
        # Historical baseline retained for the completed activity.
        self.assertEqual(rows['A1']['baselineTotalFloat'], 18.0)

    def test_float_analysis_zero_float_kpi_excludes_completed(self):
        resp = self.client.get(self._url('float-analysis/'))
        summary = resp.json()['summary']
        self.assertEqual(summary['zeroFloatCount'], 1)  # only A2
        self.assertEqual(summary['completedActivitiesExcluded'], 1)

    def test_include_completed_audit_option_restores_imported_value(self):
        resp = self.client.get(self._url('float-analysis/', includeCompleted='true'))
        body = resp.json()
        self.assertTrue(body['includeCompleted'])
        self.assertEqual(body['summary']['zeroFloatCount'], 2)  # A1's imported 0 now counted too

    def test_version_switch_incomplete_then_complete_is_handled_correctly(self):
        # A1 was incomplete (TF -3) in the previous version, complete
        # (TF 0) in current — both figures must be independently correct.
        previous = ScheduleUpload.objects.create(
            project=self.project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-06-01',
            activities_json=[_act('A1', totalFloat=-3.0, pctComplete=60.0, start='2026-01-05')],
            upload_timestamp=timezone.now() - timedelta(days=14),
        )
        resp = self.client.get(
            f'/api/projects/{self.project.id}/activity-analysis/'
            f'?currentVersion={self.current.id}&previousVersion={previous.id}&baselineVersion={self.baseline.id}'
        )
        row = next(r for r in resp.json()['rows'] if r['activityId'] == 'A1')
        self.assertIsNone(row['currentTotalFloat'])         # blanked now that it's complete
        self.assertEqual(row['previousTotalFloat'], -3.0)   # historical value retained, unaffected


class FloatBandDrillDownFilterApiTests(TestCase):
    """Main Dashboard -> Float Analysis drill-down (Negative/Zero/Near
    Critical). Every filter here reuses _filter_analysis_rows /
    activity_analysis's own per-row fields — no float band is ever
    recalculated for this endpoint. A completed activity (NEG_DONE) sits
    in every fixture to prove the canonical completion rule excludes it
    from all three bands, not just negativeFloatOnly."""

    def setUp(self):
        self.project = Project.objects.create(name='Float Band Drill-Down Test')
        now = timezone.now()
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('NEG', totalFloat=-5.0, isCritical=True),
                _act('ZERO', totalFloat=0.0, isCritical=True),
                _act('NEARCRIT', totalFloat=5.0, isCritical=False),   # 0 < 5 <= 10 -> near-critical
                _act('HIGH', totalFloat=25.0, isCritical=False),      # well past the threshold
                # Complete, with a stale negative imported float — must be
                # excluded from EVERY band (is_activity_complete: actual
                # finish present), not just Negative Float.
                _act('NEG_DONE', totalFloat=-8.0, pctComplete=100.0, start='2026-01-05', finish='2026-01-20'),
            ],
            upload_timestamp=now,
        )

    def _url(self, **params):
        base = f'/api/projects/{self.project.id}/float-analysis/?currentVersion={self.current.id}'
        return base + ''.join(f'&{k}={v}' for k, v in params.items())

    def test_negative_float_only_filters_to_negative_and_excludes_completed(self):
        resp = self.client.get(self._url(negativeFloatOnly='true'))
        summary = resp.json()['summary']
        self.assertEqual(summary['activitiesAnalyzed'], 1)
        self.assertEqual(summary['negativeFloatCount'], 1)

    def test_zero_float_only_filters_to_zero_and_excludes_completed(self):
        resp = self.client.get(self._url(zeroFloatOnly='true'))
        summary = resp.json()['summary']
        self.assertEqual(summary['activitiesAnalyzed'], 1)
        self.assertEqual(summary['zeroFloatCount'], 1)

    def test_near_critical_only_filters_to_near_critical_and_excludes_completed(self):
        resp = self.client.get(self._url(nearCriticalOnly='true'))
        summary = resp.json()['summary']
        self.assertEqual(summary['activitiesAnalyzed'], 1)
        self.assertEqual(summary['nearCriticalCount'], 1)

    def test_near_critical_threshold_is_reported_not_hard_coded_in_the_response(self):
        from scheduler.activity_analysis import NEAR_CRITICAL_FLOAT_THRESHOLD
        resp = self.client.get(self._url())
        self.assertEqual(resp.json()['nearCriticalThresholdDays'], NEAR_CRITICAL_FLOAT_THRESHOLD)

    def test_three_filters_are_mutually_exclusive_partitions_of_the_incomplete_population(self):
        # Every incomplete, non-milestone activity here falls into exactly
        # one band or none (HIGH) — the three drill-downs must never
        # overlap or double-count the same activity.
        neg = self.client.get(self._url(negativeFloatOnly='true')).json()['summary']['activitiesAnalyzed']
        zero = self.client.get(self._url(zeroFloatOnly='true')).json()['summary']['activitiesAnalyzed']
        near = self.client.get(self._url(nearCriticalOnly='true')).json()['summary']['activitiesAnalyzed']
        self.assertEqual(neg + zero + near, 3)  # NEG + ZERO + NEARCRIT, never HIGH or NEG_DONE

    def test_unfiltered_counts_reconcile_with_the_dashboard_floatHealth_panel(self):
        # The exact reconciliation guarantee the drill-down depends on:
        # Dashboard's floatHealth and Float Analysis's own (unfiltered)
        # summary must agree, since both call compute_float_summary on the
        # identical activity_analysis rows for this version.
        dash = self.client.get(f'/api/projects/{self.project.id}/dashboard-summary/?currentVersion={self.current.id}').json()
        flt = self.client.get(self._url()).json()['summary']
        self.assertEqual(dash['floatHealth']['negativeFloatCount'], flt['negativeFloatCount'])
        self.assertEqual(dash['floatHealth']['zeroFloatCount'], flt['zeroFloatCount'])
        self.assertEqual(dash['floatHealth']['nearCriticalCount'], flt['nearCriticalCount'])

    def test_filtered_drill_down_counts_reconcile_exactly_with_dashboard_floatHealth(self):
        # What actually happens when a user clicks a Dashboard Float Health
        # card: the filtered Float Analysis view's own summary count for
        # that band must equal the count the Dashboard card showed.
        dash = self.client.get(f'/api/projects/{self.project.id}/dashboard-summary/?currentVersion={self.current.id}').json()['floatHealth']
        neg = self.client.get(self._url(negativeFloatOnly='true')).json()['summary']
        zero = self.client.get(self._url(zeroFloatOnly='true')).json()['summary']
        near = self.client.get(self._url(nearCriticalOnly='true')).json()['summary']
        self.assertEqual(neg['negativeFloatCount'], dash['negativeFloatCount'])
        self.assertEqual(zero['zeroFloatCount'], dash['zeroFloatCount'])
        self.assertEqual(near['nearCriticalCount'], dash['nearCriticalCount'])


class FloatTrendApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Float Trend API Test')
        now = timezone.now()
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', totalFloat=32.0)],
            upload_timestamp=now - timedelta(days=60),
        )
        self.v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-02-01',
            activities_json=[_act('A1', totalFloat=24.0)],
            upload_timestamp=now - timedelta(days=30),
        )
        self.v3 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v3.xer', sanitized_filename='v3.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-03-01',
            activities_json=[_act('A1', totalFloat=-6.0)],
            upload_timestamp=now,
        )

    def test_trend_spans_every_version_in_chronological_order(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/float-trend/?activityId=A1')
        self.assertEqual(resp.status_code, 200, resp.content)
        points = resp.json()['points']
        self.assertEqual(len(points), 3)
        self.assertEqual([p['dataDate'] for p in points], ['2026-01-01', '2026-02-01', '2026-03-01'])
        self.assertEqual([p['totalFloat'] for p in points], [32.0, 24.0, -6.0])

    def test_each_point_uses_its_own_data_date_not_today(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/float-trend/?activityId=A1')
        points = resp.json()['points']
        for p in points:
            self.assertIn(p['dataDate'], ('2026-01-01', '2026-02-01', '2026-03-01'))

    def test_missing_activity_id_param_rejected(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/float-trend/')
        self.assertEqual(resp.status_code, 400)

    def test_activity_not_in_a_version_reported_unavailable_not_zero(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/float-trend/?activityId=DOES-NOT-EXIST')
        points = resp.json()['points']
        for p in points:
            self.assertFalse(p['found'])
            self.assertIsNone(p['totalFloat'])
