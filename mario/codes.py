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

import struct
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

# Porta 2, modo PANT: codice a 6 bit dei pantaloni (fonte: bricklife)
PANTS = {
    0x00: "nessun pantalone",
    0x03: "Ape (Bee)",
    0x05: "Luigi",
    0x06: "Rana (Frog)",
    0x0A: "Tanuki (Tanooki)",
    0x0C: "Elica (Propeller)",
    0x11: "Gatto (Cat)",
    0x12: "Fuoco (Fire)",
    0x14: "Pinguino (Penguin)",
    0x18: "Peach",
    0x21: "Mario normale",
    0x22: "Costruttore (Builder)",
    0x23: "Ghiaccio (Ice)",
}

# Porta 0, modo GEST: campo di bit a 16 bit (fonte: legomario.py)
GESTURES = {
    0x0001: "urto (bump)",
    0x0010: "scossa (shake)",
    0x0100: "rotazione (turning)",
    0x0200: "movimento rapido (fast move)",
    0x0400: "traslazione (translation)",
    0x0800: "caduta / impatto forte (high fall crash)",
    0x1000: "cambio di direzione",
    0x2000: "inversione (reverse)",
    0x8000: "salto (jump)",
}

# Porta 1, modo TAG: primi 2 byte = barcode (little endian), 0xFFFF = nessuno.
# Fonti: pyLegoMario ALL_RGB_CODES.json (131 codici, 60 con nome) + legomario.py; nomi italiani dove certi.
BARCODES = {
    0x0002: 'Goomba (stesso codice: Fly Guy, Foo, Ant Trooper, Ninji, Para-Goomba, Goombrat, Bone Goomba, Bullet Bill, Swoop, Scuttlebug)',
    0x0003: 'Shy Guy / Tipo Timido (secondo legomario.py)',
    0x0004: 'Womp (Whomp)',
    0x000B: 'LEGO NES',
    0x000D: 'Twimp (Thwimp)',
    0x000E: 'Bob-omba',
    0x0014: 'Rotazione (piattaforma rotante: girando Mario conta i giri e dà monete; per legomario.py: Refresh; verificata il 12/10/2026)',
    0x0015: 'Blocco tempo',
    0x001D: 'Bowser',
    0x0020: 'Toadette',
    0x0021: 'Tesoro 1',
    0x0023: 'Fungo velenoso',
    0x0029: 'Blocco ?',
    0x002B: 'Tesoro 2',
    0x002C: 'Ponte scorrevole',
    0x002E: 'Nuvola (zattera di nuvole del set 71360, verificata dal vivo il 12/10/2026)',
    0x0030: 'Nella (Buzzy Beetle) (stesso codice: Para-Beetle, Mechakoopa)',
    0x0031: 'Piattaforma mobile',
    0x0035: 'Fiammetto (Lava Bubble)',
    0x0037: 'Twomp (Thwomp)',
    0x003C: 'Toad',
    0x005B: 'Blocco POW',
    0x005F: 'Interruttore P',
    0x0060: 'Boom Boom',
    0x0063: 'Super Fungo',
    0x006A: 'Castello di Peach',
    0x006B: 'Occhio di pietra',
    0x006E: 'Marghibruco (Pokey)',
    0x006F: 'Piattaforma scorrevole',
    0x0079: 'Pipistrello Swoop (secondo legomario.py)',
    0x007B: 'Super Stella',
    0x007E: 'Pianta Piranha',
    0x0080: 'Tesoro 3',
    0x0081: 'Peeper',
    0x0088: 'Re Boo',
    0x0089: 'Cheep Cheep (stesso codice: Urchin, Spiny Cheep Cheep, Torpedo Ted)',
    0x008B: 'Pinguino piccolo',
    0x0091: 'Chiave inglese',
    0x0092: 'Ampere (Amp)',
    0x0099: 'Bowser Jr.',
    0x009F: 'Yoshi rosa',
    0x00A0: 'Ingranaggio',
    0x00AB: 'Altalena',
    0x00AE: 'Boo',
    0x00AF: 'Start Luigi (tubo di partenza)',
    0x00B7: 'Traguardo (bandiera)',
    0x00B8: 'Start Mario (tubo di partenza)',
    0x00F2: 'Bulbo (Bully)',
    0x00F5: 'Bowser Jr. nel Clown Koopa (letta dal vivo il 12/10/2026: icona del Clown Koopa; pyLegoMario dà Bowser Jr. = 0x99, forse basetta di un altro set)',
    0x00F4: 'Forziere di monete',
    0x012F: 'Dondolo (Swing)',
    0x0136: 'Regalo',
    0x013D: 'Ruboniglio (Nabbit)',
    0x0144: 'Frutto rosso',
    0x014B: 'Lemmy',
    0x014D: 'Start Peach (tubo di partenza)',
    0x0154: 'Martelkoopa (Hammer Bro)',
    0x015B: 'Carrello minerario (set 71425 Corsa nella miniera di Diddy Kong; letto dal vivo il 12/10/2026)',
    0x015E: 'Bowser gigante (The Mighty Bowser)',
    0x0160: 'Aereo (set 71425 Corsa nella miniera di Diddy Kong; dà 6 o 8 monete circa 3 s dopo la lettura; letto dal vivo il 12/10/2026)',
    0x0164: 'Strutzi (Birdo)',
}

