"""Run lifecycle management: real-time stepping, batch runs, interventions.

A :class:`RunManager` owns the in-memory engines for the current server
process.  Every mutation funnels through :mod:`backend.storage` so the
atomic-write / time-step-sharding guarantees hold whether a step comes from the
UI or a background batch.  Engines are kept in memory while a run exists in
this process so a run can be stepped interactively; batch runs that belong to a
comparison experiment can drop their engine on completion to bound memory.

Concurrency: each run has its own re-entrant lock, so long batch runs on one
scene never block interactive stepping on another.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from . import alerts as alerts_mod
from . import models, storage, util
from .engine import make_engine
from .engine.base import Engine


class RunManager:
    def __init__(self) -> None:
        self._engines: Dict[str, Engine] = {}
        self._monitors: Dict[str, alerts_mod.AlertMonitor] = {}
        self._locks: Dict[str, threading.RLock] = {}
        self._abort: set = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _lock_for(self, run_id: str) -> threading.RLock:
        with self._lock:
            return self._locks.setdefault(run_id, threading.RLock())

    def _engine(self, run_id: str) -> Optional[Engine]:
        with self._lock:
            return self._engines.get(run_id)

    def _monitor(self, run_id: str, meta: Optional[Dict[str, Any]] = None
                 ) -> alerts_mod.AlertMonitor:
        """Return the run's alert monitor, creating it from saved rules.

        A monitor only exists while the engine does (interactive lifecycle);
        missing rules on disk get the domain's built-in defaults.
        """
        with self._lock:
            mon = self._monitors.get(run_id)
            if mon is None:
                if meta is None:
                    meta = storage.load_run_meta(run_id)
                mon = alerts_mod.AlertMonitor(run_id, meta["domain"])
                self._monitors[run_id] = mon
            return mon

    def _record_alerts(self, run_id: str, new_alerts: List[Dict[str, Any]],
                       pending: Optional[List[Dict[str, Any]]] = None
                       ) -> None:
        """Persist newly detected alerts, extending the run's alert log."""
        if not new_alerts:
            return
        if pending is not None:
            pending.extend(new_alerts)
        else:
            log = storage.load_alerts(run_id)
            log.extend(new_alerts)
            storage.save_alerts(run_id, log)

    def _require(self, run_id: str) -> tuple:
        eng = self._engine(run_id)
        meta = storage.load_run_meta(run_id)
        if meta is None:
            raise KeyError(f"run not found: {run_id}")
        return eng, meta

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #
    def create_run(self, scene: models.Scene, name: Optional[str] = None,
                   seed: Optional[int] = None,
                   snapshot_interval: int = 1) -> Dict[str, Any]:
        config = models.resolve_config(scene)
        if seed is None:
            seed = int(config.get("seed", 0))
        engine = make_engine(scene.domain, scene.model, config=config, seed=seed)
        run_id = util.new_id("run")
        now = util.now_iso()
        meta: Dict[str, Any] = {
            "id": run_id,
            "name": name or f"{scene.name} · 运行",
            "scene_id": scene.id,
            "scene_name": scene.name,
            "domain": scene.domain,
            "model": scene.model,
            "config": config,
            "interventions": [{**i, "applied": False}
                              for i in scene.interventions],
            "snapshot_interval": max(1, int(snapshot_interval)),
            "status": "ready",
            "current_step": 0,
            "total_steps": 0,
            "seed": seed,
            "created_at": now,
            "updated_at": now,
        }
        storage.create_run_dir(run_id)
        storage.save_step(run_id, 0, engine.snapshot())
        storage.save_series(run_id, [{"step": 0, **engine.stats()}])
        storage.save_events(run_id, [])
        rules = alerts_mod.default_rules_for(scene.domain)
        storage.save_alert_rules(run_id, rules)
        storage.save_alerts(run_id, [])
        storage.save_run_meta(meta)
        with self._lock:
            self._engines[run_id] = engine
            self._monitors[run_id] = alerts_mod.AlertMonitor(
                run_id, scene.domain, rules=rules)
        return meta

    # ------------------------------------------------------------------ #
    # Stepping
    # ------------------------------------------------------------------ #
    def _apply_due(self, run_id: str, engine: Engine,
                   meta: Dict[str, Any], step: int,
                   pending_alerts: Optional[List[Dict[str, Any]]] = None
                   ) -> None:
        """Apply any scheduled intervention whose ``at_step`` has been reached."""
        events = storage.load_events(run_id)
        monitor = self._monitor(run_id, meta)
        changed = False
        for itv in meta["interventions"]:
            if itv.get("applied"):
                continue
            if int(itv.get("at_step", 0)) <= step:
                result = engine.apply_intervention(itv)
                itv["applied"] = True
                events.append({"step": step, "type": itv["type"],
                               "params": itv.get("params", {}),
                               "scheduled": True, "result": result})
                self._record_alerts(
                    run_id,
                    [monitor.intervention_alert(itv, step, scheduled=True)],
                    pending_alerts)
                changed = True
        if changed:
            storage.save_events(run_id, events)
            storage.save_run_meta(meta)

    def step(self, run_id: str, n: int = 1) -> Dict[str, Any]:
        """Advance ``n`` steps and return the current snapshot + stats."""
        with self._lock_for(run_id):
            engine, meta = self._require(run_id)
            if engine is None:
                raise RuntimeError("该运行未载入内存（服务器重启后不可续跑），请重开新运行")
            if meta["status"] in ("finished", "stopped"):
                meta["status"] = "ready"
            monitor = self._monitor(run_id, meta)
            new_alerts: List[Dict[str, Any]] = []
            for _ in range(int(n)):
                self._apply_due(run_id, engine, meta, engine.step_count,
                                pending_alerts=new_alerts)
                engine.step()
                meta["current_step"] = engine.step_count
                meta["updated_at"] = util.now_iso()
                stats = engine.stats()
                storage.append_series(run_id, {"step": engine.step_count,
                                               **stats})
                new_alerts.extend(monitor.evaluate(stats, engine.step_count))
                if engine.step_count % meta["snapshot_interval"] == 0:
                    storage.save_step(run_id, engine.step_count, engine.snapshot())
            self._record_alerts(run_id, new_alerts)
            storage.save_run_meta(meta)
            return {"step": engine.step_count, "stats": engine.stats(),
                    "snapshot": engine.snapshot(), "alerts": new_alerts}

    def run_batch(self, run_id: str, steps: int,
                  snapshot_interval: Optional[int] = None,
                  keep_engine: bool = True) -> Dict[str, Any]:
        """Run ``steps`` steps to completion, returning final stats.

        Series rows are accumulated in memory and flushed periodically (and at
        the end) so the per-step write cost stays O(1) amortised even for very
        long runs.
        """
        with self._lock_for(run_id):
            engine, meta = self._require(run_id)
            if engine is None:
                raise RuntimeError("该运行未载入内存，请重开新运行")
            if snapshot_interval is not None:
                meta["snapshot_interval"] = max(1, int(snapshot_interval))
            meta["status"] = "running"
            storage.save_run_meta(meta)

            series = storage.load_series(run_id)
            batch_alerts = storage.load_alerts(run_id)
            monitor = self._monitor(run_id, meta)
            self._abort.discard(run_id)
            for _ in range(int(steps)):
                if run_id in self._abort:
                    break
                self._apply_due(run_id, engine, meta, engine.step_count,
                                pending_alerts=batch_alerts)
                engine.step()
                meta["current_step"] = engine.step_count
                stats = engine.stats()
                series.append({"step": engine.step_count, **stats})
                batch_alerts.extend(monitor.evaluate(stats, engine.step_count))
                if engine.step_count % meta["snapshot_interval"] == 0:
                    storage.save_step(run_id, engine.step_count, engine.snapshot())
                if engine.step_count % 50 == 0:
                    storage.save_series(run_id, series)
                    storage.save_alerts(run_id, batch_alerts)
                    meta["updated_at"] = util.now_iso()
                    storage.save_run_meta(meta)

            meta["status"] = "stopped" if run_id in self._abort else "finished"
            meta["updated_at"] = util.now_iso()
            storage.save_series(run_id, series)
            storage.save_alerts(run_id, batch_alerts)
            storage.save_run_meta(meta)
            self._abort.discard(run_id)

            final = engine.snapshot()
            if not keep_engine:
                with self._lock:
                    self._engines.pop(run_id, None)
                    self._monitors.pop(run_id, None)
            return {"step": engine.step_count, "stats": engine.stats(),
                    "snapshot": final,
                    "alerts_count": len(batch_alerts)}

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #
    def pause(self, run_id: str) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            meta["status"] = "paused"
            meta["updated_at"] = util.now_iso()
            storage.save_run_meta(meta)
            return meta

    def resume(self, run_id: str) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            meta["status"] = "ready"
            meta["updated_at"] = util.now_iso()
            storage.save_run_meta(meta)
            return meta

    def stop(self, run_id: str) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            with self._lock:
                self._abort.add(run_id)
            meta["status"] = "stopped"
            meta["updated_at"] = util.now_iso()
            storage.save_run_meta(meta)
            return meta

    def reset(self, run_id: str) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            seed = meta.get("seed", 0)
            engine = make_engine(meta["domain"], meta["model"],
                                 config=meta["config"], seed=seed)
            with self._lock:
                self._engines[run_id] = engine
                # Rules survive a reset; only the detection state is cleared.
                mon = self._monitors.get(run_id)
                if mon is not None:
                    mon.reset_state()
            for itv in meta["interventions"]:
                itv["applied"] = False
            meta["current_step"] = 0
            meta["status"] = "ready"
            meta["updated_at"] = util.now_iso()
            storage.save_step(run_id, 0, engine.snapshot())
            storage.save_series(run_id, [{"step": 0, **engine.stats()}])
            storage.save_events(run_id, [])
            storage.save_alerts(run_id, [])
            storage.save_run_meta(meta)
            return meta

    def delete_run(self, run_id: str) -> bool:
        with self._lock_for(run_id):
            with self._lock:
                self._engines.pop(run_id, None)
                self._monitors.pop(run_id, None)
                self._locks.pop(run_id, None)
                self._abort.discard(run_id)
            return storage.delete_run(run_id)

    # ------------------------------------------------------------------ #
    # Interventions
    # ------------------------------------------------------------------ #
    def apply_intervention(self, run_id: str,
                           itv: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock_for(run_id):
            engine, meta = self._require(run_id)
            if engine is None:
                raise RuntimeError("该运行未载入内存，请重开新运行")
            result = engine.apply_intervention(itv)
            events = storage.load_events(run_id)
            events.append({"step": engine.step_count, "type": itv["type"],
                           "params": itv.get("params", {}),
                           "scheduled": False, "result": result})
            storage.save_events(run_id, events)
            alert = self._monitor(run_id, meta).intervention_alert(
                itv, engine.step_count, scheduled=False)
            self._record_alerts(run_id, [alert])
            meta["updated_at"] = util.now_iso()
            storage.save_run_meta(meta)
            return result

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def status(self, run_id: str) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            out = dict(meta)
            engine = self._engine(run_id)
            if engine is not None:
                out["stats"] = engine.stats()
            return out

    def get_snapshot(self, run_id: str, step: Optional[int] = None) -> Dict[str, Any]:
        with self._lock_for(run_id):
            engine, meta = self._require(run_id)
            if step is None:
                step = meta["current_step"]
            if engine is not None and int(step) == engine.step_count:
                return engine.snapshot()
            snap = storage.load_step(run_id, int(step))
            if snap is None:
                raise KeyError(f"snapshot not found: step {step}")
            return snap

    def get_series(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock_for(run_id):
            self._require(run_id)
            return storage.load_series(run_id)

    def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock_for(run_id):
            self._require(run_id)
            return storage.load_events(run_id)

    # ------------------------------------------------------------------ #
    # Event alert center
    # ------------------------------------------------------------------ #
    def get_alert_rules(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            return self._monitor(run_id, meta).get_rules()

    def upsert_alert_rule(self, run_id: str,
                          data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            monitor = self._monitor(run_id, meta)
            errors = alerts_mod.validate_rule(data, monitor.known_metrics())
            if errors:
                raise ValueError("；".join(errors))
            return monitor.upsert_rule(data)

    def delete_alert_rule(self, run_id: str, rule_id: str) -> bool:
        with self._lock_for(run_id):
            self._require(run_id)
            return self._monitor(run_id).delete_rule(rule_id)

    def restore_alert_rules(self, run_id: str) -> List[Dict[str, Any]]:
        """Reset the rule book to the domain's built-in defaults."""
        with self._lock_for(run_id):
            _, meta = self._require(run_id)
            rules = alerts_mod.default_rules_for(meta["domain"])
            storage.save_alert_rules(run_id, rules)
            with self._lock:
                monitor = alerts_mod.AlertMonitor(run_id, meta["domain"],
                                                  rules=rules)
                self._monitors[run_id] = monitor
            return rules

    def get_alerts(self, run_id: str,
                   filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock_for(run_id):
            self._require(run_id)
            return alerts_mod.query_alerts(storage.load_alerts(run_id),
                                           **(filters or {}))

    def latest_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Newest alerts across every run (for the global real-time feed)."""
        out: List[Dict[str, Any]] = []
        for meta in storage.list_runs():
            run_id = meta["id"]
            for a in storage.load_alerts(run_id):
                out.append({**a, "run_id": run_id, "run_name": meta.get("name"),
                            "domain": meta.get("domain")})
        out.sort(key=lambda a: (a.get("time", ""), a.get("step", 0)),
                 reverse=True)
        return out[:max(1, int(limit))]

    def get_individuals(self, run_id: str, step: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.get_snapshot(run_id, step).get("individuals", [])


# Global singleton used by the Flask app.
manager = RunManager()
