"""
Archivio delle partite di LEGO Mario sulla porta 7 ("Telemetry", firmware 6.5).

Visto nella cattura del traffico dell'app ufficiale del 18/10/2026 (log HCI snoop del telefono): appena collegata,
l'app scrive un solo byte `01` sulla porta 7 (WriteDirectModeData, modo 0) e Mario risponde con una raffica di
record da 16 byte, uno per notifica; il primo byte è il tipo. Dopo la raffica l'app scrive `02` (ipotesi:
"archivio ricevuto, si può cancellare"; non va mandato senza volerlo).

  01  partita   [01][record della partita, questo compreso][numero LE32][grezzo A LE16][grezzo B LE16]
                [01][3 byte ignoti][pantaloni][01]. La somma dei record di tutte le partite fa il totale
                del record finale (61 su 61 nella cattura). A e B (circa 2200) somigliano ai valori grezzi
                della tensione della porta 6: forse la batteria all'inizio e alla fine.
  02  basette   fino a 5 x ([codice del barcode LE16][volte]); erano proprio le basette dell'utente.
  03, 05        contatori, fino a 5 x ([numero][valore LE16]); significato ancora ignoto.
  ff  fine      12 x ff, [record totali LE16][partite LE16].

Il feedback 0x82 della richiesta porta due byte in più: quanti record seguiranno, LE16 (61 il 18/10 con l'app,
19 dopo la cancellazione), esclusa la fine. Le scritture del 12/10 sulla porta 7 venivano ignorate perché
mandavano 16 byte invece di uno.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from . import lwp3
from .lwp3 import MessageType, le16

SESSION, TILES, COUNTERS_A, COUNTERS_B, END = 0x01, 0x02, 0x03, 0x05, 0xFF
RECORD_NAMES = {SESSION: "partita", TILES: "basette", COUNTERS_A: "contatori (tipo 3)",
                COUNTERS_B: "contatori (tipo 5)", END: "fine dell'archivio"}
REQUEST = b"\x01"    # dati della scrittura che chiede l'archivio (osservata dall'app)
RELEASE = b"\x02"    # dati che l'app scrive dopo lo scarico (probabile cancellazione)


def _triples(body: bytes, wide_key: bool) -> list[tuple[int, int]]:
    """5 voci da 3 byte: (codice LE16, volte) per le basette, (numero, valore LE16) per i contatori.
    Le voci finali tutte a zero sono riempimento."""
    items = []
    for i in range(0, len(body) - 2, 3):
        chunk = body[i:i + 3]
        items.append((le16(chunk[0:2]), chunk[2]) if wide_key else (chunk[0], le16(chunk[1:3])))
    while items and items[-1] == (0, 0):
        items.pop()
    return items


def parse(data: bytes) -> Optional[dict]:
    """Un record della porta 7 -> dizionario, o None se non è un record dell'archivio (meno di 16 byte)."""
    data = bytes(data)
    if len(data) < 16:
        return None
    kind = data[0]
    if kind == SESSION:
        return {"kind": "session", "records": data[1], "number": int.from_bytes(data[2:6], "little"),
                "raw_a": le16(data[6:8]), "raw_b": le16(data[8:10]), "flag": data[10],
                "unknown": data[11:14].hex(" "), "pants": data[14], "last": data[15]}
    if kind == TILES:
        return {"kind": "tiles", "tiles": _triples(data[1:16], wide_key=True)}
    if kind in (COUNTERS_A, COUNTERS_B):
        return {"kind": "counters", "type": kind, "counters": _triples(data[1:16], wide_key=False)}
    if kind == END and data[1:12] == b"\xff" * 11:
        return {"kind": "end", "records": le16(data[12:14]), "sessions": le16(data[14:16])}
    return {"kind": "unknown", "type": kind}


def _tile(code: int) -> str:
    """Nome breve della basetta (la tabella aggiunge note tra parentesi)."""
    from .codes import barcode_label
    return barcode_label(code).split(" (")[0]


def _millivolts(raw: int) -> int:
    return round(raw * 3300 / 2705)  # stessa scala della porta 6 (ipotesi)


def describe(data: bytes) -> Optional[str]:
    """Descrizione di una riga per il monitor e per decode."""
    from .codes import PANTS
    rec = parse(data)
    if rec is None:
        return None
    kind = rec["kind"]
    if kind == "session":
        pants = PANTS.get(rec["pants"] & 0x3F, f"0x{rec['pants']:02X}")
        return (f"archivio: partita n. {rec['number']} ({rec['records']} record), grezzi {rec['raw_a']}/{rec['raw_b']} "
                f"(batteria? circa {_millivolts(rec['raw_a'])}/{_millivolts(rec['raw_b'])} mV), pantaloni {pants}")
    if kind == "tiles":
        return "archivio: basette " + ", ".join(f"{_tile(code)} ×{n}" for code, n in rec["tiles"])
    if kind == "counters":
        return (f"archivio: contatori tipo {rec['type']} "
                + ", ".join(f"#{key}={value}" for key, value in rec["counters"]))
    if kind == "end":
        return f"archivio: fine, {rec['records']} record in {rec['sessions']} {'partita' if rec['sessions'] == 1 else 'partite'}"
    return f"archivio: record di tipo 0x{rec['type']:02X} sconosciuto"


