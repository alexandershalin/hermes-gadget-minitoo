# Hermes MiniToo Gadget

Experimental Linux adapter that turns a **Divoom MiniToo** into the display and speaker for a **Hermes Gadget** device.

**Hermes Gadget SDK remains the core.** Pairing, Hermes protocol, conversation state, native renderer, audio streaming and the Linux control socket come from Hermes Gadget. This repository supplies MiniToo-specific Bluetooth/display adaptation.

## Current display backend

The runtime now uses the MiniToo's **native 160×128 lossless live path**:

```text
Hermes Gadget RGB565 160×128
        ↓
RGB888 (61,440 exact bytes)
        ↓
LZO1X compress + byte-for-byte local round-trip verification
        ↓
MiniToo 0x23 animation payload: 23 01 <delay> 08 0A 00 <len> <LZO>
        ↓
0x8B announce
        ↓
wait for exact 8B 55 00 01 ready ACK
        ↓
ordered 256-byte 0x8B chunks
        ↓
MiniToo LCD
```

This replaces the original 128×128/Zstandard compatibility backend. The older path is documented on the `development` branch and is no longer used at runtime.

The lossless path is based on the real-device-verified work in `sirnugget11/divoom-minitoo-dotnet`. Linux compression uses the system `liblzo2` through Python `ctypes`; every frame is decompressed locally and compared byte-for-byte before Bluetooth transmission.

## Установка на другой хост

Быстрый путь: `./install.sh`, затем `./hermes-side/install.sh` и `./scripts/doctor.sh`. Подробности и ограничения: [docs/PORTABILITY.md](docs/PORTABILITY.md).

## Status

Implemented:

- Hermes Gadget SDK `v0.4.1` as runtime core.
- Native Hermes Gadget canvas: **160×128**.
- Lossless LZO1X RGB888 MiniToo live payload.
- Exact ready-ACK handling before chunks.
- Persistent Bluetooth Classic RFCOMM/SPP connection.
- Conservative 256-byte chunks, 5 ms pacing, 600 KB payload guard.
- Latest-frame-wins worker queue so Bluetooth cannot backlog stale Hermes screens.
- Hermes Gadget Linux audio output can target MiniToo as a normal Bluetooth speaker.
- CLI/status/control socket and research capability registry.

Still awaiting physical verification on this project's Linux host:

- the first native 160×128 Hermes screen on the user's MiniToo;
- RFCOMM + A2DP simultaneously on BlueZ/PipeWire;
- best update interval for interactive Hermes state changes;
- reconnect behavior on the actual adapter.

> **Client.** `hermes-gadget-minitoo` is a client for the Hermes Gadget SDK, a sibling of the SDK's own `hermes-gadget linux`: same structure (`client`, `control`, `audio`, `display`, `cli`), same commands, same config shape. It uses only the SDK's device core, WebSocket transport and pairing; the Linux client's code is not imported. It installs on the computer that runs Hermes. Iteration 1 supports Linux (BlueZ + PipeWire); the platform-specific code lives in `src/hermes_minitoo/platforms/linux/`.

## Install

Install it **on the computer that runs Hermes** (iteration 1: Linux with BlueZ and PipeWire/WirePlumber, the speaker within Bluetooth range of that computer).

```bash
sudo apt update
sudo apt install git python3-venv cmake build-essential libportaudio2 liblzo2-2 bluez pipewire wireplumber pipewire-bin ffmpeg

git clone https://github.com/alexandershalin/hermes-gadget-minitoo.git
cd hermes-gadget-minitoo
./install.sh --address AA:BB:CC:DD:EE:FF      # the speaker's Bluetooth address
```

`install.sh` is safe to re-run. It:

1. clones the Hermes Gadget SDK into `.sdk/` at the pinned commit and builds its native core with CMake (a few minutes on a slow machine; `--sdk-dir DIR` reuses an existing SDK checkout);
2. creates `.venv/` and installs both packages;
3. writes `config.json` from `config.template.json` (it never overwrites an existing one);
4. installs the user services `hermes-minitoo`, `minitoo-talk-key` and `minitoo-autoaddr` (`--no-services` skips them);
5. runs `scripts/doctor.sh`, a read-only check of everything above.

Options: `--server ws://HOST:8765/gadget` (default: the local Hermes), `--name`, `--with-system` (USB autosuspend rules, needs sudo).

**Pair the speaker once** with `bluetoothctl` (`scan on`, `pair MAC`, `trust MAC`, `connect MAC`). Voice needs speech-to-text and text-to-speech on the Hermes side: `./hermes-side/install.sh` installs the clients and a local Piper TTS daemon (Russian and English); the `config.yaml` lines it prints go into Hermes. Details: [docs/PORTABILITY.md](docs/PORTABILITY.md).

Start the services and approve the device:

```bash
systemctl --user enable --now hermes-minitoo minitoo-talk-key minitoo-autoaddr
hermes gadget approve CODE                    # the code is shown by: hermes-gadget-minitoo status
./scripts/doctor.sh
```

Without systemd, run the client by hand (same commands as `hermes-gadget linux`):

```bash
.venv/bin/hermes-gadget-minitoo run --config config.json
.venv/bin/hermes-gadget-minitoo status
.venv/bin/hermes-gadget-minitoo send "Hello from MiniToo"
```

Press Play/Pause on the speaker to talk (it stops by itself after a pause, or press again). Joystick while idle: left = cancel, left twice = new session, right = last reply.

For development: `.venv/bin/pytest -q` and `.venv/bin/ruff check .`; `docker build -f Dockerfile.test -t minitoo-test . && docker run --rm minitoo-test` runs the tests without any hardware.

## Configuration

Safe starting profile:

```json
{
  "server": "ws://127.0.0.1:8765/gadget",
  "name": "MiniToo Gadget",
  "minitoo": {
    "address": "AA:BB:CC:DD:EE:FF",
    "channel": 1,
    "update_interval_ms": 2500,
    "chunk_delay_ms": 5,
    "ready_timeout_ms": 8000,
    "reconnect_delay_ms": 2000,
    "max_payload_bytes": 600000
  },
  "audio": {
    "output": "Divoom MiniToo",
    "rate": 48000
  }
}
```

The 2.5-second interval is intentionally conservative for the first hardware validation. Once measured on the actual Linux server, it can be tuned downward without changing the codec.

## Known issues and decisions

[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) — what was verified on hardware, what is not, and why things are built the way they are. Installing on another host: [docs/PORTABILITY.md](docs/PORTABILITY.md).

## Research archive

Protocol notes, hardware logs and firmware research live on the [`development`](https://github.com/alexandershalin/hermes-gadget-minitoo/tree/development) branch (and the `research-archive-2026-10` tag). `main` carries only working code.

Machine-readable status:

```bash
hermes-gadget-minitoo capabilities
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Attribution

- Hermes Gadget SDK: <https://github.com/Adolanium/hermes-gadget-sdk>
- Native 160×128 MiniToo live path: <https://github.com/sirnugget11/divoom-minitoo-dotnet>
- Broader MiniToo reverse engineering: <https://github.com/bugzmanov/divoom-minitoo>
- Earlier image transport work: <https://github.com/alvinunreal/divoom-minitoo-osx>

No Divoom firmware, app binaries, decompiled vendor source, proprietary libraries or account data are redistributed here.

## License

No project license has been selected yet. Dependencies and research sources keep their respective licenses.
