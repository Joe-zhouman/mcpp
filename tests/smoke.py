"""Manual smoke test: start mcpp, point to a real upstream, verify /mcp responds.

Usage:
    MCPP_CONFIG=config.yaml mcpp &
    MCPP_PORT=9020 python tests/smoke.py
"""
import httpx
import sys
import json
import os


async def smoke():
    port = os.environ.get("MCPP_PORT", "9020")
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=10) as c:
        # tools/list
        r = await c.post(f"{base}/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
        })
        data = r.json()
        assert "result" in data, f"Expected result, got: {data}"
        tools = data["result"]["tools"]
        print(f"OK: {len(tools)} tools exposed")
        for t in tools:
            print(f"  - {t['name']}: {t.get('description', '')[:60]}")

        # server/discover
        r = await c.post(f"{base}/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {}
        })
        data = r.json()
        assert data["result"]["serverInfo"]["name"] == "mcpp"
        print("OK: server/discover")

        # admin
        r = await c.get(f"{base}/api/config")
        assert r.status_code == 200
        print("OK: admin API")


if __name__ == "__main__":
    import asyncio
    asyncio.run(smoke())
