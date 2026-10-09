"""
Weekly Field Operations Review — API layer tests (Field Operations Review
Template Assessment, Minimal Enhancement). Every value on this model is a
manual entry; these tests specifically guard against headcount ever being
inferred from P6 resource hours, against forecasts being confused with
actuals, against a new week silently overwriting a prior one, and against
this feature touching any existing schedule/cost calculation.
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from scheduler.models import (
    AuditLog, Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER,
    ScheduleUpload, WeeklyFieldReport,
)


def _user(username, role, password='pw12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


def _resource_loaded_act(code, budgeted_hours=80.0, actual_hours=40.0):
    return {
        'id': code, 'code': code, 'name': 'Resource-loaded activity', 'wbs': 'Area A', 'area': 'Area A',
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'totalFloat': 5.0, 'pctComplete': 0.5,
        'isResourceLoaded': True, 'budgetedHours': budgeted_hours, 'actualHours': actual_hours,
        'remainingHours': budgeted_hours - actual_hours,
        'isCritical': False, 'onLongestPath': False, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }


class WeeklyFieldReportCrudTests(TestCase):
    """AutoAuthTestMiddleware supplies a fixture Administrator identity for
    plain self.client calls, same as every other endpoint test suite."""

    def setUp(self):
        self.project = Project.objects.create(name='Weekly Field Report Test')

    def test_create_requires_week_start_date(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/', data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_and_read_full_report(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({
                'weekStartDate': '2026-09-28', 'actualHeadcount': 39, 'nextWeekForecastHeadcount': 39,
                'pmProjectedHeadcount': 50, 'monthlyTargetHeadcount': 50, 'lastClientUpdateDate': '2026-10-07',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['weekStartDate'], '2026-09-28')
        self.assertEqual(body['actualHeadcount'], 39)
        self.assertEqual(body['nextWeekForecastHeadcount'], 39)
        self.assertEqual(body['pmProjectedHeadcount'], 50)
        self.assertEqual(body['monthlyTargetHeadcount'], 50)
        self.assertEqual(body['lastClientUpdateDate'], '2026-10-07')
        self.assertTrue(body['createdBy'])

        get_resp = self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/{body["id"]}/')
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['actualHeadcount'], 39)

    def test_missing_null_values_are_accepted(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        for field in ('actualHeadcount', 'nextWeekForecastHeadcount', 'pmProjectedHeadcount',
                      'monthlyTargetHeadcount', 'lastClientUpdateDate', 'scheduleVersionId'):
            self.assertIsNone(body[field])

    def test_negative_headcount_rejected(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': -5}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_zero_headcount_is_a_valid_value_not_treated_as_missing(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 0}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['actualHeadcount'], 0)

    def test_malformed_week_start_date_rejected(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': 'not-a-date'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_monday_week_start_date_is_accepted(self):
        # 2026-09-14 is a Monday.
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-14'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['weekStartDate'], '2026-09-14')

    def test_non_monday_week_start_date_is_rejected(self):
        # 2026-09-16 is a Wednesday.
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-16'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Monday', resp.json()['error'])
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 0)

    def test_sunday_week_start_date_is_also_rejected(self):
        # 2026-09-20 is a Sunday — the convention is Monday-start, not any
        # day within the week.
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-20'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


class WeeklyFieldReportHistoryTests(TestCase):
    """The improvement the review explicitly called for: weekly reports are
    preserved historically, never overwritten by a later week's report."""

    def setUp(self):
        self.project = Project.objects.create(name='Weekly History Test')

    def _create(self, week, headcount):
        return self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': week, 'actualHeadcount': headcount}),
            content_type='application/json',
        )

    def test_a_new_week_never_overwrites_a_prior_week(self):
        r1 = self._create('2026-09-14', 30)
        r2 = self._create('2026-09-21', 35)
        r3 = self._create('2026-09-28', 39)
        for r in (r1, r2, r3):
            self.assertEqual(r.status_code, 201, r.content)

        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 3)

        listing = self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/').json()['reports']
        by_week = {r['weekStartDate']: r['actualHeadcount'] for r in listing}
        self.assertEqual(by_week, {'2026-09-14': 30, '2026-09-21': 35, '2026-09-28': 39})

    def test_listing_is_most_recent_week_first(self):
        self._create('2026-09-14', 30)
        self._create('2026-09-28', 39)
        self._create('2026-09-21', 35)
        listing = self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/').json()['reports']
        self.assertEqual([r['weekStartDate'] for r in listing], ['2026-09-28', '2026-09-21', '2026-09-14'])

    def test_duplicate_week_is_refused_not_silently_overwritten(self):
        first = self._create('2026-09-28', 39)
        self.assertEqual(first.status_code, 201)

        duplicate = self._create('2026-09-28', 999)
        self.assertEqual(duplicate.status_code, 409)

        # The original row's real value must survive the refused duplicate.
        self.assertEqual(WeeklyFieldReport.objects.get(project=self.project).actual_headcount, 39)

    def test_editing_the_current_week_updates_in_place_and_is_audited(self):
        created = self._create('2026-09-28', 39).json()
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.content)
        self.assertEqual(patch_resp.json()['actualHeadcount'], 41)
        self.assertEqual(WeeklyFieldReport.objects.filter(project=self.project).count(), 1)

        self.assertTrue(AuditLog.objects.filter(
            object_type='WeeklyFieldReport', object_id=created['id'], action='UPDATE_WEEKLY_FIELD_REPORT',
        ).exists())

    def test_week_start_date_is_immutable_on_edit(self):
        created = self._create('2026-09-28', 39).json()
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/',
            data=json.dumps({'weekStartDate': '2026-10-05', 'actualHeadcount': 41}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()['weekStartDate'], '2026-09-28')

    def test_no_delete_endpoint_is_exposed(self):
        created = self._create('2026-09-28', 39).json()
        resp = self.client.delete(f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/')
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(WeeklyFieldReport.objects.filter(pk=created['id']).exists())


class WeeklyFieldReportSeparationTests(TestCase):
    """Guards the audit's central risk finding: headcount must never be
    inferred from P6 resource hours, and actual/forecast/target/last-client-
    update must stay in their own distinct fields, never cross-contaminated."""

    def setUp(self):
        self.project = Project.objects.create(name='Separation Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='r.xer', sanitized_filename='r.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-09-28',
            activities_json=[_resource_loaded_act('A1', budgeted_hours=4000.0, actual_hours=2000.0)],
        )

    def test_field_dashboard_headcount_remains_unavailable_regardless_of_weekly_reports(self):
        self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 39}),
            content_type='application/json',
        )
        resp = self.client.get(
            f'/api/projects/{self.project.id}/field-dashboard-summary/?currentVersion={self.version.id}',
        )
        self.assertEqual(resp.status_code, 200)
        manpower = resp.json()['manpower']
        # 4000 budgeted hours exist on this schedule — if headcount were ever
        # derived from hours, this would now report availability/a number.
        self.assertFalse(manpower['headcount']['available'])
        self.assertIn('only man-hours', manpower['headcount']['reason'])

    def test_weekly_report_headcount_is_independent_of_resource_hours_on_the_schedule(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({
                'weekStartDate': '2026-09-28', 'actualHeadcount': 39, 'scheduleVersionId': str(self.version.id),
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        # 39 is a field-verified headcount the human entered — nothing here
        # was computed from the 4000/2000 budgeted/actual hours on self.version.
        self.assertEqual(resp.json()['actualHeadcount'], 39)
        self.assertEqual(resp.json()['scheduleVersionId'], str(self.version.id))

    def test_actual_forecast_target_and_client_date_are_independently_editable(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({
                'weekStartDate': '2026-09-28', 'actualHeadcount': 39, 'nextWeekForecastHeadcount': 39,
                'pmProjectedHeadcount': 50, 'monthlyTargetHeadcount': 50, 'lastClientUpdateDate': '2026-10-01',
            }),
            content_type='application/json',
        ).json()

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        body = patch_resp.json()
        self.assertEqual(body['actualHeadcount'], 41)
        # Everything else must be untouched by editing just one field.
        self.assertEqual(body['nextWeekForecastHeadcount'], 39)
        self.assertEqual(body['pmProjectedHeadcount'], 50)
        self.assertEqual(body['monthlyTargetHeadcount'], 50)
        self.assertEqual(body['lastClientUpdateDate'], '2026-10-01')

    def test_scheduled_version_reference_must_belong_to_the_same_project(self):
        other_project = Project.objects.create(name='Other Project')
        other_version = ScheduleUpload.objects.create(
            project=other_project, original_filename='o.xer', sanitized_filename='o.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-09-28', activities_json=[],
        )
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'scheduleVersionId': str(other_version.id)}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_schedule_version_reference_is_informational_only(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 39, 'nextWeekForecastHeadcount': 41}),
            content_type='application/json',
        ).json()
        self.assertIsNone(created['scheduleVersionId'])

        # Attaching a schedule version afterward must only set that
        # reference — it must never recompute, overwrite, or touch any of
        # the manually entered reporting fields.
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/',
            data=json.dumps({'scheduleVersionId': str(self.version.id)}), content_type='application/json',
        )
        body = patch_resp.json()
        self.assertEqual(body['scheduleVersionId'], str(self.version.id))
        self.assertEqual(body['actualHeadcount'], 39)
        self.assertEqual(body['nextWeekForecastHeadcount'], 41)
        self.assertEqual(body['weekStartDate'], '2026-09-28')


