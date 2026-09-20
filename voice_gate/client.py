"""Small HTTP client for the Voice Gate API.

The client deliberately has no policy of its own.  The service owns judgment,
capture retention, and dispatch authorization; this module only sends the
documented requests and returns their JSON responses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

import httpx


DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0


def _redact(value: Any, secret: str | None = None) -> Any:
    """Return a safe representation for errors without leaking credentials."""

    sensitive_names = {"token", "authorization", "api_key", "secret", "password"}
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if str(key).lower() in sensitive_names
            else _redact(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, secret) for item in value)
    if isinstance(value, str) and secret:
        return value.replace(secret, "[redacted]")
    return value


class VoiceGateError(RuntimeError):
    """A safe, structured API or transport error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
        secret: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = _redact(payload, secret)
        self.message = str(_redact(message, secret))
        super().__init__(self.message)

    def __str__(self) -> str:
        status = f"HTTP {self.status_code}: " if self.status_code is not None else ""
        if self.payload is not None:
            try:
                detail = json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                detail = str(self.payload)
            return f"{status}{self.message} ({detail})"
        return f"{status}{self.message}"


def read_token(*, token: str | None = None, token_file: str | os.PathLike[str] | None = None) -> str:
    """Resolve a client token from an explicit value, the environment, or a file.

    The explicit value exists for embedding the client in another Python
    process.  The command line never accepts a token argument, so tokens do
    not appear in shell history or process listings.
    """

    resolved = token
    if resolved is None:
        resolved = os.getenv("VOICE_GATE_TOKEN")
    if resolved is None:
        path_value = token_file if token_file is not None else os.getenv("VOICE_GATE_TOKEN_FILE")
        if path_value:
            try:
                resolved = Path(path_value).expanduser().read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise VoiceGateError(f"Unable to read token file: {path_value}") from exc
    if not resolved or not resolved.strip():
        raise VoiceGateError("Set VOICE_GATE_TOKEN or VOICE_GATE_TOKEN_FILE")
    return resolved.strip()


class VoiceGateClient:
    """Synchronous, bounded client for the public Voice Gate endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        token_file: str | os.PathLike[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("VOICE_GATE_URL") or DEFAULT_URL).rstrip("/")
        self.token = read_token(token=token, token_file=token_file)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )
        if client is not None:
            # Test harnesses and embedders may provide a configured transport;
            # authentication still belongs to this wrapper.
            if not self._client.base_url:
                self._client.base_url = self.base_url
            self._client.headers["Authorization"] = f"Bearer {self.token}"
            self._client.headers.setdefault("Accept", "application/json")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "VoiceGateClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, *, json_body: Any = None, params: Any = None) -> Any:
        try:
            response = self._client.request(method, path, json=json_body, params=params)
        except httpx.RequestError as exc:
            raise VoiceGateError(f"Voice Gate request failed: {exc}", secret=self.token) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_error:
            detail = payload if payload is not None else response.text[:500]
            raise VoiceGateError(
                "Voice Gate API request failed",
                status_code=response.status_code,
                payload=detail,
                secret=self.token,
            )
        if payload is None:
            raise VoiceGateError(
                "Voice Gate returned a non-JSON response",
                status_code=response.status_code,
                secret=self.token,
            )
        return payload

    def turn(
        self,
        text: str,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        epoch: int | None = None,
        source: str = "api",
        start_ms: float = 0,
        end_ms: float = 0,
    ) -> dict[str, Any]:
        """Submit one transcript and wait for the service's final decision."""

        if session_id is not None and epoch is None:
            raise VoiceGateError("epoch is required when session_id is supplied")
        body: dict[str, Any] = {
            "text": text,
            "request_id": request_id or str(uuid4()),
            "source": source,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        if session_id is not None:
            body["session_id"] = session_id
            body["epoch"] = epoch
        return self._request("POST", "/v1/turns", json_body=body)

    def list_captures(self, *, session_id: str | None = None) -> dict[str, Any]:
        params = {"session_id": session_id} if session_id is not None else None
        return self._request("GET", "/v1/captures", params=params)

    def list_sessions(self) -> dict[str, Any]:
        return self._request("GET", "/v1/sessions")

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{quote(session_id, safe='')}")

    def delete_capture(self, capture_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/captures/{quote(capture_id, safe='')}")

    def dispatch(
        self,
        session_id: str,
        decision_id: str,
        target_id: str,
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise VoiceGateError("Dispatch requires confirmed=True")
        return self._request(
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/decisions/{quote(decision_id, safe='')}/dispatch",
            json_body={"target_id": target_id, "confirmed": True},
        )

    def get_config(self) -> dict[str, Any]:
        return self._request("GET", "/v1/config")


__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_URL",
    "VoiceGateClient",
    "VoiceGateError",
    "read_token",
]
