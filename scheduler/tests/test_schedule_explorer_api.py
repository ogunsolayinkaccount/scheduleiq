"""
Unified Critical Path & Longest Path Schedule Explorer — API layer tests.
Covers Critical/Longest/Both filtering, hierarchical WBS + descendants
filtering by real id (never name/path string matching), the Longest Path
coverage/provenance summary, project isolation, permissions, and the
Excel export's exact match with the displayed rows.
"""
import io
import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from openpyxl import load_workbook

from scheduler.models import Project, ROLE_ADMINISTRATOR, ROLE_SCHEDULER, ROLE_VIEWER, ScheduleUpload


def _user(username, role, password='pw12345!'):
    u = User.objects.create_user(username=username, password=password)
    u.profile.role = role
    u.profile.save(update_fields=['role'])
    return u


def _act(code, **extra):
    d = {
        'id': code, 'code': code, 'name': f'Activity {code}', 'wbs': 'General', 'wbsPath': 'General',
        'wbsId': '', 'wbsLevel': 0, 'wbsSortKey': '', 'wbsIdPath': [],
        'area': 'Area A', 'bStart': '2026-01-01', 'bFinish': '2026-01-20',
        'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0, 'totalFloat': 0.0,
        'isCritical': False, 'isMilestone': False, 'status': 'TK_NotStart',
        'onLongestPath': False, 'onLongestPathVerified': True,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class ScheduleExplorerFilterTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Explorer Filter Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                # A1: critical only (negative float, P6 verified NOT on longest path)
                _act('A1', totalFloat=-5.0, isCritical=True, onLongestPath=False, onLongestPathVerified=True,
                     wbs='Foundations', wbsPath='Area A > Foundations', wbsId='FOUND_A', wbsLevel=2,
                     wbsIdPath=['AREA_A', 'FOUND_A'], area='Area A'),
                # A2: longest path only (healthy float but P6-verified driving)
                _act('A2', totalFloat=15.0, isCritical=False, onLongestPath=True, onLongestPathVerified=True,
                     wbs='Foundations', wbsPath='Area B > Foundations', wbsId='FOUND_B', wbsLevel=2,
                     wbsIdPath=['AREA_B', 'FOUND_B'], area='Area B'),
                # A3: both critical AND longest path
                _act('A3', totalFloat=0.0, isCritical=True, onLongestPath=True, onLongestPathVerified=True,
                     wbs='Foundations', wbsPath='Area A > Foundations', wbsId='FOUND_A', wbsLevel=2,
                     wbsIdPath=['AREA_A', 'FOUND_A'], area='Area A'),
                # A4: neither
                _act('A4', totalFloat=25.0, isCritical=False, onLongestPath=False, onLongestPathVerified=True,
                     wbs='Sitework', wbsPath='Area A > Sitework', wbsId='SITE_A', wbsLevel=2,
                     wbsIdPath=['AREA_A', 'SITE_A'], area='Area A', status='TK_Active'),
                # A5: longest path unavailable (non-XER-style import, explicitly unverified)
                _act('A5', totalFloat=-2.0, isCritical=True, onLongestPath=False, onLongestPathVerified=False,
                     wbs='Sitework', wbsPath='Area A > Sitework', wbsId='SITE_A', wbsLevel=2,
                     wbsIdPath=['AREA_A', 'SITE_A'], area='Area A'),
                # A6: legacy — no onLongestPathVerified key at all
                _act('A6', totalFloat=0.0, isCritical=True, onLongestPath=True),
            ],
        )
        del self.version.activities_json[-1]['onLongestPathVerified']
        self.version.save(update_fields=['activities_json'])

    def _get(self, **params):
        resp = self.client.get(f'/api/projects/{self.project.id}/schedule-explorer/activities/', params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_critical_only_filter(self):
        body = self._get(pathClassification='critical')
        ids = {r['activityId'] for r in body['rows']}
        self.assertEqual(ids, {'A1', 'A3', 'A5', 'A6'})

    def test_longest_path_only_filter_never_includes_a_merely_negative_float_activity(self):
        body = self._get(pathClassification='longestPath')
        ids = {r['activityId'] for r in body['rows']}
        # A1 has negative float but P6 explicitly said NOT on the longest
        # path — must be excluded. A6's onLongestPath=True is UNKNOWN_LEGACY
        # (no verification key) so it must also be excluded from a
        # confident "Longest Path only" filter.
        self.assertEqual(ids, {'A2', 'A3'})

    def test_both_filter_is_deduplicated_union(self):
        body = self._get(pathClassification='both')
        ids = {r['activityId'] for r in body['rows']}
        self.assertEqual(ids, {'A1', 'A2', 'A3', 'A5', 'A6'})
        self.assertEqual(len(body['rows']), len(ids))  # no duplicate A3

    def test_counts_denominator_does_not_change_when_toggling_path_classification(self):
        unfiltered_counts = self._get()['counts']
        critical_counts = self._get(pathClassification='critical')['counts']
        longest_counts = self._get(pathClassification='longestPath')['counts']
        self.assertEqual(unfiltered_counts, critical_counts)
        self.assertEqual(unfiltered_counts, longest_counts)
        self.assertEqual(unfiltered_counts['critical'], 4)
        self.assertEqual(unfiltered_counts['longestPath'], 2)
        self.assertEqual(unfiltered_counts['both'], 5)
        self.assertEqual(unfiltered_counts['total'], 6)

    def test_longest_path_coverage_distinguishes_all_four_states(self):
        coverage = self._get()['longestPathCoverage']
        self.assertEqual(coverage, {'yes': 2, 'no': 2, 'unavailable': 1, 'unknownLegacy': 1, 'total': 6})

    def test_longest_path_coverage_is_unaffected_by_filters(self):
        coverage_filtered = self._get(area='Area B')['longestPathCoverage']
        coverage_unfiltered = self._get()['longestPathCoverage']
        self.assertEqual(coverage_filtered, coverage_unfiltered)

    def test_wbs_hierarchy_support_reports_partial_coverage_for_this_fixture(self):
        # A1-A5 carry a real wbsIdPath; A6 (the legacy-shaped row) does not.
        support = self._get()['wbsHierarchySupport']
        self.assertEqual(support, {'hasHierarchy': True, 'rowsWithHierarchy': 5, 'total': 6})

    def test_wbs_filter_with_descendants_uses_real_ids_not_names(self):
        # Both "FOUND_A" (under Area A) and "FOUND_B" (under Area B) are
        # named "Foundations" — filtering by Area A's Foundations id must
        # never pick up Area B's same-named branch.
        body = self._get(wbsId='FOUND_A', includeDescendants='true')
        ids = {r['activityId'] for r in body['rows']}
        self.assertEqual(ids, {'A1', 'A3'})

    def test_wbs_filter_with_descendants_still_matches_a_legacy_row_with_no_id_path(self):
        # A6 has a real wbsId but an empty wbsIdPath (exactly how a
        # schedule imported before this feature existed looks — see the
        # live Barn verification in the implementation report). Filtering
        # by that EXACT wbsId with includeDescendants=true must still find
        # it via the fallback exact-match, not silently return nothing.
        self.version.activities_json[-1]['wbsId'] = 'LEGACY_WBS'
        self.version.save(update_fields=['activities_json'])
        body = self._get(wbsId='LEGACY_WBS', includeDescendants='true')
        self.assertEqual([r['activityId'] for r in body['rows']], ['A6'])

    def test_wbs_filter_without_descendants_is_exact_node_match(self):
        body = self._get(wbsId='AREA_A', includeDescendants='false')
        self.assertEqual(body['rows'], [])  # no activity's OWN wbsId is the Area A node itself

    def test_wbs_filter_on_parent_includes_all_descendant_branches(self):
        # A6 deliberately has no wbsIdPath at all (it's the legacy-shaped
        # fixture) and so correctly falls outside any WBS filter.
        body = self._get(wbsId='AREA_A', includeDescendants='true')
        ids = {r['activityId'] for r in body['rows']}
        self.assertEqual(ids, {'A1', 'A3', 'A4', 'A5'})

    def test_area_filter(self):
        body = self._get(area='Area B')
        self.assertEqual([r['activityId'] for r in body['rows']], ['A2'])

    def test_status_filter(self):
        body = self._get(status='TK_Active')
        self.assertEqual([r['activityId'] for r in body['rows']], ['A4'])

    def test_search_filter_matches_activity_id_or_name(self):
        body = self._get(search='a2')
        self.assertEqual([r['activityId'] for r in body['rows']], ['A2'])

    def test_filters_are_additive(self):
        body = self._get(area='Area A', pathClassification='critical')
        ids = {r['activityId'] for r in body['rows']}
        self.assertEqual(ids, {'A1', 'A3', 'A5', 'A6'})

    def test_no_schedule_version_returns_404(self):
        empty_project = Project.objects.create(name='No Version')
        resp = self.client.get(f'/api/projects/{empty_project.id}/schedule-explorer/activities/')
        self.assertEqual(resp.status_code, 404)


class ScheduleExplorerProjectIsolationTests(TestCase):
    def test_same_wbs_id_string_in_different_projects_never_leaks(self):
        project_a = Project.objects.create(name='Project A')
        project_b = Project.objects.create(name='Project B')
        ScheduleUpload.objects.create(
            project=project_a, original_filename='a.xer', sanitized_filename='a.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('PA1', wbsId='SHARED', wbsIdPath=['SHARED'])],
        )
        ScheduleUpload.objects.create(
            project=project_b, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('PB1', wbsId='SHARED', wbsIdPath=['SHARED'])],
        )
        resp_a = self.client.get(f'/api/projects/{project_a.id}/schedule-explorer/activities/', {'wbsId': 'SHARED'})
        self.assertEqual([r['activityId'] for r in resp_a.json()['rows']], ['PA1'])
        resp_b = self.client.get(f'/api/projects/{project_b.id}/schedule-explorer/activities/', {'wbsId': 'SHARED'})
        self.assertEqual([r['activityId'] for r in resp_b.json()['rows']], ['PB1'])


class ScheduleExplorerExportTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Explorer Export Test')
        ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[
                _act('A1', totalFloat=-5.0, isCritical=True, onLongestPath=True, onLongestPathVerified=True),
                _act('A2', totalFloat=10.0, isCritical=False, onLongestPath=False, onLongestPathVerified=True),
            ],
        )

    def test_export_matches_the_same_filtered_rows_the_json_endpoint_returns(self):
        json_body = self.client.get(
            f'/api/projects/{self.project.id}/schedule-explorer/activities/', {'pathClassification': 'critical'},
        ).json()
        self.assertEqual(len(json_body['rows']), 1)

        export_resp = self.client.get(
            f'/api/projects/{self.project.id}/schedule-explorer/export/', {'pathClassification': 'critical'},
        )
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        wb = load_workbook(io.BytesIO(export_resp.content))
        ws = wb.active
        data_rows = list(ws.iter_rows(values_only=True))
        header_idx = next(i for i, row in enumerate(data_rows) if row and row[0] == 'Activity ID')
        exported_ids = [row[0] for row in data_rows[header_idx + 1:] if row[0]]
        self.assertEqual(exported_ids, [r['activityId'] for r in json_body['rows']])

    def test_export_never_claims_yes_for_an_unverified_longest_path_row(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/schedule-explorer/export/')
        wb = load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        data_rows = list(ws.iter_rows(values_only=True))
        header_idx = next(i for i, row in enumerate(data_rows) if row and row[0] == 'Activity ID')
        header = data_rows[header_idx]
        lp_col = header.index('Longest Path')
        values = {row[0]: row[lp_col] for row in data_rows[header_idx + 1:] if row[0]}
        self.assertEqual(values['A1'], 'YES')
        self.assertEqual(values['A2'], 'NO')


@override_settings(AUTO_AUTH_TEST_USER=False)
class ScheduleExplorerPermissionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Explorer Permission Test')
        ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )
        self.viewer = _user('explorer-viewer', ROLE_VIEWER, password='pw-1')

    def test_unauthenticated_refused(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/schedule-explorer/activities/')
        self.assertEqual(resp.status_code, 401)

    def test_viewer_can_read(self):
        self.client.login(username='explorer-viewer', password='pw-1')
        resp = self.client.get(f'/api/projects/{self.project.id}/schedule-explorer/activities/')
        self.assertEqual(resp.status_code, 200)
        export_resp = self.client.get(f'/api/projects/{self.project.id}/schedule-explorer/export/')
        self.assertEqual(export_resp.status_code, 200)


class ScheduleExplorerCombinedFilterCoverageTests(TestCase):
    """The review's correction: Critical Path and Both must stay USABLE —
    and Both must still show every known critical activity — regardless
    of whether Longest Path coverage for this version is full, partial,
    or entirely unavailable. Only the disabling of the Longest Path pill
    itself is a frontend concern (scheduleExplorerFormat.ts); this pins
    the API-level guarantee that "Both" never drops a critical activity
    just because its Longest Path status isn't verified."""

    def _project_with(self, activities):
        project = Project.objects.create(name='Coverage Scenario Test')
        ScheduleUpload.objects.create(
            project=project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15', activities_json=activities,
        )
        return project

    def test_full_coverage_both_matches_critical_plus_verified_longest_path(self):
        project = self._project_with([
            _act('A1', isCritical=True, onLongestPath=True, onLongestPathVerified=True),
            _act('A2', isCritical=False, onLongestPath=True, onLongestPathVerified=True),
            _act('A3', isCritical=True, onLongestPath=False, onLongestPathVerified=True),
            _act('A4', isCritical=False, onLongestPath=False, onLongestPathVerified=True),
        ])
        body = self.client.get(f'/api/projects/{project.id}/schedule-explorer/activities/', {'pathClassification': 'both'}).json()
        self.assertEqual(body['longestPathCoverage'], {'yes': 2, 'no': 2, 'unavailable': 0, 'unknownLegacy': 0, 'total': 4})
        self.assertEqual({r['activityId'] for r in body['rows']}, {'A1', 'A2', 'A3'})

    def test_partial_coverage_both_still_includes_every_critical_activity(self):
        project = self._project_with([
            _act('A1', isCritical=True, onLongestPath=True, onLongestPathVerified=True),   # verified longest path
            _act('A2', isCritical=True, onLongestPath=False, onLongestPathVerified=False),  # critical, unverified
            _act('A3', isCritical=False, onLongestPath=False, onLongestPathVerified=False),  # neither
        ])
        body = self.client.get(f'/api/projects/{project.id}/schedule-explorer/activities/', {'pathClassification': 'both'}).json()
        self.assertEqual(body['longestPathCoverage'], {'yes': 1, 'no': 0, 'unavailable': 2, 'unknownLegacy': 0, 'total': 3})
        # A2 is critical but its Longest Path status is UNAVAILABLE — it
        # must still appear in "Both", since "Both" is a union with
        # Critical, never gated on Longest Path verification.
        self.assertEqual({r['activityId'] for r in body['rows']}, {'A1', 'A2'})

    def test_zero_coverage_both_shows_known_critical_activities_only(self):
        project = self._project_with([
            _act('A1', isCritical=True, onLongestPath=False, onLongestPathVerified=False),
            _act('A2', isCritical=False, onLongestPath=False, onLongestPathVerified=False),
        ])
        body = self.client.get(f'/api/projects/{project.id}/schedule-explorer/activities/', {'pathClassification': 'both'}).json()
        self.assertEqual(body['longestPathCoverage'], {'yes': 0, 'no': 0, 'unavailable': 2, 'unknownLegacy': 0, 'total': 2})
        self.assertEqual({r['activityId'] for r in body['rows']}, {'A1'})

    def test_wbs_hierarchy_support_is_false_when_no_activity_carries_a_real_id_path(self):
        # Exactly the shape of Project Barn's current real stored version
        # (verified live): every activity has a wbsId but wbsIdPath is
        # empty everywhere, because it predates this field's existence.
        project = self._project_with([
            _act('A1', wbsId='232231', wbsIdPath=[]),
            _act('A2', wbsId='232232', wbsIdPath=[]),
        ])
        support = self.client.get(f'/api/projects/{project.id}/schedule-explorer/activities/').json()['wbsHierarchySupport']
        self.assertEqual(support, {'hasHierarchy': False, 'rowsWithHierarchy': 0, 'total': 2})

    def test_wbs_hierarchy_support_is_true_when_a_fresh_xer_import_has_real_id_paths(self):
        project = self._project_with([
            _act('A1', wbsId='FOUND_A', wbsIdPath=['AREA_A', 'FOUND_A']),
            _act('A2', wbsId='SITE_A', wbsIdPath=['AREA_A', 'SITE_A']),
        ])
        support = self.client.get(f'/api/projects/{project.id}/schedule-explorer/activities/').json()['wbsHierarchySupport']
        self.assertEqual(support, {'hasHierarchy': True, 'rowsWithHierarchy': 2, 'total': 2})

    def test_critical_filter_is_identical_regardless_of_longest_path_coverage(self):
        # Critical Path must never be affected by Longest Path data quality
        # at all — same critical set whether coverage is full or absent.
        full = self._project_with([_act('A1', isCritical=True, onLongestPathVerified=True)])
        none = self._project_with([_act('A1', isCritical=True, onLongestPathVerified=False)])
        full_rows = self.client.get(f'/api/projects/{full.id}/schedule-explorer/activities/', {'pathClassification': 'critical'}).json()['rows']
        none_rows = self.client.get(f'/api/projects/{none.id}/schedule-explorer/activities/', {'pathClassification': 'critical'}).json()['rows']
        self.assertEqual([r['activityId'] for r in full_rows], [r['activityId'] for r in none_rows])
