"""
Strumento `decode`: rilegge il traffico LWP3 da file e lo decodifica con lo stato delle porte.

Sorgenti accettate:
  - catture btsnoop (`btmon -w file`, HCI snoop log di Android): formato "Linux monitor" (2001),
    H4/UART (1002) e HCI puro (1001); vengono estratti gli ATT Write (PC -> Mario) e le
    Handle Value Notification (Mario -> PC) sulla caratteristica LEGO, con riassemblaggio L2CAP;
  - bugreport di Android (`adb bugreport`): lo zip con dentro `FS/data/misc/bluetooth/logs/btsnoop_hci.log`
    (o il percorso Samsung `/data/log/bt/`), oppure il solo `bugreport-*.txt` con le sezioni
    btsnooz (base64 + zlib, versioni 1 e 2) che Android scrive anche senza HCI snoop log attivo:
    in quel caso i dati ACL sono troncati e lo strumento lo segnala;
  - catture PacketLogger di Apple (`.pklg`, da iOS con il profilo "Bluetooth logging" o da macOS),
    big o little endian; file compressi gzip;
  - log di testo di `monitor`, `shell`, `assistant` e degli strumenti (`[... ]` con i byte grezzi
    in coda alla riga), l'uscita di `btmon -r` e le righe `TX ..`/`RX ..` dell'opzione -d;
  - righe o argomenti esadecimali.

Lo stato iniziale delle porte (IO type, modi, formati) viene da `docs/capabilities-fw-*.json`,
poi evolve con i messaggi letti (sottoscrizioni, modalità combinata), come dal vivo.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import re
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from . import lwp3
from .hubstate import HubState
from .lwp3 import Message, MessageType

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CAPS = PROJECT_DIR / "docs" / "capabilities-fw-6.5.01.json"

ATT_NOTIFY, ATT_INDICATE, ATT_WRITE_REQ, ATT_WRITE_CMD, ATT_READ_RSP = 0x1B, 0x1D, 0x12, 0x52, 0x0B
MONITOR_ACL_TX, MONITOR_ACL_RX = 4, 5
BTSNOOP_MAGIC, ZIP_MAGIC, GZIP_MAGIC = b"btsnoop\x00", b"PK\x03\x04", b"\x1f\x8b"
BTSNOOZ_BEGIN, BTSNOOZ_END = "--- BEGIN:BTSNOOP_LOG_SUMMARY", "--- END:BTSNOOP_LOG_SUMMARY"
BTSNOOZ_EPOCH_US = 0x00DCDDB30F2F8000          # microsecondi tra l'anno 0 (epoca btsnoop) e il 1970
BTSNOOZ_IN = {0x10, 0x11, 0x12, 0x17}           # evento, ACL, SCO, ISO ricevuti
BTSNOOZ_H4 = {0x20: 0x01, 0x11: 0x02, 0x21: 0x02, 0x12: 0x03, 0x22: 0x03, 0x10: 0x04, 0x17: 0x05, 0x2D: 0x05}
PKLG_TYPES = {0x00, 0x01, 0x02, 0x03, 0x08, 0x09, 0x0A, 0x0B, 0xF7, 0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE}
PKLG_SENT_ACL, PKLG_RECV_ACL = 0x02, 0x03

_BRACKET_HEX = re.compile(r"\[((?:[0-9a-fA-F]{2}[ :]?){3,})\]\s*$")
_TXRX = re.compile(r"\b(TX|RX)\s+((?:[0-9a-fA-F]{2}\s?){3,})\s*$")
_BTMON_DATA = re.compile(r"^\s*Data(?: \(\d+ bytes\))?:\s*([0-9a-fA-F]+)\s*$")
_TIME = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]")


@dataclass
class Frame:
    """Un messaggio LWP3 con la sua origine."""
    seq: int
    direction: str          # "<-" da Mario, "->" verso Mario, "?" ignota
    msg: Message
    when: str = ""          # orario (log di testo) o secondi dall'inizio (btsnoop)
    source: str = ""        # file di provenienza


@dataclass
class Decoder:
    """Decodifica una sequenza di frame tenendo lo stato delle porte."""
    state: HubState = field(default_factory=HubState)

    @classmethod
    def with_capabilities(cls, path: Optional[Path | str] = None, assume_default_modes: bool = True) -> "Decoder":
        """Stato iniziale dal JSON delle funzioni. I log degli strumenti non contengono la conferma delle
        sottoscrizioni, quindi (se richiesto) ogni porta parte nel modo che `monitor` attiva di default."""
        file = Path(path) if path else DEFAULT_CAPS
        if not file.is_file():
            return cls()
        state = HubState.from_capabilities(json.loads(file.read_text(encoding="utf-8")))
        if assume_default_modes:
            from . import codes
            for ps in state.ports.values():
                if ps.current_mode is None:
                    ps.current_mode = codes.default_mode(ps.io_type, {m: mi.name for m, mi in ps.modes.items()})
        return cls(state)

    def describe(self, frame: Frame) -> str:
        if frame.direction == "->":
            self.state.note_sent(frame.msg.raw)
            return lwp3.describe_downstream(frame.msg)
        self.state.update(frame.msg)
        return self.state.describe(frame.msg)

    def record(self, frame: Frame) -> dict:
        text = self.describe(frame)
        out = {"seq": frame.seq, "when": frame.when, "direction": frame.direction, "type": frame.msg.type_name,
               "raw": frame.msg.raw.hex(" "), "text": text}
        if frame.direction != "->":
            ev = self.state.event(frame.msg, text)
            for key in ("port", "mode", "io_type", "values", "decoded", "modes", "prop", "value"):
                if key in ev:
                    out[key] = ev[key]
        if frame.source:
            out["source"] = frame.source
        return out


# ----- btsnoop ------------------------------------------------------------------------------

def _split_messages(data: bytes, direction: str, when: str, source: str, counter: list[int]) -> Iterator[Frame]:
    for msg in lwp3.parse_all(data):
        counter[0] += 1
        yield Frame(counter[0], direction, msg, when, source)


class _L2capReassembler:
    def __init__(self) -> None:
        self.partial: dict[int, bytearray] = {}
        self.expected: dict[int, int] = {}

    def feed(self, acl: bytes) -> Optional[tuple[int, bytes]]:
        """ACL HCI (handle+flags, len, payload) -> (cid, pacchetto L2CAP completo) oppure None."""
        if len(acl) < 4:
            return None
        handle_flags = struct.unpack("<H", acl[0:2])[0]
        handle, pb = handle_flags & 0x0FFF, (handle_flags >> 12) & 0x3
        payload = acl[4:]
        if pb == 0b01:  # continuazione
            buf = self.partial.get(handle)
            if buf is None:
                return None
            buf += payload
        else:
            if len(payload) < 4:
                return None
            self.expected[handle] = struct.unpack("<H", payload[0:2])[0] + 4
            self.partial[handle] = bytearray(payload)
        buf = self.partial[handle]
        if len(buf) >= self.expected.get(handle, 1 << 30):
            del self.partial[handle]
            cid = struct.unpack("<H", buf[2:4])[0]
            return cid, bytes(buf[4:self.expected.pop(handle)])
        return None


def _acl_frames(packets: Iterator[tuple[bytes, str, str]], source: str,
                notes: Optional[list[str]] = None) -> Iterator[Frame]:
    """(ACL HCI, direzione, orario) -> messaggi LWP3 degli ATT sulla caratteristica LEGO, con riassemblaggio
    L2CAP. I pacchetti ACL più corti di quanto dichiara la loro intestazione (btsnooz, snaplen) vengono
    contati e scartati: la nota finale spiega perché la cattura è vuota."""
    counter, reasm, truncated = [0], _L2capReassembler(), 0
    for acl, direction, when in packets:
        if len(acl) < 4:
            continue
        if struct.unpack("<H", acl[2:4])[0] > len(acl) - 4:
            truncated += 1
            continue
        done = reasm.feed(acl)
        if done is None:
            continue
        cid, l2 = done
        if cid != 0x0004 or not l2:  # solo ATT
            continue
        op = l2[0]
        if op in (ATT_NOTIFY, ATT_INDICATE) and len(l2) > 3:
            yield from _split_messages(l2[3:], "<-", when, source, counter)
        elif op in (ATT_WRITE_REQ, ATT_WRITE_CMD) and len(l2) > 3:
            yield from _split_messages(l2[3:], "->", when, source, counter)
        elif op == ATT_READ_RSP and len(l2) > 1:
            yield from _split_messages(l2[1:], "<-", when, source, counter)
    if truncated and notes is not None:
        notes.append(f"{source}: {truncated} pacchetti ACL troncati (log 'riassunto' btsnooz o snaplen): "
                     "per vedere i messaggi serve l'HCI snoop log completo del telefono")


def _btsnoop_packets(data: bytes) -> Iterator[tuple[bytes, str, str]]:
    _version, datalink = struct.unpack(">II", data[8:16])
    pos, t0 = 16, None
    while pos + 24 <= len(data):
        _orig, incl, flags, _drops, ts = struct.unpack(">IIIIq", data[pos:pos + 24])
        pkt = data[pos + 24:pos + 24 + incl]
        pos += 24 + incl
        if t0 is None:
            t0 = ts
        when = f"{(ts - t0) / 1e6:.3f}s"
        acl, direction = None, "?"
        if datalink == 2001:  # Linux monitor (btmon)
            opcode = flags & 0xFFFF
            if opcode == MONITOR_ACL_TX:
                acl, direction = pkt, "->"
            elif opcode == MONITOR_ACL_RX:
                acl, direction = pkt, "<-"
        elif datalink == 1002:  # H4: primo byte = tipo pacchetto
            if pkt and pkt[0] == 0x02:
                acl, direction = pkt[1:], "<-" if flags & 1 else "->"
        elif datalink == 1001:  # HCI puro: bit 1 dei flag = comando/evento, altrimenti dati
            if not flags & 2:
                acl, direction = pkt, "<-" if flags & 1 else "->"
        if acl is not None:
            yield acl, direction, when


def read_btsnoop_bytes(data: bytes, source: str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    if not data.startswith(BTSNOOP_MAGIC):
        raise ValueError("non è un file btsnoop (manca l'intestazione)")
    return _acl_frames(_btsnoop_packets(data), source, notes)


def read_btsnoop(path: Path | str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    return read_btsnoop_bytes(Path(path).read_bytes(), Path(path).name, notes)


# ----- bugreport Android: sezioni btsnooz, zip ------------------------------------------

def decode_btsnooz(blob: bytes) -> bytes:
    """Sezione btsnooz di un bugreport (già decodificata dal base64) -> file btsnoop H4 (datalink 1002).

    Formato AOSP (tools/scripts/btsnooz.py): [versione 1 byte][timestamp dell'ultimo pacchetto, 8 byte]
    + zlib di record `[len LE16][len originale LE16 (solo v2)][delta µs LE32][tipo 1 byte][dati]`, dove
    `len` conta anche il byte del tipo e il tipo distingue ingresso (0x1x) e uscita (0x2x)."""
    if len(blob) < 9:
        raise ValueError("sezione btsnooz troppo corta")
    version, last_ts = struct.unpack_from("<bQ", blob)
    if version not in (1, 2):
        raise ValueError(f"versione btsnooz {version} non supportata")
    body = zlib.decompress(blob[9:])
    head = "<HIb" if version == 1 else "<HHIb"
    size = struct.calcsize(head)
    records: list[tuple[int, int, int, bytes]] = []
    offset = 0
    while offset + size <= len(body):
        fields = struct.unpack_from(head, body, offset)
        length, delta, kind = fields[0], fields[-2], fields[-1] & 0xFF
        orig = fields[1] if version == 2 else length
        offset += size
        payload = body[offset:offset + max(length - 1, 0)]
        offset += max(length - 1, 0)
        records.append((kind, delta, orig, payload))
    ts = last_ts + BTSNOOZ_EPOCH_US - sum(r[1] for r in records)  # i record portano solo il delta dal precedente
    out = bytearray(BTSNOOP_MAGIC + struct.pack(">II", 1, 1002))
    for kind, delta, orig, payload in records:
        ts += delta
        h4 = BTSNOOZ_H4.get(kind)
        if h4 is None:
            continue
        flags = (1 if kind in BTSNOOZ_IN else 0) | (2 if h4 in (0x01, 0x04) else 0)
        data = bytes([h4]) + payload
        out += struct.pack(">IIIIq", max(orig, len(data)), len(data), flags, 0, ts) + data
    return bytes(out)


def _bugreport_text_frames(text: str, source: str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    """Sezioni `--- BEGIN:BTSNOOP_LOG_SUMMARY ... --- END:BTSNOOP_LOG_SUMMARY` di un bugreport-*.txt."""
    found, pos = 0, 0
    while True:
        a = text.find(BTSNOOZ_BEGIN, pos)
        if a < 0:
            break
        b = text.find(BTSNOOZ_END, a)
        if b < 0:
            break
        line_end = text.find("\n", a)
        pos = b + len(BTSNOOZ_END)
        b64 = "".join(text[line_end:b].split()) if 0 <= line_end < b else ""
        try:
            snoop = decode_btsnooz(base64.b64decode(b64))
        except Exception as exc:  # base64 o zlib rovinati: si passa alla sezione successiva
            if notes is not None:
                notes.append(f"{source}: sezione btsnooz non decodificabile ({exc})")
            continue
        found += 1
        yield from read_btsnoop_bytes(snoop, f"{source}#btsnooz{found}", notes)
    if not found and notes is not None:
        notes.append(f"{source}: nessuna sezione btsnooz decodificata")


def read_bugreport_text(path: Path | str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return _bugreport_text_frames(text, Path(path).name, notes)


def _is_capture_name(name: str) -> bool:
    base = Path(name).name.lower()
    return ("btsnoop" in base and not base.endswith(".txt")) or base.endswith((".pklg", ".cfa", ".btsnoop"))


def read_zip(path: Path | str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    """Bugreport Android (.zip): prima le catture complete (btsnoop_hci.log, .cfa, .pklg, anche .gz),
    altrimenti le sezioni btsnooz del bugreport-*.txt."""
    name = Path(path).name if isinstance(path, (str, Path)) else getattr(path, "name", "archivio.zip")
    found = 0
    with zipfile.ZipFile(path) as zf:
        entries = zf.namelist()
        for entry in entries:
            if not _is_capture_name(entry):
                continue
            frames = _frames_from_bytes(zf.read(entry), f"{name}:{Path(entry).name}", notes)
            if frames is not None:
                found += 1
                yield from frames
        if not found:
            for entry in entries:
                base = Path(entry).name.lower()
                if base.startswith("bugreport") and base.endswith(".txt"):
                    text = zf.read(entry).decode("utf-8", "replace")
                    if BTSNOOZ_BEGIN in text:
                        found += 1
                        yield from _bugreport_text_frames(text, f"{name}:{Path(entry).name}", notes)
    if not found and notes is not None:
        notes.append(f"{name}: nessuna cattura Bluetooth nell'archivio (atteso FS/data/misc/bluetooth/logs/"
                     "btsnoop_hci.log oppure una sezione btsnooz nel bugreport-*.txt)")


# ----- PacketLogger di Apple (.pklg) ----------------------------------------------------

def pklg_byte_order(data: bytes) -> Optional[str]:
    """'>' o '<' se i primi record sembrano PacketLogger (nessun magic number: stessa euristica di Wireshark:
    lunghezza 8..65535, microsecondi < 1e6, tipo di pacchetto noto), altrimenti None."""
    for order in (">", "<"):
        pos, ok = 0, 0
        while pos + 12 <= len(data) and ok < 5:
            length, _secs, usecs = struct.unpack_from(order + "III", data, pos)
            if length < 8 or length >= 65536 or usecs >= 1_000_000:
                ok = 0
                break
            if length > 8 and (pos + 12 >= len(data) or data[pos + 12] not in PKLG_TYPES):
                ok = 0
                break
            pos += 4 + length
            ok += 1
        if ok:
            return order
    return None


def _pklg_packets(data: bytes, order: str) -> Iterator[tuple[bytes, str, str]]:
    pos, t0 = 0, None
    while pos + 12 <= len(data):
        length, secs, usecs = struct.unpack_from(order + "III", data, pos)
        if length < 8:
            break
        payload = data[pos + 12:pos + 4 + length]
        pos += 4 + length
        if not payload:
            continue
        ts = secs * 1_000_000 + usecs
        if t0 is None:
            t0 = ts
        when = f"{(ts - t0) / 1e6:.3f}s"
        if payload[0] == PKLG_SENT_ACL:
            yield payload[1:], "->", when
        elif payload[0] == PKLG_RECV_ACL:
            yield payload[1:], "<-", when


def read_pklg_bytes(data: bytes, source: str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    order = pklg_byte_order(data)
    if order is None:
        raise ValueError("non sembra un file PacketLogger")
    return _acl_frames(_pklg_packets(data, order), source, notes)


def _frames_from_bytes(data: bytes, source: str, notes: Optional[list[str]] = None) -> Optional[Iterator[Frame]]:
    """Riconosce il formato dal contenuto; None se non è una cattura (sarà trattato come testo)."""
    if data.startswith(GZIP_MAGIC):
        try:
            data = gzip.decompress(data)
        except OSError as exc:
            if notes is not None:
                notes.append(f"{source}: gzip non leggibile ({exc})")
            return iter(())
        source = source[:-3] if source.lower().endswith(".gz") else source
    if data.startswith(BTSNOOP_MAGIC):
        return read_btsnoop_bytes(data, source, notes)
    if data.startswith(ZIP_MAGIC):
        return _zip_bytes_frames(data, source, notes)
    if BTSNOOZ_BEGIN.encode() in data:
        return _bugreport_text_frames(data.decode("utf-8", "replace"), source, notes)
    if (source.lower().endswith(".pklg") or not _looks_like_text(data[:512])) and pklg_byte_order(data[:65536]):
        return read_pklg_bytes(data, source, notes)
    return None


def _zip_bytes_frames(data: bytes, source: str, notes: Optional[list[str]]) -> Iterator[Frame]:
    buf = io.BytesIO(data)
    buf.name = Path(source).name  # type: ignore[attr-defined]
    return read_zip(buf, notes)


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True
    printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(head) > 0.9


# ----- log di testo e righe esadecimali ---------------------------------------------------

def _hex_bytes(text: str) -> Optional[bytes]:
    try:
        return bytes.fromhex(re.sub(r"[\s:]", "", text))
    except ValueError:
        return None


def read_text_log(path: Path | str) -> Iterator[Frame]:
    counter, source = [0], Path(path).name
    pending_dir = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            when = (_TIME.match(line) or [None, ""])[1]
            m = _BRACKET_HEX.search(line)
            if m:
                raw = _hex_bytes(m.group(1))
                if raw and len(raw) >= 3 and raw[0] == len(raw):
                    direction = "->" if "-> " in line[:24] else "<-"
                    yield from _split_messages(raw, direction, when, source, counter)
                    continue
            m = _TXRX.search(line)
            if m:
                raw = _hex_bytes(m.group(2))
                if raw:
                    yield from _split_messages(raw, "->" if m.group(1) == "TX" else "<-", when, source, counter)
                continue
            if "Handle Value Notification" in line or "Handle Value Indication" in line:
                pending_dir = "<-"
            elif "Write Command" in line or "Write Request" in line:
                pending_dir = "->"
            else:
                m = _BTMON_DATA.match(line)
                if m and pending_dir:
                    raw = _hex_bytes(m.group(1))
                    if raw:
                        yield from _split_messages(raw, pending_dir, when, source, counter)
                    pending_dir = None


def read_hex_lines(lines: list[str], direction: str = "<-") -> Iterator[Frame]:
    counter = [0]
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = direction
        if line.startswith("->"):
            d, line = "->", line[2:]
        elif line.startswith("<-"):
            d, line = "<-", line[2:]
        raw = _hex_bytes(line)
        if raw:
            yield from _split_messages(raw, d, "", "", counter)


def read_any(path: Path | str, notes: Optional[list[str]] = None) -> Iterator[Frame]:
    """Riconosce il formato dal contenuto: btsnoop, zip (bugreport), gzip, bugreport-*.txt con sezioni
    btsnooz, PacketLogger; tutto il resto è letto come log di testo."""
    p = Path(path)
    data = p.read_bytes()
    frames = _frames_from_bytes(data, p.name, notes)
    return frames if frames is not None else read_text_log(p)


# ----- riepilogo ---------------------------------------------------------------------------

def summarize(records: list[dict]) -> str:
    by_type: dict[str, int] = {}
    by_port: dict[str, int] = {}
    game_events: dict[int, dict] = {}
    errors = []
    for r in records:
        by_type[f"{r['direction']} {r['type']}"] = by_type.get(f"{r['direction']} {r['type']}", 0) + 1
        if r.get("port") is not None and r["type"].startswith("PORT_VALUE"):
            key = f"porta {r['port']}"
            by_port[key] = by_port.get(key, 0) + 1
        if r["type"] == "GENERIC_ERROR":
            errors.append(r["text"])
        if r.get("io_type") == 0x46 and r.get("values") and len(r["values"]) >= 2 and r.get("mode") == 2:
            code, value = int(r["values"][0]) & 0xFFFF, int(r["values"][1]) & 0xFFFF
            g = game_events.setdefault(code, {"n": 0, "values": set()})
            g["n"] += 1
            g["values"].add(value)
    lines = ["=== Riepilogo ===", f"messaggi: {len(records)}"]
    lines += [f"  {k:<32} {v}" for k, v in sorted(by_type.items())]
    if by_port:
        lines.append("valori per porta: " + ", ".join(f"{k} {v}" for k, v in sorted(by_port.items())))
    if game_events:
        from . import codes
        lines.append("eventi porta 3 (codice: occorrenze, valori):")
        for code in sorted(game_events):
            g = game_events[code]
            name = codes.GAME_EVENTS.get(code, "")
            vals = ", ".join(str(v) for v in sorted(g["values"])[:12])
            lines.append(f"  0x{code:04X}: {g['n']:>4}  [{vals}]  {name}")
    if errors:
        lines.append(f"errori generici: {len(errors)}")
        lines += [f"  {e}" for e in errors[:10]]
    return "\n".join(lines)


def run(paths: list[str], hex_args: Optional[list[str]] = None, caps: Optional[str] = None,
        as_json: bool = False, only_types: Optional[set[int]] = None, only_port: Optional[int] = None,
        summary: bool = False, out=None, downstream: bool = True, assume_default_modes: bool = True,
        notes: Optional[list[str]] = None) -> int:
    out = out or sys.stdout
    notes = [] if notes is None else notes
    decoder = Decoder.with_capabilities(caps, assume_default_modes)
    frames: list[Iterator[Frame]] = []
    if hex_args:
        frames.append(read_hex_lines(hex_args))
    for path in paths:
        if path == "-":
            frames.append(read_hex_lines(sys.stdin.read().splitlines()))
        else:
            frames.append(read_any(path, notes))
    records: list[dict] = []
    for it in frames:
        for frame in it:
            rec = decoder.record(frame)  # aggiorna sempre lo stato, anche se la riga viene filtrata
            if not downstream and frame.direction == "->":
                continue
            if only_types and frame.msg.msg_type not in only_types:
                continue
            if only_port is not None and rec.get("port") != only_port:
                continue
            records.append(rec)
            if as_json:
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            else:
                when = f"{rec['when']} " if rec["when"] else ""
                out.write(f"{when}{rec['direction']} {rec['text']}   [{rec['raw']}]\n")
    if summary:
        out.write(("\n" if records else "") + summarize(records) + "\n")
    for note in notes:
        print(f"Nota: {note}", file=sys.stderr)
    return 0


def parse_type_filter(text: str) -> set[int]:
    """'45,46' oppure 'PORT_VALUE_SINGLE,HUB_PROPERTIES' -> insieme di tipi di messaggio."""
    out: set[int] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.add(int(item, 16) if not item.lower().startswith("0x") else int(item, 0))
            continue
        except ValueError:
            pass
        for member in MessageType:
            if member.name.lower() == item.lower():
                out.add(int(member))
                break
        else:
            raise ValueError(f"tipo di messaggio sconosciuto: {item}")
    return out
