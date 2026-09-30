from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from scheduler import report_export, report_service
from scheduler.models import MitigationAction, Project, RecoveryScenario, ScheduleRisk, ScheduleUpload


def _act(code, **extra):
    d = {
        'id': code, 'code': code, 'name': f'Activity {code}', 'wbs': 'Area A', 'discipline': 'Electrical',
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': 0.0, 'isCritical': False, 'onLongestPath': False, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class ScheduleRiskRecoverySectionTests(TestCase):
    """Report-layer wiring for the Schedule Risk & Recovery section — proves
    it reuses risk_register.build_risk_register (never a competing
    calculation), joins persisted workflow/scenario/mitigation rows, and
    degrades to an honest 'unavailable' reason rather than fabricating data."""

    def setUp(self):
        self.project = Project.objects.create(name='Risk Recovery Report Test')

    def test_unavailable_with_single_version(self):
        v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='v1', activities_json=[_act('A1')],
        )
        section = report_service.build_schedule_risk_recovery_section(self.project, v1)
        self.assertFalse(section['available'])
        self.assertIn('at least two schedule versions', section['reason'])

    def test_available_with_detected_risk_and_workflow(self):
        prev = ScheduleUpload.objects.create(
            project=self.project, original_filename='prev.xer', sanitized_filename='prev.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='Prev', upload_timestamp=timezone.now() - timedelta(days=7),
            activities_json=[_act('A1', earlyFinish='2026-01-20', totalFloat=2.0)],
        )
        curr = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', sanitized_filename='curr.xer', file_type='XER',
            data_date=date(2026, 8, 15), version_label='Curr', upload_timestamp=timezone.now(),
            activities_json=[_act('A1', earlyFinish='2026-08-30', totalFloat=-14.0, isCritical=True, onLongestPath=True)],
        )
        ScheduleRisk.objects.create(project=self.project, risk_key='A1', status='MITIGATION_PLANNED', owner='J. Smith')

        section = report_service.build_schedule_risk_recovery_section(self.project, curr)
        self.assertTrue(section['available'])
        self.assertEqual(len(section['risks']), 1)
        risk = section['risks'][0]
        self.assertEqual(risk['activityId'], 'A1')
        self.assertEqual(risk['workflow']['status'], 'MITIGATION_PLANNED')
        self.assertEqual(risk['workflow']['owner'], 'J. Smith')
        self.assertIn('CRITICAL', (section['summary']['bySeverity'].keys()))

    def test_scenarios_and_mitigation_actions_included(self):
        prev = ScheduleUpload.objects.create(
            project=self.project, original_filename='prev.xer', sanitized_filename='prev.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='Prev', upload_timestamp=timezone.now() - timedelta(days=7),
            activities_json=[_act('A1', totalFloat=2.0)],
        )
        curr = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', sanitized_filename='curr.xer', file_type='XER',
            data_date=date(2026, 8, 15), version_label='Curr', upload_timestamp=timezone.now(),
            activities_json=[_act('A1', earlyFinish='2026-08-30', totalFloat=-5.0, isCritical=True, onLongestPath=True)],
        )
        risk = ScheduleRisk.objects.create(project=self.project, risk_key='A1')
        scenario = RecoveryScenario.objects.create(
            project=self.project, schedule_upload=curr, risk=risk, name='Compress A1', status='ACCEPTED',
            assumptions=[{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 10}],
            result={
                'currentForecastFinish': '2026-08-30', 'scenarioForecastFinish': '2026-08-20', 'recoveryDays': 10,
                'projectBaselineRecovery': {'baselineFinish': '2026-01-20', 'currentBaselineVarianceDays': 30, 'scenarioBaselineVarianceDays': 20, 'varianceRecoveredDays': 10, 'recoveryPct': 33.3},
                'milestoneImpact': [], 'sideEffects': {'improvedCount': 1, 'worsenedCount': 0, 'newlyCriticalCount': 0, 'newlyNegativeFloatCount': 0},
                'warnings': [],
            },
        )
        MitigationAction.objects.create(project=self.project, risk=risk, scenario=scenario, description='Expedite crew', owner='PM', status='OPEN')

        section = report_service.build_schedule_risk_recovery_section(self.project, curr)
        self.assertTrue(section['available'])
        self.assertEqual(len(section['scenarios']), 1)
        self.assertEqual(section['scenarios'][0]['status'], 'ACCEPTED')
        self.assertEqual(len(section['acceptedScenarios']), 1)
        self.assertEqual(len(section['mitigationActions']), 1)
        self.assertEqual(section['openMitigationActionCount'], 1)

    def test_recovery_tracking_populated_for_accepted_scenario_against_newer_version(self):
        v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='v1', upload_timestamp=timezone.now() - timedelta(days=14),
            activities_json=[_act('A1', totalFloat=2.0)],
        )
        v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 8, 15), version_label='v2', upload_timestamp=timezone.now() - timedelta(days=7),
            activities_json=[_act('A1', earlyFinish='2026-08-30', totalFloat=-5.0, isCritical=True, onLongestPath=True)],
        )
        scenario = RecoveryScenario.objects.create(
            project=self.project, schedule_upload=v2, name='Compress A1', status='ACCEPTED',
            assumptions=[], result={'milestoneImpact': [{'activityId': 'A1', 'activityName': 'Activity A1', 'scenarioForecastFinish': '2026-08-20'}]},
        )
        # A genuinely newer version than the scenario's own source (v2) —
        # A1's next-update forecast finish (2026-08-22) is compared against
        # the scenario's target (2026-08-20): 2 days late -> PARTIALLY_REALIZED.
        v3 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v3.xer', sanitized_filename='v3.xer', file_type='XER',
            data_date=date(2026, 8, 22), version_label='v3', upload_timestamp=timezone.now(),
            activities_json=[_act('A1', finish='2026-08-22')],
        )
        section = report_service.build_schedule_risk_recovery_section(self.project, v3, previous_version_id=str(v2.id))
        self.assertTrue(section['available'])
        s = next(s for s in section['scenarios'] if s['id'] == str(scenario.id))
        self.assertIsNotNone(s['recoveryTracking'])
        self.assertTrue(s['recoveryTracking']['available'])
        self.assertEqual(s['recoveryTracking']['milestoneComparisons'][0]['classification'], 'PARTIALLY_REALIZED')

    def test_report_payload_includes_section(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='v1', upload_timestamp=timezone.now() - timedelta(days=7),
            activities_json=[_act('A1')],
        )
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 8, 15), version_label='v2', upload_timestamp=timezone.now(),
            activities_json=[_act('A1')],
        )
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        self.assertIn('scheduleRiskRecovery', payload['sections'])
        self.assertIsNotNone(payload['scheduleRiskRecovery'])
        self.assertTrue(payload['scheduleRiskRecovery']['available'])


