from .base import AIProvider, AIResponse


class NullProvider(AIProvider):
    """Active whenever AI_PROVIDER isn't set to a configured provider. Every
    AI endpoint must stay usable with this active — it returns a clear,
    non-error 'not configured' response rather than the app throwing."""

    name = 'none'

    def is_configured(self) -> bool:
        return False

    def complete(self, system_prompt: str, user_prompt: str) -> AIResponse:
        return AIResponse(ok=False, error='AI analysis is not configured.')
