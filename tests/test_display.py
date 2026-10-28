"""MiniTooDisplay with the default configuration: same calls and timing as origin/main.

The expected traces below were produced by running the same simulations against the
origin/main display.py (21af541); the new code must reproduce them exactly.
"""

import builtins
import logging
import threading
import time

from test_display_sim import (
    ADDR,
    Device,
    Sim,
    scenario_a_events,
    scenario_a_outcome,
    scenario_b_events,
    scenario_b_outcome,
)

from hermes_minitoo import display as D

EXPECTED_A = [
    ("queued", 0, 1), ("send", 0.0, 1, {}), ("ok", 0.3),
    ("queued", 2500, 6), ("send", 2.5, 6, {}),
    ("queued", 5000, 11), ("queued", 7500, 16), ("queued", 10000, 21),
    ("fail", 10.5, "TimeoutError"),
    ("send", 10.5, 21, {}), ("ok", 10.8),
    ("queued", 12500, 26), ("send", 12.5, 26, {}), ("fail", 12.5, "ConnectionError"),
    ("sleep", 12.5, 1.0),
    ("send", 13.5, 26, {}), ("queued", 15000, 31), ("fail", 15.5, "ConnectionRefusedError"),
    ("send", 15.5, 31, {}), ("ok", 15.8),
    ("queued", 17500, 36), ("send", 17.5, 36, {}), ("ok", 17.8),
    ("queued", 20000, 41), ("send", 20.0, 41, {}), ("ok", 20.3),
]


def expected_b():
    trace = [("queued", 0, 7)]
    for t in range(0, 70):
        if t == 62:  # retry window (60 s) expired at 61 s: present() re-queues
            trace.append(("queued", 62000, 7))
        trace += [("send", float(t), 7, {}), ("fail", float(t), "ConnectionError")]
        if t != 61:  # the expired frame is dropped instead of sleeping
            trace.append(("sleep", float(t), 1.0))
    return trace + [("send", 70.0, 7, {}), ("ok", 70.3)]


def test_default_worker_sequence_matches_main_with_newer_frames(monkeypatch):
    sim = Sim(D, monkeypatch, present_at=scenario_a_events(), outcome=scenario_a_outcome)
    sim.run(25.0)
    assert sim.trace == EXPECTED_A
    assert (sim.display.dirty, sim.display.last_queued) == (False, 20000)
    assert sim.dev.screen_calls == 0  # the default path never asks for device.screen()


def test_default_worker_sequence_matches_main_through_retry_window(monkeypatch):
    sim = Sim(D, monkeypatch, present_at=scenario_b_events(), outcome=scenario_b_outcome)
    sim.run(85.0)
    assert sim.trace == expected_b()
    assert sim.dev.fb_calls == 2


def test_transport_gets_the_same_constructor_arguments(monkeypatch):
    sim = Sim(D, monkeypatch, {"channel": 2, "update_interval_ms": 3000, "chunk_delay_ms": 7,
                               "ready_timeout_ms": 9000, "reconnect_delay_ms": 100,
                               "max_payload_bytes": 5000})
    assert sim.transport_args == ((ADDR,), {
        "channel": 2, "frame_delay_ms": 3000, "chunk_delay_ms": 7, "ready_timeout_ms": 9000,
        "reconnect_delay_ms": 100, "max_payload_bytes": 5000})


def test_present_throttles_and_needs_dirty_without_reading_files(monkeypatch):
    # The removed indicator opened ~/.cache/minitoo-indicator on every present().
    def no_open(*args, **kwargs):
        raise AssertionError("present() must not open files")

    sim = Sim(D, monkeypatch)
    monkeypatch.setattr(builtins, "open", no_open)
    queued = []
    for t in range(0, 10000, 500):
        if t in (0, 1000, 6000):
            sim.display.dirty = True
        before = sim.display.last_queued
        sim.display.present(sim.dev, t)
        if sim.display.last_queued != before:
            queued.append(t)
    assert queued == [0, 2500, 6000]
    assert not hasattr(D, "INDICATOR_FILE") and not hasattr(D.MiniTooDisplay, "_indicator")


def test_retry_warnings_are_rate_limited(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=D.LOG.name)
    sim = Sim(D, monkeypatch, present_at=scenario_b_events(), outcome=scenario_b_outcome)
    sim.run(85.0)
    retries = [r.getMessage() for r in caplog.records if "display retry" in r.getMessage()]
    assert [m.split(":")[0] for m in retries] == [
        f"MiniToo display retry {n}" for n in (1, 2, 3, 4, 5, 30, 60)]
    assert retries[0] == "MiniToo display retry 1: ConnectionError('MiniToo reconnect backoff is active')"
    assert retries[-1].endswith("(60 failures in a row)")
    # Every failure is still visible at DEBUG level.
    failures = [r for r in caplog.records if "upload attempt" in r.getMessage()]
    assert len(failures) == 70 and all(r.levelno == logging.DEBUG for r in failures)


