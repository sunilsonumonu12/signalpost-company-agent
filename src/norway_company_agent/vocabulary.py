"""Single source of truth for the envelope vocabulary (Phase 3).

Everything that names a state, a status, a module key or the schema version is
defined here and imported everywhere else. Nothing in the pipeline maps one
vocabulary onto another: the module state IS the evidence status.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

# One enum for evidence status AND module state. Identical to evidence.EvidenceStatus.
MODULE_STATES: tuple[str, ...] = (
    "available",
    "not_found",
    "not_applicable",
    "blocked",
    "source_error",
    "not_fetched",
)

# A blocked module always carries one of these.
BLOCKED_REASONS: tuple[str, ...] = ("robots", "policy")

TERMINAL_STATUSES: tuple[str, ...] = ("completed", "completed_with_gaps", "failed")

# Fixed module keys. Every envelope emits exactly these, in this order.
MODULE_KEYS: tuple[str, ...] = (
    "registry",
    "registry_live",
    "financials",
    "financial_history",
    "roles",
    "group",
    "locations",
    "accounting_obligation",
    "website",
    "website_discovery",
    "website_discovered",
)

# Official sources (BRREG plus the rule-based assessment). A source_error here
# after retries means the company record is broken: terminal_status = failed.
REQUIRED_MODULES: tuple[str, ...] = (
    "registry_live",
    "financials",
    "financial_history",
    "roles",
    "group",
    "locations",
    "accounting_obligation",
)

# Modules that must be available (or not_applicable) for a gap-free record.
# website is third-party: it can leave a gap but cannot make the record failed.
COVERAGE_MODULES: tuple[str, ...] = REQUIRED_MODULES + ("website",)

_GAP_STATES = frozenset({"not_found", "blocked", "not_fetched"})


class UnknownModuleStatusError(ValueError):
    """An evidence record carries a status outside MODULE_STATES."""


def module_state(record: dict[str, Any] | None, *, module: str = "") -> dict[str, Any]:
    """Return {state, retry_count[, reason]} for one evidence record.

    A missing record is `not_fetched`. An unknown status raises; it is never
    mapped to anything.
    """
    if not isinstance(record, dict):
        return {"state": "not_fetched", "retry_count": 0}
    status = record.get("status")
    if status not in MODULE_STATES:
        raise UnknownModuleStatusError(
            f"module {module or record.get('field')!r}: unknown evidence status {status!r}; "
            f"allowed: {list(MODULE_STATES)}"
        )
    state: dict[str, Any] = {"state": status, "retry_count": int(record.get("retry_count") or 0)}
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        state["reason"] = "robots" if "robot" in note else "policy"
    return state


def module_states(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Uniform module states for one profile: always exactly MODULE_KEYS."""
    records = profile.get("evidence") if isinstance(profile.get("evidence"), dict) else {}
    states: dict[str, dict[str, Any]] = {}
    for key in MODULE_KEYS:
        record = records.get(key)
        if record is None and key == "website_discovered" and records.get("website_discovery") is not None:
            # Discovery ran and produced no candidate page to fetch.
            states[key] = {"state": "not_applicable", "retry_count": 0}
        else:
            states[key] = module_state(record, module=key)
    return states


PUBLIC_MODULE_STATES: tuple[str, ...] = (
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
)

WEBSITE_IDENTITY_MODULES: tuple[str, ...] = ("website", "website_discovered")


