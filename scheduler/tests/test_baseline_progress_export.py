"""
Baseline vs Forecast Activity Chart — Excel export tests (Final Validation
& Usability phase). The export endpoint reuses baseline_progress.py's
existing build functions — these tests protect the export module's own
formatting rules (no fake zeros, filtered row counts match, working-day
columns honor workingDayCalendarAvailable) rather than re-testing the
underlying engine, which test_baseline_progress.py already covers.
"""

from datetime import date, datetime, timezone as dt_timezone
from io import BytesIO

from django.test import TestCase
from openpyxl import load_workbook

from scheduler.baseline_progress_export import generate_activity_chart_excel
from scheduler.models import Project, ScheduleUpload


def _act(code, name, bs, bf, wbs='Area A', pct=0.0, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': milestone, 'wbs': wbs,
    }
    d.update(extra)
    return d


class GenerateActivityChartExcelTests(TestCase):
    """Direct unit tests of the export module — no HTTP/DB involved."""

    def _rows(self):
        return [
            {'activityId': 'A1', 'activityName': 'Complete Activity', 'wbs': 'Area A',
             'baselineStart': '2026-01-01', 'baselineFinish': '2026-01-10',
             'currentStart': '2026-01-01', 'currentFinish': '2026-01-09',
             'startVarianceDays': 0, 'finishVarianceDays': -1,
             'startVarianceWorkingDays': None, 'finishVarianceWorkingDays': None,
             'workingDayCalendarAvailable': False, 'pctComplete': 100, 'totalFloat': 0, 'status': 'COMPLETE'},
            {'activityId': 'A2', 'activityName': 'Delayed Activity', 'wbs': 'Area B',
             'baselineStart': '2026-01-05', 'baselineFinish': '2026-01-20',
             'currentStart': '2026-01-05', 'currentFinish': '2026-01-27',
             'startVarianceDays': 0, 'finishVarianceDays': 7,
             'startVarianceWorkingDays': 0, 'finishVarianceWorkingDays': 5,
             'workingDayCalendarAvailable': True, 'pctComplete': 40, 'totalFloat': -2, 'status': 'DELAYED_FINISH'},
            {'activityId': 'A3', 'activityName': 'Removed Activity', 'wbs': 'Area B',
             'baselineStart': '2026-01-01', 'baselineFinish': '2026-01-05',
             'currentStart': None, 'currentFinish': None,
             'startVarianceDays': None, 'finishVarianceDays': None,
             'startVarianceWorkingDays': None, 'finishVarianceWorkingDays': None,
             'workingDayCalendarAvailable': False, 'pctComplete': None, 'totalFloat': None, 'status': 'REMOVED_FROM_CURRENT'},
        ]

    def test_workbook_valid_and_row_count_matches_filtered_rows(self):
        rows = self._rows()
        xlsx = generate_activity_chart_excel(rows, date(2026, 2, 1), 'Test Project', 'Current Update', 'Approved Baseline')
        self.assertTrue(xlsx.startswith(b'PK'))
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        header_row_idx = next(i for i, row in enumerate(ws.iter_rows(values_only=True), start=1) if row[0] == 'Activity ID')
        data_rows = list(ws.iter_rows(min_row=header_row_idx + 1, values_only=True))
        data_rows = [r for r in data_rows if r[0]]   # drop any trailing blank rows
        self.assertEqual(len(data_rows), len(rows))

    def test_unavailable_values_not_written_as_fake_zero(self):
        xlsx = generate_activity_chart_excel(self._rows(), date(2026, 2, 1), 'Test Project', 'Current', 'Baseline')
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        row_map = {row[0]: row for row in ws.iter_rows(values_only=True) if row[0] in ('A1', 'A2', 'A3')}
        a3 = row_map['A3']
        # Removed activity: current dates/variance/status genuinely absent —
        # must read 'Unavailable', never 0 or a blank-looking cell.
        self.assertIn('Unavailable', a3)
        self.assertNotIn(0, [v for v in a3 if v != 'Unavailable'])

    def test_working_day_columns_blank_when_calendar_unavailable(self):
        xlsx = generate_activity_chart_excel(self._rows(), date(2026, 2, 1), 'Test Project', 'Current', 'Baseline')
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        header = [c.value for c in next(r for r in ws.iter_rows() if r[0].value == 'Activity ID')]
        wd_finish_col = header.index('Finish Variance (working days)')
        a1_row = next(r for r in ws.iter_rows(values_only=True) if r[0] == 'A1')
        self.assertEqual(a1_row[wd_finish_col], 'Unavailable')   # A1 has no calendar decoded

    def test_working_day_value_present_when_calendar_available(self):
        xlsx = generate_activity_chart_excel(self._rows(), date(2026, 2, 1), 'Test Project', 'Current', 'Baseline')
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        header = [c.value for c in next(r for r in ws.iter_rows() if r[0].value == 'Activity ID')]
        wd_finish_col = header.index('Finish Variance (working days)')
        a2_row = next(r for r in ws.iter_rows(values_only=True) if r[0] == 'A2')
        self.assertEqual(a2_row[wd_finish_col], 5)

    def test_metadata_header_rows_present(self):
        xlsx = generate_activity_chart_excel(self._rows(), date(2026, 2, 1), 'My Project', 'Aug Update', 'Approved Baseline', 'Look-Ahead 4 Weeks')
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        first_col_values = [row[0] for row in ws.iter_rows(min_row=1, max_row=6, values_only=True)]
        self.assertIn('Project: My Project', first_col_values)
        self.assertIn('Current Version: Aug Update', first_col_values)
        self.assertIn('Baseline Version: Approved Baseline', first_col_values)
        self.assertIn('Effective Data Date: 2026-02-01', first_col_values)
        self.assertIn('Scope: Look-Ahead 4 Weeks', first_col_values)

    def test_no_data_date_shows_unavailable_not_crash(self):
        xlsx = generate_activity_chart_excel(self._rows(), None, 'Test Project', 'Current', None)
        wb = load_workbook(BytesIO(xlsx))
        ws = wb.active
        first_col_values = [row[0] for row in ws.iter_rows(min_row=1, max_row=6, values_only=True)]
        self.assertIn('Effective Data Date: Unavailable', first_col_values)
        self.assertIn('Baseline Version: Unavailable — no baseline designated', first_col_values)

    def test_empty_rows_produces_valid_empty_workbook(self):
        xlsx = generate_activity_chart_excel([], date(2026, 2, 1), 'Test Project', 'Current', 'Baseline')
        self.assertTrue(xlsx.startswith(b'PK'))