class WeeklyFieldReportProjectIsolationTests(TestCase):
    def test_reports_do_not_leak_across_projects(self):
        project_a = Project.objects.create(name='Project A')
        project_b = Project.objects.create(name='Project B')

        self.client.post(
            f'/api/projects/{project_a.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 10}), content_type='application/json',
        )
        self.client.post(
            f'/api/projects/{project_b.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 20}), content_type='application/json',
        )

        a_listing = self.client.get(f'/api/projects/{project_a.id}/weekly-field-reports/').json()['reports']
        b_listing = self.client.get(f'/api/projects/{project_b.id}/weekly-field-reports/').json()['reports']
        self.assertEqual(len(a_listing), 1)
        self.assertEqual(len(b_listing), 1)
        self.assertEqual(a_listing[0]['actualHeadcount'], 10)
        self.assertEqual(b_listing[0]['actualHeadcount'], 20)

    def test_the_same_week_is_valid_independently_in_two_different_projects(self):
        project_a = Project.objects.create(name='Project A2')
        project_b = Project.objects.create(name='Project B2')
        resp_a = self.client.post(
            f'/api/projects/{project_a.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28'}), content_type='application/json',
        )
        resp_b = self.client.post(
            f'/api/projects/{project_b.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28'}), content_type='application/json',
        )
        self.assertEqual(resp_a.status_code, 201)
        self.assertEqual(resp_b.status_code, 201)

    def test_detail_lookup_under_the_wrong_project_is_404(self):
        project_a = Project.objects.create(name='Project A3')
        project_b = Project.objects.create(name='Project B3')
        created = self.client.post(
            f'/api/projects/{project_a.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28'}), content_type='application/json',
        ).json()
        resp = self.client.get(f'/api/projects/{project_b.id}/weekly-field-reports/{created["id"]}/')
        self.assertEqual(resp.status_code, 404)


