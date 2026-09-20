"""Exercise the real stdio protocol against a fixture HTTP engine."""
import asyncio
import os
from pathlib import Path
import secrets
import socket
import sys
import httpx
from mcp.client.stdio import stdio_client,StdioServerParameters
from mcp.client.session import ClientSession

async def test_stdio_initialize_list_and_tool_call():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    token=secrets.token_urlsafe(32)
    env={**os.environ,'VG_TOKENS':'{"mcp":"'+token+'"}','VG_PROVIDER':'fixture','VG_CONFIG_FILE':'','VG_DATABASE':':memory:','VG_TARGETS':'[]'}
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','uvicorn','voice_gate.api:create_app','--factory','--host','127.0.0.1','--port',str(port),'--no-access-log',env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient() as http:
            for _ in range(100):
                try:
                    if (await http.get(f'http://127.0.0.1:{port}/healthz')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.05)
            else:raise AssertionError('Fixture API did not start')
        params=StdioServerParameters(command=sys.executable,args=['-m','voice_gate.mcp_bridge'],env={**os.environ,'VOICE_GATE_URL':f'http://127.0.0.1:{port}','VOICE_GATE_TOKEN':token})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                listing=await session.list_tools()
                assert {t.name for t in listing.tools}=={'interpret_transcript','list_captures','get_session'}
                response=await session.call_tool('interpret_transcript',{'text':'The weather is nice today.'})
                assert not response.is_error
                assert 'ordinary' in str(response.content)
    finally:
        process.terminate();await asyncio.wait_for(process.wait(),10)
