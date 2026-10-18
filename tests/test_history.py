"""Archivio delle partite (porta 7), canale della porta 4 e scritture note dell'app: record veri della cattura del 18/10."""
import asyncio
import io
import unittest

from mario import codes, decode, history, lwp3
from mario.lwp3 import MessageType

# record presi dalla cattura del traffico dell'app ufficiale (18/10/2026), dati della porta 7 senza intestazione LWP3
SESSION = bytes.fromhex("01 0b 92 01 00 00 a0 08 8e 08 01 13 04 00 21 01")
TILES = bytes.fromhex("02 02 00 0a 14 00 01 2e 00 03 b8 00 01 f5 00 01")
TILES_SHORT = bytes.fromhex("02 5b 01 01 60 01 01 00 00 00 00 00 00 00 00 00")
COUNTERS = bytes.fromhex("05 00 11 00 01 07 00 02 02 00 03 01 00 04 04 00")
COUNTERS_A = bytes.fromhex("03 03 01 00 06 02 00 00 00 00 00 00 00 00 00 00")
END = bytes.fromhex("ff ff ff ff ff ff ff ff ff ff ff ff 3d 00 08 00")


def session(number: int, records: int) -> bytes:
    return bytes([1, records]) + number.to_bytes(4, "little") + bytes.fromhex("a0 08 8e 08 01 13 04 00 21 01")


class HistoryTest(unittest.TestCase):
    def test_parse_records(self):
        s = history.parse(SESSION)
        self.assertEqual((s["kind"], s["records"], s["number"], s["raw_a"], s["raw_b"], s["pants"]),
                         ("session", 11, 402, 2208, 2190, 0x21))
        self.assertEqual(history.parse(TILES)["tiles"], [(0x02, 10), (0x14, 1), (0x2E, 3), (0xB8, 1), (0xF5, 1)])
        self.assertEqual(history.parse(TILES_SHORT)["tiles"], [(0x15B, 1), (0x160, 1)])  # zeri finali = riempimento
        self.assertEqual(history.parse(COUNTERS)["counters"], [(0, 17), (1, 7), (2, 2), (3, 1), (4, 4)])  # #0 è valido
        self.assertEqual(history.parse(COUNTERS_A)["counters"], [(3, 1), (6, 2)])
        self.assertEqual(history.parse(END), {"kind": "end", "records": 61, "sessions": 8})
        self.assertIsNone(history.parse(b"\x01"))
        self.assertEqual(history.parse(bytes([0x09]) + bytes(15))["kind"], "unknown")

    def test_describe(self):
        self.assertIn("partita n. 402 (11 record)", history.describe(SESSION))
        self.assertIn("pantaloni Mario normale", history.describe(SESSION))
        self.assertEqual(history.describe(TILES_SHORT), "archivio: basette Carrello minerario ×1, Aereo ×1")
        self.assertEqual(history.describe(END), "archivio: fine, 61 record in 8 partite")
        # il monitor e decode passano dalla stessa funzione
        self.assertEqual(codes.decode_value(codes.IO_MARIO_TELEMETRY, 0, END, []), history.describe(END))
        self.assertEqual(codes.decode_value(codes.IO_MARIO_TELEMETRY, 0, b"\x01\x02", []), "telemetria (grezza) 01 02")

    def test_collector_groups_and_checks(self):
        c = history.Collector()
        for rec in (session(402, 3), TILES, COUNTERS, session(403, 1)):
            c.feed(rec)
        self.assertFalse(c.complete)
        self.assertEqual(c.problems(), ["manca il record finale: scarico interrotto?"])
        c.feed(bytes([0xFF] * 12) + (4).to_bytes(2, "little") + (2).to_bytes(2, "little"))
        self.assertTrue(c.complete)
        self.assertEqual(c.problems(), [])
        self.assertEqual(c.sessions[0]["tiles"][0x02], 10)
        self.assertEqual(c.sessions[0]["counters"][history.COUNTERS_B][0], 17)
        text = "\n".join(c.summary())
        self.assertIn("archivio delle partite (porta 7): 2 partite, 4 record", text)
        self.assertIn("basette: Goomba ×10, Rotazione ×1, Nuvola ×3, Start Mario ×1", text)
        bad = history.Collector()
        for rec in (session(1, 3), TILES, END):
            bad.feed(rec)
        self.assertEqual(bad.problems(), ["partite: 1 ricevute, 8 annunciate", "record: 3 dichiarati dalle partite, 61 nel record finale",
                                          "partita n. 1: 2 record ricevuti su 3"])

    def test_port4_and_known_writes(self):
        self.assertEqual(codes.decode_value(codes.IO_MARIO_ALT_EVENTS, 0, bytes.fromhex("0b 00 ec ca"), []),
                         "porta 4: risposta 0x0B valore 0xCAEC (risposta alle richieste 0x09 e 0x0A)")
        self.assertIn("richiesta 0x05 con 0x00FE", codes.describe_write(4, 0x51, bytes.fromhex("00 05 00 fe 00")))
        self.assertIn("istantanea dei contatori", codes.describe_write(3, 0x51, bytes.fromhex("02 01 f0")))
        self.assertIn("richiesta dell'archivio", codes.describe_write(7, 0x51, bytes.fromhex("00 01")))
        self.assertIn("probabile cancellazione", codes.describe_write(7, 0x51, bytes.fromhex("00 02")))
        self.assertIsNone(codes.describe_write(7, 0x51, bytes.fromhex("00 03")))
        self.assertIsNone(codes.describe_write(4, 0x50, bytes.fromhex("00 05 00 fe 00")))
        self.assertIn("istantanea", codes.GAME_EVENTS[0x1902])

    def test_decode_summary_of_the_app_handshake(self):
        port7 = lambda d: "14 00 45 07 " + d.hex(" ")  # noqa: E731
        lines = ["-> 09 00 81 03 11 51 02 01 f0", "08 00 45 03 02 19 a8 00",
                 "-> 0b 00 81 04 11 51 00 05 00 fe 00", "08 00 45 04 01 00 fe 00",
                 "-> 08 00 81 07 01 51 00 01", port7(session(402, 2)), port7(TILES),
                 port7(bytes([0xFF] * 12) + bytes.fromhex("02 00 01 00")), "-> 08 00 81 07 01 51 00 02"]
        buf = io.StringIO()
        decode.run([], hex_args=lines, out=buf, summary=True)
        text = buf.getvalue()
        self.assertIn("Comando output porta 7: WriteDirectModeData, avvio in coda, feedback sì, modo 0, dati 01 → richiesta "
                      "dell'archivio delle partite", text)
        self.assertIn("archivio: partita n. 402 (2 record)", text)
        self.assertIn("0x1902:    1  [168]  istantanea", text)
        self.assertIn("scritture note dell'app ufficiale e risposte della porta 4:", text)
        self.assertIn("<- porta 4: risposta 0x01 valore 0x00FE", text)
        self.assertIn("archivio delle partite (porta 7): 1 partita, 2 record", text)
        self.assertNotIn("attenzione", text)


