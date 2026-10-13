"""
Thread Bluetooth della dashboard: possiede il loop asyncio e il MarioDevice.

Flask è sincrono e multi-thread, bleak è asyncio e vuole un solo loop: le richieste HTTP
passano da `call()` (run_coroutine_threadsafe) e gli eventi di Mario vengono spinti in una
coda per i client SSE e nel database.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from bleak.exc import BleakError

from .. import codes, lwp3
from ..device import MarioDevice, LWPError, describe_advertisement, advertised_system_type
from ..lwp3 import HubProperty, HubPropertyOperation, MessageType
from .db import Database

log = logging.getLogger("mario.web")

INTERESTING = {MessageType.PORT_VALUE_SINGLE, MessageType.PORT_VALUE_COMBINED, MessageType.HUB_PROPERTIES,
               MessageType.HUB_ATTACHED_IO, MessageType.HUB_ACTIONS, MessageType.HUB_ALERTS, MessageType.GENERIC_ERROR}


class BleWorker:
    def __init__(self, db: Database, adapter: Optional[str] = None, pair: bool = True):
        self.db = db
        self.adapter = adapter
        self.pair = pair
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="ble-worker", daemon=True)
        self._lock = threading.Lock()
        self.device: Optional[MarioDevice] = None
        self.device_row_id: Optional[int] = None
        self.state = "disconnected"
        self.last_error = ""
        self.progress: deque = deque(maxlen=40)
        self.events: deque = deque(maxlen=3000)
        self.seq = 0
        self.battery: Optional[int] = None
        self.port_last: dict[int, dict] = {}
        self.subscriptions: dict[int, dict] = {}
        self.last_scan: list[dict] = []
        self.last_scan_time: Optional[float] = None
        self._streams: list[queue.Queue] = []

    # ----- infrastruttura -------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self, timeout: float = 5.0) -> None:
        """Ferma il loop del worker (test e chiusura ordinata): annulla i task in attesa e aspetta il thread."""
        def _stop():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            self.loop.call_soon(self.loop.stop)
        if self._thread.is_alive():
            self.loop.call_soon_threadsafe(_stop)
            self._thread.join(timeout)

    def call(self, factory: Callable[[], Any], timeout: float = 90.0) -> Any:
        """Esegue una coroutine nel loop del worker e ne attende il risultato."""
        future = asyncio.run_coroutine_threadsafe(factory(), self.loop)
        return future.result(timeout)

    def _say(self, text: str) -> None:
        self.progress.append(f"[{time.strftime('%H:%M:%S')}] {text}")
        self._push({"ts": time.time(), "type": "STATUS", "text": text, "raw": "", "interesting": True})

    def _set_state(self, state: str) -> None:
        self.state = state
        self._push({"ts": time.time(), "type": "STATE", "text": state, "raw": "", "interesting": True,
                    "state": state})

    # ----- eventi ------------------------------------------------------------------------

    def _push(self, ev: dict) -> None:
        with self._lock:
            self.seq += 1
            ev["seq"] = self.seq
            self.events.append(ev)
            streams = list(self._streams)
        for q in streams:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def push(self, ev: dict) -> None:
        """Inserisce nel flusso un evento prodotto da altri componenti (es. l'assistente)."""
        self._push(ev)

    def open_stream(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._streams.append(q)
        return q

    def close_stream(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._streams:
                self._streams.remove(q)

    def events_since(self, since: int = 0, limit: int = 200, port: Optional[int] = None,
                     only_interesting: bool = True) -> list[dict]:
        with self._lock:
            items = [e for e in self.events if e["seq"] > since
                     and (port is None or e.get("port") == port)
                     and (not only_interesting or e.get("interesting"))]
        return items[-limit:]

    def _on_message(self, msg: lwp3.Message, text: str) -> None:
        """Listener registrato sul MarioDevice (gira nel thread del worker)."""
        dev = self.device
        ev: dict = {"ts": time.time(), "type": msg.type_name, "text": text, "raw": msg.raw.hex(" "),
                    "interesting": msg.msg_type in INTERESTING}
        p = msg.payload
        try:
            if dev is not None:
                ev.update(dev.state.event(msg, text, ts=ev["ts"]))
            if msg.msg_type == MessageType.PORT_VALUE_SINGLE and dev is not None and p:
                port, data = p[0], bytes(p[1:])
                self.port_last[port] = {"ts": ev["ts"], "raw": data.hex(" "), "text": ev.get("decoded") or text,
                                        "mode": ev.get("mode"), "values": ev.get("values") or []}
                self._track_accessories(ev.get("io_type"), ev.get("mode"), data)
            elif msg.msg_type == MessageType.PORT_VALUE_COMBINED and dev is not None and p:
                port = p[0]
                modes = ev.get("modes") or {}
                self.port_last[port] = {"ts": ev["ts"], "raw": " / ".join(m["data"] for m in modes.values()),
                                        "text": ev.get("decoded") or text, "mode": None,
                                        "values": [m["values"] for m in modes.values()]}
                for mode_text, m in modes.items():
                    self._track_accessories(ev.get("io_type"), int(mode_text), bytes.fromhex(m["data"]))
            elif msg.msg_type == MessageType.HUB_PROPERTIES and len(p) >= 2 and p[1] == HubPropertyOperation.UPDATE:
                if p[0] == HubProperty.BATTERY_VOLTAGE:
                    self.battery = int(p[2]) if len(p) > 2 else None
            elif msg.msg_type == MessageType.PORT_INPUT_FORMAT_SINGLE and len(p) >= 7:
                port, mode, notify = p[0], p[1], bool(p[6])
                if notify:
                    name = ""
                    if dev and port in dev.state.ports and mode in dev.state.ports[port].modes:
                        name = dev.state.ports[port].modes[mode].name
                    self.subscriptions[port] = {"mode": mode, "name": name,
                                                "delta": int.from_bytes(p[2:6], "little")}
                else:
                    self.subscriptions.pop(port, None)
        except Exception as exc:  # la decodifica non deve mai fermare il flusso
            log.debug("decodifica evento fallita: %s", exc)
        self._push(ev)
        if ev["interesting"]:
            try:
                self.db.add_event(self.device_row_id, ev)
            except Exception as exc:
                log.debug("salvataggio evento fallito: %s", exc)

    def _track_accessories(self, io_type: Optional[int], mode: Optional[int], data: bytes) -> None:
        if io_type == codes.IO_MARIO_PANTS and data:
            code = data[0] & 0x3F
            if code:
                self.db.mark_seen("pants", code)
        elif io_type == codes.IO_MARIO_SCANNER and mode == 0 and len(data) >= 4:
            barcode, color = lwp3.le16(data[0:2]), lwp3.le16(data[2:4])
            if barcode != 0xFFFF:
                self.db.mark_seen("tile", barcode)
            if color != 0xFFFF:
                self.db.mark_seen("color", color)

    # ----- stato -------------------------------------------------------------------------

    def status(self) -> dict:
        dev = self.device
        info = None
        ports = {}
        if dev is not None:
            try:
                props = dev.state.properties
                info = {
                    "name": lwp3.decode_hub_property(HubProperty.ADVERTISING_NAME, props[HubProperty.ADVERTISING_NAME])
                    if HubProperty.ADVERTISING_NAME in props else (dev.ble_device.name if dev.ble_device else None),
                    "address": dev.ble_device.address if dev.ble_device else dev.address,
                    "identity": lwp3.decode_hub_property(HubProperty.PRIMARY_MAC, props[HubProperty.PRIMARY_MAC])
                    if HubProperty.PRIMARY_MAC in props else None,
                    "fw": dev.state.property_text(HubProperty.FW_VERSION),
                    "volume": lwp3.decode_hub_property(HubProperty.VOLUME, props[HubProperty.VOLUME])
                    if HubProperty.VOLUME in props else None,
                    "bonded": dev.bonded,
                    "connected": dev.connected,
                }
                for port, ps in sorted(list(dev.state.ports.items())):
                    mi = ps.modes.get(ps.current_mode) if ps.current_mode is not None else None
                    ports[str(port)] = {
                        "io_type": f"0x{ps.io_type:04X}", "io_name": ps.io_name,
                        "current_mode": ps.current_mode, "mode_name": mi.name if mi else None,
                        "notify": ps.notify,
                        "modes": {str(m): x.name for m, x in sorted(ps.modes.items())},
                        "last": self.port_last.get(port),
                    }
            except Exception as exc:
                log.debug("status: %s", exc)
        return {"state": self.state, "error": self.last_error, "progress": list(self.progress)[-6:],
                "device": info, "ports": ports, "subscriptions": {str(k): v for k, v in self.subscriptions.items()},
                "battery": self.battery, "seq": self.seq, "db_device_id": self.device_row_id,
                "adapter": self.adapter, "pair": self.pair, "last_scan_time": self.last_scan_time,
                "streams": len(self._streams)}

    # ----- operazioni (sincrone, chiamate da Flask) ----------------------------------------

    def scan(self, duration: float = 8.0, any_lego: bool = True) -> list[dict]:
        async def run():
            found = await MarioDevice.scan(duration, any_lego, self.adapter)
            result = []
            for device, adv in found:
                system_type = advertised_system_type(adv)
                result.append({
                    "address": device.address, "name": adv.local_name or device.name or "",
                    "rssi": adv.rssi, "system_type": system_type,
                    "type_name": lwp3.SYSTEM_TYPE_NAMES.get(system_type, "hub LEGO") if system_type is not None else "LEGO",
                    "manufacturer": (adv.manufacturer_data.get(lwp3.LEGO_COMPANY_ID) or b"").hex(" "),
                    "is_mario": system_type in codes.MARIO_SYSTEM_TYPES,
                    "description": describe_advertisement(device, adv),
                })
            return result
        previous = self.state
        if previous == "disconnected":
            self._set_state("scanning")
        try:
            self.last_scan = self.call(run, timeout=duration + 20)
            self.last_scan_time = time.time()
            self._say(f"Scansione: {len(self.last_scan)} dispositivi LEGO")
            return self.last_scan
        finally:
            if self.state == "scanning":
                self._set_state("disconnected")

    def connect(self, address: Optional[str] = None, timeout: float = 45.0, subscribe: bool = True) -> dict:
        async def run():
            if self.device is not None and self.device.connected:
                return self.status()
            self.last_error = ""
            self._set_state("connecting")
            dev = MarioDevice(address or None, adapter=self.adapter, pair=self.pair)
            try:
                await dev.connect(timeout, progress=self._say)
            except Exception as exc:
                self.last_error = str(exc)
                self._set_state("disconnected")
                raise
            self.device = dev
            self.battery = None
            self.port_last.clear()
            self.subscriptions.clear()
            dev.add_listener(self._on_message)
            self._set_state("connected")
            self.loop.create_task(self._watch_disconnect(dev))
            try:
                self._set_state("discovering")
                await dev.discover(full=False, progress=self._say)
            except Exception as exc:
                self._say(f"scoperta rapida fallita: {exc}")
            finally:
                if self.device is dev and dev.connected:
                    self._set_state("connected")
            self.device_row_id = self._store_device(dev)
            if subscribe:
                await self._subscribe_defaults(dev)
            for prop in (HubProperty.BUTTON, HubProperty.BATTERY_VOLTAGE):
                try:
                    await dev.property_updates(prop, True)
                except Exception as exc:
                    log.debug("property_updates %s: %s", prop, exc)
            try:
                await dev.get_property_raw(HubProperty.BATTERY_VOLTAGE, timeout=2.0)
            except Exception:
                pass
            return self.status()
        return self.call(run, timeout=timeout + 120)

    async def _watch_disconnect(self, dev: MarioDevice) -> None:
        await dev.disconnected.wait()
        if self.device is dev:
            self._say("Mario si è disconnesso")
            self.subscriptions.clear()
            self._set_state("disconnected")
            try:
                await dev.disconnect()
            except Exception:
                pass
            self.device = None

    async def _subscribe_defaults(self, dev: MarioDevice) -> None:
        for port, ps in sorted(dev.state.ports.items()):
            names = {m: mi.name for m, mi in ps.modes.items()}
            mode = codes.default_mode(ps.io_type, names)
            if mode is None:
                continue
            delta = codes.DEFAULT_DELTAS.get(ps.io_type, 1)
            try:
                await dev.subscribe(port, mode, delta)
                self._say(f"Notifiche attive: porta {port} modo {mode} ({names.get(mode, '?')})")
            except (LWPError, asyncio.TimeoutError, BleakError) as exc:
                self._say(f"Porta {port} modo {mode}: impossibile attivare ({exc})")

    def _store_device(self, dev: MarioDevice) -> Optional[int]:
        props = dev.state.properties

        def prop(p):
            return lwp3.decode_hub_property(p, props[p]) if p in props else None

        identity = prop(HubProperty.PRIMARY_MAC) or (dev.ble_device.address if dev.ble_device else None)
        if not identity:
            return None
        try:
            return self.db.upsert_device(
                identity, name=prop(HubProperty.ADVERTISING_NAME), system_type=prop(HubProperty.SYSTEM_TYPE_ID),
                fw_version=prop(HubProperty.FW_VERSION), hw_version=prop(HubProperty.HW_VERSION),
                lwp_version=prop(HubProperty.LWP_VERSION), radio_fw=prop(HubProperty.RADIO_FW_VERSION),
                manufacturer=prop(HubProperty.MANUFACTURER_NAME), battery_type=prop(HubProperty.BATTERY_TYPE),
                volume=prop(HubProperty.VOLUME), last_battery=prop(HubProperty.BATTERY_VOLTAGE),
                last_address=dev.ble_device.address if dev.ble_device else None,
                bonded=1 if dev.bonded else 0, capabilities=json.dumps(dev.to_dict(), ensure_ascii=False))
        except Exception as exc:
            log.warning("salvataggio dispositivo fallito: %s", exc)
            return None

    def _require(self) -> MarioDevice:
        if self.device is None or not self.device.connected:
            raise BleakError("Mario non è connesso")
        return self.device

    def disconnect(self) -> dict:
        async def run():
            dev = self.device
            if dev is not None:
                self.device = None
                try:
                    await dev.disconnect()
                finally:
                    self.subscriptions.clear()
                    self._set_state("disconnected")
            return self.status()
        return self.call(run, timeout=30)

    def discover(self, full: bool = True) -> dict:
        async def run():
            dev = self._require()
            self._set_state("discovering")
            try:
                await dev.discover(full=full, progress=self._say)
            finally:
                if dev.connected:
                    self._set_state("connected")
            self.device_row_id = self._store_device(dev)
            return dev.to_dict()
        return self.call(run, timeout=300)

    def report(self) -> str:
        dev = self._require()
        return dev.report()

    def subscribe(self, port: int, mode: int, delta: int = 1, notify: bool = True) -> str:
        async def run():
            dev = self._require()
            reply = await dev.subscribe(port, mode, delta, notify)
            return dev.describe(reply)
        return self.call(run, timeout=20)

    def request_value(self, port: int) -> str:
        async def run():
            dev = self._require()
            return (await dev.request_value(port)).hex(" ")
        return self.call(run, timeout=20)

    def get_property(self, prop: int) -> dict:
        async def run():
            dev = self._require()
            raw = await dev.get_property_raw(prop)
            return {"prop": lwp3.enum_name(HubProperty, prop), "id": prop, "raw": raw.hex(" "),
                    "value": lwp3.decode_hub_property(prop, raw), "text": lwp3.format_hub_property(prop, raw)}
        return self.call(run, timeout=20)

    def get_all_properties(self) -> list[dict]:
        async def run():
            dev = self._require()
            out = []
            for prop in HubProperty:
                try:
                    raw = await dev.get_property_raw(prop, timeout=1.5)
                    out.append({"prop": prop.name, "id": int(prop), "raw": raw.hex(" "),
                                "text": lwp3.format_hub_property(prop, raw)})
                except (LWPError, asyncio.TimeoutError) as exc:
                    out.append({"prop": prop.name, "id": int(prop), "raw": "", "text": f"non disponibile ({exc})"})
            return out
        return self.call(run, timeout=60)

    def property_updates(self, prop: int, enable: bool) -> None:
        self.call(lambda: self._require().property_updates(prop, enable), timeout=20)

    def set_volume(self, level: int) -> int:
        return self.call(lambda: self._require().set_volume(level), timeout=20)

    def set_name(self, name: str) -> str:
        return self.call(lambda: self._require().set_name(name), timeout=20)

    def send_hex(self, text: str) -> None:
        self.call(lambda: self._require().send_hex(text), timeout=20)

    def send_message(self, msg_type: int, payload: bytes) -> None:
        self.call(lambda: self._require().send_message(msg_type, payload), timeout=20)

    def port_output(self, port: int, subcommand: int, payload: bytes) -> None:
        self.call(lambda: self._require().port_output(port, subcommand, payload), timeout=20)

    def hub_action(self, action: int) -> None:
        self.call(lambda: self._require().hub_action(action), timeout=20)

    def alert(self, alert_type: int, operation: int) -> Optional[str]:
        async def run():
            dev = self._require()
            reply = await dev.alert(alert_type, operation)
            return dev.describe(reply) if reply else None
        return self.call(run, timeout=20)

    def unpair(self, address: Optional[str] = None) -> bool:
        async def run():
            dev = self.device if self.device is not None else MarioDevice(address, adapter=self.adapter, pair=False)
            target = None
            if address and dev.ble_device and dev.ble_device.address.lower() != address.lower():
                dev = MarioDevice(address, adapter=self.adapter, pair=False)
            if dev.ble_device is None:
                target = await dev.find(20.0)
            return await dev.unpair(target)
        return self.call(run, timeout=60)
