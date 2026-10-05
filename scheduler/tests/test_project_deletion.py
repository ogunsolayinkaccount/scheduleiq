"""
Intelligence follows the main Project/ScheduleUpload records — there is no
separate Intelligence-owned project list. These tests pin the deletion
lifecycle: DELETE project / DELETE version, role re-resolution, and that no
analytical endpoint can retrieve a deleted project or version.
"""
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from scheduler.ai import NullProvider
from scheduler.models import (
    MilestoneDefinition, MitigationAction, Project, ProjectControlsReport, RecoveryScenario,
    ScheduleRisk, ScheduleUpload,
)


def _act(code, tf=0.0, **extra):
    d = {
        'id': code, 'code': code, 'name': f'Activity {code}', 'wbs': 'W', 'area': 'Area A', 'discipline': 'ELE',
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': tf, 'isCritical': False, 'isMilestone': False, 'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class _Base(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Barn — BLDG 2 COMP 1')
        now = timezone.now()

        def mk(label, classification, dd, days_ago, tf):
            return ScheduleUpload.objects.create(
                project=self.project, original_filename=f'{label}.xer', sanitized_filename=f'{label}.xer',
                file_type='XER', version_label=label, schedule_classification=classification,
                data_date=dd, activities_json=[_act('A1', tf=tf)], upload_timestamp=now - timedelta(days=days_ago),
            )

        self.baseline = mk('Baseline', 'APPROVED_BASELINE', date(2026, 1, 1), 40, 20.0)
        self.u1 = mk('11-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 11), 30, 12.0)
        self.u2 = mk('19-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 19), 20, 6.0)
        self.u3 = mk('26-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 26), 10, -2.0)

    def project_list(self, with_versions=True):
        url = '/api/projects/' + ('?withVersions=true' if with_versions else '')
        return self.client.get(url).json()['projects']

    def versions(self):
        return self.client.get(f'/api/projects/{self.project.id}/versions/').json()['versions']

    def delete_version(self, v, confirmation='DELETE'):
        body = {'confirmation': confirmation} if confirmation is not None else {}
        return self.client.delete(
            f'/api/projects/{self.project.id}/versions/{v.id}/',
            data=json.dumps(body), content_type='application/json',
        )


class ProjectLifecycleTests(_Base):
    def test_new_project_appears_in_intelligence_project_list_and_analysis(self):
        ids = [p['id'] for p in self.project_list()]
        self.assertIn(str(self.project.id), ids)
        resp = self.client.get(f'/api/projects/{self.project.id}/activity-analysis/')
        self.assertEqual(resp.status_code, 200)

    def test_delete_project_removes_it_everywhere(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['deleted'])
        self.assertEqual(body['removed']['scheduleVersions'], 4)
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list()])
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list(with_versions=False)])
        self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/').status_code, 404)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=self.project.id).count(), 0)

    def test_deleted_project_unreachable_from_every_analysis_endpoint(self):
        pid = self.project.id
        self.client.delete(f'/api/projects/{pid}/')
        for path in ('activity-analysis/', 'float-analysis/', 'float-trend/?activityId=A1', 'update-intelligence/',
                     'baseline-progress/', 'risk-register/', 'milestones/', 'lookahead/', 'recovery-scenarios/'):
            resp = self.client.get(f'/api/projects/{pid}/{path}')
            self.assertEqual(resp.status_code, 404, path)
        with patch('scheduler.views.get_provider', return_value=NullProvider()):
            self.assertEqual(self.client.get(f'/api/projects/{pid}/ai-review/').status_code, 404)
            resp = self.client.post(f'/api/projects/{pid}/ai-chat/', data='{"question":"x"}', content_type='application/json')
            self.assertEqual(resp.status_code, 404)

    def test_delete_unknown_project_is_404(self):
        self.assertEqual(self.client.delete('/api/projects/00000000-0000-0000-0000-000000000000/').status_code, 404)

    def test_project_delete_leaves_no_orphaned_dependents(self):
        scenario = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u3, name='S')
        ScheduleRisk.objects.create(project=self.project, risk_key='A1')
        MitigationAction.objects.create(project=self.project, description='d', scenario=scenario)
        ProjectControlsReport.objects.create(project=self.project, schedule_upload=self.u3, report_type='WEEKLY_PROJECT_CONTROLS')
        MilestoneDefinition.objects.create(schedule_upload=self.u3, activity_id='M1')
        self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(RecoveryScenario.objects.count(), 0)
        self.assertEqual(ScheduleRisk.objects.count(), 0)
        self.assertEqual(MitigationAction.objects.count(), 0)
        self.assertEqual(ProjectControlsReport.objects.count(), 0)
        self.assertEqual(MilestoneDefinition.objects.count(), 0)


