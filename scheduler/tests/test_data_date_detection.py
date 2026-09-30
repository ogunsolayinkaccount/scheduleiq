import pandas as pd
from django.test import SimpleTestCase

from scheduler.data_date_detection import (
    detect_data_date_pdf,
    detect_data_date_tabular,
    detect_data_date_xer,
)


class XerDataDateTests(SimpleTestCase):
    def test_detects_authoritative_data_date(self):
        result = detect_data_date_xer({'data_date': __import__('datetime').date(2026, 8, 25)})
        self.assertEqual(result['detectedDataDate'], '2026-08-25')
        self.assertEqual(result['source'], 'P6_XER_PROJECT')
        self.assertEqual(result['confidence'], 'authoritative')

    def test_missing_project_table_returns_unavailable(self):
        result = detect_data_date_xer({})
        self.assertIsNone(result['detectedDataDate'])
        self.assertIsNone(result['source'])
        self.assertIsNone(result['confidence'])

    def test_none_project_meta_does_not_crash(self):
        result = detect_data_date_xer(None)
        self.assertIsNone(result['detectedDataDate'])


class TabularDataDateTests(SimpleTestCase):
    def test_explicit_column_header(self):
        df = pd.DataFrame({
            'Activity ID': ['A1000', 'A1010'],
            'Data Date': ['8/25/2026', '8/25/2026'],
        })
        result = detect_data_date_tabular(df, 'EXCEL_METADATA')
        self.assertEqual(result['detectedDataDate'], '2026-08-25')
        self.assertEqual(result['confidence'], 'high')
        self.assertEqual(result['source'], 'EXCEL_METADATA')

    def test_status_date_label_same_row_next_cell(self):
        df = pd.DataFrame([
            ['Status Date', '08/25/2026', None],
            ['Activity ID', 'Activity Name', 'Finish'],
            ['A1000', 'Mobilize', '9/1/2026'],
        ])
        result = detect_data_date_tabular(df, 'EXCEL_METADATA')
        self.assertEqual(result['detectedDataDate'], '2026-08-25')
        self.assertEqual(result['confidence'], 'high')

    def test_label_above_value_medium_confidence(self):
        df = pd.DataFrame([
            ['Current Data Date', None, None],
            ['2026-08-25', None, None],
            ['Activity ID', 'Activity Name', 'Finish'],
        ])
        result = detect_data_date_tabular(df, 'CSV_METADATA')
        self.assertEqual(result['detectedDataDate'], '2026-08-25')
        self.assertEqual(result['confidence'], 'medium')
        self.assertEqual(result['source'], 'CSV_METADATA')

    def test_no_explicit_date_returns_unavailable(self):
        df = pd.DataFrame({
            'Activity ID': ['A1000', 'A1010'],
            'Activity Name': ['Mobilize', 'Excavate'],
            'Finish': ['9/1/2026', '9/15/2026'],
        })
        result = detect_data_date_tabular(df, 'EXCEL_METADATA')
        self.assertIsNone(result['detectedDataDate'])
        self.assertIsNone(result['source'])

    def test_never_infers_from_activity_progress(self):
        # Even with plausible-looking finish dates nearby, no label -> no detection.
        df = pd.DataFrame({
            'Activity ID': ['A1000'],
            '% Complete': [45],
            'Finish': ['9/1/2026'],
        })
        result = detect_data_date_tabular(df, 'EXCEL_METADATA')
        self.assertIsNone(result['detectedDataDate'])

    def test_empty_dataframe(self):
        result = detect_data_date_tabular(pd.DataFrame(), 'EXCEL_METADATA')
        self.assertIsNone(result['detectedDataDate'])

    def test_none_dataframe_does_not_crash(self):
        result = detect_data_date_tabular(None, 'EXCEL_METADATA')
        self.assertIsNone(result['detectedDataDate'])

    def test_exact_dd_label_match_only(self):
        # A cell containing "DD" as part of a longer unrelated string must
        # NOT match — only an exact-cell 'DD' label counts.
        df = pd.DataFrame([
            ['DDL-2026-001', '8/25/2026', None],
            ['Activity ID', 'Activity Name', 'Finish'],
        ])
        result = detect_data_date_tabular(df, 'EXCEL_METADATA')
        self.assertIsNone(result['detectedDataDate'])


class PdfDataDateTests(SimpleTestCase):
    def test_explicit_data_date_label(self):
        text = 'Project Schedule Report\nData Date: August 25, 2026\nActivity ID  Activity Name  Finish'
        result = detect_data_date_pdf(text)
        self.assertEqual(result['detectedDataDate'], '2026-08-25')
        self.assertEqual(result['source'], 'PDF_METADATA')
        self.assertEqual(result['confidence'], 'high')

    def test_status_date_numeric_format(self):
        text = 'Status Date: 08/25/2026\n'
        result = detect_data_date_pdf(text)
        self.assertEqual(result['detectedDataDate'], '2026-08-25')

    def test_no_label_returns_unavailable(self):
        text = 'Activity A1000 finishes 09/01/2026 with 45% complete.'
        result = detect_data_date_pdf(text)
        self.assertIsNone(result['detectedDataDate'])

    def test_empty_text(self):
        result = detect_data_date_pdf('')
        self.assertIsNone(result['detectedDataDate'])

    def test_never_guesses_from_printed_activity_dates(self):
        text = 'Schedule as of report generation. A1000 Start 08/01/2026 Finish 09/01/2026.'
        result = detect_data_date_pdf(text)
        self.assertIsNone(result['detectedDataDate'])
