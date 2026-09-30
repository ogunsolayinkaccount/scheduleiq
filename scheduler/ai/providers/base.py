"""Base AI provider contract. All providers (and tests' mock providers)
implement this same shape so views never need to know which one is active."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AIResponse:
    ok: bool
    text: str = ''
    error: Optional[str] = None
    raw: Optional[dict] = None
    # Coarse, safe-to-display category: not_configured | authentication |
    # billing_quota | rate_limit | model | timeout | network | provider | bad_response
    error_type: Optional[str] = None


class AIProvider:
    name = 'base'

    def is_configured(self) -> bool:
        return False

    def complete(self, system_prompt: str, user_prompt: str) -> AIResponse:
        raise NotImplementedError
