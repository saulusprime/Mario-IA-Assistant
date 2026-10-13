"""Collegamento del motore delle avventure con il worker Bluetooth, il database e la voce (dashboard)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from .engine import AdventureRunner
from .library import library
from .model import Adventure, load_examples, validate

log = logging.getLogger("mario.adventures")


class AdventureService:
    def __init__(self, worker, db, speaker=None, quiet: bool = False, start: bool = True, seed: bool = True):
        self.worker = worker
        self.db = db
        self.speaker = None if quiet else speaker
        self.runner: Optional[AdventureRunner] = None
        self.adventure_id: Optional[int] = None
        self._lock = threading.Lock()
        if seed and self.db.count_adventures() == 0:
            self.seed_examples()
        self._thread = threading.Thread(target=self._consume, name="adventure-events", daemon=True)
        if start:
            self._thread.start()

    # ----- catalogo ---------------------------------------------------------------------

    def seed_examples(self) -> int:
        n = 0
        for adv in load_examples():
            self.db.save_adventure(adv.to_dict())
            n += 1
        return n

    def list(self) -> list[dict]:
        return self.db.list_adventures()

    def get(self, adventure_id: int) -> Optional[dict]:
        return self.db.get_adventure(adventure_id)

    def save(self, data: dict, adventure_id: Optional[int] = None) -> int:
        adv = Adventure.from_dict(data)
        problems = validate(adv)
        if problems:
            raise ValueError("; ".join(problems))
        return self.db.save_adventure(adv.to_dict(), adventure_id)

    def delete(self, adventure_id: int) -> int:
        return self.db.delete_adventure(adventure_id)

    def library(self) -> dict:
        owned = {a["code"] for a in self.db.list_accessories("tile", owned=True) if a.get("code") is not None}
        return library(owned)

    # ----- partita -------------------------------------------------------------------------

    def _emit(self, ev: dict) -> None:
        self.worker.push(ev)

    def _set_volume(self, level: int) -> None:
        dev = getattr(self.worker, "device", None)
        if dev is not None and getattr(dev, "connected", False):
            self.worker.set_volume(level)

    def start(self, adventure_id: Optional[int] = None, data: Optional[dict] = None) -> dict:
        with self._lock:
            if data is None:
                row = self.db.get_adventure(int(adventure_id)) if adventure_id is not None else None
                if row is None:
                    raise ValueError("avventura non trovata")
                data = row["data"]
            adv = Adventure.from_dict(data)
            problems = validate(adv)
            if problems:
                raise ValueError("; ".join(problems))
            if self.runner is not None and self.runner.state == "running":
                self.runner.stop("abbandonata")
                self._store_run()
            self.adventure_id = int(adventure_id) if adventure_id is not None else None
            self.runner = AdventureRunner(adv, emit=self._emit, speaker=self.speaker, volume_setter=self._set_volume)
            self.runner.start()
            if self.runner.state == "finished":
                self._store_run()
        return self.status()

    def stop(self, reason: str = "abbandonata") -> dict:
        with self._lock:
            if self.runner is not None and self.runner.state == "running":
                self.runner.stop(reason)
                self._store_run()
        return self.status()

    def _store_run(self) -> None:
        r = self.runner
        if r is None or r.state != "finished" or getattr(r, "_stored", False):
            return
        try:
            self.db.add_run(self.adventure_id, r.adventure.name, r.started, r.ended, r.score, r.outcome, r.log)
            r._stored = True
        except Exception as exc:
            log.debug("partita non salvata: %s", exc)

    def simulate(self, ev: dict) -> bool:
        return self._handle(ev)

    def _handle(self, ev: dict) -> bool:
        with self._lock:
            r = self.runner
            if r is None or r.state != "running":
                return False
            progressed = r.handle_event(ev)
            if r.state == "finished":
                self._store_run()
            return progressed

    def _tick(self) -> None:
        with self._lock:
            r = self.runner
            if r is None or r.state != "running":
                return
            r.tick()
            if r.state == "finished":
                self._store_run()

    def status(self) -> dict:
        r = self.runner
        return {"runner": r.status() if r else None, "adventure_id": self.adventure_id,
                "voice": self.speaker is not None, "runs": self.db.list_runs(limit=10)}

    def runs(self, adventure_id: Optional[int] = None, limit: int = 50) -> list[dict]:
        return self.db.list_runs(adventure_id, limit)

    # ----- flusso del worker ------------------------------------------------------------------

    def _consume(self) -> None:
        import queue as _queue
        q = self.worker.open_stream()
        while True:
            try:
                ev = q.get(timeout=0.5)
            except _queue.Empty:
                try:
                    self._tick()
                except Exception as exc:
                    log.debug("tick avventura fallito: %s", exc)
                continue
            if ev.get("type") not in ("PORT_VALUE_SINGLE", "PORT_VALUE_COMBINED", "HUB_PROPERTIES"):
                continue
            try:
                self._handle(ev)
            except Exception as exc:
                log.debug("evento non gestito dall'avventura: %s", exc)
