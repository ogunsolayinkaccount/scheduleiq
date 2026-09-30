"""
OpenAI-compatible chat-completions provider, implemented with the standard
library only (urllib) — no `openai`/`requests` dependency needed for a
single JSON POST. Works against OpenAI directly, or any OpenAI-compatible
gateway via OPENAI_BASE_URL.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

from .base import AIProvider, AIResponse


def _sanitize(text: str) -> str:
    """Never let a key fragment echoed back by the provider reach a response/log."""
    return re.sub(r'sk-[A-Za-z0-9_\-\*\.]+', 'sk-***', text or '')


def _classify_http_error(code: int, detail: str) -> str:
    d = (detail or '').lower()
    if code in (401, 403):
        return 'authentication'
    if 'insufficient_quota' in d or 'credit_balance' in d or 'billing' in d:
        return 'billing_quota'
    if code == 429:
        return 'rate_limit'
    if code == 404 or 'model' in d and ('not found' in d or 'does not exist' in d or 'access' in d):
        return 'model'
    return 'provider'


class OpenAIProvider(AIProvider):
    name = 'openai'

    def __init__(self, api_key: str, model: str = 'gpt-4o-mini', base_url: str = 'https://api.openai.com/v1'):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip('/')

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def complete(self, system_prompt: str, user_prompt: str) -> AIResponse:
        if not self.is_configured():
            return AIResponse(ok=False, error='AI analysis is not configured.', error_type='not_configured')

        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
        }
        req = urllib.request.Request(
            f'{self.base_url}/chat/completions',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_key}'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = _sanitize(exc.read().decode('utf-8', errors='ignore')[:500])
            return AIResponse(ok=False, error=f'AI provider error ({exc.code}): {detail}',
                              error_type=_classify_http_error(exc.code, detail))
        except urllib.error.URLError as exc:
            kind = 'timeout' if isinstance(exc.reason, (socket.timeout, TimeoutError)) else 'network'
            return AIResponse(ok=False, error=f'Could not reach AI provider: {exc.reason}', error_type=kind)
        except (socket.timeout, TimeoutError) as exc:
            return AIResponse(ok=False, error=f'AI provider request timed out: {exc}', error_type='timeout')
        except Exception as exc:
            return AIResponse(ok=False, error=f'AI provider request failed: {_sanitize(str(exc))}', error_type='provider')

        try:
            text = body['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return AIResponse(ok=False, error='AI provider returned an unexpected response shape.', raw=body, error_type='bad_response')

        return AIResponse(ok=True, text=text, raw=body)
