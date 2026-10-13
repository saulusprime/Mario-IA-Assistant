"""MarioDevice e BleWorker con un BleakClient finto: connessione, richieste, errori, scoperta, notifiche."""
import asyncio
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from mario import lwp3
from mario.lwp3 import HubProperty, HubPropertyOperation, MessageType, ModeInfoType, PortInfoType

HAVE_BLEAK = importlib.util.find_spec("bleak") is not None

LEGO = 0x0397
PORTS = {0: 0x47, 1: 0x49, 2: 0x4A, 3: 0x46, 4: 0x55, 6: 0x14, 7: 0x60}
MODE_NAMES = {0: ["RAW", "GEST"], 1: ["TAG", "RGB"], 2: ["PANT"], 3: ["CHAL", "VERS", "EVENTS", "DEBUG"],
              4: ["Events"], 6: ["VLT L", "VLT S"], 7: ["Telemetry"]}
FORMATS = {(0, 0): (3, 0), (0, 1): (2, 1), (1, 0): (2, 1), (1, 1): (3, 0), (2, 0): (1, 0), (3, 2): (2, 1),
           (6, 0): (1, 1), (7, 0): (16, 0)}
PROPERTIES = {HubProperty.ADVERTISING_NAME: b"Mario test", HubProperty.FW_VERSION: bytes.fromhex("00 00 01 65"),
              HubProperty.BATTERY_VOLTAGE: bytes([80]), HubProperty.VOLUME: bytes([50]),
              HubProperty.SYSTEM_TYPE_ID: bytes([0x40]), HubProperty.PRIMARY_MAC: bytes.fromhex("f460775a2011")}


def attached_io(port, io_type):
    return lwp3.build(MessageType.HUB_ATTACHED_IO, bytes([port, 1]) + io_type.to_bytes(2, "little") + bytes(8))


class FakeHub:
    """Risponde ai messaggi LWP3 come farebbe Mario (sottoinsieme sufficiente ai test)."""

    def __init__(self):
        self.volume = 50
        self.errors_for: set[int] = set()  # tipi di messaggio a cui rispondere con INVALID_USE
        self.silent_for: set[int] = set()  # tipi di messaggio senza risposta (timeout)

    def replies(self, data: bytes) -> list[bytes]:
        msg = lwp3.parse(data)
        t, p = msg.msg_type, msg.payload
        if t in self.silent_for:
            return []
        if t in self.errors_for:
            return [lwp3.build(MessageType.GENERIC_ERROR, bytes([t, 0x06]))]
        if t == MessageType.HUB_PROPERTIES:
            prop, op = p[0], p[1]
            if op == HubPropertyOperation.SET and prop == HubProperty.VOLUME:
                self.volume = p[2]
                return []
            if op == HubPropertyOperation.REQUEST_UPDATE:
                value = bytes([self.volume]) if prop == HubProperty.VOLUME else PROPERTIES.get(prop)
                if value is None:
                    return [lwp3.build(MessageType.GENERIC_ERROR, bytes([t, 0x06]))]
                return [lwp3.build(MessageType.HUB_PROPERTIES, bytes([prop, HubPropertyOperation.UPDATE]) + value)]
            return []
        if t == MessageType.PORT_INFORMATION_REQUEST:
            port, info = p[0], p[1]
            if port not in PORTS:
                return [lwp3.build(MessageType.GENERIC_ERROR, bytes([t, 0x06]))]
            if info == PortInfoType.MODE_INFO:
                n = len(MODE_NAMES[port])
                caps = 0x06 if port in (0, 1) else 0x02
                mask = (1 << n) - 1
                return [lwp3.build(MessageType.PORT_INFORMATION, bytes([port, 1, caps, n]) + mask.to_bytes(2, "little") + bytes(2))]
            if info == PortInfoType.POSSIBLE_MODE_COMBINATIONS:
                return [lwp3.build(MessageType.PORT_INFORMATION, bytes([port, 2, 0x03, 0x00]))]
            if info == PortInfoType.PORT_VALUE:
                return [lwp3.build(MessageType.PORT_VALUE_SINGLE, bytes([port, 0x21]))]
        if t == MessageType.PORT_MODE_INFORMATION_REQUEST:
            port, mode, info = p[0], p[1], p[2]
            if info == ModeInfoType.NAME:
                return [lwp3.build(MessageType.PORT_MODE_INFORMATION, bytes([port, mode, info]) + MODE_NAMES[port][mode].encode())]
            if info == ModeInfoType.VALUE_FORMAT:
                ds, vt = FORMATS.get((port, mode), (1, 0))
                return [lwp3.build(MessageType.PORT_MODE_INFORMATION, bytes([port, mode, info, ds, vt, 3, 0]))]
            return [lwp3.build(MessageType.GENERIC_ERROR, bytes([t, 0x06]))]
        if t == MessageType.PORT_INPUT_FORMAT_SETUP_SINGLE:
            return [lwp3.build(MessageType.PORT_INPUT_FORMAT_SINGLE, p[:6] + p[6:7])]
        if t == MessageType.HUB_ALERTS and p[1] == 0x03:
            return [lwp3.build(MessageType.HUB_ALERTS, bytes([p[0], 0x04, 0x00]))]
        if t == MessageType.HUB_ACTIONS and p[0] == 0x02:
            return [lwp3.build(MessageType.HUB_ACTIONS, bytes([0x31])), b"DISCONNECT"]
        return []


