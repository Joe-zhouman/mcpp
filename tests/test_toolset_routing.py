"""Tests for toolset grouping + per-client naming through the transform layer
and the HTTP MCP routes."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from mcpp.config import Config
from mcpp.upstream import Tool
from mcpp.transform import transform_tools
from mcpp.main import _find_expose_entry
import mcpp.main as main


SAMPLE = """
upstreams:
  - name: xiaohongshu
    url: https://xhs.example.com/mcp
  - name: zhihu
    url: https://zhihu.example.com/mcp
expose:
  xiaohongshu/note_search:
    upstream: xiaohongshu
    tool: note_search
    toolset: search
    as: xiaohongshu
  zhihu/ZhihuSearch:
    upstream: zhihu
    tool: ZhihuSearch
    toolset: search
    as: zhihu
  zhihu/Profile:
    upstream: zhihu
    tool: Profile
    as: profile
"""


@pytest.fixture
def cfg():
    return Config.from_yaml(SAMPLE)


# --- transform layer ---

def test_toolset_filter_only_returns_matching_tools(cfg):
    tools = [Tool(name="note_search"), Tool(name="ZhihuSearch"), Tool(name="Profile")]
    # search toolset, claude → only the two search tools
    out = transform_tools("xiaohongshu", tools, cfg, client="claude", toolset="search")
    names = [t.name for t in out]
    # note_search belongs to search toolset → formatted
    assert "mcp__search__xiaohongshu" in names
    # Profile has no toolset → defaults to "mcpp", excluded from "search"
    assert not any(n.startswith("mcp__search__profile") for n in names)


def test_default_toolset_is_server_name(cfg):
    """Expose entries without `toolset` land in config.server_name (mcpp)."""
    tools = [Tool(name="Profile")]
    out = transform_tools("zhihu", tools, cfg, client="claude", toolset="mcpp")
    assert [t.name for t in out] == ["mcp__mcpp__profile"]


def test_default_client_unchanged_when_no_toolset(cfg):
    """No client + no toolset → bare display names (backward compat)."""
    tools = [Tool(name="note_search")]
    out = transform_tools("xiaohongshu", tools, cfg)
    assert [t.name for t in out] == ["xiaohongshu"]


# --- reverse lookup ---

def test_find_entry_claude_format_within_toolset(cfg):
    entry, up = _find_expose_entry(cfg, "mcp__search__zhihu", client="claude", toolset="search")
    assert entry is not None and entry.tool == "ZhihuSearch" and up == "zhihu"


def test_find_entry_rejects_wrong_toolset(cfg):
    """A search-formatted name must not resolve on the files/default toolset."""
    entry, up = _find_expose_entry(cfg, "mcp__search__zhihu", client="claude", toolset="mcpp")
    assert entry is None


def test_find_entry_default_client_uses_display_name(cfg):
    entry, up = _find_expose_entry(cfg, "xiaohongshu", client="default")
    assert entry is not None and entry.tool == "note_search"


# --- HTTP routes ---

@pytest.fixture
def client(cfg):
    """TestClient wired with a fake transport per upstream."""
    app = main.app
    app.state.config = cfg
    app.state.upstreams = {}
    app.state.keypools = {}

    upstream_tools = {
        "xiaohongshu": [Tool(name="note_search")],
        "zhihu": [Tool(name="ZhihuSearch"), Tool(name="Profile")],
    }
    for name in ("xiaohongshu", "zhihu"):
        fake = MagicMock()
        fake.list_tools = AsyncMock(return_value=upstream_tools[name])
        fake.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]})
        app.state.upstreams[name] = fake
    return TestClient(app)


def test_list_claude_route_formats_names(client):
    r = client.post("/search/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "mcp__search__xiaohongshu" in names
    assert "mcp__search__zhihu" in names
    # Profile is in the mcpp toolset, not search → excluded
    assert not any("profile" in n.lower() for n in names)


def test_default_toolset_route(client):
    r = client.post("/mcpp/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "mcp__mcpp__profile" in names
    # search-only tools must NOT leak into the default toolset
    assert "mcp__mcpp__zhihu" not in names


def test_unknown_toolset_404(client):
    r = client.post("/nope/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    assert r.status_code == 404


def test_call_routes_to_upstream(client):
    """tools/call with a claude-formatted name reaches the right upstream tool."""
    r = client.post("/search/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "mcp__search__zhihu", "arguments": {}},
    })
    body = r.json()
    assert body["result"]["content"][0]["text"] == "ok"
    client.app.state.upstreams["zhihu"].call_tool.assert_called_once_with("ZhihuSearch", {})


def test_discover_reports_toolset_name(client):
    r = client.post("/search/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}
    })
    assert r.json()["result"]["serverInfo"]["name"] == "search"


# --- auth ---

def test_auth_open_when_no_token(client):
    """No auth.token configured → endpoints are open."""
    r = client.post("/search/claude/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    assert r.status_code == 200


def test_auth_requires_token(cfg):
    from mcpp.config import GatewayAuth
    cfg.auth = GatewayAuth(token="s3cret")
    app = main.app
    app.state.config = cfg
    c = TestClient(app)
    # no token → 401
    r = c.get("/api/tools")
    assert r.status_code == 401
    # header → 200
    r = c.get("/api/tools", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    # query → 200
    r = c.get("/api/tools?token=s3cret")
    assert r.status_code == 200
