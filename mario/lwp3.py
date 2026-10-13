"""
LEGO Wireless Protocol 3.0 (LWP3): costanti, costruzione e decodifica dei messaggi.

Riferimento ufficiale: https://lego.github.io/lego-ble-wireless-protocol-docs/

Ogni messaggio ha la forma  [lunghezza] [hub id = 0x00] [tipo] [payload...]
La lunghezza occupa un byte se < 128, altrimenti due byte (bit 7 del primo a 1).
Questo modulo non dipende da bleak: si puo' usare e testare anche senza Bluetooth.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Iterator, Optional

LEGO_SERVICE_UUID = "00001623-1212-efde-1623-785feabcd123"
LEGO_CHARACTERISTIC_UUID = "00001624-1212-efde-1623-785feabcd123"
LEGO_COMPANY_ID = 0x0397  # LEGO System A/S (Bluetooth SIG company identifier)


def enum_name(enum_cls: type[IntEnum], value: int) -> str:
    """Nome simbolico di un valore oppure UNKNOWN_0xNN se non e' noto."""
    try:
        return enum_cls(value).name
    except ValueError:
        return f"UNKNOWN_0x{value:02X}"


def parse_int(text: str) -> int:
    """Converte '12', '0x0c' o '0b1100' in intero."""
    return int(text, 0)


def le16(data: bytes) -> int:
    return int.from_bytes(data[:2], "little")


# ---------------------------------------------------------------------------
# Enumerazioni del protocollo
# ---------------------------------------------------------------------------

class MessageType(IntEnum):
    HUB_PROPERTIES = 0x01
    HUB_ACTIONS = 0x02
    HUB_ALERTS = 0x03
    HUB_ATTACHED_IO = 0x04
    GENERIC_ERROR = 0x05
    HW_NETWORK_COMMANDS = 0x08
    FW_UPDATE_GO_INTO_BOOT_MODE = 0x10
    FW_UPDATE_LOCK_MEMORY = 0x11
    FW_UPDATE_LOCK_STATUS_REQUEST = 0x12
    FW_LOCK_STATUS = 0x13
    PORT_INFORMATION_REQUEST = 0x21
    PORT_MODE_INFORMATION_REQUEST = 0x22
    PORT_INPUT_FORMAT_SETUP_SINGLE = 0x41
    PORT_INPUT_FORMAT_SETUP_COMBINED = 0x42
    PORT_INFORMATION = 0x43
    PORT_MODE_INFORMATION = 0x44
    PORT_VALUE_SINGLE = 0x45
    PORT_VALUE_COMBINED = 0x46
    PORT_INPUT_FORMAT_SINGLE = 0x47
    PORT_INPUT_FORMAT_COMBINED = 0x48
    VIRTUAL_PORT_SETUP = 0x61
    PORT_OUTPUT_COMMAND = 0x81
    PORT_OUTPUT_COMMAND_FEEDBACK = 0x82


class HubProperty(IntEnum):
    ADVERTISING_NAME = 0x01
    BUTTON = 0x02
    FW_VERSION = 0x03
    HW_VERSION = 0x04
    RSSI = 0x05
    BATTERY_VOLTAGE = 0x06
    BATTERY_TYPE = 0x07
    MANUFACTURER_NAME = 0x08
    RADIO_FW_VERSION = 0x09
    LWP_VERSION = 0x0A
    SYSTEM_TYPE_ID = 0x0B
    HW_NETWORK_ID = 0x0C
    PRIMARY_MAC = 0x0D
    SECONDARY_MAC = 0x0E
    HW_NETWORK_FAMILY = 0x0F
    VOLUME = 0x12  # specifica di LEGO Mario/Luigi/Peach: volume altoparlante 0-100


# Alias comodi per la riga di comando
HUB_PROPERTY_ALIASES = {
    "name": HubProperty.ADVERTISING_NAME, "nome": HubProperty.ADVERTISING_NAME,
    "button": HubProperty.BUTTON, "pulsante": HubProperty.BUTTON,
    "fw": HubProperty.FW_VERSION, "firmware": HubProperty.FW_VERSION,
    "hw": HubProperty.HW_VERSION, "hardware": HubProperty.HW_VERSION,
    "rssi": HubProperty.RSSI,
    "battery": HubProperty.BATTERY_VOLTAGE, "batteria": HubProperty.BATTERY_VOLTAGE,
    "battery_type": HubProperty.BATTERY_TYPE,
    "manufacturer": HubProperty.MANUFACTURER_NAME,
    "radio": HubProperty.RADIO_FW_VERSION,
    "lwp": HubProperty.LWP_VERSION,
    "system": HubProperty.SYSTEM_TYPE_ID, "type": HubProperty.SYSTEM_TYPE_ID,
    "network": HubProperty.HW_NETWORK_ID,
    "mac": HubProperty.PRIMARY_MAC, "mac2": HubProperty.SECONDARY_MAC,
    "family": HubProperty.HW_NETWORK_FAMILY,
    "volume": HubProperty.VOLUME,
}


