from __future__ import annotations
from typing import Protocol, Callable, Optional
import httpx
from pydantic import BaseModel


class Tool(BaseModel):
    name: str
    description: Optional[str] = None
    inputSchema: Optional[dict] = None
    annotations: Optional[dict] = None
    title: Optional[str] = None


class UpstreamTransport(Protocol):
    """Protocol for upstream MCP connections. Add StdioTransport in v2."""
    name: str

    async def list_tools(self) -> list[Tool]: ...
    async def call_tool(self, name: str, arguments: dict) -> dict: ...
    async def close(self) -> None: ...


GetAuth = Callable[[], Optional[str]]


class HttpTransport:
    """HTTP upstream — Streamable HTTP MCP endpoints.

    SSE fallback deferred to v2: returns a clear error for non-Streamable endpoints.
    Per-request auth via `get_auth` callback — callers provide a fresh header on every
    _rpc, so key pool rotation takes effect immediately.
    """

    def __init__(
        self,
        name: str,
        url: str,
        get_auth: Optional[GetAuth] = None,
        connect_timeout: int = 30,
        read_timeout: int = 120,
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self._get_auth = get_auth
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect_timeout, read=read_timeout),
        )

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._get_auth:
            header = self._get_auth()
            if header:
                h["Authorization"] = header
        return h

    async def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        resp = await self._client.post(
            f"{self.url}/mcp",
            json=payload,
            headers=self._headers(),
        )
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith(
            "application/json"
        ):
            return resp.json()
        raise httpx.HTTPStatusError(
            message=f"Upstream '{self.name}' returned {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    async def list_tools(self) -> list[Tool]:
        result = await self._rpc("tools/list")
        return [Tool(**t) for t in result.get("result", {}).get("tools", [])]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        return result.get("result", {})

    async def close(self) -> None:
        await self._client.aclose()
