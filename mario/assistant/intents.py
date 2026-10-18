"""
Ingresso: trasforma gli eventi dei sensori di Mario in "intenti" per la sessione con il modello.

Un intento ha un tipo (`prompt` = frase da mandare al modello, `control` = azione locale come
"ripeti" o "attiva/disattiva l'ascolto"), un nome di regola, il testo e l'origine leggibile.
Il mapper tiene anche lo stato del corpo (pantaloni, colore del terreno, ultima basetta, ultimo
gesto, batteria): finisce nel prompt di sistema, così il modello "sa" com'è fatto Mario adesso.

Gli eventi accettati sono i dizionari prodotti dal worker della dashboard (`BleWorker._on_message`)
o dalla CLI (`event_from_message`): servono `type`, e per i valori di porta `io_type`, `mode` e i
byte (`data`, oppure `raw` con il messaggio intero in esadecimale).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .. import codes
from ..lwp3 import le16


@dataclass
class Intent:
    kind: str            # "prompt" oppure "control"
    name: str            # nome della regola (salto, pantaloni, pulsante, ...)
    text: str            # prompt: frase per il modello; control: azione (toggle_listen, repeat, stop)
    source: str          # origine leggibile, es. "gesto: salto"
    priority: int = 1    # 2 = può interrompere una risposta in corso
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Rule:
    name: str
    kind: str            # prompt / control
    text: str            # testo base del prompt o azione di controllo
    description: str     # per la pagina della dashboard
    priority: int = 1
    enabled: bool = True
    cooldown: float = 2.0


# Regole predefinite. I testi sono in prima persona: è Mario che "sente" il proprio corpo.
DEFAULT_RULES: dict[str, Rule] = {
    "salto": Rule("salto", "prompt", "Ho appena fatto un salto!", "gesto salto (bit 0x8000)", 1, True, 2.0),
    "caduta": Rule("caduta", "prompt", "Sono caduto per terra con un gran botto!",
                   "caduta / impatto forte (bit 0x0800)", 2, True, 3.0),
    "rotazione": Rule("rotazione", "prompt", "Mi stanno facendo girare su me stesso.",
                      "rotazione (bit 0x0100)", 1, True, 8.0),
    "urto": Rule("urto", "prompt", "Ho preso un colpetto.", "urto (bit 0x0001): spento perché molto frequente",
                 1, False, 3.0),
    "scossa": Rule("scossa", "control", "repeat", "scossa (bit 0x0010): ripete l'ultima risposta", 1, True, 3.0),
    "pulsante": Rule("pulsante", "control", "toggle_listen",
                     "pulsante sulla schiena: attiva o sospende l'ascolto dei sensori", 2, True, 0.5),
    "pantaloni": Rule("pantaloni", "prompt", "Mi hanno cambiato i pantaloni: ora indosso {name}. {hint}",
                      "cambio pantaloni (porta 2)", 1, True, 1.0),
    "basetta": Rule("basetta", "prompt", "Sono sulla basetta '{name}'. {hint}",
                    "basetta con barcode letta dallo scanner (porta 1)", 1, True, 4.0),
    "terreno": Rule("terreno", "prompt", "Il terreno sotto i miei piedi è {name}. {hint}",
                    "cambio di colore del terreno (porta 1)", 1, True, 8.0),
    "batteria": Rule("batteria", "prompt", "Ho la batteria quasi scarica, mi sento stanco.",
                     "batteria sotto il 15 % (una volta sola)", 1, True, 600.0),
    # Porta 3 (eventi di gioco). Fino al 18/10 "moneta" leggeva 0x3861, che è il conto alla rovescia di Mario fermo:
    # il totale delle monete del livello è 0x386B (verificato dal vivo il 12 e il 14/10).
    "moneta": Rule("moneta", "prompt", "Ho raccolto delle monete: adesso ne ho {value}!",
                   "monete del livello (porta 3, totale 0x386B): spenta perché scatta spesso", 1, False, 5.0),
    "tempo": Rule("tempo", "prompt", "Presto! Il tempo del livello sta per finire: restano 15 secondi!",
                  "ultimi 15 secondi del livello (porta 3: 0x1803 = 4, 45 s dopo lo Start)", 2, True, 30.0),
    "fine_livello": Rule("fine_livello", "prompt", "Il livello è finito! {coins}",
                         "fine del livello (porta 3: 0x1803 = 3)", 1, True, 30.0),
}
LEVEL_PHASES = {1: "in corso", 4: "ultimi 15 secondi", 3: "finito", 0: None}  # valori di 0x1803

GESTURE_BITS = {0x8000: "salto", 0x0800: "caduta", 0x0100: "rotazione", 0x0001: "urto", 0x0010: "scossa"}

# Pantaloni -> carattere che Mario assume (va anche nel prompt di sistema)
PANTS_PERSONA = {
    0x00: "Sei senza pantaloni: un po' imbarazzato ma sportivo.",
    0x03: "Sei Mario Ape: operoso, parli di fiori, miele e di volare basso.",
    0x05: "Sei vestito da Luigi: un po' timido e fifone, ma di buon cuore.",
    0x06: "Sei Mario Rana: rilassato, ami l'acqua e i salti lunghi.",
    0x0A: "Sei Mario Tanuki: furbo, ti piace trasformarti in statua.",
    0x0C: "Sei Mario Elica: sognatore, vuoi sempre volare più in alto.",
    0x11: "Sei Mario Gatto: giocoso, curioso e ti arrampichi dappertutto.",
    0x12: "Sei Mario Fuoco: energico, coraggioso e lanci palle di fuoco.",
    0x14: "Sei Mario Pinguino: freddoloso, scivoli sulla pancia e ridi.",
    0x18: "Sei vestito da Peach: gentile, regale e pieno di grazia.",
    0x21: "Sei il Mario di sempre: allegro, coraggioso, ottimista.",
    0x22: "Sei Mario Costruttore: pratico, ami costruire e aggiustare.",
    0x23: "Sei Mario Ghiaccio: calmo, freddo e preciso.",
}

# Basette: come reagire, per famiglia di codice
TILE_START = {0x00B8, 0x00AF, 0x014D}
TILE_GOAL = {0x00B7}
TILE_PRIZE = {0x0021, 0x002B, 0x0080, 0x0029, 0x0063, 0x007B, 0x00F4, 0x0136, 0x0144, 0x008B, 0x00A0, 0x0091}
TILE_FRIEND = {0x0020, 0x003C, 0x009F, 0x006A, 0x000B}
TILE_HINTS = {
    "start": "Si parte: dai il via all'avventura con entusiasmo.",
    "goal": "È il traguardo: festeggia e racconta in una frase com'è andata.",
    "prize": "È un premio: esulta.",
    "friend": "È un amico: salutalo.",
    "enemy": "È un nemico: reagisci con coraggio, o con un po' di paura, come nel gioco.",
}

COLOR_HINTS = {
    0x0015: "Scotta! È lava.",
    0x0017: "Splash, è acqua: si nuota.",
    0x0018: "Sabbia calda del deserto.",
    0x0025: "Erba fresca, si cammina bene.",
    0x010C: "Attento, è veleno!",
    0x0142: "Ghiaccio: si scivola.",
    0x0138: "Mattoni della torre.",
}


def short_name(name: str) -> str:
    """'Goomba (stesso codice: ...)' -> 'Goomba'; 'Fuoco (Fire)' -> 'Fuoco'."""
    return name.split(" (")[0].strip()


def tile_family(code: int) -> str:
    if code in TILE_START:
        return "start"
    if code in TILE_GOAL:
        return "goal"
    if code in TILE_PRIZE:
        return "prize"
    if code in TILE_FRIEND:
        return "friend"
    return "enemy" if code in codes.BARCODES else "unknown"


def event_data(ev: dict) -> bytes:
    """Byte del valore di porta: da `data` (byte o esadecimale) o dal messaggio intero `raw` (dashboard)."""
    data = ev.get("data")
    if isinstance(data, str):
        return bytes.fromhex(data.replace(" ", ""))
    if data is not None:
        return bytes(data)
    raw = ev.get("raw") or ""
    if raw:
        whole = bytes.fromhex(raw)
        return whole[4:]  # [len][hub][0x45][porta] poi i dati
    return b""


class IntentMapper:
    """Stato del corpo di Mario + regole evento -> intento, con tempo minimo tra ripetizioni."""

    def __init__(self, rules: Optional[dict[str, Rule]] = None):
        self.rules: dict[str, Rule] = {k: Rule(**asdict(v)) for k, v in (rules or DEFAULT_RULES).items()}
        self.context: dict = {"pants": None, "pants_name": None, "color": None, "color_name": None,
                              "tile": None, "tile_name": None, "gesture": None, "battery": None,
                              "lifted": None, "level": None, "coins": None, "updated": 0.0}
        # La rotazione scatta di continuo quando Mario è tenuto in mano: vale solo se lo scanner vede
        # qualcosa sotto i piedi (colore o basetta), cioè se Mario è appoggiato.
        self.rotation_only_on_ground = True
        self._last: dict[str, float] = {}
        self._tile_prev: Optional[int] = None
        self._color_prev: Optional[int] = None
        self._pants_prev: Optional[int] = None
        # Pantaloni e colore devono restare uguali per `settle` secondi prima di valere: durante il
        # cambio dei pantaloni i contatti danno codici transitori (visti 0x18 e 0x01 per i pantaloni
        # blu 0x21, 12/10/2026) e il colore oscilla quando Mario viene appoggiato o sollevato.
        self.settle = 0.8
        self._pants_candidate: Optional[tuple[int, float]] = None
        self._color_candidate: Optional[tuple[int, float]] = None
        self._battery_warned = False
        self._coins: Optional[int] = None

    # ----- configurazione -------------------------------------------------------------

    def set_rule(self, name: str, enabled: Optional[bool] = None, text: Optional[str] = None,
                 cooldown: Optional[float] = None) -> Rule:
        rule = self.rules[name]
        if enabled is not None:
            rule.enabled = bool(enabled)
        if text:
            rule.text = text
        if cooldown is not None:
            rule.cooldown = max(0.0, float(cooldown))
        return rule

    def rules_dict(self) -> list[dict]:
        return [asdict(r) for r in self.rules.values()]

    # ----- stato ------------------------------------------------------------------------

    def context_text(self) -> str:
        c = self.context
        parts = []
        if c["pants_name"]:
            parts.append(f"pantaloni: {c['pants_name']}")
            persona = PANTS_PERSONA.get(c["pants"])
            if persona:
                parts.append(persona)
        if c["color_name"]:
            parts.append(f"terreno: {c['color_name']}")
        if c["tile_name"]:
            parts.append(f"ultima basetta: {c['tile_name']}")
        if c["gesture"]:
            parts.append(f"ultimo gesto: {c['gesture']}")
        if c.get("lifted"):
            parts.append("sei sollevato da terra (in mano a qualcuno)")
        if c.get("level"):
            parts.append(f"livello: {c['level']}" + (f", monete {c['coins']}" if c.get("coins") is not None else ""))
        if c["battery"] is not None:
            parts.append(f"batteria: {c['battery']} %")
        return "; ".join(parts) if parts else "nessun dato dai sensori per ora"

    def _fire(self, rule_name: str, ts: float, **fmt) -> Optional[Intent]:
        rule = self.rules.get(rule_name)
        if rule is None or not rule.enabled:
            return None
        if ts - self._last.get(rule_name, 0.0) < rule.cooldown:
            return None
        self._last[rule_name] = ts
        text = rule.text.format(**fmt) if rule.kind == "prompt" else rule.text
        return Intent(rule.kind, rule_name, text.strip(), fmt.get("source", f"regola {rule_name}"), rule.priority, ts)

    # ----- eventi -----------------------------------------------------------------------

    def feed(self, ev: dict) -> list[Intent]:
        ts = float(ev.get("ts") or time.time())
        etype = ev.get("type")
        out: list[Intent] = []
        if etype == "PORT_VALUE_SINGLE":
            io_type, mode = ev.get("io_type"), ev.get("mode")
            data = event_data(ev)
            if io_type == codes.IO_MARIO_ACCELEROMETER and mode == 1 and len(data) >= 2:
                out += self._gesture(le16(data), ts)
            elif io_type == codes.IO_MARIO_PANTS and mode in (None, 0) and data:
                out += self._pants(data[0] & 0x3F, ts, bool(ev.get("settled")))
            elif io_type == codes.IO_MARIO_SCANNER and mode == 0 and len(data) >= 4:
                out += self._scanner(le16(data[0:2]), le16(data[2:4]), ts, bool(ev.get("settled")))
            elif io_type == codes.IO_MARIO_EVENTS and mode == 2 and len(data) >= 4:
                out += self._game_event(le16(data[0:2]), le16(data[2:4]), ts)
        elif etype == "PORT_VALUE_COMBINED":
            # modalità combinata: ogni modo ricomposto vale come un valore singolo
            for mode_text, info in (ev.get("modes") or {}).items():
                sub = {"ts": ts, "type": "PORT_VALUE_SINGLE", "io_type": ev.get("io_type"), "mode": int(mode_text),
                       "data": info.get("data"), "settled": ev.get("settled")}
                out += [i for i in self.feed(sub)]
        elif etype == "HUB_PROPERTIES":
            prop, value = ev.get("prop"), ev.get("value")
            if prop == "BUTTON" and value in (True, 1):
                intent = self._fire("pulsante", ts, source="pulsante premuto")
                out += [intent] if intent else []
            elif prop == "BATTERY_VOLTAGE" and isinstance(value, int):
                self.context["battery"] = value
                if value <= 15 and not self._battery_warned:
                    self._battery_warned = True
                    intent = self._fire("batteria", ts, source=f"batteria {value} %")
                    out += [intent] if intent else []
        out += self.tick(ts)
        if out:
            self.context["updated"] = ts
        return out

    def tick(self, now: Optional[float] = None) -> list[Intent]:
        """Promuove i valori in attesa (pantaloni, colore) rimasti stabili abbastanza a lungo.
        Va chiamata periodicamente anche senza eventi (la sessione lo fa ogni mezzo secondo)."""
        now = now or time.time()
        out: list[Intent] = []
        if self._pants_candidate and now - self._pants_candidate[1] >= self.settle:
            out += self._settle_pants(self._pants_candidate[0], now)
        if self._color_candidate and now - self._color_candidate[1] >= self.settle:
            out += self._settle_color(self._color_candidate[0], now)
        return out

    def _gesture(self, bits: int, ts: float) -> list[Intent]:
        out = []
        names = codes.gesture_names(bits)
        if names:
            self.context["gesture"] = ", ".join(short_name(n) for n in names)
        for bit, name in GESTURE_BITS.items():
            if bits & bit:
                if name == "rotazione" and self.rotation_only_on_ground and self.context.get("lifted"):
                    continue  # girato in mano: non è il gioco della piattaforma rotante
                intent = self._fire(name, ts, source=f"gesto: {name}")
                if intent:
                    out.append(intent)
        return out

    def _pants(self, code: int, ts: float, settled: bool = False) -> list[Intent]:
        if code == self._pants_prev:
            self._pants_candidate = None  # tornato al valore noto: il transitorio è finito
            return []
        if self._pants_prev is None or settled:
            return self._settle_pants(code, ts)  # primo valore (alla sottoscrizione) o simulazione
        if self._pants_candidate is None or self._pants_candidate[0] != code:
            self._pants_candidate = (code, ts)
        return []

    def _settle_pants(self, code: int, ts: float) -> list[Intent]:
        self._pants_candidate = None
        first = self._pants_prev is None
        self._pants_prev = code
        name = short_name(codes.PANTS.get(code, f"pantaloni sconosciuti 0x{code:02X}"))
        self.context.update(pants=code, pants_name=name)
        if first:
            return []  # il primo valore arriva alla sottoscrizione: non è un cambio
        intent = self._fire("pantaloni", ts, name=name, hint=PANTS_PERSONA.get(code, ""), source=f"pantaloni: {name}")
        return [intent] if intent else []

    def _scanner(self, barcode: int, color: int, ts: float, settled: bool = False) -> list[Intent]:
        out = []
        self.context["lifted"] = barcode == 0xFFFF and color == 0xFFFF
        if barcode != 0xFFFF and barcode != self._tile_prev:
            name = short_name(codes.BARCODES.get(barcode, f"basetta sconosciuta 0x{barcode:04X}"))
            self.context.update(tile=barcode, tile_name=name)
            hint = TILE_HINTS.get(tile_family(barcode), "Racconta in una frase cosa succede.")
            intent = self._fire("basetta", ts, name=name, hint=hint, source=f"basetta: {name}")
            if intent:
                out.append(intent)
        self._tile_prev = barcode
        if color != 0xFFFF:
            if color == self._color_prev:
                self._color_candidate = None
            elif self._color_prev is None or settled:
                out += self._settle_color(color, ts)
            elif self._color_candidate is None or self._color_candidate[0] != color:
                self._color_candidate = (color, ts)
        return out

    def _settle_color(self, color: int, ts: float) -> list[Intent]:
        self._color_candidate = None
        first = self._color_prev is None
        self._color_prev = color
        name = short_name(codes.COLORS.get(color, f"colore sconosciuto 0x{color:04X}"))
        self.context.update(color=color, color_name=name)
        if first:
            return []  # il primo colore (alla sottoscrizione) aggiorna solo lo stato
        intent = self._fire("terreno", ts, name=name, hint=COLOR_HINTS.get(color, ""), source=f"terreno: {name}")
        return [intent] if intent else []

    def _game_event(self, code: int, value: int, ts: float) -> list[Intent]:
        intent = None
        if code == 0x386B:  # totale monete del livello, 0 allo Start
            prev, self._coins = self._coins, value
            self.context["coins"] = value
            if prev is not None and value > prev:
                intent = self._fire("moneta", ts, value=value, source=f"monete: {value}")
        elif code == 0x1803 and value in LEVEL_PHASES:  # fase del livello
            self.context["level"] = LEVEL_PHASES[value]
            if value == 4:
                intent = self._fire("tempo", ts, source="livello: ultimi 15 secondi")
            elif value == 3:
                coins = f"Ho raccolto {self._coins} monete." if self._coins else ""
                intent = self._fire("fine_livello", ts, coins=coins, source="livello finito")
            elif value == 0:
                self.context["coins"] = self._coins = None
        return [intent] if intent else []


def event_from_message(dev, msg) -> Optional[dict]:
    """Costruisce, da un `lwp3.Message` ricevuto da `MarioDevice`, lo stesso dizionario che produce
    il worker della dashboard (solo i campi che servono al mapper)."""
    from ..lwp3 import HubPropertyOperation, MessageType
    p = msg.payload
    if msg.msg_type in (MessageType.PORT_VALUE_SINGLE, MessageType.PORT_VALUE_COMBINED) and p:
        return dev.state.event(msg, "")
    if msg.msg_type == MessageType.HUB_PROPERTIES and len(p) >= 2 and p[1] == HubPropertyOperation.UPDATE:
        return dev.state.event(msg, "")
    return None
