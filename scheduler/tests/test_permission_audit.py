"""
Final Security and Baseline Review — machine-verified permission audit.

Two complementary layers:

1. STRUCTURAL (PermissionAuditStructuralTests) — pure introspection, no
   HTTP, no DB. Cross-checks TWO independent sources for every endpoint's
   required role: EXPECTED_ROLES below (written out from this project's
   original design — see permissions.py's module docstring for the
   governing principle) against the ACTUAL `role_requirements` attribute
   every @require_role-decorated view carries at runtime, read directly
   off the live URL resolver. A mismatch means the deployed code drifted
   from the documented/intended permission matrix — the class of mistake
   a hand-maintained table or a few spot-check tests cannot catch alone.
   This is the "complete endpoint-permission audit."

2. LIVE (EndpointCategoryPermissionTests, AutoAuthTestFixtureCannotEscapeTests)
   — real HTTP requests, unauthenticated and as each of the three roles,
   concentrated on the categories explicitly called out for this review:
   imports, deletion, restoration, project consolidation, contractual
   milestone modifications, and user administration. (Broader day-to-day
   coverage of every other endpoint already exists in
   test_authentication.py's RolePermissionMatrixTests; this file does not
   repeat that, it fills the specific gaps this review asked about.)
"""
import json
import unittest

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import get_resolver
from django.utils import timezone

from scheduler.models import (
    MilestoneDefinition, Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER, ScheduleUpload,
)

V, S, A = ROLE_VIEWER, ROLE_SCHEDULER, ROLE_ADMINISTRATOR

# function_name -> {'default': role_or_None, **METHOD: role} — mirrors
# require_role's own call signature exactly. Written independently of
# views.py's current decorators (see module docstring).
EXPECTED_ROLES = {
    'upload': {'default': S},
    'metrics': {'default': S},
    'analyze': {'default': S},
    'quality': {'default': S},
    'threshold_profiles': {'default': V, 'POST': S},
    'threshold_profile_detail': {'default': V, 'PUT': S, 'PATCH': S},
    'narrative': {'default': S},
    'import_preview': {'default': S},
    'import_commit': {'default': S},
    'projects': {'default': V, 'POST': S},
    'project_detail': {'default': V, 'DELETE': A},
    'projects_reconcile': {'default': V},
    'projects_consolidation_plan': {'default': V},
    'projects_consolidation_simulate': {'default': V},
    'projects_consolidation_apply': {'default': A},
    'version_difference_report': {'default': V},
    'project_versions': {'default': V},
    'project_version_detail': {'default': S, 'DELETE': A},
    'project_deleted_versions': {'default': S},
    'project_version_restore': {'default': A},
    'project_documents': {'default': V, 'POST': S},
    'project_compare': {'default': V},
    'project_progress_curve': {'default': V},
    'project_risk': {'default': V},
    'project_milestones': {'default': V},
    'project_baseline_progress': {'default': V},
    'project_lookahead': {'default': V},
    'project_baseline_progress_export': {'default': V},
    'project_update_intelligence': {'default': V},
    'project_cost_summary': {'default': V},
    'project_earned_value': {'default': V},
    'project_productivity': {'default': V},
    'project_cost_history': {'default': V},
    'project_controls_trends': {'default': V},
    'project_controls_drivers': {'default': V},
    'project_executive_summary': {'default': V},
    'project_reports': {'default': V, 'POST': S},
    'project_report_detail': {'default': V, 'DELETE': S},
    'project_report_pdf': {'default': V},
    'project_report_excel': {'default': V},
    'project_reports_compare': {'default': V},
    'project_cost_entries': {'default': V, 'POST': S},
    'project_cost_entry_detail': {'default': S},
    'project_ai_review': {'default': V, 'POST': S},
    'project_ai_chat': {'default': S},
    'project_recovery_scenarios': {'default': V, 'POST': S},
    'project_recovery_scenario_detail': {'default': V, 'PUT': S, 'PATCH': S, 'DELETE': S},
    'project_risk_register': {'default': V},
    'project_risk_detail': {'default': S},
    'project_risk_driving_chain': {'default': V},
    'project_mitigation_actions': {'default': V, 'POST': S},
    'project_mitigation_action_detail': {'default': V, 'PATCH': S, 'DELETE': S},
    'project_issues': {'default': V, 'POST': S},
    'project_issue_detail': {'default': V, 'PATCH': S, 'DELETE': S},
    'project_contractual_milestones': {'default': V, 'POST': S},
    'project_contractual_milestone_detail': {'default': V, 'PATCH': S, 'DELETE': S},
    'project_contractual_milestone_revisions': {'default': V},
    'project_field_dashboard_summary': {'default': V},
    'project_activity_analysis': {'default': V},
    'project_float_analysis': {'default': V},
    'project_dashboard_summary': {'default': V},
    'project_float_trend': {'default': V},
    'users_list': {'default': A},
    'user_detail': {'default': A},
}

