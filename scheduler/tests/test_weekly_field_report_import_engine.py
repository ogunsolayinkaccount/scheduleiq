"""
Weekly Field Report Excel import — pure engine tests (column detection +
row extraction). No Django test client here; this is the same "pure,
DB-free" layer dashboardFormat.ts-style tests exercise on the frontend,
just on the Python side — see test_weekly_field_report_import_api.py for
the Django view layer (project identity, duplicate detection, atomic
commit) built on top of this.
"""
import pandas as pd
from django.test import SimpleTestCase

from scheduler.weekly_field_report_mapping import detect_weekly_report_columns, WEEKLY_REPORT_FIELD_SYNONYMS
from scheduler.weekly_field_report_import import extract_weekly_report_rows


class DetectWeeklyReportColumnsTests(SimpleTestCase):
    def test_recognizes_the_standard_header_set(self):
        mapped = detect_weekly_report_columns([
            'Project ID', 'Week Start Date', 'Actual Headcount', 'Next Week Forecast',
            'PM Projection', 'Monthly Target', 'Last Client Update',
        ])
        self.assertEqual(mapped['project_id'], 'Project ID')
        self.assertEqual(mapped['week_start_date'], 'Week Start Date')
        self.assertEqual(mapped['actual_headcount'], 'Actual Headcount')
        self.assertEqual(mapped['next_week_forecast_headcount'], 'Next Week Forecast')
        self.assertEqual(mapped['pm_projected_headcount'], 'PM Projection')
        self.assertEqual(mapped['monthly_target_headcount'], 'Monthly Target')
        self.assertEqual(mapped['last_client_update_date'], 'Last Client Update')

    def test_recognizes_common_header_variants(self):
        mapped = detect_weekly_report_columns(['Week Of', 'Field Headcount', 'Forecast Headcount'])
        self.assertEqual(mapped['week_start_date'], 'Week Of')
        self.assertEqual(mapped['actual_headcount'], 'Field Headcount')
        self.assertEqual(mapped['next_week_forecast_headcount'], 'Forecast Headcount')

    def test_never_matches_a_schedule_activity_vocabulary_term(self):
        # "WBS" / "Total Float" / "Activity ID" are column_mapping.py's own
        # vocabulary — must never be mistaken for a weekly-report field.
        mapped = detect_weekly_report_columns(['Activity ID', 'WBS', 'Total Float', 'Week Start Date'])
        self.assertNotIn('activity_id', WEEKLY_REPORT_FIELD_SYNONYMS)
        self.assertEqual(mapped.get('week_start_date'), 'Week Start Date')
        self.assertEqual(len(mapped), 1)  # only the one genuinely recognized column

    def test_unrecognized_headers_map_to_nothing(self):
        mapped = detect_weekly_report_columns(['Notes', 'Comments', 'Reviewed By'])
        self.assertEqual(mapped, {})


def _df(columns, rows):
    return pd.DataFrame(rows, columns=columns)


class ExtractWeeklyReportRowsTests(SimpleTestCase):
    def test_extracts_a_well_formed_workbook(self):
        df = _df(
            ['Project ID', 'Week Start Date', 'Actual Headcount', 'Next Week Forecast', 'PM Projection', 'Monthly Target', 'Last Client Update'],
            [
                ['Barn-01', '2026-09-14', 30, 35, 50, 50, '2026-09-15'],
                ['Barn-01', '2026-09-21', 35, 39, 50, 50, '2026-09-22'],
            ],
        )
        result = extract_weekly_report_rows(df)
        self.assertEqual(result['rowCount'], 2)
        self.assertEqual(result['unmappedColumns'], [])
        row0 = result['rows'][0]
        self.assertEqual(row0['rowNumber'], 2)  # header is row 1, first data row is row 2
        self.assertEqual(row0['projectIdRaw'], 'Barn-01')
        self.assertEqual(row0['weekStartDate'], '2026-09-14')
        self.assertEqual(row0['actualHeadcount'], '30')
        self.assertEqual(row0['nextWeekForecastHeadcount'], '35')
        self.assertEqual(row0['lastClientUpdateDate'], '2026-09-15')
        row1 = result['rows'][1]
        self.assertEqual(row1['rowNumber'], 3)
        self.assertEqual(row1['weekStartDate'], '2026-09-21')

    def test_blank_cells_become_none_not_nan_or_the_string_nan(self):
        df = _df(
            ['Week Start Date', 'Actual Headcount', 'PM Projection'],
            [['2026-09-14', None, '']],
        )
        row = extract_weekly_report_rows(df)['rows'][0]
        self.assertIsNone(row['actualHeadcount'])
        self.assertIsNone(row['pmProjectedHeadcount'])

    def test_unmapped_columns_are_reported_not_silently_dropped(self):
        df = _df(['Week Start Date', 'Site Notes'], [['2026-09-14', 'Rain delay Tuesday']])
        result = extract_weekly_report_rows(df)
        self.assertIn('Site Notes', result['unmappedColumns'])

    def test_a_week_start_date_that_excel_stored_as_a_real_date_is_normalized_to_iso(self):
        df = _df(['Week Start Date'], [[pd.Timestamp('2026-09-14')]])
        row = extract_weekly_report_rows(df)['rows'][0]
        self.assertEqual(row['weekStartDate'], '2026-09-14')

    def test_row_with_no_recognizable_fields_still_appears_rather_than_vanishing(self):
        df = _df(['Mystery Column'], [['???']])
        result = extract_weekly_report_rows(df)
        self.assertEqual(result['rowCount'], 1)
        self.assertIsNone(result['rows'][0]['weekStartDate'])

    def test_missing_project_id_column_yields_none_not_a_crash(self):
        df = _df(['Week Start Date'], [['2026-09-14']])
        row = extract_weekly_report_rows(df)['rows'][0]
        self.assertIsNone(row['projectIdRaw'])
