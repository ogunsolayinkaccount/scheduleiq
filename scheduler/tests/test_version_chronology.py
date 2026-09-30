"""
Effective Data Date determines schedule chronology; upload timestamp only
records when the file arrived. Same-Data-Date conflicts are explicit, never
silently resolved from upload order.
"""
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from scheduler import version_chronology as vc
from scheduler.ai import NullProvider
from scheduler.models import Project, ScheduleUpload
from .fixtures import make_activity

NOW = timezone.now()


def acts(prefix='A', n=12, tf=5.0):
    return [make_activity(f'{prefix}{i}', total_float=tf) for i in range(n)]


def mk(project, label, dd, hours_ago, activities=None, cls='CURRENT_UPDATE'):
    return ScheduleUpload.objects.create(
        project=project, original_filename=f'{label}.xer', sanitized_filename=f'{label}.xer', file_type='XER',
        version_label=label, schedule_classification=cls, data_date=dd, activities_json=activities if activities is not None else acts(),
        activity_count=len(activities) if activities is not None else 12, upload_timestamp=NOW - timedelta(hours=hours_ago),
    )


class OutOfOrderImportTests(TestCase):
    def setUp(self):
        self.p = Project.objects.create(name='Chronology')

    def roles(self):
        return {v['versionLabel']: v['role'] for v in self.client.get(f'/api/projects/{self.p.id}/versions/').json()['versions']}

    def test_older_data_date_imported_later_never_becomes_current(self):
        sep = mk(self.p, 'Sep-16', date(2026, 9, 16), hours_ago=100)         # imported FIRST
        jun = mk(self.p, 'Jun-10', date(2026, 6, 10), hours_ago=1)           # imported LATER
        roles = self.roles()
        self.assertEqual(roles['Sep-16'], 'CURRENT')
        self.assertEqual(roles['Jun-10'], 'PREVIOUS')
        # Every default-resolved endpoint agrees.
        resp = self.client.get(f'/api/projects/{self.p.id}/activity-analysis/').json()
        self.assertEqual(resp['currentVersionId'], str(sep.id))
        self.assertEqual(resp['previousVersionId'], str(jun.id))
        self.assertEqual(self.client.get(f'/api/projects/{self.p.id}/risk/').json()['versionId'], str(sep.id))

    def test_full_chain_imported_in_scrambled_order(self):
        mk(self.p, 'Aug-19', date(2026, 8, 19), hours_ago=50)
        mk(self.p, 'Sep-16', date(2026, 9, 16), hours_ago=90)
        mk(self.p, 'Jun-10', date(2026, 6, 10), hours_ago=5)
        mk(self.p, 'Aug-12', date(2026, 8, 12), hours_ago=1)
        roles = self.roles()
        self.assertEqual(roles['Sep-16'], 'CURRENT')
        self.assertEqual(roles['Aug-19'], 'PREVIOUS')
        self.assertEqual({roles['Aug-12'], roles['Jun-10']}, {'OTHER'})
        labels = [v['versionLabel'] for v in self.client.get(f'/api/projects/{self.p.id}/versions/').json()['versions']]
        self.assertEqual(labels, ['Sep-16', 'Aug-19', 'Aug-12', 'Jun-10'])      # newest Data Date first

    def test_upload_timestamp_is_only_a_tiebreak_never_the_chronology(self):
        a = ScheduleUpload(data_date=date(2026, 1, 1), upload_timestamp=NOW)
        b = ScheduleUpload(data_date=date(2026, 2, 1), upload_timestamp=NOW - timedelta(days=30))
        self.assertGreater(vc.sort_key(b), vc.sort_key(a))                       # later Data Date wins despite earlier upload

    def test_explicit_previous_and_current_query_params_still_override(self):
        sep = mk(self.p, 'Sep-16', date(2026, 9, 16), 100)
        jun = mk(self.p, 'Jun-10', date(2026, 6, 10), 1)
        resp = self.client.get(f'/api/projects/{self.p.id}/activity-analysis/?currentVersion={jun.id}&previousVersion={sep.id}').json()
        self.assertEqual(resp['currentVersionId'], str(jun.id))                  # explicit choice honoured
        self.assertEqual(resp['previousVersionId'], str(sep.id))

    def test_versions_without_a_data_date_keep_legacy_upload_ordering(self):
        old = mk(self.p, 'old', None, hours_ago=10)
        new = mk(self.p, 'new', None, hours_ago=1)
        self.assertEqual(self.roles()['new'], 'CURRENT')
        self.assertEqual(self.roles()['old'], 'PREVIOUS')
        self.assertEqual(vc.data_date_conflicts([old, new]), {})                 # never a "Data Date" conflict