def parse_hub_property(text: str) -> int:
    """Accetta un alias ('volume'), il nome dell'enum ('VOLUME') o un numero ('0x12')."""
    key = text.strip().lower()
    if key in HUB_PROPERTY_ALIASES:
        return int(HUB_PROPERTY_ALIASES[key])
    for prop in HubProperty:
        if prop.name.lower() == key:
            return int(prop)
    return parse_int(text)


class HubPropertyOperation(IntEnum):
    SET = 0x01
    ENABLE_UPDATES = 0x02
    DISABLE_UPDATES = 0x03
    RESET = 0x04
    REQUEST_UPDATE = 0x05
    UPDATE = 0x06


class HubAction(IntEnum):
    SWITCH_OFF = 0x01
    DISCONNECT = 0x02
    VCC_PORT_ON = 0x03
    VCC_PORT_OFF = 0x04
    ACTIVATE_BUSY = 0x05
    RESET_BUSY = 0x06
    SHUTDOWN = 0x2F
    # inviate dall'hub verso di noi
    WILL_SWITCH_OFF = 0x30
    WILL_DISCONNECT = 0x31
    WILL_GO_INTO_BOOT_MODE = 0x32


class HubAlertType(IntEnum):
    LOW_VOLTAGE = 0x01
    HIGH_CURRENT = 0x02
    LOW_SIGNAL_STRENGTH = 0x03
    OVER_POWER_CONDITION = 0x04


class HubAlertOperation(IntEnum):
    ENABLE_UPDATES = 0x01
    DISABLE_UPDATES = 0x02
    REQUEST_UPDATE = 0x03
    UPDATE = 0x04


class IOEvent(IntEnum):
    DETACHED = 0x00
    ATTACHED = 0x01
    ATTACHED_VIRTUAL = 0x02


class ErrorCode(IntEnum):
    ACK = 0x01
    MACK = 0x02
    BUFFER_OVERFLOW = 0x03
    TIMEOUT = 0x04
    COMMAND_NOT_RECOGNIZED = 0x05
    INVALID_USE = 0x06
    OVERCURRENT = 0x07
    INTERNAL_ERROR = 0x08


class PortInfoType(IntEnum):
    PORT_VALUE = 0x00
    MODE_INFO = 0x01
    POSSIBLE_MODE_COMBINATIONS = 0x02


class ModeInfoType(IntEnum):
    NAME = 0x00
    RAW = 0x01
    PCT = 0x02
    SI = 0x03
    SYMBOL = 0x04
    MAPPING = 0x05
    INTERNAL_USE = 0x06
    MOTOR_BIAS = 0x07
    CAPABILITY_BITS = 0x08
    VALUE_FORMAT = 0x80


class CombinedModeSubcommand(IntEnum):
    SET_COMBINATION = 0x01
    LOCK = 0x02
    UNLOCK_MULTI_UPDATE_ENABLED = 0x03
    UNLOCK_MULTI_UPDATE_DISABLED = 0x04
    RESET_SENSOR = 0x06


class ValueType(IntEnum):
    INT8 = 0x00
    INT16 = 0x01
    INT32 = 0x02
    FLOAT = 0x03


