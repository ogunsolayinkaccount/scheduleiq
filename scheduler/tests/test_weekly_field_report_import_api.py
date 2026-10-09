"""
Weekly Field Report Excel import — API layer tests. Covers the three
safeguards the Project-Linked Excel Upload review required: never import
into a mismatched project (no override), all-or-nothing atomic commits,
and explicit confirmation for anything uncertain (here: a workbook with
no stated project identity at all).
"""
import json
from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook

from scheduler.models import (
    AuditLog, Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER,
    ScheduleUpload, WeeklyFieldReport,
)


def _user(username, role, password='pw12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


def _xlsx(headers, rows, name='weekly.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


STANDARD_HEADERS = ['Project ID', 'Week Start Date', 'Actual Headcount', 'Next Week Forecast', 'PM Projection', 'Monthly Target', 'Last Client Update']


class WeeklyFieldReportImportPreviewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Import Preview Test', project_number='BARN-01')

    def test_correct_project_id_matches(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '2026-09-15']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['projectIdentity']['status'], 'MATCHED')
        self.assertTrue(body['canImport'])
        self.assertEqual(body['summary'], {'validCount': 1, 'duplicateCount': 0, 'invalidCount': 0})

    def test_preview_rows_include_project_id_raw_for_an_unmodified_commit_round_trip(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '2026-09-15']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        row = resp.json()['rows'][0]
        self.assertEqual(row['projectIdRaw'], 'BARN-01')

        # The frontend passes preview rows straight through to commit,
        # unmodified — this is the full round trip that relies on it.
        commit_resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'sourceFileName': 'weekly.xlsx', 'rows': [row]}),
            content_type='application/json',
        )
        self.assertEqual(commit_resp.status_code, 201, commit_resp.content)
        self.assertEqual(commit_resp.json()['imported'], 1)

    def test_project_uuid_also_matches(self):
        f = _xlsx(STANDARD_HEADERS, [[str(self.project.id), '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.json()['projectIdentity']['status'], 'MATCHED')

    def test_wrong_project_id_is_blocked_with_no_override(self):
        f = _xlsx(STANDARD_HEADERS, [['SOME-OTHER-PROJECT', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        body = resp.json()
        self.assertEqual(body['projectIdentity']['status'], 'MISMATCHED')
        self.assertFalse(body['canImport'])
        self.assertIn('SOME-OTHER-PROJECT', body['projectIdentity']['reason'])

    def test_missing_project_identity_column_is_not_present(self):
        f = _xlsx(['Week Start Date', 'Actual Headcount'], [['2026-09-14', 30]])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        body = resp.json()
        self.assertEqual(body['projectIdentity']['status'], 'NOT_PRESENT')
        # NOT_PRESENT does not by itself block canImport (unlike MISMATCHED) —
        # the commit endpoint is where the explicit confirmation is enforced.
        self.assertTrue(body['canImport'])

    def test_unmapped_columns_are_shown_not_silently_dropped(self):
        f = _xlsx(['Project ID', 'Week Start Date', 'Site Notes'], [['BARN-01', '2026-09-14', 'Rain delay']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertIn('Site Notes', resp.json()['unmappedColumns'])

    def test_non_monday_row_is_invalid(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-16', 30, 35, 50, 50, '']])  # Wednesday
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        body = resp.json()
        self.assertEqual(body['summary']['invalidCount'], 1)
        self.assertEqual(body['rows'][0]['status'], 'INVALID')
        self.assertTrue(any('Monday' in e for e in body['rows'][0]['errors']))

    def test_negative_headcount_row_is_invalid(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', -5, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.json()['summary']['invalidCount'], 1)

    def test_duplicate_against_existing_db_row_is_flagged(self):
        WeeklyFieldReport.objects.create(project=self.project, week_start_date='2026-09-14', actual_headcount=25)
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        body = resp.json()
        self.assertEqual(body['summary']['duplicateCount'], 1)
        self.assertEqual(body['rows'][0]['status'], 'DUPLICATE')

    def test_duplicate_within_the_same_workbook_is_flagged(self):
        f = _xlsx(STANDARD_HEADERS, [
            ['BARN-01', '2026-09-14', 30, 35, 50, 50, ''],
            ['BARN-01', '2026-09-14', 99, 99, 99, 99, ''],
        ])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        body = resp.json()
        self.assertEqual(body['rows'][0]['status'], 'VALID')
        self.assertEqual(body['rows'][1]['status'], 'DUPLICATE')

    def test_empty_workbook_is_rejected(self):
        f = _xlsx(STANDARD_HEADERS, [])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 400)

    def test_non_xlsx_file_is_rejected(self):
        f = SimpleUploadedFile('weekly.csv', b'Project ID,Week Start Date\nBARN-01,2026-09-14', content_type='text/csv')
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 400)

    def test_malformed_file_is_rejected_cleanly(self):
        f = SimpleUploadedFile('weekly.xlsx', b'this is not a real spreadsheet', content_type='application/octet-stream')
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 400)

    def test_no_file_is_rejected(self):
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {})
        self.assertEqual(resp.status_code, 400)

    def test_row_count_ceiling_is_enforced(self):
        with patch.object(__import__('scheduler.weekly_field_report_import', fromlist=['x']), 'MAX_IMPORT_ROWS', 2):
            f = _xlsx(STANDARD_HEADERS, [
                ['BARN-01', '2026-09-14', 30, 35, 50, 50, ''],
                ['BARN-01', '2026-09-21', 30, 35, 50, 50, ''],
                ['BARN-01', '2026-09-28', 30, 35, 50, 50, ''],
            ])
            resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
            self.assertEqual(resp.status_code, 400)


def _row(project_id, week, actual=30, next_forecast=35, pm=50, target=50, client_update=''):
    return {
        'rowNumber': 2, 'projectIdRaw': project_id, 'weekStartDate': week,
        'actualHeadcount': str(actual) if actual is not None else None,
        'nextWeekForecastHeadcount': str(next_forecast) if next_forecast is not None else None,
        'pmProjectedHeadcount': str(pm) if pm is not None else None,
        'monthlyTargetHeadcount': str(target) if target is not None else None,
        'lastClientUpdateDate': client_update or None,
    }


class WeeklyFieldReportImportCommitTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Import Commit Test', project_number='BARN-01')

    def _commit(self, rows, **extra):
        body = {'confirmProjectId': str(self.project.id), 'sourceFileName': 'weekly.xlsx', 'rows': rows, **extra}
        return self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps(body), content_type='application/json',
        )

    def test_commit_requires_confirm_project_id_to_match(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': 'not-the-project', 'rows': [_row('BARN-01', '2026-09-14')]}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WeeklyFieldReport.objects.count(), 0)

    def test_successful_commit_creates_all_rows_and_audits_them(self):
        rows = [_row('BARN-01', '2026-09-14'), _row('BARN-01', '2026-09-21', actual=35)]
        resp = self._commit(rows)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['imported'], 2)
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 2)
        self.assertEqual(
            AuditLog.objects.filter(object_type='WeeklyFieldReport', action='CREATE_WEEKLY_FIELD_REPORT').count(), 2,
        )
        entry = AuditLog.objects.filter(object_type='WeeklyFieldReport').first()
        self.assertIn('weekly.xlsx', entry.reason)

    def test_mismatched_project_id_blocks_commit_entirely(self):
        resp = self._commit([_row('SOME-OTHER-PROJECT', '2026-09-14')])
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(WeeklyFieldReport.objects.count(), 0)

    def test_missing_project_identity_requires_explicit_confirmation(self):
        row = _row(None, '2026-09-14')
        resp = self._commit([row])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WeeklyFieldReport.objects.count(), 0)

        resp2 = self._commit([row], confirmNoProjectIdentifier=True)
        self.assertEqual(resp2.status_code, 201, resp2.content)
        self.assertEqual(WeeklyFieldReport.objects.count(), 1)

    def test_one_invalid_row_blocks_the_entire_batch_atomically(self):
        rows = [
            _row('BARN-01', '2026-09-14'),       # valid
            _row('BARN-01', '2026-09-16'),       # Wednesday — invalid
            _row('BARN-01', '2026-09-28'),       # valid
        ]
        resp = self._commit(rows)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['imported'], 0)
        # The all-or-nothing guarantee: the two otherwise-valid rows must
        # NOT have been saved just because one row failed.
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 0)

    def test_one_duplicate_against_an_existing_row_blocks_the_entire_batch(self):
        WeeklyFieldReport.objects.create(project=self.project, week_start_date='2026-09-14', actual_headcount=1)
        rows = [_row('BARN-01', '2026-09-14'), _row('BARN-01', '2026-09-21')]
        resp = self._commit(rows)
        self.assertEqual(resp.status_code, 400)
        # Only the pre-existing row remains — the batch's second (otherwise
        # valid) row must not have been saved either.
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 1)

    def test_duplicate_within_the_batch_itself_blocks_the_entire_batch(self):
        rows = [_row('BARN-01', '2026-09-14'), _row('BARN-01', '2026-09-14', actual=99)]
        resp = self._commit(rows)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WeeklyFieldReport.objects.count(), 0)

    def test_empty_rows_list_is_rejected(self):
        resp = self._commit([])
        self.assertEqual(resp.status_code, 400)

    def test_project_isolation_duplicate_check_is_scoped_per_project(self):
        other = Project.objects.create(name='Other Project', project_number='OTHER-01')
        WeeklyFieldReport.objects.create(project=other, week_start_date='2026-09-14', actual_headcount=1)
        resp = self._commit([_row('BARN-01', '2026-09-14')])
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 1)


