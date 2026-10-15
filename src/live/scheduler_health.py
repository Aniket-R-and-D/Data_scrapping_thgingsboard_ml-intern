"""src/live/scheduler_health.py — G2 scheduler-health check (read-only, local logs only).

Detects:
  - missed expected 15-minute slots (from first scored cycle to now)
  - stale lock files (age > LOCK_STALE_AFTER, default 14 min from shadow_run)
  - repeated non-zero task results (explicit history; live query gives last result only)
  - cycles with fetch failures (telemetry or alarm fetch)
  - cycles with validator failures

No ThingsBoard access. Reads only data/live/cycles.jsonl and data/live/shadow.lock.

Usage:
    python -m src.live.scheduler_health            # human summary, exit 0/1
    python -m src.live.scheduler_health --json     # JSON report on stdout
    python -m src.live.scheduler_health --task-results 0,3221225786,0

Exit codes:
    0  healthy (no issues)
    1  issues found (missed slots, stale lock, non-zero task result, fetch/validator failures)
    2  cannot assess (no cycles logged yet, or unreadable logs)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live import shadow_run as sr

SLOT = timedelta(minutes=15)


def _parse_cycle_id(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_cycles() -> List[Dict[str, Any]]:
    if not sr.CYCLES_JSONL.exists():
        return []
    out = []
    for line in sr.CYCLES_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def missed_slots(cycles: List[Dict[str, Any]], now: datetime) -> List[str]:
    """Expected 15-min slots from first scored cycle to current slot (inclusive of past, exclusive of future)."""
    scored = sorted({c.get("cycle_id") for c in cycles
                     if c.get("status") in ("OK", "DEGRADED") and _parse_cycle_id(c.get("cycle_id", ""))})
    if not scored:
        return []
    first = _parse_cycle_id(scored[0])
    assert first is not None
    cutoff_now = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
    # Slots strictly before the current in-progress slot are expected to be logged.
    cur = first
    missing = []
    have = set(scored)
    while cur < cutoff_now:
        if cur.isoformat() not in have:
            missing.append(cur.isoformat())
        cur += SLOT
    return missing


def lock_status(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    p = sr.LOCK_FILE
    if not p.exists():
        return {"exists": False, "stale": False, "age_seconds": None}
    try:
        age = (now - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)).total_seconds()
    except OSError:
        return {"exists": False, "stale": False, "age_seconds": None}
    stale_after = sr.LOCK_STALE_AFTER.total_seconds()
    try:
        pid = p.read_text(encoding="utf-8").strip()
    except OSError:
        pid = None
    return {"exists": True, "stale": age >= stale_after, "age_seconds": round(age, 1),
            "stale_after_seconds": stale_after, "pid": pid}


def task_result_status(history: Optional[List[int]] = None,
                       last_result: Optional[int] = None) -> Dict[str, Any]:
    """Repeated non-zero detection needs a history list (oldest..newest).

    Live Windows scheduler only exposes the single LastTaskResult, so callers
    may pass history explicitly via --task-results for testing. When only
    last_result is known, repeated is reported as None (unknown).
    """
    if history is not None:
        nz = [int(x) != 0 for x in history]
        repeated = len(nz) >= 2 and nz[-1] and nz[-2]
        return {"history": [int(x) for x in history],
                "last_result": int(history[-1]) if history else None,
                "nonzero": bool(nz[-1]) if nz else False,
                "repeated_nonzero": bool(repeated),
                "history_available": True}
    if last_result is not None:
        return {"history": None, "last_result": int(last_result),
                "nonzero": int(last_result) != 0,
                "repeated_nonzero": None, "history_available": False,
                "note": "single LastTaskResult only; pass --task-results history to detect repeats"}
    return {"history": None, "last_result": None, "nonzero": False,
            "repeated_nonzero": None, "history_available": False}


def query_task_last_result(task_name: str = "TB_LiveShadow_G2") -> Optional[int]:
    """Best-effort live query of LastTaskResult via schtasks. Returns None if unavailable."""
    try:
        out = subprocess.check_output(
            ["schtasks", "/query", "/tn", task_name, "/v", "/fo", "list"],
            text=True, stderr=subprocess.DEVNULL, timeout=20)
    except Exception:
        return None
    for line in out.splitlines():
        if "Last Result" in line:
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def summarize(now: Optional[datetime] = None,
              task_history: Optional[List[int]] = None,
              task_last_result: Optional[int] = None,
              query_live_task: bool = False) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cycles = load_cycles()
    if query_live_task and task_history is None and task_last_result is None:
        task_last_result = query_task_last_result()
    status_counts: Dict[str, int] = {}
    for c in cycles:
        status_counts[str(c.get("status"))] = status_counts.get(str(c.get("status")), 0) + 1
    missing = missed_slots(cycles, now)
    lock = lock_status(now)
    tres = task_result_status(task_history, task_last_result)
    fetch_cycles = [c.get("cycle_id") for c in cycles
                    if int(c.get("fetch_failures", 0) or 0) > 0 or int(c.get("alarm_fetch_failures", 0) or 0) > 0]
    validator_cycles = [c.get("cycle_id") for c in cycles if int(c.get("validator_failures", 0) or 0) > 0]
    failed_cycles = [c.get("cycle_id") for c in cycles if c.get("status") not in ("OK", "DEGRADED",
                                                                                 "SKIPPED_ALREADY_LOGGED",
                                                                                 "STOP_FILE_PRESENT")]
    issues: List[str] = []
    if not cycles:
        issues.append("no cycles logged yet")
    if missing:
        issues.append(f"{len(missing)} missed slot(s)")
    if lock["exists"] and lock["stale"]:
        issues.append(f"stale lock (age {lock['age_seconds']}s >= {lock['stale_after_seconds']}s)")
    if tres.get("repeated_nonzero"):
        issues.append(f"repeated non-zero task results: {tres['history']}")
    elif tres.get("nonzero"):
        issues.append(f"last task result non-zero: {tres['last_result']}")
    if fetch_cycles:
        issues.append(f"{len(fetch_cycles)} cycle(s) with fetch failures")
    if validator_cycles:
        issues.append(f"{len(validator_cycles)} cycle(s) with validator failures")
    if failed_cycles:
        issues.append(f"{len(failed_cycles)} cycle(s) with status FAILED/REFUSED")
    return {
        "checked_utc": now.isoformat(),
        "cycles_logged": len(cycles),
        "cycle_status_counts": status_counts,
        "last_cycle": cycles[-1] if cycles else None,
        "missed_slots": missing,
        "missed_slot_count": len(missing),
        "lock": lock,
        "task_result": tres,
        "cycles_with_fetch_failures": fetch_cycles,
        "cycles_with_validator_failures": validator_cycles,
        "failed_cycles": failed_cycles,
        "issues": issues,
        "healthy": len(issues) == 0 and len(cycles) > 0,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON report")
    ap.add_argument("--task-results", default=None,
                    help="comma-separated task result history, e.g. 0,3221225786,0 (for repeat detection)")
    ap.add_argument("--no-live-query", action="store_true", help="do not query schtasks for LastTaskResult")
    args = ap.parse_args(argv)
    hist = None
    if args.task_results:
        hist = [int(x.strip()) for x in args.task_results.split(",") if x.strip() not in ("",)]
    rep = summarize(task_history=hist, query_live_task=not args.no_live_query)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    else:
        print(f"scheduler health: {'HEALTHY' if rep['healthy'] else 'ISSUES'} "
              f"({rep['cycles_logged']} cycles, {rep['missed_slot_count']} missed)")
        for i in rep["issues"]:
            print(f" - {i}")
        if rep["last_cycle"]:
            print(f"last cycle: {rep['last_cycle'].get('cycle_id')} status={rep['last_cycle'].get('status')}")
        print(f"lock: {rep['lock']}")
        print(f"task_result: {rep['task_result']}")
    if rep["cycles_logged"] == 0:
        return 2
    return 0 if rep["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
