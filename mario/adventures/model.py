"""Modello dati delle avventure e serializzazione JSON."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "adventures"

TRIGGER_KINDS = {
    "barcode": "basetta letta dallo scanner (codice preciso, famiglia o qualsiasi)",
    "color": "colore del terreno sotto i piedi",
    "pants": "pantaloni indossati",
    "gesture": "gesto (salto, scossa, urto, rotazione, caduta, ...)",
    "button": "pulsante sulla schiena",
    "game_event": "evento di gioco della porta 3 (monete, nemici, giri, tempo scaduto)",
    "timer": "dopo N secondi dall'inizio del passo",
    "level": "fase del livello di Mario (inizio, ultimi 15 secondi, fine, uscita) dall'evento 0x1803",
    "sequence": "più trigger in ordine entro N secondi",
    "count": "lo stesso trigger ripetuto N volte (anche tutte entro N secondi)",
    "any_of": "uno qualsiasi fra più trigger (A oppure B, anche \"oppure dopo N secondi\")",
}
ACTION_KINDS = {
    "message": "testo a schermo", "say": "frase detta dalla voce del PC", "sound": "effetto sonoro del PC",
    "score": "punti", "volume": "volume di Mario (0-100)", "webhook": "POST JSON a un URL (MQTT/WebSocket via ponte)",
    "goto": "salta a un altro passo", "end": "termina l'avventura",
    "music": "musica di sottofondo in ciclo: text = brano dell'archivio (percorso della pagina Suoni) o effetto, "
             "value = volume 0-100 (30); text vuoto o \"stop\" la ferma",
}
GESTURE_NAMES = {"salto": 0x8000, "caduta": 0x0800, "rotazione": 0x0100, "urto": 0x0001, "scossa": 0x0010,
                 "inversione": 0x2000, "movimento": 0x0200, "traslazione": 0x0400, "direzione": 0x1000}
# Valori dell'evento 0x1803 della porta 3 (ASIS, sezione 2): il livello dura 60 s dalla basetta Start
LEVEL_EVENT = 0x1803
LEVEL_PHASE_NAMES = {"inizio": 1, "ultimi_secondi": 4, "fine": 3, "uscita": 0}


def _int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    return int(str(value), 0)


@dataclass
class Trigger:
    kind: str
    code: Optional[int] = None          # barcode / color / pants / game_event
    family: Optional[str] = None        # barcode: start, goal, enemy, prize, friend, any
    name: Optional[str] = None          # gesture: salto, scossa, ...; level: inizio, ultimi_secondi, fine, uscita
    value: Optional[int] = None         # game_event: valore esatto
    min_value: Optional[int] = None     # game_event: valore minimo
    seconds: float = 0.0                # timer / sequence (entro) / count (pausa minima tra ripetizioni)
    times: int = 1                      # count
    window: float = 0.0                 # count: tutte le ripetizioni entro N secondi (0 = nessun limite)
    steps: list["Trigger"] = field(default_factory=list)  # sequence, any_of
    of: Optional["Trigger"] = None      # count

    @classmethod
    def from_dict(cls, d: dict) -> "Trigger":
        if isinstance(d, str):
            d = {"kind": d}
        return cls(kind=str(d.get("kind", "")), code=_int(d.get("code")), family=d.get("family"), name=d.get("name"),
                   value=_int(d.get("value")), min_value=_int(d.get("min_value")),
                   seconds=float(d.get("seconds") or d.get("within") or 0.0), times=int(d.get("times") or 1),
                   window=float(d.get("window") or 0.0),
                   steps=[cls.from_dict(x) for x in d.get("steps") or []],
                   of=cls.from_dict(d["of"]) if d.get("of") else None)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"kind": self.kind}
        if self.code is not None:
            d["code"] = f"0x{self.code:04X}"
        for key in ("family", "name", "value", "min_value"):
            if getattr(self, key) is not None:
                d[key] = getattr(self, key)
        if self.seconds:
            d["seconds"] = self.seconds
        if self.times != 1:
            d["times"] = self.times
        if self.window:
            d["window"] = self.window
        if self.steps:
            d["steps"] = [t.to_dict() for t in self.steps]
        if self.of:
            d["of"] = self.of.to_dict()
        return d

    def describe(self) -> str:
        from .library import describe_trigger
        return describe_trigger(self)


@dataclass
class Action:
    kind: str
    text: str = ""            # message / say / webhook (url) / music (brano)
    value: Any = None         # score (punti), volume (livello), goto (id passo), sound (nome), end (esito), music (volume)

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        if isinstance(d, str):
            d = {"kind": d}
        return cls(kind=str(d.get("kind", "")), text=str(d.get("text") or d.get("url") or ""), value=d.get("value"))

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"kind": self.kind}
        if self.text:
            d["text"] = self.text
        if self.value is not None:
            d["value"] = self.value
        return d


@dataclass
class Step:
    id: str
    title: str
    text: str                              # missione mostrata e detta all'inizio del passo
    trigger: Trigger
    points: int = 10
    success_text: str = ""
    timeout: float = 0.0                   # 0 = nessun limite
    timeout_text: str = ""
    on_success: list[Action] = field(default_factory=list)
    on_timeout: list[Action] = field(default_factory=list)
    next: Optional[str] = None             # id del passo successivo (default: il seguente)
    on_timeout_next: Optional[str] = None  # passo dopo il timeout (default: il seguente)
    sound: str = "coin"                    # effetto sonoro al successo
    emotion: str = "gioia"
    fail: Optional[Trigger] = None         # se succede questo il passo è perso (come un timeout, frase e azioni del timeout)

    @classmethod
    def from_dict(cls, d: dict, index: int) -> "Step":
        return cls(id=str(d.get("id") or f"passo{index + 1}"), title=str(d.get("title") or f"Passo {index + 1}"),
                   text=str(d.get("text") or ""), trigger=Trigger.from_dict(d.get("trigger") or {}),
                   points=int(d.get("points", 10) or 0), success_text=str(d.get("success_text") or ""),
                   timeout=float(d.get("timeout") or 0.0), timeout_text=str(d.get("timeout_text") or ""),
                   on_success=[Action.from_dict(a) for a in d.get("on_success") or []],
                   on_timeout=[Action.from_dict(a) for a in d.get("on_timeout") or []],
                   next=d.get("next"), on_timeout_next=d.get("on_timeout_next"),
                   sound=str(d.get("sound") or "coin"), emotion=str(d.get("emotion") or "gioia"),
                   fail=Trigger.from_dict(d["fail"]) if d.get("fail") else None)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "title": self.title, "text": self.text, "trigger": self.trigger.to_dict(),
                             "points": self.points}
        for key in ("success_text", "timeout", "timeout_text", "next", "on_timeout_next"):
            if getattr(self, key):
                d[key] = getattr(self, key)
        if self.on_success:
            d["on_success"] = [a.to_dict() for a in self.on_success]
        if self.on_timeout:
            d["on_timeout"] = [a.to_dict() for a in self.on_timeout]
        if self.sound != "coin":
            d["sound"] = self.sound
        if self.emotion != "gioia":
            d["emotion"] = self.emotion
        if self.fail:
            d["fail"] = self.fail.to_dict()
        return d


@dataclass
class Adventure:
    name: str
    description: str = ""
    author: str = ""
    version: int = 1
    steps: list[Step] = field(default_factory=list)
    intro_text: str = ""
    end_text: str = "Avventura finita! Hai fatto {score} punti."
    time_limit: float = 0.0        # secondi per tutta l'avventura (0 = nessuno)
    kid_mode: bool = True          # schermo con icone grandi

    @classmethod
    def from_dict(cls, d: dict) -> "Adventure":
        return cls(name=str(d.get("name") or "Senza nome"), description=str(d.get("description") or ""),
                   author=str(d.get("author") or ""), version=int(d.get("version") or 1),
                   steps=[Step.from_dict(s, i) for i, s in enumerate(d.get("steps") or [])],
                   intro_text=str(d.get("intro_text") or ""),
                   end_text=str(d.get("end_text") or "Avventura finita! Hai fatto {score} punti."),
                   time_limit=float(d.get("time_limit") or 0.0), kid_mode=bool(d.get("kid_mode", True)))

    @classmethod
    def from_json(cls, text: str) -> "Adventure":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "author": self.author, "version": self.version,
                "intro_text": self.intro_text, "end_text": self.end_text, "time_limit": self.time_limit,
                "kid_mode": self.kid_mode, "steps": [s.to_dict() for s in self.steps]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    def step_index(self, step_id: Optional[str]) -> Optional[int]:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        return None


def validate(adv: Adventure) -> list[str]:
    """Elenco di problemi (vuoto se l'avventura è coerente)."""
    problems = []
    if not adv.name.strip():
        problems.append("manca il nome")
    if not adv.steps:
        problems.append("nessun passo")
    ids = [s.id for s in adv.steps]
    if len(set(ids)) != len(ids):
        problems.append("id dei passi duplicati")
    for s in adv.steps:
        problems += [f"passo '{s.id}': {p}" for p in _validate_trigger(s.trigger)]
        if s.fail:
            problems += [f"passo '{s.id}', fail: {p}" for p in _validate_trigger(s.fail)]
        for ref in (s.next, s.on_timeout_next):
            if ref and ref not in ids:
                problems.append(f"passo '{s.id}': rimando a un passo inesistente '{ref}'")
        for a in s.on_success + s.on_timeout:
            if a.kind not in ACTION_KINDS:
                problems.append(f"passo '{s.id}': azione sconosciuta '{a.kind}'")
            elif a.kind == "goto" and a.value not in ids:
                problems.append(f"passo '{s.id}': goto verso un passo inesistente '{a.value}'")
            elif a.kind == "volume" and not (isinstance(a.value, (int, float)) and 0 <= a.value <= 100):
                problems.append(f"passo '{s.id}': volume fuori da 0-100")
            elif a.kind == "music" and a.value is not None and not (isinstance(a.value, (int, float))
                                                                    and 0 <= a.value <= 100):
                problems.append(f"passo '{s.id}': volume della musica fuori da 0-100")
        if s.timeout < 0:
            problems.append(f"passo '{s.id}': timeout negativo")
    return problems


def _validate_trigger(t: Trigger) -> list[str]:
    if t.kind not in TRIGGER_KINDS:
        return [f"trigger sconosciuto '{t.kind}'"]
    out = []
    if t.kind in ("color", "pants") and t.code is None:
        out.append(f"trigger {t.kind} senza codice")
    if t.kind == "barcode" and t.code is None and not t.family:
        out.append("trigger barcode senza codice né famiglia")
    if t.kind == "gesture" and (t.name or "").lower() not in GESTURE_NAMES:
        out.append(f"gesto sconosciuto '{t.name}' (ammessi: {', '.join(GESTURE_NAMES)})")
    if t.kind == "game_event" and t.code is None:
        out.append("trigger game_event senza codice")
    if t.kind == "level" and (t.name or "").lower() not in LEVEL_PHASE_NAMES:
        out.append(f"fase del livello sconosciuta '{t.name}' (ammesse: {', '.join(LEVEL_PHASE_NAMES)})")
    if t.kind == "timer" and t.seconds <= 0:
        out.append("timer senza secondi")
    if t.kind == "sequence":
        if len(t.steps) < 2:
            out.append("sequenza con meno di due trigger")
        for sub in t.steps:
            out += _validate_trigger(sub)
            if sub.kind in ("timer", "sequence", "count", "any_of"):
                out.append(f"in una sequenza vanno trigger semplici, non '{sub.kind}'")
    if t.kind == "count":
        if t.of is None or t.times < 2:
            out.append("count richiede 'of' e times >= 2")
        else:
            out += _validate_trigger(t.of)
            if t.of.kind in ("timer", "sequence", "count", "any_of"):
                out.append(f"count ripete un trigger semplice, non '{t.of.kind}'")
        if t.window < 0:
            out.append("count con finestra negativa")
        elif t.window and t.seconds * (t.times - 1) > t.window:
            out.append(f"count impossibile: {t.times} volte a {t.seconds:g} s di distanza non stanno in {t.window:g} s")
    if t.kind == "any_of":
        if len(t.steps) < 2:
            out.append("any_of con meno di due trigger")
        for sub in t.steps:
            out += _validate_trigger(sub)
            if sub.kind == "any_of":
                out.append("any_of dentro any_of: basta elencare tutti i trigger nel primo")
    return out


def example_files(directory: Path = EXAMPLES_DIR) -> list[tuple[str, Adventure]]:
    """Avventure di esempio con il nome del file (senza estensione)."""
    return [(path.stem, Adventure.from_json(path.read_text(encoding="utf-8")))
            for path in sorted(Path(directory).glob("*.json"))]


def load_examples(directory: Path = EXAMPLES_DIR) -> list[Adventure]:
    return [adv for _, adv in example_files(directory)]
