"""
Tabelle specifiche di LEGO Mario / Luigi / Peach (hub LWP3 con system type 0x43/0x44/0x45).

Il protocollo di base e' LWP3, ma i sensori di Mario non sono documentati da LEGO.
Queste tabelle vengono dal reverse engineering della community:
  - https://github.com/bricklife/LEGO-Mario-Reveng       (porte, modalita', pantaloni, colori)
  - https://github.com/djipko/legomario.py               (gesti, barcode, volume)
  - https://github.com/nathankellenicki/node-poweredup   (IO type, colori)
I valori non confermati vengono comunque mostrati in esadecimale.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Optional

from .lwp3 import le16

# IO type dei sensori interni di Mario (porta tipica tra parentesi)
IO_VOLTAGE = 0x14             # porta 6: tensione batteria
IO_MARIO_EVENTS = 0x46        # porta 3: CHAL / VERS / EVENTS / DEBUG
IO_MARIO_ACCELEROMETER = 0x47  # porta 0: RAW (accelerometro) / GEST (gesti)
IO_MARIO_SCANNER = 0x49       # porta 1: TAG (barcode + colore) / RGB
IO_MARIO_PANTS = 0x4A         # porta 2: PANT (pantaloni)
IO_MARIO_ALT_EVENTS = 0x55    # porta 4: "Events" (fw 6.5: un solo modo, ingresso e uscita)
IO_MARIO_TELEMETRY = 0x60     # porta 7: "Telemetry", 16 byte, ingresso e uscita (fw 6.5, non documentata)

MARIO_SYSTEM_TYPES = {0x43: "Mario", 0x44: "Luigi", 0x45: "Peach"}

# Le tabelle (pantaloni, gesti, barcode, colori, eventi di gioco) stanno in `mario/data/mario_codes.json`,
# con la fonte (community o "dal vivo") e la data della verifica con il Mario dell'utente. Qui vengono
# caricate come dizionari {codice: nome}; `table_info(kind, code)` restituisce anche fonte e verifica.
#
# Note di lettura dei dati:
# - Porta 2, modo PANT: codice a 6 bit (fonte bricklife). Verificato dal vivo il 12/10/2026 con i pantaloni
#   blu di Mario (0x21): sfilandoli arriva 0x00, infilandoli i contatti danno per un attimo codici transitori
#   (visti 0x18 e 0x01) prima di tornare a 0x21. Chi usa questi valori deve aspettare che restino stabili per
#   quasi un secondo (lo fa `assistant.intents.IntentMapper`).
# - Porta 0, modo GEST: campo di bit a 16 bit (fonte legomario.py). Osservato dal vivo il 12/10/2026
#   (tools/live_checks.py): salto 0x8000 anche sollevando/appoggiando/colpendo Mario; scossa = 0xA111
#   (urto+scossa+rotazione+inversione+salto insieme); girare Mario in piedi = 0x0100 + 0x8000 + bit 0x0002;
#   inclinarlo = 0x0100; cadere da pochi cm = 0x2100; fermo in mano = nessun gesto. Bit 0x0040 visto con
#   l'urto quando Mario viene appoggiato su una basetta (atterraggio?).
# - Porta 1, modo TAG: primi 2 byte = barcode (little endian), 0xFFFF = nessuno; byte 3-4 = colore del
#   terreno, 0xFFFF = nessuno. Barcode: pyLegoMario ALL_RGB_CODES.json (131 codici, 60 con nome) +
#   legomario.py; colori verificati dal vivo il 12/10/2026: bianco, nero, giallo, nougat, verde, rosso, blu.
# - Porta 3 modo EVENTS: codici osservati l'11 e il 12/10/2026 (fw 6.5.01), significato da confermare: le
#   descrizioni tra parentesi sono ipotesi basate sulla sequenza osservata. Sequenza di fine livello vista il
#   12/10 (Mario sollevato dopo Start, ~45 s): 0x3004=601, 0x1803=3, 0x3812=2, 0x3852=0, 0x386E=0, 0x3803=100;
#   poi (uscita dal livello): 0x1801=0, 0x1803=0, 0x3812=1, 0x3862=0.

DATA_FILE = Path(__file__).resolve().parent / "data" / "mario_codes.json"  # distribuito con il pacchetto


def load_raw_tables(path: Path = DATA_FILE) -> dict:
    """Il file dati così com'è: chiavi esadecimali e la descrizione `_descrizione`."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_tables(path: Path = DATA_FILE) -> dict[str, dict[int, dict]]:
    """{tabella: {codice: {"name", "source", "verified", ...}}} dal file dati."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {kind: {int(code, 16): dict(info) for code, info in entries.items()}
            for kind, entries in raw.items() if isinstance(entries, dict)}


TABLES = load_tables()
PANTS: dict[int, str] = {c: i["name"] for c, i in TABLES["pants"].items()}
GESTURES: dict[int, str] = {c: i["name"] for c, i in TABLES["gestures"].items()}
BARCODES: dict[int, str] = {c: i["name"] for c, i in TABLES["barcodes"].items()}
COLORS: dict[int, str] = {c: i["name"] for c, i in TABLES["colors"].items()}
GAME_EVENTS: dict[int, str] = {c: i["name"] for c, i in TABLES["game_events"].items()}
# codici che pyLegoMario conosce senza nome: a parte, così non diventano basette "nemico"
BARCODES_UNNAMED: dict[int, dict] = TABLES.get("barcodes_unnamed", {})


def barcode_label(code: int) -> str:
    """Nome della basetta per le descrizioni: tabella, codice noto senza nome, o sconosciuto."""
    if code in BARCODES:
        return BARCODES[code]
    if code in BARCODES_UNNAMED:
        return f"senza nome (in pyLegoMario, strisce {BARCODES_UNNAMED[code].get('stripes', '?')})"
    return "sconosciuto"


def table_info(kind: str, code: int) -> Optional[dict]:
    """Voce completa di una tabella ('pants', 'gestures', 'barcodes', 'colors', 'game_events'): nome, fonte, verifica."""
    return TABLES.get(kind, {}).get(code)


def verified_codes(kind: str) -> dict[int, str]:
    """{codice: data} delle voci confermate con il Mario dell'utente."""
    return {c: i["verified"] for c, i in TABLES.get(kind, {}).items() if i.get("verified")}


