"""Display of the MiniToo gadget client: framebuffer -> LZO -> RFCOMM.

Opt-in options (all absent by default; absent means the behaviour of the plain
throttled, latest-frame-wins uploader below):

- ``screen_change_immediate``: queue a frame as soon as ``device.screen()`` changes
  instead of waiting out ``update_interval_ms``.
- ``hfp_gate`` (+ ``hfp_gate_settle_ms``, ``hfp_gate_post_mic_ms``, ``hfp_gate_max_s``,
  ``hci_dev``): do not start uploads while the Gadget microphone is open or an SCO/eSCO
  link to the speaker exists, and for a short hold after either ends. The newest frame
  is kept and sent once the gate opens. Without listen_preroll the Listening frame is
  normally held too (the press opens the mic), so the screen still skips Listening, as
  today, but Thinking/Speaking land without the ready-timeout/backoff storm.
  ``switch_ready_timeout_ms`` is the ready-ACK timeout for uploads that happen while
  the gate still considers the link risky (that is, after ``hfp_gate_max_s`` released a
  long hold); it does nothing without ``hfp_gate``.
- ``listen_preroll`` (+ ``listen_preroll_max_ms``): with ``audio.Audio`` (which calls the hooks below),
  the real microphone opens only after the Listening frame was uploaded (or the cap
  expired), so the frame goes out before WirePlumber switches the speaker to HFP. Off by
  default: the owner asked to leave Listening as it is ("оставь как есть"). It delays
  the start of the recording by up to ``listen_preroll_max_ms``.
"""

from __future__ import annotations

import logging
import threading
import time

from .codec import HEIGHT, WIDTH, rgb565le_to_rgb888
from .platforms.linux.linkstate import LinkProbe
from .platforms.linux.transport import RFCOMMTransport

LOG = logging.getLogger(__name__)


# Retry warnings: the first few failures in a row are logged, then one in this many,
# so a switched-off speaker does not write a journal line every second.
_RETRY_LOG_FIRST = 5
_RETRY_LOG_EVERY = 30

_GATE_POLL_S = 0.25  # how often the HFP gate re-checks while it holds a frame
_PREROLL_SETTLE_S = 0.15  # time for the speaker to show the frame before the mic opens


class HfpGate:
    """Decides when display uploads should wait for an HFP/SCO profile switch to end.

    Signals: the Gadget microphone (runtime hooks call note_mic; opening it is what
    makes WirePlumber switch to headset-head-unit 500 ms later) and, if the kernel probe
    works, an SCO/eSCO link to the speaker (polled at most every 250 ms over one
    socket). The link is risky while the mic is open, while SCO was seen less than
    settle_s ago and for post_mic_s after the mic closed. Uploads are blocked while it
    is risky, but never for longer than max_s per episode.
    """

    def __init__(self, address: str, *, probe, settle_s: float = 1.5, post_mic_s: float = 3.0,
                 max_s: float = 60.0, clock=None, poll_s: float = _GATE_POLL_S) -> None:
        self.address = address.upper()
        self.probe = probe
        self.settle_s = settle_s
        self.post_mic_s = post_mic_s
        self.max_s = max_s
        self._clock = clock or time.monotonic
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._mic_open = False
        self._mic_closed_at = float("-inf")
        self._sco_seen_at = float("-inf")
        self._polled_at = float("-inf")
        self._sco = False
        self._probe_ok = True
        self._episode_at: float | None = None
        self._released = False

    def note_mic(self, is_open: bool) -> None:
        with self._lock:
            if self._mic_open and not is_open:
                self._mic_closed_at = self._clock()
            if is_open and not self._mic_open:
                # A new recording is a new episode, even if max_s released the last one.
                self._episode_at = None
                self._released = False
            self._mic_open = is_open

    def sco(self) -> bool | None:
        """SCO/eSCO link to the speaker exists (cached for poll_s); None if the probe fails."""
        with self._lock:
            now = self._clock()
            if now - self._polled_at >= self._poll_s:
                self._polled_at = now
                try:
                    links = self.probe.connections()
                except Exception as exc:  # OSError in practice; never kill the worker
                    if self._probe_ok:
                        LOG.warning("MiniToo HFP gate: SCO probe unavailable (%s); "
                                    "using microphone timing only", exc)
                    self._probe_ok = False
                    self._sco = False
                else:
                    if not self._probe_ok:
                        LOG.info("MiniToo HFP gate: SCO probe available again")
                    self._probe_ok = True
                    self._sco = any(link.address == self.address and link.type in ("SCO", "eSCO")
                                    for link in links)
                    if self._sco:
                        self._sco_seen_at = now
            return self._sco if self._probe_ok else None

    def risky(self) -> bool:
        sco_up = self.sco()
        with self._lock:
            now = self._clock()
            return (self._mic_open
                    or bool(sco_up)
                    or now - self._sco_seen_at < self.settle_s
                    or now - self._mic_closed_at < self.post_mic_s)

    def blocked(self) -> bool:
        risky = self.risky()
        with self._lock:
            if not risky:
                self._episode_at = None
                self._released = False
                return False
            now = self._clock()
            if self._episode_at is None:
                self._episode_at = now
            if not self._released and now - self._episode_at >= self.max_s:
                self._released = True
                LOG.warning("MiniToo HFP gate held uploads for %.0fs; sending anyway", self.max_s)
            return not self._released

    def close(self) -> None:
        close = getattr(self.probe, "close", None)
        if close is not None:
            close()


