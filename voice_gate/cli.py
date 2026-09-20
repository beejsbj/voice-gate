"""The ``voice-gate`` command line client and local server launcher."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .client import DEFAULT_TIMEOUT, VoiceGateClient, VoiceGateError


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _write_init_file(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise VoiceGateError(f"Refusing to replace existing {path}; pass --force to initialize it again")
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    contents = "\n".join(
        [
            "# Voice Gate local fixture configuration",
            "VG_PROVIDER=fixture",
            "VG_DATABASE=./voice-gate.sqlite3",
            "VG_TOKENS='" + json.dumps({"local": token}, separators=(",", ":")) + "'",
            "VG_TARGETS='[]'",
            "VOICE_GATE_URL=http://127.0.0.1:8000",
            f"VOICE_GATE_TOKEN={token}",
            "",
        ]
    )
    temp = path.with_name(f".{path.name}.tmp-{secrets.token_hex(6)}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temp, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(contents)
        except BaseException:
            descriptor = -1
            raise
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            temp.unlink()


def _client(args: argparse.Namespace) -> VoiceGateClient:
    return VoiceGateClient(
        args.url,
        token_file=args.token_file,
        timeout=args.timeout,
    )


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "voice_gate.api:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
    )
    return 0


def _run_client_command(args: argparse.Namespace) -> int:
    if args.command == "init":
        _write_init_file(Path(args.env_file), force=args.force)
        print(f"Wrote {args.env_file} with fixture configuration (token kept in that file).")
        return 0
    if args.command == "serve":
        return _serve(args)

    with _client(args) as client:
        if args.command == "turn":
            text = sys.stdin.read() if args.text == "-" else args.text
            if text is None or not text.strip():
                raise VoiceGateError("turn requires text or '-' for stdin")
            result = client.turn(
                text,
                request_id=args.request_id,
                session_id=args.session_id,
                epoch=args.epoch,
                source=args.source,
                start_ms=args.start_ms,
                end_ms=args.end_ms,
            )
        elif args.command == "captures":
            result = client.list_captures(session_id=args.session_id)
        elif args.command == "sessions":
            result = client.list_sessions()
        elif args.command == "session":
            result = client.get_session(args.session_id)
        elif args.command == "config":
            result = client.get_config()
        elif args.command == "dispatch":
            if not args.confirm:
                raise VoiceGateError("dispatch requires --confirm")
            result = client.dispatch(
                args.session_id,
                args.decision_id,
                args.target_id,
                confirmed=True,
            )
        elif args.command == "delete-capture":
            result = client.delete_capture(args.capture_id)
        else:  # pragma: no cover - argparse prevents this
            raise VoiceGateError(f"Unknown command: {args.command}")
    print(_json(result))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-gate", description="Voice Gate API client and local server")
    parser.add_argument("--url", default=None, help="Voice Gate URL (default: VOICE_GATE_URL or localhost)")
    parser.add_argument("--token-file", default=None, help="Read the bearer token from this file")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="write a local fixture .env")
    init.add_argument("--env-file", default=".env")
    init.add_argument("--force", action="store_true", help="replace an existing env file")

    serve = commands.add_parser("serve", help="run the API with one uvicorn worker")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)

    turn = commands.add_parser("turn", help="submit text, or '-' to read stdin")
    turn.add_argument("text", nargs="?")
    turn.add_argument("--request-id")
    turn.add_argument("--session-id")
    turn.add_argument("--epoch", type=int)
    turn.add_argument("--source", default="api")
    turn.add_argument("--start-ms", type=float, default=0)
    turn.add_argument("--end-ms", type=float, default=0)

    captures = commands.add_parser("captures", help="list retained captures")
    captures.add_argument("--session-id")
    commands.add_parser("sessions", help="list sessions")

    session = commands.add_parser("session", help="show one session")
    session.add_argument("session_id")

    dispatch = commands.add_parser("dispatch", help="dispatch a command decision")
    dispatch.add_argument("session_id")
    dispatch.add_argument("decision_id")
    dispatch.add_argument("target_id")
    dispatch.add_argument("--confirm", action="store_true")

    delete_capture = commands.add_parser("delete-capture", help="delete one retained capture")
    delete_capture.add_argument("capture_id")
    commands.add_parser("config", help="show public service configuration")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run_client_command(args)
    except VoiceGateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
