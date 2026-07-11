"""Per-client tool-name formatting strategies + connection presets.

mcpp exposes itself as one aggregated MCP server per toolset. Each MCP client
expects a different ``<server><sep><tool>`` shape, so mcpp serves one endpoint
per naming mode (``/<toolset>/<naming>/mcp``) and applies the matching strategy
at the tool-name layer only.

Naming modes, connect modes, and the client list are declared in
``client-presets.yaml`` (next to this file). Adding a client = one YAML entry;
adding a naming/connect mode = one YAML entry. No code change needed for the
common case. Strategies are stateless; ``parse_name`` is the inverse used by
``tools/call`` to recover the display name (server is known from config).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Protocol

import yaml

PRESET_PATH = Path(__file__).parent / "client-presets.yaml"


class ClientFormat(Protocol):
    """Bidirectional tool-name mapping for one naming mode.

    ``format_name`` produces the downstream tool name from the aggregated server
    name + the expose display name. ``parse_name`` extracts the display name
    back out; returns None on no match so the caller can fall back.
    """

    naming_id: str

    def format_name(self, server: str, display_name: str) -> str: ...

    def parse_name(self, server: str, formatted: str) -> Optional[str]: ...
        # returns display_name on match, else None


class TemplateFormat:
    """A naming strategy built from a ``{server}``/``{tool}`` format string.

    The format is compiled into a regex for the inverse parse: ``{server}``
    becomes a non-greedy group, ``{tool}`` a greedy one, everything else is
    escaped literally. This round-trips as long as the separator chars don't
    appear inside tool names (true for all known clients).
    """

    def __init__(self, naming_id: str, fmt: str) -> None:
        self.naming_id = naming_id
        self._fmt = fmt
        # Build parse regex: split on the placeholders, escape the literals.
        parts = re.split(r"(\{server\}|\{tool\})", fmt)
        pattern = "^"
        for p in parts:
            if p == "{server}":
                pattern += r"(?P<server>.+?)"
            elif p == "{tool}":
                pattern += r"(?P<tool>.+)"
            else:
                pattern += re.escape(p)
        pattern += "$"
        self._re = re.compile(pattern)

    def format_name(self, server: str, display_name: str) -> str:
        return self._fmt.format(server=server, tool=display_name)

    def parse_name(self, server: str, formatted: str) -> Optional[str]:
        m = self._re.match(formatted)
        if not m:
            return None
        if m.group("server") != server:
            return None
        return m.group("tool") or None


class DefaultFormat:
    """Raw passthrough — tool name is the expose display name, unchanged.

    Kept as a distinct class (not a TemplateFormat) because its format ``{tool}``
    would match ANY string on parse, which is wrong — default defers to the
    caller's key/display-name fallback instead.
    """

    naming_id = "default"

    def format_name(self, server: str, display_name: str) -> str:
        return display_name

    def parse_name(self, server: str, formatted: str) -> Optional[str]:
        return None


def _load_presets() -> dict:
    """Load and return the parsed client-presets.yaml."""
    with PRESET_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


_PRESETS = _load_presets()

# Build the format registry from naming_modes. "default" is special-cased.
REGISTRY: dict[str, ClientFormat] = {"default": DefaultFormat()}
for _nid, _mode in (_PRESETS.get("naming_modes") or {}).items():
    if _nid == "default":
        continue
    REGISTRY[_nid] = TemplateFormat(_nid, _mode["format"])

# Endpoint path per naming mode. Every mode (including default) serves at
# /<toolset>/<naming>/mcp. main.py additionally registers "/mcp" as a legacy
# alias for the default naming on the default toolset.
CLIENT_ROUTES: dict[str, str] = {}
for _nid in REGISTRY:
    if _nid == "default":
        CLIENT_ROUTES[_nid] = "/{toolset}/default/mcp"
    else:
        CLIENT_ROUTES[_nid] = f"/{{toolset}}/{_nid}/mcp"

# Legacy aliases: bare paths that map to (naming_id) on the default toolset.
# main.py registers these alongside the per-naming routes.
LEGACY_ALIASES: dict[str, str] = {
    "/mcp": "default",
}


def get_format(naming_id: str) -> ClientFormat:
    """Return the strategy for ``naming_id``, falling back to default."""
    return REGISTRY.get(naming_id, REGISTRY["default"])


def presets() -> dict:
    """Return the raw parsed presets (for the admin API to forward to the UI)."""
    return _PRESETS