# Modalita' da attivare di default con `monitor`: (IO type, nomi di modo accettati, modo di ripiego).
# I numeri di modo cambiano tra firmware (es. porta 4), i nomi no: si sceglie per nome.
DEFAULT_SUBSCRIPTIONS = [
    (IO_MARIO_ACCELEROMETER, ("GEST",), 1),
    (IO_MARIO_SCANNER, ("TAG",), 0),
    (IO_MARIO_PANTS, ("PANT",), 0),
    (IO_MARIO_EVENTS, ("EVENTS",), 2),
    (IO_MARIO_ALT_EVENTS, ("Events", "EVENTS"), 0),
    (IO_VOLTAGE, ("VLT L",), 0),
]

# Delta minimo per le notifiche di default (la batteria altrimenti manda un valore ogni millivolt)
DEFAULT_DELTAS = {IO_VOLTAGE: 25}


def default_mode(io_type: int, mode_names: dict, prefer: tuple = ()) -> int | None:
    """Modo da attivare per un IO type, dato {numero: nome} dei modi scoperti sulla porta.
    `prefer` permette di forzare nomi diversi (es. ("RAW",) per l'accelerometro grezzo)."""
    for wanted_io, names, fallback in DEFAULT_SUBSCRIPTIONS:
        if wanted_io != io_type:
            continue
        for name in tuple(prefer) + tuple(names):
            for number, found in mode_names.items():
                if found.lower() == name.lower():
                    return number
        return fallback if fallback in mode_names or not mode_names else None
    return None


PORT_HINTS = {
    IO_MARIO_ACCELEROMETER: "modo 0 RAW = accelerometro x/y/z (3 x int8); modo 1 GEST = gesti (bitfield 16 bit, tabella GESTURES); combinabili",
    IO_MARIO_SCANNER: "modo 0 TAG = [barcode LE16][colore LE16], 0xFFFF = niente; modo 1 RGB = r,g,b grezzi; combinabili",
    IO_MARIO_PANTS: "modo 0 PANT = codice pantaloni a 6 bit (tabella PANTS)",
    IO_MARIO_EVENTS: "modo 0 CHAL, 1 VERS (4 x int32), 2 EVENTS (2 x int16: eventi di gioco), 3 DEBUG (4 x int32)",
    IO_MARIO_ALT_EVENTS: "modo 'Events' (2 x int16) in ingresso E uscita: accetta Port Output 0x81 solo con WriteDirectModeData (0x51), "
                         "feedback 'completato' (provato 12/10/2026, nessun eco); 0x50 INVALID_USE, 0x00 non riconosciuto; significato ignoto",
    IO_MARIO_TELEMETRY: "modo 'Telemetry' (16 x int8) in ingresso E uscita, presente dal firmware 6.x; significato non documentato. "
                        "Provato il 12/10/2026: 37 scritture WriteDirectModeData senza feedback, senza errore e senza effetto (rilettura sempre zero)",
    IO_VOLTAGE: "tensione batteria: raw 0..2705 corrisponde a 0..3300 mV (modi VLT L e VLT S)",
}


