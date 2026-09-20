#!/usr/bin/env python3
"""SwiftBar plugin for a local Voice Gate service.

The menu deliberately shows counts and service state.  Retained transcript
text can be sensitive, so capture history is not placed in the menu.
"""

from __future__ import annotations

import html
import os
import sys
from urllib.parse import quote

from voice_gate.client import VoiceGateClient, VoiceGateError


def menu_escape(value: object) -> str:
    """Escape values used in SwiftBar's pipe-delimited menu syntax."""

    text = html.escape(str(value), quote=True)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _open_url(url: str) -> str:
    # SwiftBar parses href as a URL; quote only the URL's display value.
    return f"Open Voice Gate API | href={menu_escape(url)}"


def main() -> int:
    label = os.getenv("VOICE_GATE_LABEL", "Voice Gate")
    url = (os.getenv("VOICE_GATE_URL") or "http://127.0.0.1:8000").rstrip("/")
    try:
        with VoiceGateClient(base_url=url) as client:
            captures = client.list_captures().get("captures", [])
            sessions = client.list_sessions().get("sessions", [])
            config = client.get_config()
        print(f"{menu_escape(label)} | color=#245d4a")
        print("---")
        print(f"Captures: {len(captures)}")
        print(f"Sessions: {len(sessions)}")
        print(f"Provider: {menu_escape(config.get('provider', 'unknown'))}")
        print(_open_url(url))
    except VoiceGateError as exc:
        # VoiceGateError has already removed bearer material from its message.
        print(f"{menu_escape(label)} | color=#9b2c2c")
        print("---")
        print(f"Unavailable: {menu_escape(exc.message)}")
        print(_open_url(url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