def _identity_assessment(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    val = record.get("value")
    if not isinstance(val, dict):
        return None
    assessment = val.get("identity_assessment")
    if isinstance(assessment, dict) and assessment:
        return assessment
    return None


def public_module_state(
    internal: dict[str, Any],
    *,
    module: str = "",
    record: dict[str, Any] | None = None,
    website_internal_state: str = "",
) -> dict[str, Any]:
    """Map one internal module state dict onto the public 6-state vocabulary."""
    if not isinstance(internal, dict):
        raise UnknownModuleStatusError(
            f"module {module!r}: internal state is not a dict: {internal!r}"
        )
    status = internal.get("state")
    if status not in MODULE_STATES:
        raise UnknownModuleStatusError(
            f"module {module!r}: unknown internal module state {status!r}; "
            f"allowed: {list(MODULE_STATES)}"
        )

    retry_count = int(internal.get("retry_count") or 0)

    if status == "available":
        if module in WEBSITE_IDENTITY_MODULES:
            assessment = _identity_assessment(record)
            if assessment is not None and assessment.get("publishable") is False:
                pub_state = "ambiguous"
            else:
                pub_state = "available"
        else:
            pub_state = "available"
    elif status == "not_found":
        pub_state = "not_available"
    elif status == "not_applicable":
        pub_state = "not_applicable"
    elif status == "blocked":
        pub_state = "blocked"
    elif status == "source_error":
        if module in WEBSITE_IDENTITY_MODULES:
            assessment = _identity_assessment(record)
            if assessment is not None and assessment.get("decision") == "REJECT":
                pub_state = "not_available"
            else:
                pub_state = "failed"
        else:
            pub_state = "failed"
    elif status == "not_fetched":
        if module in ("website_discovery", "website_discovered") and website_internal_state == "available":
            pub_state = "not_applicable"
        elif module in REQUIRED_MODULES:
            pub_state = "failed"
        else:
            pub_state = "not_available"
    else:
        raise UnknownModuleStatusError(
            f"module {module!r}: unhandled internal state {status!r}"
        )

    out: dict[str, Any] = {"state": pub_state, "retry_count": retry_count}
    if pub_state == "blocked":
        out["reason"] = internal.get("reason") or "policy"
    return out


def public_module_states(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Uniform module states for one profile, mapped to the public 6-state vocabulary."""
    internals = module_states(profile)
    records = profile.get("evidence") if isinstance(profile.get("evidence"), dict) else {}
    website_internal = internals.get("website", {}).get("state", "")
    states: dict[str, dict[str, Any]] = {}
    for key in MODULE_KEYS:
        internal = internals[key]
        raw_record = records.get(key)
        states[key] = public_module_state(
            internal,
            module=key,
            record=raw_record,
            website_internal_state=website_internal,
        )
    return states


def derive_public_terminal_status(states: dict[str, dict[str, Any]]) -> str:
    """completed | completed_with_gaps | failed, derived from public module states."""
    if any(states[key]["state"] == "failed" for key in REQUIRED_MODULES):
        return "failed"
    for key in COVERAGE_MODULES:
        state = states[key]["state"]
        if state in {"not_available", "blocked", "ambiguous"} or (key not in REQUIRED_MODULES and state == "failed"):
            return "completed_with_gaps"
    return "completed"


def invalid_public_module_states(states: dict[str, Any]) -> list[str]:
    """Violations for one public `modules` mapping (used by validators)."""
    problems: list[str] = []
    if tuple(states) != MODULE_KEYS:
        missing = [key for key in MODULE_KEYS if key not in states]
        extra = [key for key in states if key not in MODULE_KEYS]
        problems.append(f"module keys differ from fixed list (missing={missing}, extra={extra})")
    for key, item in states.items():
        if not isinstance(item, dict):
            problems.append(f"module {key!r} is not an object")
            continue
        state = item.get("state")
        if state not in PUBLIC_MODULE_STATES:
            problems.append(f"module {key!r} has state {state!r} outside {list(PUBLIC_MODULE_STATES)}")
        reason = item.get("reason")
        if state == "blocked" and reason not in BLOCKED_REASONS:
            problems.append(f"module {key!r} is blocked with reason {reason!r}; allowed {list(BLOCKED_REASONS)}")
        if state != "blocked" and "reason" in item:
            problems.append(f"module {key!r} has a reason but state is {state!r}")
        retry = item.get("retry_count")
        if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
            problems.append(f"module {key!r} retry_count {retry!r} is not a non-negative integer")
    return problems



def derive_terminal_status(states: dict[str, dict[str, Any]]) -> str:
    """completed | completed_with_gaps | failed, derived from module states only."""
    if any(states[key]["state"] == "source_error" for key in REQUIRED_MODULES):
        return "failed"
    for key in COVERAGE_MODULES:
        state = states[key]["state"]
        if state in _GAP_STATES or (key not in REQUIRED_MODULES and state == "source_error"):
            return "completed_with_gaps"
    return "completed"


def profile_terminal_status(profile: dict[str, Any]) -> str:
    return derive_terminal_status(module_states(profile))


def batch_state(profile: dict[str, Any]) -> str:
    """profile._batch_state, tied to terminal_status: 'failed' iff terminal_status == failed."""
    return "failed" if profile_terminal_status(profile) == "failed" else "ok"


def invalid_module_states(states: dict[str, Any]) -> list[str]:
    """Violations for one `modules` mapping (used by validators)."""
    problems: list[str] = []
    if tuple(states) != MODULE_KEYS:
        missing = [key for key in MODULE_KEYS if key not in states]
        extra = [key for key in states if key not in MODULE_KEYS]
        problems.append(f"module keys differ from fixed list (missing={missing}, extra={extra})")
    for key, item in states.items():
        if not isinstance(item, dict):
            problems.append(f"module {key!r} is not an object")
            continue
        state = item.get("state")
        if state not in MODULE_STATES:
            problems.append(f"module {key!r} has state {state!r} outside {list(MODULE_STATES)}")
        reason = item.get("reason")
        if state == "blocked" and reason not in BLOCKED_REASONS:
            problems.append(f"module {key!r} is blocked with reason {reason!r}; allowed {list(BLOCKED_REASONS)}")
        if state != "blocked" and "reason" in item:
            problems.append(f"module {key!r} has a reason but state is {state!r}")
        retry = item.get("retry_count")
        if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
            problems.append(f"module {key!r} retry_count {retry!r} is not a non-negative integer")
    return problems

