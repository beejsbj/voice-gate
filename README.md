# Ambient

An ambient voice assistant in development, powered by the Voice Gate engine.

A self-hosted Jev engine that turns completed speech or typed text into **ordinary speech, a command, a retained thought, uncertainty, or no action**.

Run one engine wherever you choose. Call it from a Mac menu bar, a phone Shortcut, a browser, or an agent through HTTP, the CLI, or MCP. Transcription belongs to the client; Voice Gate receives text. No machine address, provider credential, or assistant destination is built into the engine.

## Start locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/beejsbj/voice-gate.git
cd voice-gate
uv sync --frozen
uv run voice-gate init
set -a; . ./.env; set +a
uv run voice-gate serve
```

Open `http://127.0.0.1:8000`. The generated `.env` contains your client token; paste it into the login form. The browser clears the input and uses an HTTP-only session cookie. Default `fixture` mode uses explicitly synthetic judgments for the five samples and uncertainty for other text. Nothing is sent to a provider.

For real judgments, set `VG_PROVIDER=openrouter` and `OPENROUTER_API_KEY`, or `VG_PROVIDER=typesafe` and `TYPESAFE_API_KEY`. Restart the engine. `VG_JEV_BASE_URL` and `VG_JEV_MODEL` optionally select another compatible endpoint/model. Credentials remain server-side. The official TypeSafe Python SDK is used directly.

## One request to integrate

A completed turn needs one authenticated request. Client connection settings are `VOICE_GATE_URL` and `VOICE_GATE_TOKEN` (or `VOICE_GATE_TOKEN_FILE`). These are separate from the engine's Jev provider settings.

```sh
export VOICE_GATE_URL=http://127.0.0.1:8000
# Set VOICE_GATE_TOKEN privately, or point VOICE_GATE_TOKEN_FILE at a protected file.
printf '%s' 'Save this thought: keep my running shoes beside the door.' |
  uv run voice-gate turn -
```

HTTP equivalent; tokens are sent in headers, never query strings:

```http
POST /v1/turns
Authorization: Bearer <client-token>
Content-Type: application/json

{"text":"Save this thought: keep my running shoes beside the door.",
 "request_id":"a-unique-client-generated-id"}
```

The response contains `session_id` and a `decision`: exact text and timing, one of five labels, a deterministic policy reason, raw Jev probabilities, latency, an optional capture, and handoff status. Reusing the same `request_id` with the same payload returns the original completed decision within the session/idempotency lifetime; changed payloads return `409`. No target runs from this call.

For streaming partials, create a session, submit versioned transcripts, and subscribe to SSE. Both paths use the same engine. `/docs` and `/openapi.json` describe the API. See [API behavior](docs/api.md) and [client integrations](docs/integrations.md).

## Clients and assistant targets

| Surface | Included route |
| --- | --- |
| Browser / phone browser | Responsive UI with opt-in continuous ambient listening, typed input, replay, retained thoughts and confirmed handoffs |
| Mac menu bar | SwiftBar plugin example; opens the web capture surface and shows retained count |
| Phone Shortcuts | HTTP JSON recipe; use the phone's dictation action, then POST the result |
| Hermes / Claude / Codex / compatible agent hosts | Official-SDK stdio MCP bridge and CLI; tools call your configured engine URL |
| T3 | Configure the relevant underlying agent harness; no T3-specific plugin is claimed |
| Your app | Versioned HTTP API, SSE snapshots, Python client and OpenAPI schema |

Click **Start ambient listening** once. The microphone stays armed across utterances and automatically reconnects after normal speech-service endings; no wake word or per-turn click is needed. Final text waits for a quiet window before being queued to the engine. Stop, pause, discard, permission errors, engine disconnection and provider failures stop listening. Queues and reconnect attempts are bounded. Browser speech support varies; keyboard dictation or another transcription client can submit the same API payload. Keep the browser page open and the device awake. A browser is not a dependable screen-locked/background audio service. Physical Mac/phone microphone trials and native app packaging are not claimed by the automated tests. The always-available native device listener and automatic assistant execution remain unfinished; this release provides continuous browser capture and decision routing.

