from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from eth_utils import is_address

from buyer_audit_api.adapters.artificial_analysis import (
    FixtureArtificialAnalysisSource,
    HttpArtificialAnalysisSource,
)
from buyer_audit_api.adapters.commerce_gateway import HttpCommerceGatewayClient
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.local_priority import LocalQwenPriorityClassifier
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.auth_service import AuthService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import ConfigurationError
from buyer_audit_api.core.observer import LocalQwenEvidenceObserver, ObserverMode
from buyer_audit_api.core.ports import Clock, EvidenceRepository, PayloadCipher
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.time import SystemClock
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_PAGE_PATHS,
    RUNTIME_CATALOG_PATH,
    AaFieldPaths,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import (
    MappingProvenance,
    ModelCatalog,
    TokenIdentity,
)
from buyer_audit_api.domains.ai_inference.aa_ports import AaCaptureSource
from buyer_audit_api.domains.ai_inference.aa_workflow import (
    AegisDecisionWorkflow,
    AegisEvidenceReader,
)
from buyer_audit_api.settings import Settings


def decode_key(value: str, *, name: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be valid base64") from exc
    if len(decoded) != 32:
        raise ConfigurationError(f"{name} must decode to exactly 32 bytes")
    return decoded


@dataclass(slots=True)
class AppContainer:
    repository: EvidenceRepository
    cipher: PayloadCipher
    clock: Clock
    auth_service: AuthService
    purchase_service: PurchaseService
    #: Fixed single-user demo owner. Server configuration, never a browser-selected value.
    local_owner_address: str
    internal_service_token: str = "test-internal-token"
    admin_service_token: str = "test-admin-token"
    local_allowed_origins: tuple[str, ...] = ()
    local_allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    #: Server-fixed settlement execution mode. A request can never label its own record.
    aegis_execution_mode: str = "mock"
    payment_token_symbol: str = "AEGIS"
    aegis_workflow: AegisDecisionWorkflow | None = None
    aegis_evidence_reader: AegisEvidenceReader | None = None
    commerce_gateway: HttpCommerceGatewayClient | None = None
    observer_mode: str = "off"
    checkpoint_mode: str = "off"
    evidence_observer: LocalQwenEvidenceObserver | None = None


def build_aa_capture_source(settings: Settings, catalog: ModelCatalog) -> AaCaptureSource:
    """Fail closed: a live capture is only possible with an explicitly mapped catalog.

    Without a server AA key the buyer reads the checked-in fixture pages and records
    `mode=fixture`, so no local run can present itself as verified live AA data.
    """
    try:
        overrides = json.loads(settings.aa_field_paths_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AA_FIELD_PATHS_JSON must be valid JSON") from exc
    if not isinstance(overrides, dict):
        raise ConfigurationError("AA_FIELD_PATHS_JSON must be a JSON object")
    field_paths = AaFieldPaths.from_mapping(overrides)
    api_key = (settings.aa_api_key or "").strip()
    if not api_key:
        pages: tuple[Path, ...] = FIXTURE_PAGE_PATHS
        if settings.aa_fixture_pages_json.strip():
            try:
                declared = json.loads(settings.aa_fixture_pages_json)
            except json.JSONDecodeError as exc:
                raise ConfigurationError("AA_FIXTURE_PAGES_JSON must be valid JSON") from exc
            if not isinstance(declared, list) or not all(
                isinstance(item, str) and item.strip() for item in declared
            ):
                raise ConfigurationError("AA_FIXTURE_PAGES_JSON must be a list of paths")
            pages = tuple(Path(item) for item in declared)
        return FixtureArtificialAnalysisSource(page_paths=pages, field_paths=field_paths)
    if catalog.provenance != {MappingProvenance.CONFIGURED}:
        raise ConfigurationError(
            "AA_API_KEY is set but the model catalog is not an explicitly configured "
            "live mapping; set AA_MODEL_CATALOG_PATH or unset the key"
        )
    return HttpArtificialAnalysisSource(
        api_key=api_key,
        field_paths=field_paths,
    )


def build_container(settings: Settings) -> AppContainer:
    clock = SystemClock()
    # aa-three-factor-v1: a single local owner, mock providers and a mock Facilitator.
    # No chain reader is constructed, so this composition cannot reach an RPC even by
    # accident.
    local_owner = settings.aegis_local_owner_address.strip().lower()
    if not local_owner or not is_address(local_owner):
        raise ConfigurationError("AEGIS_LOCAL_OWNER_ADDRESS must be an Ethereum address")
    repository = MongoEvidenceRepository(
        uri=settings.mongodb_uri,
        database=settings.mongodb_database,
    )
    cipher = LocalEnvelopeCipher(
        master_key=decode_key(
            settings.payload_master_key_base64,
            name="PAYLOAD_MASTER_KEY_BASE64",
        )
    )
    auth_service = AuthService(repository=repository, clock=clock)
    classifier_mode = settings.aegis_priority_classifier.strip().lower()
    if classifier_mode not in {"deterministic", "local-qwen"}:
        raise ConfigurationError("AEGIS_PRIORITY_CLASSIFIER must be deterministic or local-qwen")
    priority_classifier = (
        LocalQwenPriorityClassifier(
            base_url=settings.aegis_ollama_url,
            model=settings.aegis_ollama_model,
            timeout_seconds=settings.aegis_ollama_timeout_seconds,
        )
        if classifier_mode == "local-qwen"
        else None
    )
    observer_mode = settings.aegis_observer_mode.strip().lower()
    checkpoint_mode = settings.aegis_checkpoint_mode.strip().lower()
    if observer_mode not in {item.value for item in ObserverMode}:
        raise ConfigurationError("AEGIS_OBSERVER_MODE must be off, mock or local-qwen")
    if checkpoint_mode not in {"off", "mock", "live"}:
        raise ConfigurationError("AEGIS_CHECKPOINT_MODE must be off, mock or live")
    if checkpoint_mode != "off" and observer_mode == ObserverMode.OFF.value:
        raise ConfigurationError(
            "AEGIS_CHECKPOINT_MODE requires AEGIS_OBSERVER_MODE=mock or local-qwen"
        )
    purchase_service = PurchaseService(
        repository=repository,
        cipher=cipher,
        domains=DomainRegistry(
            [
                AiInferenceDomainModule(
                    default_max_output_tokens=settings.aegis_max_output_tokens,
                    system_prompt=settings.aegis_system_prompt,
                    priority_classifier=priority_classifier,
                )
            ]
        ),
        clock=clock,
    )
    catalog = load_model_catalog(
        Path(settings.aa_model_catalog_path)
        if settings.aa_model_catalog_path
        else RUNTIME_CATALOG_PATH
    )
    aegis_workflow = AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=catalog,
        source=build_aa_capture_source(settings, catalog),
        token=TokenIdentity(
            name=settings.payment_token_name,
            symbol=settings.payment_token_symbol,
            decimals=6,
            address=settings.aegis_token_address.lower(),
            chain_id=settings.aegis_chain_id,
            status=settings.aegis_token_status,
        ),
    )
    return AppContainer(
        repository=repository,
        cipher=cipher,
        clock=clock,
        auth_service=auth_service,
        purchase_service=purchase_service,
        local_owner_address=local_owner,
        internal_service_token=settings.internal_service_token,
        admin_service_token=settings.admin_service_token,
        local_allowed_origins=tuple(
            item.strip() for item in settings.aegis_local_allowed_origins.split(",")
        ),
        local_allowed_hosts=tuple(
            item.strip() for item in settings.aegis_local_allowed_hosts.split(",")
        ),
        aegis_execution_mode=settings.aegis_execution_mode,
        observer_mode=observer_mode,
        checkpoint_mode=checkpoint_mode,
        evidence_observer=(
            LocalQwenEvidenceObserver(
                base_url=settings.aegis_ollama_url,
                model=settings.aegis_ollama_model,
                timeout_seconds=settings.aegis_observer_timeout_seconds,
            )
            if observer_mode == ObserverMode.LOCAL_QWEN.value
            else None
        ),
        payment_token_symbol=settings.payment_token_symbol,
        aegis_workflow=aegis_workflow,
        aegis_evidence_reader=AegisEvidenceReader(repository=repository),
        commerce_gateway=HttpCommerceGatewayClient(
            base_url=settings.commerce_gateway_url,
            service_token=settings.gateway_service_token,
        ),
    )
