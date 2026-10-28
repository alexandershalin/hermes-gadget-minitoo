"""Client / control server without the native core: a fake device stands in for NativeDevice."""

import json
import threading
from pathlib import Path

import pytest

from hermes_minitoo import client as C
from hermes_minitoo import control as K

ADDR = "AA:BB:CC:DD:EE:FF"
CONFIG = {"server": "ws://127.0.0.1:1/gadget", "name": "Test", "token": "t", "minitoo": {"address": ADDR}}


class FakeDevice:
    def __init__(self, host, **kwargs):
        self.host, self.kwargs = host, kwargs
        self.sent, self.buttons, self.events = [], [], []
        self.state = {"phase": "online", "paired": True, "screen": "ready"}

    def status(self):
        return dict(self.state)

    def screen(self):
        return self.state["screen"]

    def begin(self):
        pass

    def network(self, up, label):
        self.network_state = (up, label)

    def tick(self):
        pass

    def close(self):
        self.closed = True

    def button(self, which, pressed):
        self.buttons.append((which, pressed))

    def submit_text(self, text):
        self.sent.append(text)

    def emit_event(self, name, data, notify):
        self.events.append((name, data, notify))

    def mic_samples(self, pcm):
        self.pcm = pcm

    def console(self, line):
        self.console_lines = [*getattr(self, "console_lines", []), line]
        return "@ok"


class FakeDisplay:
    width, height = 160, 128
    listen_preroll = False

    def __init__(self, config):
        self.config, self.dirty, self.closed = config, False, False

    def poll(self, device):
        return True

    def present(self, device, now_ms):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "NativeDevice", FakeDevice)
    monkeypatch.setattr(C, "MiniTooDisplay", FakeDisplay)
    monkeypatch.setattr(C, "WsTransport", lambda: type("T", (), {"shutdown": lambda s: None})())
    c = C.Client(CONFIG, tmp_path / "state")
    yield c
    c.close()


def test_device_identity_is_minitoo_with_a_160x128_screen(client):
    kw = client.device.kwargs
    assert kw["board"] == "minitoo" and kw["name"] == "Test"
    assert (kw["width"], kw["height"]) == (160, 128)
    assert kw["mic"] is False and kw["speaker"] is False  # no audio section in CONFIG
    assert kw["button_labels"] == ("TALK", "CANCEL") and kw["scroll_buttons"] is True


def test_config_is_authoritative_but_state_stays_in_device_json(client, tmp_path):
    saved = json.loads((tmp_path / "state" / "device.json").read_text())
    assert saved == {"server": CONFIG["server"], "name": "Test", "token": "t"}
    client.storage_set("device_key", "x")
    assert client.storage_get("device_key") == "x"
    client.storage_erase("device_key")
    assert client.storage_get("device_key") is None


def test_command_status_messages_button_send_event(client):
    assert client.command({"command": "status"})["phase"] == "online"
    assert client.command({"command": "messages"}) == {"messages": []}
    assert client.command({"command": "button", "button": "cancel", "pressed": True}) == {"ok": True}
    assert client.device.buttons[-1][1] is True
    assert client.command({"command": "send", "text": "hi"}) == {"ok": True}
    assert client.device.sent == ["hi"]
    assert client.command({"command": "event", "name": "door.opened", "data": {"a": 1}, "notify": True})
    assert client.device.events == [("door.opened", {"a": 1}, True)]
    assert client.command({"command": "console", "line": " set clock 60 "}) == {"output": "@ok"}
    assert client.device.console_lines == ["set clock 60"]


@pytest.mark.parametrize("request_", [
    {"command": "nope"}, {"command": "button", "button": "x", "pressed": True},
    {"command": "button", "button": "talk", "pressed": "yes"}, {"command": "send", "text": "  "},
    {"command": "send", "text": "a\x00b"}, {"command": "event", "name": "1bad"},
    {"command": "event", "name": "ok", "notify": "yes"},
    {"command": "console", "line": " "}, {"command": "console", "line": "a\nb"}, {"command": "console"},
])
def test_invalid_commands_are_rejected(client, request_):
    with pytest.raises(ValueError):
        client.command(request_)


def test_send_needs_a_paired_online_device(client):
    client.device.state["paired"] = False
    with pytest.raises(ValueError, match="paired"):
        client.command({"command": "send", "text": "hi"})


def test_unsaved_state_stops_the_service(client, monkeypatch):
    monkeypatch.setattr(client.state, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        client.storage_set("k", "v")
    with pytest.raises(RuntimeError, match="could not be saved"):
        client.step()


def test_audio_error_while_listening_cancels(client):
    class AudioStub:
        errors = {"input": "gone"}

        def read(self):
            return b"x"

        def close(self):
            pass

    client.audio = AudioStub()
    client.device.state["screen"] = "listening"
    client.transport.events = __import__("queue").Queue()
    client.transport.generation = 0
    client.step()
    assert len(client.device.buttons) == 2  # cancel press + release
    assert not hasattr(client.device, "pcm")


def test_control_socket_round_trip(client, tmp_path, monkeypatch):
    # AF_UNIX paths are limited to ~108 bytes: use a short relative path inside tmp_path.
    monkeypatch.chdir(tmp_path)
    directory = Path("s")
    directory.mkdir()
    server = K.ControlServer(directory / "control.sock", client)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            server.poll(0.01)

    t = threading.Thread(target=serve)
    t.start()
    try:
        assert K.request(directory, {"command": "status"})["phase"] == "online"
        assert "error" in K.request(directory, {"command": "bogus"})
    finally:
        stop.set()
        t.join()
        server.close()
    assert not (directory / "control.sock").exists()


def test_request_too_large_is_refused(tmp_path):
    with pytest.raises(ValueError):
        K.request(tmp_path, {"command": "send", "text": "x" * 20000})


def test_second_service_in_the_same_state_dir_is_refused(tmp_path):
    from hermes_minitoo.platforms.linux.lock import device_lock

    with device_lock(tmp_path), pytest.raises(RuntimeError, match="another"), device_lock(tmp_path):
        pass


def test_wall_time_follows_the_configured_timezone(client):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    assert client.timezone is None  # no timezone in CONFIG: the host's local time
    client.timezone = ZoneInfo("Asia/Kolkata")  # +05:30, no daylight saving
    utc = datetime.now(timezone.utc)
    t = client.wall_time()
    assert (t.tm_hour * 60 + t.tm_min - (utc.hour * 60 + utc.minute + 330)) % 1440 in (0, 1)
