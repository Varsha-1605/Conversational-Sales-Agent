import re

import pytest

from agents.workers.inventory import stock_operations
from agents.workers.inventory.manifest import INVENTORY_MANIFEST
from agents.workers.loyalty_offers import cart_quote
from agents.workers.loyalty_offers.manifest import LOYALTY_OFFERS_MANIFEST
from config import PROJECT_ROOT

WORKERS = {
    "inventory": (INVENTORY_MANIFEST, stock_operations),
    "loyalty_offers": (LOYALTY_OFFERS_MANIFEST, cart_quote),
}


@pytest.mark.parametrize("name", WORKERS)
def test_each_manifest_names_its_worker_and_lists_operations_with_timeouts(name):
    manifest, _ = WORKERS[name]
    assert manifest.name == name
    assert manifest.enabled
    assert len({operation.name for operation in manifest.operations}) == len(manifest.operations)
    assert all(operation.timeout_ms > 0 for operation in manifest.operations)


@pytest.mark.parametrize("name", WORKERS)
def test_every_manifest_operation_is_a_service_function_with_the_declared_effect(name):
    manifest, operations_module = WORKERS[name]
    for operation in manifest.operations:
        function = getattr(operations_module, operation.name)
        # only a write operation carries the mark that lets the domain core run it in its transaction
        assert getattr(function, "effect", "read") == operation.effect, operation.name


def test_the_inventory_worker_has_exactly_the_documented_operations():
    assert {(operation.name, operation.effect) for operation in INVENTORY_MANIFEST.operations} == {
        ("availability", "read"), ("fulfillment_options", "read"),
        ("create_hold", "write"), ("commit_hold", "write"), ("release_hold", "write"),
    }


def test_the_loyalty_offers_worker_only_quotes_and_names_the_worker_it_calls():
    assert [(operation.name, operation.effect) for operation in LOYALTY_OFFERS_MANIFEST.operations] == [
        ("quote", "read")]
    assert LOYALTY_OFFERS_MANIFEST.requires == ("inventory",)
    assert all(required in WORKERS for required in LOYALTY_OFFERS_MANIFEST.requires)


# PRD 4 and the Phase 4 completion criteria: these two workers decide with code only
LLM_IMPORT = re.compile(
    r"^\s*(from|import)\s+(openai|anthropic|langchain\w*|langgraph|litellm|ollama|boto3|google\.generativeai"
    r"|backend\.adapters\.llm)\b")


def test_no_rules_worker_imports_an_llm():
    offenders = []
    for worker in WORKERS:
        for path in (PROJECT_ROOT / "agents" / "workers" / worker).rglob("*.py"):
            for number, source_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if LLM_IMPORT.match(source_line):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert offenders == []


def test_the_llm_check_would_notice_an_import():
    assert LLM_IMPORT.match("import openai")
    assert LLM_IMPORT.match("from langchain_core.messages import HumanMessage")
    assert LLM_IMPORT.match("from backend.adapters.llm import chat_model")
    assert not LLM_IMPORT.match("from backend.domain.models.common import Model")