class ScheduleRiskRecoveryExportTests(TestCase):
    """PDF stays a concise management summary; Excel carries the complete,
    never-truncated detail. Both must render without error whether or not
    the section is available."""

    def setUp(self):
        self.project = Project.objects.create(name='Risk Recovery Export Test')
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2026, 8, 1), version_label='Prev', upload_timestamp=timezone.now() - timedelta(days=7),
            activities_json=[_act('A1', totalFloat=2.0)],
        )
        self.curr = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 8, 15), version_label='Curr', upload_timestamp=timezone.now(),
            activities_json=[_act('A1', earlyFinish='2026-08-30', totalFloat=-5.0, isCritical=True, onLongestPath=True)],
        )
        risk = ScheduleRisk.objects.create(project=self.project, risk_key='A1', status='OPEN')
        scenario = RecoveryScenario.objects.create(
            project=self.project, schedule_upload=self.curr, risk=risk, name='Compress A1', status='ACCEPTED',
            assumptions=[{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 10}],
            result={
                'currentForecastFinish': '2026-08-30', 'scenarioForecastFinish': '2026-08-20', 'recoveryDays': 10,
                'projectBaselineRecovery': {'baselineFinish': '2026-01-20', 'currentBaselineVarianceDays': 30, 'scenarioBaselineVarianceDays': 20, 'varianceRecoveredDays': 10, 'recoveryPct': 33.3},
                'milestoneImpact': [{'activityId': 'A1', 'activityName': 'Activity A1', 'currentForecastFinish': '2026-08-30', 'scenarioForecastFinish': '2026-08-20', 'movementDays': 10, 'baselineFinish': '2026-01-20', 'currentBaselineVarianceDays': 30, 'scenarioBaselineVarianceDays': 20, 'varianceRecoveredDays': 10, 'recoveryPct': 33.3}],
                'sideEffects': {'improvedCount': 1, 'worsenedCount': 0, 'newlyCriticalCount': 0, 'newlyNegativeFloatCount': 0},
                'warnings': [],
            },
        )
        MitigationAction.objects.create(project=self.project, risk=risk, scenario=scenario, description='Expedite crew', owner='PM', due_date=date(2026, 8, 1), status='OPEN')
        self.payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS', version_id=str(self.curr.id))

    def test_pdf_renders_with_section(self):
        pdf_bytes = report_export.generate_report_pdf(self.payload)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_pdf_renders_without_section(self):
        payload = dict(self.payload)
        payload['scheduleRiskRecovery'] = None
        pdf_bytes = report_export.generate_report_pdf(payload)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_excel_includes_all_risk_recovery_worksheets(self):
        xlsx_bytes = report_export.generate_report_excel(self.payload)
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        for name in ('Schedule Risks', 'Recovery Scenarios', 'Scenario Changes', 'Mitigation Actions', 'Recovery Tracking', 'Milestone Recovery'):
            self.assertIn(name, wb.sheetnames, f'missing worksheet: {name}')

        ws = wb['Schedule Risks']
        rows = list(ws.iter_rows(values_only=True))
        self.assertGreaterEqual(len(rows), 2)  # header + at least one risk

        ws_mit = wb['Mitigation Actions']
        mit_rows = list(ws_mit.iter_rows(values_only=True))
        self.assertEqual(mit_rows[1][0], 'Expedite crew')

    def test_excel_overdue_mitigation_action_flagged(self):
        xlsx_bytes = report_export.generate_report_excel(self.payload)
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb['Mitigation Actions']
        rows = list(ws.iter_rows(values_only=True))
        headers = rows[0]
        overdue_idx = headers.index('Overdue')
        self.assertTrue(rows[1][overdue_idx])
