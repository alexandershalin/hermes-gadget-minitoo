"""Persistent device identity and a single-threaded host for the Hermes Gadget core.

Same shape as ``hermes_gadget.linux.client`` (MIT licence, see NOTICE.md), with the MiniToo's
own display and audio instead of SDL and PortAudio devices. The device core, the WebSocket
transport and the pairing protocol come from the Hermes Gadget SDK.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import tempfile
import time
from collections import deque
from pathlib import Path

from hermes_gadget import __version__ as core_version
from hermes_gadget.sim.native import BUTTONS, NativeDevice
from hermes_gadget.sim.transport import WsTransport

from .audio import Audio
from .display import MiniTooDisplay

BOARD = "minitoo"
DEFAULT_NAME = "MiniToo Gadget"
LOG = logging.getLogger(__name__)


class State:
    """``device.json``: device key and pairing. Written atomically."""

    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            directory.chmod(0o700)
        self.path = directory / "device.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.data, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in self.data.items()
        ):
            raise ValueError("device.json is invalid; restore the saved device state")
        if "device_key" in self.data:
            key = base64.b64decode(self.data["device_key"], validate=True)
            if len(key) != 32:
                raise ValueError("invalid saved device key; restore the saved device state")

    def save(self) -> None:
        fd, name = tempfile.mkstemp(prefix=".device-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(self.data, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
            if os.name == "posix":
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class Client:
    """``config`` is the dict returned by :func:`hermes_minitoo.config.load_config`."""

    def __init__(self, config: dict, state_dir: Path, *, library: Path | None = None):
        self.state = State(state_dir)
        self.failure: Exception | None = None
        self.started = time.monotonic()
        self.messages: deque[dict] = deque(maxlen=20)
        self.sequence = 0
        name = config.get("name", DEFAULT_NAME)
        # Configuration is authoritative on every start; identity stays in device.json.
        self.state.data.update(server=config["server"], name=name, token=config.get("token", ""))
        self.state.save()

        audio = config.get("audio", {})
        self.audio = None
        self.display = MiniTooDisplay(config["minitoo"])
        try:
            if "input" in audio or "output" in audio:
                self.audio = Audio(audio, self.display)
            self.running = True
            self.device = NativeDevice(
                self, width=self.display.width, height=self.display.height, board=BOARD,
                firmware=core_version, name=name, mic="input" in audio, speaker="output" in audio,
                mic_rate=audio.get("rate", 16000), speaker_rate=audio.get("rate", 16000),
                audio_host=self.audio, backlight=False, scroll_buttons=True, library=library,
                button_labels=("TALK", "CANCEL"), touch_screen=False, round_panel=False)
        except Exception:
            if self.audio:
                self.audio.close()
            self.display.close()
            raise
        self.transport = WsTransport()

    def start(self) -> None:
        self.device.begin()
        self._check_storage()
        self.device.network(True, "Bluetooth network")

    def step(self) -> None:
        self._check_storage()
        self.running = self.display.poll(self.device)
        # Bound each iteration so traffic cannot starve controls or the core clock.
        for _ in range(64):
            try:
                event = self.transport.events.get_nowait()
            except queue.Empty:
                break
            if event.generation != self.transport.generation:
                continue
            if event.kind == "open":
                self.device.transport_open()
            elif event.kind == "text":
                self.device.transport_text(event.data)
                try:
                    message = json.loads(event.data)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") in {
                    "reply", "transcript", "prompt", "turn.end", "notice", "error"
                }:
                    self.sequence += 1
                    entry = {key: value[:4096] for key, value in message.items()
                             if key in {"type", "text", "title", "message", "outcome", "code"}
                             and isinstance(value, str)}
                    self.messages.append({"sequence": self.sequence, **entry})
            elif event.kind == "binary":
                self.device.transport_binary(event.data)
            elif event.kind == "closed":
                self.device.transport_closed(event.data or "closed")
        if self.audio:
            pcm = self.audio.read()
            if "input" in self.audio.errors:
                if self.device.screen() == "listening":
                    self.device.button(BUTTONS["cancel"], True)
                    self.device.button(BUTTONS["cancel"], False)
            elif pcm:
                self.device.mic_samples(pcm)
        self.device.tick()
        self.display.present(self.device, self.now_ms())
        self._check_storage()

    def close(self) -> None:
        try:
            if self.audio:
                self.audio.close()
        finally:
            self.transport.shutdown()
            self.device.close()
            self.display.close()

    def command(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise TypeError("request must be an object")
        command = request.get("command")
        if command == "status":
            status = {**self.device.status(), "uptime_s": int(time.monotonic() - self.started)}
            if self.audio:
                status["audio"] = {**self.audio.devices, "errors": dict(self.audio.errors)}
            return status
        if command == "messages":
            return {"messages": list(self.messages)}
        if command == "button":
            button, pressed = request.get("button"), request.get("pressed")
            if not isinstance(button, str) or button not in BUTTONS or not isinstance(pressed, bool):
                raise ValueError("button must be talk, cancel, up or down; pressed must be boolean")
            self.device.button(BUTTONS[button], pressed)
            return {"ok": True}
        if command not in {"send", "event"}:
            raise ValueError("unknown command")
        status = self.device.status()
        if status.get("phase") != "online" or not status.get("paired"):
            raise ValueError("device is not paired and online; check status")
        if command == "send":
            text = request.get("text")
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 2000 or "\x00" in text:
                raise ValueError("text must contain 1 to 2000 UTF-8 bytes")
            self.device.submit_text(text)
        else:
            name, data, notify = request.get("name"), request.get("data", {}), request.get("notify", False)
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}", name):
                raise ValueError("invalid event name")
            if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False).encode()) > 4096:
                raise ValueError("event data must be an object of at most 4096 bytes")
            if not isinstance(notify, bool):
                raise ValueError("notify must be boolean")
            self.device.emit_event(name, data, notify)
        return {"ok": True}

    # ---- host interface called by the device core --------------------------------------

    def _check_storage(self) -> None:
        if self.failure:
            raise RuntimeError("device state could not be saved; stopping") from self.failure

    def storage_get(self, key: str) -> str | None:
        return self.state.data.get(key)

    def storage_set(self, key: str, value: str) -> None:
        self.state.data[key] = value
        try:
            self.state.save()
        except OSError as exc:
            self.failure = exc
            raise

    def storage_erase(self, key: str) -> None:
        self.state.data.pop(key, None)
        try:
            self.state.save()
        except OSError as exc:
            self.failure = exc
            raise

    def transport_connect(self, url: str, subprotocol: str) -> None:
        self.transport.connect(url, subprotocol)

    def display_flush(self, y0: int, y1: int) -> None:
        self.display.dirty = True

    def transport_send_text(self, text: str) -> bool:
        return self.transport.send(text)

    def transport_send_binary(self, data: bytes) -> bool:
        return self.transport.send(data)

    def transport_close(self) -> None:
        self.transport.close()

    def now_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def random_bytes(self, n: int) -> bytes:
        return os.urandom(n)

    def wall_time(self) -> time.struct_time:
        return time.localtime()  # the OS keeps the time and timezone

    def log(self, level: int, message: str) -> None:
        # The core logs connection state and action failures, never device keys.
        LOG.log((logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)[level], message)
