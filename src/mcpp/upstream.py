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


def _parse_sse_json(text: str) -> Optional[dict]:
    """Extract the first JSON-RPC object from an SSE stream body.

    Handles both single-line and multi-line ``data:`` payloads (the JSON is
    the concatenation of consecutive data lines per event). Returns the first
    parsed object that looks like JSON-RPC, or None.
    """
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.strip() == "" and data_lines:
            # event boundary — try to parse accumulated data
            blob = "\n".join(data_lines)
            try:
                obj = json.loads(blob)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            data_lines = []
    # flush trailing event (no final blank line)
    if data_lines:
        try:
            obj = json.loads("\n".join(data_lines))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


class HttpTransport:
    """HTTP upstream — Streamable HTTP MCP endpoints.

    Handles both single-response (``application/json``) and SSE-streamed
    (``text/event-stream``) JSON-RPC replies. Endpoint path is auto-resolved:
    if the configured url already contains an ``/mcp`` segment it is used
    verbatim (supports token-in-path URLs), otherwise ``/mcp`` is appended.
    Per-request auth via ``get_auth`` — a fresh header on every _rpc, so key
    pool rotation takes effect immediately.
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
        self._session_id: Optional[str] = None
        self._request_id = 0

    def _headers(self, with_session: bool = True) -> dict[str, str]:
        # MCP Streamable HTTP: client must accept both the single JSON-RPC
        # response and the SSE stream forms. Some servers 406 without this.
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if with_session and self._session_id:
            h["MCP-Session-Id"] = self._session_id
        if self._get_auth:
            header = self._get_auth()
            if header:
                h["Authorization"] = header
        return h

    def _endpoint(self) -> str:
        """Resolve the MCP endpoint URL.

        If the configured url path already contains an ``/mcp`` segment
        (e.g. ``host/mcp`` Streamable-HTTP, or ``host/mcp/<token>`` with an
        embedded token), use it verbatim. Otherwise append ``/mcp``.
        """
        path = self.url.rstrip("/")
        if "/mcp" in path:
            return path
        return f"{path}/mcp"

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _ensure_session(self) -> None:
        """Run the MCP initialize handshake once, caching the session id.

        Streamable-HTTP servers require: initialize → (capture session id) →
        notifications/initialized → then real requests carry MCP-Session-Id.
        """
        if self._session_id is not None:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "mcpp", "version": "0.1.0"},
            },
        }
        resp = await self._client.post(
            self._endpoint(), json=payload, headers=self._headers(with_session=False)
        )
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                message=f"Upstream '{self.name}' initialize returned {resp.status_code}",
                request=resp.request, response=resp,
            )
        sid = resp.headers.get("mcp-session-id")
        if not sid:
            # Server doesn't use sessions — proceed stateless.
            return
        self._session_id = sid
        # Send the initialized notification to complete the handshake.
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        await self._client.post(
            self._endpoint(), json=notif, headers=self._headers()
        )

    async def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        # Lazy session handshake; reset on 404/invalid-session and retry once.
        for attempt in range(2):
            await self._ensure_session()
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": method,
                "params": params or {},
            }
            resp = await self._client.post(
                self._endpoint(), json=payload, headers=self._headers()
            )
            # Session expired / not found — drop it and retry once.
            if resp.status_code in (404,) and attempt == 0 and self._session_id:
                self._session_id = None
                continue
            break
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                message=f"Upstream '{self.name}' returned {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            data = resp.json()
        elif ctype.startswith("text/event-stream"):
            # MCP Streamable HTTP: JSON-RPC payload arrives as an SSE event.
            data = _parse_sse_json(resp.text)
            if data is None:
                raise RuntimeError(
                    f"Upstream '{self.name}': SSE stream had no JSON-RPC response"
                )
        else:
            # Try JSON anyway; some servers omit a proper content-type.
            try:
                data = resp.json()
            except Exception:
                raise httpx.HTTPStatusError(
                    message=(
                        f"Upstream '{self.name}': unsupported content-type {ctype!r}"
                    ),
                    request=resp.request,
                    response=resp,
                )
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
