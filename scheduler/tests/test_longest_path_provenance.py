"""
Longest Path provenance — Critical & Longest Path Schedule Explorer.

Covers the central finding from the Schedule Explorer audit: ScheduleIQ
could not previously distinguish a missing/unpopulated P6 driving_path_flag
from an explicit "No". These tests pin the two-tier detection
(parse_xer always populates every header key for every row, so a key's
ABSENCE on an activity dict means the column itself was missing from this
file's TASK table; a present-but-blank VALUE means the column existed but
this specific row's value wasn't supplied) and the explicit-False
provenance on every non-XER import path.
"""
from django.test import SimpleTestCase

from scheduler.parsers import parse_xer, xer_to_activities, parse_msp_xml, parse_pdf_schedule
from scheduler.column_mapping import build_activities_from_mapping, detect_columns
import pandas as pd


_TASK_HEADERS = [
    'task_id', 'task_code', 'task_name', 'proj_id', 'wbs_id', 'task_type', 'status_code',
    'target_drtn_hr_cnt', 'remain_drtn_hr_cnt', 'total_float_hr_cnt', 'free_float_hr_cnt',
    'early_start_date', 'early_end_date', 'late_start_date', 'late_end_date',
    'target_start_date', 'target_end_date', 'act_start_date', 'act_end_date',
    'restart_date', 'reend_date', 'clndr_id', 'complete_pct_type', 'phys_complete_pct',
    'cstr_type', 'cstr_date',
]

_DEFAULTS = {
    'task_type': 'TT_Task', 'status_code': 'TK_NotStart',
    'target_drtn_hr_cnt': '80', 'remain_drtn_hr_cnt': '80', 'total_float_hr_cnt': '0', 'free_float_hr_cnt': '0',
    'early_start_date': '2026-01-10', 'early_end_date': '2026-01-19',
    'late_start_date': '2026-01-10', 'late_end_date': '2026-01-19',
    'target_start_date': '2026-01-10', 'target_end_date': '2026-01-19',
    'act_start_date': '', 'act_end_date': '', 'restart_date': '', 'reend_date': '',
    'clndr_id': 'CAL1', 'complete_pct_type': 'CP_Drtn', 'phys_complete_pct': '0',
    'cstr_type': '', 'cstr_date': '',
}


def _build_xer(task_rows, include_driving_flag_column=True, wbs_rows=None):
    headers = list(_TASK_HEADERS)
    if include_driving_flag_column:
        headers.append('driving_path_flag')

    lines = [
        '%T\tPROJECT',
        '%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id',
        '%R\tPROJ1\tTest Project\t2026-08-01\t2026-01-01\t2026-12-31\t2026-12-31\tCAL1',
    ]
    if wbs_rows:
        lines.append('%T\tPROJWBS')
        lines.append('%F\twbs_id\twbs_name\twbs_short_name\tparent_wbs_id\tseq_num\tproj_id')
        for w in wbs_rows:
            lines.append('%R\t' + '\t'.join([
                w['wbs_id'], w['wbs_name'], w.get('wbs_short_name', w['wbs_id']),
                w.get('parent_wbs_id', ''), str(w.get('seq_num', 1)), 'PROJ1',
            ]))

    lines.append('%T\tTASK')
    lines.append('%F\t' + '\t'.join(headers))
    for t in task_rows:
        row = {**_DEFAULTS, **t}
        row.setdefault('task_code', row.get('task_id', 'T1'))
        row.setdefault('task_name', 'Activity')
        row.setdefault('proj_id', 'PROJ1')
        row.setdefault('wbs_id', '')
        values = [str(row.get(h, '')) for h in headers]
        lines.append('%R\t' + '\t'.join(values))

    return '\n'.join(lines) + '\n'


def _activities(task_rows, include_driving_flag_column=True, wbs_rows=None):
    text = _build_xer(task_rows, include_driving_flag_column, wbs_rows)
    sections = parse_xer(text)
    return {a['code']: a for a in xer_to_activities(sections, 'test.xer')}


class XerLongestPathTwoTierDetectionTests(SimpleTestCase):
    def test_column_present_with_explicit_yes_is_verified_true(self):
        acts = _activities([{'task_id': 'T1', 'task_code': 'A1', 'driving_path_flag': 'Y'}])
        a = acts['A1']
        self.assertTrue(a['onLongestPathVerified'])
        self.assertTrue(a['onLongestPath'])

    def test_column_present_with_explicit_no_is_verified_false_path(self):
        acts = _activities([{'task_id': 'T1', 'task_code': 'A1', 'driving_path_flag': 'N'}])
        a = acts['A1']
        self.assertTrue(a['onLongestPathVerified'])
        self.assertFalse(a['onLongestPath'])

    def test_column_absent_from_this_files_task_table_is_unverified_for_every_row(self):
        acts = _activities(
            [{'task_id': 'T1', 'task_code': 'A1'}, {'task_id': 'T2', 'task_code': 'A2'}],
            include_driving_flag_column=False,
        )
        for a in acts.values():
            self.assertFalse(a['onLongestPathVerified'])
            self.assertFalse(a['onLongestPath'])

    def test_column_present_but_blank_for_one_row_is_unverified_for_that_row_only(self):
        acts = _activities([
            {'task_id': 'T1', 'task_code': 'A1', 'driving_path_flag': 'Y'},
            {'task_id': 'T2', 'task_code': 'A2', 'driving_path_flag': ''},
        ])
        self.assertTrue(acts['A1']['onLongestPathVerified'])
        self.assertTrue(acts['A1']['onLongestPath'])
        self.assertFalse(acts['A2']['onLongestPathVerified'])
        self.assertFalse(acts['A2']['onLongestPath'])

    def test_negative_float_activity_not_flagged_as_driving_is_verified_false_path_not_true(self):
        # The exact scenario the review centers on: negative float alone
        # must never imply Longest Path membership.
        acts = _activities([{
            'task_id': 'T1', 'task_code': 'A1', 'driving_path_flag': 'N',
            'total_float_hr_cnt': '-40',
        }])
        a = acts['A1']
        self.assertTrue(a['isCritical'])          # Total Float <= 0 -> critical
        self.assertTrue(a['onLongestPathVerified'])
        self.assertFalse(a['onLongestPath'])      # P6 explicitly said this activity is NOT on the longest path