class FakeAttr:
    """Servizio, caratteristica o descrittore GATT finto (stessi attributi di bleak)."""

    def __init__(self, uuid, handle, characteristics=(), properties=(), descriptors=()):
        self.uuid, self.handle = uuid, handle
        self.characteristics, self.properties, self.descriptors = list(characteristics), list(properties), list(descriptors)


FAKE_SERVICES = [
    FakeAttr("00001800-0000-1000-8000-00805f9b34fb", 1, [FakeAttr("00002a00-0000-1000-8000-00805f9b34fb", 3, properties=["read"])]),
    FakeAttr(lwp3.LEGO_SERVICE_UUID, 0x10, [FakeAttr(lwp3.LEGO_CHARACTERISTIC_UUID, 0x12, properties=["write", "notify"],
                                                      descriptors=[FakeAttr("00002902-0000-1000-8000-00805f9b34fb", 0x13)])]),
    FakeAttr(lwp3.OAD_SERVICE_UUID, 0x20, [FakeAttr("f000ffc1-0451-4000-b000-000000000000", 0x22, properties=["write-without-response", "notify"])]),
]


class FakeClient:
    hub = FakeHub()
    fail_first_connect = False
    created: list = []
    services = FAKE_SERVICES

    def __init__(self, device, disconnected_callback=None, timeout=20.0, **kwargs):
        self.device = device
        self.callback = disconnected_callback
        self._connected = False
        self._notify = None
        self.written: list[bytes] = []
        FakeClient.created.append(self)

    async def connect(self):
        if FakeClient.fail_first_connect and len(FakeClient.created) == 1:
            from bleak.exc import BleakError
            raise BleakError("le-connection-abort-by-local")
        self._connected = True

    @property
    def is_connected(self):
        return self._connected

    async def start_notify(self, uuid, callback, **kwargs):
        self._notify = callback
        for port, io_type in PORTS.items():
            callback(None, bytearray(attached_io(port, io_type)))

    def push(self, raw: bytes):
        if self._notify:
            self._notify(None, bytearray(raw))

    async def write_gatt_char(self, uuid, data, response=True):
        self.written.append(bytes(data))
        for reply in self.hub.replies(bytes(data)):
            if reply == b"DISCONNECT":
                await self.disconnect()
            else:
                self.push(reply)

    async def disconnect(self):
        if self._connected:
            self._connected = False
            if self.callback:
                self.callback(self)


