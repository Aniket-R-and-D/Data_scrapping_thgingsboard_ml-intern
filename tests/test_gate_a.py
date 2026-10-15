"""Tests for Gate A approval validation (no models/data needed).

The refusal path is exercised with temp live paths; it exits before any
release load, fetch, or scoring, so no ThingsBoard access is possible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.live import gate_a
from src.live import shadow_run as sr

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "configs" / "gate_a_approval.template.yaml"

APPROVED_YAML = """\
gate_a_approval:
  approver_name: 'Automated test approver'
  approver_role: 'test operator'
  approval_date: '2026-10-15'
  approved_scope: 'test scope'
  prohibited_actions:
    - 'ThingsBoard alarm creation'
    - 'technician dispatch'
    - 'external notifications'
    - 'telemetry writes'
  retention_policy: 'test retention'
  identifier_policy: 'test identifiers'
  evidence_link: ''
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_template_itself_is_not_approved():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["gate_a_approval"]
    ok, reasons = gate_a.is_approved(data)
    assert ok is False and reasons


def test_missing_file_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(gate_a.ENV_OVERRIDE, str(tmp_path / "nope.yaml"))
    with pytest.raises(gate_a.GateANotApproved, match="missing"):
        gate_a.require_approved()


def test_placeholders_refused(tmp_path, monkeypatch):
    p = _write(tmp_path / "a.yaml", APPROVED_YAML.replace("Automated test approver", "<your name>"))
    with pytest.raises(gate_a.GateANotApproved, match="approver_name"):
        gate_a.require_approved(p)


def test_bad_date_and_missing_prohibition(tmp_path):
    p = _write(tmp_path / "a.yaml", APPROVED_YAML.replace("2026-10-15", "15/10/2026"))
    ok, reasons = gate_a.is_approved(yaml.safe_load(p.read_text())["gate_a_approval"])
    assert ok is False and any("approval_date" in r for r in reasons)
    p2 = _write(tmp_path / "b.yaml", APPROVED_YAML.replace("    - 'telemetry writes'\n", ""))
    ok2, reasons2 = gate_a.is_approved(yaml.safe_load(p2.read_text())["gate_a_approval"])
    assert ok2 is False and any("telemetry writes" in r for r in reasons2)


def test_valid_approval_accepted(tmp_path):
    p = _write(tmp_path / "a.yaml", APPROVED_YAML)
    data = yaml.safe_load(p.read_text())["gate_a_approval"]
    ok, reasons = gate_a.is_approved(data)
    assert (ok, reasons) == (True, [])
    assert gate_a.require_approved(p)["approver_name"] == "Automated test approver"


@pytest.fixture
def live_min(tmp_path, monkeypatch):
    d = tmp_path / "live"
    for name, rel in [("LIVE_DIR", ""), ("CYCLES_JSONL", "cycles.jsonl"),
                      ("LOCK_FILE", "shadow.lock"), ("STOP_FILE", "STOP")]:
        monkeypatch.setattr(sr, name, d / rel if rel else d)
    d.mkdir()
    return d


class RecordingSource:
    verify_tls = True

    def __init__(self):
        self.calls = 0

    def fetch_timeseries(self, *a):
        self.calls += 1
        return []

    def fetch_alarms(self, *a):
        self.calls += 1
        return []


def test_cycle_refused_without_approval(live_min, tmp_path, monkeypatch):
    monkeypatch.setenv(gate_a.ENV_OVERRIDE, str(tmp_path / "missing.yaml"))
    src = RecordingSource()
    summary = sr.run_cycle(src)
    assert summary["status"] == "REFUSED_GATE_A_APPROVAL"
    assert summary["gate_a_reasons"]
    assert src.calls == 0  # no ThingsBoard reads attempted


def test_run_once_refusal_exit_code_and_lock_released(live_min, tmp_path, monkeypatch):
    monkeypatch.setenv(gate_a.ENV_OVERRIDE, str(tmp_path / "missing.yaml"))
    rc = sr.run_once(lambda: RecordingSource())
    assert rc == 1
    rows = [json.loads(line) for line in sr.CYCLES_JSONL.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["status"] == "REFUSED_GATE_A_APPROVAL"
    assert not sr.LOCK_FILE.exists()
