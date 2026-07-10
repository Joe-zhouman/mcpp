from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from os import environ
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, Any

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import httpx
import yaml
from pydantic import ValidationError

from mcpp.config import Config, ExposeEntry, AuthConfig, UpstreamConfig, ParamTransform
from mcpp.upstream import HttpTransport, StdioTransport, Tool
from mcpp.keypool import KeyPool
from mcpp.transform import transform_tools, param_transform_value
from mcpp.clients import REGISTRY, CLIENT_ROUTES, get_format

logger = logging.getLogger("mcpp")
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_CONFIG_PATH = Path("config.yaml")
CONFIG_PATH = Path(environ.get("MCPP_CONFIG", str(DEFAULT_CONFIG_PATH)))


async def _fetch_tools(transport, upstream_name, config, client="default", toolset=None):
    """Fetch and transform tools from one upstream. Returns list of dicts."""
    if transport is None:
        return []
    try:
        tools = await transport.list_tools()
        transformed = transform_tools(
            upstream_name, tools, config, client=client, toolset=toolset
        )
        return [t.model_dump(exclude_none=True) for t in transformed]
    except Exception as e:
        logger.warning("Upstream '%s' failed: %s", upstream_name, e)
        return []


async def _fetch_all_tools(app, config: Config, client="default", toolset=None):
    """Concurrently fetch and transform tools from all upstreams."""
    async def _fetch_one(uc):
        transport = app.state.upstreams.get(uc.name)
        if transport is None:
            return []
        return await _fetch_tools(transport, uc.name, config, client=client, toolset=toolset)
    results = await asyncio.gather(
        *[_fetch_one(uc) for uc in config.upstreams],
        return_exceptions=True,
    )
    all_tools = []
    for r in results:
        if isinstance(r, list):
            all_tools.extend(r)
    return all_tools


def _make_get_auth(app, upstream_name):
    def get_auth():
        kp = app.state.keypools.get(upstream_name)
        if kp:
            try:
                return f"Bearer {kp.next()}"
            except RuntimeError:
                logger.error("Upstream '%s': all keys exhausted", upstream_name)
                return None
        return None
    return get_auth


