from __future__ import annotations

import httpx


class JsonRpcTokenBalanceReader:
    def __init__(self, rpc_url: str, *, timeout_seconds: float = 5.0) -> None:
        self._rpc_url = rpc_url
        self._timeout_seconds = timeout_seconds

    async def balance_of(self, *, token: str, wallet: str) -> int:
        normalized_token = token.lower()
        normalized_wallet = wallet.lower()
        if (
            not normalized_token.startswith("0x")
            or len(normalized_token) != 42
            or not normalized_wallet.startswith("0x")
            or len(normalized_wallet) != 42
        ):
            raise ValueError("token or wallet address is malformed")
        data = "0x70a08231" + normalized_wallet[2:].rjust(64, "0")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": normalized_token, "data": data}, "latest"],
                },
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
            raise ValueError("RPC balance response is malformed")
        return int(payload["result"], 16)
