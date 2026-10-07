"""Event alert center: configurable threshold rules + per-run monitoring.

Rules
-----
An alert rule watches one aggregate metric (the keys found in every
``engine.stats()`` dict / series row, e.g. ``infected``, ``mean_speed``) and
fires when the metric crosses a threshold:

    {
      "id": "rule_xxxx", "name": "拥堵形成", "metric": "stopped",
      "op": ">=", "threshold": 0.8, "severity": "critical",
      "for_steps": 3,            # hysteresis: breach must hold for N steps
      "repeat": false,           # re-alert while the same breach continues?
      "repeat_interval": 50,     # ...if so, at most once every N steps
      "enabled": true, "built_in": true,
      "created_at": ..., "updated_at": ...
    }

Anti-flooding semantics (指标在阈值附近反复波动时不要刷屏、同一次越界只记一次):

* ``for_steps`` requires the metric to stay beyond the threshold for N
  consecutive steps before the rule fires — a single noisy step is ignored;
  recovery likewise needs ``for_steps`` consecutive in-range steps (恢复确认).
* While a breach stays active a non-repeating rule never fires again; a
  repeating rule re-fires at most every ``repeat_interval`` steps.
* A metric must return fully inside the threshold (and stay there) before the
  rule can arm again, so oscillation across the boundary logs one event per
  excursion instead of one per step.

Monitors live in the run manager (in-memory) — the same lifecycle restriction
as interactive stepping: a server restart cannot resume stepping a run, so no
detection state needs to survive a restart either.  The fired alerts themselves
are always persisted to ``alerts.json``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import storage, util

OPS = ("gte", "lte")
OP_SYMBOL = {"gte": "≥", "lte": "≤"}
DIRECTIONS = ({"value": "gte", "label": "高于/达到阈值（≥）"},
              {"value": "lte", "label": "低于/达到阈值（≤）"})
SEVERITIES = ("info", "warning", "critical")
SEVERITY_LABEL = {"info": "提示", "warning": "警告", "critical": "严重"}
KIND_LABEL = {"fire": "触发", "repeat": "重复提醒",
              "recovered": "恢复正常", "intervention": "干预触发"}


# --------------------------------------------------------------------------- #
# Built-in rule templates (per domain); auto-created with every new run
# --------------------------------------------------------------------------- #
def _rule(name: str, metric: str, op: str, threshold: float,
          severity: str = "warning", for_steps: int = 3,
          repeat: bool = False, repeat_interval: int = 50) -> Dict[str, Any]:
    return {"name": name, "metric": metric, "op": op, "threshold": threshold,
            "severity": severity, "for_steps": for_steps, "repeat": repeat,
            "repeat_interval": repeat_interval}


DEFAULT_RULES: Dict[str, List[Dict[str, Any]]] = {
    "traffic": [
        _rule("拥堵形成（停车比例过高）", "stopped", "gte", 0.8,
              severity="critical", for_steps=5),
        _rule("平均速度骤降", "mean_speed", "lte", 5.0,
              severity="warning", for_steps=5),
        _rule("交通恢复畅通", "stopped", "lte", 0.2,
              severity="info", for_steps=10),
    ],
    "epidemic": [
        _rule("疫情爆发（感染者激增）", "infected", "gte", 100,
              severity="critical", for_steps=3, repeat=True, repeat_interval=50),
        _rule("感染率超过 10%", "prevalence", "gte", 0.1,
              severity="warning", for_steps=3),
        _rule("单步新增感染异常升高", "new_infections", "gte", 50,
              severity="warning", for_steps=2, repeat=True, repeat_interval=50),
        _rule("疫情消亡", "infected", "lte", 0,
              severity="info", for_steps=5),
    ],
    "ecology": [
        _rule("狐狸种群灭绝", "foxes", "lte", 0,
              severity="critical", for_steps=5),
        _rule("兔子种群灭绝", "rabbits", "lte", 0,
              severity="critical", for_steps=5),
        _rule("狐狸种群濒临灭绝", "foxes", "lte", 10,
              severity="warning", for_steps=5),
        _rule("兔子种群爆发", "rabbits", "gte", 2000,
              severity="warning", for_steps=10, repeat=True, repeat_interval=100),
    ],
}


# --------------------------------------------------------------------------- #
# Rule validation / normalisation
# --------------------------------------------------------------------------- #
def validate_rule(rule: Dict[str, Any], known_metrics: List[str]) -> List[str]:
    """Return human-readable errors (empty when valid)."""
    errors: List[str] = []
    if not str(rule.get("name", "")).strip():
        errors.append("规则名称不能为空")
    metric = str(rule.get("metric", ""))
    if metric not in known_metrics:
        errors.append(f"未知指标: {metric}")
    if rule.get("op") not in OPS:
        errors.append("比较方向必须是 ≥(gte) 或 ≤(lte)")
    try:
        threshold = float(rule.get("threshold"))
        if threshold != threshold:  # NaN
            raise ValueError
    except (TypeError, ValueError):
        errors.append("阈值必须是数字")
    if str(rule.get("severity", "")) not in SEVERITIES:
        errors.append("级别必须是 info / warning / critical")
    try:
        for_steps = int(rule.get("for_steps", 1))
        if for_steps < 1:
            errors.append("连续步数至少为 1")
    except (TypeError, ValueError):
        errors.append("连续步数必须是整数")
    if rule.get("repeat"):
        try:
            if int(rule.get("repeat_interval", 0)) < 1:
                errors.append("重复提醒间隔至少为 1 步")
        except (TypeError, ValueError):
            errors.append("重复提醒间隔必须是整数")
    return errors


def normalize_rule(data: Dict[str, Any], *, built_in: bool = False,
                   now: str = "") -> Dict[str, Any]:
    repeat = bool(data.get("repeat"))
    rule = {
        "id": str(data.get("id") or util.new_id("rule")),
        "name": str(data.get("name", "未命名规则")).strip() or "未命名规则",
        "metric": str(data.get("metric", "")),
        "op": str(data.get("op", "gte")),
        "threshold": float(data["threshold"]),
        "severity": str(data.get("severity", "warning")),
        "for_steps": max(1, int(data.get("for_steps", 1))),
        "repeat": repeat,
        "repeat_interval": max(1, int(data.get("repeat_interval", 50))),
        "enabled": bool(data.get("enabled", True)),
        "built_in": bool(data.get("built_in", built_in)),
        "created_at": str(data.get("created_at") or now or util.now_iso()),
        "updated_at": now or util.now_iso(),
    }
    return rule


def default_rules_for(domain: str) -> List[Dict[str, Any]]:
    """Instantiate the built-in templates for ``domain`` with fresh ids."""
    now = util.now_iso()
    return [normalize_rule({**tpl, "enabled": True, "built_in": True},
                           built_in=True, now=now)
            for tpl in DEFAULT_RULES.get(domain, [])]


# --------------------------------------------------------------------------- #
# Comparison + messages
# --------------------------------------------------------------------------- #
def _beyond(value: float, op: str, threshold: float) -> bool:
    return value >= threshold if op == "gte" else value <= threshold


def _msg(rule: Dict[str, Any], value: float, kind: str) -> str:
    name = rule["name"]
    sym = OP_SYMBOL[rule["op"]]
    if kind == "fire":
        return f"{name}：{rule['metric']}={value:g} 已{sym}阈值 {rule['threshold']:g}"
    if kind == "repeat":
        return f"{name}（持续中）：{rule['metric']}={value:g} 仍{sym} {rule['threshold']:g}"
    if kind == "recovered":
        return f"{rule['name']} 已恢复正常：{rule['metric']}={value:g} 回到阈值 {rule['threshold']:g} 以内"
    return name


def intervention_message(itv: Dict[str, Any]) -> str:
    label = str(itv.get("label") or itv.get("type", "干预"))
    params = itv.get("params") or {}
    if params:
        p = "，".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
                     for k, v in params.items())
        return f"干预触发：{label}（{p}）"
    return f"干预触发：{label}"


# --------------------------------------------------------------------------- #
# Per-run monitor (state machine, in-memory)
# --------------------------------------------------------------------------- #
class _RuleState:
    """Mutable detection state for one rule within one run."""

    def __init__(self) -> None:
        self.beyond = False        # current value is beyond the threshold
        self.firing = False        # an active (un-recovered) breach
        self.run_len = 0           # consecutive steps in current regime
        self.last_fire_step: Optional[int] = None


class AlertMonitor:
    """Evaluate every enabled rule against each step's stats.

    Produces alert dicts ready to be appended to ``alerts.json``::

        {"id", "step", "time", "kind", "severity", "rule_id", "rule_name",
         "metric", "op", "threshold", "value", "message"}
    """

    def __init__(self, run_id: str, domain: str,
                 rules: Optional[List[Dict[str, Any]]] = None) -> None:
        self.run_id = run_id
        self.domain = domain
        if rules is None:
            rules = storage.load_alert_rules(run_id)
            if not rules:
                rules = default_rules_for(domain)
                storage.save_alert_rules(run_id, rules)
        self.rules = rules
        self._state: Dict[str, _RuleState] = {}

    # ----- rule book management ------------------------------------------ #
    def get_rules(self) -> List[Dict[str, Any]]:
        return self.rules

    def _rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        return next((r for r in self.rules if r["id"] == rule_id), None)

    def known_metrics(self) -> List[str]:
        from . import catalog
        return [m["key"] for m in catalog.CATALOG.get(self.domain, {})
                .get("metrics", [])]

    def upsert_rule(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or replace a rule; editing resets its detection state."""
        existing = self._rule(str(data.get("id", "")))
        built_in = bool(existing.get("built_in")) if existing else False
        created_at = existing.get("created_at") if existing else None
        rule = normalize_rule({**data, "id": data.get("id") or (existing or {}).get("id"),
                               "built_in": built_in, "created_at": created_at})
        if existing:
            self.rules[self.rules.index(existing)] = rule
        else:
            self.rules.append(rule)
        self._state.pop(rule["id"], None)
        storage.save_alert_rules(self.run_id, self.rules)
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        rule = self._rule(rule_id)
        if rule is None:
            return False
        self.rules.remove(rule)
        self._state.pop(rule_id, None)
        storage.save_alert_rules(self.run_id, self.rules)
        return True

    def reset_state(self) -> None:
        """Clear all detection state (used when the run is reset to step 0)."""
        self._state.clear()

    # ----- evaluation ----------------------------------------------------- #
    def _make_alert(self, rule: Dict[str, Any], step: int, kind: str,
                    value: float, severity: str) -> Dict[str, Any]:
        return {
            "id": util.new_id("alrt"),
            "step": step,
            "time": util.now_iso(),
            "kind": kind,
            "severity": severity,
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "metric": rule["metric"],
            "op": rule["op"],
            "threshold": rule["threshold"],
            "value": value,
            "message": _msg(rule, value, kind),
        }

    def evaluate(self, stats: Dict[str, Any], step: int) -> List[Dict[str, Any]]:
        """Check all enabled rules against ``stats``; return new alerts."""
        out: List[Dict[str, Any]] = []
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            value = stats.get(rule["metric"])
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            st = self._state.setdefault(rule["id"], _RuleState())
            beyond = _beyond(float(value), rule["op"], rule["threshold"])

            if beyond != st.beyond:
                st.beyond = beyond
                st.run_len = 0
            st.run_len += 1
            need = max(1, int(rule.get("for_steps", 1)))

            if beyond and st.run_len >= need:
                if not st.firing:
                    st.firing = True
                    st.last_fire_step = step
                    out.append(self._make_alert(rule, step, "fire",
                                                float(value), rule["severity"]))
                elif rule.get("repeat") and st.last_fire_step is not None and \
                        step - st.last_fire_step >= max(1, int(rule.get("repeat_interval", 50))):
                    st.last_fire_step = step
                    out.append(self._make_alert(rule, step, "repeat",
                                                float(value), rule["severity"]))
            elif not beyond and st.firing and st.run_len >= need:
                st.firing = False
                out.append(self._make_alert(rule, step, "recovered",
                                            float(value), "info"))
        return out

    def intervention_alert(self, itv: Dict[str, Any], step: int,
                           scheduled: bool) -> Dict[str, Any]:
        """Build the always-on alert recorded whenever an intervention fires."""
        return {
            "id": util.new_id("alrt"),
            "step": step,
            "time": util.now_iso(),
            "kind": "intervention",
            "severity": "warning",
            "rule_id": "intervention",
            "rule_name": "干预措施",
            "metric": "",
            "op": "",
            "threshold": None,
            "value": None,
            "message": intervention_message(itv)
                       + ("（定时）" if scheduled else "（手动）"),
            "params": itv.get("params", {}),
            "intervention": itv.get("type", ""),
            "scheduled": scheduled,
        }


