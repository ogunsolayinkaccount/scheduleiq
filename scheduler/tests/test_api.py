import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from scheduler.models import ActivityCode, ActivityCodeType, Calendar, Project, ScheduleUpload, UDFType
from .fixtures import SAMPLE_XER, make_activity


class ProjectsApiTests(TestCase):
    def test_create_and_list_project(self):
        resp = self.client.post(
            '/api/projects/', data=json.dumps({'name': 'API Test Project'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get('/api/projects/')
        self.assertEqual(resp.status_code, 200)
        names = [p['name'] for p in resp.json()['projects']]
        self.assertIn('API Test Project', names)


class ImportCommitXerTests(TestCase):
    def test_xer_commit_persists_metadata_and_reference_data(self):
        upload = SimpleUploadedFile('sample.xer', SAMPLE_XER.encode('utf-8'))
        resp = self.client.post('/api/import/commit/', data={
            'file': upload, 'projectName': 'XER API Test Project',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['actCount'], 2)
        self.assertIsNotNone(data['scheduleUploadId'])

        su = ScheduleUpload.objects.get(pk=data['scheduleUploadId'])
        self.assertEqual(su.activity_count, 2)
        self.assertEqual(su.relationship_count, 1)
        self.assertEqual(su.data_date.isoformat(), '2026-08-01')
        self.assertEqual(su.import_method, 'xer_parser')
        self.assertEqual(su.calendar_count, 1)

        self.assertEqual(Calendar.objects.filter(schedule_upload=su).count(), 1)
        self.assertEqual(ActivityCodeType.objects.filter(schedule_upload=su).count(), 1)
        self.assertEqual(ActivityCode.objects.filter(code_type__schedule_upload=su).count(), 1)
        self.assertEqual(UDFType.objects.filter(schedule_upload=su).count(), 1)


class VersionsApiTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone

        self.project = Project.objects.create(name='Versions Test Project')
        now = timezone.now()
        # Explicit, distinct timestamps — real uploads are seconds/minutes apart;
        # timezone.now()'s default on three rows created back-to-back in a test
        # can otherwise collide at microsecond resolution and make the
        # "most recent" ordering this test checks genuinely ambiguous.
        ScheduleUpload.objects.create(
            project=self.project, original_filename='baseline.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', activities_json=[],
            upload_timestamp=now - timedelta(days=30),
        )
        ScheduleUpload.objects.create(
            project=self.project, original_filename='update1.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', activities_json=[],
            upload_timestamp=now - timedelta(days=7),
        )
        ScheduleUpload.objects.create(
            project=self.project, original_filename='update2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', activities_json=[],
            upload_timestamp=now,
        )

    def test_versions_list_assigns_roles(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        self.assertEqual(resp.status_code, 200)
        roles = {v['filename']: v['role'] for v in resp.json()['versions']}
        self.assertEqual(roles['baseline.xer'], 'BASELINE')
        self.assertEqual(roles['update2.xer'], 'CURRENT')     # most recent CURRENT_UPDATE
        self.assertEqual(roles['update1.xer'], 'PREVIOUS')

    def test_versions_response_excludes_activities_json(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/')
        body = resp.content.decode('utf-8')
        self.assertNotIn('activities_json', body)


class CompareApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Compare Test Project')
        self.other_project = Project.objects.create(name='Other Project')
        self.previous = ScheduleUpload.objects.create(
            project=self.project, original_filename='prev.xer', file_type='XER',
            activities_json=[make_activity('A1', b_finish='2026-01-10')],
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            activities_json=[make_activity('A1', b_finish='2026-01-20')],
        )
        self.foreign = ScheduleUpload.objects.create(
            project=self.other_project, original_filename='foreign.xer', file_type='XER',
            activities_json=[make_activity('A1')],
        )

    def test_compare_returns_summary_and_changes(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/compare/',
            data=json.dumps({'previousVersionId': str(self.previous.id), 'currentVersionId': str(self.current.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['summary']['movedLater'], 1)
        self.assertEqual(len(body['changes']), 1)
        self.assertIn('summaryNarrative', body)
        self.assertIn('insights', body)

    def test_compare_rejects_cross_project_version(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/compare/',
            data=json.dumps({'previousVersionId': str(self.foreign.id), 'currentVersionId': str(self.current.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_compare_pagination(self):
        many_prev = [make_activity(f'A{i}', b_finish='2026-01-10') for i in range(5)]
        many_curr = [make_activity(f'A{i}', b_finish='2026-01-20') for i in range(5)]
        prev = ScheduleUpload.objects.create(project=self.project, original_filename='p2.xer', file_type='XER', activities_json=many_prev)
        curr = ScheduleUpload.objects.create(project=self.project, original_filename='c2.xer', file_type='XER', activities_json=many_curr)
        resp = self.client.post(
            f'/api/projects/{self.project.id}/compare/',
            data=json.dumps({
                'previousVersionId': str(prev.id), 'currentVersionId': str(curr.id),
                'page': 1, 'pageSize': 2,
            }),
            content_type='application/json',
        )
        body = resp.json()
        self.assertEqual(len(body['changes']), 2)
        self.assertEqual(body['changesPagination']['totalCount'], 5)
        self.assertEqual(body['changesPagination']['totalPages'], 3)


class ProgressCurveApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Progress Curve Test Project')
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            activities_json=[make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0)],
        )

    def test_progress_curve_requires_current_version(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/progress-curve/')
        self.assertEqual(resp.status_code, 400)

    def test_progress_curve_returns_periods(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/progress-curve/',
            {'currentVersionId': str(self.current.id)},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body['periods']), 1)
        self.assertIn('methodologyNote', body)


class DocumentsApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Documents Test Project')

    def test_upload_text_document_and_list(self):
        upload = SimpleUploadedFile('narrative.txt', b'The project is currently on schedule with 3 open RFIs.')
        resp = self.client.post(f'/api/projects/{self.project.id}/documents/', data={
            'file': upload, 'documentType': 'SCHEDULE_NARRATIVE',
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['extractionStatus'], 'SUCCESS')
        self.assertGreater(body['textLength'], 0)

        resp = self.client.get(f'/api/projects/{self.project.id}/documents/')
        docs = resp.json()['documents']
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]['documentType'], 'SCHEDULE_NARRATIVE')

    def test_rejects_unsupported_file_type(self):
        upload = SimpleUploadedFile('data.bin', b'\x00\x01\x02')
        resp = self.client.post(f'/api/projects/{self.project.id}/documents/', data={'file': upload})
        self.assertEqual(resp.status_code, 415)


class ExtractPdfTextTests(TestCase):
    def test_garbage_bytes_reports_failed_status_not_crash(self):
        from io import BytesIO
        from scheduler.parsers import extract_pdf_text
        result = extract_pdf_text(BytesIO(b'not a real pdf'))
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['warnings'])


class RiskApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Risk API Test Project')
        from datetime import date
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            data_date=date(2026, 6, 1),
            activities_json=[
                make_activity('A1', total_float=-5.0, is_critical=True, area='Area C'),
                make_activity('A2', total_float=20.0, is_critical=False, area='Area A'),
            ],
        )

    def test_default_version_is_newest(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/risk/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['versionId'], str(self.version.id))
        self.assertIn('overall', body)

    def test_group_by_area(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/risk/', {'group_by': 'area'})
        body = resp.json()
        keys = {g['key'] for g in body['groups']}
        self.assertEqual(keys, {'Area C', 'Area A'})

    def test_missing_project_returns_404(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/risk/')
        self.assertEqual(resp.status_code, 404)


class MilestonesApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Milestones API Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            activities_json=[
                make_activity('M1', is_milestone=True, dur=0, total_float=-2.0),
                make_activity('M2', is_milestone=True, dur=0, total_float=15.0),
                make_activity('A1'),
            ],
        )

    def test_returns_only_milestones(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/milestones/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['milestoneCount'], 2)

    def test_negative_float_filter(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/milestones/', {'negativeFloat': 'true'})
        body = resp.json()
        self.assertEqual(body['milestoneCount'], 1)
        self.assertEqual(body['milestones'][0]['activityId'], 'M1')
