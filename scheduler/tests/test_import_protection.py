"""
Phase 2 (Import Protection) — server-side duplicate-import and schedule-
identity enforcement INSIDE import_commit itself, so a direct API call that
skips /api/import/preview/ cannot bypass any of it. Every test here posts
straight to /api/import/commit/, never through preview first — that is the
exact gap this phase closes.
"""
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from scheduler.models import Project, ScheduleUpload


def _xer(task_codes, proj_id='PROJ1', proj_name='Test Project', data_date='2026-08-01'):
    lines = [
        '%T\tPROJECT',
        '%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id',
        f'%R\t{proj_id}\t{proj_name}\t{data_date}\t2026-01-01\t2026-12-31\t2026-12-31\tCAL1',
        '%T\tPROJWBS',
        '%F\twbs_id\twbs_name\twbs_short_name\tparent_wbs_id\tseq_num\tproj_id',
        f'%R\tWBS1\tArea A\tAREAA\t\t1\t{proj_id}',
        '%T\tCALENDAR',
        '%F\tclndr_id\tclndr_name\tclndr_type\tdefault_flag\tday_hr_cnt\tweek_hr_cnt\tmonth_hr_cnt\tyear_hr_cnt\tclndr_data',
        '%R\tCAL1\tStandard 5 Day Workweek\tCA_Base\tY\t8\t40\t172\t2000\t',
        '%T\tTASK',
        '%F\ttask_id\ttask_code\ttask_name\tproj_id\twbs_id\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\ttotal_float_hr_cnt\tfree_float_hr_cnt\tearly_start_date\tearly_end_date\tlate_start_date\tlate_end_date\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\trestart_date\treend_date\tclndr_id\tcomplete_pct_type\tphys_complete_pct\tcstr_type\tcstr_date\tdriving_path_flag',
    ]
    for i, code in enumerate(task_codes):
        lines.append(
            f'%R\tTASK{i}\t{code}\tActivity {code}\t{proj_id}\tWBS1\tTT_Task\tTK_NotStart\t80\t80\t0\t0\t'
            f'2026-01-10\t2026-01-19\t2026-01-10\t2026-01-19\t2026-01-10\t2026-01-19\t\t\t\t\tCAL1\tCP_Drtn\t0\t\t\tN'
        )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _codes(prefix, n, start=0):
    return [f'{prefix}-{i:04d}' for i in range(start, start + n)]


def _upload(name, task_codes, **kwargs):
    return SimpleUploadedFile(name, _xer(task_codes, **kwargs))


class ExactDuplicateImportTests(TestCase):
    def test_exact_duplicate_within_same_project_is_refused_without_confirmation(self):
        codes = _codes('A', 5)
        r1 = self.client.post('/api/import/commit/', data={'file': _upload('v1.xer', codes), 'projectName': 'Dup Test'})
        self.assertEqual(r1.status_code, 200, r1.content)
        project_id = r1.json()['projectId']
        first_id = r1.json()['scheduleUploadId']

        r2 = self.client.post('/api/import/commit/', data={'file': _upload('v1-copy.xer', codes), 'projectId': project_id})
        self.assertEqual(r2.status_code, 409, r2.content)
        body = r2.json()
        self.assertEqual(body['error'], 'EXACT_DUPLICATE_IMPORT')
        self.assertEqual(body['matchingVersionId'], first_id)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=project_id).count(), 1)  # nothing created

    def test_exact_duplicate_confirmed_anyway_creates_a_second_independent_version(self):
        codes = _codes('A', 5)
        r1 = self.client.post('/api/import/commit/', data={'file': _upload('v1.xer', codes), 'projectName': 'Dup Test'})
        project_id = r1.json()['projectId']
        first_id = r1.json()['scheduleUploadId']

        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1-copy.xer', codes), 'projectId': project_id, 'confirmDuplicateImport': 'true',
        })
        self.assertEqual(r2.status_code, 200, r2.content)
        second_id = r2.json()['scheduleUploadId']
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=project_id).count(), 2)
        self.assertTrue(ScheduleUpload.objects.filter(pk=first_id).exists())  # original untouched

    def test_duplicate_against_a_soft_deleted_version_is_not_flagged(self):
        """A content match that exists ONLY as a soft-deleted row must not
        block a legitimate re-import — the same is_deleted exclusion every
        other query already applies governs this duplicate check too."""
        codes = _codes('A', 5)
        r1 = self.client.post('/api/import/commit/', data={'file': _upload('v1.xer', codes), 'projectName': 'Dup Test'})
        project_id = r1.json()['projectId']
        first_id = r1.json()['scheduleUploadId']
        self.client.delete(
            f'/api/projects/{project_id}/versions/{first_id}/',
            data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json',
        )

        r2 = self.client.post('/api/import/commit/', data={'file': _upload('v1-copy.xer', codes), 'projectId': project_id})
        self.assertEqual(r2.status_code, 200, r2.content)