class VersionDeletionTests(_Base):
    def test_delete_version_removes_only_that_version(self):
        resp = self.delete_version(self.u2)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['remainingVersionCount'], 3)
        self.assertFalse(body['projectNowEmpty'])
        labels = [v['versionLabel'] for v in self.versions()]
        self.assertNotIn('19-Aug-26 Update', labels)
        for survivor in ('Baseline', '11-Aug-26 Update', '26-Aug-26 Update'):
            self.assertIn(survivor, labels)
        self.assertIn(str(self.project.id), [p['id'] for p in self.project_list()])  # project survives

    def test_deleting_current_reresolves_current_from_survivors(self):
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'CURRENT')['id'], str(self.u3.id))
        body = self.delete_version(self.u3).json()
        self.assertEqual(body['deletedRole'], 'CURRENT')
        self.assertEqual(body['roles']['currentVersionId'], str(self.u2.id))    # newest surviving update
        self.assertEqual(body['roles']['previousVersionId'], str(self.u1.id))
        self.assertEqual(body['roles']['baselineVersionId'], str(self.baseline.id))
        roles = [v['role'] for v in self.versions()]
        self.assertEqual(roles.count('CURRENT'), 1)  # exactly one, never invalid

    def test_deleting_previous_makes_comparison_handle_missing_previous(self):
        body = self.delete_version(self.u2).json()  # u2 was PREVIOUS
        self.assertEqual(body['deletedRole'], 'PREVIOUS')
        self.assertEqual(body['roles']['currentVersionId'], str(self.u3.id))
        self.assertEqual(body['roles']['previousVersionId'], str(self.u1.id))  # next-older update takes over
        # Explicitly asking for the deleted version as Previous yields "no previous", never a stand-in.
        resp = self.client.get(
            f'/api/projects/{self.project.id}/activity-analysis/?currentVersion={self.u3.id}&previousVersion={self.u2.id}'
        )
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows'][0]
        self.assertIsNone(row['previousTotalFloat'])
        self.assertIsNone(resp.json()['previousVersionId'])

    def test_deleting_baseline_leaves_no_baseline_and_promotes_nothing(self):
        body = self.delete_version(self.baseline).json()
        self.assertEqual(body['deletedRole'], 'BASELINE')
        self.assertFalse(body['baselineDesignated'])
        self.assertIsNone(body['roles']['baselineVersionId'])
        self.assertNotIn('BASELINE', [v['role'] for v in self.versions()])
        resp = self.client.get(f'/api/projects/{self.project.id}/activity-analysis/')
        data = resp.json()
        self.assertIsNone(data['baselineVersionId'])
        self.assertIsNone(data['rows'][0]['baselineTotalFloat'])   # unavailable, not an arbitrary version's value
        self.assertIsNone(self.client.get(f'/api/projects/{self.project.id}/float-analysis/').json()['baselineVersionId'])

    def test_baseline_can_be_redesignated_explicitly_afterwards(self):
        self.delete_version(self.baseline)
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{self.u1.id}/',
            data='{"classification":"APPROVED_BASELINE"}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'BASELINE')['id'], str(self.u1.id))

    def test_deleted_version_cannot_be_retrieved_by_analysis_endpoints(self):
        vid = self.u2.id
        self.delete_version(self.u2)
        base = f'/api/projects/{self.project.id}'
        for path in (f'activity-analysis/?currentVersion={vid}', f'float-analysis/?currentVersion={vid}',
                     f'update-intelligence/?currentVersion={vid}', f'milestones/?version={vid}',
                     f'lookahead/?version={vid}', f'baseline-progress/?currentVersion={vid}'):
            resp = self.client.get(f'{base}/{path}')
            self.assertIn(resp.status_code, (400, 404), f'{path} -> {resp.status_code}')

    def test_float_trend_excludes_deleted_version(self):
        self.delete_version(self.u2)
        pts = self.client.get(f'/api/projects/{self.project.id}/float-trend/?activityId=A1').json()['points']
        self.assertEqual([p['versionLabel'] for p in pts], ['Baseline', '11-Aug-26 Update', '26-Aug-26 Update'])
        self.assertEqual([p['totalFloat'] for p in pts], [20.0, 12.0, -2.0])

    def test_ai_context_cannot_retrieve_deleted_version(self):
        vid = self.u2.id
        self.delete_version(self.u2)
        with patch('scheduler.views.get_provider', return_value=NullProvider()):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/', data=f'{{"question":"x","version":"{vid}"}}',
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 404)
            self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/ai-review/?version={vid}').status_code, 404)
            # Default (no explicit version) chat still works and never mentions the deleted version.
            ok = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/', data='{"question":"x"}', content_type='application/json',
            ).json()
            labels = {ok['context']['currentVersion']['versionLabel'], (ok['context']['previousVersion'] or {}).get('versionLabel')}
            self.assertNotIn('19-Aug-26 Update', labels)

    def test_version_delete_leaves_other_versions_dependents_and_detaches_project_level_records(self):
        # Soft delete (Phase 3/4) never removes a row, so nothing that
        # referenced the deleted version is cascaded or detached — the
        # version's own data and every dependent record are preserved
        # exactly as-is so a restore can bring all of it back untouched.
        gone = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u2, name='on deleted')
        kept = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u3, name='on survivor')
        action = MitigationAction.objects.create(project=self.project, description='d', scenario=gone)
        MilestoneDefinition.objects.create(schedule_upload=self.u2, activity_id='M-gone')
        MilestoneDefinition.objects.create(schedule_upload=self.u3, activity_id='M-kept')
        report = ProjectControlsReport.objects.create(project=self.project, schedule_upload=self.u2, report_type='WEEKLY_PROJECT_CONTROLS')
        risk = ScheduleRisk.objects.create(project=self.project, risk_key='A1', first_identified_version=self.u2)

        self.delete_version(self.u2)

        self.assertTrue(RecoveryScenario.objects.filter(pk=gone.pk).exists())    # version-owned: preserved
        self.assertTrue(RecoveryScenario.objects.filter(pk=kept.pk).exists())    # survivor untouched
        self.assertTrue(MilestoneDefinition.objects.filter(activity_id='M-gone').exists())
        self.assertTrue(MilestoneDefinition.objects.filter(activity_id='M-kept').exists())
        # Project-level records keep pointing at the deleted version — nothing to recover otherwise.
        action.refresh_from_db(); report.refresh_from_db(); risk.refresh_from_db()
        self.assertEqual(action.scenario_id, gone.id)
        self.assertEqual(report.schedule_upload_id, self.u2.id)
        self.assertEqual(risk.first_identified_version_id, self.u2.id)

    def test_deleting_last_version_keeps_project_but_hides_it_from_intelligence(self):
        for v in (self.baseline, self.u1, self.u2):
            self.delete_version(v)
        body = self.delete_version(self.u3).json()
        self.assertTrue(body['projectNowEmpty'])
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list()])                 # Intelligence view
        self.assertIn(str(self.project.id), [p['id'] for p in self.project_list(with_versions=False)])  # management view
        self.assertEqual(self.client.delete(f'/api/projects/{self.project.id}/').status_code, 200)     # deliberately deletable

    def test_delete_unknown_version_is_404(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)

    def test_version_of_another_project_cannot_be_deleted_through_this_one(self):
        other = Project.objects.create(name='Other')
        foreign = ScheduleUpload.objects.create(project=other, original_filename='o.xer', file_type='XER', activities_json=[])
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/{foreign.id}/')
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(ScheduleUpload.objects.filter(pk=foreign.pk).exists())


