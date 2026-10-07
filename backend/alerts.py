"""Alert rules and threshold-based event detection.

Alert rules are configurable thresholds over the aggregate metrics of a run.
Each rule has a comparator (gt/ge/lt/le), a threshold, a severity, and a
repeat flag.  The alert engine evaluates rules against the current metric
values after each step, and fires events when a rule's threshold is crossed
(with hysteresis to avoid oscillation around the threshold).

Rules are stored globally (``data/alerts/rules.json``) and apply to all runs
(optionally filtered by domain).  Events are stored per-run
(``data/runs/<run_id>/alerts.json``) and aggregated by the alert-center API.
The per-run state (which rules are currently "in alarm") is stored in
``data/runs/<run_id>/alert_state.json`` so that a server restart does not
reset the firing state.

The hysteresis mechanism prevents the "flapping" problem: when a metric
oscillates around the threshold, the rule fires only once (on the first
crossing) and re-arms only when the metric returns to a "safe" zone
(threshold ± hysteresis).  This is what prevents the alert center from being
flooded with duplicate events.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from . import storage, util

# --------------------------------------------------------------------------- #
# Rule model
# --------------------------------------------------------------------------- #
COMPARATORS = {"gt": ">", "ge": ">=", "lt": "<", "le": "<="}
SEVERITIES = ("info", "warning", "critical")


def new_rule_id() -> str:
    return "alert_" + util.new_id("").replace("_", "")[:12]


def default_hysteresis(threshold: float) -> float:
    """A reasonable default dead-band around the threshold (5% of the value)."""
    return max(1.0, abs(threshold) * 0.05)


def validate_rule(rule: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    errors: List[str] = []
    if not rule.get("name"):
        errors.append("规则名称不能为空")
    if not rule.get("metric"):
        errors.append("指标不能为空")
    if rule.get("comparator") not in COMPARATORS:
        errors.append(f"比较方向无效: {rule.get('comparator')}")
    try:
        float(rule.get("threshold", 0))
    except (TypeError, ValueError):
        errors.append("阈值必须是数字")
    if rule.get("severity") not in SEVERITIES:
        errors.append(f"严重级别无效: {rule.get('severity')}")
    return errors


def normalize_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in defaults and coerce types for a rule dict."""
    threshold = float(rule.get("threshold", 0))
    hysteresis = rule.get("hysteresis")
    if hysteresis is None or hysteresis == "":
        hysteresis = default_hysteresis(threshold)
    return {
        "id": rule.get("id") or new_rule_id(),
        "name": str(rule.get("name", "未命名规则")),
        "domain": str(rule.get("domain", "")),
        "metric": str(rule.get("metric", "")),
        "comparator": str(rule.get("comparator", "gt")),
        "threshold": threshold,
        "severity": str(rule.get("severity", "warning")),
        "repeat": bool(rule.get("repeat", True)),
        "hysteresis": float(hysteresis),
        "enabled": bool(rule.get("enabled", True)),
        "description": str(rule.get("description", "")),
    }


# --------------------------------------------------------------------------- #
# Rule storage (thin wrappers over storage.py for a cleaner API)
# --------------------------------------------------------------------------- #
def load_rules() -> List[Dict[str, Any]]:
    return storage.load_rules()


def save_rules(rules: List[Dict[str, Any]]) -> None:
    storage.save_rules(rules)


