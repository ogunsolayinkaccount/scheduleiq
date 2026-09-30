import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from scheduler.models import ScheduleUpload
from .fixtures import SAMPLE_XER


def _csv_with_data_date(data_date_value='2026-08-25'):
    return (
        f'Activity ID,Activity Name,Start,Finish,Data Date\n'
        f'A1000,Mobilize,2026-01-05,2026-01-10,{data_date_value}\n'
        f'A1010,Excavate,2026-01-11,2026-01-20,{data_date_value}\n'
    ).encode('utf-8')


def _csv_without_data_date():
    return (
        b'Activity ID,Activity Name,Start,Finish\n'
        b'A1000,Mobilize,2026-01-05,2026-01-10\n'
    )


class ImportPreviewDataDateTests(TestCase):
    def test_xer_preview_shows_authoritative_detection(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertEqual(resp.status_code, 200, resp.content)
        detection = resp.json()['dataDateDetection']
        self.assertEqual(detection['detectedDataDate'], '2026-08-01')
        self.assertEqual(detection['source'], 'P6_XER_PROJECT')
        self.assertEqual(detection['confidence'], 'authoritative')

    def test_csv_preview_detects_explicit_data_date_column(self):
        upload = SimpleUploadedFile('sched.csv', _csv_with_data_date())
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertEqual(resp.status_code, 200, resp.content)
        detection = resp.json()['dataDateDetection']
        self.assertEqual(detection['detectedDataDate'], '2026-08-25')
        self.assertEqual(detection['source'], 'CSV_METADATA')

    def test_csv_preview_with_no_data_date_is_unavailable(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertEqual(resp.status_code, 200, resp.content)
        detection = resp.json()['dataDateDetection']
        self.assertIsNone(detection['detectedDataDate'])
        self.assertIsNone(detection['source'])


class ImportCommitDataDateTests(TestCase):
    def test_xer_commit_auto_accepts_detected_date(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test 1'})
        self.assertEqual(resp.status_code, 200, resp.content)
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.data_date.isoformat(), '2026-08-01')
        self.assertEqual(su.source_data_date.isoformat(), '2026-08-01')
        self.assertFalse(su.data_date_overridden)
        self.assertEqual(su.data_date_source, 'P6_XER_PROJECT')
        self.assertEqual(su.data_date_confidence, 'authoritative')

    def test_commit_response_includes_effective_data_date_for_toolbar(self):
        """The commit response itself must carry the effective Data Date so
        the frontend toolbar can update immediately without a follow-up
        request — see App.tsx's handleImportConfirm."""
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test Toolbar 1'})
        body = resp.json()
        self.assertEqual(body['dataDate'], '2026-08-01')
        self.assertEqual(body['sourceDataDate'], '2026-08-01')
        self.assertFalse(body['dataDateOverridden'])

    def test_commit_response_reflects_override_in_toolbar_fields(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'DD Test Toolbar 2', 'dataDate': '2026-08-05',
        })
        body = resp.json()
        self.assertEqual(body['dataDate'], '2026-08-05')
        self.assertEqual(body['sourceDataDate'], '2026-08-01')
        self.assertTrue(body['dataDateOverridden'])

    def test_commit_response_data_date_null_when_undetected_and_unentered(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test Toolbar 3'})
        body = resp.json()
        self.assertIsNone(body['dataDate'])
        self.assertIsNone(body['sourceDataDate'])
        self.assertFalse(body['dataDateOverridden'])

    def test_xer_commit_with_matching_user_date_not_flagged_overridden(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'DD Test 2', 'dataDate': '2026-08-01',
        })
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertFalse(su.data_date_overridden)
        self.assertEqual(su.data_date_source, 'P6_XER_PROJECT')

    def test_xer_commit_with_overridden_date(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'DD Test 3', 'dataDate': '2026-08-02',
        })
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.data_date.isoformat(), '2026-08-02')
        self.assertEqual(su.source_data_date.isoformat(), '2026-08-01')   # immutable original
        self.assertTrue(su.data_date_overridden)
        self.assertEqual(su.data_date_source, 'USER_ENTERED')

    def test_csv_commit_user_entered_date_when_none_detected(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'DD Test 4', 'dataDate': '2026-08-25',
        })
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.data_date.isoformat(), '2026-08-25')
        self.assertIsNone(su.source_data_date)
        self.assertTrue(su.data_date_overridden)
        self.assertEqual(su.data_date_source, 'USER_ENTERED')

    def test_csv_commit_detected_date_auto_accepted(self):
        upload = SimpleUploadedFile('sched.csv', _csv_with_data_date('2026-08-25'))
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test 5'})
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.data_date.isoformat(), '2026-08-25')
        self.assertFalse(su.data_date_overridden)
        self.assertEqual(su.data_date_source, 'CSV_METADATA')

    def test_no_date_detected_and_none_entered_stays_null(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test 6'})
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertIsNone(su.data_date)
        self.assertIsNone(su.source_data_date)
        self.assertFalse(su.data_date_overridden)

    def test_upload_timestamp_never_substituted_for_data_date(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Test 7'})
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertIsNone(su.data_date)
        self.assertNotEqual(su.data_date, su.upload_timestamp.date())

    def test_invalid_data_date_returns_400(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'DD Test 8', 'dataDate': 'not-a-date',
        })
        self.assertEqual(resp.status_code, 400)


class PostImportDataDateOverrideTests(TestCase):
    def setUp(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Patch Test'})
        body = resp.json()
        self.project_id = body['projectId']
        self.version_id = body['scheduleUploadId']

    def test_patch_overrides_effective_date_and_preserves_source(self):
        resp = self.client.patch(
            f'/api/projects/{self.project_id}/versions/{self.version_id}/',
            data=json.dumps({'dataDate': '2026-08-05'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        su = ScheduleUpload.objects.get(pk=self.version_id)
        self.assertEqual(su.data_date.isoformat(), '2026-08-05')
        self.assertEqual(su.source_data_date.isoformat(), '2026-08-01')
        self.assertTrue(su.data_date_overridden)

    def test_restore_source_data_date(self):
        self.client.patch(
            f'/api/projects/{self.project_id}/versions/{self.version_id}/',
            data=json.dumps({'dataDate': '2026-08-05'}), content_type='application/json',
        )
        resp = self.client.patch(
            f'/api/projects/{self.project_id}/versions/{self.version_id}/',
            data=json.dumps({'restoreSourceDate': True}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        su = ScheduleUpload.objects.get(pk=self.version_id)
        self.assertEqual(su.data_date.isoformat(), '2026-08-01')
        self.assertFalse(su.data_date_overridden)

    def test_restore_without_source_date_returns_400(self):
        upload = SimpleUploadedFile('sched.csv', _csv_without_data_date())
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectName': 'DD Patch No Source'})
        body = resp.json()
        resp2 = self.client.patch(
            f'/api/projects/{body["projectId"]}/versions/{body["scheduleUploadId"]}/',
            data=json.dumps({'restoreSourceDate': True}), content_type='application/json',
        )
        self.assertEqual(resp2.status_code, 400)

    def test_patch_does_not_modify_activities(self):
        su_before = ScheduleUpload.objects.get(pk=self.version_id)
        act_count_before = len(su_before.activities_json)
        self.client.patch(
            f'/api/projects/{self.project_id}/versions/{self.version_id}/',
            data=json.dumps({'dataDate': '2026-08-05'}), content_type='application/json',
        )
        su_after = ScheduleUpload.objects.get(pk=self.version_id)
        self.assertEqual(len(su_after.activities_json), act_count_before)

    def test_downstream_cost_summary_uses_effective_date(self):
        self.client.patch(
            f'/api/projects/{self.project_id}/versions/{self.version_id}/',
            data=json.dumps({'dataDate': '2026-08-05'}), content_type='application/json',
        )
        resp = self.client.get(f'/api/projects/{self.project_id}/cost-summary/?version={self.version_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['dataDate'], '2026-08-05')
