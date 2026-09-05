"""Read-only ERC-8004 reputation query through the Payment Executor.

Design 18.9 step 2: the buyer never talks to a chain. It asks the Payment Executor's
read endpoint for the raw trusted feedback events in a block range, then
`ReputationAggregationPolicy` turns them into an immutable snapshot with full provenance.

This adapter performs **no writes**. A query failure never becomes a favourable score:
`snapshot()` falls back to a fresh-enough stored snapshot labelled `STALE`, or to
`NO_EVIDENCE/50` (`P6-AC-06.3`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.core.models import (
    EvidenceSource,
    JsonObject,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.ports import Clock, ReputationSnapshotPort
from buyer_audit_api.core.reputation import (
    FEEDBACK_TAG1,
    FEEDBACK_TAG2,
    RawFeedbackEvent,
    ReputationAggregationPolicy,
    ReputationQueryScope,
    ReputationSnapshot,
)


class HttpReputationQueryClient:
    """Calls the Payment Executor's read-only `POST /reputation-query`."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout_seconds

    async def query(
        self,
        *,
        erc8004_agent_id: str,
        trusted_clients: tuple[str, ...],
        from_block: int,
        to_block: int,
    ) -> JsonObject:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/reputation-query",
                headers={"authorization": f"Bearer {self._service_token}"},
                json={
                    "erc8004AgentId": erc8004_agent_id,
                    "trustedClients": list(trusted_clients),
                    "fromBlock": from_block,
                    "toBlock": to_block,
                },
            )
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict):
            raise ValueError("reputation query returned a malformed response")
        return value


def _event_from_payload(value: Any) -> RawFeedbackEvent:
    if not isinstance(value, dict):
        raise ValueError("reputation query event is malformed")
    return RawFeedbackEvent(
        value=int(value["value"]),
        value_decimals=int(value["valueDecimals"]),
        client_address=str(value["clientAddress"]).lower(),
        block_number=int(value["blockNumber"]),
        log_index=int(value["logIndex"]),
        transaction_ref=transaction_ref_from_payload(value["transactionRef"]),
        tag1=str(value.get("tag1", FEEDBACK_TAG1)),
        tag2=str(value.get("tag2", FEEDBACK_TAG2)),
    )


class GatewayReputationProvider:
    """`SellerReputationProvider` backed by the Payment Executor read endpoint."""

    def __init__(
        self,
        *,
        query_client: HttpReputationQueryClient,
        snapshots: ReputationSnapshotPort,
        clock: Clock,
        chain_id: int,
        registry_address: str,
        trusted_clients: tuple[str, ...],
        aggregation: ReputationAggregationPolicy | None = None,
    ) -> None:
        if not trusted_clients:
            raise ValueError("a reputation provider requires a trusted client allow-list")
        self._query_client = query_client
        self._snapshots = snapshots
        self._clock = clock
        self._chain_id = chain_id
        self._registry_address = registry_address.lower()
        self._trusted_clients = tuple(client.lower() for client in trusted_clients)
        self._aggregation = aggregation or ReputationAggregationPolicy()

    async def snapshot(
        self, *, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot | None:
        now = self._clock.now()
        stored = await self._snapshots.latest_snapshot(seller_agent_id)
        try:
            answer = await self._query_client.query(
                erc8004_agent_id=erc8004_agent_id,
                trusted_clients=self._trusted_clients,
                from_block=0,
                to_block=0,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # A failed query is never a favourable score.
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                erc8004_agent_id=erc8004_agent_id,
            )
        try:
            scope = ReputationQueryScope(
                chain_id=int(answer["chainId"]),
                registry_address=str(answer["registryAddress"]).lower(),
                erc8004_agent_id=str(answer["erc8004AgentId"]),
                trusted_clients=tuple(
                    str(item).lower() for item in answer["trustedClients"]
                ),
                from_block=int(answer["fromBlock"]),
                to_block=int(answer["toBlock"]),
                tag1=str(answer.get("tag1", FEEDBACK_TAG1)),
                tag2=str(answer.get("tag2", FEEDBACK_TAG2)),
            )
            queried_at = datetime.fromisoformat(str(answer["queriedAt"]))
            events = tuple(
                _event_from_payload(item) for item in list(answer.get("events", []))
            )
        except (KeyError, TypeError, ValueError):
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                erc8004_agent_id=erc8004_agent_id,
            )
        if scope.chain_id != self._chain_id or (
            scope.registry_address != self._registry_address
        ):
            # A different chain or registry is not this agent's reputation.
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                erc8004_agent_id=erc8004_agent_id,
            )
        snapshot = self._aggregation.aggregate(
            snapshot_id=_snapshot_id(scope=scope, seller_agent_id=seller_agent_id),
            seller_agent_id=seller_agent_id,
            scope=scope,
            queried_at=queried_at,
            events=events,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            now=now,
        )
        return await self._snapshots.put_snapshot(snapshot)

    async def _fallback(
        self,
        *,
        stored: ReputationSnapshot | None,
        now: datetime,
        seller_agent_id: str,
        erc8004_agent_id: str,
    ) -> ReputationSnapshot:
        scope = ReputationQueryScope(
            chain_id=self._chain_id,
            registry_address=self._registry_address,
            erc8004_agent_id=erc8004_agent_id,
            trusted_clients=self._trusted_clients,
            from_block=0,
            to_block=0,
        )
        return self._aggregation.stale_or_neutral(
            snapshot=stored,
            now=now,
            snapshot_id=_snapshot_id(scope=scope, seller_agent_id=seller_agent_id),
            seller_agent_id=seller_agent_id,
            scope=scope,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )


def _snapshot_id(*, scope: ReputationQueryScope, seller_agent_id: str) -> str:
    """Deterministic from the question plus its head, so a re-query is idempotent."""
    digest = sha256_json(
        {
            "queryFingerprint": scope.query_fingerprint,
            "sellerAgentId": seller_agent_id,
            "toBlock": scope.to_block,
        }
    )
    return f"repsnap:{digest.split(':')[-1][:32]}"
