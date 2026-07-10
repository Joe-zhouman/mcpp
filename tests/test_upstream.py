import pytest
from mcpp.upstream import HttpTransport, StdioTransport


def _mock_handshake(httpx_mock, url, session_id="sess-1"):
    """Mock the initialize + initialized-notification responses for a session."""
    httpx_mock.add_response(
        url=url,
        json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
        headers={"mcp-session-id": session_id},
    )
    # initialized notification gets an empty 202 response
    httpx_mock.add_response(url=url, status_code=202, text="")


@pytest.mark.asyncio
async def test_list_tools(httpx_mock):
    url = "https://api.example.com/mcp"
    _mock_handshake(httpx_mock, url)
    httpx_mock.add_response(
        url=url,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "search", "description": "Search repos"},
                    {"name": "list", "description": "List repos"},
                ]
            },
        },
    )
    t = HttpTransport("test", "https://api.example.com")
    tools = await t.list_tools()
    assert len(tools) == 2
    assert tools[0].name == "search"
    await t.close()


@pytest.mark.asyncio
async def test_auth_header_per_request(httpx_mock):
    """Every outbound HTTP request carries a freshly-fetched bearer token,
    so key-pool rotation takes effect immediately (not once per session)."""
    url = "https://api.example.com/mcp"
    seen: list[str] = []

    def get_auth() -> str:
        key = f"key_{len(seen)}"
        seen.append(key)
        return f"Bearer {key}"

    # initialize + notif + first tools/list + second tools/list
    for _ in range(4):
        httpx_mock.add_response(
            url=url, json={"jsonrpc": "2.0", "id": 1, "result": {}},
            headers={"mcp-session-id": "sess-1"},
        )

    t = HttpTransport("test", "https://api.example.com", get_auth=get_auth)
    await t.list_tools()
    await t.list_tools()
    # A fresh key was fetched for each of the 4 requests (init, notif, list, list).
    assert len(seen) == 4
    assert len(set(seen)) == 4  # all distinct → rotation worked
    await t.close()


@pytest.mark.asyncio
async def test_stdio_transport_list_tools():
    """StdioTransport with a minimal Python MCP server over stdin/stdout."""
    server_script = r"""
import sys, json
for line in sys.stdin:
    req = json.loads(line)
    if req["method"] == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": req["id"],
            "result": {
                "tools": [
                    {"name": "search", "description": "Search files"},
                ]
            },
        }
        print(json.dumps(resp), flush=True)
    elif req["method"] == "tools/call":
        resp = {
            "jsonrpc": "2.0",
            "id": req["id"],
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        print(json.dumps(resp), flush=True)
"""
    t = StdioTransport("test", "python3", ["-c", server_script])
    tools = await t.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "search"
    await t.close()


@pytest.mark.asyncio
async def test_stdio_transport_call_tool():
    """StdioTransport call_tool round-trip."""
    server_script = r"""
import sys, json
for line in sys.stdin:
    req = json.loads(line)
    resp = {
        "jsonrpc": "2.0",
        "id": req["id"],
        "result": {"content": [{"type": "text", "text": "hello"}]},
    }
    print(json.dumps(resp), flush=True)
"""
    t = StdioTransport("test", "python3", ["-c", server_script])
    result = await t.call_tool("any", {"q": "test"})
    assert result == {"content": [{"type": "text", "text": "hello"}]}
    await t.close()
