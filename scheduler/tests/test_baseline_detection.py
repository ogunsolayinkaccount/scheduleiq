"""
Baseline Detection and Intelligence Enhancement — unit tests for the pure
xer_baseline_detection.detect_additional_xer_project_records() function. Built
directly on scheduler.parsers.parse_xer()'s real output (not a mock), so
these pin the actual section-dict shape the parser produces.
"""
import unittest

from scheduler.parsers import parse_xer
from scheduler.xer_baseline_detection import detect_additional_xer_project_records


def _xer(project_rows, task_rows=()):
    """project_rows: list of dicts with at least proj_id. task_rows: list
    of dicts with at least task_id/proj_id. Builds minimal valid XER text."""
    lines = [
        '%T\tPROJECT',
        '%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id',
    ]
    for p in project_rows:
        lines.append('%R\t' + '\t'.join([
            p.get('proj_id', ''), p.get('proj_short_name', ''), p.get('last_recalc_date', '2026-08-01'),
            '2026-01-01', '2026-12-31', '2026-12-31', 'CAL1',
        ]))
    if task_rows:
        lines.append('%T\tTASK')
        lines.append('%F\ttask_id\ttask_code\ttask_name\tproj_id\twbs_id\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\ttotal_float_hr_cnt\tfree_float_hr_cnt\tearly_start_date\tearly_end_date\tlate_start_date\tlate_end_date\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\trestart_date\treend_date\tclndr_id\tcomplete_pct_type\tphys_complete_pct\tcstr_type\tcstr_date\tdriving_path_flag')
        for t in task_rows:
            lines.append('%R\t' + '\t'.join([
                t.get('task_id', 'T1'), t.get('task_code', 'A1'), 'Activity', t.get('proj_id', ''), 'WBS1',
                'TT_Task', 'TK_NotStart', '80', '80', '0', '0',
                '2026-01-10', '2026-01-19', '2026-01-10', '2026-01-19', '2026-01-10', '2026-01-19',
                '', '', '', '', 'CAL1', 'CP_Drtn', '0', '', '', 'N',
            ]))
    return '\n'.join(lines) + '\n'


class NoBaselineReferencesTests(unittest.TestCase):
    def test_single_project_row_has_zero_references(self):
        sections = parse_xer(_xer([{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}], [{'proj_id': 'PROJ1'}]))
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['projectRecordCount'], 1)
        self.assertEqual(result['primaryProjectId'], 'PROJ1')
        self.assertEqual(result['additionalProjectRecordCount'], 0)
        self.assertEqual(result['additionalProjectRecords'], [])

    def test_empty_file_with_no_project_table_at_all(self):
        sections = parse_xer('')
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['projectRecordCount'], 0)
        self.assertIsNone(result['primaryProjectId'])
        self.assertEqual(result['additionalProjectRecordCount'], 0)
        self.assertEqual(result['additionalProjectRecords'], [])


