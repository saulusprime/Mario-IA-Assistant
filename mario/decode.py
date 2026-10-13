"""
Strumento `decode`: rilegge il traffico LWP3 da file e lo decodifica con lo stato delle porte.

Sorgenti accettate:
  - catture btsnoop (`btmon -w file`, HCI snoop log di Android): formato "Linux monitor" (2001),
    H4/UART (1002) e HCI puro (1001); vengono estratti gli ATT Write (PC -> Mario) e le
    Handle Value Notification (Mario -> PC) sulla caratteristica LEGO, con riassemblaggio L2CAP;
  - log di testo di `monitor`, `shell`, `assistant` e degli strumenti (`[... ]` con i byte grezzi
    in coda alla riga), l'uscita di `btmon -r` e le righe `TX ..`/`RX ..` dell'opzione -d;
  - righe o argomenti esadecimali.

Lo stato iniziale delle porte (IO type, modi, formati) viene da `docs/capabilities-fw-*.json`,
poi evolve con i messaggi letti (sottoscrizioni, modalità combinata), come dal vivo.
"""
from __future__ import annotations

import json
import re
import struct
import sys
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


def read_btsnoop(path: Path | str) -> Iterator[Frame]:
    data = Path(path).read_bytes()
    if not data.startswith(b"btsnoop\x00"):
        raise ValueError("non è un file btsnoop (manca l'intestazione)")
    _version, datalink = struct.unpack(">II", data[8:16])
    pos, counter, reasm, t0 = 16, [0], _L2capReassembler(), None
    source = Path(path).name
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
        if acl is None:
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


def read_any(path: Path | str) -> Iterator[Frame]:
    p = Path(path)
    with open(p, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"btsnoop\x00"):
        return read_btsnoop(p)
    return read_text_log(p)


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
        summary: bool = False, out=None, downstream: bool = True, assume_default_modes: bool = True) -> int:
    out = out or sys.stdout
    decoder = Decoder.with_capabilities(caps, assume_default_modes)
    frames: list[Iterator[Frame]] = []
    if hex_args:
        frames.append(read_hex_lines(hex_args))
    for path in paths:
        if path == "-":
            frames.append(read_hex_lines(sys.stdin.read().splitlines()))
        else:
            frames.append(read_any(path))
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
