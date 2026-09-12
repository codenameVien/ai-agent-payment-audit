from __future__ import annotations

from typing import Any

import httpx


class HttpCommerceGatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout_seconds

    async def execute(self, *, purchase_id: str, resource_body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/execute",
                headers={"authorization": f"Bearer {self._service_token}"},
                json={"purchaseId": purchase_id, "resourceBody": resource_body},
            )
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict):
            raise ValueError("commerce gateway returned a malformed response")
        return value

    async def checkpoint(self, *, purchase_id: str, phase: str) -> dict[str, Any]:
        """Ask the protected gateway to anchor the frozen Evidence API prefix."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/evidence-checkpoints",
                headers={"authorization": f"Bearer {self._service_token}"},
                json={"purchaseId": purchase_id, "phase": phase},
            )
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict):
            raise ValueError("commerce gateway returned a malformed checkpoint response")
        return value
