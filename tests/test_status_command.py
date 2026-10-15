"""Tests for `scheduler_health --status` (offline, no ThingsBoard, no scheduler).

Covers: missing/placeholder approval => paused; valid approval with too few OK
cycles => not ready; three consecutive OK cycles => ready; scheduler disabled
=> paused; no device identifiers in output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.live import gate_a
from src.live import scheduler_health as sh
from src.live import shadow_run as sr

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

NOW = datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)


def _setup(tmp_path, monkeypatch, cycles, approval_text=APPROVED_YAML, sched=None):
    d = tmp_path / "live"
    d.mkdir()
    for name, rel in [("CYCLES_JSONL", "cycles.jsonl"), ("LOCK_FILE", "shadow.lock"),
                      ("LOG_JSONL", "shadow_predictions.jsonl"),
                      ("FETCH_STATE", "fetch_state.json")]:
        monkeypatch.setattr(sr, name, d / rel)
    sr.CYCLES_JSONL.write_text(
        "\n".join(json.dumps({"cycle_id": c, "status": s, "fetch_failures": 0,
                              "alarm_fetch_failures": 0, "validator_failures": 0},
                             sort_keys=True) for c, s in cycles) + "\n",
        encoding="utf-8")
    sr.LOG_JSONL.write_text(
        json.dumps({"cycle_id": cycles[-1][0], "device_id": "d-test-9", "risk_tier": "LOW",
                    "device_state": "ONLINE_OK", "llm_used": False, "input_complete": True,
                    "scoring_error": None, "action_taken": "LOG_ONLY_NO_DISPATCH"}) + "\n",
        encoding="utf-8")
    if approval_text is None:
        monkeypatch.setenv(gate_a.ENV_OVERRIDE, str(d / "missing.yaml"))
    else:
        (d / "gate_a_approval.yaml").write_text(approval_text, encoding="utf-8")
        monkeypatch.setenv(gate_a.ENV_OVERRIDE, str(d / "gate_a_approval.yaml"))
    monkeypatch.setattr(sh, "query_scheduler_state",
                        lambda task_name="TB_LiveShadow_G2": dict(sched or {}))
    return d


def _ok3():
    return [("2026-10-14T12:00:00+00:00", "OK"), ("2026-10-14T12:15:00+00:00", "OK"),
            ("2026-10-14T12:30:00+00:00", "OK")]


def test_missing_approval_paused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _ok3(), approval_text=None,
           sched={"determined": True, "enabled": False, "status": "Disabled",
                  "last_result": 0, "next_run": None})
    rep = sh.status(now=NOW)
    assert rep["banner"] == "LIVE OPERATION PAUSED"
    assert rep["h1_ready"] is False
    assert rep["gate_a"]["approved"] is False
    assert any("Gate A" in u for u in rep["unmet_criteria"])


def test_placeholder_approval_paused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _ok3(),
           approval_text=APPROVED_YAML.replace("Automated test approver", "PENDING"),
           sched={"determined": True, "enabled": True, "status": "Ready",
                  "last_result": 0, "next_run": "x"})
    rep = sh.status(now=NOW)
    assert rep["h1_ready"] is False and rep["gate_a"]["approved"] is False


def test_valid_approval_but_few_ok_not_ready(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [("2026-10-14T12:00:00+00:00", "OK")],
           sched={"determined": True, "enabled": True, "status": "Ready",
                  "last_result": 0, "next_run": "x"})
    rep = sh.status(now=NOW)
    assert rep["h1_ready"] is False
    assert rep["gate_a"]["approved"] is True
    assert any("three consecutive OK" in u for u in rep["unmet_criteria"])


def test_three_ok_ready(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _ok3(),
           sched={"determined": True, "enabled": True, "status": "Ready",
                  "last_result": 0, "next_run": "x"})
    rep = sh.status(now=NOW)
    assert rep["banner"] == "LIVE OPERATION READY"
    assert rep["h1_ready"] is True and rep["unmet_criteria"] == []
    assert rep["latest_cycle_status"] == "OK"
    assert rep["backlog"]["missed_slots_since_last_scored"] == 1  # 12:30 -> 13:00 slot
    text = json.dumps(rep, sort_keys=True)
    assert "d-test-9" not in text
    assert "precision" not in text and "recall" not in text


def test_scheduler_disabled_paused_despite_ok_streak(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _ok3(),
           sched={"determined": True, "enabled": False, "status": "Disabled",
                  "last_result": 0, "next_run": None})
    rep = sh.status(now=NOW)
    assert rep["h1_ready"] is False
    assert any("disabled" in u for u in rep["unmet_criteria"])


def test_unknown_scheduler_paused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _ok3(), sched={"determined": False, "enabled": None,
                                                 "status": None, "last_result": None,
                                                 "next_run": None})
    rep = sh.status(now=NOW)
    assert rep["h1_ready"] is False
    assert any("could not be determined" in u for u in rep["unmet_criteria"])
