import pytest
from mcpp.upstream import HttpTransport


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