IO_TYPE_NAMES: dict[int, str] = {
    0x0000: "Motore semplice", 0x0001: "Motore treno", 0x0002: "Luce",
    0x0008: "Luci LED", 0x0014: "Sensore tensione batteria", 0x0015: "Sensore corrente",
    0x0016: "Piezo (suono)", 0x0017: "LED RGB dell'hub",
    0x0022: "Sensore inclinazione WeDo 2.0", 0x0023: "Sensore movimento WeDo 2.0",
    0x0025: "Sensore colore/distanza Boost", 0x0026: "Motore interattivo Boost",
    0x0027: "Motore interno Move Hub", 0x0028: "Sensore inclinazione Move Hub",
    0x0029: "Motore treno Duplo", 0x002A: "Altoparlante treno Duplo",
    0x002B: "Sensore colore treno Duplo", 0x002C: "Tachimetro treno Duplo",
    0x002E: "Motore Technic L", 0x002F: "Motore Technic XL",
    0x0030: "Motore angolare medio SPIKE", 0x0031: "Motore angolare grande SPIKE",
    0x0036: "Sensore gesti hub Technic", 0x0037: "Pulsanti telecomando", 0x0038: "RSSI telecomando",
    0x0039: "Accelerometro hub Technic", 0x003A: "Giroscopio hub Technic",
    0x003B: "Inclinazione hub Technic", 0x003C: "Temperatura hub Technic",
    0x003D: "Sensore colore SPIKE", 0x003E: "Sensore distanza SPIKE", 0x003F: "Sensore forza SPIKE",
    0x0040: "Matrice LED 3x3 SPIKE", 0x0041: "Motore angolare piccolo SPIKE",
    0x0046: "LEGO Mario: eventi di gioco / debug", 0x0047: "LEGO Mario: accelerometro e gesti",
    0x0049: "LEGO Mario: scanner colore / barcode", 0x004A: "LEGO Mario: sensore pantaloni",
    0x004B: "Motore angolare medio Technic (grigio)", 0x004C: "Motore angolare grande Technic (grigio)",
    0x0055: "LEGO Mario: eventi alternativi",
    0x0060: "LEGO Mario: telemetria",
}

SYSTEM_TYPE_NAMES: dict[int, str] = {
    0x00: "WeDo 2.0 Hub", 0x20: "Duplo Train Hub",
    0x40: "Boost Move Hub (anche LEGO Mario firmware 6.x riporta 0x40 in questa proprietà)",
    0x41: "Powered Up Hub (2 porte)", 0x42: "Telecomando Powered Up",
    0x43: "LEGO Mario", 0x44: "LEGO Luigi", 0x45: "LEGO Peach",
    0x80: "Technic Medium Hub (Control+)", 0x81: "Technic Large Hub (SPIKE Prime)",
    0x83: "Technic Small Hub (SPIKE Essential)",
}


def io_type_name(io_type: int) -> str:
    return IO_TYPE_NAMES.get(io_type, f"dispositivo sconosciuto 0x{io_type:04X}")


# ---------------------------------------------------------------------------
# Costruzione dei messaggi (downstream: PC -> Mario)
# ---------------------------------------------------------------------------

def build(msg_type: int, payload: bytes = b"", hub_id: int = 0) -> bytes:
    """Aggiunge l'header LWP3 [lunghezza][hub id][tipo] a un payload."""
    payload = bytes(payload)
    length = len(payload) + 3
    if length < 128:
        header = bytes([length, hub_id, msg_type])
    else:
        length += 1
        header = bytes([(length & 0x7F) | 0x80, length >> 7, hub_id, msg_type])
    return header + payload


def hub_property(prop: int, operation: int, payload: bytes = b"") -> bytes:
    return build(MessageType.HUB_PROPERTIES, bytes([prop, operation]) + bytes(payload))


def hub_property_request(prop: int) -> bytes:
    return hub_property(prop, HubPropertyOperation.REQUEST_UPDATE)


def hub_property_set(prop: int, value: bytes) -> bytes:
    return hub_property(prop, HubPropertyOperation.SET, value)


def hub_property_updates(prop: int, enable: bool = True) -> bytes:
    op = HubPropertyOperation.ENABLE_UPDATES if enable else HubPropertyOperation.DISABLE_UPDATES
    return hub_property(prop, op)


def hub_property_reset(prop: int) -> bytes:
    return hub_property(prop, HubPropertyOperation.RESET)


def hub_action(action: int) -> bytes:
    return build(MessageType.HUB_ACTIONS, bytes([action]))


def hub_alert(alert: int, operation: int) -> bytes:
    return build(MessageType.HUB_ALERTS, bytes([alert, operation]))


def port_information_request(port: int, info_type: int = PortInfoType.MODE_INFO) -> bytes:
    return build(MessageType.PORT_INFORMATION_REQUEST, bytes([port, info_type]))


def port_mode_information_request(port: int, mode: int, info_type: int) -> bytes:
    return build(MessageType.PORT_MODE_INFORMATION_REQUEST, bytes([port, mode, info_type]))