class LegitimateRevisionTests(TestCase):
    def test_revised_schedule_same_identity_different_content_is_never_blocked(self):
        """A legitimately revised schedule (same project, different
        activities/content) must commit with NO confirmation flags at all —
        never rejected merely for being the next version of this project."""
        codes_v1 = _codes('A', 10)
        r1 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes_v1, proj_id='REAL1', proj_name='Real Project'),
            'projectName': 'Revision Test',
        })
        project_id = r1.json()['projectId']

        # 9/10 activities carried over, 1 new — a normal update: same P6 id,
        # same name, high overlap -> SAME_PROJECT, confirmation NOT required.
        codes_v2 = codes_v1[:9] + _codes('NEWACT', 1)
        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', codes_v2, proj_id='REAL1', proj_name='Real Project', data_date='2026-09-01'),
            'projectId': project_id,
        })
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=project_id).count(), 2)

    def test_p6_project_id_match_alone_never_forces_rejection(self):
        """'Do not reject legitimate updates merely because P6 project IDs
        match' — a matching id is never itself a reason to require confirmation."""
        codes = _codes('B', 10)
        r1 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes, proj_id='SAME_ID'), 'projectName': 'Same Id Test',
        })
        project_id = r1.json()['projectId']

        codes_v2 = codes[:8] + _codes('EXTRA', 2)
        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', codes_v2, proj_id='SAME_ID', data_date='2026-09-01'), 'projectId': project_id,
        })
        self.assertEqual(r2.status_code, 200, r2.content)


class MatchingProjectIdentityTests(TestCase):
    """No projectId given — the Import Identity Guard's proactive scan,
    enforced here (not just advisory in preview)."""

    def test_structural_match_to_an_existing_project_blocks_new_project_creation(self):
        codes = _codes('C', 10)
        r1 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes, proj_id='EXIST1', proj_name='Existing Project'),
            'projectName': 'Existing Project',
        })
        existing_project_id = r1.json()['projectId']

        # Same activities, no projectId -> looks exactly like "Existing
        # Project"'s current version.
        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', codes, proj_id='EXIST1', proj_name='Existing Project', data_date='2026-09-01'),
        })
        self.assertEqual(r2.status_code, 409, r2.content)
        body = r2.json()
        self.assertEqual(body['error'], 'MATCHING_PROJECT_FOUND')
        self.assertEqual(body['identityCandidate']['projectId'], existing_project_id)
        self.assertEqual(Project.objects.count(), 1)  # no second project created

    def test_confirmed_new_project_override_creates_a_separate_project_anyway(self):
        codes = _codes('D', 10)
        r1 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes, proj_id='EXIST2', proj_name='Existing Project 2'),
            'projectName': 'Existing Project 2',
        })
        existing_project_id = r1.json()['projectId']

        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', codes, proj_id='EXIST2', proj_name='Existing Project 2', data_date='2026-09-01'),
            'confirmNewProject': 'true',
        })
        self.assertEqual(r2.status_code, 200, r2.content)
        new_project_id = r2.json()['projectId']
        self.assertNotEqual(new_project_id, existing_project_id)
        self.assertEqual(Project.objects.count(), 2)

    def test_no_structural_match_creates_new_project_without_any_confirmation(self):
        codes = _codes('E', 10)
        r1 = self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes, proj_id='SOLO1'), 'projectName': 'Solo Project',
        })
        self.assertEqual(r1.status_code, 200, r1.content)

        # Completely different structure/identity -> no candidate, no 409.
        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', _codes('UNRELATED', 10), proj_id='SOLO2'), 'projectName': 'Second Solo Project',
        })
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(Project.objects.count(), 2)


class DirectApiBypassTests(TestCase):
    """Every protection above already runs on a bare commit call with no
    preceding /api/import/preview/ request — these tests just make that
    explicit for the two sharpest cases."""

    def test_duplicate_protection_applies_even_without_calling_preview_first(self):
        codes = _codes('F', 5)
        r1 = self.client.post('/api/import/commit/', data={'file': _upload('v1.xer', codes), 'projectName': 'Bypass Test'})
        project_id = r1.json()['projectId']
        r2 = self.client.post('/api/import/commit/', data={'file': _upload('v1-copy.xer', codes), 'projectId': project_id})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.json()['error'], 'EXACT_DUPLICATE_IMPORT')

    def test_identity_guard_applies_even_without_calling_preview_first(self):
        codes = _codes('G', 10)
        self.client.post('/api/import/commit/', data={
            'file': _upload('v1.xer', codes, proj_id='BYPASS1'), 'projectName': 'Bypass Identity Test',
        })
        r2 = self.client.post('/api/import/commit/', data={
            'file': _upload('v2.xer', codes, proj_id='BYPASS1', data_date='2026-09-01'),
        })
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.json()['error'], 'MATCHING_PROJECT_FOUND')


class ConcurrentSubmissionTests(TestCase):
    """Double-click / concurrent-submission guard for the 'create new
    project' path, where there is no existing row yet to select_for_update
    on (unlike adding a version to an existing project)."""

    def test_second_concurrent_new_project_submission_is_refused(self):
        codes = _codes('H', 5)
        with patch('django.core.cache.cache.add', return_value=False):
            resp = self.client.post('/api/import/commit/', data={
                'file': _upload('v1.xer', codes), 'projectName': 'Concurrent Test',
            })
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()['error'], 'DUPLICATE_SUBMISSION_IN_PROGRESS')
        self.assertEqual(Project.objects.count(), 0)  # neither request's project was created

    def test_lock_contention_only_blocks_the_new_project_path_not_existing_projects(self):
        """The cache lock only guards 'create new project' (no row to lock
        on yet); adding a version to an EXISTING project relies on
        select_for_update instead, so it must never be affected by it."""
        codes = _codes('H', 5)
        r1 = self.client.post('/api/import/commit/', data={'file': _upload('v1.xer', codes), 'projectName': 'Existing Project'})
        project_id = r1.json()['projectId']

        with patch('django.core.cache.cache.add', return_value=False):
            resp = self.client.post('/api/import/commit/', data={
                'file': _upload('v2.xer', _codes('OTHER', 5), data_date='2026-09-01'), 'projectId': project_id,
                'confirmIdentityMismatch': 'true',  # unrelated codes vs v1 — not what this test is checking
            })
        self.assertEqual(resp.status_code, 200, resp.content)