`VG_TARGETS` configures destinations by name. Supported adapters:

- `health`: fixed read-only GET, useful for a harmless integration check.
- `webhook`: POST the exact original command and a versioned envelope to your service.
- `openai`: POST the original command to an OpenAI-compatible assistant endpoint, such as a configured Hermes API.

Targets are selected from server configuration, never URLs generated from speech. A recognized command creates a proposal; an explicit `confirmed: true` dispatch or UI **Send command** invokes one target. Its assistant owns its execution permissions. Voice Gate is not a sandbox for the target's tools. All commands require confirmation, including those flagged sensitive. There is no automatic journal, task, reminder, email or shell execution.

See [.env.example](.env.example) for target configuration. The MCP bridge exposes judgment/inspection tools, leaving handoff confirmation with the host or user. Do not configure a target to call Voice Gate back as its own command dispatcher.

## Deploy your own

```sh
# .env is generated above; select your provider and tokens before deploying.
docker compose up --build -d
```

The provided Compose binds only `127.0.0.1:8000` and persists captures in a Docker volume. Put an authenticated HTTPS reverse proxy in front for device access. For a private engine, a private VPN/reverse proxy is a suitable boundary. Set `VG_ORIGINS` to any separately hosted browser origins you explicitly permit; wildcard credentialed CORS is not used.

For orchestrators, `VG_CONFIG_FILE` can name a mounted JSON secret containing environment-variable names mapped to strings. Existing environment values win. Example shape without credentials:

```json
{"VG_PROVIDER":"openrouter","VG_TOKENS":"{\"personal\":\"<random-client-token>\"}","OPENROUTER_API_KEY":"<provider-key>","VG_TARGETS":"[]"}
```

Run **one process/worker** per engine: sessions, epochs, browser logins and idempotency live in memory. Do not run multiple workers against one database and expect distributed session consistency. Use `VG_DATABASE` for retained captures; `:memory:` is the development default. Docker sets `/data/captures.sqlite`. Back up file-based SQLite using its online backup interface or while the service is stopped. [Deployment notes](docs/deployment.md) cover rollback, credentials, retention and limits.

## What informed it

Source research found [Jev Voice](https://github.com/kevinbadi/jev-voice), [Jev Voice Browser](https://github.com/moritzkremb/jev-voice-browser), [Capture](https://github.com/sgaabdu4/capture), [HA-Jev](https://github.com/AboveColin/HA-Jev) and [JevRouter](https://github.com/BillionsBobby/JevRouter).

Voice Gate incorporates the useful patterns: shared typed/speech input, complete/addressed/cancelled/sensitive judgments, strict finality and stale-response rejection, explicit policy reasons, whole-request assistant adapters, literal capture provenance, and a thin MCP boundary. [Research notes](docs/research.md) link inspected revisions and distinguish source evidence from unverified demo claims. No third-party source was copied. Multi-thought segmentation and richer recall/reminder routing remain future work.

## Checks and practical limits

```sh
uv run pytest -q
node --check voice_gate/web/app.js
node --test tests/ambient.test.mjs
npm ci
CHROME_BIN=/path/to/chrome npm run test:browser
```

Tests cover session ownership/authentication, cancellation across delayed requests, revisions and finality, idempotency, exact capture persistence, concurrent dispatch, provider failures, configuration and client adapters. Browser checks include SSE, five-outcome replay, phone layout, and synthetic speech events. Browser evidence is written to ignored `test-results/`; it is not a physical microphone trial.

The experimental 0.70 policy floor is not a calibrated accuracy guarantee. Provider failure and ambiguous speech produce uncertainty. The server bounds request size, live concurrency, judgments/minute, session count, per-session turns, retained count, and target response size. It is a small single-owner/self-hosted service, not an Internet-scale multi-tenant platform.

Sessions and idempotency expire after an idle hour; completed decisions are visible for the most recent 64 turns. Captures persist until deleted when a database file is configured. Discard clears that session's retained captures, transcript/decision state and cached retry results; it cannot retract text already sent to a provider or unsend a confirmed target request. No raw audio is stored.

MIT licensed.
