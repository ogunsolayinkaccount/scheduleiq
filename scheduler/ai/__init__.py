"""
AI provider abstraction — ScheduleIQ.

Provider selection is entirely environment-driven (never hardcoded, never a
secret in source):
  AI_PROVIDER      'openai' to enable; anything else (including unset) uses
                    the NullProvider, which keeps every AI endpoint usable
                    and returns a clear "not configured" response instead of
                    an application error.
  OPENAI_API_KEY    required when AI_PROVIDER=openai
  OPENAI_MODEL      optional, defaults to 'gpt-4o-mini'
  OPENAI_BASE_URL   optional, for OpenAI-compatible proxies/gateways
"""

import os

from .providers.base import AIProvider, AIResponse
from .providers.null_provider import NullProvider
from .providers.openai_provider import OpenAIProvider


def get_provider() -> AIProvider:
    provider_name = (os.environ.get('AI_PROVIDER') or '').strip().lower()
    if provider_name == 'openai':
        api_key = os.environ.get('OPENAI_API_KEY', '')
        if api_key:
            return OpenAIProvider(
                api_key=api_key,
                model=os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'),
                base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
            )
    return NullProvider()


__all__ = ['AIProvider', 'AIResponse', 'NullProvider', 'OpenAIProvider', 'get_provider']
