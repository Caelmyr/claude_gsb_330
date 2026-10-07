"""Smoke tests for the simulation engines, storage and run lifecycle.

Run directly::

    python3 tests/smoke.py

Each check is independent and prints PASS / FAIL; the script exits non-zero on
the first failure so it can be wired into CI or a pre-commit hook.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import alerts, models, report, storage  # noqa: E402
from backend.engine import make_engine  # noqa: E402
from backend.run_manager import manager  # noqa: E402

_ENGINES = ["traffic/ca", "traffic/abm", "ecology/ca", "ecology/abm",
            "epidemic/ca", "epidemic/abm"]


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"PASS  {name}")
    except AssertionError as exc:
        print(f"FAIL  {name}: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  {name}: {exc}")
        sys.exit(1)


def engines_step() -> None:
    for key in _ENGINES:
        d, m = key.split("/")
        eng = make_engine(d, m, seed=42)
        for _ in range(5):
            eng.step()
        assert eng.step_count == 5, key
        assert len(eng.individuals()) > 0, f"{key} has no individuals"
        stats = eng.stats()
        assert stats, f"{key} produced empty stats"
        snap = eng.snapshot()
        assert snap["step"] == 5
        assert snap["stats"] == stats


def interventions_apply() -> None:
    eng = make_engine("epidemic", "abm", seed=1)
    before = eng.stats()["susceptible"]
    res = eng.apply_intervention({"type": "vaccinate", "params": {"fraction": 1.0}})
    assert res["applied"], res
    assert eng.stats()["susceptible"] == 0
    assert before > 0


def storage_atomic_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        # Redirect the module's DATA_DIR for this isolated check.
        old = storage.DATA_DIR
        storage.DATA_DIR = td
        try:
            storage.ensure_dirs()
            storage.save_scene({"id": "x", "name": "t", "updated_at": "z"})
            assert storage.load_scene("x")["name"] == "t"
            storage.save_step("r1", 0, {"step": 0, "v": 1})
            storage.save_step("r1", 7, {"step": 7, "v": 2})
            assert storage.load_step("r1", 7)["v"] == 2
            assert storage.list_steps("r1") == [0, 7]
        finally:
            storage.DATA_DIR = old


def run_lifecycle() -> None:
    scene = models.Scene(domain="epidemic", model="abm",
                         config={"n": 200, "width": 300, "height": 300,
                                 "initial_infected": 5})
    meta = manager.create_run(scene, seed=1, snapshot_interval=2)
    rid = meta["id"]
    try:
        r = manager.step(rid, 6)
        assert r["step"] == 6
        series = manager.get_series(rid)
        assert series[0]["step"] == 0 and series[-1]["step"] == 6
        assert len(manager.get_individuals(rid, 6)) == 200
        # snapshot_interval=2 -> full snapshots persisted at 0,2,4,6
        steps = storage.list_steps(rid)
        assert steps == [0, 2, 4, 6], steps
        rpt = report.generate_report(rid)
        assert rpt["steps"] == 7
    finally:
        manager.delete_run(rid)


def alert_rules_and_anti_flooding() -> None:
    scene = models.Scene(domain="traffic", model="ca",
                         config={"density": 0.2, "length": 80, "lanes": 2})
    meta = manager.create_run(scene, seed=3, snapshot_interval=10)
    rid = meta["id"]
    try:
        # Built-in defaults were created for the run.
        rules = manager.get_alert_rules(rid)
        assert rules and all(r["built_in"] for r in rules)

        # Hysteresis state machine: noise around the threshold never fires.
        mon = alerts.AlertMonitor(rid, "traffic", rules=[])
        mon.upsert_rule({"name": "拥堵", "metric": "stopped", "op": "gte",
                         "threshold": 0.8, "severity": "critical",
                         "for_steps": 3})
        assert mon.evaluate({"stopped": 0.9}, 1) == []
        assert mon.evaluate({"stopped": 0.1}, 2) == []
        assert mon.evaluate({"stopped": 0.9}, 3) == []

        # Three consecutive breaches -> one fire; same excursion logs once.
        fired = []
        for step, v in ((4, 0.9), (5, 0.95), (6, 0.99)):
            fired += mon.evaluate({"stopped": v}, step)
        assert len(fired) == 1 and fired[0]["kind"] == "fire"
        assert mon.evaluate({"stopped": 0.99}, 7) == []  # no repeat

        # Recovery needs N consecutive in-range steps; then re-arm works.
        mon.evaluate({"stopped": 0.1}, 8)
        mon.evaluate({"stopped": 0.1}, 9)
        rec = mon.evaluate({"stopped": 0.1}, 10)
        assert len(rec) == 1 and rec[0]["kind"] == "recovered"
        again = []
        for step, v in ((11, 0.9), (12, 0.9), (13, 0.9)):
            again += mon.evaluate({"stopped": v}, step)
        assert len(again) == 1 and again[0]["kind"] == "fire"

        # Rule validation rejects unknown metrics / bad ops / non-numbers.
        bad = alerts.validate_rule(
            {"name": "x", "metric": "nope", "op": "??", "threshold": "x"},
            ["stopped"])
        assert len(bad) >= 3

        # Query filtering + pagination.
        sample = [
            {"id": "a1", "step": 1, "time": "2026-01-01T00:00:01+00:00",
             "kind": "fire", "severity": "critical", "message": "m1"},
            {"id": "a2", "step": 5, "time": "2026-01-01T00:00:05+00:00",
             "kind": "recovered", "severity": "info", "message": "m2"},
            {"id": "a3", "step": 9, "time": "2026-01-01T00:00:09+00:00",
             "kind": "intervention", "severity": "warning", "message": "m3"},
        ]
        q = alerts.query_alerts(sample, step_from=2, step_to=8,
                                page=1, page_size=1)
        assert q["total"] == 1 and q["alerts"][0]["id"] == "a2"
        qp = alerts.query_alerts(sample, page=1, page_size=1)
        assert qp["total"] == 3 and qp["pages"] == 3 and len(qp["alerts"]) == 1
        assert alerts.query_alerts(sample, kind="fire")["total"] == 1
        assert alerts.query_alerts(sample, kind="intervention")["total"] == 1
    finally:
        manager.delete_run(rid)


def main() -> None:
    check("six engines step and snapshot", engines_step)
    check("interventions apply", interventions_apply)
    check("atomic sharded storage", storage_atomic_roundtrip)
    check("run lifecycle + report", run_lifecycle)
    check("alert rules + anti-flooding", alert_rules_and_anti_flooding)
    print("\nall smoke tests passed")


if __name__ == "__main__":
    main()