class FakeScanner:
    def __init__(self, on_detection, **kwargs):
        self.on_detection = on_detection

    async def start(self):
        from bleak.backends.device import BLEDevice
        from bleak.backends.scanner import AdvertisementData
        device = BLEDevice("F4:60:77:5A:20:11", "Mario test", {"path": "/org/bluez/hci0/dev_F4", "props": {}})
        adv = AdvertisementData(local_name="Mario test", manufacturer_data={LEGO: bytes([0, 0x43, 0, 0, 0, 0])},
                                service_data={}, service_uuids=[lwp3.LEGO_SERVICE_UUID], tx_power=None, rssi=-50,
                                platform_data=())
        asyncio.get_running_loop().call_soon(self.on_detection, device, adv)

    async def stop(self):
        pass


def run(coro):
    return asyncio.run(coro)


@unittest.skipUnless(HAVE_BLEAK, "serve bleak (venv dell'host)")
class DeviceFakeTests(unittest.TestCase):
    def setUp(self):
        from mario import device as devmod
        self.devmod = devmod
        self._orig = (devmod.BleakClient, devmod.BleakScanner)
        devmod.BleakClient, devmod.BleakScanner = FakeClient, FakeScanner
        FakeClient.created = []
        FakeClient.hub = FakeHub()
        FakeClient.fail_first_connect = False

    def tearDown(self):
        self.devmod.BleakClient, self.devmod.BleakScanner = self._orig

    def make(self):
        return self.devmod.MarioDevice(pair=False)

    def test_connect_discover_report(self):
        async def go():
            dev = self.make()
            progress = []
            await dev.connect(timeout=2.0, progress=progress.append)
            self.assertTrue(dev.connected)
            self.assertEqual(sorted(dev.state.ports), sorted(PORTS))
            await dev.discover(full=False)
            report = dev.report()
            self.assertIn("associato (bond): sconosciuto", report)
            self.assertIn("modo 1: GEST", report)
            self.assertIn("FW_VERSION", report)
            self.assertEqual(await dev.get_volume(), 50)
            self.assertEqual(await dev.set_volume(70), 70)
            d = dev.to_dict()
            self.assertEqual(d["properties"]["FW_VERSION"]["value"], "6.5.01.0000")
            self.assertEqual(d["ports"][0]["combinations"], [[0, 1]])
            # tabella GATT: servizi noti con nome, OAD segnalato, descrittore CCCD
            self.assertIn("=== Servizi GATT ===", report)
            self.assertIn("Texas Instruments OAD", report)
            self.assertIn("DA NON TOCCARE", report)
            self.assertIn("desc. 0x0013 00002902", report)
            self.assertEqual([svc["uuid"] for svc in d["gatt"]], [s.uuid for s in FAKE_SERVICES])
            self.assertEqual(d["gatt"][1]["characteristics"][0]["properties"], ["write", "notify"])
            await dev.disconnect()
            self.assertFalse(dev.connected)
        run(go())

    def test_request_error_and_timeout(self):
        async def go():
            dev = self.make()
            await dev.connect(timeout=2.0)
            with self.assertRaises(self.devmod.LWPError) as ctx:
                await dev.request_port_information(9)
            self.assertEqual(ctx.exception.code, 0x06)
            self.assertIn("INVALID_USE", str(ctx.exception))
            FakeClient.hub.silent_for.add(MessageType.HUB_PROPERTIES)
            with self.assertRaises(asyncio.TimeoutError):
                await dev.get_property_raw(HubProperty.VOLUME, timeout=0.1)
            self.assertEqual(dev._waiters, [])
            await dev.disconnect()
        run(go())

    def test_subscribe_listener_and_combined(self):
        async def go():
            dev = self.make()
            await dev.connect(timeout=2.0)
            await dev.discover(full=False)
            seen = []
            dev.add_listener(lambda msg, text: seen.append((msg.type_name, text)))
            reply = await dev.subscribe(2, 0)
            self.assertEqual(reply.msg_type, MessageType.PORT_INPUT_FORMAT_SINGLE)
            self.assertEqual(dev.state.ports[2].current_mode, 0)
            FakeClient.created[-1].push(bytes.fromhex("05 00 45 02 12"))
            self.assertIn("Fuoco", seen[-1][1])
            ev = dev.event(lwp3.parse(bytes.fromhex("05 00 45 02 12")))
            self.assertEqual((ev["port"], ev["mode"], ev["io_type"]), (2, 0, 0x4A))
            await dev.set_combined_mode(0, 0, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)], deltas={0: 4, 1: 1})
            setup = [lwp3.describe_downstream(lwp3.parse(w)) for w in FakeClient.created[-1].written[-5:]]
            self.assertIn("LOCK", setup[0])
            self.assertIn("modo 0: delta 4", setup[1])
            self.assertIn("modo 1: delta 1", setup[2])
            self.assertIn("combinazione 0 con modo:dataset 0:0 0:1 0:2 1:0 1:1", setup[3])
            self.assertIn("UNLOCK_MULTI_UPDATE_ENABLED", setup[4])
            FakeClient.created[-1].push(bytes.fromhex("0d 00 46 00 00 1f 02 68 0a 00 21 00 21"))
            self.assertIn("rotazione (turning)", seen[-1][1])
            self.assertEqual(dev.state.last_combined[0][1], bytes.fromhex("00210021"))
            await dev.disconnect()
        run(go())

    def test_disconnect_callback_fails_waiters_and_retry(self):
        async def go():
            FakeClient.fail_first_connect = True
            dev = self.make()
            progress = []
            await dev.connect(timeout=2.0, progress=progress.append)
            self.assertEqual(len(FakeClient.created), 2)
            self.assertTrue(any("tentativo 2/3" in t for t in progress))
            await dev.request_disconnect()  # il finto hub risponde WILL_DISCONNECT e chiude
            await asyncio.sleep(0)
            self.assertTrue(dev.disconnected.is_set())
            self.assertFalse(dev.connected)
            from bleak.exc import BleakError
            with self.assertRaises(BleakError):
                await dev.send(b"\x04\x00\x02\x02")
            hint = self.devmod._connect_hint(BleakError("le-connection-abort-by-local"))
            self.assertIn("Broadcom", hint)
            await dev.disconnect()
        run(go())