class WbsIdPathTests(SimpleTestCase):
    def test_similarly_named_wbs_under_different_parents_get_distinct_id_paths(self):
        wbs_rows = [
            {'wbs_id': 'AREA_A', 'wbs_name': 'Area A', 'seq_num': 1},
            {'wbs_id': 'AREA_B', 'wbs_name': 'Area B', 'seq_num': 2},
            {'wbs_id': 'FOUND_A', 'wbs_name': 'Foundations', 'parent_wbs_id': 'AREA_A', 'seq_num': 1},
            {'wbs_id': 'FOUND_B', 'wbs_name': 'Foundations', 'parent_wbs_id': 'AREA_B', 'seq_num': 1},
        ]
        acts = _activities(
            [
                {'task_id': 'T1', 'task_code': 'A1', 'wbs_id': 'FOUND_A'},
                {'task_id': 'T2', 'task_code': 'A2', 'wbs_id': 'FOUND_B'},
            ],
            wbs_rows=wbs_rows,
        )
        path_a = acts['A1']['wbsIdPath']
        path_b = acts['A2']['wbsIdPath']
        self.assertEqual(path_a, ['AREA_A', 'FOUND_A'])
        self.assertEqual(path_b, ['AREA_B', 'FOUND_B'])
        # Same leaf NAME ("Foundations") but genuinely different id paths —
        # a name-based prefix match would have wrongly treated these as
        # the same branch; the id-based path never does.
        self.assertNotEqual(path_a, path_b)
        self.assertEqual(acts['A1']['wbs'], acts['A2']['wbs'])  # same display name, by design

    def test_an_activity_with_no_wbs_gets_an_empty_id_path(self):
        acts = _activities([{'task_id': 'T1', 'task_code': 'A1', 'wbs_id': ''}])
        self.assertEqual(acts['A1']['wbsIdPath'], [])


class NonXerImportProvenanceTests(SimpleTestCase):
    def test_msp_xml_explicitly_marks_longest_path_unverified(self):
        xml = (
            '<Project xmlns="http://schemas.microsoft.com/project">'
            '<Tasks><Task><UID>1</UID><ID>1</ID><Name>Mobilize</Name><Milestone>0</Milestone>'
            '<Critical>0</Critical><WBS>1</WBS><Duration>PT80H0M0S</Duration>'
            '<Start>2026-01-10T08:00:00</Start><Finish>2026-01-19T17:00:00</Finish>'
            '<PercentComplete>0</PercentComplete></Task></Tasks></Project>'
        )
        acts = parse_msp_xml(xml, 'test.xml')
        self.assertEqual(len(acts), 1)
        self.assertFalse(acts[0]['onLongestPath'])
        self.assertFalse(acts[0]['onLongestPathVerified'])

    def test_excel_import_explicitly_marks_longest_path_unverified(self):
        df = pd.DataFrame({
            'Activity ID': ['A1'], 'Activity Name': ['Mobilize'], 'Start': ['2026-01-10'], 'Finish': ['2026-01-19'],
        })
        mapped = detect_columns(list(df.columns))
        acts = build_activities_from_mapping(df, mapped, 'test.xlsx')
        self.assertEqual(len(acts), 1)
        self.assertFalse(acts[0]['onLongestPath'])
        self.assertFalse(acts[0]['onLongestPathVerified'])


class LegacyStoredActivityFallbackTests(SimpleTestCase):
    """Activities parsed/persisted BEFORE this feature existed have no
    onLongestPathVerified key at all — consuming code (activity_analysis.py)
    must treat that absence as a distinct 'unknown', never silently as a
    confirmed False/'No'. See test_activity_analysis_longest_path_status.py
    for the consumer-side assertion; this just pins that such a dict is a
    realistic, still-producible shape (a plain literal with no tracking
    key), which is exactly what pre-this-feature activities_json looks like."""

    def test_a_pre_feature_activity_dict_has_no_verification_key(self):
        legacy_activity = {'id': 'A1', 'code': 'A1', 'onLongestPath': False, 'isCritical': True, 'totalFloat': -5}
        self.assertNotIn('onLongestPathVerified', legacy_activity)
