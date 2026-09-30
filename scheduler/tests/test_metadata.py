from django.test import SimpleTestCase

from scheduler.schedule_metadata import compute_upload_metadata, derive_version_label
from .fixtures import make_activity


class ComputeUploadMetadataTests(SimpleTestCase):
    def test_empty_activities_returns_zeroed_shape(self):
        result = compute_upload_metadata([])
        self.assertEqual(result['activity_count'], 0)
        self.assertIsNone(result['min_total_float'])

    def test_counts_and_float_stats(self):
        activities = [
            make_activity('A1', total_float=10.0, pct_complete=100.0),
            make_activity('A2', total_float=-5.0, pct_complete=40.0),
            make_activity('A3', total_float=0.0, pct_complete=0.0, is_critical=True),
            make_activity('M1', is_milestone=True, dur=0.0, total_float=2.0),
        ]
        result = compute_upload_metadata(activities)

        self.assertEqual(result['activity_count'], 4)
        self.assertEqual(result['milestone_count'], 1)
        self.assertEqual(result['completed_count'], 1)
        self.assertEqual(result['negative_float_count'], 1)
        # A2 (negative float) and A3 (explicit) both count; M1 is excluded as a milestone.
        self.assertEqual(result['critical_count'], 2)
        self.assertEqual(result['min_total_float'], -5.0)
        self.assertEqual(result['max_total_float'], 10.0)

    def test_relationship_count_sums_predecessors(self):
        activities = [
            make_activity('A1'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = compute_upload_metadata(activities)
        self.assertEqual(result['relationship_count'], 1)

    def test_forecast_finish_is_max_baseline_finish(self):
        activities = [
            make_activity('A1', b_finish='2026-02-01'),
            make_activity('A2', b_finish='2026-05-15'),
        ]
        result = compute_upload_metadata(activities)
        self.assertEqual(result['forecast_finish'].isoformat(), '2026-05-15')

    def test_derive_version_label_prefers_data_date(self):
        from datetime import date
        label = derive_version_label(date(2026, 8, 5), None)
        self.assertEqual(label, '2026-08-05 Update')
