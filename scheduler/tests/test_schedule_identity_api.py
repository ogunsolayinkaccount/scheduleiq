"""
Schedule Identity Evaluator — API wiring tests. Covers import_preview's
`scheduleIdentity` field end to end (real HTTP round trip through the
Django test Client), including the confirmation-gate contract the
frontend relies on and the item-9 "never hide raw P6 identity" guarantee.
"""
import json

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


class ScheduleIdentityApiWiringTests(TestCase):
    def _commit(self, task_codes, project_id=None, **kwargs):
        upload = SimpleUploadedFile('x.xer', _xer(task_codes, **kwargs))
        # Phase 2 (Import Protection) enforces the identity evaluator at
        # commit, not just in preview — several of these fixtures deliberately
        # commit a second version whose activity codes don't overlap the
        # first (e.g. to test reference-version resolution, not identity
        # itself), which the evaluator correctly flags for confirmation. This
        # helper confirms unconditionally; it is a no-op when nothing was
        # actually flagged.
        data = {'file': upload, 'confirmIdentityMismatch': 'true'}
        if project_id:
            data['projectId'] = project_id
        else:
            data['projectName'] = 'Identity API Test'
        resp = self.client.post('/api/import/commit/', data=data)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_no_projectid_no_schedule_identity(self):
        upload = SimpleUploadedFile('x.xer', _xer(_codes('A', 5)))
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertIsNone(resp.json()['scheduleIdentity'])

    def test_same_project_same_id_high_overlap(self):
        codes = _codes('A', 150)
        base = self._commit(codes, proj_id='4551', proj_name='AWP2025')
        project_id = base['projectId']

        upload = SimpleUploadedFile('u.xer', _xer(codes, proj_id='4551', proj_name='AWP2025', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        identity = resp.json()['scheduleIdentity']
        self.assertEqual(identity['classification'], 'SAME_PROJECT')
        self.assertFalse(identity['confirmationRequired'])

    def test_changed_p6_id_high_overlap_is_likely_same_project(self):
        codes = _codes('SFRH', 111)
        base = self._commit(codes, proj_id='4551', proj_name='AWP2025-BL-1')
        project_id = base['projectId']

        # 108/111 overlap, different proj_id — mirrors the real AWP2025 finding.
        upload_codes = codes[:108] + _codes('NEWACT', 3)
        upload = SimpleUploadedFile('u.xer', _xer(upload_codes, proj_id='4532', proj_name='AWP2025-BL continued', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        body = resp.json()
        identity = body['scheduleIdentity']
        self.assertEqual(identity['classification'], 'LIKELY_SAME_PROJECT')
        self.assertFalse(identity['confirmationRequired'])
        # Raw P6 identity is never hidden (item 9), even here.
        self.assertEqual(identity['signals']['p6ProjectId']['reference'], '4551')
        self.assertEqual(identity['signals']['p6ProjectId']['uploaded'], '4532')
        self.assertIsNotNone(body['p6ProjectIdMismatch'])  # backward-compat field still present

    def test_likely_different_project_requires_confirmation(self):
        base = self._commit(_codes('AWP', 150), proj_id='4551', proj_name='AWP2025')
        project_id = base['projectId']

        upload = SimpleUploadedFile('u.xer', _xer(_codes('PIPT', 150), proj_id='9001', proj_name='PIPT001', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        identity = resp.json()['scheduleIdentity']
        self.assertEqual(identity['classification'], 'LIKELY_DIFFERENT_PROJECT')
        self.assertTrue(identity['confirmationRequired'])

    def test_reference_version_is_genuine_current_not_stale_historical(self):
        """Item 14 — the evaluator must compare against the genuine CURRENT
        version, never an obsolete historical upload."""
        codes_v1 = _codes('OLD', 50)
        base = self._commit(codes_v1, proj_id='1', proj_name='Proj')
        project_id = base['projectId']

        codes_v2 = _codes('NEW', 200)
        v2 = self._commit(codes_v2, project_id=project_id, proj_id='1', proj_name='Proj', data_date='2026-09-01')

        # Now the CURRENT version has codes_v2. An upload matching codes_v2
        # closely should be SAME/LIKELY_SAME; one matching only the old v1
        # codes should NOT get credit for matching the (no-longer-current) v1.
        upload_matches_v2 = SimpleUploadedFile('m.xer', _xer(codes_v2[:190] + _codes('X', 10), proj_id='1', proj_name='Proj', data_date='2026-09-08'))
        resp = self.client.post('/api/import/preview/', data={'file': upload_matches_v2, 'projectId': project_id})
        identity = resp.json()['scheduleIdentity']
        self.assertIn(identity['classification'], ('SAME_PROJECT', 'LIKELY_SAME_PROJECT'))
        self.assertEqual(identity['signals']['activityIdOverlap']['referenceCount'], 200)  # compared against v2, not v1's 50

    def test_baseline_only_project_uses_baseline_as_reference(self):
        base = self._commit(_codes('BL', 111), proj_id='1', proj_name='Proj')
        project_id = base['projectId']
        # Reclassify the only version as APPROVED_BASELINE (item 14: "if only
        # a baseline exists, use the baseline").
        su = ScheduleUpload.objects.get(pk=base['scheduleUploadId'])
        su.schedule_classification = 'APPROVED_BASELINE'
        su.save(update_fields=['schedule_classification'])

        upload = SimpleUploadedFile('u.xer', _xer(_codes('BL', 108) + _codes('NEW', 3), proj_id='1', proj_name='Proj', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        identity = resp.json()['scheduleIdentity']
        self.assertIn(identity['classification'], ('SAME_PROJECT', 'LIKELY_SAME_PROJECT'))
        self.assertEqual(identity['referenceVersionLabel'], su.version_label or su.original_filename)


class ImportIdentityGuardTests(TestCase):
    """
    The proactive half of the Import Identity Guard: import_preview's
    `identityCandidate` field, populated only when NO projectId is given
    (the "Create New Project" path that previously ran no identity check
    at all — the exact gap that let a real Barn weekly update land as its
    own Project instead of an eighth version of the canonical Barn
    project). Real HTTP round trip, same _xer()/_commit() helpers as the
    confirmatory scheduleIdentity tests above.
    """

    def _commit(self, task_codes, project_id=None, **kwargs):
        upload = SimpleUploadedFile('x.xer', _xer(task_codes, **kwargs))
        data = {'file': upload}
        if project_id:
            data['projectId'] = project_id
        else:
            data['projectName'] = 'Identity Guard Test'
        resp = self.client.post('/api/import/commit/', data=data)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_no_existing_projects_no_candidate(self):
        upload = SimpleUploadedFile('x.xer', _xer(_codes('A', 5)))
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertIsNone(resp.json()['identityCandidate'])

    def test_high_overlap_upload_with_no_projectid_surfaces_the_existing_project(self):
        codes = _codes('BARN', 150)
        base = self._commit(codes, proj_id='6564', proj_name='Barn 16-Sep-26- BLDG 2 COMP 1')
        project_id = base['projectId']

        # Next weekly update: high overlap, no projectId supplied — mirrors
        # the real Sep-25 scenario (uploaded via "Create New Project").
        next_update_codes = codes[:147] + _codes('NEWACT', 8)
        upload = SimpleUploadedFile(
            'barn-sep25.xer',
            _xer(next_update_codes, proj_id='6569', proj_name='Barn 25-Sep-26- BLDG 2 COMP 1.', data_date='2026-09-25'),
        )
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        body = resp.json()
        # A new-project commit must still be possible from this same
        # preview — surfacing a candidate never blocks or redirects.
        candidate = body['identityCandidate']
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['projectId'], project_id)
        self.assertIn(candidate['identity']['classification'], ('SAME_PROJECT', 'LIKELY_SAME_PROJECT'))
        self.assertEqual(candidate['identity']['versionLineageCompatibility'], 'COMPATIBLE')
        self.assertIsNone(body['scheduleIdentity'])  # confirmatory field is for the OTHER (projectId-given) path

    def test_unrelated_upload_with_no_projectid_surfaces_no_candidate(self):
        self._commit(_codes('BARN', 150), proj_id='6564', proj_name='Barn 16-Sep-26- BLDG 2 COMP 1')

        upload = SimpleUploadedFile('other.xer', _xer(_codes('UNRELATED', 20), proj_id='9999', proj_name='Totally Different', data_date='2026-09-25'))
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        self.assertIsNone(resp.json()['identityCandidate'])

    def test_scoped_master_upload_against_an_existing_smaller_subset_is_scope_divergent(self):
        # A small scoped subset already exists as the current version (the
        # real June case's shape: the existing project's current version is
        # the smaller "-SE" file)...
        subset_codes = _codes('MASTER', 180)
        base = self._commit(subset_codes, proj_id='6173', proj_name='Barn 06-May-12')
        project_id = base['projectId']

        # ...and the much larger master schedule (a superset of those same
        # activities) is uploaded with no projectId. referenceOverlapRatio
        # is near-total (every existing activity is in the upload), so
        # classification reaches SAME/LIKELY_SAME on overlap alone — but the
        # population-ratio signal must still flag scope divergence rather
        # than silently recommending "Add as Schedule Version".
        master_codes = subset_codes + _codes('MASTERSCOPE', 420)
        upload = SimpleUploadedFile('barn-master.xer', _xer(master_codes, proj_id='6173', proj_name='Barn 06-May-12', data_date='2026-06-10'))
        resp = self.client.post('/api/import/preview/', data={'file': upload})
        candidate = resp.json()['identityCandidate']
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['projectId'], project_id)
        self.assertIn(candidate['identity']['classification'], ('SAME_PROJECT', 'LIKELY_SAME_PROJECT'))
        self.assertEqual(candidate['identity']['versionLineageCompatibility'], 'SCOPE_DIVERGENT')

    def test_identity_candidate_is_null_when_projectid_is_given(self):
        # The proactive scan only runs on the "no destination chosen yet"
        # path — when a destination is already given, the confirmatory
        # scheduleIdentity field (tested above) is the relevant one.
        codes = _codes('BARN', 150)
        base = self._commit(codes, proj_id='6564', proj_name='Barn')
        project_id = base['projectId']

        upload = SimpleUploadedFile('u.xer', _xer(codes, proj_id='6564', proj_name='Barn', data_date='2026-09-25'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        self.assertIsNone(resp.json()['identityCandidate'])

    def test_preview_never_creates_or_attaches_anything(self):
        base = self._commit(_codes('BARN', 150), proj_id='6564', proj_name='Barn')
        before = Project.objects.count()
        before_versions = ScheduleUpload.objects.filter(project_id=base['projectId']).count()

        upload = SimpleUploadedFile('barn-sep25.xer', _xer(_codes('BARN', 145) + _codes('NEW', 5), proj_id='6569', proj_name='Barn 2', data_date='2026-09-25'))
        self.client.post('/api/import/preview/', data={'file': upload})

        self.assertEqual(Project.objects.count(), before)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=base['projectId']).count(), before_versions)

    def test_insufficient_data_new_tiny_project(self):
        base = self._commit(_codes('A', 2), proj_id='1', proj_name='Tiny')
        project_id = base['projectId']
        upload = SimpleUploadedFile('u.xer', _xer(_codes('A', 2), proj_id='1', proj_name='Tiny', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        identity = resp.json()['scheduleIdentity']
        self.assertEqual(identity['classification'], 'UNCERTAIN')
        self.assertTrue(identity['confirmationRequired'])

    def test_duplicate_detection_independent_of_identity(self):
        """Item 15 — high activity overlap (same project) must not be
        conflated with 'this exact file was already imported'."""
        codes = _codes('A', 111)
        base = self._commit(codes, proj_id='1', proj_name='Proj', data_date='2026-08-01')
        project_id = base['projectId']

        # A genuinely NEW update (different Data Date) with high overlap —
        # SAME_PROJECT identity, but NOT a duplicate.
        upload = SimpleUploadedFile('u.xer', _xer(codes, proj_id='1', proj_name='Proj', data_date='2026-09-01'))
        resp = self.client.post('/api/import/preview/', data={'file': upload, 'projectId': project_id})
        body = resp.json()
        self.assertEqual(body['scheduleIdentity']['classification'], 'SAME_PROJECT')
        self.assertIsNone(body['possibleDuplicate'])

        # The SAME file (same Data Date + activity count) — duplicate flagged
        # independently, identity still SAME_PROJECT.
        upload2 = SimpleUploadedFile('u2.xer', _xer(codes, proj_id='1', proj_name='Proj', data_date='2026-08-01'))
        resp2 = self.client.post('/api/import/preview/', data={'file': upload2, 'projectId': project_id})
        body2 = resp2.json()
        self.assertEqual(body2['scheduleIdentity']['classification'], 'SAME_PROJECT')
        self.assertIsNotNone(body2['possibleDuplicate'])
