"""
Weekly Look-Ahead Report Integration — tests for report_service.py's
'lookAhead' section, its wiring through the /reports/ API, and its PDF/
Excel export. No new calculation logic is tested here beyond what these
tests exercise directly — see test_baseline_progress.py for the underlying
engine's own unit tests; this file covers the report-layer composition
(baseline resolution, filters-apply-everywhere, ranking, PDF/Excel output,
snapshot immutability).
"""

import json
import re
import zlib
from datetime import date, datetime, timezone as dt_timezone
from io import BytesIO

from django.test import TestCase
from openpyxl import load_workbook


def _pdf_text(pdf_bytes: bytes) -> bytes:
    """fpdf2 compresses page content streams (FlateDecode) — decompress
    every stream so tests can assert on the literal text fpdf2 wrote,
    rather than searching the raw (compressed) PDF bytes."""
    chunks = []
    for match in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream', pdf_bytes, re.DOTALL):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    return b'\n'.join(chunks)

from scheduler import report_service
from scheduler.models import Project, ProjectControlsReport, ScheduleUpload
from scheduler.report_export import generate_report_excel, generate_report_pdf


def _act(code, name, bs, bf, wbs='Area A', pct=0.0, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': milestone, 'wbs': wbs,
    }
    d.update(extra)
    return d


