"""scripts/thingsboard/test_active_branches.py
Inspect all non-disconnected branches in fleet.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.predict.send_daily_risk_digest import load_environment, compute_fleet_risk

env = load_environment()
fleet_df, _ = compute_fleet_risk(env)
active_df = fleet_df[~fleet_df['primary_issue'].str.startswith('Hardware Disconnected')]
print(f"Total non-disconnected branches in fleet_df: {len(active_df)}")
print(f"{'Branch Name':<20} | {'Unified Tier':<18} | {'Raw Tier':<10} | {'Score':<7} | {'P_Out':<6} | {'Primary Issue'}")
print("-" * 110)
for _, r in active_df.iterrows():
    print(f"{r['device_name']:<20} | {r['unified_tier']:<18} | {r['raw_anomaly_tier']:<10} | {r['anomaly_score']:<7} | {r['outage_prob_24h']:<6} | {r['primary_issue'][:60]}")
