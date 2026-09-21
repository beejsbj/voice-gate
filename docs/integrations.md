# Integrations

Voice Gate accepts a completed transcript over HTTP and returns one final
decision. A client sends a unique `request_id`; if it has to retry a network
request, it should reuse that same ID. The service waits up to 25 seconds for
the final result and never dispatches a command as part of `/v1/turns`.

For a turn attached to an existing session, include that session snapshot's
`epoch` as well as `session_id`. Clients reject a missing epoch locally; they
do not fetch a snapshot after a request has started, because a cancel or newer
turn may have changed the epoch.

Set the client endpoint and bearer token in the environment of each client:

```sh
export VOICE_GATE_URL=http://127.0.0.1:8000
export VOICE_GATE_TOKEN_FILE="$HOME/.config/voice-gate/token"
```

`VOICE_GATE_TOKEN` is also supported. The command line intentionally has no
token argument, so a token does not appear in shell history or process lists.

## Curl and the CLI

```sh
request_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
curl --fail-with-body --silent --show-error --config - <<EOF
url = "${VOICE_GATE_URL:-http://127.0.0.1:8000}/v1/turns"
header = "Authorization: Bearer ${VOICE_GATE_TOKEN}"
header = "Content-Type: application/json"
data = "{\"text\":\"Keep my running shoes beside the door.\",\"request_id\":\"${request_id}\",\"source\":\"curl\"}"
EOF

printf '%s\n' 'The package has a good small CLI too.' | voice-gate turn -
voice-gate captures
voice-gate sessions
```

A command decision is only executable through an explicit confirmation. The
CLI requires `--confirm`, and the API requires `{"confirmed": true}`:

```sh
voice-gate dispatch SESSION_ID DECISION_ID TARGET_ID --confirm
```

Do not automate that confirmation in a transcript hook. A new turn can
invalidate an older decision, and the dispatch endpoint performs no automatic
retry.

## Mac menu bar

Use the repository's Python environment to run the plugin. Create an executable
`voice-gate.1m.sh` in SwiftBar's plugin directory containing:

```sh
#!/bin/sh
export VOICE_GATE_URL=https://your-engine.example.com
export VOICE_GATE_TOKEN_FILE="$HOME/.config/voice-gate/token"
exec /absolute/path/to/voice-gate/.venv/bin/python /absolute/path/to/voice-gate/examples/voice-gate.1m.py
```

This avoids relying on SwiftBar's system Python to find the package. Give SwiftBar `VOICE_GATE_URL` and either `VOICE_GATE_TOKEN` or
`VOICE_GATE_TOKEN_FILE` in its environment. The plugin shows service state,
capture and session counts, and a link to the service. It does not put retained
transcript text into the menu, where it could be exposed by a screenshot or a
shared menu bar. `menu_escape()` is kept in the example for any operator who
adds a safe, deliberately chosen non-sensitive menu label.

The plugin is a status client; it is not a native ambient microphone recorder.
Open the browser and start ambient listening for continuous capture while that
page remains running. A native background listener is still required for
dependable capture when the browser is closed or the device locks.

## iPhone Shortcuts and a phone

The `examples/shortcut-request.json` file contains generic values for a
Shortcut. A practical Shortcut is:

1. Dictate Text.
2. Generate a UUID and keep the result as `request_id`.
3. Use **Get Contents of URL** with POST, the Voice Gate URL plus `/v1/turns`,
   an `Authorization: Bearer ...` header, and a JSON request body from the
   example.
4. Show the returned `decision.text` and `decision.label`.

For a phone outside the host machine, expose the service through an
operator-controlled HTTPS reverse proxy or private network and set
`VOICE_GATE_URL` accordingly. Keep the bearer token in the phone's secure
credential storage. The service has no built-in public relay and audio is not
uploaded; only the transcript supplied by the Shortcut is sent.

## MCP hosts: Hermes, Codex, Claude, and T3

`voice-gate-mcp` is a stdio MCP server. It exposes exactly three tools:
`interpret_transcript`, `list_captures`, and `get_session`. The first returns
the same final decision as `/v1/turns`; the latter two inspect retained state.
There is intentionally no MCP dispatch tool. A human or the host must call the
authenticated HTTP dispatch endpoint with explicit confirmation.

Configure an MCP host with the equivalent of this generic entry (the exact
settings file and UI differ by host):

```json
{
  "mcpServers": {
    "voice-gate": {
      "command": "voice-gate-mcp",
      "env": {
        "VOICE_GATE_URL": "http://127.0.0.1:8000",
        "VOICE_GATE_TOKEN_FILE": "/absolute/path/to/voice-gate-token"
      }
    }
  }
}
```

If the host launches a project command instead of an installed entry point,
use `uv run --project /absolute/path/to/voice-gate voice-gate-mcp` with the
same environment. The MCP host owns the child process's stdin/stdout; do not
add logging or status prints to that process's stdout.

Hermes and Codex can use the entry when their configured MCP host supports
stdio servers. Claude clients use their MCP server configuration with the
same command and environment. A T3 product may invoke an underlying model
harness; configure the MCP server on that underlying harness, then verify
that the product actually forwards tool calls. “T3 support” is therefore a
host integration question, not a claim that every T3 client has a shared
configuration file.

The official MCP Python SDK documents the `MCPServer` decorator and stdio
transport at <https://py.sdk.modelcontextprotocol.io/>. Consult each host's
current MCP documentation for its configuration location and process
environment rules.

## Configuration discovery

`GET /v1/config` returns the provider, public target IDs and names, limits, and
whether captures are persistent. It never returns target credentials. Use it
to render a status view or choose a target ID before an operator confirms a
command.
