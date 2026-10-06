"""
Regression test for the "Metrics API 403 after authentication" incident.

Root cause (NOT a role-permission mistake, NOT a frontend request bypassing
the authenticated fetch wrapper): Django's CsrfViewMiddleware verifies the
browser's Origin header against request.get_host() UNCONDITIONALLY (see
django.middleware.csrf.CsrfViewMiddleware._origin_verified — this is not
gated behind HTTPS). The Vite dev server proxies /api to this backend with
changeOrigin:true, which rewrites the Host header the backend sees to its
own port — so the browser's real Origin (the Vite dev server's own origin)
never matched, and EVERY authenticated mutating request from the real app
was rejected with "Origin checking failed," regardless of role or CSRF
token correctness. The fix is CSRF_TRUSTED_ORIGINS (seglc_backend/
settings.py), Django's own documented mechanism for exactly this situation
— no decorator, role, or CSRF check was weakened.

These tests use Client(enforce_csrf_checks=True) WITH an explicit Origin
header matching the dev frontend, reproducing the real browser's request
shape — the default test Client never sends Origin at all, which is
exactly why the ~1270 pre-existing tests never caught this.
"""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from scheduler.models import Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER, ScheduleUpload
from .test_authentication import _user

DEV_FRONTEND_ORIGIN = 'http://localhost:5170'


def _xer(task_codes, proj_id='PROJ1'):
    lines = [
        '%T\tPROJECT',
        '%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id',
        f'%R\t{proj_id}\tRegression Test\t2026-08-01\t2026-01-01\t2026-12-31\t2026-12-31\tCAL1',
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


@override_settings(AUTO_AUTH_TEST_USER=False)
class MetricsApiEndToEndRegressionTests(TestCase):
    """Authenticated user -> import succeeds -> 164-activity-style version
    exists -> metrics request succeeds -> analytics load. Exercised for
    Administrator, Scheduler, Viewer, and unauthenticated, all with the
    Origin header a real browser sends."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.admin = _user('e2e-admin', ROLE_ADMINISTRATOR, password='e2e-pw-1')
        self.scheduler = _user('e2e-scheduler', ROLE_SCHEDULER, password='e2e-pw-2')
        self.viewer = _user('e2e-viewer', ROLE_VIEWER, password='e2e-pw-3')

    def _login(self, username, password):
        token = self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': username, 'password': password}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']

    def test_administrator_import_then_metrics_succeeds(self):
        token = self._login('e2e-admin', 'e2e-pw-1')
        commit = self.client.post(
            '/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', _xer([f'A-{i:04d}' for i in range(5)])), 'projectName': 'E2E Admin'},
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(commit.status_code, 200, commit.content)
        activities = commit.json()['activities']

        token = self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']
        metrics = self.client.post(
            '/api/metrics/', data=json.dumps({'activities': activities, 'dataDate': '2026-08-01'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(metrics.status_code, 200, metrics.content)
        self.assertEqual(metrics.json()['total'], 5)

    def test_scheduler_import_then_metrics_succeeds(self):
        token = self._login('e2e-scheduler', 'e2e-pw-2')
        commit = self.client.post(
            '/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', _xer([f'A-{i:04d}' for i in range(5)])), 'projectName': 'E2E Scheduler'},
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(commit.status_code, 200, commit.content)
        activities = commit.json()['activities']

        token = self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']
        metrics = self.client.post(
            '/api/metrics/', data=json.dumps({'activities': activities, 'dataDate': '2026-08-01'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(metrics.status_code, 200, metrics.content)

    def test_viewer_is_still_correctly_refused_not_a_security_regression(self):
        # Viewer cannot import OR call metrics — proves the Origin fix
        # didn't accidentally loosen the role gate too.
        token = self._login('e2e-viewer', 'e2e-pw-3')
        commit = self.client.post(
            '/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', _xer(['A-0001'])), 'projectName': 'E2E Viewer'},
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(commit.status_code, 403)
        self.assertEqual(commit.json()['error'], 'This action requires the Scheduler role or higher.')

        token = self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']
        metrics = self.client.post(
            '/api/metrics/', data=json.dumps({'activities': [], 'dataDate': '2026-08-01'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(metrics.status_code, 403)
        self.assertEqual(metrics.json()['error'], 'This action requires the Scheduler role or higher.')

    def test_unauthenticated_is_still_correctly_refused(self):
        # A real anonymous browser has already loaded the app (and so
        # already has a csrftoken cookie from /api/auth/me/) but never
        # logged in — isolates "not authenticated" from "no CSRF cookie at
        # all yet" (a cold request with neither gets a 403 from
        # CsrfViewMiddleware before the view ever runs, which is correct
        # but a different, already-covered case).
        token = self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']
        resp = self.client.post(
            '/api/metrics/', data=json.dumps({'activities': [], 'dataDate': '2026-08-01'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertEqual(resp.status_code, 401)


@override_settings(AUTO_AUTH_TEST_USER=False)
class LegacyComputeEndpointsOriginRegressionTests(TestCase):
    """The other four pure-compute, no-persistence legacy endpoints that
    share metrics' exact decorator — confirms the fix (and the pre-existing
    role gate) applies uniformly, not just to /api/metrics/ itself."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.scheduler = _user('legacy-scheduler', ROLE_SCHEDULER, password='legacy-pw')

    def _token(self):
        return self.client.get('/api/auth/csrf/', HTTP_ORIGIN=DEV_FRONTEND_ORIGIN).json()['csrfToken']

    def _login(self):
        token = self._token()
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': 'legacy-scheduler', 'password': 'legacy-pw'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_analyze_endpoint_with_origin_header_succeeds(self):
        self._login()
        resp = self.client.post(
            '/api/analyze/', data=json.dumps({'activities': []}), content_type='application/json',
            HTTP_X_CSRFTOKEN=self._token(), HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertNotIn(resp.status_code, (401, 403))

    def test_quality_endpoint_with_origin_header_succeeds(self):
        self._login()
        resp = self.client.post(
            '/api/quality/', data=json.dumps({'activities': []}), content_type='application/json',
            HTTP_X_CSRFTOKEN=self._token(), HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertNotIn(resp.status_code, (401, 403))

    def test_narrative_endpoint_with_origin_header_succeeds(self):
        self._login()
        resp = self.client.post(
            '/api/narrative/', data=json.dumps({'activities': []}), content_type='application/json',
            HTTP_X_CSRFTOKEN=self._token(), HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertNotIn(resp.status_code, (401, 403))

    def test_upload_endpoint_with_origin_header_succeeds(self):
        self._login()
        resp = self.client.post(
            '/api/upload/', data={'file': SimpleUploadedFile('v1.xer', _xer(['A-0001']))},
            HTTP_X_CSRFTOKEN=self._token(), HTTP_ORIGIN=DEV_FRONTEND_ORIGIN,
        )
        self.assertNotIn(resp.status_code, (401, 403))
