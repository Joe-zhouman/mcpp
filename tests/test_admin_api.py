"""Tests for the structured expose-entry admin API (GET/PUT/DELETE /api/expose)."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from mcpp.config import Config
from mcpp.upstream import Tool
import mcpp.main as main


SAMPLE = """
server_name: mcpp
upstreams:
  - name: gh
    url: https://api.github.com/mcp
expose:
  gh/search:
    upstream: gh
    tool: search
    toolset: search
    as: github
    description: "Search repos. See `gh/code`."
    params:
      - name: q
        map_from: query
      - name: limit
        default: 10
  gh/code:
    upstream: gh
    tool: code_search
    toolset: search
    as: code
"""

RAW_TOOLS = [
    Tool(
        name="search",
        description="raw search desc",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the query"},
                "limit": {"type": "integer", "description": "page size"},
            },
            "required": ["query"],
        },
    ),
    Tool(name="code_search", description="raw code desc"),
]


@pytest.fixture
def client():
    cfg = Config.from_yaml(SAMPLE)
    app = main.app
    app.state.config = cfg
    app.state.upstreams = {}
    app.state.keypools = {}
    fake = MagicMock()
    # both list_tools calls (GET /api/expose fetches raw) return the same tools
    fake.list_tools = AsyncMock(return_value=RAW_TOOLS)
    fake.close = AsyncMock(return_value=None)
    app.state.upstreams["gh"] = fake
    return TestClient(app)


def test_get_expose_merges_upstream_schema(client):
    r = client.get("/api/expose")
    assert r.status_code == 200
    data = r.json()
    by_key = {e["key"]: e for e in data}
    assert "gh/search" in by_key and "gh/code" in by_key

    e = by_key["gh/search"]
    assert e["as"] == "github"
    assert e["toolset"] == "search"
    assert e["description"] == "Search repos. See `gh/code`."
    assert e["raw_description"] == "raw search desc"

    # params merged with upstream schema
    pmap = {p["name"]: p for p in e["params"]}
    # 'q' maps from upstream 'query'
    assert pmap["q"]["map_from"] == "query"
    assert pmap["q"]["_upstream_name"] == "query"
    assert pmap["q"]["_required"] is True
    # 'limit' has a default and isn't required upstream
    assert pmap["limit"]["default"] == 10
    assert pmap["limit"]["_required"] is False


def test_put_expose_updates_description(client):
    r = client.put("/api/expose/gh/code", json={
        "as": "code_search_v2",
        "toolset": "search",
        "hide": False,
        "description": "new desc",
        "params": [],
    })
    assert r.status_code == 200
    assert r.json()["status"] == "saved"
    # reflected in GET
    e = {x["key"]: x for x in client.get("/api/expose").json()}["gh/code"]
    assert e["as"] == "code_search_v2"
    assert e["description"] == "new desc"


def test_put_expose_forces_identity_fields(client):
    """upstream/tool cannot be repointed via PUT."""
    client.put("/api/expose/gh/search", json={
        "upstream": "evil", "tool": "pwned", "description": "x", "params": [],
    })
    e = {x["key"]: x for x in client.get("/api/expose").json()}["gh/search"]
    assert e["upstream"] == "gh"
    assert e["tool"] == "search"


def test_put_expose_enum_mapping_round_trips(client):
    """An enum param transform survives PUT + GET + serialization."""
    r = client.put("/api/expose/gh/search", json={
        "as": "github",
        "toolset": "search",
        "description": "d",
        "params": [{
            "name": "sort",
            "map_from": "query",
            "type": "enum",
            "mapping": {"stars": "desc", "updated": "asc"},
        }],
    })
    assert r.status_code == 200, r.text
    e = {x["key"]: x for x in client.get("/api/expose").json()}["gh/search"]
    pmap = {p["name"]: p for p in e["params"]}
    assert pmap["sort"]["type"] == "enum"
    assert pmap["sort"]["mapping"] == {"stars": "desc", "updated": "asc"}


def test_put_expose_rejects_bad_backtick_ref(client):
    """validate_refs rejects a description referencing a nonexistent tool."""
    r = client.put("/api/expose/gh/code", json={
        "description": "see `gh/nope`",
        "params": [],
    })
    assert r.status_code == 400
    assert "gh/nope" in r.json()["error"]


def test_put_expose_unknown_key_404(client):
    r = client.put("/api/expose/gh/missing", json={"description": "x", "params": []})
    assert r.status_code == 404


def test_delete_expose(client):
    # gh/search is not referenced by anyone → deletes cleanly.
    r = client.delete("/api/expose/gh/search")
    assert r.status_code == 200
    keys = {e["key"] for e in client.get("/api/expose").json()}
    assert "gh/search" not in keys
    assert "gh/code" in keys


def test_delete_expose_refused_when_referenced(client):
    # gh/search's description references `gh/code`, so gh/code can't be deleted.
    r = client.delete("/api/expose/gh/code")
    assert r.status_code == 409
    assert "referenced by" in r.json()["error"]


def test_delete_expose_unknown_key_404(client):
    assert client.delete("/api/expose/gh/missing").status_code == 404
