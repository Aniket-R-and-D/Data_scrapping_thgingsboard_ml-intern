"""tests/test_release_startup.py — R6 fail-closed startup check tests.

Verifies src/tickets/startup_check.py against the real configs/release.yaml
(no mocks of the artifacts themselves):
1. Real release config passes with no errors and no unsafe flags.
2. Enabling the experimental LLM fails closed without an override.
3. Enabling dispatch / alarm creation / notifications fails closed.
4. An explicit override converts the failure into a loud warning (pass-through).
5. A tampered model hash fails closed.
6. A missing policy field fails closed.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from src.tickets.startup_check import DEFAULT_RELEASE_CONFIG, run_startup_check

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def release_config():
    with open(DEFAULT_RELEASE_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_temp_config(tmp_path, cfg):
    path = tmp_path / "release_test.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    return path


def test_real_release_config_passes(release_config):
    assert release_config["operational_defaults"]["experimental_llm_enabled"] is False
    assert release_config["operational_defaults"]["dispatch"] == "disabled"
    assert release_config["operational_defaults"]["alarm_creation"] == "disabled"
    assert release_config["operational_defaults"]["notifications"] == "disabled"
    assert release_config["operational_defaults"]["telemetry_mode"] == "read-only"
    assert release_config["policy_version"] == "ticket-policy-v1"

    result = run_startup_check(DEFAULT_RELEASE_CONFIG)
    assert result.passed, result.errors
    assert result.errors == []
    assert result.checked["unsafe_flags_triggered"] == []
    assert result.checked["manifest:train"] == "verified"


def test_llm_enabled_fails_closed(tmp_path, release_config):
    cfg = copy.deepcopy(release_config)
    cfg["operational_defaults"]["experimental_llm_enabled"] = True
    result = run_startup_check(_write_temp_config(tmp_path, cfg))
    assert not result.passed
    assert any("experimental_llm_enabled" in e for e in result.errors)


def test_dispatch_and_alarms_fail_closed(tmp_path, release_config):
    cfg = copy.deepcopy(release_config)
    cfg["operational_defaults"]["dispatch"] = "enabled"
    cfg["operational_defaults"]["alarm_creation"] = "enabled"
    cfg["operational_defaults"]["notifications"] = "enabled"
    result = run_startup_check(_write_temp_config(tmp_path, cfg))
    assert not result.passed
    assert any("dispatch" in e for e in result.errors)


def test_explicit_override_warns_but_passes(tmp_path, release_config, capsys):
    cfg = copy.deepcopy(release_config)
    cfg["operational_defaults"]["experimental_llm_enabled"] = True
    result = run_startup_check(
        _write_temp_config(tmp_path, cfg), allow_unsafe_override=True
    )
    assert result.passed
    assert any("OVERRIDE" in w for w in result.warnings)


def test_tampered_model_hash_fails_closed(tmp_path, release_config):
    cfg = copy.deepcopy(release_config)
    first_model = next(iter(cfg["model_versions"]))
    cfg["model_versions"][first_model]["sha256"] = "0" * 64
    result = run_startup_check(_write_temp_config(tmp_path, cfg))
    assert not result.passed
    assert any("hash mismatch" in e for e in result.errors)


def test_missing_policy_field_fails_closed(tmp_path, release_config):
    cfg = copy.deepcopy(release_config)
    del cfg["policy_version"]
    result = run_startup_check(_write_temp_config(tmp_path, cfg))
    assert not result.passed
    assert any("policy_version" in e for e in result.errors)


def test_missing_config_file_fails_closed(tmp_path):
    result = run_startup_check(tmp_path / "does_not_exist.yaml")
    assert not result.passed