class Collector:
    """Raccoglie i record di uno scarico dell'archivio e li raggruppa per partita."""

    def __init__(self):
        self.sessions: list[dict] = []
        self.end: Optional[dict] = None
        self.unknown = 0

    def feed(self, data: bytes) -> Optional[dict]:
        rec = parse(data)
        if rec is None:
            return None
        if rec["kind"] == "session":
            self.sessions.append({**rec, "tiles": {}, "counters": {COUNTERS_A: {}, COUNTERS_B: {}}, "seen": 1})
        elif rec["kind"] == "end":
            self.end = rec
        elif rec["kind"] in ("tiles", "counters") and self.sessions:
            current = self.sessions[-1]
            current["seen"] += 1
            if rec["kind"] == "tiles":
                for code, n in rec["tiles"]:
                    current["tiles"][code] = current["tiles"].get(code, 0) + n
            else:
                current["counters"][rec["type"]].update(dict(rec["counters"]))
        else:
            self.unknown += 1
        return rec

    @property
    def complete(self) -> bool:
        return self.end is not None

    def problems(self) -> list[str]:
        out = []
        if self.end is None:
            out.append("manca il record finale: scarico interrotto?")
            return out
        if self.end["sessions"] != len(self.sessions):
            out.append(f"partite: {len(self.sessions)} ricevute, {self.end['sessions']} annunciate")
        declared = sum(s["records"] for s in self.sessions)
        if declared != self.end["records"]:
            out.append(f"record: {declared} dichiarati dalle partite, {self.end['records']} nel record finale")
        for s in self.sessions:
            if s["seen"] != s["records"]:
                out.append(f"partita n. {s['number']}: {s['seen']} record ricevuti su {s['records']}")
        if self.unknown:
            out.append(f"{self.unknown} record di tipo sconosciuto o fuori posto")
        return out

    def summary(self) -> list[str]:
        from .codes import PANTS
        if not self.sessions and self.end is None:
            return []
        n = len(self.sessions)
        lines = [f"archivio delle partite (porta 7): {n} {'partita' if n == 1 else 'partite'}"
                 + (f", {self.end['records']} record" if self.end else "")]
        for s in self.sessions:
            pants = PANTS.get(s["pants"] & 0x3F, f"0x{s['pants']:02X}")
            lines.append(f"  partita n. {s['number']}: {s['records']} record, grezzi {s['raw_a']}/{s['raw_b']}, pantaloni {pants}")
            if s["tiles"]:
                lines.append("    basette: " + ", ".join(f"{_tile(c)} ×{n}" for c, n in sorted(s["tiles"].items())))
            for kind in (COUNTERS_A, COUNTERS_B):
                if s["counters"][kind]:
                    lines.append(f"    contatori tipo {kind}: "
                                 + " ".join(f"#{k}={v}" for k, v in sorted(s["counters"][kind].items())))
        lines += [f"  attenzione: {p}" for p in self.problems()]
        return lines


# ----- richieste a Mario, come le fa l'app ufficiale (solo quelle di lettura) -------------------------------

SNAPSHOT_PORT, CHANNEL_PORT, ARCHIVE_PORT = 3, 4, 7
READ_REQUESTS = ((0x05, 0x00FE), (0x09, 0x0000))  # porta 4: le due richieste che l'app fa prima di scrivere


def snapshot_frame() -> bytes:
    """Porta 3, modo 2, dati 01 f0: Mario risponde con un'istantanea di contatori (eventi della porta 3)."""
    return lwp3.port_output_command(SNAPSHOT_PORT, 0x51, bytes([2, 0x01, 0xF0]), 0x1, 0x1)


def channel_frame(command: int, value: int) -> bytes:
    """Porta 4, modo 0: [comando LE16][valore LE16]."""
    return lwp3.port_output_command(CHANNEL_PORT, 0x51, bytes([0]) + command.to_bytes(2, "little")
                                    + value.to_bytes(2, "little"), 0x1, 0x1)


def archive_frame() -> bytes:
    """Porta 7, modo 0, un byte 01, con i flag usati dall'app (0x01: in coda, con feedback)."""
    return lwp3.port_output_command(ARCHIVE_PORT, 0x51, bytes([0]) + REQUEST, 0x0, 0x1)


