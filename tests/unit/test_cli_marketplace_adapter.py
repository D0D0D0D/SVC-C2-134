"""Tests for the AGENTIC STAR Marketplace entry adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import cli
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import MissingSecret, SecretProvider
from shared.services.llm.base_llm import BaseLLM
from src.graph.graph import Graph
from src.services import llm_seam


class _StaticProvider(SecretProvider):
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self, key: str, default: str | None = None) -> str | None:
        del key, default
        return self._value


class _SentinelLLM(BaseLLM):
    def complete(self, messages: list) -> dict:
        del messages
        return {}

    def stream(self, messages: list):
        del messages
        yield ""

    def bind_tools(self, tools: list) -> "_SentinelLLM":
        del tools
        return self


@pytest.fixture(autouse=True)
def _restore_llm_seam():
    llm_seam.set_llm(None)
    yield
    llm_seam.set_llm(None)


def test_install_marketplace_llm_installs_azure_adapter() -> None:
    cli._install_marketplace_llm()

    assert isinstance(llm_seam.get_llm(), cli._AzureMarketplaceLLM)


def test_install_marketplace_llm_preserves_existing_injection() -> None:
    fake = _SentinelLLM()
    llm_seam.set_llm(fake)

    cli._install_marketplace_llm()

    assert llm_seam.get_llm() is fake


def test_main_installs_llm_and_passes_marketplace_runner_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict = {}

    def record_runner(*args, **kwargs) -> None:
        recorded["args"] = args
        recorded["kwargs"] = kwargs

    llm_seam.set_llm(None)
    monkeypatch.setattr(cli, "run_agent_marketplace", record_runner)
    try:
        cli.main()

        assert isinstance(llm_seam.get_llm(), cli._AzureMarketplaceLLM)
        assert recorded["args"] == (cli.MarketplaceGraph,)
        assert recorded["kwargs"]["agent_name"] == "SVC-C2-134"
        assert recorded["kwargs"]["namespace"] == "svc"
        assert isinstance(recorded["kwargs"]["config"], dict)
        assert recorded["kwargs"]["config"]
    finally:
        llm_seam.set_llm(None)


def test_azure_marketplace_llm_retains_no_connection_values() -> None:
    llm = cli._AzureMarketplaceLLM()

    assert vars(llm) == {}
    assert llm.bind_tools([{"name": "lookup"}]) is not llm


@pytest.mark.parametrize("value", ["", " leading", "trailing ", "embedded\nnewline"])
def test_require_clean_rejects_empty_or_whitespace_values(value: str) -> None:
    with pytest.raises(ValueError, match="clean non-empty string"):
        cli._require_clean(_StaticProvider(value), "AZURE_OPENAI_API_KEY")


@pytest.mark.parametrize("missing_key", cli._REQUIRED_SECRET_KEYS)
def test_provision_secrets_fails_closed_when_azure_value_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    missing_key: str,
) -> None:
    for key in cli._REQUIRED_SECRET_KEYS:
        monkeypatch.setenv(key, "configured")
    monkeypatch.delenv(missing_key)
    graph = cli.MarketplaceGraph(config={})

    with pytest.raises(MissingSecret, match=missing_key):
        graph.provision_secrets(_StaticProvider("runner-value"))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://example.services.ai.azure.example/openai",
        "https://example.services.ai.azure.example/v1",
        "https://example.services.ai.azure.example?api-version=2024-10-21",
        "example.services.ai.azure.example",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_unusable_azure_endpoint_shape_fails_during_startup(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """AzureOpenAIClient appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces inside complete(); a node that swallows it
    degrades silently while the invocation still reports status: success, so the
    adapter has to reject the shape at startup instead.
    """

    for key in cli._REQUIRED_SECRET_KEYS:
        monkeypatch.setenv(key, "configured")
    graph = cli.MarketplaceGraph(config={})
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)

    with pytest.raises(ValueError, match="bare"):
        graph.provision_secrets(_StaticProvider("runner-value"))


def test_invoke_forwards_raw_user_input_byte_identical() -> None:
    raw = '{\n  "accuser_assertion": "literal spacing"\n}'
    graph = cli.MarketplaceGraph(config={})

    with patch.object(Graph, "invoke", return_value={"status": "success"}) as parent_invoke:
        graph.invoke(raw)

    forwarded = parent_invoke.call_args.args[0]
    assert forwarded is raw
    assert forwarded.encode("utf-8") == raw.encode("utf-8")
    assert '"mode"' not in forwarded


def test_invoke_disables_hitl_on_forwarded_context() -> None:
    graph = cli.MarketplaceGraph(config={})
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, hitl_allowed=True)

    with patch.object(Graph, "invoke", return_value={"status": "success"}) as parent_invoke:
        graph.invoke("{}", ctx=ctx)

    forwarded_ctx = parent_invoke.call_args.kwargs["ctx"]
    assert forwarded_ctx.hitl_allowed is False
    assert ctx.hitl_allowed is True


def test_invoke_drops_runner_input_context() -> None:
    graph = cli.MarketplaceGraph(config={})
    canary_context = {"conversation_history": [{"role": "user", "content": "CANARY"}]}

    with patch.object(Graph, "invoke", return_value={"status": "success"}) as parent_invoke:
        graph.invoke("{}", input_context=canary_context)

    forwarded_context = parent_invoke.call_args.kwargs["input_context"]
    assert "CANARY" not in repr(forwarded_context)
    assert forwarded_context == {}
