import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from scheduler.ai import NullProvider, OpenAIProvider, get_provider
from scheduler.ai.providers.base import AIResponse
from scheduler.models import Project, ScheduleUpload
from .fixtures import make_activity


class NullProviderTests(SimpleTestCase):
    def test_not_configured(self):
        p = NullProvider()
        self.assertFalse(p.is_configured())

    def test_complete_returns_clear_error_not_exception(self):
        p = NullProvider()
        result = p.complete('system', 'user')
        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'AI analysis is not configured.')


class OpenAIProviderTests(SimpleTestCase):
    def test_not_configured_without_api_key(self):
        p = OpenAIProvider(api_key='')
        self.assertFalse(p.is_configured())
        result = p.complete('s', 'u')
        self.assertFalse(result.ok)

    def test_configured_with_api_key(self):
        p = OpenAIProvider(api_key='sk-test')
        self.assertTrue(p.is_configured())

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_successful_completion_parses_message_content(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"answer": "ok"}'}}]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        p = OpenAIProvider(api_key='sk-test')
        result = p.complete('system', 'user')
        self.assertTrue(result.ok)
        self.assertEqual(result.text, '{"answer": "ok"}')

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_malformed_response_shape_handled(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({'unexpected': 'shape'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        p = OpenAIProvider(api_key='sk-test')
        result = p.complete('system', 'user')
        self.assertFalse(result.ok)
        self.assertIn('unexpected response shape', result.error)

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_network_error_handled_gracefully(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError('connection refused')

        p = OpenAIProvider(api_key='sk-test')
        result = p.complete('system', 'user')
        self.assertFalse(result.ok)
        self.assertIn('Could not reach AI provider', result.error)

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_invalid_api_key_returns_401_as_provider_error_not_crash(self, mock_urlopen):
        import io
        import urllib.error
        body = json.dumps({'error': {'message': 'Incorrect API key provided.'}}).encode('utf-8')
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url='https://api.openai.com/v1/chat/completions', code=401, msg='Unauthorized',
            hdrs=None, fp=io.BytesIO(body),
        )
        p = OpenAIProvider(api_key='sk-invalid')
        result = p.complete('system', 'user')
        self.assertFalse(result.ok)
        self.assertIn('AI provider error (401)', result.error)
        self.assertIn('Incorrect API key', result.error)

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_error_types_distinguish_auth_billing_and_timeout(self, mock_urlopen):
        import io
        import socket
        import urllib.error

        def http_err(code, body):
            return urllib.error.HTTPError('u', code, 'm', None, io.BytesIO(json.dumps(body).encode()))

        p = OpenAIProvider(api_key='sk-test')
        mock_urlopen.side_effect = http_err(401, {'error': {'message': 'Incorrect API key sk-proj-abcDEF123'}})
        r = p.complete('s', 'u')
        self.assertEqual(r.error_type, 'authentication')
        self.assertNotIn('abcDEF123', r.error)  # key fragments never echoed

        mock_urlopen.side_effect = http_err(429, {'error': {'type': 'insufficient_quota', 'code': 'credit_balance_exhausted'}})
        self.assertEqual(p.complete('s', 'u').error_type, 'billing_quota')

        mock_urlopen.side_effect = http_err(429, {'error': {'type': 'rate_limit_exceeded'}})
        self.assertEqual(p.complete('s', 'u').error_type, 'rate_limit')

        mock_urlopen.side_effect = urllib.error.URLError(socket.timeout('timed out'))
        self.assertEqual(p.complete('s', 'u').error_type, 'timeout')

        mock_urlopen.side_effect = urllib.error.URLError('connection refused')
        self.assertEqual(p.complete('s', 'u').error_type, 'network')

    @patch('scheduler.ai.providers.openai_provider.urllib.request.urlopen')
    def test_provider_timeout_handled_gracefully(self, mock_urlopen):
        import socket
        mock_urlopen.side_effect = socket.timeout('timed out')

        p = OpenAIProvider(api_key='sk-test')
        result = p.complete('system', 'user')
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)


class GetProviderTests(SimpleTestCase):
    @patch.dict('os.environ', {}, clear=True)
    def test_defaults_to_null_provider(self):
        self.assertIsInstance(get_provider(), NullProvider)

    @patch.dict('os.environ', {'AI_PROVIDER': 'openai', 'OPENAI_API_KEY': 'sk-test'})
    def test_openai_selected_when_configured(self):
        self.assertIsInstance(get_provider(), OpenAIProvider)

    @patch.dict('os.environ', {'AI_PROVIDER': 'openai'}, clear=True)
    def test_openai_without_key_falls_back_to_null(self):
        self.assertIsInstance(get_provider(), NullProvider)


class _MockProvider:
    """Simple stand-in used to drive the AI endpoints without any network
    call — mirrors the real provider's is_configured()/complete() shape."""

    def __init__(self, configured=True, response_text=None, error=None):
        self._configured = configured
        self._response_text = response_text
        self._error = error

    def is_configured(self):
        return self._configured

    def complete(self, system_prompt, user_prompt):
        if self._error:
            return AIResponse(ok=False, error=self._error)
        return AIResponse(ok=True, text=self._response_text)


class AiReviewApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='AI Review Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            activities_json=[make_activity('A1', total_float=-5.0, is_critical=True)],
        )

    def test_not_configured_still_returns_context(self):
        with patch('scheduler.views.get_provider', return_value=_MockProvider(configured=False)):
            resp = self.client.get(f'/api/projects/{self.project.id}/ai-review/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body['configured'])
        self.assertIn('context', body)
        self.assertIn('riskOverall', body['context'])

    def test_successful_structured_response(self):
        review_json = json.dumps({
            'executiveSummary': 'Schedule is at risk due to negative float.',
            'overallCondition': 'At Risk', 'criticalPath': '', 'progress': '',
            'majorVariance': '', 'milestones': '', 'engineeringRisks': '',
            'procurementRisks': '', 'constructionRisks': '',
            'topAreasOfConcern': [], 'positiveTrends': [], 'recoveryOpportunities': [],
            'questionsForProjectTeam': [], 'recommendedMeetingDiscussionPoints': [],
        })
        with patch('scheduler.views.get_provider', return_value=_MockProvider(response_text=review_json)):
            resp = self.client.get(f'/api/projects/{self.project.id}/ai-review/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['configured'])
        self.assertEqual(body['review']['overallCondition'], 'At Risk')

    def test_provider_error_returns_502(self):
        with patch('scheduler.views.get_provider', return_value=_MockProvider(error='rate limited')):
            resp = self.client.get(f'/api/projects/{self.project.id}/ai-review/')
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()['error'], 'rate limited')

    def test_malformed_json_from_provider_returns_502(self):
        with patch('scheduler.views.get_provider', return_value=_MockProvider(response_text='not json')):
            resp = self.client.get(f'/api/projects/{self.project.id}/ai-review/')
        self.assertEqual(resp.status_code, 502)
        self.assertIn('malformed', resp.json()['error'])


class AiChatApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='AI Chat Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            activities_json=[make_activity('A1', total_float=-5.0, area='Area C')],
        )

    def test_question_required(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/ai-chat/', data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_not_configured_returns_context_and_focus(self):
        with patch('scheduler.views.get_provider', return_value=_MockProvider(configured=False)):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/',
                data=json.dumps({'question': 'Why is Area C late?'}), content_type='application/json',
            )
        body = resp.json()
        self.assertFalse(body['configured'])
        self.assertEqual(body['focus'], {'area': 'Area C'})

    def test_successful_chat_response_with_references(self):
        chat_json = json.dumps({
            'answer': 'Area C has 1 negative-float activity.',
            'references': [{'activityId': 'A1', 'note': 'negative float'}],
            'confidence': 'high',
        })
        with patch('scheduler.views.get_provider', return_value=_MockProvider(response_text=chat_json)):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/',
                data=json.dumps({'question': 'Why is Area C late?'}), content_type='application/json',
            )
        body = resp.json()
        self.assertTrue(body['configured'])
        self.assertEqual(body['references'][0]['activityId'], 'A1')
        self.assertEqual(body['confidence'], 'high')


