from __future__ import annotations

import json
import os
import stat
import asyncio
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from voice_gate.client import VoiceGateClient, VoiceGateError, read_token


TOKEN = "client-token-012345678901234567890123"


def make_client(handler, *, token: str = TOKEN) -> tuple[VoiceGateClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    return VoiceGateClient("http://gate.test", token=token, transport=transport), requests


def test_turn_sends_exact_contract_and_bearer_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("http://gate.test/v1/turns")
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        body = json.loads(request.content)
        assert body == {
            "text": "remember this",
            "request_id": "fixed-request",
            "session_id": "session-1",
            "epoch": 3,
            "source": "shortcut",
            "start_ms": 10,
            "end_ms": 20,
        }
        return httpx.Response(200, json={"session_id": "session-1", "decision": {"label": "capture"}})

    client, _ = make_client(handler)
    try:
        result = client.turn(
            "remember this",
            request_id="fixed-request",
            session_id="session-1",
            epoch=3,
            source="shortcut",
            start_ms=10,
            end_ms=20,
        )
    finally:
        client.close()
    assert result["decision"]["label"] == "capture"


def test_turn_generates_uuid_when_request_id_is_omitted() -> None:
    client, requests = make_client(lambda request: httpx.Response(200, json={"ok": True}))
    try:
        client.turn("hello")
    finally:
        client.close()
    request_id = json.loads(requests[0].content)["request_id"]
    UUID(request_id)


def test_session_turn_requires_epoch_without_fetching_or_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    try:
        with pytest.raises(VoiceGateError, match="epoch is required"):
            client.turn("hello", session_id="session-1")
    finally:
        client.close()
    assert calls == 0


def test_dispatch_requires_confirmation_and_has_no_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/sessions/s1/decisions/d1/dispatch"
        assert json.loads(request.content) == {"target_id": "hermes", "confirmed": True}
        return httpx.Response(502, json={"detail": "target unavailable"})

    client, _ = make_client(handler)
    try:
        with pytest.raises(VoiceGateError, match="confirmed=True"):
            client.dispatch("s1", "d1", "hermes")
        with pytest.raises(VoiceGateError) as excinfo:
            client.dispatch("s1", "d1", "hermes", confirmed=True)
    finally:
        client.close()
    assert calls == 1
    assert excinfo.value.status_code == 502


def test_error_payload_and_string_redact_token() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(
            401,
            json={"detail": f"bad token {TOKEN}", "token": TOKEN},
        )
    )
    try:
        with pytest.raises(VoiceGateError) as excinfo:
            client.get_config()
    finally:
        client.close()
    error = excinfo.value
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error)
    assert error.payload == {"detail": "bad token [redacted]", "token": "[redacted]"}


def test_environment_url_and_token_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text(f"{TOKEN}\n", encoding="utf-8")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    monkeypatch.delenv("VOICE_GATE_TOKEN", raising=False)
    monkeypatch.setenv("VOICE_GATE_URL", "http://env-gate:8123/")
    monkeypatch.setenv("VOICE_GATE_TOKEN_FILE", str(token_path))
    assert read_token() == TOKEN
    client, _ = make_client(lambda request: httpx.Response(200, json={}), token=TOKEN)
    client.close()
    # The environment URL is normalized independently of the injected test transport.
    env_client = VoiceGateClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    try:
        assert env_client.base_url == "http://env-gate:8123"
    finally:
        env_client.close()


def test_read_token_requires_a_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICE_GATE_TOKEN", raising=False)
    monkeypatch.delenv("VOICE_GATE_TOKEN_FILE", raising=False)
    with pytest.raises(VoiceGateError, match="VOICE_GATE_TOKEN"):
        read_token()


def test_mcp_exposes_only_the_three_read_and_interpret_tools() -> None:
    from voice_gate.mcp_bridge import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {tool.name for tool in tools} == {"interpret_transcript", "list_captures", "get_session"}
    interpret = next(tool for tool in tools if tool.name == "interpret_transcript")
    assert "epoch" in interpret.input_schema["properties"]