def test_failure_debug_line_has_exception_type_and_errno(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=D.LOG.name)
    sim = Sim(D, monkeypatch, present_at=scenario_a_events(), outcome=scenario_a_outcome)
    sim.run(25.0)
    lines = [r.getMessage() for r in caplog.records if "upload attempt" in r.getMessage()]
    assert "MiniToo upload attempt 1 failed: TimeoutError errno=None" in lines
    assert "MiniToo upload attempt 2 failed: ConnectionRefusedError errno=111" in lines
    assert not any("sco=" in line for line in lines)  # no probe without hfp_gate


# ---- real threads: shutdown only ----

class _BlockingTransport:
    def __init__(self, *args, **kwargs):
        self.release = threading.Event()
        self.aborted = False
        self.closed_while_sending = False
        self.sending = False

    def send_rgb888(self, rgb):
        self.sending = True
        self.release.wait(10)
        self.sending = False
        raise OSError(32, "Broken pipe")

    def abort(self):
        self.aborted = True
        self.release.set()

    def close(self):
        self.closed_while_sending = self.sending


def test_close_aborts_a_blocked_upload_instead_of_closing_under_it(monkeypatch):
    monkeypatch.setattr(D, "RFCOMMTransport", _BlockingTransport)
    d = D.MiniTooDisplay({"address": ADDR})
    d.present(Device(), 10_000)
    deadline = time.monotonic() + 2
    while not d.transport.sending and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    d.close()
    assert d.transport.aborted
    assert not d._worker.is_alive()
    assert not d.transport.closed_while_sending
    assert time.monotonic() - started < 3.5


class _FailingTransport:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    def send_rgb888(self, rgb):
        self.calls += 1
        raise ConnectionError("MiniToo reconnect backoff is active")

    def close(self):
        pass


def test_close_interrupts_the_retry_pause(monkeypatch):
    monkeypatch.setattr(D, "RFCOMMTransport", _FailingTransport)
    d = D.MiniTooDisplay({"address": ADDR})
    d.present(Device(), 10_000)
    deadline = time.monotonic() + 2
    while not d.transport.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    d.close()
    assert time.monotonic() - started < 1.0
    assert not d._worker.is_alive()
    assert d.transport.calls == 1


class _AckingSocket:
    """Answers every 0x8B announce with the ready ACK; records what is written."""

    READY = bytes.fromhex("01 07 00 04 8b 55 00 01 ec 00 02")

    def __init__(self):
        self.written = []
        self.acks = threading.Semaphore(0)
        self.done = threading.Event()

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self.written.append(bytes(data))
        if data[3] == 0x8B and data[4] == 0x00:  # announce
            self.acks.release()
        elif len(self.written) >= 4:
            self.done.set()

    def recv(self, n):
        self.acks.acquire()
        return self.READY

    def shutdown(self, how):
        self.acks.release()

    def close(self):
        pass


def test_default_stack_puts_the_expected_bytes_on_the_wire(monkeypatch):
    from hermes_minitoo.platforms.linux import transport as T
    from hermes_minitoo.protocol import build_transfer

    sock = _AckingSocket()
    monkeypatch.setattr(T, "_open_rfcomm", lambda address, channel, timeout: sock)
    monkeypatch.setattr(T, "build_lossless_animation",
                        lambda rgb, frame_delay_ms: bytes(range(256)) * 2 + rgb[:4])
    d = D.MiniTooDisplay({"address": ADDR})
    d.present(Device(), 10_000)
    assert sock.done.wait(5)
    d.close()
    rgb = D.rgb565le_to_rgb888(bytes([1]) * (160 * 128 * 2))
    transfer = build_transfer(bytes(range(256)) * 2 + rgb[:4])
    assert sock.written == [transfer.start, *transfer.chunks]


def test_worker_refresh_request_survives_a_concurrent_present(monkeypatch):
    sim = Sim(D, monkeypatch)
    d = sim.display
    d.dirty, d.last_queued = False, 5000
    d._refresh = True  # the worker gave up on a frame
    d.present(sim.dev, 6000)  # within update_interval_ms, nothing dirty: still re-queued
    assert (d._refresh, d.dirty, d.last_queued) == (False, False, 6000)
