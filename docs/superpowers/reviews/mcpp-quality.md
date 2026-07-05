## Code Quality Review — mcpp initial commit

### Strengths

1. **Clean separation of concerns** — config models (`config.py:13-46`), key rotation (`keypool.py:4-47`), HTTP transport (`upstream.py:27-91`), param transformation (`transform.py:72-96`), and the FastAPI app (`main.py`) each own one responsibility. A new contributor can read any file in isolation and understand it.

2. **Backtick ref validation at load time** (`config.py:55-74`) — catches missing or hidden cross-tool references in descriptions before they become silent display bugs. This is the kind of up-front validation that saves debugging hours.

3. **KeyPool is simple and testable** — `next()`, `mark_bad()`, `resume()`, `healthy_count`, `statuses()` cover all operations needed. The round-robin with health-based pause is exactly what a proxy gateway needs and no more.

4. **Transform module handles the full param matrix** — rename (`map_from`), hidden defaults, enum mapping, and preset expansion all compose correctly (`transform.py:72-96`). The `upstream |= p.preset[value]` merge for presets is the right call.

5. **Test quality is above average for an initial commit** — `test_transform.py:61-66` verifies backtick refs resolve to display names, not stable keys. `test_transform.py:78-83` verifies optional params with defaults are NOT in `required`. `test_upstream.py:28-46` tests per-request auth with `httpx_mock`. These test real behavior, not mocks of mocks.

6. **Closure capture is correct** (`main.py:56-67`) — `make_get_auth(name)` takes `name` by value, so the inner `get_auth` doesn't suffer the classic loop-closure bug. Worth calling out because most people get this wrong.

7. **`_find_expose_entry` supports dual lookup** (`main.py:207-210`) — by stable key OR display name. This is the right design: clients refer to tools by display name, but internal routing uses the stable key.

8. **Admin UI is zero-dependency** — single static HTML with vanilla JS, no build step, no npm. Refreshingly pragmatic (`index.html:48-123`).

### Issues

#### Critical (Must Fix)

1. **`src/mcpp/main.py:119` — `params["name"]` raises KeyError on missing params** — When `method == "tools/call"`, the code does `tool_name = params["name"]`. But `params` is `body.get("params", {})` (line 86), so a valid JSON-RPC request that omits the optional `params` field, or sends `params: {}`, will crash with an unhandled `KeyError`, producing an HTTP 500 instead of a proper JSON-RPC error response.
   - **Fix**: Use `params.get("name")`, check for `None`, and return a JSON-RPC error with code `-32602` (invalid params):
     ```python
     tool_name = params.get("name")
     if not tool_name:
         return JSONResponse({
             "jsonrpc": "2.0", "id": req_id,
             "error": {"code": -32602, "message": "Missing required parameter: name"},
         })
     ```

2. **`src/mcpp/main.py:227` — `Config.from_yaml(yaml_text.decode())` can crash the admin API with 500** — Two unhandled failure modes:
   - `yaml_text.decode()` raises `UnicodeDecodeError` if the POST body is not UTF-8.
   - `Config.from_yaml()` raises `ValueError` from `validate_refs` or `pydantic.ValidationError` from `model_validate`.
   Either propagates as a 500, which is the worst possible response for an admin endpoint that should guide the user to fix their input.
   - **Fix**: Wrap in try/except, return HTTP 400 with a clear message:
     ```python
     try:
         new_config = Config.from_yaml(yaml_text.decode())
     except (UnicodeDecodeError, ValueError, pydantic.ValidationError) as e:
         return JSONResponse({"error": str(e)}, status_code=400)
     ```

#### Important (Should Fix)

1. **`src/mcpp/main.py:91-103` and `src/mcpp/main.py:242-252` — `fetch_one` is duplicated verbatim** — The async function that fetches tools from an upstream transport and transforms them is written twice (once in `mcp_endpoint` for the `tools/list` handler, once in `list_tools_preview`). Identical logic, two copies.
   - **Fix**: Extract to a module-level or app-state-attached helper:
     ```python
     async def _fetch_tools(app, config, upstream_cfg):
         transport = app.state.upstreams.get(upstream_cfg.name)
         if transport is None:
             return []
         try:
             tools = await transport.list_tools()
             transformed = transform_tools(upstream_cfg.name, tools, config)
             return [t.model_dump(exclude_none=True) for t in transformed]
         except Exception as e:
             logger.warning("Upstream '%s' failed: %s", upstream_cfg.name, e)
             return []
     ```

