from __future__ import annotations

import logging
from os import environ
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
import httpx

from mcpp.config import Config, ExposeEntry
from mcpp.upstream import HttpTransport
from mcpp.keypool import KeyPool
from mcpp.transform import transform_tools, param_transform_value

logger = logging.getLogger("mcpp")
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_CONFIG_PATH = Path("config.yaml")
CONFIG_PATH = Path(environ.get("MCPP_CONFIG", str(DEFAULT_CONFIG_PATH)))


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

        def make_get_auth(name: str):
            """Capture uc.name by value, not by reference."""
            def get_auth():
                kp_inner = app.state.keypools.get(name)
                if kp_inner:
                    try:
                        return f"Bearer {kp_inner.next()}"
                    except RuntimeError:
                        logger.error("Upstream '%s': all keys exhausted", name)
                        return None
                return None
            return get_auth

        t = HttpTransport(
            name=uc.name,
            url=uc.url,
            get_auth=make_get_auth(uc.name) if kp else None,
            connect_timeout=uc.connect_timeout,
            read_timeout=uc.read_timeout,
        )
        app.state.upstreams[uc.name] = t


app = FastAPI(title="mcpp", lifespan=lifespan)


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")
    config: Config = request.app.state.config

    if method == "tools/list":
        all_tools = []
        for uc in config.upstreams:
            transport = request.app.state.upstreams.get(uc.name)
            if transport is None:
                continue
            try:
                tools = await transport.list_tools()
                transformed = transform_tools(uc.name, tools, config)
                all_tools.extend(
                    [t.model_dump(exclude_none=True) for t in transformed]
                )
            except Exception as e:
                logger.warning(
                    "Upstream '%s' failed during tools/list: %s", uc.name, e
                )
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": all_tools},
        })

    if method == "tools/call":
        tool_name = params["name"]
        arguments = params.get("arguments", {})
        entry, upstream_name = _find_expose_entry(config, tool_name)
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
                "serverInfo": {"name": "mcpp", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        })

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


def _find_expose_entry(
    config: Config,
    name_or_key: str,
) -> tuple[Optional[ExposeEntry], Optional[str]]:
    """Find an ExposeEntry by display name or stable key.

    Returns (entry, upstream_name) or (None, None).
    """
    for key, entry in config.expose.items():
        display_name = entry.as_ or key.split("/")[-1]
        if name_or_key in (key, display_name):
            return entry, key.split("/")[0]
    return None, None


# --- Admin API ---

@app.get("/api/config")
async def get_config(request: Request):
    """Return current config as YAML text."""
    config: Config = request.app.state.config
    return JSONResponse({"yaml": config.to_yaml()})


@app.post("/api/config")
async def update_config(request: Request):
    """Accept YAML body, validate, write to disk, and reload."""
    yaml_text = await request.body()
    new_config = Config.from_yaml(yaml_text.decode())
    CONFIG_PATH.write_text(yaml_text.decode())
    logger.info("Config written to %s, reloading", CONFIG_PATH)
    old_upstreams = request.app.state.upstreams
    request.app.state.config = new_config
    _build_upstreams(request.app)
    for t in old_upstreams.values():
        await t.close()
    return JSONResponse({"status": "reloaded"})


@app.get("/api/tools")
async def list_tools_preview(request: Request):
    config: Config = request.app.state.config
    all_tools = []
    for uc in config.upstreams:
        transport = request.app.state.upstreams.get(uc.name)
        if transport is None:
            continue
        try:
            tools = await transport.list_tools()
            transformed = transform_tools(uc.name, tools, config)
            all_tools.extend([t.model_dump(exclude_none=True) for t in transformed])
        except Exception as e:
            logger.warning("Preview: upstream '%s' failed: %s", uc.name, e)
    return JSONResponse(all_tools)


@app.get("/admin")
async def admin_ui():
    return FileResponse(STATIC_DIR / "index.html")


def run():
    import uvicorn
    uvicorn.run("mcpp.main:app", host="127.0.0.1", port=9020, reload=False)
