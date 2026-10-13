"""tests/test_phase_r5_explainer_benchmark.py
Unit and integration tests for Phase R5: Explanation benchmark and human audit protocol.
"""

from pathlib import Path
import pytest
from src.explain.audit_evaluator import compute_cohens_kappa, conduct_human_audit
from src.explain.validator import ComprehensiveFaithfulnessValidator


def test_cohens_kappa_calculation():
    # Perfect agreement
    r1 = [5, 5, 4, 2, 1]
    r2 = [5, 5, 4, 2, 1]
    kappa = compute_cohens_kappa(r1, r2)
    assert kappa == 1.0

    # Slight disagreement
    r1 = [5, 5, 4, 2, 1]
    r2 = [5, 4, 4, 2, 1]
    kappa_imperfect = compute_cohens_kappa(r1, r2)
    assert 0.6 <= kappa_imperfect < 1.0


def test_human_audit_execution(tmp_path):
    audit_file = tmp_path / "test_human_audit.json"
    bench_file = Path("reports/remediated_explanation_benchmark.json")

    res = conduct_human_audit(benchmark_file=bench_file, output_audit_file=audit_file)
    assert "audit_metadata" in res
    assert "inter_rater_agreement" in res
    assert "audited_cases_sample" in res
    assert len(res["audited_cases_sample"]) > 0
    assert audit_file.exists()
