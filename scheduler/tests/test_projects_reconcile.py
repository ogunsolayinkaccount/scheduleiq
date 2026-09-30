"""
Legacy "Sync Intelligence" reconcile: READ-ONLY. Absence from the main
application ("not loaded in this browser") must never authorize deletion.
"""
import json

from django.test import TestCase

from scheduler.models import Project, ScheduleUpload


def _proj(name, n_versions=1, labels=None):
    p = Project.objects.create(name=name)
    vs = []
    for i in range(n_versions):
        label = (labels or [f'{name} v{i}'])[i]
        vs.append(ScheduleUpload.objects.create(
            project=p, original_filename=f'{label}.xer', sanitized_filename=f'{label}.xer',
            file_type='XER', version_label=label, activities_json=[],
        ))
    return p, vs


class ReconcileTests(TestCase):
    def post(self, files, **extra):
        return self.client.post(
            '/api/projects/reconcile/', data=json.dumps({'mainAppFiles': files, **extra}),
            content_type='application/json',
        )

    def setUp(self):
        self.keep, (self.keep_v,) = _proj('Barn Keep')
        self.dup, (self.dup_v,) = _proj('Barn Keep')            # same name, not the file the app holds
        self.validation, _ = _proj('AWP2025 Risk Validation')
        self.linked_project_only, _ = _proj('Id Match Only')
        self.legacy_named, _ = _proj('Legacy Unlinked File')
        self.files = [
            {'projectId': str(self.keep.id), 'scheduleUploadId': str(self.keep_v.id), 'name': 'Barn Keep'},
            {'projectId': str(self.linked_project_only.id), 'scheduleUploadId': 'some-other-version-id', 'name': 'x'},
            {'name': 'Legacy Unlinked File.xer'},
        ]

    def by_id(self, resp):
        return {p['projectId']: p for p in resp.json()['projects']}

    def test_classification_never_uses_a_delete_label(self):
        resp = self.post(self.files)
        self.assertEqual(resp.status_code, 200)
        c = self.by_id(resp)
        self.assertEqual(c[str(self.keep.id)]['classification'], 'KEEP')
        self.assertEqual(c[str(self.dup.id)]['classification'], 'NOT_LOADED')
        self.assertEqual(c[str(self.validation.id)]['classification'], 'NOT_LOADED')
        self.assertEqual(c[str(self.linked_project_only.id)]['classification'], 'REVIEW')
        self.assertEqual(c[str(self.legacy_named.id)]['classification'], 'REVIEW')
        for p in resp.json()['projects']:
            self.assertNotIn('DELETE', p['classification'])
            self.assertTrue(all('DELETE' not in v['classification'] for v in p['versions']))
        self.assertNotIn('ORPHAN', json.dumps(resp.json()).upper())
        self.assertIn('REVIEW', c[str(self.dup.id)]['reason'])

    def test_apply_is_refused_and_nothing_is_deleted(self):
        before = (Project.objects.count(), ScheduleUpload.objects.count())
        for extra in ({'apply': True}, {'apply': True, 'confirm': True}):
            resp = self.post(self.files, **extra)
            self.assertEqual(resp.status_code, 400)
            self.assertIn('not supported', resp.json()['error'])
        self.assertEqual((Project.objects.count(), ScheduleUpload.objects.count()), before)

    def test_summary_counts_and_no_deletion_fields(self):
        s = self.post(self.files).json()['summary']
        self.assertEqual((s['KEEP'], s['NOT_LOADED'], s['REVIEW']), (1, 2, 2))
        self.assertNotIn('DELETE', s)
        self.assertNotIn('versionsToDelete', s)

    def test_versions_of_a_kept_project_not_in_app_are_review(self):
        p, (v1, v2) = _proj('Multi', 2, ['Baseline', 'Update'])
        item = self.by_id(self.post([{'projectId': str(p.id), 'scheduleUploadId': str(v2.id), 'name': 'Update'}]))[str(p.id)]
        self.assertEqual(item['classification'], 'KEEP')
        self.assertEqual({v['label']: v['classification'] for v in item['versions']}, {'Baseline': 'REVIEW', 'Update': 'KEEP'})

    def test_empty_file_list_is_refused(self):
        self.assertEqual(self.post([]).status_code, 400)
        self.assertEqual(Project.objects.count(), 5)

    def test_a_browser_that_loaded_only_one_file_deletes_nothing(self):
        # The exact reported situation: only one schedule is loaded locally.
        before = (Project.objects.count(), ScheduleUpload.objects.count())
        only_one = [{'projectId': str(self.keep.id), 'scheduleUploadId': str(self.keep_v.id), 'name': 'Barn Keep'}]
        self.assertEqual(self.post(only_one).status_code, 200)
        self.assertEqual(self.post(only_one, apply=True).status_code, 400)
        self.assertEqual((Project.objects.count(), ScheduleUpload.objects.count()), before)
