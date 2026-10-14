"""src/tickets/llm_bridge.py — Stage B experimental LLM boundary.

The fine-tuned Qwen model stays behind this feature flag. Default OFF.
The deterministic renderer (src/tickets/renderer.py) is the default ticket
path; nothing here changes that.

When the flag is enabled, an LLM may generate ONLY a clearly marked optional
commentary block. Authoritative fields (identifiers, values, units,
probabilities, timestamps, rules, severity wording) always come from the
validated TicketInput and are never rewritten by the LLM. Commentary is
parsed separately, validated against the typed payload, and discarded on any
violation — the deterministic ticket is always retained.

No claim is made that the LLM improves accuracy; that is Stage E work.
This module loads no model weights: generation is injected as a callable so
unit tests use stubs and production wiring remains explicit and reviewable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.tickets.renderer import format_probability
from src.tickets.schema import TicketInput

# Experimental feature flag. Default OFF. Flip only via set_experimental_llm_enabled,
# which exists so tests and explicit operator action can exercise the path.
EXPERIMENTAL_LLM_ENABLED = False

COMMENTARY_HEADER = "OPTIONAL MODEL COMMENTARY (experimental, unverified — not authoritative)"


def is_experimental_llm_enabled() -> bool:
    return EXPERIMENTAL_LLM_ENABLED


def set_experimental_llm_enabled(enabled: bool) -> bool:
    global EXPERIMENTAL_LLM_ENABLED
    EXPERIMENTAL_LLM_ENABLED = bool(enabled)
    return EXPERIMENTAL_LLM_ENABLED


@dataclass
class CommentaryAuditRecord:
    llm_enabled: bool
    commentary_included: bool
    decision: str  # "llm-disabled" | "commentary-kept" | "commentary-discarded"
    raw_output: Optional[str] = None
    violations: List[str] = field(default_factory=list)


def validate_commentary(commentary: str, ticket_input: TicketInput) -> List[str]:
    """Validate LLM commentary against the typed payload. Returns violations."""
    violations: List[str] = []
    if not commentary or not commentary.strip():
        return ["Empty commentary."]
    lowered = commentary.lower()

    # 1. No rule IDs outside the validated triggered set.
    cited_rules = set(re.findall(r"\bRULE_[A-Z0-9_]+\b", commentary))
    allowed_rules = set(ticket_input.triggered_rules)
    for rule in sorted(cited_rules):
        if rule not in allowed_rules:
            violations.append(
                f"Commentary cites rule '{rule}' not present in validated triggered_rules."
            )

    # 2. No probability figure contradicting the canonical probability.
    percentages = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", commentary)]
    if ticket_input.calibrated_probability is not None:
        canonical_pct = round(float(ticket_input.calibrated_probability) * 100.0, 1)
        for pct in percentages:
            if abs(pct - canonical_pct) > 0.05:
                violations.append(
                    f"Commentary states {pct}% but canonical probability is "
                    f"{format_probability(ticket_input.calibrated_probability)}."
                )
    else:
        if percentages or re.search(r"outage\s+probability\s*:\s*\d", lowered):
            violations.append(
                "Commentary states an outage probability on a connectivity ticket "
                "where electrical condition is unknown."
            )

    # 3. No contradictory device/bank identity claims.
    # (Heuristic: a "device ... is <X>" claim naming a different site.)
    for match in re.findall(r"device\s+(?:site\s+)?(?:is\s+)?([A-Za-z0-9][A-Za-z0-9 _\-]{2,40})", commentary):
        candidate = match.strip()
        if candidate and candidate != ticket_input.device_name and candidate.lower() not in (
            ticket_input.device_name.lower(), ticket_input.bank.lower(),
        ):
            # Only flag when it looks like a naming claim, not a passing mention
            # of the correct site. Conservative: flag only exact "device is X".
            if re.search(rf"\bdevice\s+is\s+{re.escape(candidate)}\b", commentary, re.IGNORECASE):
                violations.append(
                    f"Commentary names device {candidate!r}, expected "
                    f"{ticket_input.device_name!r}."
                )

    # 4. Commentary must not issue dispatch orders or emergency escalation.
    if re.search(r"\b(dispatch\s+(immediately|now|a\s+technician)|emergency\s+dispatch|mobilize)\b", lowered):
        violations.append(
            "Commentary issues dispatch/emergency orders; investigation actions "
            "are suggestions only."
        )

    return violations


def attach_optional_commentary(
    deterministic_text: str,
    ticket_input: TicketInput,
    generate_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> tuple[str, CommentaryAuditRecord]:
    """Attach validated LLM commentary to a deterministic ticket, or skip.

    Returns (final_text, audit_record). The deterministic ticket text is never
    modified except by appending a validated, clearly marked commentary block.
    """
    if not is_experimental_llm_enabled():
        return deterministic_text, CommentaryAuditRecord(
            llm_enabled=False,
            commentary_included=False,
            decision="llm-disabled",
        )
    if generate_fn is None:
        return deterministic_text, CommentaryAuditRecord(
            llm_enabled=True,
            commentary_included=False,
            decision="commentary-discarded",
            raw_output=None,
            violations=["No generation function provided; commentary unavailable."],
        )
    payload_view = {
        "device_id": ticket_input.device_id,
        "device_name": ticket_input.device_name,
        "bank": ticket_input.bank,
        "device_state": ticket_input.device_state,
        "risk_tier": ticket_input.risk_tier,
        "triggered_rules": list(ticket_input.triggered_rules),
        "calibrated_probability": ticket_input.calibrated_probability,
    }
    raw = generate_fn(payload_view)
    violations = validate_commentary(raw if isinstance(raw, str) else "", ticket_input)
    if violations:
        return deterministic_text, CommentaryAuditRecord(
            llm_enabled=True,
            commentary_included=False,
            decision="commentary-discarded",
            raw_output=raw if isinstance(raw, str) else repr(raw),
            violations=violations,
        )
    final_text = (
        deterministic_text
        + "\n\n"
        + COMMENTARY_HEADER
        + "\n"
        + raw.strip()
    )
    return final_text, CommentaryAuditRecord(
        llm_enabled=True,
        commentary_included=True,
        decision="commentary-kept",
        raw_output=raw,
        violations=[],
    )


__all__ = [
    "COMMENTARY_HEADER",
    "EXPERIMENTAL_LLM_ENABLED",
    "CommentaryAuditRecord",
    "attach_optional_commentary",
    "is_experimental_llm_enabled",
    "set_experimental_llm_enabled",
    "validate_commentary",
]
