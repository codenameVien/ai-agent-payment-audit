from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Protocol

from buyer_audit_api.adapters.ai_inference import (
    HttpSellerQuoteClient,
    JsonRpcSellerIdentityVerifier,
)
from buyer_audit_api.adapters.auth.siwe import PythonSiweVerifier
from buyer_audit_api.adapters.chain.json_rpc import JsonRpcTokenBalanceReader
from buyer_audit_api.adapters.commerce_gateway import HttpCommerceGatewayClient
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.auth_service import AuthService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import ConfigurationError
from buyer_audit_api.core.ports import Clock, EvidenceRepository, PayloadCipher
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.session import SessionCodec
from buyer_audit_api.core.time import SystemClock
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.benchmark import ManualBenchmarkProvider
from buyer_audit_api.domains.ai_inference.quote_verification import Eip712SellerQuoteVerifier
from buyer_audit_api.domains.ai_inference.workflow import (
    AiInferenceDecisionWorkflow,
    DeterministicExplanationAdapter,
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


class TokenBalanceReader(Protocol):
    async def balance_of(self, *, token: str, wallet: str) -> int: ...


@dataclass(slots=True)
class AppContainer:
    repository: EvidenceRepository
    cipher: PayloadCipher
    clock: Clock
    auth_service: AuthService
    purchase_service: PurchaseService
    cookie_secure: bool
    internal_service_token: str = "test-internal-token"
    token_balance_reader: TokenBalanceReader | None = None
    ai_inference_workflow: AiInferenceDecisionWorkflow | None = None
    commerce_gateway: HttpCommerceGatewayClient | None = None


def build_container(settings: Settings) -> AppContainer:
    clock = SystemClock()
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
    session_codec = SessionCodec(
        decode_key(settings.session_secret_base64, name="SESSION_SECRET_BASE64")
    )
    auth_service = AuthService(
        repository=repository,
        verifier=PythonSiweVerifier(),
        session_codec=session_codec,
        clock=clock,
        domain=settings.siwe_domain,
        uri=settings.siwe_uri,
        chain_id=settings.siwe_chain_id,
    )
    purchase_service = PurchaseService(
        repository=repository,
        cipher=cipher,
        domains=DomainRegistry([AiInferenceDomainModule()]),
        clock=clock,
    )
    try:
        seller_routes = json.loads(settings.seller_routes_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("SELLER_ROUTES_JSON must be valid JSON") from exc
    if not isinstance(seller_routes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in seller_routes.items()
    ):
        raise ConfigurationError("SELLER_ROUTES_JSON must map provider IDs to URLs")
    benchmark_time = clock.now()
    benchmarks = ManualBenchmarkProvider(
        [
            {
                "provider_id": "gemini",
                "model_id": settings.gemini_model_id,
                "model_version": settings.gemini_model_version,
                "observed_at": benchmark_time,
                "source_url": "https://ai.google.dev/gemini-api/docs/models",
                "quality_score": 86,
                "speed_score": 94,
                "reputation_score": 90,
                "capabilities": ["korean", "reasoning"],
            },
            {
                "provider_id": "nemotron",
                "model_id": settings.nemotron_model_id,
                "model_version": settings.nemotron_model_version,
                "observed_at": benchmark_time,
                "source_url": "https://build.nvidia.com/models",
                "quality_score": 92,
                "speed_score": 76,
                "reputation_score": 86,
                "capabilities": ["korean", "reasoning"],
            },
        ]
    )
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=benchmarks,
        quotes=HttpSellerQuoteClient(
            routes=seller_routes,
            chain_id=settings.siwe_chain_id,
            verifying_contract=settings.quote_verifying_contract,
            internal_service_token=settings.internal_service_token,
        ),
        quote_verifier=Eip712SellerQuoteVerifier(
            chain_id=settings.siwe_chain_id,
            verifying_contract=settings.quote_verifying_contract,
        ),
        identity_verifier=JsonRpcSellerIdentityVerifier(
            rpc_url=settings.base_sepolia_rpc_url or "",
            identity_registry=settings.erc8004_identity_registry,
        ),
        explanation=DeterministicExplanationAdapter(),
    )
    return AppContainer(
        repository=repository,
        cipher=cipher,
        clock=clock,
        auth_service=auth_service,
        purchase_service=purchase_service,
        cookie_secure=settings.cookie_secure,
        internal_service_token=settings.internal_service_token,
        token_balance_reader=(
            JsonRpcTokenBalanceReader(settings.base_sepolia_rpc_url)
            if settings.base_sepolia_rpc_url
            else None
        ),
        ai_inference_workflow=workflow,
        commerce_gateway=HttpCommerceGatewayClient(
            base_url=settings.commerce_gateway_url,
            service_token=settings.gateway_service_token,
        ),
    )