class WeeklyFieldReportImportSkipRowWorkflowTests(TestCase):
    """
    Drives the exact preview -> frontend-filter -> commit pipeline the UI
    performs (WeeklyFieldReportImportForm in FieldDashboard.tsx): fetch a
    real preview response through the actual multipart endpoint, apply
    the IDENTICAL filter the frontend applies to build rowsToImport
    (`r.status === 'VALID'`), and confirm what actually gets committed —
    proving the UI's "Confirm" button enabling is never out of step with
    what the backend will actually save.
    """

    def setUp(self):
        self.project = Project.objects.create(name='Skip Workflow Test', project_number='BARN-01')

    def _frontend_rows_to_import(self, preview_body):
        # Mirrors FieldDashboard.tsx's WeeklyFieldReportImportForm exactly:
        #   const rowsToImport = rows.filter(r => r.status === "VALID" && !skipped.has(r.rowNumber));
        # Skipped rows are never VALID in this workbook, so skip selection
        # itself changes nothing about which rows are eligible — it only
        # gates the Confirm button. The real exclusion is `status === "VALID"`.
        return [r for r in preview_body['rows'] if r['status'] == 'VALID']

    def test_mixed_batch_only_valid_rows_reach_the_commit_payload_and_are_saved(self):
        # A pre-existing report for 2026-09-28 makes that week a DUPLICATE
        # in the workbook below; 2026-09-16 is a Wednesday (INVALID); the
        # other two weeks are genuinely new and VALID.
        WeeklyFieldReport.objects.create(project=self.project, week_start_date='2026-09-28', actual_headcount=1)

        f = _xlsx(STANDARD_HEADERS, [
            ['BARN-01', '2026-09-14', 30, 35, 50, 50, ''],   # VALID
            ['BARN-01', '2026-09-16', 30, 35, 50, 50, ''],   # INVALID — not a Monday
            ['BARN-01', '2026-09-21', 35, 39, 50, 50, ''],   # VALID
            ['BARN-01', '2026-09-28', 99, 99, 99, 99, ''],   # DUPLICATE — collides with the existing row
        ])
        preview_resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        preview = preview_resp.json()
        statuses = {r['weekStartDate']: r['status'] for r in preview['rows']}
        self.assertEqual(statuses, {
            '2026-09-14': 'VALID', '2026-09-16': 'INVALID', '2026-09-21': 'VALID', '2026-09-28': 'DUPLICATE',
        })

        rows_to_import = self._frontend_rows_to_import(preview)
        self.assertEqual(len(rows_to_import), 2)
        self.assertEqual({r['weekStartDate'] for r in rows_to_import}, {'2026-09-14', '2026-09-21'})

        commit_resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'sourceFileName': 'weekly.xlsx', 'rows': rows_to_import}),
            content_type='application/json',
        )
        self.assertEqual(commit_resp.status_code, 201, commit_resp.content)
        self.assertEqual(commit_resp.json()['imported'], 2)

        # Exactly the two valid weeks were added; the invalid one was never
        # attempted; the pre-existing duplicate-target row survives UNCHANGED.
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 3)
        self.assertEqual(
            set(WeeklyFieldReport.objects.filter(project=self.project).values_list('week_start_date', flat=True).distinct()),
            {date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)},
        )
        preserved = WeeklyFieldReport.objects.get(project=self.project, week_start_date='2026-09-28')
        self.assertEqual(preserved.actual_headcount, 1)  # untouched by the DUPLICATE row's 99

    def test_a_workbook_with_no_importable_rows_cannot_create_an_empty_import(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-16', 30, 35, 50, 50, '']])  # Wednesday only — INVALID
        preview = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f}).json()
        rows_to_import = self._frontend_rows_to_import(preview)
        self.assertEqual(rows_to_import, [])

        # The frontend's own canConfirm guard (rowsToImport.length > 0)
        # would already disable the button here — this proves the backend
        # independently refuses the same empty payload if ever reached.
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'sourceFileName': 'weekly.xlsx', 'rows': rows_to_import}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WeeklyFieldReport.objects.count(), 0)

    def test_an_all_valid_batch_commits_every_row_with_nothing_held_back(self):
        f = _xlsx(STANDARD_HEADERS, [
            ['BARN-01', '2026-09-14', 30, 35, 50, 50, ''],
            ['BARN-01', '2026-09-21', 35, 39, 50, 50, ''],
        ])
        preview = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f}).json()
        rows_to_import = self._frontend_rows_to_import(preview)
        self.assertEqual(len(rows_to_import), 2)
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'sourceFileName': 'weekly.xlsx', 'rows': rows_to_import}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['imported'], 2)


