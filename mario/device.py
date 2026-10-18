"""
Comunicazione bidirezionale con LEGO Mario tramite Bluetooth LE (bleak su BlueZ).

Flusso tipico:
    dev = MarioDevice()
    await dev.connect()           # scansione + connessione + notifiche
    await dev.discover()          # proprieta' hub, porte, modalita', formati
    print(dev.report())
    await dev.subscribe(2, 0)     # notifiche sensore pantaloni
    dev.add_listener(lambda msg, text: print(text))
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

try:  # dbus-fast arriva con bleak su Linux; serve per agente di pairing e RemoveDevice
    from dbus_fast import BusType
    from dbus_fast import Message as DBusMessage  # alias: Message e' gia' quello di lwp3
    from dbus_fast import MessageType as DBusMessageType
    from dbus_fast.aio import MessageBus
    from dbus_fast.errors import DBusError
    from dbus_fast.service import ServiceInterface, method as dbus_method
    HAVE_DBUS = True
except ImportError:  # altri backend (macOS/Windows)
    HAVE_DBUS = False

from . import codes, lwp3
from .hubstate import HubState, ModeInfo, PortState  # noqa: F401  (riesportati per compatibilità)
from .lwp3 import (
    CombinedModeSubcommand, HubAction, HubAlertOperation, HubProperty, HubPropertyOperation,
    Message, MessageType, ModeInfoType, PortInfoType,
)

log = logging.getLogger("mario")
Listener = Callable[[Message, str], None]


class LWPError(Exception):
    """Mario ha risposto con un 'Generic Error Message'."""

    def __init__(self, command: int, code: int):
        self.command, self.code = command, code
        super().__init__(f"{lwp3.enum_name(lwp3.ErrorCode, code)} per il comando "
                         f"{lwp3.enum_name(MessageType, command)}")


# Lo stato dell'hub (porte, modi, formati, valori combinati) vive in `mario.hubstate`, senza bleak.


# ---------------------------------------------------------------------------
# Riconoscimento degli advertisement
# ---------------------------------------------------------------------------

MARIO_NAME_PREFIXES = ("mario", "luigi", "peach", "lego mario", "lego luigi", "lego peach")


def advertised_system_type(adv: AdvertisementData) -> Optional[int]:
    data = adv.manufacturer_data.get(lwp3.LEGO_COMPANY_ID)
    return data[1] if data and len(data) >= 2 else None


def is_lego_advertisement(adv: AdvertisementData) -> bool:
    uuids = [u.lower() for u in (adv.service_uuids or [])]
    return lwp3.LEGO_COMPANY_ID in adv.manufacturer_data or lwp3.LEGO_SERVICE_UUID in uuids


def is_mario_advertisement(adv: AdvertisementData) -> bool:
    system_type = advertised_system_type(adv)
    if system_type is not None:
        return system_type in codes.MARIO_SYSTEM_TYPES
    return is_lego_advertisement(adv) and (adv.local_name or "").lower().startswith(MARIO_NAME_PREFIXES)


def describe_advertisement(device: BLEDevice, adv: AdvertisementData) -> str:
    parts = [device.address, f"nome={adv.local_name!r}", f"RSSI={adv.rssi} dBm"]
    system_type = advertised_system_type(adv)
    if system_type is not None:
        parts.append(f"tipo=0x{system_type:02X} ({lwp3.SYSTEM_TYPE_NAMES.get(system_type, '?')})")
    data = adv.manufacturer_data.get(lwp3.LEGO_COMPANY_ID)
    if data:
        parts.append(f"manufacturer={data.hex(' ')} (pulsante {'premuto' if data[0] else 'rilasciato'})")
    return " | ".join(parts)



def bluez_address_type(device: BLEDevice) -> str:
    """Tipo di indirizzo ('public' o 'random') come registrato da BlueZ, se disponibile."""
    details = device.details if isinstance(device.details, dict) else {}
    return str(details.get("props", {}).get("AddressType", "?"))


async def remove_bluez_device(device: BLEDevice) -> bool:
    """Chiede a BlueZ di dimenticare il dispositivo (Adapter1.RemoveDevice). Solo backend BlueZ."""
    details = device.details if isinstance(device.details, dict) else {}
    path = details.get("path")
    if not path:
        return False
    try:
        from dbus_fast import BusType, Message
        from dbus_fast import MessageType as DBusMessageType
        from dbus_fast.aio import MessageBus
    except ImportError:
        return False
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception as exc:
        log.debug("bus di sistema non disponibile: %s", exc)
        return False
    try:
        reply = await bus.call(Message(destination="org.bluez", path=path.rsplit("/", 1)[0],
                                       interface="org.bluez.Adapter1", member="RemoveDevice",
                                       signature="o", body=[path]))
        if reply is None or reply.message_type != DBusMessageType.METHOD_RETURN:
            log.debug("RemoveDevice rifiutato: %s", reply.body if reply else None)
            return False
        return True
    except Exception as exc:
        log.debug("RemoveDevice fallito: %s", exc)
        return False
    finally:
        bus.disconnect()


# ---------------------------------------------------------------------------
# Pairing: Mario richiede un pairing "Just Works" (IO NoInputNoOutput, bonding) per
# scrivere i CCCD. Senza un agente che accetti, BlueZ chiede conferma all'agente di
# sistema (GNOME), che la nega, e Mario chiude la connessione. Verificato con btmon.
# L'agente accetta solo gli indirizzi passati ad allow() (il Mario che si sta collegando): finché
# l'app è aperta è l'agente predefinito, e con il pannello Bluetooth di GNOME aperto (PC
# accoppiabile) accettare tutto farebbe accoppiare senza conferma qualsiasi dispositivo vicino,
# anche una tastiera. Alla chiusura BlueZ torna da solo all'agente predefinito precedente
# (coda default_agents di src/agent.c, verificato sui sorgenti di BlueZ 5.82).
# ---------------------------------------------------------------------------

AGENT_PATH = "/it/lego/mario/agent"


def agent_address(device_path: str) -> str:
    """Indirizzo di un oggetto org.bluez.Device1 (`/org/bluez/hci0/dev_AA_BB_...` -> `AA:BB:...`)."""
    tail = device_path.rsplit("/", 1)[-1]
    return tail[4:].replace("_", ":").upper() if tail.startswith("dev_") else ""


if HAVE_DBUS:
    class _Agent1(ServiceInterface):
        """org.bluez.Agent1 con capacità NoInputNoOutput che accetta solo i dispositivi in `allowed`."""

        def __init__(self, allowed: set[str]):
            super().__init__("org.bluez.Agent1")
            self.allowed = allowed

        def _check(self, device: str, what: str) -> None:
            address = agent_address(device)
            if address not in self.allowed:
                log.warning("%s da %s rifiutata: l'agente accetta solo il dispositivo LEGO in collegamento",
                            what, address or device)
                raise DBusError("org.bluez.Error.Rejected", "non è il dispositivo LEGO in collegamento")

        @dbus_method()
        def Release(self):
            log.debug("agente rilasciato da BlueZ")

        @dbus_method()
        def RequestPinCode(self, device: "o") -> "s":
            self._check(device, "richiesta del PIN")
            return "0000"

        @dbus_method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            log.info("PIN da mostrare: %s", pincode)

        @dbus_method()
        def RequestPasskey(self, device: "o") -> "u":
            self._check(device, "richiesta della passkey")
            return 0

        @dbus_method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            log.info("passkey da mostrare: %06d", passkey)

        @dbus_method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            self._check(device, "conferma del pairing")
            log.info("conferma pairing accettata automaticamente (passkey %06d)", passkey)

        @dbus_method()
        def RequestAuthorization(self, device: "o"):
            self._check(device, "autorizzazione del pairing")
            log.info("autorizzazione pairing accettata automaticamente")

        @dbus_method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            self._check(device, f"autorizzazione del servizio {uuid}")
            log.debug("servizio %s autorizzato", uuid)

        @dbus_method()
        def Cancel(self):
            log.debug("richiesta di pairing annullata da BlueZ")


class BlueZAgent:
    """Registra l'agente di pairing come predefinito per la durata della sessione."""

    def __init__(self):
        self.bus = None
        self.registered = False
        self.allowed: set[str] = set()  # indirizzi accettati; condiviso con l'oggetto esportato

    def allow(self, address: str) -> None:
        self.allowed.add(address.upper())

    async def start(self) -> bool:
        if not HAVE_DBUS:
            return False
        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self.bus.export(AGENT_PATH, _Agent1(self.allowed))
            for member, signature, body in (("RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"]),
                                            ("RequestDefaultAgent", "o", [AGENT_PATH])):
                reply = await self.bus.call(DBusMessage(destination="org.bluez", path="/org/bluez",
                                                    interface="org.bluez.AgentManager1",
                                                    member=member, signature=signature, body=body))
                if reply.message_type != DBusMessageType.METHOD_RETURN:
                    log.warning("%s rifiutato da BlueZ: %s", member, reply.body)
                    if member == "RegisterAgent":
                        return False
            self.registered = True
            return True
        except Exception as exc:
            log.warning("agente di pairing non disponibile: %s", exc)
            return False

    async def stop(self) -> None:
        if self.bus is None:
            return
        try:
            if self.registered:
                await asyncio.wait_for(self.bus.call(DBusMessage(
                    destination="org.bluez", path="/org/bluez", interface="org.bluez.AgentManager1",
                    member="UnregisterAgent", signature="o", body=[AGENT_PATH])), timeout=5.0)
        except Exception as exc:
            log.debug("UnregisterAgent: %s", exc)
        finally:
            try:
                self.bus.disconnect()
            except Exception:
                pass
            self.bus = None
            self.registered = False

    async def device_call(self, path: str, member: str, timeout: float = 45.0) -> tuple[bool, str]:
        """Chiama un metodo di org.bluez.Device1 (es. Pair, CancelPairing)."""
        reply = await asyncio.wait_for(self.bus.call(DBusMessage(
            destination="org.bluez", path=path, interface="org.bluez.Device1", member=member)), timeout)
        if reply.message_type == DBusMessageType.METHOD_RETURN:
            return True, ""
        detail = reply.body[0] if reply.body else ""
        return False, f"{reply.error_name}: {detail}"

    async def device_property(self, path: str, name: str):
        from dbus_fast import Variant  # noqa: F401  (solo per chiarezza del tipo di ritorno)
        reply = await asyncio.wait_for(self.bus.call(DBusMessage(
            destination="org.bluez", path=path, interface="org.freedesktop.DBus.Properties",
            member="Get", signature="ss", body=["org.bluez.Device1", name])), 5.0)
        if reply.message_type == DBusMessageType.METHOD_RETURN and reply.body:
            return reply.body[0].value
        return None