def port_input_format_setup(port: int, mode: int, delta: int = 1, notify: bool = True) -> bytes:
    """Imposta la modalita' di una porta e abilita (o no) le notifiche sui cambi di valore."""
    payload = bytes([port, mode]) + struct.pack("<I", delta) + bytes([1 if notify else 0])
    return build(MessageType.PORT_INPUT_FORMAT_SETUP_SINGLE, payload)


def port_input_format_setup_combined(port: int, subcommand: int, payload: bytes = b"") -> bytes:
    return build(MessageType.PORT_INPUT_FORMAT_SETUP_COMBINED, bytes([port, subcommand]) + bytes(payload))


def port_combination_set(port: int, combination_index: int, mode_datasets: list[tuple[int, int]]) -> bytes:
    """Sub-comando 0x01: definisce la combinazione (indice + lista di (modo, dataset))."""
    entries = bytes(((mode & 0x0F) << 4) | (dataset & 0x0F) for mode, dataset in mode_datasets)
    return port_input_format_setup_combined(port, CombinedModeSubcommand.SET_COMBINATION,
                                            bytes([combination_index]) + entries)


def virtual_port_connect(port_a: int, port_b: int) -> bytes:
    return build(MessageType.VIRTUAL_PORT_SETUP, bytes([0x01, port_a, port_b]))


def virtual_port_disconnect(port: int) -> bytes:
    return build(MessageType.VIRTUAL_PORT_SETUP, bytes([0x00, port]))


def port_output_command(port: int, subcommand: int, payload: bytes = b"",
                        startup: int = 0x1, completion: int = 0x1) -> bytes:
    """Port Output Command generico. startup: 0=buffer,1=esegui subito; completion: 0=nessun feedback,1=feedback."""
    flags = ((startup & 0x0F) << 4) | (completion & 0x0F)
    return build(MessageType.PORT_OUTPUT_COMMAND, bytes([port, flags, subcommand]) + bytes(payload))


# ---------------------------------------------------------------------------
# Decodifica (upstream: Mario -> PC)
# ---------------------------------------------------------------------------

def _bcd(value: int) -> int:
    result, factor = 0, 1
    while value:
        result += (value & 0xF) * factor
        factor *= 10
        value >>= 4
    return result


def decode_version(data: bytes) -> str:
    """Version Number Encoding LWP3: major.minor.bugfix.build (BCD)."""
    if len(data) < 4:
        return data.hex()
    v = int.from_bytes(data[:4], "little")
    return f"{(v >> 28) & 0xF}.{(v >> 24) & 0xF}.{_bcd((v >> 16) & 0xFF):02d}.{_bcd(v & 0xFFFF):04d}"


def decode_lwp_version(data: bytes) -> str:
    v = le16(data)
    return f"{v >> 8}.{v & 0xFF}"


def decode_hub_property(prop: int, data: bytes) -> Any:
    """Valore Python di una proprieta' dell'hub a partire dal payload grezzo."""
    data = bytes(data)
    if not data:
        return None
    if prop in (HubProperty.ADVERTISING_NAME, HubProperty.MANUFACTURER_NAME, HubProperty.RADIO_FW_VERSION):
        return data.decode("utf-8", "replace").rstrip("\x00")
    if prop == HubProperty.BUTTON:
        return bool(data[0])
    if prop in (HubProperty.FW_VERSION, HubProperty.HW_VERSION):
        return decode_version(data)
    if prop == HubProperty.RSSI:
        return struct.unpack("<b", data[:1])[0]
    if prop == HubProperty.LWP_VERSION:
        return decode_lwp_version(data)
    if prop in (HubProperty.PRIMARY_MAC, HubProperty.SECONDARY_MAC):
        return ":".join(f"{b:02X}" for b in data[:6])
    if prop in (HubProperty.BATTERY_VOLTAGE, HubProperty.BATTERY_TYPE, HubProperty.SYSTEM_TYPE_ID,
                HubProperty.HW_NETWORK_ID, HubProperty.HW_NETWORK_FAMILY, HubProperty.VOLUME):
        return data[0]
    return data.hex()


