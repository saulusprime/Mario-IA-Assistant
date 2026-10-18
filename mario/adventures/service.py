"""Collegamento del motore delle avventure con il worker Bluetooth, il database e la voce (dashboard)."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from .. import codes
from ..assistant.intents import short_name, tile_family
from .engine import AdventureRunner
from .library import library
from .model import Adventure, example_files, validate

log = logging.getLogger("mario.adventures")

EXAMPLES_KEY = "adventures.examples"
# Esempi caricati prima che il database tenesse il conto di quelli già offerti
FIRST_EXAMPLES = ("01-caccia-ai-colori", "02-percorso-a-ostacoli", "03-quiz-dei-nemici")


def draft_from_tiles(tiles: list[dict], name: str = "") -> dict:
    """Avventura "percorso" dalle basette registrate: un passo per basetta, nello stesso ordine. Il tempo
    impiegato durante la registrazione suggerisce il timeout (tre volte tanto, almeno 30 s)."""
    steps = []
    for i, tile in enumerate(tiles):
        took = tile["ts"] - tiles[i - 1]["ts"] if i else 0.0
        steps.append({"id": f"tappa{i + 1}", "title": tile["name"], "text": f"Porta Mario sulla basetta {tile['name']}!",
                      "trigger": {"kind": "barcode", "code": tile["hex"]}, "points": 10,
                      "success_text": "Trovata!" if i + 1 < len(tiles) else "Percorso completato!",
                      "timeout": int(max(30, 3 * took)) if i else 0})
    when = time.strftime("%d/%m %H:%M")
    return {"name": name or f"Percorso registrato {when}", "author": "", "version": 1,
            "description": f"Percorso di {len(tiles)} basette registrato il {when} facendo camminare Mario.",
            "intro_text": "Segui il percorso: ti dico io la prossima basetta.",
            "end_text": "Percorso finito! Hai fatto {score} punti.", "time_limit": 0, "kid_mode": True, "steps": steps}


def seed_examples(db) -> int:
    """Aggiunge al database gli esempi non ancora offerti. Un esempio nuovo arriva anche in un database già in
    uso; uno cancellato dall'utente non torna."""
    raw = db.get_setting(EXAMPLES_KEY)
    if raw is not None:
        offered = set(json.loads(raw))
    else:
        offered = set(FIRST_EXAMPLES) if db.count_adventures() else set()
    added = 0
    for stem, adv in example_files():
        if stem not in offered:
            db.save_adventure(adv.to_dict())
            offered.add(stem)
            added += 1
    if raw is None or added:
        db.set_setting(EXAMPLES_KEY, json.dumps(sorted(offered)))
    return added


class AdventureService:
    def __init__(self, worker, db, speaker=None, quiet: bool = False, start: bool = True, seed: bool = True):
        self.worker = worker
        self.db = db
        self.speaker = None if quiet else speaker
        self.runner: Optional[AdventureRunner] = None
        self.adventure_id: Optional[int] = None
        self.recording: Optional[dict] = None     # modalità "registra": basette lette in ordine
        self._lock = threading.Lock()
        if seed:
            seed_examples(self.db)
        self._thread = threading.Thread(target=self._consume, name="adventure-events", daemon=True)
        if start:
            self._thread.start()

    # ----- catalogo ---------------------------------------------------------------------

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

    # ----- modalità "registra": il percorso costruito facendo camminare Mario sulle basette ------

    def record_start(self) -> dict:
        with self._lock:
            self.recording = {"started": time.time(), "tiles": []}
        return self.record_status()

    def record_status(self) -> dict:
        rec = self.recording
        return {"recording": rec is not None, "tiles": list(rec["tiles"]) if rec else []}

    def record_stop(self, name: str = "") -> dict:
        """Chiude la registrazione e restituisce la bozza dell'avventura (non salvata: la salva l'editor)."""
        with self._lock:
            rec, self.recording = self.recording, None
        tiles = rec["tiles"] if rec else []
        return {"tiles": tiles, "draft": draft_from_tiles(tiles, name)}

    def _record(self, ev: dict) -> None:
        rec = self.recording
        if rec is None:
            return
        signal = AdventureRunner._signal(ev)
        if signal is None or signal[0] != "scanner" or signal[1]["barcode"] == 0xFFFF:
            return
        code = signal[1]["barcode"]
        if rec["tiles"] and rec["tiles"][-1]["code"] == code:
            return  # la stessa basetta letta di nuovo subito dopo: una sola tappa
        name = short_name(codes.BARCODES.get(code, f"0x{code:04X}"))
        rec["tiles"].append({"code": code, "hex": f"0x{code:04X}", "name": name, "family": tile_family(code),
                             "ts": float(ev.get("ts") or time.time())})

    def simulate(self, ev: dict) -> bool:
        return self._handle(ev)

    def _handle(self, ev: dict) -> bool:
        with self._lock:
            self._record(ev)
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
