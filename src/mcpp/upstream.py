from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import Protocol, Callable, Optional
import httpx
from pydantic import BaseModel

logger = logging.getLogger("mcpp")


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
            data = resp.json()
            if "error" in data:
                raise RuntimeError(
                    f"Upstream '{self.name}' JSON-RPC error: "
                    f"{data['error'].get('message', 'unknown')}"
                )
            return data
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


class StdioTransport:
    """Stdio upstream — MCP servers launched as subprocesses.

    Communicates via JSON-RPC over stdin/stdout. Supports both
    newline-delimited JSON and Content-Length-prefixed message formats.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        read_timeout: int = 120,
    ) -> None:
        self.name = name
        self._command = command
        self._args = args or []
        self._extra_env = env or {}
        self._read_timeout = read_timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0

    async def _ensure_process(self):
        if self._process is None or self._process.returncode is not None:
            merged_env = {**os.environ, **self._extra_env}
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
            logger.info(
                "Stdio upstream '%s': started %s %s (pid %d)",
                self.name,
                self._command,
                " ".join(self._args),
                self._process.pid,
            )

    async def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        await self._ensure_process()
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        # Read response: try Content-Length header first, then fallback to line-delimited
        try:
            raw = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self._read_timeout,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Stdio upstream '{self.name}': read timeout after {self._read_timeout}s"
            )
        if not raw:
            stderr_data = b""
            try:
                stderr_data = await self._process.stderr.read()
            except Exception:
                pass
            raise RuntimeError(
                f"Stdio upstream '{self.name}': process exited with code "
                f"{self._process.returncode}. stderr: {stderr_data.decode(errors='replace')[:500]}"
            )
        data = json.loads(raw.decode())
        if "error" in data:
            raise RuntimeError(
                f"Upstream '{self.name}' JSON-RPC error: "
                f"{data['error'].get('message', 'unknown')}"
            )
        return data

    async def list_tools(self) -> list[Tool]:
        result = await self._rpc("tools/list")
        return [Tool(**t) for t in result.get("result", {}).get("tools", [])]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        return result.get("result", {})

    async def close(self) -> None:
        if self._process and self._process.returncode is None:
            logger.info(
                "Stdio upstream '%s': terminating pid %d",
                self.name,
                self._process.pid,
            )
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning(
                    "Stdio upstream '%s': kill after timeout", self.name
                )
                self._process.kill()
                await self._process.wait()