class SameDataDateConflictTests(TestCase):
    def setUp(self):
        self.p = Project.objects.create(name='Conflict')
        self.jun = mk(self.p, 'Jun-10', date(2026, 6, 10), 300)
        self.aug19 = mk(self.p, 'Aug-19', date(2026, 8, 19), 200)
        self.aug26a = mk(self.p, 'Aug-26 A', date(2026, 8, 26), 150, acts(n=12))
        alt = acts(n=12)
        alt.append(make_activity('EXTRA', total_float=1.0))
        self.aug26b = mk(self.p, 'Aug-26 B', date(2026, 8, 26), 140, alt)
        self.sep = mk(self.p, 'Sep-16', date(2026, 9, 16), 100)

    def test_previous_is_unresolved_not_silently_the_next_older_update(self):
        ver = {v['versionLabel']: v for v in self.client.get(f'/api/projects/{self.p.id}/versions/').json()['versions']}
        self.assertEqual(ver['Sep-16']['role'], 'CURRENT')
        self.assertNotEqual(ver['Aug-19']['role'], 'PREVIOUS')                   # Aug-19 must NOT be presented as the previous update
        self.assertNotIn('PREVIOUS', {v['role'] for v in ver.values()})
        info = ver['Sep-16']['previousUnresolved']
        self.assertEqual(info['dataDate'], '2026-08-26')
        self.assertEqual({c['versionLabel'] for c in info['candidates']}, {'Aug-26 A', 'Aug-26 B'})
        self.assertIn('Previous version unresolved', info['reason'])
        self.assertTrue(ver['Aug-26 A']['dataDateConflict'] and ver['Aug-26 B']['dataDateConflict'])
        self.assertFalse(ver['Aug-19']['dataDateConflict'])

    def test_analysis_endpoints_report_the_unresolved_state(self):
        aa = self.client.get(f'/api/projects/{self.p.id}/activity-analysis/').json()
        self.assertIsNone(aa['previousVersionId'])
        self.assertIn('Previous version unresolved', aa['previousUnresolved']['reason'])
        ui = self.client.get(f'/api/projects/{self.p.id}/update-intelligence/').json()
        self.assertFalse(ui['available'])
        self.assertIn('Previous version unresolved', ui['reason'])
        rr = self.client.get(f'/api/projects/{self.p.id}/risk-register/').json()
        self.assertFalse(rr['available'])
        self.assertIn('Previous version unresolved', rr['reason'])

    def test_choosing_a_previous_explicitly_resolves_the_conflict_for_that_analysis(self):
        aa = self.client.get(
            f'/api/projects/{self.p.id}/activity-analysis/?currentVersion={self.sep.id}&previousVersion={self.aug26b.id}').json()
        self.assertEqual(aa['previousVersionId'], str(self.aug26b.id))

    def test_exact_copies_on_one_data_date_are_not_a_conflict(self):
        p = Project.objects.create(name='Copies')
        mk(p, 'Aug-26 first', date(2026, 8, 26), 50)
        mk(p, 'Aug-26 copy', date(2026, 8, 26), 40)              # identical content
        cur = mk(p, 'Sep-16', date(2026, 9, 16), 10)
        info = self.client.get(f'/api/projects/{p.id}/versions/').json()['versions']
        sep = next(v for v in info if v['versionLabel'] == 'Sep-16')
        self.assertIsNone(sep['previousUnresolved'])
        self.assertEqual(next(v for v in info if v['role'] == 'PREVIOUS')['dataDate'], '2026-08-26')
        self.assertFalse(any(v['dataDateConflict'] for v in info))

    def test_a_copy_of_current_is_never_treated_as_its_previous(self):
        p = Project.objects.create(name='CurrentCopy')
        mk(p, 'Aug-19', date(2026, 8, 19), 50)
        mk(p, 'Sep-16 first', date(2026, 9, 16), 40)
        mk(p, 'Sep-16 copy', date(2026, 9, 16), 10)
        prev = next(v for v in self.client.get(f'/api/projects/{p.id}/versions/').json()['versions'] if v['role'] == 'PREVIOUS')
        self.assertEqual(prev['dataDate'], '2026-08-19')

    def test_ai_chat_reports_previous_unresolved_and_does_not_compare_against_another_version(self):
        with patch('scheduler.views.get_provider') as gp:
            gp.return_value = type('P', (), {'is_configured': lambda self: False})()
            ctx = self.client.post(f'/api/projects/{self.p.id}/ai-chat/', data=json.dumps({'question': 'What changed?'}),
                                   content_type='application/json').json()['context']
        self.assertIsNone(ctx['previousVersion'])
        self.assertEqual(ctx['previousVersionUnresolved']['dataDate'], '2026-08-26')
        self.assertFalse(ctx['hasUpdateIntelligence'])


class ReportChronologyTests(TestCase):
    def test_report_previous_follows_data_date_not_upload_order(self):
        from scheduler import report_service
        p = Project.objects.create(name='ReportChron')
        sep = mk(p, 'Sep-16', date(2026, 9, 16), 100)
        aug = mk(p, 'Aug-19', date(2026, 8, 19), 90)
        mk(p, 'Jun-10', date(2026, 6, 10), 1)                    # imported last
        section = report_service.build_float_intelligence_section(p, sep)
        self.assertEqual(section['previousVersionId'], str(aug.id))