def format_hub_property(prop: int, data: bytes) -> str:
    """Testo leggibile di una proprieta' dell'hub."""
    value = decode_hub_property(prop, data)
    if value is None:
        return "(vuoto)"
    if prop == HubProperty.BUTTON:
        return "premuto" if value else "rilasciato"
    if prop == HubProperty.RSSI:
        return f"{value} dBm"
    if prop == HubProperty.BATTERY_VOLTAGE:
        return f"{value} %"
    if prop == HubProperty.BATTERY_TYPE:
        return {0: "normale (pile)", 1: "ricaricabile"}.get(value, str(value))
    if prop == HubProperty.SYSTEM_TYPE_ID:
        return f"0x{value:02X} ({SYSTEM_TYPE_NAMES.get(value, 'sconosciuto')})"
    if prop == HubProperty.VOLUME:
        return f"{value} %"
    return str(value)


@dataclass(frozen=True)
class ValueFormat:
    """Formato dei valori di una modalita': numero di dataset, tipo, cifre, decimali."""
    datasets: int
    value_type: int
    figures: int
    decimals: int

    @property
    def width(self) -> int:
        return {0: 1, 1: 2, 2: 4, 3: 4}.get(self.value_type, 1)

    @property
    def type_name(self) -> str:
        return {0: "int8", 1: "int16", 2: "int32", 3: "float"}.get(self.value_type, f"tipo {self.value_type}")

    def decode(self, data: bytes) -> list:
        fmt = {0: "b", 1: "h", 2: "i", 3: "f"}.get(self.value_type, "b")
        count = len(data) // self.width
        if count == 0:
            return []
        values = list(struct.unpack("<" + fmt * count, bytes(data[: count * self.width])))
        if self.decimals and self.value_type != ValueType.FLOAT:
            values = [v / (10 ** self.decimals) for v in values]
        return values

    def __str__(self) -> str:
        return f"{self.datasets} x {self.type_name}, {self.figures} cifre, {self.decimals} decimali"


@dataclass(frozen=True)
class PortInformation:
    port: int
    capabilities: int
    mode_count: int
    input_modes: int
    output_modes: int

    @property
    def capability_names(self) -> list[str]:
        names = []
        if self.capabilities & 0x01:
            names.append("output")
        if self.capabilities & 0x02:
            names.append("input")
        if self.capabilities & 0x04:
            names.append("combinabile")
        if self.capabilities & 0x08:
            names.append("sincronizzabile")
        return names

    @property
    def input_mode_list(self) -> list[int]:
        return [m for m in range(16) if (self.input_modes >> m) & 1]

    @property
    def output_mode_list(self) -> list[int]:
        return [m for m in range(16) if (self.output_modes >> m) & 1]

    @property
    def all_modes(self) -> list[int]:
        modes = set(self.input_mode_list) | set(self.output_mode_list)
        return sorted(modes) if modes else list(range(self.mode_count))


@dataclass(frozen=True)
class AttachedIO:
    port: int
    event: int
    io_type: Optional[int] = None
    hw_version: str = ""
    sw_version: str = ""
    port_a: Optional[int] = None
    port_b: Optional[int] = None


def decode_attached_io(payload: bytes) -> AttachedIO:
    port, event = payload[0], payload[1]
    if event == IOEvent.DETACHED:
        return AttachedIO(port, event)
    io_type = le16(payload[2:4])
    if event == IOEvent.ATTACHED:
        return AttachedIO(port, event, io_type, decode_version(payload[4:8]), decode_version(payload[8:12]))
    return AttachedIO(port, event, io_type, port_a=payload[4], port_b=payload[5])


def decode_port_information(payload: bytes):
    """Ritorna PortInformation (info type 1) oppure (porta, [combinazioni]) (info type 2)."""
    port, info_type = payload[0], payload[1]
    if info_type == PortInfoType.MODE_INFO:
        return PortInformation(port, payload[2], payload[3], le16(payload[4:6]), le16(payload[6:8]))
    if info_type == PortInfoType.POSSIBLE_MODE_COMBINATIONS:
        combos = []
        for i in range(2, len(payload) - 1, 2):
            mask = le16(payload[i:i + 2])
            if mask:
                combos.append([m for m in range(16) if (mask >> m) & 1])
        return port, combos
    raise ValueError(f"info type {info_type} non gestito")


MAPPING_BITS = {7: "NULL", 6: "Functional mapping 2.0+", 4: "ABS", 3: "REL", 2: "DIS"}


def mapping_names(bits: int) -> list[str]:
    return [name for bit, name in MAPPING_BITS.items() if (bits >> bit) & 1]