class ExportEndpointTests(TestCase):
    """GET /api/projects/<id>/baseline-progress/export/ — filtered row
    counts must match what the chart's own filter panel would show."""

    def setUp(self):
        self.project = Project.objects.create(name='Export API Project')
        baseline_acts = [
            _act('A1', 'Area A Activity', '2026-01-01', '2026-01-10', wbs='Area A'),
            _act('A2', 'Area B Activity', '2026-01-05', '2026-01-20', wbs='Area B'),
        ]
        current_acts = [
            _act('A1', 'Area A Activity', '2026-01-01', '2026-01-10', wbs='Area A', pct=100.0, start='2026-01-01', finish='2026-01-09'),
            _act('A2', 'Area B Activity', '2026-01-05', '2026-01-20', wbs='Area B', earlyFinish='2026-01-27'),
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
        self.base = f'/api/projects/{self.project.id}/baseline-progress/export/'

    @staticmethod
    def _data_rows(content):
        wb = load_workbook(BytesIO(content))
        ws = wb.active
        header_row_idx = next(i for i, row in enumerate(ws.iter_rows(values_only=True), start=1) if row[0] == 'Activity ID')
        return [r for r in ws.iter_rows(min_row=header_row_idx + 1, values_only=True) if r[0]]

    def test_export_all_scope_returns_xlsx(self):
        resp = self.client.get(self.base)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertEqual(len(self._data_rows(resp.content)), 2)

    def test_export_filtered_row_count_matches_filter(self):
        resp = self.client.get(self.base, {'wbs': 'Area A'})
        data_rows = self._data_rows(resp.content)
        self.assertEqual(len(data_rows), 1)
        self.assertEqual(data_rows[0][0], 'A1')

    def test_export_lookahead_scope_windows_rows(self):
        resp = self.client.get(self.base, {'scope': 'lookahead', 'lookaheadWeeks': '1'})
        self.assertEqual(resp.status_code, 200)
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active
        first_col_values = [row[0] for row in ws.iter_rows(min_row=1, max_row=6, values_only=True)]
        self.assertTrue(any('Look-Ahead' in str(v) for v in first_col_values))

    def test_export_no_schedule_version_returns_404(self):
        empty = Project.objects.create(name='Empty Export Project')
        resp = self.client.get(f'/api/projects/{empty.id}/baseline-progress/export/')
        self.assertEqual(resp.status_code, 404)

    def test_export_project_not_found_returns_404(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/baseline-progress/export/')
        self.assertEqual(resp.status_code, 404)

    def test_export_invalid_lookahead_weeks_returns_400(self):
        resp = self.client.get(self.base, {'scope': 'lookahead', 'lookaheadWeeks': 'nope'})
        self.assertEqual(resp.status_code, 400)