def _extract_token(request: Request) -> Optional[str]:
    """Pull a bearer token from header, query, or cookie (in that order)."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if "token" in request.query_params:
        return request.query_params["token"]
    return request.cookies.get("mcpp_token")


async def gateway_auth_dep(request: Request) -> None:
    """Require a gateway bearer token when ``config.auth.token`` is set.

    With no token configured the gateway is fully open (preserves prior
    behavior). Comparison uses ``hmac.compare_digest`` to avoid timing leaks.
    Token may arrive via ``Authorization: Bearer``, ``?token=``, or the
    ``mcpp_token`` cookie.
    """
    config: Config = request.app.state.config
    expected = config.auth.token if config.auth else None
    if not expected:
        return
    provided = _extract_token(request)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


# A reusable Depends object so each route just lists ``dependencies=[AUTH]``.
AUTH = [Depends(gateway_auth_dep)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [mcpp] %(message)s")
    logger.info("Loading config from %s", CONFIG_PATH)
    app.state.config = Config.from_file(CONFIG_PATH)
    _build_upstreams(app)
    yield
    for t in app.state.upstreams.values():
        await t.close()


def _build_upstreams(app: FastAPI):
    config: Config = app.state.config
    app.state.upstreams = {}
    app.state.keypools = {}
    for uc in config.upstreams:
        kp = None
        if uc.auth and uc.auth.keys:
            raw_keys = [
                environ.get(k.removeprefix("${").removesuffix("}"), k)
                for k in uc.auth.keys
            ]
            kp = KeyPool(raw_keys)
            app.state.keypools[uc.name] = kp
            logger.info(
                "Upstream '%s': keypool with %d keys", uc.name, len(raw_keys)
            )
        else:
            logger.info("Upstream '%s': no auth", uc.name)

        if uc.transport == "stdio":
            t = StdioTransport(
                name=uc.name,
                command=uc.command,
                args=uc.args or [],
                env=uc.env,
                read_timeout=uc.read_timeout,
            )
            logger.info(
                "Upstream '%s': stdio %s %s",
                uc.name, uc.command, " ".join(uc.args or []),
            )
        else:
            t = HttpTransport(
                name=uc.name,
                url=uc.url,
                get_auth=_make_get_auth(app, uc.name) if kp else None,
                connect_timeout=uc.connect_timeout,
                read_timeout=uc.read_timeout,
            )
        app.state.upstreams[uc.name] = t


async def _persist_and_reload(app, config: Config) -> None:
    """Serialize ``config`` to disk, swap it into app.state, and rebuild upstreams.

    Shared by the YAML, single-entry, and add-server writers. Serializes the
    in-memory object (not caller text) then re-parses for the live state, so
    what runs is exactly what is on disk.
    """
    new_yaml = config.to_yaml()
    CONFIG_PATH.write_text(new_yaml)
    logger.info("Config written to %s, reloading", CONFIG_PATH)
    old_upstreams = app.state.upstreams
    app.state.config = Config.from_yaml(new_yaml)
    _build_upstreams(app)
    for t in old_upstreams.values():
        await t.close()


async def _fetch_all_raw_tools(app) -> dict[tuple[str, str], dict]:
    """Fetch raw (untransformed) tools from every upstream, concurrently.

    Returns a lookup ``(upstream_name, tool_name) -> {name, description, inputSchema}``
    for merging the upstream schema into expose entries in the admin editor.
    Upstream failures are logged and skipped.
    """
    async def _one(uc: UpstreamConfig):
        transport = app.state.upstreams.get(uc.name)
        if transport is None:
            return {}
        try:
            tools = await asyncio.wait_for(transport.list_tools(), timeout=30)
        except Exception as e:
            logger.warning("raw fetch upstream '%s' failed: %s", uc.name, e)
            return {}
        out = {}
        for t in tools:
            out[(uc.name, t.name)] = {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
        return out

    results = await asyncio.gather(*[_one(uc) for uc in app.state.config.upstreams])
    merged: dict[tuple[str, str], dict] = {}
    for r in results:
        merged.update(r)
    return merged


def _merge_params(entry: ExposeEntry, upstream_schema: Optional[dict]) -> list[dict]:
    """Merge an ExposeEntry's param transforms with the upstream tool's schema.

    Emits one row per upstream parameter (plus any entry.params that no longer
    matches an upstream param — kept so edits aren't lost). Each row carries
    the editable transform fields plus read-only upstream context.
    """
    upstream_schema = upstream_schema or {}
    props: dict[str, Any] = upstream_schema.get("properties", {}) or {}
    required: list[str] = upstream_schema.get("required", []) or []

    # Index the entry's transforms by their effective upstream param name.
    by_upstream: dict[str, "ParamTransform"] = {}
    for p in entry.params or []:
        by_upstream[p.map_from or p.name] = p

    rows: list[dict] = []
    seen: set[str] = set()
    for up_name, up_prop in props.items():
        seen.add(up_name)
        p = by_upstream.get(up_name)
        if p is None:
            # upstream param not yet transformed — show defaults
            rows.append({
                "name": up_name, "map_from": None, "hidden": False,
                "default": None, "type": None, "mapping": None, "preset": None,
                "_upstream_name": up_name,
                "_upstream_type": up_prop.get("type"),
                "_upstream_description": up_prop.get("description", ""),
                "_required": up_name in required,
            })
        else:
            rows.append({
                "name": p.name, "map_from": p.map_from, "hidden": p.hidden,
                "default": p.default, "type": p.type, "mapping": p.mapping, "preset": p.preset,
                "_upstream_name": up_name,
                "_upstream_type": up_prop.get("type"),
                "_upstream_description": up_prop.get("description", ""),
                "_required": up_name in required,
            })

    # Preserve transforms whose upstream param no longer exists (orphaned),
    # so the user can still see/edit them rather than silently dropping.
    for up_name, p in by_upstream.items():
        if up_name in seen:
            continue
        rows.append({
            "name": p.name, "map_from": p.map_from, "hidden": p.hidden,
            "default": p.default, "type": p.type, "mapping": p.mapping, "preset": p.preset,
            "_upstream_name": up_name,
            "_upstream_type": None,
            "_upstream_description": "",
            "_required": False,
        })
    return rows


app = FastAPI(title="mcpp", lifespan=lifespan)


def _known_toolsets(config: Config) -> set[str]:
    """All toolset names actually used by expose entries, plus the default."""
    names = {entry.toolset or config.server_name for entry in config.expose.values()}
    names.add(config.server_name)
    return names


def _resolve_toolset(config: Config, toolset: str) -> Optional[str]:
    """Normalize a URL toolset segment to a real toolset name, or None if unknown.

    The default toolset is reachable under both ``config.server_name`` and the
    literal alias ``mcpp`` (so users can always reach it even if server_name
    was customized).
    """
    known = _known_toolsets(config)
    if toolset in known:
        return toolset
    if toolset == "mcpp":
        return config.server_name
    return None


def _make_mcp_handler(client_id: str):
    """Build a JSON-RPC MCP endpoint handler for one client format.

    Routes are registered as ``/{toolset}/{client}/mcp``; the handler reads the
    toolset from the path, validates it against the configured toolsets, and
    serves only the tools belonging to that aggregated MCP server — formatted
    per ``client_id``.
    """

    async def handler(request: Request):
        config: Config = request.app.state.config
        # Per-client disable guard: config.clients.{id} == False => 404.
        if config.clients and config.clients.get(client_id, True) is False:
            return JSONResponse(
                {"error": f"client '{client_id}' endpoint disabled"}, status_code=404
            )

        raw_toolset = request.path_params.get("toolset", config.server_name)
        toolset = _resolve_toolset(config, raw_toolset)
        if toolset is None:
            return JSONResponse(
                {"error": f"Unknown toolset: {raw_toolset}"}, status_code=404
            )

        body = await request.json()
        if body is None:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: body must be a JSON object"},
            })
        method = body.get("method")
        params = body.get("params", {})
        req_id = body.get("id")

        if method == "tools/list":
            all_tools = await _fetch_all_tools(
                request.app, config, client=client_id, toolset=toolset
            )
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": all_tools},
            })

        if method == "tools/call":
            tool_name = params.get("name")
            if not tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Missing required parameter: name"},
                })
            arguments = params.get("arguments", {})
            entry, upstream_name = _find_expose_entry(
                config, tool_name, client=client_id, toolset=toolset
            )
            if entry is None:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                })
            upstream_args = param_transform_value(arguments, entry)
            transport = request.app.state.upstreams.get(upstream_name)
            if transport is None:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": f"[gateway] upstream={upstream_name}: no transport",
                    },
                })
            try:
                result = await transport.call_tool(entry.tool, upstream_args)
                logger.info(
                    "tools/call %s -> upstream %s/%s",
                    tool_name, upstream_name, entry.tool,
                )
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": result,
                })
            except httpx.HTTPStatusError as e:
                kp = request.app.state.keypools.get(upstream_name)
                if kp and e.response.status_code in (401, 403, 429):
                    kp.mark_bad(kp.current)
                    logger.warning(
                        "Upstream '%s': key marked bad after %d, %d healthy remaining",
                        upstream_name, e.response.status_code, kp.healthy_count,
                    )
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": (
                            f"[gateway] upstream={upstream_name}: "
                            f"HTTP {e.response.status_code}"
                        ),
                    },
                })
            except Exception as e:
                logger.error("Upstream '%s' call_tool error: %s", upstream_name, e)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": f"[gateway] upstream={upstream_name}: {e}",
                    },
                })

        if method == "server/discover":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": toolset, "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            })

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        })

    return handler


# Register one MCP endpoint per client format at /{toolset}/{client}/mcp.
# Adding a new client = one entry in clients.CLIENT_ROUTES + REGISTRY.
# toolset is a dynamic path segment resolved per-request against config.
for _cid in CLIENT_ROUTES:
    app.add_api_route(
        "/{toolset}/" + CLIENT_ROUTES[_cid].lstrip("/"),
        _make_mcp_handler(_cid),
        methods=["POST"],
        dependencies=AUTH,
    )


def _find_expose_entry(
    config: Config,
    name_or_key: str,
    client: str = "default",
    toolset: Optional[str] = None,
) -> tuple[Optional[ExposeEntry], Optional[str]]:
    """Find an ExposeEntry by formatted name, display name, or stable key.

    Returns (entry, upstream_name) or (None, None).

    ``toolset`` (when given) restricts the search to entries belonging to that
    aggregated MCP server (entry.toolset or config.server_name). The incoming
    name on a client endpoint is client-formatted (e.g. ``mcp__search__zhihu``);
    the strategy extracts the display name, which we then match against the
    expose table within the toolset. Falls back to bare key/display matching so
    the default client and existing callers are unaffected.
    """
    fmt = get_format(client)

    def _in_toolset(entry: ExposeEntry) -> bool:
        if toolset is None:
            return True
        return (entry.toolset or config.server_name) == toolset

    # Try client-formatted parse: strips the server prefix, leaves display name.
    if client != "default":
        display = fmt.parse_name(config.server_name if toolset is None else toolset, name_or_key)
        # The format prefix uses the resolved toolset name; if that didn't match
        # (e.g. server_name differs from the URL toolset), retry with the other.
        if display is None and toolset is not None and toolset != config.server_name:
            display = fmt.parse_name(config.server_name, name_or_key)
        if display is not None:
            for key, entry in config.expose.items():
                if entry.hide or not _in_toolset(entry):
                    continue
                if entry.display_name(key) == display:
                    return entry, key.split("/")[0]

    # Default / fallback: match by stable key or display name within toolset.
    for key, entry in config.expose.items():
        if not _in_toolset(entry):
            continue
        display_name = entry.display_name(key)
        if name_or_key in (key, display_name):
            return entry, key.split("/")[0]
    return None, None


# --- Admin API ---

@app.get("/api/config", dependencies=AUTH)
async def get_config(request: Request):
    """Return current config as YAML text."""
    config: Config = request.app.state.config
    return JSONResponse({"yaml": config.to_yaml()})


@app.post("/api/config", dependencies=AUTH)
async def update_config(request: Request):
    """Accept YAML body, validate, write to disk, and reload."""
    yaml_text = await request.body()
    try:
        new_config = Config.from_yaml(yaml_text.decode())
    except (UnicodeDecodeError, ValueError, ValidationError, yaml.YAMLError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    await _persist_and_reload(request.app, new_config)
    return JSONResponse({"status": "reloaded"})


@app.get("/api/tools", dependencies=AUTH)
async def list_tools_preview(request: Request):
    config: Config = request.app.state.config
    all_tools = await _fetch_all_tools(request.app, config)
    return JSONResponse(all_tools)


@app.get("/api/upstreams", dependencies=AUTH)
async def list_upstreams(request: Request):
    """Return the names of all configured upstreams."""
    config: Config = request.app.state.config
    return JSONResponse([uc.name for uc in config.upstreams])


@app.get("/api/toolsets", dependencies=AUTH)
async def list_toolsets(request: Request):
    """Return all aggregated-MCP-server (toolset) names in use, with member tools.

    Each toolset becomes one对外 MCP server reachable at ``/<toolset>/<client>/mcp``.
    The default toolset (config.server_name) is always present.
    """
    config: Config = request.app.state.config
    groups: dict[str, list[str]] = {config.server_name: []}
    for key, entry in config.expose.items():
        if entry.hide:
            continue
        name = entry.toolset or config.server_name
        groups.setdefault(name, []).append(key)
    return JSONResponse([
        {"toolset": name, "tools": keys, "default": name == config.server_name}
        for name, keys in groups.items()
    ])


@app.get("/api/upstreams/{name}/tools", dependencies=AUTH)
async def raw_upstream_tools(name: str, request: Request):
    """Return RAW upstream tools (no transform) for the description-editor picker."""
    transport = request.app.state.upstreams.get(name)
    if transport is None:
        return JSONResponse({"error": f"Unknown upstream: {name}"}, status_code=404)
    try:
        tools = await transport.list_tools()
        return JSONResponse({
            "upstream": name,
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in tools
            ],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/expose", dependencies=AUTH)
async def list_expose(request: Request):
    """Return all expose entries with the upstream schema merged in.

    Each entry carries editable fields (as, toolset, hide, description, params)
    plus read-only upstream context (raw_description, per-param _upstream_*).
    The frontend renders one form card per entry from this payload.
    """
    config: Config = request.app.state.config
    raw = await _fetch_all_raw_tools(request.app)
    out = []
    for key, entry in config.expose.items():
        rt = raw.get((entry.upstream, entry.tool), {})
        out.append({
            "key": key,
            "upstream": entry.upstream,
            "tool": entry.tool,
            "as": entry.as_,
            "toolset": entry.toolset or config.server_name,
            "hide": entry.hide,
            "description": entry.description,
            "raw_description": rt.get("description"),
            "params": _merge_params(entry, rt.get("inputSchema")),
        })
    return JSONResponse(out)


@app.put("/api/expose/{key:path}", dependencies=AUTH)
async def put_expose(key: str, request: Request):
    """Update a single expose entry from a structured JSON body.

    The body is an ExposeEntry (with ``as`` alias). upstream/tool are identity
    fields — they're forced back to the existing values, so clients can't
    repoint an entry at a different upstream tool via this endpoint.
    """
    config: Config = request.app.state.config
    # URL-decoded key must exist. (FastAPI decodes path params already.)
    if key not in config.expose:
        return JSONResponse({"error": f"Unknown expose key: {key}"}, status_code=404)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    # Strip client-only read-only fields the GET emits (prefixed _).
    body = {k: v for k, v in body.items() if not k.startswith("_")}
    # Force identity fields to existing values.
    body["upstream"] = config.expose[key].upstream
    body["tool"] = config.expose[key].tool
    try:
        entry = ExposeEntry.model_validate(body)
        # Stage, validate refs against the staged config, then persist.
        config.expose[key] = entry
        config.validate_refs()
    except (ValidationError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    await _persist_and_reload(request.app, config)
    return JSONResponse({"status": "saved", "key": key})


@app.delete("/api/expose/{key:path}", dependencies=AUTH)
async def delete_expose(key: str, request: Request):
    """Delete a single expose entry.

    Refuses with 409 if other entries' descriptions backtick-reference this
    tool — the user must remove those refs first (otherwise the reload would
    fail validate_refs).
    """
    config: Config = request.app.state.config
    if key not in config.expose:
        return JSONResponse({"error": f"Unknown expose key: {key}"}, status_code=404)
    referrers = [
        other for other, e in config.expose.items()
        if other != key and e.description and f"`{key}`" in e.description
    ]
    if referrers:
        return JSONResponse({
            "error": f"Cannot delete '{key}': referenced by {referrers}. "
                     f"Remove those backtick refs first.",
        }, status_code=409)
    del config.expose[key]
    await _persist_and_reload(request.app, config)
    return JSONResponse({"status": "deleted", "key": key})


@app.post("/api/upstreams/{name}/test", dependencies=AUTH)
async def test_upstream(name: str, request: Request):
    """Probe one upstream's connectivity by calling list_tools directly.

    Returns ok/latency/tool_count plus the raw tool names — no transform layer.
    """
    transport = request.app.state.upstreams.get(name)
    if transport is None:
        return JSONResponse({"error": f"Unknown upstream: {name}"}, status_code=404)
    t0 = time.perf_counter()
    try:
        tools = await asyncio.wait_for(transport.list_tools(), timeout=30)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({
            "ok": True,
            "latency_ms": latency_ms,
            "tool_count": len(tools),
            "tools": [{"name": t.name, "description": t.description} for t in tools],
            "error": None,
        })
    except Exception as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.warning("Test upstream '%s' failed: %s", name, e)
        return JSONResponse({
            "ok": False,
            "latency_ms": latency_ms,
            "tool_count": 0,
            "tools": [],
            "error": str(e),
        })


@app.post("/api/add-server", dependencies=AUTH)
async def add_server(request: Request):
    """Add MCP server(s) from Claude-Desktop-style JSON.

    Body: ``{"json_text": "..."}`` where the JSON maps server names to specs:
    ``{"name": {"command": ..., "args": [...]} | {"url": ..., "headers": {...}}}``.

    For each server: parse to an UpstreamConfig, fetch its tool list via a
    throwaway transport, and generate one passthrough ExposeEntry per tool.
    Merges into the in-memory config, persists to disk, and rebuilds upstreams.
    At least one server must succeed; per-server errors are returned.
    """
    body = await request.json()
    raw_text = body.get("json_text", "")
    # Optional toolset: when set, all added tools are assigned to this aggregated
    # MCP server. When omitted, tools land in the default toolset (server_name).
    toolset = body.get("toolset") or None
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
    if not isinstance(raw, dict):
        return JSONResponse({"error": "Top-level JSON must be an object"}, status_code=400)

    # Tolerate the full Claude-Desktop file shape: {"mcpServers": {...}}.
    # Unwrap it so users can paste either the bare server map or the whole file.
    if "mcpServers" in raw and isinstance(raw["mcpServers"], dict):
        raw = raw["mcpServers"]

    config: Config = request.app.state.config
    added: list[dict] = []
    errors: list[dict] = []

    # Index existing upstreams/expose by name/key for dedupe.
    existing_upstreams = {uc.name: uc for uc in config.upstreams}

    for server_name, spec in raw.items():
        try:
            if not isinstance(spec, dict):
                raise ValueError("server spec must be an object")
            uc, keys = _parse_claude_desktop_entry(server_name, spec)

            # Probe the server with a throwaway transport to auto-expose tools.
            try:
                raw_tools = await asyncio.wait_for(
                    _probe_tools(uc, keys), timeout=30
                )
            except Exception as e:
                raise ValueError(f"failed to fetch tools: {e}") from e

            # Append upstream (dedupe by name — skip if already present).
            if server_name not in existing_upstreams:
                config.upstreams.append(uc)
                existing_upstreams[server_name] = uc

            # Generate passthrough expose entries for each tool.
            new_tools: list[str] = []
            for tname in raw_tools:
                key = f"{server_name}/{tname}"
                if key in config.expose:
                    continue
                config.expose[key] = ExposeEntry(
                    upstream=server_name, tool=tname, toolset=toolset
                )
                new_tools.append(tname)

            added.append({"server": server_name, "toolset": toolset, "tools": new_tools})
            logger.info(
                "Added server '%s' via JSON (%d tools, toolset=%s)",
                server_name, len(new_tools), toolset,
            )
        except Exception as e:
            errors.append({"server": server_name, "error": str(e)})

    if not added:
        return JSONResponse(
            {"added": [], "errors": errors or [{"server": "*", "error": "no servers added"}]},
            status_code=400,
        )

    # Persist merged config and rebuild upstreams.
    new_yaml = config.to_yaml()
    CONFIG_PATH.write_text(new_yaml)
    logger.info("Config written to %s after add-server", CONFIG_PATH)
    request.app.state.config = Config.from_yaml(new_yaml)
    _build_upstreams(request.app)
    return JSONResponse({"added": added, "errors": errors})


def _parse_claude_desktop_entry(name: str, spec: dict) -> tuple[UpstreamConfig, list[str]]:
    """Parse one Claude-Desktop-style server spec into (UpstreamConfig, keys).

    - ``command`` present  => stdio upstream (command/args/env)
    - ``url`` present      => http upstream
    - ``headers.Authorization: Bearer <k>`` => stripped into AuthConfig.keys
    """
    if "command" in spec:
        uc = UpstreamConfig(
            name=name,
            transport="stdio",
            command=spec["command"],
            args=spec.get("args"),
            env=spec.get("env"),
        )
    elif "url" in spec:
        uc = UpstreamConfig(name=name, transport="http", url=spec["url"])
    else:
        raise ValueError(f"server '{name}': spec must have 'command' or 'url'")

    keys: list[str] = []
    headers = spec.get("headers", {}) or {}
    authz = headers.get("Authorization", "")
    if isinstance(authz, str) and authz.startswith("Bearer "):
        token = authz[len("Bearer "):].strip()
        if token:
            keys.append(token)
    if keys:
        uc.auth = AuthConfig(keys=keys)
    return uc, keys


async def _probe_tools(uc: UpstreamConfig, keys: list[str]) -> list[str]:
    """Build a throwaway transport for ``uc`` and return raw tool names."""
    if uc.transport == "stdio":
        transport: HttpTransport | StdioTransport = StdioTransport(
            name=uc.name, command=uc.command, args=uc.args or [], env=uc.env,
            read_timeout=uc.read_timeout,
        )
    else:
        get_auth = None
        if keys:

            def get_auth(_keys=keys):
                return f"Bearer {_keys[0]}"

        transport = HttpTransport(
            name=uc.name, url=uc.url, get_auth=get_auth,
            connect_timeout=uc.connect_timeout, read_timeout=uc.read_timeout,
        )
    try:
        tools = await transport.list_tools()
        return [t.name for t in tools]
    finally:
        await transport.close()


@app.get("/api/keys", dependencies=AUTH)
async def list_keys(request: Request):
    """Return key pool statuses for all upstreams."""
    result = {}
    for name, kp in request.app.state.keypools.items():
        result[name] = {
            "healthy_count": kp.healthy_count,
            "keys": kp.statuses(),
        }
    return JSONResponse(result)


@app.post("/api/keys/{upstream_name}/resume", dependencies=AUTH)
async def resume_key(upstream_name: str, request: Request):
    """Resume a paused key by index."""
    body = await request.json()
    key_index = body.get("key_index", 0)
    kp = request.app.state.keypools.get(upstream_name)
    if not kp:
        return JSONResponse(
            {"error": "No keypool for this upstream"}, status_code=404
        )
    key = kp.key_at(key_index)
    kp.resume(key)
    logger.info(
        "Upstream '%s': key %d resumed manually", upstream_name, key_index
    )
    return JSONResponse({"status": "resumed", "key": key[:4] + "..."})


@app.get("/admin", dependencies=AUTH)
async def admin_ui():
    return FileResponse(STATIC_DIR / "index.html")


def run():
    import uvicorn
    host = environ.get("MCPP_HOST", "0.0.0.0")
    try:
        port = int(environ.get("MCPP_PORT", "9020"))
    except ValueError:
        port = 9020
    # Warn loudly about the open-bind + no-auth footgun before serving.
    _config = Config.from_file(CONFIG_PATH)
    if host != "127.0.0.1" and not (_config.auth and _config.auth.token):
        logger.warning(
            "Binding to %s with NO gateway auth token — anyone on the network "
            "can access /admin and call your MCP tools. Set auth.token in %s "
            "or bind 127.0.0.1 via MCPP_HOST.",
            host, CONFIG_PATH,
        )
    uvicorn.run("mcpp.main:app", host=host, port=port, reload=False)