def decode_port_mode_information(payload: bytes) -> tuple[int, int, int, Any]:
    """Ritorna (porta, modo, info type, valore decodificato)."""
    port, mode, info_type = payload[0], payload[1], payload[2]
    data = bytes(payload[3:])
    if info_type in (ModeInfoType.NAME, ModeInfoType.SYMBOL):
        value = data.decode("ascii", "replace").rstrip("\x00").strip()
    elif info_type in (ModeInfoType.RAW, ModeInfoType.PCT, ModeInfoType.SI):
        value = struct.unpack("<ff", data[:8]) if len(data) >= 8 else data.hex()
    elif info_type == ModeInfoType.MAPPING:
        value = (data[0], data[1]) if len(data) >= 2 else data.hex()
    elif info_type == ModeInfoType.MOTOR_BIAS:
        value = data[0]
    elif info_type == ModeInfoType.VALUE_FORMAT:
        value = ValueFormat(data[0], data[1], data[2], data[3]) if len(data) >= 4 else data.hex()
    else:
        value = data.hex()
    return port, mode, info_type, value


def format_mode_info(info_type: int, value: Any) -> str:
    if info_type in (ModeInfoType.RAW, ModeInfoType.PCT, ModeInfoType.SI) and isinstance(value, tuple):
        return f"{value[0]:g} .. {value[1]:g}"
    if info_type == ModeInfoType.MAPPING and isinstance(value, tuple):
        return f"input={mapping_names(value[0]) or '-'} output={mapping_names(value[1]) or '-'}"
    return str(value)


FEEDBACK_BITS = {0: "buffer vuoto, comando in corso", 1: "buffer vuoto, comando completato",
                 2: "comando scartato", 3: "inattivo", 4: "occupato / buffer pieno"}


@dataclass
class Message:
    msg_type: int
    payload: bytes
    raw: bytes
    hub_id: int = 0

    @property
    def type_name(self) -> str:
        return enum_name(MessageType, self.msg_type)

    def __str__(self) -> str:
        return f"{self.type_name} {self.payload.hex(' ')}"


def parse_all(data: bytes) -> Iterator[Message]:
    """Spezza un pacchetto in uno o piu' messaggi LWP3 (normalmente uno per notifica)."""
    data = bytes(data)
    pos = 0
    while pos < len(data):
        first = data[pos]
        if first & 0x80:
            if pos + 1 >= len(data):
                break
            length = (first & 0x7F) | (data[pos + 1] << 7)
            header = 4
        else:
            length = first
            header = 3
        if length < header or pos + length > len(data):
            # messaggio malformato: lo consegniamo cosi' com'e'
            yield Message(data[pos + header - 1] if pos + header - 1 < len(data) else 0,
                          data[pos + header:], data[pos:], 0)
            return
        chunk = data[pos:pos + length]
        yield Message(chunk[header - 1], chunk[header:], chunk, chunk[header - 2])
        pos += length


def parse(data: bytes) -> Message:
    return next(parse_all(data))


PortLookup = Callable[[int], Optional[tuple]]


def scale_values(values: list, scale) -> Optional[list]:
    """Converte valori raw in unità SI usando (intervallo raw, intervallo SI, simbolo) della modalità.
    Ritorna None se la conversione e' identica o non applicabile."""
    if not scale or not values:
        return None
    raw_range, si_range, _symbol = scale
    if not raw_range or not si_range or tuple(raw_range) == tuple(si_range):
        return None
    r0, r1 = raw_range
    s0, s1 = si_range
    if r1 == r0:
        return None
    out = []
    for v in values:
        if not isinstance(v, (int, float)):
            return None
        out.append(round((v - r0) * (s1 - s0) / (r1 - r0) + s0, 2))
    return out
ValueDecoder = Callable[[int, Optional[int], bytes, list], Optional[str]]


