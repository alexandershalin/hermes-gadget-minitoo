# Changelog

## 0.2.3 — 2026-10-28

- `hermes-gadget-minitoo console "<line>"` runs one line of the device core's console (`set clock 60`, `get volume`, `status`).
- Config `timezone` (IANA name, e.g. `Europe/Moscow`) sets the zone of the resting clock; without it the host's zone is used (servers often run in UTC).
- STT client: sends 16 kHz WAV with a high-pass and loudness normalisation instead of 32 kbit/s mp3; the model stays `openai/whisper-large-v3-turbo` and is set by `model:` in the Hermes STT config; docs/PORTABILITY.md lists measured WER and per-request cost of the alternatives.

## 0.2.2 — 2026-10-28

- Hermes Gadget SDK updated from v0.2.0 (`323e330`) to v0.4.1 (`81f05a1`); the native core (`libhgsim`, ABI 8) must be rebuilt with `hermes-gadget build-sim`.
- The client answers the core's new `wall_time` request with the host's local time (for the resting clock).
- The `hermes-minitoo` alias of `hermes-gadget-minitoo` (kept for one release since 0.2.0) is removed; the systemd unit already runs `hermes-gadget-minitoo`.
- Fix: when the display worker gave up on a frame after `retry_window_s`, it set `dirty`/`last_queued` from its own thread and `present()` could overwrite them, so the screen waited for the next change. The worker now only raises a flag that `present()` consumes.
- New 0.3/0.4 features (Opus speaker audio, dashboard, per-profile faces, timers) also need the matching Hermes plugin; this release does not change it.

## 0.2.1 — 2026-10-10

- Repository renamed to `hermes-gadget-minitoo` (GitHub redirects the old name). Docs and research links updated. The Python package name, the local checkout path and the state directory (`~/.local/state/hermes-minitoo-gadget`, holds the pairing) keep their names.

## 0.2.0 — 2026-10-10

- **Own client, not an add-on.** `hermes-gadget-minitoo` is built like `hermes-gadget linux`: its own `client`, `control`, `audio`, `cli`; only the SDK's device core, WebSocket transport and pairing are imported. No monkey-patching of `hermes_gadget.linux` (the old `runtime.py` and the `ACTIVE` display registry are gone).
- **New executable** `hermes-gadget-minitoo` (alias `hermes-minitoo` for one release). The `hermes-gadget` shadow script from 0.1.3 is removed.
- Platform code moved to `hermes_minitoo/platforms/linux/` (transport, link table, lock).
- Microphone/preroll hooks live in `audio.Audio`; `config.json` is read as is, no translation to the SDK's config.

## 0.1.3 — 2026-10-10

- `hermes-gadget minitoo ...` next to `hermes-gadget linux ...` (same commands as `hermes-minitoo`, which stays as an alias). The package installs a `hermes-gadget` entry point that adds `minitoo` to the SDK parser and keeps all SDK commands.
- systemd unit starts the service as `hermes-gadget minitoo run`.

## 0.1.2 — 2026-10-10

- Fix: `RFCOMMTransport.last_failure` started at `0.0`, so on a host with `time.monotonic()` < `reconnect_delay` (first minute after boot) the first `connect()` was refused with "backoff is active". Found by a flaky CI run on Python 3.14.

## 0.1.1 — 2026-10-10

- `research/` removed from `main`; capability references now point to the `development` branch.
- CI matrix reduced to Python 3.13 and 3.14.
- Joystick in recording checked on hardware: left (165) does not stop the recording; right and up send only `AT+CHUP`, indistinguishable from Play. `joystick_guard` is therefore useless and off by default (see `docs/KNOWN_ISSUES.md`).

## 0.1.0 — 2026-10-10

First stable cut: everything verified on a real MiniToo.

- Native 160×128 lossless LZO1X display path over RFCOMM, ready-ACK handling, reconnect with backoff, latest-frame-wins queue.
- Hermes Gadget SDK v0.2.0 (`323e330`) as the core; MiniToo as A2DP/HFP speaker and microphone.
- `minitoo-talk-key`: Play/Pause talk toggle, adaptive VAD (`vad_ceiling`), `hfp_keys` (stop recording via `AT+CHUP`), joystick: left = cancel, double left = new session, right = last reply.
- `listen_preroll` and `hfp_gate`: `Listening` / `Thinking` screens survive the A2DP↔HFP profile switch.
- `minitoo-autoaddr` with `SAFE_SCAN`, USB autosuspend fix, WirePlumber fragments, diagnostic scripts.
- Bilingual voice (ru/en): STT `auto`, language-based TTS voice, language hint for the model.
- `install.sh`, `scripts/doctor.sh`, `hermes-side/` (STT client, Piper TTS daemon), `Dockerfile.test`.

Not hardware-verified: installers on a clean foreign host. See `docs/KNOWN_ISSUES.md`.