@override_settings(AUTO_AUTH_TEST_USER=False)
class WeeklyFieldReportPermissionTests(TestCase):
    """Viewer: read only. Scheduler: create/edit. Administrator: inherits
    everything Scheduler can do via the existing rank system — same tier
    convention the Issue Register already established."""

    def setUp(self):
        self.project = Project.objects.create(name='Weekly Permission Test')
        self.admin = _user('weekly-admin', ROLE_ADMINISTRATOR, password='pw-1')
        self.scheduler = _user('weekly-scheduler', ROLE_SCHEDULER, password='pw-2')
        self.viewer = _user('weekly-viewer', ROLE_VIEWER, password='pw-3')
        self.report = WeeklyFieldReport.objects.create(project=self.project, week_start_date='2026-09-28', actual_headcount=39)

    def _login(self, username, password):
        self.client.login(username=username, password=password)

    def test_unauthenticated_list_refused(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/')
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_create_refused(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-10-05'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_viewer_can_list_and_read(self):
        self._login('weekly-viewer', 'pw-3')
        self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/').status_code, 200)
        self.assertEqual(
            self.client.get(f'/api/projects/{self.project.id}/weekly-field-reports/{self.report.id}/').status_code, 200,
        )

    def test_viewer_cannot_create_or_edit(self):
        self._login('weekly-viewer', 'pw-3')
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-10-05'}), content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 403)
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{self.report.id}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 403)

    def test_scheduler_can_create_and_edit(self):
        self._login('weekly-scheduler', 'pw-2')
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-10-05', 'actualHeadcount': 42}), content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.content)
        self.assertEqual(create_resp.json()['createdBy'], 'weekly-scheduler')

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{self.report.id}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()['updatedBy'], 'weekly-scheduler')

    def test_administrator_can_create_and_edit(self):
        self._login('weekly-admin', 'pw-1')
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-10-05'}), content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 201)
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{self.report.id}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        self.assertEqual(patch_resp.status_code, 200)

    def test_created_by_is_always_the_authenticated_user_never_caller_supplied(self):
        self._login('weekly-scheduler', 'pw-2')
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-10-05', 'createdBy': 'someone-else'}),
            content_type='application/json',
        )
        self.assertEqual(resp.json()['createdBy'], 'weekly-scheduler')


class WeeklyFieldReportAuditTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Weekly Audit Test')

    def test_create_is_audited(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 39}), content_type='application/json',
        )
        report_id = resp.json()['id']
        entry = AuditLog.objects.get(object_type='WeeklyFieldReport', object_id=report_id, action='CREATE_WEEKLY_FIELD_REPORT')
        self.assertEqual(entry.outcome, 'SUCCESS')
        self.assertEqual(entry.new_value['actualHeadcount'], 39)

    def test_update_is_audited_with_previous_and_new_value(self):
        created = self.client.post(
            f'/api/projects/{self.project.id}/weekly-field-reports/',
            data=json.dumps({'weekStartDate': '2026-09-28', 'actualHeadcount': 39}), content_type='application/json',
        ).json()
        self.client.patch(
            f'/api/projects/{self.project.id}/weekly-field-reports/{created["id"]}/',
            data=json.dumps({'actualHeadcount': 41}), content_type='application/json',
        )
        entry = AuditLog.objects.get(
            object_type='WeeklyFieldReport', object_id=created['id'], action='UPDATE_WEEKLY_FIELD_REPORT',
        )
        self.assertEqual(entry.previous_value['actualHeadcount'], 39)
        self.assertEqual(entry.new_value['actualHeadcount'], 41)
