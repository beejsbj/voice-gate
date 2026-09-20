# API contract

Every `/v1` data route needs `Authorization: Bearer <token>` or a browser login cookie. Client identity comes from the token. Names in `VG_TOKENS` are isolation boundaries: different names cannot see each other's sessions or captures. Use the same client token on your devices if you want one shared capture collection; use separate tokens/names for separate consumers.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/turns` | Submit one final transcript and wait up to 25 seconds; no dispatch |
| `POST /v1/sessions` | Create a streaming session (`name` optional); returns `id`, `epoch`, current state |
| `GET /v1/sessions` | List the caller's active sessions |
| `GET /v1/sessions/{id}` | Current snapshot, decisions and this session's captures |
| `POST /v1/sessions/{id}/transcripts` | Accept a transcript revision; returns `202` snapshot |
| `POST /v1/sessions/{id}/controls` | `pause`, `resume`, `cancel`, `discard`, `speaking_on`, `speaking_off` |
| `GET /v1/sessions/{id}/events` | SSE `state` snapshots, with sequence IDs and heartbeat; reconnect returns current state |
| `POST /v1/sessions/{id}/decisions/{id}/dispatch` | Explicit confirmation to a configured `target_id` |
| `DELETE /v1/sessions/{id}` | Close the session, cancel pending work; retain already saved captures |
| `GET /v1/captures` | All captures owned by this client |
| `DELETE /v1/captures/{id}` | Delete one owned capture |
| `GET /v1/config` | Public provider/target names, sample corpus and limits; no secrets or destination URLs |
| `GET /healthz` | Public process liveness, not provider or assistant health |

Streaming transcript example:

```json
{
  "id": "utterance-8",
  "epoch": 0,
  "revision": 2,
  "text": "Save this exact thought.",
  "final": true,
  "complete": true,
  "start_ms": 1200,
  "end_ms": 2650,
  "source": "mac-dictation",
  "timing": "client audio timeline"
}
```

Use the epoch from the latest session snapshot. Pause, cancel, discard and speaking-on advance it; callbacks holding an older epoch return `409 stale_session_epoch`. Revisions increase within an utterance ID. Exact repeats are idempotent; reused revisions with changed text fail. After a turn is consumed, revisions cannot alter its result: submit a new explicit correction turn.

`final` means the transcriber considers its text final; `complete` means the client considers the utterance finished. Both must be true before a 650 ms cancellation window followed by Jev judgment. Open/partial turns expire after 4 seconds without a new revision. The model additionally judges semantic completeness; this does not replace the deterministic transcription flags.

The simple `/v1/turns` path assumes final+complete. With no `session_id`, it creates a session. To reuse a session, also supply its **observed `epoch`**. This requirement prevents a delayed old request from being accepted after cancellation. Never silently refresh an old callback's epoch and retry it.

Keep the same `request_id` and payload on network retry. Completed responses are cached within the bounded idempotency lifetime, even after the 64-item visible decision history rolls over. Discard removes cached response material and leaves a rejection tombstone until expiry. A cached response is evidence of the original decision, not a promise that its command remains actionable; current session state and dispatch validation own freshness.

A new transcript supersedes older pending commands. There is no execution from a probability or label alone. To dispatch a current command:

```json
{"target_id":"my-assistant","confirmed":true}
```

Concurrent repeats for the same decision/target return its current receipt and do not execute twice. A different target after dispatch returns conflict. A network/target failure is not retried automatically; inspect the result before making a fresh request. The idempotency guarantee is scoped to the running engine/session; this is not a distributed exactly-once system. On restart all sessions disappear, so old decision IDs cannot be dispatched.

The engine retains the entire final turn verbatim, including whitespace, and copies its supplied times. Browser microphone times are receipt/session timestamps, not word-aligned audio. Jev never generates the captured passage.

SSE snapshots are state synchronization, not a durable event log. Reconnection sends the current state; it does not replay commands. Audio/transcription stay outside this API.

Errors include `401` authentication, `404` missing/not-owned resource, `409` stale/superseded/conflicting request, `422` schema errors, `429` bounded resource budget, and `504` decision wait timeout. A provider outage is represented as an `uncertain` decision with `reason=provider_error`; do not treat it as an ordinary negative judgment.
