#!/usr/bin/env python3
"""
Test MCP server running in local container mode.

This test connects to the MCP server running in standalone container mode,
using the authorized service token from config.json for authentication.
"""

import asyncio
import json
import httpx
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_local_mcp():
    """Test MCP server in local container mode."""

    # Load authorized service token from config.json
    config_path = Path(__file__).parent.parent / "config.json"
    try:
        with open(config_path) as f:
            config = json.load(f)
            auth_token = config.get("qradar", {}).get("authorized_service_token", "")
            if not auth_token:
                print("❌ No authorized_service_token found in config.json")
                return
    except FileNotFoundError:
        print(f"❌ Config file not found: {config_path}")
        return
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in config file: {e}")
        return

    base_url = "http://localhost:5001"
    mcp_endpoint = f"{base_url}/mcp"

    print("QRadar MCP Server - Local Container Test")
    print("=" * 50)
    print(f"Endpoint: {mcp_endpoint}")
    print(f"Auth: Using authorized service token from config.json")
    print(f"Token: {auth_token[:20]}...{auth_token[-10:]}\n")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Get session ID
            print("Step 1: Getting session ID...")
            response = await client.get(
                mcp_endpoint,
                headers={
                    "Accept": "text/event-stream",
                    "SEC": auth_token
                }
            )

            print(f"Status: {response.status_code}")
            session_id = response.headers.get('mcp-session-id')

            if not session_id:
                print("❌ No session ID received!")
                print(f"Headers: {dict(response.headers)}")
                return

            print(f"✅ Session ID: {session_id}\n")

            # Step 2: Send initialize command
            print("Step 2: Sending initialize command...")
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "local-test-client",
                        "version": "1.0.0"
                    }
                }
            }

            response = await client.post(
                mcp_endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "mcp-session-id": session_id,
                    "SEC": auth_token
                },
                json=init_request
            )

            print(f"Status: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type')}")

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'text/event-stream' in content_type:
                    print(f"✅ Initialize command sent (SSE stream response)")
                    if response.text:
                        lines = response.text.split('\n')
                        for line in lines:
                            if line.startswith('data: '):
                                try:
                                    data = json.loads(line[6:])
                                    print(f"\nServer Response:")
                                    print(json.dumps(data, indent=2))
                                except json.JSONDecodeError:
                                    print(f"Raw data: {line}")
                else:
                    result = response.json()
                    print(f"✅ Initialize successful!")
                    server_info = result.get('result', {}).get('serverInfo', {})
                    print(f"Server: {server_info.get('name')}")
                    print(f"Version: {server_info.get('version')}")
                    print(f"Protocol: {result.get('result', {}).get('protocolVersion')}\n")
            else:
                print(f"❌ Initialize failed")
                print(f"Response: {response.text}\n")
                return

            # Step 3: List tools
            print("Step 3: Listing available tools...")
            list_tools_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }

            response = await client.post(
                mcp_endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "mcp-session-id": session_id,
                    "SEC": auth_token
                },
                json=list_tools_request
            )

            print(f"Status: {response.status_code}")

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'text/event-stream' in content_type:
                    if response.text:
                        lines = response.text.split('\n')
                        for line in lines:
                            if line.startswith('data: '):
                                try:
                                    data = json.loads(line[6:])
                                    tools = data.get('result', {}).get('tools', [])
                                    if tools:
                                        print(f"\n✅ Found {len(tools)} tools")
                                        print("\nFirst 10 tools:")
                                        for i, tool in enumerate(tools[:10], 1):
                                            desc = tool['description'].split('\n')[0][:60]
                                            print(f"  {i}. {tool['name']}")
                                            print(f"     {desc}...")
                                except json.JSONDecodeError:
                                    pass
                else:
                    result = response.json()
                    tools = result.get('result', {}).get('tools', [])
                    print(f"\n✅ Found {len(tools)} tools")
                    print("\nFirst 10 tools:")
                    for i, tool in enumerate(tools[:10], 1):
                        desc = tool['description'].split('\n')[0][:60]
                        print(f"  {i}. {tool['name']}: {desc}...")
            else:
                print(f"❌ List tools failed")
                print(f"Response: {response.text}")
                return

            print("\n" + "=" * 50)
            print("✅ MCP Server is fully operational in local mode!")
            print("=" * 50)

    except httpx.ConnectError as e:
        print(f"\n❌ Connection Error: Cannot connect to {base_url}")
        print(f"   Make sure the container is running: docker-compose ps")
        print(f"   Error details: {e}")
    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_local_mcp())