# --------------------------------------------------------------------------- #
# Alert engine
# --------------------------------------------------------------------------- #
def evaluate(run_id: str, run_name: str, domain: str, step: int,
             stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate all rules against the current stats and fire events.

    Returns the list of newly fired events (empty if none).
    """
    rules = storage.load_rules()
    if not rules:
        return []

    events = storage.load_run_alerts(run_id)
    state = storage.load_run_alert_state(run_id)
    new_events: List[Dict[str, Any]] = []
    changed = False

    for rule in rules:
        if not rule["enabled"]:
            continue
        if rule["domain"] and rule["domain"] != domain:
            continue
        value = stats.get(rule["metric"])
        if value is None:
            continue

        fired = state.get(rule["id"], {}).get("fired", False)
        threshold = rule["threshold"]
        hysteresis = rule["hysteresis"]

        # Determine whether the metric is currently in the alarm zone.
        if rule["comparator"] in ("gt", "ge"):
            in_alarm = value > threshold or (rule["comparator"] == "ge" and value == threshold)
            cleared = value < threshold - hysteresis
        else:
            in_alarm = value < threshold or (rule["comparator"] == "le" and value == threshold)
            cleared = value > threshold + hysteresis

        if not fired and in_alarm:
            # Fire the event (only on the transition from armed to fired).
            event = {
                "id": "evt_" + util.new_id("").replace("_", "")[:12],
                "run_id": run_id,
                "run_name": run_name,
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "metric": rule["metric"],
                "value": value,
                "threshold": threshold,
                "comparator": rule["comparator"],
                "severity": rule["severity"],
                "step": step,
                "time": util.now_iso(),
                "message": (
                    f"{rule['name']}: {rule['metric']} = {value} "
                    f"{COMPARATORS[rule['comparator']]} {threshold}"
                ),
            }
            new_events.append(event)
            events.append(event)
            state[rule["id"]] = {"fired": True, "last_value": value, "last_step": step}
            changed = True
        elif fired and cleared:
            # Re-arm the rule (only if repeat is enabled).
            if rule["repeat"]:
                state[rule["id"]] = {"fired": False, "last_value": value, "last_step": step}
            else:
                state[rule["id"]] = {"fired": True, "last_value": value, "last_step": step, "cleared": True}
            changed = True

    if changed:
        storage.save_run_alerts(run_id, events)
        storage.save_run_alert_state(run_id, state)

    return new_events


# --------------------------------------------------------------------------- #
# Query helpers
# --------------------------------------------------------------------------- #
def query_events(run_id: Optional[str] = None,
                 rule_id: Optional[str] = None,
                 metric: Optional[str] = None,
                 severity: Optional[str] = None,
                 start_step: Optional[int] = None,
                 end_step: Optional[int] = None,
                 start_time: Optional[str] = None,
                 end_time: Optional[str] = None,
                 limit: int = 50,
                 offset: int = 0) -> Dict[str, Any]:
    """Query alert events across runs (or a specific run) with filters.

    Returns ``{"events": [...], "total": N, "has_more": bool}``.
    """
    events: List[Dict[str, Any]] = []
    if run_id:
        events = storage.load_run_alerts(run_id)
    else:
        for rid in storage.list_run_ids():
            events.extend(storage.load_run_alerts(rid))

    filtered: List[Dict[str, Any]] = []
    for e in events:
        if rule_id and e.get("rule_id") != rule_id:
            continue
        if metric and e.get("metric") != metric:
            continue
        if severity and e.get("severity") != severity:
            continue
        if start_step is not None and e.get("step", 0) < start_step:
            continue
        if end_step is not None and e.get("step", 0) > end_step:
            continue
        if start_time and e.get("time", "") < start_time:
            continue
        if end_time and e.get("time", "") > end_time:
            continue
        filtered.append(e)

    # Sort by time (newest first), then by step.
    filtered.sort(key=lambda e: (e.get("time", ""), e.get("step", 0)), reverse=True)
    total = len(filtered)
    return {
        "events": filtered[offset:offset + limit],
        "total": total,
        "has_more": offset + limit < total,
    }


def summary() -> Dict[str, Any]:
    """Return a summary of recent alerts for the alert banner.

    The banner shows the count of critical alerts in the last 5 minutes,
    plus the most recent events for the toast notification.
    """
    events: List[Dict[str, Any]] = []
    for rid in storage.list_run_ids():
        events.extend(storage.load_run_alerts(rid))

    # Recent events (last 5 minutes).
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = []
    for e in events:
        try:
            t = datetime.datetime.fromisoformat(e["time"])
            if (now - t).total_seconds() < 300:
                recent.append(e)
        except (ValueError, KeyError):
            continue
    recent.sort(key=lambda e: e.get("time", ""), reverse=True)

    critical = sum(1 for e in events if e.get("severity") == "critical")
    warning = sum(1 for e in events if e.get("severity") == "warning")
    info = sum(1 for e in events if e.get("severity") == "info")

    return {
        "total_events": len(events),
        "critical_count": critical,
        "warning_count": warning,
        "info_count": info,
        "recent_events": recent[:10],
        "recent_critical": sum(1 for e in recent if e.get("severity") == "critical"),
        "active_rules": len([r for r in storage.load_rules() if r.get("enabled")]),
    }