# Endpoints deliberately reachable without authentication (csrf_bootstrap,
# login_view, me_view — see auth_views.py) or without any role requirement
# beyond "has a session or not" (logout_view is idempotent for anonymous
# callers too) — carry no role_requirements attribute at all, by design.
PUBLIC_OR_UNRESTRICTED_ENDPOINTS = {'csrf_bootstrap', 'login_view', 'me_view', 'logout_view'}


def _iter_view_functions():
    """Every distinct view callback registered in the whole project (admin/
    static included), keyed by __name__. functools.wraps on require_role's
    wrapper means this is the real underlying function name."""
    resolver = get_resolver()
    seen = {}

    def walk(patterns):
        for p in patterns:
            if hasattr(p, 'url_patterns'):
                walk(p.url_patterns)
            elif hasattr(p, 'callback'):
                seen[p.callback.__name__] = p.callback
    walk(resolver.url_patterns)
    return seen


class PermissionAuditStructuralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        all_views = _iter_view_functions()
        # Scope to this app's own views — Django admin's own views (user
        # management UI, etc.) are not ours to audit and were never
        # decorated with our require_role.
        cls.views = {
            name: fn for name, fn in all_views.items()
            if name in EXPECTED_ROLES or name in PUBLIC_OR_UNRESTRICTED_ENDPOINTS
        }

    def test_every_expected_endpoint_actually_exists_and_is_decorated(self):
        missing = [name for name in EXPECTED_ROLES if name not in self.views]
        self.assertEqual(missing, [], f'Expected endpoint(s) not found in urls.py: {missing}')
        undecorated = [name for name in EXPECTED_ROLES if not hasattr(self.views[name], 'role_requirements')]
        self.assertEqual(undecorated, [], f'Endpoint(s) missing @require_role entirely: {undecorated}')

    def test_deployed_role_requirements_match_the_independently_documented_expectation(self):
        mismatches = []
        for name, expected in EXPECTED_ROLES.items():
            actual = getattr(self.views[name], 'role_requirements', None)
            if actual != expected:
                mismatches.append((name, expected, actual))
        self.assertEqual(mismatches, [], f'Deployed permission(s) differ from the documented matrix: {mismatches}')

    def test_public_or_unrestricted_endpoints_carry_no_role_requirement(self):
        for name in PUBLIC_OR_UNRESTRICTED_ENDPOINTS:
            self.assertIn(name, self.views, f'{name} not found among registered views')
            self.assertFalse(
                hasattr(self.views[name], 'role_requirements'),
                f'{name} was expected to be public/unrestricted but carries a role requirement',
            )

    def test_no_destructive_endpoint_was_left_at_viewer_or_scheduler_by_mistake(self):
        # Belt-and-suspenders beyond the exact-match check above: the
        # specific operations this review calls out as highest-risk must
        # never resolve to anything below ADMINISTRATOR for their
        # destructive method, however EXPECTED_ROLES above might be edited
        # in the future.
        destructive = {
            'project_detail': 'DELETE', 'project_version_detail': 'DELETE',
            'project_version_restore': 'default', 'projects_consolidation_apply': 'default',
        }
        for name, method_key in destructive.items():
            reqs = self.views[name].role_requirements
            self.assertEqual(reqs.get(method_key), A, f'{name}[{method_key}] must require ADMINISTRATOR')


