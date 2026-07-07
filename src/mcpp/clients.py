"""Per-client tool-name formatting strategies.

mcpp exposes itself as ONE aggregated MCP server (named via ``Config.server_name``,
default ``mcpp``). Every upstream tool is renamed and surfaced under that single
server — upstream names never appear in the downstream tool name. So:

    upstream xiaohongshu / note_search   (as: search_xiaohongshu)
    upstream zhihu       / ZhihuSearch   (as: search_zhihu)

becomes, for a Claude client:

    mcp__mcpp__search_xiaohongshu
    mcp__mcpp__search_zhihu

Each MCP client expects a different ``<server>__<tool>`` shape, so mcpp serves
one endpoint per client (see ``CLIENT_ROUTES``) and applies the matching strategy
at the tool-name layer only. ``<server>`` is always ``Config.server_name``;
``<tool>`` is always the expose entry's display name (``as`` or key tail).

Strategies are stateless; ``parse_name`` is the inverse used by ``tools/call``
to recover the display name (server is known from config, so only the tool part
needs extracting).
"""
from __future__ import annotations

from typing import Optional, Protocol


class ClientFormat(Protocol):
    """Bidirectional tool-name mapping for one client.

    ``format_name`` produces the downstream tool name from the aggregated server
    name + the expose display name.
    ``parse_name`` extracts the display name back out of a formatted tool name;
    returns None if the input doesn't match this client's shape (caller then
    falls back to the default key/display-name lookup).
    """

    client_id: str

    def format_name(self, server: str, display_name: str) -> str: ...

    def parse_name(self, server: str, formatted: str) -> Optional[str]: ...
        # returns display_name on match, else None


class DefaultFormat:
    """Raw passthrough — tool name is the expose display name, unchanged."""

    client_id = "default"

    def format_name(self, server: str, display_name: str) -> str:
        return display_name

    def parse_name(self, server: str, formatted: str) -> Optional[str]:
        return None  # caller uses key/display_name fallback


class ClaudeFormat:
    """Claude Code / Claude Desktop convention: ``mcp__<server>__<tool>``."""

    client_id = "claude"
    _PREFIX = "mcp__"
    _SEP = "__"

    def format_name(self, server: str, display_name: str) -> str:
        return f"{self._PREFIX}{server}{self._SEP}{display_name}"

    def parse_name(self, server: str, formatted: str) -> Optional[str]:
        expected_prefix = f"{self._PREFIX}{server}{self._SEP}"
        if not formatted.startswith(expected_prefix):
            return None
        return formatted[len(expected_prefix):] or None


class CursorFormat:
    """Cursor convention: ``mcp_<server>_<tool>`` (single underscore)."""

    client_id = "cursor"
    _PREFIX = "mcp_"
    _SEP = "_"

    def format_name(self, server: str, display_name: str) -> str:
        return f"{self._PREFIX}{server}{self._SEP}{display_name}"

    def parse_name(self, server: str, formatted: str) -> Optional[str]:
        expected_prefix = f"{self._PREFIX}{server}{self._SEP}"
        if not formatted.startswith(expected_prefix):
            return None
        return formatted[len(expected_prefix):] or None


REGISTRY: dict[str, ClientFormat] = {
    "default": DefaultFormat(),
    "claude": ClaudeFormat(),
    "cursor": CursorFormat(),
}

# client id -> endpoint path. Adding a new client = one entry here + one
# strategy class above; main.py loops over this to register routes.
CLIENT_ROUTES: dict[str, str] = {
    "default": "/mcp",
    "claude": "/claude/mcp",
    "cursor": "/cursor/mcp",
}


def get_format(client_id: str) -> ClientFormat:
    """Return the strategy for ``client_id``, falling back to default."""
    return REGISTRY.get(client_id, REGISTRY["default"])
