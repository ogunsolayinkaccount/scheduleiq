"""
Field Dashboard — API layer tests.

Covers the Contractual Milestone register CRUD (GET/POST/PATCH/DELETE,
project-scoped persistence across versions, revision history on an
authorized date change) and GET /api/projects/<id>/field-dashboard-summary/
(every section's authoritative-engine reuse, "Contractual Dates Not
Configured" when the register is empty, GREEN/YELLOW/RED/UNVERIFIED
scenarios against real activity rows, headcount/Shelby/field-data/
project-issues honestly reported unavailable, panel error isolation).
"""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from scheduler.models import ContractualMilestoneRevision, MilestoneDefinition, Project, ScheduleUpload
from .test_schedule_identity_api import _xer


def _act(code, name='Activity', b_start='2026-01-01', b_finish='2026-01-20', **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'wbs': 'Area A', 'area': 'Area A', 'discipline': 'Electrical',
        'bStart': b_start, 'bFinish': b_finish, 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class ContractualMilestoneCrudApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Field Dashboard CRUD Test')
        self.base = f'/api/projects/{self.project.id}/contractual-milestones/'

    def _create(self, **overrides):
        body = {'activityId': 'MS-FINAL', 'description': 'Final Completion', 'category': 'CONTRACTUAL_COMPLETION',
                'contractRequiredDate': '2026-12-15', 'sourceDocumentReference': 'Contract Section 3.2'}
        body.update(overrides)
        return self.client.post(self.base, data=json.dumps(body), content_type='application/json')

    def test_create_requires_activity_id(self):
        resp = self.client.post(self.base, data=json.dumps({'description': 'x'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_create_contractual_milestone(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertTrue(body['isContractual'])
        self.assertEqual(body['contractRequiredDate'], '2026-12-15')
        self.assertEqual(body['sourceDocumentReference'], 'Contract Section 3.2')
        self.assertEqual(MilestoneDefinition.objects.count(), 1)

    def test_rejects_unknown_category(self):
        resp = self._create(category='NOT_A_REAL_CATEGORY')
        self.assertEqual(resp.status_code, 400)

    def test_informational_category_is_not_contractual(self):
        resp = self._create(category='INFORMATIONAL')
        self.assertFalse(resp.json()['isContractual'])

    def test_list_is_scoped_to_project(self):
        self._create(activityId='A1')
        other_project = Project.objects.create(name='Other')
        self.client.post(f'/api/projects/{other_project.id}/contractual-milestones/',
                          data=json.dumps({'activityId': 'B1', 'category': 'CONTRACTUAL_COMPLETION'}),
                          content_type='application/json')
        resp = self.client.get(self.base)
        body = resp.json()
        self.assertTrue(body['configured'])
        self.assertEqual(len(body['milestones']), 1)
        self.assertEqual(body['milestones'][0]['activityId'], 'A1')

    def test_empty_register_reports_not_configured(self):
        resp = self.client.get(self.base)
        self.assertFalse(resp.json()['configured'])

    def test_register_entry_survives_a_schedule_version_deletion(self):
        # The whole point of scoping by project, not schedule_upload: a
        # contractual obligation must outlive every weekly re-import.
        version = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer',
            file_type='XER', activities_json=[_act('MS-FINAL')],
        )
        self._create()
        version.delete()
        self.assertEqual(MilestoneDefinition.objects.filter(project=self.project).count(), 1)

    def test_patching_contract_date_creates_a_revision(self):
        created = self._create().json()
        resp = self.client.patch(
            f"{self.base}{created['id']}/",
            data=json.dumps({'contractRequiredDate': '2027-01-10', 'revisionReason': 'Owner-approved schedule extension',
                              'revisionSourceDocumentReference': 'Change Order 7', 'changedBy': 'J. Smith'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['contractRequiredDate'], '2027-01-10')
        revisions = ContractualMilestoneRevision.objects.filter(milestone_id=created['id'])
        self.assertEqual(revisions.count(), 1)
        rev = revisions.first()
        self.assertEqual(rev.previous_date.isoformat(), '2026-12-15')
        self.assertEqual(rev.new_date.isoformat(), '2027-01-10')
        self.assertEqual(rev.reason, 'Owner-approved schedule extension')
        self.assertEqual(rev.changed_by, 'J. Smith')

    def test_patching_an_unrelated_field_does_not_create_a_revision(self):
        created = self._create().json()
        self.client.patch(f"{self.base}{created['id']}/", data=json.dumps({'notes': 'just a note'}), content_type='application/json')
        self.assertEqual(ContractualMilestoneRevision.objects.filter(milestone_id=created['id']).count(), 0)

    def test_patching_to_the_same_date_does_not_create_a_revision(self):
        created = self._create().json()
        self.client.patch(f"{self.base}{created['id']}/", data=json.dumps({'contractRequiredDate': '2026-12-15'}), content_type='application/json')
        self.assertEqual(ContractualMilestoneRevision.objects.filter(milestone_id=created['id']).count(), 0)

    def test_revisions_endpoint_lists_history(self):
        created = self._create().json()
        self.client.patch(f"{self.base}{created['id']}/", data=json.dumps({'contractRequiredDate': '2027-01-10'}), content_type='application/json')
        self.client.patch(f"{self.base}{created['id']}/", data=json.dumps({'contractRequiredDate': '2027-02-01'}), content_type='application/json')
        resp = self.client.get(f"{self.base}{created['id']}/revisions/")
        self.assertEqual(len(resp.json()['revisions']), 2)

    def test_delete_removes_entry(self):
        created = self._create().json()
        resp = self.client.delete(f"{self.base}{created['id']}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MilestoneDefinition.objects.count(), 0)

    def test_404_for_milestone_not_belonging_to_project(self):
        created = self._create().json()
        other_project = Project.objects.create(name='Other')
        resp = self.client.get(f'/api/projects/{other_project.id}/contractual-milestones/{created["id"]}/')
        self.assertEqual(resp.status_code, 404)

    def test_bad_contract_date_rejected(self):
        resp = self._create(contractRequiredDate='not-a-date')
        self.assertEqual(resp.status_code, 400)

    def test_register_survives_a_real_new_xer_upload_to_the_same_project(self):
        # import_commit/import_preview never reference MilestoneDefinition
        # at all (confirmed by inspection) — this proves it end to end
        # through the real upload pipeline, not just a deletion.
        created = self._create().json()
        upload = SimpleUploadedFile('next-update.xer', _xer(['A1', 'A2', 'A3'], proj_id='9', proj_name='Next Update'))
        resp = self.client.post('/api/import/commit/', data={'file': upload, 'projectId': str(self.project.id)})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(ScheduleUpload.objects.filter(project=self.project).count(), 1)
        after = self.client.get(f"{self.base}{created['id']}/").json()
        self.assertEqual(after['contractRequiredDate'], created['contractRequiredDate'])
        self.assertEqual(after['sourceDocumentReference'], created['sourceDocumentReference'])
        self.assertEqual(MilestoneDefinition.objects.filter(project=self.project).count(), 1)


class FieldDashboardSummaryApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Field Dashboard Summary Test')

    def _register(self, **overrides):
        body = {'activityId': 'MS-B', 'description': 'Area B turnover', 'category': 'CONTRACTUAL_INTERIM',
                'contractRequiredDate': '2026-01-20'}
        body.update(overrides)
        return self.client.post(f'/api/projects/{self.project.id}/contractual-milestones/',
                                 data=json.dumps(body), content_type='application/json')

    def test_no_schedule_version_is_404(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        self.assertEqual(resp.status_code, 404)

    def test_no_contractual_milestones_configured(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-10', activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        self.assertEqual(resp.status_code, 200)
        cm = resp.json()['contractualMilestones']
        self.assertTrue(cm['available'])
        self.assertFalse(cm['configured'])
        self.assertEqual(cm['reason'], 'Contractual Dates Not Configured')

    def test_green_milestone_scenario(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05',
            activities_json=[_act('MS-B', b_finish='2026-01-20', totalFloat=20.0)],
        )
        self._register(contractRequiredDate='2026-01-20')
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        m = resp.json()['contractualMilestones']['milestones'][0]
        self.assertEqual(m['status'], 'GREEN')

    def test_red_milestone_scenario(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05',
            activities_json=[_act('MS-B', b_finish='2026-01-30', totalFloat=20.0)],
        )
        self._register(contractRequiredDate='2026-01-20')
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        m = resp.json()['contractualMilestones']['milestones'][0]
        self.assertEqual(m['status'], 'RED')

    def test_yellow_milestone_scenario_via_float_threshold(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05',
            activities_json=[_act('MS-B', b_finish='2026-01-20', totalFloat=2.0)],
        )
        self._register(contractRequiredDate='2026-01-20')
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/?warningThresholdDays=10')
        m = resp.json()['contractualMilestones']['milestones'][0]
        self.assertEqual(m['status'], 'YELLOW')

    def test_warning_threshold_is_configurable_via_query_param(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05',
            activities_json=[_act('MS-B', b_finish='2026-01-20', totalFloat=8.0)],
        )
        self._register(contractRequiredDate='2026-01-20')
        resp_default = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/?warningThresholdDays=10')
        resp_tight = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/?warningThresholdDays=5')
        self.assertEqual(resp_default.json()['contractualMilestones']['milestones'][0]['status'], 'YELLOW')
        self.assertEqual(resp_tight.json()['contractualMilestones']['milestones'][0]['status'], 'GREEN')

    def test_unverified_when_activity_id_unmapped(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05', activities_json=[_act('SOME-OTHER-ACTIVITY')],
        )
        self._register(activityId='MS-DOES-NOT-EXIST', contractRequiredDate='2026-01-20')
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        m = resp.json()['contractualMilestones']['milestones'][0]
        self.assertEqual(m['status'], 'UNVERIFIED')

    def test_headcount_reported_unavailable_never_derived_from_hours(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05',
            activities_json=[_act('A1', isResourceLoaded=True, budgetedHours=1000.0, actualHours=400.0, remainingHours=600.0)],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        body = resp.json()
        self.assertTrue(body['manpower']['available'])  # hours ARE available
        self.assertFalse(body['manpower']['headcount']['available'])  # headcount is NOT

    def test_shelby_and_field_data_never_claimed_connected(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05', activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        sources = resp.json()['integrationSources']
        self.assertEqual(sources['primaveraP6']['status'], 'CONNECTED')
        self.assertEqual(sources['shelby']['status'], 'NOT_CONNECTED')
        self.assertEqual(sources['fieldData']['status'], 'NOT_CONNECTED')

    def test_project_issues_not_fabricated(self):
        # Project Issue Tracking is now a real, always-on engine (see
        # issue_register.py) — but with no ProjectIssue rows created for
        # this project, it must report real zero counts, never an invented
        # example issue.
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05', activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        issues = resp.json()['projectIssues']
        self.assertTrue(issues['configured'])
        self.assertEqual(issues['topIssues'], [])
        self.assertEqual(issues['openCount'], 0)

    def test_financials_reconcile_with_cost_summary_endpoint(self):
        acts = [_act('A1', isCostLoaded=True, budgetedCost=100000.0, actualCost=40000.0, pctComplete=50.0)]
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05', activities_json=acts,
        )
        field = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/').json()
        cost = self.client.get(f'/api/projects/{self.project.id}/cost-summary/').json()
        self.assertEqual(field['financials']['currentSpend']['value'], cost['actuals']['actualCost']['value'])
        self.assertEqual(field['financials']['budget']['value'], cost['budget']['currentBudget']['value'])

    def test_one_panel_failure_does_not_break_the_whole_dashboard(self):
        from unittest.mock import patch
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v.xer', sanitized_filename='v.xer',
            file_type='XER', data_date='2026-01-05', activities_json=[_act('A1')],
        )
        with patch('scheduler.views.compute_productivity', side_effect=RuntimeError('boom')):
            resp = self.client.get(f'/api/projects/{self.project.id}/field-dashboard-summary/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body['manpower']['available'])
        self.assertIn('boom', body['manpower']['reason'])
        self.assertTrue(body['financials']['available'])
        self.assertTrue(body['scheduleHealth']['available'])