class FakeMario:
    """Risponde alle richieste dell'app come il Mario della cattura del 18/10."""

    def __init__(self, records=(), port4=True):
        self.records, self.port4 = list(records), port4
        self.listeners, self.sent, self.subscribed = [], [], []

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    async def subscribe(self, port, mode, delta=1):
        self.subscribed.append((port, mode))

    def emit(self, port, data):
        msg = lwp3.parse(lwp3.build(MessageType.PORT_VALUE_SINGLE, bytes([port]) + data))
        for listener in list(self.listeners):
            listener(msg, "")

    async def send(self, frame):
        self.sent.append(frame)
        p = lwp3.parse(frame).payload
        port, data = p[0], p[4:]  # porta, flag, sottocomando, modo, dati
        if port == 3:
            self.emit(3, bytes.fromhex("02 19 a8 00"))
        elif port == 4 and self.port4:
            cmd, value = lwp3.le16(data[0:2]), lwp3.le16(data[2:4])
            self.emit(4, (b"\x01\x00" + value.to_bytes(2, "little")) if cmd == 5 else bytes.fromhex("0b 00 ff ff"))
        elif port == 7 and data == b"\x01":
            feedback = lwp3.parse(lwp3.build(MessageType.PORT_OUTPUT_COMMAND_FEEDBACK,
                                             bytes([7, 0x0A]) + max(0, len(self.records) - 1).to_bytes(2, "little")))
            for listener in list(self.listeners):
                listener(feedback, "")
            loop = asyncio.get_running_loop()
            for i, rec in enumerate(self.records):
                loop.call_later(0.001 * (i + 1), self.emit, 7, rec)


class FetchTest(unittest.TestCase):
    def test_fetch_repeats_only_the_read_requests(self):
        records = [session(402, 2), TILES, bytes([0xFF] * 12) + bytes.fromhex("02 00 01 00")]
        dev = FakeMario(records)
        result = asyncio.run(history.fetch(dev, timeout=2.0))
        self.assertEqual(dev.subscribed, [(3, 2), (4, 0), (7, 0)])
        # stessi byte dell'app ufficiale; mai 02 sulla porta 7 né 0x0A sulla porta 4
        self.assertEqual([f.hex(" ") for f in dev.sent], ["09 00 81 03 11 51 02 01 f0", "0b 00 81 04 11 51 00 05 00 fe 00",
                                                          "0b 00 81 04 11 51 00 09 00 00 00", "08 00 81 07 01 51 00 01"])
        self.assertEqual(result["snapshot"], [(0x1902, 168)])
        self.assertEqual([(i["request"], i["reply"], i["reply_value"]) for i in result["port4"]],
                         [(0x05, 0x01, 0x00FE), (0x09, 0x0B, 0xFFFF)])
        self.assertTrue(result["collector"].complete)
        text = "\n".join(history.fetch_report(result))
        self.assertIn("istantanea della porta 3: 0x1902=168", text)
        self.assertIn("porta 4: richiesta 0x05 -> risposta 0x01 valore 0x00FE (risposta alla richiesta 0x05)", text)
        self.assertIn("porta 7: 3 record", text)
        self.assertIn("Mario ne aveva annunciati 2 più quello finale", text)
        self.assertIn("partita n. 402", text)
        data = history.fetch_json(result)
        self.assertEqual(data["sessions"][0]["tiles"]["0x0002"], 10)
        self.assertEqual((data["end"]["sessions"], data["problems"]), (1, []))

    def test_fetch_without_answers(self):
        dev = FakeMario(records=[], port4=False)
        result = asyncio.run(history.fetch(dev, snapshot=False, timeout=0.1, reply_timeout=0.1))
        self.assertEqual([f[3] for f in dev.sent], [4, 4, 7])  # niente richiesta della porta 3
        self.assertEqual([i["reply"] for i in result["port4"]], [None, None])
        text = "\n".join(history.fetch_report(result))
        self.assertIn("porta 4: richiesta 0x09 senza risposta", text)
        self.assertIn("porta 7: nessun record", text)


if __name__ == "__main__":
    unittest.main()
