"""L1/L2 release guards.

L1: nightly_risk_chain.main() refuses to run (exit 1) when the startup check fails.
L2: score_daily_risk.load_model_bundle() refuses unpinned or tampered artifacts.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import nightly_risk_chain
import score_daily_risk
from src.tickets import startup_check
from src.tickets.startup_check import (
    StartupCheckResult,
    UnpinnedArtifactError,
    require_pinned_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
PINNED_DIR = ROOT / "models" / "gbm_outage_oct2026"


def test_chain_refuses_when_startup_check_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nightly_risk_chain,
        "run_startup_check",
        lambda: StartupCheckResult(passed=False, errors=["tampered"]),
    )
    monkeypatch.setattr(nightly_risk_chain, "run", lambda *a, **k: calls.append(a) or 0)
    monkeypatch.setattr(sys, "argv", ["nightly_risk_chain.py"])
    assert nightly_risk_chain.main() == 1
    assert calls == [], "no pull/score step may run after a failed startup check"


def test_pinned_nightly_bundle_loads():
    if not (PINNED_DIR / "model.joblib").exists():
        pytest.skip("model weights not present (untracked in git)")
    model, feats, meta = score_daily_risk.load_model_bundle(PINNED_DIR)
    assert len(feats) == meta["num_features"]


def test_unpinned_model_dir_refused(tmp_path):
    if not (PINNED_DIR / "model.joblib").exists():
        pytest.skip("model weights not present (untracked in git)")
    other = ROOT / "models" / "gbm_outage_production_v2"
    if not (other / "model.joblib").exists():
        pytest.skip("production_v2 bundle not present")
    # production_v2 is byte-identical for model.joblib but is not pinned by path.
    with pytest.raises(UnpinnedArtifactError, match="not pinned"):
        score_daily_risk.load_model_bundle(other)


def test_tampered_pinned_artifact_refused(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "models").mkdir(parents=True)
    target = fake_root / "models" / "m.bin"
    target.write_bytes(b"original")
    cfg = tmp_path / "release.yaml"
    good = startup_check._sha256_file(target)
    cfg.write_text(f'model_versions:\n  m:\n    path: "models/m.bin"\n    sha256: "{good}"\n')
    monkeypatch.setattr(startup_check, "ROOT", fake_root)
    assert require_pinned_artifact(target, cfg) == good
    target.write_bytes(b"tampered")
    with pytest.raises(UnpinnedArtifactError, match="hash mismatch"):
        require_pinned_artifact(target, cfg)
