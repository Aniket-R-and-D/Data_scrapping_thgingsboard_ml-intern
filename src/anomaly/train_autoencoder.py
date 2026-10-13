"""src/anomaly/train_autoencoder.py
PyTorch Autoencoder anomaly baseline (Reconstruction Error).

Trained on normal telemetry periods (y_outage_24h == 0) to learn normal operating dynamics.
High reconstruction error indicates anomalous behavior.
Compares performance directly against deterministic rules and Isolation Forest.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.anomaly.autoencoder")

FEATURE_COLS = [
    "ac_voltage",
    "ac_voltage_mean_1h",
    "ac_voltage_std_1h",
    "ac_voltage_mean_6h",
    "ac_voltage_std_6h",
    "ac_voltage_slope_6h",
    "ac_zscore_7d",
    "battery_voltage",
    "battery_voltage_mean_1h",
    "battery_voltage_std_1h",
    "battery_voltage_mean_6h",
    "battery_discharge_rate_1h",
    "battery_discharge_rate_6h",
    "battery_zscore_7d",
    "heartbeat_gap_seconds",
    "telemetry_age_seconds",
    "missing_indicator",
    "time_since_mains_loss_hours",
    "mains_loss_count_7d",
    "fas_fault_flag",
]


class AnomalyAutoencoder(nn.Module):
    def __init__(self, input_dim: int, latent_dim: int = 6):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 14),
            nn.BatchNorm1d(14),
            nn.ReLU(),
            nn.Linear(14, latent_dim),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 14),
            nn.BatchNorm1d(14),
            nn.ReLU(),
            nn.Linear(14, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        return self.decoder(z)


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def train_and_eval_autoencoder() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    config = load_config()
    features_path = Path(config["paths"]["data_processed"]) / "features_matrix.parquet"
    events_path = Path(config["paths"]["data_processed"]) / "outage_events.parquet"
    models_dir = Path(config["paths"]["models_dir"]) / "autoencoder"
    reports_dir = Path(config["paths"]["reports_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Using device: {device} for Autoencoder training.")

    logger.info(f"Loading features from {features_path}...")
    df = pd.read_parquet(features_path)

    # Standardize features using normal samples only
    normal_mask = df["y_outage_24h"] == 0
    X_normal = df.loc[normal_mask, FEATURE_COLS].values

    means = np.mean(X_normal, axis=0)
    stds = np.std(X_normal, axis=0) + 1e-5

    X_all_norm = (df[FEATURE_COLS].values - means) / stds
    X_train_norm = (X_normal - means) / stds

    # Subsample 150k normal rows for rapid training
    if len(X_train_norm) > 150000:
        np.random.seed(42)
        idx = np.random.choice(len(X_train_norm), size=150000, replace=False)
        X_train_norm = X_train_norm[idx]

    train_tensor = torch.tensor(X_train_norm, dtype=torch.float32)
    train_loader = DataLoader(TensorDataset(train_tensor), batch_size=512, shuffle=True)

    input_dim = len(FEATURE_COLS)
    model = AnomalyAutoencoder(input_dim=input_dim, latent_dim=6).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.MSELoss()

    logger.info(f"Training Autoencoder on {len(X_train_norm)} normal samples for 4 epochs...")
    model.train()
    for epoch in range(4):
        total_loss = 0.0
        for (batch_x,) in train_loader:
            batch_x = batch_x.to(device)
            optimizer.zero_grad()
            recon = model(batch_x)
            loss = criterion(recon, batch_x)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(batch_x)
        avg_loss = total_loss / len(train_loader.dataset)
        logger.info(f"Epoch {epoch+1}/4 - MSE Loss: {avg_loss:.5f}")

    # Save model weights
    torch.save(model.state_dict(), models_dir / "autoencoder_baseline.pt")

    logger.info("Computing reconstruction errors across full dataset...")
    model.eval()
    all_tensor = torch.tensor(X_all_norm, dtype=torch.float32)
    all_loader = DataLoader(TensorDataset(all_tensor), batch_size=2048, shuffle=False)

    recon_errors = []
    with torch.no_grad():
        for (batch_x,) in all_loader:
            batch_x = batch_x.to(device)
            recon = model(batch_x)
            mse = torch.mean((recon - batch_x) ** 2, dim=1).cpu().numpy()
            recon_errors.append(mse)

    df["ae_reconstruction_error"] = np.concatenate(recon_errors)

    # Evaluate against labeled incidents
    events_df = pd.read_parquet(events_path)
    eval_metrics = evaluate_autoencoder_metrics(df, events_df)

    metrics_out = reports_dir / "phase_3_autoencoder_metrics.json"
    with open(metrics_out, "w", encoding="utf-8") as f:
        json.dump(eval_metrics, f, indent=2)
    logger.info(f"Autoencoder metrics saved to {metrics_out}.")

    return df, eval_metrics


def evaluate_autoencoder_metrics(
    df: pd.DataFrame,
    events_df: pd.DataFrame,
) -> Dict[str, Any]:
    y = df["y_outage_24h"].values
    scores = df["ae_reconstruction_error"].values
    num_devices = df["device_id"].nunique()

    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    t_min = timestamps.min()
    t_max = timestamps.max()
    duration_weeks = max(1.0, (t_max - t_min).total_seconds() / (7.0 * 86400.0))

    df_eval = df[["device_id", "ae_reconstruction_error"]].copy()
    df_eval["dt"] = timestamps
    dev_timelines = {str(did): g.set_index("dt").sort_index() for did, g in df_eval.groupby("device_id")}

    events_clean = events_df.copy()
    events_clean["start_dt"] = pd.to_datetime(events_clean["start_ts"], unit="ms", utc=True)
    valid_events = events_clean[
        (events_clean["start_dt"] >= t_min + pd.Timedelta(hours=24))
        & (events_clean["start_dt"] <= t_max)
    ].copy()
    n_valid_events = len(valid_events)

    normal_scores = scores[y == 0]
    threshold_specs = [
        ("q90", float(np.percentile(normal_scores, 90))),
        ("q95", float(np.percentile(normal_scores, 95))),
        ("q98", float(np.percentile(normal_scores, 98))),
        ("q99", float(np.percentile(normal_scores, 99))),
    ]

    results: Dict[str, Any] = {}

    print("\n" + "=" * 92)
    print(f"{'Threshold':<16} | {'Cutoff':<8} | {'Precision':<9} | {'Recall':<8} | {'F1':<6} | {'Ev Recall':<10} | {'Mean LT':<8} | {'FP Ep/dev/wk':<12}")
    print("-" * 92)

    for tag, cutoff in threshold_specs:
        pred = scores >= cutoff

        tp = int(np.sum(pred & (y == 1)))
        fp = int(np.sum(pred & (y == 0)))
        fn = int(np.sum((~pred) & (y == 1)))
        tn = int(np.sum((~pred) & (y == 0)))

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        is_fp_series = pd.Series((pred & (y == 0)).astype(int), index=df.index)
        dev_series = df["device_id"]
        fp_starts = (is_fp_series == 1) & (
            (is_fp_series.shift(1, fill_value=0) == 0) | (dev_series != dev_series.shift(1))
        )
        total_fp_episodes = int(fp_starts.sum())
        fp_episodes_per_dev_week = float(total_fp_episodes / (num_devices * duration_weeks))
        fp_steps_per_dev_week = float(fp / (num_devices * duration_weeks))

        detected_events = 0
        lead_times_hours: List[float] = []

        for _, ev in valid_events.iterrows():
            did = str(ev["device_id"])
            ev_start = ev["start_dt"]
            if did in dev_timelines:
                dev_tl = dev_timelines[did]
                win_start = ev_start - pd.Timedelta(hours=24)
                sub_win = dev_tl.loc[win_start:ev_start]
                fires = sub_win[sub_win["ae_reconstruction_error"] >= cutoff]
                if not fires.empty:
                    detected_events += 1
                    lead_h = float((ev_start - fires.index[0]).total_seconds() / 3600.0)
                    lead_times_hours.append(lead_h)

        event_recall = float(detected_events / n_valid_events) if n_valid_events > 0 else 0.0
        mean_lead_time_h = float(np.mean(lead_times_hours)) if lead_times_hours else 0.0
        median_lead_time_h = float(np.median(lead_times_hours)) if lead_times_hours else 0.0

        results[tag] = {
            "threshold_cutoff": round(cutoff, 4),
            "sample_metrics": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
            },
            "operational_metrics": {
                "fp_steps_per_dev_week": round(fp_steps_per_dev_week, 2),
                "total_fp_episodes": total_fp_episodes,
                "fp_episodes_per_dev_week": round(fp_episodes_per_dev_week, 2),
            },
            "incident_metrics": {
                "total_events_in_range": n_valid_events,
                "detected_events": detected_events,
                "event_recall": round(event_recall, 4),
                "mean_lead_time_hours": round(mean_lead_time_h, 2),
                "median_lead_time_hours": round(median_lead_time_h, 2),
            },
        }

        print(
            f"{tag:<16} | "
            f"{cutoff:8.4f} | "
            f"{prec*100:8.2f}% | "
            f"{rec*100:7.2f}% | "
            f"{f1:6.4f} | "
            f"{event_recall*100:9.1f}% | "
            f"{mean_lead_time_h:6.1f}h | "
            f"{fp_episodes_per_dev_week:12.2f}"
        )

    print("=" * 92 + "\n")
    return results


if __name__ == "__main__":
    train_and_eval_autoencoder()