def _user(username, role, password='pw-12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


@override_settings(AUTO_AUTH_TEST_USER=False)
class EndpointCategoryPermissionTests(TestCase):
    """Live HTTP requests for the categories this review names explicitly:
    imports, deletion, restoration, project consolidation, contractual
    milestone modifications, user administration — each exercised
    unauthenticated and as all three roles."""

    def setUp(self):
        self.viewer = _user('audit-viewer', V)
        self.scheduler = _user('audit-scheduler', S)
        self.admin = _user('audit-admin', A)
        self.project = Project.objects.create(name='Permission Audit Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', file_type='XER',
            activities_json=[], schedule_classification='CURRENT_UPDATE',
        )
        self.milestone = MilestoneDefinition.objects.create(
            project=self.project, activity_id='M1', milestone_category='CONTRACTUAL_COMPLETION',
        )

    # ── Imports ────────────────────────────────────────────────────────
    def test_import_commit_unauthenticated_401(self):
        self.assertEqual(self.client.post('/api/import/commit/', data={'projectName': 'X'}).status_code, 401)

    def test_import_commit_viewer_403(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post('/api/import/commit/', data={'projectName': 'X'}).status_code, 403)

    def test_import_commit_scheduler_allowed_past_the_permission_gate(self):
        # Proves Scheduler clears the 401/403 gate — a 422 (file parse
        # failure, since no real file is attached) confirms the request
        # reached the view body, not "blocked by permissions."
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/import/commit/', data={'projectName': 'X'})
        self.assertNotIn(resp.status_code, (401, 403))

    # ── Schedule-version deletion ──────────────────────────────────────
    def _delete_version_url(self):
        return f'/api/projects/{self.project.id}/versions/{self.version.id}/'

    def test_version_delete_unauthenticated_401(self):
        resp = self.client.delete(self._delete_version_url(), data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_version_delete_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.delete(self._delete_version_url(), data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_version_delete_scheduler_403(self):
        self.client.force_login(self.scheduler)
        resp = self.client.delete(self._delete_version_url(), data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_version_delete_administrator_200(self):
        self.client.force_login(self.admin)
        resp = self.client.delete(self._delete_version_url(), data=json.dumps({'confirmation': 'DELETE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    # ── Schedule-version restoration ───────────────────────────────────
    def _restore_url(self):
        return f'/api/projects/{self.project.id}/versions/{self.version.id}/restore/'

    def _soft_delete_version(self):
        self.version.is_deleted = True
        self.version.deleted_at = timezone.now()
        self.version.save(update_fields=['is_deleted', 'deleted_at'])

    def test_version_restore_unauthenticated_401(self):
        self._soft_delete_version()
        resp = self.client.post(self._restore_url(), data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_version_restore_viewer_403(self):
        self._soft_delete_version()
        self.client.force_login(self.viewer)
        resp = self.client.post(self._restore_url(), data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_version_restore_scheduler_403(self):
        self._soft_delete_version()
        self.client.force_login(self.scheduler)
        resp = self.client.post(self._restore_url(), data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_version_restore_administrator_200(self):
        self._soft_delete_version()
        self.client.force_login(self.admin)
        resp = self.client.post(self._restore_url(), data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    # ── Project consolidation (plan is read-only; apply is destructive) ─
    def test_consolidation_plan_viewer_allowed(self):
        self.client.force_login(self.viewer)
        resp = self.client.post('/api/projects/consolidation-plan/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

    def test_consolidation_apply_unauthenticated_401(self):
        resp = self.client.post('/api/projects/consolidation-apply/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_consolidation_apply_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.post('/api/projects/consolidation-apply/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_consolidation_apply_scheduler_403(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/projects/consolidation-apply/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_consolidation_apply_administrator_clears_the_permission_gate(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/api/projects/consolidation-apply/', data=json.dumps({}), content_type='application/json')
        self.assertNotIn(resp.status_code, (401, 403))

    # ── Contractual milestone modifications ────────────────────────────
    def _milestones_url(self):
        return f'/api/projects/{self.project.id}/contractual-milestones/'

    def _milestone_detail_url(self):
        return f'/api/projects/{self.project.id}/contractual-milestones/{self.milestone.id}/'

    def test_contractual_milestone_create_unauthenticated_401(self):
        resp = self.client.post(self._milestones_url(), data=json.dumps({'activityId': 'M2'}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_contractual_milestone_create_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.post(self._milestones_url(), data=json.dumps({'activityId': 'M2'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MilestoneDefinition.objects.filter(activity_id='M2').exists())

    def test_contractual_milestone_create_scheduler_201(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post(self._milestones_url(), data=json.dumps({'activityId': 'M2'}), content_type='application/json')
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_contractual_milestone_read_viewer_200(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self._milestones_url()).status_code, 200)

    def test_contractual_milestone_patch_unauthenticated_401(self):
        resp = self.client.patch(self._milestone_detail_url(), data=json.dumps({'notes': 'x'}), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_contractual_milestone_patch_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.patch(self._milestone_detail_url(), data=json.dumps({'notes': 'x'}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_contractual_milestone_patch_scheduler_200(self):
        self.client.force_login(self.scheduler)
        resp = self.client.patch(self._milestone_detail_url(), data=json.dumps({'notes': 'Updated by scheduler'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_contractual_milestone_delete_unauthenticated_401(self):
        resp = self.client.delete(self._milestone_detail_url())
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(MilestoneDefinition.objects.filter(pk=self.milestone.pk).exists())

    def test_contractual_milestone_delete_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.delete(self._milestone_detail_url())
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(MilestoneDefinition.objects.filter(pk=self.milestone.pk).exists())

    def test_contractual_milestone_delete_scheduler_200(self):
        self.client.force_login(self.scheduler)
        resp = self.client.delete(self._milestone_detail_url())
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(MilestoneDefinition.objects.filter(pk=self.milestone.pk).exists())

    # ── User administration ────────────────────────────────────────────
    def test_user_list_unauthenticated_401(self):
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 401)

    def test_user_list_viewer_403(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 403)

    def test_user_list_scheduler_403(self):
        self.client.force_login(self.scheduler)
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 403)

    def test_user_list_administrator_200(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 200)

    def test_user_create_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.post('/api/auth/users/', data=json.dumps({'username': 'nope', 'password': 'x12345!', 'role': V}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username='nope').exists())

    def test_user_create_scheduler_403(self):
        self.client.force_login(self.scheduler)
        resp = self.client.post('/api/auth/users/', data=json.dumps({'username': 'nope2', 'password': 'x12345!', 'role': V}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username='nope2').exists())

    def test_user_patch_viewer_403(self):
        self.client.force_login(self.viewer)
        resp = self.client.patch(f'/api/auth/users/{self.scheduler.id}/', data=json.dumps({'role': A}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.scheduler.profile.refresh_from_db()
        self.assertEqual(self.scheduler.profile.role, S)

    def test_user_patch_scheduler_403(self):
        self.client.force_login(self.scheduler)
        resp = self.client.patch(f'/api/auth/users/{self.viewer.id}/', data=json.dumps({'role': A}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.viewer.profile.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, V)

    def test_user_patch_administrator_200(self):
        self.client.force_login(self.admin)
        resp = self.client.patch(f'/api/auth/users/{self.viewer.id}/', data=json.dumps({'role': S}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)


class AutoAuthTestFixtureCannotEscapeTests(TestCase):
    """Confirms the AutoAuthTestMiddleware convenience (see
    scheduler/test_auth_middleware.py) that lets the ~1160 pre-existing
    tests run unmodified cannot leak outside the isolated test run."""

    def test_auto_auth_is_only_on_because_settings_detected_a_test_run(self):
        from django.conf import settings
        import sys
        self.assertTrue(settings.TESTING)
        self.assertIn('test', sys.argv)
        self.assertTrue(settings.AUTO_AUTH_TEST_USER)

    def test_the_setting_is_computed_not_hardcoded_true(self):
        # Reading settings.py's own source is the most direct proof this
        # isn't a stray `AUTO_AUTH_TEST_USER = True` left in by mistake —
        # it must be an expression keyed off sys.argv/pytest, so it is
        # False in any process that isn't `manage.py test`.
        import inspect
        import seglc_backend.settings as real_settings
        src = inspect.getsource(real_settings)
        self.assertIn('TESTING = "test" in sys.argv', src)
        self.assertNotIn('AUTO_AUTH_TEST_USER = True\n', src)

    @override_settings(AUTO_AUTH_TEST_USER=False)
    def test_with_the_flag_off_a_bare_request_is_genuinely_anonymous(self):
        resp = self.client.get('/api/projects/')
        self.assertEqual(resp.status_code, 401)
