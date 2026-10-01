from __future__ import annotations

import httpx
from eth_utils import is_address

# ERC-20 balanceOf(address) selector.
_BALANCE_OF = "0x70a08231"


class RpcTokenBalanceReader:
    """Read-only ERC-20 balance lookup over JSON-RPC `eth_call`. It never signs or sends."""

    def __init__(self, *, rpc_url: str, timeout_seconds: float = 5.0) -> None:
        self._rpc_url = rpc_url
        self._timeout = timeout_seconds

    async def balance_of(self, *, token: str, holder: str) -> int:
        if not is_address(token) or not is_address(holder):
            raise ValueError("token and holder must be Ethereum addresses")
        data = _BALANCE_OF + holder.lower().removeprefix("0x").rjust(64, "0")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": token, "data": data}, "latest"],
                },
            )
            response.raise_for_status()
            value = response.json()
        result = value.get("result") if isinstance(value, dict) else None
        if not isinstance(result, str) or not result.startswith("0x"):
            raise ValueError("RPC returned a malformed balanceOf response")
        return int(result, 16)