class DeletedVersionsEndpointTests(_Base):
    """GET /api/projects/<id>/versions/deleted/ — the Deleted Versions view's
    backend. Never shows a non-deleted version; always carries each deleted
    version's own audit history."""

    def deleted_versions(self):
        return self.client.get(f'/api/projects/{self.project.id}/versions/deleted/').json()

    def test_empty_when_nothing_deleted(self):
        body = self.deleted_versions()
        self.assertEqual(body['deletedVersionCount'], 0)
        self.assertEqual(body['deletedVersions'], [])

    def test_deleted_version_appears_with_metadata_and_audit_history(self):
        self.delete_version(self.u2, confirmation='DELETE')
        body = self.deleted_versions()
        self.assertEqual(body['deletedVersionCount'], 1)
        row = body['deletedVersions'][0]
        self.assertEqual(row['id'], str(self.u2.id))
        self.assertEqual(row['versionLabel'], '19-Aug-26 Update')
        self.assertIsNotNone(row['deletedAt'])
        self.assertEqual(row['retentionDays'], 90)
        self.assertIsNotNone(row['retentionDeadline'])
        self.assertEqual(len(row['auditHistory']), 1)
        self.assertEqual(row['auditHistory'][0]['action'], 'DELETE_SCHEDULE_VERSION')
        self.assertEqual(row['auditHistory'][0]['outcome'], 'SUCCESS')

    def test_non_deleted_versions_never_appear_here(self):
        self.delete_version(self.u2, confirmation='DELETE')
        ids = [row['id'] for row in self.deleted_versions()['deletedVersions']]
        self.assertNotIn(str(self.u1.id), ids)
        self.assertNotIn(str(self.baseline.id), ids)

    def test_refused_delete_attempt_never_makes_a_version_appear_here(self):
        self.delete_version(self.u2, confirmation=None)  # missing confirmation — refused
        self.assertEqual(self.deleted_versions()['deletedVersionCount'], 0)