class MiniTooDisplay:
    """The device core's framebuffer, uploaded to a Divoom MiniToo over RFCOMM."""

    def __init__(self, config: dict):
        self.width = WIDTH
        self.height = HEIGHT
        self.touch = False
        self.round = False
        self.dirty = True

        self.update_interval_ms = int(config.get("update_interval_ms", 2500))
        self.last_queued = -self.update_interval_ms
        # How long one snapshot is retried before present() is asked for a fresh one.
        # Retrying itself never stops.
        self.retry_window_s = float(config.get("retry_window_s", 60))
        self.transport = RFCOMMTransport(
            config["address"],
            channel=int(config.get("channel", 1)),
            frame_delay_ms=self.update_interval_ms,
            chunk_delay_ms=int(config.get("chunk_delay_ms", 5)),
            ready_timeout_ms=int(config.get("ready_timeout_ms", 8000)),
            reconnect_delay_ms=int(config.get("reconnect_delay_ms", 2000)),
            max_payload_bytes=int(config.get("max_payload_bytes", 600000)),
        )

        # ---- opt-in options (absent = off) ----
        self.screen_change_immediate = bool(config.get("screen_change_immediate", False))
        self.listen_preroll = bool(config.get("listen_preroll", False))
        self.listen_preroll_max_s = int(config.get("listen_preroll_max_ms", 1500)) / 1000.0
        switch_ms = config.get("switch_ready_timeout_ms")
        self.switch_ready_timeout = None if switch_ms is None else int(switch_ms) / 1000.0
        self._gate: HfpGate | None = None
        if config.get("hfp_gate", False):
            self._gate = HfpGate(
                config["address"],
                probe=LinkProbe(int(config.get("hci_dev", 0))),
                settle_s=int(config.get("hfp_gate_settle_ms", 1500)) / 1000.0,
                post_mic_s=int(config.get("hfp_gate_post_mic_ms", 3000)) / 1000.0,
                max_s=float(config.get("hfp_gate_max_s", 60)),
            )
        elif self.switch_ready_timeout is not None:
            LOG.warning("minitoo.switch_ready_timeout_ms has no effect without minitoo.hfp_gate")
        self._watch_screen = self.screen_change_immediate or self.listen_preroll
        self._queued_screen: str | None = None
        if self._watch_screen or self._gate is not None:
            LOG.info("MiniToo display options: screen_change_immediate=%s hfp_gate=%s "
                     "switch_ready_timeout=%s listen_preroll=%s", self.screen_change_immediate,
                     self._gate is not None, self.switch_ready_timeout, self.listen_preroll)

        # listen_preroll state (see arm_listening / preroll_ready)
        self.listening_sent = threading.Event()
        self._listening_sent_at: float | None = None
        self._preroll_gen = 0
        self._preroll_armed = False
        self._preroll_started = 0.0
        self._preroll_deadline = 0.0

        self._condition = threading.Condition()
        self._pending: bytes | None = None
        self._pending_tag: int | None = None  # preroll generation of a Listening frame
        self._closed = False
        # Set only by close(): makes the 1 s retry pause interruptible without
        # changing when frames are retried during normal operation.
        self._stop = threading.Event()
        self._fail_streak = 0
        self._refresh = False  # set by the worker, consumed by present()
        self._worker = threading.Thread(target=self._run, name="minitoo-display", daemon=True)
        self._worker.start()

    def poll(self, device) -> bool:
        return True

    def present(self, device, now_ms: int) -> None:
        if self._refresh:
            # The worker gave up on a frame: take a fresh snapshot now. Only this (main)
            # thread writes dirty/last_queued, so the request cannot be overwritten.
            self._refresh = False
            self.dirty = True
            self.last_queued = -self.update_interval_ms
        if not self.dirty:
            return
        screen = self._screen(device) if self._watch_screen else None
        immediate = (screen is not None and screen != self._queued_screen
                     and (self.screen_change_immediate
                          or (self.listen_preroll and screen == "listening")))
        if now_ms - self.last_queued < self.update_interval_ms and not immediate:
            return

        raw = device.framebuffer_rows()
        expected = self.width * self.height * 2
        if len(raw) != expected:
            raise RuntimeError(
                f"unexpected Hermes framebuffer size {len(raw)}; expected {expected}"
            )
        rgb = rgb565le_to_rgb888(raw)
        tag = self._preroll_gen if self._preroll_armed and screen == "listening" else None
        with self._condition:
            # Latest-frame-wins: never build a backlog of stale Hermes screens.
            self._pending = rgb
            self._pending_tag = tag
            self._condition.notify()
        self.dirty = False
        self.last_queued = now_ms
        if screen is not None:
            self._queued_screen = screen

    @staticmethod
    def _screen(device) -> str | None:
        screen = getattr(device, "screen", None)
        if not callable(screen):
            return None
        try:
            return screen()
        except Exception:
            return None

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                rgb, tag = self._pending, self._pending_tag
                self._pending = None
            assert rgb is not None
            if self._gate is not None:
                held = self._wait_for_gate(rgb, tag)
                if held is None:
                    return
                rgb, tag = held
            started = time.monotonic()
            attempt = 0
            while True:
                ready_timeout = self._ready_timeout()
                try:
                    if ready_timeout is None:
                        self.transport.send_rgb888(rgb)
                    else:
                        self.transport.send_rgb888(rgb, ready_timeout=ready_timeout)
                    self._fail_streak = 0
                    if tag is not None:
                        self._note_listening_sent(tag)
                    if attempt:
                        LOG.info("MiniToo display recovered after %d retries (%.1fs)",
                                 attempt, time.monotonic() - started)
                    break
                except Exception as exc:
                    attempt += 1
                    self._fail_streak += 1
                    elapsed = time.monotonic() - started
                    detail = self._failure_detail()
                    LOG.debug("MiniToo upload attempt %d failed: %s errno=%s%s",
                              attempt, type(exc).__name__, getattr(exc, "errno", None), detail)
                    # Transient outages (e.g. BT profile switch after recording):
                    # retry the same frame until it lands, unless a newer one arrived.
                    if elapsed > self.retry_window_s:
                        LOG.warning("MiniToo display update failed after %.0fs: %s%s",
                                    elapsed, exc, detail)
                        # Do not lose the screen: present() takes a fresh snapshot.
                        self._refresh = True
                        break
                    streak = self._fail_streak
                    if streak <= _RETRY_LOG_FIRST or streak % _RETRY_LOG_EVERY == 0:
                        more = f" ({streak} failures in a row)" if streak > _RETRY_LOG_FIRST else ""
                        LOG.warning("MiniToo display retry %d: %r%s%s", attempt, exc, detail, more)
                    # The switch started during this upload: hold the frame behind the
                    # gate instead of burning ready timeouts and backoffs.
                    hold = self._gate is not None and self._gate.blocked()
                    with self._condition:
                        if self._pending is not None or self._closed:
                            break
                        if hold:
                            self._pending, self._pending_tag = rgb, tag
                            break
                    if self._stop.wait(1.0):
                        break

    def _wait_for_gate(self, rgb: bytes, tag: int | None) -> tuple[bytes, int | None] | None:
        """Hold the upload while the HFP gate is closed; a newer frame replaces the held one.

        Returns the frame to send, or None when the display is closing.
        """
        assert self._gate is not None
        paused_at = None
        while self._gate.blocked():
            if paused_at is None:
                paused_at = time.monotonic()
                LOG.info("MiniToo display paused: HFP/SCO")
            with self._condition:
                if not self._closed and self._pending is None:
                    self._condition.wait(_GATE_POLL_S)
                if self._closed:
                    return None
                if self._pending is not None:
                    rgb, tag = self._pending, self._pending_tag
                    self._pending = None
        if paused_at is not None:
            LOG.info("MiniToo display resumed after %.1fs", time.monotonic() - paused_at)
        return rgb, tag

    def _ready_timeout(self) -> float | None:
        if (self.switch_ready_timeout is not None and self._gate is not None
                and self._gate.risky()):
            return self.switch_ready_timeout
        return None

    def _failure_detail(self) -> str:
        if self._gate is None:
            return ""
        sco = self._gate.sco()
        return " sco=" + ("?" if sco is None else "up" if sco else "down")

    # ---- hooks called by audio.Audio ----

    def note_mic(self, is_open: bool) -> None:
        """The Gadget microphone stream was really opened or closed."""
        if self._gate is not None:
            self._gate.note_mic(is_open)
        with self._condition:
            self._condition.notify_all()

    def arm_listening(self) -> None:
        """listen_preroll: the Gadget asked for the mic; wait for the Listening frame first."""
        now = time.monotonic()
        self.listening_sent.clear()
        self._listening_sent_at = None
        self._preroll_started = now
        self._preroll_deadline = now + self.listen_preroll_max_s
        self._preroll_gen += 1
        self._preroll_armed = True

    def preroll_ready(self) -> bool:
        """True once the mic may open: the Listening frame was uploaded and had
        _PREROLL_SETTLE_S to show, or listen_preroll_max_ms passed."""
        if not self._preroll_armed:
            return True
        now = time.monotonic()
        sent_at = self._listening_sent_at
        return now >= self._preroll_deadline or (
            sent_at is not None and now - sent_at >= _PREROLL_SETTLE_S)

    def end_preroll(self) -> None:
        """The deferred mic was opened (or the Gadget stopped listening)."""
        if not self._preroll_armed:
            return
        self._preroll_armed = False
        LOG.info("MiniToo listen preroll: microphone after %.0f ms (Listening frame %s)",
                 (time.monotonic() - self._preroll_started) * 1000,
                 "sent" if self.listening_sent.is_set() else "not sent")

    def _note_listening_sent(self, tag: int) -> None:
        if self._preroll_armed and tag == self._preroll_gen and not self.listening_sent.is_set():
            self._listening_sent_at = time.monotonic()
            self.listening_sent.set()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._stop.set()
        # Same grace period as before for an upload in progress ...
        self._worker.join(timeout=2)
        abort = getattr(self.transport, "abort", None)
        if self._worker.is_alive() and abort is not None:
            # ... then wake a worker blocked in recv()/sendall() instead of closing the
            # socket under it, and stop it from opening a new one.
            abort()
            self._worker.join(timeout=2)
        self.transport.close()
        if self._gate is not None:
            self._gate.close()
