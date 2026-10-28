from pathlib import Path

import hermes_minitoo
from hermes_minitoo.capabilities import CAPABILITIES, RESEARCH_URL
from hermes_minitoo.cli import parser


def test_capability_references_point_to_development_branch():
    for cap in CAPABILITIES.values():
        assert cap.reference.startswith(RESEARCH_URL), cap.reference
        assert cap.reference.split("#")[0].endswith(".md"), cap.reference


def test_common_options_work_before_and_after_subcommand():
    p = parser()
    assert p.parse_args(["--verbose", "status"]).verbose is True
    assert p.parse_args(["status", "--verbose"]).verbose is True
    assert p.parse_args(["--state-dir", "/a", "status"]).state_dir == Path("/a")
    assert p.parse_args(["status", "--state-dir", "/b"]).state_dir == Path("/b")
    assert p.parse_args(["status"]).verbose is False


def test_version_is_a_string():
    assert isinstance(hermes_minitoo.__version__, str)


def test_cli_mirrors_the_sdk_linux_client_commands():
    group = next(a for a in parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    sdk = {"run", "audio-devices", "audio-check", "status", "messages", "send", "event", "button"}
    assert sdk <= set(group.choices)
    assert "capabilities" in group.choices


def test_cli_parses_button_event_and_state_dir_in_either_position():
    p = parser()
    args = p.parse_args(["--state-dir", "/x", "button", "talk", "press"])
    assert (args.button, args.state, args.state_dir) == ("talk", "press", Path("/x"))
    args = p.parse_args(["event", "door.opened", "--data", "{}", "--notify", "--state-dir", "/y"])
    assert (args.name, args.data, args.notify, args.state_dir) == ("door.opened", "{}", True, Path("/y"))


def test_entry_points_name_the_new_command():
    import tomllib

    root = Path(__file__).resolve().parent.parent
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["hermes-gadget-minitoo"] == "hermes_minitoo.cli:main"
    assert "hermes-minitoo" not in scripts  # the 0.2.0 alias was removed in 0.2.2
    assert "hermes-gadget" not in scripts  # never shadow the SDK's own command
