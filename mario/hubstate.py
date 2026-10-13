"""
Stato dell'hub ricostruito dai messaggi LWP3, senza dipendere da bleak.

Lo usano `MarioDevice` (dal vivo), lo strumento `decode` (su file e log) e la dashboard. Tiene
proprietà, porte, modalità e formati scoperti, e decodifica anche i **valori combinati**
(messaggio 0x46): Mario fw 6.5.01 manda la maschera dei dataset con il byte alto per primo
(`00 1f` = dataset 0..4) e, con il multi-update, solo i dataset cambiati. Lo stato ricompone i
byte di ogni modo e li espone come se fossero valori singoli (`last_combined`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import codes, lwp3
from .lwp3 import (
    CombinedModeSubcommand, HubPropertyOperation, IOEvent, Message, MessageType, ModeInfoType, ValueFormat,
)

STRUCT_CODES = {0: "b", 1: "h", 2: "i", 3: "f"}


@dataclass
class ModeInfo:
    mode: int
    name: str = ""
    raw: Optional[tuple[float, float]] = None
    pct: Optional[tuple[float, float]] = None
    si: Optional[tuple[float, float]] = None
    symbol: str = ""
    mapping: Optional[tuple[int, int]] = None
    value_format: Optional[ValueFormat] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "symbol": self.symbol,
            "raw": list(self.raw) if self.raw else None,
            "pct": list(self.pct) if self.pct else None,
            "si": list(self.si) if self.si else None,
            "mapping": {"input": lwp3.mapping_names(self.mapping[0]),
                        "output": lwp3.mapping_names(self.mapping[1])} if self.mapping else None,
            "value_format": {"datasets": self.value_format.datasets, "type": self.value_format.type_name,
                             "figures": self.value_format.figures, "decimals": self.value_format.decimals}
            if self.value_format else None,
        }

    @classmethod
    def from_dict(cls, mode: int, d: dict) -> "ModeInfo":
        vf = d.get("value_format") or None
        types = {"int8": 0, "int16": 1, "int32": 2, "float": 3}
        return cls(mode, name=d.get("name") or "", symbol=d.get("symbol") or "",
                   raw=tuple(d["raw"]) if d.get("raw") else None, pct=tuple(d["pct"]) if d.get("pct") else None,
                   si=tuple(d["si"]) if d.get("si") else None,
                   value_format=ValueFormat(int(vf["datasets"]), types.get(str(vf.get("type")), 0),
                                            int(vf.get("figures") or 0), int(vf.get("decimals") or 0)) if vf else None)


@dataclass
class PortState:
    port: int
    io_type: int
    event: int = IOEvent.ATTACHED
    hw_version: str = ""
    sw_version: str = ""
    info: Optional[lwp3.PortInformation] = None
    combinations: list[list[int]] = field(default_factory=list)
    modes: dict[int, ModeInfo] = field(default_factory=dict)
    current_mode: Optional[int] = None
    delta: Optional[int] = None
    notify: Optional[bool] = None
    last_value: Optional[bytes] = None
    virtual_ports: Optional[tuple[int, int]] = None
    # modalità combinata: (modo, dataset) nell'ordine inviato con il sub-comando 0x01, indice e valori per dataset
    combination: list[tuple[int, int]] = field(default_factory=list)
    combination_index: Optional[int] = None
    multi_update: bool = False
    combined_values: dict[int, Any] = field(default_factory=dict)

    @property
    def io_name(self) -> str:
        return lwp3.io_type_name(self.io_type)

    @property
    def combined_active(self) -> bool:
        return bool(self.combination) and self.combination_index is not None

    def mode(self, m: int) -> ModeInfo:
        return self.modes.setdefault(m, ModeInfo(m))

    def value_format(self, m: Optional[int]) -> Optional[ValueFormat]:
        if m is None:
            return None
        mi = self.modes.get(m)
        return mi.value_format if mi else None

    def layout(self) -> list[tuple[int, int, str]]:
        """[(modo, dataset, codice struct)] per i dataset della combinazione, nell'ordine del messaggio 0x46."""
        out = []
        for mode, dataset in self.combination:
            vf = self.value_format(mode)
            out.append((mode, dataset, STRUCT_CODES.get(vf.value_type, "b") if vf else "b"))
        return out

    def mode_bytes(self, mode: int) -> Optional[bytes]:
        """Ricompone i byte del valore di un modo dai dataset ricevuti in modalità combinata."""
        vf = self.value_format(mode)
        if vf is None:
            return None
        code = STRUCT_CODES.get(vf.value_type, "b")
        parts = []
        for ds in range(vf.datasets):
            value = self.combined_values.get((mode, ds))
            if value is None:
                return None
            parts.append(struct.pack("<" + code, value))
        return b"".join(parts)

    def to_dict(self) -> dict:
        return {
            "io_type": f"0x{self.io_type:04X}", "io_name": self.io_name,
            "hw_version": self.hw_version, "sw_version": self.sw_version,
            "capabilities": self.info.capability_names if self.info else None,
            "mode_count": self.info.mode_count if self.info else None,
            "input_modes": self.info.input_mode_list if self.info else None,
            "output_modes": self.info.output_mode_list if self.info else None,
            "combinations": self.combinations,
            "modes": {m: mi.to_dict() for m, mi in sorted(self.modes.items())},
            "hint": codes.port_hint(self.io_type),
        }


