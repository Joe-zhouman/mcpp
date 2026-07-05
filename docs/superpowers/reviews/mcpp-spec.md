# Spec Compliance Review — mcpp (MCP Plus) — Re-review

**Reviewed**: BASE f9419ce → HEAD d2ef132
**Previous review**: BASE f9419ce → HEAD a265848 (3 missing, 1 extra, 1 ambiguous)
**Spec**: `docs/superpowers/specs/mcp-tool-gateway.md`
**Plan**: `docs/superpowers/plans/2026-07-05-mcpp.md`
**Reviewer**: scorpio

## Verdict
❌ Issues found — 3 issues (0 missing, 2 extra, 1 ambiguous). All 3 previously-missing issues are now fixed.

---

## Previously Missing — Now Fixed

### M1: Concurrent upstream fetch — FIXED

**Previous finding**: Sequential `for` loop at `main.py:90-104`. Spec requires concurrent fetch.

**Fix**: `main.py:91-112` now defines an inner `fetch_one(uc)` coroutine and calls all upstreams via `asyncio.gather(*[fetch_one(uc) for uc in config.upstreams], return_exceptions=True)`. Results are collected and extended into `all_tools`. Same pattern applied for `/api/tools` at `main.py:242-262`.

**Verified at**:
- `src/mcpp/main.py:3` — `import asyncio`
- `src/mcpp/main.py:91-103` — `fetch_one` coroutine
- `src/mcpp/main.py:105-108` — `asyncio.gather()` dispatch
- `src/mcpp/main.py:242-252` — same pattern for `/api/tools`
- `src/mcpp/main.py:254-257` — `asyncio.gather()` for `/api/tools`

**Verdict**: Correct. All upstreams are queried concurrently. `return_exceptions=True` prevents one upstream timeout from aborting others.

---

### M2: Key pool auto-pause on 401/403/429 was dead code — FIXED

**Previous finding**: `HttpTransport._rpc()` raised `RuntimeError`, which was caught by the generic `except Exception` at `main.py:162`, never by the `httpx.HTTPStatusError` handler at `main.py:143`. Keys were never marked bad.

**Fix**: `upstream.py:76-80` now raises `httpx.HTTPStatusError` (constructed manually with `message`, `request`, `response`) instead of `RuntimeError`. The handler at `main.py:151-158` catches `httpx.HTTPStatusError` and calls `kp.mark_bad(kp.current)` for 401/403/429 responses.

**Verified at**:
- `src/mcpp/upstream.py:76-80` — raises `httpx.HTTPStatusError(message=..., request=resp.request, response=resp)`
- `src/mcpp/main.py:151` — `except httpx.HTTPStatusError as e:`
- `src/mcpp/main.py:153-158` — `e.response.status_code in (401, 403, 429)` check, `kp.mark_bad(kp.current)`, health count logged

**Verdict**: Correct. The catch chain is now connected. A 401 from upstream → `httpx.HTTPStatusError` raised → caught by specific handler → key marked bad. Round-robin then skips the bad key on next `next()` call.

---

### M3: Key resume via UI was missing — FIXED

**Previous finding**: `KeyPool.resume()` existed but was never called. No endpoint, no UI element.

**Fix**:
- `GET /api/keys` at `main.py:265-274` returns key pool statuses for all upstreams (healthy count + per-key status with masked key)
- `POST /api/keys/{upstream_name}/resume` at `main.py:277-292` resumes a paused key by index
- Keys tab added to admin UI at `index.html:28,43-46,85-119` with per-key status display and Resume buttons
- `KeyPool.statuses()` at `keypool.py:36-41` provides masked key info for the admin API

**Verified at**:
- `src/mcpp/main.py:265-274` — `GET /api/keys`
- `src/mcpp/main.py:277-292` — `POST /api/keys/{upstream_name}/resume`
- `src/mcpp/keypool.py:36-41` — `statuses()` method
- `src/mcpp/static/index.html:28` — Keys tab in nav
- `src/mcpp/static/index.html:43-46` — Keys panel div
- `src/mcpp/static/index.html:85-119` — `loadKeys()` and `resumeKey()` JS functions

**Verdict**: Correct. Paused keys are visible in the Keys tab with a Resume button. Clicking Resume calls the API, which calls `KeyPool.resume()`. Spec requirement satisfied.