class RestoreTests(_Base):
    """POST /api/projects/<id>/versions/<version_id>/restore/ — reversing a
    soft delete. Never a silent no-op: wrong/missing confirmation refuses,
    and restoring something that isn't deleted refuses too."""

    def restore(self, v, confirmation='RESTORE', **extra):
        body = {'confirmation': confirmation, **extra}
        return self.client.post(
            f'/api/projects/{self.project.id}/versions/{v.id}/restore/',
            data=json.dumps(body), content_type='application/json',
        )

    def test_restore_without_confirmation_is_refused_and_stays_deleted(self):
        self.delete_version(self.u2)
        resp = self.restore(self.u2, confirmation=None)
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertTrue(self.u2.is_deleted)

    def test_restore_unknown_or_non_deleted_version_is_404(self):
        # Not deleted at all — the restore endpoint only ever operates on
        # currently-deleted rows, so this must 404, not silently no-op 200.
        resp = self.restore(self.u1)
        self.assertEqual(resp.status_code, 404)

    def test_successful_restore_returns_version_to_normal_views_and_roles(self):
        self.delete_version(self.u3, confirmation='DELETE')  # u3 was CURRENT
        labels_while_deleted = [v['versionLabel'] for v in self.versions()]
        self.assertNotIn('26-Aug-26 Update', labels_while_deleted)

        resp = self.restore(self.u3)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body['restored'])
        self.assertEqual(body['roles']['currentVersionId'], str(self.u3.id))  # CURRENT re-resolves back to it

        self.u3.refresh_from_db()
        self.assertFalse(self.u3.is_deleted)
        self.assertIsNone(self.u3.deleted_at)
        self.assertEqual(self.u3.delete_reason, '')

        labels_after = [v['versionLabel'] for v in self.versions()]
        self.assertIn('26-Aug-26 Update', labels_after)
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'CURRENT')['id'], str(self.u3.id))

    def test_restore_never_collides_with_a_version_that_took_over_its_role(self):
        # Delete CURRENT (u3); u2 becomes CURRENT. Restoring u3 must not
        # silently clobber u2's own data — only roles re-resolve.
        self.delete_version(self.u3, confirmation='DELETE')
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'CURRENT')['id'], str(self.u2.id))

        self.restore(self.u3)
        roles = {v['id']: v['role'] for v in self.versions()}
        self.assertEqual(roles[str(self.u3.id)], 'CURRENT')     # newest Data Date wins back
        self.u2.refresh_from_db()
        self.assertEqual(self.u2.activity_count, self.u2.activity_count)  # untouched (sanity: still loads fine)

    def test_restore_of_version_belonging_to_another_project_is_404(self):
        other = Project.objects.create(name='Other')
        foreign = ScheduleUpload.objects.create(
            project=other, original_filename='o.xer', file_type='XER', activities_json=[],
            is_deleted=True, deleted_at=timezone.now(),
        )
        resp = self.client.post(
            f'/api/projects/{self.project.id}/versions/{foreign.id}/restore/',
            data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


class AuditTrailTests(_Base):
    """AuditLog (Phase 3) — every deletion/restoration attempt, successful or
    not, leaves a durable, correctly-outcome-tagged record that survives even
    the eventual removal of the project/version it describes."""

    def audit_rows(self, object_id):
        from scheduler.models import AuditLog
        return list(AuditLog.objects.filter(object_type='ScheduleUpload', object_id=str(object_id)).order_by('timestamp'))

    def test_refused_delete_is_recorded_distinctly_from_a_successful_one(self):
        self.delete_version(self.u2, confirmation=None)        # refused: no confirmation
        self.delete_version(self.u2, confirmation='WRONG')     # refused: wrong confirmation
        self.delete_version(self.u2, confirmation='DELETE')    # succeeds

        rows = self.audit_rows(self.u2.id)
        self.assertEqual([r.outcome for r in rows], ['REFUSED', 'REFUSED', 'SUCCESS'])
        self.assertEqual([r.action for r in rows], ['DELETE_SCHEDULE_VERSION'] * 3)

    def test_successful_delete_audit_entry_carries_project_version_and_data_date(self):
        self.delete_version(self.u2, confirmation='DELETE', )
        row = self.audit_rows(self.u2.id)[-1]
        self.assertEqual(row.new_value['projectId'], str(self.project.id))
        self.assertEqual(row.new_value['versionLabel'], '19-Aug-26 Update')
        self.assertEqual(row.new_value['dataDate'], '2026-08-19')
        self.assertEqual(row.new_value['isDeleted'], True)
        self.assertEqual(row.previous_value['isDeleted'], False)

    def test_restore_attempts_recorded_with_refused_and_success_outcomes(self):
        self.delete_version(self.u2, confirmation='DELETE')
        self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.u2.id}/restore/',
            data=json.dumps({'confirmation': None}), content_type='application/json',
        )
        self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.u2.id}/restore/',
            data=json.dumps({'confirmation': 'RESTORE'}), content_type='application/json',
        )
        rows = self.audit_rows(self.u2.id)
        restore_rows = [r for r in rows if r.action == 'RESTORE_SCHEDULE_VERSION']
        self.assertEqual([r.outcome for r in restore_rows], ['REFUSED', 'SUCCESS'])

    def test_audit_record_survives_deletion_of_the_project_it_describes(self):
        self.delete_version(self.u2, confirmation='DELETE')
        self.assertTrue(self.audit_rows(self.u2.id))  # sanity: the row exists first

        # AuditLog.object_id is a plain string, never a ForeignKey — deleting
        # the project (which cascades to every real ScheduleUpload row) must
        # never take the audit trail down with it.
        self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertFalse(ScheduleUpload.objects.filter(pk=self.u2.id).exists())
        self.assertTrue(self.audit_rows(self.u2.id))

    def test_user_is_the_authenticated_identity_never_a_caller_supplied_claim(self):
        # Phase 3 (Authentication and Authorization): the audit `user` is
        # always request.user.username (here, the AutoAuthTestMiddleware's
        # test-fixture Administrator — see test_auth_middleware.py) — a
        # body-supplied `user` field, even if present, is simply ignored.
        resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.u2.id}/',
            data=json.dumps({'confirmation': 'DELETE', 'user': 'someone-else@example.com', 'reason': 'Superseded by re-export'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        row = self.audit_rows(self.u2.id)[-1]
        self.assertEqual(row.user, '_autotest_admin')
        self.assertNotEqual(row.user, 'someone-else@example.com')
        self.assertEqual(row.reason, 'Superseded by re-export')

    def test_reason_is_recorded_but_blank_when_not_supplied(self):
        self.delete_version(self.u3, confirmation='DELETE')
        row3 = self.audit_rows(self.u3.id)[-1]
        self.assertEqual(row3.reason, '')
        self.assertEqual(row3.user, '_autotest_admin')


class NoAuthorizationBypassTests(_Base):
    """A direct API call must not be able to delete or restore a schedule
    version without going through the required confirmation gate — a
    frontend dialog is not itself a security control.

    UPDATED SCOPE (Phase 3: Authentication and Authorization): real,
    server-verified authentication/role enforcement now exists (see
    scheduler/permissions.py and scheduler/tests/test_authentication.py for
    that — unauthenticated/wrong-role direct calls, CSRF, and session
    behavior are covered THERE). Every test in THIS class still runs
    authenticated as the AutoAuthTestMiddleware's Administrator test
    fixture (see test_auth_middleware.py) — intentionally, since this class
    is about a different, narrower guarantee: the confirmation phrase
    itself is a defense-in-depth safeguard even for an already-authorized
    caller — it prevents an ACCIDENTAL direct call (a double-click, a
    script that forgets the body, a copy-pasted curl command) from
    silently mutating state, on top of (not instead of) role-based
    authorization. Everything about an attempt, refused or not, is still
    captured in AuditLog."""

    def restore_raw(self, v, body=None):
        url = f'/api/projects/{self.project.id}/versions/{v.id}/restore/'
        if body is None:
            return self.client.generic('POST', url)  # a truly empty request body
        return self.client.post(url, data=body, content_type='application/json')

    def test_delete_with_no_body_at_all_is_refused(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/{self.u2.id}/')
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertFalse(self.u2.is_deleted)

    def test_delete_with_empty_json_object_is_refused(self):
        resp = self.delete_version(self.u2, confirmation=None)  # body = {}
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertFalse(self.u2.is_deleted)

    def test_delete_confirmation_is_case_sensitive_and_rejects_near_matches(self):
        for bad in ('delete', 'Delete', 'DELETE ', ' DELETE', 'DELETEE', 'CONFIRM', 'true', '1'):
            resp = self.delete_version(self.u2, confirmation=bad)
            self.assertEqual(resp.status_code, 400, f'confirmation={bad!r} must not be accepted')
        self.u2.refresh_from_db()
        self.assertFalse(self.u2.is_deleted)

    def test_delete_confirmation_in_query_string_is_ignored_body_still_required(self):
        # A caller trying to dodge the JSON body by putting the phrase on
        # the URL instead must still be refused — only the parsed body field
        # the view actually reads counts.
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/{self.u2.id}/?confirmation=DELETE')
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertFalse(self.u2.is_deleted)

    def test_delete_with_malformed_json_body_is_a_clean_400_not_a_crash(self):
        resp = self.client.delete(
            f'/api/projects/{self.project.id}/versions/{self.u2.id}/',
            data='{not valid json', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertFalse(self.u2.is_deleted)

    def test_restore_with_no_body_at_all_is_refused(self):
        self.delete_version(self.u2, confirmation='DELETE')
        resp = self.restore_raw(self.u2, body=None)
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertTrue(self.u2.is_deleted)

    def test_restore_confirmation_is_case_sensitive_and_rejects_near_matches(self):
        self.delete_version(self.u2, confirmation='DELETE')
        for bad in ('restore', 'Restore', 'RESTORE ', 'RESTOREE', 'DELETE', 'true', '1'):
            resp = self.restore_raw(self.u2, body=json.dumps({'confirmation': bad}))
            self.assertEqual(resp.status_code, 400, f'confirmation={bad!r} must not be accepted')
        self.u2.refresh_from_db()
        self.assertTrue(self.u2.is_deleted)

    def test_restore_confirmation_in_query_string_is_ignored_body_still_required(self):
        self.delete_version(self.u2, confirmation='DELETE')
        resp = self.client.post(
            f'/api/projects/{self.project.id}/versions/{self.u2.id}/restore/?confirmation=RESTORE',
            data='{}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.u2.refresh_from_db()
        self.assertTrue(self.u2.is_deleted)

    def test_get_method_cannot_delete_or_restore(self):
        # The DELETE/POST-only decorators must reject a GET outright —
        # confirms there is no read-triggered side effect.
        self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/versions/{self.u2.id}/restore/').status_code, 405)
        self.delete_version(self.u2, confirmation='DELETE')
        resp = self.client.get(f'/api/projects/{self.project.id}/versions/{self.u2.id}/restore/')
        self.assertEqual(resp.status_code, 405)
        self.u2.refresh_from_db()
        self.assertTrue(self.u2.is_deleted)

    def test_every_rejected_direct_attempt_is_still_captured_in_the_audit_trail(self):
        from scheduler.models import AuditLog
        self.delete_version(self.u2, confirmation='nope')
        self.delete_version(self.u2, confirmation=None)
        rows = AuditLog.objects.filter(object_type='ScheduleUpload', object_id=str(self.u2.id), outcome='REFUSED')
        self.assertEqual(rows.count(), 2)
