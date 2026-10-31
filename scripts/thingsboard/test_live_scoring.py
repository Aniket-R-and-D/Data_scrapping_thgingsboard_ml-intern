"""scripts/thingsboard/test_live_scoring.py
Verify live fleet scoring on target branches with the fail-closed freshness gate.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.predict.send_daily_risk_digest import load_environment, compute_fleet_risk

env = load_environment()
fleet_df, summary = compute_fleet_risk(env)
names = [
    'SBI BHAWAL', 'CANARA-BURNPUR', 'BOI-CUTTACK', 'BOI-SINGH-MORE',
    'BOI-BAGULA', 'BOI-COOCHBEHAR', 'CANARA-BARUIPUR', 'CANARA-KAMARHATI',
    'CANARA-CHETLA', 'CANARA-HALDI2', 'DX7'
]
sub = fleet_df[fleet_df['device_name'].isin(names)]
print(f"{'Branch Name':<18} | {'Unified Tier':<16} | {'Raw Tier':<10} | {'Score':<7} | {'Outage P':<8} | {'Primary Issue'}")
print("-" * 115)
for _, r in sub.iterrows():
    print(f"{r['device_name']:<18} | {r['unified_tier']:<16} | {r['raw_anomaly_tier']:<10} | {r['anomaly_score']:<7} | {r['outage_prob_24h']:<8} | {r['primary_issue']}")

print("\nFleet Summary:")
for k, v in summary.items():
    print(f"  {k}: {v}")