2. **`src/mcpp/main.py:287` — `kp._keys[key_index]` accesses a private attribute** — The `resume_key` endpoint reaches into `KeyPool._keys` to get the key by index. This breaks encapsulation — `_keys` is private by convention.
   - **Fix**: Add a public `key_at(self, index: int) -> str` method to `KeyPool`, or make `statuses()` return the full key (since it only needs to be masked for display, not storage).

3. **`src/mcpp/main.py:300-302` — `run()` hardcodes port 9020 and 127.0.0.1** — No way to configure host/port without editing source. The project already has a config file and env vars (`MCPP_CONFIG`), so this is inconsistent.
   - **Fix**: Read `MCPP_HOST` and `MCPP_PORT` from environment with fallbacks.

4. **`src/mcpp/main.py:58-67` — `make_get_auth` defined inside the loop every iteration** — Named function `make_get_auth` is redefined each iteration of the `for uc in config.upstreams` loop. While technically correct (the closure captures `name` by value via the parameter), it's unconventional and confuses code flow.
   - **Fix**: Extract `make_get_auth` to a module-level function that takes `app` and `name` as parameters.

5. **`src/mcpp/keypool.py:43-47` — `current` property has a surprising edge case** — The `current` property returns `self._keys[(self._idx - 1) % len]`. Before any `next()` call, `_idx` is 0, so `current` returns the **last** key in the pool, not the first. If `mark_bad` is ever called defensively before the first `next()`, the wrong key gets paused.
   - No tests cover `current`.
   - **Fix**: Either document this behavior explicitly, or raise `RuntimeError` if `current` is read before any `next()` call, or initialize `_idx = -1` and handle the edge case.

6. **`tests/test_keypool.py` no coverage for `current` property or `statuses()`** — `current` has a subtle edge case (see above) and `statuses()` returns masked keys for admin display. Neither is tested.

#### Minor (Nice to Have)

1. **`src/mcpp/config.py:10` — `BACKTICK_REF` pattern is fragile** — `r"`(\S+)"` works because of regex backtracking, but `\S+` is overly broad. A backtick ref like `` `gh/code`. `` (period immediately after closing backtick) is parsed correctly only because the engine backtracks. A stricter pattern like `` r"`([\w/]+)`" `` would be more intention-revealing and avoid depending on backtracking behavior.

2. **`pyproject.toml:9` — `mcp>=1.0.0` dependency is never imported** — The `mcp` SDK package is listed as a dependency but not used anywhere in the codebase. The HTTP transport writes JSON-RPC directly. Either remove the dependency or add a comment explaining its intended use (e.g., "for StdioTransport in v2").

3. **`src/mcpp/main.py:208` — `name_or_key in (key, display_name)` constructs a tuple per iteration** — Minor allocation overhead for each expose entry on each lookup. Not a problem for typical config sizes (<100 entries), but worth noting if scale grows.

4. **`src/mcpp/config.py:82` — `sort_keys=False` in `to_yaml`** — YAML output ordering depends on Python dict insertion order. This works on CPython 3.7+ but means the output isn't deterministic across Python implementations. If the admin UI round-trips config through this method, the output could reorder unpredictably. Consider using `sort_keys=True` or an explicit field order.

5. **`src/mcpp/main.py:26-27` — `CONFIG_PATH` is a module-level `Path` evaluated at import time** — If the working directory changes between import and runtime, `config.yaml` (the fallback) resolves relative to wherever the process started. Consider deferring resolution until `lifespan`.

### Recommendations

- **Add a test for the `resume_key` admin endpoint** — currently the full HTTP handler for key resumption has no test coverage. The underlying `KeyPool.resume()` is tested, but the endpoint that extracts `key_index` from JSON and calls `kp._keys[key_index]` is not.
- **Consider adding integration tests** — `tests/smoke.py` is a manual smoke test. An automated integration test (e.g., with `TestClient` from `httpx` or `fastapi.testclient`) would catch the `params["name"]` KeyError and other runtime issues before they reach production.
- **The `expose` key format convention (`upstream/tool`) is enforced nowhere** — `_find_expose_entry` splits on "/" to extract the upstream name. A key without "/" would be misinterpreted as both upstream and tool. Consider validating this in `Config.validate_refs()` or in a Pydantic model validator on the `expose` dict keys.

### Assessment

**Verdict**: CHANGES REQUESTED
**Reasoning**: Two critical bugs — an unhandled KeyError in the primary `tools/call` handler (`main.py:119`) and unhandled exceptions in the admin config reload endpoint (`main.py:227`) — will crash the server or produce 500s in production. Both are trivially fixable. The rest of the code is well-structured with good test coverage; fix these two, extract the duplicated `fetch_one`, and the project is solid for 0.1.0.
