import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .fixtures import make_activity


class UploadApiTests(TestCase):
    def test_no_file_returns_400(self):
        resp = self.client.post('/api/upload/')
        self.assertEqual(resp.status_code, 400)

    def test_unsupported_extension_returns_415(self):
        upload = SimpleUploadedFile('data.bin', b'\x00\x01')
        resp = self.client.post('/api/upload/', data={'file': upload})
        self.assertEqual(resp.status_code, 415)

    def test_mpp_gives_clear_export_message(self):
        upload = SimpleUploadedFile('schedule.mpp', b'\x00\x01')
        resp = self.client.post('/api/upload/', data={'file': upload})
        self.assertEqual(resp.status_code, 415)
        self.assertIn('XML', resp.json()['error'])

    def test_valid_csv_returns_activities(self):
        csv_content = (
            b'Activity ID,Activity Name,Start,Finish,Original Duration,Total Float,% Complete\n'
            b'A1000,Mobilize,2026-01-05,2026-01-10,5,10,100\n'
        )
        upload = SimpleUploadedFile('sched.csv', csv_content)
        resp = self.client.post('/api/upload/', data={'file': upload})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['actCount'], 1)
        self.assertEqual(body['activities'][0]['code'], 'A1000')


class MetricsApiTests(TestCase):
    def test_non_list_activities_returns_400(self):
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': 'not-a-list'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_empty_activities_list_returns_zeroed_metrics(self):
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': []}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 0)

    def test_valid_activities_return_correct_total(self):
        acts = [make_activity('A1'), make_activity('A2')]
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': acts}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 2)

    def test_invalid_json_returns_400(self):
        resp = self.client.post('/api/metrics/', data='not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_empty_activities_authoritative_evm_present_and_sane(self):
        # Regression guard: the new authoritative-engine call must not crash
        # on the same empty-list path compute_metrics() early-returns on.
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': []}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('authoritativeEvm', body)
        self.assertIsNone(body['authoritativeEvm']['cost']['bac'])

    def test_authoritative_evm_never_fabricates_cpi_when_not_cost_loaded(self):
        # The legacy `evm.CPI` field defaults to 1.0 here (duration-only
        # fallback) — the authoritative field must report None instead.
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0)]
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': acts}), content_type='application/json')
        body = resp.json()
        self.assertEqual(body['evm']['CPI'], 1.0)   # legacy fabrication, unchanged (not this phase's scope)
        self.assertIsNone(body['authoritativeEvm']['cost']['cpi'])   # authoritative: honest

    def test_authoritative_evm_reflects_real_cost_loaded_performance(self):
        acts = [make_activity(
            'A1', dur=10.0, pct_complete=50.0, b_start='2026-01-01', b_finish='2026-01-11',
            isCostLoaded=True, budgetedCost=1000.0, actualCost=800.0, remainingCost=200.0,
        )]
        resp = self.client.post(
            '/api/metrics/', data=json.dumps({'activities': acts, 'dataDate': '2026-01-06'}),
            content_type='application/json',
        )
        body = resp.json()
        self.assertAlmostEqual(body['authoritativeEvm']['cost']['ev'], 500.0)
        self.assertAlmostEqual(body['authoritativeEvm']['cost']['cpi'], 500.0 / 800.0, places=3)

    def test_authoritative_evm_by_project_breaks_down_per_project(self):
        acts = [
            make_activity('A1', projectId='P1', dur=10.0, pct_complete=50.0,
                           isCostLoaded=True, budgetedCost=1000.0, actualCost=500.0),
            make_activity('B1', projectId='P2', dur=10.0, pct_complete=50.0,
                           isCostLoaded=True, budgetedCost=2000.0, actualCost=2000.0),
        ]
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': acts}), content_type='application/json')
        body = resp.json()
        by_project = body['authoritativeEvmByProject']
        self.assertEqual(set(by_project.keys()), {'P1', 'P2'})
        self.assertAlmostEqual(by_project['P1']['cost']['bac'], 1000.0)
        self.assertAlmostEqual(by_project['P2']['cost']['bac'], 2000.0)

    def test_authoritative_productivity_present(self):
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0, isResourceLoaded=True, budgetedHours=100.0, actualHours=40.0, remainingHours=60.0)]
        resp = self.client.post('/api/metrics/', data=json.dumps({'activities': acts}), content_type='application/json')
        body = resp.json()
        self.assertTrue(body['authoritativeProductivity']['overall']['available'])
        self.assertAlmostEqual(body['authoritativeProductivity']['overall']['budgetedHours'], 100.0)


class QualityApiTests(TestCase):
    def test_empty_activities_returns_400(self):
        resp = self.client.post('/api/quality/', data=json.dumps({'activities': []}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_valid_activities_return_overall_score(self):
        acts = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        resp = self.client.post('/api/quality/', data=json.dumps({'activities': acts}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('overall_score', resp.json())

    def test_circular_logic_detected_through_api(self):
        # Regression guard for the actId key-mismatch bug fixed in quality_engine.py.
        acts = [
            make_activity('A1', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        resp = self.client.post('/api/quality/', data=json.dumps({'activities': acts}), content_type='application/json')
        self.assertEqual(resp.json()['circular_logic_count'], 2)


class AnalyzeApiTests(TestCase):
    def test_missing_current_activities_returns_400(self):
        resp = self.client.post('/api/analyze/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_empty_current_activities_returns_400(self):
        resp = self.client.post('/api/analyze/', data=json.dumps({'current_activities': []}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_valid_minimal_request_returns_status(self):
        acts = [make_activity('A1')]
        resp = self.client.post(
            '/api/analyze/',
            data=json.dumps({'current_activities': acts, 'data_date': '2026-06-01'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp.json()['status'], ('ON_TRACK', 'AT_RISK', 'OFF_TRACK', 'UNDETERMINED'))


class NarrativeApiTests(TestCase):
    def test_missing_current_activities_returns_400(self):
        resp = self.client.post('/api/narrative/', data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_valid_request_returns_executive_summary(self):
        acts = [make_activity('A1')]
        resp = self.client.post(
            '/api/narrative/',
            data=json.dumps({'current_activities': acts, 'data_date': '2026-06-01'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('executive_summary', resp.json())
