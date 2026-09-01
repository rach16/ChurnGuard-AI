"""
The provider abstraction must actually abstract.

Constructs models for each configured provider without calling any of them, so
this costs nothing and needs no credentials beyond what is already set. The point
is that switching providers is configuration, and that a missing one fails with an
instruction rather than a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.llm import (  # noqa: E402
    EMBEDDING_PROVIDERS,
    PROVIDERS,
    ProviderNotAvailable,
    active_configuration,
    chat_model,
    embedding_model,
)


def test_no_vendor_construction_outside_the_factory():
    """A direct ChatOpenAI() anywhere else reintroduces the lock-in this removes."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "llm.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "ChatOpenAI(" in text or "OpenAIEmbeddings(" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"vendor classes constructed outside core/llm.py: {offenders}"


def test_default_is_unchanged(monkeypatch):
    """The swap must be invisible unless someone sets the environment."""
    for key in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    cfg = active_configuration()
    assert cfg["llm_provider"] == "openai"
    assert cfg["llm_model"] == "gpt-4o-mini"
    assert cfg["embedding_model"] == "text-embedding-3-small"

    # Constructed, never called -- no request is made and nothing is billed.
    assert type(chat_model()).__name__ == "ChatOpenAI"
    assert type(embedding_model()).__name__ == "OpenAIEmbeddings"


def test_embeddings_default_to_the_chat_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    assert active_configuration()["embedding_provider"] == "openai"


def test_unknown_provider_names_the_known_ones(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt5-turbo-ultra")
    with pytest.raises(ProviderNotAvailable) as e:
        chat_model()
    assert "Known:" in str(e.value)


@pytest.mark.parametrize("name", sorted(set(PROVIDERS) - {"openai"}))
def test_uninstalled_provider_gives_an_install_command(monkeypatch, name):
    """The person hitting this is configuring a deployment, not debugging Python."""
    monkeypatch.setenv("LLM_PROVIDER", name)
    spec = PROVIDERS[name]
    try:
        __import__(spec.chat_import)
    except ImportError:
        with pytest.raises(ProviderNotAvailable) as e:
            chat_model()
        assert "uv add" in str(e.value), "error should say how to install it"


def test_missing_credentials_are_named(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderNotAvailable) as e:
        chat_model()
    assert "OPENAI_API_KEY" in str(e.value)


def test_local_provider_is_reported_as_local(monkeypatch):
    """An operator who chose a local model needs to see that nothing leaves."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    assert active_configuration()["data_leaves_host"] is False

    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    assert active_configuration()["data_leaves_host"] is True


def test_embedding_provider_never_falls_back_silently(monkeypatch):
    """Falling back to a hosted model would send data the operator chose to keep."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "anthropic")   # no embeddings offered
    with pytest.raises(ProviderNotAvailable):
        embedding_model()


def test_every_provider_declares_its_package():
    for name, spec in {**PROVIDERS, **EMBEDDING_PROVIDERS}.items():
        assert spec.package, f"{name} has no install package declared"
        assert spec.chat_class, f"{name} has no class declared"