def _connect_hint(error: Optional[BaseException]) -> str:
    """Testo dell'errore di connessione con un suggerimento per i casi noti."""
    text = (str(error) or type(error).__name__) if error else "errore sconosciuto"
    if ("abort-by-local" in text or "discover services" in text
            or isinstance(error, asyncio.TimeoutError)):
        text += (" | BlueZ interrompe il collegamento LE subito dopo averlo stabilito: di solito è colpa "
                 "dell'adattatore (autosospensione USB, Wi-Fi a 2,4 GHz sullo stesso chip). Controllo "
                 "automatico: ./run.sh doctor; vedi anche il README, sezione 'Problemi di connessione'.")
    return text

# ---------------------------------------------------------------------------
# Il dispositivo
# ---------------------------------------------------------------------------

class MarioDevice:
    def __init__(self, address: Optional[str] = None, *, any_lego: bool = False, debug: bool = False,
                 adapter: Optional[str] = None, pair: bool = True):
        self.address = address
        self.pair_enabled = pair     # esegue il pairing Just Works se Mario non è ancora associato
        self.agent = BlueZAgent()
        self.bonded: Optional[bool] = None
        self.adapter = adapter  # es. "hci1" (solo BlueZ); None = adattatore predefinito
        self.write_timeout = 5.0   # secondi massimi per una scrittura GATT con risposta
        self.any_lego = any_lego
        self.debug = debug
        self.client: Optional[BleakClient] = None
        self.ble_device: Optional[BLEDevice] = None
        self.gatt: list[dict] = []   # servizi e caratteristiche GATT visti da bleak alla connessione
        self.state = HubState()
        self.disconnected = asyncio.Event()
        self._waiters: list[tuple[Callable[[Message], bool], asyncio.Future]] = []
        self._listeners: list[Listener] = []
        self._write_lock = asyncio.Lock()

    # ----- scansione e connessione -------------------------------------------------

    @staticmethod
    async def scan(duration: float = 10.0, any_lego: bool = False,
                   adapter: Optional[str] = None) -> list[tuple[BLEDevice, AdvertisementData]]:
        """Elenca i dispositivi LEGO Mario (o tutti gli hub LEGO) visibili."""
        kwargs = {"adapter": adapter} if adapter else {}
        found = await BleakScanner.discover(timeout=duration, return_adv=True, **kwargs)
        result = []
        for device, adv in found.values():
            if is_mario_advertisement(adv) or (any_lego and is_lego_advertisement(adv)):
                result.append((device, adv))
        return result

    def _backend_kwargs(self) -> dict:
        return {"adapter": self.adapter} if self.adapter else {}

    def _matches(self, device: BLEDevice, adv: AdvertisementData) -> bool:
        if self.address:
            return device.address.lower() == self.address.lower()
        return is_mario_advertisement(adv) or (self.any_lego and is_lego_advertisement(adv))

    async def _scan_for_device(self, timeout: float) -> tuple[BleakScanner, BLEDevice]:
        """Avvia uno scanner e attende Mario. Lo scanner viene restituito ancora attivo:
        il chiamante lo ferma subito prima di connettersi (vedi connect())."""
        loop = asyncio.get_running_loop()
        found: asyncio.Future = loop.create_future()

        def on_detection(device: BLEDevice, adv: AdvertisementData) -> None:
            if not found.done() and self._matches(device, adv):
                found.set_result(device)

        scanner = BleakScanner(on_detection, **self._backend_kwargs())
        await scanner.start()
        try:
            device = await asyncio.wait_for(found, timeout)
        except asyncio.TimeoutError:
            await scanner.stop()
            raise BleakError("Mario non trovato: accendilo e premi il tasto Bluetooth sulla schiena "
                             "(l'icona Bluetooth sul petto deve lampeggiare).")
        except BaseException:
            await scanner.stop()
            raise
        return scanner, device

    async def find(self, timeout: float = 30.0) -> BLEDevice:
        """Cerca Mario senza connettersi (per mostrare nome e indirizzo)."""
        scanner, device = await self._scan_for_device(timeout)
        await scanner.stop()
        self.ble_device = device
        self.address = device.address
        return device

    async def connect(self, timeout: float = 30.0, retries: int = 3,
                      progress: Optional[Callable[[str], None]] = None) -> None:
        """Cerca Mario e si connette; a ogni tentativo rifa' la scansione da zero."""
        say = progress or (lambda text: None)
        last_error: Optional[BaseException] = None
        await self._release_client()  # riconnessione (monitor, shell): il client di prima va chiuso
        if self.pair_enabled and not self.agent.registered:
            if not await self.agent.start():
                say("Attenzione: agente di pairing non registrato; se Mario chiede il pairing, "
                    "la conferma arriverà all'agente di sistema (GNOME)")
        for attempt in range(1, retries + 1):
            try:
                scanner, device = await self._scan_for_device(timeout if attempt == 1 else min(timeout, 20.0))
            except BleakError:
                if last_error is None:
                    raise
                raise BleakError(f"connessione fallita ({_connect_hint(last_error)}) e Mario non è più visibile: "
                                 "premi di nuovo il tasto Bluetooth e riprova") from last_error
            self.ble_device = device
            self.address = device.address
            self.agent.allow(device.address)  # l'agente accetta il pairing solo da questo Mario
            say(f"Trovato {device.name!r} [{device.address}] (indirizzo {bluez_address_type(device)}), "
                f"mi connetto (tentativo {attempt}/{retries})...")
            # Fermiamo la scansione PRIMA di connetterci. Se resta attiva, BlueZ la riprende
            # appena il link e' su (finestra di scansione al 100%) e il controller Broadcom
            # perde gli eventi di connessione: latenze ACL di 200-400 ms e caduta del link per
            # supervision timeout (420 ms). Verificato con btmon l'11/10/2026.
            try:
                await scanner.stop()
            except Exception as exc:
                log.debug("stop scanner: %s", exc)
            client = BleakClient(device, disconnected_callback=self._on_disconnect, timeout=20.0,
                                 **self._backend_kwargs())
            try:
                if self.pair_enabled and self.agent.registered:
                    await self._ensure_bond(device, say)
                await asyncio.wait_for(client.connect(), timeout=45.0)
            except (BleakError, asyncio.TimeoutError, OSError) as exc:
                last_error = exc
                log.warning("Tentativo di connessione %d/%d fallito: %s", attempt, retries,
                            str(exc) or type(exc).__name__)
                if "br-connection" in str(exc):
                    # BlueZ ha scelto il Bluetooth classico (BR/EDR) per un dispositivo solo LE:
                    # eliminando il record, al prossimo advertisement viene ricreato come LE.
                    say("BlueZ ha tentato una connessione BR/EDR: rimuovo il record del dispositivo e riprovo via LE")
                    await remove_bluez_device(device)
                await asyncio.sleep(1.0)
                continue
            self.client = client
            break
        else:
            raise BleakError(f"connessione fallita dopo {retries} tentativi: {_connect_hint(last_error)}")
        self.disconnected.clear()
        self.gatt = self._read_gatt()
        say("Collegato: attivo le notifiche LWP3 (StartNotify)...")
        try:
            await asyncio.wait_for(
                self.client.start_notify(lwp3.LEGO_CHARACTERISTIC_UUID, self._on_notify), timeout=15.0)
        except asyncio.TimeoutError:
            # BlueZ non ha risposto alla scrittura del CCCD: proviamo la via alternativa (AcquireNotify)
            say("Nessuna risposta in 15 s: riprovo con AcquireNotify...")
            try:
                await asyncio.wait_for(
                    self.client.start_notify(lwp3.LEGO_CHARACTERISTIC_UUID, self._on_notify,
                                             bluez={"use_start_notify": False}), timeout=10.0)
            except (asyncio.TimeoutError, BleakError) as exc:
                await self.disconnect()
                raise BleakError("Mario è collegato ma BlueZ non riesce ad attivare le notifiche "
                                 f"(StartNotify: timeout 15 s; AcquireNotify: {exc or 'timeout 10 s'}). "
                                 "Serve una cattura btmon per vedere la scrittura del CCCD.") from None
        say("Notifiche attive: attendo l'elenco delle porte...")
        await self.wait_for_ports()

    async def _ensure_bond(self, device: BLEDevice, say: Callable[[str], None]) -> None:
        """Se Mario non è associato, esegue Device1.Pair() (Just Works, con bonding) prima della connessione."""
        details = device.details if isinstance(device.details, dict) else {}
        path = details.get("path")
        if not path:
            return
        try:
            bonded = await self.agent.device_property(path, "Bonded")
            paired = await self.agent.device_property(path, "Paired")
        except Exception as exc:
            log.debug("lettura stato pairing fallita: %s", exc)
            bonded = paired = None
        if bonded or paired:
            self.bonded = True
            return
        say("Mario non è ancora associato a questo PC: eseguo il pairing Just Works (automatico)...")
        ok, error = await self.agent.device_call(path, "Pair", timeout=60.0)
        if ok or "AlreadyExists" in error:
            self.bonded = True
            say("Pairing completato: Mario è associato (bond salvato da BlueZ).")
            return
        if "InProgress" in error:
            say("Pairing già in corso, proseguo...")
            return
        raise BleakError(f"pairing con Mario fallito: {error}")

    async def unpair(self, device: Optional[BLEDevice] = None) -> bool:
        """Rimuove l'associazione (RemoveDevice) del dispositivo trovato o di quello passato."""
        device = device or self.ble_device
        if device is None:
            device = await self.find()
        return await remove_bluez_device(device)

    async def disconnect(self) -> None:
        await self.agent.stop()
        await self._release_client()

    async def _release_client(self) -> None:
        """Chiude il BleakClient anche quando il link è già caduto.

        Se è Mario a chiudere (spegnimento, sonno, azione DISCONNECT), bleak 3 toglie il proprio watcher
        ma lascia aperta la connessione D-Bus privata del client. BlueZ tiene in vita la sessione di
        StartNotify di quella connessione e alla riconnessione la riattiva (register_notify in
        src/gatt-client.c, BlueZ 5.82): con la sessione del client nuovo ogni notifica arrivava due
        volte (live-checks del 12/10, re-checks del 14/10). client.disconnect() chiude anche la
        connessione D-Bus, e con lei la vecchia sessione.
        """
        client, self.client = self.client, None
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10.0)
        except Exception as exc:  # BleakError, timeout, controlli interni di bleak su un client già caduto
            log.debug("disconnect: %s", exc)

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected and not self.disconnected.is_set()

    def _on_disconnect(self, _client: BleakClient) -> None:
        log.info("Mario si è disconnesso")
        self.disconnected.set()
        for _, fut in self._waiters:
            if not fut.done():
                fut.set_exception(BleakError("disconnesso"))
        self._waiters.clear()

    # ----- I/O grezzo -------------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def send(self, data: bytes) -> None:
        """Invia un messaggio LWP3 completo (header incluso)."""
        if self.client is None or not self.client.is_connected:
            raise BleakError("non connesso")
        if self.debug:
            print(f"      TX {bytes(data).hex(' ')}")
        async with self._write_lock:
            try:
                await asyncio.wait_for(
                    self.client.write_gatt_char(lwp3.LEGO_CHARACTERISTIC_UUID, bytes(data), response=True),
                    timeout=self.write_timeout)
            except asyncio.TimeoutError:
                raise BleakError(f"nessuna conferma dalla scrittura GATT entro {self.write_timeout:g} s: "
                                 "link Bluetooth instabile") from None
        self.state.note_sent(bytes(data))  # es. definizione della modalità combinata

    async def send_hex(self, text: str) -> None:
        await self.send(bytes.fromhex(text.replace(" ", "").replace(":", "")))

    async def send_message(self, msg_type: int, payload: bytes = b"") -> None:
        """Costruisce l'header e invia: comodo per sperimentare tipi di messaggio arbitrari."""
        await self.send(lwp3.build(msg_type, payload))

    async def request(self, data: bytes, predicate: Callable[[Message], bool], timeout: float = 3.0) -> Message:
        """Invia e attende il primo messaggio che soddisfa `predicate` (o un errore generico)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        entry = (predicate, fut)
        self._waiters.append(entry)
        try:
            await self.send(data)
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            if entry in self._waiters:
                self._waiters.remove(entry)
        if msg.msg_type == MessageType.GENERIC_ERROR:
            raise LWPError(msg.payload[0], msg.payload[1])
        return msg

    @staticmethod
    def _expect(response_type: int, prefix: bytes, request_type: int) -> Callable[[Message], bool]:
        def predicate(msg: Message) -> bool:
            if msg.msg_type == response_type and msg.payload.startswith(prefix):
                return True
            return (msg.msg_type == MessageType.GENERIC_ERROR and len(msg.payload) >= 2
                    and msg.payload[0] == request_type)
        return predicate

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        for msg in lwp3.parse_all(bytes(data)):
            if self.debug:
                print(f"      RX {msg.raw.hex(' ')}")
            try:
                self._update_state(msg)
            except Exception as exc:  # non deve mai bloccare la ricezione
                log.debug("aggiornamento stato fallito per %s: %s", msg, exc)
            for predicate, fut in list(self._waiters):
                if not fut.done() and predicate(msg):
                    fut.set_result(msg)
                    self._waiters.remove((predicate, fut))
                    break
            text = self.describe(msg)
            for listener in list(self._listeners):
                try:
                    listener(msg, text)
                except Exception:
                    log.exception("listener fallito")

    def _update_state(self, msg: Message) -> None:
        self.state.update(msg)

    def _port_lookup(self, port: int):
        return self.state.port_lookup(port)

    def describe(self, msg: Message) -> str:
        return self.state.describe(msg)

    def event(self, msg: Message, text: Optional[str] = None) -> dict:
        """Dizionario dell'evento (porta, modo, valori, decodifica) come lo usa la dashboard."""
        return self.state.event(msg, text if text is not None else self.describe(msg))

    # ----- proprieta' e azioni dell'hub ------------------------------------------------

    async def get_property_raw(self, prop: int, timeout: float = 3.0) -> bytes:
        msg = await self.request(lwp3.hub_property_request(prop),
                                 self._expect(MessageType.HUB_PROPERTIES,
                                              bytes([prop, HubPropertyOperation.UPDATE]),
                                              MessageType.HUB_PROPERTIES), timeout)
        return bytes(msg.payload[2:])

    async def get_property(self, prop: int, timeout: float = 3.0) -> Any:
        return lwp3.decode_hub_property(prop, await self.get_property_raw(prop, timeout))

    async def set_property(self, prop: int, value: bytes) -> None:
        await self.send(lwp3.hub_property_set(prop, value))

    async def property_updates(self, prop: int, enable: bool = True) -> None:
        await self.send(lwp3.hub_property_updates(prop, enable))

    async def reset_property(self, prop: int) -> None:
        await self.send(lwp3.hub_property_reset(prop))

    async def set_name(self, name: str) -> str:
        await self.set_property(HubProperty.ADVERTISING_NAME, name.encode("utf-8")[:14])
        return await self.get_property(HubProperty.ADVERTISING_NAME)

    async def get_volume(self) -> int:
        return await self.get_property(HubProperty.VOLUME)

    async def set_volume(self, level: int) -> int:
        level = max(0, min(100, int(level)))
        await self.set_property(HubProperty.VOLUME, bytes([level]))
        return await self.get_volume()

    async def hub_action(self, action: int) -> None:
        await self.send(lwp3.hub_action(action))

    async def switch_off(self) -> None:
        await self.hub_action(HubAction.SWITCH_OFF)

    async def request_disconnect(self) -> None:
        await self.hub_action(HubAction.DISCONNECT)

    async def alert(self, alert_type: int, operation: int = HubAlertOperation.REQUEST_UPDATE,
                    timeout: float = 3.0) -> Optional[Message]:
        data = lwp3.hub_alert(alert_type, operation)
        if operation == HubAlertOperation.REQUEST_UPDATE:
            return await self.request(data, self._expect(MessageType.HUB_ALERTS, bytes([alert_type]),
                                                         MessageType.HUB_ALERTS), timeout)
        await self.send(data)
        return None

    # ----- porte --------------------------------------------------------------------

    async def request_port_information(self, port: int, info_type: int = PortInfoType.MODE_INFO,
                                       timeout: float = 3.0):
        msg = await self.request(lwp3.port_information_request(port, info_type),
                                 self._expect(MessageType.PORT_INFORMATION, bytes([port, info_type]),
                                              MessageType.PORT_INFORMATION_REQUEST), timeout)
        return lwp3.decode_port_information(msg.payload)

    async def request_mode_information(self, port: int, mode: int, info_type: int, timeout: float = 3.0) -> Any:
        msg = await self.request(lwp3.port_mode_information_request(port, mode, info_type),
                                 self._expect(MessageType.PORT_MODE_INFORMATION, bytes([port, mode, info_type]),
                                              MessageType.PORT_MODE_INFORMATION_REQUEST), timeout)
        return lwp3.decode_port_mode_information(msg.payload)[3]

    async def request_value(self, port: int, timeout: float = 3.0) -> bytes:
        """Chiede il valore corrente di una porta (Port Information Request, info type 0)."""
        msg = await self.request(lwp3.port_information_request(port, PortInfoType.PORT_VALUE),
                                 self._expect(MessageType.PORT_VALUE_SINGLE, bytes([port]),
                                              MessageType.PORT_INFORMATION_REQUEST), timeout)
        return bytes(msg.payload[1:])

    async def subscribe(self, port: int, mode: int, delta: int = 1, notify: bool = True,
                        timeout: float = 3.0) -> Message:
        """Mette la porta nel modo richiesto e attiva le notifiche sui cambi di valore."""
        return await self.request(lwp3.port_input_format_setup(port, mode, delta, notify),
                                  self._expect(MessageType.PORT_INPUT_FORMAT_SINGLE, bytes([port, mode]),
                                               MessageType.PORT_INPUT_FORMAT_SETUP_SINGLE), timeout)

    async def unsubscribe(self, port: int, mode: int) -> Message:
        return await self.subscribe(port, mode, delta=1, notify=False)

    async def set_combined_mode(self, port: int, combination_index: int,
                                mode_datasets: list[tuple[int, int]], delta: int = 1,
                                deltas: Optional[dict[int, int]] = None) -> None:
        """Modalita' combinata (porte 'combinabili'): piu' modi attivi insieme sulla stessa porta.
        `deltas` permette un delta diverso per modo (es. {0: 4, 1: 1} per accelerometro e gesti)."""
        await self.send(lwp3.port_input_format_setup_combined(port, CombinedModeSubcommand.LOCK))
        for mode in sorted({m for m, _ in mode_datasets}):
            await self.send(lwp3.port_input_format_setup(port, mode, (deltas or {}).get(mode, delta), True))
        await self.send(lwp3.port_combination_set(port, combination_index, mode_datasets))
        await self.send(lwp3.port_input_format_setup_combined(
            port, CombinedModeSubcommand.UNLOCK_MULTI_UPDATE_ENABLED))

    async def subscribe_combined_default(self, port: int) -> Optional[list[int]]:
        """Modalità combinata con la prima combinazione annunciata dalla porta, tutti i dataset di ogni modo
        (accelerometro grezzo con delta 4, gli altri 1): sono `monitor --combined` e il pulsante della
        dashboard. Ritorna i modi attivati, None se la porta non è combinabile."""
        ps = self.state.ports.get(port)
        if not (ps and ps.info and ps.info.capabilities & 0x04 and ps.combinations):
            return None
        modes = list(ps.combinations[0])
        entries, deltas = [], {}
        for mode in modes:
            vf = ps.value_format(mode)
            entries += [(mode, ds) for ds in range(vf.datasets if vf else 1)]
            deltas[mode] = 4 if (ps.io_type == codes.IO_MARIO_ACCELEROMETER and mode == 0) else 1
        await self.set_combined_mode(port, 0, entries, deltas=deltas)
        return modes

    async def reset_combined_mode(self, port: int) -> None:
        await self.send(lwp3.port_input_format_setup_combined(port, CombinedModeSubcommand.RESET_SENSOR))

    async def port_output(self, port: int, subcommand: int, payload: bytes = b"",
                          startup: int = 0x1, completion: int = 0x1) -> None:
        await self.send(lwp3.port_output_command(port, subcommand, payload, startup, completion))

    # ----- scoperta delle funzioni --------------------------------------------------

    async def wait_for_ports(self, settle: float = 0.6, timeout: float = 3.0) -> None:
        """Attende gli 'Hub Attached I/O' che l'hub manda spontaneamente dopo l'attivazione delle notifiche."""
        deadline = time.monotonic() + timeout
        last_count, last_change = -1, time.monotonic()
        while time.monotonic() < deadline:
            count = len(self.state.ports)
            if count != last_count:
                last_count, last_change = count, time.monotonic()
            elif count > 0 and time.monotonic() - last_change > settle:
                break
            await asyncio.sleep(0.05)
        if not self.state.ports:
            await self.probe_ports()

    async def probe_ports(self, ports=range(0, 16)) -> None:
        """Interroga le porte una per una: utile se l'hub non annuncia i dispositivi collegati."""
        for port in ports:
            try:
                await self.request_port_information(port, timeout=0.8)
            except (LWPError, asyncio.TimeoutError):
                continue

    async def discover(self, full: bool = True, progress: Optional[Callable[[str], None]] = None) -> HubState:
        """Legge tutte le proprieta' dell'hub e, per ogni porta, tutte le modalita' con i loro formati."""
        say = progress or (lambda text: None)
        say("Lettura delle proprietà dell'hub...")
        for prop in HubProperty:
            try:
                await self.get_property_raw(prop, timeout=1.5)
            except (LWPError, asyncio.TimeoutError) as exc:
                log.debug("proprietà %s non disponibile: %s", prop.name, exc)
        for port in sorted(self.state.ports):
            await self.discover_port(port, full, say)
        return self.state

    async def discover_port(self, port: int, full: bool = True,
                            progress: Optional[Callable[[str], None]] = None) -> Optional[PortState]:
        """Legge informazioni, modalita' e formati di una singola porta."""
        say = progress or (lambda text: None)
        ps = self.state.port(port)
        say(f"Porta {port} ({ps.io_name}): lettura delle modalità...")
        try:
            info = await self.request_port_information(port)
        except (LWPError, asyncio.TimeoutError) as exc:
            log.debug("porta %d: nessuna informazione (%s)", port, exc)
            return None
        if info.capabilities & 0x04:
            try:
                await self.request_port_information(port, PortInfoType.POSSIBLE_MODE_COMBINATIONS)
            except (LWPError, asyncio.TimeoutError):
                pass
        info_types = [ModeInfoType.NAME, ModeInfoType.VALUE_FORMAT]
        if full:
            info_types += [ModeInfoType.RAW, ModeInfoType.PCT, ModeInfoType.SI,
                           ModeInfoType.SYMBOL, ModeInfoType.MAPPING]
        for mode in info.all_modes:
            for info_type in info_types:
                try:
                    await self.request_mode_information(port, mode, info_type, timeout=1.5)
                except (LWPError, asyncio.TimeoutError):
                    pass
        return ps

    def _port_lines(self, ps: PortState) -> list[str]:
        port = ps.port
        lines: list[str] = []
        lines.append(f"Porta {port}: {ps.io_name} (IO type 0x{ps.io_type:04X})"
                     + (f" HW {ps.hw_version} SW {ps.sw_version}" if ps.hw_version else ""))
        if ps.virtual_ports:
            lines.append(f"  porta virtuale di {ps.virtual_ports[0]} + {ps.virtual_ports[1]}")
        if ps.info:
            lines.append(f"  capacità: {', '.join(ps.info.capability_names) or '-'} | "
                         f"{ps.info.mode_count} modalità | input {ps.info.input_mode_list} | "
                         f"output {ps.info.output_mode_list}")
        if ps.combinations:
            lines.append(f"  combinazioni possibili: {ps.combinations}")
        for m, mi in sorted(ps.modes.items()):
            fmt = str(mi.value_format) if mi.value_format else "formato ?"
            lines.append(f"  modo {m}: {mi.name or '?':<10} {fmt}")
            details = []
            if mi.raw:
                details.append(f"raw {mi.raw[0]:g}..{mi.raw[1]:g}")
            if mi.pct:
                details.append(f"pct {mi.pct[0]:g}..{mi.pct[1]:g}")
            if mi.si:
                details.append(f"si {mi.si[0]:g}..{mi.si[1]:g}")
            if mi.symbol:
                details.append(f"unità '{mi.symbol}'")
            if mi.mapping:
                details.append(f"mapping in={lwp3.mapping_names(mi.mapping[0]) or '-'} "
                               f"out={lwp3.mapping_names(mi.mapping[1]) or '-'}")
            if details:
                lines.append("          " + " | ".join(details))
        hint = codes.port_hint(ps.io_type)
        if hint:
            lines.append(f"  significato noto: {hint}")
        return lines

    def _read_gatt(self) -> list[dict]:
        """Tabella GATT (servizi, caratteristiche, descrittori) come la espone bleak dopo la connessione.
        Solo lettura della tabella: nessuna caratteristica viene letta o scritta (il servizio OAD resta intatto)."""
        services = getattr(self.client, "services", None)
        out: list[dict] = []
        if not services:
            return out
        try:
            for svc in services:
                chars = []
                for ch in getattr(svc, "characteristics", None) or []:
                    chars.append({"uuid": str(ch.uuid).lower(), "handle": getattr(ch, "handle", None),
                                  "properties": [str(x) for x in (getattr(ch, "properties", None) or [])],
                                  "descriptors": [{"uuid": str(d.uuid).lower(), "handle": getattr(d, "handle", None)}
                                                  for d in (getattr(ch, "descriptors", None) or [])]})
                out.append({"uuid": str(svc.uuid).lower(), "handle": getattr(svc, "handle", None),
                            "characteristics": chars})
        except Exception as exc:  # la tabella GATT è solo informativa
            log.debug("lettura della tabella GATT fallita: %s", exc)
        return out

    def gatt_lines(self) -> list[str]:
        """Righe del report per la tabella GATT (con il nome dei servizi noti e l'avviso sul servizio OAD)."""
        lines: list[str] = []
        if not self.gatt:
            return ["  (tabella GATT non disponibile)"]
        for svc in self.gatt:
            handle = f"0x{svc['handle']:04X} " if svc.get("handle") is not None else ""
            lines.append(f"  {handle}{svc['uuid']}  {lwp3.gatt_name(svc['uuid'])}")
            for ch in svc.get("characteristics", []):
                chandle = f"0x{ch['handle']:04X} " if ch.get("handle") is not None else ""
                props = ", ".join(ch.get("properties") or []) or "-"
                lines.append(f"      car. {chandle}{ch['uuid']}  {lwp3.gatt_name(ch['uuid'])} [{props}]")
                for d in ch.get("descriptors", []):
                    dhandle = f"0x{d['handle']:04X} " if d.get("handle") is not None else ""
                    lines.append(f"          desc. {dhandle}{d['uuid']}  {lwp3.gatt_name(d['uuid'])}")
        return lines

    def report(self) -> str:
        st = self.state
        bond = {True: "sì", False: "no", None: "sconosciuto"}[self.bonded]
        lines = ["=== Collegamento ===",
                 f"  indirizzo: {self.ble_device.address if self.ble_device else self.address or '-'}"
                 f" | associato (bond): {bond} | connesso: {'sì' if self.connected else 'no'}",
                 "", "=== Proprietà dell'hub ==="]
        for prop in HubProperty:
            if prop in st.properties:
                lines.append(f"  {prop.name:<18} (0x{prop:02X}): {st.property_text(prop)}")
        for prop, data in sorted(st.properties.items()):
            if prop not in HubProperty.__members__.values():
                lines.append(f"  proprietà 0x{prop:02X}: {data.hex(' ')}")
        lines += ["", "=== Porte e sensori ==="]
        if not st.ports:
            lines.append("  (nessuna porta annunciata)")
        for port, ps in sorted(st.ports.items()):
            lines += self._port_lines(ps)
        lines += ["", "=== Servizi GATT ==="] + self.gatt_lines()
        return "\n".join(lines)

    def port_report(self, port: int) -> str:
        ps = self.state.ports.get(port)
        if ps is None:
            return f"Porta {port}: nessun dispositivo annunciato"
        return "\n".join(self._port_lines(ps))

    def to_dict(self) -> dict:
        st = self.state
        props = {}
        for prop, data in sorted(st.properties.items()):
            key = lwp3.enum_name(HubProperty, prop)
            props[key] = {"id": f"0x{prop:02X}", "raw": data.hex(),
                          "value": lwp3.decode_hub_property(prop, data)}
        return {"address": self.ble_device.address if self.ble_device else self.address,
                "bonded": self.bonded,
                "properties": props,
                "gatt": self.gatt,
                "ports": {port: ps.to_dict() for port, ps in sorted(st.ports.items())}}