**Minor note**: `main.py:287` accesses `kp._keys[key_index]` (private attribute). This couples the API to KeyPool's internal storage. Not a spec issue — the spec doesn't prescribe how key indexing works — but the design is fragile if KeyPool refactors its internal key storage. Routing recommendation: taurus for code hygiene.

---

## Extra (unrequested) — Still Present

### E1: Graceful degradation on `tools/list` failure (unchanged)

**Previous finding**: When an upstream fails during `tools/list`, the error is silently logged and that upstream's tools are omitted. No error reaches the client.

**Current state**: Unchanged. At `main.py:99-103`, the `fetch_one` coroutine catches all exceptions, logs a warning, and returns `[]`. The `asyncio.gather()` at lines 105-108 uses `return_exceptions=True`, so even unhandled exceptions are swallowed. Same pattern for `/api/tools` at `main.py:250-252`.

**What changed since last review**: Nothing. The concurrency refactor preserved the same error-swallowing behavior. The only difference is that `fetch_one` now uses `request.app.state.upstreams.get(uc.name)` (defensive `.get()`) instead of direct dict access — but the error-silencing logic is identical.

**Risk**: If upstream B is misconfigured, `tools/list` returns only upstream A's tools with no indication that B failed. The client has no way to know tools are missing. The spec doesn't prescribe partial-failure behavior, so this is not strictly a compliance violation — but it is a design choice the spec never requested.

**Route to**: libra (spec/design question — should partial failure be silent, noisy, or fatal?)

---

### E2: `server/discover` endpoint (unchanged)

**Previous finding**: `server/discover` at `main.py:181-190` is not mentioned in the spec. Returns `protocolVersion: "2025-11-25"`.

**Current state**: Unchanged. Still at `main.py:181-190`.

**Risk**: Low. This is arguably necessary for MCP client compatibility. Not a compliance violation per se, but the spec didn't request it.

**Route to**: libra (keep or remove? If keeping, version string should be consistent with spec reference)

---

## Ambiguous — Still Present

### A1: `transport` field absent from UpstreamConfig (unchanged)

**Previous finding**: Spec says "每个 upstream 通过 `transport` 字段声明连接方式". Plan design decision says "`UpstreamConfig.transport` field removed — v1 only has HTTP, add field when stdio arrives." Pydantic silently ignores unknown fields.

**Current state**: Unchanged. `src/mcpp/config.py:17-22` — `UpstreamConfig` has no `transport` field. No `model_config = {"extra": "forbid"}` is set on `Config` or `UpstreamConfig`.

**Risk**: A config YAML with `transport: stdio` would parse without error and silently use HTTP. User gets no feedback that their `transport` field was ignored.

