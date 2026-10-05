"""
Phase 3 (Authentication and Authorization) — real, server-verified
identity and role enforcement.

Every class here disables the AutoAuthTestMiddleware test convenience
(@override_settings(AUTO_AUTH_TEST_USER=False) — see
scheduler/test_auth_middleware.py) so requests are exactly what a real
unauthenticated or specific-role caller would see. The rest of the suite
(~1160 tests) leaves that middleware ON and is unaffected by this phase —
see that middleware's module docstring for why that split is safe.
"""
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from scheduler.models import Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER, ScheduleUpload, AuditLog


def _user(username, role, password='pw12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


@override_settings(AUTO_AUTH_TEST_USER=False)
class UnauthenticatedAccessTests(TestCase):
    """A direct API call with no session at all must be refused — this is
    the real version of the "direct API bypass" guarantee; previously
    there was no identity system to enforce it at all."""

    def setUp(self):
        self.project = Project.objects.create(name='Auth Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', file_type='XER',
            activities_json=[], schedule_classification='CURRENT_UPDATE',
        )

    def test_read_only_endpoint_requires_authentication(self):
        resp = self.client.get('/api/projects/')
        self.assertEqual(resp.status_code, 401)

    def test_import_commit_requires_authentication(self):
        resp = self.client.post('/api/import/commit/', data={'projectName': 'X'})
        self.assertEqual(resp.status_code, 401)

    def test_version_delete_requires_authentication(self):
        resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)
        self.version.refresh_from_db()
        self.assertFalse(self.version.is_deleted)

    def test_version_restore_requires_authentication(self):
        self.version.is_deleted = True
        self.version.deleted_at = timezone.now()
        self.version.save(update_fields=['is_deleted', 'deleted_at'])
        resp = self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/restore/',
            data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)
        self.version.refresh_from_db()
        self.assertTrue(self.version.is_deleted)

    def test_project_delete_requires_authentication(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(Project.objects.filter(pk=self.project.id).exists())

    def test_user_management_requires_authentication(self):
        resp = self.client.get('/api/auth/users/')
        self.assertEqual(resp.status_code, 401)

    def test_me_endpoint_is_reachable_while_unauthenticated(self):
        # /api/auth/me/ must itself be reachable anonymously — the frontend
        # needs it to decide whether to show the login screen.
        resp = self.client.get('/api/auth/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['authenticated'])

    def test_csrf_bootstrap_is_reachable_while_unauthenticated(self):
        resp = self.client.get('/api/auth/csrf/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('csrfToken', resp.json())


@override_settings(AUTO_AUTH_TEST_USER=False)
class LoginLogoutTests(TestCase):
    def setUp(self):
        self.user = _user('scheduler1', ROLE_SCHEDULER, password='correct-horse-1')

    def test_login_with_correct_credentials_succeeds_and_establishes_a_session(self):
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': 'scheduler1', 'password': 'correct-horse-1'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['user']['username'], 'scheduler1')
        self.assertEqual(body['user']['role'], ROLE_SCHEDULER)

        me = self.client.get('/api/auth/me/')
        self.assertTrue(me.json()['authenticated'])
        self.assertEqual(me.json()['user']['username'], 'scheduler1')

    def test_login_with_wrong_password_is_refused(self):
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': 'scheduler1', 'password': 'wrong'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(self.client.get('/api/auth/me/').json()['authenticated'])

    def test_login_with_unknown_username_is_refused_identically_to_wrong_password(self):
        # Never reveal whether the username exists — same status/shape either way.
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': 'nobody-here', 'password': 'whatever'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_inactive_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        resp = self.client.post(
            '/api/auth/login/', data=json.dumps({'username': 'scheduler1', 'password': 'correct-horse-1'}),
            content_type='application/json',
        )
        # Django's ModelBackend itself refuses to authenticate an inactive
        # user (returns None, same as a wrong password) — 401, not 403, and
        # indistinguishable from "wrong password" to the caller, which is
        # the more secure behavior (never confirms the account exists).
        self.assertEqual(resp.status_code, 401)

    def test_logout_ends_the_session(self):
        self.client.login(username='scheduler1', password='correct-horse-1')
        self.assertTrue(self.client.get('/api/auth/me/').json()['authenticated'])
        resp = self.client.post('/api/auth/logout/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.client.get('/api/auth/me/').json()['authenticated'])

    def test_logout_without_a_session_is_a_harmless_no_op(self):
        resp = self.client.post('/api/auth/logout/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['loggedOut'])

    def test_a_request_body_claiming_a_username_never_authenticates_anything(self):
        # There is no endpoint that reads a bare "username" field as an
        # identity without a password — confirm login is the ONLY door in.
        resp = self.client.post('/api/projects/', data=json.dumps({'name': 'X', 'username': 'scheduler1'}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)


@override_settings(AUTO_AUTH_TEST_USER=False)
class CsrfProtectionTests(TestCase):
    """Uses a CSRF-enforcing client (the default test Client does not
    enforce CSRF at all) to verify the protection is real, not just
    configured."""
    client_class = Client

    def setUp(self):
        self.enforcing_client = Client(enforce_csrf_checks=True)
        self.user = _user('scheduler2', ROLE_SCHEDULER, password='correct-horse-2')

    def _login_and_get_csrf_token(self):
        csrf_resp = self.enforcing_client.get('/api/auth/csrf/')
        token = csrf_resp.json()['csrfToken']
        login_resp = self.enforcing_client.post(
            '/api/auth/login/', data=json.dumps({'username': 'scheduler2', 'password': 'correct-horse-2'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login_resp.status_code, 200, login_resp.content)
        # django.contrib.auth.login() rotates the CSRF token (rotate_token)
        # to defend against session fixation, so the pre-login token above
        # is now stale — fetch the new one issued after login.
        return self.enforcing_client.get('/api/auth/csrf/').json()['csrfToken']

    def test_mutating_request_without_csrf_token_is_rejected(self):
        token = self._login_and_get_csrf_token()
        resp = self.enforcing_client.post('/api/projects/', data=json.dumps({'name': 'No Token'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Project.objects.filter(name='No Token').exists())

    def test_mutating_request_with_correct_csrf_token_succeeds(self):
        token = self._login_and_get_csrf_token()
        resp = self.enforcing_client.post(
            '/api/projects/', data=json.dumps({'name': 'With Token'}), content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(Project.objects.filter(name='With Token').exists())

    def test_get_requests_never_need_a_csrf_token(self):
        self._login_and_get_csrf_token()
        resp = self.enforcing_client.get('/api/projects/')
        self.assertEqual(resp.status_code, 200)


@override_settings(AUTO_AUTH_TEST_USER=False)
class RolePermissionMatrixTests(TestCase):
    """Spot-checks the permission matrix documented in permissions.py —
    one representative case per boundary, not an exhaustive sweep of all
    60 endpoints (that would just restate the DECORATORS table)."""

    def setUp(self):
        self.viewer = _user('viewer1', ROLE_VIEWER)
        self.scheduler = _user('scheduler3', ROLE_SCHEDULER)
        self.admin = _user('admin1', ROLE_ADMINISTRATOR)
        self.project = Project.objects.create(name='Matrix Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', file_type='XER',
            activities_json=[], schedule_classification='CURRENT_UPDATE',
        )

    # ── Viewer: read yes, write no ──────────────────────────────────────
    def test_viewer_can_read_projects(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get('/api/projects/').status_code, 200)

    def test_viewer_cannot_create_a_project(self):
        self.client.force_login(self.viewer)
        resp = self.client.post('/api/projects/', data=json.dumps({'name': 'Viewer Project'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Project.objects.filter(name='Viewer Project').exists())

    def test_viewer_cannot_import(self):
        self.client.force_login(self.viewer)
        resp = self.client.post('/api/import/commit/', data={'projectName': 'X'})
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_patch_version_classification(self):
        self.client.force_login(self.viewer)
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'classification': 'APPROVED_BASELINE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    # ── Scheduler: read + everyday writes yes, destructive no ────────────
    def test_scheduler_can_create_a_project(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/projects/', data=json.dumps({'name': 'Scheduler Project'}), content_type='application/json')
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_scheduler_can_patch_version_classification(self):
        self.client.force_login(self.scheduler)
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'classification': 'APPROVED_BASELINE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_scheduler_cannot_delete_a_schedule_version(self):
        self.client.force_login(self.scheduler)
        resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)
        self.version.refresh_from_db()
        self.assertFalse(self.version.is_deleted)

    def test_scheduler_cannot_restore_a_schedule_version(self):
        self.version.is_deleted = True
        self.version.deleted_at = timezone.now()
        self.version.save(update_fields=['is_deleted', 'deleted_at'])
        self.client.force_login(self.scheduler)
        resp = self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/restore/',
            data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)
        self.version.refresh_from_db()
        self.assertTrue(self.version.is_deleted)

    def test_scheduler_cannot_delete_a_project(self):
        self.client.force_login(self.scheduler)
        resp = self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Project.objects.filter(pk=self.project.id).exists())

    def test_scheduler_cannot_apply_consolidation(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/projects/consolidation-apply/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_scheduler_can_read_consolidation_plan(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/projects/consolidation-plan/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

    def test_scheduler_cannot_manage_users(self):
        self.client.force_login(self.scheduler)
        resp = self.client.get('/api/auth/users/')
        self.assertEqual(resp.status_code, 403)

    # ── Administrator: everything ─────────────────────────────────────────
    def test_administrator_can_delete_and_restore_a_schedule_version(self):
        self.client.force_login(self.admin)
        del_resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json',
        )
        self.assertEqual(del_resp.status_code, 200, del_resp.content)
        restore_resp = self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/restore/',
            data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json',
        )
        self.assertEqual(restore_resp.status_code, 200, restore_resp.content)

    def test_administrator_can_delete_a_project(self):
        self.client.force_login(self.admin)
        resp = self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(resp.status_code, 200)

    def test_administrator_can_manage_users(self):
        self.client.force_login(self.admin)
        resp = self.client.get('/api/auth/users/')
        self.assertEqual(resp.status_code, 200)
        usernames = {u['username'] for u in resp.json()['users']}
        self.assertIn('admin1', usernames)


@override_settings(AUTO_AUTH_TEST_USER=False)
class AuditTrailUsesRealIdentityTests(TestCase):
    def setUp(self):
        self.admin = _user('admin2', ROLE_ADMINISTRATOR)
        self.project = Project.objects.create(name='Audit Identity Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', file_type='XER',
            activities_json=[], schedule_classification='CURRENT_UPDATE',
        )

    def test_delete_audit_row_records_the_authenticated_username(self):
        self.client.force_login(self.admin)
        self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'confirmation': 'DELETE', 'user': 'spoofed-identity'}), content_type='application/json',
        )
        row = AuditLog.objects.filter(object_type='ScheduleUpload', object_id=str(self.version.id)).latest('timestamp')
        self.assertEqual(row.user, 'admin2')
        self.assertNotEqual(row.user, 'spoofed-identity')

    def test_unauthenticated_refusal_still_has_no_user_but_is_not_a_security_hole(self):
        # An outright-401 request never reaches _write_audit_log at all (the
        # decorator refuses it before the view runs) — confirm no row with a
        # fabricated identity is ever written for a request that was never
        # authenticated in the first place.
        resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.version.id}/',
            data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(AuditLog.objects.filter(object_type='ScheduleUpload', object_id=str(self.version.id)).exists())


@override_settings(AUTO_AUTH_TEST_USER=False)
class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = _user('root-admin', ROLE_ADMINISTRATOR)

    def test_administrator_can_create_a_user_with_a_role(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/api/auth/users/', data=json.dumps({
            'username': 'new-scheduler', 'password': 'a-real-password-1', 'role': ROLE_SCHEDULER,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['user']['role'], ROLE_SCHEDULER)
        created = User.objects.get(username='new-scheduler')
        self.assertTrue(created.check_password('a-real-password-1'))

    def test_cannot_create_a_user_with_an_invalid_role(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/api/auth/users/', data=json.dumps({
            'username': 'bad-role', 'password': 'a-real-password-1', 'role': 'SUPERUSER',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(username='bad-role').exists())

    def test_cannot_create_a_duplicate_username(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/api/auth/users/', data=json.dumps({
            'username': 'root-admin', 'password': 'a-real-password-1', 'role': ROLE_VIEWER,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 409)

    def test_cannot_demote_the_last_active_administrator(self):
        self.client.force_login(self.admin)
        resp = self.client.patch(
            f'/api/auth/users/{self.admin.id}/', data=json.dumps({'role': ROLE_VIEWER}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409)
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, ROLE_ADMINISTRATOR)

    def test_can_demote_an_administrator_when_another_remains(self):
        second_admin = _user('second-admin', ROLE_ADMINISTRATOR)
        self.client.force_login(self.admin)
        resp = self.client.patch(
            f'/api/auth/users/{second_admin.id}/', data=json.dumps({'role': ROLE_VIEWER}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        second_admin.profile.refresh_from_db()
        self.assertEqual(second_admin.profile.role, ROLE_VIEWER)

    def test_cannot_deactivate_the_last_active_administrator(self):
        self.client.force_login(self.admin)
        resp = self.client.patch(
            f'/api/auth/users/{self.admin.id}/', data=json.dumps({'isActive': False}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409)


@override_settings(AUTO_AUTH_TEST_USER=False)
class SignalCreatesProfileTests(TestCase):
    def test_a_superuser_is_profiled_as_administrator_automatically(self):
        su = User.objects.create_superuser(username='boot-admin', email='', password='x')
        self.assertEqual(su.profile.role, ROLE_ADMINISTRATOR)

    def test_an_ordinary_user_defaults_to_viewer_the_least_privileged_role(self):
        u = User.objects.create_user(username='plain-user', password='x')
        self.assertEqual(u.profile.role, ROLE_VIEWER)
