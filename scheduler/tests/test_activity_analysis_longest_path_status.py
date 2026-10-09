"""
activity_analysis.py's longestPathStatus — the single master-row field the
Schedule Explorer (and every future consumer) reads for Longest Path
membership. Four states, never collapsed: YES / NO / UNAVAILABLE /
UNKNOWN_LEGACY. See parsers.py/column_mapping.py for where
onLongestPath/onLongestPathVerified are actually decided from the source
file; this only classifies what they already determined.
"""
from django.test import SimpleTestCase

from scheduler import activity_analysis
from .fixtures import make_activity


def _row(rows, activity_id):
    return next(r for r in rows if r['activityId'] == activity_id)


class LongestPathStatusTests(SimpleTestCase):
    def test_verified_yes(self):
        acts = [make_activity('A1', onLongestPath=True, onLongestPathVerified=True)]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['longestPathStatus'], 'YES')

    def test_verified_no(self):
        acts = [make_activity('A1', onLongestPath=False, onLongestPathVerified=True)]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['longestPathStatus'], 'NO')

    def test_explicitly_unverified_new_import_without_the_source_flag(self):
        # e.g. an Excel/CSV/MSP-XML/PDF import, or an XER whose TASK table
        # genuinely lacked the driving_path_flag column — the NEW parser
        # code explicitly wrote False/False, so this is a confident
        # "we checked, it's not there", not a guess.
        acts = [make_activity('A1', onLongestPath=False, onLongestPathVerified=False)]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['longestPathStatus'], 'UNAVAILABLE')

    def test_legacy_activity_with_no_tracking_key_at_all_is_unknown_not_no(self):
        # make_activity() never sets onLongestPathVerified unless told to —
        # exactly the shape of activities_json persisted before this
        # feature existed. Must NOT read as a confirmed "No".
        acts = [make_activity('A1', onLongestPath=False)]
        self.assertNotIn('onLongestPathVerified', acts[0])
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['longestPathStatus'], 'UNKNOWN_LEGACY')

    def test_legacy_activity_that_happened_to_store_true_is_still_unknown_not_yes(self):
        # Equally important the other direction: an old onLongestPath=True
        # with no verification key must not be upgraded to a confident YES.
        acts = [make_activity('A1', onLongestPath=True)]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['longestPathStatus'], 'UNKNOWN_LEGACY')

    def test_negative_float_alone_never_implies_longest_path_membership(self):
        # The review's central rule, asserted at the master-row level:
        # a critical (TF<=0) activity that P6 verified is NOT on the
        # longest path must show NO, never YES, regardless of float.
        acts = [make_activity('A1', total_float=-15.0, onLongestPath=False, onLongestPathVerified=True)]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertTrue(r['criticalActionable'])
        self.assertEqual(r['longestPathStatus'], 'NO')

    def test_wbs_hierarchy_fields_pass_through_unmodified(self):
        acts = [make_activity(
            'A1', wbs='Foundations', wbsPath='Area A > Foundations', wbsId='FOUND_A',
            wbsLevel=2, wbsSortKey='0000000001.0000000001', wbsIdPath=['AREA_A', 'FOUND_A'],
        )]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['wbsId'], 'FOUND_A')
        self.assertEqual(r['wbsLevel'], 2)
        self.assertEqual(r['wbsSortKey'], '0000000001.0000000001')
        self.assertEqual(r['wbsIdPath'], ['AREA_A', 'FOUND_A'])

    def test_missing_wbs_hierarchy_fields_default_safely(self):
        acts = [make_activity('A1')]
        r = _row(activity_analysis.build_activity_analysis(acts)['rows'], 'A1')
        self.assertEqual(r['wbsId'], '')
        self.assertIsNone(r['wbsLevel'])
        self.assertEqual(r['wbsSortKey'], '')
        self.assertEqual(r['wbsIdPath'], [])
