"""Configuration validation for the MiniToo gadget client."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger(__name__)

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")

# "talk_key" belongs to scripts/minitoo-talk-key.py: only its type is checked here and
# it is never forwarded to Hermes Gadget.
_ALLOWED = {"server", "name", "token", "timezone", "audio", "minitoo", "talk_key"}
_MINITOO_ALLOWED = {
    "address", "channel", "update_interval_ms", "chunk_delay_ms",
    "ready_timeout_ms", "reconnect_delay_ms", "max_payload_bytes",
    "retry_window_s",
    # opt-in display options (see display.py); absent means off / unchanged
    "screen_change_immediate", "hfp_gate", "hfp_gate_settle_ms", "hfp_gate_post_mic_ms",
    "hfp_gate_max_s", "hci_dev", "switch_ready_timeout_ms", "listen_preroll",
    "listen_preroll_max_ms",
}
# Optional integers, validated only when present: key -> (low, high).
_MINITOO_OPTIONAL_INT = {
    "retry_window_s": (1, 3600),
    "hfp_gate_settle_ms": (0, 10000),
    "hfp_gate_post_mic_ms": (0, 30000),
    "hfp_gate_max_s": (1, 600),
    "hci_dev": (0, 31),
    "switch_ready_timeout_ms": (100, 30000),
    "listen_preroll_max_ms": (0, 5000),
}
_MINITOO_OPTIONAL_BOOL = ("screen_change_immediate", "hfp_gate", "listen_preroll")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - _ALLOWED:
        raise ValueError("unknown config field; expected server, name, token, timezone, audio, "
                         "minitoo or talk_key")
    if not isinstance(raw.get("server"), str):
        raise ValueError("server is required")
    url = urlsplit(raw["server"])
    if url.scheme not in {"ws", "wss"} or not url.hostname:
        raise ValueError("server must be a ws:// or wss:// URL")

    if "name" in raw and (not isinstance(raw["name"], str) or not raw["name"].strip()):
        raise ValueError("name must be a non-empty string")
    if "timezone" in raw:
        # The resting clock's zone (IANA name, e.g. Europe/Moscow); absent = the host's zone.
        try:
            ZoneInfo(raw["timezone"])
        except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be an IANA zone name like Europe/Moscow") from exc
    if "token" in raw:
        if not isinstance(raw["token"], str) or not raw["token"]:
            raise ValueError("token must be a non-empty string")
        if url.scheme == "ws" and not _is_loopback(url.hostname):
            LOG.warning("token is sent unencrypted to %s; use wss:// for non-local servers",
                        url.hostname)
        try:
            if path.stat().st_mode & 0o077:
                LOG.warning("%s contains a token but is accessible to other users; "
                            "run: chmod 600 %s", path, path)
        except OSError:
            pass

    mini = raw.get("minitoo")
    if not isinstance(mini, dict) or set(mini) - _MINITOO_ALLOWED:
        raise ValueError("minitoo must be an object with known MiniToo options")
    if not isinstance(mini.get("address"), str) or not MAC_RE.match(mini["address"]):
        raise ValueError("minitoo.address must be a Bluetooth MAC like AA:BB:CC:DD:EE:FF")

    numeric_ranges = {
        "channel": (1, 30, 1),
        "update_interval_ms": (250, 60000, 2500),
        "chunk_delay_ms": (0, 100, 5),
        "ready_timeout_ms": (100, 30000, 8000),
        "reconnect_delay_ms": (0, 60000, 2000),
        "max_payload_bytes": (1024, 600000, 600000),
    }
    for key, (low, high, default) in numeric_ranges.items():
        value = mini.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"minitoo.{key} must be an integer from {low} to {high}")
    for key, (low, high) in _MINITOO_OPTIONAL_INT.items():
        if key in mini and (type(mini[key]) is not int or not low <= mini[key] <= high):
            raise ValueError(f"minitoo.{key} must be an integer from {low} to {high}")
    for key in _MINITOO_OPTIONAL_BOOL:
        if key in mini and not isinstance(mini[key], bool):
            raise ValueError(f"minitoo.{key} must be true or false")

    audio = raw.get("audio", {})
    if not isinstance(audio, dict) or set(audio) - {"input", "output", "rate"}:
        raise ValueError("audio accepts input, output and rate")
    for key in ("input", "output"):
        if key in audio and not isinstance(audio[key], str):
            raise ValueError(f"audio.{key} must be a string")
    if "rate" in audio and (type(audio["rate"]) is not int or not 8000 <= audio["rate"] <= 192000):
        raise ValueError("audio.rate must be an integer from 8000 to 192000")
    if not isinstance(raw.get("talk_key", {}), dict):
        raise ValueError("talk_key must be an object")
    return raw