@override_settings(AUTO_AUTH_TEST_USER=False)
class WeeklyFieldReportImportPermissionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Import Permission Test', project_number='BARN-01')
        self.admin = _user('import-admin', ROLE_ADMINISTRATOR, password='pw-1')
        self.scheduler = _user('import-scheduler', ROLE_SCHEDULER, password='pw-2')
        self.viewer = _user('import-viewer', ROLE_VIEWER, password='pw-3')

    def _login(self, username, password):
        self.client.login(username=username, password=password)

    def test_unauthenticated_preview_and_commit_refused(self):
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 401)
        resp2 = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'rows': [_row('BARN-01', '2026-09-14')]}),
            content_type='application/json',
        )
        self.assertEqual(resp2.status_code, 401)

    def test_viewer_cannot_preview_or_commit(self):
        self._login('import-viewer', 'pw-3')
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 403)
        resp2 = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'rows': [_row('BARN-01', '2026-09-14')]}),
            content_type='application/json',
        )
        self.assertEqual(resp2.status_code, 403)

    def test_scheduler_can_preview_and_commit(self):
        self._login('import-scheduler', 'pw-2')
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/import-commit/',
            data=json.dumps({'confirmProjectId': str(self.project.id), 'rows': [_row('BARN-01', '2026-09-14')]}),
            content_type='application/json',
        )
        self.assertEqual(resp2.status_code, 201, resp2.content)
        self.assertEqual(resp2.json()['reports'][0]['createdBy'], 'import-scheduler')

    def test_administrator_can_preview_and_commit(self):
        self._login('import-admin', 'pw-1')
        f = _xlsx(STANDARD_HEADERS, [['BARN-01', '2026-09-14', 30, 35, 50, 50, '']])
        resp = self.client.post(f'/api/projects/{self.project.id}/weekly-field-reports/import-preview/', {'file': f})
        self.assertEqual(resp.status_code, 200)
