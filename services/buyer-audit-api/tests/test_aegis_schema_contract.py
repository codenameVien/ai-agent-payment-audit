from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.aa_policy import amount_units
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    REPO_FIXTURE_DIR,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import AegisDecisionWorkflow

SCHEMA_DIR = REPO_FIXTURE_DIR.parents[1] / "domains" / "ai_inference"
PRICING_CASES = REPO_FIXTURE_DIR / "pricing-cases.json"
PROMPT = "x" * 100


def _schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


@pytest.fixture
def decision_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    """One real decision plus the snapshot body it was computed from."""

    async def build() -> tuple[dict[str, Any], dict[str, Any]]:
        repository = InMemoryEvidenceRepository()
        clock = FrozenClock()
        purchases = PurchaseService(
            repository=repository,
            cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
            domains=DomainRegistry(
                [AiInferenceDomainModule(default_max_output_tokens=1024)]
            ),
            clock=clock,
        )
        created = await purchases.create(
            owner_address="0x0000000000000000000000000000000000000001",
            domain_id="ai_inference",
            raw_request={"requestSchema": "aegis-aa-v1", "prompt": PROMPT},
            budget_units=5_000,
            policy={},
        )
        workflow = AegisDecisionWorkflow(
            repository=repository,
            clock=clock,
            catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
            source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
            token=TokenIdentity(
                name="AEGIS",
                symbol="AEGIS",
                decimals=6,
                address="0x0000000000000000000000000000000000000000",
                chain_id=84532,
            ),
        )
        decision = await workflow.decide(purchase_id=created.purchase_id)
        events = await repository.list_events(created.purchase_id)
        document = await repository.get_document(decision.snapshot_id)
        assert document is not None
        return dict(events[2].payload), {
            **document.payload,
            "snapshotId": document.document_id,
        }

    import asyncio

    return asyncio.run(build())


def _assert_required(payload: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    missing = [key for key in schema["required"] if key not in payload]
    assert not missing, f"{label} is missing {missing}"


def test_snapshot_evidence_matches_the_shared_schema(
    decision_payloads: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, snapshot = decision_payloads
    schema = _schema("aa-snapshot.schema.json")
    _assert_required(snapshot, schema, "snapshot")
    assert set(snapshot) == set(schema["properties"])
    assert re.fullmatch(
        schema["properties"]["snapshotId"]["pattern"], snapshot["snapshotId"]
    )
    assert snapshot["mode"] in schema["properties"]["mode"]["enum"]
    model_schema = schema["properties"]["models"]["items"]
    for model in snapshot["models"]:
        _assert_required(model, model_schema, "snapshot model")
        for field, rule in model_schema["properties"].items():
            if rule.get("pattern"):
                assert re.fullmatch(rule["pattern"], model[field]), (field, model[field])


def test_decision_evidence_matches_the_shared_schema(
    decision_payloads: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    decision, _ = decision_payloads
    schema = _schema("aa-decision.schema.json")
    _assert_required(decision, schema, "decision")
    assert (
        decision["scoringPolicyVersion"]
        == schema["properties"]["scoringPolicyVersion"]["const"]
    )
    assert set(decision["weights"]) == set(schema["properties"]["weights"]["properties"])
    assert (
        decision["priority"]["effectivePriority"]
        in schema["properties"]["priority"]["properties"]["effectivePriority"]["enum"]
    )
    for scored in [*decision["eligible"], decision["winner"]]:
        _assert_required(scored, schema["$defs"]["scored"], "scored candidate")
        assert set(scored["scores"]) == {
            "price",
            "completionTime",
            "intelligence",
            "total",
        }
    for candidate in decision["candidates"]:
        _assert_required(candidate, schema["$defs"]["candidate"], "candidate")
        assert candidate["capabilitiesSource"] == "catalog"


def test_rejection_reason_codes_agree_with_the_shared_schema() -> None:
    from buyer_audit_api.core.aa_policy import FilterReason

    schema = _schema("aa-decision.schema.json")
    allowed = schema["properties"]["rejected"]["items"]["properties"]["reasons"]["items"][
        "enum"
    ]
    assert sorted(item.value for item in FilterReason) == sorted(allowed)


@pytest.mark.parametrize("case", json.loads(PRICING_CASES.read_text())["cases"])
def test_python_agrees_with_the_shared_pricing_fixture(case: dict[str, Any]) -> None:
    """The same fixture is recomputed by `scripts/validate_schemas.mjs` in JavaScript."""
    assert (
        amount_units(
            input_tokens=case["estimatedInputTokens"],
            max_output_tokens=case["maxOutputTokens"],
            input_price_per_million=Decimal(case["inputPricePerMillion"]),
            output_price_per_million=Decimal(case["outputPricePerMillion"]),
        )
        == case["amountUnits"]
    ), case["name"]


def test_shared_fixture_directory_is_the_one_the_service_reads() -> None:
    assert PRICING_CASES.is_file()
    assert FIXTURE_CATALOG_PATH.is_file()
    assert all(Path(path).is_file() for path in FIXTURE_PAGE_PATHS)
