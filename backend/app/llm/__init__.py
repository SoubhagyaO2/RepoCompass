"""LLM providers behind one small interface (Milestone 13)."""

from app.config import Settings
from app.llm.base import LLMClient, LLMConfigError
from app.llm.gemini import GeminiClient

PROVIDERS = ("gemini",)


def get_llm_client(settings: Settings) -> LLMClient:
    """The client selected by REPOCOMPASS_LLM_PROVIDER. Clear errors, never a key in a message."""
    provider = settings.llm_provider.strip().lower()
    if provider == "gemini":
        key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
        if not key:
            raise LLMConfigError("GEMINI_API_KEY is not set (put it in backend/.env)")
        return GeminiClient(key, settings.gemini_model)
    raise LLMConfigError(
        f"unknown LLM provider {settings.llm_provider!r}; available: {', '.join(PROVIDERS)}"
    )