def port_hint(io_type: int) -> Optional[str]:
    return PORT_HINTS.get(io_type)


def printable_runs(data: bytes, minimum: int = 5) -> list[str]:
    """Pezzi di testo ASCII leggibile (almeno `minimum` caratteri di fila) dentro dati binari."""
    return [m.group().decode("ascii") for m in re.finditer(rb"[ -~]{%d,}" % minimum, bytes(data))]


def gesture_names(value: int) -> list[str]:
    names = [name for bit, name in GESTURES.items() if value & bit]
    unknown = value & ~sum(GESTURES)
    if unknown:
        names.append(f"bit sconosciuti 0x{unknown:04X}")
    return names


def decode_value(io_type: int, mode: Optional[int], data: bytes, values: list) -> Optional[str]:
    """Interpretazione 'umana' di un Port Value di Mario. None se non sappiamo decodificarlo."""
    data = bytes(data)
    if io_type == IO_MARIO_PANTS and mode in (None, 0) and data:
        code = data[0] & 0x3F
        return f"pantaloni: {PANTS.get(code, f'sconosciuti 0x{code:02X}')} (0b{code:06b})"

    if io_type == IO_MARIO_ACCELEROMETER:
        if mode == 0 and len(data) >= 3:
            x, y, z = struct.unpack("<bbb", data[:3])
            return f"accelerazione x={x} y={y} z={z}"
        if mode == 1 and len(data) >= 2:
            g = le16(data)
            return f"gesto: {', '.join(gesture_names(g)) or 'nessuno'} (0x{g:04X})"

    if io_type == IO_MARIO_SCANNER:
        if mode == 0 and len(data) >= 4:
            barcode, color = le16(data[0:2]), le16(data[2:4])
            parts = []
            if barcode != 0xFFFF:
                parts.append(f"barcode 0x{barcode:04X} = {barcode_label(barcode)}")
            if color != 0xFFFF:
                parts.append(f"colore 0x{color:04X} = {COLORS.get(color, 'sconosciuto')}")
            return "; ".join(parts) or "nessun barcode/colore (sensore sollevato)"
        if mode == 1 and len(data) >= 3:
            return f"RGB r={data[0]} g={data[1]} b={data[2]}"

    if io_type == IO_VOLTAGE and len(data) >= 2:
        raw = le16(data)
        return f"batteria circa {raw * 3300 / 2705:.0f} mV (raw {raw})"

    if io_type == IO_MARIO_TELEMETRY:
        return f"telemetria (grezza) {data.hex(' ')}"

    if io_type == IO_MARIO_EVENTS and mode == 2 and len(data) >= 4:
        # Osservato dal vivo (fw 6.5): 2 x int16 LE = [codice evento][valore].
        # Il codice si legge meglio come due byte: data[1] = classe, data[0] = evento.
        code, value = le16(data[0:2]), le16(data[2:4])
        name = GAME_EVENTS.get(code)
        label = f" = {name}" if name else ""
        return f"evento classe 0x{data[1]:02X} codice 0x{data[0]:02X} (0x{code:04X}{label}) valore {value}"

    if io_type == IO_MARIO_EVENTS and mode == 3:
        # Dal vivo il 14/10/2026: in modo DEBUG Mario trasmette una traccia del firmware come flusso continuo a
        # pezzi di 16 byte; record e testo attraversano i confini dei pacchetti (decode --summary li ricompone).
        text = printable_runs(data)
        return "traccia DEBUG (pezzo di un flusso)" + (f", testo: {' | '.join(text)}" if text else "")

    if io_type == IO_MARIO_EVENTS and mode in (0, 1):
        seen = "c0 00 00 00" if mode == 0 else "e2 00 01 b5 ×3, 32 00 32 00"
        return f"modo {'CHAL' if mode == 0 else 'VERS'}, significato ignoto (il 14/10/2026 valeva {seen})"

    return None
