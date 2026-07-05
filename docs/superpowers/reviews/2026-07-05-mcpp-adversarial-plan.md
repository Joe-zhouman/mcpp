# Existence Audit — mcpp Implementation Plan

**net: -67 lines deletable**

## Tagged Findings

§File Map: delete: config.yaml path missing from file map. main.py hardcodes `Config.from_file("config.yaml")` as a relative path with no CLI arg or env var. Run from any other directory and it silently fails.

§Task 2, UpstreamConfig.transport: yagni: field with default `"http"` but v1 implements HTTP only. No code reads this field, no transport selection logic exists. Delete until a second transport type is added.

§Task 2, UpstreamConfig.auth: shrink: typed as `dict | None`. Should be a typed Pydantic model with `keys: list[str]` to validate structure at parse time — currently a bare dict with no schema enforcement.

§Task 3 + Task 6: shrink: `_BACKTICK_REF = re.compile(r"`(\S+)`")` defined identically in config.py and transform.py. Define once in a shared module (config.py exports it, transform.py imports it).

§Task 5 para 2 + Task 9: yagni: SSE fallback. Task 5's _rpc explicitly says "v1: error out for non-streamable endpoints, implement SSE fallback in v2." Task 9 implements it anyway — contradictory design intent. The fallback implementation also has no session lifecycle management, no event stream parsing. Defer the entire task to v2 as the original comment stated. (~60 lines deletable)

§Task 6, _build_downstream_schema: bug: `required.append(name)` unconditionally adds every exposed param to the required list, regardless of the upstream's original optionality. Downstream schema will reject calls that omit optional params the upstream would accept.

§Task 6, _build_downstream_schema: shrink: `prop["description"] = ""` sets all param descriptions to empty string, stripping any original upstream documentation. If no override is provided, the upstream description should pass through.

§Task 6, _param_transform_value: shrink: first parameter `tool_name` declared but never used in the function body. Dead parameter.

§Task 7, main.py lifespan: shrink: `Config.from_file("config.yaml")` — hardcoded relative path with no configuration mechanism. Should accept `--config` CLI argument or `MCPP_CONFIG` env var.

§Task 7, _build_upstreams: delete: `auth_header = None` assignment followed by `pass  # Task 8 wires this`. Dead code that Task 8 replaces entirely. Either write the complete auth wiring here or leave the entire handler for Task 8 — do not ship a no-op stub.

§Task 7, tools/call handler `f"{upstream_name}__{entry.tool}"`: bug: upstream expects the original tool name (`entry.tool`), not a prefixed decorated name. `upstream_name__` prefix is wrong. Should be `entry.tool`.

§Task 7, /api/config POST handler: bug: sets `request.app.state.config = new_config` (in-memory only) but never writes to `config.yaml` on disk. Process restart loses all UI edits. The spec's rationale for YAML was git version control and portability — the UI bypasses the file entirely.

§Task 7, Admin UI `loadConfig` + `saveConfig` roundtrip: bug: `/api/config` GET returns JSON via Pydantic `model_dump()`. UI displays JSON in textarea. User edits JSON. POST sends JSON text. `Config.from_yaml()` parses it (JSON is valid YAML). Config file on disk is YAML. No indication to user that the format silently converted. At minimum the config API should roundtrip as YAML.

§Task 8, `_is_auth_failure(e)`: bug: checks error message string for substrings "401", "403", "429". httpx raises `httpx.HTTPStatusError` with the status code on `response.status_code`, not embedded in the exception string. The check will miss most auth failures. Should inspect `e.response.status_code` if it's an HTTPStatusError.

§Task 8, `transport.auth_header = f"Bearer {new_key}"`: bug: sets attribute on `HttpTransport` instance, but `httpx.AsyncClient` was constructed with `headers={...}` at `__init__` time. The client's default request headers are NOT updated. New key never takes effect on subsequent requests. The client needs reconstruction or per-request auth.

§§: hidden assumption: zero logging across the entire plan. A proxy that silently drops upstream failures, key pool exhaustions, and config reload errors provides no debug surface. Every finding in this audit becomes invisible at runtime.

§§: hidden assumption: `tools/list` iterates all upstreams concurrently but with no error isolation. One upstream failure fails the entire downstream request. No graceful degradation, no partial results.

Lean. Ship. — the core concept (tool surface rewriting with backtick cross-references) is sound, the spec's design rationales are well-reasoned, and the scope boundaries (no tool merging, no output transform, no watchdog) are correctly defended. The deletions and fixes above tighten the v1 scope to what actually belongs there.
