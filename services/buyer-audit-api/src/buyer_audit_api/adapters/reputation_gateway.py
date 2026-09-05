"""Read-only ERC-8004 reputation query through the Payment Executor.

Design 18.9 step 2: the buyer never talks to a chain. It asks the Payment Executor's
read endpoint for the raw trusted feedback events in a bounded window ending at the
chain head, then `ReputationAggregationPolicy` turns them into an immutable snapshot
with full provenance.

This adapter performs **no writes**. Everything it accepts is bound to what it asked
for: the requested seller agent, the requested ERC-8004 agent ID, the configured
trusted-client allow-list, the canonical tag pair, the configured chain and registry,
and a block window that the gateway must have resolved from a real chain head. Every
returned event has to sit inside that window and agree with its own transaction
reference. Anything else is not this agent's reputation, so it is discarded rather
than scored.

A query failure never becomes a favourable score: `snapshot()` falls back to a
fresh-enough stored snapshot **for exactly the same question**, labelled `STALE`, or to
`NO_EVIDENCE/50` (`P6-AC-06.3`). An empty trusted-client allow-list - the default
deployment state - short-circuits to `NO_EVIDENCE/50` without issuing any call at all.
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
    FEEDBACK_CLIENT_PATTERN,
    FEEDBACK_TAG1,
    FEEDBACK_TAG2,
    MAX_REPUTATION_LOOKBACK_BLOCKS,
    REPUTATION_LOOKBACK_BLOCKS,
    RawFeedbackEvent,
    ReputationAggregationPolicy,
    ReputationQueryScope,
    ReputationSnapshot,
)


def parse_feedback_clients(raw: str | None) -> tuple[str, ...]:
    """Strictly parses the `PBL_AUDIT_FEEDBACK_CLIENTS` deployment declaration.

    Requirements 12.10: the trusted reviewer/client allow-list is explicit deployment
    config and an unknown client is excluded. The value is a comma-separated list of
    EVM addresses; entries are lowercased and de-duplicated while keeping their declared
    order. Absent, empty or whitespace-only means **no client is trusted**, which is a
    valid fail-closed state, not a configuration error. A malformed entry is a
    configuration error and must stop the deployment rather than silently trust nothing:
    a typo that degraded every score to neutral would be invisible in production.
    """
    if raw is None:
        return ()
    declared = [item.strip().lower() for item in raw.split(",") if item.strip()]
    for item in declared:
        if FEEDBACK_CLIENT_PATTERN.fullmatch(item) is None:
            raise ValueError(
                "PBL_AUDIT_FEEDBACK_CLIENTS entries must be 0x-prefixed EVM addresses"
            )
    return tuple(dict.fromkeys(declared))


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
        lookback_blocks: int,
    ) -> JsonObject:
        """Asks for a bounded window; the gateway resolves it against the chain head."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/reputation-query",
                headers={"authorization": f"Bearer {self._service_token}"},
                json={
                    "erc8004AgentId": erc8004_agent_id,
                    "trustedClients": list(trusted_clients),
                    "lookbackBlocks": lookback_blocks,
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
        lookback_blocks: int = REPUTATION_LOOKBACK_BLOCKS,
        aggregation: ReputationAggregationPolicy | None = None,
    ) -> None:
        if any(
            FEEDBACK_CLIENT_PATTERN.fullmatch(client) is None
            for client in trusted_clients
        ):
            raise ValueError("trusted reputation clients must be lowercase EVM addresses")
        if not 1 <= lookback_blocks <= MAX_REPUTATION_LOOKBACK_BLOCKS:
            raise ValueError("reputation lookback window is out of range")
        self._query_client = query_client
        self._snapshots = snapshots
        self._clock = clock
        self._chain_id = chain_id
        self._registry_address = registry_address.lower()
        # An empty allow-list is the default deployment state, not an error: nothing is
        # trusted, so nothing is queried and every candidate scores neutral.
        self._trusted_clients = tuple(dict.fromkeys(trusted_clients))
        self._lookback_blocks = lookback_blocks
        self._aggregation = aggregation or ReputationAggregationPolicy()

    @property
    def is_configured(self) -> bool:
        """True once a deployment has declared at least one trusted feedback client."""
        return bool(self._trusted_clients)

    async def snapshot(
        self, *, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot | None:
        now = self._clock.now()
        if not self._trusted_clients:
            # Fail closed before any I/O: with no declared client, no on-chain feedback
            # is attributable, so the only honest answer is an explicit neutral.
            return self._neutral(
                now=now,
                seller_agent_id=seller_agent_id,
                erc8004_agent_id=erc8004_agent_id,
            )
        requested = self._requested_scope(erc8004_agent_id)
        stored = await self._snapshots.latest_snapshot(
            seller_agent_id=seller_agent_id,
            query_fingerprint=requested.query_fingerprint,
        )
        try:
            answer = await self._query_client.query(
                erc8004_agent_id=erc8004_agent_id,
                trusted_clients=self._trusted_clients,
                lookback_blocks=self._lookback_blocks,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # A failed query is never a favourable score.
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                requested=requested,
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
            latest_block = int(answer["latestBlock"])
            queried_at = datetime.fromisoformat(str(answer["queriedAt"]))
            events = tuple(
                _event_from_payload(item) for item in list(answer.get("events", []))
            )
        except (KeyError, TypeError, ValueError):
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                requested=requested,
            )
        if not self._answers_the_question(
            scope=scope, requested=requested, latest_block=latest_block
        ):
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                requested=requested,
            )
        try:
            snapshot = self._aggregation.aggregate(
                snapshot_id=_snapshot_id(scope=scope, seller_agent_id=seller_agent_id),
                seller_agent_id=seller_agent_id,
                scope=scope,
                queried_at=queried_at,
                events=events,
                evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
                now=now,
            )
        except ValueError:
            # An event that contradicts the window, its own coordinates or its evidence
            # source is not a weaker answer, it is a broken one.
            return await self._fallback(
                stored=stored,
                now=now,
                seller_agent_id=seller_agent_id,
                requested=requested,
            )
        return await self._snapshots.put_snapshot(snapshot)

    def _requested_scope(self, erc8004_agent_id: str) -> ReputationQueryScope:
        """The question this buyer asked, with the window left unresolved."""
        return ReputationQueryScope(
            chain_id=self._chain_id,
            registry_address=self._registry_address,
            erc8004_agent_id=erc8004_agent_id,
            trusted_clients=self._trusted_clients,
            from_block=0,
            to_block=0,
        )

    def _answers_the_question(
        self,
        *,
        scope: ReputationQueryScope,
        requested: ReputationQueryScope,
        latest_block: int,
    ) -> bool:
        """Every field of the answer must be the field that was asked about.

        `query_fingerprint` covers chain, registry, ERC-8004 agent, the trusted-client
        allow-list and the tag pair, so one comparison binds them all. The window is
        checked separately because the gateway resolves it: it must end exactly at the
        head the gateway reports and span at most the requested lookback.
        """
        if scope.query_fingerprint != requested.query_fingerprint:
            return False
        if scope.to_block != latest_block:
            return False
        return scope.resolved_for_lookback(self._lookback_blocks)

    async def _fallback(
        self,
        *,
        stored: ReputationSnapshot | None,
        now: datetime,
        seller_agent_id: str,
        requested: ReputationQueryScope,
    ) -> ReputationSnapshot:
        return self._aggregation.stale_or_neutral(
            snapshot=stored,
            now=now,
            snapshot_id=_snapshot_id(scope=requested, seller_agent_id=seller_agent_id),
            seller_agent_id=seller_agent_id,
            scope=requested,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )

    def _neutral(
        self, *, now: datetime, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot:
        """An explicit `NO_EVIDENCE/50` that never touches the store or the network."""
        scope = ReputationQueryScope(
            chain_id=self._chain_id,
            registry_address=self._registry_address,
            erc8004_agent_id=erc8004_agent_id,
            # A scope needs a non-empty allow-list to be well formed; the unconfigured
            # deployment has none, so the neutral answer names the deployment itself as
            # the client it could not resolve.
            trusted_clients=(UNCONFIGURED_CLIENT,),
            from_block=0,
            to_block=0,
        )
        return self._aggregation.stale_or_neutral(
            snapshot=None,
            now=now,
            snapshot_id=_snapshot_id(scope=scope, seller_agent_id=seller_agent_id),
            seller_agent_id=seller_agent_id,
            scope=scope,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )


# A reserved, non-wallet marker for the unconfigured deployment. It can never match a
# real feedback client, so it cannot accidentally admit an event.
UNCONFIGURED_CLIENT = "0x" + "00" * 20


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
