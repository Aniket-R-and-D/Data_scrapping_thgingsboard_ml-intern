"""src/predict/explainer.py
Local Root-Cause Explainability and Feature Attribution Engine.

Computes marginal counterfactual feature attributions for gradient boosted
and tree-based models, isolating the top physical driving factors (e.g., AC grid loss,
battery cell degradation, load surges) for high-risk branches and generating human-readable
diagnostic explanations for ThingsBoard dashboards and operations tickets.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("explainer")

# Domain Feature Taxonomy Mapping
FEATURE_TAXONOMY = {
    "ac_voltage": "AC Grid mains failure / voltage sag",
    "battery_voltage": "DC Battery cell degradation / discharge",
    "load_current": "Load current surge / inverter overload",
    "temperature": "Elevated enclosure temperature",
    "cpu": "Gateway processor saturation",
    "cctvUptime": "CCTV communication disconnect",
    "heartbeat": "Telemetry communication latency",
    "Total_Data_Usage": "High gateway cellular data usage",
    "fan_status": "Cooling fan malfunction",
    "door_status": "Enclosure tamper / door open",
}


def _get_human_label(feature_name: str) -> str:
    """Map raw feature name to a clear, human-readable operational description."""
    for key, label in FEATURE_TAXONOMY.items():
        if key.lower() in feature_name.lower():
            # Add temporal or statistical qualifier if present
            if "__min" in feature_name:
                return f"{label} (Acute Drop)"
            elif "__max" in feature_name:
                return f"{label} (Acute Surge)"
            elif "__std" in feature_name:
                return f"{label} (High Fluctuation)"
            elif "__age_h" in feature_name:
                return f"{label} (Prolonged Stale Duration)"
            return label
    return feature_name.replace("__", " ").replace("_", " ").title()


def explain_instance_outage_risk(
    sample_values: np.ndarray,
    baseline_medians: np.ndarray,
    model: Any,
    feature_names: List[str],
    top_k: int = 3,
    candidate_limit: int = 30
) -> List[Dict[str, Any]]:
    """Compute local counterfactual marginal feature attributions for a single sample.
    
    For each candidate feature whose value deviates significantly from the healthy fleet baseline,
    we compute the marginal reduction in predicted outage probability if that feature were
    returned to its nominal baseline median:
        Delta_j = P(Y=1 | x) - P(Y=1 | x_{-j}, baseline_median_j)
    """
    x = np.asarray(sample_values, dtype=float).copy()
    base_m = np.asarray(baseline_medians, dtype=float).copy()

    # Handle NaNs in sample by replacing with baseline medians
    nan_mask = np.isnan(x)
    x[nan_mask] = base_m[nan_mask]

    # Compute base prediction probability
    x_2d = x.reshape(1, -1)
    base_prob = float(model.predict_proba(x_2d)[0, 1])

    # Rank candidate features by absolute deviation from median
    # Prioritize features with physical meaning
    deviations = np.abs(x - base_m)
    # Give higher priority to known electrical columns
    priority_mask = np.array([
        any(k.lower() in f.lower() for k in FEATURE_TAXONOMY.keys())
        for f in feature_names
    ], dtype=float)
    score_rank = deviations * (1.0 + priority_mask * 2.0)
    top_indices = np.argsort(score_rank)[::-1][:candidate_limit]

    # Evaluate counterfactual batch
    counterfactuals = np.tile(x, (len(top_indices), 1))
    for i, idx in enumerate(top_indices):
        counterfactuals[i, idx] = base_m[idx]

    cf_probs = model.predict_proba(counterfactuals)[:, 1]
    deltas = base_prob - cf_probs

    # Collect positive risk contributors
    factors: List[Dict[str, Any]] = []
    for idx, delta in zip(top_indices, deltas):
        feat_name = feature_names[idx]
        obs_val = float(sample_values[idx]) if not np.isnan(sample_values[idx]) else float(base_m[idx])
        med_val = float(base_m[idx])

        factors.append({
            "feature": feat_name,
            "label": _get_human_label(feat_name),
            "marginal_delta": float(round(delta, 4)),
            "observed_value": float(round(obs_val, 3)),
            "baseline_median": float(round(med_val, 3)),
            "direction": "INCREASES_RISK" if delta > 0 else "REDUCES_RISK"
        })

    # Sort primarily by positive marginal delta (highest risk contributors first)
    factors.sort(key=lambda item: item["marginal_delta"], reverse=True)

    # Compute relative impact percentage over positive contributors
    pos_sum = sum(max(0.0, f["marginal_delta"]) for f in factors)
    top_factors = factors[:top_k]
    for f in top_factors:
        if pos_sum > 1e-6 and f["marginal_delta"] > 0:
            f["impact_pct"] = round((f["marginal_delta"] / pos_sum) * 100.0, 1)
        else:
            f["impact_pct"] = round(100.0 / top_k, 1)
        f["formatted_summary"] = (
            f"{f['label']} ({f['impact_pct']}% impact; "
            f"observed: {f['observed_value']} vs baseline: {f['baseline_median']})"
        )

    return top_factors


def batch_explain_fleet(
    features_df: pd.DataFrame,
    model: Any,
    feature_names: List[str],
    baseline_df: Optional[pd.DataFrame] = None,
    top_k: int = 3
) -> Dict[str, Dict[str, Any]]:
    """Compute local explanations for all devices in a fleet dataframe."""
    ref_df = baseline_df if baseline_df is not None else features_df
    
    # Precompute baseline medians across features
    available_feats = [f for f in feature_names if f in ref_df.columns and f in features_df.columns]
    baseline_medians = ref_df[available_feats].median().fillna(0.0).values

    explanations: Dict[str, Dict[str, Any]] = {}

    for _, row in features_df.iterrows():
        dev_id = str(row["device_id"])
        x_vals = row[available_feats].values.astype(float)

        factors = explain_instance_outage_risk(
            sample_values=x_vals,
            baseline_medians=baseline_medians,
            model=model,
            feature_names=available_feats,
            top_k=top_k
        )

        d1 = factors[0]["formatted_summary"] if len(factors) > 0 else "Nominal operating profile"
        d2 = factors[1]["formatted_summary"] if len(factors) > 1 else "None"
        d3 = factors[2]["formatted_summary"] if len(factors) > 2 else "None"

        explanations[dev_id] = {
            "device_id": dev_id,
            "primary_driver": d1,
            "secondary_driver": d2,
            "tertiary_driver": d3,
            "factors": factors
        }

    return explanations
