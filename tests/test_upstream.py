import pytest
from mcpp.upstream import HttpTransport, StdioTransport


@pytest.mark.asyncio
async def test_list_tools(httpx_mock):
    httpx_mock.add_response(
        url="https://api.example.com/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
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
    calls: list[str] = []
    def get_auth() -> str:
        key = "key_a" if len(calls) == 0 else "key_b"
        calls.append(key)
        return f"Bearer {key}"

    httpx_mock.add_response(url="https://api.example.com/mcp", json={
        "jsonrpc": "2.0", "id": 1, "result": {"tools": []}
    })
    httpx_mock.add_response(url="https://api.example.com/mcp", json={
        "jsonrpc": "2.0", "id": 1, "result": {"tools": []}
    })

    t = HttpTransport("test", "https://api.example.com", get_auth=get_auth)
    await t.list_tools()
    await t.list_tools()
    assert calls == ["key_a", "key_b"]  # fresh auth on each call
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