# Porta 1, modo TAG: byte 3-4 = colore del terreno (little endian), 0xFFFF = nessuno
COLORS = {
    0x0013: "bianco",
    0x0015: "rosso (lava / fuoco)",
    0x0017: "blu (acqua)",
    0x0018: "giallo (deserto / sabbia)",
    0x001A: "nero",
    0x0025: "verde (erba)",
    0x006A: "marrone",
    0x010C: "viola (veleno)",
    0x0138: "nougat (torre)",
    0x0142: "ciano (ghiaccio)",
}

# Porta 3 modo EVENTS: codici osservati l'11 e il 12/10/2026 (fw 6.5.01). Significato da confermare:
# le descrizioni tra parentesi sono ipotesi basate sulla sequenza osservata. Sequenza di fine livello
# vista il 12/10 (Mario sollevato dopo Start, ~45 s): 0x3004=601, 0x1803=3, 0x3812=2, 0x3852=0,
# 0x386E=0, 0x3803=100; poi (uscita dal livello): 0x1801=0, 0x1803=0, 0x3812=1, 0x3862=0.
GAME_EVENTS = {
    0x3861: "contatore (1..8 in sequenza, probabilmente monete raccolte)",
    0x3862: "evento 0x38/0x62 (valori 0, 2 e 4; 0 quasi a ogni basetta letta o sollevata, 4 alla connessione delle 17:39 del 12/10)",
    0x3857: "evento 0x38/0x57 (valori 0 e 1 osservati)",
    0x3866: "evento 0x38/0x66 (valori 0 e 1 osservati)",
    0x3803: "evento 0x38/0x03 (0 subito dopo alcune basette, 100 alla fine del livello e poco dopo: forse avanzamento di un'animazione)",
    0x200E: "evento 0x20/0x0E (valore 2 osservato)",
    0x1802: "evento 0x18/0x02 (valori 1 e 2 osservati)",
    # sessione dashboard 17:45-17:51
    0x0000: "evento nullo 0x00/0x00",
    0x0113: "basetta letta: il valore è il codice del barcode (visto 184 = 0xB8 Start Mario)",
    0x1801: "evento 0x18/0x01 (valori 0 e 1; 0 all'uscita dal livello, 12/10)",
    0x1803: "evento 0x18/0x03 (12/10: 4 subito dopo Start, 3 a fine livello, 0 fuori dal livello: forse fase di gioco o vite)",
    0x2009: "evento 0x20/0x09 (valore 1 osservato)",
    0x200C: "evento 0x20/0x0C (valori 2 e 4 osservati)",
    0x3004: "evento 0x30/0x04 (sempre 601: visto al tempo scaduto del 12/10 17:33 e 8 s dopo la basetta Clown Koopa alle 18:10 senza fine livello; significato da chiarire)",
    0x3812: "evento 0x38/0x12 (12/10: 2 a fine livello, 1 fuori dal livello: forse stato del gioco)",
    0x3852: "evento 0x38/0x52 (valore 0, nella sequenza di fine livello)",
    0x386B: "totale monete del livello (12/10 18:25: 1 dopo un Goomba, 7 dopo la basetta 0x0160 che ne dà 6, 8 dopo un altro nemico)",
    0x386E: "evento 0x38/0x6E (valore 0, nella sequenza di fine livello)",
    # sessione con le basette reali, 12/10 18:09 (tools/catalog_tiles.py)
    0x2042: "nemici sconfitti nel livello: 1, 2, 3 a ogni basetta con codice Goomba (0x0002)",
    0x3712: "evento 0x37/0x12 = 1 circa 1 s dopo ogni nemico sconfitto (fine dell'animazione dello schiacciamento?)",
    0x20C2: "monete date dalla basetta appena letta (6 o 8 con l'aereo 0x0160 del set 71425, circa 3 s dopo), il totale va in 0x386B",
    0x2022: "contatore legato alla basetta Rotazione 0x0014: 1, 2, 3 mentre Mario viene fatto girare (giri contati?)",
}

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
                parts.append(f"barcode 0x{barcode:04X} = {BARCODES.get(barcode, 'sconosciuto')}")
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

    return None
