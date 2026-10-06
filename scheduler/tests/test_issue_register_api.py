"""
Project Issue Tracking — API layer tests (SCHEDULEIQ — PROJECT ISSUE
TRACKING, Phase 7). Covers CRUD, the Resolve/Close workflow, role
permissions (including a direct-API, unauthenticated pass), activity
linking (single/multiple/missing), negative-float and contractual-
milestone schedule exposure, overdue logic, Field Dashboard aggregation,
persistence across re-imports, project-consolidation compatibility,
soft-delete compatibility, and audit-trail behavior.

ScheduleIQ never creates an Issue from schedule conditions — every issue
in these tests is created exactly as a human user would, through the API.
"""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from scheduler.models import (
    AuditLog, MilestoneDefinition, MitigationAction, Project, ProjectIssue,
    ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER, ScheduleUpload,
)


def _user(username, role, password='pw12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


def _act(code, name='Activity', tf=5.0, critical=False, driving=False, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'wbs': 'Area A', 'area': 'Area A', 'discipline': 'Electrical',
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'totalFloat': tf, 'pctComplete': 0.0,
        'isCritical': critical, 'onLongestPath': driving, 'isMilestone': milestone,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class IssueReferenceFormattingTests(TestCase):
    """Final Issue ID formatting adjustment: the permanent user-facing
    reference is ISS-{issue_number:04d}, always DERIVED from the
    authoritative issue_number — never a second stored value, never
    truncated past 4 digits, never used to decide numbering."""

    def test_reference_is_derived_not_stored_and_never_truncates(self):
        project = Project.objects.create(name='Reference Formatting Test')
        cases = [(1, 'ISS-0001'), (7, 'ISS-0007'), (42, 'ISS-0042'), (999, 'ISS-0999'), (1000, 'ISS-1000'), (10001, 'ISS-10001')]
        for number, expected in cases:
            issue = ProjectIssue.objects.create(project=project, issue_number=number, title=f'Case {number}')
            resp = self.client.get(f'/api/projects/{project.id}/issues/{issue.id}/')
            self.assertEqual(resp.json()['reference'], expected)
            self.assertEqual(str(issue), f'{expected} — Case {number} ({project.name})')


class IssueRegisterCrudTests(TestCase):
    """Basic create/list/detail/update/delete — AutoAuthTestMiddleware
    supplies a fixture Administrator identity, same as every other
    pre-existing endpoint test in this suite."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue CRUD Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1'), _act('A2')],
        )

    def test_create_issue_requires_title(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_and_list_issue(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/',
            data=json.dumps({
                'title': 'Steel delivery delayed', 'category': 'EQUIPMENT_DELIVERY', 'severity': 'HIGH',
                'owner': 'Procurement Lead', 'linkedActivityIds': ['A1'],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['title'], 'Steel delivery delayed')
        self.assertEqual(body['issueNumber'], 1)
        self.assertEqual(body['reference'], 'ISS-0001')
        self.assertEqual(body['status'], 'OPEN')
        self.assertTrue(body['createdBy'])

        listing = self.client.get(f'/api/projects/{self.project.id}/issues/')
        self.assertEqual(listing.status_code, 200)
        lbody = listing.json()
        self.assertEqual(len(lbody['issues']), 1)
        self.assertEqual(lbody['summary']['openCount'], 1)
        self.assertEqual(lbody['summary']['highCount'], 1)

    def test_sequential_issue_numbers_per_project(self):
        for i in range(3):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': f'Issue {i}'}), content_type='application/json',
            )
            self.assertEqual(resp.json()['issueNumber'], i + 1)

    def test_deleted_issue_number_is_never_reused(self):
        # Final pre-commit review, item 2: Issue IDs are permanent — a
        # deleted issue's number must never be handed to a later issue.
        # Numbering comes from Project.next_issue_number (a monotonic
        # counter), never from MAX(issue_number) among surviving rows,
        # which would let a deleted issue's number resurface.
        ids = []
        for i in range(3):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': f'Issue {i}'}), content_type='application/json',
            )
            ids.append(resp.json()['id'])
        self.assertEqual(
            [r.json()['issueNumber'] for r in [self.client.get(f'/api/projects/{self.project.id}/issues/{i}/') for i in ids]],
            [1, 2, 3],
        )
        # Delete the highest-numbered issue (3), then create a new one —
        # it must get 4, never a reused 3.
        self.client.delete(f'/api/projects/{self.project.id}/issues/{ids[2]}/')
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'Issue after delete'}), content_type='application/json',
        )
        self.assertEqual(resp.json()['issueNumber'], 4)

    def test_issue_numbers_are_project_isolated(self):
        other_project = Project.objects.create(name='Other Project for Numbering Test')
        resp1 = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'In project 1'}), content_type='application/json',
        )
        resp2 = self.client.post(
            f'/api/projects/{other_project.id}/issues/', data=json.dumps({'title': 'In project 2'}), content_type='application/json',
        )
        self.assertEqual(resp1.json()['issueNumber'], 1)
        self.assertEqual(resp2.json()['issueNumber'], 1)  # independent counter per project

    def test_invalid_category_rejected(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/',
            data=json.dumps({'title': 'X', 'category': 'NOT_A_REAL_CATEGORY'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_detail_get_patch_delete(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'QA/QC nonconformance'}), content_type='application/json',
        ).json()
        issue_id = created['id']

        get_resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue_id}/')
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['title'], 'QA/QC nonconformance')

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{issue_id}/',
            data=json.dumps({'severity': 'CRITICAL', 'owner': 'QA Manager'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        self.assertEqual(patch_resp.json()['severity'], 'CRITICAL')
        self.assertEqual(patch_resp.json()['owner'], 'QA Manager')
        self.assertTrue(patch_resp.json()['updatedBy'])

        delete_resp = self.client.delete(f'/api/projects/{self.project.id}/issues/{issue_id}/')
        self.assertEqual(delete_resp.status_code, 200)
        self.assertFalse(ProjectIssue.objects.filter(pk=issue_id).exists())

    def test_patch_unknown_status_rejected(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'X'}), content_type='application/json',
        ).json()
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{created["id"]}/',
            data=json.dumps({'status': 'NOT_A_REAL_STATUS'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_issue_not_found(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)

    def test_project_not_found(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/issues/')
        self.assertEqual(resp.status_code, 404)


class IssueResolveCloseWorkflowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Issue Workflow Test')
        self.issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Rebar shortage', status='OPEN')

    def test_resolving_stamps_actual_resolution_date(self):
        self.assertIsNone(self.issue.actual_resolution_date)
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'status': 'RESOLVED'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNotNone(resp.json()['actualResolutionDate'])
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.actual_resolution_date, timezone.localdate())

    def test_closing_stamps_actual_resolution_date_too(self):
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'status': 'CLOSED'}), content_type='application/json',
        )
        self.assertIsNotNone(resp.json()['actualResolutionDate'])

    def test_reopening_clears_actual_resolution_date(self):
        self.issue.status = 'RESOLVED'
        self.issue.actual_resolution_date = timezone.localdate()
        self.issue.save()
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'status': 'MONITORING'}), content_type='application/json',
        )
        self.assertIsNone(resp.json()['actualResolutionDate'])

    def test_explicit_actual_resolution_date_is_not_overwritten_by_auto_stamp(self):
        # Caller sets BOTH status and an explicit actualResolutionDate in the
        # same PATCH — the explicit value must win, not today's auto-stamp.
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'status': 'RESOLVED', 'actualResolutionDate': '2026-01-05'}), content_type='application/json',
        )
        self.assertEqual(resp.json()['actualResolutionDate'], '2026-01-05')


@override_settings(AUTO_AUTH_TEST_USER=False)
class IssueRegisterPermissionTests(TestCase):
    """Direct-API authorization — unauthenticated and each of the three
    roles, matching the tier the Phase-1 audit recommended: Viewer reads,
    Scheduler creates/edits/resolves, Administrator inherits everything
    Scheduler can do via the existing rank system (no special carve-out
    needed for 'full administration')."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue Permission Test')
        self.admin = _user('issue-admin', ROLE_ADMINISTRATOR, password='pw-1')
        self.scheduler = _user('issue-scheduler', ROLE_SCHEDULER, password='pw-2')
        self.viewer = _user('issue-viewer', ROLE_VIEWER, password='pw-3')
        self.issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Existing issue')
        # Keep the project's numbering counter consistent with this
        # directly-created fixture row, matching the invariant the create
        # view itself maintains (see test_deleted_issue_number_is_never_reused).
        self.project.next_issue_number = 2
        self.project.save(update_fields=['next_issue_number'])

    def _login(self, username, password):
        self.client.login(username=username, password=password)

    def test_unauthenticated_list_refused(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/')
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_create_refused(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'X'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_viewer_can_list_and_read(self):
        self._login('issue-viewer', 'pw-3')
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/')
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get(f'/api/projects/{self.project.id}/issues/{self.issue.id}/')
        self.assertEqual(resp2.status_code, 200)

    def test_viewer_cannot_create(self):
        self._login('issue-viewer', 'pw-3')
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'X'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_update_or_delete(self):
        self._login('issue-viewer', 'pw-3')
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'severity': 'HIGH'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 403)
        delete_resp = self.client.delete(f'/api/projects/{self.project.id}/issues/{self.issue.id}/')
        self.assertEqual(delete_resp.status_code, 403)

    def test_scheduler_can_create_update_resolve_delete(self):
        self._login('issue-scheduler', 'pw-2')
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'Scheduler-created issue'}), content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.content)
        new_id = create_resp.json()['id']
        self.assertEqual(create_resp.json()['createdBy'], 'issue-scheduler')

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{new_id}/',
            data=json.dumps({'status': 'RESOLVED'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()['updatedBy'], 'issue-scheduler')

        delete_resp = self.client.delete(f'/api/projects/{self.project.id}/issues/{new_id}/')
        self.assertEqual(delete_resp.status_code, 200)

    def test_administrator_has_full_access(self):
        self._login('issue-admin', 'pw-1')
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'Admin-created issue'}), content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 201)
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{self.issue.id}/',
            data=json.dumps({'severity': 'LOW'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200)

    def test_created_by_is_never_caller_supplied(self):
        # Even if a malicious/careless client sends its own createdBy, the
        # authenticated session identity must win — never trust a
        # caller-supplied username (the exact Phase 6 requirement).
        self._login('issue-scheduler', 'pw-2')
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/',
            data=json.dumps({'title': 'Spoofed author attempt', 'createdBy': 'totally-not-a-real-admin'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['createdBy'], 'issue-scheduler')


class IssueActivityLinkingAndExposureTests(TestCase):
    """Schedule exposure is computed fresh from the CURRENT version's live
    activities and reported separately from the human-set severity field —
    never fabricated, never used to override severity."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue Exposure Test')

    def _set_version(self, activities):
        return ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15', activities_json=activities,
        )

    def test_no_linked_activities_reports_unavailable_exposure(self):
        self._set_version([_act('A1')])
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='No link')
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        self.assertFalse(resp.json()['exposure']['hasLinkedActivities'])

    def test_single_linked_activity_with_negative_float(self):
        self._set_version([_act('A1', tf=-5.0)])
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Linked', linked_activity_ids=['A1'])
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        exposure = resp.json()['exposure']
        self.assertTrue(exposure['hasLinkedActivities'])
        self.assertTrue(exposure['anyNegativeFloat'])
        self.assertEqual(exposure['activities'][0]['floatCondition'], 'NEGATIVE')
        # Human severity is untouched by exposure — never auto-escalated.
        self.assertEqual(resp.json()['severity'], 'MEDIUM')

    def test_multiple_linked_activities_all_reported(self):
        self._set_version([_act('A1', tf=5.0), _act('A2', tf=-1.0), _act('A3', driving=True)])
        issue = ProjectIssue.objects.create(
            project=self.project, issue_number=1, title='Multi-link', linked_activity_ids=['A1', 'A2', 'A3'],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        exposure = resp.json()['exposure']
        self.assertEqual(len(exposure['activities']), 3)
        self.assertTrue(exposure['anyNegativeFloat'])
        self.assertTrue(exposure['anyOnDrivingPath'])

    def test_missing_activity_never_fabricates_a_replacement_mapping(self):
        self._set_version([_act('A1')])
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Dangling link', linked_activity_ids=['GHOST'])
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        a = resp.json()['exposure']['activities'][0]
        self.assertFalse(a['available'])
        self.assertEqual(a['reason'], 'Activity GHOST not found in selected schedule version.')

    def test_contractual_milestone_exposure_flagged(self):
        self._set_version([
            _act('A1', tf=5.0, successors=[{'actId': 'MS1', 'relType': 'FS', 'lagDays': 0}]),
            _act('MS1', milestone=True, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ])
        MilestoneDefinition.objects.create(
            project=self.project, activity_id='MS1', activity_name='Substantial Completion',
            milestone_category='CONTRACTUAL_COMPLETION',
        )
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Reaches contractual milestone', linked_activity_ids=['A1'])
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        self.assertTrue(resp.json()['exposure']['anyReachesContractualMilestone'])

    def test_no_schedule_version_at_all_degrades_gracefully(self):
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='No version yet', linked_activity_ids=['A1'])
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        self.assertEqual(resp.status_code, 200)
        exposure = resp.json()['exposure']
        self.assertEqual(exposure['activities'], [])
        self.assertIn('reason', exposure)


class IssueContractualMilestoneLinkTests(TestCase):
    """Final pre-commit review, item 17: 'linked contractual milestone
    where supported' must actually round-trip through the API, not just
    exist as an unused model field."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue Milestone Link Test')
        self.milestone = MilestoneDefinition.objects.create(
            project=self.project, activity_id='MS1', activity_name='Substantial Completion',
            milestone_category='CONTRACTUAL_COMPLETION',
        )

    def test_create_with_linked_milestone(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/',
            data=json.dumps({'title': 'Delay to substantial completion', 'linkedMilestoneId': str(self.milestone.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['linkedMilestoneId'], str(self.milestone.id))

    def test_reject_milestone_from_another_project(self):
        other_project = Project.objects.create(name='Other Project')
        other_milestone = MilestoneDefinition.objects.create(
            project=other_project, activity_id='MS2', activity_name='Belongs elsewhere',
            milestone_category='CONTRACTUAL_COMPLETION',
        )
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/',
            data=json.dumps({'title': 'Should fail', 'linkedMilestoneId': str(other_milestone.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_can_link_and_unlink_milestone(self):
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Needs a milestone link')
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{issue.id}/',
            data=json.dumps({'linkedMilestoneId': str(self.milestone.id)}), content_type='application/json',
        )
        self.assertEqual(resp.json()['linkedMilestoneId'], str(self.milestone.id))
        resp2 = self.client.patch(
            f'/api/projects/{self.project.id}/issues/{issue.id}/',
            data=json.dumps({'linkedMilestoneId': ''}), content_type='application/json',
        )
        self.assertIsNone(resp2.json()['linkedMilestoneId'])

    def test_milestone_deletion_clears_the_link_without_deleting_the_issue(self):
        # linked_milestone uses on_delete=SET_NULL — the issue itself must
        # survive the removal of a milestone it was linked to.
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Link survives', linked_milestone=self.milestone)
        self.milestone.delete()
        issue.refresh_from_db()
        self.assertIsNone(issue.linked_milestone_id)
        self.assertTrue(ProjectIssue.objects.filter(pk=issue.id).exists())


class IssueOverdueLogicTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Issue Overdue Test')

    def test_open_issue_past_target_date_is_overdue(self):
        issue = ProjectIssue.objects.create(
            project=self.project, issue_number=1, title='Overdue', status='OPEN',
            target_resolution_date=timezone.localdate() - timedelta(days=5),
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        self.assertTrue(resp.json()['overdue'])

    def test_resolved_issue_past_target_date_is_not_overdue(self):
        issue = ProjectIssue.objects.create(
            project=self.project, issue_number=1, title='Resolved', status='RESOLVED',
            target_resolution_date=timezone.localdate() - timedelta(days=5),
            actual_resolution_date=timezone.localdate(),
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        self.assertFalse(resp.json()['overdue'])

    def test_overdue_only_filter(self):
        ProjectIssue.objects.create(
            project=self.project, issue_number=1, title='Overdue', status='OPEN',
            target_resolution_date=timezone.localdate() - timedelta(days=5),
        )
        ProjectIssue.objects.create(project=self.project, issue_number=2, title='Fine', status='OPEN')
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/?overdueOnly=true')
        body = resp.json()
        self.assertEqual(len(body['issues']), 1)
        self.assertEqual(body['summary']['overdueCount'], 1)


class IssueFieldDashboardIntegrationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Issue Field Dashboard Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', tf=-3.0)],
        )

    def test_placeholder_replaced_with_real_counts(self):
        ProjectIssue.objects.create(project=self.project, issue_number=1, title='Open critical', status='OPEN', severity='CRITICAL')
        ProjectIssue.objects.create(
            project=self.project, issue_number=2, title='Overdue high', status='MONITORING', severity='HIGH',
            target_resolution_date=timezone.localdate() - timedelta(days=1),
        )
        ProjectIssue.objects.create(project=self.project, issue_number=3, title='Closed', status='CLOSED', severity='CRITICAL')

        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        self.assertEqual(resp.status_code, 200, resp.content)
        issues = resp.json()['projectIssues']
        self.assertTrue(issues['available'])
        self.assertTrue(issues['configured'])
        self.assertNotIn('is not yet configured', str(issues))
        self.assertEqual(issues['openCount'], 1)
        self.assertEqual(issues['criticalCount'], 1)
        self.assertEqual(issues['highCount'], 1)
        self.assertEqual(issues['overdueCount'], 1)
        # Closed issue excluded from active counts entirely.
        self.assertEqual(len(issues['topIssues']), 2)

    def test_empty_register_still_reports_real_zero_counts_not_unconfigured(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        issues = resp.json()['projectIssues']
        self.assertTrue(issues['configured'])
        self.assertEqual(issues['openCount'], 0)
        self.assertEqual(issues['topIssues'], [])

    def test_top_issues_prioritize_overdue_and_exposure_over_plain_severity(self):
        ProjectIssue.objects.create(project=self.project, issue_number=1, title='Low, linked to negative float', severity='LOW', linked_activity_ids=['A1'])
        ProjectIssue.objects.create(project=self.project, issue_number=2, title='Critical, no link', severity='CRITICAL')
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        top = resp.json()['projectIssues']['topIssues']
        self.assertEqual(top[0]['title'], 'Low, linked to negative float')


class IssuePersistenceAcrossReimportTests(TestCase):
    """ProjectIssue is project-scoped, not version-scoped — a new import
    must never delete or alter existing issue rows (Phase 7's explicit
    'persistence across re-imports' requirement)."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue Reimport Test')
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-01', activities_json=[_act('A1', tf=5.0)],
        )
        self.issue = ProjectIssue.objects.create(
            project=self.project, issue_number=1, title='Persists across reimport', linked_activity_ids=['A1'],
        )

    def test_issue_survives_new_version_import_and_exposure_updates(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15', activities_json=[_act('A1', tf=-9.0)],
            upload_timestamp=timezone.now() + timedelta(days=1),
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{self.issue.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['id'], str(self.issue.id))
        # Exposure now reflects the NEW current version, not the stale one.
        self.assertTrue(resp.json()['exposure']['anyNegativeFloat'])
        self.assertEqual(ProjectIssue.objects.filter(project=self.project).count(), 1)


class IssueSoftDeleteCompatibilityTests(TestCase):
    """A soft-deleted schedule version must never resurface as 'current'
    for issue exposure — same guarantee _resolve_latest_version already
    gives every other analysis endpoint."""

    def setUp(self):
        self.project = Project.objects.create(name='Issue Soft Delete Test')
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-01', activities_json=[_act('A1', tf=5.0)],
        )
        self.v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15', activities_json=[_act('A1', tf=-9.0)],
            upload_timestamp=timezone.now() + timedelta(days=1),
        )
        self.issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='Soft delete test', linked_activity_ids=['A1'])

    def test_exposure_ignores_soft_deleted_current_version(self):
        self.v2.is_deleted = True
        self.v2.deleted_at = timezone.now()
        self.v2.save()
        resp = self.client.get(f'/api/projects/{self.project.id}/issues/{self.issue.id}/')
        self.assertEqual(resp.status_code, 200)
        # Falls back to v1 (tf=5.0), never the soft-deleted v2 (tf=-9.0).
        self.assertFalse(resp.json()['exposure']['anyNegativeFloat'])

    def test_field_dashboard_also_ignores_soft_deleted_version(self):
        self.v2.is_deleted = True
        self.v2.deleted_at = timezone.now()
        self.v2.save()
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        self.assertEqual(resp.status_code, 200)


class IssueAuditTrailTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Issue Audit Test')

    def test_create_writes_audit_log(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'Audited creation'}), content_type='application/json',
        )
        issue_id = resp.json()['id']
        entries = AuditLog.objects.filter(object_type='ProjectIssue', object_id=issue_id, action='CREATE_ISSUE')
        self.assertEqual(entries.count(), 1)
        self.assertTrue(entries.first().user)

    def test_status_change_logged_distinctly_from_plain_update(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/issues/', data=json.dumps({'title': 'X'}), content_type='application/json',
        ).json()
        self.client.patch(
            f'/api/projects/{self.project.id}/issues/{created["id"]}/',
            data=json.dumps({'owner': 'New owner'}), content_type='application/json',
        )
        self.client.patch(
            f'/api/projects/{self.project.id}/issues/{created["id"]}/',
            data=json.dumps({'status': 'RESOLVED'}), content_type='application/json',
        )
        self.assertTrue(AuditLog.objects.filter(object_id=created['id'], action='UPDATE_ISSUE').exists())
        self.assertTrue(AuditLog.objects.filter(object_id=created['id'], action='UPDATE_STATUS_ISSUE').exists())

    def test_delete_writes_audit_log_with_previous_value(self):
        issue = ProjectIssue.objects.create(project=self.project, issue_number=1, title='To be deleted')
        self.client.delete(f'/api/projects/{self.project.id}/issues/{issue.id}/')
        entry = AuditLog.objects.get(object_type='ProjectIssue', object_id=str(issue.id), action='DELETE_ISSUE')
        self.assertEqual(entry.previous_value['title'], 'To be deleted')


class IssueMitigationActionLinkTests(TestCase):
    """MitigationAction.issue (the nullable FK extension) — confirms the
    reuse-not-duplicate wiring actually works end to end, through the ORM
    AND through the existing mitigation-actions API (final pre-commit
    review, item 17: an issue's mitigation actions must be reachable, not
    just a model field nothing ever populates)."""

    def test_mitigation_action_can_link_to_an_issue(self):
        project = Project.objects.create(name='Issue Mitigation Link Test')
        issue = ProjectIssue.objects.create(project=project, issue_number=1, title='Needs a mitigation action')
        action = MitigationAction.objects.create(project=project, issue=issue, description='Expedite air freight')
        self.assertEqual(issue.mitigation_actions.count(), 1)
        self.assertEqual(action.issue_id, issue.id)

    def test_mitigation_action_api_creates_and_links_to_an_issue(self):
        project = Project.objects.create(name='Issue Mitigation API Link Test')
        issue = ProjectIssue.objects.create(project=project, issue_number=1, title='Needs a mitigation action')
        resp = self.client.post(
            f'/api/projects/{project.id}/mitigation-actions/',
            data=json.dumps({'description': 'Expedite air freight', 'owner': 'Procurement', 'issueId': str(issue.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['issueId'], str(issue.id))

    def test_mitigation_action_api_rejects_issue_from_another_project(self):
        project = Project.objects.create(name='Issue Mitigation API Link Test A')
        other_project = Project.objects.create(name='Issue Mitigation API Link Test B')
        issue = ProjectIssue.objects.create(project=other_project, issue_number=1, title='Belongs elsewhere')
        resp = self.client.post(
            f'/api/projects/{project.id}/mitigation-actions/',
            data=json.dumps({'description': 'Should fail', 'issueId': str(issue.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_mitigation_actions_api_filters_by_issue(self):
        project = Project.objects.create(name='Issue Mitigation API Filter Test')
        issue_a = ProjectIssue.objects.create(project=project, issue_number=1, title='Issue A')
        issue_b = ProjectIssue.objects.create(project=project, issue_number=2, title='Issue B')
        MitigationAction.objects.create(project=project, issue=issue_a, description='For A')
        MitigationAction.objects.create(project=project, issue=issue_b, description='For B')
        resp = self.client.get(f'/api/projects/{project.id}/mitigation-actions/?issueId={issue_a.id}')
        actions = resp.json()['actions']
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['description'], 'For A')
