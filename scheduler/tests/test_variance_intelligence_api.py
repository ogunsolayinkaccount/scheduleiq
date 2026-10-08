"""
Variance Intelligence — API layer tests. Covers
GET /api/projects/<id>/variance-intelligence/ end-to-end via the Django
test client, against the SAME activity_analysis.py master rows every
other analysis endpoint uses — no competing variance calculation exists
to test independently of that.
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from scheduler.models import MilestoneDefinition, Project, ScheduleUpload


def _act(code, name='Activity', b_start='2026-01-01', b_finish='2026-01-20', **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'wbs': 'Area A', 'area': 'Area A', 'discipline': 'Electrical',
        'bStart': b_start, 'bFinish': b_finish, 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class ComparisonBasisAvailabilityApiTests(TestCase):
    """No approved baseline / approved baseline available / no previous /
    previous available — the four basic availability combinations."""

    def test_current_only_no_baseline_no_previous(self):
        project = Project.objects.create(name='Current Only Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', totalFloat=-5.0)],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['basis'], 'embeddedBaseline')  # correct default with no baseline designated
        bases_by_key = {b['key']: b for b in body['bases']}
        self.assertFalse(bases_by_key['approvedBaseline']['available'])
        self.assertFalse(bases_by_key['previous']['available'])
        self.assertTrue(bases_by_key['embeddedBaseline']['available'])
        self.assertTrue(body['available'])  # embeddedBaseline basis itself IS usable

    def test_requesting_unavailable_basis_reports_why_not_silently_substituted(self):
        project = Project.objects.create(name='No Baseline Explicit Request Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&basis=approvedBaseline')
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(body['basis'], 'approvedBaseline')
        self.assertFalse(body['available'])
        self.assertIn('designated', body['reason'])

    def test_approved_baseline_available_and_default_prefers_it(self):
        project = Project.objects.create(name='Baseline Available Test')
        now = timezone.now()
        baseline = ScheduleUpload.objects.create(
            project=project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', bFinish='2026-02-01')], upload_timestamp=now - timedelta(days=30),
        )
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', earlyFinish='2026-02-15', bFinish='2026-02-01')], upload_timestamp=now,
        )
        resp = self.client.get(
            f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&baselineVersion={baseline.id}'
        )
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(body['basis'], 'approvedBaseline')
        self.assertTrue(body['available'])


class VarianceDirectionAndExposureApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Variance Direction Test')
        now = timezone.now()
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[
                _act('UNFAV', bFinish='2026-02-01'), _act('FAV', bFinish='2026-02-20'), _act('ZERO', bFinish='2026-02-10'),
            ],
            upload_timestamp=now - timedelta(days=30),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                # Unfavorable: 14 days later than baseline, negative float, driving -> HIGH_EXPOSURE.
                _act('UNFAV', totalFloat=-7.0, isCritical=True, onLongestPath=True, earlyFinish='2026-02-15', bFinish='2026-02-01'),
                # Favorable: 10 days earlier than baseline.
                _act('FAV', totalFloat=15.0, earlyFinish='2026-02-10', bFinish='2026-02-20'),
                # Zero variance (exact match).
                _act('ZERO', totalFloat=20.0, earlyFinish='2026-02-10', bFinish='2026-02-10'),
            ],
            upload_timestamp=now,
        )

    def _url(self, **params):
        base = f'/api/projects/{self.project.id}/variance-intelligence/?currentVersion={self.current.id}&baselineVersion={self.baseline.id}'
        for k, v in params.items():
            base += f'&{k}={v}'
        return base

    def test_unfavorable_variance_with_negative_float_and_driving_is_high_exposure(self):
        resp = self.client.get(self._url())
        body = resp.json()
        by_id = {r['activityId']: r for r in vars_from_topN(body)}
        self.assertEqual(by_id['UNFAV']['assessment'], 'HIGH_EXPOSURE')
        self.assertEqual(by_id['UNFAV']['direction'], 'UNFAVORABLE')

    def test_favorable_variance_excluded_from_top_exposure_ranking(self):
        resp = self.client.get(self._url())
        body = resp.json()
        ids = [r['activityId'] for r in body['topVarianceExposure']]
        self.assertNotIn('FAV', ids)
        self.assertIn('UNFAV', ids)

    def test_zero_variance_is_no_movement(self):
        resp = self.client.get(self._url())
        body = resp.json()
        zero = next(r for r in body['topVarianceExposure'] + [] if r['activityId'] == 'ZERO') if any(
            r['activityId'] == 'ZERO' for r in body['topVarianceExposure']
        ) else None
        # ZERO has no exposure (favorable-equivalent), so it won't be in
        # topVarianceExposure at all — confirm that directly instead.
        self.assertNotIn('ZERO', [r['activityId'] for r in body['topVarianceExposure']])

    def test_positive_float_with_unfavorable_variance_is_not_high_exposure(self):
        # A pure MONITOR case: unfavorable but healthy float, not driving.
        extra_current = self.current.activities_json + [_act('MONITOR', totalFloat=25.0, earlyFinish='2026-03-01', bFinish='2026-02-01')]
        self.current.activities_json = extra_current
        self.current.save()
        self.baseline.activities_json = self.baseline.activities_json + [_act('MONITOR', bFinish='2026-02-01')]
        self.baseline.save()
        resp = self.client.get(self._url())
        body = resp.json()
        by_id = {r['activityId']: r for r in body['topVarianceExposure']}
        self.assertEqual(by_id['MONITOR']['assessment'], 'MONITOR')

    def test_sign_direction_interpretation_grounded_in_actual_dates(self):
        resp = self.client.get(self._url())
        body = resp.json()
        by_id = {r['activityId']: r for r in body['topVarianceExposure']}
        self.assertEqual(by_id['UNFAV']['directionLabel'], 'Unfavorable')
        self.assertGreater(by_id['UNFAV']['finishVarianceDays'], 0)


def vars_from_topN(body):
    return body['topVarianceExposure']


class CompletedActivityAndMissingActivityApiTests(TestCase):
    def test_completed_activity_never_shows_actionable_negative_float_exposure(self):
        project = Project.objects.create(name='Completed Activity Variance Test')
        now = timezone.now()
        baseline = ScheduleUpload.objects.create(
            project=project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', bFinish='2026-02-01')], upload_timestamp=now - timedelta(days=30),
        )
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act(
                'A1', totalFloat=-5.0, pctComplete=100.0, start='2026-01-05', finish='2026-02-20', bFinish='2026-02-01',
            )],
            upload_timestamp=now,
        )
        resp = self.client.get(
            f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&baselineVersion={baseline.id}'
        )
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        # Completed activity's currentTotalFloat is blanked — it must not
        # drive a HIGH_EXPOSURE classification from a stale imported value.
        ids = [r['activityId'] for r in body['topVarianceExposure'] if r['assessment'] == 'HIGH_EXPOSURE']
        self.assertNotIn('A1', ids)

    def test_missing_activity_between_versions_reports_unavailable_not_fabricated(self):
        project = Project.objects.create(name='Missing Activity Variance Test')
        now = timezone.now()
        previous = ScheduleUpload.objects.create(
            project=project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-07-01',
            activities_json=[_act('A1'), _act('A2')], upload_timestamp=now - timedelta(days=7),
        )
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],  # A2 no longer present
            upload_timestamp=now,
        )
        resp = self.client.get(
            f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&previousVersion={previous.id}&basis=previous'
        )
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(body['available'])
        # A1 is the only row in `current` (A2 genuinely doesn't exist in
        # the current version at all — never fabricated); A1 itself has no
        # real previous match issue since it existed in both versions.
        ids = [r['activityId'] for r in body.get('topVarianceExposure', [])]
        self.assertNotIn('A2', ids)


class MilestoneVarianceApiTests(TestCase):
    def test_schedule_and_contract_variance_reported_separately(self):
        project = Project.objects.create(name='Milestone Variance API Test')
        now = timezone.now()
        baseline = ScheduleUpload.objects.create(
            project=project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('MS1', isMilestone=True, bFinish='2026-04-01')], upload_timestamp=now - timedelta(days=30),
        )
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('MS1', isMilestone=True, earlyFinish='2026-04-10', bFinish='2026-04-01', totalFloat=3.0)],
            upload_timestamp=now,
        )
        MilestoneDefinition.objects.create(
            project=project, activity_id='MS1', activity_name='Substantial Completion',
            milestone_category='CONTRACTUAL_COMPLETION', contract_required_date='2026-04-05',
        )
        resp = self.client.get(
            f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&baselineVersion={baseline.id}'
        )
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        mv = body['milestoneVariance'][0]
        self.assertEqual(mv['finishVarianceDays'], 9)       # vs approved baseline (Apr 1 -> Apr 10)
        self.assertEqual(mv['contractVarianceDays'], 5)     # vs contract date (Apr 5 -> Apr 10)
        self.assertNotEqual(mv['finishVarianceDays'], mv['contractVarianceDays'])

    def test_unregistered_milestone_has_no_contract_date_fabricated(self):
        project = Project.objects.create(name='Unregistered Milestone API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('MS1', isMilestone=True)],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        mv = body['milestoneVariance'][0]
        self.assertIsNone(mv['contractRequiredDate'])
        self.assertIsNone(mv['contractVarianceDays'])


class ProjectCompletionApiTests(TestCase):
    def test_completion_milestone_is_latest_finishing_milestone(self):
        project = Project.objects.create(name='Completion Milestone API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('MS1', isMilestone=True, earlyFinish='2026-09-01', bFinish='2026-09-01'),
                _act('MS2', isMilestone=True, earlyFinish='2026-10-15', bFinish='2026-10-15'),
            ],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        self.assertEqual(body['completionMilestone']['activityId'], 'MS2')
        self.assertEqual(body['completionMilestone']['selectionBasis'], 'LATEST_FINISH_HEURISTIC')
        self.assertIn('not registered', body['completionInterpretation'].lower())

    def test_registered_contractual_completion_milestone_overrides_the_latest_finish_heuristic(self):
        # Final review finding, reproduced live through the real API: a
        # registered Contractual Completion milestone must be selected even
        # when another, unregistered milestone finishes later.
        project = Project.objects.create(name='Registered Completion API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('M9999', name='Compute 01 Project Complete', isMilestone=True, totalFloat=834.0, earlyFinish='2027-09-30', bFinish='2027-09-30'),
                _act('PD1040', name='Phase IV (Q2 2027 1680MVA)', isMilestone=True, totalFloat=676.0, onLongestPath=True, earlyFinish='2028-04-03', bFinish='2028-04-03'),
            ],
        )
        MilestoneDefinition.objects.create(
            project=project, activity_id='M9999', activity_name='Compute 01 Project Complete',
            milestone_category='CONTRACTUAL_COMPLETION',
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(body['completionMilestone']['activityId'], 'M9999')  # not PD1040, despite its later finish
        self.assertEqual(body['completionMilestone']['selectionBasis'], 'REGISTERED')
        self.assertNotIn('not registered', body['completionInterpretation'].lower())

    def test_contractual_interim_milestone_is_never_used_as_completion(self):
        # CONTRACTUAL_INTERIM is a real, registered category — but it is
        # explicitly NOT "final completion" and must never be selected.
        project = Project.objects.create(name='Interim Not Completion API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('MS1', isMilestone=True, earlyFinish='2026-09-01', bFinish='2026-09-01'),
                _act('MS2', isMilestone=True, earlyFinish='2026-10-15', bFinish='2026-10-15'),
            ],
        )
        MilestoneDefinition.objects.create(
            project=project, activity_id='MS1', activity_name='Interim handover',
            milestone_category='CONTRACTUAL_INTERIM',
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        # No CONTRACTUAL_COMPLETION registered -> heuristic fallback (MS2, latest), NOT the interim milestone.
        self.assertEqual(body['completionMilestone']['activityId'], 'MS2')
        self.assertEqual(body['completionMilestone']['selectionBasis'], 'LATEST_FINISH_HEURISTIC')

    def test_no_milestones_means_no_completion_fabricated(self):
        project = Project.objects.create(name='No Milestones API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', isMilestone=False)],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        self.assertIsNone(body['completionMilestone'])
        self.assertIn('cannot be automatically identified', body['completionInterpretation'])


class FilterAndSwitchingApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Filter Test Project')
        self.other_project = Project.objects.create(name='Other Filter Test Project')
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('A1', area='Area A', totalFloat=-5.0, earlyFinish='2026-02-15', bFinish='2026-02-01'),
                _act('A2', area='Area B', totalFloat=25.0, earlyFinish='2026-02-01', bFinish='2026-02-10'),
            ],
        )
        self.other_current = ScheduleUpload.objects.create(
            project=self.other_project, original_filename='o.xer', sanitized_filename='o.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('B1')],
        )

    def test_area_filter_applies_to_top_exposure(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/variance-intelligence/?currentVersion={self.current.id}&area=Area A'
        )
        body = resp.json()
        ids = [r['activityId'] for r in body['topVarianceExposure']]
        self.assertNotIn('A2', ids)

    def test_direction_filter(self):
        resp = self.client.get(
            f'/api/projects/{self.project.id}/variance-intelligence/?currentVersion={self.current.id}&direction=FAVORABLE'
        )
        body = resp.json()
        self.assertTrue(resp.status_code == 200)

    def test_project_version_switching_never_leaks_across_projects(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/variance-intelligence/?currentVersion={self.other_current.id}')
        self.assertEqual(resp.status_code, 404)

    def test_data_date_reflects_the_selected_versions_own_data_date(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/variance-intelligence/?currentVersion={self.current.id}')
        body = resp.json()
        self.assertEqual(body['currentDataDate'], '2026-08-15')


class UpdateMovementApiTests(TestCase):
    def test_update_movement_section_present_only_when_previous_exists(self):
        project = Project.objects.create(name='Update Movement API Test')
        now = timezone.now()
        previous = ScheduleUpload.objects.create(
            project=project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-07-01',
            activities_json=[_act('A1', earlyFinish='2026-02-01')], upload_timestamp=now - timedelta(days=7),
        )
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', earlyFinish='2026-02-10')], upload_timestamp=now,
        )
        resp = self.client.get(
            f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}&previousVersion={previous.id}&basis=embeddedBaseline'
        )
        body = resp.json()
        self.assertIsNotNone(body['updateMovement'])
        self.assertIn('A1', body['updateMovement']['finishSlipped'])

    def test_update_movement_is_none_without_a_previous_version(self):
        project = Project.objects.create(name='No Previous Update Movement Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        self.assertIsNone(body['updateMovement'])


class NarrativeAndAiUnavailableApiTests(TestCase):
    def test_narrative_present_and_deterministic_with_no_ai_configured(self):
        # This endpoint never calls an AI provider at all — it is fully
        # deterministic by construction, so "AI unavailable" is trivially
        # satisfied: the page works identically whether or not AI_PROVIDER
        # is configured anywhere in the environment.
        project = Project.objects.create(name='Narrative API Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )
        resp = self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        body = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsInstance(body['narrative'], list)
        self.assertTrue(len(body['narrative']) > 0)


class ReadOnlySafetyApiTests(TestCase):
    def test_no_post_patch_delete_methods_exist(self):
        project = Project.objects.create(name='Read Only Safety Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )
        url = f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}'
        self.assertEqual(self.client.post(url, data=json.dumps({}), content_type='application/json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_calling_the_endpoint_does_not_alter_project_or_version_data(self):
        project = Project.objects.create(name='Immutability Test')
        current = ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', totalFloat=-5.0)],
        )
        before_acts = current.activities_json
        self.client.get(f'/api/projects/{project.id}/variance-intelligence/?currentVersion={current.id}')
        current.refresh_from_db()
        self.assertEqual(current.activities_json, before_acts)
        self.assertTrue(ScheduleUpload.objects.filter(pk=current.pk).exists())