class AiChatBaselineResolutionTests(TestCase):
    """Chat uses ONLY the designated baseline — never guesses one."""

    def _project(self, with_baseline):
        from datetime import date, timedelta
        from django.utils import timezone
        p = Project.objects.create(name='Baseline Resolution')
        now = timezone.now()
        if with_baseline:
            ScheduleUpload.objects.create(
                project=p, original_filename='b.xer', file_type='XER', schedule_classification='APPROVED_BASELINE',
                data_date=date(2026, 1, 1), activities_json=[make_activity('A1')], upload_timestamp=now - timedelta(days=5))
        ScheduleUpload.objects.create(
            project=p, original_filename='c.xer', file_type='XER', schedule_classification='CURRENT_UPDATE',
            data_date=date(2026, 6, 1), activities_json=[make_activity('A1')], upload_timestamp=now)
        return p

    def _ctx(self, p):
        with patch('scheduler.views.get_provider', return_value=_MockProvider(configured=False)):
            resp = self.client.post(f'/api/projects/{p.id}/ai-chat/', data=json.dumps({'question': 'x'}), content_type='application/json')
        return resp.json()['context']

    def test_designated_baseline_is_used(self):
        self.assertTrue(self._ctx(self._project(True))['hasBaseline'])

    def test_no_designated_baseline_means_none_not_a_guess(self):
        ctx = self._ctx(self._project(False))
        self.assertFalse(ctx['hasBaseline'])
        self.assertIsNone(ctx['lookAhead']['statusCounts'])
