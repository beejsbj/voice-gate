# Ambient for the Mac menu bar

Ambient keeps a microphone session running independently of your browser. After you click **Start listening**, it transcribes locally with Apple's on-device Speech recognizer and sends finalized utterances to your engine. **Pause listening** immediately stops the microphone and asks the engine to invalidate pending work. Nothing records at launch or starts at login.

## Install

Requires macOS 14 or later, Apple's Command Line Tools (`xcode-select --install`), and an on-device speech model for your chosen locale. Full Xcode is unnecessary.

```sh
bash clients/macos/build.sh "$HOME/Applications/Ambient.app"
mkdir -p "$HOME/Library/Application Support/Ambient"
chmod 700 "$HOME/Library/Application Support/Ambient"
cp clients/macos/config.example.json "$HOME/Library/Application Support/Ambient/config.json"
chmod 600 "$HOME/Library/Application Support/Ambient/config.json"
```

Edit `config.json`: set `engineURL` to your own HTTPS engine, `tokenFile` to a private file containing its bearer token, and `locale` to an installed speech language, for example `en-US`. Save the token file with mode `600`; never add it to Git. The app refuses insecure HTTP endpoints and tokens readable by other users. The public example contains no personal server or token.

Double-click `~/Applications/Ambient.app`. Select **Ambient → Start listening** in the menu bar and allow microphone and speech recognition when macOS asks. The menu title becomes **● Ambient** while listening. If macOS blocks this locally built application, use **System Settings → Privacy & Security → Open Anyway** for your own build. The build is ad-hoc signed, not notarized.

Speak normally, with a short pause between thoughts. Local partial text is never submitted. After approximately 1.5 seconds without a transcript update, the client requests a final transcript; the next recognition task starts automatically. Tasks also rotate after 50 seconds. Open the dashboard to read captures and classifications. Commands are classified only; this client never executes them or dispatches an assistant.

**Discard session** stops recording, invalidates pending work, and deletes retained captures for the current engine session. Captures from previously rotated sessions remain available in the dashboard; delete those there. **Quit Ambient** stops recording and attempts server cancellation before exiting.

## Boundaries and troubleshooting

- Apple must support on-device recognition for the configured language. Ambient refuses to fall back to cloud speech. Speech and microphone permission are separate macOS controls.
- Recognition task boundaries can produce short gaps while the final transcript is produced. This is a continuous-session prototype, not lossless continuous audio recording. No raw audio is written to disk.
- A stalled final result is discarded after eight seconds; an unfinished partial is never promoted to final.
- Text is sent sequentially, with at most eight queued utterances. Queue overflow, provider failure, rate-budget exhaustion, or capture-storage failure stops recording. No automatic retry replays an ambiguous submitted turn.
- After 256 submitted turns the old session is paused and a new one is created, preserving earlier captures. Expired sessions are replaced when starting again.
- Pausing invalidates local callbacks immediately. If the server cannot confirm cancellation, the menu says so: open the dashboard before assuming remote pending work was cancelled.
- The authenticated engine receives finalized text, and its configured judgment provider may receive that text. On-device transcription does not make downstream classification local.
- The application stays paused after launch. Login-item installation, automatic permission grants, speech output, wake-word activation, and automatic assistant actions are not included.

## Verification

`build.sh` compiles against AppKit, AVFoundation and Speech, ad-hoc signs the app, and runs a non-recording configuration/transcript self-test. That check does not validate recognition or permissions. Actual microphone acceptance requires a user to click Start and grant access: speak two distinct turns, verify both in the dashboard, pause mid-turn, and confirm no late capture appears. Then resume and confirm a new turn is accepted; use Discard and verify current-session captures disappear.
