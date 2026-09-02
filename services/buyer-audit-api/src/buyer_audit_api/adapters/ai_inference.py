from __future__ import annotations

import asyncio

import httpx
from eth_utils import keccak

from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    NormalizedAiRequest,
    SellerIdentityEvidence,
    SellerQuote,
)
from buyer_audit_api.domains.ai_inference.wire import seller_quote_from_wire


class HttpSellerQuoteClient:
    def __init__(
        self,
        *,
        routes: dict[str, str],
        chain_id: int,
        verifying_contract: str,
        internal_service_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not routes:
            raise ValueError("at least one seller route is required")
        self._routes = {key: value.rstrip("/") for key, value in routes.items()}
        self._chain_id = chain_id
        self._verifying_contract = verifying_contract
        self._authorization = f"Bearer {internal_service_token}"
        self._client = client or httpx.AsyncClient(timeout=15)

    async def quote(
        self,
        *,
        purchase_id: str,
        request: NormalizedAiRequest,
        benchmark: BenchmarkSnapshot,
    ) -> SellerQuote:
        route = self._routes.get(benchmark.provider_id)
        if route is None:
            raise ValueError(f"seller route is missing for {benchmark.provider_id}")
        payload: dict[str, object] = {
            "purchaseId": purchase_id,
            "requestId": (f"{purchase_id}:{benchmark.provider_id}:{benchmark.model_id}"),
            "minInputLimit": request.min_input_limit,
            "minOutputLimit": request.min_output_limit,
            "preferredModelId": benchmark.model_id,
        }
        if request.max_latency_ms is not None:
            payload["maxLatencyMs"] = request.max_latency_ms
        response = await self._client.post(
            f"{route}/internal/quotes",
            headers={"authorization": self._authorization},
            json=payload,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("seller quote response is malformed")
        return seller_quote_from_wire(
            payload,
            chain_id=self._chain_id,
            verifying_contract=self._verifying_contract,
        )


class JsonRpcSellerIdentityVerifier:
    def __init__(
        self,
        *,
        rpc_url: str,
        identity_registry: str,
        client: httpx.AsyncClient | None = None,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self._rpc_url = rpc_url
        self._identity_registry = identity_registry.lower()
        self._client = client or httpx.AsyncClient(timeout=10)
        self._retry_delay_seconds = retry_delay_seconds

    async def verify(self, quote: SellerQuote) -> SellerIdentityEvidence:
        if not self._rpc_url:
            raise ValueError("Base Sepolia RPC URL is required for ERC-8004 verification")
        selector = keccak(text="getAgentWallet(uint256)")[:4].hex()
        argument = int(quote.erc8004_agent_id).to_bytes(32, "big").hex()
        result: str | None = None
        last_error = "unknown RPC response"
        for attempt in range(3):
            try:
                response = await self._client.post(
                    self._rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_call",
                        "params": [
                            {
                                "to": self._identity_registry,
                                "data": f"0x{selector}{argument}",
                            },
                            "latest",
                        ],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                candidate = payload.get("result") if isinstance(payload, dict) else None
                if not isinstance(candidate, str) or len(candidate) != 66:
                    rpc_error = payload.get("error") if isinstance(payload, dict) else None
                    raise ValueError(f"malformed response: {rpc_error or type(payload).__name__}")
                result = candidate
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc) or type(exc).__name__
                if attempt < 2:
                    await asyncio.sleep(self._retry_delay_seconds)
        if result is None:
            raise ValueError(f"ERC-8004 agent wallet RPC failed after 3 attempts: {last_error}")
        agent_wallet = "0x" + result[-40:]
        verified = (
            agent_wallet != "0x" + "0" * 40 and agent_wallet.lower() == quote.signer_address.lower()
        )
        if not verified:
            raise ValueError("quote signer is not the ERC-8004 agent wallet")
        return SellerIdentityEvidence(
            quoteId=quote.quote_id,
            erc8004AgentId=quote.erc8004_agent_id,
            identityRegistry=self._identity_registry,
            agentWallet=agent_wallet,
            signerAddress=quote.signer_address,
            identityVerified=True,
        )
