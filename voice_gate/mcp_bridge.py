"""MCP stdio bridge for transcript interpretation and read-only inspection."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer

from .client import VoiceGateClient


mcp = MCPServer(
    "voice-gate",
    description="Transcript interpretation and retained-capture inspection for a Voice Gate service.",
    instructions="Use dispatch through the Voice Gate HTTP API after a human or host confirms it.",
)


async def _call(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    def invoke() -> dict[str, Any]:
        with VoiceGateClient() as client:
            return getattr(client, method)(*args, **kwargs)

    return await asyncio.to_thread(invoke)


@mcp.tool()
async def interpret_transcript(
    text: str,
    session_id: str | None = None,
    epoch: int | None = None,
) -> dict[str, Any]:
    """Interpret one transcript and return the final Voice Gate decision."""

    return await _call("turn", text, session_id=session_id, epoch=epoch, source="mcp")


@mcp.tool()
async def list_captures(session_id: str | None = None) -> dict[str, Any]:
    """List retained captures, optionally limited to one session."""

    return await _call("list_captures", session_id=session_id)


@mcp.tool()
async def get_session(session_id: str) -> dict[str, Any]:
    """Return a session snapshot."""

    return await _call("get_session", session_id)


def main() -> int:
    # MCP owns stdout for the stdio protocol; do not print status messages.
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
