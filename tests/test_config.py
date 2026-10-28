import json

import pytest

from hermes_minitoo.config import load_config


def test_config_keeps_minitoo_and_audio_sections(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "server": "ws://127.0.0.1:8765/gadget",
        "name": "Desk MiniToo",
        "minitoo": {
            "address": "AA:BB:CC:DD:EE:FF",
            "channel": 1,
            "update_interval_ms": 2500,
            "chunk_delay_ms": 5,
        },
        "audio": {"output": "Divoom MiniToo", "rate": 48000},
    }))
    cfg = load_config(path)
    assert cfg["minitoo"]["address"] == "AA:BB:CC:DD:EE:FF"
    assert cfg["minitoo"]["update_interval_ms"] == 2500
    assert cfg["audio"]["rate"] == 48000


def _write(tmp_path, **overrides):
    import json

    cfg = {"server": "ws://127.0.0.1:1/g", "minitoo": {"address": "AA:BB:CC:DD:EE:FF"}}
    cfg.update(overrides)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    return path


def test_address_must_be_a_mac(tmp_path):
    import pytest

    from hermes_minitoo.config import load_config

    with pytest.raises(ValueError, match="MAC"):
        load_config(_write(tmp_path, minitoo={"address": "x:y"}))


@pytest.mark.parametrize("extra", [
    {"token": 123}, {"token": ""}, {"name": 5}, {"audio": {"rate": "48000"}},
    {"audio": {"output": 1}}, {"audio": {"rate": 5}},
])
def test_rejects_bad_types(tmp_path, extra):
    from hermes_minitoo.config import load_config

    with pytest.raises(ValueError):
        load_config(_write(tmp_path, **extra))


def test_warns_on_open_token_file_and_plain_ws(tmp_path, caplog):
    from hermes_minitoo.config import load_config

    path = _write(tmp_path, token="secret", server="ws://example.com/g")
    path.chmod(0o644)
    load_config(path)
    text = caplog.text
    assert "unencrypted" in text and "chmod 600" in text


def test_retry_window_is_accepted(tmp_path):
    cfg = load_config(_write(tmp_path, minitoo={
        "address": "AA:BB:CC:DD:EE:FF", "retry_window_s": 30}))
    assert cfg["minitoo"]["retry_window_s"] == 30


def test_talk_key_section_is_accepted(tmp_path):
    cfg = load_config(_write(tmp_path, talk_key={"vad_warmup_s": 0.5, "x": [1]}))
    assert cfg["talk_key"] == {"vad_warmup_s": 0.5, "x": [1]}


@pytest.mark.parametrize("talk_key", [[], "on", 1, None])
def test_talk_key_must_be_an_object(tmp_path, talk_key):
    with pytest.raises(ValueError, match="talk_key"):
        load_config(_write(tmp_path, talk_key=talk_key))


OPTIONS = {
    "retry_window_s": 120, "screen_change_immediate": True, "hfp_gate": True,
    "hfp_gate_settle_ms": 1500, "hfp_gate_post_mic_ms": 6000, "hfp_gate_max_s": 60,
    "hci_dev": 1, "switch_ready_timeout_ms": 1500, "listen_preroll": False,
    "listen_preroll_max_ms": 1500,
}


def test_all_opt_in_display_options_are_accepted(tmp_path):
    cfg = load_config(_write(tmp_path, minitoo={"address": "AA:BB:CC:DD:EE:FF", **OPTIONS}))
    assert {k: cfg["minitoo"][k] for k in OPTIONS} == OPTIONS


def test_absent_options_stay_absent(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg["minitoo"] == {"address": "AA:BB:CC:DD:EE:FF"}


@pytest.mark.parametrize("key,value", [
    ("retry_window_s", 0), ("retry_window_s", 3601), ("retry_window_s", 1.5),
    ("retry_window_s", True), ("hfp_gate_settle_ms", -1), ("hfp_gate_settle_ms", 10001),
    ("hfp_gate_post_mic_ms", 30001), ("hfp_gate_max_s", 0), ("hfp_gate_max_s", 601),
    ("hci_dev", -1), ("hci_dev", 32), ("hci_dev", "0"), ("switch_ready_timeout_ms", 99),
    ("switch_ready_timeout_ms", 30001), ("listen_preroll_max_ms", 5001),
    ("listen_preroll_max_ms", None),
    ("hfp_gate", 1), ("hfp_gate", "true"), ("screen_change_immediate", 0),
    ("listen_preroll", None),
])
def test_bad_option_values_are_rejected(tmp_path, key, value):
    with pytest.raises(ValueError, match=f"minitoo.{key}"):
        load_config(_write(tmp_path, minitoo={"address": "AA:BB:CC:DD:EE:FF", key: value}))


def test_unknown_minitoo_option_is_still_rejected(tmp_path):
    with pytest.raises(ValueError, match="known MiniToo options"):
        load_config(_write(tmp_path, minitoo={"address": "AA:BB:CC:DD:EE:FF",
                                              "chunk_timeout_ms": 1}))


def test_timezone_is_validated(tmp_path):
    assert load_config(_write(tmp_path, timezone="Europe/Moscow"))["timezone"] == "Europe/Moscow"
    for bad in ("Mars/Olympus", 3, ""):
        with pytest.raises(ValueError, match="timezone"):
            load_config(_write(tmp_path, timezone=bad))
