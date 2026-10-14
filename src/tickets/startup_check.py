"""src/tickets/startup_check.py — R6 fail-closed release startup verification.

Verifies, before anything operational runs:
1. Required policy fields are present (policy_version, validator_version,
   operational_defaults).
2. Model artifact SHA-256 hashes match configs/release.yaml.
3. Evaluation manifests and their data files match the recorded SHA-256s.
4. The unsafe path is OFF: experimental LLM disabled, dispatch / alarm
   creation / notifications disabled, telemetry read-only — unless the caller
   passes an explicit override, which is recorded loudly in the result.

Fails closed: any violation is returned as an error and the CLI exits non-zero.
No dispatch, alarm, notification, or model-loading side effect occurs here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE_CONFIG = ROOT / "configs" / "release.yaml"

EXPECTED_POLICY_VERSION = "ticket-policy-v1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class StartupCheckResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked: Dict[str, Any] = field(default_factory=dict)


def run_startup_check(
    config_path: Path = DEFAULT_RELEASE_CONFIG,
    allow_unsafe_override: bool = False,
) -> StartupCheckResult:
    errors: List[str] = []
    warnings: List[str] = []
    checked: Dict[str, Any] = {}

    if not Path(config_path).exists():
        return StartupCheckResult(
            passed=False, errors=[f"Release config not found: {config_path}."]
        )
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 1. Required policy fields -------------------------------------------------
    for key in ("policy_version", "validator_version", "operational_defaults"):
        if not cfg.get(key):
            errors.append(f"Missing required policy field: {key}.")
    if cfg.get("policy_version") != EXPECTED_POLICY_VERSION:
        errors.append(
            f"policy_version must be {EXPECTED_POLICY_VERSION!r}, "
            f"got {cfg.get('policy_version')!r}."
        )
    checked["policy_version"] = cfg.get("policy_version")

    ops = cfg.get("operational_defaults", {}) or {}
    for key in (
        "ticket_renderer",
        "experimental_llm_enabled",
        "telemetry_mode",
        "alarm_creation",
        "dispatch",
        "notifications",
    ):
        if key not in ops:
            errors.append(f"Missing operational default: {key}.")
    if ops.get("ticket_renderer") != "enabled":
        errors.append("Deterministic ticket renderer must be 'enabled'.")

    # 2. Unsafe-path flags (fail closed unless explicitly overridden) ------------
    unsafe_flags = {
        "experimental_llm_enabled": ops.get("experimental_llm_enabled") is True,
        "dispatch": ops.get("dispatch") == "enabled",
        "alarm_creation": ops.get("alarm_creation") == "enabled",
        "notifications": ops.get("notifications") == "enabled",
        "telemetry_mode": ops.get("telemetry_mode") not in (None, "read-only"),
    }
    triggered = sorted(k for k, v in unsafe_flags.items() if v)
    checked["unsafe_flags_triggered"] = triggered
    if triggered and not allow_unsafe_override:
        errors.append(
            "Unsafe operational flags set without explicit operator override: "
            + ", ".join(triggered)
            + ". Startup refused (fail-closed)."
        )
    elif triggered:
        warnings.append(
            "OPERATOR OVERRIDE ACTIVE — unsafe flags explicitly allowed: "
            + ", ".join(triggered)
            + ". This override must be reviewed and logged."
        )

    # 3. Model artifact hashes ----------------------------------------------------
    models = cfg.get("model_versions", {}) or {}
    if not models:
        errors.append("No model_versions recorded in release config.")
    for name, spec in models.items():
        rel = (spec or {}).get("path")
        expected = (spec or {}).get("sha256")
        if not rel or not expected:
            errors.append(f"Model entry {name!r} missing path or sha256.")
            continue
        full = ROOT / rel
        if not full.exists():
            errors.append(f"Model artifact missing on disk: {rel}.")
            continue
        actual = _sha256_file(full)
        checked[f"model_hash:{name}"] = actual[:16] + "..."
        if actual.lower() != str(expected).lower():
            errors.append(f"Model artifact hash mismatch: {rel}.")

    # 4. Evaluation manifest + data-file integrity -----------------------------------
    manifests = cfg.get("evaluation_manifests", {}) or {}
    if not manifests:
        errors.append("No evaluation_manifests recorded in release config.")
    for split_name, spec in manifests.items():
        for key in ("manifest", "manifest_sha256", "data_sha256"):
            if not (spec or {}).get(key):
                errors.append(f"Manifest entry {split_name!r} missing {key}.")
        if errors and any(split_name in e for e in errors):
            continue
        manifest_path = ROOT / spec["manifest"]
        if not manifest_path.exists():
            errors.append(f"Manifest file missing on disk: {spec['manifest']}.")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stored_m_sha = manifest.get("manifest_sha256")
        body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        recomputed_m_sha = hashlib.sha256(
            json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
        ).hexdigest()
        if stored_m_sha != recomputed_m_sha:
            errors.append(f"Manifest self-hash mismatch: {spec['manifest']}.")
        if stored_m_sha != spec["manifest_sha256"]:
            errors.append(
                f"Manifest hash does not match release config: {spec['manifest']}."
            )
        data_path = ROOT / manifest.get("data_file", "")
        if not data_path.exists():
            errors.append(f"Manifest data file missing: {manifest.get('data_file')}.")
            continue
        actual_data_sha = _sha256_file(data_path)
        if actual_data_sha != manifest.get("data_file_sha256"):
            errors.append(f"Manifest data-file hash mismatch: {manifest.get('data_file')}.")
        if actual_data_sha != spec["data_sha256"]:
            errors.append(
                f"Data-file hash does not match release config: {manifest.get('data_file')}."
            )
        if manifest.get("frozen") is not True:
            errors.append(f"Manifest not marked frozen: {spec['manifest']}.")
        checked[f"manifest:{split_name}"] = "verified"

    return StartupCheckResult(passed=not errors, errors=errors, warnings=warnings, checked=checked)


class UnpinnedArtifactError(RuntimeError):
    """Raised when code tries to load an artifact not pinned in release.yaml."""


def require_pinned_artifact(path: Path, config_path: Path = DEFAULT_RELEASE_CONFIG) -> str:
    """Fail closed unless ``path`` is listed in model_versions with a matching SHA-256.

    Call immediately before loading any model artifact. Returns the verified hash.
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    try:
        rel = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        raise UnpinnedArtifactError(f"Artifact outside repository is not pinned: {path}.")
    pinned = {
        (spec or {}).get("path"): str((spec or {}).get("sha256", "")).lower()
        for spec in (cfg.get("model_versions") or {}).values()
    }
    if rel not in pinned:
        raise UnpinnedArtifactError(f"Artifact not pinned in {config_path}: {rel}.")
    actual = _sha256_file(ROOT / rel)
    if actual != pinned[rel]:
        raise UnpinnedArtifactError(f"Artifact hash mismatch vs release config: {rel}.")
    return actual


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    override = "--allow-unsafe-override" in args
    config = DEFAULT_RELEASE_CONFIG
    for i, a in enumerate(args):
        if a == "--config" and i + 1 < len(args):
            config = Path(args[i + 1])
    result = run_startup_check(config, allow_unsafe_override=override)
    print(f"startup check: {'PASS' if result.passed else 'FAIL'}")
    for w in result.warnings:
        print(f"  WARNING: {w}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