@dataclass
class HubState:
    properties: dict[int, bytes] = field(default_factory=dict)
    ports: dict[int, PortState] = field(default_factory=dict)
    # porta -> {modo: byte} dei modi aggiornati dall'ultimo valore combinato ricevuto
    last_combined: dict[int, dict[int, bytes]] = field(default_factory=dict)

    # ----- costruzione --------------------------------------------------------------------

    @classmethod
    def from_capabilities(cls, caps: dict) -> "HubState":
        """Ricostruisce porte, modi e formati dal JSON di `info --json` (docs/capabilities-fw-*.json)."""
        st = cls()
        for port_text, pd in (caps.get("ports") or {}).items():
            port = int(port_text)
            io_type = pd.get("io_type")
            io_type = int(str(io_type), 0) if io_type not in (None, "") else 0xFFFF
            ps = PortState(port, io_type, hw_version=pd.get("hw_version") or "", sw_version=pd.get("sw_version") or "")
            ps.combinations = [list(c) for c in (pd.get("combinations") or [])]
            for mode_text, md in (pd.get("modes") or {}).items():
                ps.modes[int(mode_text)] = ModeInfo.from_dict(int(mode_text), md or {})
            caps_names = pd.get("capabilities") or []
            bits = (1 if "output" in caps_names else 0) | (2 if "input" in caps_names else 0) | \
                   (4 if "combinabile" in caps_names else 0) | (8 if "sincronizzabile" in caps_names else 0)
            in_mask = sum(1 << m for m in (pd.get("input_modes") or []))
            out_mask = sum(1 << m for m in (pd.get("output_modes") or []))
            ps.info = lwp3.PortInformation(port, bits, int(pd.get("mode_count") or len(ps.modes)), in_mask, out_mask)
            st.ports[port] = ps
        for name, info in (caps.get("properties") or {}).items():
            try:
                prop = lwp3.parse_hub_property(name) if not str(info.get("id", "")).startswith("0x") else int(info["id"], 16)
                st.properties[prop] = bytes.fromhex(info.get("raw") or "")
            except (ValueError, KeyError, TypeError):
                continue
        return st

    def property_text(self, prop: int) -> Optional[str]:
        data = self.properties.get(prop)
        return None if data is None else lwp3.format_hub_property(prop, data)

    def port(self, port: int, io_type: int = 0xFFFF) -> PortState:
        return self.ports.setdefault(port, PortState(port, io_type))

    # ----- messaggi in uscita (PC -> Mario) -----------------------------------------------------

    def note_sent(self, data: bytes) -> None:
        """Annota ciò che serve dei comandi inviati: oggi la definizione della modalità combinata."""
        try:
            msg = lwp3.parse(bytes(data))
        except (StopIteration, IndexError):
            return
        p = msg.payload
        if msg.msg_type == MessageType.PORT_INPUT_FORMAT_SETUP_COMBINED and len(p) >= 2:
            ps = self.port(p[0])
            if p[1] == CombinedModeSubcommand.SET_COMBINATION and len(p) >= 3:
                ps.combination = [((b >> 4) & 0x0F, b & 0x0F) for b in p[3:]]
                ps.combination_index = p[2]
                ps.combined_values.clear()
            elif p[1] == CombinedModeSubcommand.RESET_SENSOR:
                ps.combination, ps.combination_index, ps.multi_update = [], None, False
                ps.combined_values.clear()

    # ----- messaggi in ingresso (Mario -> PC) ---------------------------------------------------

    def update(self, msg: Message) -> None:
        t, p = msg.msg_type, msg.payload
        if t == MessageType.HUB_PROPERTIES and len(p) >= 2 and p[1] == HubPropertyOperation.UPDATE:
            self.properties[p[0]] = bytes(p[2:])
        elif t == MessageType.HUB_ATTACHED_IO:
            io = lwp3.decode_attached_io(p)
            if io.event == IOEvent.DETACHED:
                self.ports.pop(io.port, None)
            else:
                old = self.ports.get(io.port)
                ps = PortState(io.port, io.io_type or 0xFFFF, io.event, io.hw_version, io.sw_version)
                if old is not None and old.io_type == ps.io_type:
                    ps.modes, ps.info, ps.combinations = old.modes, old.info, old.combinations  # riconnessione
                if io.event == IOEvent.ATTACHED_VIRTUAL:
                    ps.virtual_ports = (io.port_a, io.port_b)
                self.ports[io.port] = ps
        elif t == MessageType.PORT_INFORMATION:
            info = lwp3.decode_port_information(p)
            if isinstance(info, lwp3.PortInformation):
                self.port(info.port).info = info
            else:
                port, combos = info
                self.port(port).combinations = combos
        elif t == MessageType.PORT_MODE_INFORMATION:
            port, mode, info_type, value = lwp3.decode_port_mode_information(p)
            mi = self.port(port).mode(mode)
            if info_type == ModeInfoType.NAME:
                mi.name = value
            elif info_type == ModeInfoType.RAW and isinstance(value, tuple):
                mi.raw = value
            elif info_type == ModeInfoType.PCT and isinstance(value, tuple):
                mi.pct = value
            elif info_type == ModeInfoType.SI and isinstance(value, tuple):
                mi.si = value
            elif info_type == ModeInfoType.SYMBOL:
                mi.symbol = value
            elif info_type == ModeInfoType.MAPPING and isinstance(value, tuple):
                mi.mapping = value
            elif info_type == ModeInfoType.VALUE_FORMAT and isinstance(value, ValueFormat):
                mi.value_format = value
        elif t == MessageType.PORT_INPUT_FORMAT_SINGLE and len(p) >= 7:
            ps = self.port(p[0])
            ps.current_mode = p[1]
            ps.delta = int.from_bytes(p[2:6], "little")
            ps.notify = bool(p[6])
        elif t == MessageType.PORT_INPUT_FORMAT_COMBINED and len(p) >= 4:
            ps = self.port(p[0])
            ps.combination_index = p[1] & 0x0F
            ps.multi_update = bool(p[1] & 0x80)
            if not ps.combination:
                ps.combination = self.default_combination(p[0], ps.combination_index)
        elif t == MessageType.PORT_VALUE_SINGLE and p:
            self.port(p[0]).last_value = bytes(p[1:])
        elif t == MessageType.PORT_VALUE_COMBINED and len(p) >= 3:
            self.last_combined[p[0]] = self.combined_update(p[0], bytes(p[1:3]), bytes(p[3:]))

    def default_combination(self, port: int, index: int) -> list[tuple[int, int]]:
        """Senza aver visto il comando di impostazione: tutti i dataset dei modi della combinazione `index`."""
        ps = self.ports.get(port)
        if ps is None:
            return []
        modes = ps.combinations[index] if index < len(ps.combinations) else sorted(ps.modes)
        out = []
        for mode in modes:
            vf = ps.value_format(mode)
            out += [(mode, ds) for ds in range(vf.datasets if vf else 1)]
        return out

    @staticmethod
    def _mask(mask_bytes: bytes, layout: list, data_len: int) -> int:
        """Maschera dei dataset: Mario la manda con il byte alto per primo; si sceglie l'ordine che
        corrisponde alla lunghezza dei dati (a parità, quello osservato dal vivo)."""
        candidates = (int.from_bytes(mask_bytes, "big"), int.from_bytes(mask_bytes, "little"))
        for mask in candidates:
            size = sum(struct.calcsize("<" + code) for i, (_, _, code) in enumerate(layout) if mask & (1 << i))
            if size == data_len and mask < (1 << max(1, len(layout))):
                return mask
        return candidates[0]

    def combined_update(self, port: int, mask_bytes: bytes, data: bytes) -> dict[int, bytes]:
        """Aggiorna i dataset ricevuti e ritorna {modo: byte ricomposti} dei modi toccati."""
        ps = self.port(port)
        layout = ps.layout()
        if not layout:
            return {}
        mask, pos, touched = self._mask(mask_bytes, layout, len(data)), 0, set()
        for i, (mode, dataset, code) in enumerate(layout):
            if not mask & (1 << i):
                continue
            size = struct.calcsize("<" + code)
            if pos + size > len(data):
                break
            ps.combined_values[(mode, dataset)] = struct.unpack("<" + code, data[pos:pos + size])[0]
            pos += size
            touched.add(mode)
        out = {}
        for mode in sorted(touched):
            raw = ps.mode_bytes(mode)
            if raw is not None:
                out[mode] = raw
        return out

    # ----- decodifica ------------------------------------------------------------------------------

    def port_lookup(self, port: int):
        """(io_type, modo corrente, formato, scala) per `lwp3.describe`."""
        ps = self.ports.get(port)
        if ps is None:
            return None
        mi = ps.modes.get(ps.current_mode) if ps.current_mode is not None else None
        scale = (mi.raw, mi.si, mi.symbol) if mi and mi.raw and mi.si else None
        return ps.io_type, ps.current_mode, ps.value_format(ps.current_mode), scale

    def mode_values(self, port: int, mode: int, data: bytes) -> tuple[list, Optional[str]]:
        """Valori numerici e interpretazione 'umana' dei byte di un modo."""
        ps = self.ports.get(port)
        vf = ps.value_format(mode) if ps else None
        values = vf.decode(data) if vf else []
        decoded = codes.decode_value(ps.io_type, mode, data, values) if ps else None
        return values, decoded

    def describe_combined(self, msg: Message) -> str:
        p = msg.payload
        port = p[0]
        ps = self.ports.get(port)
        changed = self.last_combined.get(port) or {}
        if ps is None or not changed:
            return lwp3.describe(msg)
        parts = []
        for mode, raw in changed.items():
            name = ps.modes[mode].name if mode in ps.modes and ps.modes[mode].name else f"modo {mode}"
            values, decoded = self.mode_values(port, mode, raw)
            text = f"{name} {raw.hex(' ')}"
            if values:
                text += f" = {values}"
            if decoded:
                text += f" -> {decoded}"
            parts.append(text)
        return f"Valore combinato porta {port}: " + " | ".join(parts)

    def describe(self, msg: Message) -> str:
        if msg.msg_type == MessageType.PORT_VALUE_COMBINED:
            return self.describe_combined(msg)
        return lwp3.describe(msg, self.port_lookup, codes.decode_value)

    def event(self, msg: Message, text: str, ts: Optional[float] = None) -> dict:
        """Dizionario dell'evento come lo usano dashboard (SSE, database), `monitor --json` e assistente."""
        import time as _time
        ev: dict = {"ts": ts if ts is not None else _time.time(), "type": msg.type_name, "text": text,
                    "raw": msg.raw.hex(" ")}
        p = msg.payload
        if msg.msg_type == MessageType.PORT_VALUE_SINGLE and p:
            port, data = p[0], bytes(p[1:])
            ps = self.ports.get(port)
            mode = ps.current_mode if ps else None
            values, decoded = self.mode_values(port, mode, data) if ps and mode is not None else ([], None)
            ev.update(port=port, mode=mode, io_type=ps.io_type if ps else None, io_name=ps.io_name if ps else None,
                      data=data.hex(" "), values=values, decoded=decoded)
        elif msg.msg_type == MessageType.PORT_VALUE_COMBINED and p:
            port = p[0]
            ps = self.ports.get(port)
            modes = {}
            for mode, raw in (self.last_combined.get(port) or {}).items():
                values, decoded = self.mode_values(port, mode, raw)
                modes[str(mode)] = {"name": ps.modes[mode].name if ps and mode in ps.modes else "",
                                    "data": raw.hex(" "), "values": values, "decoded": decoded}
            ev.update(port=port, mode=None, io_type=ps.io_type if ps else None, io_name=ps.io_name if ps else None,
                      modes=modes, decoded=" | ".join(f"{m['name'] or k}: {m['decoded'] or m['data']}" for k, m in modes.items()) or None)
        elif msg.msg_type == MessageType.HUB_PROPERTIES and len(p) >= 2 and p[1] == HubPropertyOperation.UPDATE:
            prop = p[0]
            ev.update(prop=lwp3.enum_name(lwp3.HubProperty, prop), value=lwp3.decode_hub_property(prop, p[2:]))
        elif msg.msg_type == MessageType.HUB_ATTACHED_IO and p:
            ev.update(port=p[0])
        return ev
