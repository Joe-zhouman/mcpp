# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
No tags are cut yet — entries below are grouped by date, not version.

## Unreleased

mcpp is a thin proxy gateway for MCP tools. What started as a single `/mcp`
endpoint with a YAML-only admin UI has become a multi-toolset aggregated
gateway: each对外 MCP server is a renameable group of tools, served per client
naming convention, driven by a preset catalogue, edited through a structured UI.

### 2026-07-05 — initial baseline

A thin proxy gateway for MCP tools. *(this baseline has known bugs; not tagged.)*

#### Added

- YAML config model with typed auth (`upstreams`, `expose`, `AuthConfig`). (`cd49f89`)
- Backtick cross-tool reference validation in descriptions. (`296cf41`)
- API key pool with round-robin rotation and health-pause on 401/403/429. (`a091004`, `d2ef132`)
- Upstream HTTP transport with per-request auth callback (key rotation takes
  effect immediately). (`4325e69`)
- Tool surface transform: backtick resolution, downstream schema builder
  (enum / preset / hidden / default params). (`905e146`)
- **stdio upstream transport** for local MCP servers launched as subprocesses
  (newline-delimited JSON + Content-Length framing). (`7bb8ba6`)
- FastAPI app with `/mcp` JSON-RPC endpoint, admin UI (tool preview, YAML
  editor, key pool view), and structured logging. (`7352081`)
- Integration smoke test. (`a265848`)

#### Fixed

- KeyError guards, admin error handling, dedup fetch, `key_at` public API,
  env-var overrides for host/port. (`1c6c07b`)
- `to_yaml` `by_alias`, dedup `fetch_all`, port validation, enum/preset guard. (`5aec616`)
- Propagate upstream JSON-RPC errors; dedup display-name logic. (`e93c07e`)
- Null JSON-RPC body guard; catch `yaml.YAMLError` in admin config save. (`d382ef8`)

### 2026-07-07 → 2026-07-12 — the对外-server redesign

mcpp no longer leaks upstream names. It exposes aggregated, renameable MCP
servers (toolsets), one endpoint per client naming convention, driven by a
preset catalogue. Plus a real structured admin UI.

#### Added

- **Aggregated toolset endpoints.** Each toolset is one对外 MCP server reachable
  at `/<toolset>/<client>/mcp`. Tools are renamed (`as`) and grouped by `toolset`
  in config; upstream names never appear downstream. (`48a3ac1`)
  - 6 client naming modes, each its own endpoint: `claude` (`mcp__s__t`),
    `cursor` (`mcp_s_t`), `opencode` (`s_t`), `coder` (`s__t`), `codex`
    (`s::t`), `default` (bare). (`4edf376`)
  - Legacy `/mcp` kept as an alias for default-naming on the default toolset.

- **Client preset catalogue** (`src/mcpp/client-presets.yaml`). 12 clients
  (Claude Code/Desktop, Cursor, OpenCode, Reasonix, Coder, Trae, Codex,
  Hermes, Pi, WorkBuddy, OpenClaw) mapped to a naming mode + a connect mode.
  Adding a client or mode is one YAML line — no code change. (`4edf376`)
  - 6 connect modes cover JSON / TOML / YAML config formats, rendered as
    copy-paste templates with `{endpoint}` / `{token_query}` / `{token_header}`
    placeholders that collapse cleanly when no auth is set.
  - `GET /api/client-presets` exposes the catalogue to the UI.

- **Structured per-tool editor** (Tools tab). Each tool is a form card: rename
  (`as`), toolset, hide, description (with backtick-ref picker), and a per-param
  editor (rename, `map_from`, hidden, default, **enum** mapping, **preset**
  combos — all visual). Per-tool Save/Revert/Delete, dirty indicator, live
  client-name preview. (`cdc3436`)
  - `GET /api/expose` returns entries with the upstream raw schema merged in.
  - `PUT /api/expose/{key}` updates one entry (locks `upstream`/`tool` as
    identity fields, runs backtick-ref validation).
  - `DELETE /api/expose/{key}` refuses with 409 if other descriptions still
    reference the tool.

- **Preview tab** shows the actual client-facing surface: pick a toolset + a
  client format and see the transformed tool names, descriptions (refs
  resolved), and schemas (enum applied, hidden params dropped). (`878b1f0`)
  - `GET /api/tools?toolset=&client=` mirrors the real MCP routes.

- **Toolsets tab** renders import snippets for every client in the catalogue,
  auto-including the auth token when set. Dropdown to switch toolset. (`d317290`,
  `4edf376`)

- **Add Server tab.** Paste Claude-Desktop-style JSON (with or without the
  `mcpServers` wrapper, or bare server map) → parses, probes each server via a
  throwaway transport, generates passthrough expose entries under a chosen
  toolset. `POST /api/add-server`. (`48a3ac1`, `5d0c38e`)

- **Upstreams tab.** Per-upstream connectivity Test (`POST /api/upstreams/{name}/test`
  — ok / latency / tool count / raw names) and Raw Tools view
  (`GET /api/upstreams/{name}/tools`). (`48a3ac1`)

- **Optional gateway auth.** `auth.token` in config enables a bearer-token
  dependency on `/admin`, `/api/*`, and all MCP endpoints. Token accepted via
  `Authorization: Bearer`, `?token=`, or `mcpp_token` cookie. Constant-time
  compare. Open when unset. (`48a3ac1`)

- **One-shot launcher** `start.sh` — resolves venv/system python, installs deps
  if missing, auto-copies `config.yaml.example`, prints local + LAN URLs,
  optional `TOKEN=yes` to generate a bearer token. (`3fcff24`)

#### Changed

- **HTTP transport speaks real MCP now** — lazy `initialize` handshake, caches
  `mcp-session-id`, sends `notifications/initialized`, carries the session id
  on subsequent requests (auto-resets on 404). Required by session-based
  servers (e.g. MCP Gateway), which 400 with "Missing session ID" otherwise. (`5d0c38e`)
  - Parses SSE (`text/event-stream`) JSON-RPC replies, not just `application/json`.
  - Sends `Accept: application/json, text/event-stream` (some servers 406 without).
  - Endpoint path: uses the configured url verbatim if it already contains an
    `/mcp` segment (supports token-in-path URLs like `host/mcp/<token>`).
- **Default bind is `0.0.0.0`** (was `127.0.0.1`) for cross-machine use; logs a
  loud warning when binding open with no auth token. (`3fcff24`)
- Tool-ref insertion is a `<select>` dropdown instead of an inline button list. (`1d23b70`)
- `config.yaml` is gitignored (local-only); `config.yaml.example` is the tracked
  template. `start.sh` auto-generates it on first launch. (`3fcff24`)
- Old YAML-only config tab demoted to "YAML (advanced)".

#### Fixed

- `add-server` tolerates the Claude-Desktop `{"mcpServers": {...}}` wrapper. (`5d0c38e`)
- `_persist_and_reload` extracted so the YAML editor, per-entry writer, and
  add-server all share one write/rebuild path.

#### Docs

- `docs/reasonix-proposal.md` — draft on tool-transform / inter-tool graph. (`03fdaab`)

