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

Le scritture del 12/10 sulla porta 7 venivano ignorate perché mandavano 16 byte invece di uno.
"""
from __future__ import annotations

from typing import Optional

from .lwp3 import le16

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
