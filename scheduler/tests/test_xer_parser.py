from django.test import SimpleTestCase

from scheduler.parsers import (
    extract_xer_reference_data,
    parse_project_meta_from_xer,
    parse_xer,
    xer_to_activities,
)
from .fixtures import SAMPLE_XER


class XerParserGapFillTests(SimpleTestCase):
    def setUp(self):
        self.sections = parse_xer(SAMPLE_XER)

    def test_sections_present(self):
        for table in ('PROJECT', 'PROJWBS', 'TASK', 'TASKPRED', 'CALENDAR',
                      'RSRC', 'TASKRSRC', 'ACTVTYPE', 'ACTVCODE', 'TASKACTV',
                      'UDFTYPE', 'UDFVALUE'):
            self.assertIn(table, self.sections, f'{table} section missing from parsed XER')

    def test_project_metadata(self):
        meta = parse_project_meta_from_xer(self.sections)
        self.assertEqual(meta['project_id'], 'PROJ1')
        self.assertEqual(meta['project_name'], 'Test Project')
        self.assertEqual(meta['data_date'].isoformat(), '2026-08-01')
        self.assertEqual(meta['planned_start'].isoformat(), '2026-01-01')
        self.assertEqual(meta['forecast_finish'].isoformat(), '2026-12-31')
        self.assertEqual(meta['default_calendar_id'], 'CAL1')

    def test_activities_parsed(self):
        activities = xer_to_activities(self.sections, 'sample.xer')
        self.assertEqual(len(activities), 2)
        by_code = {a['code']: a for a in activities}
        self.assertIn('A1000', by_code)
        self.assertIn('A1010', by_code)

    def test_orig_dur_source_imported_when_target_drtn_present(self):
        # Master Activity Analysis phase — Original Duration provenance.
        # TASK1/TASK2 both have a nonzero target_drtn_hr_cnt in SAMPLE_XER.
        activities = xer_to_activities(self.sections, 'sample.xer')
        by_code = {a['code']: a for a in activities}
        self.assertEqual(by_code['A1000']['origDurSource'], 'IMPORTED')
        self.assertEqual(by_code['A1010']['origDurSource'], 'IMPORTED')

    def test_orig_dur_source_fallback_when_target_drtn_blank(self):
        # A task with target_drtn_hr_cnt=0 but a genuine remain_drtn_hr_cnt
        # must report the fallback — `dur` still silently equals remainDur
        # (unchanged, load-bearing behavior) but origDurSource must disclose
        # it was NOT a confidently-imported Original Duration.
        xer = SAMPLE_XER.replace(
            "%R\tTASK1\tA1000\tMobilize Site\tPROJ1\tWBS1\tTT_Task\tTK_Active\t40\t40\t0\t0\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t\t\t2026-01-05\t2026-01-09\tCAL1\tCP_Drtn\t0\t\t\tY",
            "%R\tTASK1\tA1000\tMobilize Site\tPROJ1\tWBS1\tTT_Task\tTK_Active\t0\t40\t0\t0\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t\t\t2026-01-05\t2026-01-09\tCAL1\tCP_Drtn\t0\t\t\tY",
        )
        sections = parse_xer(xer)
        activities = xer_to_activities(sections, 'sample.xer')
        a1000 = next(a for a in activities if a['code'] == 'A1000')
        self.assertEqual(a1000['origDurSource'], 'FALLBACK_REMAINING')
        self.assertEqual(a1000['dur'], a1000['remainDur'])  # unchanged fallback behavior preserved

    def test_orig_dur_source_unavailable_when_both_blank(self):
        xer = SAMPLE_XER.replace(
            "%R\tTASK1\tA1000\tMobilize Site\tPROJ1\tWBS1\tTT_Task\tTK_Active\t40\t40\t0\t0\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t\t\t2026-01-05\t2026-01-09\tCAL1\tCP_Drtn\t0\t\t\tY",
            "%R\tTASK1\tA1000\tMobilize Site\tPROJ1\tWBS1\tTT_Task\tTK_Active\t0\t0\t0\t0\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t\t\t2026-01-05\t2026-01-09\tCAL1\tCP_Drtn\t0\t\t\tY",
        )
        sections = parse_xer(xer)
        activities = xer_to_activities(sections, 'sample.xer')
        a1000 = next(a for a in activities if a['code'] == 'A1000')
        self.assertEqual(a1000['origDurSource'], 'UNAVAILABLE')
        self.assertEqual(a1000['dur'], 0.0)

    def test_cost_loaded_activity_captures_budget_actual_remaining(self):
        # Phase A audit finding: TASKRSRC-sourced cost/hours are already
        # captured at import time — this locks that behavior in. TASK1
        # (A1000) has a fully-consumed cost-loaded assignment in SAMPLE_XER
        # (40 budgeted units/$2000, 40 actual units/$2000, 0 remaining).
        activities = xer_to_activities(self.sections, 'sample.xer')
        a = next(a for a in activities if a['code'] == 'A1000')
        self.assertTrue(a['isCostLoaded'])
        self.assertTrue(a['isResourceLoaded'])
        self.assertEqual(a['budgetedCost'], 2000.0)
        self.assertEqual(a['actualCost'], 2000.0)
        self.assertEqual(a['remainingCost'], 0.0)
        self.assertEqual(a['budgetedHours'], 40.0)
        self.assertEqual(a['actualHours'], 40.0)
        self.assertEqual(a['resourceAssignments'][0]['rsrcName'], 'John Smith')

    def test_activity_without_taskrsrc_row_is_not_loaded(self):
        # A1010/TASK2 has no TASKRSRC row in SAMPLE_XER — must report
        # unloaded, not a fabricated zero-cost budget.
        activities = xer_to_activities(self.sections, 'sample.xer')
        a = next(a for a in activities if a['code'] == 'A1010')
        self.assertFalse(a['isCostLoaded'])
        self.assertFalse(a['isResourceLoaded'])

    def test_wbs_hierarchy(self):
        activities = xer_to_activities(self.sections, 'sample.xer')
        a = next(a for a in activities if a['code'] == 'A1000')
        self.assertEqual(a['wbs'], 'Area A')
        self.assertEqual(a['wbsCode'], 'AREAA')

    def test_relationship_parsed_with_type_and_lag(self):
        activities = xer_to_activities(self.sections, 'sample.xer')
        succ = next(a for a in activities if a['code'] == 'A1010')
        self.assertEqual(len(succ['predecessors']), 1)
        pred = succ['predecessors'][0]
        self.assertEqual(pred['actId'], 'TASK1')
        self.assertEqual(pred['relType'], 'FS')
        self.assertEqual(pred['lagDays'], 0.0)

    def test_calendar_reference_data(self):
        ref = extract_xer_reference_data(self.sections)
        self.assertEqual(len(ref['calendars']), 1)
        cal = ref['calendars'][0]
        self.assertEqual(cal['calendar_id'], 'CAL1')
        self.assertEqual(cal['name'], 'Standard 5 Day Workweek')
        self.assertEqual(cal['hours_per_day'], 8.0)
        self.assertFalse(cal['has_detailed_definition'])   # honesty check — no fabricated precision

    def test_activity_code_type_and_assignment(self):
        ref = extract_xer_reference_data(self.sections)
        self.assertEqual(len(ref['code_types']), 1)
        self.assertEqual(ref['code_types'][0]['name'], 'Discipline')
        self.assertEqual(len(ref['codes']), 1)
        self.assertEqual(ref['codes'][0]['code_value'], 'Electrical')
        self.assertIn('TASK1', ref['task_codes'])
        self.assertEqual(ref['task_codes']['TASK1'][0]['value'], 'Electrical')

    def test_udf_value_attached_to_task(self):
        ref = extract_xer_reference_data(self.sections)
        self.assertEqual(ref['udf_types'][0]['field_name'], 'Contract Reference')
        self.assertEqual(ref['task_udfs']['TASK1']['Contract Reference'], 'CR-2024-001')

    def test_activity_enriched_with_calendar_codes_and_udfs(self):
        activities = xer_to_activities(self.sections, 'sample.xer')
        a = next(a for a in activities if a['code'] == 'A1000')
        self.assertEqual(a['calendarName'], 'Standard 5 Day Workweek')
        self.assertEqual(a['activityCodes'], {'Discipline': 'Electrical'})
        self.assertEqual(a['udfs'], {'Contract Reference': 'CR-2024-001'})
        # Canonical field so Excel-imported and XER-imported schedules filter uniformly
        self.assertEqual(a['discipline'], 'Electrical')

    def test_calendar_with_decodable_clndr_data_gets_detailed_definition(self):
        # A calendar row carrying a decodable clndr_data blob (Mon-Fri
        # working week) should come through with has_detailed_definition
        # True and a populated standard_workweek — unlike the shared
        # SAMPLE_XER fixture's calendar, whose clndr_data is intentionally
        # blank (see test_calendar_reference_data's honesty check above).
        clndr_data = (
            "(CalendarData"
            "(DaysOfWeek(Day(1))(Day(2)(Shift(1)))(Day(3)(Shift(1)))"
            "(Day(4)(Shift(1)))(Day(5)(Shift(1)))(Day(6)(Shift(1)))(Day(7)))"
            "(Exceptions))"
        )
        sections = dict(self.sections)
        sections['CALENDAR'] = [{
            'clndr_id': 'CAL2', 'clndr_name': 'Detailed Cal', 'clndr_type': 'CA_Base',
            'default_flag': 'N', 'day_hr_cnt': '8', 'week_hr_cnt': '40',
            'month_hr_cnt': '172', 'year_hr_cnt': '2000', 'clndr_data': clndr_data,
        }]
        ref = extract_xer_reference_data(sections)
        cal = ref['calendars'][0]
        self.assertTrue(cal['has_detailed_definition'])
        self.assertEqual(cal['standard_workweek'][1], True)   # Monday
        self.assertEqual(cal['standard_workweek'][7], False)  # Sunday
        self.assertEqual(cal['exceptions'], [])