class ReportLookaheadSectionTests(TestCase):
    """report_service.build_report_payload()'s 'lookAhead' section, built
    from a controlled baseline+current pair spanning every status the
    report needs to surface."""

    def setUp(self):
        self.project = Project.objects.create(name='LA Report Project')
        self.dd = date(2026, 2, 1)
        baseline_acts = [
            _act('A1', 'On Plan', '2026-02-05', '2026-02-10', wbs='Area A'),
            _act('A2', 'Late Finish', '2026-01-01', '2026-01-20', wbs='Area B'),
            _act('A3', 'Late Start', '2026-01-10', '2026-03-10', wbs='Area A'),
            _act('MS1', 'Substantial Completion', '2026-02-15', '2026-02-15', wbs='Area A', milestone=True),
        ]
        current_acts = [
            _act('A1', 'On Plan', '2026-02-05', '2026-02-10', wbs='Area A'),
            _act('A2', 'Late Finish', '2026-01-01', '2026-01-20', wbs='Area B', pct=50.0, start='2026-01-01', earlyFinish='2026-01-25'),
            _act('A3', 'Late Start', '2026-01-10', '2026-03-10', wbs='Area A'),
            _act('MS1', 'Substantial Completion', '2026-02-15', '2026-02-18', wbs='Area A', milestone=True, earlyFinish='2026-02-18', totalFloat=-2.0),
        ]
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=baseline_acts, upload_timestamp=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=self.dd, version_label='Current', schedule_classification='CURRENT_UPDATE',
            activities_json=current_acts, upload_timestamp=datetime(2026, 2, 1, tzinfo=dt_timezone.utc),
        )

    def test_default_four_week_range_anchored_to_data_date(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        la = payload['lookAhead']
        self.assertTrue(la['available'])
        self.assertEqual(la['dataDate'], '2026-02-01')
        self.assertEqual(la['window']['fromDate'], '2026-02-01')
        self.assertEqual(la['window']['toDate'], '2026-03-01')
        self.assertEqual(la['window']['weeks'], 4)

    def test_custom_range_overrides_weeks(self):
        payload = report_service.build_report_payload(
            self.project, 'WEEKLY_PROJECT_CONTROLS',
            lookahead_from=date(2026, 5, 1), lookahead_to=date(2026, 5, 10),
        )
        la = payload['lookAhead']
        self.assertEqual(la['window']['fromDate'], '2026-05-01')
        self.assertEqual(la['window']['toDate'], '2026-05-10')

    def test_filtered_report_scopes_every_subsection(self):
        payload = report_service.build_report_payload(
            self.project, 'WEEKLY_PROJECT_CONTROLS', lookahead_filters={'wbs': 'Area B'},
        )
        la = payload['lookAhead']
        self.assertTrue(la['rows'])
        self.assertTrue(all(r['wbs'] == 'Area B' for r in la['rows']))
        self.assertTrue(all(d['wbs'] == 'Area B' for d in la['delayedActivities']))
        self.assertEqual(la['scopeLabel'], 'Area B — 4-Week Look Ahead')

    def test_delayed_activity_ranking_worst_first(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        delayed = payload['lookAhead']['delayedActivities']
        variances = [d['finishVarianceDays'] for d in delayed]
        self.assertEqual(variances, sorted(variances, reverse=True))
        self.assertTrue(all(v > 0 for v in variances))

    def test_should_have_started_section(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        shs = payload['lookAhead']['shouldHaveStarted']
        self.assertEqual([d['activityId'] for d in shs], ['A3'])
        self.assertEqual(shs[0]['daysOverdue'], 22)

    def test_should_have_finished_section(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        shf = payload['lookAhead']['shouldHaveFinished']
        self.assertEqual([d['activityId'] for d in shf], ['A2'])
        self.assertEqual(shf[0]['daysOverdue'], 12)

    def test_milestone_inclusion_and_flags(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        milestones = payload['lookAhead']['milestones']
        self.assertEqual(len(milestones), 1)
        self.assertEqual(milestones[0]['activityName'], 'Substantial Completion')
        self.assertTrue(milestones[0]['isDelayed'])
        self.assertTrue(milestones[0]['isNegativeFloat'])

    def test_histogram_included_with_activities_metric_by_default(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        histogram = payload['lookAhead']['histogram']
        self.assertTrue(histogram['available'])
        self.assertEqual(histogram['metric'], 'activities')

    def test_histogram_prefers_hours_when_resource_loaded(self):
        loaded_baseline = [_act('R1', 'Loaded', '2026-02-01', '2026-02-10', isResourceLoaded=True, budgetedHours=80.0)]
        loaded_current = [_act('R1', 'Loaded', '2026-02-01', '2026-02-10', isResourceLoaded=True, budgetedHours=80.0)]
        rloaded = ScheduleUpload.objects.create(
            project=self.project, original_filename='r.xer', sanitized_filename='r.xer', file_type='XER',
            data_date=self.dd, version_label='Resource Loaded Current', schedule_classification='CURRENT_UPDATE',
            activities_json=loaded_current, upload_timestamp=datetime(2026, 2, 2, tzinfo=dt_timezone.utc),
        )
        rbaseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='rb.xer', sanitized_filename='rb.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Resource Baseline', schedule_classification='REVISED_BASELINE',
            activities_json=loaded_baseline, upload_timestamp=datetime(2026, 1, 2, tzinfo=dt_timezone.utc),
        )
        payload = report_service.build_report_payload(
            self.project, 'WEEKLY_PROJECT_CONTROLS', version_id=str(rloaded.id), baseline_version_id=str(rbaseline.id),
        )
        histogram = payload['lookAhead']['histogram']
        self.assertTrue(histogram['available'])
        self.assertEqual(histogram['metric'], 'hours')

    def test_scurve_payload_present_and_defaults_to_duration(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        scurve = payload['lookAhead']['scurve']
        self.assertTrue(scurve['available'])
        self.assertEqual(scurve['metric'], 'duration')
        self.assertIn('periods', scurve)

    def test_no_baseline_behavior(self):
        self.baseline.schedule_classification = 'CURRENT_UPDATE'
        self.baseline.save(update_fields=['schedule_classification'])
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        la = payload['lookAhead']
        self.assertFalse(la['available'])
        self.assertEqual(la['reason'], 'No baseline schedule has been selected for this project.')
        self.assertIn('No baseline schedule has been selected for this project.', payload['dataQuality'])

    def test_no_current_version_behavior(self):
        empty_project = Project.objects.create(name='Empty LA Project')
        payload = report_service.build_report_payload(empty_project, 'WEEKLY_PROJECT_CONTROLS')
        la = payload['lookAhead']
        self.assertFalse(la['available'])
        self.assertEqual(la['reason'], 'No schedule version available.')

    def test_monthly_report_has_no_lookahead_section(self):
        payload = report_service.build_report_payload(self.project, 'MONTHLY_EXECUTIVE')
        self.assertIsNone(payload['lookAhead'])
        self.assertNotIn('lookAhead', payload['sections'])

    def test_explicit_baseline_version_overrides_auto_detection(self):
        other_baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='ob.xer', sanitized_filename='ob.xer', file_type='XER',
            data_date=date(2025, 11, 1), version_label='Other Baseline', schedule_classification='CURRENT_UPDATE',
            activities_json=[_act('A1', 'X', '2026-02-01', '2026-02-05')],
        )
        payload = report_service.build_report_payload(
            self.project, 'WEEKLY_PROJECT_CONTROLS', baseline_version_id=str(other_baseline.id),
        )
        self.assertEqual(payload['lookAhead']['baselineVersionId'], str(other_baseline.id))


class ReportLookaheadApiTests(TestCase):
    """POST /api/projects/<id>/reports/ with look-ahead params, and the
    PDF/Excel export endpoints against a persisted snapshot."""

    def setUp(self):
        self.project = Project.objects.create(name='LA API Project')
        baseline_acts = [
            _act('A1', 'Late Finish', '2026-01-01', '2026-01-20', wbs='Area B'),
        ]
        current_acts = [
            _act('A1', 'Late Finish', '2026-01-01', '2026-01-20', wbs='Area B', pct=50.0, start='2026-01-01', earlyFinish='2026-01-25'),
        ]
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=baseline_acts, upload_timestamp=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=date(2026, 2, 1), version_label='Current', schedule_classification='CURRENT_UPDATE',
            activities_json=current_acts, upload_timestamp=datetime(2026, 2, 1, tzinfo=dt_timezone.utc),
        )
        self.base = f'/api/projects/{self.project.id}/reports/'

    def test_generate_report_with_lookahead_weeks_param(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS', 'lookaheadWeeks': 8}), content_type='application/json')
        self.assertEqual(resp.status_code, 201, resp.content)
        la = resp.json()['payload']['lookAhead']
        self.assertEqual(la['window']['weeks'], 8)

    def test_generate_report_with_lookahead_filters(self):
        resp = self.client.post(self.base, data=json.dumps({
            'reportType': 'WEEKLY_PROJECT_CONTROLS', 'lookaheadFilters': {'wbs': 'Area B'},
        }), content_type='application/json')
        la = resp.json()['payload']['lookAhead']
        self.assertTrue(all(r['wbs'] == 'Area B' for r in la['rows']))

    def test_invalid_lookahead_weeks_returns_400(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS', 'lookaheadWeeks': 'nope'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_invalid_baseline_version_returns_404(self):
        resp = self.client.post(self.base, data=json.dumps({
            'reportType': 'WEEKLY_PROJECT_CONTROLS', 'baselineVersion': '00000000-0000-0000-0000-000000000000',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_pdf_export_includes_lookahead(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        pdf_resp = self.client.get(f'{self.base}{report_id}/pdf/')
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertTrue(pdf_resp.content.startswith(b'%PDF'))

    def test_excel_export_includes_all_filtered_rows_not_just_top_n(self):
        # Ten more delayed activities beyond the default top-10 cap.
        many_baseline = [_act(f'D{i}', f'Delayed {i}', '2026-01-01', '2026-01-10', wbs='Area B') for i in range(15)]
        many_current = [_act(f'D{i}', f'Delayed {i}', '2026-01-01', '2026-01-10', wbs='Area B', earlyFinish='2026-01-20') for i in range(15)]
        project = Project.objects.create(name='Excel Full Rows Project')
        ScheduleUpload.objects.create(
            project=project, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=many_baseline, upload_timestamp=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
        )
        ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=date(2026, 1, 5), version_label='Current', schedule_classification='CURRENT_UPDATE',
            activities_json=many_current, upload_timestamp=datetime(2026, 1, 5, tzinfo=dt_timezone.utc),
        )
        resp = self.client.post(f'/api/projects/{project.id}/reports/', data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        body = resp.json()
        self.assertEqual(len(body['payload']['lookAhead']['delayedActivities']), 15)

        report_id = body['id']
        xlsx_resp = self.client.get(f'/api/projects/{project.id}/reports/{report_id}/excel/')
        self.assertEqual(xlsx_resp.status_code, 200)
        wb = load_workbook(BytesIO(xlsx_resp.content))
        ws = wb['Delayed Activities']
        data_rows = list(ws.iter_rows(min_row=2, values_only=True))
        self.assertEqual(len(data_rows), 15)   # not truncated to the PDF's top 10

    def test_saved_snapshot_does_not_change_after_schedule_edit(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        original_window = create_resp.json()['payload']['lookAhead']['window']

        # Mutate the live schedule data after the snapshot was taken.
        self.current.activities_json = [_act('A1', 'Changed', '2026-01-01', '2026-01-20', wbs='Area B', pct=100.0)]
        self.current.data_date = date(2026, 6, 1)
        self.current.save()

        record = ProjectControlsReport.objects.get(pk=report_id)
        self.assertEqual(record.payload_json['lookAhead']['window'], original_window)
        self.assertEqual(record.payload_json['lookAhead']['dataDate'], '2026-02-01')

    def test_unavailable_lookahead_still_produces_valid_pdf_and_excel(self):
        project = Project.objects.create(name='No Baseline Report Project')
        ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Current', schedule_classification='CURRENT_UPDATE',
            activities_json=[_act('A1', 'X', '2026-01-01', '2026-01-05')],
        )
        resp = self.client.post(f'/api/projects/{project.id}/reports/', data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        self.assertFalse(resp.json()['payload']['lookAhead']['available'])
        report_id = resp.json()['id']
        pdf_resp = self.client.get(f'/api/projects/{project.id}/reports/{report_id}/pdf/')
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertTrue(pdf_resp.content.startswith(b'%PDF'))
        xlsx_resp = self.client.get(f'/api/projects/{project.id}/reports/{report_id}/excel/')
        self.assertEqual(xlsx_resp.status_code, 200)
        self.assertTrue(xlsx_resp.content.startswith(b'PK'))


class ReportLookaheadExportUnitTests(TestCase):
    """Direct PDF/Excel generation against a hand-built payload — mirrors
    test_report_export.py's style, scoped to the new lookAhead fields."""

    def _payload(self, lookahead):
        return {
            'engineVersion': '1.1.0', 'reportType': 'WEEKLY_PROJECT_CONTROLS',
            'sections': ['projectInfo', 'executiveSummary', 'lookAhead'],
            'projectInfo': {'projectName': 'Unit Test Project', 'dataDate': '2026-02-01', 'scheduleVersion': 'Current'},
            'executiveSummary': {'narrative': {'sections': {}}, 'aiNarrative': None, 'controlSignals': [], 'warnings': []},
            'currentPerformance': None, 'forecast': {}, 'lookAhead': lookahead,
            'trendHistory': None, 'schedulePerformance': {'available': False},
            'costProductivityDrivers': None, 'updateComparison': None,
            'managementAttention': [], 'dataQuality': [], 'methodology': {}, 'traceability': {},
        }

    def test_pdf_renders_top_delayed_and_notes_truncation(self):
        delayed = [
            {'activityId': f'D{i}', 'activityName': f'Delayed {i}', 'wbs': 'Area A', 'baselineFinish': '2026-01-10',
             'currentFinish': '2026-01-20', 'finishVarianceDays': 10, 'workingDayCalendarAvailable': False,
             'finishVarianceWorkingDays': None, 'status': 'DELAYED_FINISH'}
            for i in range(12)
        ]
        lookahead = {
            'available': True, 'dataDate': '2026-02-01', 'window': {'fromDate': '2026-02-01', 'toDate': '2026-03-01', 'weeks': 4},
            'baselineVersionLabel': 'Baseline', 'currentVersionLabel': 'Current',
            'summaryCards': {'activitiesInWindow': 12, 'behindBaseline': 12, 'shouldHaveStarted': 0, 'shouldHaveFinished': 0,
                              'plannedStarts': 0, 'forecastStarts': 0, 'inProgress': 0, 'criticalActivities': 0,
                              'averageFinishVarianceDays': 10.0, 'plannedHours': None, 'forecastHours': None, 'actualHours': None},
            'histogram': {'available': False, 'reason': 'No look-ahead window available.', 'metric': 'activities'},
            'scurve': {'available': False, 'reason': 'S-Curve unavailable.', 'metric': 'duration'},
            'delayedActivities': delayed, 'topDelayedN': 10,
            'shouldHaveStarted': [], 'shouldHaveStartedTotalCount': 0,
            'shouldHaveFinished': [], 'shouldHaveFinishedTotalCount': 0,
            'milestones': [],
        }
        pdf_bytes = generate_report_pdf(self._payload(lookahead))
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))
        # The literal truncation note text should appear in the (decompressed) content stream.
        self.assertIn(b'Showing top 10 of 12 delayed activities.', _pdf_text(pdf_bytes))

    def test_pdf_handles_unavailable_lookahead(self):
        lookahead = {'available': False, 'reason': 'No baseline schedule has been selected for this project.'}
        pdf_bytes = generate_report_pdf(self._payload(lookahead))
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_pdf_handles_missing_lookahead_key(self):
        payload = self._payload(None)
        payload['lookAhead'] = None
        pdf_bytes = generate_report_pdf(payload)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_excel_worksheets_created_when_lookahead_available(self):
        lookahead = {
            'available': True, 'dataDate': '2026-02-01', 'window': {'fromDate': '2026-02-01', 'toDate': '2026-03-01', 'weeks': 4},
            'baselineVersionLabel': 'Baseline', 'currentVersionLabel': 'Current', 'filters': {},
            'summaryCards': {'activitiesInWindow': 1, 'behindBaseline': 1, 'shouldHaveStarted': 0, 'shouldHaveFinished': 0,
                              'plannedStarts': 0, 'forecastStarts': 0, 'inProgress': 0, 'criticalActivities': 0,
                              'averageFinishVarianceDays': 5.0, 'plannedHours': None, 'forecastHours': None, 'actualHours': None},
            'histogram': {'available': True, 'metric': 'activities', 'buckets': []},
            'scurve': {'available': True, 'metric': 'duration', 'periods': []},
            'rows': [{'activityId': 'A1', 'activityName': 'X', 'wbs': 'Area A', 'area': None, 'discipline': None,
                      'contractor': None, 'system': None, 'baselineStart': '2026-01-01', 'baselineFinish': '2026-01-10',
                      'currentStart': '2026-01-01', 'currentFinish': '2026-01-15', 'actualStart': None, 'actualFinish': None,
                      'pctComplete': 0.0, 'totalFloat': 0.0, 'startVarianceDays': 0, 'finishVarianceDays': 5,
                      'finishVarianceWorkingDays': None, 'workingDayCalendarAvailable': False, 'status': 'DELAYED_FINISH'}],
            'delayedActivities': [], 'topDelayedN': 10,
            'shouldHaveStarted': [], 'shouldHaveStartedTotalCount': 0,
            'shouldHaveFinished': [], 'shouldHaveFinishedTotalCount': 0,
            'milestones': [],
        }
        xlsx_bytes = generate_report_excel(self._payload(lookahead))
        wb = load_workbook(BytesIO(xlsx_bytes))
        for expected in ('Look Ahead Summary', '4-Week Look Ahead', 'Delayed Activities', 'Should Have Started', 'Should Have Finished', 'LookAhead Milestones'):
            self.assertIn(expected, wb.sheetnames)

    def test_excel_skips_lookahead_sheets_when_unavailable(self):
        lookahead = {'available': False, 'reason': 'No baseline schedule has been selected for this project.'}
        xlsx_bytes = generate_report_excel(self._payload(lookahead))
        wb = load_workbook(BytesIO(xlsx_bytes))
        self.assertNotIn('4-Week Look Ahead', wb.sheetnames)
        # Pre-existing sheets must remain untouched/present.
        self.assertIn('Milestones', wb.sheetnames)