def describe(msg: Message, port_lookup: Optional[PortLookup] = None,
             value_decoder: Optional[ValueDecoder] = None) -> str:
    """Descrizione leggibile di un messaggio in arrivo."""
    t, p = msg.msg_type, msg.payload
    try:
        if t == MessageType.HUB_PROPERTIES:
            prop, op, data = p[0], p[1], p[2:]
            text = f"Proprietà {enum_name(HubProperty, prop)} (0x{prop:02X}) [{enum_name(HubPropertyOperation, op)}]"
            if op == HubPropertyOperation.UPDATE:
                return f"{text}: {format_hub_property(prop, data)}"
            return f"{text} {data.hex(' ')}".rstrip()
        if t == MessageType.HUB_ACTIONS:
            return f"Azione hub: {enum_name(HubAction, p[0])}"
        if t == MessageType.HUB_ALERTS:
            text = f"Allarme {enum_name(HubAlertType, p[0])} [{enum_name(HubAlertOperation, p[1])}]"
            if len(p) > 2:
                text += ": ATTIVO" if p[2] else ": nessun problema"
            return text
        if t == MessageType.HUB_ATTACHED_IO:
            io = decode_attached_io(p)
            if io.event == IOEvent.DETACHED:
                return f"Porta {io.port}: dispositivo scollegato"
            if io.event == IOEvent.ATTACHED_VIRTUAL:
                return f"Porta {io.port}: porta virtuale di {io.port_a}+{io.port_b}, {io_type_name(io.io_type)} (0x{io.io_type:04X})"
            return (f"Porta {io.port}: collegato {io_type_name(io.io_type)} (IO type 0x{io.io_type:04X}), "
                    f"HW {io.hw_version}, SW {io.sw_version}")
        if t == MessageType.GENERIC_ERROR:
            return f"ERRORE per il comando {enum_name(MessageType, p[0])}: {enum_name(ErrorCode, p[1])}"
        if t == MessageType.PORT_INFORMATION:
            info = decode_port_information(p)
            if isinstance(info, PortInformation):
                return (f"Info porta {info.port}: capacità={info.capability_names}, modalità={info.mode_count}, "
                        f"input={info.input_mode_list}, output={info.output_mode_list}")
            port, combos = info
            return f"Info porta {port}: combinazioni possibili {combos}"
        if t == MessageType.PORT_MODE_INFORMATION:
            port, mode, info_type, value = decode_port_mode_information(p)
            return f"Porta {port} modo {mode} {enum_name(ModeInfoType, info_type)}: {format_mode_info(info_type, value)}"
        if t == MessageType.PORT_VALUE_SINGLE:
            port, data = p[0], bytes(p[1:])
            text = f"Valore porta {port}: {data.hex(' ')}"
            if port_lookup:
                found = port_lookup(port)
                if found:
                    io_type, mode, vf = found[0], found[1], found[2]
                    scale = found[3] if len(found) > 3 else None
                    values = vf.decode(data) if vf else []
                    if mode is not None:
                        text = f"Valore porta {port} modo {mode}: {data.hex(' ')}"
                    if values:
                        text += f" = {values}"
                        si_values = scale_values(values, scale)
                        if si_values is not None:
                            text += f" (SI: {si_values}{' ' + scale[2] if scale[2] else ''})"
                    if value_decoder:
                        extra = value_decoder(io_type, mode, data, values)
                        if extra:
                            text += f" -> {extra}"
            return text
        if t == MessageType.PORT_VALUE_COMBINED:
            # Mario (fw 6.5.01) manda la maschera dei dataset con il byte alto per primo: `00 1f` = dataset 0..4
            port, mask, data = p[0], int.from_bytes(bytes(p[1:3]), "big"), p[3:]
            return f"Valore combinato porta {port}: dataset mask 0b{mask:016b} dati {data.hex(' ')}"
        if t == MessageType.PORT_INPUT_FORMAT_SINGLE:
            port, mode, delta, notify = p[0], p[1], struct.unpack("<I", p[2:6])[0], p[6]
            return f"Formato input porta {port}: modo {mode}, delta {delta}, notifiche {'ON' if notify else 'OFF'}"
        if t == MessageType.PORT_INPUT_FORMAT_COMBINED:
            port, control, mask = p[0], p[1], le16(p[2:4])
            return (f"Formato combinato porta {port}: combinazione {control & 0x0F}, "
                    f"multi-update {'ON' if control & 0x80 else 'OFF'}, dataset mask 0b{mask:016b}")
        if t == MessageType.PORT_OUTPUT_COMMAND_FEEDBACK:
            parts = []
            for i in range(0, len(p) - 1, 2):
                bits = [name for bit, name in FEEDBACK_BITS.items() if (p[i + 1] >> bit) & 1]
                parts.append(f"porta {p[i]}: {', '.join(bits) or hex(p[i + 1])}")
            return "Feedback comando output: " + "; ".join(parts)
    except Exception as exc:  # decodifica fallita: mostriamo comunque i byte
        return f"{msg.type_name}: {p.hex(' ')} (decodifica fallita: {exc})"
    return f"{msg.type_name}: {p.hex(' ')}"