# --------------------------------------------------------------------------- #
# Alert log queries (filtering + pagination for long runs)
# --------------------------------------------------------------------------- #
def query_alerts(alerts: List[Dict[str, Any]], *, run_id: Optional[str] = None,
                 kind: Optional[str] = None, severity: Optional[str] = None,
                 metric: Optional[str] = None,
                 step_from: Optional[int] = None,
                 step_to: Optional[int] = None,
                 time_from: Optional[str] = None,
                 time_to: Optional[str] = None,
                 search: Optional[str] = None,
                 page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    """Filter, sort (newest first) and paginate an alert list.

    ``kind=fire`` also covers repeat reminders (any active-breach alert);
    ``time_from`` / ``time_to`` filter wall-clock ISO timestamps.
    """
    rows = alerts
    if kind:
        if kind == "fire":
            rows = [a for a in rows if a.get("kind") in ("fire", "repeat")]
        else:
            rows = [a for a in rows if a.get("kind") == kind]
    if severity:
        rows = [a for a in rows if a.get("severity") == severity]
    if metric:
        rows = [a for a in rows if a.get("metric") == metric]
    if step_from is not None:
        rows = [a for a in rows if a.get("step", 0) >= step_from]
    if step_to is not None:
        rows = [a for a in rows if a.get("step", 0) <= step_to]
    if time_from:
        rows = [a for a in rows if a.get("time", "") >= time_from]
    if time_to:
        rows = [a for a in rows if a.get("time", "") <= time_to]
    if search:
        q = search.lower()
        rows = [a for a in rows if q in str(a.get("message", "")).lower()
                or q in str(a.get("rule_name", "")).lower()]
    rows = sorted(rows, key=lambda a: (a.get("step", 0), a.get("time", "")),
                  reverse=True)
    total = len(rows)
    page = max(1, int(page))
    page_size = min(500, max(1, int(page_size)))
    start = (page - 1) * page_size
    return {"alerts": rows[start:start + page_size], "total": total,
            "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "counts": alert_counts(alerts)}


def alert_counts(alerts: List[Dict[str, Any]]) -> Dict[str, int]:
    """Whole-run facet counts (ignoring filters) for summary chips."""
    counts = {"total": len(alerts), "critical": 0, "warning": 0, "info": 0,
              "fire": 0, "recovered": 0, "intervention": 0}
    for a in alerts:
        sev = a.get("severity")
        if sev in ("critical", "warning", "info"):
            counts[sev] += 1
        kind = a.get("kind")
        if kind in ("fire", "repeat"):
            counts["fire"] += 1
        elif kind in ("recovered", "intervention"):
            counts[kind] += 1
    return counts