@unittest.skipUnless(HAVE_BLEAK and importlib.util.find_spec("flask"), "servono bleak e flask (venv dell'host)")
class WorkerFakeTests(unittest.TestCase):
    def setUp(self):
        from mario import device as devmod
        from mario.web import ble_worker
        self.devmod, self.ble_worker = devmod, ble_worker
        self._orig = (devmod.BleakClient, devmod.BleakScanner)
        devmod.BleakClient, devmod.BleakScanner = FakeClient, FakeScanner
        FakeClient.created = []
        FakeClient.hub = FakeHub()
        FakeClient.fail_first_connect = False
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.devmod.BleakClient, self.devmod.BleakScanner = self._orig
        self.tmp.cleanup()

    def test_worker_connect_events_and_db(self):
        from mario.web.db import Database
        db = Database(Path(self.tmp.name) / "w.sqlite3")
        worker = self.ble_worker.BleWorker(db, pair=False)
        worker.start()
        try:
            stream = worker.open_stream()
            status = worker.connect(timeout=3.0)
            self.assertEqual(status["state"], "connected")
            self.assertEqual(status["device"]["fw"], "6.5.01.0000")
            self.assertIn("2", status["subscriptions"])
            self.assertIsNotNone(worker.device_row_id)
            self.assertEqual(db.get_device(worker.device_row_id)["fw_version"], "6.5.01.0000")
            FakeClient.created[-1].push(bytes.fromhex("08 00 45 01 02 00 25 00"))
            time.sleep(0.2)
            self.assertIn("Goomba", worker.port_last[1]["text"])
            evs = worker.events_since(0, 500)
            tile = [e for e in evs if e.get("port") == 1][-1]
            self.assertEqual(tile["decoded"][:14], "barcode 0x0002")
            seen = [a for a in db.list_accessories("tile") if a["code"] == 2]
            self.assertEqual(seen[0]["seen_count"], 1)
            self.assertEqual(worker.set_volume(30), 30)
            self.assertFalse(stream.empty())
            worker.disconnect()
            self.assertEqual(worker.status()["state"], "disconnected")
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