# ---------------------------------------------------------------------------
# Decodifica dei messaggi in uscita (downstream: PC -> Mario), per log e catture
# ---------------------------------------------------------------------------

OUTPUT_SUBCOMMANDS = {0x01: "StartPower", 0x02: "StartPower2", 0x05: "SetAccTime", 0x06: "SetDecTime",
                      0x07: "StartSpeed", 0x08: "StartSpeed2", 0x09: "StartSpeedForTime",
                      0x0B: "StartSpeedForDegrees", 0x0D: "GotoAbsolutePosition", 0x50: "WriteDirect",
                      0x51: "WriteDirectModeData"}


def describe_downstream(msg: Message) -> str:
    """Descrizione leggibile di un messaggio inviato all'hub."""
    t, p = msg.msg_type, msg.payload
    try:
        if t == MessageType.HUB_PROPERTIES:
            prop, op = p[0], p[1]
            text = f"Proprietà {enum_name(HubProperty, prop)} (0x{prop:02X}): {enum_name(HubPropertyOperation, op)}"
            if op == HubPropertyOperation.SET and len(p) > 2:
                text += f" = {format_hub_property(prop, p[2:])} [{p[2:].hex(' ')}]"
            return text
        if t == MessageType.HUB_ACTIONS:
            return f"Azione hub: {enum_name(HubAction, p[0])}"
        if t == MessageType.HUB_ALERTS:
            return f"Allarme {enum_name(HubAlertType, p[0])}: {enum_name(HubAlertOperation, p[1])}"
        if t == MessageType.PORT_INFORMATION_REQUEST:
            return f"Richiesta info porta {p[0]}: {enum_name(PortInfoType, p[1])}"
        if t == MessageType.PORT_MODE_INFORMATION_REQUEST:
            return f"Richiesta info porta {p[0]} modo {p[1]}: {enum_name(ModeInfoType, p[2])}"
        if t == MessageType.PORT_INPUT_FORMAT_SETUP_SINGLE:
            delta = struct.unpack("<I", p[2:6])[0] if len(p) >= 6 else 0
            notify = "ON" if len(p) > 6 and p[6] else "OFF"
            return f"Imposta porta {p[0]} modo {p[1]}: delta {delta}, notifiche {notify}"
        if t == MessageType.PORT_INPUT_FORMAT_SETUP_COMBINED:
            sub = enum_name(CombinedModeSubcommand, p[1])
            if p[1] == CombinedModeSubcommand.SET_COMBINATION and len(p) >= 3:
                entries = [f"{(b >> 4) & 0xF}:{b & 0xF}" for b in p[3:]]
                return f"Modalità combinata porta {p[0]}: combinazione {p[2]} con modo:dataset {' '.join(entries)}"
            return f"Modalità combinata porta {p[0]}: {sub}"
        if t == MessageType.VIRTUAL_PORT_SETUP:
            return (f"Porta virtuale: collega {p[1]} + {p[2]}" if p[0] == 1 and len(p) >= 3
                    else f"Porta virtuale: scollega {p[1] if len(p) > 1 else '?'}")
        if t == MessageType.PORT_OUTPUT_COMMAND:
            port, flags, sub = p[0], p[1], p[2]
            name = OUTPUT_SUBCOMMANDS.get(sub, f"sottocomando 0x{sub:02X}")
            text = (f"Comando output porta {port}: {name}, avvio {'subito' if flags & 0x10 else 'in coda'}, "
                    f"feedback {'sì' if flags & 0x01 else 'no'}")
            if sub == 0x51 and len(p) > 3:
                text += f", modo {p[3]}, dati {p[4:].hex(' ')}"
            elif len(p) > 3:
                text += f", dati {p[3:].hex(' ')}"
            return text
        if t in (MessageType.FW_UPDATE_GO_INTO_BOOT_MODE, MessageType.FW_UPDATE_LOCK_MEMORY,
                 MessageType.FW_UPDATE_LOCK_STATUS_REQUEST):
            return f"{msg.type_name} (aggiornamento firmware: da non inviare) {p.hex(' ')}"
    except Exception as exc:  # pragma: no cover - decodifica fallita: byte grezzi
        return f"{msg.type_name}: {p.hex(' ')} (decodifica fallita: {exc})"
    return f"{msg.type_name}: {p.hex(' ')}"
