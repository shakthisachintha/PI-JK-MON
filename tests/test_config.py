import pytest

from ray_vault.core.config import ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_example_config_loads():
    cfg, _ = load_config("config.example.toml", env={})
    assert cfg.bms.enabled and cfg.inverter.profile == "hf" and cfg.control.pin == ""


def test_legacy_flat_keys_map_to_sections(tmp_path):
    cfg, explicit = load_config(
        write(tmp_path, 'bms_mac = "AA:BB:CC:DD:EE:FF"\ndb_path = "/x.db"\nhttp_port = 9000\nraw_retention_days = 7\n'),
        env={},
    )
    assert cfg.bms.mac == "aa:bb:cc:dd:ee:ff"
    assert (cfg.storage.db_path, cfg.http.port, cfg.storage.raw_retention_days) == ("/x.db", 9000, 7)
    assert "storage.db_path" in explicit


def test_env_overrides_and_types(tmp_path):
    cfg, _ = load_config(
        write(tmp_path, "[inverter]\nport = '/dev/a'\n"),
        env={"RAYVAULT_INVERTER_ENABLED": "yes", "RAYVAULT_INVERTER_SLAVE": "3", "RAYVAULT_LOG_LEVEL": "debug"},
    )
    assert cfg.inverter.enabled is True and cfg.inverter.slave == 3 and cfg.log_level == "DEBUG"


@pytest.mark.parametrize(
    "text",
    [
        "bogus = 1\n",
        "[bms]\nnope = 1\n",
        "[inverter]\nenabled = true\n",  # no port
        "[storage]\nsample_interval_s = 0\n",
        "[http]\nport = 'abc'\n",
        "bms = 3\n",
    ],
)
def test_invalid_configs(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text), env={})


def test_missing_explicit_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml", env={})
