"""
Provider-agnostic model construction.

Nine call sites constructed ChatOpenAI or OpenAIEmbeddings directly, which made
"we cannot send customer data to OpenAI" unanswerable without editing nine files.
That is a real constraint in regulated or procurement-bound deployments, and it is
usually discovered late.

Everything now goes through here, and the provider is configuration:

    LLM_PROVIDER=openai          LLM_MODEL=gpt-4o-mini
    LLM_PROVIDER=bedrock         LLM_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
    LLM_PROVIDER=anthropic       LLM_MODEL=claude-sonnet-4-5
    LLM_PROVIDER=ollama          LLM_MODEL=llama3.1        (local, no data leaves the host)
    LLM_PROVIDER=azure_openai    LLM_MODEL=<deployment name>

Providers are declared here but their packages are optional. Selecting one that is
not installed fails with the install command rather than an ImportError traceback,
because the person hitting it is usually configuring a deployment, not debugging
Python.

Defaults are unchanged -- openai and gpt-4o-mini -- so this swap is invisible
unless someone sets the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass(frozen=True)
class Provider:
    """How to build a chat model for one provider, and what it needs."""

    chat_import: str          # module providing the chat class
    chat_class: str
    package: str              # what to install if the module is missing
    env_keys: tuple[str, ...] = ()   # credentials it expects, for a clearer error
    local: bool = False       # true when no data leaves the host


PROVIDERS: Dict[str, Provider] = {
    "openai": Provider(
        chat_import="langchain_openai", chat_class="ChatOpenAI",
        package="langchain-openai", env_keys=("OPENAI_API_KEY",),
    ),
    "anthropic": Provider(
        chat_import="langchain_anthropic", chat_class="ChatAnthropic",
        package="langchain-anthropic", env_keys=("ANTHROPIC_API_KEY",),
    ),
    "bedrock": Provider(
        chat_import="langchain_aws", chat_class="ChatBedrock",
        package="langchain-aws", env_keys=("AWS_REGION",),
    ),
    "azure_openai": Provider(
        chat_import="langchain_openai", chat_class="AzureChatOpenAI",
        package="langchain-openai",
        env_keys=("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
    ),
    "ollama": Provider(
        chat_import="langchain_ollama", chat_class="ChatOllama",
        package="langchain-ollama", local=True,
    ),
}

# Embeddings are a separate choice: a deployment may run inference locally while
# still using a hosted embedding model, or the reverse.
EMBEDDING_PROVIDERS: Dict[str, Provider] = {
    "openai": Provider("langchain_openai", "OpenAIEmbeddings", "langchain-openai",
                       ("OPENAI_API_KEY",)),
    "bedrock": Provider("langchain_aws", "BedrockEmbeddings", "langchain-aws",
                        ("AWS_REGION",)),
    "ollama": Provider("langchain_ollama", "OllamaEmbeddings", "langchain-ollama",
                       local=True),
}


class ProviderNotAvailable(RuntimeError):
    """Raised when a configured provider cannot be constructed."""


def _resolve(spec: Provider, name: str) -> type:
    try:
        module = __import__(spec.chat_import, fromlist=[spec.chat_class])
    except ImportError as e:
        raise ProviderNotAvailable(
            f"Provider '{name}' needs the {spec.package} package.\n"
            f"  uv add {spec.package}"
        ) from e

    missing = [k for k in spec.env_keys if not os.getenv(k)]
    if missing:
        raise ProviderNotAvailable(
            f"Provider '{name}' requires {', '.join(missing)} in the environment."
        )

    return getattr(module, spec.chat_class)


def chat_model(temperature: float = 0.0, **kwargs: Any):
    """Build the configured chat model.

    Args:
        temperature: sampling temperature; 0 for anything whose output is parsed
        **kwargs: passed through to the provider class
    """
    name = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    model = os.getenv("LLM_MODEL", DEFAULT_CHAT_MODEL)

    spec = PROVIDERS.get(name)
    if spec is None:
        raise ProviderNotAvailable(
            f"Unknown LLM_PROVIDER '{name}'. Known: {', '.join(sorted(PROVIDERS))}"
        )

    cls = _resolve(spec, name)
    logger.debug(f"chat model: {name}/{model}")
    return cls(model=model, temperature=temperature, **kwargs)


def embedding_model(**kwargs: Any):
    """Build the configured embedding model.

    Changing this invalidates the vector store: embeddings from different models
    are not comparable, so the corpus must be re-indexed after a switch.
    """
    name = os.getenv("EMBEDDING_PROVIDER", os.getenv("LLM_PROVIDER", "openai")).strip().lower()
    model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

    spec = EMBEDDING_PROVIDERS.get(name)
    if spec is None:
        # A local chat provider with no embedding support should not silently fall
        # back to a hosted one -- that would send data the operator chose to keep in.
        raise ProviderNotAvailable(
            f"No embedding provider '{name}'. Known: {', '.join(sorted(EMBEDDING_PROVIDERS))}. "
            "Set EMBEDDING_PROVIDER explicitly if it differs from LLM_PROVIDER."
        )

    cls = _resolve(spec, name)
    logger.debug(f"embedding model: {name}/{model}")
    return cls(model=model, **kwargs)


def active_configuration() -> dict:
    """What is configured, for /health. Never returns a credential."""
    llm = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    emb = os.getenv("EMBEDDING_PROVIDER", llm).strip().lower()
    return {
        "llm_provider": llm,
        "llm_model": os.getenv("LLM_MODEL", DEFAULT_CHAT_MODEL),
        "embedding_provider": emb,
        "embedding_model": os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        "data_leaves_host": not (
            PROVIDERS.get(llm, Provider("", "", "")).local
            and EMBEDDING_PROVIDERS.get(emb, Provider("", "", "")).local
        ),
    }
