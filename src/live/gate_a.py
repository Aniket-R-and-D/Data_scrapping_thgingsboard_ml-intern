"""src/live/gate_a.py — Gate A approval validation (read-only polling authorization).

The live scorer refuses to touch ThingsBoard unless a completed, non-placeholder
approval file exists. The live path is `configs/gate_a_approval.yaml` (gitignored);
`configs/gate_a_approval.template.yaml` is the committed template and is NEVER
accepted as an approval. Tests point `GATE_A_APPROVAL_FILE` at a temp file.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APPROVAL_FILE = ROOT / "configs" / "gate_a_approval.yaml"
ENV_OVERRIDE = "GATE_A_APPROVAL_FILE"

REQUIRED_PROHIBITIONS = (
    "ThingsBoard alarm creation",
    "technician dispatch",
    "external notifications",
    "telemetry writes",
)

REQUIRED_TEXT_FIELDS = (
    "approver_name",
    "approver_role",
    "approval_date",
    "approved_scope",
    "retention_policy",
    "identifier_policy",
)


class GateANotApproved(RuntimeError):
    """Raised when live polling is not authorized. Carries machine-readable reasons."""

    def __init__(self, reasons: List[str]):
        super().__init__("; ".join(reasons) if reasons else "Gate A approval missing")
        self.reasons = list(reasons)


def approval_path() -> Path:
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    return Path(override) if override else DEFAULT_APPROVAL_FILE


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    if "<" in s or ">" in s:
        return True
    return low in ("pending", "tbd", "todo", "t.b.d", "xxx", "changeme", "n/a", "none")


def load_approval(path: Path | None = None) -> Dict[str, Any]:
    path = path or approval_path()
    if not path.exists():
        raise GateANotApproved([f"approval file missing: {path}"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise GateANotApproved([f"approval file unreadable: {e}"])
    if not isinstance(data, dict) or not isinstance(data.get("gate_a_approval"), dict):
        raise GateANotApproved(["approval file must contain a 'gate_a_approval' mapping"])
    return data["gate_a_approval"]


def is_approved(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Check a parsed approval mapping. Returns (ok, reasons)."""
    reasons: List[str] = []
    for field in REQUIRED_TEXT_FIELDS:
        if _is_placeholder(data.get(field)):
            reasons.append(f"{field} is missing or a placeholder")
    date = str(data.get("approval_date", "")).strip()
    if not _is_placeholder(date):
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            reasons.append("approval_date must be YYYY-MM-DD")
    prohib = data.get("prohibited_actions")
    if not isinstance(prohib, list) or not prohib:
        reasons.append("prohibited_actions must be a non-empty list")
    else:
        have = {str(x).strip().lower() for x in prohib}
        for req in REQUIRED_PROHIBITIONS:
            if req.lower() not in have:
                reasons.append(f"prohibited_actions missing: {req}")
    return (len(reasons) == 0, reasons)


def require_approved(path: Path | None = None) -> Dict[str, Any]:
    """Return the approval mapping, or raise GateANotApproved. Never returns placeholders."""
    data = load_approval(path)
    ok, reasons = is_approved(data)
    if not ok:
        raise GateANotApproved(reasons)
    return data