class SingleBaselineReferenceTests(unittest.TestCase):
    def test_reference_with_complete_schedule_data(self):
        sections = parse_xer(_xer(
            [{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}, {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'}],
            [{'proj_id': 'PROJ1'}, {'proj_id': 'PROJ1'}, {'proj_id': 'BL1'}],
        ))
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['projectRecordCount'], 2)
        self.assertEqual(result['primaryProjectId'], 'PROJ1')
        self.assertEqual(result['additionalProjectRecordCount'], 1)
        ref = result['additionalProjectRecords'][0]
        self.assertEqual(ref['projectId'], 'BL1')
        self.assertEqual(ref['projectName'], 'Baseline 1')
        self.assertTrue(ref['hasCompleteScheduleData'])
        self.assertEqual(ref['activityCount'], 1)
        self.assertIsNone(ref['assignmentType'])  # never invented — see module docstring

    def test_reference_without_any_schedule_data_is_distinguished(self):
        # A bare PROJECT row for BL1 — no TASK rows for it anywhere in the file.
        sections = parse_xer(_xer(
            [{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}, {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'}],
            [{'proj_id': 'PROJ1'}],
        ))
        result = detect_additional_xer_project_records(sections)
        ref = result['additionalProjectRecords'][0]
        self.assertFalse(ref['hasCompleteScheduleData'])
        self.assertEqual(ref['activityCount'], 0)


class MultipleBaselineReferencesTests(unittest.TestCase):
    def test_multiple_references_are_all_reported_distinctly(self):
        sections = parse_xer(_xer(
            [
                {'proj_id': 'PROJ1', 'proj_short_name': 'Main'},
                {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'},
                {'proj_id': 'BL2', 'proj_short_name': 'Baseline 2'},
            ],
            [{'proj_id': 'PROJ1'}, {'proj_id': 'BL1'}],
        ))
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['projectRecordCount'], 3)
        self.assertEqual(result['additionalProjectRecordCount'], 2)
        ids = {r['projectId'] for r in result['additionalProjectRecords']}
        self.assertEqual(ids, {'BL1', 'BL2'})
        by_id = {r['projectId']: r for r in result['additionalProjectRecords']}
        self.assertTrue(by_id['BL1']['hasCompleteScheduleData'])
        self.assertFalse(by_id['BL2']['hasCompleteScheduleData'])


class MissingOrIncompleteMetadataTests(unittest.TestCase):
    def test_reference_missing_a_project_name_reports_none_not_a_guess(self):
        sections = parse_xer(_xer([{'proj_id': 'PROJ1'}, {'proj_id': 'BL1', 'proj_short_name': ''}]))
        result = detect_additional_xer_project_records(sections)
        self.assertIsNone(result['additionalProjectRecords'][0]['projectName'])

    def test_project_row_with_no_proj_id_is_never_counted_as_a_reference(self):
        sections = parse_xer(_xer([{'proj_id': 'PROJ1'}, {'proj_id': ''}]))
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['projectRecordCount'], 2)
        self.assertEqual(result['additionalProjectRecordCount'], 0)

    def test_explicit_primary_proj_id_overrides_the_first_row_default(self):
        sections = parse_xer(_xer([{'proj_id': 'A'}, {'proj_id': 'B'}]))
        result = detect_additional_xer_project_records(sections, primary_proj_id='B')
        self.assertEqual(result['primaryProjectId'], 'B')
        self.assertEqual([r['projectId'] for r in result['additionalProjectRecords']], ['A'])

    def test_duplicate_proj_id_rows_are_not_double_counted(self):
        sections = parse_xer(_xer([{'proj_id': 'PROJ1'}, {'proj_id': 'BL1'}, {'proj_id': 'BL1'}]))
        result = detect_additional_xer_project_records(sections)
        self.assertEqual(result['additionalProjectRecordCount'], 1)


# ─────────────────────────────────────────────────────────────────────────
# Integration tests — real HTTP round trip through /api/import/preview/ and
# /api/import/commit/, a full multi-PROJECT-row XER fixture (not the
# minimal builder above), matching this codebase's established pattern
# (see test_import_protection.py / test_schedule_identity_api.py).
# ─────────────────────────────────────────────────────────────────────────
import json as _json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from scheduler.models import Project, ScheduleUpload


def _full_xer(project_rows, task_rows_by_proj):
    """project_rows: [{proj_id, proj_short_name}]. task_rows_by_proj:
    {proj_id: [task_code, ...]} — activities for the FIRST project row
    (the one actually imported) need enough rows to clear the identity
    evaluator's minimum population; other proj_ids can have as few as needed
    to represent "a complete baseline schedule" for this test's purpose."""
    lines = [
        '%T\tPROJECT',
        '%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id',
    ]
    for p in project_rows:
        lines.append('%R\t' + '\t'.join([p['proj_id'], p.get('proj_short_name', ''), '2026-08-01', '2026-01-01', '2026-12-31', '2026-12-31', 'CAL1']))
    lines += [
        '%T\tPROJWBS',
        '%F\twbs_id\twbs_name\twbs_short_name\tparent_wbs_id\tseq_num\tproj_id',
        f'%R\tWBS1\tArea A\tAREAA\t\t1\t{project_rows[0]["proj_id"]}',
        '%T\tCALENDAR',
        '%F\tclndr_id\tclndr_name\tclndr_type\tdefault_flag\tday_hr_cnt\tweek_hr_cnt\tmonth_hr_cnt\tyear_hr_cnt\tclndr_data',
        '%R\tCAL1\tStandard 5 Day Workweek\tCA_Base\tY\t8\t40\t172\t2000\t',
        '%T\tTASK',
        '%F\ttask_id\ttask_code\ttask_name\tproj_id\twbs_id\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\ttotal_float_hr_cnt\tfree_float_hr_cnt\tearly_start_date\tearly_end_date\tlate_start_date\tlate_end_date\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\trestart_date\treend_date\tclndr_id\tcomplete_pct_type\tphys_complete_pct\tcstr_type\tcstr_date\tdriving_path_flag',
    ]
    i = 0
    for proj_id, codes in task_rows_by_proj.items():
        for code in codes:
            i += 1
            lines.append(
                f'%R\tTASK{i}\t{code}\tActivity {code}\t{proj_id}\tWBS1\tTT_Task\tTK_NotStart\t80\t80\t0\t0\t'
                f'2026-01-10\t2026-01-19\t2026-01-10\t2026-01-19\t2026-01-10\t2026-01-19\t\t\t\t\tCAL1\tCP_Drtn\t0\t\t\tN'
            )
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _main_codes(n):
    return [f'A-{i:04d}' for i in range(n)]


class ImportApiIntegrationTests(TestCase):
    def test_preview_reports_zero_references_for_an_ordinary_single_project_file(self):
        xer = _full_xer([{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}], {'PROJ1': _main_codes(5)})
        resp = self.client.post('/api/import/preview/', data={'file': SimpleUploadedFile('v1.xer', xer)})
        self.assertEqual(resp.status_code, 200, resp.content)
        info = resp.json()['xerProjectRecordInfo']
        self.assertEqual(info['additionalProjectRecordCount'], 0)
        self.assertEqual(info['projectRecordCount'], 1)

    def test_preview_reports_references_for_a_file_exported_with_baselines(self):
        xer = _full_xer(
            [{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}, {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'}],
            {'PROJ1': _main_codes(5), 'BL1': ['B-0001', 'B-0002']},
        )
        resp = self.client.post('/api/import/preview/', data={'file': SimpleUploadedFile('v1.xer', xer)})
        self.assertEqual(resp.status_code, 200, resp.content)
        info = resp.json()['xerProjectRecordInfo']
        self.assertEqual(info['additionalProjectRecordCount'], 1)
        self.assertTrue(info['additionalProjectRecords'][0]['hasCompleteScheduleData'])

    def test_preview_xer_baseline_info_is_null_for_non_xer_formats(self):
        from django.core.files.uploadedfile import SimpleUploadedFile as SUF
        csv_content = b'Activity ID,Activity Name,Start,Finish\nA1,Test,2026-01-01,2026-01-10\n'
        resp = self.client.post('/api/import/preview/', data={'file': SUF('v1.csv', csv_content)})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(resp.json()['xerProjectRecordInfo'])

    def test_commit_persists_baseline_info_on_the_schedule_upload_row(self):
        xer = _full_xer(
            [{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}, {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'}],
            {'PROJ1': _main_codes(5), 'BL1': ['B-0001']},
        )
        resp = self.client.post('/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', xer), 'projectName': 'Baseline Detect Test'})
        self.assertEqual(resp.status_code, 200, resp.content)
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.xer_project_record_count, 2)
        self.assertEqual(su.xer_additional_project_record_count, 1)
        self.assertEqual(su.xer_additional_project_records[0]['projectId'], 'BL1')

    def test_commit_leaves_baseline_fields_null_for_a_single_project_file(self):
        xer = _full_xer([{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}], {'PROJ1': _main_codes(5)})
        resp = self.client.post('/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', xer), 'projectName': 'No Baseline Refs Test'})
        self.assertEqual(resp.status_code, 200, resp.content)
        su = ScheduleUpload.objects.get(pk=resp.json()['scheduleUploadId'])
        self.assertEqual(su.xer_project_record_count, 1)
        self.assertEqual(su.xer_additional_project_record_count, 0)

    def test_baseline_reference_count_is_never_confused_with_imported_baseline_version_count(self):
        # Importing a file with an in-file baseline REFERENCE must not, by
        # itself, create or count as an imported baseline VERSION — those
        # are entirely different things (see module docstring).
        xer = _full_xer(
            [{'proj_id': 'PROJ1', 'proj_short_name': 'Main'}, {'proj_id': 'BL1', 'proj_short_name': 'Baseline 1'}],
            {'PROJ1': _main_codes(5), 'BL1': ['B-0001']},
        )
        resp = self.client.post('/api/import/commit/', data={'file': SimpleUploadedFile('v1.xer', xer), 'projectName': 'Distinction Test'})
        project_id = resp.json()['projectId']
        self.assertEqual(ScheduleUpload.objects.filter(project_id=project_id).count(), 1)  # only ONE version was created
        bp = self.client.get(f'/api/projects/{project_id}/baseline-progress/').json()
        self.assertEqual(bp['baselineInfo']['importedBaselineVersionCount'], 0)  # CURRENT_UPDATE by default, not a baseline
        self.assertEqual(bp['baselineInfo']['selectedVersionXerInfo']['available'], False)  # no baseline resolved to report on