**Route to**: libra (add the field now? Add `extra = "forbid"`? Accept the plan's YAGNI decision?)

---

## Confirmed Correct (verified by reading code, unchanged from previous review unless noted)

| Requirement | Location | Notes |
|---|---|---|
| `/mcp` POST endpoint (Streamable HTTP) | `src/mcpp/main.py:82-196` | Matches MCP protocol structure |
| tools/list: filter by `expose`, rewrite name/desc/schema | `src/mcpp/transform.py:99-133` | Now uses `by_upstream_tool` tuple-keyed lookup (line 109-112) — functionally equivalent to plan's string concatenation, slightly cleaner |
| tools/call: parse tool name, reverse-transform params, forward | `src/mcpp/main.py:119-179` + `src/mcpp/transform.py:72-96` | `_find_expose_entry` at `main.py:199-211` matches by display name or stable key. Now uses `.get()` with None check at line 130 for defensive transport lookup. |
| Param transform: rename (map_from), enum mapping, preset expansion, hidden+default | `src/mcpp/transform.py:72-96` | Order: per-param resolution → hidden/default → enum/preset. Matches spec. |
| Backtick ref validation: rejects missing/hidden refs at config load | `src/mcpp/config.py:55-74` | `validate_refs()` called from `from_yaml` at line 52 |
| Backtick ref resolution: `key` replaced with display name | `src/mcpp/transform.py:17-24` | Uses `_build_display_name_map` (lines 9-14) |
| `as:` rename auto-propagates to backtick refs | `src/mcpp/transform.py:9-14,17-24,122-123` | Display map rebuilt per `transform_tools` call |
| Stable key is `{upstream}/{tool}` | `src/mcpp/config.py:46` | `expose: dict[str, ExposeEntry]` |
| Config reload via `/api/config` POST, no restart | `src/mcpp/main.py:223-235` | Writes YAML, rebuilds upstreams, closes old transports |
| `/admin` UI: tool preview + YAML editor + Keys tab | `src/mcpp/static/index.html` | Three tabs now (was two): Tool Preview, Config Editor, Keys |
| Error propagation: `[gateway] upstream=<name>` prefix | `src/mcpp/main.py:159-168,172-178` | Both HTTP and generic error paths. `tools/call` now also returns `"[gateway] upstream=<name>: no transport"` at line 137 for missing transport case. |
| `UpstreamTransport` protocol abstraction | `src/mcpp/upstream.py:15-21` | Protocol with `list_tools`, `call_tool`, `close` |
| `HttpTransport` with `connect_timeout`/`read_timeout` defaults 30s/120s | `src/mcpp/upstream.py:40-50` | Passed to `httpx.Timeout` |
| Per-request auth callback | `src/mcpp/upstream.py:52-58` + `src/mcpp/main.py:56-67` | `_headers()` calls `_get_auth()` fresh each RPC |
| KeyPool round-robin | `src/mcpp/keypool.py:18-24` | Unchanged |
| KeyPool `current` property | `src/mcpp/keypool.py:43-47` | Unchanged |
| KeyPool `statuses()` method (new) | `src/mcpp/keypool.py:36-41` | Returns masked key list for admin API |
| `MCPP_CONFIG` env var | `src/mcpp/main.py:23` | Defaults to `config.yaml` |
| `ExposeEntry.as_` uses `Field(alias="as")` | `src/mcpp/config.py:38` | Correct YAML `as` → Python `as_` mapping |
| Hidden params removed from downstream schema | `src/mcpp/transform.py:45-46` | `if p.hidden: continue` |
| Params with default → optional | `src/mcpp/transform.py:60-61` | `if p.default is None: required.append(p.name)` |
| Upstream param descriptions preserved | `src/mcpp/transform.py:50-52` | Copied from upstream schema |
| YAML roundtrip for config API | `src/mcpp/config.py:80-86` `to_yaml()` + `src/mcpp/main.py:223-235` | Config API uses YAML text |
| `BACKTICK_REF` defined once in config.py, imported by transform.py | `src/mcpp/config.py:10` + `src/mcpp/transform.py:5` | Matches plan design decision |
| No file watching | Confirmed by absence | Spec says "不做文件监听" |
| No database, no auth for gateway | Confirmed by absence | Spec design rationale |
| Python 3.12+ | `pyproject.toml:5` | `requires-python = ">=3.12"` |
| `src/mcpp/__init__.py` exists | `src/mcpp/__init__.py` | Package marker |
| `config.yaml.example` shipped | `config.yaml.example:1-29` | Includes `gh/code` entry (was not in plan's example) |

---

## Test execution status

Tests could not be executed in the current environment (Python 3.9 vs required 3.12+). All 5 test files import cleanly based on static analysis. Test coverage matches plan expectations for config, keypool, upstream, transform, and smoke tests. No test for the new `/api/keys` or `/api/keys/{name}/resume` endpoints exists — the plan did not call for one (these endpoints were added in the fix commit, not in the original plan).

---

## Glossary check

No `glossary.md` found at `docs/superpowers/glossary.md` or project root. Skipped.

---

## Routing recommendations

| Issue | Route to | Rationale |
|---|---|---|
| E1 (silent tools/list degradation) | libra | Design decision: should partial failure be silent, noisy, or fatal? Spec doesn't prescribe. |
| E2 (server/discover endpoint) | libra | Keep or remove the endpoint. Version string "2025-11-25" postdates spec reference "2024-11-05+". |
| A1 (transport field absent) | libra | Add field now? Add `extra="forbid"`? Accept YAGNI? |
| `kp._keys` private access at main.py:287 | taurus | Code hygiene: API layer depends on KeyPool internal storage structure |

All three previously-missing issues (M1, M2, M3) are fixed. No new missing requirements found.
