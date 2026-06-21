"""Shared PydanticAI model resolution.

pydantic-settings reads .env into its own config object but does not inject
values into os.environ, so PydanticAI's default env-var lookup fails for keys
like ANTHROPIC_API_KEY. Pass the key explicitly here instead.
"""
import structlog

from config import get_settings

log = structlog.get_logger()


def resolve_model(model_str: str):
    """Return a PydanticAI model instance for the given model string."""
    log.debug("resolve_model", model=model_str)
    settings = get_settings()
    if model_str.startswith("ollama:"):
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider
        model_name = model_str.removeprefix("ollama:")
        return OllamaModel(model_name, provider=OllamaProvider(base_url=f"{settings.ollama_base_url}/v1"))
    if model_str.startswith("anthropic:"):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        model_name = model_str.removeprefix("anthropic:")
        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=settings.anthropic_api_key))
    return model_str
