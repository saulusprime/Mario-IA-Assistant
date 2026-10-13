"""
Motore di esecuzione: una macchina a stati che ascolta gli eventi del Mario fisico (gli stessi
dizionari del worker della dashboard e della CLI) e fa avanzare l'avventura passo per passo.

Uso:
    runner = AdventureRunner(adventure, emit=print, speaker=speaker)
    runner.start()
    runner.handle_event(ev)   # per ogni evento del worker
    runner.tick()             # ogni mezzo secondo: timeout e timer
    runner.status()           # stato per la pagina di gioco
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable, Optional

from .. import codes
from ..assistant.intents import event_data, short_name, tile_family
from ..lwp3 import le16
from .model import GESTURE_NAMES, Action, Adventure, Step, Trigger


class AdventureRunner:
    def __init__(self, adventure: Adventure, emit: Optional[Callable[[dict], None]] = None, speaker=None,
                 volume_setter: Optional[Callable[[int], Any]] = None, clock: Callable[[], float] = time.time,
                 webhooks: bool = False):
        self.adventure = adventure
        self.emit_cb = emit or (lambda ev: None)
        self.speaker = speaker                    # oggetto con speak(text, emotion) e play(name), facoltativo
        self.volume_setter = volume_setter        # callable(level) verso Mario, facoltativo
        self.clock = clock
        self.webhooks = webhooks                  # i POST verso URL esterni partono solo se abilitati
        self.state = "idle"                       # idle / running / finished
        self.outcome = ""                         # completata / tempo scaduto / abbandonata / terminata
        self.index = 0
        self.score = 0
        self.started = 0.0
        self.ended = 0.0
        self.step_started = 0.0
        self.log: list[dict] = []
        self.path: list[dict] = []                # basette lette durante la partita (stima della posizione)
        self.context: dict = {"pants": None, "color": None, "tile": None, "gesture": None, "lifted": None}
        self._progress: dict = {}                 # stato dei trigger composti (sequence, count)
        self._last_gesture: dict[str, float] = {}
        self._last_tile: Optional[int] = None
        self._last_color: Optional[int] = None
        self.step_results: list[dict] = []

    # ----- eventi in uscita ---------------------------------------------------------------

    def emit(self, kind: str, **fields) -> dict:
        ev = {"ts": self.clock(), "type": "ADVENTURE", "kind": kind, "raw": "", "interesting": False,
              "adventure": self.adventure.name, "score": self.score, "step": self.current_step.id if self.current_step else None,
              **fields}
        ev.setdefault("text", "")
        self.log.append({k: v for k, v in ev.items() if k not in ("raw", "interesting", "type")})
        try:
            self.emit_cb(ev)
        except Exception:
            pass
        return ev

    def say(self, text: str, emotion: str = "calma") -> None:
        if text and self.speaker is not None:
            try:
                self.speaker.speak(text, emotion)
            except Exception:
                pass

    def play(self, name: str) -> None:
        if name and self.speaker is not None:
            try:
                self.speaker.play(name)
            except Exception:
                pass

    # ----- stato ------------------------------------------------------------------------------

    @property
    def current_step(self) -> Optional[Step]:
        if self.state == "running" and 0 <= self.index < len(self.adventure.steps):
            return self.adventure.steps[self.index]
        return None

    def status(self) -> dict:
        step = self.current_step
        now = self.clock()
        return {
            "state": self.state, "outcome": self.outcome, "adventure": self.adventure.name, "score": self.score,
            "step_index": self.index, "steps_total": len(self.adventure.steps),
            "step": {"id": step.id, "title": step.title, "text": step.text, "trigger": step.trigger.describe(),
                     "timeout": step.timeout, "remaining": max(0.0, step.timeout - (now - self.step_started)) if step.timeout else None,
                     "progress": self._progress_text(step)} if step else None,
            "elapsed": round(now - self.started, 1) if self.started else 0.0,
            "time_limit": self.adventure.time_limit,
            "context": dict(self.context), "path": self.path[-12:], "results": self.step_results,
            "started": self.started, "ended": self.ended,
        }

    def _progress_text(self, step: Step) -> str:
        p = self._progress.get(step.id)
        if not p:
            return ""
        if step.trigger.kind == "count":
            return f"{p.get('n', 0)}/{step.trigger.times}"
        if step.trigger.kind == "sequence":
            return f"{p.get('n', 0)}/{len(step.trigger.steps)}"
        return ""

    # ----- ciclo di vita -----------------------------------------------------------------------

    def start(self) -> None:
        self.state, self.outcome = "running", ""
        self.index, self.score = 0, 0
        self.started = self.step_started = self.clock()
        self.ended = 0.0
        self.log.clear()
        self.path.clear()
        self.step_results.clear()
        self._progress.clear()
        self.emit("start", text=self.adventure.intro_text or f"Inizia: {self.adventure.name}", steps=len(self.adventure.steps))
        self.play("hello")
        self.say(self.adventure.intro_text, "gioia")
        if not self.adventure.steps:
            self._finish("completata")
            return
        self._announce()

    def stop(self, reason: str = "abbandonata") -> None:
        if self.state == "running":
            self._finish(reason)

    def _announce(self) -> None:
        step = self.current_step
        if step is None:
            return
        self.step_started = self.clock()
        self._progress.pop(step.id, None)
        self.emit("step", text=step.text, title=step.title, index=self.index, trigger=step.trigger.describe(),
                  timeout=step.timeout)
        self.say(step.text, "calma")

    def _finish(self, outcome: str) -> None:
        self.state, self.outcome = "finished", outcome
        self.ended = self.clock()
        text = self.adventure.end_text.format(score=self.score, outcome=outcome)
        self.emit("finish", text=text, outcome=outcome, elapsed=round(self.ended - self.started, 1),
                  results=self.step_results)
        self.play("powerup" if outcome == "completata" else "sad")
        self.say(text, "gioia" if outcome == "completata" else "tristezza")

    def _goto(self, step_id: Optional[str]) -> None:
        """Passa al passo indicato (o al successivo); finisce se non ce n'è."""
        if step_id:
            idx = self.adventure.step_index(step_id)
            if idx is None:
                idx = self.index + 1
        else:
            idx = self.index + 1
        if idx >= len(self.adventure.steps):
            self._finish("completata")
            return
        self.index = idx
        self._announce()

    # ----- eventi in ingresso -------------------------------------------------------------------

    def handle_event(self, ev: dict) -> bool:
        """Aggiorna il contesto e, se l'evento completa il trigger del passo, avanza. True se c'è stato un progresso."""
        signal = self._signal(ev)
        if signal is None:
            return False
        kind, payload = signal
        self._update_context(kind, payload)
        if self.state != "running":
            return False
        if not self._check_time():
            return False
        step = self.current_step
        if step is None:
            return False
        now = self.clock()
        if self._matches(step.trigger, step.id, kind, payload, now):
            self._succeed(step)
            return True
        return False

    def tick(self, now: Optional[float] = None) -> None:
        """Timeout del passo, trigger a tempo e limite dell'avventura; da chiamare ogni mezzo secondo."""
        if self.state != "running":
            return
        now = now if now is not None else self.clock()
        if not self._check_time(now):
            return
        step = self.current_step
        if step is None:
            return
        if step.trigger.kind == "timer" and now - self.step_started >= step.trigger.seconds:
            self._succeed(step)
            return
        if step.timeout and now - self.step_started >= step.timeout:
            self._timeout(step)

    def _check_time(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else self.clock()
        if self.adventure.time_limit and now - self.started >= self.adventure.time_limit:
            self._finish("tempo scaduto")
            return False
        return True

    # ----- segnali dagli eventi -------------------------------------------------------------------

    @staticmethod
    def _signal(ev: dict) -> Optional[tuple[str, dict]]:
        etype = ev.get("type")
        if etype == "PORT_VALUE_COMBINED":
            # più modi insieme: li proviamo uno per uno, vince il primo che produce un segnale
            for mode_text, info in (ev.get("modes") or {}).items():
                sig = AdventureRunner._signal({"type": "PORT_VALUE_SINGLE", "io_type": ev.get("io_type"),
                                               "mode": int(mode_text), "data": info.get("data")})
                if sig:
                    return sig
            return None
        if etype == "PORT_VALUE_SINGLE":
            io_type, mode = ev.get("io_type"), ev.get("mode")
            data = event_data(ev)
            if io_type == codes.IO_MARIO_ACCELEROMETER and mode == 1 and len(data) >= 2:
                return "gesture", {"bits": le16(data)}
            if io_type == codes.IO_MARIO_PANTS and mode in (None, 0) and data:
                return "pants", {"code": data[0] & 0x3F}
            if io_type == codes.IO_MARIO_SCANNER and mode == 0 and len(data) >= 4:
                return "scanner", {"barcode": le16(data[0:2]), "color": le16(data[2:4])}
            if io_type == codes.IO_MARIO_EVENTS and mode == 2 and len(data) >= 4:
                return "game_event", {"code": le16(data[0:2]), "value": le16(data[2:4])}
            return None
        if etype == "HUB_PROPERTIES" and ev.get("prop") == "BUTTON":
            return ("button", {}) if ev.get("value") in (True, 1) else None
        return None

    def _update_context(self, kind: str, payload: dict) -> None:
        if kind == "gesture":
            names = [n for n, bit in GESTURE_NAMES.items() if payload["bits"] & bit]
            if names:
                self.context["gesture"] = ", ".join(names)
        elif kind == "pants":
            self.context["pants"] = short_name(codes.PANTS.get(payload["code"], f"0x{payload['code']:02X}"))
        elif kind == "scanner":
            barcode, color = payload["barcode"], payload["color"]
            self.context["lifted"] = barcode == 0xFFFF and color == 0xFFFF
            if color != 0xFFFF:
                self.context["color"] = short_name(codes.COLORS.get(color, f"0x{color:04X}"))
            if barcode != 0xFFFF:
                name = short_name(codes.BARCODES.get(barcode, f"0x{barcode:04X}"))
                self.context["tile"] = name
                if barcode != self._last_tile and self.state == "running":
                    self.path.append({"ts": self.clock(), "code": barcode, "name": name, "family": tile_family(barcode)})
                    del self.path[:-50]
            self._last_tile = barcode if barcode != 0xFFFF else None

    # ----- trigger -------------------------------------------------------------------------------

    def _matches(self, t: Trigger, key: str, kind: str, payload: dict, now: float) -> bool:
        if t.kind == "sequence":
            return self._match_sequence(t, key, kind, payload, now)
        if t.kind == "count":
            return self._match_count(t, key, kind, payload, now)
        return self._match_simple(t, kind, payload, now)

    def _match_simple(self, t: Trigger, kind: str, payload: dict, now: float) -> bool:
        if t.kind == "gesture" and kind == "gesture":
            bit = GESTURE_NAMES.get((t.name or "").lower())
            if not bit or not payload["bits"] & bit:
                return False
            last = self._last_gesture.get(t.name or "", 0.0)
            self._last_gesture[t.name or ""] = now
            return now - last >= max(0.3, t.seconds)  # i gesti arrivano a raffica: una sola conta
        if t.kind == "barcode" and kind == "scanner":
            code = payload["barcode"]
            if code == 0xFFFF:
                return False
            if t.code is not None:
                return code == t.code
            fam = t.family or "any"
            return fam == "any" or tile_family(code) == fam
        if t.kind == "color" and kind == "scanner":
            return payload["color"] != 0xFFFF and payload["color"] == t.code
        if t.kind == "pants" and kind == "pants":
            return payload["code"] == t.code
        if t.kind == "button" and kind == "button":
            return True
        if t.kind == "game_event" and kind == "game_event":
            if payload["code"] != t.code:
                return False
            if t.value is not None and payload["value"] != t.value:
                return False
            if t.min_value is not None and payload["value"] < t.min_value:
                return False
            return True
        return False

    def _match_sequence(self, t: Trigger, key: str, kind: str, payload: dict, now: float) -> bool:
        p = self._progress.setdefault(key, {"n": 0, "t0": 0.0})
        if t.seconds and p["n"] and now - p["t0"] > t.seconds:
            p.update(n=0, t0=0.0)  # troppo lenti: si ricomincia
        sub = t.steps[p["n"]]
        if self._match_simple(sub, kind, payload, now):
            if p["n"] == 0:
                p["t0"] = now
            p["n"] += 1
            self.emit("progress", text=f"{p['n']}/{len(t.steps)}: {sub.describe()}", progress=f"{p['n']}/{len(t.steps)}")
            if p["n"] >= len(t.steps):
                return True
        elif p["n"] and self._match_simple(t.steps[0], kind, payload, now):
            p.update(n=1, t0=now)  # ricominciata dal primo elemento
        return False

    def _match_count(self, t: Trigger, key: str, kind: str, payload: dict, now: float) -> bool:
        if t.of is None:
            return False
        p = self._progress.setdefault(key, {"n": 0, "last": 0.0})
        if not self._match_simple(t.of, kind, payload, now):
            return False
        if now - p["last"] < max(0.0, t.seconds):
            return False
        p["n"] += 1
        p["last"] = now
        self.emit("progress", text=f"{p['n']}/{t.times}: {t.of.describe()}", progress=f"{p['n']}/{t.times}")
        return p["n"] >= t.times

    # ----- esiti e azioni -----------------------------------------------------------------------

    def _succeed(self, step: Step) -> None:
        self.score += step.points
        self.step_results.append({"id": step.id, "title": step.title, "outcome": "ok", "points": step.points,
                                  "seconds": round(self.clock() - self.step_started, 1)})
        self.emit("success", text=step.success_text or f"Fatto: {step.title}!", points=step.points,
                  seconds=round(self.clock() - self.step_started, 1))
        self.play(step.sound)
        self.say(step.success_text, step.emotion)
        target = self._apply(step.on_success)
        if self.state != "running":
            return
        self._goto(target if target is not None else step.next)

    def _timeout(self, step: Step) -> None:
        self.step_results.append({"id": step.id, "title": step.title, "outcome": "tempo scaduto", "points": 0,
                                  "seconds": round(step.timeout, 1)})
        self.emit("timeout", text=step.timeout_text or f"Tempo scaduto: {step.title}", timeout=step.timeout)
        self.play("sad")
        self.say(step.timeout_text, "tristezza")
        target = self._apply(step.on_timeout)
        if self.state != "running":
            return
        self._goto(target if target is not None else step.on_timeout_next)

    def _apply(self, actions: list[Action]) -> Optional[str]:
        """Esegue le azioni; ritorna l'id del passo a cui saltare se un'azione lo richiede."""
        target = None
        for a in actions:
            try:
                if a.kind == "message":
                    self.emit("message", text=a.text)
                elif a.kind == "say":
                    self.emit("message", text=a.text)
                    self.say(a.text, str(a.value or "calma"))
                elif a.kind == "sound":
                    self.play(str(a.value or a.text or "coin"))
                elif a.kind == "score":
                    self.score += int(a.value or 0)
                    self.emit("score", text=f"{int(a.value or 0):+d} punti", points=int(a.value or 0))
                elif a.kind == "volume":
                    if self.volume_setter is not None:
                        self.volume_setter(int(a.value))
                    self.emit("volume", text=f"volume di Mario {int(a.value)}", level=int(a.value))
                elif a.kind == "webhook":
                    self._webhook(a.text)
                elif a.kind == "goto":
                    target = str(a.value)
                elif a.kind == "end":
                    self._finish(str(a.value or "terminata"))
                    return None
            except Exception as exc:
                self.emit("error", text=f"azione {a.kind} fallita: {exc}")
        return target

    def _webhook(self, url: str) -> None:
        body = json.dumps(self.status(), ensure_ascii=False, default=str).encode("utf-8")
        if not self.webhooks:
            self.emit("webhook", text=f"webhook non inviato (disabilitati): {url}", url=url)
            return
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5):
            pass
        self.emit("webhook", text=f"webhook inviato a {url}", url=url)
