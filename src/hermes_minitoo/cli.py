"""``hermes-gadget-minitoo``: run and control a MiniToo gadget.

Same commands as ``hermes-gadget linux`` (run, status, messages, send, event, button,
audio-devices, audio-check) plus ``capabilities`` and ``console``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from pathlib import Path


def default_state_dir() -> Path:
    # Deliberately not "hermes-gadget": device identity and pairing live here, renaming forces a re-pair.
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "hermes-minitoo-gadget"


def _common(p: argparse.ArgumentParser, *, top: bool) -> None:
    # Accepted both before and after the subcommand; the subparser copies use
    # SUPPRESS so they never overwrite a value given before the subcommand.
    p.add_argument("--state-dir", type=Path, default=default_state_dir() if top else argparse.SUPPRESS)
    p.add_argument("--verbose", action="store_true", default=False if top else argparse.SUPPRESS)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes-gadget-minitoo",
                                description="Run and control a Divoom MiniToo as a Hermes Gadget")
    _common(p, top=True)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kwargs)
        _common(sp, top=False)
        return sp

    run = add("run", help="Run the persistent device service")
    run.add_argument("--config", type=Path, required=True)
    add("audio-devices", help="List available input and output devices")
    check = add("audio-check", help="Record a short clip and play it back locally")
    check.add_argument("--config", type=Path, required=True)
    add("status", help="Show connection, pairing code and service uptime")
    add("messages", help="Show the last 20 replies and notices")
    add("capabilities", help="Show implemented and research-only MiniToo capabilities")
    send = add("send", help="Send a text message to Hermes")
    send.add_argument("text")
    event = add("event", help="Report a device event")
    event.add_argument("name")
    event.add_argument("--data", default="{}", help="JSON object")
    event.add_argument("--notify", action="store_true", help="Ask Hermes to handle the event")
    button = add("button", help="Press or release a device control")
    button.add_argument("button", choices=("talk", "cancel", "up", "down"))
    button.add_argument("state", choices=("press", "release"))
    console = add("console", help="Run one device console line, e.g. 'set clock 60'")
    console.add_argument("line")
    return p


def _run(config_path: Path, state_dir: Path) -> None:
    from .config import load_config
    from .control import run

    config = load_config(config_path)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        run(config, state_dir, stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "capabilities":
            from .capabilities import capabilities_dict
            print(json.dumps(capabilities_dict(), indent=2, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "run":
            _run(args.config, args.state_dir)
            return 0
        if args.command == "audio-devices":
            from .audio import backend
            print(backend().query_devices())
            return 0
        if args.command == "audio-check":
            from .audio import check_loopback
            from .config import load_config
            check_loopback(load_config(args.config).get("audio", {}))
            return 0
        from .control import request
        message: dict = {"command": args.command}
        if args.command == "send":
            message["text"] = args.text
        elif args.command == "event":
            message.update(name=args.name, data=json.loads(args.data), notify=args.notify)
        elif args.command == "button":
            message.update(button=args.button, pressed=args.state == "press")
        elif args.command == "console":
            message["line"] = args.line
        result = request(args.state_dir, message)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if "error" in result else 0
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        print(f"hermes-gadget-minitoo: {exc}")
        return 1
