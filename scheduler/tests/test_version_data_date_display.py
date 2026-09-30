import json
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.test import TestCase

from scheduler.models import Project, ScheduleUpload

# CURRENT/PREVIOUS/BASELINE role assignment (_assign_version_roles in
# views.py) is ordered by upload_timestamp, NOT by Data Date — a version
# uploaded later is CURRENT even if its Data Date is earlier than another
# version's. This is pre-existing, documented behavior (see views.py's
# _assign_version_roles docstring) that this phase deliberately leaves
# unchanged (Phase 8: "do not automatically change CURRENT/PREVIOUS
# inference solely because a user edits the Data Date"). Tests pin an
# explicit upload_timestamp per version rather than relying on wall-clock
# creation order, which is unreliable at Windows' clock granularity when
# several rows are created within the same test.
_BASE_TS = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
_upload_seq = iter(range(100000))   # guarantees strictly-increasing upload_timestamp per call, in call order


def _make_version(project, data_date, classification='CURRENT_UPDATE', **extra):
    extra.setdefault('upload_timestamp', _BASE_TS + timedelta(minutes=next(_upload_seq)))
    return ScheduleUpload.objects.create(
        project=project, original_filename=f'{data_date}.xer', sanitized_filename=f'{data_date}.xer',
        file_type='XER', data_date=data_date, schedule_classification=classification,
        version_label=f'{data_date} Update', activities_json=[], **extra,
    )


class VersionListDataDateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Multi-Version Display Test')

    def test_each_version_returns_its_own_data_date(self):
        v1 = _make_version(self.project, date(2026, 8, 11))
        v2 = _make_version(self.project, date(2026, 8, 18))
        v3 = _make_version(self.project, date(2026, 8, 25))
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        self.assertEqual(resp.status_code, 200)
        by_id = {v['id']: v for v in resp.json()['versions']}
        self.assertEqual(by_id[str(v1.id)]['dataDate'], '2026-08-11')
        self.assertEqual(by_id[str(v2.id)]['dataDate'], '2026-08-18')
        self.assertEqual(by_id[str(v3.id)]['dataDate'], '2026-08-25')

    def test_current_previous_baseline_show_respective_dates(self):
        baseline = _make_version(self.project, date(2026, 1, 1), classification='APPROVED_BASELINE')
        v1 = _make_version(self.project, date(2026, 8, 11))
        v2 = _make_version(self.project, date(2026, 8, 18))
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        by_role = {v['role']: v for v in resp.json()['versions']}
        self.assertEqual(by_role['BASELINE']['dataDate'], '2026-01-01')
        self.assertEqual(by_role['CURRENT']['dataDate'], '2026-08-18')   # most recent CURRENT_UPDATE
        self.assertEqual(by_role['PREVIOUS']['dataDate'], '2026-08-11')

    def test_unavailable_date_returns_null_not_fabricated(self):
        _make_version(self.project, None)
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        self.assertIsNone(resp.json()['versions'][0]['dataDate'])

    def test_overridden_version_returns_effective_and_source_separately(self):
        v = _make_version(
            self.project, date(2026, 8, 18),
            source_data_date=date(2026, 8, 17), data_date_overridden=True, data_date_source='USER_ENTERED',
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        row = resp.json()['versions'][0]
        self.assertEqual(row['dataDate'], '2026-08-18')       # effective — this is what's displayed
        self.assertEqual(row['sourceDataDate'], '2026-08-17')  # preserved for traceability
        self.assertTrue(row['dataDateOverridden'])

    def test_non_overridden_version_has_matching_source_and_effective(self):
        _make_version(self.project, date(2026, 8, 25), source_data_date=date(2026, 8, 25))
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        row = resp.json()['versions'][0]
        self.assertEqual(row['dataDate'], row['sourceDataDate'])
        self.assertFalse(row['dataDateOverridden'])

    def test_changing_one_version_does_not_affect_another(self):
        v1 = _make_version(self.project, date(2026, 8, 11))
        v2 = _make_version(self.project, date(2026, 8, 18))
        self.client.patch(
            f'/api/projects/{self.project.id}/versions/{v1.id}/',
            data=json.dumps({'dataDate': '2026-09-01'}), content_type='application/json',
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        by_id = {v['id']: v for v in resp.json()['versions']}
        self.assertEqual(by_id[str(v1.id)]['dataDate'], '2026-09-01')
        self.assertEqual(by_id[str(v2.id)]['dataDate'], '2026-08-18')   # untouched

    def test_patch_then_list_reflects_new_date_immediately(self):
        v = _make_version(self.project, date(2026, 8, 18))
        self.client.patch(
            f'/api/projects/{self.project.id}/versions/{v.id}/',
            data=json.dumps({'dataDate': '2026-08-19'}), content_type='application/json',
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        row = next(r for r in resp.json()['versions'] if r['id'] == str(v.id))
        self.assertEqual(row['dataDate'], '2026-08-19')
        self.assertTrue(row['dataDateOverridden'])

    def test_patch_response_role_matches_full_project_context_not_current(self):
        """Regression guard: the PATCH response's own `role` field must be
        computed against the FULL project version set, not a single-element
        list (which always resolves to CURRENT regardless of the version's
        true position) — this bit both PATCH branches once already."""
        v1 = _make_version(self.project, date(2026, 8, 11))   # uploaded first
        v2 = _make_version(self.project, date(2026, 8, 18))   # uploaded second -> CURRENT
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{v1.id}/',
            data=json.dumps({'dataDate': '2026-08-12'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['role'], 'PREVIOUS')   # v1 was uploaded before v2 -> stays PREVIOUS
        # v2, untouched, must still independently report CURRENT.
        list_resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        by_id = {v['id']: v for v in list_resp.json()['versions']}
        self.assertEqual(by_id[str(v2.id)]['role'], 'CURRENT')

    def test_restore_source_date_patch_response_role_correct(self):
        v1 = _make_version(self.project, date(2026, 8, 11), source_data_date=date(2026, 8, 10), data_date_overridden=True)
        v2 = _make_version(self.project, date(2026, 8, 18))
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{v1.id}/',
            data=json.dumps({'restoreSourceDate': True}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['role'], 'PREVIOUS')

    def test_compare_endpoint_versions_include_traceability_fields(self):
        v1 = _make_version(self.project, date(2026, 8, 11))
        v2 = _make_version(
            self.project, date(2026, 8, 18),
            source_data_date=date(2026, 8, 17), data_date_overridden=True,
        )
        resp = self.client.post(
            f'/api/projects/{self.project.id}/compare/',
            data=json.dumps({'previousVersionId': str(v1.id), 'currentVersionId': str(v2.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['currentVersion']['dataDate'], '2026-08-18')
        self.assertEqual(body['currentVersion']['sourceDataDate'], '2026-08-17')
        self.assertTrue(body['currentVersion']['dataDateOverridden'])
        self.assertEqual(body['previousVersion']['dataDate'], '2026-08-11')
        self.assertFalse(body['previousVersion']['dataDateOverridden'])

    def test_risk_endpoint_scoped_to_correct_version(self):
        v1 = _make_version(self.project, date(2026, 8, 11))
        v2 = _make_version(self.project, date(2026, 8, 18))
        resp = self.client.get(f'/api/projects/{self.project.id}/risk/?version={v1.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['dataDate'], '2026-08-11')