async def fetch(dev, snapshot: bool = True, timeout: float = 10.0,
                log: Optional[Callable[[str], None]] = None, reply_timeout: float = 2.0) -> dict:
    """Ripete le richieste di lettura dell'app ufficiale e raccoglie le risposte: istantanea della porta 3,
    risposte della porta 4, archivio della porta 7. Non manda mai 02 sulla porta 7 né la richiesta 0x0A sulla
    porta 4, che modificano dati salvati in Mario. `dev` è un MarioDevice (o un oggetto con subscribe, send,
    add_listener e remove_listener)."""
    log = log or (lambda text: None)
    result = {"snapshot": [], "port4": [], "records": [], "collector": Collector(), "errors": []}
    collector: Collector = result["collector"]
    replies: asyncio.Queue = asyncio.Queue()
    state = {"snapshot": False}
    done = asyncio.Event()

    def on_message(msg, _text: str) -> None:
        p = msg.payload
        if msg.msg_type == MessageType.GENERIC_ERROR:
            result["errors"].append(p.hex(" "))
        if msg.msg_type == MessageType.PORT_OUTPUT_COMMAND_FEEDBACK and len(p) == 4 and p[0] == ARCHIVE_PORT:
            # il feedback della richiesta dell'archivio porta anche quanti record seguiranno (61 e 19 il 18/10)
            result["announced"] = le16(p[2:4])
        if msg.msg_type != MessageType.PORT_VALUE_SINGLE or not p:
            return
        port, data = p[0], bytes(p[1:])
        if port == SNAPSHOT_PORT and state["snapshot"] and len(data) >= 4:
            result["snapshot"].append((le16(data[0:2]), le16(data[2:4])))
        elif port == CHANNEL_PORT and len(data) >= 4:
            replies.put_nowait((le16(data[0:2]), le16(data[2:4])))
        elif port == ARCHIVE_PORT:
            result["records"].append(data.hex(" "))
            collector.feed(data)
            if collector.complete:
                done.set()

    dev.add_listener(on_message)
    try:
        for port, mode in ((SNAPSHOT_PORT, 2), (CHANNEL_PORT, 0), (ARCHIVE_PORT, 0)):
            await dev.subscribe(port, mode, 1)
        if snapshot:
            state["snapshot"] = True
            log("porta 3: richiesta dell'istantanea (01 f0)")
            await dev.send(snapshot_frame())
            await asyncio.sleep(1.0)
            state["snapshot"] = False
        for command, value in READ_REQUESTS:
            log(f"porta 4: richiesta 0x{command:02X} con 0x{value:04X}")
            await dev.send(channel_frame(command, value))
            try:
                reply = await asyncio.wait_for(replies.get(), reply_timeout)
            except asyncio.TimeoutError:
                reply = None
            result["port4"].append({"request": command, "value": value,
                                    "reply": None if reply is None else reply[0],
                                    "reply_value": None if reply is None else reply[1]})
        log("porta 7: richiesta dell'archivio delle partite (01)")
        started = time.monotonic()
        await dev.send(archive_frame())
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        result["seconds"] = round(time.monotonic() - started, 2)
    finally:
        dev.remove_listener(on_message)
    return result


def fetch_report(result: dict) -> list[str]:
    """Righe leggibili del risultato di fetch()."""
    from .codes import GAME_EVENTS, PORT4_REPLIES
    lines = []
    if result["snapshot"]:
        lines.append("istantanea della porta 3: " + ", ".join(f"0x{c:04X}={v}" for c, v in result["snapshot"]))
        unnamed = [c for c, _ in result["snapshot"] if c not in GAME_EVENTS]
        if unnamed:
            lines.append("  codici non in tabella: " + ", ".join(f"0x{c:04X}" for c in unnamed))
    for item in result["port4"]:
        if item["reply"] is None:
            lines.append(f"porta 4: richiesta 0x{item['request']:02X} senza risposta")
        else:
            meaning = PORT4_REPLIES.get(item["reply"], "")
            lines.append(f"porta 4: richiesta 0x{item['request']:02X} -> risposta 0x{item['reply']:02X} "
                         f"valore 0x{item['reply_value']:04X}" + (f" ({meaning})" if meaning else ""))
    collector: Collector = result["collector"]
    if result["records"]:
        announced = result.get("announced")
        lines.append(f"porta 7: {len(result['records'])} record in {result.get('seconds', 0):g} s"
                     + (f" (Mario ne aveva annunciati {announced} più quello finale)" if announced is not None else ""))
        lines += collector.summary()
    else:
        lines.append("porta 7: nessun record (archivio vuoto o richiesta ignorata)")
    if result["errors"]:
        lines.append("errori LWP3: " + "; ".join(result["errors"]))
    return lines


def fetch_json(result: dict) -> dict:
    """Il risultato di fetch() in forma serializzabile (senza il Collector)."""
    collector: Collector = result["collector"]
    sessions = [{**{k: v for k, v in s.items() if k not in ("tiles", "counters")},
                 "tiles": {f"0x{c:04X}": n for c, n in s["tiles"].items()},
                 "counters": {str(kind): {str(k): v for k, v in values.items()} for kind, values in s["counters"].items()}}
                for s in collector.sessions]
    return {"snapshot": [{"code": f"0x{c:04X}", "value": v} for c, v in result["snapshot"]],
            "port4": result["port4"], "records": result["records"], "sessions": sessions, "end": collector.end,
            "problems": collector.problems() if result["records"] else [], "errors": result["errors"],
            "announced": result.get("announced"),
            "seconds": result.get("seconds")}
